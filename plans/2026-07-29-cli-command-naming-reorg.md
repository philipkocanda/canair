# CLI command naming & structure reorganization

A review of the `canair` command surface for first-time-user intuitiveness,
and a concrete reorganization. Two phases:

- **Phase 1 (committed, specified in full):** promote `monitor` to a top-level
  command; rename `query` → `read`.
- **Phase 2 (decided):** the analysis commands (`captures`, `decode`, and the
  `correlate`/`hunt`/`investigate` trio) keep their current names and structure;
  the intuitiveness gap is closed with **documentation**, not renames. See
  Phase 2 for the rationale and the doc action items.

A separate **north-star appendix** (end of file) records the from-scratch *ideal*
structure (`car`/`data`/`dig`/`map` groups, domain inferred from the operand).
It is not committed work — it exists to steer the incremental phases in a
consistent direction.

## Motivation

The command surface grew organically. Two clusters aren't intuitive to a
first-time user and don't reward memorization:

1. **The "talk to device" verbs** — `query` / `scan` / `discover` / `raw` all
   "send a request", but the names don't encode the *distinguishing axis*
   (known-vs-unknown signal × PID-vs-ECU × decoded-vs-raw). A newcomer can't
   derive "read a PID I already know" → `query`.
2. **`captures` vs `decode`** — the split is really *raw payloads/bytes*
   (`captures`) vs *parameter values over time* (`decode`), but neither name
   conveys that, and `captures` is a **noun** while almost every other command
   is an imperative **verb** (`query`, `scan`, `decode`, `correlate`, `hunt`,
   `investigate`). `routines` and `logs` are the other noun outliers.

Additionally, the `query` command is badly overloaded: it is simultaneously a
one-shot **read**, a live **monitor** (a full Textual TUI — scroll, follow-tail,
pause, live save modal, `● REC`, keep-modes, live interval change), and a
**record** surface (`--save`/`--label`/`--state`/`--notes`). The monitor is a
headline feature buried inside a flag.

## Architectural facts (grounding)

- All live-device commands (`query`/`scan`/`discover`/`io`/`routines`/
  `identity`/`dtc`/`repl`) are **thin argparse surfaces** over one shared
  runtime: `canlib/commands/_live.py::async_main` → `dispatch_mode`. Each sets a
  mode flag on a shared namespace (`CANAIR_DEFAULTS`).
- The monitor is already a self-contained mode (`canlib/modes/monitor.py::
  mode_monitor`), dispatched when `args.multi and args.monitor` are set
  (`_live.py::dispatch_mode`, first branch). It runs on both the ELM
  (`_monitor_tui.py`) and raw (`raw_monitor.py`) transports.
- **`--save` is already cross-cutting** — it works on `query`, `raw`, `scan`,
  `discover`, and `--monitor` (see `async_main` ~L413–428). Recording is *not*
  query-specific; it's a property of any capture-producing live read.
- The monitor TUI **already supports live interval changes** at runtime:
  `=` (faster) / `-` (slower) — `_monitor_tui.py:347-349`, `608-621`.
- Command registration is `canlib/commands/__init__.py::COMMAND_NAMES` (import
  order = help order). argparse `aliases=[...]` is already used
  (`io.py`→`iocontrol`, `repl.py`→`interactive`, `profile.py`→`init`/`new`).
- Group defaulting lives in `canlib/cli.py::_GROUP_DEFAULTS` (e.g. `captures` →
  `uds`, `scan` → `range`, `ecu` → `show`).
- CLI reference pages under `docs/reference/cli/` are **generated** by
  `scripts/gen_cli_reference.py`, keyed off each module's `NAME`.
- Tests target `mode_monitor`/`MonitorController` **directly** (mode-level), not
  the `query` argparse surface → promoting `monitor` has near-zero test churn.

---

## Phase 1 — Promote `monitor`; rename `query` → `read` (COMMITTED)

### Decisions (locked)

