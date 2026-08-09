"""Tests for the capture-store data layer (:mod:`canlib.capture_store`).

The loader, PID-definition resolution and decode preview used to live beside the
``captures`` command, which forced :mod:`canlib.align`, :mod:`canlib.capture_dates`
and :mod:`canlib.state_infer` to import *up* into ``canlib.commands`` (with lazy
in-function imports to dodge the resulting cycle). They are library concerns; these
tests cover them directly, and :class:`TestLayering` keeps the inversion from
coming back.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from canlib.capture_store import (
    decoded_preview,
    entry_path,
    load_all_captures,
    resolve_capture_states,
    resolve_pid_defs,
    unresolved_state_summary,
)
from canlib.state_spans import span_keys

CANLIB = Path(__file__).resolve().parents[1] / "canlib"


def _seed(dirpath: Path, name: str, sessions: list[dict]) -> None:
    (dirpath / name).write_text(json.dumps({"sessions": sessions}))


class TestLoadAllCaptures:
    def test_empty_dir_is_no_captures(self, tmp_path):
        assert load_all_captures(tmp_path) == []

    def test_session_metadata_is_denormalised_onto_every_row(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-01.json",
            [
                {
                    "date": "2026-01-01",
                    "label": "drive",
                    "version": "1.2.3",
                    "vehicle_states": ["READY"],
                    "notes": "session note",
                    "keep_mode": "changes",
                    "transport": "slcan-tcp",
                    "quality": {"exchanges": 9, "drop": 1},
                    "captures": [
                        {"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "09:00:00"},
                        {"rx": "0x7EC", "pid": "2102", "payload": "6102BB", "time": "09:00:01"},
                    ],
                }
            ],
        )
        rows = load_all_captures(tmp_path)
        assert len(rows) == 2
        for row in rows:
            assert row["date"] == "2026-01-01"
            assert row["session_label"] == "drive"
            assert row["session_version"] == "1.2.3"
            assert row["vehicle_states"] == ["READY"]
            assert row["session_notes"] == "session note"
            assert row["keep_mode"] == "changes"
            assert row["transport"] == "slcan-tcp"
            assert row["quality"] == {"exchanges": 9, "drop": 1}
            assert row["file"] == "2026-01-01.json"

    def test_locators_address_the_capture_in_its_file(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-01.json",
            [
                {"date": "2026-01-01", "captures": [{"rx": "0x7EC", "pid": "2101"}]},
                {
                    "date": "2026-01-01",
                    "captures": [{"rx": "0x7EC", "pid": "2102"}, {"rx": "0x7EC", "pid": "2103"}],
                },
            ],
        )
        rows = load_all_captures(tmp_path)
        assert [(r["_session_idx"], r["_capture_idx"]) for r in rows] == [(0, 0), (1, 0), (1, 1)]

    def test_rx_address_is_kept_and_resolved(self, tmp_path):
        # `rx` on disk is the CAN *response* address, not an ECU name: it stays in
        # ecu_addr, and `ecu` carries the resolved short name (BMS answers on 0x7EC).
        _seed(
            tmp_path,
            "2026-01-01.json",
            [{"date": "2026-01-01", "captures": [{"rx": "0x7EC", "pid": "2101"}]}],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["ecu_addr"] == "0x7EC"
        assert row["ecu"] == "BMS"

    def test_legacy_ecu_key_is_still_read(self, tmp_path):
        # Pre-migration files spell the response address `ecu`; capture_rx tolerates
        # it, so a profile that has not run `captures migrate-rx` still loads.
        _seed(
            tmp_path,
            "2026-01-01.json",
            [{"date": "2026-01-01", "captures": [{"ecu": "0x7EC", "pid": "2101"}]}],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["ecu_addr"] == "0x7EC"
        assert row["ecu"] == "BMS"

    def test_unknown_address_leaves_the_name_unresolved(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-01.json",
            [{"date": "2026-01-01", "captures": [{"rx": "0x123", "pid": "2101"}]}],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["ecu_addr"] == "0x123"

    def test_files_are_read_in_date_order(self, tmp_path):
        for day, pid in (("2026-01-02", "2102"), ("2026-01-01", "2101")):
            _seed(
                tmp_path,
                f"{day}.json",
                [{"date": day, "captures": [{"rx": "0x7EC", "pid": pid}]}],
            )
        assert [r["pid"] for r in load_all_captures(tmp_path)] == ["2101", "2102"]

    def test_missing_optional_fields_default_empty(self, tmp_path):
        _seed(tmp_path, "2026-01-01.json", [{"date": "2026-01-01", "captures": [{"rx": "0x7EC"}]}])
        (row,) = load_all_captures(tmp_path)
        assert row["pid"] == ""
        assert row["time"] == ""
        assert row["payload"] is None
        assert row["quality"] is None

    def test_a_file_without_sessions_is_skipped(self, tmp_path):
        (tmp_path / "broken.json").write_text(json.dumps({"not_sessions": []}))
        assert load_all_captures(tmp_path) == []


class TestResolvePidDefs:
    def test_exact_pid_match_returns_parameters_and_tx_id(self):
        from canlib.capture_store import load_ecu_index

        params, tx_id = resolve_pid_defs(load_ecu_index(), "BMS", "2101")
        assert tx_id == 0x7E4
        assert "SOC_BMS" in params

    def test_case_insensitive_on_both_ecu_and_pid(self):
        from canlib.capture_store import load_ecu_index

        index = load_ecu_index()
        assert resolve_pid_defs(index, "bms", "2101") == resolve_pid_defs(index, "BMS", "2101")

    def test_unknown_ecu_is_empty(self):
        assert resolve_pid_defs({}, "NOPE", "2101") == ({}, None)

    def test_unknown_pid_keeps_the_tx_id(self):
        # A capture with no exact PID definition renders as raw hex (no params),
        # but the ECU is still known, so its request address is still reported.
        from canlib.capture_store import load_ecu_index

        params, tx_id = resolve_pid_defs(load_ecu_index(), "BMS", "FFFF")
        assert params == {}
        assert tx_id == 0x7E4


class TestDecodedPreview:
    @pytest.mark.parametrize(
        "entry",
        [
            {"ecu": "BMS", "pid": "2101"},  # no payload
            {"pid": "2101", "payload": "6101AA"},  # no ecu
            {"ecu": "BMS", "payload": "6101AA"},  # no pid
            {"ecu": "BMS", "pid": "2101", "payload": ""},
        ],
    )
    def test_incomplete_entry_decodes_to_nothing(self, entry):
        assert decoded_preview(entry) is None

    def test_undecodable_payload_is_swallowed(self):
        # A malformed payload must not take the whole view down.
        assert decoded_preview({"ecu": "BMS", "pid": "2101", "payload": "ZZ"}) in (None, {})


class TestEntryPath:
    """The locator a mutation reopens: ``_path``, never ``captures_dir / file``."""

    def test_uses_the_rows_own_locator(self, tmp_path):
        _seed(tmp_path, "2026-01-01.json", [{"captures": [{"rx": "0x7EC", "pid": "2101"}]}])
        (row,) = load_all_captures(tmp_path)
        assert row["file"] == "2026-01-01.json"
        assert entry_path(row) == tmp_path / "2026-01-01.json"
        # An unrelated captures dir cannot redirect a located row.
        assert entry_path(row, tmp_path / "elsewhere") == tmp_path / "2026-01-01.json"

    def test_two_dirs_with_the_same_file_name_stay_distinct(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            _seed(
                tmp_path / name,
                "2026-01-01.json",
                [{"captures": [{"rx": "0x7EC", "pid": "2101"}]}],
            )
        (a,) = load_all_captures(tmp_path / "a")
        (b,) = load_all_captures(tmp_path / "b")
        assert a["file"] == b["file"]
        assert entry_path(a) != entry_path(b)

    def test_falls_back_to_the_captures_dir_for_a_hand_built_row(self, tmp_path):
        assert entry_path({"file": "2026-01-01.json"}, tmp_path) == tmp_path / "2026-01-01.json"


class TestLayering:
    """The library must not import back up into the command layer.

    Checked on the parsed import graph, not on the text: a ``:mod:`` docstring
    cross-reference to a command module is documentation, not a dependency.
    """

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        """Every module named by an ``import``/``from … import`` in ``path``.

        Relative imports are resolved against ``canlib`` so ``from .commands.x``
        and ``from canlib.commands.x`` compare equal.
        """
        tree = ast.parse(path.read_text())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: level 1 == the canlib package
                    prefix = "canlib" + "." * (node.level - 1)
                    names.add(f"{prefix}.{node.module}" if node.module else prefix)
                elif node.module:
                    names.add(node.module)
        return names

    def test_no_library_module_imports_the_captures_command(self):
        offenders = {
            path.name: sorted(m for m in mods if m.startswith("canlib.commands"))
            for path in sorted(CANLIB.glob("*.py"))
            if path.name != "cli.py"  # the CLI entry point legitimately imports commands
            for mods in [self._imported_modules(path)]
            if any(m.startswith("canlib.commands.captures") for m in mods)
        }
        assert not offenders, (
            "library modules must read captures through canlib.capture_store, not "
            f"canlib.commands.captures: {offenders}"
        )

    def test_capture_store_imports_nothing_from_commands(self):
        offenders = sorted(
            m
            for m in self._imported_modules(CANLIB / "capture_store.py")
            if m.startswith("canlib.commands")
        )
        assert not offenders, f"capture_store must stay below the command layer: {offenders}"


class TestPerCaptureStateResolution:
    """The read seam that makes `--state CHARGING` mean "sampled while charging".

    A session's `vehicle_states` is a union over the whole recording, so a single
    `monitor --save` across a drive -> charge -> drive trip is tagged both and
    filtering it by either used to return the other's captures. `state_spans`
    narrows each row to its own instant; these cover the three-case ladder in
    :func:`canlib.capture_store.resolve_capture_states`.
    """

    def _seed_trip(self, tmp_path, *, spans: bool = True):
        session: dict = {
            "date": "2026-01-01",
            "label": "drive charge drive",
            "vehicle_states": ["READY", "DRIVING", "CHARGING", "PLUGGED", "PARKED"],
            "captures": [
                {"rx": "0x7EC", "pid": "2101", "payload": "610101", "time": "10:00:00"},
                {"rx": "0x7EC", "pid": "2101", "payload": "610102", "time": "11:30:00"},
                {"rx": "0x7EC", "pid": "2101", "payload": "610103", "time": "12:30:00"},
            ],
        }
        if spans:
            session["state_spans"] = {
                "source": "backfill",
                "version": "1.17.0",
                "spans": [
                    {"at": "10:00:00", "states": ["READY", "DRIVING"]},
                    {"at": "11:00:00", "states": ["CHARGING", "PLUGGED", "PARKED"]},
                    {"at": "12:00:00", "states": ["READY", "DRIVING"]},
                ],
            }
        _seed(tmp_path, "2026-01-01.json", [session])

    def test_case1_spans_resolve_each_capture_to_its_instant(self, tmp_path):
        self._seed_trip(tmp_path)
        rows = load_all_captures(tmp_path)
        assert [r["vehicle_states"] for r in rows] == [
            ["READY", "DRIVING"],
            ["CHARGING", "PLUGGED", "PARKED"],
            ["READY", "DRIVING"],
        ]
        assert all(r["states_resolved"] for r in rows)

    def test_case1_the_reported_defect_charging_no_longer_returns_driving(self, tmp_path):
        self._seed_trip(tmp_path)
        rows = load_all_captures(tmp_path)
        charging = [r for r in rows if "CHARGING" in r["vehicle_states"]]
        assert len(charging) == 1
        assert charging[0]["payload"] == "610102"
        assert "DRIVING" not in charging[0]["vehicle_states"]

    def test_session_union_is_preserved_separately(self, tmp_path):
        self._seed_trip(tmp_path)
        for row in load_all_captures(tmp_path):
            assert row["session_states"] == [
                "READY",
                "DRIVING",
                "CHARGING",
                "PLUGGED",
                "PARKED",
            ]

    def test_case2_single_state_session_is_exact_without_spans(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-02.json",
            [
                {
                    "date": "2026-01-02",
                    "label": "charge",
                    "vehicle_states": ["CHARGING"],
                    "captures": [
                        {"rx": "0x7EC", "pid": "2101", "payload": "61", "time": "10:00:00"}
                    ],
                }
            ],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["vehicle_states"] == ["CHARGING"]
        assert row["states_resolved"] is True

    def test_case2_stateless_session_is_exact(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-02.json",
            [
                {
                    "date": "2026-01-02",
                    "label": "no state",
                    "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "61"}],
                }
            ],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["vehicle_states"] == []
        assert row["states_resolved"] is True

    def test_case3_multi_state_without_spans_degrades_loudly(self, tmp_path):
        self._seed_trip(tmp_path, spans=False)
        rows = load_all_captures(tmp_path)
        # The union is still the honest best answer -- but it is flagged, not
        # silently presented as if it were exact.
        assert all(r["vehicle_states"] == r["session_states"] for r in rows)
        assert not any(r["states_resolved"] for r in rows)
        assert unresolved_state_summary(rows) == (3, 1)

    def test_case3_untimed_capture_in_a_spanned_session_is_flagged(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-03.json",
            [
                {
                    "date": "2026-01-03",
                    "label": "mixed",
                    "vehicle_states": ["DRIVING", "CHARGING"],
                    "state_spans": {"spans": [{"at": "10:00:00", "states": ["DRIVING"]}]},
                    "captures": [
                        {"rx": "0x7EC", "pid": "2101", "payload": "61AA", "time": "10:30:00"},
                        {"rx": "0x7EC", "pid": "2102", "payload": "62BB"},  # no time
                    ],
                }
            ],
        )
        timed, untimed = load_all_captures(tmp_path)
        assert timed["vehicle_states"] == ["DRIVING"]
        assert timed["states_resolved"] is True
        assert untimed["vehicle_states"] == ["DRIVING", "CHARGING"]
        assert untimed["states_resolved"] is False

    def test_capture_before_the_first_span_falls_back(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-04.json",
            [
                {
                    "date": "2026-01-04",
                    "label": "late spans",
                    "vehicle_states": ["DRIVING", "CHARGING"],
                    "state_spans": {"spans": [{"at": "12:00:00", "states": ["CHARGING"]}]},
                    "captures": [
                        {"rx": "0x7EC", "pid": "2101", "payload": "61AA", "time": "09:00:00"},
                    ],
                }
            ],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["vehicle_states"] == ["DRIVING", "CHARGING"]
        assert row["states_resolved"] is False

    def test_empty_span_is_an_exact_answer_not_a_fallback(self, tmp_path):
        _seed(
            tmp_path,
            "2026-01-05.json",
            [
                {
                    "date": "2026-01-05",
                    "label": "gap",
                    "vehicle_states": ["DRIVING", "CHARGING"],
                    "state_spans": {
                        "spans": [
                            {"at": "10:00:00", "states": ["DRIVING"]},
                            {"at": "10:05:00", "states": []},
                        ]
                    },
                    "captures": [
                        {"rx": "0x7EC", "pid": "2101", "payload": "61AA", "time": "10:06:00"},
                    ],
                }
            ],
        )
        (row,) = load_all_captures(tmp_path)
        assert row["vehicle_states"] == []
        assert row["states_resolved"] is True

    def test_resolve_capture_states_ladder_directly(self):
        spans = [{"at": "10:00:00", "states": ["DRIVING"]}]
        keys = span_keys(spans)
        assert resolve_capture_states(["A", "B"], spans, keys, "10:30:00") == (["DRIVING"], True)
        assert resolve_capture_states(["A", "B"], spans, keys, "09:00:00") == (["A", "B"], False)
        assert resolve_capture_states(["A"], [], None, "09:00:00") == (["A"], True)
        assert resolve_capture_states([], [], None, None) == ([], True)

    def test_malformed_state_spans_block_is_ignored(self, tmp_path):
        # A hand-edited/legacy file must degrade, never raise.
        for bad in ("nonsense", [], {"spans": "nope"}, {}):
            _seed(
                tmp_path,
                "2026-01-06.json",
                [
                    {
                        "date": "2026-01-06",
                        "label": "bad",
                        "vehicle_states": ["DRIVING", "CHARGING"],
                        "state_spans": bad,
                        "captures": [
                            {"rx": "0x7EC", "pid": "2101", "payload": "61", "time": "10:00:00"}
                        ],
                    }
                ],
            )
            (row,) = load_all_captures(tmp_path)
            assert row["vehicle_states"] == ["DRIVING", "CHARGING"]
            assert row["states_resolved"] is False


class TestUnresolvedStateSummary:
    def test_counts_captures_and_sessions(self):
        rows = [
            {"file": "a.json", "_session_idx": 0, "states_resolved": False},
            {"file": "a.json", "_session_idx": 0, "states_resolved": False},
            {"file": "a.json", "_session_idx": 1, "states_resolved": False},
            {"file": "b.json", "_session_idx": 0, "states_resolved": True},
        ]
        assert unresolved_state_summary(rows) == (3, 2)

    def test_absent_flag_counts_as_resolved(self):
        assert unresolved_state_summary([{"file": "a.json", "_session_idx": 0}]) == (0, 0)
