"""Tests for canlib.uds_layout — per-service response byte-role layouts."""

import pytest

from canlib.uds_layout import (
    ROLE_CTRL,
    ROLE_DID,
    ROLE_HELP,
    ROLE_LID,
    ROLE_NRC,
    ROLE_PID,
    ROLE_REJ_SID,
    ROLE_RID,
    ROLE_SF,
    ROLE_SID,
    nrc_name,
    response_layout,
    role_definitions,
)
from canlib.uds_services import SERVICES, service_info


class TestResponseLayout:
    @pytest.mark.parametrize(
        "resp_sid,fields",
        [
            (0x62, [(ROLE_DID, 2)]),  # 0x22 ReadDataByIdentifier
            (0x61, [(ROLE_LID, 1)]),  # 0x21 ReadDataByLocalIdentifier
            (0x41, [(ROLE_PID, 1)]),  # OBD-II mode 01
            (0x6F, [(ROLE_DID, 2), (ROLE_CTRL, 1)]),  # 0x2F IOControl: param AFTER the DID
            (0x70, [(ROLE_LID, 1), (ROLE_CTRL, 1)]),  # 0x30 KWP IOControl
            (0x71, [(ROLE_SF, 1), (ROLE_RID, 2)]),  # 0x31 RoutineControl: SF BEFORE the RID
            (0x73, [(ROLE_LID, 1)]),  # 0x33 RequestRoutineResults
        ],
    )
    def test_known_layouts(self, resp_sid, fields):
        layout = response_layout(resp_sid)
        assert layout is not None
        assert [(f.role, f.width) for f in layout.fields] == fields

    def test_routine_control_puts_the_subfunction_before_the_rid(self):
        # The ordering that a width-only model gets wrong: `71 {SF} {RID_HI} {RID_LO}`.
        # Mislabelling it shifts the RID low byte into the data region.
        layout = response_layout(0x71)
        assert layout is not None
        assert [layout.role_at(i) for i in range(5)] == [
            ROLE_SID,
            ROLE_SF,
            ROLE_RID,
            ROLE_RID,
            None,  # first real data byte
        ]

    def test_negative_response_is_service_independent(self):
        layout = response_layout(0x7F)
        assert layout is not None
        assert layout.negative
        assert [layout.role_at(i) for i in range(4)] == [
            ROLE_SID,
            ROLE_REJ_SID,
            ROLE_NRC,
            None,
        ]

    def test_unknown_service_returns_none(self):
        # 0x99 -> request 0x59, which is not a service. Callers must fall back
        # rather than be handed a guessed layout.
        assert response_layout(0x99) is None

    def test_registry_fallback_covers_plain_identifier_reads(self):
        # 0x32 has no explicit entry shape beyond its identifier, so the
        # uds_services id_width supplies it (1 byte -> LID).
        layout = response_layout(0x72)
        assert layout is not None
        assert [(f.role, f.width) for f in layout.fields] == [(ROLE_LID, 1)]

    def test_header_and_subfunction_bytes(self):
        did = response_layout(0x62)
        routine = response_layout(0x71)
        assert did is not None and routine is not None
        assert (did.header_bytes, did.subfunction_bytes) == (3, 2)
        # The generalisation a -1/-2 flag cannot express: SID + SF + RID = 4 bytes.
        assert (routine.header_bytes, routine.subfunction_bytes) == (4, 3)

    def test_role_at_is_none_for_data_and_negative_indices(self):
        layout = response_layout(0x62)
        assert layout is not None
        assert layout.role_at(3) is None  # first data byte
        assert layout.role_at(-1) is None

    def test_roles_lists_sid_first(self):
        layout = response_layout(0x6F)
        assert layout is not None
        assert layout.roles() == [ROLE_SID, ROLE_DID, ROLE_CTRL]

    def test_every_layout_role_has_a_definition(self):
        # A role with no ROLE_HELP entry would silently vanish from the legend.
        for resp_sid in list(range(0x40, 0x100)):
            layout = response_layout(resp_sid)
            if layout is None:
                continue
            for role in layout.roles():
                assert role in ROLE_HELP, f"0x{resp_sid:02X} role {role!r} has no definition"

    def test_no_layout_declares_a_zero_width_field(self):
        for resp_sid in range(0x40, 0x100):
            layout = response_layout(resp_sid)
            if layout is None:
                continue
            assert all(f.width >= 1 for f in layout.fields)

    def test_explicit_layouts_agree_with_the_registry_id_width(self):
        # Where uds_services declares an identifier width, the layout must account
        # for at least that many bytes — else the two sources of truth disagree.
        for info in SERVICES:
            if info.id_width <= 0:
                continue
            layout = response_layout((info.sid + 0x40) & 0xFF)
            if layout is None:
                continue
            widths = [f.width for f in layout.fields]
            assert info.id_width in widths, (
                f"0x{info.sid:02X} declares id_width={info.id_width} "
                f"but its response layout has widths {widths}"
            )


class TestNrcName:
    def test_known_codes(self):
        assert nrc_name(0x31) == "requestOutOfRange"
        assert nrc_name(0x78) == "requestCorrectlyReceivedResponsePending"

    def test_unknown_code_is_reported_not_hidden(self):
        assert nrc_name(0xAB) == "unknown (0xAB)"


class TestRoleDefinitions:
    def test_returns_only_requested_roles(self):
        assert [r for r, _ in role_definitions([ROLE_NRC, ROLE_SID])] == [ROLE_SID, ROLE_NRC]

    def test_order_is_vocabulary_order_not_caller_order(self):
        # A definition list must read the same whatever payload produced it.
        a = role_definitions([ROLE_NRC, ROLE_SID, ROLE_REJ_SID])
        b = role_definitions([ROLE_SID, ROLE_REJ_SID, ROLE_NRC])
        assert a == b

    def test_dedupes(self):
        assert len(role_definitions([ROLE_DID, ROLE_DID, ROLE_DID])) == 1

    def test_unknown_roles_are_skipped(self):
        assert role_definitions(["NOT_A_ROLE"]) == []

    def test_lid_definition_cross_references_canair_pid_wording(self):
        # canair calls 21xx identifiers "PIDs" everywhere else; the legend has to
        # bridge that or LID reads as a different thing entirely.
        ((_, help_),) = role_definitions([ROLE_LID])
        assert "21xx" in help_ and "PID" in help_


def test_obd2_modes_are_registered_with_a_pid_identifier():
    for mode in (0x01, 0x02, 0x09):
        info = service_info(mode)
        assert info is not None
        assert info.id_width == 1
