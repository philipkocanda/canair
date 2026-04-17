"""Tests for canlib.modes.monitor — rendering and helper functions."""

from canlib.modes.monitor import _bytes_to_ascii, _render_hex_line, _render_results


class TestBytesToAscii:
    def test_printable(self):
        assert _bytes_to_ascii("48656C6C6F") == "Hello"

    def test_non_printable_replaced(self):
        # 0x00='.', 0x01='.', 0x7f=DEL='.', 0x80='.', 0xff='.'
        assert _bytes_to_ascii("00017F80FF") == "....."

    def test_non_printable_dots(self):
        # 0x00, 0x01, 0x1f are all non-printable
        assert _bytes_to_ascii("00011F") == "..."

    def test_mixed(self):
        # "62 C0 0B 41" -> 'b' is printable, 0xC0/0x0B not, 'A' is printable
        assert _bytes_to_ascii("62C00B41") == "b..A"

    def test_empty(self):
        assert _bytes_to_ascii("") == ""

    def test_space_is_printable(self):
        assert _bytes_to_ascii("20") == " "

    def test_tilde_is_printable(self):
        assert _bytes_to_ascii("7E") == "~"

    def test_del_is_not_printable(self):
        assert _bytes_to_ascii("7F") == "."


class TestRenderHexLine:
    """Test _render_hex_line Rich Text output."""

    def test_unmapped_shows_ascii(self):
        t = _render_hex_line("62B00140", [], unmapped=True)
        text = t.plain
        assert "62 B0 01 40" in text
        assert "b..@" in text
        assert "(4 B)" in text

    def test_unmapped_no_params(self):
        t = _render_hex_line("AABB", [], unmapped=False)
        text = t.plain
        assert "AA BB" in text
        assert "(2 B)" in text

    def test_mapped_shows_byte_count(self):
        params = [("SOC", 50.0, "%", "B02/2", None, True)]
        t = _render_hex_line("62010064", params, unmapped=False)
        text = t.plain
        assert "(4 B)" in text

    def test_empty_hex(self):
        t = _render_hex_line("", [], unmapped=True)
        text = t.plain
        assert "(0 B)" in text


