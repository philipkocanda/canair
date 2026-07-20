"""Tests for `canair pids` CLI orchestration (snapshot -> edit -> validate gate)."""


import pytest

from canlib.commands import pids as cli


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "e.yaml").write_text(
        "TESTECU:\n  tx_id: 0x700\n  pids:\n    2101:\n      parameters: {}\n"
    )
    return tmp_path


def test_guarded_commits_when_gate_passes(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"
    monkeypatch.setattr(cli, "_schema_validate", lambda p: (True, ""))
    cli._guarded("TESTECU", pids_dir, lambda: fp.write_text(fp.read_text() + "# ok\n"),
                 validate=True)
    assert "# ok" in fp.read_text()


def test_guarded_rolls_back_when_gate_fails(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"
    original = fp.read_text()
    monkeypatch.setattr(cli, "_schema_validate", lambda p: (False, "  ERROR: bad"))
    with pytest.raises(SystemExit):
        cli._guarded("TESTECU", pids_dir, lambda: fp.write_text("CORRUPTED"), validate=True)
    assert fp.read_text() == original  # reverted


def test_guarded_skips_gate_when_disabled(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"

    def _boom(_):
        raise AssertionError("validator should not run when validate=False")

    monkeypatch.setattr(cli, "_schema_validate", _boom)
    cli._guarded("TESTECU", pids_dir, lambda: fp.write_text("whatever"), validate=False)
    assert fp.read_text() == "whatever"
