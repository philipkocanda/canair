"""The byte-by-byte annotation of a UDS response payload, as data.

What ``canair bix --annotate`` shows for each byte: its WiCAN, ISO-TP, Torque and bix
indices, its role, and the defined signal that reads it. Nothing here prints.
"""

from __future__ import annotations

from dataclasses import dataclass

from canair.uds.byteindex import (
    isotp_to_torque,
    mapped_bits,
    mapped_offsets,
    torque_label,
    torque_to_bix,
)
from canair.uds.layout import (
    ROLE_DID,
    ROLE_PCI,
    ROLE_PID,
    ROLE_SID,
    ResponseLayout,
    response_layout,
    subfunction_bytes_for_pid,
)
from canair.uds.support_map import MapDecl, ResolvedMap, resolve

Frame = list[tuple[int, int | None]]


@dataclass(frozen=True)
class WidthConflict:
    """An explicit ``-1``/``-2`` that disagrees with the evidence for the width.

    The evidence is either a ``pid`` that names its service or the payload's own
    ``layout``; exactly one of the two is set.
    """

    explicit: int
    implied: int
    pid: str | None = None
    layout: ResponseLayout | None = None


@dataclass(frozen=True)
class Width:
    """The subfunction width of a payload-less view, and where it came from."""

    sub_bytes: int
    derived: bool
    conflict: WidthConflict | None = None


@dataclass(frozen=True)
class AnnotateLayout:
    """The width and layout chosen for an annotated payload."""

    sub_bytes: int
    layout: ResponseLayout | None
    derived_from: str | None
    conflict: WidthConflict | None = None


@dataclass(frozen=True)
class ByteRow:
    """One byte of the annotated payload. A ``None`` index or cell is shown as a dash."""

    wican: int
    isotp: int | None
    value: int
    role: str
    torque: str | None
    bix: int | None
    signal: str | None


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


def resolve_width(explicit: int | None, pid: str | None) -> Width:
    """Effective subfunction width and whether it was derived from ``pid``.

    Precedence: an explicit ``-1``/``-2`` > the width ``pid`` conclusively
    implies (:func:`pid_sub_bytes`) > 1 (the ``21xx`` default). A ``--pid`` names
    the request whose echo is in the payload, so its service fixes the width — the
    derivation every other command does via
    :func:`canair.uds.layout.subfunction_bytes_for_pid`. Without it, ``--pid 22BC03``
    silently kept the 1-byte default and mislabelled the second DID echo byte as
    an unmapped data byte. An explicit width that disagrees with ``pid`` still wins
    and is returned as a conflict.
    """
    conflict = None
    if pid:
        implied = pid_sub_bytes(pid)
        if implied is not None:
            if explicit is None:
                return Width(implied, True)
            if implied != explicit:
                conflict = WidthConflict(explicit, implied, pid=pid)
    return Width(explicit if explicit is not None else 1, False, conflict)


def frame_sid(frame: Frame) -> int | None:
    """The response SID of a reconstructed WiCAN frame (ISO-TP payload byte 0)."""
    return next((value for value, pi in frame if pi == 0), None)


def frame_sub_function(frame: Frame) -> int | None:
    """ISO-TP payload byte 1 of a reconstructed frame: the sub-function, where there is one."""
    return next((value for value, pi in frame if pi == 1), None)


