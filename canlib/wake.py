"""Per-ECU wake rituals — how to rouse a fast-sleeping ECU before reads.

Some modules power their CAN transceiver only briefly. A Smart Key Module, for
instance, answers a single diagnostic request and then sleeps again within a
second or two, so the usual single ``10 01`` wake (see
:meth:`canlib.session_manager.SessionManager.open_session`) races the sleep
timer: the wake frame lands, the ECU stirs, but by the time the follow-up read
arrives it is asleep again and returns ``NO DATA``.

The reliable fix — established empirically on the Ioniq SKM — is to fire a cheap
request *back-to-back* several times, keeping the transceiver awake long enough
to open a session, then read. That ritual is **make/ECU-specific**, so rather
than hardcode it, a profile declares it per ECU under a ``wake:`` block in
``ecus/<name>.yaml`` and this module resolves it into a :class:`WakePlan` the
shared session layer executes on **both** transports.

This is the generic primitive (idea 1). The Ioniq SKM relay procedure
(:mod:`canlib.modes.skm_wakeup`, idea 3) builds on the same
:func:`rapid_read <canlib.session_manager.SessionManager.rapid_read_wake>`
primitive and adds relay actuation, gated behind the ``skm_wakeup`` profile
quirk.

Make-neutral by default: an ECU with no ``wake:`` block yields ``None`` and the
session layer falls back to the built-in single-``10 01`` wake.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

# Wake methods (mirror valid_wake_methods in pids_schema.yaml).
RAPID_READ: Final = "rapid_read"
SESSION: Final = "session"
RELAY: Final = "relay"
KNOWN_METHODS: Final = frozenset({RAPID_READ, SESSION, RELAY})

# Defaults for a rapid_read ritual when the profile omits a field. Tuned to the
# Ioniq SKM (~2 s sleep timer): six primes at 60 ms comfortably fit inside the
# window, and 10 01 is a universally safe prime when no cheap PID is named.
DEFAULT_ATTEMPTS: Final = 6
DEFAULT_INTERVAL_MS: Final = 60
DEFAULT_PRIME: Final = "1001"
DEFAULT_SESSION_MODE: Final = "03"


@dataclass(frozen=True)
class WakePlan:
    """A resolved wake ritual for one ECU (defaults filled in).

    ``prime`` is the hex request fired repeatedly to hold the transceiver awake
    (a full ``22Bxxx`` DID or a bare ``1001``). ``attempts``/``interval_ms``
    shape the rapid-fire loop; ``interval_ms`` must stay under the ECU's sleep
    timer. ``session_mode`` is the DiagnosticSessionControl sub-function entered
    once the ECU is awake.
    """

    method: str
    prime: str
    attempts: int
    interval_ms: int
    session_mode: str
    sleep_timer_ms: int | None = None

    @property
    def interval_s(self) -> float:
        return self.interval_ms / 1000.0


def _normalize_prime(value: Any) -> str:
    """Coerce a declared ``prime_pid`` into a bare hex request string.

    Whitespace-insensitive, upper-cased, ``0x`` stripped. ``prime_pid`` is a full
    UDS request (SID-first) — e.g. ``"22B003"`` (read DID B003), ``"1001"``
    (DiagnosticSessionControl), ``"3E00"`` (TesterPresent). We deliberately do
    NOT auto-prefix a bare DID: a 4-hex value like ``1001``/``3E00`` is itself a
    valid standalone request, so guessing "that's a service-22 DID" would be
    ambiguous. Author the service byte explicitly.
    """
    text = str(value).strip().upper().replace(" ", "").removeprefix("0X")
    return text or DEFAULT_PRIME


def resolve_wake(ecu_def: Mapping[str, Any] | None) -> WakePlan | None:
    """Resolve an ECU definition's ``wake:`` block into a :class:`WakePlan`.

    Returns ``None`` when the ECU declares no ``wake:`` block (the caller falls
    back to the default single-``10 01`` wake). Missing fields are filled from the
    module defaults. Validation of field *values* is the schema's job
    (``canair validate pids``); this resolver is tolerant and best-effort so a
    slightly malformed block still yields a usable plan rather than crashing a
    live read.
    """
    if not isinstance(ecu_def, Mapping):
        return None
    raw = ecu_def.get("wake")
    if not isinstance(raw, Mapping):
        return None

    method = str(raw.get("method", RAPID_READ)).strip() or RAPID_READ
    prime = _normalize_prime(raw.get("prime_pid", DEFAULT_PRIME))

    def _int(key: str, default: int) -> int:
        v = raw.get(key, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    attempts = max(1, _int("attempts", DEFAULT_ATTEMPTS))
    interval_ms = max(0, _int("interval_ms", DEFAULT_INTERVAL_MS))
    session_mode = (
        str(raw.get("session_mode", DEFAULT_SESSION_MODE)).strip() or DEFAULT_SESSION_MODE
    )
    sleep_timer = raw.get("sleep_timer_ms")
    sleep_timer_ms = None
    if sleep_timer is not None:
        try:
            sleep_timer_ms = int(sleep_timer)
        except (TypeError, ValueError):
            sleep_timer_ms = None

    return WakePlan(
        method=method,
        prime=prime,
        attempts=attempts,
        interval_ms=interval_ms,
        session_mode=session_mode,
        sleep_timer_ms=sleep_timer_ms,
    )
