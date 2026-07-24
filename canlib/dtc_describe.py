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
annotate each decoded record.
"""

from __future__ import annotations

CATEGORY = {
    "P": "Powertrain",
    "C": "Chassis",
    "B": "Body",
    "U": "Network",
}

# DTCFailureType (ISO 14229-1 Annex D.3 / SAE J2012-DA) — a confident subset.
FAILURE_TYPE = {
    0x00: "no sub-type information",
    0x01: "general electrical failure",
    0x02: "general signal failure",
    0x11: "circuit short to ground",
    0x12: "circuit short to battery",
    0x13: "circuit open",
    0x16: "circuit voltage below threshold",
    0x17: "circuit voltage above threshold",
    0x18: "circuit current below threshold",
    0x19: "circuit current above threshold",
    0x1A: "circuit resistance below threshold",
    0x1B: "circuit resistance above threshold",
    0x1C: "circuit voltage out of range",
    0x21: "signal amplitude below minimum",
    0x22: "signal amplitude above maximum",
    0x29: "signal invalid",
    0x2F: "signal erratic",
    0x31: "no signal",
    0x49: "internal electronic failure",
    0x62: "signal compare failure",
    0x81: "invalid serial data received",
    0x86: "signal invalid (bus)",
    0x87: "missing message",
    0x88: "bus off",
    0x92: "performance or incorrect operation",
    0x96: "component internal failure",
    0x97: "component or system operation obstructed",
    0x98: "component or system over temperature",
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


def describe_dtc(code: str, defs: dict | None = None) -> dict:
    """Return an interpretation of a formatted DTC string.

    Accepts ``"B2915-00"`` (UDS, with failure-type byte) or ``"P0420"`` (KWP /
    no failure type). Reports the standardized structural interpretation
    (category, generic-vs-manufacturer, failure-type byte) and layers on any
    known *meaning*: the profile's definitions first (per-ECU ``dtcs:`` sections
    plus profile-wide ``failure_types:``), then the small bundled generic
    (ISO/SAE) table. Manufacturer meanings are never invented — they come only
    from those curated sources.

    ``defs`` is the profile definitions mapping (``{"dtcs":…, "failure_types":…}``);
    it defaults to the active profile's aggregated definitions (pass ``{}`` for
    none).
    """
    if defs is None:
        defs = _profile_dtc_defs()
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

    # Failure-type meaning: tool's standard table, then profile overrides/additions.
    ftb_desc = None
    if ftb is not None:
        ftb_desc = FAILURE_TYPE.get(ftb) or profile_ftb.get(ftb)

    # Code meaning: profile definition first, then the bundled generic table.
    entry = profile_dtcs.get(base)
    if isinstance(entry, str):
        description = entry
    elif isinstance(entry, dict):
        description = entry.get("description")
    else:
        description = None
    description = description or GENERIC_DTCS.get(base)
    if description:
        description = " ".join(description.split())  # collapse folded-YAML newlines

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


def _profile_dtc_defs() -> dict:
    """Aggregate DTC meanings from the active profile, or {} if unavailable.

    Per-ECU ``dtcs:`` sections (in each ``ecus/<name>.yaml``) are merged into a
    single ``{base_code: entry}`` map; ``failure_types:`` comes from the
    profile-wide ``profile.yaml``.
    """
    try:
        from .pids import load_pids

        data = load_pids()
    except Exception:
        return {}

    dtcs: dict[str, object] = {}
    for _name, ecu_def in (data.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        for code, entry in (ecu_def.get("dtcs") or {}).items():
            dtcs[str(code).upper()] = entry

    ftb: dict[int, str] = {}
    for key, val in (data.get("failure_types") or {}).items():
        try:
            byte = key if isinstance(key, int) else int(str(key), 16)
        except (TypeError, ValueError):
            continue
        ftb[byte] = val

    return {"dtcs": dtcs, "failure_types": ftb}
