# The query mini-language

`canair query` — and the capture/decode tools — select ECUs and PIDs with a
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

!!! warning "Bind each PID to its ECU with a colon, never a space"
    `IGPM 22BC07` means "all of IGPM **plus** a bogus ECU `22BC07`" — write
    `IGPM:22BC07`. A bare PID in the ECU slot is rejected with a hint. In a query
    step, a space separates *independent ECU selectors*.

## Pipelines

`canair query` also accepts a **pipeline** of steps (each a quoted string), run
in order over one session. A bare selector is shorthand for a `query` step.

```bash
canair query "session IGPM --wake" "query IGPM:BC03,BC06"
```

Step verbs include: `query`, `session <ECU> [--wake]`, `skm-wake [acc|ign1|ign2]`,
`raw <TX:PID>`, `scan`, `iocontrol`, `security`, `sleep`, `repl`. This lets you,
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

To send one by hand, use a query step (`canair query BMS:3E00`).

## After a session

Using the WebSocket terminal overrides the WiCAN's AutoPID mode. Pass `--reboot`
to any live command to restore AutoPID after your session ends.
