"""The rewriting status line: stderr only, and only on a terminal."""

from __future__ import annotations

import io

import pytest

from canair.profile import active
from canair.render.progress import ProgressLine


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_writes_nothing_when_the_stream_is_not_a_terminal():
    stream = io.StringIO()
    line = ProgressLine(stream)
    line.update("  … 16/256")
    line.clear()
    assert stream.getvalue() == ""
    assert not line.enabled


def test_rewrites_one_line_and_clears_it_on_a_terminal():
    stream = _Tty()
    line = ProgressLine(stream)
    line.update("  … 16/256 (6%)")
    line.update("  … 32/256")
    line.clear()
    out = stream.getvalue()
    assert out.startswith("\r  … 16/256 (6%)")
    assert "\r" + "  … 32/256".ljust(len("  … 16/256 (6%)")) in out  # padded over the longer text
    assert out.endswith("\r" + " " * 15 + "\r")


def test_clear_without_an_update_prints_nothing():
    stream = _Tty()
    ProgressLine(stream).clear()
    assert stream.getvalue() == ""


def test_defaults_to_stderr(monkeypatch):
    stream = _Tty()
    monkeypatch.setattr("sys.stderr", stream)
    ProgressLine().update("x")
    assert "x" in stream.getvalue()


@pytest.mark.asyncio
async def test_discover_progress_never_reaches_stdout(capsys, monkeypatch, tmp_path):
    """A 16-address sweep that finds nothing draws a counter: stderr, and only on a tty."""
    from canair import profile
    from canair.ecus.pids import clear_cache
    from canair.live.discover import physical as disc
    from tests.support.fakes import FakeTerminal

    saved = profile._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    profile.set_active(str(root))
    clear_cache()
    try:
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        term = FakeTerminal(default={"ok": False, "error": "NO DATA", "raw": "NO DATA"})
        await disc.mode_discover(
            term, (0x7E0, 0x7EF), verbose=False, as_json=False, delay=0.0, profile=active()
        )
    finally:
        profile._active = saved
        clear_cache()
    captured = capsys.readouterr()
    assert "\r" not in captured.out and "\r" not in captured.err  # captured streams: no tty
