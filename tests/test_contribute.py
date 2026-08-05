"""Tests for `canair contribute` orchestration and command flow.

The git/gh calls go through the module-level ``contribute._run``; unit tests
drive the orchestration with a fake runner (no network, no real GitHub). The
command-level dry-run test uses a real throwaway git repo as ``--repo-dir`` so
the branch/copy/commit path is exercised end-to-end without pushing.
"""

from __future__ import annotations

import argparse
import subprocess

from canlib import contribute as C
from canlib.commands import contribute as cmd
from canlib.profile import Profile


class FakeRunner:
    """Records commands and returns programmed :class:`contribute.Step`s."""

    def __init__(self, responder):
        self.calls: list[list[str]] = []
        self._responder = responder

    def __call__(self, cmd_args, cwd=None, env=None):
        self.calls.append(cmd_args)
        rc, out, err = self._responder(cmd_args)
        return C.Step(cmd=cmd_args, returncode=rc, stdout=out, stderr=err)


def _ready() -> C.Preflight:
    return C.Preflight(gh="gh", git="git", authenticated=True)


class TestPreflight:
    def test_not_ready_without_gh(self):
        assert not C.Preflight(gh=None, git="git", authenticated=True).ready

    def test_ready(self):
        assert _ready().ready

    def test_gh_install_hint_mentions_gh_and_login(self):
        hint = C.gh_install_hint()
        assert "gh auth login" in hint
        assert "cli/cli" in hint


class TestEnsureWorkspace:
    def test_direct_mode_clones_upstream_no_fork(self, tmp_path, monkeypatch):
        ws = tmp_path / "canair"  # fresh

        def responder(args):
            if "repo" in args and "view" in args:  # viewerPermission probe
                return 0, "ADMIN", ""
            return 0, "", ""

        runner = FakeRunner(responder)
        monkeypatch.setattr(C, "_run", runner)
        steps, mode, ok = C.ensure_workspace(_ready(), ws)
        assert ok and mode == C.MODE_DIRECT
        assert steps
        # Must clone upstream directly, never fork.
        assert runner.calls[0][:3] == ["gh", "repo", "clone"] or any(
            call[:3] == ["gh", "repo", "clone"] for call in runner.calls
        )
        assert not any(call[:3] == ["gh", "repo", "fork"] for call in runner.calls)

    def test_fork_mode_when_no_push_access(self, tmp_path, monkeypatch):
        ws = tmp_path / "canair"  # fresh

        def responder(args):
            if "repo" in args and "view" in args:
                return 0, "READ", ""  # no push access → must fork
            if "remote" in args and "add" not in args:
                return 0, "origin\nupstream\n", ""
            return 0, "", ""

        runner = FakeRunner(responder)
        monkeypatch.setattr(C, "_run", runner)
        steps, mode, ok = C.ensure_workspace(_ready(), ws)
        assert ok and mode == C.MODE_FORK
        assert steps
        assert any(call[:3] == ["gh", "repo", "fork"] for call in runner.calls)
        assert not any(call[:3] == ["gh", "repo", "clone"] for call in runner.calls)

    def test_existing_clone_detects_fork_by_upstream_remote(self, tmp_path, monkeypatch):
        ws = tmp_path / "canair"
        (ws / ".git").mkdir(parents=True)  # existing

        def responder(args):
            if args[-1] == "remote":
                return 0, "origin\nupstream\n", ""  # has upstream → fork clone
            return 0, "", ""

        runner = FakeRunner(responder)
        monkeypatch.setattr(C, "_run", runner)
        _, mode, ok = C.ensure_workspace(_ready(), ws)
        assert ok and mode == C.MODE_FORK
        # No permission probe / no fork for an existing clone.
        assert not any("view" in call for call in runner.calls)
        assert not any(call[:3] == ["gh", "repo", "fork"] for call in runner.calls)

    def test_fetch_failure_is_tolerated(self, tmp_path, monkeypatch):
        ws = tmp_path / "canair"
        (ws / ".git").mkdir(parents=True)  # existing, direct

        def responder(args):
            if args[-1] == "remote":
                return 0, "origin\n", ""  # no upstream → direct
            if "fetch" in args:
                return 1, "", "offline"  # fetch fails
            return 0, "", ""

        monkeypatch.setattr(C, "_run", FakeRunner(responder))
        _, mode, ok = C.ensure_workspace(_ready(), ws)
        assert ok and mode == C.MODE_DIRECT  # fetch failure not fatal


