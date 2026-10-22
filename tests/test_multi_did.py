"""Tests for the multi-DID reply walk (``canlib.multi_did``)."""

from __future__ import annotations

import pytest

from canlib.multi_did import is_fill, split_chain

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
