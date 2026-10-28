"""Capture writers respect a layered profile's read-only base, and resolve the right profile.

``captures migrate-discover`` repairs files in place, so it owes the same base-layer refusal as
the other capture writers — including when the base's store is named directly with ``--dir``.
It also needs the *definitions* of the profile that owns the captures: for a layered profile
those live in the base, not in the overlay ``--dir`` points at. The ``--set-*`` session editors
are covered behaviourally here too (refusal, partial failure, confirmation, clean ``--json``).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from canlib.profile import ProfileError, resolve_profile

LEGACY = {
    "date": "2026-10-21",
    "label": "sweep",
    "captures": [
        {
            "rx": "broadcast",
            "pid": "discover 700-7EF",
            "time": "10:00:00",
            "scan_results": {"responding": [{"rx": "0x760", "response": "ABS (positive)"}]},
        }
    ],
}


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """A bundled base ``ev6`` (a +0x20 profile) and the user's overlay, made active."""
    from canlib import config
    from canlib import profile as profile_mod
    from canlib.pids import clear_cache

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
    monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
    config.load_config.cache_clear()
    base = tmp_path / "profiles" / "ev6"
    (base / "ecus").mkdir(parents=True)
    (base / "captures").mkdir()
    (base / "profile.yaml").write_text(
        'car_model: "ev6"\ninit: "ATSP6;"\naddressing:\n  rx_offset: 0x20\n'
    )
    (base / "ecus" / "abs.yaml").write_text("ABS:\n  tx_id: 0x740\n")
    overlay = tmp_path / "cfg" / "canair" / "profiles" / "ev6"
    (overlay / "captures").mkdir(parents=True)
    (overlay / "profile.yaml").write_text("extends: ev6\n")
    saved = profile_mod._active
    profile_mod._active = resolve_profile("ev6")
    clear_cache()
    yield argparse.Namespace(base=base, overlay=overlay)
    profile_mod._active = saved
    config.load_config.cache_clear()
    clear_cache()


def _write(path: Path, *sessions) -> Path:
    path.write_text(json.dumps({"sessions": list(sessions)}))
    return path


def _migrate(*argv: str) -> int:
    from canlib.commands import captures as cap

    parser = cap.add_parser(argparse.ArgumentParser().add_subparsers())
    args = parser.parse_args(["migrate-discover", *argv])
    return args.func(args)


def _responder(path: Path) -> dict:
    return json.loads(path.read_text())["sessions"][0]["captures"][0]["scan_results"]["responding"][
        0
    ]


class TestMigrateDiscoverLayered:
    def test_without_dir_repairs_the_overlay_with_the_base_definitions(self, layered):
        mine = _write(layered.overlay / "captures" / "2026-10-21.json", LEGACY)
        assert _migrate() == 0
        assert _responder(mine) == {"tx": "0x740", "rx": "0x760", "response": "ABS (positive)"}

    def test_an_overlay_dir_resolves_the_layered_profile(self, layered):
        """Before, the overlay was taken as a plain profile with no ecus/ and it crashed."""
        mine = _write(layered.overlay / "captures" / "2026-10-21.json", LEGACY)
        assert _migrate("--dir", str(layered.overlay / "captures")) == 0
        assert _responder(mine)["tx"] == "0x740"

    def test_the_base_store_named_directly_is_refused(self, layered, capsys):
        theirs = _write(layered.base / "captures" / "2026-10-21.json", LEGACY)
        before = theirs.read_text()
        assert _migrate("--dir", str(layered.base / "captures")) == 1
        err = capsys.readouterr().err
        assert "read-only base" in err and "cannot be repaired" in err
        assert theirs.read_text() == before

    def test_the_base_store_can_still_be_previewed(self, layered, capsys):
        theirs = _write(layered.base / "captures" / "2026-10-21.json", LEGACY)
        before = theirs.read_text()
        assert _migrate("--dir", str(layered.base / "captures"), "--dry-run") == 0
        assert "Would repair" in capsys.readouterr().out
        assert theirs.read_text() == before

    def test_a_dir_outside_any_profile_is_a_profile_error(self, tmp_path, layered):
        stray = tmp_path / "scratch" / "captures"
        stray.mkdir(parents=True)
        _write(stray / "2026-10-21.json", LEGACY)
        with pytest.raises(ProfileError, match="no ECU registry"):
            _migrate("--dir", str(stray))


