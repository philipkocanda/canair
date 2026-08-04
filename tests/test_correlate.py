"""Tests for `canair correlate` cross-ECU mirror finding (T1.3)."""

import argparse
import json

from canlib.commands import correlate


def _write(tmp_path, keep_mode="unique"):
    """Two co-polled PIDs where IGPM B10:7 mirrors a bit on a second PID."""
    caps = []
    for i, t in enumerate(["09:00:00", "09:00:02", "09:00:04", "09:00:06"]):
        bit = 0x80 if i % 2 else 0x00  # B10 bit7 toggles
        caps.append(
            {"ecu": "IGPM", "pid": "22BC03", "payload": f"62BC03FDEE3C73{bit:02X}0000", "time": t}
        )
        # Second PID: B05 mirrors the same bit in its bit3 (0x08).
        mirror = 0x08 if i % 2 else 0x00
        caps.append(
            {
                "ecu": "IGPM",
                "pid": "22BC05",
                "payload": f"62BC057F1120{mirror:02X}000000",
                "time": t,
            }
        )
    doc = {
        "sessions": [
            {
                "date": "2026-07-24",
                "vehicle_states": ["sleep"],
                "keep_mode": keep_mode,
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


def _run(tmp_path, monkeypatch, argv):
    import canlib.align as align

    orig = align.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.correlate.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    monkeypatch.setattr(
        "canlib.commands.correlate._discover_specs",
        lambda *a, **k: [("IGPM", "22BC03"), ("IGPM", "22BC05")],
    )
    p = correlate.add_parser(argparse.ArgumentParser().add_subparsers())
    args = p.parse_args(["uds", *argv])
    return args.func(args)


class TestCrossMirrors:
    def test_finds_cross_pid_mirror(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["IGPM", "--find-mirrors", "--bits", "--min-n", "3", "--json"]
        )
        assert rc == 0
        import json

        data = json.loads(capsys.readouterr().out)
        pairs = {(m["a"], m["b"]) for m in data["mirrors"]}
        assert any("22BC03:B10:7" in a and "22BC05" in b for a, b in pairs)

    def test_excludes_same_pid(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "--find-mirrors", "--bits", "--min-n", "3", "--json"])
        import json

        data = json.loads(capsys.readouterr().out)
        for m in data["mirrors"]:
            a_pid = m["a"].split(":")[1]
            b_pid = m["b"].split(":")[1]
            assert a_pid != b_pid  # same-PID mirrors are decode's job

    def test_no_keep_unique_banner_text_mode(self, tmp_path, monkeypatch, capsys):
        # No blanket keep:unique scope banner — only the --transform/--lag-scan
        # caveats mention it (see test_transform_caveat_*).
        _write(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "--min-n", "3"])
        assert "keep:unique" not in capsys.readouterr().out

    def test_keep_unique_transform_caveat(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "--min-n", "3", "--transform", "delta"])
        out = capsys.readouterr().out
        assert "--transform delta on keep:unique data is unreliable" in out

    def test_keep_changes_banner_text_mode(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, keep_mode="changes")
        _run(tmp_path, monkeypatch, ["IGPM", "--min-n", "3"])
        out = capsys.readouterr().out
        assert "keep:changes" in out
        assert "keep:unique" not in out


def _write_ramp(tmp_path):
    """One PID whose B10 ramps over four timed captures (≥4 distinct → byte series)."""
    caps = []
    for i, t in enumerate(["09:00:00", "09:00:02", "09:00:04", "09:00:06"]):
        v = 10 + i * 20  # 10,30,50,70
        caps.append(
            {"ecu": "IGPM", "pid": "22BC03", "payload": f"62BC03FDEE3C73{v:02X}0000", "time": t}
        )
    doc = {"sessions": [{"date": "2026-07-24", "vehicle_states": ["ready"], "captures": caps}]}
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


