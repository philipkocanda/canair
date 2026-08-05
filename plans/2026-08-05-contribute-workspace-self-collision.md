Status: **NOT STARTED** — root causes diagnosed for both issues below, fixes
designed, no code changed yet.

# `canair contribute` issues: workspace self-collision + bloated capture diffs

This plan covers two related `canair contribute` problems surfaced in the same
session:

1. A crash when run from inside its own managed workspace clone (below).
2. An earlier successful contribution ([PR #7](https://github.com/philipkocanda/canair/pull/7/files))
   touched far more capture files than intended — see "Issue 2" further down.

## Issue 1: crashes when run from inside its own managed workspace clone

## Bug report

```
$ pwd
/home/philip/.config/canair/contribute/canair
$ uv run canair contribute --diff
Using CPython 3.12.13 interpreter at: .../python3.12
Creating virtual environment at: .venv
      Built canair @ file:///home/philip/.config/canair/contribute/canair
Installed 50 packages in 46ms

  reading profile from: /home/philip/.config/canair/contribute/canair/profiles/ioniq-2017
  staging in workspace: /home/philip/.config/canair/contribute/canair
  syncing (first run clones — may take a moment) …
  mode: direct (will push to philipkocanda/canair)
Traceback (most recent call last):
  ...
  File ".../canlib/commands/contribute.py", line 273, in run
    C.copy_profile(profile, workspace, include_captures=include_captures)
  File ".../canlib/contribute.py", line 412, in copy_profile
    shutil.copy2(src, target)
  ...
shutil.SameFileError: PosixPath('.../profiles/ioniq-2017/profile.yaml') and
PosixPath('.../profiles/ioniq-2017/profile.yaml') are the same file
```

## Root cause

`canlib/contribute.py::workspace_dir()` returns a **fixed, persistent** clone
location: `config_dir() / "contribute" / "canair"` →
`~/.config/canair/contribute/canair`. This directory is a full git clone of
the canair repo (created/refreshed by `ensure_workspace()`), so it has its own
`pyproject.toml`, its own bundled `profiles/ioniq-2017/`, etc. — it *is* a
canair checkout, structurally identical to the project repo.

The user `cd`'d into that workspace clone and ran `uv run canair contribute
--diff` from there. `uv run` found the local `pyproject.toml` and
built/installed canair **from that checkout** (`Built canair @
file:///home/philip/.config/canair/contribute/canair` in the transcript).
Because canair's `BUNDLED_PROFILES_DIR` (`canlib/constants.py`) is computed
relative to the running package's own file location, `active()`
(`canlib/profile.py`) resolved the "active profile" to
`<workspace>/profiles/ioniq-2017` — i.e. `profile.root` ended up **path-identical**
to the copy destination `copy_profile()` computes: `dest = workspace /
"profiles" / profile.name`.

`copy_profile()` (`canlib/contribute.py:378-416`) then iterates
`_DEFINITION_MEMBERS` and does `shutil.copy2(src, target)` for file members
(e.g. `profile.yaml`) and `shutil.rmtree(target)` + `shutil.copytree(src,
target)` for directory members (e.g. `ecus/`) — with `src == target`, the
file case raises `SameFileError` immediately; the directory case would be
**worse** (silently `rmtree`s the source, then tries to copy from a directory
that no longer exists).

This is not a one-off config mistake — it's a structural foot-gun: **any**
`uv run canair ...` invocation with cwd inside
`~/.config/canair/contribute/canair` will resolve the active profile to that
clone's own bundled copy, which is always exactly the `contribute` command's
own destination path. AGENTS.md already says to always run `uv run canair`
from the *project repo root*, not from the managed workspace clone — but
`canair contribute` prints the workspace path prominently
(`staging in workspace: ...`), which invites a user to `cd` there to poke
around and then re-run a command in place.

## Fix plan

1. **CLI-level guard (primary fix)** — in `canlib/commands/contribute.py::run()`,
   right after `workspace` is computed (Step 4, before `C.ensure_workspace(...)`
   is called), compute `dest_would_be = workspace / "profiles" / profile.name`
   and compare `profile.root.resolve() == dest_would_be.resolve()` (guarded by
   try/except OSError → False). On a match, fail fast with a clear, actionable
   error (both `--json` and human paths, returning `_CANNOT` like the other
   early guards in this function already do) explaining that the active
   profile *is* the workspace's own copy — i.e. the command is being run from
   inside the managed workspace clone itself — and instructing the user to
   re-run from their actual project checkout instead.

2. **Defense-in-depth in `copy_profile()`** (`canlib/contribute.py:395-416`) —
   guard each per-member copy against `src.resolve() == target.resolve()`:
   skip the `shutil.copy2` (file case) and skip the `rmtree`+`copytree` pair
   (directory case) when paths coincide, rather than crashing / destructively
   `rmtree`-ing the source. Makes `copy_profile` itself idempotent/safe even
   if reached with coincident paths via some other caller (e.g. a future
   `--repo-dir` misuse), independent of the CLI-level guard.

3. **Tests** — add a case to `tests/test_contribute.py::TestCopyProfile` for
   the same-path scenario (no crash, no data loss), and a test for the new
   early-guard error message/return code in whichever test module covers
   `commands/contribute.py::run` (or add one if none exists yet).

4. **Docs** — this is a bug fix / robustness guard producing a friendlier
   error in an edge case, not a new user-facing flag or behavior change, so
   no `docs/`/`README.md`/`AGENTS.md` updates are expected. Confirm this
   holds once the fix is written.

## Issue 2: contribution PRs touch far more capture files than intended

### Bug report

A previous `canair contribute` run opened
[PR #7](https://github.com/philipkocanda/canair/pull/7/files) ("profiles:
contribute ioniq-2017 (Hyundai Ioniq 2017)", branch
`contribute/ioniq-2017-20260805`, `+16,776 −14,549`, 12 files changed — all
`.json` under `profiles/ioniq-2017/captures/`). **This run was made from the
normal repo checkout** (`/home/philip/projects/canair`), not from inside the
managed workspace clone — so Issue 2 is confirmed **independent of Issue 1**;
it is a real bug in the capture-overlay/union logic itself, not a byproduct of
running from the wrong directory:

```
profiles/ioniq-2017/captures/2026-04-14.json
profiles/ioniq-2017/captures/2026-04-15.json
profiles/ioniq-2017/captures/2026-04-16.json
profiles/ioniq-2017/captures/2026-04-17.json
profiles/ioniq-2017/captures/2026-04-18.json
profiles/ioniq-2017/captures/2026-04-19.json
profiles/ioniq-2017/captures/2026-07-20.json
profiles/ioniq-2017/captures/2026-07-21.json
profiles/ioniq-2017/captures/2026-07-22.json
profiles/ioniq-2017/captures/2026-07-29.json
profiles/ioniq-2017/captures/2026-08-03.json
profiles/ioniq-2017/captures/2026-08-05.json
```

The user's intent was to contribute only the **new** sessions recorded on
**2026-08-03 and 2026-08-05** — the other 10 files were already part of an
earlier, already-merged contribution and should not have shown any diff at
all this time. Inspecting the actual diff for `2026-04-14.json` (a file the
user did *not* touch) shows a large `+71/−71` change that, on inspection, is
**pure reordering**: the same "ACC mode (accessory on, parked)" session block
is removed from one position in the `sessions` array and re-added verbatim
(byte-identical content) in a different position relative to the
"Charging (AC)" / "Keyfob wake" session blocks. No session content actually
changed — only the array order — but git's line-based diff renders that as a
near-total rewrite of the file.

### Root cause

`canlib/contribute.py::copy_profile()` (lines 414-415) calls
`_overlay_captures(profile.root / _CAPTURE_MEMBER, dest / _CAPTURE_MEMBER)`
whenever `include_captures` is true (the default). `_overlay_captures()`
(lines 419-444) walks **every** file under the source `captures/` tree
(`sorted(src.rglob("*"))`) and, for each top-level dated `*.json` log that
**already exists in the destination** (i.e. every previously-contributed file
still present in the persistent workspace clone / already merged upstream),
unconditionally calls `_union_capture_files(upstream=target, source=path)`
(lines 447-463) — with no check for whether the source side actually
contributes anything new to that particular file.

`_union_capture_files()` loads both documents and calls
`dump_capture_file(upstream, union_documents(up, ours))` — **always
rewriting** `upstream` (the workspace copy, which becomes the PR diff) with
the result, even when the merged content is set-identical to what was already
there. `union_documents(a, b)` (`canlib/captures_merge.py:136-147`) is a
`merge_documents({"sessions": []}, a, b)` — every session on both sides is
deduped by exact-content key (`_canonical`, `json.dumps(session,
sort_keys=True)`) and the result is **always re-sorted** by `_sort_key`
(`canlib/captures_merge.py:63-70`): `(date, first_capture_time, label)`. This
sort is intentionally *deterministic and order-independent* (per its
docstring: "makes the merge order-independent … produces byte-identical
output") — a good property for the git merge-driver — but it does **not**
necessarily match whatever order the file already has on disk upstream
(e.g. if upstream was committed/ordered before this canonical-sort discipline
existed, or the original save/append order for same-day sessions wasn't
strictly increasing by first-capture-time). So on every `canair contribute`
run, **every dated capture file that exists on both sides gets unconditionally
rewritten into canonical sort order**, regardless of whether the local source
added any new session to it — inflating the diff with pure-reordering noise
across the contributor's *entire* capture history, not just the file(s) they
actually meant to add to.

In short: the union-merge is correct and safe (no data loss, as designed —
"never deletes a session"), but it is **not diff-minimal** — it doesn't
special-case "nothing new to add" to leave the existing upstream file's bytes
(and order) untouched.

### Fix plan

1. **Skip the rewrite when nothing changed** — in `_union_capture_files()`
   (`canlib/contribute.py`), before calling `dump_capture_file`, compare the
   merged session set against the **upstream** file's existing session set
   (e.g. `{_canonical-equivalent key for s in existing_sessions}` vs the same
   for the merged result — reuse/expose a session-set-equality helper from
   `canlib/captures_merge.py`, or just compare `union_documents(up, up)`'s
   sessions against `up`'s own sessions to know whether `ours` contributed
   anything at all). If the merged set is identical to upstream's existing
   set, **leave the file untouched** (don't call `dump_capture_file` at all) —
   preserving upstream's exact on-disk bytes/order for files the contribution
   doesn't actually touch.
2. Alternatively/additionally, when a file genuinely gains new sessions,
   consider whether the merge should **preserve upstream's existing order for
   the sessions upstream already had**, and only append the new ones at the
   position the canonical sort would place them — minimizing the diff to just
   the added sessions rather than resorting the whole file. (Needs more
   thought: the canonical sort exists specifically so the git merge-driver's
   3-way `merge_sessions` produces order-independent, re-mergeable output;
   changing `_union_capture_files`'s behavior must not break that guarantee
   for the actual merge-driver path — this fix should be scoped to the
   `contribute` copy-time overlay, not `captures_merge.py`'s merge-driver
   logic, unless it can be done without weakening that property.)
3. **Tests** — add a `tests/test_contribute.py` case: given an upstream
   capture file and a source file with the *same* sessions (possibly in a
   different order on disk), `_overlay_captures`/`copy_profile` must leave the
   destination file **byte-identical** to its pre-existing content (no diff).
   Add a second case where the source *does* add a new session to a file that
   already has others, and assert only that file changes (and — once fix #2
   is scoped — assert the diff is minimal, not a full resort, if that part is
   implemented).
4. **Docs** — likely no user-facing doc changes needed (internal correctness
   fix to an already-documented behavior — "captures are unioned… append-only
   evidence"), but double check `docs/concepts/captures-and-states.md` and the
   `canair contribute` AGENTS.md paragraph don't need a caveat removed/added
   once the fix lands.

## Not yet done

No code has been changed for either issue yet. This plan file exists solely to
record the diagnosis before implementation, per user request.