def resolve_annotate_layout(frame: Frame, explicit: int | None, pid: str | None) -> AnnotateLayout:
    """Resolve the width and layout for an annotate run.

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
    ``PID``/``DID`` labelling is used instead, with a conflict to report.
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
            return AnnotateLayout(layout.subfunction_bytes, layout, f"SID 0x{sid:02X}")
        if explicit == layout.subfunction_bytes:
            return AnnotateLayout(explicit, layout, None)
        return AnnotateLayout(
            explicit, None, None, WidthConflict(explicit, layout.subfunction_bytes, layout=layout)
        )
    if explicit is not None:
        # No recognisable service, so --pid is the only other evidence a
        # contradicting override can be checked against.
        conflict = None
        if from_pid is not None and from_pid[1] != explicit:
            conflict = WidthConflict(explicit, from_pid[1], pid=from_pid[0])
        return AnnotateLayout(explicit, None, None, conflict)
    if from_pid is not None:
        return AnnotateLayout(from_pid[1], None, f"--pid {from_pid[0].upper()}")
    return AnnotateLayout(1, None, None)


def generic_role(isotp_idx: int, sub_bytes: int) -> str:
    """Role of a payload byte with no recognised service: SID then PID/DID by width."""
    if isotp_idx == 0:
        return ROLE_SID
    if isotp_idx < 1 + sub_bytes:
        return ROLE_DID if sub_bytes == 2 else ROLE_PID
    return ""


def annotate_rows(
    frame: Frame,
    payload: bytes,
    layout: ResponseLayout | None,
    sub_bytes: int,
    *,
    params: dict | None = None,
    support: MapDecl | None = None,
    torque_columns: bool = False,
) -> list[ByteRow]:
    """One row per byte of the reconstructed ``frame``.

    Per-byte roles come from the payload's own service when it is recognised, so
    every header field is named (``SF``/``RID``/``CTRL``/``NRC``/…); otherwise the
    width-based ``SID`` + ``PID``/``DID`` labelling applies. With ``params`` (a PID's
    ``parameters`` dict) each row also carries the signal that reads the byte, or
    ``unmapped``. A declared ``support`` map adds its own bytes, the covered bytes that
    carry no signal, and the marked bits of an unmapped byte; ``payload`` is the
    reassembled response it is read from.

    Every notation is derived from the byte's ACTUAL ISO-TP index in the
    reconstructed frame, so single-frame (1 PCI byte) and multi-frame
    (2 FF + 1/CF PCI bytes) payloads are both correct. The length-agnostic
    ``wican_to_isotp`` assumes the multi-frame layout and is off by one for
    single-frame responses. Torque and bix are only computed with ``torque_columns``
    (the letter has a boundary past ``ZZ`` that a hidden column should not trip).
    """
    overlay = params is not None
    mapped = mapped_offsets(params) if params is not None else {}
    mbits = mapped_bits(params) if params is not None else {}
    resolved = None
    if overlay and support is not None:
        resolved = resolve(support, payload, 1 + sub_bytes)

    rows = []
    for w, (value, pi) in enumerate(frame):
        if pi is None:
            role = ROLE_PCI
        elif layout is not None:
            role = layout.role_at(pi) or ""
        else:
            role = generic_role(pi, sub_bytes)
        torque = bix = letter = None
        if torque_columns and pi is not None:
            torque = isotp_to_torque(pi, sub_bytes)
            bix = torque_to_bix(torque) if torque is not None else None
            letter = torque_label(torque)
        signal = param_cell(w, value, role, mapped, mbits) if overlay else None
        if resolved is not None and pi is not None:
            signal = support_cell(resolved, pi, signal or "")
        rows.append(ByteRow(w, pi, value, role, letter or None, bix, signal))
    return rows


def support_cell(rm: ResolvedMap, index: int, cell: str) -> str:
    """Fold the declared support map into a byte's Param cell (``index`` is the ISO-TP index)."""
    if index in rm.map_indices:
        return "support map"
    if index not in rm.covered:
        return cell
    marks = sorted(rm.signal_bits(index))
    if not marks:
        return "not a signal" if cell == "unmapped" else cell
    if cell == "unmapped" and rm.decl.unit == "bit":
        return f"unmapped (marked bits {','.join(map(str, marks))})"
    return cell


def param_cell(offset: int, byte_val: int, role: str, mapped: dict, mbits: dict) -> str:
    """The Param-overlay cell for one byte: covering param(s), or 'unmapped'.

    Any non-empty ``role`` is ISO-TP framing or a UDS header field, which a
    parameter can never read — an empty role is the only thing that means "data",
    so new roles (``SF``/``RID``/``CTRL``/``NRC``/…) are excluded automatically.
    """
    if role:
        return ""
    bit_hits = sorted((k, mbits[(offset, k)]) for k in range(8) if (offset, k) in mbits)
    parts = []
    byte_map = mapped.get(offset)
    if byte_map and not bit_hits:
        parts.append(f"[{byte_map[0]}]" if byte_map[1] else f"[{byte_map[0]}?]")
    for k, (name, verified) in bit_hits:
        parts.append(f"[{name}:{k}]" if verified else f"[{name}?:{k}]")
    if not parts:
        return "unmapped"
    return " ".join(parts)
