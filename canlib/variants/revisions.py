"""ECU revisions: the versions of one ECU that differ in protocol or decoding.

A variant describes the car; a *revision* describes one ECU, because ECUs change on
their own production dates and a dealer-replaced one follows the newer rules in an
older car. An ECU file declares its revisions, the first being the reference::

    VCU:
      tx_id: 0x7E2
      revisions:
        kwp:
          identity: {id_protocol: KWP2000}
          fingerprints: [{responds: "1A90"}]
        uds:
          identity: {id_protocol: UDS}
      variants: [28kwh]            # ECU presence: only these cars have it (absent: all)
      pids:
        "2101":
          revisions: [kwp]         # applicability: only this revision answers it
          parameters: {...}

A revision carries ECU-level overrides from a whitelist (:data:`OVERRIDE_FIELDS`) plus
``fingerprints:`` (stored and checked here, matched by a later stage). A PID or signal
lists the revisions it applies to; absent means every revision.

Which revision an ECU is, most authoritative first (:func:`resolve`):

1. a pin — ``--revision ECU=ID`` or ``profile_settings.<profile>.revisions.<ECU>``;
2. the only one possible — the selected variant allows one, or the ECU declares one;
3. otherwise **undetermined**: no guess. The ECU's revision-gated PIDs and signals are
   left out (:func:`apply`), and its ungated ones still read.

:func:`apply` turns raw definitions into the ones the selected car has, so readers of
``load_pids()`` never see a gate. It is a pure function of (raw definitions, variant,
resolutions), and ECUs that declare neither revisions nor presence come back as the very
same objects — a profile without revisions is untouched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from canlib.variants import id_problem as _id_problem

if TYPE_CHECKING:
    from canlib.variants import Variant

# The ECU fields a revision may override, replaced whole — except ``identity``, of which
# only the protocol may differ (the rest describes the ECU a capture came from).
OVERRIDE_FIELDS: tuple[str, ...] = (
    "identity",
    "sessions",
    "wake",
    "multi_did",
    "multi_did_max",
    "response_timeout_ms",
    "addressing",
    "rx_id",
)
IDENTITY_OVERRIDE_FIELDS: tuple[str, ...] = ("id_protocol",)
REVISION_FIELDS: tuple[str, ...] = (*OVERRIDE_FIELDS, "fingerprints")
# How a revision is recognised: identity fields matching globs, or a safe read only this
# revision answers.
FINGERPRINT_KINDS: tuple[str, ...] = ("identity", "responds")

# Where a pin came from: the global --revision flag or the user config.
PinSource = Literal["flag", "config"]
ResolutionSource = Literal["flag", "config", "only", "detected"]

_HEX_RE = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")


class RevisionError(ValueError):
    """A pin or a reference names a revision that cannot be."""


def normalize_revision_id(value: object) -> str:
    return str(value).strip().lower()


def revision_id_problem(rid: str) -> str | None:
    return _id_problem(rid, "revision", "uds")


def declared(ecu_def: Any) -> list[str]:
    """The ECU's revision IDs in file order (the first is the reference), or []."""
    block = ecu_def.get("revisions") if isinstance(ecu_def, Mapping) else None
    if not isinstance(block, Mapping):
        return []
    return [normalize_revision_id(k) for k in block]


def revision_entry(ecu_def: Mapping, rid: str) -> Mapping:
    block = ecu_def.get("revisions") or {}
    for key, value in block.items():
        if normalize_revision_id(key) == rid:
            return value if isinstance(value, Mapping) else {}
    return {}


def gate(node: Any) -> list[str] | None:
    """A PID's or signal's applicability list, or None when it applies to every revision."""
    value = node.get("revisions") if isinstance(node, Mapping) else None
    if not isinstance(value, list):
        return None
    return [normalize_revision_id(r) for r in value]


def presence(ecu_def: Any) -> list[str] | None:
    """The variants an ECU is fitted to, or None when every variant has it."""
    value = ecu_def.get("variants") if isinstance(ecu_def, Mapping) else None
    if not isinstance(value, list):
        return None
    return [str(v).strip().lower() for v in value]


