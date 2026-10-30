"""Tests for canlib.did_families — DID families and the IOControl families never read."""

from canlib.did_families import did_families, unread_families


def _ecu(**fields):
    return {"pids": {}, **fields}


class TestDidFamilies:
    def test_an_nrc_only_discovery_is_not_evidence(self):
        ecu = _ecu(
            iocontrol_discoveries={
                "B100": {"response": "6FB10000"},
                "B200": {"nrc": 0x22, "nrc_desc": "conditionsNotCorrect"},
            }
        )
        assert did_families(ecu) == {0xB1: "IOControl"}

    def test_a_curated_actuator_always_counts(self):
        assert did_families(_ecu(iocontrol={"B401": {"on": "2FB40103FF"}})) == {0xB4: "IOControl"}


class TestUnreadFamilies:
    def test_an_actuator_family_with_no_read_is_a_gap(self):
        ecu = _ecu(iocontrol={"B401": {}, "B408": {}})
        (gap,) = unread_families(ecu, read=[(0xB001, 0xB010)])
        assert gap.high == 0xB4
        assert gap.iocontrol_dids == (0xB401, 0xB408)
        assert gap.read_range == (0xB400, 0xB4FF)
        assert not gap.tracked

    def test_a_read_of_any_actuator_did_closes_the_family(self):
        ecu = _ecu(iocontrol={"B401": {}, "B408": {}})
        assert unread_families(ecu, read=[(0xB405, 0xB410)]) == []

    def test_a_read_elsewhere_in_the_family_does_not(self):
        # Reaching B4F0 says nothing about whether the actuator DIDs answer 22.
        ecu = _ecu(iocontrol={"B401": {}})
        assert len(unread_families(ecu, read=[(0xB4F0, 0xB4FF)])) == 1

    def test_a_defined_read_pid_in_the_family_closes_it(self):
        ecu = {"pids": {"22BC03": {}}, "iocontrol": {"BC01": {}}}
        assert unread_families(ecu, read=[]) == []

    def test_planned_reads_mark_a_gap_tracked(self):
        ecu = _ecu(iocontrol={"B401": {}})
        (gap,) = unread_families(ecu, read=[], planned=[(0xB400, 0xB40F)])
        assert gap.tracked