class TestAgainstFile:
    def test_external_reference_ranks_byte(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        csv = tmp_path / "meter.csv"
        csv.write_text(
            "timestamp,value\n"
            "2026-07-24 09:00:00,1.0\n"
            "2026-07-24 09:00:02,3.0\n"
            "2026-07-24 09:00:04,5.0\n"
            "2026-07-24 09:00:06,7.0\n"
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs",
            lambda *a, **k: [("IGPM", "22BC03")],
        )
        rc = _run(
            tmp_path,
            monkeypatch,
            [
                "IGPM",
                "--against-file",
                str(csv),
                "--bytes",
                "--min-n",
                "3",
                "--min-r",
                "0.5",
                "--json",
            ],
        )
        assert rc == 0
        import json

        data = json.loads(capsys.readouterr().out)
        assert data["reference"] == "meter.csv"
        assert any("B10" in h["signal"] for h in data["hits"])

    def test_against_and_against_file_mutually_exclusive(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        csv = tmp_path / "meter.csv"
        csv.write_text("2026-07-24 09:00:00,1.0\n")
        rc = _run(
            tmp_path,
            monkeypatch,
            ["--against", "IGPM:22BC03:B10", "--against-file", str(csv)],
        )
        assert rc == 2
        assert "mutually exclusive" in capsys.readouterr().err


class TestControl:
    def _write_confounded(self, tmp_path):
        # B4 == confounder Z; B9 == independent component W. Reference X = Z + W.
        z = [0, 1, 2, 3, 4, 5, 6, 7]
        w = [0, 3, 1, 4, 2, 5, 3, 6]
        caps = [
            {
                "ecu": "OBC",
                "pid": "2101",
                "payload": f"6101{z[i]:02X}000000{w[i]:02X}00",
                "time": f"09:00:0{i}",
            }
            for i in range(8)
        ]
        (tmp_path / "2026-07-24.json").write_text(
            json.dumps({"sessions": [{"date": "2026-07-24", "captures": caps}]})
        )
        x = tmp_path / "ref.csv"
        x.write_text("".join(f"2026-07-24 09:00:0{i},{z[i] + w[i]}\n" for i in range(8)))
        zc = tmp_path / "ctrl.csv"
        zc.write_text("".join(f"2026-07-24 09:00:0{i},{z[i]}\n" for i in range(8)))
        return x, zc

    def _run_obc(self, tmp_path, monkeypatch, argv):
        import canlib.align as align

        orig = align.load_signal_captures
        monkeypatch.setattr(
            "canlib.commands.correlate.load_signal_captures",
            lambda s, **kw: orig(
                s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
            ),
        )
        monkeypatch.setattr(
            "canlib.commands.correlate._discover_specs", lambda *a, **k: [("OBC", "2101")]
        )
        p = correlate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["uds", *argv])
        return args.func(args)

    def test_control_removes_the_confounder(self, tmp_path, monkeypatch, capsys):
        import json

        x, zc = self._write_confounded(tmp_path)

        def signals(argv):
            rc = self._run_obc(tmp_path, monkeypatch, argv)
            assert rc == 0
            return {h["signal"] for h in json.loads(capsys.readouterr().out)["hits"]}

        # Without control the confounder byte B4 shows up against the reference.
        plain = signals(
            ["--against-file", str(x), "--bytes", "--min-n", "4", "--min-r", "0.5", "--json"]
        )
        assert "OBC:2101:B4" in plain

        # Controlling for Z drops B4 (it *is* Z) while keeping the independent B9.
        controlled = signals(
            [
                "--against-file",
                str(x),
                "--control-file",
                str(zc),
                "--bytes",
                "--min-n",
                "4",
                "--min-r",
                "0.3",
                "--json",
            ]
        )
        assert "OBC:2101:B4" not in controlled
        assert "OBC:2101:B9" in controlled

    def test_control_rejects_categorical_method(self, tmp_path, monkeypatch, capsys):
        x, zc = self._write_confounded(tmp_path)
        rc = self._run_obc(
            tmp_path,
            monkeypatch,
            [
                "--against-file",
                str(x),
                "--control-file",
                str(zc),
                "--bytes",
                "--min-n",
                "4",
                "--method",
                "cramers_v",
            ],
        )
        assert rc == 2
        assert "undefined for a categorical" in capsys.readouterr().err
