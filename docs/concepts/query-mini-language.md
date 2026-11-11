# The query mini-language

`canair read`, the capture tools and the decode tools select electronic control units (ECUs) and
parameter identifiers (PIDs) with one small syntax.

## Selectors

A **selector** is `ECU[:PIDLIST]`. The names `BMS`, `IGPM` and `VCU` below come from the bundled
Ioniq profile and serve as examples. The ECU and PID names that you can use are the names that the
*active* profile defines. Run `canair ecu` to see them.

| Selector | Meaning |
|---|---|
| `BMS` | all known PIDs for BMS |
| `BMS:2101` | BMS PID `2101` only |
| `IGPM:BC03,BC06` | two IGPM DIDs (comma-separated) |
| `BMS:01/0D` | OBD-II mode 01 PID `0D` (request `010D`) — see below |
| `VCU:22` | every VCU PID starting with `22` (all its service-22 DIDs) |
| `VCU:2101 BMS:2101` | cross-ECU — a **space separates independent selectors** |

A data identifier (DID) is the 2-byte identifier of a Unified Diagnostic Services (UDS) read.
OBD-II is On-Board Diagnostics II.

A PID token picks every PID that **starts or ends** with the token. `BC03` picks `22BC03`, and `22`
picks every `22xxxx` DID. It never picks a PID that contains the token only in the middle. `read`,
`monitor` and the capture tools all use this rule.

One difference exists. In a live read, a token that *is* a defined PID picks only that PID. `0101`
reads `0101` and not also `220101`. The capture tools list both.

Two PIDs such as `2101` and `22B002` differ by their service-byte prefix. See
[ECU protocols & PID prefixes](ecu-protocols.md) for what the `21` and `22` mean and why canair
can resolve a bare identifier.

### Naming the service: `SERVICE/ID`

canair sends a PID that the profile does not define with a service that it infers from the length
of the token:

- Two hex digits are a Keyword Protocol 2000 (KWP2000) local identifier (`01` → `2101`).
- Four digits that start with `21` are a KWP2000 read as written (`2101`).
- Any other four digits are a UDS service-22 DID (`B001` → `22B001`, `2201` → `222201`).
- Six digits that start with `22` are a DID read as written (`22BC03`).

This makes `0101` the DID `0x0101`. That DID is a real identifier on some ECUs. For this reason,
`0101` can never also mean OBD-II mode 01 PID `01`.

canair skips anything else with a warning and does not send a guess. Examples are an odd number of
digits, or a longer request such as `BC0301`. Write such a request with `SERVICE/ID`, or send it
with a `raw` step.

Inference applies only when no defined PID matches. A short token such as `21` or `22` picks the
defined pages. To read the bare identifier, use the explicit form (`21/22` for local identifier
`0x22`).

Write the service and the identifier with a `/` to say exactly which request you mean:

| Token | Request | What it is |
|---|---|---|
| `01/0D` | `010D` | OBD-II mode 01 (current data), PID `0D` |
| `09/02` | `0902` | OBD-II mode 09 (vehicle information), InfoType `02` (the VIN) |
| `21/01` | `2101` | KWP2000 local identifier `01` |
| `22/0101` | `220101` | UDS DID `0x0101` |

```bash
canair read BMS:01/00,01/20 --save --label "BMS mode 01 support"   # support bitmaps
canair captures uds BMS:01/00                                       # their captures
```

A `SERVICE/ID` token matches **only** that request code. This holds in `read`, `monitor` and the
capture tools. `BMS:01/00` therefore never picks a `220100` DID, as a bare `0100` can with
prefix/suffix matching.

Only these services are accepted, each with a single identifier. A multi-PID mode 01 request
(`01 0C 0D`) is not accepted.

canair still finds a PID that the profile *defines* by its key. A mode 01 PID defined as `010D`
decodes with its signals whether you write `01/0D` or `010D`.

Quote an all-digit key in YAML, for example `"0105"`. Unquoted, YAML reads it as an octal number.
`canair validate pids` reports this.

!!! warning "Bind each PID to its ECU with a colon, never a space"
    `IGPM 22BC07` means "all of IGPM **plus** a bogus ECU `22BC07`". Write `IGPM:22BC07`. canair
    rejects a bare PID in the ECU slot and prints a hint. In a query step, a space separates
    *independent ECU selectors*.

## Groups (`@name`)

A **group** is a named, reusable set of selectors. canair saves it for each profile in
`groups.yaml`. It is a saved query. Recall a group on the command line with the `@` sigil. canair
expands it to its member selectors before it parses the query. A group therefore composes with
other groups and with ad-hoc selectors:

