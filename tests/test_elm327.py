"""Tests for canlib.elm327 — ELM327 parsing, safety checks, byte conversion."""

from canlib.elm327 import check_command_safety, elm_hex_to_wican_bytes, parse_elm_response

# --- check_command_safety ---


class TestCheckCommandSafety:
    def test_at_commands_always_safe(self):
        assert check_command_safety("ATSP6") is None
        assert check_command_safety("ATSH7E4") is None
        assert check_command_safety("atst96") is None

    def test_read_services_allowed(self):
        assert check_command_safety("2101") is None  # ReadDataByLocalId
        assert check_command_safety("22BC03") is None  # ReadDataByIdentifier
        assert check_command_safety("1001") is None  # DiagSession default
        assert check_command_safety("1003") is None  # DiagSession extended

    def test_blocked_write_services(self):
        assert "BLOCKED" in check_command_safety("2E F187 00")
        assert "BLOCKED" in check_command_safety("3400")
        assert "BLOCKED" in check_command_safety("35")
        assert "BLOCKED" in check_command_safety("3601AABB")

    def test_blocked_programming_session(self):
        result = check_command_safety("1002")
        assert result is not None
        assert "programmingSession" in result

    def test_iocontrol_allowed(self):
        # 0x2F IOControl is NOT in blocked list (deliberate — used for testing)
        assert check_command_safety("2FBC1003") is None

    def test_empty_and_nonsense(self):
        assert check_command_safety("") is None
        assert check_command_safety("hello") is None
        assert check_command_safety("A") is None  # single hex char


# --- parse_elm_response ---


class TestParseElmResponse:
    def test_positive_single_frame(self):
        r = parse_elm_response("61 01 FF 00 AA")
        assert r["ok"] is True
        assert r["hex"] == "6101FF00AA"
        assert r["bytes"] == bytes.fromhex("6101FF00AA")

    def test_negative_response(self):
        r = parse_elm_response("7F 21 31")
        assert r["ok"] is False
        assert r["nrc"] == 0x31
        assert r["nrc_desc"] == "requestOutOfRange"
        assert r["nrc_service"] == 0x21

    def test_no_data(self):
        r = parse_elm_response("NO DATA")
        assert r["ok"] is False
        assert "No response" in r["error"]

    def test_can_error(self):
        r = parse_elm_response("CAN ERROR")
        assert r["ok"] is False
        assert "CAN bus error" in r["error"]

    def test_unknown_command(self):
        r = parse_elm_response("?")
        assert r["ok"] is False
        assert "Unknown command" in r["error"]

    def test_empty_response(self):
        r = parse_elm_response("")
        assert r["ok"] is False
        assert "Empty response" in r["error"]

    def test_ok_line_filtered(self):
        r = parse_elm_response("OK\n61 01 FF")
        assert r["ok"] is True
        assert r["hex"] == "6101FF"

    def test_at_echo_filtered(self):
        r = parse_elm_response("ATSH7E4\nOK\n61 01 AA BB")
        assert r["ok"] is True
        assert r["hex"] == "6101AABB"

    def test_multiframe_iso_tp(self):
        r = parse_elm_response("0:6101AABBCCDDEE\n1:FF0011223344556\n2:67788")
        assert r["ok"] is True
        # Frames concatenated in order
        assert r["hex"].startswith("6101AABBCCDDEE")

    def test_request_echo_filtered(self):
        """Request echo (starts with service byte 0x10-0x3E) stripped when multi-line."""
        r = parse_elm_response("2101\n61 01 AA BB CC")
        assert r["ok"] is True
        assert r["hex"] == "6101AABBCC"

    def test_buffer_full(self):
        r = parse_elm_response("BUFFER FULL")
        assert r["ok"] is False
        assert "buffer" in r["error"].lower()

    def test_nrc_unknown_code(self):
        r = parse_elm_response("7F 22 99")
        assert r["ok"] is False
        assert r["nrc"] == 0x99
        assert "unknown" in r["nrc_desc"]

    def test_raw_preserved(self):
        r = parse_elm_response("NO DATA")
        assert r["raw"] == "NO DATA"