class TestPrHead:
    def test_direct_is_bare_branch(self):
        assert C.pr_head(_ready(), "br", C.MODE_DIRECT) == "br"

    def test_fork_is_owner_prefixed(self, monkeypatch):
        monkeypatch.setattr(C, "_run", FakeRunner(lambda a: (0, "octocat", "")))
        assert C.pr_head(_ready(), "br", C.MODE_FORK) == "octocat:br"


class TestCreatePr:
    def test_targets_upstream_and_head(self, monkeypatch, tmp_path):
        runner = FakeRunner(lambda a: (0, "https://github.com/x/y/pull/1", ""))
        monkeypatch.setattr(C, "_run", runner)
        step = C.create_pr(_ready(), tmp_path, title="t", body="b", head="me:branch")
        assert step.ok
        call = runner.calls[0]
        assert "--repo" in call and C.UPSTREAM_REPO in call
        assert "--head" in call and "me:branch" in call


class TestCopyProfile:
    def test_excludes_out_and_journal(self, tmp_path):
        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "ecus" / "bms.yaml").write_text("x")
        (src / "out").mkdir()
        (src / "out" / "autopid.json").write_text("{}")
        (src / "captures").mkdir()
        (src / "captures" / "2026-01-01.json").write_text("{}")
        (src / "captures" / ".journal").mkdir()
        (src / "captures" / ".journal" / "wal.jsonl").write_text("x")
        (src / "profile.yaml").write_text("car_model: X\n")
        prof = Profile("mycar", src)

        ws = tmp_path / "ws"
        ws.mkdir()
        dest = C.copy_profile(prof, ws, include_captures=True)

        assert (dest / "ecus" / "bms.yaml").exists()
        assert (dest / "captures" / "2026-01-01.json").exists()
        assert not (dest / "out").exists()  # generated — excluded
        assert not (dest / "captures" / ".journal").exists()  # transient — excluded

    def test_excludes_underscore_prefixed_scratch_files(self, tmp_path):
        # The skip list is a glob per path component, so `_`-prefixed helper
        # files are skipped the same way capture_io's readers skip them (it used
        # to match only a component named exactly "_", contributing scratch).
        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "ecus" / "_scratch.yaml").write_text("x")
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "captures").mkdir()
        (src / "captures" / "_notes.json").write_text("{}")
        (src / "captures" / "2026-01-01.json").write_text('{"sessions": []}')
        (src / "captures" / "half-written.json.tmp").write_text("{")

        dest = C.copy_profile(Profile("mycar", src), tmp_path / "ws", include_captures=True)

        assert (dest / "captures" / "2026-01-01.json").exists()
        assert not (dest / "ecus" / "_scratch.yaml").exists()
        assert not (dest / "captures" / "_notes.json").exists()
        assert not (dest / "captures" / "half-written.json.tmp").exists()

    def test_no_captures_omits_capture_dir(self, tmp_path):
        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "captures").mkdir()
        (src / "captures" / "2026-01-01.json").write_text("{}")
        (src / "profile.yaml").write_text("car_model: X\n")
        ws = tmp_path / "ws"
        ws.mkdir()
        dest = C.copy_profile(Profile("mycar", src), ws, include_captures=False)
        assert not (dest / "captures").exists()

    def test_preserves_unmanaged_upstream_members(self, tmp_path):
        # An existing upstream profile with captures/references/out that this
        # (definitions-only) contribution does not include must NOT be deleted.
        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "ecus" / "bms.yaml").write_text("new\n")
        (src / "profile.yaml").write_text("car_model: X\n")

        ws = tmp_path / "ws"
        existing = ws / "profiles" / "mycar"
        (existing / "captures").mkdir(parents=True)
        (existing / "captures" / "2026-01-01.json").write_text("{}")
        (existing / "references").mkdir()
        (existing / "references" / "sheet.csv").write_text("a,b\n")
        (existing / "out").mkdir()
        (existing / "out" / "autopid.json").write_text("{}")
        (existing / "ecus").mkdir()
        (existing / "ecus" / "bms.yaml").write_text("old\n")

        dest = C.copy_profile(Profile("mycar", src), ws, include_captures=False)
        # Managed member updated …
        assert (dest / "ecus" / "bms.yaml").read_text() == "new\n"
        # … unmanaged members preserved (not deleted by the contribution).
        assert (dest / "captures" / "2026-01-01.json").exists()
        assert (dest / "references" / "sheet.csv").exists()
        assert (dest / "out" / "autopid.json").exists()

    def test_copies_every_curated_definition_member(self, tmp_path):
        # Regression: groups.yaml was silently dropped, so a contributed profile
        # lost its saved selector groups. Every curated definition member the
        # profile owns must reach the workspace.
        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
        (src / "signals").mkdir()
        (src / "signals" / "p-can.yaml").write_text("messages: {}\n")
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "vehicle_states.yaml").write_text("states:\n  SLEEP:\n")
        (src / "can_buses.yaml").write_text("can_buses:\n  P-CAN:\n")
        (src / "groups.yaml").write_text("groups:\n  charging: [BMS]\n")

        ws = tmp_path / "ws"
        ws.mkdir()
        dest = C.copy_profile(Profile("mycar", src), ws, include_captures=False)

        for member in ("profile.yaml", "vehicle_states.yaml", "can_buses.yaml", "groups.yaml"):
            assert (dest / member).exists(), f"{member} not contributed"
        assert (dest / "ecus" / "bms.yaml").exists()
        assert (dest / "signals" / "p-can.yaml").exists()


