"""Which WiCAN firmware generation is on the other end, and what canair can do with it.

WiCAN firmware **v6** is a ground-up rewrite, not an increment on 4.x: the
``/ws`` ELM327 terminal, the global ``protocol`` mode, the SLCAN dialect, the
legacy HTTP routes and the AutoPID format are all gone or changed. canair speaks
the 4.x interfaces, so before using any of them it asks the device which
generation it runs (:func:`probe`) and looks the answer up in one policy table
(:func:`compatibility`). Audit and rationale:
``plans/2026-10-20-wican-fw-v6-compatibility.md``.

The probe sends only ``GET`` requests (``/api/info``, then ``/check_status``
unless the first answer was positively v6), so it has no side effect on either
generation, and is memoised per host for the life of the process. A device that
is neither generation (any other SLCAN gateway or ELM327 clone) is ``unknown``,
which is never an error. Whether a surface is probed at all is
:func:`probes` (``transport.firmware_check``).

**Never key an ELM327 chip capability on this.** The ESP32 reflashes its OBD
co-processor on its own schedule, so a chip feature must be gated on a chip probe.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Literal, TextIO

import requests

Generation = Literal["v4", "v6", "unknown"]
Gate = Literal["auth", "untrusted"]
Level = Literal["ok", "note", "warn", "refuse", "skip"]

# Every place canair touches a firmware-specific interface. The three transport
# names double as surfaces, so a resolved ``TransportConfig.type`` can be passed
# straight in.
Surface = Literal[
    "wican-ws",  # live connect over the /ws ELM327 terminal
    "slcan-tcp",  # live connect or sniff over the SLCAN socket
    "elm327-tcp",  # live connect over a plain ELM327 socket
    "sniff-listen-only",  # `canair sniff --listen-only`
    "wican-mode",  # `canair wican mode show/set`
    "autopid-sync",  # `canair wican autopid upload/download/diff`
    "reboot",  # `--reboot` after a session
]

# v4 reports "%ld.%02ld" of its tag (v4.52p -> "4.52"); a tag it cannot parse
# reads "0.00", which names no version at all.
_V4_VERSION = re.compile(r"^(\d+)\.(\d{2})$")
_V4_UNPARSED = "0.00"
_V6_UNTRUSTED = "configuration disabled on this network"


@dataclass(frozen=True)
class FirmwareInfo:
    """What the probe learned. Holds no secrets: v4's status body is discarded."""

    # "v4" is the legacy HTTP API family /check_status answers for: 4.x, and the
    # older releases that share it (reported, and warned about, by version).
    generation: Generation
    version: str | None = None  # "4.52" / "6.0.0-beta.1"; None when the device didn't say
    build: str | None = None  # v4 git_version / v6 fw_build
    hw_version: str | None = None  # "WiCAN-OBD-PRO" / "WiCAN-PRO"
    api_level: int | None = None  # v6 only
    gated: Gate | None = None  # v6 refused the probe: 401 / 403

    def label(self) -> str:
        """Short human name, e.g. ``4.52 (v4.52p)`` or ``v6 6.0.0-beta.1``."""
        if self.generation == "v4":
            text = self.version or "4.x"
            return f"{text} ({self.build})" if self.build else text
        if self.generation == "v6":
            if self.gated == "auth":
                return "v6 (version hidden: the device has an admin password)"
            if self.gated == "untrusted":
                return "v6 (version hidden: this Wi-Fi network is marked untrusted)"
            return f"v6 {self.version}" if self.version else "v6"
        return "unknown"

    def as_dict(self) -> dict:
        return {
            "generation": self.generation,
            "version": self.version,
            "build": self.build,
            "hw_version": self.hw_version,
            "api_level": self.api_level,
            "gated": self.gated,
        }


UNKNOWN = FirmwareInfo("unknown")


@dataclass(frozen=True)
class SupportedRange:
    generation: Generation
    low: str
    high: str
    note: str


