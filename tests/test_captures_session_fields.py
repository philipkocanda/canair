"""`canair captures uds --set-notes` / `--set-transport` — correcting session metadata.

Before these modes a session note could only be changed in the TTY-only ``--step``
TUI, and a session's transport not at all, so a contributor who found recorded
metadata contradicted by the session's own data had no scriptable way to fix it
(and hand-editing ``captures/`` is forbidden).
"""

from __future__ import annotations

import argparse
import json

from canlib.captures import build_query_session, save_session


def _seed(cdir, *sessions: tuple[str, str], transport: str | None = None) -> None:
    for label, notes in sessions:
        session = build_query_session(
            [("0x7EB", "2101", "6101EEFF", "")], label, [], notes, transport=transport
        )
        save_session(session, cdir)


def _sessions(cdir) -> list[dict]:
    (path,) = cdir.glob("*.json")
    return json.loads(path.read_text())["sessions"]


def _field(cdir, key: str) -> dict[str, str | None]:
    return {s["label"]: s.get(key) for s in _sessions(cdir)}


def _run(cdir, editor: str, value: str, **kw) -> int:
    from canlib.capture_store import load_all_captures
    from canlib.commands.captures import session_fields

    return getattr(session_fields, editor)(load_all_captures(cdir), value, **kw)


def _cli(cdir, *argv: str) -> int:
    from canlib.commands import captures as cap

    parser = cap.add_parser(argparse.ArgumentParser().add_subparsers())
    args = parser.parse_args(["uds", *argv, "--dir", str(cdir)])
    return args.func(args)


class TestSetNotes:
    def test_replaces_the_notes(self, tmp_path):
        _seed(tmp_path, ("sweep", "LBC answered regardless"))
        assert _run(tmp_path, "cmd_set_notes", "Only 0x758 answered.", assume_yes=True) == 0
        assert _field(tmp_path, "notes") == {"sweep": "Only 0x758 answered."}

    def test_empty_text_clears_the_field(self, tmp_path):
        _seed(tmp_path, ("sweep", "wrong"))
        assert _run(tmp_path, "cmd_set_notes", "", assume_yes=True) == 0
        assert _field(tmp_path, "notes") == {"sweep": None}

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        assert _run(tmp_path, "cmd_set_notes", "new", dry_run=True, assume_yes=True) == 0
        out = capsys.readouterr().out
        assert "--dry-run" in out
        assert "was: old" in out
        assert _field(tmp_path, "notes") == {"sweep": "old"}

    def test_unchanged_notes_are_not_rewritten(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "same"))
        assert _run(tmp_path, "cmd_set_notes", "same", assume_yes=True) == 0
        assert "0 of 1" in capsys.readouterr().out

    def test_non_tty_without_yes_refuses(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        assert _run(tmp_path, "cmd_set_notes", "new") == 2
        assert "refusing to set notes" in capsys.readouterr().err
        assert _field(tmp_path, "notes") == {"sweep": "old"}

    def test_json_dry_run_emits_rows(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        capsys.readouterr()  # discard the save banner
        assert _run(tmp_path, "cmd_set_notes", "new", dry_run=True, as_json=True) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert (row["recorded"], row["new"], row["will_write"]) == ("old", "new", True)

    def test_an_added_field_lands_before_captures(self, tmp_path):
        _seed(tmp_path, ("sweep", ""))
        assert _run(tmp_path, "cmd_set_notes", "added", assume_yes=True) == 0
        (session,) = _sessions(tmp_path)
        assert list(session)[-1] == "captures"


class TestSetTransport:
    def test_corrects_a_mislabelled_transport(self, tmp_path):
        _seed(tmp_path, ("sweep", ""), transport="slcan-tcp")
        assert _run(tmp_path, "cmd_set_transport", "wican-ws", assume_yes=True) == 0
        assert _field(tmp_path, "transport") == {"sweep": "wican-ws"}
        assert list(_sessions(tmp_path)[0])[-1] == "captures"

    def test_an_unknown_transport_is_refused(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", ""), transport="slcan-tcp")
        assert _run(tmp_path, "cmd_set_transport", "carrier-pigeon", assume_yes=True) == 2
        assert "not a known transport" in capsys.readouterr().err
        assert _field(tmp_path, "transport") == {"sweep": "slcan-tcp"}

    def test_import_is_a_known_transport(self, tmp_path):
        _seed(tmp_path, ("pasted", ""))
        assert _run(tmp_path, "cmd_set_transport", "import", assume_yes=True) == 0
        assert _field(tmp_path, "transport") == {"pasted": "import"}

    def test_empty_value_clears_the_field(self, tmp_path):
        _seed(tmp_path, ("sweep", ""), transport="slcan-tcp")
        assert _run(tmp_path, "cmd_set_transport", "", assume_yes=True) == 0
        assert _field(tmp_path, "transport") == {"sweep": None}


class TestSessionEditorCli:
    def test_scope_selects_which_sessions_change(self, tmp_path):
        _seed(tmp_path, ("discovery sweep", "wrong"), ("pid sweep", "keep me"))
        rc = _cli(tmp_path, "--set-notes", "fixed", "--label", "discovery", "--yes")
        assert rc == 0
        assert _field(tmp_path, "notes") == {"discovery sweep": "fixed", "pid sweep": "keep me"}

    def test_set_transport_through_the_cli(self, tmp_path):
        _seed(tmp_path, ("discovery sweep", ""), ("pid sweep", ""), transport="slcan-tcp")
        rc = _cli(tmp_path, "--set-transport", "wican-ws", "--label", "discovery", "--yes")
        assert rc == 0
        assert _field(tmp_path, "transport") == {
            "discovery sweep": "wican-ws",
            "pid sweep": "slcan-tcp",
        }

    def test_bare_invocation_is_refused(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"), transport="slcan-tcp")
        assert _cli(tmp_path, "--set-notes", "new", "--yes") == 2
        assert _cli(tmp_path, "--set-transport", "wican-ws", "--yes") == 2
        err = capsys.readouterr().err
        assert "--set-notes requires a scope filter" in err
        assert "--set-transport requires a scope filter" in err
        assert _field(tmp_path, "notes") == {"sweep": "old"}
        assert _field(tmp_path, "transport") == {"sweep": "slcan-tcp"}
