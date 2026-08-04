# Multi-PID stacked compare in `captures --step` (Textual port)

Status: in progress (2026-08-04)

## Motivation

Cross-comparing several PIDs of the same ECU over time is a core
reverse-engineering move: *"show me the HVAC duct temperatures while the AC
compressor toggles"* means reading `HVAC:220100` (compressor duty / interior
temp), `HVAC:2201A0` (mode/blower/setpoints) and `HVAC:2201A2` (heat power)
**at the same instant**.

What existed before:

- `captures --step` walked matching captures **one per frame**, interleaved
  chronologically across the selected PIDs. To compare two PIDs you had to
  remember frame N-1 while looking at frame N.
- `captures --step --pair` stacked **exactly two** (ECU, PID) keys, joined by
  nearest timestamp within `--join-tol`. Hard-coded to two keys
  (`_build_pair_frames` returned `key_a`/`key_b`), read-only (no note/delete),
  the PID set was fixed at launch, and the tolerance could only be set on the
  command line.
- Both were rendered by a **hand-rolled cbreak + full-redraw Rich loop** — the
  last non-Textual interactive view in the tree. No scrolling, so a frame taller
  than the terminal was simply unreadable. Three stacked HVAC blocks are
  11 + 17 + 4 = 32 param rows plus headers and hex ≈ 50+ lines: structurally
  impossible to display.
- Piped, `--step` fell back to `--diff`; piped, `--step --pair` **silently did
  nothing**, and `--json` was rejected outright — so the pair capability was
  reachable *only* from an interactive TTY, violating the "no TUI-only
  capability" rule (contributing-code non-negotiable #0).

## What ships

`canair captures uds "HVAC:220100,2201A0,2201A2" --step` stacks the three PIDs
underneath each other in one time-joined frame. N is unbounded; the PID set and
the join tolerance are editable **from inside the TUI**.

Decisions taken with the user up front:

1. **One Textual app for both step views** — single-PID stepping is just the
   N=1 case. Kills the last non-Textual view; brings scrolling, `?` help
   (shared `HelpMixin`), and real modal screens.
2. **`--pair`/`-P` removed** (breaking). The PID set comes from the query
   mini-language alone; any multi-key query stacks. No alias kept — the
   capability lives on as the default.
3. **Union-anchored frames.** Every *timed* capture of any selected PID anchors
   a frame; the other PIDs are nearest-joined within `--join-tol`; consecutive
   frames with an identical joined index-tuple collapse. Hides nothing (a
   strict superset of what `--pair` showed) and reads naturally for
   event-driven captures.
4. **The stepper's own join default, `DEFAULT_STEP_JOIN_TOL_S = 10s`**, wider
   than the shared `align.DEFAULT_JOIN_TOL_S` (5s). A full round-robin `monitor`
   cycle over several ECUs spans ~8-10s, so two PIDs polled in the *same* cycle
   can sit >5s apart and would not be joined — the frames split and the
   comparison disappears (observed in the bundled profile: `HVAC:220100` and
   `HVAC:2201A0` 8.5s apart). The stepper can afford the looser window because
   it is a **viewer** — every block reports its `Δt`, so an over-wide join is
   visible and self-correcting. `align`/`correlate`/`hunt` keep the tighter
   default, where a loose pairing would silently move a coefficient.
5. **Per-block cursor** (`tab`/`shift+tab`, `▶` marker) so `e` (note),
   `d` (delete) and `x` (drop PID) act on a chosen block — compare mode reaches
   parity with single-PID stepping rather than staying read-only.

### View modes

`--view {auto,stacked,signals,changed,interleaved}` (default `auto`), cycled
in-TUI with `V`:

| view | content |
|---|---|
| `stacked` | header + param table + byte-diff hex per block |
| `signals` | params only, no hex — the 3-PID HVAC case fits one screen |
| `changed` | per block, only params whose decoded value moved since that PID's previous capture, + the hex diff |
| `interleaved` | the pre-existing one-capture-per-frame chronological walk |

`auto` picks `stacked` for N ≤ 6 keys and `interleaved` above it. That threshold
exists because `canair cap BMS --step` (all ~20 PIDs of an ECU) is a real
browse workflow that stacking 20 blocks would ruin; it is a *display default*
only, overridable with one keypress or the flag. The flag exists so the view is
also selectable non-interactively (rule 0).

