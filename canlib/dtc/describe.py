"""Structural interpretation of DTC codes (category, control, failure type).

Manufacturer-specific code *text* (e.g. what Hyundai means by ``B2915``) is not
encoded in the DTC itself and lives in vehicle references, not here. What *is*
standardized — and what this module decodes — is:

* the category letter (P/C/B/U → Powertrain/Chassis/Body/Network),
* whether the code is generic (ISO/SAE) or manufacturer-specific (SAE J2012
  second-character convention), and
* the failure-type byte / DTCFailureType (ISO 14229-1 Annex D.3 / SAE J2012-DA),
  the ``-XX`` suffix on a 3-byte UDS DTC.

Keep this pure and data-driven; ``modes/dtc.py`` calls :func:`describe_dtc` to
annotate each decoded record, naming the ECU that reported it.
"""

from __future__ import annotations

from .failure_types import FAILURE_TYPE

CATEGORY = {
    "P": "Powertrain",
    "C": "Chassis",
    "B": "Body",
    "U": "Network",
}

# A small set of well-known *generic* (ISO/SAE) codes. Manufacturer-specific
# meanings belong in vehicle references, not this shared table — do not seed
# guesses here.
GENERIC_DTCS = {
    "P0100": "Mass or Volume Air Flow circuit",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0420": "Catalyst system efficiency below threshold (Bank 1)",
    "U0100": "Lost communication with ECM/PCM",
    "U0101": "Lost communication with TCM",
    "U0111": "Lost communication with battery energy control module",
}


def dtc_kind(letter: str, first_digit: int | None) -> str:
    """Classify a DTC as generic (ISO/SAE) or manufacturer-specific.

    Uses the SAE J2012 second-character convention: for C/B/U codes digit 0 is
    generic and 1/2 are manufacturer-specific (3 reserved); for P codes 0/2 are
    generic and 1/3 manufacturer-specific.
    """
    if first_digit is None:
        return "unknown"
    if letter == "P":
        return "generic (ISO/SAE)" if first_digit in (0, 2) else "manufacturer-specific"
    # C, B, U
    return {
        0: "generic (ISO/SAE)",
        1: "manufacturer-specific",
        2: "manufacturer-specific",
        3: "reserved",
    }.get(first_digit, "unknown")


def describe_dtc(code: str, defs: dict | None = None, *, ecu: str | None = None) -> dict:
    """Return an interpretation of a formatted DTC string.

    Accepts ``"B2915-00"`` (UDS, with failure-type byte) or ``"P0420"`` (KWP /
    no failure type). Reports the standardized structural interpretation
    (category, generic-vs-manufacturer, failure-type byte) and layers on any
    known *meaning*: the profile's definitions first (the reporting ECU's
    ``dtcs:`` section, then the profile-wide ``dtcs:`` in ``profile.yaml``, plus
    the profile-wide ``failure_types:``), then the small bundled generic (ISO/SAE)
    table. Manufacturer meanings are never invented — they come only from those
    curated sources.

    A manufacturer code means what the ECU that reported it says it means — two
    units can give one code different meanings — so pass ``ecu`` (the ECU's
    name). ``defs`` is an already-resolved definitions mapping
    (``{"dtcs":…, "failure_types":…}``); it defaults to :func:`profile_dtc_defs`
    for ``ecu`` (pass ``{}`` for none).
    """
    if defs is None:
        defs = profile_dtc_defs(ecu)
    profile_dtcs = defs.get("dtcs", {})
    profile_ftb = defs.get("failure_types", {})

    code = (code or "").strip().upper()
    base, _, ftb_hex = code.partition("-")
    ftb = None
    if ftb_hex:
        try:
            ftb = int(ftb_hex, 16)
        except ValueError:
            ftb = None

    letter = base[0] if base else "?"
    category = CATEGORY.get(letter, "unknown")
    try:
        first_digit = int(base[1])
    except (IndexError, ValueError):
        first_digit = None
    kind = dtc_kind(letter, first_digit)

    # Failure-type meaning: the standard table, then the profile's manufacturer-specific additions.
    ftb_desc = None
    if ftb is not None:
        ftb_desc = FAILURE_TYPE.get(ftb) or profile_ftb.get(ftb)

    # Code meaning: profile definition first, then the bundled generic table.
    description = _description(profile_dtcs.get(base)) or GENERIC_DTCS.get(base)

    base_summary = description or " · ".join(p for p in (category, kind) if p and p != "unknown")
    parts = [base_summary]
    if ftb is not None and ftb != 0x00:
        parts.append(f"FTB 0x{ftb:02X}: {ftb_desc}" if ftb_desc else f"failure type 0x{ftb:02X}")
    meaning = " · ".join(parts)

    return {
        "category": category,
        "kind": kind,
        "failure_type": f"0x{ftb:02X}" if ftb is not None else None,
        "failure_type_desc": ftb_desc,
        "description": description,
        "meaning": meaning,
    }


