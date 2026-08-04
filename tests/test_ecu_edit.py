"""Tests for `canair ecu <ECU> edit` (open the ECU's ecus/ YAML in $EDITOR).

`edit` is a human escape hatch: it opens $EDITOR on the ECU's `ecus/<name>.yaml`
for edits the surgical `canair pids` subcommands don't reach. It is TTY-only by
design so agents can't drive it (they must use the validated editors) — and it
re-validates the edited file after the editor exits.
"""

from __future__ import annotations

import pytest

from canlib import profile
from canlib.commands.ecu import cmd_edit
from canlib.ecus_edit import find_ecu_file_by_tx, register_ecu
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    from canlib import config

    saved = profile._active
    clear_cache()
    config.load_config.cache_clear()
    yield
    profile._active = saved
    clear_cache()
    config.load_config.cache_clear()


def _mk_profile(tmp_path, name="prof"):
    root = tmp_path / name
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    return root


def _activate(root):
    prof = profile.Profile(name=root.name, root=root)
    profile._active = prof
    clear_cache()
    return prof


class TestFindEcuFile:
    def test_finds_registered_file(self, tmp_path):
        root = _mk_profile(tmp_path)
        register_ecu(0x7C6, name="CLU", ecus_dir=root / "ecus")
        _activate(root)
        path = find_ecu_file_by_tx(0x7C6)
        assert path is not None
        assert path.name == "clu.yaml"

    def test_missing_returns_none(self, tmp_path):
        root = _mk_profile(tmp_path)
        _activate(root)
        assert find_ecu_file_by_tx(0x7C6) is None


class TestEcuEditTtyGuard:
    def test_refuses_without_tty(self, tmp_path, capsys, monkeypatch):
        root = _mk_profile(tmp_path)
        register_ecu(0x7C6, name="CLU", ecus_dir=root / "ecus")
        _activate(root)
        # Non-interactive stdin/stdout (pytest capture) — must refuse.
        rc = cmd_edit({"name": "CLU"}, 0x7C6)
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires an interactive terminal" in err
        assert "canair pids" in err


class TestEcuEditInteractive:
    def _force_tty(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def test_opens_editor_then_validates(self, tmp_path, monkeypatch):
        root = _mk_profile(tmp_path)
        register_ecu(0x7C6, name="CLU", ecus_dir=root / "ecus")
        _activate(root)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "true")

        opened: list[str] = []

        def fake_call(cmd):
            opened.append(cmd[-1])
            return 0

        monkeypatch.setattr("subprocess.call", fake_call)
        rc = cmd_edit({"name": "CLU"}, 0x7C6)
        assert rc == 0
        assert opened and opened[0].endswith("clu.yaml")

    def test_unknown_file_errors(self, tmp_path, capsys, monkeypatch):
        root = _mk_profile(tmp_path)
        _activate(root)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "true")
        rc = cmd_edit({"name": "CLU"}, 0x7C6)
        assert rc == 1
        assert "No ecus/ file found" in capsys.readouterr().err

    def test_editor_nonzero_skips_validation(self, tmp_path, capsys, monkeypatch):
        root = _mk_profile(tmp_path)
        register_ecu(0x7C6, name="CLU", ecus_dir=root / "ecus")
        _activate(root)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "false")
        monkeypatch.setattr("subprocess.call", lambda cmd: 3)
        rc = cmd_edit({"name": "CLU"}, 0x7C6)
        assert rc == 3
        assert "skipping validation" in capsys.readouterr().out
