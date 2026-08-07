"""Tests for multi-modal signal analysis: typed decoding (enum/bitmask/ascii/
date/bcd/struct), categorical statistics, and the event field-transition view.
"""

from datetime import date

from canlib import decode_value as dv
from canlib import stats
from canlib.autopid_layout import uds_hex_to_wican_bytes


class TestTypedDecode:
    """canlib.decode_value.decode_typed — the parallel typed decoder."""

    def test_numeric_default_is_unchanged(self):
        d = dv.decode_typed({"expression": "B3"}, bytes([0, 0, 0, 42]))
        assert d.kind == "numeric"
        assert d.raw == 42
        assert dv.render(d, "°C") == "42°C"

    def test_enum_maps_label(self):
        p = {"expression": "B3", "type": "enum", "values": {40: "fan1", 45: "fanMAX"}}
        assert dv.render(dv.decode_typed(p, bytes([0, 0, 0, 45]))) == "fanMAX (45)"
        # Unmapped raw falls back to the numeric string.
        assert dv.render(dv.decode_typed(p, bytes([0, 0, 0, 99]))) == "99 (99)"

    def test_enum_accepts_str_keys(self):
        p = {"expression": "B3", "type": "enum", "values": {"7": "drive"}}
        assert dv.decode_typed(p, bytes([0, 0, 0, 7])).label == "drive"

    def test_bitmask_flags_in_order(self):
        p = {
            "expression": "B3",
            "type": "bitmask",
            "bits": {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat"},
        }
        d = dv.decode_typed(p, bytes([0, 0, 0, 0x3F]))
        assert d.flags == ["mon", "tue", "wed", "thu", "fri", "sat"]
        assert dv.render(d) == "mon|tue|wed|thu|fri|sat"
        assert dv.render(dv.decode_typed(p, bytes([0, 0, 0, 0]))) == "(none)"

    def test_bitmask_unmapped_bit_shown(self):
        p = {"expression": "B3", "type": "bitmask", "bits": {0: "a"}}
        d = dv.decode_typed(p, bytes([0, 0, 0, 0b101]))
        assert d.flags == ["a", "bit2"]

    def test_date_bcd(self):
        # BCD 20 17 06 06 at B3..B6
        p = {"expression": "0", "type": "date"}
        d = dv.decode_typed(p, bytes([0x62, 0xB0, 0x0C, 0x20, 0x17, 0x06, 0x06]))
        assert d.dt == date(2017, 6, 6)
        assert d.text == "2017-06-06"

    def test_ascii(self):
        p = {"expression": "0", "type": "ascii"}
        d = dv.decode_typed(p, b"\x62\xb0\x04ABCD")
        assert d.text == "ABCD"

    def test_bcd_scalar(self):
        p = {"expression": "B3", "type": "bcd"}
        d = dv.decode_typed(p, bytes([0, 0, 0, 0x25]))
        assert d.raw == 25
        assert d.text == "25"

    def test_struct_nested(self):
        p = {
            "expression": "0",
            "type": "struct",
            "fields": [
                {
                    "name": "days",
                    "expression": "B3",
                    "type": "bitmask",
                    "bits": {1: "tue"},
                },
                {"name": "hour", "expression": "B4"},
                {"name": "minute", "expression": "B5"},
            ],
        }
        d = dv.decode_typed(p, bytes([0, 0, 0, 0b10, 7, 30]))
        assert dv.render(d) == "{days=tue, hour=7, minute=30}"

    def test_eval_error_reported(self):
        d = dv.decode_typed({"expression": "B99", "type": "enum", "values": {}}, b"\x00")
        assert d.raw is None
        assert d.error is not None
        assert dv.render(d) == "ERROR"

    def test_category_key(self):
        e = dv.decode_typed(
            {"expression": "B3", "type": "enum", "values": {1: "on"}}, bytes([0, 0, 0, 1])
        )
        assert e.category() == "on"
        b = dv.decode_typed(
            {"expression": "B3", "type": "bitmask", "bits": {0: "x"}}, bytes([0, 0, 0, 1])
        )
        assert b.category() == "x"
        assert dv.decode_typed({"expression": "B3"}, bytes([0, 0, 0, 1])).category() is None


class TestAsciiDateRunSlicing:
    """``ascii``/``date`` read a byte RUN — ISO-TP framing must not leak into it.

    ``decode_typed`` takes WiCAN-layout bytes (PCI re-inserted), so any run wider
    than one CAN frame spans framing bytes. A Consecutive-Frame PCI byte is
    ``0x21``/``0x22``/… — *printable ASCII* (``!``, ``"``) — so leaving it in
    corrupts the text silently instead of failing loudly. Regression guard for the
    VIN that decoded as ``KMHC!XXXXXXX"XXXXXX``.
    """

    VIN = "KMHCXXXXXXXXXXXXX"  # 17 chars, the ISO 3779 length

    @staticmethod
    def _wican(payload_hex: str) -> bytes:
        return uds_hex_to_wican_bytes(payload_hex)

    def _vin_1a90(self) -> bytes:
        # KWP2000 ReadEcuIdentification record 0x90: SID 0x5A + REC + 17 ASCII.
        return self._wican("5A90" + self.VIN.encode().hex().upper())

    def test_multiframe_ascii_skips_cf_pci(self):
        p = {"expression": "[B04:B22]", "type": "ascii"}
        assert dv.decode_typed(p, self._vin_1a90()).text == self.VIN

    def test_multiframe_range_past_payload_ignores_padding(self):
        # B23 is the zero-padding of the final CAN frame, past the declared
        # 19-byte payload — dropped, not rendered.
        p = {"expression": "[B04:B23]", "type": "ascii"}
        assert dv.decode_typed(p, self._vin_1a90()).text == self.VIN

    def test_singleframe_ascii_range(self):
        # A single-frame response carries ONE PCI byte, so data starts at B02.
        wb = self._wican("5A90" + b"ABCDE".hex().upper())
        p = {"expression": "[B03:B07]", "type": "ascii"}
        assert dv.decode_typed(p, wb).text == "ABCDE"

    def test_fallback_looks_up_header_width_kwp(self):
        # No usable range expression: 0x5A (KWP2000 1A) header is SID + REC = 2.
        assert dv.decode_typed({"type": "ascii"}, self._vin_1a90()).text == self.VIN

    def test_fallback_looks_up_header_width_did(self):
        # 0x62 (UDS 22) header is SID + DID = 3 — a different width, same code path.
        wb = self._wican("62F190" + self.VIN.encode().hex().upper())
        assert dv.decode_typed({"type": "ascii"}, wb).text == self.VIN

    def test_fallback_unknown_service_drops_only_the_sid(self):
        # 0x05 is not a response SID canair has a layout for; only the byte every
        # positive response certainly has (the SID) is dropped.
        wb = self._wican("05" + b"ABCDEFGHIJKLMNOP".hex().upper())
        assert dv.decode_typed({"type": "ascii"}, wb).text == "ABCDEFGHIJKLMNOP"

    def test_multiframe_date_skips_cf_pci(self):
        # A BCD date straddling the FF/CF boundary: payload 5A 91 <4 pad> 20 17 06 06
        # puts the 4 date bytes at B08(PCI) onwards, so the run must skip B08.
        payload = "5A91" + "AA" * 4 + "20170606"
        wb = self._wican(payload)
        d = dv.decode_typed({"expression": "[B09:B12]", "type": "date"}, wb)
        assert d.text == "2017-06-06"
        assert d.dt == date(2017, 6, 6)


class TestCategoricalStats:
    """Cramér's V / mutual information for nominal association."""

    def test_perfect_association(self):
        x = [0, 0, 1, 1, 2, 2]
        y = ["a", "a", "b", "b", "c", "c"]
        assert stats.cramers_v(x, y) == 1.0
        assert stats.mutual_information(x, y) == 1.0

    def test_no_association(self):
        import random

        random.seed(1)
        x = [i % 3 for i in range(120)]
        y = [random.randint(0, 2) for _ in range(120)]
        assert stats.cramers_v(x, y) < 0.2
        assert stats.mutual_information(x, y) < 0.2

    def test_degenerate_returns_none(self):
        assert stats.cramers_v([1], [2]) is None
        assert stats.cramers_v([1, 1, 1], ["a", "a", "a"]) is None  # single category

    def test_dispatch_and_correlation_routing(self):
        x = [0, 0, 1, 1]
        y = ["a", "a", "b", "b"]
        assert stats.categorical_association(x, y, "cramers_v") == 1.0
        # correlation() routes categorical methods through the same path.
        assert stats.correlation(x, [0.0, 0.0, 1.0, 1.0], "cramers_v") == 1.0
        assert "cramers_v" in stats.CATEGORICAL_METHODS
        assert "mutual_info" in stats.CATEGORICAL_METHODS


class TestIdentityRegression:
    """De-siloing date/ASCII into decode_value must not change identity output."""

    def test_identity_date_and_ascii(self):
        from canlib.modes.identity_decode import decode_identity_payload

        assert decode_identity_payload(bytes([0x20, 0x17, 0x06, 0x06]), "date") == "2017-06-06"
        assert decode_identity_payload(b"HELLO", "ascii") == "HELLO"
        # Non-date bytes under a date hint fall back to text/hex.
        assert decode_identity_payload(bytes([0x1E, 0x09, 0x0D, 0x14]), "date") != "0000-00-00"
