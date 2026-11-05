"""captures/ validation — payload files vs captures_schema.json + soft warnings."""

import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from canair.captures import io as capture_io
from canair.states.implies import contradictory_states
from canair.states.predicates import StatePredicateError, StateRule
from canair.states.vocabulary import load_states, state_names
from canair.uds.memory import MEMORY_SIGNAL_MAX_BYTES, exceeds_signal_cap

from ._common import CAPTURES_SCHEMA_FILE, DEPRECATED_FIELDS
from .capture_schema import CaptureSchemaChecker
from .report import CapturesReport, CaptureWarning, FileFindings
from .variant_tags import variant_tag_report


def load_valid_rx_addrs() -> set[int]:
    """Set of valid ECU CAN response addresses (each ECU's resolved ``rx_id``)."""
    from canair.ecus.registry import build_rx_index

    return set(build_rx_index())


def _path_str(abs_path) -> str:
    """Render a jsonschema error path (deque) as e.g. sessions[0].captures[3].ecu."""
    out = ""
    for part in abs_path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "<root>"


def validate_captures_file(
    path: Path, validator: Validator | CaptureSchemaChecker, rx_addrs: set[int]
) -> list[str]:
    from canair.ecus.registry import SENTINELS, parse_ecu_ref

    if not isinstance(validator, CaptureSchemaChecker):
        validator = CaptureSchemaChecker(validator.schema)
    errors: list[str] = []
    data = capture_io.load_capture_file(path)

    # Schema validation (structure, types, required/allowed fields, patterns).
    for at, err in sorted(validator.errors(data), key=lambda found: found[0]):
        loc = _path_str(at)
        # Nicer message for known-deprecated fields flagged by additionalProperties.
        dep = DEPRECATED_FIELDS & set(err.instance) if isinstance(err.instance, dict) else set()
        if err.validator == "additionalProperties" and dep:
            errors.append(f"{loc}: deprecated field(s): {sorted(dep)}")
        else:
            errors.append(f"{loc}: {err.message}")

    # Cross-file / cross-field checks not expressible in JSON Schema.
    if isinstance(data, dict):
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            date = session.get("date", "")
            if date:
                # Schema checks the shape; verify the leading date is a real
                # calendar date (a "-<suffix>" for same-day sessions is allowed).
                try:
                    datetime.strptime(str(date)[:10], "%Y-%m-%d")
                except ValueError:
                    errors.append(f"sessions[{si}]: date '{date}' is not a valid calendar date")
                if path.stem != date:
                    errors.append(
                        f"sessions[{si}]: date '{date}' doesn't match filename '{path.stem}'"
                    )

            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                ecu = capture_io.capture_rx(cap) or None
                if ecu and str(ecu).lower() not in SENTINELS:
                    rx = parse_ecu_ref(ecu)
                    if rx is not None and rx not in rx_addrs:
                        errors.append(
                            f"sessions[{si}].captures[{ci}].rx: response address "
                            f"'{ecu}' is not a known ECU response address"
                        )

    return errors


def check_captures(
    strict: bool = False,
    max_untimed: int | None = None,
    show_untimed: bool = False,
    max_untagged: int | None = None,
) -> CapturesReport:
    """Check every capture file of the active profile: errors first, then the soft warnings."""
    # Every check below reads the same files; parse each one once.
    with capture_io.reading_once():
        return _check_captures(strict, max_untimed, show_untimed, max_untagged)


