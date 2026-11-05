"""Tests for canair.uds.parse — UDS response parsing and NRC handling."""

from canair.uds.echo import request_echo
from canair.uds.parse import parse_uds_response
from canair.uds.payload import payload_echo_mismatch, payload_not_hex


class TestParseUdsResponse:
    def test_positive_single_frame(self):
        r = parse_uds_response("61 01 FF 00 AA")
        assert r["ok"] is True
        assert r["hex"] == "6101FF00AA"
        assert r["bytes"] == bytes.fromhex("6101FF00AA")

    def test_negative_response(self):
        r = parse_uds_response("7F 21 31")
        assert r["ok"] is False
        assert r["nrc"] == 0x31
        assert r["nrc_desc"] == "requestOutOfRange"
        assert r["nrc_service"] == 0x21

    def test_no_data(self):
        r = parse_uds_response("NO DATA")
        assert r["ok"] is False
        assert "No response" in r["error"]

    def test_can_error(self):
        r = parse_uds_response("CAN ERROR")
        assert r["ok"] is False
        assert "CAN bus error" in r["error"]

    def test_unknown_command(self):
        r = parse_uds_response("?")
        assert r["ok"] is False
        assert "Unknown command" in r["error"]

    def test_empty_response(self):
        r = parse_uds_response("")
        assert r["ok"] is False
        assert "Empty response" in r["error"]

    def test_ok_line_filtered(self):
        r = parse_uds_response("OK\n61 01 FF")
        assert r["ok"] is True
        assert r["hex"] == "6101FF"

    def test_at_echo_filtered(self):
        r = parse_uds_response("ATSH7E4\nOK\n61 01 AA BB")
        assert r["ok"] is True
        assert r["hex"] == "6101AABB"

    def test_multiframe_iso_tp(self):
        r = parse_uds_response("0:6101AABBCCDDEE\n1:FF0011223344556\n2:67788")
        assert r["ok"] is True
        # Frames concatenated in order
        assert r["hex"].startswith("6101AABBCCDDEE")

    def test_request_echo_filtered(self):
        """Request echo (starts with service byte 0x10-0x3E) stripped when multi-line."""
        r = parse_uds_response("2101\n61 01 AA BB CC")
        assert r["ok"] is True
        assert r["hex"] == "6101AABBCC"

    def test_buffer_full(self):
        r = parse_uds_response("BUFFER FULL")
        assert r["ok"] is False
        assert "buffer" in r["error"].lower()

    def test_nrc_unknown_code(self):
        r = parse_uds_response("7F 22 5A")
        assert r["ok"] is False
        assert r["nrc"] == 0x5A
        assert "unknown" in r["nrc_desc"]

    def test_raw_preserved(self):
        r = parse_uds_response("NO DATA")
        assert r["raw"] == "NO DATA"


