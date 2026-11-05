"""The human report of ``canair status``: one Rich block per area (transport, profile, WiCAN, lock).

Everything that came from the device, the profile or the environment goes through ``escape``
before it reaches markup, so a name or a path with ``[`` in it prints as written.
"""

from __future__ import annotations

from canair.render import glyphs
from canair.render.console import console as make_console
from canair.render.console import escape


def _firmware_line(fw: dict) -> str:
    """One rendered line for the probed firmware (device strings markup-escaped)."""
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


def render_status(info: dict) -> None:
    """Print the report for ``info`` (the dict ``status.gather`` builds)."""
    c = make_console()
    ver = info.get("canair_version")
    if ver:
        build = f"  [dim]({escape(info['canair_build'])})[/dim]" if info.get("canair_build") else ""
        c.print(f"\n  [bold]canair[/bold]     {escape(str(ver))}{build}")
    t = info.get("transport")
    if t:
        loc = t.get("host") or "?"
        if t.get("port"):
            loc = f"{loc}:{t['port']}"
        usable = t.get("usable")
        mark = "[green]✓[/green]" if usable else "[red]✗[/red]"
        c.print(
            f"\n  [bold]Transport[/bold]  {escape(str(t['type']))}  "
            f"[dim]({escape(loc)})[/dim]  {mark}"
        )
        summary = t.get("summary")
        if summary:
            c.print(f"             [dim]{escape(str(summary))}[/dim]")
        if t.get("bitrate"):
            c.print(f"             [dim]bitrate:[/dim]  {escape(str(t['bitrate']))}")
        if t.get("fell_back"):
            c.print(
                f"             [yellow]fell back from {escape(str(t['selected']))}[/yellow] "
                "[dim](a live command would use this device too)[/dim]"
            )
        for note in info.get("notes", []):
            c.print(f"             [dim]{escape(str(note))}[/dim]")

    p = info.get("profile")
    if p:
        c.print(
            f"  [bold]Profile[/bold]    {escape(str(p['name']))}  "
            f"[dim]{escape(str(p['root']))}[/dim]"
        )
        if p.get("variant"):
            c.print(
                f"  [bold]Variant[/bold]    {escape(str(p['variant']))}  "
                f"[dim](from {escape(str(p['variant_source']))})[/dim]"
            )
        from canair.variants.revisions import Resolution, describe_resolution

        for name, r in (p.get("revisions") or {}).items():
            res = Resolution(name, r["revision"], r["source"], tuple(r["possible"]))
            c.print(f"  [bold]Revision[/bold]   {escape(name)} {escape(describe_resolution(res))}")

    d = info.get("device")
    fw = info.get("firmware") or {}
    fw_known = fw.get("generation") in ("v4", "v6")
    if fw_known or (d and d["http_reachable"]):
        c.print("\n  [bold]WiCAN[/bold]")
        if fw_known:
            c.print(f"    firmware   {_firmware_line(fw)}")
    if d:
        if d["http_reachable"]:
            c.print(
                f"    protocol   [cyan]{escape(str(d['protocol']))}[/cyan]  "
                f"[dim](socket port {d['socket_port']})[/dim]"
            )
            fw_version = d.get("fw_version")
            hw = d.get("hw_version")
            if not fw_known and (fw_version or hw):
                parts = []
                if hw:
                    parts.append(escape(str(hw)))
                if fw_version:
                    parts.append(f"fw {escape(str(fw_version))}")
                git = d.get("git_version")
                line = "  ".join(parts)
                if git:
                    line += f"  [dim]({escape(str(git))})[/dim]"
                c.print(f"    version    {line}")
            slp = d.get("sleep")
            if slp is not None:
                c.print(
                    f"    sleep      {escape(str(slp))}  "
                    f"[dim](threshold {d.get('sleep_volt')}V)[/dim]"
                )
            if d.get("battery") is not None:
                c.print(f"    battery    {escape(str(d['battery']))}")
            if d.get("ip"):
                c.print(f"    ip         {escape(str(d['ip']))}")
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
            who = escape(str(lock.get("summary") or "another session"))
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
            c.print(f"             [yellow]{escape(str(lock['error']))}[/yellow]")

    for w in info.get("warnings", []):
        c.print(f"  [yellow]{glyphs.WARN} {escape(str(w))}[/yellow]")
    for err in info.get("errors", []):
        c.print(f"  [red]{glyphs.FAIL} {escape(str(err))}[/red]")
    if not info.get("warnings") and not info.get("errors") and t and t.get("usable"):
        c.print("\n  [green]Ready.[/green]")
    notice = info.get("update_notice")
    if notice:
        c.print(f"\n  [dim yellow]↑ {escape(str(notice))}[/dim yellow]")
    c.print()
