"""``canair captures migrate-discover`` — repairing legacy discover captures.

Legacy discover responders stored only an ``rx``, computed as ``TX + 0x08`` for an
unregistered address whatever the profile's addressing said. The repair recovers
the probed TX from the old writer's labelling (``NAME (status)`` for a registered
ECU, the bare status otherwise) and records the ``rx`` the profile gives it.
"""

from __future__ import annotations

import json

import pytest

from canlib.addressing import DEFAULT_MODE
from canlib.captures import save_session
from canlib.migrations.discover import Addressing, infer_tx, migrate_dir, repair_doc

# A +0x20 profile, as on the Nissan Leaf ZE1, with two registered ECUs.
ECUS = {
    0x70E: {"name": "BRAKE", "rx_id": 0x70F},
    0x740: {"name": "ABS", "alias": "VDC", "rx_id": 0x760},
}
ADDRESSING = Addressing(ecus=ECUS, rx_offset=0x20, mode=DEFAULT_MODE)

LEGACY = [
    {"rx": "0x70F", "response": "BRAKE (NRC 0x12)", "notes": "Raw: subFunctionNotSupported"},
    {"rx": "0x760", "response": "ABS (positive)", "notes": "Raw: 5001003201F4"},
    # Unregistered 0x758: saved as +8, which collides with ABS's rx_id.
    {"rx": "0x760", "response": "NRC 0x12", "notes": "Raw: subFunctionNotSupported"},
    {"rx": "0x756", "response": "positive", "notes": "Raw: 5001003201F4"},
]


def _doc(entries):
    session = {
        "date": "2026-10-21",
        "label": "sweep",
        "transport": "slcan-tcp",
        "captures": [
            {
                "rx": "broadcast",
                "pid": "discover 700-7EF",
                "scan_results": {"responding": [dict(e) for e in entries]},
            }
        ],
    }
    return {"sessions": [session]}


def _responding(doc):
    return doc["sessions"][0]["captures"][0]["scan_results"]["responding"]


class TestInferTx:
    @pytest.mark.parametrize(
        "entry,tx",
        [
            ({"rx": "0x70F", "response": "BRAKE (NRC 0x12)"}, 0x70E),
            ({"rx": "0x760", "response": "VDC (positive)"}, 0x740),  # alias resolves
            ({"rx": "0x760", "response": "NRC 0x12"}, 0x758),
            ({"rx": "0x756", "response": "positive"}, 0x74E),
        ],
    )
    def test_the_label_selects_the_inverse(self, entry, tx):
        names = {"BRAKE": 0x70E, "ABS": 0x740, "VDC": 0x740}
        assert infer_tx(entry, names) == tx

    def test_an_unknown_ecu_name_is_a_reason_not_a_guess(self):
        reason = infer_tx({"rx": "0x7BB", "response": "LBC (NRC 0x12)"}, {})
        assert isinstance(reason, str)
        assert "no longer in the registry" in reason

    def test_a_legacy_ecu_key_needs_migrate_rx_first(self):
        assert "migrate-rx" in str(infer_tx({"ecu": "0x7BB", "response": "positive"}, {}))


class TestRepairDoc:
    def test_records_tx_and_the_profile_rx(self):
        doc = _doc(LEGACY)
        repairs, skipped = repair_doc(doc, ADDRESSING)
        assert skipped == []
        assert [(e["tx"], e["rx"], e["response"]) for e in _responding(doc)] == [
            ("0x70E", "0x70F", "BRAKE (NRC 0x12)"),
            ("0x740", "0x760", "ABS (positive)"),
            ("0x758", "0x778", "NRC 0x12"),
            ("0x74E", "0x76E", "positive"),
        ]
        assert [(r.old_rx, r.rx) for r in repairs if r.old_rx != r.rx] == [
            ("0x760", "0x778"),
            ("0x756", "0x76E"),
        ]

    def test_fields_follow_the_builder_order(self):
        doc = _doc(LEGACY[:1])
        repair_doc(doc, ADDRESSING)
        assert list(_responding(doc)[0]) == ["tx", "rx", "response", "notes"]

    def test_is_idempotent(self):
        doc = _doc(LEGACY)
        repair_doc(doc, ADDRESSING)
        once = json.dumps(doc)
        repairs, _ = repair_doc(doc, ADDRESSING)
        assert repairs == []
        assert json.dumps(doc) == once

    def test_matches_what_the_fixed_writer_records(self):
        """A repaired legacy capture is indistinguishable from a fresh one."""
        from canlib.captures import build_discover_session

        doc = _doc(LEGACY)
        repair_doc(doc, ADDRESSING)
        fresh = build_discover_session(
            [
                (0x70E, 0x70F, "BRAKE (NRC 0x12)", "subFunctionNotSupported"),
                (0x740, 0x760, "ABS (positive)", "5001003201F4"),
                (0x758, 0x778, "NRC 0x12", "subFunctionNotSupported"),
                (0x74E, 0x76E, "positive", "5001003201F4"),
            ],
            0,
            0,
            (0x700, 0x7EF),
            "sweep",
            [],
            "",
        )
        assert _responding(doc) == fresh["captures"][0]["scan_results"]["responding"]

    def test_leaves_other_captures_alone(self):
        doc = _doc(LEGACY)
        doc["sessions"][0]["captures"].append(
            {"rx": "0x7BB", "pid": "2101", "payload": "6101AA", "time": "12:00:00"}
        )
        before = json.dumps(doc["sessions"][0]["captures"][1])
        repair_doc(doc, ADDRESSING)
        assert json.dumps(doc["sessions"][0]["captures"][1]) == before
        assert doc["sessions"][0]["transport"] == "slcan-tcp"  # not inferable


