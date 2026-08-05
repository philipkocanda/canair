# Layering and module-size follow-ups from the captures split

Status: **PLANNED** (2026-08-05). Two independent parts; B is cheap and low-risk,
A is the bigger call. Neither is user-facing.

Both were found and deliberately deferred while splitting
`commands/captures.py` — see `plans/2026-08-05-captures-command-package-split.md`
("Deliberately out of scope"). That work fixed one instance of the layering
inversion (`load_all_captures`/`resolve_pid_defs`/`decoded_preview` →
`canlib/capture_store.py`); Part A here is the larger remaining instance.

---

## Part A — the validators are library API living in the command layer

`canlib/commands/validate/` holds the profile validators. But the *dependency
graph already treats them as library code*:

| Consumer | Imports | Why |
|---|---|---|
| `canlib/signals_edit.py` (3×) | `check_signals_doc` | the editor's rollback guard |
| `canlib/ecus_edit.py` | `load_schema`, `validate_pids_file` | allowed identity fields; post-write validation |
| `canlib/commands/pids.py` | `validate_pids_file` | the `_guarded` edit wrapper |
| `canlib/commands/_promote.py` | `check_pci_bytes` | expression sanity before writing a candidate |
| `canlib/commands/ecu.py` | `_run_pids` | re-validate after `ecu ... edit` |
| **13 test modules** | `collect_pids_validation`, `validate_meta`, `load_schema`, `validate_ecu_file`, `_capture_*_warnings`, … | they test the validators, not the CLI |

Two library modules and thirteen test modules import *up* into
`canlib.commands`. That is the same inversion `capture_store.py` just fixed, and
it is structural rather than incidental: the **edit → validate → revert on
failure** loop is a library behaviour (`pids_edit`/`ecus_edit`/`signals_edit`/
`states_edit`/`groups_edit` all rely on it), so the pure check cannot live above
the code that needs it.

### Supporting evidence

- **`load_schema` calls `sys.exit(1)`** on a malformed schema
  (`commands/validate/pids.py:19-26`). A library function reached from
  `ecus_edit._allowed_identity_fields()` can kill the process — acceptable for a
  CLI verb, not for the editor path.
- **`validate/pids.py` is 1441 lines**, well past the smell line, and mixes the
  pure collection (`collect_pids_validation` returns `(errors, warnings, stats)`)
  with ~20 `print()` reporting sites. The seam already exists; it just isn't a
  module boundary.
- **`first_run.py:108` imports `commands.profile.create_profile`** with the comment
  *"local import to avoid cycles"* — the comment is the finding. `create_profile`
  even documents itself as "Pure of argparse — callable from the CLI and the
  first-run wizard alike."

### Proposed shape

Split each validator domain along the seam that already exists: **pure checks
down, reporting up.**

- **New `canlib/validation/` package** (peer of `canlib/schema/`, which it
  validates against) holding the pure functions: `pids.py`, `captures.py`,
  `signals.py`, `other.py` — everything that takes parsed data and returns
  errors/warnings/stats. No printing, no `sys.exit`; `load_schema` raises instead.
- **`canlib/commands/validate/` becomes the CLI shell**: argparse, the `_run_*`
  reporters, exit codes. It imports down.
- **`create_profile` moves to the library** (`canlib/profile_create.py`, or
  `profile.py` if it fits without bloating it), leaving `commands/profile.py` as
  CLI. `first_run.py`'s lazy import becomes a plain one and the comment goes away.

Follow the `capture_store.py` precedent: **no re-export shims** — rewire every
call site, including the 13 test modules, and let the old import path fail loudly.

### Risks and sequencing

- **The 13 test modules are the main cost** and also the main safety net: they
  already pin validator behaviour precisely, so a pure move is well covered.
- **`canair validate all` output must stay byte-identical.** Pin it *first* with a
  golden (`tests/_golden.py` + a fixture profile — the trick that caught two real
  regressions during the captures split), then move code.
- Do **one domain per commit** (pids → captures → signals/other → `create_profile`),
  each independently green. `validate/pids.py` is the big one; consider splitting
  its 1441 lines by concern in the same pass rather than moving a monolith.
- Watch for a cycle: `canlib/validation/pids.py` needs `pids`/`ecus`/`profile`/
  `states`/`can_buses`, none of which may import it back. `capture_store.py` shows
  the shape (module-level for leaves, deferred for `profile`).

---

## Part B — the last oversized modules in `commands/captures/`

After the split, three modules are still worth a look. **Two are genuinely over
the line; the third is not — correct the record:**

| Module | Lines | Verdict |
|---|---:|---|
| `step_model.py` | 748 | over — split |
| `step_tui.py` | 611 | over — split |
| `query.py` | **331** | **fine** — leave it |

`query.py` was 489 lines mid-refactor and is often quoted at that number; moving
the data layer to `capture_store.py` took it to 331. It still holds four loosely
related groups (ANSI constants, JSON shaping, the QUERY mini-language, and the
keying/dedup/grouping primitives), but nothing outside the command layer needs any
of them and it is comfortably under the smell line. **Not a task — revisit only if
it grows.**

Both remaining files come from the Textual multi-PID stepper
(`plans/2026-08-04-captures-step-textual-multi-pid.md`) and are one concern each
too many:

- **`step_model.py`** — `StepModel` is a ~30-method god object. The clearest
  seam is the **jump list**: `JumpTarget`, `JumpList`, `jump_targets()`,
  `_note_target`/`_note_placeable`/`_note_block`, `_session_frame` are a
  self-contained "where can I jump to, and how is it labelled/searched" concern.
  Extract to `step_jump.py`; `StepModel` delegates. Follows the C6
  delegate-to-collaborator precedent (`MonitorRawPoller`/`MonitorEditor`).
- **`step_tui.py`** — the Textual `App` plus its modals. Extract the modals
  (`PidSelectModal`, `JumpModal`, …) to `step_modals.py`, mirroring the existing
  `canlib/tui_modals.py` split.

**Oracle:** `tests/test_captures_step.py` has 111 tests, several driving the
Textual app directly, plus `test_tui_help.py`. A pure extraction should not need
new tests — but a renderer or model helper that becomes independently testable
should get one.

**Risk:** low-medium and purely mechanical, but the TUI tests are the only thing
standing between a bad extraction and a broken interactive view, so run them
serially (`-n0`) as well as in parallel.

---

## Ordering

Part B first — independent, cheap, and it finishes the captures package rather
than leaving it half-tidied. Part A is a separate decision with a real cost;
its golden-first step is what makes it safe.

## Out of scope

- **Folding `validate`'s CLI into the library.** The verb, its argparse surface
  and its exit codes stay a command; only the pure checks move.
- **The mode-as-flag redesign** for `captures uds` (sub-subcommands instead of
  seven mutually exclusive flags) — tracked in the captures split plan; it is
  user-facing and needs its own deprecation path.
- **`cmd_summary`'s session count** — already fixed (`e33152e`).
