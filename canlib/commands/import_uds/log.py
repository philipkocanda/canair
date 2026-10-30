"""``canair import uds --from-log``: extract UDS replies from a recorded frame log.

The CLI half of :mod:`canlib.captures.from_log`: read the log through the same
readers as ``import can``, narrow it (bus, log-time window), extract the replies of
every ECU in the active profile, and record them as one imported session with
per-reply timestamps. A dry run reports what would be recorded without writing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

from canlib import ansi
from canlib.captures.from_log import Extraction, extract_replies, stamp
from canlib.captures.types import CaptureSession, Quality

# A log whose first frame is later than this is on the wall clock (2001-09-09), not
# relative to the start of the recording; same cut-off as ``import can``.
_ABSOLUTE_EPOCH = 1_000_000_000
_NORMAL_ADDRESSING = ("normal_11bit", "normal_29bit", "normal_fixed_29bit")


class LogImportError(Exception):
    """The log cannot be turned into a session; the message says what to change."""


def check_args(args: argparse.Namespace) -> None:
    """Reject option combinations that mean nothing in log mode."""
    if args.spec:
        raise LogImportError("give either ECU:PID=PAYLOAD captures or --from-log, not both")
    if args.capture_note:
        raise LogImportError("--capture-note applies to ECU:PID=PAYLOAD captures, not --from-log")
    if args.from_ts is not None and args.until_ts is not None and args.from_ts >= args.until_ts:
        raise LogImportError("--from-ts must be earlier than --until-ts")


def run_from_log(args: argparse.Namespace) -> int:
    from canlib.profile import active
    from canlib.variants.selection import require_named_car

    require_named_car(active(), "import uds")
    try:
        check_args(args)
        return _import_log(args)
    except LogImportError as e:
        print(f"{ansi.RED}error: {e}{ansi.RESET}", file=sys.stderr)
        return 2


def _import_log(args: argparse.Namespace) -> int:
    from canlib import can_logs
    from canlib.addressing import resolve_mode
    from canlib.captures import build_manual_session, save_session
    from canlib.ecus import load_ecus
    from canlib.pids import load_pids
    from canlib.states import parse_states

    mode = str(resolve_mode(load_pids()))
    if mode not in _NORMAL_ADDRESSING:
        raise LogImportError(
            f"this profile uses {mode} addressing, whose ISO-TP frames carry a target-address "
            "byte before the PCI byte; passive reassembly of such a log is not supported"
        )
    ecus = load_ecus()
    if not ecus:
        raise LogImportError("the active profile has no ECUs; register them first (canair ecu add)")

    path = Path(args.from_log)
    if not path.is_file():
        raise LogImportError(f"no such file: {path}")
    try:
        fmt = can_logs.detect_format(path, args.log_format)
        extraction = extract_replies(
            _select_frames(can_logs.iter_frames(path, fmt), args),
            ecus,
            keep_negative=args.keep_nrc,
        )
    except can_logs.CanLogError as e:
        raise LogImportError(str(e)) from e
    if not extraction.replies:
        _report(extraction, path, json_out=False, file=sys.stderr)
        raise LogImportError(
            "no data-read replies from a registered ECU in the selected frames"
            + (" (see the unregistered ids above)" if extraction.unregistered else "")
        )

    start = _start_datetime(args, extraction.replies[0].timestamp)
    try:
        captures = stamp(extraction.replies, start)
    except ValueError as e:
        raise LogImportError(str(e)) from e

    notes = "; ".join(
        part
        for part in (args.notes, f"Passively extracted from {path.name} (ISO-TP replies on a tap)")
        if part
    )
    quality: Quality = {"exchanges": len(captures) + extraction.lost}
    if extraction.lost:
        quality["drop"] = extraction.lost
    session = build_manual_session(
        captures,
        label=args.label,
        date=start.date().isoformat(),
        vehicle_states=parse_states(args.state) if args.state else [],
        notes=notes,
        quality=quality,
    )

    target_dir = args.dir if args.dir is not None else _active_captures_dir()
    if not args.dry_run:
        duplicate = _identical_session(target_dir, session)
        if duplicate is not None:
            raise LogImportError(
                f"{duplicate.name} already holds an identical session; remove it first "
                "(canair captures uds --delete) if you mean to import again"
            )
        # The saved-to banner is for people; --json promises machine-readable stdout.
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            fpath = save_session(session, args.dir)
    else:
        fpath = None

    _report(extraction, path, json_out=args.json, file=sys.stdout, written=fpath, session=session)
    return 0


def _select_frames(frames, args: argparse.Namespace):
    """The frames on the chosen bus inside the chosen log-time window."""
    for frame in frames:
        if args.bus is not None and frame.channel != args.bus:
            continue
        ts = frame.timestamp
        if args.from_ts is not None and ts < args.from_ts:
            continue
        if args.until_ts is not None and ts > args.until_ts:
            continue
        yield frame


def _start_datetime(args: argparse.Namespace, first_ts: float) -> datetime:
    """Wall-clock time of the first reply: the flags, else the log's own clock, else midnight."""
    logged = datetime.fromtimestamp(first_ts) if first_ts > _ABSOLUTE_EPOCH else None
    try:
        day = date.fromisoformat(args.date) if args.date else None
        at = datetime.strptime(args.time, "%H:%M:%S").time() if args.time else None
    except ValueError as e:
        raise LogImportError(f"--date must be YYYY-MM-DD and --time HH:MM:SS ({e})") from e
    if logged is None and day is None:
        print(
            f"{ansi.YELLOW}  warning: the log has relative timestamps and no --date was given; "
            f"dating the session today{ansi.RESET}",
            file=sys.stderr,
        )
    return datetime.combine(
        day or (logged.date() if logged else date.today()),
        at or (logged.time() if logged else time(0)),
    )


