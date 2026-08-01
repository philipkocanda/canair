# `canair write` — the deliberately-gated config-DID writability probe

A `write` command that tests whether a suspected config DID is *writable* by
reading its current value (`0x22`) and writing the **same bytes** back (`0x2E`),
then classifying the response. The point is diagnostic ("is this DID
configurable at all?"), not coding.

**This command is the exception that proves the rule.** canair is a
read/diagnostics tool; writing to a real ECU can be irreversible and dangerous.
The command's own friction — a manually-set config flag, `--unsafe`, loud
warnings, mandatory backup, typed confirmation — *is* the lesson. It should be
hard enough that an autonomous agent balks at the warnings rather than sailing
through, and a human has to deliberately opt in on the specific machine.

Decisions below were settled interactively; this doc is the authoritative record
(not yet implemented — captured for later).

## The core tension (why this fights canair's design)

- `0x2E` WriteDataByIdentifier is the **first entry** in
  `BLOCKED_UDS_SERVICES` (`canlib/safety.py:20`), alongside reflash/upload
  services. Every outbound command on **both** transports routes through
  `enforce_command_safety` (`terminal.py:159`, `raw_terminal.py:122`), so a write
  is refused unless `--unsafe`, which then demands an interactive `YES` per
  command.
- Skill non-negotiable **#3**: *"Never break the real car… extend the blocklist,
  never quietly bypass it."* Non-negotiable **#7**: canair ships no `0x27`
  seed→key solver and won't defeat SecurityAccess — so most *interesting* config
  DIDs answer a write with NRC `0x33 securityAccessDenied` anyway.
- A "no-op" same-value write is **not** guaranteed side-effect-free: an ECU can
  recompute checksums, reset learned adaptations/counters, re-latch relays, or
  act on *any* write regardless of value. The genuinely dangerous case is the
  **wrong DID** (airbag/immobilizer/coding), where even a same-value write can
  misconfigure a safety system.

The design keeps the global blocklist intact and layers *additional* gates on
top; it never weakens the default posture.

## Gating model (defense in depth, agent-hostile by design)

A write proceeds only when **all** of these hold — each is an independent,
deliberate act:

1. **A manually-set config flag.** A new user-config key, default absent/false,
   that a human must set by hand on the specific machine:

   ```yaml
   # ~/.config/canair/config.yaml
   allow_config_writes: true   # default false; must be set by a human, per machine
   ```

   Rationale: `--unsafe` is a per-invocation flag an agent can add on its own.
   A config flag is *ambient machine state* the agent shouldn't (and by policy
   won't) flip — it's the "you had to walk over and enable this" gate. canair
   never sets it programmatically; there is intentionally **no**
   `canair config set allow_config_writes true` convenience beyond the generic
   `config set` (which itself prints a stern warning for this key — see below).
2. **`--unsafe`** on the invocation (the existing per-command bypass), which puts
   the terminal into unsafe mode.
3. **Typed confirmation** after the pre-flight read + backup + warning
   (interactive), with a `--yes` scripting escape hatch for the *confirmation
   step only* — `--yes` does **not** substitute for the config flag or
   `--unsafe`.

If the config flag is unset, the command **refuses before touching the bus** and
prints the educational block (why canair is read-only, what `0x2E` risks, that
protected DIDs need `0x27` which canair won't defeat, and exactly how to enable
if you truly mean it). Same refusal (with its own message) when `--unsafe` is
missing.

The warnings are deliberately verbose and alarming so an agent reading them is
expected to stop and surface the risk to its human rather than proceed.

## What the command does (v1: no-op probe only)

`canair write ECU:DID --unsafe` (e.g. `canair write IGPM:BC03 --unsafe`), with
`allow_config_writes: true` set:

1. **Gate checks** (config flag → `--unsafe`) — refuse + educate if either is
   missing, before any bus traffic.
2. **Read current value** (`0x22 <did>`), optionally in an extended session
   (`--session` / `--wake`). Abort if the read fails (nothing safe to write
   back).
3. **Back up** the pre-write read as a normal capture via the existing `--save`
   machinery (journaled, queryable, diffable), labeled e.g.
   `pre-write backup DID BC03`.
4. **Show** the value + a loud warning block, then require typed confirmation
   (`--yes` scripting hatch for this step only).
5. **Write the same bytes back** (`2E <did> <same-data>`), via `send_uds(...,
   expected_sid=0x6E, expected_did=…)`.
6. **Re-read** (`0x22`) to verify the value is unchanged; warn hard if it
   changed.
7. **Classify writability** from the write response:
   - positive `0x6E` → **WRITABLE (accepted)**
   - NRC `0x33` → *not writable without security access — canair does not defeat
     `0x27`*
   - NRC `0x31` requestOutOfRange / `0x22` conditionsNotCorrect / `0x13`
     incorrectMessageLength / `0x11`/`0x12`/`0x7F` not-supported → mapped to
     human meaning (via the existing `NRC_CODES` table in `canlib/uds_parse.py`).
   - `--json` for machine output (`{did, before, after, write_nrc, verdict}`).

**Explicitly out of scope for v1:** writing an *arbitrary* new value
(`--value`). v1 only echoes the read-back bytes — it is a *writability probe*,
not a coding tool. This keeps the risk asymmetry down and stays on-theme.
Revisit only with a separate, even-more-gated design.

## Architecture / files

Follow the `dtc` / `routines` command+mode template. The mode uses **only** the
shared terminal surface (`set_header` / `send_uds` / `enter_extended_session`),
so it works on **both** transports for free (non-negotiable #1) — no
`transport.type` branching, no new bus code.

**New:**
- `canlib/commands/write.py` — argparse surface. Positional `ECU:DID` parsed via
  the shared query mini-language helper (skill: "prefer the mini-language", not a
  `--ecu/--pid` pair). Flags: `--session`, `--wake`, `--yes`, `--json`, plus
  `add_connection_args` / `finalize_live_parser`. `run()` performs the
  config-flag + `--unsafe` gate checks and prints the reminder on refusal.
- `canlib/modes/write.py` — `mode_write(...)`: read → backup-capture → confirm →
  write-back → verify → classify. Builds the request string `"2E"+did+data`.

**Edited:**
- `canlib/safety.py` — minimal single-guard extension: add
  `assume_yes: bool = False` to `enforce_command_safety`; when
  `unsafe and assume_yes`, log "user pre-consented" and proceed without the
  blocking `input()`. This is the scripting hatch required by non-negotiable #0
  (every prompt needs a flag escape). It is **not** a new bypass — still gated on
  `unsafe`, and the write command still requires the config flag above it.
- `canlib/terminal.py` + `canlib/transport/raw_terminal.py` — add an
  `assume_yes` attribute (default `False`); pass it into their
  `enforce_command_safety` calls. Set by the write mode *after* it resolves
  consent. Keeps the guard identical across transports.
- `canlib/config.py` (user-config loader) — recognize `allow_config_writes`
  (bool, default `False`); expose a resolver the write command reads. `config
  set allow_config_writes true` prints a stern one-time warning (this is the
  "arm the write capability" switch). Keep it out of `config.example.yaml`
  except as a commented, warned-about line.
- `canlib/commands/_live.py` — add a `write` selector to `CANAIR_DEFAULTS` (falsy
  default — the skill's AttributeError warning), add the `elif args.write:`
  dispatch branch in `dispatch_mode`, and include `write` in the session-start
  banner mode detection.
- `canlib/commands/__init__.py` — register `"write"` in `COMMAND_NAMES`
  (live-device group, after `raw`).
- `canlib/modes/__init__.py` — export `mode_write`.

## Tests (`tests/test_write.py`, device-free fake terminal)

- Refuses when `allow_config_writes` is unset (prints reminder, **zero** bus
  traffic).
- Refuses when `--unsafe` is missing (even with the flag set).
- Happy path: flag+`--unsafe`+consent → read → write-back → `0x6E` → reports
  WRITABLE; backup capture recorded *before* the write.
- NRC `0x33` → "not writable / security access (not defeated)"; `0x31` / `0x11`
  mapped correctly.
- Declined confirmation aborts before any `0x2E`.
- `--yes` sets `assume_yes` and suppresses the guard's prompt (scripting path)
  **only** with flag+`--unsafe` present.
- Post-write verify detects a changed value and warns.
- `TestDispatchTransportAgnostic`-style test proving it runs through
  `dispatch_mode` (both transports covered).
- `tests/test_safety.py`: `assume_yes=True` proceeds without prompting;
  `assume_yes` with `unsafe=False` still refuses.

## Docs (part of the change)

- `docs/reference/cli/write.md` + link in `docs/reference/cli/index.md`.
- `docs/concepts/safety.md` — add "why writing is the exception, not the norm"
  and document the `allow_config_writes` gate; cross-link from the write page.
- `README.md` command-map line (terse, links into docs).
- `AGENTS.md` tool list entry (note the config-flag + `--unsafe` gate and the
  no-op-only scope).
- `config.example.yaml` — commented, warned `allow_config_writes` line.
- `CHANGELOG.md` `[Unreleased]` bullet.

## Verification

`uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`,
`uv run ty check`, `uv run canair write --help`, `uv run canair validate all`.

## Open / revisit later

- Arbitrary-value writes (`--value`) — deliberately excluded from v1.
- A dedicated `write_log.yaml` audit trail (before/after/NRC) — v1 relies on the
  backup capture; add only if the capture-based backup proves insufficient.
