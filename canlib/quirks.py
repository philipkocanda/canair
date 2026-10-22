"""Profile-scoped make quirks — small behavioral toggles a profile opts into.

Some diagnostic behaviors are make-specific and would be *wrong* to apply
universally. Rather than hardcode a Hyundai/Kia assumption into shared code, a
profile declares the quirks it needs under a top-level ``quirks:`` list in
``profile.yaml`` and the shared code gates on it. New profiles start with no
quirks (make-neutral); the bundled ``ioniq-2017`` profile opts into the ones its
ECUs actually exhibit.

Today there are three quirks:

- :data:`HK_F1XX_MINUS_ONE` — Hyundai/Kia identity DIDs answer one *less* than
  requested (request ``22F188`` → response ``62F187``). Expected HK behaviour,
  but on any other make an off-by-one identifier echo is a genuinely stale /
  misfiled frame, so echo validation must only tolerate it when this quirk is on.
- :data:`SKM_WAKEUP` — the Ioniq/HKMC Smart Key Module *relay*-wake procedure
  (:mod:`canlib.modes.skm_wakeup`): rouse the SKM then close a power relay
  (ACC/IGN) via IOControl. The relay DIDs / magic bytes / addresses are
  Ioniq-particular, so the ``skm-wake`` relay command is refused for profiles
  that don't declare this capability. (Merely *reading* a fast-sleeping ECU is
  the make-neutral per-ECU ``wake:`` block — :mod:`canlib.wake` — not this quirk.)
- :data:`NISSAN_C0_SESSION` — Nissan ECUs gate their KWP2000 actuator tests
  (``0x30``) and many ``0x21`` reads behind the OEM diagnostic session ``10 C0``.
  The KWP2000 ``0x80``-``0xFF`` session band is OEM-defined, so the command
  blocklist refuses ``10 C0`` on every other make; this quirk permits it on the
  profile's KWP2000 ECUs only (on a UDS ECU the same byte is an OEM session with
  the suppress-response bit). It also
  switches the KWP2000 IOControl scan to Nissan's ``0x30`` layout
  (:mod:`canlib.modes.kwp_iocontrol_scan`), where the ISO probe would begin a
  one-shot command.

See ``plans/2026-07-28-multi-vehicle-support.md`` (Phase 4, gap F).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# The Hyundai/Kia identity-DID -1 offset (22F188 → 62F187).
HK_F1XX_MINUS_ONE: Final = "hk_f1xx_minus_one"

# The Ioniq/HKMC Smart Key Module relay-wake procedure (canlib.modes.skm_wakeup):
# rouse the SKM, then actuate a power relay (ACC/IGN) via IOControl. Make-specific
# (relay DIDs / magic bytes / addresses are Ioniq-particular), so the `skm-wake`
# relay command is gated behind this capability. Reading a fast-sleeping ECU is
# the make-neutral per-ECU `wake:` block (canlib.wake); this quirk is only the
# relay-actuation extension.
SKM_WAKEUP: Final = "skm_wakeup"

# The Nissan OEM diagnostic session (10 C0), which the shared blocklist refuses on
# makes that never declared it (canlib.safety).
NISSAN_C0_SESSION: Final = "nissan_c0_session"

# Every recognized quirk token (the validation whitelist).
KNOWN_QUIRKS: Final = frozenset({HK_F1XX_MINUS_ONE, SKM_WAKEUP, NISSAN_C0_SESSION})

# DiagnosticSessionControl sub-functions a quirk makes safe to enter, keyed by the
# protocol whose ECUs it applies to. Only ever widens the OEM band the blocklist
# guards; it cannot reach the programming or safety-system sessions, which
# canlib.safety refuses regardless. Nissan's C0 is a KWP2000 session: on a UDS ECU
# the same byte is OEM session 0x40 with the suppress-response bit, so the
# allowance is scoped to KWP2000 ECUs.
_QUIRK_SESSIONS: Final[dict[str, dict[str, frozenset[int]]]] = {
    NISSAN_C0_SESSION: {"kwp": frozenset({0xC0})},
}


def resolve_quirks(meta: Mapping[str, Any] | None) -> frozenset[str]:
    """The set of quirk tokens declared in a profile's ``quirks:`` list.

    ``meta`` is the profile-wide settings mapping (``profile.yaml`` contents,
    merged into the loaded PID data). Tolerates a missing/malformed block by
    returning an empty set — unknown tokens are surfaced by ``canair validate``,
    not here.
    """
    if isinstance(meta, Mapping):
        quirks = meta.get("quirks")
        if isinstance(quirks, (list, tuple, set)):
            return frozenset(str(q) for q in quirks)
    return frozenset()


def has_quirk(meta: Mapping[str, Any] | None, name: str) -> bool:
    """True when the profile ``meta`` declares quirk ``name``."""
    return name in resolve_quirks(meta)


def allowed_session_modes(meta: Mapping[str, Any] | None, protocol: str) -> frozenset[int]:
    """The OEM session sub-functions the profile's quirks make safe on ``protocol`` ECUs.

    ``protocol`` is a :data:`canlib.safety.SessionProtocol` (``"uds"``/``"kwp"``/
    ``"unknown"``). An ECU of unknown protocol gets no allowance.
    """
    allowed: frozenset[int] = frozenset()
    for quirk in resolve_quirks(meta):
        allowed |= _QUIRK_SESSIONS.get(quirk, {}).get(protocol, frozenset())
    return allowed