# The firmware canair has been checked against. The one place to widen when a
# release is verified.
SUPPORTED: tuple[SupportedRange, ...] = (
    SupportedRange(
        "v4",
        "4.50",
        "4.52",
        "measured on 4.50; 4.51/4.52 are source-identical on canair's paths, and "
        "4.52's bundled OBD chip firmware V2.3.24 is unmeasured",
    ),
)


@dataclass(frozen=True)
class Verdict:
    level: Level
    message: str = ""

    @property
    def refused(self) -> bool:
        return self.level == "refuse"


OK = Verdict("ok")

_COMPAT_DOC = "docs/getting-started/connect-device.md#firmware-compatibility"

_V6_SLCAN_SETUP = (
    "  A stock v6 device serves no SLCAN. In its settings, enable all three, then submit "
    "(the device reboots):\n"
    "    1. socket_manager: the slcan0 server (TCP 3333)\n"
    "    2. bridge_manager: a bridge from 'can' to 'slcan0' with translator 'slcan'\n"
    "    3. can_manager: enabled\n"
    "  Without the bridge (or with can_manager off) the port accepts the connection and "
    "silently discards everything, so every ECU looks dead. Port 35000 on v6 is the "
    "ELM327 server, not SLCAN."
)

_ELM327_TCP_HINT = (
    "--transport elm327-tcp (port 35000, on by default on v6), or make it the device's "
    "default: canair config set devices.<alias>.transport elm327-tcp"
)


def _v6_policy(info: FirmwareInfo, surface: Surface) -> Verdict:
    name = f"WiCAN firmware {info.label()}"
    if surface == "wican-ws":
        return Verdict(
            "refuse",
            f"{name} has no /ws ELM327 terminal, so the 'wican-ws' transport cannot work.\n"
            f"  • use its plain ELM327 socket instead: {_ELM327_TCP_HINT}\n"
            f"  • see {_COMPAT_DOC}",
        )
    if surface == "slcan-tcp":
        return Verdict(
            "warn",
            f"{name}: canair has only verified the 'slcan-tcp' transport on 4.x firmware.\n"
            f"{_V6_SLCAN_SETUP}\n"
            f"  • simpler: {_ELM327_TCP_HINT}",
        )
    if surface == "sniff-listen-only":
        return Verdict(
            "refuse",
            f"{name} ignores the SLCAN listen-only command, so --listen-only would still put "
            "an acknowledging, transmit-capable node on the bus.\n"
            "  • for a truly passive sniff, set can_manager.silent = true on the device and "
            "re-run without --listen-only\n"
            "  • or re-run without --listen-only to sniff in active mode deliberately",
        )
    if surface == "wican-mode":
        return Verdict(
            "refuse",
            f"{name} has no protocol mode to show or switch: v6 replaced it with servers, "
            "bridges and translators configured in the device's settings.\n"
            f"  • for ELM327 access: {_ELM327_TCP_HINT}\n"
            "  • for SLCAN, enable the slcan0 server, a 'can' to 'slcan0' bridge and "
            "can_manager in the device's settings",
        )
    if surface == "autopid-sync":
        return Verdict(
            "refuse",
            f"{name} uses a new AutoPID format and API that canair cannot sync yet: the 4.x "
            "routes canair uses are gone, so the request would only fail.\n"
            "  • `canair wican autopid write` generates the v6 table (autopid-v6.json, from "
            "6.0.0-beta.3) to load through the device's web UI",
        )
    if surface == "reboot":
        return Verdict(
            "skip",
            "skipping --reboot: WiCAN firmware v6 resumes AutoPID by itself about 10 s after "
            "the last client command, so no reboot is needed",
        )
    # elm327-tcp: works, but the socket is a raw pass-through to a shared chip.
    return Verdict(
        "note",
        f"{name}: its ELM327 socket is a raw pass-through to a shared OBD chip. Other "
        "clients' replies can reach this session, and AutoPID (if enabled) reclaims the chip "
        "after 10 s without a command, resetting the adapter's settings.",
    )