def has_gates(ecus: Mapping[str, Any]) -> bool:
    """Whether any ECU declares revisions or is fitted only to some variants."""
    return any(declared(d) or presence(d) is not None for d in ecus.values())


# ── resolution ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolution:
    """Which revision one ECU is for the selected car, and how that was decided."""

    ecu: str
    revision: str | None
    source: ResolutionSource | None
    # The revisions this ECU can be on this car, in declaration order.
    possible: tuple[str, ...]

    @property
    def determined(self) -> bool:
        return self.revision is not None


def parse_pin(text: str) -> tuple[str, str]:
    """``ECU=ID`` → ``(ECU, id)``; raises :class:`RevisionError` on another shape."""
    ecu, sep, rid = str(text).partition("=")
    if not sep or not ecu.strip() or not rid.strip():
        raise RevisionError(f"expected ECU=REVISION (e.g. BMS=uds), got {text!r}")
    return ecu.strip(), normalize_revision_id(rid)


def possible_revisions(ecu: str, ecu_def: Mapping, variant: Variant | None) -> tuple[str, ...]:
    """The ECU's revisions the car can carry: all of them, narrowed by the variant."""
    revs = declared(ecu_def)
    allowed = variant.allowed_revisions(ecu) if variant is not None else None
    return tuple(r for r in revs if allowed is None or r in allowed)


def canonical_ecu(name: str, ecus: Mapping[str, Any]) -> str | None:
    """The ECU key ``name`` names, matched case-insensitively."""
    return next((k for k in ecus if str(k).upper() == str(name).strip().upper()), None)


def resolve(
    ecus: Mapping[str, Any],
    variant: Variant | None,
    pins: Iterable[tuple[str, str, PinSource]] = (),
    detections: Iterable[tuple[str, str]] = (),
) -> dict[str, Resolution]:
    """Resolve every ECU that declares revisions (keyed by ECU name).

    A flag pin outranks a config pin for the same ECU. A pin naming an unknown ECU, an
    ECU without revisions, or a revision the ECU does not declare or the car cannot
    carry raises :class:`RevisionError` — never silently ignored, like an undeclared
    variant.
    """
    chosen: dict[str, tuple[str, PinSource]] = {}
    for ecu, rid, source in sorted(pins, key=lambda p: p[2] != "flag"):
        name = canonical_ecu(ecu, ecus)
        origin = "--revision" if source == "flag" else "profile_settings revisions in the config"
        if name is None:
            raise RevisionError(f"{origin} pins {ecu}={rid}, but the profile has no ECU '{ecu}'")
        revs = declared(ecus[name])
        if not revs:
            raise RevisionError(f"{origin} pins {name}={rid}, but {name} declares no revisions")
        if rid not in revs:
            raise RevisionError(
                f"{origin} pins {name}={rid}, which {name} does not declare "
                f"(declared: {', '.join(revs)})"
            )
        chosen.setdefault(name, (rid, source))

    # A detection that names a revision this car can't carry is stale: ignored, not an
    # error (it is remembered state, not something the user typed).
    detected = {str(e).upper(): normalize_revision_id(r) for e, r in detections}
    out: dict[str, Resolution] = {}
    for name, ecu_def in ecus.items():
        if not declared(ecu_def):
            continue
        possible = possible_revisions(name, ecu_def, variant)
        pin = chosen.get(name)
        if pin is not None:
            rid, source = pin
            if rid not in possible:
                car = f"variant {variant.id}" if variant is not None else "this car"
                raise RevisionError(
                    f"{name}={rid} is pinned, but {car} can only carry "
                    f"{', '.join(possible) or 'none'} of {name}'s revisions"
                )
            out[name] = Resolution(name, rid, source, possible)
        elif len(possible) == 1:
            out[name] = Resolution(name, possible[0], "only", possible)
        elif detected.get(name.upper()) in possible:
            out[name] = Resolution(name, detected[name.upper()], "detected", possible)
        else:
            out[name] = Resolution(name, None, None, possible)
    return out


