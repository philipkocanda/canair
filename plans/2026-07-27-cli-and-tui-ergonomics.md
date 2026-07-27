# canair — CLI & TUI Ergonomics

Status: implementation plan (backlog). A grab-bag of ergonomics/quality-of-life
improvements gathered 2026-07-27 from hands-on use. Two themes: **CLI** (defaults,
time-scoping, session shortcuts, help discoverability, domain labelling) and
**TUIs** (converge the decode/query/plot interfaces, add discoverability, richer
in-TUI editing/annotation, live controls). Each item is independently shippable;
tackle them as separate, individually-scoped changes, not one mega-PR. Every item
below carries tests + docs per the contributing skill.

Baseline pointers (verify before editing — these drift):

- Keep-mode flags: `canlib/commands/query.py` (`--keep-unique/--keep-all/--keep`),
  resolved in `_live.py`, applied in `canlib/modes/monitor.py`
  (`MonitorController`), raw path `canlib/modes/raw_monitor.py`. Downstream
  keep-unique awareness centralized in `canlib/keepmode.py`.
- Date/scope flags: the single shared `canlib/capture_dates.py`
  (`parse_iso_date`, `add_scope_args`, `resolve_date_bounds`,
  `filter_by_date_range`, `entry_datetime`). Consumers: `captures.py`,
  `decode.py`, `correlate.py`, `hunt.py`, `investigate.py`. `--first/--last`
  are **decode-local**, not in the shared helper.
- CLI wiring: `canlib/cli.py` (`build_parser`, `_GROUP_DEFAULTS`,
  `_inject_default_subcommand`), `canlib/commands/__init__.py` (`COMMAND_NAMES`),
  `canlib/commands/_group.py` (`group_help`).
- TUIs: query `--monitor` is **Textual** (`MonitorApp` in
  `canlib/modes/_monitor_tui.py`, controller in `monitor.py`); `sniff` is Textual
  (`_sniff_tui.py`); `decode --plot` is **hand-rolled ANSI/termios**
  (`canlib/commands/_decode_plot.py::cmd_plot`); captures `--step` is Rich + raw
  ANSI (`_captures_step.py`). Shared helpers only in `canlib/tui.py`.
- ECU alias: identity field (`pids_schema.yaml` `alias`), resolved in
  `canlib/ecus.py` (`canonical_ecu_name`, `build_canonical_name_index`); the
  `ecu` report displays it, the monitor does not.

---

## Part A — CLI enhancements

### A1. Make `--keep-unique` the monitor default (avoid multi-MB capture files)

**Problem.** A `--monitor --save` session with the default keep-mode retains
*every* payload, producing capture YAML files megabytes in size (a recent
overnight session file ballooned). `keep:unique` (rising-edge dedup) captures the
information that matters for RE at a fraction of the size.

**Change.** Flip the monitor default so unique-dedup is on unless the user asks
otherwise. Add an explicit opt-out that restores full retention.

- In `query.py`'s mutually-exclusive keep group, invert the default: absent any
  flag ⇒ `keep_mode="unique"`. Keep `--keep-all` as the explicit "retain every
  payload" escape hatch and `--keep N` for last-N. Consider renaming/aliasing so
  the intent reads well (`--keep-unique` becomes the implied default; `--keep-all`
  stays the override).
- Update the resolution in `_live.py` and defaults so a non-monitor path is
  unaffected. Confirm the raw path (`raw_monitor.py::_keep_mode`) mirrors it.
- **Trade-off to weigh (surface to user):** unique-only drops dwell time and
  falling edges, and every analysis that consumes it must respect
  `keep:unique` semantics. The `keepmode.py` warnings already fire for
  `correlate`/`decode --corr-transform`/`investigate`/`hunt` — audit that the
  warning coverage is complete now that unique becomes the *common* case (it was
  previously the exception). Time-rate transforms (`delta`/`cumsum`/`--lag-scan`)
  are unreliable on unique data — the warnings must be prominent.
- **Docs:** this is a shifted default — update `README`, `docs/`, `AGENTS.md`
  (the `--save`/monitor prose), and the RE skills (capture workflow). Note the
  `--keep-all` override for when full time-series is genuinely needed
  (e.g. computing a rate).

**Alternative to consider:** rather than change the default globally, keep the
current default but (a) warn loudly when a `--save` session's file crosses a size
threshold, and/or (b) auto-suggest `--keep-unique`. Decide during implementation;
flipping the default is simpler and matches the "sane default" principle, but is a
behavior change. **Recommend flipping** with a clear `--keep-all` opt-out.

