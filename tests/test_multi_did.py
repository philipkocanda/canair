"""Tests for the multi-DID reply walk (``canlib.multi_did``)."""

from __future__ import annotations

import pytest

from canlib.multi_did import is_fill, is_padding, split_chain

BC03 = "62BC03FDEE3C730A000000"
BC06 = "62BC06B480000000000000"
LENGTHS = {"BC03": 8, "BC06": 8, "BC04": 8}.get


def _batch(*replies: str, tail: str = "") -> bytes:
    """``62`` + each single reply minus its own SID, the way an ECU answers a batch."""
    return bytes.fromhex("62" + "".join(r[2:] for r in replies) + tail)


def test_splits_a_two_did_reply_into_single_replies():
    parts = split_chain(_batch(BC03, BC06), LENGTHS)
    assert parts == [("BC03", bytes.fromhex(BC03)), ("BC06", bytes.fromhex(BC06))]


def test_trailing_iso_tp_fill_is_allowed():
    assert split_chain(_batch(BC03, BC06, tail="AAAAAA"), LENGTHS) is not None


@pytest.mark.parametrize(
    "payload",
    [
        bytes.fromhex(BC03),  # one DID is a single reply, not a batch
        bytes.fromhex(BC03 + "AAAA"),  # a padded single reply
        _batch(BC03, BC06, tail="12"),  # bytes past the last DID that are not fill
        _batch(BC03, "62BC0700"),  # a DID with no known length ends the walk early
        bytes.fromhex("7F2213"),
        b"",
    ],
    ids=["single", "padded-single", "stray-tail", "unknown-did", "nrc", "empty"],
)
def test_anything_but_an_exact_chain_is_rejected(payload):
    assert split_chain(payload, LENGTHS) is None


def test_a_did_whose_data_overruns_the_payload_is_rejected():
    assert split_chain(_batch(BC03, BC06)[:-2], LENGTHS) is None


@pytest.mark.parametrize(("tail", "fill"), [("", True), ("AAAA", True), ("AA00", False)])
def test_is_fill(tail, fill):
    assert is_fill(bytes.fromhex(tail)) is fill


@pytest.mark.parametrize("tail", ["00", "55", "CC", "FF", "000000", "AA"])
def test_any_single_fill_byte_may_trail_the_chain(tail):
    assert split_chain(_batch(BC03, BC06, tail=tail), LENGTHS) is not None


def test_the_same_did_repeated_in_the_chain_splits_into_each_occurrence():
    parts = split_chain(_batch(BC03, BC03), LENGTHS)
    assert parts == [("BC03", bytes.fromhex(BC03))] * 2


def test_a_zero_length_did_never_chains():
    # BC07 with no data would match any two bytes `BC 07` in a single reply's data.
    lengths = {"BC03": 8, "BC07": 0}.get
    payload = bytes.fromhex("62BC03" + "0102BC07")  # the trailing `BC 07` is data, not a DID
    assert split_chain(payload, {"BC03": 2, "BC07": 0}.get) is None
    assert split_chain(_batch(BC03, "62BC07"), lengths) is None


def test_a_negative_length_is_unknown():
    assert split_chain(_batch(BC03, BC06), {"BC03": 8, "BC06": -1}.get) is None


@pytest.mark.parametrize(
    ("payload", "length", "padded"),
    [
        ("62BC03" + "11" * 8, 11, True),  # no padding at all
        ("62BC03" + "11" * 8 + "AAAA", 11, True),  # fills the 2nd frame (13 bytes)
        ("62BC03" + "11" * 8 + "AA", 11, False),  # 12 bytes: does not end on a frame
        ("62BC03" + "11" * 4 + "00" * 6, 7, False),  # would reach back into the 1st frame
        ("62BC03" + "11" * 8 + "AA00", 11, False),  # mixed fill
        ("62BC0311", 5, False),  # shorter than the length
    ],
)
def test_is_padding(payload, length, padded):
    assert is_padding(bytes.fromhex(payload), length) is padded
