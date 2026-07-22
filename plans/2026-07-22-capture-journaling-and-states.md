# Plan: Robust capture journaling + standardized, auto-suggested vehicle states

Status: IN PROGRESS.
Trigger: `canair query --save` (esp. `--monitor`) buffers all captures in memory
and only writes on a clean exit. A crash, `kill`, or dropped connection loses the
whole session. Concretely, in `--monitor` a disconnect raises `ConnectionError`
at `monitor.py:811` *before* `save_captures()` at `816`, so a dropped connection
silently discards everything. Separately, the capture `state` field is free text
and not standardized across vehicle profiles, and there is no way to auto-suggest
it from known PID values (an explicit project goal in AGENTS.md).

---

## A. Capture journaling (write-ahead log)

New module `canlib/capture_journal.py` (keeps `captures.py`, already 429 lines,
from ballooning):

- `CaptureJournal` — append-only JSONL at
  `profiles/<name>/captures/.journal/<UTC-ts>-<pid>.jsonl`.
  - Line 1 = meta record:
    `{"v":1,"type":"meta","date","label","state","notes","source","keep_mode"}`.
  - `append(ecu_ref, pid, hex, time)` → `capture` line, `flush()` + `os.fsync`
    (loses at most the in-flight line).
  - `append_session(session_dict)` → one-shot scan/raw/discover (stores the
    pre-built session verbatim).
  - `update_meta(label, state, notes)` → new meta line; reconcile takes
    **last-wins** (upfront + mid-session edits).
  - `reconcile(keep_mode=None)` → read lines, dedup per keep-mode, build a session
    via `captures.build_query_session` (or use the stored session), call
    `save_session()`, then **unlink** the journal (write-then-delete = atomic).
  - Context manager: reconcile on clean exit; **leave the journal on an
    unclean/exception exit** so it can be recovered.
  - `list_orphans(captures_dir)` / classmethod `recover(path, discard=False)`.
- `.gitignore`: add `profiles/*/captures/.journal/`.

Wiring (all save paths, uniformly):
- `modes/monitor.py` — controller opens a journal in `setup()` when `save`;
  `_record_polled` appends as payloads arrive; **reconcile moved into
  `mode_monitor`'s `finally`** (fixes the disconnect data-loss bug).
- `modes/multi.py` — `_collect_query`/raw append to a journal; `_save_collected`
  becomes a reconcile. A mid-pipeline exception leaves a recoverable journal.
- `modes/scan.py`, `modes/raw.py`, `modes/discover.py` — build the session as
  today but write it through `append_session()` + reconcile.

Recovery UX:
- `canair captures --recover` — lists orphaned journals, reconciles each (session
  `notes` tagged `[recovered]`); `--discard` deletes without saving.
- On any `--save` run start, `list_orphans()` prints a one-line notice.

## B. Standardized state definitions

New `profiles/<name>/states.yaml` — ordered, named states with optional
auto-suggest predicates. Base vocabulary reuses the canonical power states already
in `pids_schema.yaml` `availability:` (`sleep, plugged, acc, acc2, ready,
charging`); profiles may add composite states.

- New `canlib/schema/states_schema.yaml` (tool-owned); `canair validate states`
  folded into `validate all`.
- `Profile.states_file` property; loader in `canlib/states.py`.
- Suggest, don't enforce: `canair validate captures` emits a **soft warning** for
  capture `state`s outside the profile vocabulary (never a hard error).
- `canair profile show` lists defined states.
- Seed `profiles/ioniq-2017/states.yaml` from verified params.

## C. Auto-suggest state

`canlib/states.py`:
- `StateRule` (name, description, compiled predicate).
- `load_states(profile)` — compiles each `when:` once into a **whitelisted AST**
  (`BoolOp and/or`, `UnaryOp not`, `Compare`, dotted `ECU.PARAM` names,
  numeric/string constants, sentinels like `__no_response__`). No `eval()`.
- `suggest_state(values, responded) -> str | None` — first match wins.

Value source & UX:
- Monitor decodes named params each cycle (`decode_param_rows`); build a
  `{"ECU.PARAM": value}` dict + `responded` set, compute suggested state, show it
  in the status line, pre-fill journal/SaveDialog `state`.
- Multi pipeline builds the same dict from collected decoded rows.
- `resolve_metadata`/`prompt_metadata`/`SaveDialog` gain `suggested_state`.

Upfront metadata UX:
- `--label` (agents): non-interactive, journal opens immediately.
- No `--label` on a streaming `--save`: prompt for metadata **before** connecting/
  entering the TUI, so journaling starts at cycle 1.
- TUI `s` becomes edit-metadata/checkpoint: pre-filled SaveDialog, `update_meta()`
  persists instantly.

## Tests
- `tests/test_capture_journal.py` — append/flush, reconcile builds session +
  deletes journal, keep-mode dedup, meta last-wins, orphan list + recover/discard.
- Monitor disconnect regression — dropped connection leaves a recoverable session.
- `tests/test_states.py` — AST safety, first-match ordering, missing-param skip,
  `__no_response__` sentinel, `validate states` schema, captures soft-warn.

## Docs
- Update `--save` sections in `AGENTS.md`, `ioniq-reverse-engineering` and
  `reverse-engineer-pid` skills.

## Execution order
1. Journal core + tests.
2. Wire monitor (fix disconnect) → multi pipeline → scan/raw/discover.
3. `captures --recover` + orphan notice.
4. `states.yaml` schema + loader + `validate states` + captures soft-warn.
5. Auto-suggest evaluator + wire into monitor/pipeline + status line.
6. Upfront-metadata UX + TUI `s` → edit-metadata.
7. Docs + seed `ioniq-2017/states.yaml`.
