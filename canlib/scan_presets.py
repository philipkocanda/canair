"""Friendly service presets and smart scan defaults for ``canair scan``.

Turns the cryptic ``--service 22 --range BC00-BCFF`` surface into something a
newcomer can drive: named services (``read-did``, ``live-data``, …) and
per-ECU smart defaults derived from the active profile (known PIDs + the ECU's
``id_protocol``).

This module is intentionally side-effect free and connection-free so it can be
imported cheaply by the CLI, the interactive wizard, and the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .did_families import FAMILY_SIZE, did_families, family_ranges
from .id_ranges import IdRange


@dataclass(frozen=True)
class ServicePreset:
    """A named UDS/KWP2000 service, with the metadata needed to scan it."""

    name: str
    service: int
    wide: bool  # 4-hex-digit DIDs (22/2F/31) vs 2-hex-digit PIDs (21)
    default_range: str
    summary: str
    # Whether scanning this service typically needs an extended session.
    needs_session: bool = False
    # A safety caveat shown in help / the wizard, or None.
    caution: str | None = None


# Ordered registry — the order is used when listing presets in help/wizard.
SERVICE_PRESETS: tuple[ServicePreset, ...] = (
    ServicePreset(
        name="live-data",
        service=0x21,
        wide=False,
        default_range="01-FF",
        summary="KWP2000 paged live data (powertrain ECUs: BMS, VCU, MCU, LDC)",
    ),
    ServicePreset(
        name="read-did",
        service=0x22,
        wide=True,
        default_range="F100-F1FF",
        summary="UDS ReadDataByIdentifier (body/comfort ECUs)",
    ),
    ServicePreset(
        name="iocontrol",
        service=0x2F,
        wide=True,
        # Make-neutral fallback: the full DID space. Real scans PID-derive a
        # tighter per-ECU range (see `infer_iocontrol_ranges`) or read the
        # ECU's `iocontrol_scan_ranges:` profile field.
        default_range="0000-FFFF",
        summary="UDS InputOutputControlByIdentifier (actuators)",
        needs_session=True,
        caution="may actuate physical hardware — prefer `canair scan iocontrol` "
        "(safe subfunction only) and keep the car in a safe state",
    ),
    ServicePreset(
        name="iocontrol-kwp",
        service=0x30,
        wide=False,  # KWP2000 local identifier is a single byte (2 hex digits)
        default_range="00-FF",
        summary="KWP2000 InputOutputControlByLocalIdentifier (powertrain actuators)",
        needs_session=True,
        caution="may actuate physical hardware — prefer `canair scan iocontrol BMS` "
        "(auto-selects 0x30, safe IOCP 0x00 only) and keep the car in a safe state",
    ),
    ServicePreset(
        name="routine",
        service=0x31,
        wide=True,
        default_range="F000-F0FF",
        summary="UDS RoutineControl (diagnostic routines)",
        needs_session=True,
        caution="prefer `canair scan routines` (probes requestRoutineResults only)",
    ),
)

# name / alias -> preset
_BY_NAME: dict[str, ServicePreset] = {}
for _p in SERVICE_PRESETS:
    _BY_NAME[_p.name] = _p
# Aliases for discoverability.
_ALIASES = {
    "live": "live-data",
    "livedata": "live-data",
    "read": "read-did",
    "readdid": "read-did",
    "did": "read-did",
    "io": "iocontrol",
    "ioctl": "iocontrol",
    "kwp-io": "iocontrol-kwp",
    "iokwp": "iocontrol-kwp",
    "routines": "routine",
}
for _alias, _target in _ALIASES.items():
    _BY_NAME[_alias] = _BY_NAME[_target]

# service int -> preset (canonical)
_BY_SERVICE: dict[int, ServicePreset] = {p.service: p for p in SERVICE_PRESETS}


def preset_by_service(service: int) -> ServicePreset | None:
    """Return the canonical preset for a raw service int, or None."""
    return _BY_SERVICE.get(service)


def is_wide_service(service: int) -> bool:
    """True if this service uses 4-hex-digit DIDs (22/2F/31)."""
    return service in (0x22, 0x2F, 0x31)


class ServiceError(ValueError):
    """Raised when a --service value can't be resolved."""


