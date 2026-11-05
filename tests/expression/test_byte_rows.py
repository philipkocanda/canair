"""The byte-by-byte annotation of a response payload, as data."""

from __future__ import annotations

from canair.expression.byte_rows import annotate_rows, resolve_annotate_layout, resolve_width
from canair.uds.byteindex import payload_to_wican_frame


def _frame(hex_payload: str):
    return payload_to_wican_frame(bytes.fromhex(hex_payload))


def test_the_payloads_own_service_fixes_the_layout():
    frame = _frame("62BC03AABB")
    chosen = resolve_annotate_layout(frame, None, None)
    assert chosen.sub_bytes == 2 and chosen.derived_from == "SID 0x62" and chosen.conflict is None


def test_an_explicit_width_against_the_service_drops_the_layout_and_reports_it():
    chosen = resolve_annotate_layout(_frame("62BC03AABB"), 1, None)
    assert chosen.layout is None and chosen.sub_bytes == 1
    assert chosen.conflict is not None and chosen.conflict.implied == 2


def test_a_conclusive_pid_sets_the_width_and_an_explicit_one_conflicts():
    assert resolve_width(None, "22BC03").sub_bytes == 2
    assert resolve_width(None, "B004").sub_bytes == 1  # a short DID says nothing
    clash = resolve_width(1, "22BC03")
    assert clash.sub_bytes == 1 and clash.conflict is not None and clash.conflict.pid == "22BC03"


def test_rows_name_the_roles_and_the_signal_that_reads_each_byte():
    frame = _frame("62BC03AABB")
    chosen = resolve_annotate_layout(frame, None, None)
    params = {"SIG": {"expression": "B4", "verified": True}}
    rows = annotate_rows(
        frame, bytes.fromhex("62BC03AABB"), chosen.layout, chosen.sub_bytes, params=params
    )
    assert [r.role for r in rows][:2] == ["PCI", "SID"]
    by_index = {r.wican: r for r in rows}
    assert by_index[4].signal == "[SIG]"
    assert by_index[5].signal == "unmapped"
    assert all(r.torque is None and r.bix is None for r in rows)


def test_torque_columns_are_only_computed_on_request():
    frame = _frame("6101AABB")
    rows = annotate_rows(frame, bytes.fromhex("6101AABB"), None, 1, torque_columns=True)
    data = [r for r in rows if r.role == "" and r.isotp is not None]
    assert data[0].torque == "A" and data[0].bix == 0
