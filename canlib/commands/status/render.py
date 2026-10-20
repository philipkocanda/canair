"""Rendering ``canair status``'s gathered report for a terminal (rich markup)."""

from __future__ import annotations


def _firmware_line(fw: dict) -> str:
    """One rendered line for the probed firmware (device strings markup-escaped)."""
    from rich.markup import escape

    parts = [escape(str(fw["hw_version"]))] if fw.get("hw_version") else []
    parts.append(escape(str(fw["label"])))
    extra = []
    if fw.get("generation") == "v6":
        if fw.get("build"):
            extra.append(f"build {escape(str(fw['build']))}")
        if fw.get("api_level") is not None:
            extra.append(f"api_level {fw['api_level']}")
    if not fw.get("checked"):
        extra.append("firmware check off")
    line = "  ".join(parts)
    if extra:
        line += f"  [dim]({', '.join(extra)})[/dim]"
    return line


def _v6_device_lines(dev: dict | None) -> list[str]:
    """The sleep, battery and OBD-chip lines for a v6 device (device strings escaped)."""
    from rich.markup import escape

    if not dev:
        return []
    lines = []
    if dev.get("sleep_enabled") is not None:
        state = "enable" if dev["sleep_enabled"] else "disable"
        extra = [f"threshold {dev['sleep_v']:.2f}V"] if dev.get("sleep_v") is not None else []
        if dev.get("sleep_state") and dev["sleep_state"] != "normal":
            extra.append(escape(str(dev["sleep_state"])))
        tail = f"  [dim]({', '.join(extra)})[/dim]" if extra else ""
        lines.append(f"sleep      {state}{tail}")
    if dev.get("battery_v") is not None:
        lines.append(f"battery    {dev['battery_v']:.2f}V")
    if dev.get("obd_chip_ready") is not None:
        ready = "ready" if dev["obd_chip_ready"] else "[yellow]not ready[/yellow]"
        claim = dev.get("obd_chip_claim")
        tail = f"  [dim](claim: {escape(str(claim))})[/dim]" if claim and claim != "none" else ""
        lines.append(f"obd chip   {ready}{tail}")
    return lines


def _render(info: dict) -> None:
    from rich.console import Console

    c = Console()
    ver = info.get("canair_version")
    if ver:
        c.print(f"\n  [bold]canair[/bold]     {ver}")
    t = info.get("transport")
    if t:
        loc = t.get("host") or "?"
        if t.get("port"):
            loc = f"{loc}:{t['port']}"
        usable = t.get("usable")
        mark = "[green]✓[/green]" if usable else "[red]✗[/red]"
        c.print(f"\n  [bold]Transport[/bold]  {t['type']}  [dim]({loc})[/dim]  {mark}")
        summary = t.get("summary")
        if summary:
            c.print(f"             [dim]{summary}[/dim]")
        if t.get("bitrate"):
            c.print(f"             [dim]bitrate:[/dim]  {t['bitrate']}")
        if t.get("fell_back"):
            c.print(
                f"             [yellow]fell back from {t['selected']}[/yellow] "
                "[dim](a live command would use this device too)[/dim]"
            )
        for note in info.get("notes", []):
            c.print(f"             [dim]{note}[/dim]")

    p = info.get("profile")
    if p:
        c.print(f"  [bold]Profile[/bold]    {p['name']}  [dim]{p['root']}[/dim]")

    d = info.get("device")
    fw = info.get("firmware") or {}
    fw_known = fw.get("generation") in ("v4", "v6")
    if fw_known or (d and d["http_reachable"]):
        c.print("\n  [bold]WiCAN[/bold]")
        if fw_known:
            c.print(f"    firmware   {_firmware_line(fw)}")
    for line in _v6_device_lines(info.get("device_v6")):
        c.print(f"    {line}")
    if d:
        if d["http_reachable"]:
            c.print(
                f"    protocol   [cyan]{d['protocol']}[/cyan]  "
                f"[dim](socket port {d['socket_port']})[/dim]"
            )
            fw_version = d.get("fw_version")
            hw = d.get("hw_version")
            if not fw_known and (fw_version or hw):
                parts = []
                if hw:
                    parts.append(str(hw))
                if fw_version:
                    parts.append(f"fw {fw_version}")
                git = d.get("git_version")
                line = "  ".join(parts)
                if git:
                    line += f"  [dim]({git})[/dim]"
                c.print(f"    version    {line}")
            slp = d.get("sleep")
            if slp is not None:
                c.print(f"    sleep      {slp}  [dim](threshold {d.get('sleep_volt')}V)[/dim]")
            if d.get("battery") is not None:
                c.print(f"    battery    {d['battery']}")
            if d.get("ip"):
                c.print(f"    ip         {d['ip']}")
        elif fw_known:
            c.print("    config     [yellow]HTTP config API not reachable[/yellow]")
        else:
            c.print(
                "\n  [bold]WiCAN[/bold]   [yellow]HTTP config API not reachable[/yellow] "
                "[dim](non-WiCAN gateway, offline, or wrong host)[/dim]"
            )

    lock = info.get("lock")
    if lock:
        if lock.get("held"):
            who = lock.get("summary") or "another session"
            if lock.get("ours"):
                who += " — this process"
            c.print(f"\n  [bold]Lock[/bold]       [yellow]held[/yellow] by {who}")
            c.print("             [dim]one session at a time — `canair lock` for detail[/dim]")
            if lock.get("steal_pending"):
                c.print(
                    f"             [dim]release requested by PID "
                    f"{lock['steal_pending']['by']}[/dim]"
                )
        else:
            c.print("\n  [bold]Lock[/bold]       [green]free[/green]")
        if lock.get("error"):
            c.print(f"             [yellow]{lock['error']}[/yellow]")

    for w in info.get("warnings", []):
        c.print(f"  [yellow]⚠ {w}[/yellow]")
    for e in info.get("errors", []):
        c.print(f"  [red]✖ {e}[/red]")
    if not info.get("warnings") and not info.get("errors") and t and t.get("usable"):
        c.print("\n  [green]Ready.[/green]")
    notice = info.get("update_notice")
    if notice:
        c.print(f"\n  [dim yellow]↑ {notice}[/dim yellow]")
    c.print()