- **`canair monitor`** becomes a top-level command (was `query --monitor`).
- **`query` → `read`**, keeping `query` as an argparse alias (zero breakage for
  the read path).
- **`--monitor` is removed from `read`** — `canair monitor` is the only way to
  monitor. No friendly redirect: `read` simply doesn't declare `--monitor`, so a
  leftover flag gets the **standard argparse "unrecognized arguments" error**.
- **Interval = `--interval` flag** on `monitor` (default 5.0s), justified because
  the TUI already adjusts it live with `=`/`-`. No positional interval.
- **`--save`/`--label`/`--state`/`--notes`** remain the cross-cutting record
  modifier on `read`/`monitor`/`scan`/`discover`.
- Regenerate the CLI reference; **drop `query.md`**.

### Known break (document it)

`canair query … --monitor` now fails with a standard argparse
unrecognized-argument error. The `query`→`read` alias preserves the *read* path;
monitoring moves to `canair monitor …`. Note in the changelog / migration.

### Changes

1. **`read` command** — `canlib/commands/query.py` → `read.py`; `NAME="read"`;
   register `aliases=["query"]`. Drop `--monitor`, `--keep-*`, `--rulers`
   (monitor-only). Update docstring/epilog to `canair read …`.
2. **New `monitor` command** — `canlib/commands/monitor.py`, a thin surface like
   `read.py`. Positional `STEP` args; flags `--interval` (default 5.0),
   `--keep-unique/--keep-all/--keep`, `--save/--label/--state/--notes`,
   `--rulers`, `--include-static`, `--session/--wake`, connection args. `run()`
   sets `args.monitor = args.interval` and `args.multi` from steps, reuses
   `read`'s up-front step validation, then calls `run_live` — reusing the
   existing `dispatch_mode` monitor branch unchanged.
3. **Registration** — `commands/__init__.py`: `query`→`read` in `COMMAND_NAMES`,
   insert `monitor` right after it (LIVE DEVICE group).
4. **Categories** — `commands/_categories.py`: `read` + `monitor` under LIVE
   DEVICE; preserve the `[UDS]` help tag.
5. **Docs (same change):**
   - Regenerate `docs/reference/cli/` → `read.md` + `monitor.md`; remove stale
     `query.md`; update `reference/cli/index.md`.
   - `README.md` command map: `query`→`read`, add a `monitor` line (terse, links
     into docs).
   - 10 `docs/` prose files referencing `canair query`: `getting-started/
     first-read.md`, `concepts/query-mini-language.md`,
     `concepts/captures-and-states.md`, `bring-your-own-car/{overview,05-capture,
     06-analyze,07-define-and-verify}.md`, `reference/cli/{index,repl}.md`.
     **Do NOT rename the `query` mini-language *step verb*** (the pipeline
     `query` step in `concepts/query-mini-language.md` stays); only the
     top-level command changes.
   - `AGENTS.md`: rewrite the `canair query` entry → `read`; add a `canair
     monitor` entry; move the extensive `--monitor` TUI/keys/`--save`/keep-mode/
     `● REC`/live-interval docs under it; note the break.
   - Skills: `reverse-engineer-signal/SKILL.md` capture step (`canair query …
     --monitor` → `canair monitor …`); scan `ioniq-reverse-engineering` for
     `query`.
6. **Internal docstrings** — `_live.py`, `monitor.py` (~L10-13), `_monitor_tui.py`
   (L1) usage lines `canair query --monitor` → `canair monitor`.
7. **Tests** — new parser-level test: `monitor` registered; `query` resolves to
   `read` (alias); `canair monitor STEP` sets `args.monitor`+`args.multi`.
   Existing `test_monitor.py` (mode-level) unchanged.
8. **Gates** — `uv run pytest`; `uv run canair {--help, read --help, monitor
   --help, query --help}`; `uv run python scripts/gen_cli_reference.py`.

---

## Phase 2 — Analysis-command naming/structure (DECIDED: no renames, document instead)