# ── applying a resolution to the definitions ────────────────────────────────


@dataclass(frozen=True)
class Skipped:
    """What an undetermined ECU left out: its revision-gated PIDs and signals."""

    ecu: str
    possible: tuple[str, ...]
    pids: int
    signals: int


def _overridden(ecu_def: Mapping, rid: str) -> dict:
    entry = revision_entry(ecu_def, rid)
    out = {k: v for k, v in ecu_def.items() if k not in ("revisions", "variants")}
    for key in OVERRIDE_FIELDS:
        if key not in entry:
            continue
        if key == "identity":
            identity = dict(out.get("identity") or {})
            identity.update(dict(entry["identity"] or {}))
            out["identity"] = identity
        else:
            out[key] = entry[key]
    return out


# Fields a PID may set per revision (``per_revision: {uds: {...}}``), and a signal.
PID_OVERRIDE_FIELDS: tuple[str, ...] = (
    "period",
    "response_frames",
    "verified",
    "notes",
    "source",
    "source_links",
)
SIGNAL_OVERRIDE_FIELDS: tuple[str, ...] = (
    "expression",
    "unit",
    "min",
    "max",
    "type",
    "values",
    "bits",
    "fields",
    "verified",
    "notes",
    "source",
    "source_links",
)
# PID-level overrides a signal follows when it has none of its own and inherited the
# PID's value (the PID-default fields that are also revision evidence or provenance).
_FOLLOWED_PID_FIELDS: tuple[str, ...] = ("verified", "source", "source_links")
_RESOLVED_KEYS = ("revisions", "per_revision")


def reference_of(applies: Sequence[str] | None, revs: Sequence[str]) -> str | None:
    """An entity's reference revision: the first declared one it applies to.

    A definition's own (base) evidence — ``verified``, ``response_frames`` — speaks for
    this revision; any other one has only what its ``per_revision`` entry says.
    """
    return next((r for r in revs if applies is None or r in applies), None)


def _override(node: Mapping, rid: str | None) -> Mapping:
    block = node.get("per_revision") if isinstance(node, Mapping) else None
    if not isinstance(block, Mapping) or rid is None:
        return {}
    entry = next((v for k, v in block.items() if normalize_revision_id(k) == rid), None)
    return entry if isinstance(entry, Mapping) else {}


def _resolved_pid(pid_def: Mapping, rid: str | None, revs: Sequence[str]) -> dict:
    """One PID of an ECU with revisions, resolved for revision ``rid`` (None: unknown).

    Order: base fields, then evidence the base cannot speak for is dropped (another
    revision's ``verified``/``response_frames``), then the revision's ``per_revision``
    entry replaces fields one by one. An unknown revision keeps no count and verifies
    nothing: either would be a claim about a revision nobody determined.
    """
    pgate = gate(pid_def)
    pref = reference_of(pgate, revs)
    out = {k: v for k, v in pid_def.items() if k not in _RESOLVED_KEYS}
    pover = _override(pid_def, rid)
    if rid != pref:
        out.pop("response_frames", None)
        if "verified" in out:
            out["verified"] = False
    out.update({k: v for k, v in pover.items() if k in PID_OVERRIDE_FIELDS})
    params = pid_def.get("parameters")
    if not isinstance(params, Mapping):
        return out
    kept = {}
    for name, param in params.items():
        if not isinstance(param, Mapping):
            kept[name] = param
            continue
        sgate = gate(param)
        if sgate is not None and (rid is None or rid not in sgate):
            continue
        sref = reference_of(sgate if sgate is not None else pgate, revs)
        new = {k: v for k, v in param.items() if k not in _RESOLVED_KEYS}
        inherited = {
            f for f in _FOLLOWED_PID_FIELDS if f in pid_def and new.get(f) == pid_def.get(f)
        }
        # A value inherited from the PID speaks for the PID's reference revision.
        if rid != sref or ("verified" in inherited and sref != pref):
            new["verified"] = False
        sover = _override(param, rid)
        for f in _FOLLOWED_PID_FIELDS:
            if f in pover and f not in sover and f in inherited:
                new[f] = pover[f]
        new.update({k: v for k, v in sover.items() if k in SIGNAL_OVERRIDE_FIELDS})
        kept[name] = new
    out["parameters"] = kept
    return out


