"""Tests for canair.core.terminal — the terminal's size."""

import os

from canair.core.terminal import terminal_columns, terminal_lines


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
