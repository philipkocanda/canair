Status: **DONE** — both issues diagnosed, reviewed (Issue 2 reproduced, see
"Evidence") and **fixed** on 2026-08-05. Review corrections are marked ⓡ;
what actually landed is marked ✅. See "What landed" at the bottom for the
verification of the original failure scenarios.

# `canair contribute` issues: workspace self-collision + bloated capture diffs

This plan covers two related `canair contribute` problems surfaced in the same
session:

1. A crash when run from inside its own managed workspace clone (below).
2. An earlier successful contribution ([PR #7](https://github.com/philipkocanda/canair/pull/7/files))
   touched far more capture files than intended — see "Issue 2" further down.

The two are independent (confirmed below); either can be fixed without the
other. Issue 2 is the one that has already polluted an upstream PR, so it is
the higher-value fix.

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
Because canair's `BUNDLED_PROFILES_DIR` (`canlib/constants.py`, `SCRIPT_DIR /
"profiles"`, i.e. relative to the *running* package) is computed from the
running copy's own location, `profiles_roots()` → `discover_profiles()`
(`canlib/profile.py`) resolved the active profile to
`<workspace>/profiles/ioniq-2017` — i.e. `profile.root` ended up
**path-identical** to the copy destination `copy_profile()` computes: `dest =
workspace / "profiles" / profile.name`.

`copy_profile()` (`canlib/contribute.py`) then iterates `_DEFINITION_MEMBERS`
and does `shutil.copy2(src, target)` for file members and `shutil.rmtree(target)`
+ `shutil.copytree(src, target)` for directory members — with `src == target`,
the file case raises `SameFileError`; the directory case would be **worse**
(`rmtree` deletes the source, then `copytree` fails on a directory that no
longer exists).

ⓡ **Correction (severity):** the destructive directory branch is *in practice*
pre-empted, not merely unlikely: `_DEFINITION_MEMBERS` is ordered from
`BUNDLE_MEMBERS` (`canlib/profile.py`) as `profile.yaml, ecus, …`, so the
first member copied is always the file `profile.yaml`, which raises
`SameFileError` before `ecus/` is reached. It is not *structurally* guaranteed
(`_looks_like_profile` accepts a bundle with only `ecus/`, so a
`profile.yaml`-less profile would hit `rmtree` first), but no data was lost in
the reported run. So this is a crash, not a data-loss bug — which lowers the
urgency but not the need for the guard.

ⓡ **Correction (a second, quieter hazard — and why guard placement matters):**
`copy_profile` is not the first thing that touches the workspace tree.
`C.start_branch()` (step 5 of `commands/contribute.py::run`) runs `git checkout
-B <branch> upstream/main` **before** the copy, which resets the workspace's
*tracked* files. When the source profile lives inside that tree, the source is
mutated mid-run:

* uncommitted local edits → git aborts the checkout ("local changes would be
  overwritten"), so the run fails at "could not create branch" with a confusing
  message;
* edits committed on a previous contribute branch → the working tree reverts to
  `upstream/main` (the content survives on the old branch, so nothing is truly
  lost), the copy is then a no-op, and the run reports the misleading "No
  changes to contribute — the upstream profile already matches yours."

Both outcomes are silent-ish wrong behavior rather than a crash, and both are
avoided only if the guard runs **before** `ensure_workspace`/`start_branch`.

This is not a one-off config mistake — it's a structural foot-gun: **any**
`uv run canair ...` invocation with cwd inside
`~/.config/canair/contribute/canair` resolves the active profile to that
clone's own bundled copy, which is always exactly the `contribute` command's
own destination path (and is a throwaway tree that the next `checkout -B`
resets — so e.g. a `--save` run from there quietly writes captures into a
directory that will be reverted). AGENTS.md already says to always run `uv run
canair` from the *project repo root*, not from the managed workspace clone —
but `canair contribute` prints the workspace path prominently (`staging in
workspace: ...`), which invites a user to `cd` there to poke around and then
re-run a command in place.

## Fix plan

ⓡ Revised in review: containment (not just path equality), a two-tier
severity, and the predicate extracted into `canlib/contribute.py` to mirror the
existing `installed_snapshot_kind` guard.

1. **Detection helper in `canlib/contribute.py`** — add a sibling to
   `installed_snapshot_kind()` (device-free, unit-testable, no git/gh):

   ```python
   def workspace_collision(profile_root: Path, workspace: Path, profile_name: str) -> str | None:
       """"self" when the profile IS the workspace's own copy, "inside" when it
       merely lives under the workspace, else None."""
   ```

   Compare `resolve()`d paths (`try/except OSError` → treat as no collision);
   use `Path.is_relative_to` for the containment case (fine on the project's
   `requires-python = ">=3.12"`).

2. **CLI guard in `canlib/commands/contribute.py::run()`** — call it right
   after `workspace` is computed (step 4), **before** `C.ensure_workspace(...)`
   and therefore before `start_branch` resets the tree (see the hazard above).
   Two tiers:
   * `"self"` (source == destination) → **hard refuse**, returning `_CANNOT`
     like the other early guards. This is unconditionally broken (the copy
     cannot mean anything), so `--yes` must **not** override it — unlike the
     snapshot/PII/rollback warnings.
   * `"inside"` (source under the workspace but not at the destination — e.g.
     `--repo-dir ~/projects/canair` with a profile kept elsewhere in that same
     checkout) → **warn + confirm** via the existing `_confirm(...)`. This
     configuration can work today, so don't break it; but `start_branch` still
     resets the tree the source lives in, which the user should acknowledge.
   Message content: name both paths (`reading profile from` / `staging in
   workspace`), say plainly *the profile you're contributing is the workspace's
   own copy — you're running from inside the managed clone*, and instruct
   re-running `uv run canair contribute` from the real project checkout. In
   `--json` mode emit `{"ok": false, "cannot": true, "workspace_collision":
   "self"|"inside", "source": …, "workspace": …, "error": …}` (a distinct
   machine-readable key, matching how `installed_snapshot` is surfaced).

3. **Defense-in-depth in `copy_profile()`** — guard each per-member copy with
   `src.resolve() == target.resolve()`: skip the `shutil.copy2` (file case) and
   skip the `rmtree`+`copytree` pair (directory case) rather than crashing /
   destructively `rmtree`-ing the source. Also worth skipping `_overlay_captures`
   when `src == dst` for the same reason. Makes `copy_profile` safe for any
   caller (a future `--repo-dir` misuse) independent of the CLI guard.

4. **Tests** — `tests/test_contribute.py` already has both homes: add the
   same-path case to `TestCopyProfile` (no crash, source still intact
   afterwards) and the guard cases to `TestContributeCommand` (`repo_dir`
   pointing at a throwaway repo whose `profiles/<name>/` *is* the source →
   `_CANNOT` + the `workspace_collision` key in the JSON payload; plus a
   `--yes` run asserting it is still refused). Unit-test
   `workspace_collision()` directly for the self/inside/None cases.

5. **Docs** — a friendlier error in an edge case, no flag/behavior change, so
   `docs/reference/cli/contribute.md` (generated from `--help` by
   `scripts/gen_cli_reference.py`) needs no regeneration. ⓡ Optional: one
   troubleshooting line in `docs/bring-your-own-car/08-share.md` ("run it from
   your own checkout, not from the workspace clone canair prints"). Confirm
   once written.

6. ⓡ **Optional, broader mitigation (consider; not required for this fix)** —
   the foot-gun is wider than `contribute`: *any* command run from inside the
   managed clone resolves to its throwaway bundled profiles.
   `profiles_roots()` (`canlib/profile.py`) already imports `config_dir`, so it
   could skip `BUNDLED_PROFILES_DIR` when that path is under
   `config_dir() / "contribute"`. Tradeoff: with the bundled copy hidden, a run
   from inside the clone then fails with the generic `ProfileError` ("Profile
   'ioniq-2017' not found") unless the message is special-cased — so this only
   pays off if paired with a targeted hint. Decide separately; the `contribute`
   guard gives the better message for the reported case.

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

`canlib/contribute.py::copy_profile()` calls
`_overlay_captures(profile.root / _CAPTURE_MEMBER, dest / _CAPTURE_MEMBER)`
whenever `include_captures` is true (the default). `_overlay_captures()`
walks **every** file under the source `captures/` tree (`sorted(src.rglob("*"))`)
and, for each top-level dated `*.json` log that **already exists in the
destination** (i.e. every previously-contributed file still present in the
persistent workspace clone / already merged upstream), unconditionally calls
`_union_capture_files(upstream=target, source=path)` — with no check for
whether the source side actually contributes anything new to that file.

`_union_capture_files()` loads both documents and calls
`dump_capture_file(upstream, union_documents(up, ours))` — **always
rewriting** `upstream` (the workspace copy, which becomes the PR diff), even
when the merged content is set-identical to what was already there.
`union_documents(a, b)` (`canlib/captures_merge.py`) is
`merge_documents({"sessions": []}, a, b)`: every session on both sides is
deduped by exact-content key (`_canonical`) and the result is **always
re-sorted** by `_sort_key` — `(date, first_capture_time, label)`. That sort is
intentionally deterministic and order-independent (a good property for the git
merge-driver) but it does **not** match the order the file already has on disk
upstream.

ⓡ **Correction — the precise reason the orders differ.** The original plan
speculated ("committed before this canonical-sort discipline existed, or the
append order wasn't strictly increasing by first-capture-time"). The actual,
verified cause is narrower and more actionable: **`_first_time()` returns `""`
for a session whose first capture has no `time` field**, so for those sessions
`_sort_key` degenerates to `(date, "", label)` and the tie-break is
**alphabetical by label** — which bears no relation to append order. Those are
exactly the "untimed payload captures" that `canair validate captures` already
soft-warns about (scan sessions and pre-timestamp legacy recordings). Every
file whose order the union changes has untimed sessions; files where all
sessions carry a `time` round-trip unchanged.

ⓡ **Also verified: formatting is *not* a contributing factor.** Re-dumping an
order-stable file through `dump_capture_file` reproduces it **byte-identically**
(`indent=2`, `ensure_ascii=False`, trailing newline all match the committed
files). Ordering is the *sole* source of the diff noise, which means an
order-preserving overlay is sufficient — no reformatting concerns.

### Evidence (reproduction, 2026-08-05 review)

Running `union_documents(doc, doc)` over each committed
`profiles/ioniq-2017/captures/*.json` and comparing to the file's own bytes:

| result | files |
| --- | --- |
| unchanged (byte-identical) | 13 files |
| **reordered** (same session *set*, different order → full-file diff) | `2026-04-14/15/16/17/18/19`, `2026-07-20/21/22/29` — **exactly the 10 spurious files in PR #7** |

The session set is always preserved (`set(before) == set(after)` everywhere),
confirming "pure reordering, no data change". The remaining 2 of PR #7's 12
files (`2026-08-03`, `2026-08-05`) are the genuinely new ones. Repro script
pattern: `union_documents(json.load(f), json.load(f))` vs
`json.dumps(..., indent=2, ensure_ascii=False) + "\n"`.

In short: the union-merge is correct and safe (no data loss, as designed —
"never deletes a session"), but it is **not diff-minimal**: it neither
special-cases "nothing new to add" nor preserves the destination's existing
order.

### Fix plan

ⓡ Revised in review: one order-preserving overlay function fixes both the
no-op rewrite and the resort, so the two original items collapse into one.

1. **Add an order-preserving overlay to `canlib/captures_merge.py`** — a new
   function alongside (not replacing) the 3-way merge:

   ```python
   def overlay_documents(upstream: Any, source: Any) -> dict | None:
       """`upstream` with `source`'s not-yet-present sessions appended.

       Returns None when `source` adds nothing (caller leaves the file alone).
       """
   ```

   Semantics: keep **upstream's** session list in its existing on-disk order,
   append the sessions of `source` whose `_canonical` key isn't already present
   (in source order), and return `None` when that set is empty. Deterministic
   given `(upstream, source)`; asymmetric by design (upstream is authoritative
   for order), which is exactly right for an overlay where one side is the
   thing being added to. ⓡ Preserve upstream's other top-level keys too
   (`{**upstream, "sessions": …}`): `union_documents` returns a bare
   `{"sessions": …}`, which would silently drop any future top-level field
   (today every file has only `sessions`, and `captures_schema.json` requires
   just that — so this is prophylactic, not a live bug).

2. **Use it in `_union_capture_files()`** — `overlay_documents(up, ours)`;
   when it returns `None`, **do not call `dump_capture_file` at all**, leaving
   the upstream file's bytes (and mtime) untouched. That alone reduces PR #7's
   12 changed files to 2; the order preservation keeps a file that *does* gain
   sessions to a minimal append-only diff.

3. **Leave `merge_sessions`/`merge_documents` (the git merge-driver) alone** —
   the canonical sort is load-bearing there (order-independent, re-mergeable
   output; `tests/test_captures_merge.py` asserts `merge(A,B) == merge(B,A)`).
   `union_documents` currently has exactly one caller (`_union_capture_files`),
   so once it is replaced it is dead code: either delete it (and update the
   AGENTS.md reference, see item 6) or keep it if a future caller is expected —
   decide when writing the change, don't leave both live and unexplained.
   *Known, accepted residue:* when the merge-driver does fire on a same-day
   file it still reorders untimed sessions. That path is rare (only genuinely
   concurrent same-day appends) and its symmetry requirement is real, so it is
   an explicit non-goal here.

4. **Rejected alternative — normalize the committed data instead.** A one-off
   commit rewriting every `captures/*.json` in canonical `_sort_key` order
   would also silence the noise (subsequent unions become no-ops for
   untouched files). Rejected: it buys one large churn commit, doesn't help any
   *other* contributor's profile whose files are in append order, and leaves the
   unconditional-rewrite wart in place. The code fix needs no data migration.

5. **Tests** — extend `tests/test_contribute.py::TestCopyProfileCapturesUnion`:
   * source and upstream hold the *same* sessions in a *different* on-disk
     order (use untimed sessions with labels that sort against append order —
     the reproducing shape) → after `copy_profile`, the destination file is
     **byte-identical** to its pre-existing content;
   * source adds one new session to a file that already has others → only that
     file changes, upstream's existing sessions keep their original order and
     the new one is appended (assert the resulting session order explicitly,
     not just the set);
   * a source that is *behind* upstream still adds nothing and rewrites
     nothing (the existing `test_union_keeps_upstream_sessions_a_behind_source_lacks`
     covers preservation; assert no-rewrite too).
   Plus direct unit tests of `overlay_documents` in
   `tests/test_captures_merge.py` (None on no-op, append order, non-`sessions`
   keys preserved).

6. **Docs** ⓡ — the original "likely no doc changes" is wrong on one point:
   AGENTS.md's `canair contribute` paragraph names the helper explicitly
   ("captures are **unioned** with the upstream copy at copy time (append-only
   merge via `canlib/captures_merge.py::union_documents`…)"), so it must be
   updated for the new function name and the added guarantee (*files the
   contribution doesn't add to are left untouched; upstream order is
   preserved*). `docs/concepts/captures-and-states.md` describes the
   **merge-driver** union, which is unchanged — no edit needed there; verify
   once written. No CLI surface change → no `docs/reference/cli/` regeneration.

## Adjacent nits spotted during review (Boy Scout, optional)

Both are in the same functions this plan already touches; fix opportunistically
or leave — neither is implicated in the reported bugs.

1. **`_CAPTURE_SKIP` doesn't do what its docstrings claim.**
   `_CAPTURE_SKIP = ("_",)` is described as "never copied even under
   `captures/`", and `_overlay_captures`'s docstring says transient members
   "``.journal``, ``_*``, ``*.tmp``" are skipped — but both the
   `shutil.ignore_patterns("_")` in `copy_profile` and the
   `any(part in (".journal", "_") …)` check in `_overlay_captures` match only a
   path component *exactly* equal to `_`, never an `_`-**prefixed** name. Compare
   `capture_io._SKIP_PREFIXES = ("SCHEMA", "_")`, which uses `startswith` — so
   a `captures/_scratch.json` is skipped by every capture *reader* but would be
   *contributed*. Either make the patterns prefix-globs (`"_*"`, and consider
   `SCHEMA*`) or correct the docstrings.
2. **`_union_capture_files`'s parse-failure fallback can delete upstream
   sessions.** On `OSError`/`JSONDecodeError` it does
   `shutil.copy2(source, upstream)` "so a malformed file is still contributed
   rather than lost" — but the exception may have come from reading
   **upstream**, in which case a wholesale overwrite drops upstream's sessions,
   which is precisely what the union exists to prevent. Distinguish which side
   failed: fall back to overwrite only when *source* parses and *upstream*
   doesn't… and arguably not even then (a corrupt upstream file is worth
   surfacing loudly rather than silently replacing).

## What landed (2026-08-05)

✅ **Issue 2 — `canlib/captures_merge.py::overlay_documents`** (new) is the
order-preserving, never-deleting overlay: it keeps the destination's session list
verbatim, appends only sessions not already present, and returns `None` when the
source adds nothing. `merge_sessions`/`merge_documents` (the git merge driver)
are untouched, so their symmetry guarantee is intact; the now-dead
`union_documents` was removed. `contribute.py::_union_capture_files` became
`_overlay_capture_file`, which **skips `dump_capture_file` entirely** on a
`None` overlay — so a capture log the contribution doesn't add to keeps its
exact bytes.

✅ **Issue 1 — `canlib/contribute.py::workspace_collision`** (new, device-free,
next to `installed_snapshot_kind`) classifies the source profile as `"self"` /
`"inside"` / `None` relative to the staging workspace. `commands/contribute.py`
calls it in step **4b**, before `ensure_workspace`/`start_branch`: `"self"` is a
hard `_CANNOT` refusal that `--yes` cannot override (naming both paths), and
`"inside"` warns + confirms (json-without-`--yes` refuses, mirroring the
snapshot guard). `--json` reports `workspace_collision`.

✅ **Defense in depth** — `copy_profile` skips any member whose source resolves
to its destination (`_same_path`), and `_overlay_captures` returns early when
`src`/`dst` coincide. So the crash/`rmtree` pair is unreachable regardless of
caller.

✅ **Both Boy Scout nits** — the skip list is now one shared tuple of *globs*
(`_SKIP_PATTERNS = ("_*", ".journal", "*.tmp")`) matched per path component by
`_is_transient`, used by both the `copytree` ignore and the captures overlay (so
`_`-prefixed scratch files are genuinely skipped, as the docstrings always
claimed). An unreadable capture file on **either** side is now skipped with a
warning instead of overwriting upstream; `copy_profile` takes a `warn` callback,
the command prints those warnings and includes them in `--json` as `warnings`.

**Verification of the reported failures** (both scripted against the real
bundled profile, device-free):

* Contribute-style copy of `profiles/ioniq-2017` onto an upstream copy of
  itself → **0 files changed** (was: 10 files rewritten as pure reordering).
* Same, with one synthetic new session added to an existing day → **exactly 1
  file changed, `+15 −0`** (a pure append; was: that file plus 9 unrelated
  ones, each fully rewritten).
* `overlay_documents(doc, doc)` returns `None` for **all 23** committed capture
  files.

**Tests** — `tests/test_captures_merge.py::TestOverlayDocuments` (6 cases: no-op
detection, subset, order preservation, never-drops, extra top-level keys, missing
`sessions`); `tests/test_contribute.py` gains the byte-identical regression, the
append-without-reorder case, the unreadable-upstream case,
`TestWorkspaceCollision` (3), `TestCopyProfileSelfPath`, and two command-level
guard cases (`self` refused even with `--yes`; `inside` refused in json without
`--yes`).

**Docs** — `CHANGELOG.md` `[Unreleased]` (3 fix entries), the `canair contribute`
paragraph in `AGENTS.md` (overlay semantics, the new guard, the `--json` keys),
the flow summary in `.claude/skills/contributing-profiles/SKILL.md`, and a
"run it from your own checkout" tip in `docs/bring-your-own-car/08-share.md`.
No `--help` change → `docs/reference/cli/contribute.md` needed no regeneration
(`gen_cli_reference.py --check` clean).

**Not done (deliberately):** the optional broader mitigation in Issue 1 item 6
(hiding the managed clone's bundled profiles from discovery) — it degrades the
error message for every other command run from that directory, so it stays a
separate decision. The merge-driver's own untimed-session reorder (item 3
residue) also stands.

## Gates

`uv run pytest -q` (4392 passed, 1 skipped) · `ruff check`/`format --check` ·
`ty check` · `canair validate all` · `gen_cli_reference.py --check` ·
`gen_screenshots.py --check` — all green.
