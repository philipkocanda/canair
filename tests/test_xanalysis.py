"""Tests for the cross-signal analysis engine (canlib.xanalysis) and the
correlate/hunt commands."""

from datetime import datetime, timedelta

import pytest
import yaml

from canlib import xanalysis
from canlib.align import TimePoint


def _tp(sec, val):
    return TimePoint(datetime(2026, 7, 22, 9, 0, 0) + timedelta(seconds=sec), val)


# ---------------------------------------------------------------------------
# stats primitives
# ---------------------------------------------------------------------------
class TestStats:
    def test_pearson_perfect(self):
        assert xanalysis.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
        assert xanalysis.pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)

    def test_pearson_degenerate(self):
        assert xanalysis.pearson([1], [1]) is None
        assert xanalysis.pearson([1, 1, 1], [1, 2, 3]) is None

    def test_linear_fit(self):
        m, c, resid = xanalysis.linear_fit([0, 1, 2, 3], [1, 3, 5, 7])  # y=2x+1
        assert m == pytest.approx(2.0)
        assert c == pytest.approx(1.0)
        assert resid == pytest.approx(0.0)


class TestSniffUnit:
    def test_mph_scaling(self):
        # candidate = speed / 1.609 (MPH), so slope ≈ 0.6214
        xs = [0, 16, 32, 48]  # km/h reference
        ys = [x * 0.6214 for x in xs]  # candidate byte in mph
        guess = xanalysis.sniff_unit(xs, ys)
        assert guess is not None
        assert "mph" in guess.lower()

    def test_direct_temp_offset(self):
        # candidate = ref (raw ≈ value), slope 1.0
        xs = [10, 20, 30]
        ys = [10.0, 20.0, 30.0]
        guess = xanalysis.sniff_unit(xs, ys)
        assert guess is not None and "×1" in guess

    def test_no_fit_returns_none(self):
        assert xanalysis.sniff_unit([1], [1]) is None


# ---------------------------------------------------------------------------
# correlate_matrix
# ---------------------------------------------------------------------------
class TestCorrelateMatrix:
    def test_cross_pid_pair_surfaces(self):
        # A on ECU1 and B on ECU2 are the same ramp; C is noise
        ramp = [_tp(i, i) for i in range(20)]
        series = {
            "E1:P:A": ramp,
            "E2:P:B": [_tp(i + 0.2, i) for i in range(20)],
            "E2:P:C": [_tp(i + 0.2, (i * 7) % 5) for i in range(20)],
        }
        hits = xanalysis.correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10)
        assert hits
        top = hits[0]
        assert {top.a, top.b} == {"E1:P:A", "E2:P:B"}
        assert top.r == pytest.approx(1.0)

    def test_intra_pid_excluded_by_default(self):
        ramp = [_tp(i, i) for i in range(20)]
        series = {"E1:P:A": ramp, "E1:P:B": [_tp(i, i) for i in range(20)]}
        assert xanalysis.correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10) == []
        # ...but included on request
        hits = xanalysis.correlate_matrix(
            series, tol_s=1.0, min_r=0.9, min_n=10, include_intra=True
        )
        assert len(hits) == 1

    def test_min_n_threshold(self):
        series = {"E1:P:A": [_tp(i, i) for i in range(5)], "E2:P:B": [_tp(i, i) for i in range(5)]}
        assert xanalysis.correlate_matrix(series, tol_s=1.0, min_r=0.5, min_n=15) == []


# ---------------------------------------------------------------------------
# hunt_byte (fixture PID where B4 == reference)
# ---------------------------------------------------------------------------
def _write_hunt_fixture(tmp_path):
    caps = []
    ref_caps = []
    for i in range(20):
        t = f"09:00:{i:02d}"
        # target PID: payload 61 81 00 <val> => WiCAN B4 = val
        caps.append({"ecu": "AAF", "pid": "2181", "payload": f"618100{i:02X}", "time": t})
        # reference PID: payload 62 C1 01 00 <val> => WiCAN B5 = val (same ramp)
        ref_caps.append({"ecu": "ESC", "pid": "22C101", "payload": f"62C10100{i:02X}", "time": t})
    doc = {
        "sessions": [
            {
                "date": "2026-07-22",
                "label": "drive",
                "vehicle_states": ["driving"],
                "captures": caps + ref_caps,
            }
        ]
    }
    (tmp_path / "2026-07-22.yaml").write_text(yaml.safe_dump(doc))