def _gated_pids(pids: Mapping, rid: str | None, revs: Sequence[str]) -> tuple[dict, int, int]:
    """``pids`` resolved for revision ``rid`` (None: undetermined, every gated one left out).

    Returns the PIDs and how many PIDs and signals were left out because they apply only
    to some revisions and ``rid`` is undetermined.
    """
    out: dict = {}
    dropped_pids = dropped_signals = 0
    for code, pid_def in pids.items():
        if not isinstance(pid_def, Mapping):
            out[code] = pid_def
            continue
        pgate = gate(pid_def)
        if pgate is not None and (rid is None or rid not in pgate):
            if rid is None:
                dropped_pids += 1
            continue
        if rid is None:
            params = pid_def.get("parameters")
            if isinstance(params, Mapping):
                dropped_signals += sum(1 for p in params.values() if gate(p) is not None)
        out[code] = _resolved_pid(pid_def, rid, revs)
    return out, dropped_pids, dropped_signals


def apply(
    ecus: Mapping[str, Any],
    variant_id: str | None,
    resolutions: Mapping[str, Resolution],
) -> tuple[dict[str, Any], list[Skipped]]:
    """The definitions the selected car has, and what undetermined ECUs left out.

    * An ECU fitted only to other variants is left out (kept when no car is selected).
    * An ECU with a determined revision gets that revision's overrides and only the PIDs
      and signals that apply to it.
    * An undetermined ECU keeps only its ungated PIDs and signals.

    ``revisions``/``variants`` keys are removed from what is returned: they are resolved.
    """
    out: dict[str, Any] = {}
    skipped: list[Skipped] = []
    for name, ecu_def in ecus.items():
        if not isinstance(ecu_def, Mapping):
            out[name] = ecu_def
            continue
        fitted = presence(ecu_def)
        if variant_id and fitted is not None and variant_id not in fitted:
            continue
        if not declared(ecu_def):
            out[name] = (
                ecu_def if fitted is None else {k: v for k, v in ecu_def.items() if k != "variants"}
            )
            continue
        res = resolutions.get(name)
        rid = res.revision if res is not None else None
        resolved = (
            _overridden(ecu_def, rid)
            if rid
            else {k: v for k, v in ecu_def.items() if k not in ("revisions", "variants")}
        )
        pids, n_pids, n_signals = _gated_pids(ecu_def.get("pids") or {}, rid, declared(ecu_def))
        if "pids" in ecu_def:
            resolved["pids"] = pids
        if rid is None and (n_pids or n_signals):
            possible = res.possible if res is not None else tuple(declared(ecu_def))
            skipped.append(Skipped(name, possible, n_pids, n_signals))
        out[name] = resolved
    return out, skipped


def skipped_note(
    profile_name: str, skipped: Iterable[Skipped], unchosen: Sequence[str] = ()
) -> str:
    """The one message naming what undetermined ECUs left out, and the fix.

    ``unchosen`` are the profile's variants when none is selected: then the fix is to
    name the car, which settles every ECU its variant allows one revision of. A pin is
    the fix when the chosen car itself allows several.
    """
    lines = []
    skipped = list(skipped)
    for s in skipped:
        what = ", ".join(
            p
            for p in (
                f"{s.pids} PID(s)" if s.pids else "",
                f"{s.signals} signal(s)" if s.signals else "",
            )
            if p
        )
        line = (
            f"note: {s.ecu} could be any of {', '.join(s.possible)}, so its {what} specific to "
            "one revision are skipped."
        )
        if not unchosen:
            example = s.possible[0] if s.possible else "ID"
            line += (
                f" Pin yours: canair profile use {profile_name} --pin {s.ecu}={example} "
                f"(or --revision {s.ecu}={example} for one command)."
            )
        lines.append(line)
    if unchosen and skipped:
        lines.append(
            f"  No car is chosen ({', '.join(unchosen)}). Name yours: canair profile use "
            f"{profile_name} --variant {unchosen[0]} (or --variant ID for one command)."
        )
    return "\n".join(lines)


