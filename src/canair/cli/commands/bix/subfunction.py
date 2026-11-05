"""The subfunction width and the response layout of an annotated payload."""

from __future__ import annotations

from canair.uds.layout import (
    ROLE_DID,
    ROLE_PID,
    ROLE_SID,
    ResponseLayout,
    response_layout,
    subfunction_bytes_for_pid,
)
from canair.uds.services import service_response_name

from .payload import emit_warning


def pid_sub_bytes(pid: str) -> int | None:
    """The subfunction width ``pid`` *conclusively* implies, or ``None``.

    :func:`canair.uds.layout.subfunction_bytes_for_pid` answers "2 for ``22xxxx``,
    else 1" — a **default**, not a determination. Its "else" branch swallows forms
    that say nothing about the service: the short DID form profiles also accept
    (``B004`` for ``22B004``) and standard OBD-II PIDs (``010C``) both read as
    1-byte. Deriving a width from that — or warning that an explicit ``-2``
    "contradicts" it — would state a layout with false confidence and, for a short
    DID, the wrong one. So only an explicit ``21``/``22`` service prefix counts as
    conclusive here; anything else keeps the plain 1-byte default, silently.

    The width itself still comes from the shared helper, so a conclusive PID can
    never be labelled differently here than by ``decode``/``hunt``/``coverage``.
    """
    if not pid.upper().startswith(("21", "22")):
        return None
    return subfunction_bytes_for_pid(pid)


def resolve_sub_bytes(args) -> tuple[int, bool]:
    """Effective subfunction width and whether it was derived from ``--pid``.

    Precedence: an explicit ``-1``/``-2`` > the width ``--pid`` conclusively
    implies (:func:`pid_sub_bytes`) > 1 (the ``21xx`` default). A ``--pid`` names
    the request whose echo is in the payload, so its service fixes the width — the
    derivation every other command does via
    :func:`canair.uds.layout.subfunction_bytes_for_pid`. Without it, ``--pid 22BC03``
    silently kept the 1-byte default and mislabelled the second DID echo byte as
    an unmapped data byte.
    """
    pid = getattr(args, "pid", None)
    explicit = args.sub_bytes
    if pid:
        implied = pid_sub_bytes(pid)
        if implied is not None:
            if explicit is None:
                return implied, True
            warn_sub_bytes_contradicts_pid(explicit, pid, implied)
    return (explicit if explicit is not None else 1), False


def warn_sub_bytes_contradicts_pid(sub_bytes: int, pid: str, implied: int):
    """Warn when an explicit ``-1``/``-2`` disagrees with ``--pid``'s service.

    The explicit flag still wins (it is the override), but silently honouring
    ``-1 --pid 22BC03`` reproduces the very mislabelling the derivation fixes, so
    say so. Same spirit as :func:`warn_payload_kind_mismatch`: the input
    contradicts the chosen mode. Only ever called for a *conclusive* ``--pid``
    (see :func:`pid_sub_bytes`) — warning off the 1-byte default would tell users
    to drop a correct ``-2`` from a short-form DID.
    """
    if implied == sub_bytes:
        return
    kind = "2-byte DID" if implied == 2 else "1-byte PID"
    emit_warning(
        f"-{sub_bytes} contradicts --pid {pid.upper()}, which is a {kind} subfunction.",
        f"Honouring the explicit -{sub_bytes}; the header/Signal columns will be off by one",
        f"if that is not what you meant. Drop it (or pass `-{implied}`) "
        "to use the PID's own width.",
    )


def frame_sid(frame: list[tuple[int, int | None]]) -> int | None:
    """The response SID of a reconstructed WiCAN frame (ISO-TP payload byte 0)."""
    return next((value for value, pi in frame if pi == 0), None)


def frame_sub_function(frame: list[tuple[int, int | None]]) -> int | None:
    """ISO-TP payload byte 1 of a reconstructed frame: the sub-function, where there is one."""
    return next((value for value, pi in frame if pi == 1), None)


def resolve_annotate_layout(
    frame: list[tuple[int, int | None]], explicit: int | None, pid: str | None
) -> tuple[int, ResponseLayout | None, str | None]:
    """Resolve ``(sub_bytes, layout, derived_from)`` for an annotate run.

    Evidence order, strongest first:

    1. the payload's own **response SID** — it names the service, which fixes the
       whole header shape (``0x62`` → ``DID``, ``0x71`` → ``SF`` + ``RID``, ``0x7F``
       → rejected SID + NRC). Nothing a flag or a PID string says is better
       evidence than the bytes in hand.
    2. a conclusive ``--pid`` (see :func:`pid_sub_bytes`) — a width only.
    3. the 1-byte default.

    An explicit ``-1``/``-2`` overrides all of it. When it *agrees* with the
    recognised layout the rich per-field roles are kept; when it disagrees the
    layout is dropped (its roles imply its own width, so mixing the two would
    label bytes by a shape the user just rejected) and the generic ``SID`` +
    ``PID``/``DID`` labelling is used instead, with a warning.
    """
    sid = frame_sid(frame)
    layout = response_layout(sid, frame_sub_function(frame)) if sid is not None else None
    # Carry the conclusive PID and its width together, so the non-optional name is
    # available wherever the width is (and neither can be used without the other).
    from_pid: tuple[str, int] | None = None
    if pid:
        width = pid_sub_bytes(pid)
        if width is not None:
            from_pid = (pid, width)
    if layout is not None:
        if explicit is None:
            return layout.subfunction_bytes, layout, f"SID 0x{sid:02X}"
        if explicit == layout.subfunction_bytes:
            return explicit, layout, None
        warn_sub_bytes_contradicts_sid(explicit, layout)
        return explicit, None, None
    if explicit is not None:
        # No recognisable service, so --pid is the only other evidence a
        # contradicting override can be checked against.
        if from_pid is not None:
            warn_sub_bytes_contradicts_pid(explicit, from_pid[0], from_pid[1])
        return explicit, None, None
    if from_pid is not None:
        return from_pid[1], None, f"--pid {from_pid[0].upper()}"
    return 1, None, None


def warn_sub_bytes_contradicts_sid(sub_bytes: int, layout: ResponseLayout):
    """Warn when an explicit ``-1``/``-2`` disagrees with the payload's own service."""
    name = service_response_name(layout.resp_sid) or "unknown service"
    shape = " + ".join(f.role if f.width == 1 else f"{f.role}({f.width}B)" for f in layout.fields)
    emit_warning(
        f"-{sub_bytes} contradicts the payload: SID 0x{layout.resp_sid:02X} is {name},",
        f"whose header is SID + {shape} ({layout.subfunction_bytes} byte(s) after the SID).",
        f"Honouring the explicit -{sub_bytes} and falling back to generic role labels; "
        f"drop it to use the service's own layout.",
    )


def generic_role(isotp_idx: int, sub_bytes: int) -> str:
    """Role of a payload byte with no recognised service: SID then PID/DID by width."""
    if isotp_idx == 0:
        return ROLE_SID
    if isotp_idx < 1 + sub_bytes:
        return ROLE_DID if sub_bytes == 2 else ROLE_PID
    return ""
