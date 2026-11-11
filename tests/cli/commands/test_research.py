"""Tests for research.py — aggregating pids/ research: sections."""

import pytest

from canair.cli.commands import research


@pytest.fixture
def pids_dir(tmp_path):
    """A minimal pids/ dir with two ECUs carrying research entries."""
    (tmp_path / "mcu.yaml").write_text(
        """\
MCU:
  tx_id: 0x7E3
  pids: {}
  research:
    - type: decode
      target: "2102"
      status: captured
      priority: P1
      vehicle_states: [charging]
      notes: "motor torque candidate"
      what_to_test: ["a", "b"]
    - type: scan
      target: "22 E001-E010"
      status: pending
      priority: P2
      vehicle_states: [acc]
    - type: verify
      target: "OLD"
      status: done
      priority: P1
"""
    )
    (tmp_path / "bms.yaml").write_text(
        """\
BMS:
  tx_id: 0x7E4
  pids: {}
  research:
    - type: iocontrol_scan
      target: "2F E000-E0FF"
      status: pending
      priority: P1
      vehicle_states: [acc]
"""
    )
    # A file with no research: section must be tolerated.
    (tmp_path / "clu.yaml").write_text("CLU:\n  tx_id: 0x7C6\n  pids: {}\n")
    return tmp_path


class TestLoadResearch:
    def test_flattens_all_ecus_with_context(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert len(recs) == 4  # 3 MCU + 1 BMS
        mcu = [r for r in recs if r["ecu"] == "MCU"]
        assert len(mcu) == 3
        assert all(r["tx_id"] == 0x7E3 for r in mcu)

    def test_ignores_files_without_research(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert not any(r["ecu"] == "CLU" for r in recs)


class TestFilters:
    def test_done_hidden_by_default(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs)
        assert all(r["status"] != "done" for r in out)
        assert len(out) == 3

    def test_include_done(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, include_done=True)
        assert len(out) == 4

    def test_explicit_done_status_shows_done(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, status="done")
        assert len(out) == 1
        assert out[0]["status"] == "done"

    def test_filter_by_ecu_case_insensitive(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert len(research.filter_records(recs, ecu="mcu")) == 2  # done hidden

    def test_filter_by_type(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, rtype="scan")
        assert len(out) == 1 and out[0]["target"] == "22 E001-E010"

    def test_filter_by_priority(self, pids_dir):
        recs = research.load_research(pids_dir)
        # P1 open items: MCU decode 2102 + BMS iocontrol_scan (done P1 hidden)
        out = research.filter_records(recs, priority="P1")
        assert len(out) == 2

    def test_filter_by_prerequisite(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, state="acc")
        assert {r["ecu"] for r in out} == {"MCU", "BMS"}
        assert all("acc" in r["vehicle_states"] for r in out)

    def test_filters_combine_and(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, ecu="MCU", state="acc")
        assert len(out) == 1 and out[0]["type"] == "scan"


class TestSort:
    def test_priority_orders_first(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = sorted(research.filter_records(recs), key=research._sort_key)
        prios = [r.get("priority") for r in out]
        assert prios == sorted(prios, key=lambda p: research._PRIO_RANK.get(p, 99))
        assert out[0]["priority"] == "P1"


class TestFormatting:
    def test_test_preview_caps_and_counts(self):
        preview = research._test_preview(["a", "b", "c", "d"], limit=2)
        assert preview.startswith("a; b")
        assert "+2 more" in preview

    def test_test_preview_no_tail_when_within_limit(self):
        assert research._test_preview(["x", "y"], limit=2) == "x; y"

    def test_wrapped_uncapped_keeps_all_lines(self):
        text = "word " * 200
        lines = research._wrapped("notes", text)
        assert not any("for full" in ln for ln in lines)

    def test_wrapped_caps_with_marker(self):
        text = "word " * 200
        lines = research._wrapped("notes", text, max_lines=3)
        assert any("lines, -v for full" in ln for ln in lines)

    def test_list_reports_hidden_done(self, pids_dir, capsys):
        recs = research.load_research(pids_dir)
        research.cmd_list(research.filter_records(recs), hidden_done=5)
        out = capsys.readouterr().out
        assert "5 done hidden" in out

    def test_list_verbose_expands_what_to_test(self, pids_dir, capsys):
        recs = research.load_research(pids_dir)
        research.cmd_list(research.filter_records(recs), verbose=True)
        out = capsys.readouterr().out
        # The MCU decode 2102 item carries what_to_test ["a", "b"].
        assert "- a" in out and "- b" in out

    def test_list_limit_keeps_the_highest_priority_and_says_so(self, pids_dir, capsys):
        recs = research.filter_records(research.load_research(pids_dir))
        research.cmd_list(recs, limit=2)
        out = capsys.readouterr().out
        assert "top 2 of 3 items" in out
        assert "2F E000-E0FF" in out and "2102" in out  # the two P1 items
        assert "22 E001-E010" not in out  # the P2 item
        assert "1 more items not shown" in out and "--limit 0" in out

    def test_list_limit_zero_or_above_the_count_hides_nothing(self, pids_dir, capsys):
        recs = research.filter_records(research.load_research(pids_dir))
        for limit in (0, 3):
            research.cmd_list(recs, limit=limit)
            out = capsys.readouterr().out
            assert "3 items" in out and "not shown" not in out


class TestRunLimit:
    def _run(self, pids_dir, capsys, *argv):
        import argparse

        parser = argparse.ArgumentParser()
        research.add_parser(parser.add_subparsers())
        args = parser.parse_args(["research", "--dir", str(pids_dir), *argv])
        assert research.run(args) == 0
        return capsys.readouterr()

    def test_default_limit_is_twenty(self, pids_dir, capsys):
        import argparse

        parser = argparse.ArgumentParser()
        research.add_parser(parser.add_subparsers())
        assert parser.parse_args(["research"]).limit == research.DEFAULT_LIMIT == 20

    def test_json_keeps_the_list_and_warns_on_stderr(self, pids_dir, capsys):
        import json

        captured = self._run(pids_dir, capsys, "--json", "--limit", "1")
        assert [r["target"] for r in json.loads(captured.out)] == ["2F E000-E0FF"]
        assert "top 1 of 3 items" in captured.err

    def test_summary_counts_every_item(self, pids_dir, capsys):
        out = self._run(pids_dir, capsys, "--summary", "--limit", "1").out
        # The done item counts too, though the list view hides it without --all.
        assert "4 total (3 open, 1 done)" in out
