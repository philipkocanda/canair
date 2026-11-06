"""Collecting everything ``canair status`` reports into a plain dict."""

from __future__ import annotations

from canair.core.tcp import tcp_open
from canair.profile import active

from .probe import device_status, load_device_config

# Exit codes.
_OK = 0


_UNREACHABLE = 1


_MISCONFIGURED = 2


# Floor for the read-only reachability probes (s). The effective timeout is the
# larger of this and the configured `transport.connect_timeout`, so a user who
# raised it for a slow VPN/cellular link isn't told "unreachable" by a probe
# stricter than the one the live commands use.
_PROBE_TIMEOUT = 4.0


def gather(args) -> dict:
    """Collect everything status reports into a plain dict (also used for --json)."""
    from canair.cli.args.transport import transport_overrides
    from canair.config.devices import fallback_settings
    from canair.install.build_info import build_summary, full_version
    from canair.transport import (
        TransportError,
        resolve_transport_candidates,
        select_reachable_transport,
    )

    info: dict = {"exit": _OK, "warnings": [], "errors": [], "canair_version": full_version()}
    info["canair_build"] = build_summary()

    try:
        candidates = resolve_transport_candidates(transport_overrides(args))
    except TransportError as e:
        info["errors"].append(str(e))
        info["exit"] = _MISCONFIGURED
        return info

    # Probe the whole candidate list exactly as a live command does, so status
    # reports the device `read`/`monitor` would actually connect to instead of
    # calling the setup unreachable because the *selected* device is down.
    _, connect_timeout, _ = fallback_settings()
    probe_timeout = max(_PROBE_TIMEOUT, connect_timeout)
    notes: list[str] = []
    t = select_reachable_transport(candidates, connect_timeout=connect_timeout, notice=notes.append)
    info["notes"] = notes

    info["transport"] = {
        "type": t.type,
        "host": t.host,
        "port": t.port,
        "bitrate": t.bitrate,
        "family": "raw" if t.is_raw else "elm",
        "summary": t.summary,
        "selected": candidates[0].describe(),
        "candidates": [c.describe() for c in candidates],
        "fell_back": t is not candidates[0],
    }

    # Active vehicle profile.
    try:
        from canair.ecus.pids import revision_resolutions
        from canair.variants import load_variants

        prof = active()
        info["profile"] = {
            "name": prof.name,
            "root": str(prof.root),
            "variant": prof.variant,
            "variant_source": prof.variant_source,
            "revisions": {
                name: {"revision": r.revision, "source": r.source, "possible": list(r.possible)}
                for name, r in revision_resolutions(prof).items()
            },
        }
        if prof.variant is None and len(load_variants(prof)) > 1:
            info["warnings"].append(
                f"no variant selected for {prof.name}: live commands will refuse — pick yours "
                f"with `canair profile use {prof.name} --variant ID`"
            )
    except Exception as e:
        info["profile"] = None
        info["warnings"].append(f"no active profile: {e}")

    host = t.host
    if not host:
        info["errors"].append("transport has no host configured")
        info["exit"] = _MISCONFIGURED
        return info

    # Firmware generation. Status is the diagnostic, so a WiCAN transport is
    # probed here even with transport.firmware_check off, and the verdict is what
    # that firmware really allows: a v6 device has no /ws however the check is
    # set. elm327-tcp is probed under the same rule as a live connect (only with
    # `firmware_check: always`), since it is usually a clone with no HTTP server.
    from canair.config.transport import firmware_check_mode
    from canair.device.wican.firmware import UNKNOWN, compatibility, probe, probes

    check_mode = firmware_check_mode()
    probed = t.is_wican_http or (t.type == "elm327-tcp" and probes("elm327-tcp"))
    fw = probe(host, timeout=probe_timeout) if probed else UNKNOWN
    verdict = compatibility(fw, t.type)
    info["firmware"] = {
        **fw.as_dict(),
        "label": fw.label(),
        "verdict": verdict.level,
        "message": verdict.message or None,
        "checked": check_mode != "off",
        "check": check_mode,
    }
    if check_mode == "off" and verdict.level != "ok":
        notes.append(
            "transport.firmware_check is off, so live commands skip this check and "
            "neither refuse nor warn"
        )
    v6 = fw.generation == "v6"

    # WiCAN 4.x HTTP config API — queryable only for WiCAN-backed transports
    # (slcan-tcp / wican-ws) on 4.x. A direct ELM327 adapter (elm327-tcp) has no
    # such API, and v6 removed these routes, so skip the probe and report
    # device: None.
    cfg = load_device_config(host, probe_timeout) if t.is_wican_http and not v6 else None
    st = device_status(host, probe_timeout) if cfg is not None else None
    device_protocol = str(cfg.get("protocol")) if cfg else None
    dev_port = None
    if cfg and cfg.get("port"):
        try:
            dev_port = int(cfg["port"])
        except (TypeError, ValueError):
            dev_port = None

    info["device"] = (
        {
            "http_reachable": cfg is not None,
            "protocol": device_protocol,
            "socket_port": dev_port,
            "sleep": cfg.get("sleep_status") if cfg else None,
            "sleep_volt": cfg.get("sleep_volt") if cfg else None,
            "battery": (st or {}).get("batt_voltage") if st else None,
            "ip": (st or {}).get("sta_ip") if st else None,
            "fw_version": (st or {}).get("fw_version") if st else None,
            "hw_version": (st or {}).get("hw_version") if st else None,
            "git_version": (st or {}).get("git_version") if st else None,
        }
        if t.is_wican_http and not v6
        else None
    )

    if verdict.level == "warn":
        info["warnings"].append(verdict.message)
    elif verdict.level == "note":
        notes.append(verdict.message)

    # Transport usability check.
    if verdict.refused:
        info["errors"].append(verdict.message)
        info["exit"] = _MISCONFIGURED
        info["transport"]["usable"] = False
    elif t.is_wican_http and t.is_elm:
        # The /ws ELM327 terminal lives on the HTTP port and works in any mode.
        if cfg is None:
            if fw.generation == "unknown":
                info["errors"].append(
                    f"WiCAN HTTP not reachable at {host} (is it online / on VPN?)"
                )
            else:
                # The probe got an answer, so the device is online: only its config
                # route failed.
                info["errors"].append(
                    f"WiCAN at {host} answered the firmware probe but not /load_config"
                )
            info["exit"] = _UNREACHABLE
        info["transport"]["usable"] = cfg is not None
    elif t.is_elm:
        # Direct ELM327 adapter (elm327-tcp): no HTTP API — usability is just
        # whether the ELM socket accepts a TCP connection.
        from canair.transport.config import DEFAULT_ELM327_TCP_PORT

        elm_port = t.port or DEFAULT_ELM327_TCP_PORT
        info["transport"]["port"] = elm_port
        elm_ok = tcp_open(host, elm_port, probe_timeout)
        info["transport"]["usable"] = elm_ok
        if not elm_ok:
            info["errors"].append(
                f"ELM327 adapter {host}:{elm_port} not reachable "
                f"(powered on? on its Wi-Fi? for offline testing: elm -n {elm_port})"
            )
            info["exit"] = _UNREACHABLE
    else:
        raw_port = t.port or dev_port or 3333
        info["transport"]["port"] = raw_port
        raw_ok = tcp_open(host, raw_port, probe_timeout)
        mismatch = bool(device_protocol and device_protocol != "slcan")
        # Usable only if the port is open AND the device is actually serving SLCAN
        # (an open port in the wrong mode won't speak SLCAN).
        info["transport"]["usable"] = raw_ok and not mismatch
        if mismatch:
            info["warnings"].append(
                f"device is in '{device_protocol}' mode but the raw transport needs 'slcan' — "
                f"set it with: canair wican mode set slcan"
            )
            info["exit"] = _MISCONFIGURED
        if not raw_ok:
            if v6:
                hint = " (firmware v6 serves SLCAN only once set up — see the warning above)"
            elif device_protocol == "slcan":
                hint = ""
            else:
                hint = " (likely because it's not in 'slcan' mode)"
            info["errors"].append(f"SLCAN port {host}:{raw_port} not reachable{hint}")
            if info["exit"] == _OK:
                info["exit"] = _UNREACHABLE

    # A liveness probe that rejected the primary and still handed it back means
    # every candidate was down; say so, or the verdict reads as if only the
    # selected device had been tried.
    if info["exit"] == _UNREACHABLE and notes and t is candidates[0]:
        others = ", ".join(c.describe() for c in candidates[1:])
        info["errors"].append(f"no other configured device answered either (tried {others})")

    # Connection mutex (local-only): who, if anyone, currently owns the device
    # connection. Read-only — it never takes or clears the lock.
    try:
        from canair.device.lock import lock_state

        info["lock"] = lock_state().as_dict()
    except Exception:
        info["lock"] = None

    # Cached update notice (local-only; no network on the status hot path).
    try:
        from canair.install.update_check import pending_notice

        info["update_notice"] = pending_notice()
    except Exception:
        info["update_notice"] = None

    # Install context (local-only): warn when a bare `canair` would run a
    # different version than `uv run canair` in the source clone.
    try:
        from canair.install.context import describe as describe_install
        from canair.install.context import source_clone

        install = describe_install(source_clone())
        info["install"] = install
        if install["out_of_sync"]:
            info["warnings"].append(
                f"installed `canair` ({install['tool_version']}) is out of sync with the "
                f"source clone ({install['clone_version']}) — run `canair update` to reinstall"
            )
        elif install["running_origin"] == "uv-tool":
            info["warnings"].append(
                "running the `uv tool install` copy — its bundled profiles are a frozen "
                "snapshot; edits to the repo's profiles/ won't appear until reinstall. Use "
                "`uv run canair` from the repo root for live profile edits"
            )
    except Exception:
        info["install"] = None

    return info