```bash
canair monitor @charging            # e.g. BMS:2101 BMS:2105 OBC:2101 VCU:2101 …
canair read @driving                # a whole group
canair monitor @driving CLU:220B    # a group plus an extra selector
canair monitor "@charging @powertrain"   # two groups in one step
```

Groups work anywhere that `canair read` and `canair monitor` take steps. Members are plain
selectors (ECU or `ECU:PID`). A member is never another group and never a full pipeline step. A
group reference carries no PID suffix (`@charging`, not `@charging:2101`).

List and edit the vocabulary with [`canair groups`](../reference/cli/index.md)
(`add`/`rm`/`rename`/`set-description`/`set-members`). The editor validates again on write.
`canair validate groups` checks that the ECU of every member exists.

## Overlapping selectors are coalesced

Groups overlap, and a group often already contains an ECU that you also named by hand.
`canair read` and `canair monitor` therefore **coalesce every overlapping selector down to one
selector per ECU** before they poll. This holds however many steps or groups the selectors came
from:

```bash
canair monitor IGPM OBC AAF @driving
```

Suppose `@driving` already contains `IGPM` and `AAF:2180 AAF:2181`. Then the command names IGPM
twice and AAF three times. canair polls and renders only one block for each ECU. It reports the
overlap up front, so the collapse is never silent:

```
Merged overlapping selectors (each ECU is polled once):
  IGPM ← IGPM ×2
  AAF ← AAF, AAF:2180, AAF:2181
```

The rules:

- **A bare ECU supersedes its `ECU:PID` selectors.** `AAF` already means every AAF PID. `AAF` +
  `AAF:2180` is therefore `AAF`.
- **Different PIDs on one ECU form a union.** `BMS:2101` + `BMS:2105` becomes `BMS:2101,2105`.
  This is a legitimate combination, so canair merges it without a note.
- **Aliases resolve to the canonical ECU.** `MDPS` and `EPS` are one ECU, so
  `canair read MDPS EPS:220101` polls it once.
- **Position is the position of the first mention.** Coalescing never reorders the display.
- **A non-`query` step ends the run.** A deliberate re-read across a pipeline
  (`canair read "query BMS:2101" "sleep 5" "query BMS:2101"`) keeps both reads.

!!! note "Why this matters in `canair monitor`"
    canair used to poll a duplicated ECU twice for each cycle. This halved the refresh rate for no
    new data. The rows of the duplicate also shared a selection key, so the TUI cursor and
    viewport snapped back to the first copy. Coalescing removes both problems.

## Pipelines

`canair read` also accepts a **pipeline** of steps, each a quoted string. It runs the steps in
order over one session. A bare selector is shorthand for a `query` step.

```bash
canair read "session IGPM --wake" "query IGPM:BC03,BC06"
```

The step verbs include `query`, `session <ECU> [--wake]`, `skm-wake [acc|ign1|ign2]`,
`raw <TX:PID>`, `scan`, `iocontrol <ECU> <DID> [--off]`, `sleep` and `repl`. With them, you can
wake an ECU, open a session and read several PIDs in one command over one connection.

An `iocontrol` step switches an actuator on (or off with `--off`) in the session that its entry
declares. When the pipeline ends, canair releases every actuator that it switched on and did not
switch off. It does this before it closes the sessions, and also when you press Ctrl-C:

```bash
canair read "iocontrol IGPM BC0C" "sleep 2" IGPM:BC03   # the defogger is released at the end
```

## Sessions and keepalive

Some ECUs answer certain requests only inside an **extended diagnostic session**. When you open
one, canair handles the details. You open a session with a `session <ECU>` step or with the
`--session` flag of any command.

!!! note "Keeping a session alive is automatic"
    There is no `tester-present` command or flag. Once a session is open, canair keeps it alive.
    It sends TesterPresent (`3E00`) whenever the session goes idle past the timeout. Real request
    traffic resets that timer. A busy polling loop or scan therefore sends no redundant keepalives.

    UDS and KWP2000 share TesterPresent, so canair sends it the same way for any ECU protocol.
    Every command and every transport opens and keeps sessions the same way.

`--wake` (or `session <ECU> --wake`) rouses a sleeping ECU before the session request. The wake
depends on the ECU:

- If the profile of the ECU declares a `wake:` ritual, the ECU gets that ritual. The ritual is a
  burst of cheap reads. It suits a module such as a smart key unit, which sleeps again within
  seconds.
- Any other ECU gets a `10 01`. canair asks twice if the first request draws no answer.

To send TesterPresent by hand, use a raw step (`canair read "raw 7E4:3E00"`). As a query token,
`3E00` would be read as the DID `0x3E00`.

## After a session

Using the WebSocket terminal overrides the AutoPID mode of the WiCAN. Pass `--reboot` to any live
command to restore AutoPID after your session ends.