class TestHuntByte:
    def test_finds_matching_byte(self, tmp_path):
        from canlib.align import extract_series, load_signal_captures

        _write_hunt_fixture(tmp_path)
        loaded = load_signal_captures(
            [("AAF", "2181"), ("ESC", "22C101")], captures_dir=tmp_path
        )
        ref = extract_series(loaded[("ESC", "22C101")], "B5")
        hits = xanalysis.hunt_byte(loaded[("AAF", "2181")], ref, tol_s=1.0, min_n=10)
        assert hits
        top = hits[0]
        assert top.r == pytest.approx(1.0, abs=1e-6)
        assert top.expr == "B4"  # narrowest exact match preferred
        assert top.slope == pytest.approx(1.0)
        assert top.width == 1


# ---------------------------------------------------------------------------
# command smoke tests (parser wiring)
# ---------------------------------------------------------------------------
class TestCommandParsers:
    def test_correlate_registered(self):
        from canlib.commands import correlate

        assert correlate.NAME == "correlate"
        assert hasattr(correlate, "run") and hasattr(correlate, "add_parser")

    def test_hunt_registered(self):
        from canlib.commands import hunt

        assert hunt.NAME == "hunt"
        assert hasattr(hunt, "run") and hasattr(hunt, "add_parser")


class TestHuntPromote:
    """Tranche 2.5 — promoting a hunt hit to a candidate param."""

    def _hit(self, expr="B12", no_expr=False):
        from canlib.xanalysis import HuntHit

        return HuntHit(
            expr="<no-expr>" if no_expr else expr,
            interp="u8",
            offset=12,
            r=0.997,
            n=66,
            slope=0.6243,
            intercept=0.0,
            resid=0.14,
            unit_guess="slope≈0.6243 ⇒ raw×1.609 (mph→km/h)",
            width=1,
        )

    def test_promote_calls_upsert_with_evidence(self, monkeypatch):
        from canlib.commands import hunt

        captured = {}

        def fake_upsert(ecu, pid, name, expr, **kw):
            captured.update(dict(ecu=ecu, pid=pid, name=name, expr=expr, **kw))
            from pathlib import Path

            return Path("aaf.yaml")

        monkeypatch.setattr(hunt, "_promote", hunt._promote)  # keep real
        monkeypatch.setattr("canlib.pids_edit.upsert_parameter", fake_upsert)
        rc = hunt._promote("AAF_SPEED", "AAF", "2181", [self._hit()], "ESC:22C101:REAL_SPEED_KMH")
        assert rc == 0
        assert captured["name"] == "AAF_SPEED"
        assert captured["expr"] == "B12"
        assert captured["enabled"] is True
        assert captured["verified"] is False
        assert "r=+0.997" in captured["notes"]
        assert "mph" in captured["notes"].lower()

    def test_promote_refuses_no_expr(self, capsys):
        from canlib.commands import hunt

        rc = hunt._promote("X", "AAF", "2181", [self._hit(no_expr=True)], "REF")
        assert rc == 1
        assert "no WiCAN expression" in capsys.readouterr().err

    def test_promote_empty_hits(self, capsys):
        from canlib.commands import hunt

        assert hunt._promote("X", "AAF", "2181", [], "REF") == 1

    def test_promote_end_to_end_writes_enabled_unverified(self, tmp_path, monkeypatch):
        """Real guarded write into a temp ecus/ dir: schema-validated + committed."""
        import textwrap

        import canlib.pids_edit as pe
        from canlib.commands import hunt, pids

        (tmp_path / "test.yaml").write_text(
            textwrap.dedent(
                """\
                AAF:
                  tx_id: 0x7EA
                  pids:
                    2181:
                      status: active
                      parameters: {}
                """
            )
        )
        f = tmp_path / "test.yaml"
        # Point the guard + editor at our temp file/dir.
        monkeypatch.setattr(pids, "find_ecu_file", lambda ecu, pids_dir=None: f)
        monkeypatch.setattr(pe, "_resolve_pids_dir", lambda d: tmp_path)

        rc = hunt._promote("AAF_SPEED", "AAF", "2181", [self._hit(expr="B12")], "ESC:22C101:X")
        assert rc == 0
        doc = yaml.safe_load(f.read_text())
        # PID key round-trips as an int (2181) in YAML.
        pids_map = doc["AAF"]["pids"]
        pid_block = pids_map.get(2181) or pids_map.get("2181")
        p = pid_block["parameters"]["AAF_SPEED"]
        assert p["expression"] == "B12"
        assert p["enabled"] is True
        assert p["verified"] is False
        assert "r=+0.997" in p["notes"]
