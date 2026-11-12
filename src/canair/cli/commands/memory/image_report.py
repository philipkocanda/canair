"""Text and JSON for ``canair memory image``: the plan it asks about, and the final report."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from canair.live.memory.image import ImageRun
from canair.memory.job import DATA, FULL
from canair.memory.verify import FIRST_READ_ERROR, REREAD_ERROR, SAME, VOLATILE
from canair.render import messages
from canair.render.formatting import duration_text, size_text
from canair.render.glyphs import EMPTY, OK

from .render import span_text

_VERDICTS = {
    SAME: "same",
    REREAD_ERROR: "the re-read was a link error",
    FIRST_READ_ERROR: "the first read was a link error (replaced)",
    VOLATILE: "volatile",
}


def resume_command(run: ImageRun) -> str | None:
    if run.dump is None or not run.dump.resumable:
        return None
    return f"canair memory image --resume {run.dump.id}"


def plan_lines(run: ImageRun) -> list[str]:
    """The plan as the user approves it: areas, reads, time, block and conditions."""
    plan, found, est = run.plan, run.survey, run.estimate
    if plan is None:
        return []
    width = 6 if plan.end <= 0xFFFFFF else 8
    lines = [f"  {run.ecu} · {plan.mode} image · software {plan.software or 'unknown'}"]
    if found is not None:
        flash = f"flash {size_text(found.flash_size)}" if found.flash_size else "flash size unknown"
        alias = (
            f"repeats every {size_text(found.alias)} (reads the first)"
            if found.alias
            else "no aliasing"
        )
        lines.append(f"  map: {found.mapped_from} · {flash} · {alias}")
    lines.append("  areas:")
    for a in plan.areas:
        how = (
            "read in full"
            if a.kind == DATA or plan.mode != "sparse"
            else f"{plan.sample_size} B every {size_text(plan.sample_every)}"
        )
        lines.append(
            f"    {span_text(a.start, a.end, width)}  {size_text(a.size):>10}  {a.kind:<7} {how}"
        )
    extents = plan.extents()
    full = sum(1 for e in extents if e.kind == FULL)
    lines.append(f"  {len(extents)} extents of {size_text(plan.extent_size)}, {full} read in full")
    if est is not None:
        lines.append(
            f"  {size_text(est.full_bytes)} in full, {size_text(est.sampled_bytes)} sampled · "
            f"about {est.reads} reads · about {duration_text(est.seconds)}"
        )
    conditions = [f"block {plan.ceiling}"]
    if plan.state:
        conditions.append(f"state {plan.state}")
    if plan.health:
        conditions.append(f"health {plan.health}")
    lines.append("  " + " · ".join(conditions))
    return lines


def print_plan(run: ImageRun, stream: TextIO) -> None:
    print("\n".join(["", *plan_lines(run), ""]), file=stream)


def image_doc(run: ImageRun) -> dict[str, Any]:
    dump = run.dump
    return {
        "ecu": run.ecu,
        "dump": dump.id if dump is not None else None,
        "path": str(dump.path) if dump is not None and dump.path else None,
        "complete": run.complete,
        "resumed": run.resumed,
        "declined": run.declined,
        "stopped": run.stopped,
        "saved": run.saved,
        "resume": resume_command(run),
        "software": run.software,
        "plan": run.plan.to_doc() if run.plan is not None else None,
        "estimate": (
            {"reads": run.estimate.reads, "seconds": round(run.estimate.seconds)}
            if run.estimate is not None
            else None
        ),
        "reads": run.reads,
        "seconds": round(run.seconds, 1),
        "bytes_read": run.bytes_now,
        "rate": (
            {"block": run.rate.block, "throttle_ms": run.rate.throttle_ms} if run.rate else None
        ),
        "verdicts": dict(run.verdicts),
        "pauses": dump.pauses if dump is not None else [],
        "fault_codes": [
            {"ecu": c.ecu, "new": list(c.new), "gone": list(c.gone), "unknown": c.unknown}
            for c in run.dtc_changes
        ],
        "written": str(run.written) if run.written else None,
        "identity": {
            f: {"text": text, "addresses": hits} for f, (text, hits) in run.identity.items()
        },
    }


def _report_lines(run: ImageRun) -> list[str]:
    dump = run.dump
    assert dump is not None
    state = "complete" if run.complete else "incomplete"
    lines = [f"\n  {dump.id} · {state} · {size_text(dump.bytes_read)} in the dump"]
    speed = run.bytes_now / run.seconds if run.seconds else 0
    block = f" · block {run.rate.block}" if run.rate else ""
    lines.append(
        f"  this run: {size_text(run.bytes_now)} in {duration_text(run.seconds)} "
        f"({speed:.0f} B/s){block} · {run.reads} reads"
    )
    for p in dump.pauses:
        how = p.get("how", "")
        lines.append(f"  paused {p.get('at', '?')}: {p.get('reason', '')} ({how})")
    if run.verdicts:
        parts = ", ".join(f"{n} {_VERDICTS.get(v, v)}" for v, n in sorted(run.verdicts.items()))
        lines.append(f"  read twice: {sum(run.verdicts.values())} extents ({parts})")
    for c in run.dtc_changes:
        if c.unknown:
            lines.append(f"  fault codes {c.ecu}: not compared (no answer)")
        elif c.new or c.gone:
            new, gone = ", ".join(c.new) or EMPTY, ", ".join(c.gone) or EMPTY
            lines.append(f"  fault codes {c.ecu}: new {new}, gone {gone}")
    if run.dtc_changes and not any(c.new or c.gone for c in run.dtc_changes):
        lines.append(f"  fault codes: no change on {len(run.dtc_changes)} ECU(s)")
    for f, (text, hits) in run.identity.items():
        where = ", ".join(f"0x{h:X}" for h in hits[:4]) if hits else "not found"
        lines.append(f"  identity {f} {text}: {where}")
    return lines


def print_written(run: ImageRun) -> None:
    if run.written is not None:
        print(f"  {OK} Recorded the map in {run.written}", file=sys.stderr)


def print_report(run: ImageRun, *, as_json: bool) -> None:
    """The result on stdout; new fault codes, stops and the resume hint on stderr."""
    for change in run.dtc_changes:
        if change.new:
            messages.warning(f"new fault code(s) on {change.ecu}: {', '.join(change.new)}")
    if as_json:
        print(json.dumps(image_doc(run), indent=2))
    elif run.dump is not None:
        print("\n".join(_report_lines(run)))
    print_written(run)
    if run.stopped:
        messages.error(f"the image stopped: {run.stopped}")
    elif run.saved:
        messages.warning(f"the image paused and was saved: {run.saved}")
    if run.dump is not None and run.dump.path is not None:
        print(f"  {OK} Saved to {run.dump.path} (local only; never shared)", file=sys.stderr)
    if (command := resume_command(run)) is not None:
        messages.hint(f"read on later with `{command}`")