def resolve_service(value: str) -> tuple[int, str | None]:
    """Resolve a ``--service`` value to ``(service_int, preset_name | None)``.

    Accepts a friendly preset name/alias (``read-did``, ``live``, ``io``) or a
    raw hex byte (``22``, ``0x2F``). Returns the canonical preset name when the
    value maps to one, else ``None`` for the name.
    """
    if value is None:
        raise ServiceError("no service given")
    key = str(value).strip().lower().replace("_", "-")
    if key in _BY_NAME:
        p = _BY_NAME[key]
        return p.service, p.name
    # Fall back to hex.
    hexval = key.removeprefix("0x")
    try:
        service = int(hexval, 16)
    except ValueError as e:
        names = ", ".join(p.name for p in SERVICE_PRESETS)
        raise ServiceError(
            f"unknown service {value!r}. Use a preset name ({names}) or a hex byte (e.g. 22)."
        ) from e
    if not 0 <= service <= 0xFF:
        raise ServiceError(f"service 0x{service:X} out of range (expected a single byte 00-FF)")
    canonical = _BY_SERVICE.get(service)
    return service, (canonical.name if canonical else None)


def service_label(service: int, preset_name: str | None = None) -> str:
    """Human label for a service, e.g. ``read-did (0x22)``.

    Prefers the friendly scan-preset name; otherwise falls back to the canonical
    service name from the :mod:`canlib.uds.services` registry (so any SID gets a
    name, e.g. ``InputOutputControlByLocalIdentifier (0x30)``); finally bare hex.
    """
    if preset_name is None:
        p = _BY_SERVICE.get(service)
        preset_name = p.name if p else None
    if preset_name:
        return f"{preset_name} (0x{service:02X})"
    from canlib.uds.services import service_name

    canonical = service_name(service)
    if canonical:
        return f"{canonical} (0x{service:02X})"
    return f"0x{service:02X}"


# ---------------------------------------------------------------------------
# Smart per-ECU defaults
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanPlan:
    """A suggested scan configuration for one ECU."""

    ecu: str  # canonical ECU name (or hex TX string if unknown)
    tx_id: int
    service: int
    service_name: str | None
    ranges: tuple[IdRange, ...]
    session: bool
    wake: bool
    reason: str  # human-readable explanation of how this plan was derived


def _range_str(rng: tuple[int, int], wide: bool) -> str:
    fmt = "04X" if wide else "02X"
    return f"{rng[0]:{fmt}}-{rng[1]:{fmt}}"


def _find_ecu_def(ecu_name: str, pids_data: dict) -> tuple[str, dict] | None:
    """Case-insensitive lookup of an ECU definition in pids_data."""
    ecus = pids_data.get("ecus", {}) if pids_data else {}
    for name, defn in ecus.items():
        if name.upper() == ecu_name.upper():
            return name, defn
    return None


def _dominant_service(pid_keys: list[str]) -> int | None:
    """The service byte most of an ECU's PID keys use (``2101`` -> 0x21, ``22BC07`` -> 0x22)."""
    from collections import Counter

    services: list[int] = []
    for key in pid_keys:
        k = str(key).upper().removeprefix("0X")
        if len(k) < 4 or len(k) % 2 != 0:
            continue
        try:
            int(k[2:], 16)
            services.append(int(k[:2], 16))
        except ValueError:
            continue
    return Counter(services).most_common(1)[0][0] if services else None


def infer_iocontrol_ranges(ecu_def: dict) -> list[tuple[int, int]] | None:
    """Candidate IOControl (0x2F) DID ranges derived from an ECU's known keys.

    An ECU's IOControl DIDs cluster near the identifiers it already exposes for
    ReadDataByIdentifier (0x22) and InputOutputControl (0x2F), so its known
    wide-service DID keys are the best *make-neutral* prior for where to sweep —
    replacing the old hardcoded HKMC body-controller DID map. Returns one
    ``(start, end)`` range per observed DID high byte (``0xNN00``..``0xNNFF``),
    or ``None`` when the ECU declares no such keys (caller then falls back to the
    generic full-DID space).
    """
    families = did_families(ecu_def, include_iocontrol=False)
    if not families:
        return None
    return list(family_ranges(families))