class TestEchoValidation:
    """SID/DID echo validation catches stale/misaligned frames buffered
    in the ELM327 adapter — the off-by-one we saw during IGPM 0x2F scans
    where a late 6FBC0900 leaked into the next read and got recorded as
    BC0A's response."""

    def test_sid_echo_ok(self):
        r = parse_elm_response("6F BC 0A 00", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is True
        assert r["hex"] == "6FBC0A00"

    def test_sid_mismatch_detected(self):
        """Response SID 0x62 (from a 0x22 request) arriving in a 0x2F read."""
        r = parse_elm_response("62 BC 0A 00", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is False
        assert "SID mismatch" in r["error"]
        assert "0x62" in r["error"]
        assert "0x6F" in r["error"]

    def test_did_mismatch_detected(self):
        """The off-by-one: probe BC0A gets back BC09's late response."""
        r = parse_elm_response("6F BC 09 00", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is False
        assert "DID mismatch" in r["error"]
        assert "0xBC09" in r["error"]
        assert "0xBC0A" in r["error"]

    def test_response_too_short_for_did(self):
        r = parse_elm_response("6F BC", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is False
        assert "too short" in r["error"].lower()

    def test_garbage_first_byte_rejected(self):
        """The '7E00' anomaly seen in discoveries — not a valid 2F response."""
        r = parse_elm_response("7E 00", expected_sid=0x2F, expected_did=0xBB51)
        assert r["ok"] is False
        # '7E00' parses as 2 bytes; first is 0x7E != 0x6F → SID mismatch
        assert "SID mismatch" in r["error"]

    def test_nrc_with_matching_service_ok(self):
        """NRC 0x7F {SID=0x2F} {0x31} — correctly-addressed NRC for our 0x2F request."""
        r = parse_elm_response("7F 2F 31", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is False
        assert r["nrc"] == 0x31
        assert r.get("error") is None  # No mismatch error

    def test_nrc_with_wrong_service_rejected(self):
        """NRC 0x7F {SID=0x22} {0x31} arriving in a 0x2F scan — stale frame."""
        r = parse_elm_response("7F 22 31", expected_sid=0x2F, expected_did=0xBC0A)
        assert r["ok"] is False
        assert "NRC echo mismatch" in r["error"]
        # The stale nrc is suppressed so scanner doesn't misclassify as "absent"
        assert "nrc" not in r

    def test_validation_disabled_by_default(self):
        """Without expected_sid, old behaviour preserved — no mismatch check."""
        r = parse_elm_response("6F BC 09 00")
        assert r["ok"] is True
        r = parse_elm_response("7F 22 31")
        assert r["ok"] is False
        assert r["nrc"] == 0x31

    def test_sid_only_without_did(self):
        """expected_sid alone validates SID but not DID echo."""
        r = parse_elm_response("6F BC 99 00", expected_sid=0x2F)
        assert r["ok"] is True  # SID matches, DID not checked


# --- elm_hex_to_wican_bytes ---


class TestElmHexToWicanBytes:
    def test_single_frame(self):
        """Single-frame: PCI = length byte prepended."""
        result = elm_hex_to_wican_bytes("6101FF")
        assert result[0] == 3  # length
        assert result[1:] == bytes.fromhex("6101FF")

    def test_single_frame_max(self):
        """7 payload bytes = max single frame."""
        result = elm_hex_to_wican_bytes("61010203040506")
        assert result[0] == 7
        assert len(result) == 8

    def test_multi_frame_basic(self):
        """8+ payload bytes triggers multi-frame with PCI insertion."""
        payload = "6101" + "AA" * 20  # 22 bytes total
        result = elm_hex_to_wican_bytes(payload)
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
        result = elm_hex_to_wican_bytes(payload)
        assert len(result) == 5  # 1 PCI + 4 data

        # 8 bytes -> multi-frame: FF[10 08] + 6 data, CF[21] + 2 data + 5 padding
        payload = "6101AABBCCDDEEFF"  # 8 bytes
        result = elm_hex_to_wican_bytes(payload)
        assert result[0] == 0x10
        assert result[1] == 8
        cf1_data = result[9:16]
        assert cf1_data[0:2] == bytes.fromhex("EEFF")
        assert cf1_data[2:] == b"\x00" * 5

    def test_roundtrip_byte_indices(self):
        """Verify B00, B08, B16 are PCI bytes (matching WiCAN expression indexing)."""
        payload = bytes(range(0x61, 0x61 + 30)).hex()  # 30 bytes
        result = elm_hex_to_wican_bytes(payload)
        assert result[0] == 0x10  # B00 = FF PCI high
        assert result[8] == 0x21  # B08 = CF1 PCI
        assert result[16] == 0x22  # B16 = CF2 PCI
        assert result[24] == 0x23  # B24 = CF3 PCI
