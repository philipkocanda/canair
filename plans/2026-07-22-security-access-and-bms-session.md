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

---

## Results so far (on-car, 2026-07-22, SLCAN via 192.168.3.2)

### BCM (0x7A0) — SecurityAccess machinery validated; algorithm NOT in our set
`canair query "session BCM --wake" "security BCM"`:
- `10 03` extended session **accepted** (BCM is UDS).
- `27 01` returns a fresh 4-byte **random seed every request** (e.g. 74FF3B09, CAE8E5FE…).
- All **48 built-in algorithms → NRC 0x35 (invalidKey)** — i.e. correct key *length*,
  wrong *value*. Not `0x13` (length) / `0x11` (unsupported), so our seed parse + 4-byte
  key format are correct; only the transform is unknown.
- **Aggressive lockout**: ~2 key attempts → NRC 0x37 (11s delay); tool auto-waited and
  re-opened the session, so all 48 were tried (complete negative).
- Conclusion: BCM uses a **non-trivial HKMC seed-key** (likely SA2 bytecode/secret),
  not a simple transform. Blind guessing won't crack it → need a sniffed seed→key pair
  (→ `canair query --pair`). **No scanner access currently**, so parked.

### BMS (0x7E4) — session byte identified; no SecurityAccess exposed
- `10 03` (UDS extended) → **NRC 0x12** (subFunctionNotSupported).
- **`10 81` (KWP2000 standard session) → accepted** (positive `50 81`, `session
  established`). This is the KWP session byte the BMS wants.
- **Inside the confirmed `10 81` session, all three still return `NRC 0x11`:**
  - `27 01` (SecurityAccess) → 0x11
  - `30 00 00` (IOControlByLocalIdentifier, IOCP 00) → 0x11
  - `33 00` (RequestRoutineResultsByLocalIdentifier) → 0x11
- So `10 81` was NOT the missing key — the BMS simply does not expose SecurityAccess,
  IOControl-by-LID, or RoutineResults over OBD in any session we can safely reach
  (default / 10 81; 10 03 rejected).

### Safe empirical avenues on the BMS are now EXHAUSTED
Remaining leads, all requiring info we don't have or carrying risk we won't take blind:
1. **`0x31` StartRoutineByLocalIdentifier may still exist.** `0x33` (results) returning
   0x11 does NOT rule out `0x31` — many HKMC actuator tests are fire-and-forget
   StartRoutine with no results service. But `0x31` **actuates**, so we will NOT
   blind-probe it. Needs the exact LID/params from a sniff.
2. **A Hyundai-specific session mode in the `10 8x` band** (blocked by the safety guard
   as potential programming/dev modes). GDS may use one of these before actuation.
3. **Sniff a working tool (Kingbolen/GDS)** — the only path that reveals the exact
   session + service + LID + params in one shot. Blocked on scanner access.

Conclusion: **paused pending a sniff.** The `--session --mode` scanner support (this
change) is still correct/useful generally, but won't crack the BMS fan because
`0x30`/`0x33` are service-absent even in `10 81`.
