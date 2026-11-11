"""``canair.cli.main``: a reader that closes stdout early ends the command quietly."""

from __future__ import annotations

import io
import os

import pytest

import canair.cli as cli


@pytest.fixture
def closed_pipe():
    """A real pipe whose reader is gone, as after ``canair … | head -1``.

    Each test swaps it in as ``sys.stdout`` itself, because pytest's capture resets
    ``sys.stdout`` when the test call starts.
    """
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    stream = open(write_fd, "w", buffering=1)
    yield stream
    try:
        stream.close()
    except BrokenPipeError:
        pass


def test_a_closed_stdout_exits_141_without_a_traceback(monkeypatch, closed_pipe):
    def flood(_argv):
        while True:
            print("x" * 80)

    monkeypatch.setattr(cli, "_run", flood)
    monkeypatch.setattr("sys.stdout", closed_pipe)
    assert cli.main([]) == cli.EXIT_BROKEN_PIPE == 141
    # stdout now points at the null device, so the flush at exit cannot fail again.
    os.write(closed_pipe.fileno(), b"after\n")


def test_a_reader_that_left_before_the_final_flush(monkeypatch, closed_pipe):
    def small(_argv):
        print("fits in the buffer", end="")
        return 0

    monkeypatch.setattr(cli, "_run", small)
    monkeypatch.setattr("sys.stdout", closed_pipe)
    assert cli.main([]) == 141


def test_a_broken_pipe_from_a_socket_still_propagates(monkeypatch):
    monkeypatch.setattr("sys.stdout", io.StringIO())

    def device_dropped(_argv):
        raise BrokenPipeError("the device closed the connection")

    monkeypatch.setattr(cli, "_run", device_dropped)
    with pytest.raises(BrokenPipeError, match="device closed"):
        cli.main([])


def test_a_normal_run_keeps_its_exit_code(monkeypatch):
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr(cli, "_run", lambda _argv: 2)
    assert cli.main([]) == 2
