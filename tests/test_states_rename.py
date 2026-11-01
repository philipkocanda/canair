"""Tests for canlib.states.rename — renaming a state across a whole profile."""

from __future__ import annotations

import json

import pytest

from canlib.dtc.log import log_file
from canlib.profile import Profile
from canlib.states.rename import (
    StateRenameError,
    apply_state_rename,
    plan_state_rename,
    rewrite_flow_lists,
    swap_token,
)

_STATES = """\
# vocabulary header comment
states:
  - name: READY
    implies: [ACC2]
    when: VCU.EV_READY == 1
  - name: ACC2
    description: Ignition on.
  - name: ALL
"""

_ECU = """\
# ECU header comment
ENGINE:
  tx_id: 0x7E0
  vehicle_states: [READY, ACC2]  # trailing comment
  identity:
    description: test ECU
    id_protocol: UDS
  pids:
    "010D":
      vehicle_states: [acc2]
      parameters:
        ACC2_IGN_ON:
          expression: B03
          unit: ""
          notes: Reads 1 in ACC2. Long values stay exactly as written, however long they are, never re-flowed.
      status: active
  research:
    - type: decode
      target: 010D B04
      status: pending
      vehicle_states: [ACC2, RUN]
"""


def _session(states, spans=None):
    s = {"date": "2026-01-01", "label": "t", "vehicle_states": states}
    if spans is not None:
        s["state_spans"] = {"source": "backfill", "spans": spans}
    s["captures"] = [{"time": "10:00:00.000", "rx": "0x7E8", "pid": "010D", "payload": "410D00"}]
    return s


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setattr("canlib.dtc.log.log_dir", lambda: tmp_path / "dtc_log")
    root = tmp_path / "car"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    (root / "vehicle_states.yaml").write_text(_STATES)
    (root / "ecus" / "engine.yaml").write_text(_ECU)
    data = {
        "sessions": [
            _session(
                ["READY", "ACC2"],
                [{"at": "10:00:00", "states": ["ACC2"]}, {"at": "10:05:00", "states": ["READY"]}],
            ),
            _session(["acc2", "PARKED"]),
            _session(["READY"]),
        ]
    }
    (root / "captures" / "2026-01-01.json").write_text(json.dumps(data, indent=2) + "\n")
    profile = Profile("car", root)
    log = log_file(profile)
    log.parent.mkdir(parents=True)
    log.write_text("scans:\n- timestamp: '2026-01-01T10:00:00'\n  vehicle_states:\n  - ACC2\n")
    return profile


class TestSwapToken:
    def test_replaces_case_insensitively_and_keeps_the_rest_verbatim(self):
        assert swap_token(["ready", "acc2"], "ACC2", "RUN") == ["ready", "RUN"]

    def test_absent_returns_none(self):
        assert swap_token(["READY"], "ACC2", "RUN") is None
        assert swap_token(None, "ACC2", "RUN") is None

    def test_does_not_duplicate_a_new_name_already_present(self):
        assert swap_token(["ACC2", "RUN"], "ACC2", "RUN") == ["RUN"]


class TestRewriteFlowLists:
    def test_only_vehicle_states_lists_change(self):
        text, n = rewrite_flow_lists(_ECU, "ACC2", "RUN")
        assert n == 3
        assert "  vehicle_states: [READY, RUN]  # trailing comment\n" in text
        assert "      vehicle_states: [RUN]\n" in text  # lowercase acc2 matched
        assert "      vehicle_states: [RUN]\n" in text.split("research:")[1]  # deduped
        # A signal name and prose that mention the token are not state references.
        assert "ACC2_IGN_ON:" in text and "Reads 1 in ACC2." in text


class TestPlan:
    def test_counts_every_store_and_writes_nothing(self, prof):
        before = {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()}
        plan = plan_state_rename("acc2", "run", profile=prof)
        assert (plan.old, plan.new) == ("ACC2", "RUN")
        assert plan.ecu_refs == {prof.ecus_dir / "engine.yaml": 3}
        # Session 0: union + one span; session 1: union (lowercase); session 2: none.
        assert plan.capture_refs == {prof.captures_dir / "2026-01-01.json": 3}
        assert plan.log_refs == {log_file(prof): 1}
        assert plan.rewrites_history
        assert {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()} == before

    @pytest.mark.parametrize(
        ("old", "new", "match"),
        [
            ("NOPE", "X", "not found"),
            ("ACC2", "READY", "already exists"),
            ("ALL", "EVERY", "meta-token"),
            ("ACC2", "ACC2", "already called"),
        ],
    )
    def test_refuses(self, prof, old, new, match):
        with pytest.raises(StateRenameError, match=match):
            plan_state_rename(old, new, profile=prof)

    def test_refuses_a_block_style_list_it_cannot_rewrite(self, prof):
        path = prof.ecus_dir / "engine.yaml"
        path.write_text(_ECU.replace("vehicle_states: [acc2]", "vehicle_states:\n      - ACC2"))
        with pytest.raises(StateRenameError, match="block-style"):
            plan_state_rename("ACC2", "RUN", profile=prof)


