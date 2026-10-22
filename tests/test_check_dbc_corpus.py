"""Smoke test for scripts/check_dbc_corpus.py on tiny synthetic DBCs (no network).

The checker is opt-in and normally runs over a fetched corpus, so without this its
comparison logic would only ever run by hand.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cantools.database import Database, Message, Signal
from cantools.database.conversion import BaseConversion

import canlib.profile as profile
from canlib.commands.export import _dbc_specifics

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_dbc_corpus.py"


@pytest.fixture
def checker(monkeypatch):
    # The checker drives the real CLI, which sets the active profile.
    monkeypatch.setattr(profile, "_active", None)
    spec = importlib.util.spec_from_file_location("check_dbc_corpus", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sig(name, start=0, length=8, **conv):
    order = conv.pop("byte_order", "little_endian")
    return Signal(
        name=name,
        start=start,
        length=length,
        byte_order=order,
        conversion=BaseConversion.factory(**conv),
    )


def _write(path: Path, *messages: Message) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(messages=list(messages), strict=False, dbc_specifics=_dbc_specifics(messages))
    path.write_text(db.as_dbc_string())
    return path


def _msg(frame_id, *signals, length=8, **kw):
    return Message(
        frame_id=frame_id, name=f"M{frame_id:X}", length=length, signals=list(signals),
        strict=False, **kw,
    )  # fmt: skip


def test_rich_dbc_round_trips_clean(checker, tmp_path, capsys):
    dbc = _write(
        tmp_path / "car.dbc",
        _msg(0x100, _sig("F", 0, 32, is_float=True), _sig("G", 32, 4, choices={1: "P"})),
        _msg(0x123, _sig("E"), is_extended_frame=True),
        _msg(0x1DA, _sig("RPM", 39, 16, byte_order="big_endian", scale=0.5)),
        _msg(0x300, _sig("FD", 88, 8), length=12, is_fd=True),
        _msg(0x301, _sig("SMALL_FD"), is_fd=True),
    )
    assert checker.main([str(dbc), "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "problems: 0 in 0 DBC(s)" in out
    assert "float signals: 1" in out


def test_same_stem_dbcs_use_separate_buses(checker, tmp_path, capsys):
    # Merged into one bus, the second file's 2-byte length would cut off the first's
    # signal and the import would fail.
    a = _write(tmp_path / "a" / "car.dbc", _msg(0x100, _sig("A", 32, 8)))
    b = _write(tmp_path / "b" / "car.dbc", _msg(0x100, _sig("B"), length=2))
    assert checker.main([str(a), str(b)]) == 0
    assert "problems: 0 in 0 DBC(s)" in capsys.readouterr().out


def test_id_clash_is_skipped_not_failed(checker, tmp_path, capsys):
    dbc = _write(
        tmp_path / "clash.dbc",
        _msg(0x7, _sig("A")),
        _msg(0x7, _sig("B"), is_extended_frame=True),
    )
    assert checker.main([str(dbc)]) == 0
    out = capsys.readouterr().out
    assert "skipped  clash.dbc" in out and "share this number" in out


def test_fetch_with_explicit_paths_warns(checker, tmp_path, capsys):
    dbc = _write(tmp_path / "car.dbc", _msg(0x1, _sig("A")))
    assert checker.main([str(dbc), "--fetch"]) == 0  # never fetches with explicit DBCs
    assert "--fetch ignored" in capsys.readouterr().err


def test_index_keys_frame_format(checker):
    std, ext = _msg(0x5, _sig("A")), _msg(0x5, _sig("A"), is_extended_frame=True)
    assert checker._key(std) != checker._key(ext)
    assert checker._label(ext) == "0x5 (extended)"