class TestRequestEchoStripping:
    """An echoed request is recognised by equality with what was sent.

    Regression: the BMS mode 01 probe stored ``0100410080000001`` for the reply
    ``410080000001``, because the old first-byte guess (0x10-0x3E) knows no OBD-II
    mode.
    """

    def test_obd_mode_01_echo_stripped_when_request_known(self):
        r = parse_uds_response("0100\n410080000001\n\n>", request="0100")
        assert r["ok"] is True
        assert r["hex"] == "410080000001"

    def test_echo_with_frame_count_digit_stripped(self):
        r = parse_uds_response("010D1\n410D32", request="010D")
        assert r["hex"] == "410D32"
        r = parse_uds_response("010D1\n410D32", request="010D1")
        assert r["hex"] == "410D32"

    def test_a_length_token_spelling_the_request_plus_a_digit_is_kept(self):
        # Echo off: `03A` is the First Frame length of a mode 03 reply, not the `03`
        # request plus a count digit; dropping it lost the declared length.
        payload = "43" + "AB" * (0x3A - 1)
        frames = [payload[:12]] + [payload[i : i + 14] for i in range(12, len(payload), 14)]
        raw = "03A\n" + "\n".join(f"{n:X}:{f}" for n, f in enumerate(frames))
        r = parse_uds_response(raw, request="03")
        assert r["ok"] is True
        assert r["isotp_declared_len"] == 0x3A
        assert len(r["bytes"]) == 0x3A

    def test_an_echo_with_digit_ahead_of_a_multiframe_reply_is_stripped(self):
        raw = "22C00B3\n00A\n0:62C00BAABBCC\n1:DDEEFF00"
        r = parse_uds_response(raw, request="22C00B")
        assert r["ok"] is True
        assert r["isotp_declared_len"] == 10

    def test_echo_spaces_and_case_ignored(self):
        r = parse_uds_response("01 0d\n41 0D 32", request="010D")
        assert r["hex"] == "410D32"

    def test_multiframe_length_token_kept_with_echo_off(self):
        # Echo off: the first line is the ISO-TP length token `017`, whose leading
        # `01` an OBD-II-aware first-byte guess would take for an echo.
        raw = "017\n0:62C00BFFFF00AA\n1:BBCCDDEEFF0011\n2:22334455667788\n3:99AABB"
        r = parse_uds_response(raw, request="22C00B")
        assert r["ok"] is True
        assert r["isotp_declared_len"] == 0x17
        assert r["hex"].startswith("62C00B")

    def test_multiframe_with_echo(self):
        raw = "0902\n014\n0:490201314847\n1:43473535343031\n2:32333435363738"
        r = parse_uds_response(raw, request="0902")
        assert r["ok"] is True
        assert r["hex"].startswith("490201")

    def test_another_requests_echo_marks_the_block_stale(self):
        # `2101` is not this request's echo, so it is kept — and it shows the block is
        # an earlier command's whole exchange, so its reply is not this one's answer.
        r = parse_uds_response("2101\n6101AA", request="2102")
        assert r["ok"] is False
        assert r["error_kind"] == "stale"

    def test_only_the_echo_is_no_data(self):
        r = parse_uds_response("0100", request="0100")
        assert r["ok"] is False
        assert r["error_kind"] == "no_data"

    def test_unknown_request_falls_back_to_the_first_byte_guess(self):
        assert parse_uds_response("2101\n6101AA")["hex"] == "6101AA"
        # Without the request, an OBD-II echo is indistinguishable from a reply, and
        # two lines are never merged into one payload.
        r = parse_uds_response("0100\n410080000001")
        assert r["ok"] is False
        assert r["error_kind"] == "stale"


