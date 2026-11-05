# Named protocol constants: read every protocol byte from `canair.uds`

Status: **PROPOSED** (2026-11-05). Phase 0 is done. Not user-facing: no command, flag, output or
profile changes, except where phase 3 names a behaviour change and asks first.

## Why

A diagnostic-protocol byte — a service ID, a response marker, an NRC, a sub-function, the
positive-response offset, a canned request such as `3E00` — is one fact. Today many modules
write that fact again as a bare literal or as their own module constant:

- `NRC_WRONG_SESSION = 0x7F` has four definitions, in `live/sessions_scan.py`,
  `live/routines_scan.py`, `live/iocontrol_scan.py` and `live/kwp_iocontrol_scan.py`. The
  "NRC means the id exists" sets beside them are copied the same way.
- `transport/functional.py` defines `_POSITIVE_OFFSET = 0x40` and `_NEGATIVE_RESPONSE = 0x7F`,
  although `uds/services.py` exports `RESPONSE_SID_OFFSET` and `NEGATIVE_RESPONSE_SID`.
- `SF_START`/`SF_STOP`/`SF_RESULTS` have two definitions (`live/routines.py`,
  `live/routines_scan.py`), and `0x22` has three private names (`_READ`, `_READ_SERVICE`,
  `_READ_BY_ID`).
- `"3E00"` is written out in eight modules.

Three costs follow:

1. **One value, two meanings.** `0x7F` is the negative-response SID and also the UDS NRC
   serviceNotSupportedInActiveSession. A bare `0x7F` does not say which one the code means.
2. **No way to find the users.** A grep for `0x7F` finds masks, ASCII bounds and comments.
   A grep for a name finds exactly the code that depends on the fact.
3. **Copies drift.** This already happened with protocol classification (six copies, fixed by
   `classify_protocol`) and the command blocklist (two divergent copies). The one-byte-id set in
   `uds/echo.py` (`0x01, 0x09, 0x1A, 0x21`) and the `id_width=1` rows of the service registry
   already disagree (the registry also lists `0x02`, `0x30`, `0x32`, `0x33`, `0x3B`).

## Phase 0 — the rule and the ratchet (done, 2026-11-05)

- The **contributing-code** skill states the rule ("A protocol fact has one named home").
- `scripts/check/protocol_literals.py` finds protocol bytes written as literals outside
  `uds/services.py` and `uds/nrc.py`. Its docstring names the four patterns (compare, offset,
  constant, hex string) and what it deliberately does not match (masks, ranges, data tables).
- `tests/contracts/test_structure.py::test_protocol_literals_do_not_spread` holds the count per
  module to the ceiling in `baseline.json` (`protocol_literals`). A new module with a literal
  fails, a module whose count grows fails, and a module at zero must leave the baseline.
  `uv run python -m tests.contracts.test_structure --write` lowers the ceilings.

Baseline at the start: **126 literals in 36 modules.**

## Decisions to make before phase 1

| # | Question | Proposal |
|---|---|---|
| 1 | Name style for SIDs | The ISO service name in upper snake case: `READ_DATA_BY_IDENTIFIER`, `TESTER_PRESENT`. A KWP2000-only service gets a `KWP_` prefix (`KWP_READ_DATA_BY_LOCAL_IDENTIFIER`). Short forms such as `SID_22` say nothing a literal does not. |
| 2 | Positive-response SIDs | One function, `positive_response_sid(sid)`, not a second constant per service. Module-level sets build from it (`{positive_response_sid(READ_DATA_BY_IDENTIFIER)}`). |
| 3 | Home for sub-functions | A new module `uds/subfunctions.py`, one small namespace per service (`RoutineControl.START`, `IoControlParameter.RETURN_CONTROL_TO_ECU`, KWP `0x30` parameters, ReadDTCInformation report types, session modes). `uds/layout.py::SUBFUNCTION_NAMES` then builds from it, so the display names and the constants cannot disagree. The scanner adds it to `HOMES`. |
| 4 | Do the registry rows use the constants? | No. `SERVICES` and the NRC tables stay literal: they *are* the data, and they read like the standard. A test asserts that every SID constant names a registry row and every NRC constant a row of its protocol's table. |
| 5 | Canned requests | `TESTER_PRESENT_REQUEST = "3E00"` (and the suppressed `"3E80"` if a caller needs it) in `uds/services.py`, as hex text, because every caller passes hex text. |

## Phase 1 — add the names (one commit, no caller changes)

1. `uds/services.py`: a `Final` constant for each SID that a caller outside the homes uses, and
   `positive_response_sid()` / `request_sid_of_response()`. Keep `RESPONSE_SID_OFFSET` and
   `NEGATIVE_RESPONSE_SID`.
2. `uds/nrc.py`: constants for the NRCs callers test — at least `0x12`, `0x13`, `0x22`, `0x31`,
   `0x7E`, the UDS `0x7F` and the KWP2000 `0x80` — next to the two that exist. Add one set,
   `WRONG_SESSION_NRCS` (UDS `0x7F`, KWP2000 `0x80`), because `transport/keepalive.py`,
   `live/identity_decode.py` and the scans each restate "not in this session".
3. `uds/subfunctions.py` (decision 3), and `SUBFUNCTION_NAMES` rebuilt from it.
4. Tests in `tests/uds/`: each constant matches its registry or table row (decision 4).

The scanner count does not change in this phase.

## Phase 2 — mechanical replacement (one package per commit)

