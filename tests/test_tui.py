"""Tests for canlib.tui — small shared terminal helpers."""

import os

from canlib.tui import read_key_raw, terminal_columns, terminal_lines


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