### A2. `--since`/`--until` accept timestamps, not only dates

**Problem.** Scope flags only parse `YYYY-MM-DD` (`parse_iso_date`). Within a busy
day of captures you can't narrow to a time window — you get the whole day.

**Change.** Extend the shared scope parser to accept a **date-or-datetime**:
`YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, `YYYY-MM-DD HH:MM:SS`, and with fractional
seconds (`HH:MM:SS.ffffff`). This is all in one place — `capture_dates.py`:

- Replace `parse_iso_date` usage for `--since`/`--until` with a
  `parse_iso_datetime`-style parser that returns a `datetime` (bare date ⇒
  midnight for `--since`, end-of-day for `--until` to keep inclusive semantics).
  Keep `--date` as a whole-day shorthand.
- `resolve_date_bounds` and `filter_by_date_range` must compare against the
  per-capture `entry_datetime` (date + `time`) when a time component is present,
  falling back to date-only comparison otherwise. `entry_datetime` already
  combines session date + capture time — reuse it.
- Preserve backward compatibility: a bare `YYYY-MM-DD` behaves exactly as today.
- **Consumers get it for free** (captures/decode/correlate/hunt/investigate all
  route through `add_scope_args`) — but add tests per consumer proving a
  time-window narrows results.
- **Docs:** update the scope-flag description everywhere it's documented
  (`AGENTS.md` scope prose, `docs/reference`), and the `metavar`
  (`YYYY-MM-DD[THH:MM:SS]`).

**Contributing-doc rule (do this as part of A2).** Add a standing convention to
the contributing skill: **any new time-bound/scope flag should accept a timestamp
down to microseconds by default, not date-only.** Record it under a "Time &
scoping conventions" note so future flags don't repeat the date-only limitation.

### A3. `--today` and `--last-session(s)` shortcuts

**Problem.** The two most common scopes — "just now / today" and "the session I
just recorded" — require manually typing dates or knowing session boundaries.

**Change.** Add convenience scope flags, ideally in the **shared**
`add_scope_args` so every analysis command gains them at once:

- `--today` — shorthand for `--date <today>` (mutually exclusive with
  `--since/--until/--date`).
- `--last-session` / `--last-sessions N` — restrict to the most recent recorded
  session (or last N). A "session" is the journaled `--save` segment; the session
  index is already computable (`captures uds --sessions` builds the TOC). Factor
  the session-boundary resolution into a shared helper so scope filtering can map
  "last N sessions" → a set of session ids / a datetime bound.
- Apply to **at least** `decode` and `correlate` (the ask), and — since it's in
  the shared helper — `captures`, `hunt`, `investigate` too. Confirm no command
  breaks from the new attributes (mirror the `CANAIR_DEFAULTS` discipline for live
  commands; these are analysis commands so it's argparse-local).
- **Interaction with A2:** `--today` is a date shorthand; `--last-session` is
  session-scoped and should compose with `--state`/`--label` filters.
- **`--json` + non-interactive**: pure flags, already scriptable.
- **Docs:** scope-flag reference + examples ("`decode MCU 2102 --today`",
  "`correlate --last-session`").

### A4. Each command's help notes its domain: UDS / CAN / both

**Problem.** It's not obvious from `--help` whether a command operates on
diagnostic UDS captures (domain A), raw broadcast-CAN frame logs (domain B), or
both. The group commands (`captures`/`correlate`/`hunt`/`investigate`/`import`)
already say `uds | can` in their top line, but single-domain commands
(`query`, `scan`, `dtc`, `decode`, `coverage`, `pids`, `sniff`, `signals`,
`export`, …) don't advertise their domain.

**Change.** Add a short, consistent **domain tag near the top of every command's
help/description**. Pick a uniform convention, e.g. a leading
`[UDS]` / `[CAN]` / `[UDS+CAN]` marker in the `description=` (and/or a line in the
epilog). Suggested mapping (verify per command):

- **UDS (domain A):** `query`, `scan`, `dtc`, `identity`, `routines`, `io`,
  `discover`, `decode`, `coverage`, `pids`, `import uds`, `research`.
- **CAN (domain B):** `sniff`, `signals`, `import can`/`import dbc`, `export dbc`,
  the `can` kind of the group commands.
- **Both:** `captures`, `correlate`, `hunt`, `investigate` (group commands with
  `uds`/`can` kinds), `validate`, `bix`.

Keep it terse — one token, not a paragraph. Consider centralizing the tag strings
so they're consistent (a small constant, or a helper that prefixes
`description=`). **Docs:** the command *map* in `README`/`AGENTS.md` could echo the
same tag for at-a-glance scanning.

### A5. Universal `help` positional alongside `--help`

**Problem.** Only argparse's built-in `-h/--help` works; `canair captures help`
or `canair help decode` does nothing useful. Users (and agents) reach for the bare
`help` word.

**Change.** Accept `help` as a universal token, mapping to the same output as
`--help`:

- **Top level:** `canair help` ⇒ top-level help; `canair help <command>` ⇒ that
  command's `--help`. Implement in `cli.py` by rewriting argv before parsing
  (near `_inject_default_subcommand`): translate a leading `help`/`help X` into
  the `-h` form (`canair -h` / `canair X -h`). This composes with group defaults
  (`canair help captures` → `canair captures -h`).
- **Per-command / per-group:** `canair captures help` ⇒ group help;
  `canair captures uds help` ⇒ that kind's help. Handle by recognizing a trailing
  `help` token and converting to `-h`, being careful not to clobber a legitimate
  positional named `help` (none exist today — verify).
- Keep it a pure argv-rewrite so it's uniform across *all* commands/groups without
  touching each subparser.
- **Tests:** `help`, `help <cmd>`, `<cmd> help`, `<group> <kind> help` all match
  their `-h` equivalents; a value that merely *contains* "help" (e.g. an ECU/PID
  arg) is untouched.
- **Docs:** mention the `help` alias once in `README`/`AGENTS.md`.

---

## Part B — TUI enhancements

Guiding principle (contributing non-negotiable #0): TUIs are a *convenience layer*
over already-scriptable commands — never the *only* way to reach a capability.
Every TUI affordance below must have a non-interactive equivalent.

### B0. Converge on one TUI framework (prerequisite for B1–B2)

There are three interactive stacks today: Textual (query monitor, sniff),
hand-rolled ANSI/termios (decode plot, io, routines), and Rich+ANSI (captures
step). The query monitor's Textual app is the mature, "fancy" one the user wants
elsewhere. `MonitorApp` and `SniffApp` are near-duplicate Textual apps with no
shared base — extract a small shared Textual base (scroll `#body` + docked
`#status`, `ansi-dark` theme, common bindings, modal helpers) before porting a
third consumer onto it. This de-duplicates and gives the decode TUI a home.

