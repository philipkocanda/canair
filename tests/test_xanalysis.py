"""Tests for the cross-signal analysis engine (canlib.xanalysis) and the
correlate/hunt commands."""

import json
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


class TestReferenceIsBimodal:
    def test_two_flat_clusters_true(self):
        # 12V-bus style: ~14.5V charging vs ~12.2V otherwise, tight within each.
        assert xanalysis.reference_is_bimodal([14.5] * 40 + [12.2] * 20)

    def test_minority_cluster_still_true(self):
        # Charge dominates (~92%), ACC2 minority (~8%) — still a two-cluster ref.
        assert xanalysis.reference_is_bimodal([14.5] * 90 + [12.2] * 8)

    def test_continuous_ramp_false(self):
        assert not xanalysis.reference_is_bimodal([12.0 + i * 0.05 for i in range(60)])

    def test_speed_like_false(self):
        # Big flat-at-zero block + a WIDE moving cluster: the gap doesn't dominate
        # the moving cluster's own spread, so it must NOT be flagged (speed works).
        assert not xanalysis.reference_is_bimodal([0.0] * 200 + [5 + i * 0.1 for i in range(500)])

    def test_lone_outlier_false(self):
        assert not xanalysis.reference_is_bimodal([12.0 + i * 0.02 for i in range(200)] + [40.0])

    def test_too_few_samples_false(self):
        assert not xanalysis.reference_is_bimodal([14.5] * 3 + [12.2] * 2)

    def test_constant_false(self):
        assert not xanalysis.reference_is_bimodal([12.4] * 30)


class TestReferenceIsAbsoluteLevel:
    def test_pack_voltage_true(self):
        # ~360V pack with a ±5V swing: tiny relative span on a big baseline.
        import random

        random.seed(0)
        assert xanalysis.reference_is_absolute_level(
            [360 + random.uniform(-5, 5) for _ in range(50)]
        )

    def test_12v_rail_true(self):
        assert xanalysis.reference_is_absolute_level([14.2 + 0.01 * i for i in range(40)])

    def test_speed_sweep_false(self):
        # 0..100 km/h: huge relative span → dynamic, not a level.
        assert not xanalysis.reference_is_absolute_level([float(i) for i in range(0, 100)])

    def test_rpm_sweep_false(self):
        assert not xanalysis.reference_is_absolute_level([float(i * 60) for i in range(100)])

    def test_near_zero_baseline_false(self):
        # A temperature swinging around 0°C: baseline below the floor → not flagged
        # (DC offset doesn't dominate a near-zero mean).
        assert not xanalysis.reference_is_absolute_level([(-2.0 + 0.1 * i) for i in range(40)])

    def test_too_few_samples_false(self):
        assert not xanalysis.reference_is_absolute_level([360.0] * 5)