class TestRenderResults:
    """Test _render_results composites."""

    def _make_pid_result(
        self, pid="2101", params=None, raw_hex="610100", error=None, unmapped=False
    ):
        return {
            "pid": pid,
            "params": params or [],
            "raw_hex": raw_hex,
            "error": error,
            "unmapped": unmapped,
        }

    def test_empty_queries(self):
        t = _render_results([], verbose=False, cycle=1, elapsed=0.5, interval=5.0)
        text = t.plain
        assert "cycle 1" in text
        assert "Ctrl+C" in text

    def test_cycle_header(self):
        t = _render_results([], verbose=False, cycle=42, elapsed=1.2, interval=3.0)
        text = t.plain
        assert "cycle 42" in text
        assert "1.2s" in text
        assert "3.0s" in text

    def test_ecu_label_shown(self):
        results = [("BMS (0x7E4)", [self._make_pid_result()])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "BMS (0x7E4)" in t.plain

    def test_pid_shown(self):
        results = [("BMS (0x7E4)", [self._make_pid_result(pid="2101")])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "2101" in t.plain

    def test_error_shown(self):
        entry = self._make_pid_result(error="NRC 0x12")
        results = [("BMS (0x7E4)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "NRC 0x12" in t.plain

    def test_unmapped_label(self):
        entry = self._make_pid_result(unmapped=True)
        results = [("BCM (0x7A0)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "(unmapped)" in t.plain

    def test_params_rendered(self):
        params = [("SOC_BMS", 50.0, "%", "B09/2", None, True, "")]
        entry = self._make_pid_result(params=params, raw_hex="6101000000000000006400")
        results = [("BMS (0x7E4)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        text = t.plain
        assert "SOC_BMS" in text
        assert "50 %" in text

    def test_params_verbose_shows_expression(self):
        params = [("SOC_BMS", 50.0, "%", "B09/2", None, True, "")]
        entry = self._make_pid_result(params=params, raw_hex="6101000000000000006400")
        results = [("BMS (0x7E4)", [entry])]
        t = _render_results(results, verbose=True, cycle=1, elapsed=0.1, interval=5.0)
        assert "B09/2" in t.plain

    def test_params_verified_mark(self):
        params_v = [("SOC_BMS", 50.0, "%", "B09/2", None, True, "")]
        params_u = [("UNKNOWN", 1.0, "", "B03", None, False, "")]
        entry_v = self._make_pid_result(params=params_v, raw_hex="610100")
        entry_u = self._make_pid_result(pid="2102", params=params_u, raw_hex="610200")
        results = [("BMS (0x7E4)", [entry_v, entry_u])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "✓" in t.plain
        assert "?" in t.plain

    def test_param_error_shown(self):
        params = [("BAD_PARAM", None, "", "B99/0", "division by zero", False, "")]
        entry = self._make_pid_result(params=params, raw_hex="610100")
        results = [("BMS (0x7E4)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "division by zero" in t.plain

    def test_display_field(self):
        params = [
            ("PREHEAT_TIME", 480.0, "min", "B07", None, True, "f'{int(v)//60:02d}:{int(v)%60:02d}'")
        ]
        entry = self._make_pid_result(params=params, raw_hex="610100000000000001E0")
        results = [("BCM (0x7A0)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "480 min (08:00)" in t.plain

    def test_multiple_ecus(self):
        r1 = [self._make_pid_result(pid="2101")]
        r2 = [self._make_pid_result(pid="BC03")]
        results = [("BMS (0x7E4)", r1), ("IGPM (0x770)", r2)]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        text = t.plain
        assert "BMS (0x7E4)" in text
        assert "IGPM (0x770)" in text

    def test_skip_empty_ecu(self):
        results = [("EMPTY (0x000)", [])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "EMPTY" not in t.plain

    def test_decode_fallback(self):
        entry = {
            "pid": "22B001",
            "params": [],
            "raw_hex": "62B001FF",
            "error": None,
            "unmapped": True,
            "decode": "raw: FF",
        }
        results = [("BCM (0x7A0)", [entry])]
        t = _render_results(results, verbose=False, cycle=1, elapsed=0.1, interval=5.0)
        assert "raw: FF" in t.plain

    def test_changed_pid_shows_indicator(self):
        """Changed PID between cycles shows ● indicator."""
        entry = self._make_pid_result(pid="2101", raw_hex="610100")
        results = [("BMS (0x7E4)", [entry])]
        prev = {("BMS (0x7E4)", "2101"): "610199"}  # different from current
        t = _render_results(
            results, verbose=False, cycle=2, elapsed=0.1, interval=5.0, prev_hex=prev
        )
        assert "●" in t.plain

    def test_unchanged_pid_no_indicator(self):
        """Unchanged PID shows no ● indicator."""
        entry = self._make_pid_result(pid="2101", raw_hex="610100")
        results = [("BMS (0x7E4)", [entry])]
        prev = {("BMS (0x7E4)", "2101"): "610100"}  # same as current
        t = _render_results(
            results, verbose=False, cycle=2, elapsed=0.1, interval=5.0, prev_hex=prev
        )
        assert "●" not in t.plain

    def test_first_cycle_no_indicator(self):
        """First cycle never shows change indicator even with prev_hex."""
        entry = self._make_pid_result(pid="2101", raw_hex="610100")
        results = [("BMS (0x7E4)", [entry])]
        prev = {("BMS (0x7E4)", "2101"): "610199"}
        t = _render_results(
            results, verbose=False, cycle=1, elapsed=0.1, interval=5.0, prev_hex=prev
        )
        assert "●" not in t.plain

    def test_new_pid_no_indicator(self):
        """PID not in prev_hex (first time seen) shows no indicator."""
        entry = self._make_pid_result(pid="2101", raw_hex="610100")
        results = [("BMS (0x7E4)", [entry])]
        prev = {}  # never seen before
        t = _render_results(
            results, verbose=False, cycle=2, elapsed=0.1, interval=5.0, prev_hex=prev
        )
        assert "●" not in t.plain