class TestInstalledSnapshotKind:
    def test_working_checkout_is_none(self, tmp_path):
        assert (
            C.installed_snapshot_kind(tmp_path / "projects" / "canair" / "profiles" / "x") is None
        )

    def test_uv_tool_snapshot(self):
        from pathlib import Path

        p = Path(
            "/home/u/.local/share/uv/tools/canair/lib/python3.12/site-packages/profiles/ioniq-2017"
        )
        assert C.installed_snapshot_kind(p) == "uv tool"

    def test_generic_site_packages(self):
        from pathlib import Path

        p = Path("/usr/lib/python3.12/site-packages/profiles/ioniq-2017")
        assert C.installed_snapshot_kind(p) == "installed package"


class TestCopyProfileCapturesUnion:
    def test_union_keeps_upstream_sessions_a_behind_source_lacks(self, tmp_path):
        # Upstream has a session the (behind) source doesn't; the contribution
        # must NOT propose deleting it — the merged file keeps both.
        import json

        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "captures").mkdir()
        ours = {
            "sessions": [{"date": "2026-01-02", "captures": [{"time": "11:00", "pid": "2102"}]}]
        }
        (src / "captures" / "2026-01-01.json").write_text(json.dumps(ours))

        ws = tmp_path / "ws"
        dest = ws / "profiles" / "mycar"
        (dest / "captures").mkdir(parents=True)
        upstream = {
            "sessions": [{"date": "2026-01-01", "captures": [{"time": "10:00", "pid": "2101"}]}]
        }
        (dest / "captures" / "2026-01-01.json").write_text(json.dumps(upstream))

        C.copy_profile(Profile("mycar", src), ws, include_captures=True)
        merged = json.loads((dest / "captures" / "2026-01-01.json").read_text())
        pids = {c["pid"] for s in merged["sessions"] for c in s["captures"]}
        assert pids == {"2101", "2102"}  # upstream session preserved, source added

    def test_new_capture_file_is_copied(self, tmp_path):
        import json

        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "captures").mkdir()
        (src / "captures" / "2026-02-02.json").write_text(json.dumps({"sessions": []}))

        ws = tmp_path / "ws"
        dest = ws / "profiles" / "mycar"
        (dest / "captures").mkdir(parents=True)  # exists but empty upstream

        C.copy_profile(Profile("mycar", src), ws, include_captures=True)
        assert (dest / "captures" / "2026-02-02.json").exists()

    def test_upstream_only_capture_file_not_deleted(self, tmp_path):
        import json

        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "captures").mkdir()  # source has no capture files at all

        ws = tmp_path / "ws"
        dest = ws / "profiles" / "mycar"
        (dest / "captures").mkdir(parents=True)
        (dest / "captures" / "2026-01-01.json").write_text(json.dumps({"sessions": []}))

        C.copy_profile(Profile("mycar", src), ws, include_captures=True)
        assert (dest / "captures" / "2026-01-01.json").exists()  # preserved

    def _capture_pair(self, tmp_path, *, upstream_doc, source_doc):
        """A src profile + workspace dest, each with one dated capture file."""
        from canlib import capture_io

        src = tmp_path / "src"
        (src / "ecus").mkdir(parents=True)
        (src / "profile.yaml").write_text("car_model: X\n")
        (src / "captures").mkdir()
        capture_io.dump_capture_file(src / "captures" / "2026-01-01.json", source_doc)

        ws = tmp_path / "ws"
        dest = ws / "profiles" / "mycar"
        (dest / "captures").mkdir(parents=True)
        upstream = dest / "captures" / "2026-01-01.json"
        capture_io.dump_capture_file(upstream, upstream_doc)
        return src, ws, upstream

    def test_same_sessions_in_different_order_leaves_file_byte_identical(self, tmp_path):
        # Regression: the union used to re-sort every file it touched into
        # canonical order. Sessions without a capture `time` tie-break on label,
        # so an untouched log was rewritten end-to-end as pure reordering noise
        # (10 spurious files in PR #7).
        def session(label):
            return {"date": "2026-01-01", "label": label, "captures": [{"rx": "0x7EC", "pid": "1"}]}

        upstream = {"sessions": [session("zzz"), session("aaa")]}
        source = {"sessions": [session("aaa"), session("zzz")]}  # same set, other order
        src, ws, up_file = self._capture_pair(tmp_path, upstream_doc=upstream, source_doc=source)
        before = up_file.read_bytes()

        C.copy_profile(Profile("mycar", src), ws, include_captures=True)

        assert up_file.read_bytes() == before

    def test_new_session_is_appended_without_reordering(self, tmp_path):
        import json

        def session(label):
            return {"date": "2026-01-01", "label": label, "captures": [{"rx": "0x7EC", "pid": "1"}]}

        upstream = {"sessions": [session("zzz"), session("aaa")]}
        source = {"sessions": [session("zzz"), session("aaa"), session("mmm")]}
        src, ws, up_file = self._capture_pair(tmp_path, upstream_doc=upstream, source_doc=source)

        C.copy_profile(Profile("mycar", src), ws, include_captures=True)

        merged = json.loads(up_file.read_text())
        assert [s["label"] for s in merged["sessions"]] == ["zzz", "aaa", "mmm"]

    def test_unreadable_upstream_file_is_not_overwritten(self, tmp_path):
        # A wholesale overwrite would drop exactly the upstream sessions this
        # overlay exists to preserve; skip and warn instead.
        src, ws, up_file = self._capture_pair(
            tmp_path,
            upstream_doc={"sessions": []},
            source_doc={"sessions": [{"date": "2026-01-01", "label": "new", "captures": []}]},
        )
        up_file.write_text("{not json")
        warnings: list[str] = []

        C.copy_profile(Profile("mycar", src), ws, include_captures=True, warn=warnings.append)

        assert up_file.read_text() == "{not json"
        assert warnings and "2026-01-01.json" in warnings[0]