The analysis commands (`captures`, `decode`, and the `correlate`/`hunt`/
`investigate` trio) were reviewed with the same "reorganize functionality, not
just rename" lens. The findings were real but the **decision is to keep the
current names and structure and fix the confusion with documentation** — the
churn/breakage of renaming or restructuring outweighed the intuitiveness gain,
and the boundaries are legible once written down.

### `captures` — keep as-is (name and flags)

Findings (recorded for context): `captures` bundles three kinds of work on one
flag namespace — **inspect** (default list, `--diff`, `--step`/`--pair`,
`--latest`, `--summary`, `--sessions`), **mutate** (`--delete`, `--recover`),
and **maintain** (`migrate`/`merge-driver`, already sub-kinds). The `uds`/`can`
domain split occupies the subcommand slot, so *kinds* are subcommands but *modes*
are flags.

Options considered and **rejected**: rename → `inspect`/`payloads`; split
mutation (`--delete`/`--recover`) into an explicit surface; a full
`captures list|diff|step|…` subcommand grammar (rejected — collides with the
`uds`/`can` split, three levels deep for the everyday command).

**Decision:** keep `captures` unchanged. Action item is **docs only** — make the
raw-bytes-vs-values boundary explicit (see below).

### `decode` — keep as-is; document the single-vs-cross-PID split

`decode`'s own docstring already states the intended boundary: payload/byte-level
views → `captures`; parameter/value-centric → `decode`. `decode` also carries
cross-signal analysis (`--corr`/`--plot`/`--discriminate`/`--find-mirrors`/
`--dump-bytes`) that overlaps the dedicated `correlate`/`hunt`/`investigate`
commands.

**Decision:** keep both (decode's analysis = *single-PID* convenience; the trio =
*cross-PID*). Document the distinction so a user knows when to reach for which.

### Documentation action items (the actual Phase 2 work)

1. **`captures` ↔ `decode` boundary** — a crisp one-liner in each command's help
   epilog and in `docs/`: *"`captures` = raw payloads/bytes (hex, byte-diff,
   dedup, session TOC); `decode` = decoded parameter values over time."* (The
   `decode` docstring already says this; mirror it into `captures` and the docs
   concept pages.)
2. **`decode` vs `correlate`/`hunt`/`investigate`** — document the
   **single-PID (decode) vs cross-PID (trio)** rule of thumb where the analysis
   flags are described (help + `docs/`).
3. **The "talk to device" cluster** (`query`→`read`, `scan`, `discover`, `raw`) —
   a short **"which command do I use?"** decision note (`discover` = find
   *ECUs*; `scan` = find *PIDs* on an ECU; `read` = read a *known* PID; `raw` =
   escape hatch). Docs, not renames.

### Names left unchanged (with rationale)

- `captures`, `decode` — see above (docs, not renames).
- `scan`, `discover`, `raw` — keep; fixed by the decision note above.
- `routines` — keep (UDS domain term, groups with `io`/`dtc`/`identity`).
- `logs` — keep (universal convention).
- `research` — keep (reads fine).

### Rejected direction

A `canair live read|monitor|scan|…` group (truest to the shared-runtime
architecture) was considered but rejected for over-nesting everyday commands and
the largest breaking surface.

---

## Migration mechanics (all phases)

- Every rename adds the **old name as an argparse alias** — old invocations and
  scripts keep working; help and docs lead with the new name.
- Update `COMMAND_NAMES`, the `[UDS]`/`[CAN]` category tags, `_GROUP_DEFAULTS`
  keys where a group command is renamed, `docs/`, `README.md`, `AGENTS.md`, and
  the skills **in the same change** (docs-currency rule). Regenerate the CLI
  reference. Verify internal `.md` cross-links still resolve.

---

## Appendix — North-star design (a from-scratch ideal)