### Keys

| Key | Action |
|---|---|
| `→` `l` `n` `space` / `←` `h` `p` | next / prev frame |
| `]` `[` | ±100 frames |
| `g` `G` | first / last frame |
| `:` | goto frame # (prompt modal) |
| `↑` `↓` `j` `k` PgUp PgDn wheel | scroll within the frame |
| `tab` `shift+tab` | move the block cursor |
| `a` | add/remove PIDs (filterable multi-select modal) |
| `x` | drop the focused block's PID |
| `t` | set join tolerance (prompt modal); `<` `>` step the ladder |
| `V` | cycle view · `r` rulers · `u` unique/all payloads |
| `e` | edit focused capture's note · `d` delete it (confirm modal) |
| `?` help · `q` `esc` quit |

### Non-interactive paths

- Piped `--step` renders the last `--limit` frames (default 50, `0` = all) with
  the *same* renderer — replacing the `--diff` fallback and the pair no-op.
- `--json --step` is now **allowed** and emits
  `{view, tol_s, keys, frames:[{time, blocks:[{ecu, pid, time, dt_s, payload, decoded}]}]}`.

## Architecture

`_captures_step.py` was 615 lines and printed straight to a `rich.Console`, so
it had to be split and made framework-free before a Textual shell could consume
it. The shape follows `decode --plot` (`_decode_plot.py` model +
`_decode_plot_tui.py` shell), the cleanest TUI split in the tree: a
UI-framework-free state+render model, plus a thin Textual app, so the non-TTY
path renders with zero Textual involvement.

| File | Role |
|---|---|
| `_captures_query.py` | pure data layer (load, select, key/group/dedup); loses `_pair_by_time`/`_build_pair_frames`, gains `key_index` |
| `_captures_join.py` | **new** — the N-way union join: `JoinFrame`, `build_join_frames`, `_nearest_within` (split out to keep `_captures_query.py` under the ~500-line smell) |
| `_captures_step_render.py` | **new** — the renderers, refactored from `console.print(...)` to *return* `rich.text.Text`; one renderer serves TTY, piped, and Textual |
| `_captures_step_model.py` | **new** — framework-free `StepModel`: key set, tolerance, view, cursors, `rebuild()`, `render()`, `to_json()` |
| `_captures_step_tui.py` | **new** — `CapturesStepApp(HelpMixin, App)` + `PidSelectModal` |
| `canlib/tui_modals.py` | **new** — shared `TextPromptModal` (moved out of `_decode_plot_tui.py` now that two TUIs need it) + `ConfirmModal` |
| `_captures_step.py` | shrinks to the entry point: build model → TTY / piped / JSON |
| `captures.py` | `--pair` removed, `--view` added, `--join-tol`/`--limit` retargeted, `--json` allowed with `--step` |

### The join

```
inputs : captures (chronological, deduped unless --all), keys (block order), tol_s
per key: sorted index list + epoch array; untimed captures counted, skipped
anchors: every timed index, ordered by (dt, key order)          # union
row    : per key, the nearest index with |Δt| ≤ tol_s (bisect), else None
         (an anchor's own key always self-matches at Δ=0)
collapse: drop a frame whose index tuple equals the *previous* frame's
          (consecutive only, so a repeating pattern later still shows)
returns : list[JoinFrame(anchor_dt, indices)], n_no_time
```

The nearest-within-tolerance tie rule mirrors `align.join_prepared` (smaller
|Δ| wins, earlier on a tie). A test asserts **parity with `join_prepared`** on a
shared fixture, so the two joins cannot silently diverge — the old
`_pair_by_time` only *claimed* alignment with `canlib.align` in a docstring.

## Behavior changes (user-visible)

1. `--pair`/`-P` removed; a multi-key query with `--step` now stacks and joins
   instead of interleaving one capture per frame.
2. `--step` is a Textual app: the frame scrolls, so PgUp/PgDn now scroll
   (`[`/`]` keep ±100 frames) and `?` opens the shared help modal.
3. `--json --step` works (previously an error).
4. Piped `--step` renders frames statically instead of falling back to `--diff`;
   `--limit` now applies to that static render.