def _active_captures_dir() -> Path:
    from canlib.profile import active

    return active().captures_dir


def _identical_session(captures_dir: Path, session: CaptureSession) -> Path | None:
    """The day file already holding a session with this label and these captures, if any."""
    from canlib.captures import io as capture_io

    fpath = captures_dir / f"{session['date']}{capture_io.CAPTURE_SUFFIX}"
    if not fpath.exists():
        return None
    for existing in capture_io.load_capture_file(fpath).get("sessions", []):
        if (
            existing.get("label") == session["label"]
            and existing.get("captures") == session["captures"]
        ):
            return fpath
    return None


def _report(
    extraction: Extraction,
    path: Path,
    *,
    json_out: bool,
    file,
    written: Path | None = None,
    session: CaptureSession | None = None,
) -> None:
    by_pid = Counter((r.rx, r.pid) for r in extraction.replies)
    stats = extraction.reassembly
    if json_out:
        json.dump(
            {
                "source": path.name,
                "file": str(written) if written else None,
                "dry_run": written is None,
                "date": session["date"] if session else None,
                "captures": len(extraction.replies),
                "by_pid": {f"{rx}:{pid}": n for (rx, pid), n in sorted(by_pid.items())},
                "lost": extraction.lost,
                "batched_skipped": extraction.batched_skipped,
                "negatives_skipped": extraction.negatives_skipped,
                "other_services": {f"0x{s:02X}": n for s, n in extraction.other_services.items()},
                "unregistered": {f"0x{i:X}": n for i, n in extraction.unregistered.items()},
            },
            file,
            indent=2,
        )
        print(file=file)
        return

    verb = "Extracted" if written else "Would extract"
    print(f"{verb} {len(extraction.replies)} reply(ies) from {path.name}", file=file)
    for (rx, pid), n in sorted(by_pid.items()):
        print(f"  {ansi.GREEN}✓{ansi.RESET} {rx} {pid}  ×{n}", file=file)
    if written is None and extraction.replies:
        print("  (dry run: nothing written)", file=file)

    left_out = [
        (stats.truncated, "multi-frame replies cut short (a frame was lost); not recorded"),
        (stats.orphan_runs, "multi-frame replies whose first frame was lost; not recorded"),
        (stats.malformed, "frames with an ISO-TP header this reader does not understand"),
        (extraction.batched_skipped, "replies to batched requests (cannot be split safely)"),
        (extraction.negatives_skipped, "negative responses (pass --keep-nrc to record them)"),
    ]
    for count, what in left_out:
        if count:
            print(f"  {ansi.YELLOW}skipped {count} {what}{ansi.RESET}", file=file)
    if extraction.other_services:
        sids = ", ".join(f"0x{s:02X} ×{n}" for s, n in extraction.other_services.most_common(5))
        print(f"  {ansi.DIM}ignored replies of other services: {sids}{ansi.RESET}", file=file)
    if extraction.unregistered:
        ids = ", ".join(f"0x{i:X} (×{n})" for i, n in extraction.unregistered.most_common(6))
        print(
            f"  {ansi.YELLOW}replies from ids not in the profile: {ids}; "
            f"register with canair ecu add{ansi.RESET}",
            file=file,
        )
    if extraction.lost:
        print(
            f"  {ansi.DIM}session quality records {extraction.lost} dropped message(s){ansi.RESET}",
            file=file,
        )
