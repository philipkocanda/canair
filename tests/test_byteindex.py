"""Tests for canlib.byteindex — byte index conversion between notations."""

import pytest

from canlib.byteindex import (
    bix_to_torque,
    conversion_table,
    elm_to_wican_idx,
    extract_byte_indices,
    isotp_to_wican,
    letter_to_torque_idx,
    torque_idx_to_letter,
    torque_to_bix,
    torque_to_wican,
    wican_to_bix,
    wican_to_elm_idx,
    wican_to_isotp,
    wican_to_torque,
)

# ── Reference table from docs/wican-iso-tp-index-conversion.md ──
# (wican, isotp_hex, torque1_letter, bix1, torque2_letter, bix2)
# None means PCI / header byte.
REFERENCE_TABLE = [
    # Frame 0 (First Frame)
    (0, None, None, None, None, None),  # FF PCI high
    (1, None, None, None, None, None),  # FF PCI low
    (2, 0x00, None, None, None, None),  # SID byte
    (3, 0x01, None, None, None, None),  # subfunction (T1: header; T2: header byte 1)
    (4, 0x02, "A", 0, None, None),  # T1 data start; T2: subfunction byte 2
    (5, 0x03, "B", 8, "A", 0),  # T2 data start
    (6, 0x04, "C", 16, "B", 8),
    (7, 0x05, "D", 24, "C", 16),
    # Frame 1 (CF)
    (8, None, None, None, None, None),  # CF PCI
    (9, 0x06, "E", 32, "D", 24),
    (10, 0x07, "F", 40, "E", 32),
    (11, 0x08, "G", 48, "F", 40),
    (12, 0x09, "H", 56, "G", 48),
    (13, 0x0A, "I", 64, "H", 56),
    (14, 0x0B, "J", 72, "I", 64),
    (15, 0x0C, "K", 80, "J", 72),
    # Frame 2
    (16, None, None, None, None, None),
    (17, 0x0D, "L", 88, "K", 80),
    (18, 0x0E, "M", 96, "L", 88),
    (19, 0x0F, "N", 104, "M", 96),
    (20, 0x10, "O", 112, "N", 104),
    (21, 0x11, "P", 120, "O", 112),
    (22, 0x12, "Q", 128, "P", 120),
    (23, 0x13, "R", 136, "Q", 128),
    # Frame 3
    (24, None, None, None, None, None),
    (25, 0x14, "S", 144, "R", 136),
    (26, 0x15, "T", 152, "S", 144),
    (27, 0x16, "U", 160, "T", 152),
    (28, 0x17, "V", 168, "U", 160),
    (29, 0x18, "W", 176, "V", 168),
    (30, 0x19, "X", 184, "W", 176),
    (31, 0x1A, "Y", 192, "X", 184),
    # Frame 4
    (32, None, None, None, None, None),
    (33, 0x1B, "Z", 200, "Y", 192),
    (34, 0x1C, "AA", 208, "Z", 200),
    (35, 0x1D, "AB", 216, "AA", 208),
    (36, 0x1E, "AC", 224, "AB", 216),
    (37, 0x1F, "AD", 232, "AC", 224),
    (38, 0x20, "AE", 240, "AD", 232),
    (39, 0x21, "AF", 248, "AE", 240),
    # Frame 5
    (40, None, None, None, None, None),
    (41, 0x22, "AG", 256, "AF", 248),
    (42, 0x23, "AH", 264, "AG", 256),
    (43, 0x24, "AI", 272, "AH", 264),
    (44, 0x25, "AJ", 280, "AI", 272),
    (45, 0x26, "AK", 288, "AJ", 280),
    (46, 0x27, "AL", 296, "AK", 288),
    (47, 0x28, "AM", 304, "AL", 296),
    # Frame 6
    (48, None, None, None, None, None),
    (49, 0x29, "AN", 312, "AM", 304),
    (50, 0x2A, "AO", 320, "AN", 312),
    (51, 0x2B, "AP", 328, "AO", 320),
    (52, 0x2C, "AQ", 336, "AP", 328),
    (53, 0x2D, "AR", 344, "AQ", 336),
    (54, 0x2E, "AS", 352, "AR", 344),
    (55, 0x2F, "AT", 360, "AS", 352),
    # Frame 7
    (56, None, None, None, None, None),
    (57, 0x30, "AU", 368, "AT", 360),
    (58, 0x31, "AV", 376, "AU", 368),
    (59, 0x32, "AW", 384, "AV", 376),
    (60, 0x33, "AX", 392, "AW", 384),
    (61, 0x34, "AY", 400, "AX", 392),
    (62, 0x35, "AZ", 408, "AY", 400),
    (63, 0x36, "BA", 416, "AZ", 408),
    # Frame 8
    (64, None, None, None, None, None),
    (65, 0x37, "BB", 424, "BA", 416),
    (66, 0x38, "BC", 432, "BB", 424),
    (67, 0x39, "BD", 440, "BC", 432),
    (68, 0x3A, "BE", 448, "BD", 440),
    (69, 0x3B, "BF", 456, "BE", 448),
    (70, 0x3C, "BG", 464, "BF", 456),
    (71, 0x3D, "BH", 472, "BG", 464),
]