class TestWorkspaceCollision:
    def test_profile_is_the_workspace_copy(self, tmp_path):
        ws = tmp_path / "ws"
        root = ws / "profiles" / "mycar"
        root.mkdir(parents=True)
        assert C.workspace_collision(root, ws, "mycar") == "self"

    def test_profile_elsewhere_under_the_workspace(self, tmp_path):
        ws = tmp_path / "ws"
        root = ws / "myprofiles" / "mycar"
        root.mkdir(parents=True)
        assert C.workspace_collision(root, ws, "mycar") == "inside"

    def test_normal_source_has_no_collision(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        root = tmp_path / "elsewhere" / "mycar"
        root.mkdir(parents=True)
        assert C.workspace_collision(root, ws, "mycar") is None


class TestCopyProfileSelfPath:
    def test_source_equal_to_destination_is_a_noop_not_a_crash(self, tmp_path):
        # Running from inside the staging workspace makes src == dest. Copying
        # must neither raise SameFileError nor rmtree the source.
        ws = tmp_path / "ws"
        root = ws / "profiles" / "mycar"
        (root / "ecus").mkdir(parents=True)
        (root / "ecus" / "bms.yaml").write_text("x")
        (root / "profile.yaml").write_text("car_model: X\n")
        (root / "captures").mkdir()
        (root / "captures" / "2026-01-01.json").write_text('{"sessions": []}')

        C.copy_profile(Profile("mycar", root), ws, include_captures=True)

        assert (root / "ecus" / "bms.yaml").read_text() == "x"  # source intact
        assert (root / "profile.yaml").exists()
        assert (root / "captures" / "2026-01-01.json").exists()


class TestDefinitionRollback:
    def _repo_with_committed_ecu(self, tmp_path, ecu_text):
        ws = tmp_path / "ws"
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        d = ws / "profiles" / "testcar" / "ecus"
        d.mkdir(parents=True)
        (d / "bms.yaml").write_text(ecu_text)
        (ws / "profiles" / "testcar" / "profile.yaml").write_text("car_model: X\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "seed profile")
        return ws

    def test_flags_removed_upstream_lines(self, tmp_path):
        ws = self._repo_with_committed_ecu(tmp_path, "line1\nline2\nline3\n")
        # Source rolls the file back (drops line2/line3).
        (ws / "profiles" / "testcar" / "ecus" / "bms.yaml").write_text("line1\n")
        rolled = C.definition_rollback(_ready(), ws, "testcar")
        assert rolled and rolled[0][0] == "profiles/testcar/ecus/bms.yaml"
        assert rolled[0][1] == 2  # two lines removed

    def test_pure_additions_are_not_flagged(self, tmp_path):
        ws = self._repo_with_committed_ecu(tmp_path, "line1\n")
        (ws / "profiles" / "testcar" / "ecus" / "bms.yaml").write_text("line1\nline2\n")
        assert C.definition_rollback(_ready(), ws, "testcar") == []


class TestDiffProfile:
    def test_diff_shows_new_untracked_files(self, tmp_path):
        # A real throwaway repo: copy a profile in, then diff should include the
        # freshly-copied (untracked) files as additions.
        ws = tmp_path / "ws"
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        (ws / "README.md").write_text("seed\n")
        _git(ws, "add", "README.md")
        _git(ws, "commit", "-m", "seed")

        prof = _make_profile(tmp_path / "prof")
        C.copy_profile(prof, ws, include_captures=False)
        diff = C.diff_profile(_ready(), ws, prof.name)
        assert "profiles/testcar/ecus/bms.yaml" in diff
        assert "car_model: Test EV 2022" in diff


# --- command-level -----------------------------------------------------------


def _cmd_args(**kw):
    base = {
        "captures": True,
        "branch": None,
        "title": None,
        "body": None,
        "repo_dir": None,
        "dry_run": False,
        "diff": False,
        "yes": True,
        "json": True,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _make_profile(root):
    (root / "ecus").mkdir(parents=True)
    (root / "ecus" / "bms.yaml").write_text("x")
    (root / "profile.yaml").write_text("car_model: Test EV 2022\ninit: ATSP6;\n")
    return Profile("testcar", root)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class TestContributeCommand:
    def test_gh_missing_reports_cannot(self, tmp_path, monkeypatch):
        prof = _make_profile(tmp_path / "prof")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(
            C, "preflight", lambda: C.Preflight(gh=None, git="git", authenticated=True)
        )
        rc = cmd.run(_cmd_args())
        assert rc == cmd._CANNOT

    def test_dry_run_prepares_commit(self, tmp_path, monkeypatch, capsys):
        # Real throwaway upstream-less repo used as the workspace.
        ws = tmp_path / "ws"
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        (ws / "README.md").write_text("seed\n")
        _git(ws, "add", "README.md")
        _git(ws, "commit", "-m", "seed")

        prof = _make_profile(tmp_path / "prof")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())

        rc = cmd.run(_cmd_args(repo_dir=str(ws), dry_run=True))
        assert rc == cmd._OK
        # The profile was committed onto a contribute branch, nothing pushed.
        assert (ws / "profiles" / "testcar" / "ecus" / "bms.yaml").exists()
        log = subprocess.run(
            ["git", "-C", str(ws), "log", "--oneline", "-1"], capture_output=True, text=True
        ).stdout
        assert "contribute" in log.lower()

    def test_pii_blocks_json_without_yes(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        (ws / "README.md").write_text("seed\n")
        _git(ws, "add", "README.md")
        _git(ws, "commit", "-m", "seed")

        root = tmp_path / "prof"
        prof = _make_profile(root)
        (root / "profile.yaml").write_text("car_model: owner me@example.com\ninit: ATSP6;\n")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())
        rc = cmd.run(_cmd_args(repo_dir=str(ws), yes=False, json=True))
        assert rc == cmd._CANNOT

    def _seed_repo(self, ws):
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        (ws / "README.md").write_text("seed\n")
        _git(ws, "add", "README.md")
        _git(ws, "commit", "-m", "seed")

    def test_diff_emits_diff_and_does_not_commit(self, tmp_path, monkeypatch, capsys):
        import json as _json

        ws = tmp_path / "ws"
        self._seed_repo(ws)
        prof = _make_profile(tmp_path / "prof")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())

        rc = cmd.run(_cmd_args(repo_dir=str(ws), diff=True, json=True))
        assert rc == cmd._OK
        payload = _json.loads(capsys.readouterr().out)
        assert payload["pr_url"] is None
        assert "profiles/testcar/ecus/bms.yaml" in payload["diff"]
        assert payload["source"] == str(prof.root)
        # --diff must not create a commit on the branch.
        log = subprocess.run(
            ["git", "-C", str(ws), "log", "--oneline"], capture_output=True, text=True
        ).stdout
        assert "contribute" not in log.lower()

    def test_push_not_confirmed_aborts(self, tmp_path, monkeypatch):
        # Non-interactive (json) without --yes must not push; it aborts at the
        # push-confirmation gate rather than opening a PR.
        ws = tmp_path / "ws"
        self._seed_repo(ws)
        prof = _make_profile(tmp_path / "prof")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())
        rc = cmd.run(_cmd_args(repo_dir=str(ws), yes=False, json=True))
        assert rc == cmd._CANNOT
        # Nothing pushed: no push/PR gh|git calls happened because we bailed.

    def test_installed_snapshot_blocks_json_without_yes(self, tmp_path, monkeypatch):
        # A profile resolved from a site-packages snapshot is refused (json, no
        # --yes) before any workspace work.
        root = tmp_path / "site-packages" / "profiles" / "testcar"
        prof = _make_profile(root)
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())
        rc = cmd.run(_cmd_args(yes=False, json=True))
        assert rc == cmd._CANNOT

    def test_rollback_blocks_json_without_yes(self, tmp_path, monkeypatch, capsys):
        # A source that removes committed upstream definition lines is refused
        # (json, no --yes) at the rollback gate.
        ws = tmp_path / "ws"
        ws.mkdir()
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "T")
        d = ws / "profiles" / "testcar" / "ecus"
        d.mkdir(parents=True)
        (d / "bms.yaml").write_text("a\nb\nc\n")
        (ws / "profiles" / "testcar" / "profile.yaml").write_text("car_model: X\ninit: ATSP6;\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "seed profile")

        # Source rolls the ecu back to one line.
        root = tmp_path / "prof"
        (root / "ecus").mkdir(parents=True)
        (root / "ecus" / "bms.yaml").write_text("a\n")
        (root / "profile.yaml").write_text("car_model: X\ninit: ATSP6;\n")
        prof = Profile("testcar", root)

        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())
        rc = cmd.run(_cmd_args(repo_dir=str(ws), captures=False, yes=False, json=True))
        assert rc == cmd._CANNOT
        import json as _json

        payload = _json.loads(capsys.readouterr().out)
        assert payload["rollback"] and payload["rollback"][0]["path"].endswith("ecus/bms.yaml")

    def test_self_collision_is_refused_even_with_yes(self, tmp_path, monkeypatch, capsys):
        # Running from inside the staging workspace: the source profile IS the
        # copy destination, so there is nothing to contribute. Unlike the
        # snapshot/PII/rollback warnings this is unconditional — --yes must not
        # push through it.
        ws = tmp_path / "ws"
        prof = _make_profile(ws / "profiles" / "testcar")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())

        rc = cmd.run(_cmd_args(repo_dir=str(ws), yes=True, json=True))

        assert rc == cmd._CANNOT
        import json as _json

        payload = _json.loads(capsys.readouterr().out)
        assert payload["workspace_collision"] == "self"

    def test_profile_inside_workspace_blocks_json_without_yes(self, tmp_path, monkeypatch, capsys):
        # Source under the workspace but not at the destination: workable, but
        # `start_branch` resets that checkout — warn and require confirmation.
        ws = tmp_path / "ws"
        prof = _make_profile(ws / "myprofiles" / "testcar")
        monkeypatch.setattr(cmd, "active", lambda: prof)
        monkeypatch.setattr(cmd, "_validate", lambda p: (True, ""))
        monkeypatch.setattr(C, "preflight", lambda: _ready())

        rc = cmd.run(_cmd_args(repo_dir=str(ws), yes=False, json=True))

        assert rc == cmd._CANNOT
        import json as _json

        payload = _json.loads(capsys.readouterr().out)
        assert payload["workspace_collision"] == "inside"