_SOURCE_LABEL = {
    "flag": "--revision",
    "config": "a pin in the config",
    "only": "the only one",
    "detected": "detected from the car",
}


def describe_resolution(res: Resolution) -> str:
    """One line: which revision the ECU is and why, or that it is undetermined."""
    if res.revision is not None:
        return f"{res.revision}  (from {_SOURCE_LABEL.get(res.source or 'only', res.source)})"
    return f"undetermined — could be {' or '.join(res.possible) or 'none'}; pin it with --pin"


# ── combinations (for checks that must hold on every car) ───────────────────


def reachable_combinations(
    ecus: Mapping[str, Any], variants: Mapping[str, Variant]
) -> list[tuple[str | None, dict[str, str]]]:
    """Every (variant, {ECU: revision}) the profile can describe.

    One entry per variant (or one with no variant when none is declared), crossed with
    each revision every ECU can carry on it. Bounded by each variant's ``revisions:``.
    """
    from itertools import product

    cars: list[Variant | None] = list(variants.values()) or [None]
    out: list[tuple[str | None, dict[str, str]]] = []
    for car in cars:
        names: list[str] = []
        choices: list[tuple[str, ...]] = []
        for name, ecu_def in ecus.items():
            fitted = presence(ecu_def)
            if car is not None and fitted is not None and car.id not in fitted:
                continue
            possible = possible_revisions(name, ecu_def, car)
            if possible:
                names.append(name)
                choices.append(possible)
        for combo in product(*choices) if choices else [()]:
            out.append((car.id if car else None, dict(zip(names, combo, strict=True))))
    return out


def definitions_for(
    ecus: Mapping[str, Any], variant: Variant | None, revisions: Mapping[str, str]
) -> dict[str, Any]:
    """The definitions one fully determined car has (every revision given)."""
    resolutions = {name: Resolution(name, rid, "only", (rid,)) for name, rid in revisions.items()}
    resolved, _ = apply(ecus, variant.id if variant else None, resolutions)
    return resolved


# ── checks ──────────────────────────────────────────────────────────────────


def _fingerprint_problem(fp: Any, identity_fields: set[str]) -> str | None:
    if not isinstance(fp, Mapping) or len(fp) != 1:
        return f"each fingerprint is one of {', '.join(FINGERPRINT_KINDS)}"
    kind, value = next(iter(fp.items()))
    if kind == "responds":
        if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
            return 'responds: must be a quoted hex request (whole bytes, e.g. "1A90")'
        return None
    if kind == "identity":
        if not isinstance(value, Mapping) or not value:
            return "identity: must map identity fields to glob patterns"
        for field_name, pattern in value.items():
            if identity_fields and field_name not in identity_fields:
                return f"identity: unknown identity field '{field_name}'"
            if not isinstance(pattern, str) or not pattern.strip():
                return f"identity: '{field_name}' must be a text pattern"
        return None
    return f"unknown fingerprint kind '{kind}' (known: {', '.join(FINGERPRINT_KINDS)})"


