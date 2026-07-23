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

    def test_hk_minus40_temp_offset(self):
        # Hyundai/Kia raw-40 temperature: raw byte = temp_degC + 40.
        # Regression: the offset candidate used to be dead code (slope-only
        # match), so a -40 temp was mislabeled as a plain x1 scaling.
        xs = [float(t) for t in range(-10, 40)]  # reference temperature degC
        ys = [x + 40 for x in xs]  # raw byte
        guess = xanalysis.sniff_unit(xs, ys)
        assert guess is not None
        assert "−40" in guess or "-40" in guess
        assert "×1)" not in guess  # must NOT collapse to the plain x1 label

    def test_hk_half_minus40_temp(self):
        # physical = raw/2 - 40  =>  raw = (temp + 40) * 2
        xs = [float(t) for t in range(-10, 40)]
        ys = [(x + 40) * 2 for x in xs]
        guess = xanalysis.sniff_unit(xs, ys)
        assert guess is not None
        assert "raw/2−40" in guess or "raw/2-40" in guess

    def test_no_fit_returns_none(self):
        assert xanalysis.sniff_unit([1], [1]) is None


class TestTransformRef:
    """T1.2 — transform the reference series (level vs rate) for hunt/correlate."""

    def test_raw_and_none_passthrough(self):
        ref = [_tp(0, 1.0), _tp(1, 2.0)]
        assert xanalysis.transform_ref(ref, "raw") is ref
        assert xanalysis.transform_ref(ref, None) is ref
        assert xanalysis.transform_ref([], "delta") == []

    def test_delta_sorts_by_time_then_differences(self):
        # deliberately out of time order; delta must sort first
        ref = [_tp(2, 4.0), _tp(0, 0.0), _tp(1, 1.0)]
        out = xanalysis.transform_ref(ref, "delta")
        assert [tp.value for tp in out] == [0.0, 1.0, 3.0]  # delta of [0,1,4]
        assert [tp.dt for tp in out] == sorted(tp.dt for tp in ref)


class TestLagScan:
    """T2.2 — lead/lag cross-correlation."""

    def test_finds_positive_lag(self):
        # cand is ref shifted +2 samples (1 s spacing): best lag ≈ +2 samples
        ref = [_tp(i, float(i % 7)) for i in range(30)]
        cand = [_tp(i + 2, float(i % 7)) for i in range(30)]
        hit = xanalysis.lag_scan(ref, cand, tol_s=0.6, max_lag=3)
        assert hit is not None
        assert hit.lag_samples == -2  # shift cand back 2 to align with ref
        assert hit.r == pytest.approx(1.0, abs=1e-6)

    def test_zero_lag_when_aligned(self):
        ref = [_tp(i, float(i % 5)) for i in range(20)]
        cand = [_tp(i, float(i % 5)) for i in range(20)]
        hit = xanalysis.lag_scan(ref, cand, tol_s=0.4, max_lag=3)
        assert hit is not None and hit.lag_samples == 0

    def test_empty_returns_none(self):
        assert xanalysis.lag_scan([], [_tp(0, 1.0)], tol_s=1.0) is None


class TestCorrelateGate:
    """T2.3 — signal-predicate gating on correlate --against."""

    def test_parse_gate_reference_form(self):
        from canlib.commands import correlate

        signal, op_fn, value, _ = correlate._parse_gate("> 0")
        assert signal is None and value == 0.0
        assert op_fn(1, 0) and not op_fn(-1, 0)

    def test_parse_gate_named_signal(self):
        from canlib.commands import correlate

        signal, op_fn, value, _ = correlate._parse_gate("MCU:2102:MCU_MOTOR_RPM >= 100")
        assert signal == "MCU:2102:MCU_MOTOR_RPM" and value == 100.0
        assert op_fn(100, 100)

    def test_parse_gate_invalid(self):
        import pytest

        from canlib.commands import correlate

        with pytest.raises(ValueError):
            correlate._parse_gate("not a gate")

    def test_apply_gate_reference_value(self):
        from canlib.commands import correlate

        ref = [_tp(0, -5.0), _tp(1, 0.0), _tp(2, 10.0), _tp(3, 20.0)]
        kept = correlate._apply_gate(
            ref, "> 0", 1.0, since=None, until=None, state=None, label=None
        )
        assert [tp.value for tp in kept] == [10.0, 20.0]


