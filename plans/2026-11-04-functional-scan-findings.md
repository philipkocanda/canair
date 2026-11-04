# Functional-scan findings: what to fix

Status: **DONE except the on-car check** (2026-11-04). The 2026-11-03 on-car `discover --functional` runs (Ioniq, READY,
`wican-ws`, WiCAN Pro v4.50p) found seven things. This plan says which are code, which are profile
data and which need no change, and does them. It also closes section 7 of
`profiles/hyundai-ioniq-ae-ev/research/FUNCTIONAL_ADDRESSING_FINDINGS.md`.

## Findings and decisions

| # | Finding | Kind | Decision |
|---|---|---|---|
| 1 | CLU answers a physical `3E00` but never the functional one (3 rounds, 1 s window). | Car fact | Record it on the CLU and in the research doc. The report cannot tell "ignores the functional request" from "asleep", so its Silent section says so and names the physical check. |
| 2 | AMP answered one of two sweeps at 300 ms and all three rounds at 1 s. | Code | `DEFAULT_WINDOW_S` goes from 0.3 to 1.0, the only value tested to catch every responder. `--window` still overrides it. |
| 3 | BMS answers `3E00` with NRC 0x11, so a keepalive does nothing. | Code | Stop sending `3E00` to an ECU once it answers NRC 0x11 (serviceNotSupported). Three senders: `SessionManager`, the ELM327 loop, the raw loop. NRC 0x7F/0x80/0x22 do not stop it: they mean "not now", not "never". |
| 4 | TesterPresent answers by group: 0x7F (WPC, BCM, MFC, EPB), 0x80 (VCU, MCU, OBC), 0x22 (Unknown-746). | Car fact | A table in the research doc. No code: these ECUs do answer, so a keepalive still resets nothing it should not. |
| 5 | The DDM note "the part number repeats in many fields" is the repeat-previous-reply behaviour. | Profile data | Fix the DDM and ADM notes. Their F100 (`170`) is a real answer, so add `boot_sw` to both. |
| 6 | EPB serial `170601000039`; BCM serial `1705310070`, F100 `180`, F193 `100`, F195 `220`, F194 `0880`, F1A2 `620`. | Profile data | Add through `pids set-identity`. F194 and F1A2 have no identity field, so they go in the BCM note. EPS and SCC also answered a serial the profile lacks; add those too. |
| 7 | BSD-L and BSD-R return different F190/F188 bytes on each run. | Car fact | Not a VIN. Say so in both notes. The VIN label in `identity` stays: it is the standard DID name. |

## Changes

1. `canlib/session_manager.py`, `canlib/transport/elm327_session.py`,
   `canlib/transport/raw_terminal.py`: end the keepalive for an ECU that answers `3E00` with NRC
   0x11. One log line per ECU. `NRC_SERVICE_NOT_SUPPORTED` goes in `canlib/uds/nrc.py`.
2. `canlib/transport/functional.py`: `DEFAULT_WINDOW_S = 1.0`. Help text of `--window`, the CLI
   reference and `docs/` follow.
3. `canlib/modes/discover_functional.py`: the Silent heading says the ECUs "did not answer the
   functional request", with a dim line pointing at `canair raw ECU:3E00`.
4. Profile data through the editors, never by hand.
5. Research doc: results of section 7, the TesterPresent table, the CLU and AMP facts.

## Not changed

- No automatic physical probe of silent ECUs. It would add a read per silent ECU to a one-shot
  sweep; the dim line costs nothing. Revisit if the question comes up again.
- NRC 0x7F/0x80 keepalives stay: in a session those ECUs may accept `3E00`, and nothing shows
  they do not.
- Captures saved through the old "one lower" tolerance stay as they are.

## Verification

Device-free tests for each code change. The car was asleep when this was written, so nothing here
was re-read on it; the window and the keepalive need one run in READY:
`canair discover --functional` (24 responders including AMP) and `canair monitor BMS:2101
--session` (no `3E00` after the first refusal, visible with `--verbose`).
