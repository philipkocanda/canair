# Plan: SecurityAccess (0x27) hardening + pair-solver + BMS session probe

Status: DONE (code + tests: 1252 passed; `canair validate pids` clean). On-car Phase 2
(`security BCM`) and Phase 3b (BMS `--mode 81`) are the remaining user-driven steps.
Context: `canair scan iocontrol/routines BMS` → NRC 0x11 (0x30/0x33 unimplemented in
default session) and `10 03` → NRC 0x12 (BMS rejects the UDS extended-session mode
byte). A generic scanner (Kingbolen) DOES actuate the battery fan, so it's reachable
— the fan is locked behind a **manufacturer KWP2000 diagnostic session + security
access (0x27)**, mirroring GDS. This work de-risks the 0x27 half and prepares the
session half.

## Phase 1 — harden the live SecurityAccess scanner (`modes/multi.py`)

`_exec_security` hardcoded a 4-byte seed (`67 01 SS SS SS SS`, `bytes[2:6]`, key
`%08X`). Change:
- seed = **all bytes after `67 01`**; derive `seed_len` dynamically.
- run algorithms mask-aware to seed width; format the key to the **seed's byte
  length**; send `27 02 <key>`.
- **always print the raw seed hex + length**, even when every algorithm fails (so we
  can record it / match a Kingbolen pair offline).
- 4-byte stays the common path; non-4-byte labelled clearly.

## Phase 1b — offline seed/key pair-solver

`solve_key_pair(seed, key)` iterates `SECURITY_ALGORITHMS` and returns the entries
that reproduce `key` from `seed`. Surfaced as `canair query security --pair SEED:KEY`
(offline — no device connection). Feed it a Kingbolen-sniffed pair to identify/verify
the algorithm with zero car interaction.

## Phase 1c — blocklist hardening (`elm327.check_command_safety`)

Currently blocks only `10 02` (UDS programmingSession). Add:
- block `10 85` (KWP2000 ECU programming mode).
- gate unknown `10 8x` KWP session modes behind `--unsafe` (they may be
  development/programming modes on some ECUs).
Enforced on both transports via `safety.enforce_command_safety`.

## Phase 3a — configurable session mode

`SessionManager.open_session` / `terminal.enter_extended_session` / the `session`
mini-language step hardcode `10 03`. Add a `mode` option (default `03`) so we can open
`10 81` on the BMS: `canair query "session BMS --mode 81" ...`. Programming modes are
still blocked by Phase 1c.

## On-car (user-run)

- Phase 2: `canair query "session BCM --wake" "security BCM"` → validate 0x27, capture
  any ACCEPTED algorithm / record seeds.
- Phase 3b: `canair query "session BMS --mode 81" "raw 7E4:2701"` (fallback `--mode 01`)
  — **minimal whitelist only**; hard-stop + Kingbolen sniff if both fail. No sweep.

## Phase 4 (contingent)

Once session+security work: re-run `scan iocontrol/routines BMS` in the working session
(needs the scanners to accept a session mode — follow-up), then a deliberate single-LID
fan actuation.

## Verify

`pytest -q` + `canair validate pids` green; focused commits.
