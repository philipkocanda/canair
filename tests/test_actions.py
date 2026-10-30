"""Profile actions (canlib.actions): the definition rules, the editor and the loader.

The rules here are the safety contract a profile cannot get around: every action
is guarded by live reads and a TRUE-only condition, the condition can only get
stricter (no ``or``/``not``), and it can only test ECUs the action reads.
"""

from __future__ import annotations

import pytest

from canlib.actions import (
    FUNCTIONAL,
    ActionError,
    ActionRequest,
    load_actions,
    parse_action,
    parse_actions_doc,
    parse_request_spec,
    registry_issues,
)
from canlib.actions.edit import add_action, remove_action, update_action
from canlib.profile import Profile

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
