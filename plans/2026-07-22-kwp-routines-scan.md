# Plan: KWP2000-aware RoutineControl discovery + 0x31 StartRoutine safety guard

Status: DONE — code + tests green (1240 passed; `canair validate pids` clean).
        `canair scan routines BMS` now uses the safe 0x33 read; end-to-end check
        confirms 0x31 (StartRoutine) is never sent to a KWP2000 ECU. Remaining:
        empirical on-car `canair scan routines BMS` to enumerate routine LIDs (and
        the separate Step A `raw 7E4:300000 --session` 0x30 session-gating test).
Trigger: `canair scan iocontrol BMS` returned `NRC 0x11` — the BMS does not expose
KWP2000 InputOutputControlByLocalIdentifier (0x30) in the default session. The
battery-fan actuator test (as used by Kingbolen) is therefore most likely a
**routine**, and probing routines on a KWP2000 ECU exposes a safety footgun.

---

## 0. The footgun

Service `0x31` means different things per protocol:

| Protocol | 0x31 | Safe "read results" |
|----------|------|---------------------|
| UDS (ISO 14229)     | RoutineControl (`31 {SF} {RID16}`); SF `0x03` = requestRoutineResults (safe) | `31 03 {RID}` |
| KWP2000 (ISO 14230) | **StartRoutineByLocalIdentifier** (`31 {LID} …`) — **actuates** | service **`0x33`** RequestRoutineResultsByLocalIdentifier (`33 {LID}`) |

`canair scan routines` blind-sends `31 03 {RID}`. Against a KWP2000 ECU (BMS/VCU/
MCU/LDC/AAF) that parses as **StartRoutine LID 0x03** → could actuate hardware.
So `canair scan routines BMS` is currently UNSAFE. This mirrors the 0x2F↔0x30
IOControl split exactly and gets the same protocol-aware treatment.

## 1. Changes

1. **`uds_services.py`** — correct routine entries:
   - `0x31` name → "RoutineControl (UDS) / StartRoutineByLocalIdentifier (KWP2000)";
     `safe_discovery_sf=0x03` documented as UDS-only.
   - add `0x32` StopRoutineByLocalIdentifier (KWP2000), `0x33`
     RequestRoutineResultsByLocalIdentifier (KWP2000, `id_width=1`, read-only/safe).

2. **`pids_edit.append_routines_block(..., key_width=4)`** — thread `key_width` so
   KWP routine LIDs write as quoted 2-hex-digit keys (like the KWP iocontrol path).

3. **New `modes/kwp_routines_scan.py`** — safe `0x33 {LID}` probe
   (RequestRoutineResultsByLocalIdentifier; never starts a routine), positive resp
   `0x73`, LID-echo guard, shared classification (0x11 aborts, 0x31 absent, others
   exist). A `DiscoveryProbe` on the shared engine; writeback to `routines:` with
   `key_width=2`. `mode_kwp_routines_scan()` wrapper (default LID range 00-FF).

4. **Dispatch** (`_live.py`, `args.routines_scan`) — split ECUs by `id_protocol`:
   KWP2000 → `mode_kwp_routines_scan` (0x33); others → `mode_routines_scan` (0x31/SF03).
   This is the guard: the UDS 0x31 probe is NEVER sent to a KWP2000 ECU. Same shape
   as the iocontrol split.

5. **Tests** — `test_kwp_routines_scan.py`: probe format `33{LID}`, classify,
   2-digit-key round-trip, and a dispatch test proving `31` is never sent to a
   KWP ECU (only `33`).

6. **Docs/AGENTS** — note KWP routine semantics + `scan routines` auto-selection.

## 2. Related follow-up (separate, pending Step A result)

`canair raw 7E4:300000 --session` (user-run) tests whether 0x30 is merely
session-gated on the BMS. If it responds (not 0x11), add a `--session`/`--wake`
option to `scan iocontrol` and re-sweep. If 0x11 again, 0x30 is out and routines
(this plan) is the path.

## 3. Verify

`python3 -m pytest -q`; `canair validate pids`; then empirically
`canair scan routines BMS` (0x33, safe) to enumerate routine LIDs. Actually starting
a routine (0x31) stays a separate, explicit, confirmed, one-LID step — never in a scan.
