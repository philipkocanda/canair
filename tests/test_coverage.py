"""Tests for the `canair coverage` audit — verified-aware byte mapping."""

import pytest

from canlib.commands import coverage


class TestAnalyzePid:
    """analyze_pid is a pure function over (parameters, payload, subfunction bytes)."""

    def test_unmapped_and_unverified_split(self):
        # 2101 single-frame: payload 6101 AA BB CC DD -> WiCAN B0=SF PCI, B1=61,
        # B2=01 (sub echo), B3=AA(data), B4=BB, B5=CC, B6=DD. Data bytes B3..B6.
        params = {
            "VERIFIED_ONE": {"expression": "B3", "verified": True},
            "GUESS_ONE": {"expression": "B4"},  # unverified
        }
        result = coverage.analyze_pid(params, "6101AABBCCDD", sfb=1)
        assert result["unmapped"] == [5, 6]  # B5, B6 covered by nothing
        assert result["unverified_mapped"] == [4]  # B4 mapped but unverified
        # B3 is verified-mapped -> neither unmapped nor unverified.
        assert 3 not in result["unmapped"]
        assert 3 not in result["unverified_mapped"]

    def test_verified_covering_shared_byte_clears_unverified(self):
        params = {
            "GUESS": {"expression": "B3"},
            "CONFIRMED": {"expression": "B3", "verified": True},
        }
        result = coverage.analyze_pid(params, "6101AABBCCDD", sfb=1)
        # B3 is verified by CONFIRMED even though GUESS also reads it.
        assert result["unverified_mapped"] == []

    def test_fully_unverified(self):
        params = {"G1": {"expression": "B3"}, "G2": {"expression": "B4"}}
        result = coverage.analyze_pid(params, "6101AABB", sfb=1)
        assert result["unverified_mapped"] == [3, 4]
        assert result["unmapped"] == []


class TestBitfieldGaps:
    """A byte read bit-wise reports its undecoded bits.

    Regression cover for three defects that made real gaps invisible: a
    whole-byte read used to suppress the finding entirely, ``Sn:k`` bit reads
    were not counted, and a ``type: bitmask`` map contributed no coverage.
    """

    # 2101 single-frame: B3..B6 are the data bytes (see TestAnalyzePid).
    PAYLOAD = "6101AABBCCDD"

    def _gaps(self, params):
        return coverage.analyze_pid(params, self.PAYLOAD, sfb=1)["incomplete_bitfields"]

    def test_partial_bitfield_reported(self):
        params = {"F0": {"expression": "B3:0"}, "F1": {"expression": "B3:1"}}
        assert self._gaps(params) == [
            {"byte": 3, "have": [0, 1], "missing": [2, 3, 4, 5, 6, 7], "also_whole": False}
        ]

    def test_whole_byte_read_does_not_suppress_the_gap(self):
        """The `DEBUG_*_FLAGS` convention — a raw byte plus per-bit params.

        The raw byte does not decode the individual bits, so the gap stands; it
        is only flagged. This hid VCU 2101 B10 (the PRND byte) and BMS 2101 B14.
        """
        params = {
            "DEBUG_FLAGS": {"expression": "B3", "verified": True},
            "BIT0": {"expression": "B3:0", "verified": True},
        }
        (gap,) = self._gaps(params)
        assert gap["have"] == [0]
        assert gap["missing"] == [1, 2, 3, 4, 5, 6, 7]
        assert gap["also_whole"] is True

    def test_whole_byte_only_is_not_a_bitfield(self):
        """No bit is read, so the byte has no bit gap — unchanged behaviour."""
        assert self._gaps({"SCALAR": {"expression": "B3"}}) == []

    def test_signed_bit_read_counts(self):
        """`Sn:k` is a bit read too — the old private regex matched only `B`."""
        (gap,) = self._gaps({"F": {"expression": "S3:4"}})
        assert gap["have"] == [4]

    def test_bitmask_map_contributes_coverage(self):
        """A `type: bitmask` param's labelled bits are decoded bits."""
        params = {
            "MASK": {
                "expression": "B3",
                "type": "bitmask",
                "bits": {0: "mon", 1: "tue", 2: "wed"},
            }
        }
        (gap,) = self._gaps(params)
        assert gap["have"] == [0, 1, 2]
        assert gap["missing"] == [3, 4, 5, 6, 7]

    def test_fully_labelled_bitmask_has_no_gap(self):
        params = {
            "MASK": {
                "expression": "B3",
                "type": "bitmask",
                "bits": {i: f"b{i}" for i in range(8)},
            }
        }
        assert self._gaps(params) == []

    def test_bits_from_both_models_are_unioned(self):
        params = {
            "MASK": {"expression": "B3", "type": "bitmask", "bits": {0: "a"}},
            "BIT7": {"expression": "B3:7"},
        }
        (gap,) = self._gaps(params)
        assert gap["have"] == [0, 7]
        assert gap["also_whole"] is True

    def test_non_data_byte_is_not_reported(self):
        """A bit read on the PCI/SID/echo header is not a data-byte gap."""
        assert self._gaps({"F": {"expression": "B1:0"}}) == []