Replace each literal with the name. No behaviour change: the existing tests pass unchanged, and
each commit lowers the baseline (`--write`). Order, with the current counts:

| Commit | Modules | Literals | What replaces them |
|---|---|---:|---|
| `transport/` | `functional`, `keepalive`, `stats`, `session_manager`, `raw/terminal`, `elm327/session`, `elm327/terminal` | 9 | `NEGATIVE_RESPONSE_SID`, `RESPONSE_SID_OFFSET`, `TESTER_PRESENT`, `TESTER_PRESENT_REQUEST`, `DIAGNOSTIC_SESSION_CONTROL` |
| `live/` scans | `sessions_scan`, `routines_scan`, `kwp_routines_scan`, `iocontrol_scan`, `kwp_iocontrol_scan`, `routines` | 43 | NRC constants and `WRONG_SESSION_NRCS`; sub-function namespaces. The four `NRC_WRONG_SESSION` and the `NRC_EXISTS_HINTS`/`NRC_ABSENT` copies go. A per-scan set that differs on purpose stays, built from names, with a comment that says why it differs. |
| `live/` others | `monitor_raw`, `multi_batch`, `multi_exec`, `interactive`, `discover_functional`, `identity_decode`, `dtc/reading`, `dtc/snapshots` | 17 | as above |
| `safety/` | `actuation`, `blocklist` (the comparisons and the `"3E00"` row) | 8 | SID constants, `RoutineControl` sub-functions |
| `dtc/`, `ecus/`, `interop/`, `captures/`, `cli/` | `dtc/snapshot`, `ecus/did_families`, `interop/frame_log`, `captures`, `cli/commands/research/gaps` | 15 | SID constants, `positive_response_sid()` |
| `uds/` | `multi_did`, `echo` (the `== 0x22`) | 2 | SID constants |

`live/scan.py`, `live/scan_presets.py`, `captures/__init__.py` (the `in (0x22, 0x2F, 0x31)` sets),
`uds/echo.py`'s `_ONE_BYTE_ID_SERVICES`, `uds/describe.py`, `live/skm_wakeup.py`, `live/tester.py`
and `cli/commands/coverage/payloads.py` wait for phase 3, because a faithful rewrite of each
changes or risks changing behaviour.

## Phase 3 — derived facts and text matching (one commit each, each with tests)

These sites restate a fact that the registry already holds, or match protocol bytes inside
text. A rewrite can change behaviour, so each commit adds a regression test first and names any
behaviour change in its body. Ask the user before a change that alters output.

1. **Identifier width.** `service in (0x22, 0x2F, 0x31)` appears five times
   (`captures/__init__.py` twice, `live/scan.py` twice, `live/scan_presets.py::is_wide_service`).
   It means "this service takes a 2-byte id", which is `ServiceInfo.id_width == 2`. The registry
   also gives `0x2E` a width of 2. Check each caller for what `0x2E` would do (the blocklist
   refuses it, so a scan should never reach it), then read the width from the registry through
   one helper. Do the same for `uds/echo.py::_ONE_BYTE_ID_SERVICES` against the `id_width=1`
   rows. Here the sets disagree today, so decide per service whether the echo rule or the
   registry is right, and fix the wrong one.
2. **`uds/describe.py`** (20 literals). A chain of `if sid == 0x50 … if sid == 0x7E` names each
   positive response. Make it a table keyed by `positive_response_sid(<service>)`, so the module
   is a lookup and not a list of magic numbers. Pin the current output for every branch first.
3. **Hex-text matching.** `live/skm_wakeup.py` searches ELM327 text for `"7F2F78"` and
   `"7F2F7F"`, `live/tester.py` counts `"7F3E"`, and `cli/commands/coverage/payloads.py` tests
   `payload[:2] == "7F"`. A substring search over a whole block can match bytes that span a
   boundary or sit inside a positive reply. 6b07390f fixed exactly this in the ELM327 pipe.
   Parse the reply into bytes and use `is_response_pending()` and a matching
   `is_negative_response(reply, sid, nrc)` in `uds/nrc.py`. Add a test with a positive reply
   that contains the old substring.

## Phase 4 — close the ratchet

When the baseline is empty, remove the `protocol_literals` key and make the test absolute: no
module outside the homes may contain a match. Then widen the scanner with what it misses today,
each found by hand while writing this plan:

- range tests on a plain name: `cli/commands/bix.py` (`0x40 <= b <= 0x7F`, `b < 0x40`) and
  `uds/parse.py` (`0x10 <= … <= 0x3E`);
- session sub-function constants without a protocol word in the name:
  `safety/blocklist.py` (`_UDS_PROGRAMMING`, `_KWP_SAFE`, …) and `live/sessions_scan.py`
  (`UDS_SESSION_MODES`, `KWP_SESSION_MODES`);
- the rejected-SID position: `interop/frame_log.py` tests `payload[1] in (0x21, 0x22)`, which
  the scan counts only because the subscript is constant.

Each new pattern lands with its sites fixed in the same commit, so the absolute test stays green.

## Out of scope

- Test payloads and fixtures. A test that reads `62 01 01 …` checks the constants independently.
- Bit masks (`sub & 0x7F`), ASCII and byte ranges, padding bytes, CAN ids and addressing
  defaults (`0x08`, `0xF1`). These are not diagnostic-service facts.
- Profile data. `ecus/` YAML writes requests as hex text by design.
