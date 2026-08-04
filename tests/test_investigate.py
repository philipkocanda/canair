"""Tests for the `canair investigate` one-shot per-byte report (T3.2)."""

import json

from canlib.commands import _investigate_render as render
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
    (tmp_path / "2026-07-22.json").write_text(json.dumps(doc))


class TestInvestigate:
    def test_registered(self):
        assert investigate.NAME == "investigate"
        assert hasattr(investigate, "run") and hasattr(investigate, "add_parser")

    def test_best_anchor_picks_strongest(self):
        from datetime import datetime, timedelta

        from canlib.align import TimePoint, prepare_series

        def s(vals):
            base = datetime(2026, 7, 22, 9, 0, 0)
            return [TimePoint(base + timedelta(seconds=i), v) for i, v in enumerate(vals)]

        target = s([float(i) for i in range(20)])
        anchors = {
            "E:P:MATCH": prepare_series(s([float(i) for i in range(20)])),  # perfect
            "E:P:NOISE": prepare_series(s([(i * 7) % 5 for i in range(20)])),
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
        args = p.parse_args(["uds", "AAF", "2181", "--min-r", "0.5", "--min-n", "10"])
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

        args = argparse.Namespace(join_tol=2.5, min_r=0.6, all=True, bits=False, notation=None)
        render.print_report("AAF", "2181", rpts, args, _LP(), True)
        out = capsys.readouterr().out
        assert "B12" in out and "ESC:22C101:REAL_SPEED_KMH" in out and "r=+0.997" in out
        assert "mph" in out
        assert "B20" in out and "VCU_VEHICLE_SPEED" in out  # mapped tag shown


def _write_events(tmp_path, keep_mode="unique"):
    """A body PID whose B10 bits toggle over time, with a narrated event note."""
    # B10 goes 0x00 -> 0x20 (bit5) -> 0x00 (a genuine falling edge back to 0).
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
                "keep_mode": keep_mode,
                "notes": "door event test",
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


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
    return investigate.run(p.parse_args(["uds", *argv]))


class TestInvestigateBitsEvents:
    def test_bits_reports_toggling_bit(self, tmp_path, monkeypatch, capsys):
        _write_events(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "B10:5" in out  # the toggling bit surfaces at bit granularity

    def test_no_keep_unique_banner(self, tmp_path, monkeypatch, capsys):
        # keep:unique gets no blanket scope banner (it fired on nearly every
        # report over historical data); its caveats are raised only where they
        # change a reading, e.g. the dwell classes.
        _write_events(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")])
        assert "keep:unique" not in capsys.readouterr().out

    def test_keep_changes_banner(self, tmp_path, monkeypatch, capsys):
        _write_events(tmp_path, keep_mode="changes")
        _run(tmp_path, monkeypatch, ["IGPM", "22BC03", "--bits", "--all"], [("IGPM", "22BC03")])
        out = capsys.readouterr().out
        assert "keep:changes" in out
        assert "keep:unique" not in out

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


def _write_can_log(tmp_path):
    """0x386:r0 and 0x331:r2 ramp together (a mirror); 0x386 other bytes are noise/const."""
    lines = []
    for i in range(30):
        t = i * 0.1
        v = i % 256
        lines.append(f"({t:.6f}) can0 386#{v:02X}FF{v:02X}00000000")
        lines.append(f"({t:.6f}) can0 331#0000{v:02X}00000000")
    p = tmp_path / "drive.log"
    p.write_text("\n".join(lines) + "\n")
    return p


class TestInvestigateCan:
    def _run(self, argv):
        import argparse

        p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["can", *argv])
        return args.func(args)

    def test_ranks_byte_by_cross_id_anchor(self, tmp_path, capsys):
        log = _write_can_log(tmp_path)
        rc = self._run([str(log), "--id", "0x386", "--min-r", "0.9", "--join-tol", "0.05"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Investigate 0x386" in out
        assert "0x386:r0" in out and "0x331:r2" in out  # the mirror is found

    def test_json(self, tmp_path, capsys):
        import json

        log = _write_can_log(tmp_path)
        assert (
            self._run([str(log), "--id", "0x386", "--min-r", "0.9", "--join-tol", "0.05", "--json"])
            == 0
        )
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "0x386"
        top = data["bytes"][0]
        assert top["signal"] == "0x386:r0" and top["anchor"] == "0x331:r2"

    def test_missing_file(self, tmp_path, capsys):
        assert self._run([str(tmp_path / "nope.log"), "--id", "0x386"]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_bad_id_clean_error(self, tmp_path, capsys):
        log = _write_can_log(tmp_path)
        assert self._run([str(log), "--id", "0xZZZ"]) == 1
        err = capsys.readouterr().err
        assert "invalid arbitration ID" in err
        assert "invalid literal" not in err

    def test_unknown_id_no_frames(self, tmp_path, capsys):
        log = _write_can_log(tmp_path)
        assert self._run([str(log), "--id", "0x999"]) == 1
        assert "no varying" in capsys.readouterr().err


class TestFieldEvents:
    """investigate --events --field NAME: one logical transition per decoded
    value change of a typed param (Layer 3)."""

    def test_field_transition_timeline(self, capsys):
        # Two timed captures where B5 (a fan enum) changes 0x2D -> 0x28.
        caps = [
            {
                "ecu": "HVAC",
                "pid": "220100",
                "payload": "620001042D",
                "time": "10:00:00",
                "date": "2026-07-25",
            },
            {
                "ecu": "HVAC",
                "pid": "220100",
                "payload": "6200010428",
                "time": "10:05:00",
                "date": "2026-07-25",
            },
        ]
        param = {
            "expression": "B5",
            "type": "enum",
            "values": {0x28: "fan1", 0x2D: "fanMAX"},
        }

        class LP:
            captures = caps
            n_no_time = 0

        args = type("A", (), {"field": "FAN", "json": False})()
        render.print_events("HVAC", "220100", LP(), {}, {}, args, {"FAN": param})
        out = capsys.readouterr().out
        assert "fanMAX (45)" in out and "fan1 (40)" in out
        assert "→" in out

    def test_missing_field_reports_error(self, capsys):
        class LP:
            captures = ()

        args = type("A", (), {"field": "NOPE", "json": False})()
        render.print_events("HVAC", "220100", LP(), {}, {}, args, {})
        err = capsys.readouterr().err
        assert "no parameter" in err


class TestIndependenceScore:
    def test_high_statef_low_driver_wins(self):
        # A byte that separates by state (high F) but barely tracks the driver
        # scores higher than one that separates equally but tracks it strongly.
        indep = investigate._independence_score(10.0, 0.05)
        dependent = investigate._independence_score(10.0, 0.95)
        assert indep > dependent

    def test_none_state_f_is_none(self):
        assert investigate._independence_score(None, 0.1) is None

    def test_missing_driver_counts_as_independent(self):
        assert investigate._independence_score(4.0, None) == 4.0


class TestIndependentOf:
    def _write(self, tmp_path):
        # AAF 2181: B4 ramps (tracks the driver), B5 flips by state (independent).
        # State comes from the session, so split into a ready and a charging
        # session interleaved in time (B5=0 while ready, 100 while charging).
        ready, charging = [], []
        for i in range(6):
            b4 = 10 + i * 10  # 10..60, tracks the driver
            cap = {
                "ecu": "AAF",
                "pid": "2181",
                "payload": f"6181{b4:02X}{0 if i % 2 == 0 else 100:02X}00000000",
                "time": f"09:00:0{i}",
            }
            (ready if i % 2 == 0 else charging).append(cap)
        doc = {
            "sessions": [
                {"date": "2026-07-24", "vehicle_states": ["ready"], "captures": ready},
                {"date": "2026-07-24", "vehicle_states": ["charging"], "captures": charging},
            ]
        }
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))

    def test_independent_of_file_ranks_and_labels(self, tmp_path, monkeypatch, capsys):
        import argparse

        self._write(tmp_path)
        csv = tmp_path / "driver.csv"
        csv.write_text("".join(f"2026-07-24 09:00:0{i},{10 + i * 10}\n" for i in range(6)))
        import canlib.align as align

        orig = align.load_signal_captures
        monkeypatch.setattr(
            "canlib.commands.investigate.load_signal_captures",
            lambda specs, **kw: orig(
                specs, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
            ),
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs", lambda *a, **k: [("AAF", "2181")]
        )
        p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(
            ["uds", "AAF", "2181", "--independent-of-file", str(csv), "--min-n", "3", "--json"]
        )
        rc = investigate.run(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["independent_of"] == "driver.csv"
        # B5 (state-separating, driver-independent) should rank above B4 (tracks driver).
        offs = [b["offset"] for b in data["bytes"]]
        assert offs.index(5) < offs.index(4)
        b4 = next(b for b in data["bytes"] if b["offset"] == 4)
        b5 = next(b for b in data["bytes"] if b["offset"] == 5)
        assert abs(b4["driver_r"]) > abs(b5["driver_r"])


class TestTriageIntegration:
    def _write(self, tmp_path):
        # A word hides across [B4:B5]: hi byte barely moves (85-86), lo byte
        # sweeps 5..255 (the fraction). Multi-frame payload (SID@B2, word@B4:B5).
        pairs = [(85, 10), (85, 250), (86, 40), (85, 200), (86, 80), (85, 255), (86, 5), (85, 180)]
        caps = []
        for i, (hi, lo) in enumerate(pairs):
            caps.append(
                {
                    "ecu": "OBC",
                    "pid": "2101",
                    "payload": f"6101{hi:02X}{lo:02X}00000000",
                    "time": f"09:00:0{i}",
                }
            )
        doc = {
            "sessions": [{"date": "2026-07-24", "vehicle_states": ["charging"], "captures": caps}]
        }
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))

    def test_word_candidates_and_kind(self, tmp_path, monkeypatch, capsys):
        import argparse

        self._write(tmp_path)
        import canlib.align as align

        orig = align.load_signal_captures
        monkeypatch.setattr(
            "canlib.commands.investigate.load_signal_captures",
            lambda specs, **kw: orig(
                specs, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
            ),
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs", lambda *a, **k: [("OBC", "2101")]
        )
        p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["uds", "OBC", "2101", "--min-n", "3", "--json"])
        rc = investigate.run(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        # The [B4:B5] word (near-constant hi + wide lo) should be detected.
        exprs = [w["expr"] for w in data["word_candidates"]]
        assert "[B4:B5]" in exprs
        # Every byte carries a triage classification.
        assert all(b["kind"] for b in data["bytes"])

    def test_no_spanning_word_across_a_constant_gap_byte(self, tmp_path, monkeypatch, capsys):
        import argparse

        # B4 varies a little, B5 is CONSTANT (dropped from the min_distinct=2
        # report set), B6 sweeps wide. The old wiring fed the filtered set and
        # would pair (B4,B6) across the dropped B5 as a misleading [B4:B6].
        # The fix pairs only truly-adjacent bytes: expect [B5:B6], never [B4:B6].
        b4_vals = [85, 86, 85, 86, 85, 86]
        b6_vals = [10, 250, 40, 200, 5, 230]
        caps = []
        for i in range(6):
            payload = f"6101{b4_vals[i]:02X}00{b6_vals[i]:02X}000000"  # B5 const 0x00
            caps.append({"ecu": "OBC", "pid": "2101", "payload": payload, "time": f"09:00:0{i}"})
        doc = {
            "sessions": [{"date": "2026-07-24", "vehicle_states": ["charging"], "captures": caps}]
        }
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))

        import canlib.align as align

        orig = align.load_signal_captures
        monkeypatch.setattr(
            "canlib.commands.investigate.load_signal_captures",
            lambda specs, **kw: orig(
                specs, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
            ),
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs", lambda *a, **k: [("OBC", "2101")]
        )
        p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["uds", "OBC", "2101", "--min-n", "3", "--json"])
        rc = investigate.run(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        exprs = [w["expr"] for w in data["word_candidates"]]
        assert "[B5:B6]" in exprs  # the real adjacent word (constant hi + wide lo)
        assert "[B4:B6]" not in exprs  # no misleading pair spanning the dropped B5


class TestDwellSummary:
    """Per-signal on-duration classification (momentary door vs sustained hood)."""

    def _lp(self):
        from canlib.align import LoadedPid

        # B10 (payload idx 7) carries two bits: bit0 pulses ON for one 5s sample
        # (a door flicked open→closed); bit1 stays ON for 20s (a hood left up).
        seq = [(0, 0x00), (5, 0x01), (10, 0x02), (15, 0x02), (20, 0x02), (25, 0x02), (30, 0x00)]
        lp = LoadedPid("IGPM", "22BC03")
        for s, v in seq:
            lp.captures.append(
                {
                    "ecu": "IGPM",
                    "pid": "22BC03",
                    "date": "2026-07-24",
                    "time": f"09:00:{s:02d}",
                    "payload": f"62BC03FDEE3C73{v:02X}0000",
                }
            )
        return lp

    def test_classifies_momentary_vs_sustained(self):
        rows = {r["signal"]: r for r in render.dwell_summary(self._lp(), {}, {}, bits=True)}
        assert rows["B10:0"]["class"] == "momentary"  # 5s pulse ≈ one poll interval
        assert rows["B10:1"]["class"] == "sustained"  # 20s held
        assert rows["B10:1"]["median_on_s"] == 20.0
        assert rows["B10:0"]["median_on_s"] == 5.0

    def test_sustained_ranked_first(self):
        rows = render.dwell_summary(self._lp(), {}, {}, bits=True)
        assert rows[0]["class"] == "sustained"  # held signals surface above flickers

    def test_still_on_at_end_is_unknown(self):
        from canlib.align import LoadedPid

        lp = LoadedPid("IGPM", "22BC03")
        for s, v in [(0, 0x00), (5, 0x01), (10, 0x01)]:  # rises, never falls in scope
            lp.captures.append(
                {
                    "ecu": "IGPM",
                    "pid": "22BC03",
                    "date": "2026-07-24",
                    "time": f"09:00:{s:02d}",
                    "payload": f"62BC03FDEE3C73{v:02X}0000",
                }
            )
        rows = {r["signal"]: r for r in render.dwell_summary(lp, {}, {}, bits=True)}
        assert rows["B10:0"]["class"] == "unknown"
        assert rows["B10:0"]["median_on_s"] is None
