"""``canair bix --annotate``: map a real payload byte by byte."""

from __future__ import annotations

from canair.render import ansi, messages
from canair.uds.byteindex import payload_to_wican_frame
from canair.uds.layout import (
    ROLE_PCI,
    ResponseLayout,
    role_definitions,
)
from canair.uds.nrc import nrc_description
from canair.uds.protocol import DiagProtocol
from canair.uds.services import service_response_name
from canair.uds.support_map import MapDecl

from .payload import parse_hex_payload, warn_payload_kind_mismatch
from .rows import annotate_rows, resolve_annotate_layout
from .subfunction import report_conflict
from .table import pad


def print_service_line(
    layout: ResponseLayout,
    frame: list[tuple[int, int | None]],
    protocol: DiagProtocol | None = None,
):
    """One dim line naming the service the payload came from (and, if negative, why).

    ``protocol`` is the ECU's diagnostic protocol when ``--ecu`` names one, so an NRC
    is named as that ECU means it.
    """
    name = service_response_name(layout.resp_sid) or "unknown service"
    if layout.negative:
        by_idx = {pi: value for value, pi in frame if pi is not None}
        rejected, nrc = by_idx.get(1), by_idx.get(2)
        detail = ""
        if rejected is not None:
            rej_name = service_response_name(rejected) or f"service 0x{rejected:02X}"
            detail = f" rejecting 0x{rejected:02X} {rej_name}"
        if nrc is not None:
            detail += f" — NRC 0x{nrc:02X} {nrc_description(nrc, protocol)}"
        print(ansi.c(f"  {name}{detail}", ansi.DIM))
        return
    shape = " + ".join(f"{f.role}×{f.width}" if f.width > 1 else f.role for f in layout.fields)
    header = f"SID + {shape}" if shape else "SID only"
    print(ansi.c(f"  service: 0x{layout.resp_sid:02X} {name} — header {header}", ansi.DIM))


def print_role_legend(roles: list[str], *, has_data: bool):
    """The trailing definition list: only the Role terms this payload actually used."""
    defs = role_definitions(roles)
    if not defs and not has_data:
        return
    blank = "(blank)"
    # Size the term column to every label rendered, "(blank)" included, so the
    # definitions line up as one block whatever roles the payload used.
    width = max([len(r) for r, _ in defs] + [len(blank) if has_data else 0])
    print()
    print(
        ansi.c(
            "  ‖ separates the byte-INDEX columns (where the byte sits) from the byte's\n"
            "  actual VALUE (Hex/Dec) — canair expressions index with WiCAN Bnn.",
            ansi.DIM,
        )
    )
    print()
    print(ansi.c("  Roles in this payload", ansi.BOLD))
    for role, help_ in defs:
        print(f"    {pad(role, width, ansi.CYAN)}  {help_}")
    if has_data:
        print(f"    {blank:<{width}}  data — the bytes an expression reads")


