"""Tests for `canair hunt` external-reference (--against-file) support."""

import argparse
import json

from canlib.commands import hunt
from canlib.inspect_bytes import NO_EXPR


def _write_ramp(tmp_path):
    """AAF 2181 where the first data byte (B3) ramps over five timed captures."""
    caps = []
    times = ["09:00:00", "09:00:01", "09:00:02", "09:00:03", "09:00:04"]
    for i, t in enumerate(times):
        v = 10 + i * 10  # 10,20,30,40,50 — ≥3 distinct
        caps.append({"ecu": "AAF", "pid": "2181", "payload": f"6181{v:02X}0000", "time": t})
    doc = {"sessions": [{"date": "2026-07-24", "vehicle_states": ["driving"], "captures": caps}]}
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


def _run(tmp_path, monkeypatch, argv):
    import canlib.align as align

    orig = align.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.hunt.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    monkeypatch.setattr("canlib.ecus.canonical_ecu_name_safe", lambda e: e.upper())
    p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
    args = p.parse_args(["uds", *argv])
    return args.func(args)


class TestHuntAgainstFile:
    def test_external_reference_finds_byte(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        csv = tmp_path / "gps.csv"
        csv.write_text(
            "timestamp,value\n"
            "2026-07-24 09:00:00,1.0\n"
            "2026-07-24 09:00:01,2.0\n"
            "2026-07-24 09:00:02,3.0\n"
            "2026-07-24 09:00:03,4.0\n"
            "2026-07-24 09:00:04,5.0\n"
        )
        rc = _run(
            tmp_path,
            monkeypatch,
            ["AAF", "2181", "--against-file", str(csv), "--min-n", "4", "--json"],
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["reference"] == "gps.csv"
        # The ramping byte B3 should be the top (perfectly-correlated) hit.
        assert data["hits"], "expected at least one hit"
        assert data["hits"][0]["expr"] == "B3"
        assert abs(data["hits"][0]["r"]) > 0.99

    def test_bad_file_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["AAF", "2181", "--against-file", str(tmp_path / "nope.csv")]
        )
        assert rc == 1
        assert "--against-file error" in capsys.readouterr().err

    def test_against_and_against_file_mutually_exclusive(self, tmp_path):
        # argparse enforces the mutually-exclusive required group at parse time.
        p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
        try:
            p.parse_args(
                ["uds", "AAF", "2181", "--against", "ESC:22C101:X", "--against-file", "f.csv"]
            )
            raise AssertionError("expected SystemExit from mutually-exclusive group")
        except SystemExit:
            pass


class TestHuntPhysical:
    def _write_voltage(self, tmp_path):
        # OBC 2101 multi-frame: a centivolt AC-voltage word at [B4:B5].
        caps = []
        for i, cv in enumerate((21850, 22100, 22400, 22750, 22200)):
            caps.append(
                {
                    "ecu": "OBC",
                    "pid": "2101",
                    "payload": f"6101{cv:04X}00000000",
                    "time": f"09:00:0{i}",
                }
            )
        doc = {
            "sessions": [{"date": "2026-07-24", "vehicle_states": ["charging"], "captures": caps}]
        }
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))

    def test_physical_flags_mains_band(self, tmp_path, monkeypatch, capsys):
        self._write_voltage(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["OBC", "2101", "--physical", "--min-n", "3", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "physical"
        assert any(h["band"] == "mains RMS V" and h["scaling"] == "/100" for h in data["hits"])

    def test_physical_needs_no_reference(self, tmp_path, monkeypatch):
        # --physical is a valid member of the required reference group.
        self._write_voltage(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["OBC", "2101", "--physical", "--min-n", "3"])
        assert rc == 0


class TestHuntControl:
    def test_control_and_control_file_mutually_exclusive(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        rc = _run(
            tmp_path,
            monkeypatch,
            [
                "AAF",
                "2181",
                "--against",
                "ESC:22C101:X",
                "--control",
                "A:B:C",
                "--control-file",
                "f",
            ],
        )
        assert rc == 2
        assert "mutually exclusive" in capsys.readouterr().err


class TestHuntControlBehaviour:
    def _write_confounded(self, tmp_path):
        # B4 == the confounder Z; B9 == an independent component W (placed a frame
        # apart so no multi-byte read merges them). Reference X = Z + W, so both
        # bytes correlate with X. Controlling for Z should collapse everything at
        # offset 4 (it is Z) and keep B9 (the part of X not explained by Z).
        z = [0, 1, 2, 3, 4, 5, 6, 7]
        w = [0, 3, 1, 4, 2, 5, 3, 6]
        caps = []
        for i in range(8):
            # raw payload: 61 01 Z 00 00 00 W 00 -> WiCAN B4=Z, B9=W
            payload = f"6101{z[i]:02X}000000{w[i]:02X}00"
            caps.append({"ecu": "OBC", "pid": "2101", "payload": payload, "time": f"09:00:0{i}"})
        doc = {"sessions": [{"date": "2026-07-24", "captures": caps}]}
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))
        x_csv = tmp_path / "ref.csv"
        x_csv.write_text("".join(f"2026-07-24 09:00:0{i},{z[i] + w[i]}\n" for i in range(8)))
        z_csv = tmp_path / "ctrl.csv"
        z_csv.write_text("".join(f"2026-07-24 09:00:0{i},{z[i]}\n" for i in range(8)))
        return x_csv, z_csv

    def test_control_demotes_the_confounder_byte(self, tmp_path, monkeypatch, capsys):
        x_csv, z_csv = self._write_confounded(tmp_path)

        def max_abs_r_by_offset(out):
            by_off: dict[int, float] = {}
            for h in json.loads(out)["hits"]:
                by_off[h["offset"]] = max(by_off.get(h["offset"], 0.0), abs(h["r"]))
            return by_off

        # Without control: the confounder byte at offset 4 correlates strongly.
        rc = _run(
            tmp_path,
            monkeypatch,
            ["OBC", "2101", "--against-file", str(x_csv), "--min-n", "4", "--json"],
        )
        assert rc == 0
        plain = max_abs_r_by_offset(capsys.readouterr().out)
        assert plain.get(4, 0.0) > 0.6  # strong apparent link via the confounder

        # With control for Z: the strong Z-driven read at offset 4 has an
        # undefined/collapsed partial correlation, so offset 4's strength drops
        # sharply (only weak float-sweep noise remains); the independent byte at
        # offset 9 stays strong.
        rc = _run(
            tmp_path,
            monkeypatch,
            [
                "OBC",
                "2101",
                "--against-file",
                str(x_csv),
                "--control-file",
                str(z_csv),
                "--min-n",
                "4",
                "--json",
            ],
        )
        assert rc == 0
        controlled = max_abs_r_by_offset(capsys.readouterr().out)
        assert controlled.get(4, 0.0) < 0.6  # confounder-driven correlation removed
        assert controlled.get(4, 0.0) < plain[4] - 0.3  # markedly weaker than uncontrolled
        assert controlled.get(9, 0.0) > 0.4  # the genuinely-independent byte remains


class TestHuntNoExprWarning:
    """A float-reinterpretation top hit must be flagged as unpromotable.

    Ranking is by |r| alone, so a float read (which has NO WiCAN expression and
    therefore cannot be promoted or written as a param) can top the table and push
    the expressible candidates down. Reading integer byte runs as IEEE floats
    correlates spuriously, so the user needs to be told — see
    plans/2026-08-02-blind-tooling-stress-test.md.
    """

    def _write_float_topping(self, tmp_path):
        """AAF 2181 whose f16 read out-correlates every integer read of the same bytes.

        B3/B4 are chosen so the big-endian f16 at B3 rises monotonically (a clean
        linear ramp vs the reference) while no single byte does.
        """
        import struct

        caps = []
        # f16 values forming a clean ramp; their raw bytes deliberately zig-zag.
        for i, fv in enumerate((1.0, 2.0, 3.0, 4.0, 5.0)):
            hi, lo = struct.pack(">e", fv)
            caps.append(
                {
                    "ecu": "AAF",
                    "pid": "2181",
                    "payload": f"6181{hi:02X}{lo:02X}00",
                    "time": f"09:00:0{i}",
                }
            )
        doc = {
            "sessions": [{"date": "2026-07-24", "vehicle_states": ["driving"], "captures": caps}]
        }
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))

    def _ramp_csv(self, tmp_path):
        csv = tmp_path / "ref.csv"
        csv.write_text(
            "timestamp,value\n" + "".join(f"2026-07-24 09:00:0{i},{i + 1}.0\n" for i in range(5))
        )
        return csv

    def test_warns_when_top_hit_is_a_float(self, tmp_path, monkeypatch, capsys):
        self._write_float_topping(tmp_path)
        csv = self._ramp_csv(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["AAF", "2181", "--against-file", str(csv), "--min-n", "4"]
        )
        assert rc == 0
        captured = capsys.readouterr()  # read ONCE — a second call returns empty
        assert NO_EXPR in captured.out, "expected a float hit to top this fixture"
        assert "float reinterpretation" in captured.err
        assert "cannot be promoted" in captured.err

    def test_promote_refuses_a_float_top_hit(self, tmp_path, monkeypatch, capsys):
        """`--promote` must refuse rather than write an unusable expression."""
        self._write_float_topping(tmp_path)
        csv = self._ramp_csv(tmp_path)
        rc = _run(
            tmp_path,
            monkeypatch,
            ["AAF", "2181", "--against-file", str(csv), "--min-n", "4", "--promote", "CAND"],
        )
        assert rc == 1
        assert "cannot promote" in capsys.readouterr().err

    def test_no_warning_when_top_hit_is_expressible(self, tmp_path, monkeypatch, capsys):
        """The control case: a plain ramping byte tops the table -> stay silent."""
        _write_ramp(tmp_path)
        csv = self._ramp_csv(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["AAF", "2181", "--against-file", str(csv), "--min-n", "4"]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "B3" in captured.out
        assert "float reinterpretation" not in captured.err


class TestNoExprConstant:
    def test_only_floats_are_inexpressible(self):
        """NO_EXPR must be reachable ONLY for floats.

        Regression guard for the stress-test finding: LE and PCI-straddling signed
        ints once printed ``<no-expr>`` though they are expressible as arithmetic
        shift forms, which made a top-ranked hit un-promotable for no reason.
        """
        from canlib.inspect_bytes import INSPECT_TYPES, wican_expr

        for spec in INSPECT_TYPES:
            for little in (False, True):
                expr = wican_expr(9, spec, little=little)
                if spec.kind == "float":
                    assert expr is None, f"{spec.name} should have no expression"
                else:
                    assert expr, f"{spec.name} (little={little}) must be expressible"

    def test_le_signed_uses_arithmetic_form(self):
        from canlib.inspect_bytes import INSPECT_TYPES, wican_expr

        i16 = next(s for s in INSPECT_TYPES if s.name == "i16")
        assert wican_expr(9, i16, little=True) == "B9 + S10*256"
        assert wican_expr(9, i16, little=False) == "[S9:S10]"
