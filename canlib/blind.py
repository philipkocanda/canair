"""Blind-rediscovery harness core — strip a profile, pick targets, grade guesses.

This is the reusable, unit-testable core behind ``scripts/blind_rediscovery.py``
(the on-demand "can the analysis tooling rediscover a known signal blindfolded?"
eval). Three pure-ish pieces, no device or network:

* :func:`strip_profile` — copy a profile and remove every answer-bearing field
  (``parameters``/``notes``/``research``/``dtcs``/``scan_log``/``iocontrol``/
  ``routines``/``signals``/generated ``out``/``references``), keeping only the
  structural skeleton a blind analyst legitimately has: each ECU's address block
  (so captures still resolve by ``rx``), parameter-less ``draft`` PID keys, the
  raw ``captures/``, and the ``vehicle_states``/``can_buses`` vocab. Stripping is
  itself the blindfold: with no stored names/expressions, the tools that would
  otherwise leak (``investigate``/``coverage``/``decode``/``captures``) have
  nothing to echo.
* :func:`select_targets` — pick the signals to rediscover, either the curated
  non-trivial set or a reproducible seeded-random draw filtered to signals with
  enough capture variance to be findable.
* :func:`grade_answer` — objective scoring: evaluate the analyst's guessed
  expression *and* the held-out ground-truth expression over the same captures
    and compare the two decoded series (Pearson + linear fit for numeric,
    Cramér's V for typed enum/bitmask), so a scale-only miss, a partial, and a
  categorical match are graded consistently.

The ground-truth expressions are read from the *source* (unstripped) profile and
are the grader's private answer key — they never enter the sandbox.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import capture_io, yaml_rt
from . import pids as pids_mod
from .byteindex import payload_to_wican_bytes
from .decode_value import decode_typed
from .expression import evaluate_expression
from .stats import cramers_v, pearson, spearman
from .xanalysis import linear_fit

# ── What the strip removes ────────────────────────────────────────────────────
# Whole ECU-level sections that describe or hint at decodes.
_STRIP_ECU_SECTIONS = (
    "research",
    "dtcs",
    "scan_log",
    "iocontrol",
    "iocontrol_discoveries",
    "routines",
    "notes",
)
# Identity sub-fields that narrate the module's function or carry PII.
_STRIP_IDENTITY_FIELDS = ("description", "notes", "vin", "serial")
# Top-level profile members copied but not needed (and answer-bearing).
_STRIP_BUNDLE_MEMBERS = (
    "out",  # generated autopid.json — contains every expression!
    "references",  # third-party sheets with decodes
    "signals",  # broadcast signal definitions (names/scale = answers)
    "logs",
    "dtc_log.yaml",
)
_COPY_IGNORE = shutil.ignore_patterns(
    *_STRIP_BUNDLE_MEMBERS, ".journal", "*.tmp", ".git", "__pycache__"
)

# ── Curated non-trivial corpus (stable across runs) ─────────────────────────────
# (ecu, pid, param, role_hint). The hint states the physical quantity to locate —
# which follows from the ECU's role, not from the stored answer — mirroring the
# 2026-08-02 manual run so scores stay comparable.
CURATED: tuple[tuple[str, str, str, str], ...] = (
    ("MCU", "2102", "MCU_MOTOR_RPM", "the electric drive motor's rotational speed (RPM)"),
    (
        "VCU",
        "2101",
        "VCU_VEHICLE_SPEED",
        "the vehicle road speed (unusual unit/byte-order possible)",
    ),
    ("VCU", "2101", "VCU_POWER_STATE", "the powertrain power-mode state-machine byte (enum)"),
    ("ESC", "22C101", "STEERING_ANGLE", "the steering wheel angle (absolute SAS)"),
    ("EPS", "220101", "EPS_STEERING_ANGLE", "the steering wheel angle"),
    ("VCU", "2102", "VCU_INVERTER_INPUT_VOLTAGE", "the HV DC-link / traction-battery pack voltage"),
    ("VCU", "2102", "VCU_AUX_BATTERY_VOLTAGE", "the 12V auxiliary battery voltage"),
    (
        "BMS",
        "2101",
        "BATTERY_CURRENT",
        "the HV battery pack current (signed; note ISO-TP PCI-skip)",
    ),
    ("OBC", "2101", "AC_INPUT_V", "the AC mains input voltage (RMS) at the charger inlet"),
    ("OBC", "2101", "LDC_TEMP", "the LDC / DC-DC converter temperature"),
    ("HVAC", "220100", "HVAC_HEAT_PUMP_TEMP", "the heat-pump / refrigerant-circuit temperature"),
    ("AAF", "2180", "AMBIENT_TEMP", "the ambient / outside air temperature"),
    ("IGPM", "22BC03", "HOOD_OPEN", "the hood / bonnet ajar switch (single bit)"),
    ("CLU", "22B002", "ODOMETER", "the odometer (total distance, km)"),
    ("HVAC", "2201A0", "HVAC_BLOWER_FAN_SPEED", "the blower fan speed level"),
)

# Coarse physical-quantity hint for randomly-drawn targets (no unit → generic).
_UNIT_HINT = {
    "°C": "a temperature",
    "V": "a voltage",
    "A": "a current",
    "kW": "a power",
    "kWh": "an energy total",
    "Ah": "a charge total",
    "km/h": "a speed",
    "RPM": "a rotational speed",
    "deg": "an angle",
    "%": "a percentage / ratio",
    "bar": "a pressure",
    "km": "a distance / odometer",
    "h": "an elapsed-time total",
    "kΩ": "a resistance",
}

# ── Grade verdicts ──────────────────────────────────────────────────────────────
EXACT = "EXACT"
EQUIVALENT_SCALE = "EQUIVALENT_UP_TO_SCALE"
STRONG_PARTIAL = "STRONG_PARTIAL"
MONOTONE_PARTIAL = "MONOTONE_PARTIAL"
CATEGORICAL_MATCH = "CATEGORICAL_MATCH"
MISS = "MISS"
INSUFFICIENT = "INSUFFICIENT_DATA"
ERROR = "EXPR_ERROR"


@dataclass
class Target:
    """One signal to rediscover, plus its private ground truth (answer key)."""

    ecu: str
    pid: str
    name: str
    expression: str
    rx: str
    role_hint: str
    unit: str = ""
    type: str | None = None
    values: dict | None = None
    bits: dict | None = None
    n_captures: int = 0
    distinct: int = 0

    def quest(self) -> dict[str, Any]:
        """The blindfolded view handed to the analyst (no answer)."""
        return {
            "ecu": self.ecu,
            "pid": self.pid,
            "role_hint": self.role_hint,
            "n_captures": self.n_captures,
        }

    def answer(self) -> dict[str, Any]:
        """The full record incl. ground truth (grader-only)."""
        return {
            "ecu": self.ecu,
            "pid": self.pid,
            "name": self.name,
            "expression": self.expression,
            "unit": self.unit,
            "type": self.type,
            "values": self.values,
            "bits": self.bits,
            "rx": self.rx,
            "role_hint": self.role_hint,
            "n_captures": self.n_captures,
            "distinct": self.distinct,
        }


@dataclass
class StripReport:
    files: int = 0
    params_removed: int = 0
    notes_removed: int = 0
    sections_removed: int = 0
    residual_leaks: list[str] = field(default_factory=list)


# ── Strip ────────────────────────────────────────────────────────────────────────
def _strip_ecu_doc(body: dict, report: StripReport) -> None:
    """Remove answer-bearing content from one ECU mapping (in place)."""
    for sec in _STRIP_ECU_SECTIONS:
        if sec in body:
            del body[sec]
            report.sections_removed += 1
    ident = body.get("identity")
    if isinstance(ident, dict):
        for f in _STRIP_IDENTITY_FIELDS:
            ident.pop(f, None)
    pids = body.get("pids")
    if isinstance(pids, dict):
        for pid_def in pids.values():
            if not isinstance(pid_def, dict):
                continue
            params = pid_def.get("parameters")
            if isinstance(params, dict):
                report.params_removed += len(params)
            pid_def.pop("parameters", None)
            if "notes" in pid_def:
                pid_def.pop("notes", None)
                report.notes_removed += 1
            # A parameter-less PID must stay queryable but is, by definition, not
            # yet decoded → draft (also drops it from any generated device profile).
            if pid_def.get("status") in ("active", "static"):
                pid_def["status"] = "draft"


def _scrub_capture_labels(captures_dir: Path) -> int:
    """Blank session/capture labels+notes (maneuver narration), keep states."""
    scrubbed = 0
    for path in capture_io.iter_capture_files(captures_dir):
        doc = capture_io.load_capture_file(path)
        for sess in doc.get("sessions", []):
            if sess.get("label"):
                sess["label"] = ""
                scrubbed += 1
            sess.pop("notes", None)
            for cap in sess.get("captures", []):
                cap.pop("label", None)
                cap.pop("notes", None)
        capture_io.dump_capture_file(path, doc)
    return scrubbed


def _strip_vehicle_states(path: Path) -> None:
    """Rebuild vehicle_states.yaml with only names+descriptions.

    The ``when:`` auto-suggest predicates (and this file's comments) reference
    decoded parameters as ``ECU.PARAM`` and even cite byte offsets — a direct
    answer leak. Dropping them is safe for the blind test: ``--discriminate
    state`` reads each capture's *stored* ``vehicle_states``, not a re-derived
    label, so state analysis still works.
    """
    import yaml

    doc = yaml.safe_load(path.read_text()) or {}
    clean = []
    for s in doc.get("states") or []:
        if not isinstance(s, dict):
            continue
        entry: dict[str, Any] = {"name": s["name"]}
        if s.get("description"):
            entry["description"] = s["description"]
        clean.append(entry)
    path.write_text(yaml.safe_dump({"states": clean}, sort_keys=False, allow_unicode=True))


def strip_profile(src: Path, dst: Path, *, scrub_labels: bool = True) -> StripReport:
    """Copy the profile at ``src`` to ``dst`` and strip all answer-bearing content.

    Keeps: each ECU's address block (``tx_id``/``rx_id``/``addressing``/
    ``can_bus``/``wake``/session config), parameter-less ``draft`` PID keys, the
    raw ``captures/`` (labels optionally scrubbed), ``vehicle_states.yaml``,
    ``can_buses.yaml`` and ``profile.yaml``. Removes everything in
    :data:`_STRIP_ECU_SECTIONS`/:data:`_STRIP_BUNDLE_MEMBERS` plus all
    ``parameters``/``notes``. Re-validates by scanning the result for leaks.
    """
    src, dst = Path(src), Path(dst)
    if dst.exists():
        raise FileExistsError(f"strip destination already exists: {dst}")
    shutil.copytree(src, dst, ignore=_COPY_IGNORE)

    report = StripReport()
    yaml = yaml_rt.round_trip_yaml()
    ecus_dir = dst / "ecus"
    for path in sorted(ecus_dir.glob("*.yaml")):
        text = path.read_text()
        doc = yaml.load(text)
        if not doc:
            continue
        report.files += 1
        for body in doc.values():  # single top-level ECU key per file
            if isinstance(body, dict):
                _strip_ecu_doc(body, report)
        seq, off = yaml_rt.detect_sequence_indent(text) or (2, 0)
        with open(path, "w") as f:
            yaml_rt.dump(doc, f, sequence=seq, offset=off)

    if scrub_labels and (dst / "captures").is_dir():
        _scrub_capture_labels(dst / "captures")

    states_file = dst / "vehicle_states.yaml"
    if states_file.exists():
        _strip_vehicle_states(states_file)

    report.residual_leaks = _find_leaks(ecus_dir)
    if states_file.exists() and "when:" in states_file.read_text():
        report.residual_leaks.append("vehicle_states.yaml:when-predicate")
    return report


def _find_leaks(ecus_dir: Path) -> list[str]:
    """Return descriptions of any answer-bearing content still present (self-check)."""
    leaks: list[str] = []
    yaml = yaml_rt.round_trip_yaml()
    for path in sorted(ecus_dir.glob("*.yaml")):
        doc = yaml.load(path.read_text())
        if not doc:
            continue
        for ecu, body in doc.items():
            if not isinstance(body, dict):
                continue
            for sec in (*_STRIP_ECU_SECTIONS, "notes"):
                if sec in body:
                    leaks.append(f"{path.name}:{ecu}:{sec}")
            for pid, pid_def in (body.get("pids") or {}).items():
                if isinstance(pid_def, dict) and ("parameters" in pid_def or "notes" in pid_def):
                    leaks.append(f"{path.name}:{ecu}:{pid}:parameters/notes")
    return leaks


# ── Capture loading + expression evaluation (for target selection + grading) ──────
def load_target_payloads(profile_root: Path, rx: str, pid: str) -> list[bytes]:
    """WiCAN-layout byte arrays for every payload capture matching ``rx``+``pid``.

    Resolves purely by CAN response address (``rx``) — no dependency on the active
    profile or on the PID being defined — so it works identically against a
    stripped sandbox. Unparseable payloads are skipped.
    """
    captures_dir = Path(profile_root) / "captures"
    if not captures_dir.is_dir():
        return []
    want_rx = rx.lower()
    want_pid = str(pid).upper()
    out: list[bytes] = []
    for path in capture_io.iter_capture_files(captures_dir):
        doc = capture_io.load_capture_file(path)
        for sess in doc.get("sessions", []):
            for cap in sess.get("captures", []):
                if capture_io.capture_rx(cap).lower() != want_rx:
                    continue
                if str(cap.get("pid", "")).upper() != want_pid:
                    continue
                payload = cap.get("payload")
                if not payload:
                    continue
                try:
                    out.append(payload_to_wican_bytes(str(payload)))
                except Exception:
                    continue
    return out


def eval_series(expr: str, payloads: list[bytes]) -> list[float | None]:
    """Evaluate ``expr`` against each payload; None where it raises/non-finite."""
    out: list[float | None] = []
    for wb in payloads:
        try:
            v = evaluate_expression(expr, wb)
        except Exception:
            out.append(None)
            continue
        out.append(v if _finite(v) else None)
    return out


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))


def _paired(a: list[float | None], b: list[float | None]) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in zip(a, b, strict=True):
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


# ── Grading ──────────────────────────────────────────────────────────────────────
def grade_answer(
    guess_expr: str,
    truth_param: dict,
    payloads: list[bytes],
    *,
    min_n: int = 8,
) -> dict[str, Any]:
    """Score a guessed expression against ground truth over shared captures.

    ``truth_param`` is the full parameter definition dict (``expression`` plus
    optional ``type``/``values``/``bits``). Returns a dict with ``verdict`` (one
    of the module constants) and the supporting metrics (pearson/spearman, the
    ``y=m·x+c`` fit of guess-vs-truth, sample count, and for typed targets the
    categorical association / exact-match rate).
    """
    truth_expr = str(truth_param.get("expression") or "")
    ttype = truth_param.get("type")
    typed = bool(
        ttype and ttype != "numeric" and (truth_param.get("values") or truth_param.get("bits"))
    )

    guess = eval_series(guess_expr, payloads)
    if all(v is None for v in guess):
        return {
            "verdict": ERROR,
            "n": 0,
            "detail": "guess expression did not evaluate on any capture",
        }

    result: dict[str, Any] = {"typed": typed}

    if typed:
        tcat = [decode_typed(truth_param, wb).category() for wb in payloads]
        # Guess categories: round the numeric guess to an integer code.
        gcat = [str(round(v)) if v is not None else None for v in guess]
        pairs = [(t, g) for t, g in zip(tcat, gcat, strict=True) if t is not None and g is not None]
        n = len(pairs)
        result["n"] = n
        if n < min_n:
            result["verdict"] = INSUFFICIENT
            return result
        ts = [p[0] for p in pairs]
        gs = [p[1] for p in pairs]
        cv = cramers_v(ts, gs)
        # Exact-match rate treats the guess as correct when its raw code
        # partitions the captures identically to the labelled truth.
        result["cramers_v"] = cv
        result["distinct_truth"] = len(set(ts))
        if cv is not None and cv >= 0.95:
            result["verdict"] = CATEGORICAL_MATCH
        elif cv is not None and cv >= 0.6:
            result["verdict"] = STRONG_PARTIAL
        else:
            result["verdict"] = MISS
        return result

    truth = eval_series(truth_expr, payloads)
    xs, ys = _paired(truth, guess)  # xs = truth, ys = guess
    n = len(xs)
    result["n"] = n
    if n < min_n:
        result["verdict"] = INSUFFICIENT
        return result

    exact = all(abs(a - b) <= 1e-6 * (abs(a) + 1.0) for a, b in zip(xs, ys, strict=True))
    r = pearson(xs, ys)
    rho = spearman(xs, ys)
    fit = linear_fit(xs, ys)  # guess ≈ m·truth + c
    result["pearson"] = r
    result["spearman"] = rho
    if fit is not None:
        result["fit_m"], result["fit_c"], result["fit_resid"] = fit

    if exact:
        result["verdict"] = EXACT
    elif r is not None and abs(r) >= 0.999:
        result["verdict"] = EQUIVALENT_SCALE
    elif r is not None and abs(r) >= 0.9:
        result["verdict"] = STRONG_PARTIAL
    elif rho is not None and abs(rho) >= 0.95:
        result["verdict"] = MONOTONE_PARTIAL
    else:
        result["verdict"] = MISS
    return result


PASS_VERDICTS = frozenset({EXACT, EQUIVALENT_SCALE, CATEGORICAL_MATCH})


# ── Target selection ──────────────────────────────────────────────────────────────
def _iter_verified_params(pids_data: dict):
    """Yield (ecu, pid, name, param_dict) for every verified param with an expr."""
    ecus = pids_data.get("ecus", pids_data)
    for ecu, body in ecus.items():
        if not isinstance(body, dict) or "pids" not in body:
            continue
        for pid, pid_def in (body.get("pids") or {}).items():
            if not isinstance(pid_def, dict):
                continue
            for name, pdef in (pid_def.get("parameters") or {}).items():
                if (
                    isinstance(pdef, dict)
                    and pdef.get("verified") is True
                    and pdef.get("expression")
                ):
                    yield ecu, str(pid), name, pdef


def _difficulty(expr: str, pdef: dict) -> float:
    """Heuristic non-triviality weight (higher = more interesting to test)."""
    w = 1.0
    if "[" in expr:  # multi-byte
        w += 2.0
    if "S" in expr:  # signed
        w += 1.0
    if "<<" in expr or ">>" in expr:  # explicit shift (often a PCI-skip)
        w += 2.0
    if ":" in expr and "[" not in expr:  # bit read
        w += 1.0
    if any(op in expr for op in ("*", "/", "-", "+")):  # scaled/offset
        w += 0.5
    if pdef.get("type") and pdef.get("type") != "numeric":  # typed enum/bitmask
        w += 1.5
    return w


def _role_hint(ecu: str, pdef: dict, pids_data: dict) -> str:
    """Coarse physical-quantity hint for a random target (no answer leak)."""
    unit = str(pdef.get("unit") or "")
    cls = _UNIT_HINT.get(unit)
    if not cls:
        cls = "a discrete state/flag" if (pdef.get("type") not in (None, "numeric")) else "a signal"
    return f"{cls} on the {ecu} module"


def select_targets(
    source_root: Path,
    *,
    curated: bool = True,
    n: int | None = None,
    seed: int = 0,
    min_captures: int = 8,
    min_distinct: int = 3,
    max_per_pid: int = 1,
    max_per_ecu: int = 2,
) -> list[Target]:
    """Build the target list (with private ground truth) from the SOURCE profile.

    ``curated=True`` returns the fixed :data:`CURATED` corpus (skipping any whose
    param is missing/unverified). Otherwise draws ``n`` targets by
    difficulty-weighted seeded sampling from all verified params that clear the
    ``min_captures``/``min_distinct`` findability filter. ``max_per_pid`` /
    ``max_per_ecu`` spread a random draw across the vehicle (an ECU like the BMS
    has hundreds of params — without a cap a draw collapses onto its cell
    voltages).
    """
    source_root = Path(source_root)
    pids_data = pids_mod.load_pids(source_root / "ecus")
    index = pids_mod.build_ecu_index(pids_data)

    def _rx_for(ecu: str) -> str | None:
        entry = index.get(ecu.upper()) or index.get(ecu)
        if not entry:
            return None
        rx_id = entry.get("rx_id")
        return f"0x{int(rx_id):X}" if rx_id is not None else None

    def _make(ecu: str, pid: str, name: str, pdef: dict, hint: str) -> Target | None:
        rx = _rx_for(ecu)
        if rx is None:
            return None
        payloads = load_target_payloads(source_root, rx, pid)
        series = [v for v in eval_series(str(pdef["expression"]), payloads) if v is not None]
        distinct = len(set(series))
        return Target(
            ecu=ecu,
            pid=pid,
            name=name,
            expression=str(pdef["expression"]),
            rx=rx,
            role_hint=hint,
            unit=str(pdef.get("unit") or ""),
            type=pdef.get("type"),
            values=pdef.get("values"),
            bits=pdef.get("bits"),
            n_captures=len(series),
            distinct=distinct,
        )

    targets: list[Target] = []
    if curated:
        lookup = {(e, p, nm): pd for e, p, nm, pd in _iter_verified_params(pids_data)}
        for ecu, pid, name, hint in CURATED:
            pdef = lookup.get((ecu, pid, name))
            if pdef is None:
                continue
            t = _make(ecu, pid, name, pdef, hint)
            if t is not None:
                targets.append(t)
        return targets

    # Random draw.
    pool: list[tuple[str, str, str, dict]] = []
    for ecu, pid, name, pdef in _iter_verified_params(pids_data):
        rx = _rx_for(ecu)
        if rx is None:
            continue
        payloads = load_target_payloads(source_root, rx, pid)
        series = [v for v in eval_series(str(pdef["expression"]), payloads) if v is not None]
        if len(series) < min_captures or len(set(series)) < min_distinct:
            continue
        pool.append((ecu, pid, name, pdef))

    want = n if n is not None else 15
    rng = random.Random(seed)
    weights = [_difficulty(str(pd["expression"]), pd) for *_, pd in pool]
    chosen: list[tuple[str, str, str, dict]] = []
    per_pid: dict[tuple[str, str], int] = {}
    per_ecu: dict[str, int] = {}
    pool_idx = list(range(len(pool)))
    while pool_idx and len(chosen) < want:
        pick = rng.choices(pool_idx, weights=[weights[i] for i in pool_idx], k=1)[0]
        pool_idx.remove(pick)
        ecu, pid, _name, _pdef = pool[pick]
        if per_pid.get((ecu, pid), 0) >= max_per_pid or per_ecu.get(ecu, 0) >= max_per_ecu:
            continue
        per_pid[(ecu, pid)] = per_pid.get((ecu, pid), 0) + 1
        per_ecu[ecu] = per_ecu.get(ecu, 0) + 1
        chosen.append(pool[pick])
    for ecu, pid, name, pdef in chosen:
        t = _make(ecu, pid, name, pdef, _role_hint(ecu, pdef, pids_data))
        if t is not None:
            targets.append(t)
    return targets