def plan_scan(
    ecu: str,
    pids_data: dict | None = None,
    ecus_data: dict | None = None,
    *,
    slots: int = FAMILY_SIZE,
) -> ScanPlan | None:
    """Suggest a sensible scan for ``ecu`` (name or hex TX id).

    Strategy:
      1. A KWP2000 ECU whose profile PIDs are paged live data (``21xx``) gets
         its paged id space.
      2. Any other ECU with known DID families (from its 22/2F PIDs or its
         IOControl hits, see :func:`did_families`) gets a ReadDataByIdentifier
         sweep of each family: its first ``slots`` DIDs, the whole family by
         default. Reading an ECU's IOControl families is what finds the record
         DIDs of an ECU known only by its actuators.
      3. Otherwise fall back to the ECU's ``id_protocol`` (KWP2000 -> live-data,
         UDS -> identity DIDs) from the ECU registry.

    Returns ``None`` if the ECU can't be resolved to a TX id.
    """
    from .ecus import build_name_tx_index, load_ecus, resolve_tx
    from .ecus import ecu_name as _ecu_name

    if ecus_data is None:
        try:
            ecus_data = load_ecus()
        except Exception:
            ecus_data = {}
    if pids_data is None:
        from .pids import load_pids

        try:
            pids_data = load_pids()
        except Exception:
            pids_data = {}

    # Resolve names against the provided ecus_data (not just the active profile).
    name_index = build_name_tx_index(ecus_data) if ecus_data else None
    tx_id = resolve_tx(ecu, name_index=name_index)
    if tx_id is None:
        return None

    canonical_name = _ecu_name(tx_id, ecus_data)
    info = ecus_data.get(tx_id, {}) if ecus_data else {}
    protocol = str(info.get("id_protocol", "")).upper()

    found = _find_ecu_def(canonical_name, pids_data) or _find_ecu_def(str(ecu), pids_data)
    defn = found[1] if found else {}
    pid_keys = list((defn.get("pids") or {}).keys())
    dominant = _dominant_service(pid_keys)

    # 1. Paged KWP2000 live data: the id space is small, so scan all of it.
    if dominant is not None and not is_wide_service(dominant):
        preset = _BY_SERVICE.get(dominant)
        return ScanPlan(
            ecu=canonical_name,
            tx_id=tx_id,
            service=dominant,
            service_name=preset.name if preset else None,
            ranges=((0x01, 0xFF),),
            session=bool(preset and preset.needs_session),
            wake=False,
            reason=f"derived from {len(pid_keys)} known PID(s) in the profile",
        )

    # 2. The ECU's DID families.
    families = did_families(defn)
    if families:
        listed = ", ".join(f"{hi:02X}xx ({src})" for hi, src in families.items())
        extent = "whole families" if slots == FAMILY_SIZE else f"first {slots} DIDs of each"
        return ScanPlan(
            ecu=canonical_name,
            tx_id=tx_id,
            service=0x22,
            service_name="read-did",
            ranges=family_ranges(families, slots),
            session=False,
            wake=False,
            reason=f"DID families from the profile: {listed}; {extent}",
        )

    # 3. Fall back to id_protocol.
    if protocol.startswith("KWP"):
        return ScanPlan(
            ecu=canonical_name,
            tx_id=tx_id,
            service=0x21,
            service_name="live-data",
            ranges=((0x01, 0xFF),),
            session=False,
            wake=False,
            reason="KWP2000 ECU (no known PIDs yet) — scanning paged live data",
        )
    # Default / UDS: identity DIDs are the safe "what is this ECU?" scan.
    return ScanPlan(
        ecu=canonical_name,
        tx_id=tx_id,
        service=0x22,
        service_name="read-did",
        ranges=((0xF100, 0xF1FF),),
        session=False,
        wake=False,
        reason="UDS ECU (no known DIDs yet) — scanning identity DIDs (F1xx)",
    )


def presets_help() -> str:
    """A compact multi-line description of the service presets for --help."""
    lines = ["service presets (pass to --service, or use a raw hex byte):"]
    for p in SERVICE_PRESETS:
        lines.append(f"  {p.name:<10} 0x{p.service:02X}  {p.summary}")
        if p.caution:
            lines.append(f"  {'':<10}       ⚠ {p.caution}")
    return "\n".join(lines)
