"""Tests for the DTC snapshot reply parser (`canlib.dtc.snapshot`).

The two CCM replies are real (Ioniq 28 kWh, DTC C182C, 2026-11-01): one record each, holding
DID 4710 with 9 bytes.
"""

import pytest

from canlib.dtc.snapshot import (
    SnapshotDid,
    SnapshotError,
    did_of_read,
    parse_snapshot_ids,
    parse_snapshot_reply,
    read_pid,
)

RECORD_01 = bytes.fromhex("5904582C000801014710021AA7207C0AFFFFFF")
BOTH = bytes.fromhex("5904582C000801014710021AA7207C0AFFFFFF02014710C02E6736FC0AFFFFF8")
IDS = bytes.fromhex("5903582C0001582C0002")


def _unknown(_did):
    return None


def _nine(did):
    return 9 if did == 0x4710 else None


class TestDidHelpers:
    @pytest.mark.parametrize(
        "pid, did", [("224710", 0x4710), ("22c350", 0xC350), (" 22F190 ", 0xF190)]
    )
    def test_did_of_a_read(self, pid, did):
        assert did_of_read(pid) == did

    @pytest.mark.parametrize("pid", ["2101", "2247100", "2247", "21C350", "22ZZZZ", ""])
    def test_not_a_did_read(self, pid):
        assert did_of_read(pid) is None

    def test_read_pid_round_trips(self):
        assert read_pid(0x4710) == "224710"
        assert did_of_read(read_pid(0x0101)) == 0x0101


class TestSnapshotIds:
    def test_lists_dtc_and_record_pairs(self):
        assert parse_snapshot_ids(IDS) == [
            (bytes.fromhex("582C00"), 1),
            (bytes.fromhex("582C00"), 2),
        ]

    def test_no_snapshots_is_an_empty_list(self):
        assert parse_snapshot_ids(bytes.fromhex("5903")) == []

    def test_a_partial_pair_is_an_error(self):
        with pytest.raises(SnapshotError, match="multiple of 4"):
            parse_snapshot_ids(bytes.fromhex("5903582C00"))

    def test_the_wrong_reply_is_an_error(self):
        with pytest.raises(SnapshotError, match="59 03"):
            parse_snapshot_ids(RECORD_01)


class TestSnapshotReply:
    def test_one_record_one_did_is_self_describing(self):
        reply = parse_snapshot_reply(RECORD_01, _unknown, single_record=True)
        assert reply.dtc == bytes.fromhex("582C00")
        assert reply.status == 0x08
        (record,) = reply.records
        assert record.number == 1 and record.complete
        assert record.dids == (SnapshotDid(0x4710, bytes.fromhex("021AA7207C0AFFFFFF")),)

    def test_several_records_split_with_a_known_length(self):
        reply = parse_snapshot_reply(BOTH, _nine)
        assert [r.number for r in reply.records] == [1, 2]
        assert reply.records[1].dids[0].data == bytes.fromhex("C02E6736FC0AFFFFF8")

    def test_an_unknown_length_is_not_guessed(self):
        reply = parse_snapshot_reply(BOTH, _unknown)
        (record,) = reply.records
        assert not record.complete
        assert record.unresolved_did == 0x4710
        assert record.dids == ()
        assert record.undecoded == BOTH[8:]

    def test_single_record_only_frees_the_last_did_of_that_record(self):
        # Two DIDs in one record: the first needs a known length even with single_record.
        two = bytes.fromhex("5904582C0008" + "0102" + "0101AABB" + "0102CC")
        reply = parse_snapshot_reply(two, _unknown, single_record=True)
        assert reply.records[0].unresolved_did == 0x0101

        def length(did):
            return 2 if did == 0x0101 else None

        reply = parse_snapshot_reply(two, length, single_record=True)
        assert reply.records[0].dids == (
            SnapshotDid(0x0101, bytes.fromhex("AABB")),
            SnapshotDid(0x0102, bytes.fromhex("CC")),
        )

    def test_no_stored_record(self):
        reply = parse_snapshot_reply(bytes.fromhex("5904582C0008"), _unknown)
        assert reply.records == ()

    def test_an_empty_record(self):
        reply = parse_snapshot_reply(bytes.fromhex("5904582C00080100"), _unknown)
        assert reply.records[0].number == 1 and reply.records[0].dids == ()

    @pytest.mark.parametrize(
        "hex_, needle",
        [
            ("5904582C", "shorter than its 6-byte header"),
            ("5904582C000801", "inside a record header"),
            ("5904582C0008010147", "inside DID"),
            ("5904582C000801014710021A", "needs 9 bytes, 2 remain"),
            ("5902FF", "59 04"),
            ("7F1912", "59 04"),
        ],
    )
    def test_malformed_replies(self, hex_, needle):
        with pytest.raises(SnapshotError, match=needle):
            parse_snapshot_reply(bytes.fromhex(hex_), _nine)