class TestMigrateDir:
    def test_dry_run_writes_nothing_and_a_rerun_is_a_no_op(self, tmp_path):
        save_session(_doc(LEGACY)["sessions"][0], tmp_path)
        (path,) = tmp_path.glob("*.json")
        original = path.read_text()

        (dry,) = migrate_dir(tmp_path, ADDRESSING, dry_run=True)
        assert len(dry.repairs) == 4 and not dry.written
        assert path.read_text() == original

        (real,) = migrate_dir(tmp_path, ADDRESSING)
        assert real.written
        (again,) = migrate_dir(tmp_path, ADDRESSING)
        assert again.repairs == [] and not again.written

    def test_the_cli_resolves_the_profile_owning_the_captures(self, tmp_path, capsys):
        import argparse

        from canlib.commands import captures as cap

        root = tmp_path / "prof"
        (root / "ecus").mkdir(parents=True)
        (root / "captures").mkdir()
        (root / "profile.yaml").write_text(
            'car_model: "T"\ninit: "ATSP6;"\naddressing:\n  rx_offset: 0x20\n'
        )
        (root / "ecus" / "abs.yaml").write_text("ABS:\n  tx_id: 0x740\n")
        save_session(_doc(LEGACY[1:4])["sessions"][0], root / "captures")

        parser = cap.add_parser(argparse.ArgumentParser().add_subparsers())
        args = parser.parse_args(["migrate-discover", "--dir", str(root / "captures")])
        assert args.func(args) == 0
        (path,) = (root / "captures").glob("*.json")
        got = [(e["tx"], e["rx"]) for e in _responding(json.loads(path.read_text()))]
        assert got == [("0x740", "0x760"), ("0x758", "0x778"), ("0x74E", "0x76E")]
        assert "--set-transport" in capsys.readouterr().out


class TestRepairEdgeCases:
    def test_an_ambiguous_registry_names_the_real_reason(self):
        """Two ECUs claiming one alias must not read as "no longer in the registry"."""
        ecus = {**ECUS, 0x7E4: {"name": "BMS", "alias": "ABS", "rx_id": 0x7EC}}
        doc = _doc(LEGACY)
        repairs, skipped = repair_doc(doc, Addressing(ecus, 0x20, DEFAULT_MODE))
        assert {s.reason for s in skipped} == {"ambiguous ECU name/alias in the registry"}
        assert [s.response for s in skipped] == ["BRAKE (NRC 0x12)", "ABS (positive)"]
        # Unregistered responders need no name, so they are still repaired.
        assert [r.response for r in repairs] == ["NRC 0x12", "positive"]

    @pytest.mark.parametrize("label", ["Front Camera (positive)", "ABS front (NRC 0x12)"])
    def test_a_status_suffix_without_a_single_name_is_skipped_not_guessed(self, label):
        """Falling through to the ``rx - 8`` inverse would write a wrong TX."""
        doc = _doc([{"rx": "0x760", "response": label}])
        repairs, [skip] = repair_doc(doc, ADDRESSING)
        assert repairs == []
        assert "does not name a single ECU" in skip.reason
        assert "tx" not in _responding(doc)[0]

    def test_a_legacy_29bit_responder(self):
        from canlib.addressing import AddressingMode

        doc = _doc([{"rx": "0x18DA10F9", "response": "positive"}])
        fixed = Addressing({}, 0x08, AddressingMode.NORMAL_FIXED_29BIT)
        [repair], skipped = repair_doc(doc, fixed)
        assert skipped == []
        assert (repair.tx, repair.rx) == ("0x18DA10F1", "0x18DAF110")
        assert _responding(doc)[0] == {
            "tx": "0x18DA10F1",
            "rx": "0x18DAF110",
            "response": "positive",
        }

    @pytest.mark.parametrize("pid", ["scan 22 0100-01FF", "discovery 700-7EF", "sessions"])
    def test_other_broadcast_captures_are_left_alone(self, pid):
        doc = _doc(LEGACY)
        doc["sessions"][0]["captures"][0]["pid"] = pid
        before = json.dumps(doc)
        assert repair_doc(doc, ADDRESSING) == ([], [])
        assert json.dumps(doc) == before