This appendix records the **clean-slate design** the tool would adopt if built
from scratch today. It is **not committed work** — Phases 1–2 above are the
pragmatic, incremental, back-compatible steps. The north star exists to **steer
those increments in a consistent direction** so we don't rename toward a shape we
later regret. Any future rename should move *toward* this picture (behind
aliases), never away from it.

### The design

```
car     ── the live vehicle
  read  monitor  scan  discover  sniff  raw
  actuate  routine  identity  dtc  repl  status

data    ── your recordings (inspect + manage + move in/out)
  list  show  latest  sessions  summary  rm  recover
  import  export  migrate

dig     ── study the recordings
  decode  correlate  hunt  investigate  coverage  research

map     ── the signal map you draw (the knowledge base)
  param  signal  ecu  bus  validate

wican   ── sync the device's on-board AutoPID profile
  build  push  mode

profile ── vehicle profiles
  list  show  use  create

── flat top-level (genuinely cross-cutting) ──
  config   bix   logs   update   completion
```

The mnemonic: **drive the `car` → collect `data` → `dig` through it → draw the
`map`** → `wican` pushes what you learned back to the device.

### The three principles doing the heavy lifting

1. **Short, real prefixes.** `car`/`data`/`dig`/`map` are 3-char *required*
   namespaces — cheap enough to type (`car read`, `dig decode`, `map param`), so
   you get the discoverability of groups without a token tax. (Chosen over both a
   flat namespace and longer group words like `device`/`analyze`/`define`
   precisely because verbosity was the deciding value.)

2. **Domain inferred from the operand — never selected.** One `dig correlate`,
   `dig hunt`, `data show`, `data import`; whether it's diagnostic (domain A) or
   broadcast (domain B) **falls out of the operand** — an `ECU:PID` selector
   (`BMS:2102`) is diagnostic, an arbitration ID / frame-log (`0x386`,
   `drive.blf`) is broadcast. This **removes the `uds`/`can` sub-kind level
   entirely** (today's `captures uds …`, `correlate can …`) and needs no
   `--domain` flag. The distinction stays visible only where it is *intrinsic*:
     - **acquisition** — `car scan` (you request) vs `car sniff` (you listen) are
       different verbs because the *action* differs, not because you pick a domain;
     - **authoring** — `map param` (a WiCAN expression over a reassembled payload)
       vs `map signal` (a linear map over a broadcast frame) differ because the
       *definition shape* differs.

3. **One consistent verb vocabulary across every group.** `list`/`show`/`add`/
   `rm`/`set`/`read` mean the same thing everywhere. The `ecu` overlap resolves by
   intent: **`car ecu`** inspects the live registry, **`map ecu`** authors it.

### Notable departures from today's CLI

- `query` → `car read`; the live monitor promoted → `car monitor` (Phase 1 is the
  first real step toward this).
- `captures` (flag-overloaded: inspect + mutate + maintain) → decomposed into
  `data` verbs (`list`/`show`/`latest`/`sessions`/`summary`/`rm`/`recover`) plus
  `data import`/`export`/`migrate`. *(This intentionally diverges from the
  conservative Phase-2 "keep `captures`" decision — the north star is the ideal,
  Phase 2 is the pragmatic near-term.)*
- `uds`/`can` sub-kinds → **gone**, replaced by operand inference.
- `pids` → `map param`; `signals` → `map signal`.
- `io` → `car actuate`; `routines` → `car routine`.
- `wican`/`profile` **kept** as-is (short and clear enough already); `config`/
  `bix`/`logs`/`update`/`completion` **stay flat** (genuinely cross-cutting —
  grouping them would be ceremony for no gain).

### How to use this appendix

- When a Phase adds/renames a command, prefer the north-star name as the new
  canonical (old name as an alias) **when the cost is low**. Don't force the whole
  regrouping in one breaking change — converge incrementally.
- The `car`/`data`/`dig`/`map` prefixes are the biggest single move and would be a
  large, breaking restructure; treat adopting them as its own future phase with a
  full alias/migration plan, not a drive-by.
