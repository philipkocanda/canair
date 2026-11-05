"""Reporting a subfunction width that contradicts its evidence."""

from __future__ import annotations

from canair.uds.services import service_response_name

from .payload import emit_warning
from .rows import WidthConflict, resolve_width


def resolve_sub_bytes(args) -> tuple[int, bool]:
    """Effective subfunction width and whether it was derived from ``--pid``.

    The rule is :func:`.rows.resolve_width`; an explicit width that contradicts
    ``--pid`` is reported here, and still wins.
    """
    width = resolve_width(args.sub_bytes, getattr(args, "pid", None))
    if width.conflict is not None:
        report_conflict(width.conflict)
    return width.sub_bytes, width.derived


def report_conflict(conflict: WidthConflict) -> None:
    """Warn that an explicit ``-1``/``-2`` disagrees with its evidence.

    The explicit flag still wins (it is the override), but silently honouring
    ``-1 --pid 22BC03`` reproduces the very mislabelling the derivation fixes, so
    say so. Same spirit as :func:`.payload.warn_payload_kind_mismatch`: the input
    contradicts the chosen mode.
    """
    sub_bytes, implied = conflict.explicit, conflict.implied
    if conflict.layout is not None:
        layout = conflict.layout
        name = service_response_name(layout.resp_sid) or "unknown service"
        shape = " + ".join(
            f.role if f.width == 1 else f"{f.role}({f.width}B)" for f in layout.fields
        )
        emit_warning(
            f"-{sub_bytes} contradicts the payload: SID 0x{layout.resp_sid:02X} is {name},",
            f"whose header is SID + {shape} ({layout.subfunction_bytes} byte(s) after the SID).",
            f"Honouring the explicit -{sub_bytes} and falling back to generic role labels; "
            f"drop it to use the service's own layout.",
        )
        return
    assert conflict.pid is not None
    kind = "2-byte DID" if implied == 2 else "1-byte PID"
    emit_warning(
        f"-{sub_bytes} contradicts --pid {conflict.pid.upper()}, which is a {kind} subfunction.",
        f"Honouring the explicit -{sub_bytes}; the header/Signal columns will be off by one",
        f"if that is not what you meant. Drop it (or pass `-{implied}`) "
        "to use the PID's own width.",
    )
