"""The contribution summary and the diagnostics-log excerpt for the PR body."""

from __future__ import annotations

import json

from canlib import contribute_logs as L
from canlib import contribute_summary as S


def _session(label, *, states=("READY",), rx="0x7EC", quality=None, events=None, **extra):
    s = {
        "date": "2026-11-01",
        "label": label,
        "version": "1.2.0",
        "vehicle_states": list(states),
        "transport": "slcan-tcp",
        "captures": [{"rx": rx, "pid": "2101", "payload": "6101", "time": "10:00:00.000"}],
        **extra,
    }
    if quality:
        s["quality"] = quality
    if events:
        s["link_events"] = events
    return s


def _write(path, sessions):
    path.write_text(json.dumps({"sessions": sessions}))


class TestSummarize:
    def test_only_sessions_upstream_lacks_are_counted(self, tmp_path):
        old = _session("old drive")
        new = _session(
            "new drive",
            states=("DRIVING", "READY"),
            quality={"exchanges": 40, "drop": 2, "stale": 1, "resyncs": 1},
            events=[{"at": "10:00:01.000", "kind": "disconnect", "until": "10:00:05.000"}],
            variant="28kwh",
            revisions={"BMS": "a"},
        )
        caps = tmp_path / "captures"
        caps.mkdir()
        _write(caps / "2026-11-01.json", [old, new])
        upstream = json.dumps({"sessions": [old]})

        summary = S.summarize(
            caps,
            profile_name="car",
            base_reader=lambda rel: upstream if rel.endswith("2026-11-01.json") else None,
            definition_files=["ecus/bms.yaml"],
            rx_names={0x7EC: "BMS"},
        )

        assert [s.label for s in summary.sessions] == ["new drive"]
        assert summary.ecus == {"BMS": 1}
        assert summary.variants == {"28kwh": 1}
        assert summary.revisions == {"BMS": {"a"}}
        assert summary.errors == {"drop": 2, "stale": 1}
        assert summary.resyncs == 1
        assert summary.link_events == {"disconnect": 1}

        md = S.render_markdown(summary, include_captures=True)
        assert "1 new (1 captures, 2026-11-01)" in md
        assert "drop ×2, stale" in md
        assert "| Disconnects / reconnects | 1 |" in md
        assert "`ecus/bms.yaml`" in md
        assert "BMS=a" in md

    def test_definitions_only(self):
        md = S.render_markdown(
            S.ContributionSummary(definition_files=["ecus/bms.yaml"]), include_captures=False
        )
        assert "Definitions only" in md


class TestLogs:
    def test_scrubs_addresses_paths_and_vins(self):
        line = (
            "2026-11-01T10:00:00.000Z WARNING drop transport=wican-ws ecu=BMS :: "
            "ws://wican.local/ws 10.0.2.86 aa:bb:cc:dd:ee:ff /Users/me/x KMHC851CGLU000001 a@b.io"
        )
        clean = L.scrub(line, home="/Users/me")
        assert clean.startswith("2026-11-01T10:00:00.000Z")  # times survive
        for gone in ("wican.local", "10.0.2.86", "aa:bb", "/Users/me", "KMHC851", "a@b.io"):
            assert gone not in clean
        assert "~/x" in clean

    def test_window_and_cap(self):
        lines = [f"2026-10-0{d}T00:00:00Z WARNING drop :: n{d}" for d in range(1, 10)]
        excerpt = L.collect("2026-10-05", lines=lines, max_lines=3)
        assert [ln[-2:] for ln in excerpt.lines] == ["n7", "n8", "n9"]
        assert excerpt.total == 6 and excerpt.truncated
        assert "last 3 of 6" in L.render_markdown(excerpt)
