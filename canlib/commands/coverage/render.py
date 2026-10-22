"""Human-readable rendering of ``canair coverage`` results."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from canlib import ansi
from canlib.notation import relabel_signal, resolve_notation, subfunction_bytes_for_pid

if TYPE_CHECKING:
    from . import CoverageEntry


def render_text(results: Sequence[CoverageEntry], notation_arg: str | None, scoped: bool) -> None:
    """Print the audit; ``scoped`` qualifies every "no data" claim with "in scope"."""
    if not results:
        print("No matching findings.")
        return

    in_scope = " in scope" if scoped else ""
    n_nocap = sum(1 for e in results if e.get("no_capture"))
    n_nrc = sum(1 for e in results if e.get("negative_response_only"))
    n_gaps = len(results) - n_nocap - n_nrc
    nrc_note = f", {n_nrc} negative-response only" if n_nrc else ""
    print(
        f"\n{ansi.BOLD}PID coverage audit{ansi.RESET} — {n_gaps} PID(s) with gaps, "
        f"{n_nocap} without captures{in_scope}{nrc_note}\n"
    )

    notation = resolve_notation(notation_arg)

    for e in results:
        sub_bytes = subfunction_bytes_for_pid(e["pid"])
        # The frame layout depends on the payload's length, so the non-WiCAN
        # notations need it to name the right byte (see ByteRef.from_wican).
        payload_len = e.get("payload_len")

        def label(i: int, sub_bytes=sub_bytes, payload_len=payload_len) -> str:
            return relabel_signal(f"B{i}", notation, sub_bytes=sub_bytes, payload_len=payload_len)

        header = (
            f"  {ansi.BOLD}{ansi.CYAN}{e['ecu']} {e['pid']}{ansi.RESET} "
            f"{ansi.DIM}({e['params']}p, {e['verified']} verified){ansi.RESET}"
        )
        if e.get("no_capture"):
            print(f"{header}  {ansi.YELLOW}NO CAPTURE{ansi.RESET}{in_scope}")
            continue
        nrc = e.get("negative_response")
        if nrc is not None:
            print(
                f"{header}  {ansi.YELLOW}NEGATIVE RESPONSE ONLY{ansi.RESET}{in_scope} "
                f"{ansi.DIM}NRC {nrc['nrc']} {nrc['name']} ({nrc['count']}x, latest "
                f"{nrc['date']}){ansi.RESET}"
            )
            continue
        capture = e.get("capture") or {}
        padding = (
            ", may include trailing frame padding" if capture.get("may_include_padding") else ""
        )
        print(
            f"{header}  {ansi.DIM}{e.get('data_bytes')} data bytes, "
            f"{capture.get('date')}{padding}{ansi.RESET}"
        )
        if e.get("unmapped"):
            print(f"      {ansi.YELLOW}UNMAPPED{ansi.RESET} {','.join(map(label, e['unmapped']))}")
        if e.get("unverified_mapped"):
            byts = ",".join(map(label, e["unverified_mapped"]))
            print(f"      {ansi.YELLOW}UNVERIFIED{ansi.RESET} {byts}")
        for bf in e.get("incomplete_bitfields") or []:
            have = ",".join(map(str, bf["have"]))
            miss = ",".join(map(str, bf["missing"]))
            whole = f" {ansi.DIM}(also read whole){ansi.RESET}" if bf.get("also_whole") else ""
            print(
                f"      {ansi.RED}BITS{ansi.RESET} {label(bf['byte'])} "
                f"have{{{have}}} missing{{{miss}}}{whole}"
            )
        if e.get("past_end"):
            reads = ", ".join(
                f"{pe['signal']} ({','.join(map(label, pe['bytes']))})" for pe in e["past_end"]
            )
            last = label(int(e.get("frame_len") or 1) - 1)
            print(
                f"      {ansi.RED}PAST END{ansi.RESET} {reads} "
                f"{ansi.DIM}— payload ends at {last}{ansi.RESET}"
            )
    print()
