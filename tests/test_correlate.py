"""Tests for `canair correlate` cross-ECU mirror finding (T1.3)."""

import argparse

import yaml

from canlib.commands import correlate


def _write(tmp_path):
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
                "keep_mode": "unique",
                "captures": caps,
            }
        ]
    }
    (tmp_path / "2026-07-24.yaml").write_text(yaml.safe_dump(doc))


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
    return correlate.run(p.parse_args(argv))


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

    def test_keep_unique_banner_text_mode(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path)
        _run(tmp_path, monkeypatch, ["IGPM", "--min-n", "3"])
        assert "keep:unique" in capsys.readouterr().out