# --------------------------------------------------------------------------
# --set-notes / --set-transport / --set-state
# --------------------------------------------------------------------------


def _session(label: str, time: str = "10:00:00") -> dict:
    return {
        "date": "2026-10-21",
        "label": label,
        "captures": [{"rx": "0x760", "pid": "2101", "payload": "6101AA", "time": time}],
    }


def _entries():
    from canlib.captures.store import load_all_captures

    return load_all_captures()


class TestSessionEditorsRefuseTheBase:
    @pytest.mark.parametrize(
        "editor,value", [("cmd_set_notes", "corrected"), ("cmd_set_transport", "wican-ws")]
    )
    def test_a_base_session_is_refused(self, layered, capsys, editor, value):
        from canlib.commands.captures import session_fields

        theirs = _write(layered.base / "captures" / "2026-10-21.json", _session("upstream"))
        before = theirs.read_text()
        assert getattr(session_fields, editor)(_entries(), value, assume_yes=True) == 1
        assert "cannot be edited" in capsys.readouterr().err
        assert theirs.read_text() == before

    def test_set_state_refuses_a_base_session(self, layered, capsys):
        from canlib.commands.captures.set_state import cmd_set_state

        theirs = _write(layered.base / "captures" / "2026-10-21.json", _session("upstream"))
        before = theirs.read_text()
        assert cmd_set_state(_entries(), "ACC", assume_yes=True) == 1
        assert "cannot be relabelled" in capsys.readouterr().err
        assert theirs.read_text() == before


class TestSessionEditorsBehaviour:
    def _store(self, tmp_path) -> Path:
        cdir = tmp_path / "captures"
        cdir.mkdir()
        _write(cdir / "2026-10-21.json", _session("a"), _session("b", "11:00:00"))
        return cdir

    def test_a_partial_write_failure_exits_1(self, tmp_path, monkeypatch, capsys):
        from canlib.captures.store import load_all_captures
        from canlib.commands.captures import session_fields

        cdir = self._store(tmp_path)
        real = session_fields._notes_field().write
        calls = []

        def flaky(path, idx, value):
            calls.append(idx)
            if idx == 1:
                raise OSError("disk full")
            real(path, idx, value)

        monkeypatch.setattr(
            session_fields,
            "_notes_field",
            lambda: session_fields.SessionField("notes", "--set-notes", flaky),
        )
        rc = session_fields.cmd_set_notes(load_all_captures(cdir), "x", assume_yes=True)
        assert rc == 1 and calls == [0, 1]
        out = capsys.readouterr().out
        assert "disk full" in out and "Set notes on 1 session(s)" in out

    @pytest.mark.parametrize("answer,rc,written", [("y", 0, True), ("n", 1, False)])
    def test_the_confirmation_prompt(self, tmp_path, monkeypatch, capsys, answer, rc, written):
        from canlib.captures.store import load_all_captures
        from canlib.commands.captures import session_fields

        cdir = self._store(tmp_path)
        tty = io.StringIO(answer + "\n")
        tty.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", tty)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert session_fields.cmd_set_notes(load_all_captures(cdir), "x") == rc
        doc = json.loads((cdir / "2026-10-21.json").read_text())
        notes = [s.get("notes") for s in doc["sessions"]]
        assert notes == (["x", "x"] if written else [None, None])
        assert "Set notes on these 2 session(s)? [y/N]" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "editor,value",
        [("notes", "corrected"), ("transport", "wican-ws"), ("state", "ACC")],
    )
    def test_json_without_dry_run_keeps_stdout_pure(self, tmp_path, capsys, editor, value):
        from canlib.captures.store import load_all_captures
        from canlib.commands.captures import session_fields
        from canlib.commands.captures.set_state import cmd_set_state

        cdir = self._store(tmp_path)
        entries = load_all_captures(cdir)
        if editor == "state":
            rc = cmd_set_state(entries, value, assume_yes=True, as_json=True)
        else:
            fn = getattr(session_fields, f"cmd_set_{editor}")
            rc = fn(entries, value, assume_yes=True, as_json=True)
        assert rc == 0
        captured = capsys.readouterr()
        rows = json.loads(captured.out)  # nothing but the JSON
        assert len(rows) == 2 and all(r["will_write"] for r in rows)
        assert "session(s)" in captured.err