class TestWicanToIsotpAgainstTable:
    """Verify wican_to_isotp against every row in the reference table."""

    @pytest.mark.parametrize(
        "wican, isotp_hex",
        [(r[0], r[1]) for r in REFERENCE_TABLE],
        ids=[f"W{r[0]}" for r in REFERENCE_TABLE],
    )
    def test_wican_to_isotp(self, wican, isotp_hex):
        result = wican_to_isotp(wican)
        assert result == isotp_hex


class TestTorque1AgainstTable:
    """Verify Torque 1-byte subfunction conversion against reference table."""

    @pytest.mark.parametrize(
        "wican, torque_letter, bix",
        [(r[0], r[2], r[3]) for r in REFERENCE_TABLE],
        ids=[f"W{r[0]}" for r in REFERENCE_TABLE],
    )
    def test_wican_to_torque1(self, wican, torque_letter, bix):
        torque = wican_to_torque(wican, subfunction_bytes=1)
        if torque_letter is None:
            assert torque is None
        else:
            assert torque == letter_to_torque_idx(torque_letter)
            assert wican_to_bix(wican, subfunction_bytes=1) == bix


class TestTorque2AgainstTable:
    """Verify Torque 2-byte subfunction conversion against reference table."""

    @pytest.mark.parametrize(
        "wican, torque_letter, bix",
        [(r[0], r[4], r[5]) for r in REFERENCE_TABLE],
        ids=[f"W{r[0]}" for r in REFERENCE_TABLE],
    )
    def test_wican_to_torque2(self, wican, torque_letter, bix):
        torque = wican_to_torque(wican, subfunction_bytes=2)
        if torque_letter is None:
            assert torque is None
        else:
            assert torque == letter_to_torque_idx(torque_letter)
            assert wican_to_bix(wican, subfunction_bytes=2) == bix


class TestRoundTrips:
    """Verify all conversions round-trip correctly."""

    @pytest.mark.parametrize("wican", [w for w, isotp, *_ in REFERENCE_TABLE if isotp is not None])
    def test_isotp_round_trip(self, wican):
        isotp = wican_to_isotp(wican)
        assert isotp_to_wican(isotp) == wican

    @pytest.mark.parametrize("wican", [w for w, _, t1, *_ in REFERENCE_TABLE if t1 is not None])
    def test_torque1_round_trip(self, wican):
        t = wican_to_torque(wican, subfunction_bytes=1)
        assert torque_to_wican(t, subfunction_bytes=1) == wican

    @pytest.mark.parametrize(
        "wican", [w for w, _, _, _, t2, _ in REFERENCE_TABLE if t2 is not None]
    )
    def test_torque2_round_trip(self, wican):
        t = wican_to_torque(wican, subfunction_bytes=2)
        assert torque_to_wican(t, subfunction_bytes=2) == wican

    def test_bix_round_trip(self):
        for idx in range(50):
            assert bix_to_torque(torque_to_bix(idx)) == idx