## Incidental fixes (Boy Scout)

- **The `?` help modal rendered raw Textual key identifiers** (`]` showed as
  `right_square_bracket`) because `tui_help._display_key` used a hand-written
  table covering six names. It now delegates to Textual's own `format_key`,
  which also fixed `monitor`'s and `decode --plot`'s cheat-sheets; a test pins
  "no raw identifiers" across every shipped TUI.
- **An all-untimed selection reported "No captures" and `frame 1/0`** — now it
  says the captures exist but can't be placed on a timeline, and points at
  `--view interleaved`.

- **A capture's `label` was silently swallowed** by the step view: it was
  interpolated as `f"  [{escape(label)}]"` into a Rich *markup* string, so
  `[ac-on]` was parsed as a style tag and vanished. Building the header as a
  `Text` (rather than printing markup) makes capture-owned free text
  unparseable as markup by construction, and the label now shows.
- `TextPromptModal` was private to `_decode_plot_tui`; it moved to the shared
  `canlib/tui_modals.py` alongside a new `ConfirmModal`.
- `cmd_step_pair`'s `captures_dir` parameter was resolved but never used (the
  view was read-only); gone with the function.

## Follow-up: session & note jump list (`s`)

Navigating a 2400-capture session frame by frame is impractical, so `s` opens a
jump list: every session in scope (newest first — date, span, state, label,
counts) with its **noted captures** nested underneath. A session row lands on
that session's first frame; a note row lands on the noted capture.

Design points worth recording:

- **Locators, not indices.** `StepModel.captures` is rebuilt on every key /
  tolerance / dedup change, so a jump target addresses a capture by its
  immutable `(file, session_idx, capture_idx)` locator (`CaptureRef`) and
  resolves it through two lookups rebuilt in `rebuild()`: locator → capture
  index, capture index → containing frame.
- **The jump makes its target visible.** Selecting a note whose PID isn't in the
  comparison **adds that PID**, and unique-payload dedup is **lifted** when it
  hid the target — both named in the status line, both undone by `x` / `u`.
  Reporting "can't show that" when the user unambiguously asked to go there
  would be obtuse.
- **Only relevant sessions are listed.** A session earns a row by having a frame
  for the current selection *or* by carrying notes; one with neither offers
  nowhere to go, so it is omitted and counted in the footer (`JumpList
  .hidden_sessions`) instead of padding the list with unreachable rows — on the
  bundled profile that is 161 of 223 sessions for a two-PID HVAC comparison. A
  session kept purely to head its notes is a non-selectable heading and shows no
  inline reason; the reason belongs on the note.
- **A note the current view cannot place is omitted, not dimmed.** Untimed
  captures have no frame in a stacked view, and 314 of this profile's 315 notes
  are untimed legacy rows — listing them all left 39 of 62 sessions as blocks of
  dead rows. They are dropped and counted (`JumpList.hidden_notes`), with the
  footer pointing at `captures --sessions`; the interleaved view, needing no
  timestamps, lists and reaches them normally. Net effect on the default view:
  23 sessions, 0 dead blocks.
- **A note *no* view can render is listed, disabled, with the reason** — one on a
  non-payload capture (`response`/`scan_results`). Unlike an unplaceable note,
  which is one keypress away, flagging it is the only way to surface it at all. Untimed captures are deliberately *not* given frames:
  they are legacy data slated for removal, so the join stays timestamp-only.
- **Session grouping moved to the data layer.** `_group_sessions`/`_SessionGroup`
  → `_captures_query.group_sessions`/`SessionGroup` (plus a new `noted` field
  carrying the noted entries). `captures.py` imports `_captures_step`, so the TUI
  importing `captures.py` would have been circular — and the pure grouping
  belongs beside the loader anyway. `cmd_sessions` and the jump list now share
  one implementation, so the two can't disagree.

Non-interactive parity needs no new flag: `canair captures --sessions` already
lists every session with its distinct capture notes.

## Status

Landed. `pytest` (3605), `ruff`, `ty`, `validate all`, `gen-check` green;
verified interactively over a pty and against the bundled profile's 54k captures
(1923 joined frames for the three HVAC PIDs; every live mutation < 0.15s).