def annotate_payload(
    payload_hex: str,
    explicit_sub_bytes: int | None,
    params: dict | None = None,
    *,
    raw_frame: bool = False,
    show_torque: bool = False,
    show_obdb: bool = False,
    pid: str | None = None,
    legend: bool = True,
    support: MapDecl | None = None,
    protocol: DiagProtocol | None = None,
) -> int:
    """Annotate each byte of a UDS response payload with WiCAN Bnn indices.

    By default ``payload_hex`` is the **reassembled UDS response payload**
    (SID-first, ISO-TP PCI bytes stripped — what the transport/captures hand
    back); the framing is reconstructed to show the WiCAN indices. With
    ``raw_frame=True`` the hex is an **already-framed CAN payload** (PCI present,
    e.g. copied straight off the bus) and is indexed as-is.

    The Torque letter and OBDb bix columns are distinct notations, each shown
    only when its flag (``show_torque`` / ``show_obdb``) is set — WiCAN and
    ISO-TP are the notations canair expressions use.

    **Roles come from the payload's own service.** The response SID is looked up in
    :mod:`canair.uds.layout`, so each header byte is named for what it actually is
    (``DID`` / ``LID`` / ``RID`` / ``SF`` / ``CTRL``, or a negative response's
    rejected SID + ``NRC``) instead of being guessed from a 1-vs-2-byte width.
    ``explicit_sub_bytes`` (``-1``/``-2``) overrides that and ``pid`` is the weaker
    fallback — see :func:`resolve_annotate_layout`. The chosen width is captioned
    so it is never a silent choice, and with ``legend`` a definition list of the
    roles this payload used is printed underneath.

    When ``params`` (a PID's ``parameters`` dict) is given, add a ``Param`` column
    showing which defined parameter maps each byte (``[NAME]`` verified,
    ``[NAME?]`` unverified, ``[NAME:k]`` a specific bit), and mark data bytes no
    param reads as ``unmapped`` — the overlay that makes a wrong byte offset
    obvious at a glance. With the PID's declared ``support`` map the column also names the
    map's own bytes, marks covered bytes that carry no signal, and lists which bits of an
    unmapped byte the map marks.
    """
    from canair.uds.byteindex import NotAFrameError, framed_to_wican_frame

    payload_bytes = parse_hex_payload(payload_hex)
    if payload_bytes:
        warn_payload_kind_mismatch(payload_bytes[0], raw_frame)
    if raw_frame:
        try:
            frame = framed_to_wican_frame(payload_bytes)
        except NotAFrameError as e:
            messages.error(str(e))
            return 2
    else:
        frame = payload_to_wican_frame(payload_bytes)

    chosen = resolve_annotate_layout(frame, explicit_sub_bytes, pid)
    if chosen.conflict is not None:
        report_conflict(chosen.conflict)
    sub_bytes, layout, derived_from = chosen.sub_bytes, chosen.layout, chosen.derived_from
    overlay = params is not None
    # A raw (already framed) paste has no reassembled payload to read the map off.
    rows = annotate_rows(
        frame,
        bytes(payload_bytes),
        layout,
        sub_bytes,
        params=params,
        support=None if raw_frame else support,
        torque_columns=show_torque or show_obdb,
    )

    if layout is not None:
        print_service_line(layout, frame, protocol)
    elif derived_from:
        kind = "2-byte DID" if sub_bytes == 2 else "1-byte PID"
        print(
            ansi.c(
                f"  subfunction: {kind} — derived from {derived_from} (override with -1/-2)",
                ansi.DIM,
            )
        )

    if show_torque or show_obdb:
        # The Torque/OBDb numbering isn't fixed — it counts from the first UDS
        # *data* byte, so it shifts with the header size. For a plain identifier
        # read that is the familiar Torque 1 (21xx) / Torque 2 (22xxxx) split; a
        # richer header (e.g. 0x71's SF + RID) simply skips more bytes, so describe
        # the header generically and only offer the -1/-2 hint where it applies.
        if layout is not None:
            skipped = "SID + " + " + ".join(
                f.role if f.width == 1 else f"{f.width} {f.role} bytes" for f in layout.fields
            )
        else:
            skipped = "SID + 2 DID bytes" if sub_bytes == 2 else "SID + 1 PID byte"
        variant = f"Torque {sub_bytes}" if sub_bytes in (1, 2) else f"Torque (sub={sub_bytes})"
        hint = f" pass -{3 - sub_bytes} for the other variant." if sub_bytes in (1, 2) else ""
        if show_torque:
            cols = "Torque + bix columns" if show_obdb else "Torque column"
            caption = f"  {cols} = {variant} (skips {skipped});{hint}"
        else:  # bix only — describe it without leaning on the Torque label
            caption = (
                f"  bix column = OBDb bit index from the first UDS data byte "
                f"(skips {skipped});{hint}"
            )
        print(ansi.c(caption.rstrip(";").rstrip(), ansi.DIM))

    roles = [row.role for row in rows]

    # Column widths, shared by header / separator / data rows so everything lines
    # up (the Role cell must be padded to width, else the trailing Param divider
    # floats). Torque is a letter (A, AB…), bix up to 3 digits. Role stays at 6 for
    # the common labels and only widens for a longer one ("REJ SID"), so existing
    # output is byte-identical. Torque and bix are independent opt-ins.
    #
    # Layout groups the columns by what they MEAN: every byte-INDEX notation first
    # (WiCAN | ISO-TP | Torque | bix), then the byte's actual VALUE (Hex | Dec),
    # then Role. A double rule (╫) separates the two groups, because an index
    # rendered as 0x0A and a value rendered as 0x0A are otherwise easy to confuse.
    W_WICAN, W_ISOTP, W_TORQUE, W_BIX, W_HEX, W_DEC = 5, 6, 6, 5, 4, 3
    W_ROLE = max(6, *(len(r) for r in roles)) if roles else 6

    def _row(wican, isotp, hex_, dec, role, param=None, *, torque=None, bix=None):
        index_cells = [f"{wican:>{W_WICAN}}", f"{isotp:>{W_ISOTP}}"]
        if show_torque:
            index_cells.append(f"{torque:>{W_TORQUE}}")
        if show_obdb:
            index_cells.append(f"{bix:>{W_BIX}}")
        value_cells = [f"{hex_:>{W_HEX}}", f"{dec:>{W_DEC}}", f"{role:<{W_ROLE}}"]
        if param is not None:
            value_cells.append(str(param))
        return ("  " + " | ".join(index_cells) + " ‖ " + " | ".join(value_cells)).rstrip()

    def _seg():
        index_parts = [f"{'─' * W_WICAN}─", f"─{'─' * W_ISOTP}─"]
        if show_torque:
            index_parts.append(f"─{'─' * W_TORQUE}─")
        if show_obdb:
            index_parts.append(f"─{'─' * W_BIX}─")
        value_parts = [f"─{'─' * W_HEX}─", f"─{'─' * W_DEC}─", f"─{'─' * W_ROLE}"]
        return "  " + "┼".join(index_parts) + "╫" + "┼".join(value_parts)

    hdr = _row(
        "WiCAN",
        "ISO-TP",
        "Hex",
        "Dec",
        "Role",
        "Signal" if overlay else None,
        torque="Torque",
        bix="bix",
    )
    seg = _seg()
    divider_width = len(seg)  # span the fixed columns (up to Role), before Param
    if overlay:
        seg += "─┼─" + "─" * 12
    print(hdr)
    print(seg)

    prev_frame = -1
    for row in rows:
        cur_frame = row.wican // 8
        if cur_frame != prev_frame and len(rows) > 8:
            # Only mark boundaries for genuinely multi-frame responses.
            label = f"  ── Frame {cur_frame} "
            label += "─" * max(0, divider_width - len(label))
            print(ansi.c(label, ansi.CYAN))
            prev_frame = cur_frame

        line = _row(
            f"B{row.wican:02d}",
            f"0x{row.isotp:02X}" if row.isotp is not None else "—",
            f"0x{row.value:02X}",
            str(row.value),
            row.role,
            row.signal,
            torque=row.torque if row.torque else "—",
            bix=str(row.bix) if row.bix is not None else "—",
        )
        print(ansi.c(line, ansi.DIM) if row.role == ROLE_PCI else line)

    if legend:
        print_role_legend(roles, has_data=any(r == "" for r in roles))
    return 0
