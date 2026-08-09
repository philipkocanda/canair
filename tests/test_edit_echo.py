"""The shared authoring-confirmation line (:mod:`canlib.commands._edit_echo`).

Two behaviours are load-bearing enough to pin: the confirmation names the *full*
path (a bare ``bms.yaml`` does not say which profile got the edit, and canair
resolves several), and an edit that landed in an install snapshot says so, because
the next reinstall erases it.
"""

from pathlib import Path

from canlib.commands._edit_echo import echo_edit, edit_line


def _plain(text: str) -> str:
    """Strip ANSI escapes so assertions read on content, not colour."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestEditLine:
    def test_names_the_full_path_not_the_file_name(self, tmp_path):
        path = tmp_path / "profiles" / "ioniq-2017" / "ecus" / "bms.yaml"
        line = _plain(edit_line("BMS 2101 SOC", path))
        assert "✓ BMS 2101 SOC" in line
        assert f"({path})" in line
        assert "(bms.yaml)" not in line


class TestEchoEdit:
    def test_quiet_for_an_ordinary_profile(self, tmp_path, capsys):
        echo_edit("BMS 2101 SOC", tmp_path / "ecus" / "bms.yaml")
        out = _plain(capsys.readouterr().out)
        assert "✓ BMS 2101 SOC" in out
        assert "warning" not in out

    def test_warns_when_the_edit_landed_in_an_install_snapshot(self, tmp_path, capsys):
        path = (
            tmp_path
            / "uv"
            / "tools"
            / "canair"
            / "lib"
            / "python3.12"
            / "site-packages"
            / "profiles"
            / "ioniq-2017"
            / "ecus"
            / "bms.yaml"
        )
        path.parent.mkdir(parents=True)
        echo_edit("BMS 2101 SOC", path)
        out = _plain(capsys.readouterr().out)
        assert "✓ BMS 2101 SOC" in out
        assert "uv tool install snapshot" in out
        assert "A reinstall" in out


class TestAuthoringCommandsUseIt:
    """Every definition editor must route its confirmation through the helper.

    A command that hand-rolls the line drifts back to printing ``path.name``,
    which is the defect this helper exists to close — so pin the wiring rather
    than each command's output.
    """

    def test_no_authoring_command_prints_a_bare_file_name(self):
        root = Path(__file__).resolve().parent.parent / "canlib" / "commands"
        for name in ("pids.py", "signals.py", "states.py", "groups.py", "ecu.py"):
            text = (root / name).read_text()
            assert "echo_edit" in text, f"{name} does not use the shared edit echo"
            for marker in ("({fpath.name})", "({path.name})", "'(' + path.name"):
                assert marker not in text, f"{name} still prints a bare file name"
