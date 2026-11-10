"""Profile actions (canair.actions): the definition rules, the editor and the loader.

The rules here are the safety contract a profile cannot get around: every action
is guarded by live reads and a TRUE-only condition, the condition can only get
stricter (no ``or``/``not``), and it can only test ECUs the action reads.
"""

from __future__ import annotations

import pytest

from canair.actions import (
    FUNCTIONAL,
    ActionError,
    ActionRequest,
    load_actions,
    parse_action,
    parse_actions_doc,
    parse_frame_spec,
    parse_request_spec,
    registry_issues,
)
from canair.profile.model import Profile
from canair.yaml_edit.actions import add_action, remove_action, update_action

GOOD = {
    "description": "Put every ECU to sleep.",
    "reads": ["IGPM:22BC03"],
    "require": "IGPM.IGN1_SWITCH == 0 and IGPM.IGN2_SWITCH == 0",
    "requests": [{"to": "functional", "send": "10 d1", "response": "none"}],
}


def _with(**changes):
    return {**GOOD, **changes}


class TestParse:
    def test_good_action(self):
        a = parse_action("Sleep", GOOD)
        assert a.name == "sleep"
        assert a.reads == ("IGPM:22BC03",)
        assert a.requests == (ActionRequest(FUNCTIONAL, "10D1", "none"),)

    def test_response_defaults_to_expected(self):
        a = parse_action("x", _with(requests=[{"to": "igpm", "send": "22BC03"}]))
        assert a.requests[0] == ActionRequest("IGPM", "22BC03", "expected")

    @pytest.mark.parametrize("field", ["reads", "require", "requests"])
    def test_guard_and_requests_are_mandatory(self, field):
        raw = dict(GOOD)
        del raw[field]
        with pytest.raises(ActionError):
            parse_action("x", raw)

    @pytest.mark.parametrize(
        "expr",
        [
            "IGPM.IGN1_SWITCH == 0 or IGPM.IGN2_SWITCH == 0",
            "not IGPM.IGN1_SWITCH == 1",
        ],
    )
    def test_condition_cannot_be_loosened(self, expr):
        with pytest.raises(ActionError, match="'or' or 'not'"):
            parse_action("x", _with(require=expr))

    def test_condition_must_test_a_signal(self):
        with pytest.raises(ActionError, match="at least one"):
            parse_action("x", _with(require="1 == 1"))

    def test_condition_may_only_test_read_ecus(self):
        with pytest.raises(ActionError, match="no 'reads' selector reads VCU"):
            parse_action("x", _with(require="VCU.VCU_IG3_ON == 0"))

    def test_reads_must_name_pids(self):
        with pytest.raises(ActionError, match="must name its PIDs"):
            parse_action("x", _with(reads=["IGPM"]))

    def test_unknown_fields_are_refused(self):
        with pytest.raises(ActionError, match="unknown field"):
            parse_action("x", _with(unsafe=True))
        with pytest.raises(ActionError, match="unknown field"):
            parse_action("x", _with(requests=[{"to": "functional", "send": "10D1", "x": 1}]))

    def test_send_must_be_whole_hex_bytes(self):
        with pytest.raises(ActionError, match="whole hex bytes"):
            parse_action("x", _with(requests=[{"to": "functional", "send": "10D"}]))

    def test_bad_name(self):
        with pytest.raises(ActionError, match="invalid action name"):
            parse_action("put to sleep", GOOD)

    def test_doc_rejects_duplicates_case_insensitively(self):
        with pytest.raises(ActionError, match="duplicate"):
            parse_actions_doc({"actions": {"Sleep": GOOD, "sleep": GOOD}})

    def test_empty_doc(self):
        assert parse_actions_doc(None) == {}


class TestRequestSpec:
    def test_forms(self):
        assert parse_request_spec("functional:10D1:none") == ActionRequest(
            FUNCTIONAL, "10D1", "none"
        )
        assert parse_request_spec("igpm:2FBC0103") == ActionRequest("IGPM", "2FBC0103")

    @pytest.mark.parametrize("spec", ["10D1", "functional:", "a:b:c:d", "functional:10D1:maybe"])
    def test_malformed(self, spec):
        with pytest.raises(ActionError):
            parse_request_spec(spec)


def _pids_data():
    return {
        "ecus": {
            "IGPM": {
                "tx_id": 0x770,
                "pids": {
                    "22BC03": {
                        "parameters": {
                            "IGN1_SWITCH": {"expression": "B11:5"},
                            "IGN2_SWITCH": {"expression": "B11:6"},
                        }
                    }
                },
            }
        }
    }