class TestDiscriminability:
    def test_clean_separation_high(self):
        # Two states, tight within each, far apart between -> large F.
        groups = {"a": [1.0, 1.0, 1.1], "b": [9.0, 9.1, 9.0]}
        f = xanalysis.discriminability(groups)
        assert f is not None and f > 100

    def test_noise_low(self):
        # Overlapping, noisy groups -> small F.
        groups = {"a": [1.0, 5.0, 9.0], "b": [2.0, 6.0, 8.0]}
        f = xanalysis.discriminability(groups)
        assert f is not None and f < 1

    def test_single_group_none(self):
        assert xanalysis.discriminability({"a": [1.0, 2.0, 3.0]}) is None

    def test_zero_within_is_inf(self):
        groups = {"a": [1.0, 1.0], "b": [2.0, 2.0]}
        assert xanalysis.discriminability(groups) == float("inf")


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

    def test_domain_hint_suppressed_on_mismatched_ref_unit(self):
        # An RPM slope ≈64 best-fits the 0.02 "cell V" candidate; with a speed
        # reference the voltage hint must be dropped, leaving the numeric scale.
        xs = [0.0, 10.0, 20.0, 30.0]  # km/h reference
        ys = [x / 0.02 for x in xs]  # candidate byte, slope ≈ 50
        guess = xanalysis.sniff_unit(xs, ys, ref_unit="km/h")
        assert guess is not None
        assert "cell V" not in guess
        assert "raw×0.02" in guess

    def test_domain_hint_kept_on_matching_ref_unit(self):
        xs = [3.6, 3.7, 3.8, 3.9]  # voltage reference
        ys = [x / 0.02 for x in xs]  # candidate byte ≈ centivolts
        guess = xanalysis.sniff_unit(xs, ys, ref_unit="V")
        assert guess is not None and "cell V" in guess

    def test_domain_hint_kept_when_ref_unit_unknown(self):
        # No/unknown reference unit → don't gate (conservative, unchanged).
        xs = [3.6, 3.7, 3.8, 3.9]
        ys = [x / 0.02 for x in xs]
        assert "cell V" in (xanalysis.sniff_unit(xs, ys) or "")

    def test_no_hyundai_label_in_builtins(self):
        from canlib.unit_guess import DEFAULT_UNIT_CANDIDATES

        hints = [c[3] for c in DEFAULT_UNIT_CANDIDATES]
        labels = [c[2] for c in DEFAULT_UNIT_CANDIDATES]
        assert "HK temp" not in hints
        assert all("HK" not in (h or "") for h in hints)
        assert all("HK" not in ln for ln in labels)

    def test_profile_candidate_threaded_through(self):
        # A profile-declared scaling (raw/4 minus 50) is used when passed in.
        xs = [float(t) for t in range(-10, 40)]
        ys = [(x + 50) * 4 for x in xs]
        extra = [(0.25, -50.0, "raw/4−50", None, None)]
        guess = xanalysis.sniff_unit(xs, ys, candidates=extra)
        assert guess is not None
        assert "raw/4−50" in guess

    def test_unit_dimension_mapping(self):
        assert xanalysis.unit_dimension("km/h") == "speed"
        assert xanalysis.unit_dimension("V") == "voltage"
        assert xanalysis.unit_dimension("°C") == "temperature"
        assert xanalysis.unit_dimension("rpm") is None
        assert xanalysis.unit_dimension(None) is None


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
        from canlib.commands._correlate_calc import _parse_gate

        signal, op_fn, value, _ = _parse_gate("> 0")
        assert signal is None and value == 0.0
        assert op_fn(1, 0) and not op_fn(-1, 0)

    def test_parse_gate_named_signal(self):
        from canlib.commands._correlate_calc import _parse_gate

        signal, op_fn, value, _ = _parse_gate("MCU:2102:MCU_MOTOR_RPM >= 100")
        assert signal == "MCU:2102:MCU_MOTOR_RPM" and value == 100.0
        assert op_fn(100, 100)

    def test_parse_gate_bracketed_signal(self):
        # The documented '[SIGNAL] OP VALUE' form must parse to the same bare
        # signal — otherwise the brackets leak into SignalRef.parse, yield a bogus
        # ECU, and the gate silently matches nothing (the field-observed trap).
        from canlib.commands._correlate_calc import _parse_gate

        signal, _op, value, _ = _parse_gate("[MCU:2102:MCU_MOTOR_RPM] > 0")
        assert signal == "MCU:2102:MCU_MOTOR_RPM" and value == 0.0

    def test_parse_gate_bracketed_matches_bare(self):
        from canlib.commands._correlate_calc import _parse_gate

        assert (
            _parse_gate("[HVAC:220102:HVAC_COMPRESSOR_ON] > 0")[0]
            == (_parse_gate("HVAC:220102:HVAC_COMPRESSOR_ON > 0")[0])
        )

    def test_parse_gate_invalid(self):
        import pytest

        from canlib.commands._correlate_calc import _parse_gate

        with pytest.raises(ValueError):
            _parse_gate("not a gate")

    def test_apply_gate_reference_value(self):
        from canlib.commands._correlate_calc import _apply_gate

        ref = [_tp(0, -5.0), _tp(1, 0.0), _tp(2, 10.0), _tp(3, 20.0)]
        kept = _apply_gate(ref, "> 0", 1.0, since=None, until=None, state=None, label=None)
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
        payloads = ["61" + "".join(f"{(i + k) & 0xFF:02X}" for k in range(19)) for i in range(8)]
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

    def test_intra_pid_bit_labels_excluded_by_default(self):
        """Bit labels (`ECU:PID:Bn:k`) must group by ECU+PID like every other label.

        Regression: the grouping key was `rsplit(":", 1)[0]`, which on the 4-field
        bit form yielded `ECU:PID:Bn` — so every `--bits` pair looked cross-PID.
        `correlate --bits` then ranked a param against its own backing bit
        (r=1.000) as the top "cross-signal" hit, flooding out real findings.
        """
        ramp = [_tp(i, i) for i in range(20)]
        series = {
            "IGPM:22BC03:DOOR_DRV_OPEN": ramp,
            "IGPM:22BC03:B10:5": [_tp(i, i) for i in range(20)],
        }
        assert xanalysis.correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10) == []
        hits = xanalysis.correlate_matrix(
            series, tol_s=1.0, min_r=0.9, min_n=10, include_intra=True
        )
        assert len(hits) == 1

    def test_cross_pid_bit_labels_still_surface(self):
        # The fix must not over-group: a bit on a *different* PID is a real hit.
        series = {
            "IGPM:22BC03:B10:5": [_tp(i, i) for i in range(20)],
            "IGPM:22BC06:B10:0": [_tp(i + 0.2, i) for i in range(20)],
        }
        hits = xanalysis.correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10)
        assert len(hits) == 1
        assert hits[0].r == pytest.approx(1.0)


