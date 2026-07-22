"""Tests for canlib.wican_bytes — WiCAN AutoPID byte-layout reconstruction."""

from canlib.wican_bytes import uds_hex_to_wican_bytes


class TestUdsHexToWicanBytes:
    def test_single_frame(self):
        """Single-frame: PCI = length byte prepended."""
        result = uds_hex_to_wican_bytes("6101FF")
        assert result[0] == 3  # length
        assert result[1:] == bytes.fromhex("6101FF")

    def test_single_frame_max(self):
        """7 payload bytes = max single frame."""
        result = uds_hex_to_wican_bytes("61010203040506")
        assert result[0] == 7
        assert len(result) == 8

    def test_multi_frame_basic(self):
        """8+ payload bytes triggers multi-frame with PCI insertion."""
        payload = "6101" + "AA" * 20  # 22 bytes total
        result = uds_hex_to_wican_bytes(payload)
        # First frame: [10 16] + 6 data bytes = 8
        assert result[0] == 0x10
        assert result[1] == 22  # payload length
        assert result[2:8] == bytes.fromhex("6101" + "AA" * 4)
        # Consecutive frame 1: [21] + 7 data bytes
        assert result[8] == 0x21
        # Consecutive frame 2: [22] + 7 data bytes
        assert result[16] == 0x22

    def test_multi_frame_padding(self):
        """Last consecutive frame padded with zeros."""
        payload = "6101AABB"  # 4 bytes -> single frame, no padding needed
        result = uds_hex_to_wican_bytes(payload)
        assert len(result) == 5  # 1 PCI + 4 data

        # 8 bytes -> multi-frame: FF[10 08] + 6 data, CF[21] + 2 data + 5 padding
        payload = "6101AABBCCDDEEFF"  # 8 bytes
        result = uds_hex_to_wican_bytes(payload)
        assert result[0] == 0x10
        assert result[1] == 8
        cf1_data = result[9:16]
        assert cf1_data[0:2] == bytes.fromhex("EEFF")
        assert cf1_data[2:] == b"\x00" * 5

    def test_roundtrip_byte_indices(self):
        """Verify B00, B08, B16 are PCI bytes (matching WiCAN expression indexing)."""
        payload = bytes(range(0x61, 0x61 + 30)).hex()  # 30 bytes
        result = uds_hex_to_wican_bytes(payload)
        assert result[0] == 0x10  # B00 = FF PCI high
        assert result[8] == 0x21  # B08 = CF1 PCI
        assert result[16] == 0x22  # B16 = CF2 PCI
        assert result[24] == 0x23  # B24 = CF3 PCI
