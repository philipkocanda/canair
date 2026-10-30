"""Tests for canlib.states.rename — renaming a state across a whole profile."""

from __future__ import annotations

import json

import pytest

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
def prof(tmp_path):
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
    (root / "dtc_log.yaml").write_text(
        "scans:\n- timestamp: '2026-01-01T10:00:00'\n  vehicle_states:\n  - ACC2\n"
    )
    return Profile("car", root)


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
        assert plan.log_refs == {prof.dtc_log_file: 1}
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

        assert "- RUN" in prof.dtc_log_file.read_text()

    def test_a_failed_write_restores_every_file(self, prof, monkeypatch):
        before = {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()}
        plan = plan_state_rename("ACC2", "RUN", profile=prof)

        def _reject(path, profile=None):
            return False, "  ERROR: injected"

        monkeypatch.setattr("canlib.commands.validate.validate_pids_file", _reject)
        with pytest.raises(StateRenameError, match="invalid after the rename"):
            apply_state_rename(plan, profile=prof)
        assert {p: p.read_text() for p in prof.root.rglob("*") if p.is_file()} == before