### B1. Port the decode/plot TUI to the Textual framework

**Problem.** `decode --plot` is hand-rolled ANSI/termios — inconsistent with the
query monitor's look/feel, and harder to extend (no modals, manual key handling,
manual redraw).

**Change.** Reimplement `_decode_plot.cmd_plot` as a Textual app on the shared
base from B0, preserving all current behavior: braille line chart, bytes-mode
(`u8..f64` × endianness sweep) vs param-mode, transforms, overlay reference with
live Pearson `r`, x-zoom/pan, the captures-in-view modal (`i`), the mapped-byte
flagging, and the equivalent-WiCAN-expression readout. Keep the braille/interpret
primitives (`_Braille`, `interpret_bytes`, `wican_expr`, `apply_transform`) —
they're data logic reused by `xanalysis`; only the *shell* changes.

- Non-TTY fallback (print one static frame) must survive.
- This is a UI reimplementation, not a feature change — snapshot current keybinds
  and reproduce them, then layer B2/B4 on top.

### B2. Switch PID inline in the decode TUI (modal picker)

**Problem.** Decode's ECU/PID is fixed at launch (`canair decode MCU 2102 …`);
exploring another PID means quitting and relaunching. In-plot you can only change
byte offset / param of the *current* PID.

**Change.** Add a modal PID picker (Textual `ModalScreen`, mirroring the monitor's
`SaveDialog`/`EditParamDialog`) bound to a key (e.g. `p`). It lists ECU:PID
candidates (from the profile registry, ideally filtered to those with captures in
scope), and on select re-loads the plot's capture set for the new PID without
leaving the app. Requires factoring decode's PID-resolution + capture-loading
(currently one-shot in `decode.run`) into something re-callable from the TUI.

- **Non-interactive parity:** the CLI positional selection already covers this;
  the modal is pure convenience.
- Depends on B1 (needs the Textual shell + modal support).

### B3. Help menu in every Textual TUI via `?`

**Problem.** The Textual TUIs' keybindings are only discoverable from the CLI
`--help` text and the terse status line. There's no in-TUI cheat-sheet.

**Change.** Add a `?` binding opening a modal (Textual `ModalScreen`) that lists
all keybindings + their actions, **derived from each app's `BINDINGS`** so it
can't drift. Put the "bindings → help modal" helper on the shared base (B0) so
**every** Textual TUI gets `?` for free — land it in **both** the query monitor
(`MonitorApp`) **and** the sniff TUI (`SniffApp`), and inherit it automatically in
the ported decode TUI (B1).