# ---------------------------------------------------------------------------
# build_byte_series
# ---------------------------------------------------------------------------
class TestBuildByteSeries:
    def _loaded(self, payloads):
        from canlib.align import LoadedPid

        lp = LoadedPid("BMS", "2101")
        lp.captures = [
            {"date": "2026-07-22", "time": f"09:00:{i:02d}", "payload": p}
            for i, p in enumerate(payloads)
        ]
        return lp

    def test_covers_wican_tail_beyond_raw_length(self):
        # Regression: build_byte_series used the RAW payload length for max_len,
        # but Bn indexes the longer WiCAN frame (PCI bytes inserted). The tail
        # bytes of a multi-frame response were never generated.
        # 20-byte raw payload (multi-frame); only the LAST raw byte varies.
        payloads = ["6181" + "00" * 17 + f"{i * 10:02X}" for i in range(8)]
        raw_len = len(payloads[0]) // 2
        assert raw_len == 20
        series = xanalysis.build_byte_series(self._loaded(payloads), min_distinct=2)
        offsets = sorted(int(k.rsplit(":B", 1)[1]) for k in series)
        assert offsets, "the varying tail byte must produce a series"
        # The only varying byte lands in the WiCAN tail beyond the raw length.
        assert max(offsets) >= raw_len

    def test_skips_pci_offsets(self):
        from canlib.byteindex import payload_to_wican_bytes, wican_to_isotp

        # Vary EVERY byte so nothing is filtered by min_distinct; then assert no
        # PCI offset (wican_to_isotp is None) appears in the output.
        payloads = [
            "61" + "".join(f"{(i + k) & 0xFF:02X}" for k in range(19)) for i in range(8)
        ]
        series = xanalysis.build_byte_series(self._loaded(payloads), min_distinct=2)
        offsets = {int(k.rsplit(":B", 1)[1]) for k in series}
        wlen = len(payload_to_wican_bytes(payloads[0]))
        pci = {i for i in range(wlen) if wican_to_isotp(i) is None}
        assert pci  # multi-frame frame has PCI bytes
        assert not (offsets & pci), f"PCI offsets leaked into series: {offsets & pci}"

    def test_build_bit_series_only_toggling_bits(self):
        # single-frame payload 62 C1 01 00 -> WiCAN 04 62 C1 01 00: B0=PCI, B1=SID,
        # B4=last data byte. d0 toggles bit0 only (0x00 <-> 0x01); all else const.
        lp = self._loaded(["62C10100", "62C10101", "62C10100", "62C10101"])
        bits = xanalysis.build_bit_series(lp)
        keys = set(bits)
        assert "BMS:2101:B4:0" in keys
        assert all(k.endswith(":0") for k in keys)  # only bit 0 varies


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

    def test_hunt_has_transform_flag(self):
        import argparse

        from canlib.commands import hunt

        p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["MCU", "2102", "--against", "X:Y:Z", "--transform", "delta"])
        assert args.transform == "delta"
        assert p.parse_args(["MCU", "2102", "--against", "X:Y:Z"]).transform == "raw"

    def test_correlate_has_transform_flag(self):
        import argparse

        from canlib.commands import correlate

        p = correlate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["--against", "X:Y:Z", "--transform", "abs"])
        assert args.transform == "abs"


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


class TestCorrelatePromote:
    """T1.3 — promote the top raw-byte hit from correlate --against."""

    def _rows_and_series(self):
        # ranked rows: a defined param first, then a raw byte hit
        ramp = [_tp(i, i) for i in range(20)]
        series = {"MCU:2102:MCU_MOTOR_RPM": ramp, "AAF:2181:B12": [_tp(i, i) for i in range(20)]}
        rows = [("MCU:2102:MCU_MOTOR_RPM", 0.99, 20), ("AAF:2181:B12", 0.95, 20)]
        return rows, series, ramp

    def test_promote_picks_first_raw_byte(self, tmp_path, monkeypatch):
        import textwrap

        import canlib.pids_edit as pe
        from canlib.commands import correlate, pids

        (tmp_path / "aaf.yaml").write_text(
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
        f = tmp_path / "aaf.yaml"
        monkeypatch.setattr(pids, "find_ecu_file", lambda ecu, pids_dir=None: f)
        monkeypatch.setattr(pe, "_resolve_pids_dir", lambda d: tmp_path)

        rows, series, ref = self._rows_and_series()
        rc = correlate._promote_top_byte("AAF_CAND", rows, series, ref, "MCU:2102:MCU_MOTOR_RPM", 1.0)
        assert rc == 0
        doc = yaml.safe_load(f.read_text())
        pid_block = doc["AAF"]["pids"].get(2181) or doc["AAF"]["pids"].get("2181")
        p = pid_block["parameters"]["AAF_CAND"]
        assert p["expression"] == "B12"  # the raw byte, not the param
        assert p["enabled"] is True and p["verified"] is False
        assert "r=+0.950" in p["notes"]

    def test_promote_refuses_when_no_byte_hit(self, capsys):
        from canlib.commands import correlate

        # only a defined-param hit — nothing raw to promote
        rows = [("MCU:2102:MCU_MOTOR_RPM", 0.99, 20)]
        series = {"MCU:2102:MCU_MOTOR_RPM": [_tp(i, i) for i in range(20)]}
        rc = correlate._promote_top_byte("X", rows, series, [], "REF", 1.0)
        assert rc == 1
        assert "no raw-byte hit" in capsys.readouterr().err