class TestRegistry:
    def test_resolves(self):
        assert registry_issues(parse_action("x", GOOD), _pids_data()) == []

    def test_unknown_target_read_and_signal(self):
        action = parse_action(
            "x",
            _with(
                reads=["IGPM:22BC09"],
                require="IGPM.NO_SUCH == 0",
                requests=[{"to": "BMS", "send": "1001"}],
            ),
        )
        issues = "\n".join(registry_issues(action, _pids_data()))
        assert "'BMS' is not a registered ECU" in issues
        assert "PID 22BC09 is not defined" in issues
        assert "IGPM.NO_SUCH" in issues


class TestEditor:
    def _profile(self, tmp_path):
        (tmp_path / "profile.yaml").write_text("car_model: test\n")
        (tmp_path / "ecus").mkdir()
        return Profile("t", tmp_path)

    def test_add_update_remove_round_trip(self, tmp_path):
        prof = self._profile(tmp_path)
        add_action(
            "sleep",
            reads=GOOD["reads"],
            require=GOOD["require"],
            requests=[parse_request_spec("functional:10D1:none")],
            description="Sleep.",
            profile=prof,
        )
        assert load_actions(prof)["sleep"].requests[0].send == "10D1"
        update_action("sleep", description="", require="IGPM.IGN1_SWITCH == 0", profile=prof)
        a = load_actions(prof)["sleep"]
        assert a.description == "" and a.require == "IGPM.IGN1_SWITCH == 0"
        remove_action("sleep", profile=prof)
        assert load_actions(prof) == {}

    def test_add_refuses_an_unguarded_action_and_leaves_no_file(self, tmp_path):
        prof = self._profile(tmp_path)
        with pytest.raises(ActionError):
            add_action(
                "sleep",
                reads=GOOD["reads"],
                require="IGPM.IGN1_SWITCH == 0 or IGPM.IGN2_SWITCH == 0",
                requests=[parse_request_spec("functional:10D1:none")],
                profile=prof,
            )
        assert not prof.actions_file.exists()

    def test_update_that_breaks_the_rules_is_reverted(self, tmp_path):
        prof = self._profile(tmp_path)
        add_action(
            "sleep",
            reads=GOOD["reads"],
            require=GOOD["require"],
            requests=[parse_request_spec("functional:10D1:none")],
            profile=prof,
        )
        before = prof.actions_file.read_text()
        with pytest.raises(ActionError):
            update_action("sleep", require="VCU.VCU_IG3_ON == 0", profile=prof)
        assert prof.actions_file.read_text() == before

    def test_duplicate_add(self, tmp_path):
        prof = self._profile(tmp_path)

        def add(name):
            add_action(
                name,
                reads=GOOD["reads"],
                require=GOOD["require"],
                requests=[parse_request_spec("functional:10D1:none")],
                profile=prof,
            )

        add("sleep")
        with pytest.raises(ActionError, match="already exists"):
            add("SLEEP")


IG3 = {
    "reads": ["IGPM:22BC07"],
    "require": "IGPM.IG3_RELAY_OUTPUT == 0",
    "hold": ["IGPM"],
    "requests": [{"to": "igpm", "send": "2FBC4403"}],
    "then": {"wait": 3, "read": ["VCU:2101", "BMS"]},
    "release": [{"to": "igpm", "send": "2FBC4400"}],
}


