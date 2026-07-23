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
    doc = {"sessions": [{"date": "2026-07-22", "vehicle_states": ["driving"],
                         "captures": caps + refs}]}
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
                specs, captures_dir=tmp_path,
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
                offset=12, mapped_by=None, state_f=3.2, anchor="ESC:22C101:REAL_SPEED_KMH",
                anchor_r=0.997, anchor_n=66, slope=0.6243, intercept=0.0,
                unit_guess="slope≈0.6243 ⇒ raw×1.609 (mph→km/h)",
            ),
            investigate._ByteReport(
                offset=20, mapped_by="VCU_VEHICLE_SPEED", state_f=None, anchor=None,
                anchor_r=None, anchor_n=0, slope=None, intercept=None, unit_guess=None,
            ),
        ]

        class _LP:
            captures = (1, 2, 3)

        args = argparse.Namespace(join_tol=2.5, min_r=0.6, all=True)
        investigate._print_report("AAF", "2181", rpts, args, _LP())
        out = capsys.readouterr().out
        assert "B12" in out and "ESC:22C101:REAL_SPEED_KMH" in out and "r=+0.997" in out
        assert "mph" in out
        assert "B20" in out and "VCU_VEHICLE_SPEED" in out  # mapped tag shown