class TestEchoValidation:
    """SID/DID echo validation catches stale/misaligned frames buffered
    in the ELM327 adapter — the off-by-one we saw during IGPM 0x2F scans
    where a late 6FBC0900 leaked into the next read and got recorded as
    BC0A's response."""

    def test_sid_echo_ok(self):
        r = parse_uds_response(
            "6F BC 0A 00", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A")
        )
        assert r["ok"] is True
        assert r["hex"] == "6FBC0A00"

    def test_sid_mismatch_detected(self):
        """Response SID 0x62 (from a 0x22 request) arriving in a 0x2F read."""
        r = parse_uds_response(
            "62 BC 0A 00", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A")
        )
        assert r["ok"] is False
        assert "SID mismatch" in r["error"]
        assert "0x62" in r["error"]
        assert "0x6F" in r["error"]

    def test_did_mismatch_detected(self):
        """The off-by-one: probe BC0A gets back BC09's late response."""
        r = parse_uds_response(
            "6F BC 09 00", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A")
        )
        assert r["ok"] is False
        assert "Echo mismatch" in r["error"]
        assert "BC09" in r["error"]
        assert "BC0A" in r["error"]

    def test_response_too_short_for_did(self):
        r = parse_uds_response("6F BC", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A"))
        assert r["ok"] is False
        assert "too short" in r["error"].lower()

    def test_garbage_first_byte_rejected(self):
        """The '7E00' anomaly seen in discoveries — not a valid 2F response."""
        r = parse_uds_response("7E 00", expected_sid=0x2F, expected_echo=bytes.fromhex("BB51"))
        assert r["ok"] is False
        # '7E00' parses as 2 bytes; first is 0x7E != 0x6F → SID mismatch
        assert "SID mismatch" in r["error"]

    def test_nrc_with_matching_service_ok(self):
        """NRC 0x7F {SID=0x2F} {0x31} — correctly-addressed NRC for our 0x2F request."""
        r = parse_uds_response("7F 2F 31", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A"))
        assert r["ok"] is False
        assert r["nrc"] == 0x31
        assert r.get("error") is None  # No mismatch error

    def test_nrc_with_wrong_service_rejected(self):
        """NRC 0x7F {SID=0x22} {0x31} arriving in a 0x2F scan — stale frame."""
        r = parse_uds_response("7F 22 31", expected_sid=0x2F, expected_echo=bytes.fromhex("BC0A"))
        assert r["ok"] is False
        assert "NRC echo mismatch" in r["error"]
        # The stale nrc is suppressed so scanner doesn't misclassify as "absent"
        assert "nrc" not in r

    def test_validation_disabled_by_default(self):
        """Without expected_sid, old behaviour preserved — no mismatch check."""
        r = parse_uds_response("6F BC 09 00")
        assert r["ok"] is True
        r = parse_uds_response("7F 22 31")
        assert r["ok"] is False
        assert r["nrc"] == 0x31

    def test_sid_only_without_did(self):
        """expected_sid alone validates SID but not DID echo."""
        r = parse_uds_response("6F BC 99 00", expected_sid=0x2F)
        assert r["ok"] is True  # SID matches, DID not checked


class TestRequestEcho:
    """request_echo derives the (sid, echo bytes) a positive response must repeat."""

    def test_service_21_single_pid(self):
        assert request_echo("2102") == (0x21, b"\x02")

    def test_service_22_did(self):
        assert request_echo("22BC03") == (0x22, b"\xbc\x03")

    def test_kwp_read_ecu_identification(self):
        # ISO 14230-3: the positive response repeats the identificationOption.
        assert request_echo("1A90") == (0x1A, b"\x90")

    def test_a_late_identification_record_is_caught(self):
        echo = request_echo("1A91")
        assert echo is not None
        r = parse_uds_response("5A9041454556", expected_sid=echo[0], expected_echo=echo[1])
        assert r["ok"] is False
        assert r["error_kind"] == "stale"

    def test_spaces_tolerated(self):
        assert request_echo("21 01") == (0x21, b"\x01")

    def test_service_21_multi_pid_not_validated(self):
        # Unusual multi-PID 21 request — we don't validate it.
        assert request_echo("210102") is None

    def test_service_22_multi_did_validated_against_its_first_did(self):
        # A batch response repeats its DIDs in request order, so the first one
        # always follows the SID — validatable however many were asked for. The
        # later DIDs sit at data-dependent offsets and are left to split_multi_did.
        assert request_echo("22BC03BC04") == (0x22, b"\xbc\x03")
        assert request_echo("22BC03BC04BC05") == (0x22, b"\xbc\x03")

    def test_service_22_odd_trailing_byte_not_validated(self):
        # Not a whole number of 2-byte DIDs, so we can't say what it asked for.
        assert request_echo("22BC03BC") is None

    def test_other_service_none(self):
        assert request_echo("1003") is None

    def test_every_one_byte_id_service_of_the_registry_is_validated(self):
        # KWP2000 0x33/0x32 answer 73/72 {LID}: the registry gives them a 1-byte id,
        # so their single-LID reads are validated like 0x21 and 0x1A.
        assert request_echo("3301") == (0x33, b"\x01")
        assert request_echo("3201") == (0x32, b"\x01")
        # A 0x30 request carries its control option after the LID, so it is not a plain read.
        assert request_echo("300A00") is None

    def test_obd_mode_01_and_09_single_pid(self):
        assert request_echo("010D") == (0x01, b"\x0d")
        assert request_echo("0902") == (0x09, b"\x02")

    def test_obd_multi_pid_not_validated(self):
        # A multi-PID reply interleaves PID and data: not a plain echo.
        assert request_echo("010C0D") is None
        assert request_echo("01") is None

    def test_obd_reply_in_the_wrong_slot_is_stale(self):
        sid, echo = request_echo("010D")
        r = parse_uds_response("410C1AF8", expected_sid=sid, expected_echo=echo)
        assert r["ok"] is False
        assert r["error_kind"] == "stale"
        assert parse_uds_response("410D32", expected_sid=sid, expected_echo=echo)["ok"]

    def test_obd_payload_echo_mismatch(self):
        assert payload_echo_mismatch("0100", "410080000001") is None
        # The echo-contaminated BMS probe payload is caught by the capture lint.
        assert "0x01" in payload_echo_mismatch("0100", "0100410080000001")
        assert payload_echo_mismatch("010D", "410C1AF8") is not None

    def test_garbage_none(self):
        assert request_echo("ZZ") is None
        assert request_echo("2") is None


class TestParseServicePidEcho:
    """1-byte service-21 PID echo — the VCU 6101-for-2102 stale-frame bug."""

    def test_pid_echo_ok(self):
        r = parse_uds_response("61 02 F8 F8", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is True

    def test_pid_echo_mismatch_rejected(self):
        # A 2101 response (6101…) arriving for a 2102 request.
        r = parse_uds_response("61 01 FF E0", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is False
        assert "Echo mismatch" in r["error"]
        assert "01" in r["error"]

    def test_an_identity_reply_one_lower_is_rejected(self):
        # Some Hyundai/Kia ECUs answer an unsupported F1xx DID by repeating their previous
        # reply, which often names the DID just below: another DID's reply, not this one's.
        r = parse_uds_response("62 F1 87 00", expected_sid=0x22, expected_echo=b"\xf1\x88")
        assert r["ok"] is False
        assert "Echo mismatch" in r["error"]


class TestPayloadEchoMismatch:
    """The capture lint: cross-check stored pid vs payload SID/identifier echo."""

    def test_match_returns_none(self):
        assert payload_echo_mismatch("2102", "6102F8F800") is None

    def test_pid_mismatch_flagged(self):
        reason = payload_echo_mismatch("2102", "6101FFE000")
        assert reason is not None
        assert "01" in reason and "2102" in reason

    def test_did_mismatch_flagged(self):
        reason = payload_echo_mismatch("22F195", "62F19300")
        assert reason is not None
        assert "F193" in reason

    def test_an_identity_reply_one_lower_is_flagged(self):
        assert payload_echo_mismatch("22F188", "62F18700") is not None

    def test_sid_mismatch_flagged(self):
        reason = payload_echo_mismatch("2102", "7F2131")
        # 0x7F negative response is not treated as an echo mismatch.
        assert reason is None

    def test_non_identifier_request_skipped(self):
        assert payload_echo_mismatch("1003", "5003") is None

    def test_unparseable_payload_skipped(self):
        assert payload_echo_mismatch("2102", "ZZ") is None
        assert payload_echo_mismatch("2102", "") is None

    def test_payload_too_short_for_the_identifier_is_flagged(self):
        reason = payload_echo_mismatch("22F190", "62F1")
        assert reason is not None
        assert "too short" in reason

    def test_wrong_service_is_flagged(self):
        reason = payload_echo_mismatch("2102", "6202AA")
        assert reason is not None
        assert "SID 0x62" in reason and "0x61" in reason


class TestPayloadNotHex:
    """The non-hex payload lint: flag captures that aren't valid UDS byte strings."""

    def test_valid_hex_ok(self):
        assert payload_not_hex("6102F8F800") is None

    def test_valid_hex_with_spaces_ok(self):
        assert payload_not_hex("61 02 F8 F8") is None

    def test_empty_or_none_skipped(self):
        assert payload_not_hex("") is None
        assert payload_not_hex(None) is None

    def test_error_string_flagged(self):
        reason = payload_not_hex("NO DATA")
        assert reason is not None
        assert "not hex" in reason

    def test_free_text_flagged(self):
        reason = payload_not_hex("5001 at attempts 2-4, NO DATA at 5-10")
        assert reason is not None
        assert "not hex" in reason

    def test_mixed_hex_ascii_flagged(self):
        # Part-number transcription: the 'G' breaks pure hex.
        reason = payload_not_hex("62F18791950G7510")
        assert reason is not None
        assert "not hex" in reason
        assert "G" in reason

    def test_odd_length_flagged(self):
        reason = payload_not_hex("6102F")
        assert reason is not None
        assert "odd" in reason

    def test_too_short_flagged(self):
        reason = payload_not_hex("6")
        assert reason is not None
        assert "too short" in reason


class TestIsoTpTruncationGuard:
    """ELM327 multi-frame reads carry a declared ISO-TP length; reject short ones."""

    # Real wire format of a live BCM 22C00B read (declared 0x017 = 23 bytes):
    #   22C00B\r 017 \r 0:62C00BFFFF00 \r 1:00C84F0100C84E \r 2:0100C84D0100C6 \r 3:4D0100AAAAAAAA
    _COMPLETE = "017\n0:62C00BFFFF00\n1:00C84F0100C84E\n2:0100C84D0100C6\n3:4D0100AAAAAAAA"

    def test_complete_multiframe_accepted(self):
        r = parse_uds_response(self._COMPLETE)
        assert r["ok"] is True
        assert r["isotp_declared_len"] == 0x017
        # Reassembled payload is trimmed to the declared length (AA padding dropped),
        # matching what the slcan-tcp client-side ISO-TP path returns.
        assert r["hex"] == "62C00BFFFF0000C84F0100C84E0100C84D0100C64D0100"
        assert len(r["bytes"]) == 0x017

    def test_truncated_multiframe_rejected(self):
        # Drop the last consecutive frame: 8 fewer data bytes than declared.
        truncated = "017\n0:62C00BFFFF00\n1:00C84F0100C84E\n2:0100C84D0100C6"
        r = parse_uds_response(truncated)
        assert r["ok"] is False
        assert "truncated ISO-TP" in r["error"]
        assert "declared 23" in r["error"]

    def test_single_frame_has_no_length_guard(self):
        # A single-frame response carries no declared-length line; guard is a no-op.
        r = parse_uds_response("62 BC 07 08 41 FB CC 00 00 15 00")
        assert r["ok"] is True
        assert "isotp_declared_len" not in r

    def test_multiframe_without_length_line_still_parses(self):
        # slcan-tcp / older adapters may emit only N: frames (no length token).
        r = parse_uds_response("0:6101AABBCCDDEE\n1:FF0011223344556\n2:67788")
        assert r["ok"] is True
        assert "isotp_declared_len" not in r


class TestIsoTpContiguityGuard:
    """A dropped/duplicate/stale ISO-TP frame reorders or gaps the frame counters;
    reject those so a misaligned payload never reaches decoders/captures. A gap
    can be hidden from the declared-length guard by trailing padding, so counter
    contiguity is checked independently. The ELM327 emits frames in transmission
    order with a single hex-digit counter that wraps 0..F,0.."""

    def test_contiguous_frames_accepted(self):
        r = parse_uds_response("0:6101AABBCCDDEE\n1:FF0011223344556\n2:67788")
        assert r["ok"] is True

    def test_out_of_order_arrival_rejected(self):
        # Real ELM327 output is in transmission order; a reordered counter
        # sequence means a stale/dropped frame, not benign reordering.
        r = parse_uds_response("2:67788\n0:6101AABBCCDDEE\n1:FF0011223344556")
        assert r["ok"] is False
        assert "non-contiguous ISO-TP frames" in r["error"]

    def test_missing_middle_frame_rejected(self):
        # Frame counter 2 dropped: [0,1,3] is not contiguous.
        r = parse_uds_response("0:62C00BFFFF00\n1:00C84F0100C84E\n3:4D0100AAAAAAAA")
        assert r["ok"] is False
        assert "non-contiguous ISO-TP frames" in r["error"]
        assert "['0', '1', '3']" in r["error"]

    def test_duplicate_frame_index_rejected(self):
        # A stale frame with a repeated counter: [0,1,1,2].
        r = parse_uds_response(
            "0:62C00BFFFF00\n1:00C84F0100C84E\n1:DEADBEEFDEADBE\n2:0100C84D0100C6"
        )
        assert r["ok"] is False
        assert "non-contiguous ISO-TP frames" in r["error"]

    def test_gap_masked_by_padding_still_rejected(self):
        # Counters [0,1,3] but the declared length (0x017 = 23) is satisfied by
        # the payload bytes present, so the truncation guard would pass — the
        # contiguity check must still fire.
        masked = "017\n0:62C00BFFFF00\n1:00C84F0100C84E\n3:0100C84D0100C6"
        r = parse_uds_response(masked)
        assert r["ok"] is False
        assert "non-contiguous ISO-TP frames" in r["error"]


class TestIsoTpFrameCounterWrap:
    """The ELM327 counter is a SINGLE hex digit that wraps 0..F then repeats, so
    a response with 16+ frames uses A..F and starts over at 0. These must
    reassemble by transmission order (unwrapping the counter), not by sorting the
    printed nibble — sorting collides once the counter wraps."""

    @staticmethod
    def _frames(n: int) -> tuple[str, str]:
        """Build an n-frame ELM327 multiframe body + its expected reassembly.

        Each line carries a distinct 2-byte payload so a misalignment would show;
        the printed counter is ``i mod 16`` in hex (how real hardware numbers it).
        """
        lines, payload = [], ""
        for i in range(n):
            data = f"{i:02X}FF"  # 2 bytes, unique per frame
            lines.append(f"{i & 0xF:X}:{data}")
            payload += data
        return "\n".join(lines), payload

    def test_eighteen_frames_wrap_reassembles(self):
        # 18 frames: counters 0..F,0,1 — the case the old decimal-only regex
        # silently dropped (A..F never matched) and the sort mis-ordered.
        body, expected = self._frames(18)
        r = parse_uds_response(body)
        assert r["ok"] is True
        assert r["hex"] == expected.upper()
        assert len(r["bytes"]) == 18 * 2

    def test_sixteen_frames_exact_wrap_boundary(self):
        # Exactly 16 frames: counters 0..F, no repeat yet.
        body, expected = self._frames(16)
        r = parse_uds_response(body)
        assert r["ok"] is True
        assert r["hex"] == expected.upper()

    def test_broken_after_wrap_rejected(self):
        # 0..F,0,2 — counter 1 dropped after the wrap; must be rejected.
        body, _ = self._frames(18)
        broken = body.rsplit("\n", 1)[0] + "\n2:1111"  # replace the final "1:..." line
        r = parse_uds_response(broken)
        assert r["ok"] is False
        assert "non-contiguous ISO-TP frames" in r["error"]

    def test_a_to_f_counters_matched(self):
        # Direct check that hex counters A..F are parsed (not dropped as before).
        body, expected = self._frames(12)  # counters 0..B, includes A, B
        assert "A:" in body and "B:" in body
        r = parse_uds_response(body)
        assert r["ok"] is True
        assert r["hex"] == expected.upper()
