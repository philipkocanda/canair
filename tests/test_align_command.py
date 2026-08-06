"""Tests for `canair align` — the time-aligned wide multi-signal table."""

import argparse
import json

from canlib.commands import align


def _write(tmp_path):
    """Two co-polled PIDs, each with a byte that ramps over four timed captures."""
    caps = []
    for i, t in enumerate(["09:00:00", "09:00:02", "09:00:04", "09:00:06"]):
        v = 10 + i * 20  # 10, 30, 50, 70
        caps.append(
            {"ecu": "IGPM", "pid": "22BC03", "payload": f"62BC03FDEE3C73{v:02X}0000", "time": t}
        )
        w = i  # 0,1,2,3
        caps.append(
            {"ecu": "IGPM", "pid": "22BC05", "payload": f"62BC057F1120{w:02X}000000", "time": t}
        )
    doc = {
        "sessions": [
            {
                "date": "2026-07-24",
                "vehicle_states": ["ready"],
                "keep_mode": "unique",
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


def _run(tmp_path, monkeypatch, argv):
    import canlib.align as align_lib

    orig = align_lib.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.align.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    p = align.add_parser(argparse.ArgumentParser().add_subparsers())
    args = p.parse_args(argv)
    return args.func(args)


class TestAlign:
    def test_csv_wide_table(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B10", "IGPM:22BC05:B10", "--csv"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "time,IGPM:22BC03:B10,IGPM:22BC05:B10"
        # 4 reference rows, both columns populated (co-polled, same timestamps).
        assert len(lines) == 5
        first = lines[1].split(",")
        # absolute space-separated timestamp (joinable, no ISO 'T')
        assert first[0].startswith("2026-07-24 09:00:00") and "T" not in first[0]
        assert first[1] == "10.0"  # reference ramp (raw float, for scripting fidelity)
        assert first[2] == "0.0"  # second signal

    def test_json_shape(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B10", "IGPM:22BC05:B10", "--json"])
        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 4
        r0 = rows[0]
        assert r0["date"] == "2026-07-24"
        assert "T" not in r0["time"]  # time-only, matches decode --json
        assert r0["values"]["IGPM:22BC03:B10"] == 10.0
        assert r0["values"]["IGPM:22BC05:B10"] == 0.0

    def test_table_shows_legend_without_keep_unique_banner(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B10", "IGPM:22BC05:B10"])
        assert rc == 0
        out = capsys.readouterr().out
        # The fixture session is keep_mode: unique — no blanket scope banner.
        assert "keep:unique" not in out
        assert "c1 = IGPM:22BC03:B10" in out
        assert "c2 = IGPM:22BC05:B10" in out

    def test_needs_two_signals(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B10"])
        assert rc == 2
        assert "at least two" in capsys.readouterr().err

    def test_quoted_whitespace_string_is_split(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B10 IGPM:22BC05:B10", "--csv"])
        assert rc == 0
        assert capsys.readouterr().out.splitlines()[0] == "time,IGPM:22BC03:B10,IGPM:22BC05:B10"

    def test_unknown_signal_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["BOGUS:9999:B3", "IGPM:22BC03:B10"])
        assert rc == 1
        assert "no timed captures" in capsys.readouterr().err

    def test_warns_on_zero_joined_column(self, tmp_path, monkeypatch, capsys):
        # Two PIDs recorded at disjoint times (>tol apart) → the second joins
        # zero reference rows; align must warn (not silently emit empty cells).
        caps = []
        for t in ["09:00:00", "09:00:02", "09:00:04"]:  # reference cadence
            caps.append({"ecu": "IGPM", "pid": "22BC03", "payload": "62BC03FDEE3C7300", "time": t})
        for t in ["09:05:00", "09:05:02"]:  # far away — beyond any sane tol
            caps.append({"ecu": "IGPM", "pid": "22BC05", "payload": "62BC057F112000", "time": t})
        doc = {"sessions": [{"date": "2026-07-24", "keep_mode": "all", "captures": caps}]}
        (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))
        rc = _run(tmp_path, monkeypatch, ["IGPM:22BC03:B3", "IGPM:22BC05:B3", "--csv"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "joined 0 of 3 reference rows" in err
        assert "--join-tol" in err


def _write_run_length(tmp_path):
    """A dense reference plus a run-length signal read once and then held.

    The shape the whole forward-fill change exists for (measured on the bundled
    profile as IGPM's charge-port lock vs BMS SOC): the sparse signal's value is
    known for the entire window, but it has a stored row at only one instant.
    """
    caps = []
    for i in range(20):
        t = f"09:00:{i * 3:02d}"
        caps.append(
            {"ecu": "IGPM", "pid": "22BC03", "payload": f"62BC03FDEE3C73{i:02X}0000", "time": t}
        )
    caps.insert(
        1, {"ecu": "IGPM", "pid": "22BC05", "payload": "62BC057F112001000000", "time": "09:00:00"}
    )
    doc = {
        "sessions": [
            {
                "date": "2026-07-24",
                "vehicle_states": ["charging"],
                "keep_mode": "changes",
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.json").write_text(json.dumps(doc))


class TestAlignForwardFill:
    def _rows(self, tmp_path, monkeypatch, capsys, extra=()):
        _write_run_length(tmp_path)
        rc = _run(
            tmp_path,
            monkeypatch,
            ["IGPM:22BC03:B7", "IGPM:22BC05:B7", "--json", *extra],
        )
        assert rc == 0
        return json.loads(capsys.readouterr().out)

    def test_fills_every_reference_row_by_default(self, tmp_path, monkeypatch, capsys):
        rows = self._rows(tmp_path, monkeypatch, capsys)
        assert len(rows) == 20
        assert all(r["values"]["IGPM:22BC05:B7"] is not None for r in rows)

    def test_fill_none_drops_the_rows_again(self, tmp_path, monkeypatch, capsys):
        rows = self._rows(tmp_path, monkeypatch, capsys, extra=["--fill", "none"])
        joined = [r for r in rows if r["values"]["IGPM:22BC05:B7"] is not None]
        assert len(rows) == 20 and len(joined) < 3

    def test_filled_rows_are_marked_and_measured_rows_are_not(self, tmp_path, monkeypatch, capsys):
        rows = self._rows(tmp_path, monkeypatch, capsys)
        assert "filled" not in rows[0]  # the instant it was actually read
        assert rows[-1]["filled"] == ["IGPM:22BC05:B7"]

    def test_max_hold_bounds_the_carry(self, tmp_path, monkeypatch, capsys):
        rows = self._rows(tmp_path, monkeypatch, capsys, extra=["--max-hold", "10"])
        filled = [r for r in rows if "filled" in r]
        assert 0 < len(filled) < 15

    def test_table_reports_the_carry_per_column(self, tmp_path, monkeypatch, capsys):
        _write_run_length(tmp_path)
        assert _run(tmp_path, monkeypatch, ["IGPM:22BC03:B7", "IGPM:22BC05:B7"]) == 0
        out = capsys.readouterr().out
        assert "2 joined + 18 held" in out and "up to 57s" in out

    def test_csv_stays_a_plain_table_but_says_so_on_stderr(self, tmp_path, monkeypatch, capsys):
        _write_run_length(tmp_path)
        assert _run(tmp_path, monkeypatch, ["IGPM:22BC03:B7", "IGPM:22BC05:B7", "--csv"]) == 0
        cap = capsys.readouterr()
        assert cap.out.splitlines()[0] == "time,IGPM:22BC03:B7,IGPM:22BC05:B7"
        assert "forward-filled" in cap.err

    def test_forced_hold_over_unique_data_warns(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)  # keep_mode: unique
        assert (
            _run(
                tmp_path,
                monkeypatch,
                ["IGPM:22BC03:B10", "IGPM:22BC05:B10", "--fill", "hold", "--json"],
            )
            == 0
        )
        assert "--fill hold" in capsys.readouterr().err