def check_ecu(
    ecu_name: str,
    ecu_def: Mapping,
    *,
    variant_ids: Iterable[str] | None = None,
    identity_fields: set[str] | None = None,
    valid_protocols: set[str] | None = None,
) -> list[str]:
    """Every problem with one ECU's revisions, presence and gates (empty when valid).

    ``variant_ids`` are the profile's declared variants; None skips the presence check
    (nothing to check against).
    """
    errors: list[str] = []
    label = ecu_name
    block = ecu_def.get("revisions")
    revs: list[str] = []
    if block is not None:
        if not isinstance(block, Mapping) or not block:
            errors.append(f"{label}: revisions: must map revision IDs to their overrides")
        else:
            for raw_id, entry in block.items():
                rid = normalize_revision_id(raw_id)
                where = f"{label}/revisions/{raw_id}"
                if problem := revision_id_problem(rid):
                    errors.append(f"{where}: {problem}")
                if rid in revs:
                    errors.append(f"{where}: duplicate revision ID")
                revs.append(rid)
                errors += _entry_errors(where, entry, identity_fields or set(), valid_protocols)

    fitted = ecu_def.get("variants")
    if fitted is not None:
        if not isinstance(fitted, list) or not fitted:
            errors.append(f"{label}: variants: must list the variants this ECU is fitted to")
        else:
            ids = [str(v).strip().lower() for v in fitted]
            if len(set(ids)) != len(ids):
                errors.append(f"{label}: variants: lists a variant twice")
            if variant_ids is not None:
                known = set(variant_ids)
                if not known:
                    errors.append(f"{label}: variants: is set, but the profile declares none")
                errors += [
                    f"{label}: variants: names '{v}', which variants.yaml does not declare"
                    for v in ids
                    if known and v not in known
                ]

    pids = ecu_def.get("pids")
    for code, pid_def in pids.items() if isinstance(pids, Mapping) else ():
        if not isinstance(pid_def, Mapping):
            continue
        pgate, perr = _gate_errors(f"{label}/{code}", pid_def, revs)
        errors += perr
        errors += _override_errors(f"{label}/{code}", pid_def, pgate, revs, PID_OVERRIDE_FIELDS)
        params = pid_def.get("parameters")
        for pname, param in params.items() if isinstance(params, Mapping) else ():
            if not isinstance(param, Mapping):
                continue
            sgate, serr = _gate_errors(f"{label}/{code}/{pname}", param, revs)
            errors += serr
            errors += _override_errors(
                f"{label}/{code}/{pname}",
                param,
                sgate if sgate is not None else pgate,
                revs,
                SIGNAL_OVERRIDE_FIELDS,
            )
            if sgate is not None and pgate is not None:
                outside = [r for r in sgate if r not in pgate]
                if outside:
                    errors.append(
                        f"{label}/{code}/{pname}: revisions [{', '.join(outside)}] are not "
                        f"among its PID's [{', '.join(pgate)}] — a signal can only apply "
                        "where its PID does"
                    )
    return errors


def _entry_errors(
    where: str, entry: Any, identity_fields: set[str], valid_protocols: set[str] | None
) -> list[str]:
    if entry is None:
        return []
    if not isinstance(entry, Mapping):
        return [f"{where}: must be a mapping of overrides and fingerprints"]
    errors = [
        f"{where}: '{k}' cannot differ by revision (only: {', '.join(REVISION_FIELDS)})"
        for k in entry
        if k not in REVISION_FIELDS
    ]
    identity = entry.get("identity")
    if identity is not None:
        if not isinstance(identity, Mapping):
            errors.append(f"{where}: identity: must be a mapping")
        else:
            errors += [
                f"{where}: identity.{k} cannot differ by revision (only id_protocol)"
                for k in identity
                if k not in IDENTITY_OVERRIDE_FIELDS
            ]
            proto = identity.get("id_protocol")
            if valid_protocols and proto is not None and proto not in valid_protocols:
                errors.append(f"{where}: invalid id_protocol '{proto}'")
    fps = entry.get("fingerprints")
    if fps is not None:
        if not isinstance(fps, list):
            errors.append(f"{where}: fingerprints: must be a list")
        else:
            for i, fp in enumerate(fps):
                if problem := _fingerprint_problem(fp, identity_fields):
                    errors.append(f"{where}/fingerprints[{i}]: {problem}")
    return errors


