"""``canair config show``: the effective settings."""

from __future__ import annotations

from canair.cli.profile_context import active
from canair.config.devices import fallback_settings, wican_devices, wican_model, wican_settings
from canair.config.file import load_config, user_config_file, user_profiles_dir
from canair.core.paths import CONFIG_DOCS_URL, CONFIG_FILE, config_dir
from canair.render.console import console, escape


def _gather(args) -> dict:
    """Collect config locations + effective settings into a plain dict."""

    cfg = load_config()
    user_file = user_config_file()

    info: dict = {
        "files": {
            "user": {"path": str(user_file), "exists": user_file.exists()},
            "legacy": {"path": str(CONFIG_FILE), "exists": CONFIG_FILE.exists()},
            "config_dir": str(config_dir()),
            "profiles_dir": str(user_profiles_dir()),
        },
        "config": cfg,
    }

    addresses, default_alias = wican_settings()
    info["wican"] = {
        "addresses": addresses,
        "default": default_alias,
        "model": wican_model(),
    }

    devices, _ = wican_devices()
    info["devices"] = {
        alias: {
            "host": dev.host,
            "transport": dev.transport,
            "port": dev.port,
            "bitrate": dev.bitrate,
        }
        for alias, dev in devices.items()
    }
    _fb_enabled, _fb_timeout, _fb_order = fallback_settings()
    info["fallback"] = {
        "enabled": _fb_enabled,
        "connect_timeout": _fb_timeout,
        "order": _fb_order,
    }

    try:
        from canair.transport import resolve_transport

        t = resolve_transport()
        info["transport"] = {
            "type": t.type,
            "host": t.host,
            "port": t.port,
            "bitrate": t.bitrate,
        }
    except Exception as e:
        info["transport"] = None
        info["transport_error"] = str(e)

    try:
        prof = active()
        info["profile"] = {"name": prof.name, "root": str(prof.root)}
    except Exception as e:
        info["profile"] = None
        info["profile_error"] = str(e)

    info["dtc_log"] = dtc_log_stats((info.get("profile") or {}).get("name"))
    return info


def dtc_log_stats(active_name: str | None) -> dict:
    """Every profile's DTC log in user state; open codes only for the active car."""
    from canair.dtc.history import log_stats
    from canair.dtc.log import log_dir
    from canair.dtc.log_upgrade import migrate_all
    from canair.dtc.scope import ANY_CAR

    try:
        migrate_all()  # a legacy in-profile log is counted only once it is moved
    except Exception:
        pass
    directory = log_dir()
    logs = []
    for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
        is_active = path.stem == active_name
        try:
            stats = log_stats(path, car=None if is_active else ANY_CAR, profile=active())
        except Exception as e:
            logs.append({"profile": path.stem, "file": str(path), "error": str(e)})
            continue
        entry = {"profile": path.stem, "active": is_active, **stats.as_dict()}
        if not is_active:
            del entry["open"], entry["open_codes"]
        logs.append(entry)
    return {"dir": str(directory), "logs": logs}


# Keys rendered on their own in the "Settings" block are shown from the
# effective config; these are surfaced elsewhere so we skip them there.
_SPECIAL_KEYS = {"wican_addresses", "devices", "transport", "wican_model"}


