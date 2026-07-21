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
        summary="UDS ReadDataByIdentifier (body/comfort ECUs: IGPM, BCM, …)",
    ),
    ServicePreset(
        name="iocontrol",
        service=0x2F,
        wide=True,
        default_range="B000-B0FF",
        summary="UDS InputOutputControlByIdentifier (actuators)",
        needs_session=True,
        caution="may actuate physical hardware — prefer `canair iocontrol-scan` "
        "(safe subfunction only) and keep the car in a safe state",
    ),
    ServicePreset(
        name="routine",
        service=0x31,
        wide=True,
        default_range="F000-F0FF",
        summary="UDS RoutineControl (diagnostic routines)",
        needs_session=True,
        caution="prefer `canair routines-scan` (probes requestRoutineResults only)",
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
    """Human label for a service, e.g. ``read-did (0x22)`` or ``0x18``."""
    if preset_name is None:
        p = _BY_SERVICE.get(service)
        preset_name = p.name if p else None
    if preset_name:
        return f"{preset_name} (0x{service:02X})"
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
    pid_range: tuple[int, int]
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


def _infer_from_pids(pid_keys: list[str]) -> tuple[int, tuple[int, int]] | None:
    """Infer (service, range) from an ECU's defined PID/DID keys.

    PID keys embed the service byte, e.g. ``2101`` (service 21, id 01) or
    ``22BC07`` (service 22, DID BC07). The dominant service wins; the range
    spans the observed id/DID high bytes.
    """
    from collections import Counter

    parsed: list[tuple[int, int]] = []  # (service, did)
    for key in pid_keys:
        k = str(key).upper().removeprefix("0X")
        if len(k) < 4 or len(k) % 2 != 0:
            continue
        try:
            service = int(k[:2], 16)
            did = int(k[2:], 16)
        except ValueError:
            continue
        parsed.append((service, did))
    if not parsed:
        return None

    dominant = Counter(s for s, _ in parsed).most_common(1)[0][0]
    dids = [d for s, d in parsed if s == dominant]
    if is_wide_service(dominant):
        his = [d >> 8 for d in dids]
        lo = min(his) << 8
        hi = (max(his) << 8) | 0xFF
        return dominant, (lo, hi)
    # Narrow service (e.g. 21): the paged id space is small; scan it fully.
    return dominant, (0x01, 0xFF)


def plan_scan(
    ecu: str,
    pids_data: dict | None = None,
    ecus_data: dict | None = None,
) -> ScanPlan | None:
    """Suggest a sensible scan for ``ecu`` (name or hex TX id).

    Strategy:
      1. If the profile defines PIDs for this ECU, infer service + range from
         them (most useful — scans the space we already know about).
      2. Otherwise fall back to the ECU's ``id_protocol`` (KWP2000 -> live-data,
         UDS -> identity DIDs) from ecus.yaml.

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

    # 1. Infer from known PIDs.
    found = _find_ecu_def(canonical_name, pids_data) or _find_ecu_def(str(ecu), pids_data)
    if found:
        _name, defn = found
        pid_keys = list((defn.get("pids") or {}).keys())
        inferred = _infer_from_pids(pid_keys)
        if inferred:
            service, rng = inferred
            preset = _BY_SERVICE.get(service)
            return ScanPlan(
                ecu=canonical_name,
                tx_id=tx_id,
                service=service,
                service_name=preset.name if preset else None,
                pid_range=rng,
                session=bool(preset and preset.needs_session),
                wake=False,
                reason=f"derived from {len(pid_keys)} known PID(s) in the profile",
            )

    # 2. Fall back to id_protocol.
    info = ecus_data.get(tx_id, {}) if ecus_data else {}
    protocol = str(info.get("id_protocol", "")).upper()
    if protocol.startswith("KWP"):
        return ScanPlan(
            ecu=canonical_name,
            tx_id=tx_id,
            service=0x21,
            service_name="live-data",
            pid_range=(0x01, 0xFF),
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
        pid_range=(0xF100, 0xF1FF),
        session=False,
        wake=False,
        reason="UDS ECU (no known PIDs yet) — scanning identity DIDs (F1xx)",
    )


def presets_help() -> str:
    """A compact multi-line description of the service presets for --help."""
    lines = ["service presets (pass to --service, or use a raw hex byte):"]
    for p in SERVICE_PRESETS:
        lines.append(f"  {p.name:<10} 0x{p.service:02X}  {p.summary}")
        if p.caution:
            lines.append(f"  {'':<10}       ⚠ {p.caution}")
    return "\n".join(lines)
