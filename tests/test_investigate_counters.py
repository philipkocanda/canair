"""Tests for `canair investigate --counters` — the capture-model side.

The pure sweep is covered in ``test_counters.py``; this covers the bridge: the
row-aligned payload matrix, ISO-TP -> WiCAN expression rendering (including a
PCI-straddling window), the mapped-parameter overlay, and the CLI/JSON surface.
"""

from __future__ import annotations

import argparse
import json

from canlib.commands import investigate
from canlib.commands.investigate import counters as inv_counters


def _run(tmp_path, monkeypatch, argv, specs=()):
    import canlib.align as align

    orig = align.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.investigate.uds.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    monkeypatch.setattr(
        "canlib.commands.investigate.uds.discover_signal_specs", lambda *a, **k: list(specs)
    )
    p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
    return investigate.run(p.parse_args(["uds", *argv]))


def _odometer_payload(km: int, *, pad: bool = False, noise: int = 0xAD) -> str:
    """A CLU-22B002-shaped payload with a 3-byte BE odometer at ISO-TP i9-i11.

    ``62 B0 02 E0 00 00 00 00 <noise> <km hi> <km mid> <km lo> 00 00 00``
    """
    body = f"62B002E000000000{noise:02X}{km:06X}000000"
    return body + ("AA" if pad else "")


# A jittering neighbour byte at ISO-TP i8, as the real CLU 22B002 has. It matters:
# a CONSTANT non-zero neighbour would be absorbed into a wider window that is
# genuinely canonical and wins on width, so a synthetic payload without this jitter
# tests the wrong window.
_NOISE = [0xAA, 0xAD, 0xAB, 0xAD]


def _write_odometer(tmp_path, *, days, pad_from=None, keep_mode=None):
    """One session per day, several reads each, odometer rising between days."""
    sessions = []
    for idx, (date, km) in enumerate(days):
        caps = []
        for i in range(4):
            pad = pad_from is not None and idx >= pad_from
            caps.append(
                {
                    "ecu": "CLU",
                    "pid": "22B002",
                    "date": date,
                    "time": f"09:0{i}:00",
                    "payload": _odometer_payload(km, pad=pad, noise=_NOISE[i]),
                }
            )
        session = {"date": date, "vehicle_states": ["READY"], "captures": caps}
        if keep_mode is not None:
            session["keep_mode"] = keep_mode
        sessions.append(session)
        (tmp_path / f"{date}.json").write_text(json.dumps({"sessions": [sessions[-1]]}))
    return sessions


_DAYS = [
    ("2026-04-16", 70047),
    ("2026-05-20", 71200),
    ("2026-06-14", 72010),
    ("2026-07-21", 72982),
    ("2026-08-05", 73048),
]


