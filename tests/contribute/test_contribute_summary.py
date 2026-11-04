"""The contribution summary and the diagnostics-log excerpt for the PR body."""

from __future__ import annotations

import json
import time

import pytest

from canair.contribute import logs as L
from canair.contribute import summary as S


@pytest.fixture
def local_tz(monkeypatch):
    """Pin the local time zone (capture times are local, event times UTC)."""
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


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

    @pytest.mark.parametrize(
        "address",
        [
            "10.0.2.86",
            "192.168.80.1",
            "2001:db8:85a3:0:0:8a2e:370:7334",
            "2001:db8::1",
            "fe80::1c2b:3aff:fe4d:5e6f",
            "fe80::1%en0",
            "::1",
            "::ffff:10.0.2.86",
            "2001:DB8::ABCD",
        ],
    )
    @pytest.mark.parametrize(
        "template",
        [
            "connecting to {}",
            "connecting to {}.",
            "peer {}:35000 closed",
            "peer [{}]:35000 closed",
            "ws://[{}]/ws dropped",
            "addr=({}), retrying",
        ],
    )
    def test_scrubs_every_ip_form(self, address, template):
        if template.startswith("peer {}:") and ":" in address:
            pytest.skip("an unbracketed IPv6 address cannot carry a port")
        clean = L.scrub(f"2026-11-01T10:00:00.000Z WARNING bus :: {template.format(address)}")
        assert address not in clean
        assert address.split("%")[0] not in clean
        assert "<ip>" in clean or "<host>" in clean

    @pytest.mark.parametrize(
        "kept",
        [
            "2026-11-01T10:00:00.123456+00:00",
            "elapsed 10:00:00",
            "ecu=BMS :: drop",
            "reply 62:F1:90 short",
            "0x7EC:2101",
            "Class::method",
            "deadbeef",
        ],
    )
    def test_leaves_times_separators_and_hex_alone(self, kept):
        assert L.scrub(kept, home="/nonexistent") == kept

    def test_window_and_cap(self, local_tz):
        lines = [f"2026-10-0{d}T10:00:00Z WARNING drop :: n{d}" for d in range(1, 10)]
        windows = L.session_windows((f"2026-10-0{d}", "", "") for d in range(4, 10))
        excerpt = L.collect(windows, lines=lines, max_lines=3)
        assert [ln[-2:] for ln in excerpt.lines] == ["n7", "n8", "n9"]
        assert excerpt.total == 6 and excerpt.truncated
        assert "last 3 of 6" in L.render_markdown(excerpt)

    def test_only_events_inside_a_contributed_session(self, local_tz):
        # 10:00 to 10:30 local (UTC+1 in winter) is 09:00 to 09:30 UTC, widened by the margins.
        windows = L.session_windows([("2026-11-01", "10:00:00.000", "10:30:00.000")])
        lines = [
            "2026-11-01T08:40:00.000000Z WARNING drop run=a :: before the session",
            "2026-11-01T08:59:00.000000Z WARNING bus run=b :: while connecting",
            "2026-11-01T09:15:00.000000Z WARNING stale run=b :: mid-session",
            "2026-11-01T09:30:30.000000Z WARNING drop run=b :: shutting down",
            "2026-11-01T10:15:00.000000Z WARNING drop run=c :: a later session",
            "garbage",
        ]
        excerpt = L.collect(windows, lines=lines)
        assert [ln.rsplit(":: ", 1)[1] for ln in excerpt.lines] == [
            "while connecting",
            "mid-session",
            "shutting down",
        ]

    def test_untimed_session_covers_its_local_day(self, local_tz):
        window = L.session_window("2026-11-01")
        assert window is not None
        lo, hi = window
        assert lo.isoformat() == "2026-10-31T23:00:00+00:00"
        assert hi.isoformat() == "2026-11-01T23:00:00+00:00"
        assert L.session_window("not a date") is None

    def test_no_sessions_attaches_nothing(self):
        excerpt = L.collect([], lines=["2026-11-01T09:00:00Z WARNING drop :: x"])
        assert excerpt.lines == []
        assert "adds no capture sessions" in L.render_markdown(excerpt)

    def test_summary_records_each_session_span(self, tmp_path):
        session = _session("drive")
        session["captures"] += [
            {"rx": "0x7EC", "pid": "2101", "payload": "6102", "time": "10:20:00.500"},
            {"rx": "0x7EC", "pid": "2101", "payload": "6103", "time": "09:59:59"},
        ]
        caps = tmp_path / "captures"
        caps.mkdir()
        _write(caps / "2026-11-01.json", [session])
        summary = S.summarize(caps, profile_name="car", base_reader=lambda rel: None)
        line = summary.sessions[0]
        assert (line.date, line.start, line.end) == ("2026-11-01", "09:59:59", "10:20:00.500")
