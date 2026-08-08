# The query mini-language

`canair read` — and the capture/decode tools — select ECUs and PIDs with a
small, consistent syntax.

## Selectors

A **selector** is `ECU[:PIDLIST]`. (The `BMS`/`IGPM`/`VCU` names below are from
the bundled Ioniq profile, used as examples — the ECU and PID names available are
whatever the *active* profile defines; see `canair ecu`.)

| Selector | Meaning |
|---|---|
| `BMS` | all known PIDs for BMS |
| `BMS:2101` | BMS PID `2101` only |
| `IGPM:BC03,BC06` | two IGPM DIDs (comma-separated) |
| `VCU:2101 BMS:2101` | cross-ECU — a **space separates independent selectors** |

A PID like `2101` vs `22B002` differs by its service-byte prefix — see
[ECU protocols & PID prefixes](ecu-protocols.md) for what the `21`/`22` means and
why canair can resolve a bare identifier.

!!! warning "Bind each PID to its ECU with a colon, never a space"
    `IGPM 22BC07` means "all of IGPM **plus** a bogus ECU `22BC07`" — write
    `IGPM:22BC07`. A bare PID in the ECU slot is rejected with a hint. In a query
    step, a space separates *independent ECU selectors*.

## Groups (`@name`)

A **group** is a named, reusable set of selectors saved per-profile in
`groups.yaml` — a saved query. Recall one on the command line with the `@`
sigil; it expands to its member selectors before the query is parsed, so it
composes with other groups and with ad-hoc selectors:

```bash
canair monitor @charging            # e.g. BMS:2101 BMS:2105 OBC VCU MCU
canair read @driving                # a whole group
canair monitor @driving CLU:220B    # a group plus an extra selector
canair monitor "@charging @powertrain"   # two groups in one step
```

Groups work anywhere `canair read`/`canair monitor` take steps. Members are
plain selectors (ECU or `ECU:PID`) — never other groups, never full pipeline
steps. A group reference carries no PID suffix (`@charging`, not
`@charging:2101`).

List and edit the vocabulary with [`canair groups`](../reference/cli/index.md)
(`add`/`rm`/`rename`/`set-description`/`set-members`) — never hand-edit
`groups.yaml`; the editor re-validates on write. `canair validate groups` checks
every member's ECU exists.

## Overlapping selectors are coalesced

Groups overlap, and a group often already contains an ECU you also named by hand.
`canair read`/`canair monitor` therefore **coalesce every overlapping selector down
to one per ECU** before polling, however many steps or groups they came from:

```bash
canair monitor IGPM OBC AAF @driving
```

If `@driving` already contains `IGPM` and `AAF:2180 AAF:2181`, that names IGPM
twice and AAF three times. Only one block per ECU is polled and rendered, and the
overlap is reported up front so the collapse is never silent:

```
Merged overlapping selectors (each ECU is polled once):
  IGPM ← IGPM ×2
  AAF ← AAF, AAF:2180, AAF:2181
```

The rules:

- **A bare ECU supersedes its `ECU:PID` selectors.** `AAF` already means every AAF
  PID, so `AAF` + `AAF:2180` is just `AAF`.
- **Different PIDs on one ECU union.** `BMS:2101` + `BMS:2105` becomes
  `BMS:2101,2105` — a legitimate combination, so it merges without a note.
- **Aliases resolve to the canonical ECU.** `LDC` and `OBC` are one ECU, so
  `canair read LDC OBC:2101` polls it once.
- **Position is the first mention's.** Coalescing never reorders the display.
- **A non-`query` step ends the run.** A deliberate re-read across a pipeline
  (`canair read "query BMS:2101" "sleep 5" "query BMS:2101"`) keeps both reads.

!!! note "Why this matters in `canair monitor`"
    A duplicated ECU used to be polled twice per cycle — halving the refresh rate
    for no new data — and its rows shared a selection key, so the TUI cursor and
    viewport would snap back to the first copy. Coalescing removes both.

## Pipelines

`canair read` also accepts a **pipeline** of steps (each a quoted string), run
in order over one session. A bare selector is shorthand for a `query` step.

```bash
canair read "session IGPM --wake" "query IGPM:BC03,BC06"
```

Step verbs include: `query`, `session <ECU> [--wake]`, `skm-wake [acc|ign1|ign2]`,
`raw <TX:PID>`, `scan`, `iocontrol`, `sleep`, `repl`. This lets you,
for example, wake an ECU, open a session, and read several PIDs in one command
over one connection.

## Sessions and keepalive

Some ECUs only answer certain requests inside an **extended diagnostic session**.
Opening one (a `session <ECU>` step, or any command's `--session`) does the right
thing automatically:

!!! note "Keeping a session alive is automatic"
    There is no `tester-present` command or flag. Once a session is open, canair
    keeps it alive by sending TesterPresent (`3E00`) whenever the session goes
    idle past the timeout; real request traffic resets that timer, so a busy
    polling loop injects no redundant keepalives. TesterPresent is shared by UDS
    and KWP2000, so it's sent identically regardless of the ECU's protocol.

To send one by hand, use a query step (`canair read BMS:3E00`).

## After a session

Using the WebSocket terminal overrides the WiCAN's AutoPID mode. Pass `--reboot`
to any live command to restore AutoPID after your session ends.