class TestCountersCli:
    def test_finds_three_byte_odometer_with_wican_expression(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[B12:B14]" in out, "the 3-byte ISO-TP window must render as a WiCAN range"
        assert "70047" in out and "73048" in out
        assert "CYCLE COUNTERS" in out  # flat within each session, steps between

    def test_json_shape(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(
            tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4", "--json"]
        )
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["target"] == "CLU:22B002"
        assert doc["n_days"] == 5
        top = doc["counters"][0]
        assert top["expression"] == "[B12:B14]"
        assert top["isotp_offsets"] == [9, 10, 11]
        assert top["kind"] == "cycle"
        assert top["n_down"] == 0
        assert top["bits"] == 4.0
        assert top["first"] == 70047 and top["last"] == 73048
        assert top["per_year"] is not None

    def test_padded_and_unpadded_payloads_both_counted(self, tmp_path, monkeypatch, capsys):
        """A trailing ISO-TP pad byte must not cost the older captures.

        Regression: aligning on the *modal* payload length instead of a common
        prefix discarded every capture of the minority length — on real data that
        silently threw away three months of the horizon the search depends on.
        """
        _write_odometer(tmp_path, days=_DAYS, pad_from=3)  # last 2 days padded
        rc = _run(
            tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4", "--json"]
        )
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["n_captures"] == 20, "both payload lengths must be kept"
        assert doc["n_days"] == 5
        top = doc["counters"][0]
        assert top["first"] == 70047 and top["last"] == 73048

    def test_unmapped_only_hides_a_mapped_window(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        monkeypatch.setattr(
            "canlib.pids.load_pids",
            lambda *a, **k: {
                "ecus": {
                    "CLU": {
                        "tx_id": 0x7C6,
                        "pids": {
                            "22B002": {
                                "parameters": {
                                    "ODOMETER": {
                                        "expression": "[B12:B14]",
                                        "verified": True,
                                    }
                                }
                            }
                        },
                    }
                }
            },
        )
        rc = _run(
            tmp_path,
            monkeypatch,
            ["CLU", "22B002", "--counters", "--min-bits", "4", "--json"],
        )
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["counters"][0]["mapped_by"] == "ODOMETER"

        rc = _run(
            tmp_path,
            monkeypatch,
            ["CLU", "22B002", "--counters", "--min-bits", "4", "--unmapped-only", "--json"],
        )
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["counters"] == []

    def test_unmapped_only_keeps_a_window_mapped_only_by_an_unverified_param(
        self, tmp_path, monkeypatch, capsys
    ):
        """An unverified mapping is open work, and monotonicity is what refutes it.

        Real case: VCU 21F2 B75 was defined as CHARGE_TIMER_WKND_END_HOUR from a
        single before/after coincidence, but the byte only ever rises — a counter,
        not a user-set hour. Suppressing it under --unmapped-only hid the finding.
        """
        _write_odometer(tmp_path, days=_DAYS)
        monkeypatch.setattr(
            "canlib.pids.load_pids",
            lambda *a, **k: {
                "ecus": {
                    "CLU": {
                        "tx_id": 0x7C6,
                        "pids": {
                            "22B002": {
                                "parameters": {
                                    "GUESSED_HOUR": {
                                        "expression": "[B12:B14]",
                                        "verified": False,
                                    }
                                }
                            }
                        },
                    }
                }
            },
        )
        rc = _run(
            tmp_path,
            monkeypatch,
            ["CLU", "22B002", "--counters", "--min-bits", "4", "--unmapped-only", "--json"],
        )
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert [c["mapped_by"] for c in doc["counters"]] == ["GUESSED_HOUR"], (
            "an unverified mapping must not hide the window"
        )
        assert doc["counters"][0]["mapped_verified"] is False

    def test_verified_mapping_is_reported_as_settled(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        monkeypatch.setattr(
            "canlib.pids.load_pids",
            lambda *a, **k: {
                "ecus": {
                    "CLU": {
                        "tx_id": 0x7C6,
                        "pids": {
                            "22B002": {
                                "parameters": {
                                    "ODOMETER": {
                                        "expression": "[B12:B14]",
                                        "verified": True,
                                    }
                                }
                            }
                        },
                    }
                }
            },
        )
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--json"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["counters"][0]["mapped_verified"] is True

    def test_empty_report_names_the_useful_threshold(self, tmp_path, monkeypatch, capsys):
        # Only 2 up-steps -> 2 bits, below the default: the dead end must say so.
        _write_odometer(tmp_path, days=_DAYS[:3])
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "8"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing above 8 bits" in out
        assert "--min-bits 2" in out
        assert "[B12:B14]" in out

    def test_notation_torque_relabels_the_label(self, tmp_path, monkeypatch, capsys):
        """--notation re-renders the ISO-TP label; the expression stays WiCAN."""
        from canlib.notation import ByteNotation, ByteRef, subfunction_bytes_for_pid

        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(
            tmp_path,
            monkeypatch,
            ["CLU", "22B002", "--counters", "--min-bits", "4", "--notation", "torque"],
        )
        assert rc == 0
        out = capsys.readouterr().out
        expected = ByteRef.from_isotp(9, width=3).render(
            ByteNotation.TORQUE, sub_bytes=subfunction_bytes_for_pid("22B002")
        )
        assert expected in out, "the label column must be relabelled to torque"
        assert "i9-i11" not in out, "the ISO-TP label must not survive under --notation torque"
        assert "[B12:B14]" in out, "the expression stays WiCAN regardless of --notation"

    def test_notation_default_keeps_isotp_label(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "i9-i11" in out, "the default label is the canonical ISO-TP window"
        assert "[B12:B14]" in out

    def test_scoped_run_warns(self, tmp_path, monkeypatch, capsys):
        """A scope filter understates the bits ranking; warn + flag it in --json."""
        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(
            tmp_path,
            monkeypatch,
            ["CLU", "22B002", "--counters", "--min-bits", "4", "--state", "READY", "--json"],
        )
        assert rc == 0
        cap = capsys.readouterr()
        assert "scope filter" in cap.err and "--state" in cap.err
        doc = json.loads(cap.out)
        assert doc["scoped"] is True

    def test_unscoped_run_not_flagged(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS)
        rc = _run(
            tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4", "--json"]
        )
        assert rc == 0
        cap = capsys.readouterr()
        assert "scope filter" not in cap.err
        assert json.loads(cap.out)["scoped"] is False

    def test_keep_changes_scope_shows_banner(self, tmp_path, monkeypatch, capsys):
        """A keep:changes scope must be flagged in the counters view like the others."""
        _write_odometer(tmp_path, days=_DAYS, keep_mode="changes")
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters", "--min-bits", "4"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "keep:changes" in out

    def test_too_few_captures_errors(self, tmp_path, monkeypatch, capsys):
        _write_odometer(tmp_path, days=_DAYS[:1])
        (tmp_path / "2026-04-16.json").write_text(
            json.dumps(
                {
                    "sessions": [
                        {
                            "date": "2026-04-16",
                            "captures": [
                                {
                                    "ecu": "CLU",
                                    "pid": "22B002",
                                    "date": "2026-04-16",
                                    "time": "09:00:00",
                                    "payload": _odometer_payload(70047),
                                }
                            ],
                        }
                    ]
                }
            )
        )
        rc = _run(tmp_path, monkeypatch, ["CLU", "22B002", "--counters"])
        assert rc == 1
        assert "Not enough timed captures" in capsys.readouterr().err


class TestPayloadMatrix:
    def _lp(self, payloads):
        from canlib.align import LoadedPid

        lp = LoadedPid("CLU", "22B002")
        for i, p in enumerate(payloads):
            lp.captures.append(
                {
                    "ecu": "CLU",
                    "pid": "22B002",
                    "date": "2026-04-16",
                    "time": f"09:00:{i:02d}",
                    "payload": p,
                }
            )
        return lp

    def test_truncates_to_common_prefix(self):
        lp = self._lp([_odometer_payload(1, pad=True), _odometer_payload(2)])
        dts, rows = inv_counters._payload_matrix(lp)
        assert len(dts) == 2
        assert {len(r) for r in rows} == {15}, "padded payload is truncated, not dropped"

    def test_drops_a_payload_too_short_to_align(self):
        # One runt among many must not clamp every other capture's usable prefix.
        payloads = [_odometer_payload(k) for k in range(20)] + ["62B002E0"]
        dts, rows = inv_counters._payload_matrix(self._lp(payloads))
        assert len(dts) == 20
        assert {len(r) for r in rows} == {15}

    def test_sorted_by_time(self):
        lp = self._lp([])
        for t, km in [("09:05:00", 5), ("09:01:00", 1), ("09:03:00", 3)]:
            lp.captures.append(
                {
                    "ecu": "CLU",
                    "pid": "22B002",
                    "date": "2026-04-16",
                    "time": t,
                    "payload": _odometer_payload(km),
                }
            )
        dts, rows = inv_counters._payload_matrix(lp)
        assert dts == sorted(dts)
        assert [int.from_bytes(r[9:12], "big") for r in rows] == [1, 3, 5]

    def test_empty(self):
        assert inv_counters._payload_matrix(self._lp([])) == ([], [])


class TestExpressionRendering:
    def _cand(self, offsets, little=False):
        from canlib.counters import CounterCandidate

        return CounterCandidate(
            keys=tuple(offsets),
            little=little,
            kind="accumulator",
            bits=10.0,
            n=10,
            n_distinct=10,
            n_up=9,
            n_down=0,
            n_varying=len(offsets),
            canonical=True,
            first=0.0,
            last=9.0,
            lo=0.0,
            hi=9.0,
            med_step=1.0,
            max_step=1.0,
            msb_jump=0.0,
            step_ratio=0.1,
            span_s=100.0,
            n_sessions=1,
            flat_sessions=0,
            boundary_steps=0,
        )

    def test_contiguous_range(self):
        assert inv_counters._expression(self._cand([9, 10, 11])) == "[B12:B14]"

    def test_pci_straddling_window_renders_shift_composition(self):
        # ISO-TP i11-i14 crosses the B16 consecutive-frame PCI byte, so it cannot
        # be a [Bn:Bm] range — it must render as an explicit shift composition.
        expr = inv_counters._expression(self._cand([11, 12, 13, 14], little=True))
        assert expr == "B14 | (B15 << 8) | (B17 << 16) | (B18 << 24)"
        assert "[" not in expr

    def test_single_byte(self):
        assert inv_counters._expression(self._cand([10])) == "B13"

    def test_non_integer_keys_unrenderable(self):
        assert inv_counters._expression(self._cand(["0x386:r1"])) is None

    def test_label_shows_isotp_span_and_endianness(self):
        assert inv_counters._label(self._cand([9, 10, 11])) == "i9-i11"
        assert inv_counters._label(self._cand([11, 12], little=True)) == "i11-i12LE"
        assert inv_counters._label(self._cand([10])) == "i10"