### B4. Decode-plot annotation + PID rename

**Problem.** While exploring in the plot TUI you spot something worth recording (a
byte's meaning, a capture worth noting, a better PID name) but have to leave and
run a separate `pids`/`captures` command.

**Change.** Add annotation affordances in the (now Textual) decode TUI, each
routing through the **existing surgical/validated editors** (never hand-editing):

- **Annotate a PID or byte:** open a note modal; write into the PID's `notes`
  (via `canair pids set-identity`/`upsert-param --notes`) or a param's notes. For
  a raw byte with no param yet, offer to create an unverified candidate param at
  that offset (via `pids upsert-param`, like `--promote` elsewhere).
- **Annotate a capture:** attach/edit a capture note via the `canlib.captures`
  helper (`set_capture_note`) — the same mechanism the monitor's save modal uses.
- **Rename a PID:** modal → `canair pids rename-pid ECU OLD NEW` (surgical,
  comment-preserving, validated).
- Every action must be reproducible from the CLI (it *is* — these wrap existing
  subcommands); the TUI just saves a round-trip. Confirm-before-write for mutating
  actions, consistent with the contributing "mutative operations" rule.
- Depends on B1.

### B5. Query TUI: true frame counter (captured vs displayed)

**Problem.** The monitor shows cycles + ELM `cmds`/`req` + per-PID history depth,
but **no total-frames-received counter**, and the displayed rows don't equal the
number of frames actually captured — a genuinely misleading gap the user flagged.

**Change.** Track total CAN frames/responses received in `MonitorController`
(initialize alongside the other live counters; increment as responses arrive on
both the ELM and raw paths) and surface a `captured N · displayed M` (or similar)
figure in the status line (`_update_status`). Model it on the sniff TUI's
`SniffStats.total_frames` pattern. Make the distinction explicit so "displayed ≠
captured" is understood at a glance (especially relevant once A1 makes
unique-dedup the default — displayed unique rows will differ from raw frames
captured).

- Ensure the counter reflects `--save`d frames too, and reads sensibly under
  `keep:unique` (distinguish "frames seen" from "unique payloads retained").

### B6. Query TUI: dynamically change polling rate

**Problem.** The poll interval (`--monitor [INTERVAL]`) is fixed at launch;
adjusting it means quitting and relaunching.

**Change.** Add keybindings to change `controller.interval` live (e.g. `+`/`-` to
step the interval, or a modal to type an exact value). The poll loop already
sleeps `interval - elapsed` in chunked slices (`_poll_loop`), so reading the
interval each cycle is enough; just mutate it and reflect the new value in the
status line. Guard a sane minimum. Document the new keys (and B3's help modal
will list them automatically).

### B7. Captures TUI: show ECU alias when present

**Problem.** The captures stepper shows canonical `ECU:PID` keys but not the ECU's
`alias` (the secondary self-identified name, e.g. SMK for SKM), which is useful
context. The monitor likewise shows `NAME (0xTX)` without the alias.

**Change.** When an ECU has an `alias` in its identity, display it alongside the
name in the captures stepper (and consider the same in the monitor row label):
`SKM (alias SMK)` or `SKM/SMK`. Resolve via the existing
`canonical_ecu_name`/registry lookup — the alias is already in the identity
record; just surface it. Keep it terse and only when an alias exists.

---

## Cross-cutting notes

- **Order of work:** A1–A5 are independent and can land in any order. For Part B,
  **B0 → B1 → {B2, B4}** is a dependency chain (Textual shell first); B3, B5, B6,
  B7 are independent of the decode port.
- **Non-interactive parity** (contributing #0): B2/B4 wrap existing subcommands;
  don't introduce any TUI-only capability. B5/B6/B7 are display/control only.
- **Both transports** (contributing #1): B5/B6 touch `MonitorController`, which is
  transport-agnostic (ELM + raw paths) — cover both.
- **Docs** (every user-facing change): update `docs/`, `README` command map,
  `AGENTS.md` tool reference, and the RE/contributing skills as each item lands.
  A1 (default flip), A2 (flag format), A3 (new flags), A4 (help tags), A5 (help
  alias) are all user-facing. A2 additionally adds a *convention* to the
  contributing skill.

## Verification

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair <touched-cmd> --help      # parser + domain tag sane
uv run canair validate all              # if schema/data touched (A1 keep-mode metadata)
```
