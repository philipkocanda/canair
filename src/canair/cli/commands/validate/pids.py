"""Run and print ``canair validate pids|ecus``."""

from pathlib import Path

from canair.render import messages
from canair.validation.pids import check_pids


def run_pids(files: list[str] | None, stats: bool) -> int:
    report = check_pids([Path(f) for f in files] if files else None)
    all_errors, all_warnings, total_stats = report.errors, report.warnings, report.stats

    for w in all_warnings:
        messages.warning(w)

    if all_errors:
        for e in all_errors:
            messages.error(e)
        print(f"{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1

    ignored = total_stats["ignored"]
    ignored_str = f", {ignored} ignored" if ignored else ""
    ioctl = total_stats["iocontrol"]
    ioctl_str = f", {ioctl} IOControl DIDs" if ioctl else ""
    research = total_stats["research"]
    research_str = f", {research} research items" if research else ""
    routines = total_stats["routines"]
    routines_str = f", {routines} routines" if routines else ""
    ioctl_disc = total_stats["iocontrol_discoveries"]
    ioctl_disc_str = f", {ioctl_disc} IO discoveries" if ioctl_disc else ""
    dtcs = total_stats["dtcs"]
    dtcs_str = f", {dtcs} DTCs" if dtcs else ""
    print(
        f"OK — {report.n_files} ECU files, {total_stats['pids']} PIDs{ignored_str}{ioctl_str}"
        f"{research_str}{routines_str}{ioctl_disc_str}{dtcs_str}, "
        f"{total_stats['params']} signals "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Ignored:    {total_stats['ignored']}")
        print(f"  Signals:    {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")
        print(f"  IOControl:  {total_stats['iocontrol']}")
        print(f"  Research:   {total_stats['research']}")
        print(f"  Routines:   {total_stats['routines']}")
        print(f"  IO Discoveries: {total_stats['iocontrol_discoveries']}")
        print(f"  Scan-log:   {total_stats['scan_log']}")
        print(f"  DTCs:       {total_stats['dtcs']}")
        print(f"  Sessions:   {total_stats['sessions']}")

    return 0