def _version_key(version: str) -> tuple[int, int] | None:
    m = _V4_VERSION.match(version)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _v4_range(info: FirmwareInfo) -> Verdict:
    current = _version_key(info.version or "")
    for rng in SUPPORTED:
        if rng.generation != "v4" or current is None:
            continue
        low, high = _version_key(rng.low), _version_key(rng.high)
        assert low is not None and high is not None  # SUPPORTED is static data
        tested = f"canair has tested {rng.low}–{rng.high}"
        if current > high:
            return Verdict("warn", f"WiCAN firmware {info.label()} is newer than {tested}")
        if current < low:
            return Verdict("warn", f"WiCAN firmware {info.label()} is older than {tested}")
    return OK


def compatibility(info: FirmwareInfo, surface: Surface) -> Verdict:
    """What canair should do on ``surface`` against firmware ``info``."""
    if info.generation == "v6":
        return _v6_policy(info, surface)
    if info.generation == "v4" and surface != "reboot":
        return _v4_range(info)
    return OK


# --- the probe ------------------------------------------------------------------

_cache: dict[str, FirmwareInfo] = {}


def clear_cache() -> None:
    _cache.clear()


def _base_url(host: str) -> str:
    return host.rstrip("/") if host.startswith(("http://", "https://")) else f"http://{host}"


def _fetch(url: str, timeout: float) -> requests.Response:
    # One seam for the HTTP call, so tests can stand in for the device.
    return requests.get(url, timeout=timeout)


def _json(resp: requests.Response) -> dict | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _from_api_info(resp: requests.Response) -> FirmwareInfo | None:
    """Classify a ``GET /api/info`` reply; None means "not v6, try the 4.x route".

    Only a positive v6 answer stops here. Anything else — v4's 404, but also a
    200 without an integer ``api_level``, a foreign 401/403 or a 5xx — says
    nothing about 4.x, so the 4.x route still gets its chance.
    """
    if resp.status_code == 200:
        body = _json(resp) or {}
        level = body.get("api_level")
        if isinstance(level, int) and not isinstance(level, bool):
            return FirmwareInfo(
                "v6",
                version=_text(body.get("fw_version")),
                build=_text(body.get("fw_build")),
                hw_version=_text(body.get("hw_version")),
                api_level=level,
            )
    elif resp.status_code == 401:
        if 'realm="WiCAN"' in resp.headers.get("WWW-Authenticate", ""):
            return FirmwareInfo("v6", gated="auth")
    elif resp.status_code == 403:
        if (_json(resp) or {}).get("error") == _V6_UNTRUSTED:
            return FirmwareInfo("v6", gated="untrusted")
    return None


def _from_check_status(resp: requests.Response) -> FirmwareInfo:
    """Classify a v4 ``GET /check_status`` reply, keeping only the version fields.

    The body also carries plaintext Wi-Fi and MQTT passwords, so nothing but the
    three version strings may leave this function.
    """
    if resp.status_code != 200:
        return UNKNOWN
    body = _json(resp)
    if body is None:
        return UNKNOWN
    version = _text(body.get("fw_version"))
    hw_version = _text(body.get("hw_version"))
    build = _text(body.get("git_version"))
    del body
    if version is None or not _V4_VERSION.match(version):
        return UNKNOWN
    if version == _V4_UNPARSED:
        # A 4.x-family device whose build tag the firmware couldn't parse: no
        # version to range-check, so no "older than tested" warning either.
        version = None
    return FirmwareInfo("v4", version=version, build=build, hw_version=hw_version)