def _gate_errors(where: str, node: Mapping, revs: list[str]) -> tuple[list[str] | None, list[str]]:
    value = node.get("revisions")
    if value is None:
        return None, []
    if not isinstance(value, list) or not value:
        return None, [f"{where}: revisions: must list at least one revision ID"]
    ids = [normalize_revision_id(r) for r in value]
    errors = []
    if len(set(ids)) != len(ids):
        errors.append(f"{where}: revisions: lists a revision twice")
    if not revs:
        errors.append(f"{where}: revisions: is set, but its ECU declares no revisions")
    else:
        errors += [
            f"{where}: revisions: names '{r}', which its ECU does not declare "
            f"(declared: {', '.join(revs)})"
            for r in ids
            if r not in revs
        ]
    return ids, errors


def _override_errors(
    where: str,
    node: Mapping,
    applies: list[str] | None,
    revs: list[str],
    allowed: tuple[str, ...],
) -> list[str]:
    """Problems with a PID's or signal's ``per_revision:`` block."""
    block = node.get("per_revision")
    if block is None:
        return []
    if not isinstance(block, Mapping) or not block:
        return [f"{where}: per_revision: must map revision IDs to the fields that differ"]
    if not revs:
        return [f"{where}: per_revision: is set, but its ECU declares no revisions"]
    errors = []
    for raw_id, entry in block.items():
        rid = normalize_revision_id(raw_id)
        at = f"{where}/per_revision/{raw_id}"
        if rid not in revs:
            errors.append(f"{at}: its ECU declares no revision '{rid}'")
            continue
        if applies is not None and rid not in applies:
            errors.append(
                f"{at}: it applies only to [{', '.join(applies)}], so an override for "
                f"'{rid}' is never read"
            )
        if not isinstance(entry, Mapping) or not entry:
            errors.append(f"{at}: must be a mapping of the fields that differ")
            continue
        for key in entry:
            if key in ("revisions", "per_revision", "parameters"):
                errors.append(
                    f"{at}: '{key}' cannot be overridden"
                    + (" (override each signal instead)" if key == "parameters" else "")
                )
            elif key not in allowed:
                errors.append(
                    f"{at}: '{key}' cannot differ by revision (only: {', '.join(allowed)})"
                )
        verified = entry.get("verified")
        if verified is not None and not isinstance(verified, bool):
            errors.append(f"{at}: verified must be a boolean")
        frames = entry.get("response_frames")
        if frames is not None and (
            isinstance(frames, bool) or not isinstance(frames, int) or frames < 1
        ):
            errors.append(f"{at}: response_frames must be an int >= 1")
        expr = entry.get("expression")
        if expr is not None and (not isinstance(expr, str) or not expr.strip()):
            errors.append(f"{at}: expression must be non-empty text")
    return errors


def owner_revisions(ecu_def: Mapping, pid_def: Mapping, param: Mapping) -> frozenset[str]:
    """The revisions a signal is defined on: its gate, else its PID's, else the ECU's."""
    for node in (param, pid_def):
        g = gate(node)
        if g is not None:
            return frozenset(g)
    return frozenset(declared(ecu_def))


def variant_reference_errors(variants: Mapping[str, Variant], ecus: Mapping[str, Any]) -> list[str]:
    """Every ECU or revision a variant's ``revisions:`` names that the ECU files lack."""
    errors: list[str] = []
    for vid, variant in variants.items():
        for ecu, revs in variant.revisions.items():
            where = f"variant '{vid}': revisions: {ecu}"
            ecu_def = ecus.get(ecu)
            if ecu_def is None:
                match = canonical_ecu(ecu, ecus)
                hint = f" (did you mean {match}?)" if match else ""
                errors.append(f"{where}: no ECU '{ecu}' in ecus/{hint}")
                continue
            have = declared(ecu_def)
            if not have:
                errors.append(f"{where}: {ecu} declares no revisions")
                continue
            errors += [
                f"{where}: {ecu} declares no revision '{r}' (declared: {', '.join(have)})"
                for r in revs
                if r not in have
            ]
    return errors
