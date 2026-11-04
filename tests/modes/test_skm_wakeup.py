"""Tests for canlib.modes.skm_wakeup — parsing helpers."""

from canlib.modes.skm_wakeup import parse_bcm_b003, parse_igpm_bc03

# ── parse_igpm_bc03 ──────────────────────────────────────────────────────────


class TestParseIgpmBc03:
    """Test IGPM BC03 ignition/ACC byte extraction."""

    def test_acc_on_with_fob(self):
        """ACC mode confirmed — B4=0x20 (bit 5 set)."""
        # Real capture: ACC mode with fob, 2026-04-15
        r = parse_igpm_bc03("62BC03FDEE3C7320600000")
        assert r["ok"] is True
        assert r["acc"] is True
        assert r["ign_byte"] == 0x20
        assert r["ign2_byte"] == 0x60

    def test_no_acc_deep_sleep(self):
        """Deep sleep, no fob — B4=0x00."""
        # Real capture: deep sleep no fob, 2026-04-16
        r = parse_igpm_bc03("62BC03FDEE3C730A000000AAAA")
        assert r["ok"] is True
        assert r["acc"] is False
        assert r["ign_byte"] == 0x0A
        assert r["ign2_byte"] == 0x00

    def test_no_acc_off_state(self):
        """Off state — B4=0x00, B5=0x00."""
        r = parse_igpm_bc03("62BC03FDEE3C7300000000")
        assert r["ok"] is True
        assert r["acc"] is False
        assert r["ign_byte"] == 0x00

    def test_acc2_lights_on(self):
        """ACC2 with DRL+tail lights — B4=0x0A, but bit 5 not set = no ACC."""
        # Real capture: ACC2 mode with lights, 2026-04-15
        r = parse_igpm_bc03("62BC03FDEE3C730A600C00AAAA")
        assert r["ok"] is True
        assert r["acc"] is False  # 0x0A has bit 5 clear
        assert r["ign_byte"] == 0x0A

    def test_multiframe_prefixes_stripped(self):
        """Real multi-frame response with 0: 1: prefixes."""
        raw = "0:62BC03FDEE3C\n1:7320600000AAAA"
        r = parse_igpm_bc03(raw)
        assert r["ok"] is True
        assert r["acc"] is True
        assert r["ign_byte"] == 0x20

    def test_spaces_in_response(self):
        """Response with spaces between bytes."""
        r = parse_igpm_bc03("62 BC 03 FD EE 3C 73 20 60 00 00")
        assert r["ok"] is True
        assert r["acc"] is True

    def test_no_62bc03_in_response(self):
        """NO DATA or NRC response."""
        r = parse_igpm_bc03("NO DATA")
        assert r["ok"] is False
        assert r["acc"] is False
        assert "No 62BC03" in r["error"]

    def test_response_too_short(self):
        """Truncated response — less than 6 data bytes."""
        r = parse_igpm_bc03("62BC03FDEE3C73")
        assert r["ok"] is False
        assert "Too short" in r["error"]

    def test_charging_no_acc(self):
        """Charging state — B4=0x00, B5=0x60."""
        # Real capture: charging, 2026-04-15
        r = parse_igpm_bc03("62BC03FDEE3C7300600000AAAA")
        assert r["ok"] is True
        assert r["acc"] is False
        assert r["ign_byte"] == 0x00
        assert r["ign2_byte"] == 0x60


# ── parse_bcm_b003 ──────────────────────────────────────────────────────────


class TestParseBcmB003:
    """Test BCM B003 power mode byte extraction."""

    def test_acc_state(self):
        """ACC mode — byte 6 = 0x09."""
        # Real capture: ACC only, 2026-04-15
        r = parse_bcm_b003("62B003BF8B8000973D09B8F9F8F9F73DF800000000000000AAAAAA")
        assert r["ok"] is True
        assert r["power_byte"] == 0x09
        assert r["state"] == "ACC?"

    def test_acc_ign1_state(self):
        """ACC+IGN1 mode — byte 6 = 0x0A."""
        # Real capture: ACC+IGN1, 2026-04-15
        r = parse_bcm_b003("62B003BF8B8000973D0AB8F9F8F9F73DF800000000000000AAAAAA")
        assert r["ok"] is True
        assert r["power_byte"] == 0x0A
        assert r["state"] == "ACC+IGN1?"

    def test_deep_sleep_f5(self):
        """Deep sleep state — byte 6 = 0xF5."""
        # Real capture: deep sleep, 2026-04-16
        r = parse_bcm_b003("62B003BF8B8000B73DF59CF9F8F9F83DF800000000000000AAAAAA")
        assert r["ok"] is True
        assert r["power_byte"] == 0xF5
        assert r["state"] == "OFF/sleep?"

    def test_unknown_state(self):
        """Unknown byte value — state should be 'unknown'."""
        r = parse_bcm_b003("62B003BF8B8000943DFFBCF9F8F9F73DF800000000000000AAAAAA")
        assert r["ok"] is True
        assert r["power_byte"] == 0xFF
        assert r["state"] == "unknown"

    def test_multiframe_prefixes(self):
        """Real multi-frame response with prefixes."""
        raw = "0:62B003BF8B80\n1:00973D09B8F9F8\n2:F9F73DF8000000\n3:00000000AAAAAA"
        r = parse_bcm_b003(raw)
        assert r["ok"] is True
        assert r["power_byte"] == 0x09

    def test_no_62b003(self):
        """NO DATA response."""
        r = parse_bcm_b003("NO DATA")
        assert r["ok"] is False
        assert "No 62B003" in r["error"]

    def test_response_too_short(self):
        """Truncated response — less than 7 data bytes."""
        r = parse_bcm_b003("62B003BF8B800097")
        assert r["ok"] is False
        assert "Too short" in r["error"]

    def test_deep_sleep_wake_09(self):
        """Deep sleep after wake — byte 6 = 0x09 (ambiguous with ACC)."""
        # Real capture: deep sleep woken via 1001, 2026-04-16
        r = parse_bcm_b003("62B003BF8B8000943D09BCF9F8F9F73DF800000000000000AAAAAA")
        assert r["ok"] is True
        assert r["power_byte"] == 0x09
        # This is the ambiguity — 0x09 in both sleep and ACC
