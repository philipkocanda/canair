"""Tests for the `canair investigate` one-shot per-byte report (T3.2)."""

import yaml

from canlib.commands import investigate


def _write(tmp_path):
    """Target AAF 2181 with a byte (B4) that ramps like the ESC speed reference."""
    caps, refs = [], []
    for i in range(20):
        t = f"09:00:{i:02d}"
        caps.append({"ecu": "AAF", "pid": "2181", "payload": f"618100{i:02X}", "time": t})
        refs.append({"ecu": "ESC", "pid": "22C101", "payload": f"62C10100{i:02X}", "time": t})
    doc = {
        "sessions": [{"date": "2026-07-22", "vehicle_states": ["driving"], "captures": caps + refs}]
    }
    (tmp_path / "2026-07-22.yaml").write_text(yaml.safe_dump(doc))


class TestInvestigate:
    def test_registered(self):
        assert investigate.NAME == "investigate"
        assert hasattr(investigate, "run") and hasattr(investigate, "add_parser")

    def test_best_anchor_picks_strongest(self):
        from datetime import datetime, timedelta

        from canlib.align import TimePoint

        def s(vals):
            base = datetime(2026, 7, 22, 9, 0, 0)
            return [TimePoint(base + timedelta(seconds=i), v) for i, v in enumerate(vals)]

        target = s([float(i) for i in range(20)])
        anchors = {
            "E:P:MATCH": s([float(i) for i in range(20)]),  # perfect
            "E:P:NOISE": s([(i * 7) % 5 for i in range(20)]),
        }
        best = investigate._best_anchor(target, anchors, tol=1.0, min_n=10)
        assert best is not None
        assert best[0] == "E:P:MATCH"
        assert abs(best[1]) > 0.99

    def test_report_flags_mapped_and_anchor(self, tmp_path, monkeypatch, capsys):
        import argparse

        _write(tmp_path)
        import canlib.align as align

        orig = align.load_signal_captures
        monkeypatch.setattr(
            "canlib.commands.investigate.load_signal_captures",
            lambda specs, **kw: orig(
                specs,
                captures_dir=tmp_path,
                **{k: v for k, v in kw.items() if k != "captures_dir"},
            ),
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs",
            lambda *a, **k: [("AAF", "2181"), ("ESC", "22C101")],
        )

        p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["AAF", "2181", "--min-r", "0.5", "--min-n", "10"])
        rc = investigate.run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Investigate AAF 2181" in out
        assert "B4" in out and "unmapped" in out  # the varying byte is reported

    def test_print_report_renders_anchor_and_mapped(self, capsys):
        import argparse

        rpts = [
            investigate._ByteReport(
                offset=12,
                mapped_by=None,
                mapped_verified=False,
                state_f=3.2,
                anchor="ESC:22C101:REAL_SPEED_KMH",
                anchor_r=0.997,
                anchor_n=66,
                slope=0.6243,
                intercept=0.0,
                unit_guess="slope≈0.6243 ⇒ raw×1.609 (mph→km/h)",
            ),
            investigate._ByteReport(
                offset=20,
                mapped_by="VCU_VEHICLE_SPEED",
                mapped_verified=True,
                state_f=None,
                anchor=None,
                anchor_r=None,
                anchor_n=0,
                slope=None,
                intercept=None,
                unit_guess=None,
            ),
        ]

        class _LP:
            captures = (1, 2, 3)

        args = argparse.Namespace(join_tol=2.5, min_r=0.6, all=True, bits=False)
        investigate._print_report("AAF", "2181", rpts, args, _LP(), True)
        out = capsys.readouterr().out
        assert "B12" in out and "ESC:22C101:REAL_SPEED_KMH" in out and "r=+0.997" in out
        assert "mph" in out
        assert "B20" in out and "VCU_VEHICLE_SPEED" in out  # mapped tag shown


def _write_events(tmp_path):
    """A body PID whose B10 bits toggle over time, with a narrated event note."""
    # B10 goes 0x00 -> 0x20 (bit5) -> 0x00; keep:unique-style rising edges.
    seq = [("09:00:00", "00"), ("09:00:05", "20"), ("09:00:10", "00")]
    caps = [
        {
            "ecu": "IGPM",
            "pid": "22BC03",
            "payload": f"62BC03FDEE3C73{v}0000",
            "time": t,
            "notes": "open drv door" if v == "20" else "",
        }
        for t, v in seq
    ]
    doc = {
        "sessions": [
            {
                "date": "2026-07-24",
                "vehicle_states": ["sleep"],
                "keep_mode": "unique",
                "notes": "door event test",
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.yaml").write_text(yaml.safe_dump(doc))


def _run(tmp_path, monkeypatch, argv, specs):
    import argparse

    import canlib.align as align

    orig = align.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.investigate.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    monkeypatch.setattr("canlib.commands.correlate._discover_specs", lambda *a, **k: specs)
    p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
    return investigate.run(p.parse_args(argv))


class TestInvestigateBitsEvents:
    def test_bits_reports_toggling_bit(self, tmp_path, monkeypatch, capsys):
        _write_events(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "B10:5" in out  # the toggling bit surfaces at bit granularity

    def test_keep_unique_banner(self, tmp_path, monkeypatch, capsys):
        _write_events(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")])
        assert "keep:unique" in capsys.readouterr().out

    def test_events_edges_with_note(self, tmp_path, monkeypatch, capsys):
        _write_events(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["IGPM", "22BC03", "--events", "--bits"], [("IGPM", "22BC03")]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Events IGPM 22BC03" in out
        assert "B10:5" in out and "0→1" in out  # rising edge
        assert "open drv door" in out  # aligned to the narrated note

    def test_events_json(self, tmp_path, monkeypatch, capsys):
        import json

        _write_events(tmp_path)
        _run(
            tmp_path,
            monkeypatch,
            ["IGPM", "22BC03", "--events", "--bits", "--json"],
            [("IGPM", "22BC03")],
        )
        data = json.loads(capsys.readouterr().out)
        assert data["keep_unique"] is True
        rises = [e for e in data["events"] if e["signal"] == "B10:5" and e["after"] == 1]
        assert rises and rises[0]["note"] == "open drv door"

    def test_no_anchor_hint(self, tmp_path, monkeypatch, capsys):
        # A body PID with no co-polled partner should not say "nothing"; it
        # should rank by state and hint at --events.
        _write_events(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")])
        out = capsys.readouterr().out
        assert "no co-polled anchor" in out and "--events" in out