def _render(info: dict) -> None:
    c = console()

    files = info["files"]
    c.print("\n  [bold]Config files[/bold]")
    for label, entry in (("user", files["user"]), ("legacy", files["legacy"])):
        mark = "[green]ok[/green]" if entry["exists"] else "[dim]not present[/dim]"
        c.print(f"    {label:<9}{escape(str(entry['path']))}  ({mark})")
    c.print(f"    {'profiles':<9}{escape(str(files['profiles_dir']))}")

    t = info.get("transport")
    if t:
        loc = t.get("host") or "?"
        if t.get("port"):
            loc = f"{loc}:{t['port']}"
        c.print(f"\n  [bold]Transport[/bold]   {t['type']}  [dim]({escape(str(loc))})[/dim]")
        if t.get("bitrate"):
            c.print(f"    {'bitrate':<9}{t['bitrate']}")
        fb = info.get("fallback") or {}
        if fb.get("enabled"):
            order = fb.get("order")
            order_str = " → ".join(order) if order else "primary, then other devices"
            probe = f"fallback on ({fb.get('connect_timeout')}s probe; {order_str})"
            c.print(f"    [dim]{escape(probe)}[/dim]")
        else:
            c.print("    [dim]fallback off[/dim]")
        c.print("    [dim]resolved from config; override with --transport/--wican[/dim]")
    elif info.get("transport_error"):
        c.print(f"\n  [bold]Transport[/bold]   [red]{escape(str(info['transport_error']))}[/red]")

    w = info["wican"]
    model = w.get("model", "pro")
    model_note = "" if model == "pro" else "  [dim](AutoPID sync disabled)[/dim]"
    c.print(f"\n  [bold]WiCAN[/bold]   model: {model}{model_note}")
    devices = info.get("devices") or {}
    c.print("  [bold]Devices[/bold]")
    for alias, dev in devices.items():
        default = "  [dim](default)[/dim]" if alias == w["default"] else ""
        extra = ""
        if dev.get("transport"):
            extra = f"  [dim]{escape(str(dev['transport']))}[/dim]"
        if dev.get("port"):
            extra += f"[dim]:{dev['port']}[/dim]"
        c.print(
            f"    [cyan]{escape(f'{alias:<9}')}[/cyan]{escape(str(dev['host']))}{extra}{default}"
        )

    cfg = info["config"]
    others = {k: v for k, v in cfg.items() if k not in _SPECIAL_KEYS}
    if others:
        c.print("\n  [bold]Settings[/bold]")
        width = max(len(k) for k in others)
        for k, v in others.items():
            c.print(f"    {escape(f'{k:<{width + 2}}')}{escape(str(v))}")

    _render_dtc_log(c, info.get("dtc_log") or {})

    p = info.get("profile")
    if p:
        c.print(
            f"\n  [bold]Active profile[/bold]  [cyan]{escape(p['name'])}[/cyan]  "
            f"[dim]{escape(str(p['root']))}[/dim]"
        )
    elif info.get("profile_error"):
        c.print(
            f"\n  [bold]Active profile[/bold]  [yellow]{escape(str(info['profile_error']))}[/yellow]"
        )
    c.print(
        f"\n  [dim]Every key documented: `canair config example` · {escape(CONFIG_DOCS_URL)}[/dim]"
    )
    c.print()


def _render_dtc_log(c, dtc: dict) -> None:
    logs = dtc.get("logs") or []
    c.print(f"\n  [bold]DTC log[/bold]   {escape(str(dtc.get('dir', '?')))}")
    if not logs:
        c.print("    [dim]no scans logged yet (`canair dtc --all` records one)[/dim]")
        return
    width = max(len(e["profile"]) for e in logs)
    for e in logs:
        name = f"[cyan]{escape(e['profile'])}[/cyan]" + " " * (width - len(e["profile"]))
        if e.get("error"):
            c.print(f"    {name}  [red]{escape(str(e['error']))}[/red]")
            continue
        text = f"{e['scans']} scan(s), {e['dtcs_seen']} DTC(s) seen"
        if e.get("active"):
            n, ecus = e["open_codes"], len(e["open"])
            text += f", [yellow]{n} open on {ecus} ECU(s)[/yellow]" if n else ", none open"
        if e.get("last_scan"):
            text += f"  [dim]last {e['last_scan']}[/dim]"
        if e.get("other_car_scans"):
            text += f"  [dim](+{e['other_car_scans']} for another car or revision)[/dim]"
        c.print(f"    {name}  {text}")


def cmd_show(args) -> int:
    info = _gather(args)
    if getattr(args, "json", False):
        import json

        print(json.dumps(info, indent=2, default=str))
    else:
        _render(info)
    return 0