class TestKeepFilter:
    """The default view treats unverified-mapped bytes as a gap; --unverified isolates them."""

    def _run(self, tmp_path, monkeypatch, capsys, argv, payloads):
        import argparse

        monkeypatch.setattr(coverage, "load_representative_payloads", lambda: payloads)
        monkeypatch.setattr(coverage, "load_pids", lambda *_: {})
        monkeypatch.setattr(
            coverage,
            "build_ecu_index",
            lambda *_: {"MCU": {"pids": {"2102": {"parameters": {"GUESS": {"expression": "B4"}}}}}},
        )
        monkeypatch.setattr("canlib.ecus.canonical_ecu_name_safe", lambda n: n)
        p = coverage.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(argv)
        rc = coverage.run(args)
        return rc, capsys.readouterr().out

    def test_unverified_shown_by_default(self, tmp_path, monkeypatch, capsys):
        payloads = {
            ("MCU", "2102"): {
                "payload": "6102AABB",
                "date": "2026-07-01",
                "label": "",
                "file": "x.yaml",
            }
        }
        rc, out = self._run(tmp_path, monkeypatch, capsys, ["MCU", "2102"], payloads)
        assert rc == 0
        assert "UNVERIFIED" in out and "B4" in out

    def test_unverified_flag_isolates(self, tmp_path, monkeypatch, capsys):
        payloads = {
            ("MCU", "2102"): {
                "payload": "6102AABB",
                "date": "2026-07-01",
                "label": "",
                "file": "x.yaml",
            }
        }
        rc, out = self._run(
            tmp_path, monkeypatch, capsys, ["MCU", "2102", "--unverified"], payloads
        )
        assert rc == 0
        assert "UNVERIFIED" in out


class TestRepresentativePayload:
    """The payload that sets a PID's length comes from its newest session.

    Captures before 2026-07-28 store a multi-frame response padded to a whole CAN frame,
    and the padding is invisible in the hex. Taking the longest payload reported that
    filler as undecoded data (HVAC 2201A0 B69-B71).
    """

    CLEAN = "62" + "0102" + "11" * 13  # 16 bytes
    PADDED = CLEAN + "00" * 4  # 20 bytes: padded out to a whole 3-frame response

    @staticmethod
    def _session(date, payloads, **extra):
        caps = [{"rx": "0x7BB", "pid": "220102", "payload": p} for p in payloads]
        return {"date": date, "label": "x", "captures": caps, **extra}

    def _load(self, tmp_path, monkeypatch, files):
        import json
        import types

        for name, sessions in files.items():
            (tmp_path / f"{name}.json").write_text(json.dumps({"sessions": sessions}))
        monkeypatch.setattr(
            "canlib.profile.active", lambda: types.SimpleNamespace(capture_layers=[tmp_path])
        )
        monkeypatch.setattr("canlib.ecus.build_rx_index", lambda: {})
        return coverage.load_representative_payloads()[("0X7BB", "220102")]

    def test_newer_clean_session_beats_older_padded_one(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {
                "2026-04-17": [self._session("2026-04-17", [self.PADDED] * 5)],
                "2026-08-09": [self._session("2026-08-09", [self.CLEAN], version="1.18.0")],
            },
        )
        assert len(got["payload"]) // 2 == 16
        assert got["date"] == "2026-08-09"
        assert got["may_include_padding"] is False

    def test_stray_inside_an_older_session_is_ignored(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {
                "2026-08-08": [self._session("2026-08-08", [self.CLEAN, "62" + "00" * 54])],
                "2026-08-09": [self._session("2026-08-09", [self.CLEAN] * 3)],
            },
        )
        assert len(got["payload"]) // 2 == 16

    def test_most_common_length_within_the_session_wins(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {"2026-08-09": [self._session("2026-08-09", [self.CLEAN] * 3 + [self.PADDED])]},
        )
        assert len(got["payload"]) // 2 == 16

    def test_negative_response_is_not_a_length(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {
                "2026-08-08": [self._session("2026-08-08", [self.CLEAN])],
                "2026-08-09": [self._session("2026-08-09", ["7F2231"])],
            },
        )
        assert got["date"] == "2026-08-08"

    def test_old_frame_aligned_session_is_flagged_as_possibly_padded(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {"2026-04-17": [self._session("2026-04-17", [self.PADDED])]},
        )
        assert len(got["payload"]) // 2 == 20
        assert got["may_include_padding"] is True

    def test_frame_aligned_length_with_version_metadata_is_trusted(self, tmp_path, monkeypatch):
        got = self._load(
            tmp_path,
            monkeypatch,
            {"2026-08-09": [self._session("2026-08-09", [self.PADDED], version="1.18.0")]},
        )
        assert got["may_include_padding"] is False

    @pytest.mark.parametrize(
        ("length", "aligned"),
        [(20, True), (62, True), (13, True), (16, False), (59, False), (7, False)],
    )
    def test_is_frame_aligned(self, length, aligned):
        assert coverage.is_frame_aligned(length) is aligned