def probe(host: str, *, timeout: float) -> FirmwareInfo:
    """Identify the firmware generation at ``host`` (a host, ``host:port`` or URL).

    ``GET /api/info`` first (v6, including its password and untrusted-network
    gates); unless that was positively v6, ``GET /check_status`` (4.x). So the
    worst case is two requests, each bounded by ``timeout`` to connect and again
    to read. An unreachable host stops after the first; it and any other
    outcome are ``unknown``. Memoised per host.
    """
    base = _base_url(host)
    cached = _cache.get(base)
    if cached is not None:
        return cached
    info = UNKNOWN
    try:
        info = _from_api_info(_fetch(f"{base}/api/info", timeout)) or _from_check_status(
            _fetch(f"{base}/check_status", timeout)
        )
    except requests.RequestException:
        info = UNKNOWN
    _cache[base] = info
    return info


def probes(surface: Surface) -> bool:
    """Whether ``transport.firmware_check`` lets canair probe for ``surface``.

    ``elm327-tcp`` is usually a generic clone with no HTTP server, so it is
    probed only when the check is ``always``; every other surface reaches a
    WiCAN's HTTP API and is probed unless the check is off.
    """
    from canlib.config import firmware_check_mode

    mode = firmware_check_mode()
    if mode == "off":
        return False
    return mode == "always" if surface == "elm327-tcp" else True


def detect(host: str, surface: Surface, *, timeout: float | None = None) -> FirmwareInfo:
    """:func:`probe` when :func:`probes` allows it for ``surface``, else ``unknown``.

    ``timeout`` defaults to ``transport.connect_timeout``.
    """
    if not probes(surface):
        return UNKNOWN
    if timeout is None:
        from canlib.config import fallback_settings

        _, timeout, _ = fallback_settings()
    return probe(host, timeout=timeout)


def check(host: str, surface: Surface, *, timeout: float | None = None) -> Verdict:
    """:func:`detect` then :func:`compatibility` — the one-call form for callers."""
    return compatibility(detect(host, surface, timeout=timeout), surface)


# Why the generation stayed unknown, for :func:`unconfirmed`.
Unconfirmed = Literal["check-off", "unidentified"]

_UNCONFIRMED_WHY: dict[Unconfirmed, str] = {
    "check-off": "transport.firmware_check is off",
    "unidentified": "the device did not identify itself as WiCAN 4.x or v6",
}


def unconfirmed(surface: Surface, why: Unconfirmed) -> Verdict:
    """The verdict when a WiCAN's generation could not be established.

    ``unknown`` is ``ok`` in :func:`compatibility`, because a non-WiCAN gateway
    is legitimate. A caller that has reason to think it *is* talking to a WiCAN
    asks this instead, for the surfaces whose safety only 4.x guarantees. Both
    only warn: refusing would lock out an offline-configured or unusual device,
    and the user can see the risk spelled out.
    """
    reason = _UNCONFIRMED_WHY[why]
    if surface == "sniff-listen-only":
        return Verdict(
            "warn",
            f"--listen-only cannot be guaranteed: {reason}. WiCAN firmware v6 ignores the "
            "SLCAN listen-only command and stays an acknowledging, transmit-capable node "
            "on the bus.\n"
            "  • if this is a v6 WiCAN, set can_manager.silent = true on the device",
        )
    if surface == "autopid-sync":
        return Verdict(
            "warn",
            f"could not confirm this WiCAN runs firmware 4.x ({reason}). canair's AutoPID "
            "sync speaks only the 4.x API, so on other firmware the upload fails.",
        )
    return OK


_PREFIX: dict[Level, str] = {
    "ok": "",
    "note": "note: ",
    "warn": "warning: ",
    "refuse": "error: ",
    "skip": "note: ",
}


def print_verdict(verdict: Verdict, *, file: TextIO | None = None) -> None:
    """Print a verdict's message to stderr with its level prefix; silent when ``ok``."""
    if verdict.level == "ok" or not verdict.message:
        return
    print(f"{_PREFIX[verdict.level]}{verdict.message}", file=file or sys.stderr)
