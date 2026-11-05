# Named protocol constants: read every protocol byte from `canair.uds`

Status: **IN PROGRESS** (2026-11-05). Phases 0 to 2 are done; 33 literals in 8 modules are
left for phase 3. Not user-facing: no command, flag, output or
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

## Decisions (2026-11-05)

| # | Question | Decision |
|---|---|---|
| 1 | Name style for SIDs | The ISO service name in upper snake case: `READ_DATA_BY_IDENTIFIER`, `TESTER_PRESENT`. A KWP2000-only service gets a `KWP_` prefix (`KWP_READ_DATA_BY_LOCAL_IDENTIFIER`). Short forms such as `SID_22` say nothing a literal does not. |
| 2 | Positive-response SIDs | One function, `positive_response_sid(sid)`, not a second constant per service. Module-level sets build from it (`{positive_response_sid(READ_DATA_BY_IDENTIFIER)}`). |
| 3 | Home for sub-functions | A new module `uds/subfunctions.py`: one `IntEnum` per service, named after the standard (`RoutineControlType.START_ROUTINE`, `IoControlParameter.RETURN_CONTROL_TO_ECU`, `KwpIoControlParameter`, `DtcReportType`, `SessionType`, `KwpSessionType`). `SUBFUNCTION_NAMES` and `subfunction_name()` move there from `uds/layout.py` and stay a literal table, like decision 4; a test pins every member to the name the table gives its value, so the two cannot disagree. The scanner adds the module to `HOMES`. |
| 4 | Do the registry rows use the constants? | No. `SERVICES` and the NRC tables stay literal: they *are* the data, and they read like the standard. A test asserts that every SID constant names a registry row and every NRC constant a row of its protocol's table. |
| 5 | Canned requests | `TESTER_PRESENT_REQUEST = "3E00"` (and the suppressed `"3E80"` if a caller needs it) in `uds/services.py`, as hex text, because every caller passes hex text. |

## Phase 1 — add the names (done, 2026-11-05)

1. `uds/services.py`: a `Final` constant for every registry row (`KWP_`/`OBD_` prefixes per
   decision 1, and both names where one byte is a different service on each protocol), plus
   `positive_response_sid()`, `request_sid_of()` and `TESTER_PRESENT_REQUEST`.
2. `uds/nrc.py`: constants for the NRCs callers test, a `KWP_NRC_` name where KWP2000 gives the
   code another meaning (`0x12`, `0x22`, `0x72`, `0x80`), and `NOT_IN_ACTIVE_SESSION_NRCS`
   (UDS `0x7F`, KWP2000 `0x80`).
3. `uds/subfunctions.py` (decision 3).
4. Tests: `tests/uds/test_uds_services.py`, `test_uds_nrc.py` and `test_uds_subfunctions.py`
   pin each name to its registry or table row.

The scanner count does not change in this phase.

**Found while pinning:** `SUBFUNCTION_NAMES[0x30]` reuses the UDS `0x2F` names, but SSF
14230-3 (the table `live/kwp_iocontrol_scan.py` cites) gives `0x01` reportCurrentState, not
resetToDefault, and `0x04`-`0x09` other meanings again. A strict `xfail` in
`test_uds_subfunctions.py` records it; phase 3 item 4 fixes it.

## Phase 2 — mechanical replacement (done, 2026-11-05)

Each literal became its name, one package per commit, with no behaviour change: the existing
tests passed unchanged except where they imported a constant that moved to `canair.uds`. Each
commit lowered the baseline. 126 literals in 36 modules became 33 in 8.

| Commit | What changed |
|---|---|
| `refactor(transport)` | the functional collector's private offset and negative SID, `keepalive.TESTER_PRESENT_SID`, and every `"3E00"` keepalive |
| `refactor(scan)` | the four `NRC_WRONG_SESSION` copies, the scanners' `SF_`/`IOCP_` constants and the routines TUI's labels. `NRC_EXISTS_HINTS`/`NRC_ABSENT` stay per scan, built from names; each IOControl scanner names its one probe value `SAFE_CONTROL_PARAMETER` |
| `refactor(live)` | the raw monitor, the batched reads, the REPL, identity probing, the DTC and snapshot reads, the functional sweep and the tester loop |
| `refactor(safety)` | the actuation classifier's service sets and RoutineControl checks; `IOCONTROL_UDS`/`IOCONTROL_KWP`/`UDS_RETURN_CONTROL` go; the blocklist's session and routine checks |
| `refactor(uds)` | `multi_did.BATCH_REFUSAL_NRCS`, shared by the raw monitor and the ELM327 batched read |
| `refactor:` | `dtc/snapshot`, `ecus/did_families`, `cli/commands/research/gaps`, `interop/frame_log`, `uds/echo` (`== 0x22`) |

Left for phase 3, because a faithful rewrite of each changes or risks changing behaviour:
`captures/__init__.py`, `live/scan.py` and `live/scan_presets.py` (the `in (0x22, 0x2F, 0x31)`
sets), `uds/echo.py`'s `_ONE_BYTE_ID_SERVICES`, `uds/describe.py`, `live/skm_wakeup.py`,
`live/tester.py` (`"7F3E"`) and `cli/commands/coverage/payloads.py`.

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

4. **KWP2000 `0x30` names.** Name `0x30` values with `KwpIoControlParameter` instead of the UDS
   table, and remove the `xfail`. This changes what `bix`/`decode_uds_response` print for a
   KWP2000 IOControl reply, so ask first.
5. **UDS-only codes on KWP2000 paths.** `live/kwp_routines_scan.py` and
   `live/kwp_iocontrol_scan.py` test `0x7F` as "wrong session" and `0x24` as "exists", but
   SSF 14230-3 leaves both reserved on KWP2000 (its "not in this session" is `0x80`). Phase 2
   keeps the behaviour and names them with the UDS constants; decide here whether a KWP2000
   scan should test the KWP2000 codes instead.

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