def _check_captures(
    strict: bool, max_untimed: int | None, show_untimed: bool, max_untagged: int | None
) -> CapturesReport:
    with open(CAPTURES_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = CaptureSchemaChecker(schema)
    rx_addrs = load_valid_rx_addrs()

    from canair.profile import active

    prof = active()
    # Both layers are validated: a layered profile's base holds captures the user
    # cannot edit, but a schema break there still breaks every analysis, so
    # `validate captures` must report it rather than only auditing the overlay.
    files: list[Path] = []
    for layer in prof.capture_layers:
        if not layer.is_dir():
            continue
        capture_io.ensure_migrated(layer)
        files.extend(capture_io.iter_capture_files(layer))

    report = CapturesReport(strict=strict, max_untimed=max_untimed, show_untimed=show_untimed)
    if not files:
        return report
    report.n_files = len(files)

    # State vocabulary for soft warnings (empty when no vehicle_states.yaml → no warnings).

    vocab = {n.lower() for n in state_names()}

    # excludes: declarations, for the sequenced-session warning. Empty (or a
    # profile whose states fail to load) simply disables that check.

    try:
        state_rules = [r for r in load_states() if r.excludes]
    except StatePredicateError:
        state_rules = []

    # Cross-file: a reply saved under the wrong ECU, or a multi-DID reply saved whole under one
    # DID, only stands out against the whole corpus. Load it once for both checks.
    from canair.captures.payload_corpus import load_payload_corpus

    from .link_events import link_event_warnings
    from .misfiled import misfile_warnings
    from .unsplit import unsplit_batch_warnings

    corpus = load_payload_corpus(files)
    misfiled = misfile_warnings(files, corpus=corpus)
    unsplit = unsplit_batch_warnings(files, corpus=corpus)

    from canair.captures.sensitive_purge import sensitive_findings
    from canair.privacy.sensitive import profile_set

    sset = profile_set(prof)
    for path in files:
        errors = validate_captures_file(path, validator, rx_addrs)
        errors += sensitive_findings(path, sset)
        errors += _oversized_memory_errors(path)
        warnings: list[CaptureWarning] = (
            _capture_state_warnings(path, vocab, state_rules) if vocab else []
        )
        warnings += _capture_echo_warnings(path)
        warnings += _capture_nonhex_warnings(path)
        warnings += _capture_quality_warnings(path)
        warnings += link_event_warnings(path)
        warnings += misfiled.get(path, [])
        warnings += unsplit.get(path, [])
        # Missing-time on payload captures: an error under --strict (new-data
        # gate). Otherwise the grandfathered rows are a single tracked COUNT (the
        # footer + the --max-untimed ratchet), not a per-file warning stream — 284
        # identical untimed lines otherwise drown the handful of echo/quality
        # warnings that need attention. --show-untimed opts back into per-file detail.
        time_gaps = _capture_missing_time_warnings(path)
        report.total_time_gaps += len(time_gaps)
        strict_gaps = time_gaps if strict else []
        if not strict and show_untimed:
            warnings += time_gaps
        found = FileFindings(path.name, errors, strict_gaps, warnings)
        # Only files with something to report are kept — a clean profile can
        # hold hundreds of capture files, so the "OK" lines would bury the signal.
        if not found.error_count and not warnings:
            continue
        report.files.append(found)
        report.total_errors += found.error_count
        report.total_warnings += len(warnings)

    report.tags = variant_tag_report(files, prof, max_untagged)
    report.total_warnings += report.tags.warnings
    return report


def _capture_state_warnings(
    path: Path, vocab: set[str], rules: list[StateRule] | None = None
) -> list[CaptureWarning]:
    """Soft warnings for a session's vehicle_states: vocabulary, then exclusivity.

    A session's ``vehicle_states`` is a list of tokens (e.g. [ready, parked]); a
    session is flagged only when *none* of its tokens is a known state name
    (case-insensitive). Never an error — this only nudges toward the
    standardized vehicle_states.yaml vocabulary.

    The second check needs the profile's ``excludes:`` declarations. A session
    tagged with a pair that cannot hold at one instant did not overlap, it
    *sequenced* — so its tokens describe a span of time rather than a reading,
    and every capture in it currently answers for states it was never in. That
    is only a warning while the session carries no ``state_spans`` timeline to
    resolve each capture against; once it does, the pair is expected and
    correct.
    """
    warnings: list[CaptureWarning] = []
    exclusive = list(rules or [])
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        states = session.get("vehicle_states")
        if not states:
            continue
        if not isinstance(states, list):
            warnings.append(CaptureWarning(f"sessions[{si}]", "vehicle_states must be a list"))
            continue
        tokens = [str(t).strip().lower() for t in states if str(t).strip()]
        if tokens and not any(t in vocab for t in tokens):
            warnings.append(
                CaptureWarning(
                    f"sessions[{si}]",
                    f"vehicle_states {states} has no token in the vehicle_states.yaml vocabulary",
                )
            )
        if exclusive and not _has_spans(session):
            for a, b in contradictory_states(exclusive, states):
                warnings.append(
                    CaptureWarning(
                        f"sessions[{si}]",
                        f"vehicle_states has the mutually exclusive pair {a}+{b} but no "
                        "state_spans timeline — every capture in it answers for both; "
                        "run: canair captures uds --backfill-state-spans",
                    )
                )
    return warnings


def _has_spans(session: dict) -> bool:
    """Whether a session carries a non-empty ``state_spans`` timeline."""
    block = session.get("state_spans")
    return isinstance(block, dict) and bool(block.get("spans"))


def _capture_quality_warnings(path: Path) -> list[CaptureWarning]:
    """Soft warnings for sessions recorded with dropped/stale ISO-TP frames.

    A session's ``quality`` footprint (written at capture time from the transport
    diagnostics recorder) records ``drop``/``stale`` counts — dropped, duplicated,
    out-of-order, or truncated multi-frame reads, and stale frames leaking between
    requests. Those are exactly the faults that silently corrupted multi-frame
    payloads before the ISO-TP reassembly hardening, so a non-zero count is a
    trustworthiness flag on that session's multi-frame captures. Reported as a
    warning, never an error — the payloads that survived the guards are fine; this
    only nudges you to re-capture if a multi-frame PID looks off. ``no_data``/
    ``bus``/``decode`` are non-answers (nothing was stored), so they don't warn.
    """
    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        quality = session.get("quality")
        if not isinstance(quality, dict):
            continue
        drops = (quality.get("drop", 0) or 0) + (quality.get("stale", 0) or 0)
        if drops:
            transport = session.get("transport", "?")
            warnings.append(
                CaptureWarning(
                    f"sessions[{si}]",
                    f"recorded {drops} dropped/stale ISO-TP frame(s) during capture "
                    f"(transport={transport}) — multi-frame payloads in this session "
                    "may be unreliable",
                )
            )
    return warnings


def _capture_echo_warnings(path: Path) -> list[CaptureWarning]:
    """Soft warnings for captures whose payload doesn't echo their recorded PID.

    A UDS positive response echoes the request SID (+0x40) and identifier bytes;
    a ``6101…`` payload stored under a ``2102`` request is a stale/misfiled frame
    (the ELM327 leaks a previous request's late response into the next read — see
    ``canair.uds.payload.payload_echo_mismatch``). Reported as a warning, never an error,
    since free-form/raw captures and multi-frame quirks shouldn't hard-fail.
    """
    from canair.uds.payload import payload_echo_mismatch

    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            pid = cap.get("pid")
            payload = cap.get("payload")
            if not pid or not payload:
                continue
            reason = payload_echo_mismatch(str(pid), str(payload))
            if reason:
                warnings.append(
                    CaptureWarning(
                        f"sessions[{si}].captures[{ci}] "
                        f"({capture_io.capture_rx(cap) or '?'} {pid} @ {cap.get('time', '?')})",
                        reason,
                    )
                )
    return warnings


def _oversized_memory_errors(path: Path) -> list[str]:
    """One error per stored memory reply over the signal cap: a dump, which stays local.

    Nothing writes one (:mod:`canair.privacy.sensitive` withholds it at save), so a hit is a
    file from an older canair or a hand edit. ``contribute`` runs this check, so a dump
    can never be shared through ``captures/``.
    """
    found: list[str] = []
    data = capture_io.load_capture_file(path)
    for si, session in enumerate((data or {}).get("sessions") or []):
        caps = session.get("captures") if isinstance(session, dict) else None
        for ci, cap in enumerate(caps or []):
            if isinstance(cap, dict) and cap.get("payload") and exceeds_signal_cap(cap.get("pid")):
                found.append(
                    f"sessions[{si}].captures[{ci}]: memory read {cap.get('pid')} holds more than "
                    f"{MEMORY_SIGNAL_MAX_BYTES} bytes — a dump; remove it with "
                    "`canair captures uds --delete`"
                )
    return found


def _capture_nonhex_warnings(path: Path) -> list[CaptureWarning]:
    """Soft warnings for captures whose payload isn't a valid UDS byte string.

    Payloads are recorded by the tool as raw response hex, so a non-hex one
    (an ELM327 status string like ``NO DATA``, free-text notes, or a mixed
    hex+ASCII transcription) signals a mis-recorded capture — see
    ``canair.uds.payload.payload_not_hex``. Reported as a warning, never an error.
    """
    from canair.uds.payload import payload_not_hex

    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            payload = cap.get("payload")
            if not payload:
                continue
            reason = payload_not_hex(str(payload))
            if reason:
                warnings.append(
                    CaptureWarning(
                        f"sessions[{si}].captures[{ci}] "
                        f"({capture_io.capture_rx(cap) or '?'} {cap.get('pid', '?')} "
                        f"@ {cap.get('time', '?')})",
                        reason,
                    )
                )
    return warnings


def _capture_missing_time_warnings(path: Path) -> list[CaptureWarning]:
    """Soft warnings for time-series (``payload``) captures with no usable ``time``.

    A payload capture is a time-series sample and should carry a timestamp so
    cross-signal time-alignment (``canair correlate``/``hunt``) can use it. One-shot
    ``scan_results``/``response`` captures are exempt — a timestamp was never
    meaningful there. Existing untimed payload rows are grandfathered (warning,
    not error, unless ``validate captures --strict``). See Tranche 2.6.
    """
    from canair.captures.dates import entry_datetime

    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        date = session.get("date", "")
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict) or not cap.get("payload"):
                continue  # only payload (time-series) captures; scans exempt
            # entry_datetime needs the session date + capture time.
            if entry_datetime({"date": date, "time": cap.get("time", "")}) is None:
                warnings.append(
                    CaptureWarning(
                        f"sessions[{si}].captures[{ci}] "
                        f"({capture_io.capture_rx(cap) or '?'} {cap.get('pid', '?')})",
                        "payload capture has no usable time (excluded from time-aligned analysis)",
                    )
                )
    return warnings