class TestHoldThenRelease:
    """An action that drives an output, reads while it is driven, and hands it back."""

    def test_parse(self):
        a = parse_action("ig3", IG3)
        assert [h.describe() for h in a.hold] == ["IGPM:03"]
        assert a.then is not None and a.then.wait == 3 and a.then.read == ("VCU:2101", "BMS")
        assert a.release == (ActionRequest("IGPM", "2FBC4400"),)

    def test_an_output_without_a_release_is_refused(self):
        with pytest.raises(ActionError, match="drives an output"):
            parse_action("ig3", {**IG3, "release": []})
        with pytest.raises(ActionError, match="drives an output"):
            parse_action("ig3", {k: v for k, v in IG3.items() if k != "release"})

    @pytest.mark.parametrize(
        "release",
        [
            [{"to": "igpm", "send": "2FBC4500"}],  # another DID
            [{"to": "bcm", "send": "2FBC4400"}],  # another ECU
            [{"to": "igpm", "send": "2FBC4403"}],  # the ON request itself
        ],
    )
    def test_the_release_must_hand_back_the_same_output(self, release):
        with pytest.raises(ActionError, match="drives an output"):
            parse_action("ig3", {**IG3, "release": release})

    def test_a_masked_release_releases(self):
        raw = {
            **IG3,
            "requests": [{"to": "igpm", "send": "2FBC440307FF"}],
            "release": [{"to": "igpm", "send": "2FBC4400FF"}],
        }
        assert parse_action("ig3", raw).release[0].send == "2FBC4400FF"

    @pytest.mark.parametrize("hold", [["IGPM:02"], ["IGPM:85"], ["IGPM:zz"], [":03"], "IGPM"])
    def test_a_hold_must_be_a_safe_session(self, hold):
        with pytest.raises(ActionError):
            parse_action("ig3", {**IG3, "hold": hold})

    @pytest.mark.parametrize(
        "then",
        [
            {"wait": 61, "read": ["VCU:2101"]},
            {"wait": 3},
            {"read": []},
            {"wait": "x", "read": ["VCU"]},
            ["VCU"],
            {"read": ["VCU"], "x": 1},
        ],
    )
    def test_then_is_checked(self, then):
        with pytest.raises(ActionError):
            parse_action("ig3", {**IG3, "then": then})

    def test_registry_checks_hold_then_and_release(self):
        pids = {
            "ecus": {
                "IGPM": {
                    "tx_id": 0x770,
                    "pids": {"22BC07": {"parameters": {"IG3_RELAY_OUTPUT": {}}}},
                },
                "VCU": {"tx_id": 0x7E2, "pids": {"2101": {}}},
            }
        }
        raw = {
            **IG3,
            "hold": ["IGPM", "XYZ"],
            "then": {"read": ["VCU:2101", "VCU:2199", "BMS"]},
        }
        issues = "\n".join(registry_issues(parse_action("ig3", raw), pids))
        assert "hold 'XYZ'" in issues
        assert "PID 2199 is not defined on VCU" in issues
        assert "then.read 'BMS' names unknown ECU" in issues

    def test_the_editor_round_trips_the_new_fields(self, tmp_path):
        from canair.actions import ThenReads, parse_hold_spec

        (tmp_path / "profile.yaml").write_text("car_model: test\n")
        (tmp_path / "ecus").mkdir()
        prof = Profile("t", tmp_path)
        add_action(
            "ig3",
            reads=IG3["reads"],
            require=IG3["require"],
            requests=[parse_request_spec("IGPM:2FBC4403")],
            hold=[parse_hold_spec("IGPM")],
            then=ThenReads(3.0, ("VCU:2101",)),
            release=[parse_request_spec("IGPM:2FBC4400")],
            profile=prof,
        )
        text = prof.actions_file.read_text()
        assert "hold: [IGPM:03]" in text and "then: {wait: 3, read: [VCU:2101]}" in text
        a = load_actions(prof)["ig3"]
        assert a.then == ThenReads(3.0, ("VCU:2101",)) and a.release[0].send == "2FBC4400"
        update_action("ig3", clear_then=True, profile=prof)
        assert load_actions(prof)["ig3"].then is None
        # Removing the release of a driven output is refused, and the file is left as it was.
        before = prof.actions_file.read_text()
        with pytest.raises(ActionError, match="drives an output"):
            update_action("ig3", release=[], profile=prof)
        assert prof.actions_file.read_text() == before


CLIMATE = {
    "reads": ["IGPM:22BC03"],
    "require": "IGPM.IGN1_SWITCH == 0",
    "frames": [
        {
            "id": "0x587",
            "data": "10 00 00 00 00 00 00 00",
            "mask": "10 00 00 00 00 00 00 0F",
            "counter": {"byte": 7, "nibble": "low"},
            "rate_ms": 100,
            "duration_s": 30,
        }
    ],
}


