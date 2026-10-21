"""`canair captures uds --set-notes TEXT` — correcting a recorded session's notes.

Before this mode a session note could only be changed in the TTY-only ``--step``
TUI, so a contributor who found a recorded note contradicted by the session's own
data had no scriptable way to fix it (and hand-editing ``captures/`` is
forbidden).
"""

from __future__ import annotations

import argparse
import json

from canlib.captures import build_query_session, save_session


def _seed(cdir, *sessions: tuple[str, str]) -> None:
    for label, notes in sessions:
        save_session(
            build_query_session([("0x7EB", "2101", "6101EEFF", "")], label, [], notes), cdir
        )


def _notes(cdir) -> dict[str, str | None]:
    (path,) = cdir.glob("*.json")
    return {s["label"]: s.get("notes") for s in json.loads(path.read_text())["sessions"]}


def _run(cdir, notes_arg: str, **kw) -> int:
    from canlib.capture_store import load_all_captures
    from canlib.commands.captures.set_notes import cmd_set_notes

    return cmd_set_notes(load_all_captures(cdir), notes_arg, **kw)


def _cli(cdir, *argv: str) -> int:
    from canlib.commands import captures as cap

    parser = cap.add_parser(argparse.ArgumentParser().add_subparsers())
    args = parser.parse_args(["uds", *argv, "--dir", str(cdir)])
    return args.func(args)


class TestCmdSetNotes:
    def test_replaces_the_notes(self, tmp_path):
        _seed(tmp_path, ("sweep", "LBC answered regardless"))
        assert _run(tmp_path, "Only 0x758 answered.", assume_yes=True) == 0
        assert _notes(tmp_path) == {"sweep": "Only 0x758 answered."}

    def test_empty_text_clears_the_field(self, tmp_path):
        _seed(tmp_path, ("sweep", "wrong"))
        assert _run(tmp_path, "", assume_yes=True) == 0
        assert _notes(tmp_path) == {"sweep": None}

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        assert _run(tmp_path, "new", dry_run=True, assume_yes=True) == 0
        out = capsys.readouterr().out
        assert "--dry-run" in out
        assert "was: old" in out
        assert _notes(tmp_path) == {"sweep": "old"}

    def test_unchanged_notes_are_not_rewritten(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "same"))
        assert _run(tmp_path, "same", assume_yes=True) == 0
        assert "0 of 1" in capsys.readouterr().out

    def test_non_tty_without_yes_refuses(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        assert _run(tmp_path, "new") == 2
        assert "refusing to set notes" in capsys.readouterr().err
        assert _notes(tmp_path) == {"sweep": "old"}

    def test_json_dry_run_emits_rows(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        capsys.readouterr()  # discard the save banner
        assert _run(tmp_path, "new", dry_run=True, as_json=True) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert (row["recorded"], row["new_notes"], row["will_write"]) == ("old", "new", True)


class TestSetNotesCli:
    def test_scope_selects_which_sessions_change(self, tmp_path):
        _seed(tmp_path, ("discovery sweep", "wrong"), ("pid sweep", "keep me"))
        rc = _cli(tmp_path, "--set-notes", "fixed", "--label", "discovery", "--yes")
        assert rc == 0
        assert _notes(tmp_path) == {"discovery sweep": "fixed", "pid sweep": "keep me"}

    def test_bare_invocation_is_refused(self, tmp_path, capsys):
        _seed(tmp_path, ("sweep", "old"))
        assert _cli(tmp_path, "--set-notes", "new", "--yes") == 2
        assert "--set-notes requires a scope filter" in capsys.readouterr().err
        assert _notes(tmp_path) == {"sweep": "old"}