class TestSignalGroupKey:
    """The cross-domain 'same signal source' key (see xanalysis.signal_group_key)."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            # domain A — diagnostics: key on ECU+PID
            ("BMS:2101:SOC", "BMS:2101"),
            ("IGPM:22BC03:B12", "IGPM:22BC03"),
            ("IGPM:22BC03:B12:3", "IGPM:22BC03"),  # 4-field bit form
            # domain B — raw broadcast frames: key on the arbitration ID
            ("0x220:r1", "0x220"),
            ("0x220:r1.3", "0x220"),  # bit uses '.', not ':'
        ],
    )
    def test_group_key(self, label, expected):
        assert xanalysis.signal_group_key(label) == expected

    @pytest.mark.parametrize(
        "a,b,same",
        [
            ("IGPM:22BC03:B12", "IGPM:22BC03:B13", True),
            ("IGPM:22BC03:B12:3", "IGPM:22BC03:B13:4", True),
            ("IGPM:22BC03:SOC", "IGPM:22BC03:B13:4", True),
            ("IGPM:22BC03:B12:3", "IGPM:22BC04:B13:4", False),  # different PID
            ("IGPM:22BC03:B12:3", "BCM:22BC03:B13:4", False),  # different ECU
            ("0x220:r1.3", "0x220:r2.4", True),
            ("0x220:r1", "0x386:r2", False),
        ],
    )
    def test_same_pid(self, a, b, same):
        assert xanalysis._same_pid(a, b) is same


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
    (tmp_path / "2026-07-22.json").write_text(json.dumps(doc))


class TestHuntByte:
    def test_finds_matching_byte(self, tmp_path):
        from canlib.align import extract_series, load_signal_captures

        _write_hunt_fixture(tmp_path)
        loaded = load_signal_captures([("AAF", "2181"), ("ESC", "22C101")], captures_dir=tmp_path)
        ref = extract_series(loaded[("ESC", "22C101")], "B5")
        hits = xanalysis.hunt_byte(loaded[("AAF", "2181")], ref, tol_s=1.0, min_n=10)
        assert hits
        top = hits[0]
        assert top.r == pytest.approx(1.0, abs=1e-6)
        assert top.expr == "B4"  # narrowest exact match preferred
        assert top.slope == pytest.approx(1.0)
        assert top.width == 1

    def test_skips_pci_bytes_including_index_1(self):
        """hunt_byte must never surface a byte window overlapping an ISO-TP PCI
        byte — including WiCAN index 1 (the first frame's *second* PCI byte).

        Regression for the `i % 8 == 0` PCI guard, which flagged 0/8/16/… but
        missed index 1, unlike the canonical `wican_to_isotp` detector used
        everywhere else. Fixture: a multi-frame target whose only varying byte is
        the length-low byte at B1, ramped to correlate perfectly with the
        reference. The buggy guard surfaces a B1 hit; the fix drops it.
        """
        from datetime import date, datetime, time

        from canlib.align import LoadedPid
        from canlib.byteindex import wican_to_isotp

        lp = LoadedPid("AAF", "2181")
        ref: list[TimePoint] = []
        for i in range(15):
            # 9..23-byte payloads -> multi-frame; B1 (length-low) = 9+i ramps, all
            # other bytes constant (SID 0x61 + zero data).
            payload = "61" + "00" * (8 + i)
            lp.captures.append({"date": "2026-07-22", "time": f"09:00:{i:02d}", "payload": payload})
            ref.append(TimePoint(datetime.combine(date(2026, 7, 22), time(9, 0, i)), float(i)))

        hits = xanalysis.hunt_byte(lp, ref, tol_s=1.0, min_n=10)
        pci = {j for j in range(64) if wican_to_isotp(j) is None}
        spanning = [h for h in hits if any((h.offset + k) in pci for k in range(h.width))]
        assert not spanning, (
            f"hunt surfaced PCI-spanning hit(s): {[(h.expr, h.offset, h.width) for h in spanning]}"
        )
        assert 1 not in {h.offset for h in hits}  # B1 is a PCI byte, never a signal

    def test_finds_frame_straddling_word(self):
        """A 16-bit value split across an ISO-TP frame boundary (high byte last in
        one frame, low byte first in the next, PCI byte between) must be surfaced
        by the PCI-skip sweep as an arithmetic ``S15*256 + B17`` expression — the
        BMS pack-current layout the contiguous sweep structurally can't find.
        """
        from datetime import date, datetime, time

        from canlib.align import LoadedPid

        lp = LoadedPid("BMS", "2101")
        ref: list[TimePoint] = []
        for i in range(15):
            val = 1000 + i * 10  # 16-bit, both bytes vary; low byte wraps (non-monotonic)
            pb = bytearray(14)  # 14-byte payload → multi-frame; idx 12→B15, idx 13→B17
            pb[0] = 0x61
            pb[12] = (val >> 8) & 0xFF
            pb[13] = val & 0xFF
            lp.captures.append(
                {"date": "2026-07-22", "time": f"09:00:{i:02d}", "payload": pb.hex()}
            )
            ref.append(TimePoint(datetime.combine(date(2026, 7, 22), time(9, 0, i)), float(val)))

        hits = xanalysis.hunt_byte(lp, ref, tol_s=1.0, min_n=10, all_interps=True)
        assert any("skip-PCI" in h.interp for h in hits), "no PCI-skip candidates generated"
        straddle = [h for h in hits if h.expr in ("B15*256 + B17", "S15*256 + B17")]
        assert straddle, f"frame-straddling word not surfaced: {sorted(h.expr for h in hits)}"
        assert max(abs(h.r) for h in straddle) > 0.999

    def test_per_session_flag_accepted(self):
        from datetime import date, datetime, time

        from canlib.align import LoadedPid

        lp = LoadedPid("AAF", "2181")
        ref: list[TimePoint] = []
        for i in range(12):
            pb = bytearray(6)
            pb[0] = 0x61
            pb[3] = i  # a single varying data byte
            lp.captures.append(
                {"date": "2026-07-22", "time": f"09:00:{i:02d}", "payload": pb.hex()}
            )
            ref.append(TimePoint(datetime.combine(date(2026, 7, 22), time(9, 0, i)), float(i)))
        hits = xanalysis.hunt_byte(lp, ref, tol_s=1.0, min_n=10, per_session=True)
        assert hits  # runs and returns candidates with per-session detrending on


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
        args = p.parse_args(["uds", "MCU", "2102", "--against", "X:Y:Z", "--transform", "delta"])
        assert args.transform == "delta"
        assert p.parse_args(["uds", "MCU", "2102", "--against", "X:Y:Z"]).transform == "raw"

    def test_hunt_notation_flag_and_label_rendering(self):
        import argparse

        from canlib.commands import hunt
        from canlib.notation import ByteNotation
        from canlib.xanalysis import HuntHit

        p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
        # Flag parses; default is None (resolved to wican downstream).
        assert p.parse_args(["uds", "M", "2102", "--against", "X:Y:Z"]).notation is None
        assert p.parse_args(
            ["uds", "M", "2102", "--against", "X:Y:Z", "--notation", "isotp"]
        ).notation == ("isotp")
        # Label rendering: WiCAN keeps the promotable expr; others render position.
        hit = HuntHit(
            expr="B9",
            interp="u8",
            offset=9,
            r=1.0,
            n=15,
            slope=1.0,
            intercept=0.0,
            resid=0.0,
            unit_guess=None,
            width=1,
        )
        assert hunt._hit_label(hit, ByteNotation.WICAN, 1) == "B9"
        assert hunt._hit_label(hit, ByteNotation.ISOTP, 1) == "i6"
        assert hunt._hit_label(hit, ByteNotation.TORQUE, 1) == "E"

    def test_correlate_has_transform_flag(self):
        import argparse

        from canlib.commands import correlate

        p = correlate.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["uds", "--against", "X:Y:Z", "--transform", "abs"])
        assert args.transform == "abs"


class TestOutputHygiene:
    """T3.3 — interpretation collapse + co-linear clustering."""

    def test_hunt_collapses_to_one_per_offset(self, tmp_path):
        from canlib.align import load_signal_captures

        # target byte B4 ramps; hunt should not emit u8+u16+u24 all at offset 4
        caps, refs = [], []
        for i in range(20):
            t = f"09:00:{i:02d}"
            caps.append({"ecu": "AAF", "pid": "2181", "payload": f"618100{i:02X}", "time": t})
            refs.append({"ecu": "ESC", "pid": "22C101", "payload": f"62C10100{i:02X}", "time": t})
        doc = {
            "sessions": [
                {"date": "2026-07-22", "vehicle_states": ["driving"], "captures": caps + refs}
            ]
        }
        (tmp_path / "2026-07-22.json").write_text(json.dumps(doc))
        loaded = load_signal_captures([("AAF", "2181"), ("ESC", "22C101")], captures_dir=tmp_path)
        from canlib.align import extract_series

        ref = extract_series(loaded[("ESC", "22C101")], "B5")
        collapsed = xanalysis.hunt_byte(loaded[("AAF", "2181")], ref, tol_s=1.0, min_n=10)
        offsets = [h.offset for h in collapsed]
        assert len(offsets) == len(set(offsets))  # one row per offset
        expanded = xanalysis.hunt_byte(
            loaded[("AAF", "2181")], ref, tol_s=1.0, min_n=10, all_interps=True
        )
        assert len(expanded) >= len(collapsed)  # --all-interps shows more

    def test_colinear_clusters_groups_mutual(self):
        from canlib.xanalysis import CorrHit, colinear_clusters

        # A,B,C mutually ~1.0 -> one cluster of 3; D unrelated
        hits = [
            CorrHit("A", "B", 0.999, 30),
            CorrHit("B", "C", 0.998, 30),
            CorrHit("A", "C", 0.997, 30),
            CorrHit("A", "D", 0.5, 30),
        ]
        clusters = colinear_clusters(hits)
        assert len(clusters) == 1
        assert clusters[0] == {"A", "B", "C"}


class TestOverlap:
    """T3.1 — co-poll overlap matrix."""

    def test_reports_overlapping_pairs(self, capsys, monkeypatch):
        from canlib.align import LoadedPid
        from canlib.commands import correlate

        def lp(ecu, pid, secs):
            x = LoadedPid(ecu, pid)
            x.captures = [
                {"date": "2026-07-22", "time": f"09:00:{s:02d}", "payload": "62C10100"}
                for s in secs
            ]
            return x

        # A & B co-polled (near-simultaneous); C polled at a disjoint time
        fake = {
            ("ESC", "22C101"): lp("ESC", "22C101", [0, 2, 4, 6]),
            ("MCU", "2102"): lp("MCU", "2102", [0, 2, 4, 6]),
            ("BMS", "2101"): lp("BMS", "2101", [40, 42]),
        }
        monkeypatch.setattr(correlate, "load_signal_captures", lambda *a, **k: fake)
        rc = correlate._print_overlap(list(fake), None, None, None, None, 1.0, 2, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ESC:22C101" in out and "MCU:2102" in out
        # ESC⟷MCU overlap (4) shown; BMS shares nothing within tol
        assert "BMS:2101  ⟷" not in out and "⟷  BMS:2101" not in out


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
        monkeypatch.setattr("canlib.pids_edit._text._resolve_pids_dir", lambda d: tmp_path)

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
        monkeypatch.setattr("canlib.pids_edit._text._resolve_pids_dir", lambda d: tmp_path)

        rows, series, ref = self._rows_and_series()
        rc = correlate._promote_top_byte(
            "AAF_CAND", rows, series, ref, "MCU:2102:MCU_MOTOR_RPM", 1.0
        )
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


# ---------------------------------------------------------------------------
# physical_scan — reference-free physical-value band detection
# ---------------------------------------------------------------------------
class TestPhysicalScan:
    def _loaded(self, payloads):
        from canlib.align import LoadedPid

        lp = LoadedPid("OBC", "2101")
        lp.captures = [
            {"date": "2026-07-22", "time": f"09:00:{i:02d}", "payload": p}
            for i, p in enumerate(payloads)
        ]
        return lp

    def test_finds_centivolt_word_in_mains_band(self):
        # A 16-bit centivolt AC-voltage word ~218-228 V -> raw 21800..22800.
        # 8-byte payload reconstructs multi-frame: SID@B2, echo@B3, word@[B4:B5].
        payloads = [f"6101{cv:04X}00000000" for cv in (21850, 22100, 22400, 22750, 22200)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        # The [B4:B5]/100 word should land in the mains-RMS band at /100.
        mains = [h for h in hits if h.band == "mains RMS V"]
        assert mains, f"expected a mains-RMS hit, got {[(h.expr, h.band) for h in hits]}"
        top = mains[0]
        assert top.scaling == "/100"
        assert 200 <= top.median <= 250

    def test_no_hit_when_out_of_all_bands(self):
        # Small values (~3-7) stay out of every band (>=11 V) at every scaling,
        # including the /100 and x-sqrt2 words. Multi-frame payload at B2.
        payloads = [f"6101{v:02X}0{v}0000000" for v in (3, 4, 5, 6, 7)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        assert hits == []

    def test_skips_protocol_header_bytes(self):
        # The SID (B2) and PID-echo (B3) are never sensor data — a constant echo
        # byte must not join a data byte into a spurious in-band word.
        payloads = [f"6101{cv:04X}00000000" for cv in (21850, 22100, 22400, 22750)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        assert all(h.offset >= 4 for h in hits)  # nothing at/below the echo byte B3

    def test_800v_pack_missed_by_defaults_hit_by_override(self):
        # A 16-bit deci-volt HV word ~550-800 V (800 V architecture) -> raw
        # 5500..8000, /10 = 550..800 V — outside the built-in HV pack 300-450.
        payloads = [f"6101{dv:04X}00000000" for dv in (5500, 6200, 7000, 7600, 8000)]
        lp = self._loaded(payloads)

        # Default bands: no HV-pack hit (out of the 300-450 band at /10).
        default_hits = xanalysis.physical_scan(lp, min_n=3)
        assert not [h for h in default_hits if h.band == "HV pack V"]

        # With an 800 V override the /10 word lands in the widened band.
        from canlib.physical_bands import resolve_physical_bands

        bands = resolve_physical_bands({"physical_bands": {"hv_pack": [450, 850]}})
        hits = xanalysis.physical_scan(lp, min_n=3, bands=bands)
        hv = [h for h in hits if h.band == "HV pack V"]
        assert hv, f"expected an HV-pack hit, got {[(h.expr, h.band) for h in hits]}"
        assert hv[0].scaling == "/10"
        assert 450 <= hv[0].median <= 850

    def test_temperature_byte_flagged_in_temp_band(self):
        # A single byte encoding a Hyundai/Kia temperature (raw/2-40): raw
        # 110..130 -> 15..25 °C. Must surface a temp-band hit, separate from any
        # voltage reading of the same byte.
        payloads = [f"6101{t:02X}0000000000" for t in (110, 116, 120, 124, 130)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        temp = [h for h in hits if h.band == "temp °C"]
        assert temp, f"expected a temp-band hit, got {[(h.expr, h.band) for h in hits]}"
        assert "40" in temp[0].scaling  # a raw/2-40 (or -40) temperature scaling
        assert 10 <= temp[0].median <= 30

    # -- single-frame layout -------------------------------------------------
    # Regression: the skip-set was built from wican_to_isotp/isotp_to_wican, which
    # hardcode the multi-frame layout (two First-Frame PCI bytes). A single-frame
    # response has only ONE PCI byte, so the header was shifted a byte too far and
    # the FIRST real data byte was silently excluded from the scan — a false
    # negative in the one tool meant to find an anchorless signal. Every other test
    # in this class uses an 8-byte (multi-frame) payload, which is why it survived.

    def test_finds_value_in_first_data_byte_of_single_frame_response(self):
        # 7-byte payload -> single frame: B0=PCI, B1=SID, B2=echo, B3..B7=data.
        # A mains-RMS-looking value sits in B3, the first data byte.
        payloads = [f"6101{v:02X}01020304" for v in (225, 228, 231, 226, 233)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        assert "B3" in {h.expr for h in hits}, (
            f"first data byte B3 must be scanned, got {[(h.expr, h.band) for h in hits]}"
        )

    def test_single_frame_still_excludes_pci_sid_and_echo(self):
        # The narrower skip-set must not swing the other way: B0 (PCI), B1 (SID)
        # and B2 (PID echo) are never sensor data.
        payloads = [f"6101{v:02X}01020304" for v in (225, 228, 231, 226, 233)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        assert all(h.offset >= 3 for h in hits), (
            f"PCI/SID/echo must stay excluded, got {[(h.expr, h.offset) for h in hits]}"
        )

    def test_mixed_frame_layouts_scan_only_commonly_safe_bytes(self):
        # If a PID answered with both layouts, a given WiCAN index can be a data
        # byte in one capture and the SID in another. Interpreting those together
        # is garbage, so only the intersection is scanned — never a byte whose role
        # differs across captures.
        single = [f"6101{v:02X}01020304" for v in (225, 228, 231)]
        multi = [f"6101{v:02X}010203040506" for v in (226, 229, 232)]
        hits = xanalysis.physical_scan(self._loaded(single + multi), min_n=3)
        # B1 is the SID under the single-frame layout and a PCI byte under the
        # multi-frame one — it must never be reported.
        assert all(h.offset >= 3 for h in hits), [(h.expr, h.offset) for h in hits]

    def test_voltage_hit_not_displaced_by_temp(self):
        # The mains-RMS word still surfaces though its bytes also get a
        # (lower-confidence) temperature reading — collapse is per (offset, kind).
        payloads = [f"6101{cv:04X}00000000" for cv in (21850, 22100, 22400, 22750, 22200)]
        hits = xanalysis.physical_scan(self._loaded(payloads), min_n=3)
        assert [h for h in hits if h.band == "mains RMS V"]

    def test_bands_none_matches_default_constant(self):
        # bands=None must reproduce the built-in PHYSICAL_BANDS behaviour exactly.
        payloads = [f"6101{cv:04X}00000000" for cv in (21850, 22100, 22400, 22750, 22200)]
        lp = self._loaded(payloads)
        a = xanalysis.physical_scan(lp, min_n=3)
        b = xanalysis.physical_scan(lp, min_n=3, bands=xanalysis.PHYSICAL_BANDS)
        assert [(h.offset, h.band, h.scaling) for h in a] == [
            (h.offset, h.band, h.scaling) for h in b
        ]


class TestFindFrameMirrors:
    """Generic positional (intra-frame) mirror finder shared by decode's
    single-PID --find-mirrors (via _decode_calc.find_mirrors)."""

    def test_byte_mirror_detected(self):
        # Frames b4/b5 always equal; b6 varies independently.
        frames = [
            bytes([0, 0, 0, 0, 0x0A, 0x0A, 0x01]),
            bytes([0, 0, 0, 0, 0x14, 0x14, 0x05]),
            bytes([0, 0, 0, 0, 0x09, 0x09, 0xFF]),
        ]
        pairs = {(a, b) for a, b, _ in xanalysis.find_frame_mirrors(frames)}
        assert ("B4", "B5") in pairs
        assert ("B4", "B6") not in pairs

    def test_constant_positions_excluded(self):
        # B4 constant 0x00 across all frames -> excluded (only varying positions).
        frames = [
            bytes([0, 0, 0, 0, 0x00, 0xAA]),
            bytes([0, 0, 0, 0, 0x00, 0xBB]),
            bytes([0, 0, 0, 0, 0x00, 0xCC]),
        ]
        mirrors = xanalysis.find_frame_mirrors(frames)
        assert all("B4" not in (a, b) for a, b, _ in mirrors)

    def test_bit_mirror(self):
        # B4 bits 0 and 2 co-vary (0x00/0x05).
        frames = [bytes([0, 0, 0, 0, v]) for v in (0x00, 0x05, 0x00, 0x05)]
        pairs = {(a, b) for a, b, _ in xanalysis.find_frame_mirrors(frames, bits=True)}
        assert ("B4:0", "B4:2") in pairs

    def test_too_few_frames(self):
        assert xanalysis.find_frame_mirrors([bytes([1, 2, 3])]) == []

    def test_n_is_frame_count(self):
        frames = [bytes([0, 0, 0, 0, v, v]) for v in (1, 2, 3)]
        mirrors = xanalysis.find_frame_mirrors(frames)
        assert mirrors and all(n == 3 for _, _, n in mirrors)


# ---------------------------------------------------------------------------
# byte_state_buckets — arbitrary grouping axis (group_of)
# ---------------------------------------------------------------------------
class TestByteStateBucketsGroupOf:
    """The --discriminate axis generalization: bucket bytes by any group key."""

    def test_group_of_overrides_state(self):
        results = [
            {"capture": {"payload": f"6101{v:02X}", "_g": g}, "decoded": {}}
            for v, g in [(0x10, "off"), (0x10, "off"), (0x40, "on"), (0x40, "on")]
        ]
        buckets = xanalysis.byte_state_buckets(
            results, "axis", group_of=lambda r: r["capture"]["_g"]
        )
        # The varying data byte is bucketed by the custom key, not the state.
        grouped = [b for b in buckets.values() if set(b) == {"off", "on"}]
        assert grouped, "expected a byte bucketed into the custom on/off groups"
        b = grouped[0]
        assert b["off"] == [16.0, 16.0] and b["on"] == [64.0, 64.0]

    def test_group_of_none_skips_capture(self):
        results = [
            {"capture": {"payload": "610110", "_g": "a"}, "decoded": {}},
            {"capture": {"payload": "610140", "_g": None}, "decoded": {}},
            {"capture": {"payload": "610120", "_g": "a"}, "decoded": {}},
        ]
        buckets = xanalysis.byte_state_buckets(
            results, "axis", group_of=lambda r: r["capture"]["_g"]
        )
        # The None-group capture (0x40) is dropped; no bucket ever holds 64.0.
        for b in buckets.values():
            assert set(b) <= {"a"}
            for vals in b.values():
                assert 64.0 not in vals


# ---------------------------------------------------------------------------
# axis_group_keys — discretize a cross-signal into per-capture group labels
# ---------------------------------------------------------------------------
class TestAxisGroupKeys:
    def _results(self, secs):
        return [{"capture": {"date": "2026-07-22", "time": f"09:00:{s:02d}"}} for s in secs]

    def test_discretizes_and_maps(self, monkeypatch):
        from canlib.commands import _decode_calc

        axis = [_tp(s, v) for s, v in [(0, 0.0), (2, 0.0), (4, 1.0), (6, 1.0)]]
        monkeypatch.setattr(
            _decode_calc, "load_cross_ref_series", lambda spec, **kw: (axis, "AXIS")
        )
        results = self._results([0, 2, 4, 6])
        keys, label = _decode_calc.axis_group_keys(results, "E:P:X", scope={}, tol_s=2.5)
        assert label == "AXIS"
        assert keys[id(results[0])] == "0"  # integer-valued → no ".0"
        assert keys[id(results[1])] == "0"
        assert keys[id(results[2])] == "1"
        assert keys[id(results[3])] == "1"

    def test_rejects_high_cardinality(self, monkeypatch):
        from canlib.commands import _decode_calc

        axis = [_tp(s, float(s)) for s in range(20)]  # 20 distinct values
        monkeypatch.setattr(
            _decode_calc, "load_cross_ref_series", lambda spec, **kw: (axis, "AXIS")
        )
        results = self._results(list(range(20)))
        with pytest.raises(ValueError, match="distinct values"):
            _decode_calc.axis_group_keys(results, "E:P:X", scope={}, tol_s=2.5)

    def test_no_alignment_raises(self, monkeypatch):
        from canlib.commands import _decode_calc

        axis = [_tp(1000 + s, 1.0) for s in range(4)]  # far from the captures
        monkeypatch.setattr(
            _decode_calc, "load_cross_ref_series", lambda spec, **kw: (axis, "AXIS")
        )
        results = self._results([0, 2, 4, 6])
        with pytest.raises(ValueError, match="aligned to no captures"):
            _decode_calc.axis_group_keys(results, "E:P:X", scope={}, tol_s=2.5)