class TestFrames:
    """A profile action that injects a crafted broadcast CAN frame."""

    def test_parse(self):
        a = parse_action("climate", CLIMATE)
        assert not a.requests
        (frame,) = a.frames
        assert frame.can_id == 0x587
        assert frame.data == bytes.fromhex("1000000000000000")
        assert frame.mask == bytes.fromhex("100000000000000F")
        assert frame.counter is not None and frame.counter.byte == 7 and not frame.counter.high
        assert frame.rate_ms == 100 and frame.duration_s == 30 and frame.count is None

    def test_an_action_needs_requests_or_frames(self):
        with pytest.raises(ActionError, match="at least one 'requests' or 'frames'"):
            parse_action("x", {"reads": CLIMATE["reads"], "require": CLIMATE["require"]})

    def test_frames_and_requests_may_coexist(self):
        raw = {**CLIMATE, "requests": [{"to": "igpm", "send": "22BC03"}]}
        a = parse_action("mix", raw)
        assert a.requests and a.frames

    def test_a_diagnostic_id_is_refused(self):
        bad = {**CLIMATE["frames"][0], "id": "0x770"}
        with pytest.raises(ActionError, match="below 0x700"):
            parse_action("x", {**CLIMATE, "frames": [bad]})

    def test_data_bits_outside_the_mask_are_refused(self):
        # bit 1 (VehImmo) set in data but not in mask.
        bad = {**CLIMATE["frames"][0], "data": "12 00 00 00 00 00 00 00"}
        with pytest.raises(ActionError, match="outside 'mask'"):
            parse_action("x", {**CLIMATE, "frames": [bad]})

    def test_the_dangerous_bits_of_0x587_are_outside_the_test_mask(self):
        # VehSld(0) VehImmo(1) IG3RLY(32,33) CHAR_CANCEL(36) AIRCONCTR_CANCEL(38).
        (frame,) = parse_action("climate", CLIMATE).frames
        for bit in (0, 1, 32, 33, 36, 38):
            byte, offset = bit // 8, bit % 8
            assert not frame.mask[byte] & (1 << offset), f"bit {bit} must be outside the mask"

    def test_a_counter_outside_the_mask_is_refused(self):
        bad = {**CLIMATE["frames"][0], "counter": {"byte": 0, "nibble": "low"}}
        with pytest.raises(ActionError, match="outside 'mask'"):
            parse_action("x", {**CLIMATE, "frames": [bad]})

    def test_a_repeating_frame_needs_a_bound(self):
        bad = {"id": "0x587", "data": "00" * 8, "mask": "00" * 8, "rate_ms": 100}
        with pytest.raises(ActionError, match="one of 'count' or 'duration_s'"):
            parse_action("x", {**CLIMATE, "frames": [bad]})

    def test_a_bound_needs_a_rate(self):
        bad = {"id": "0x587", "data": "00" * 8, "mask": "00" * 8, "count": 10}
        with pytest.raises(ActionError, match="need 'rate_ms'"):
            parse_action("x", {**CLIMATE, "frames": [bad]})

    @pytest.mark.parametrize(
        "field,value,match",
        [
            ("rate_ms", 1, "at least"),
            ("duration_s", 999, "0-300"),
            ("data", "00 10", "8 whole hex bytes"),
        ],
    )
    def test_bounds(self, field, value, match):
        frame = {**CLIMATE["frames"][0]}
        frame.pop("count", None)
        frame[field] = value
        if field == "rate_ms":
            frame["count"] = 5  # a valid bound, so the rate itself is what fails
            frame.pop("duration_s", None)
        with pytest.raises(ActionError, match=match):
            parse_action("x", {**CLIMATE, "frames": [frame]})

    def test_frame_spec_parses(self):
        spec = (
            "id=0x587,data=1000000000000000,mask=100000000000000F,counter=7:low,rate_ms=100,count=5"
        )
        frame = parse_frame_spec(spec)
        assert frame.can_id == 0x587 and frame.count == 5 and frame.counter.byte == 7

    def test_frame_spec_rejects_a_bad_field(self):
        with pytest.raises(ActionError, match="bad field"):
            parse_frame_spec("id=0x587,nope=1")

    def test_the_editor_round_trips_a_frame(self, tmp_path):
        (tmp_path / "profile.yaml").write_text("car_model: test\n")
        (tmp_path / "ecus").mkdir()
        prof = Profile("t", tmp_path)
        spec = (
            "id=0x587,data=1000000000000000,mask=100000000000000F,"
            "counter=7:low,rate_ms=100,duration_s=30"
        )
        add_action(
            "climate",
            reads=CLIMATE["reads"],
            require=CLIMATE["require"],
            requests=[],
            frames=[parse_frame_spec(spec)],
            profile=prof,
        )
        text = prof.actions_file.read_text()
        assert "frames:" in text and "0x587" in text and "10 00 00 00 00 00 00 0F" in text
        a = load_actions(prof)["climate"]
        (frame,) = a.frames
        assert frame.can_id == 0x587 and frame.rate_ms == 100 and frame.duration_s == 30
        # Clearing the frames leaves an action with no requests either: refused and reverted.
        before = prof.actions_file.read_text()
        with pytest.raises(ActionError, match="at least one"):
            update_action("climate", frames=[], profile=prof)
        assert prof.actions_file.read_text() == before