class TestApply:
    def test_rewrites_every_reference(self, prof):
        apply_state_rename(plan_state_rename("ACC2", "RUN", profile=prof), profile=prof)

        states = prof.states_file.read_text()
        assert "- name: RUN" in states and "implies: [RUN]" in states
        assert states.startswith("# vocabulary header comment")

        ecu = (prof.ecus_dir / "engine.yaml").read_text()
        assert ecu == rewrite_flow_lists(_ECU, "ACC2", "RUN")[0]

        sessions = json.loads((prof.captures_dir / "2026-01-01.json").read_text())["sessions"]
        assert sessions[0]["vehicle_states"] == ["READY", "RUN"]
        assert [s["states"] for s in sessions[0]["state_spans"]["spans"]] == [["RUN"], ["READY"]]
        assert sessions[1]["vehicle_states"] == ["RUN", "PARKED"]
        assert list(sessions[0]) == ["date", "label", "vehicle_states", "state_spans", "captures"]

        assert "- RUN" in log_file(prof).read_text()

    def test_a_failed_write_restores_every_file(self, prof, monkeypatch):
        before = {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()}
        plan = plan_state_rename("ACC2", "RUN", profile=prof)

        def _reject(path, profile=None):
            return False, "  ERROR: injected"

        monkeypatch.setattr("canlib.commands.validate.validate_pids_file", _reject)
        with pytest.raises(StateRenameError, match="invalid after the rename"):
            apply_state_rename(plan, profile=prof)
        assert {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()} == before


class TestBooleanWordStates:
    """A state named ON is legal, but YAML 1.1 reads a bare ON as true."""

    def test_rename_to_on_writes_it_quoted_everywhere(self, prof):
        from canlib import yaml_io

        apply_state_rename(plan_state_rename("ACC2", "ON", profile=prof), profile=prof)

        states = prof.states_file.read_text()
        assert '- name: "ON"' in states and 'implies: ["ON"]' in states
        ecu = (prof.ecus_dir / "engine.yaml").read_text()
        assert '  vehicle_states: [READY, "ON"]  # trailing comment\n' in ecu
        # Every YAML store reads back the string, never the boolean.
        assert yaml_io.safe_load(ecu)["ENGINE"]["vehicle_states"] == ["READY", "ON"]
        assert yaml_io.safe_load(states)["states"][1]["name"] == "ON"
        assert yaml_io.safe_load(log_file(prof).read_text())["scans"][0]["vehicle_states"] == ["ON"]
        # JSON needs no quoting.
        sessions = json.loads((prof.captures_dir / "2026-01-01.json").read_text())["sessions"]
        assert sessions[1]["vehicle_states"] == ["ON", "PARKED"]

    def test_load_states_refuses_a_bare_boolean(self, prof):
        from canlib.states import StatePredicateError, load_states

        prof.states_file.write_text(_STATES.replace("name: ACC2", "name: ON"))
        with pytest.raises(StatePredicateError, match="unquoted YAML boolean"):
            load_states(prof)

    def test_validate_pids_errors_on_a_bare_boolean(self, prof):
        from canlib.commands.validate import validate_pids_file

        path = prof.ecus_dir / "engine.yaml"
        path.write_text(_ECU.replace("[READY, ACC2]", "[READY, ON]"))
        ok, msg = validate_pids_file(path, prof)
        assert not ok
        assert "unquoted YAML boolean" in msg

    def test_validate_states_errors_on_a_bare_boolean(self, prof, monkeypatch, capsys):
        from canlib.commands.validate.other import _run_states

        prof.states_file.write_text(_STATES.replace("implies: [ACC2]", "implies: [ON]"))
        monkeypatch.setattr("canlib.profile.active", lambda: prof)
        assert _run_states() == 1
        assert "unquoted YAML boolean" in capsys.readouterr().out

    def test_states_editor_quotes_a_new_boolean_word(self, prof):
        from canlib import yaml_io
        from canlib.states.edit import add_state, set_state_field

        add_state("OFF", description="Button off", profile=prof)
        set_state_field("READY", "excludes", "OFF", profile=prof)
        text = prof.states_file.read_text()
        assert '- name: "OFF"' in text and 'excludes: ["OFF"]' in text
        names = [s["name"] for s in yaml_io.safe_load(text)["states"]]
        assert "OFF" in names


def test_research_writer_quotes_a_boolean_word():
    from canlib.pids_edit.research import _format_research_item

    lines = _format_research_item({"type": "decode", "vehicle_states": ["ON", "READY"]})
    assert '  vehicle_states: ["ON", READY]' in "\n".join(lines)
