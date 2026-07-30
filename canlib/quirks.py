"""Profile-scoped make quirks — small behavioral toggles a profile opts into.

Some diagnostic behaviors are make-specific and would be *wrong* to apply
universally. Rather than hardcode a Hyundai/Kia assumption into shared code, a
profile declares the quirks it needs under a top-level ``quirks:`` list in
``profile.yaml`` and the shared code gates on it. New profiles start with no
quirks (make-neutral); the bundled ``ioniq-2017`` profile opts into the ones its
ECUs actually exhibit.

Today there is one quirk:

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

# Every recognized quirk token (the validation whitelist).
KNOWN_QUIRKS: Final = frozenset({HK_F1XX_MINUS_ONE, SKM_WAKEUP})


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