def profile_dtc_defs(ecu: str | None = None) -> dict:
    """The active profile's DTC meanings for ``ecu``, or {} if unavailable.

    ``dtcs`` is the profile-wide ``dtcs:`` (in ``profile.yaml``, for codes that mean the
    same on every unit) overlaid with that ECU's own ``dtcs:`` (in ``ecus/<name>.yaml``,
    matched case-insensitively), so an ECU's entry wins; another ECU's meaning is never
    borrowed. With no ECU, a code is described only when every meaning it can have —
    each ECU's entry, and the profile-wide one — agrees: otherwise the answer would
    depend on which unit reported it.
    ``failure_types`` comes from the profile-wide ``profile.yaml``.
    """
    index = _profile_dtc_index()
    if not index:
        return {}
    shared, by_ecu, ftb = index
    if ecu is not None:
        own = next((d for name, d in by_ecu.items() if name.upper() == ecu.upper()), {})
        dtcs = {**shared, **own}
    else:
        dtcs = _agreed([shared, *by_ecu.values()])
    return {"dtcs": dtcs, "failure_types": ftb}


def _description(entry: object) -> str | None:
    """An entry's text (a bare string or a ``description:``), folded-YAML newlines collapsed."""
    text = entry.get("description") if isinstance(entry, dict) else entry
    return " ".join(str(text).split()) if text else None


def _agreed(sections: list[dict[str, object]]) -> dict[str, object]:
    """The codes whose meaning is the same in every section that defines them."""
    out: dict[str, object] = {}
    conflicting: set[str] = set()
    for dtcs in sections:
        for code, entry in dtcs.items():
            if code in conflicting:
                continue
            if code in out and _description(out[code]) != _description(entry):
                conflicting.add(code)
                del out[code]
            else:
                out.setdefault(code, entry)
    return out


_DtcIndex = tuple[dict[str, object], dict[str, dict[str, object]], dict[int, str]]
_index_cache: tuple[object, _DtcIndex] | None = None


def _codes(dtcs: object) -> dict[str, object]:
    """A loaded ``dtcs:`` section's entries, keyed by upper-cased base code."""
    return {str(code).upper(): e for code, e in dtcs.items()} if isinstance(dtcs, dict) else {}


def _profile_dtc_index() -> _DtcIndex | None:
    """``(profile-wide {code: entry}, {ECU: {code: entry}}, {failure_type_byte: text})``."""
    global _index_cache
    try:
        from canlib.pids import load_pids

        data = load_pids()
    except Exception:
        return None
    if _index_cache is not None and _index_cache[0] is data:
        return _index_cache[1]

    by_ecu: dict[str, dict[str, object]] = {}
    for name, ecu_def in (data.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        dtcs = _codes(ecu_def.get("dtcs"))
        if dtcs:
            by_ecu[str(name)] = dtcs

    ftb: dict[int, str] = {}
    for key, val in (data.get("failure_types") or {}).items():
        try:
            byte = key if isinstance(key, int) else int(str(key), 16)
        except (TypeError, ValueError):
            continue
        ftb[byte] = val

    # The profile-wide section comes straight from profile.yaml, so its fields stated
    # once (source, verified, ...) are resolved here; the loader did the ECUs' already.
    from canlib.dtc.definitions import effective_entries

    raw_shared = data.get("dtcs")
    shared = _codes(effective_entries(raw_shared)) if isinstance(raw_shared, dict) else {}
    index: _DtcIndex = (shared, by_ecu, ftb)
    _index_cache = (data, index)
    return index
