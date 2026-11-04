"""Tests for canlib.tui — small shared terminal helpers."""

import os

from canlib.tui import key_to_action, read_key_raw, terminal_columns, terminal_lines


class TestTerminalSize:
    def test_lines_returns_int(self):
        assert isinstance(terminal_lines(), int)
        assert terminal_lines() > 0

    def test_columns_returns_int(self):
        assert isinstance(terminal_columns(), int)
        assert terminal_columns() > 0

    def test_fallback_used_without_tty(self, monkeypatch):
        # With no real terminal, get_terminal_size falls back to our defaults.
        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.delenv("LINES", raising=False)

        import shutil

        monkeypatch.setattr(
            shutil, "get_terminal_size", lambda fallback: os.terminal_size(fallback)
        )
        assert terminal_lines(default=42) == 42
        assert terminal_columns(default=137) == 137


class TestReadKeyRaw:
    def test_reads_bytes_from_fd(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\x1b[A")  # up-arrow escape sequence
            assert read_key_raw(r) == "\x1b[A"
        finally:
            os.close(r)
            os.close(w)

    def test_decodes_utf8_and_ignores_errors(self):
        r, w = os.pipe()
        try:
            os.write(w, b"q")
            assert read_key_raw(r) == "q"
        finally:
            os.close(r)
            os.close(w)


class TestKeyToAction:
    def test_select_keys(self):
        assert key_to_action("\r") == "select"
        assert key_to_action("\n") == "select"

    def test_cancel_keys(self):
        for key in ("q", "\x1b", "\x1b\x1b", "\x03"):
            assert key_to_action(key) == "cancel"

    def test_navigation_keys(self):
        assert key_to_action("\x1b[A") == "up"
        assert key_to_action("\x1bOA") == "up"
        assert key_to_action("k") == "up"
        assert key_to_action("\x1b[B") == "down"
        assert key_to_action("\x1bOB") == "down"
        assert key_to_action("j") == "down"
        assert key_to_action("\x1b[H") == "home"
        assert key_to_action("g") == "home"
        assert key_to_action("\x1b[F") == "end"
        assert key_to_action("G") == "end"

    def test_unknown_key_ignored(self):
        assert key_to_action("x") == ""
        assert key_to_action("") == ""