class TestLetterNotation:
    """Test Torque letter ↔ index conversion."""

    CASES = [  # noqa: RUF012
        (0, "A"),
        (1, "B"),
        (25, "Z"),
        (26, "AA"),
        (27, "AB"),
        (51, "AZ"),
        (52, "BA"),
        (53, "BB"),
        (71, "BT"),
    ]

    @pytest.mark.parametrize("idx, letter", CASES)
    def test_idx_to_letter(self, idx, letter):
        assert torque_idx_to_letter(idx) == letter

    @pytest.mark.parametrize("idx, letter", CASES)
    def test_letter_to_idx(self, idx, letter):
        assert letter_to_torque_idx(letter) == idx

    def test_letter_case_insensitive(self):
        assert letter_to_torque_idx("aa") == 26
        assert letter_to_torque_idx("Az") == 51

    def test_invalid_letter(self):
        with pytest.raises(ValueError):
            letter_to_torque_idx("ABC")

    def test_idx_to_letter_last_two_letter(self):
        assert torque_idx_to_letter(701) == "ZZ"

    def test_idx_to_letter_out_of_range_raises(self):
        with pytest.raises(ValueError):
            torque_idx_to_letter(702)

    def test_idx_to_letter_negative_raises(self):
        with pytest.raises(ValueError):
            torque_idx_to_letter(-1)


class TestExtractByteIndices:
    """Test WiCAN expression byte index extraction."""

    def test_single_bytes(self):
        assert extract_byte_indices("B09/2") == {9}

    def test_signed_byte(self):
        assert extract_byte_indices("S12-40") == {12}

    def test_bit_access(self):
        assert extract_byte_indices("B05:3") == {5}

    def test_range(self):
        assert extract_byte_indices("[B04:B05]*256") == {4, 5}

    def test_signed_range(self):
        assert extract_byte_indices("[S10:S11]/10") == {10, 11}

    def test_mixed(self):
        expr = "([B04:B05]<<8|B06)/10"
        assert extract_byte_indices(expr) == {4, 5, 6}

    def test_multiple_ranges(self):
        expr = "[B04:B05]+[B06:B07]"
        assert extract_byte_indices(expr) == {4, 5, 6, 7}

    def test_empty(self):
        assert extract_byte_indices("42") == set()


class TestWicanToElmIdx:
    """Test backward-compat wican_to_elm_idx function."""

    def test_single_frame_pci(self):
        assert wican_to_elm_idx(0, payload_len=5) is None

    def test_single_frame_data(self):
        assert wican_to_elm_idx(1, payload_len=5) == 0
        assert wican_to_elm_idx(5, payload_len=5) == 4

    def test_multi_frame_delegates(self):
        # Should match wican_to_isotp for multi-frame
        assert wican_to_elm_idx(0, payload_len=20) is None
        assert wican_to_elm_idx(2, payload_len=20) == 0x00
        assert wican_to_elm_idx(9, payload_len=20) == 0x06


class TestElmToWicanIdx:
    """Test elm_to_wican_idx — inverse of wican_to_elm_idx."""

    def test_single_frame(self):
        assert elm_to_wican_idx(0, payload_len=5) == 1
        assert elm_to_wican_idx(4, payload_len=5) == 5

    def test_multi_frame(self):
        # ELM/ISO-TP index → WiCAN, skipping PCI bytes
        assert elm_to_wican_idx(0, payload_len=27) == 2
        assert elm_to_wican_idx(6, payload_len=27) == 9  # skips PCI at wican 8
        assert elm_to_wican_idx(13, payload_len=27) == 17  # skips PCI at wican 16

    def test_round_trip_multi_frame(self):
        for elm in range(25):
            assert wican_to_elm_idx(elm_to_wican_idx(elm, 27), 27) == elm

    def test_round_trip_single_frame(self):
        for elm in range(7):
            assert wican_to_elm_idx(elm_to_wican_idx(elm, 5), 5) == elm


class TestConversionTable:
    """Test table generation."""

    def test_table_length(self):
        table = conversion_table(max_wican=71)
        assert len(table) == 72

    def test_table_pci_rows(self):
        table = conversion_table(max_wican=71)
        pci_wican = [r["wican"] for r in table if r["isotp"] is None]
        assert pci_wican == [0, 1, 8, 16, 24, 32, 40, 48, 56, 64]
