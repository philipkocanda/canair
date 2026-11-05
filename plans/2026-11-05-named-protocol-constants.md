# Named protocol constants: read every protocol byte from `canair.uds`

Status: **DONE** (2026-11-05). No protocol byte is written as a literal outside `canair.uds`,
and `tests/contracts/test_structure.py` fails on any new one. Not user-facing: no command, flag
or profile changes; phase 3's commits name the small output changes.

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

**Found while pinning:** `SUBFUNCTION_NAMES[0x30]` reused the UDS `0x2F` names, but SSF
14230-3 (the table `live/kwp_iocontrol_scan.py` cites) gives `0x01` reportCurrentState, not
resetToDefault, and `0x04`-`0x09` other meanings again. Phase 3 item 4 fixed it.

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

## Phase 3 — derived facts and text matching (done, 2026-11-05)

These sites restated a fact that the registry already holds, or matched protocol bytes inside
text. Each got its own commit with a regression test; each commit body names what changed.

1. **Identifier width** (`fix(uds)`). Five `service in (0x22, 0x2F, 0x31)` tests and the echo
   rule's own list of 1-byte-id services now read `uds.services.identifier_width()`, and the
   unread `ServicePreset.wide` field is gone. Two results follow the registry now: `0x2E`
   formats as a 4-digit DID (the blocklist refuses it, so a scan only prints it), and a
   two-byte KWP2000 `0x33`/`0x32` request is echo-validated, as SSF 14230-3 has `73`/`72`
   repeat the local identifier.
2. **`uds/describe.py`** (`refactor(uds)`). The chain of twenty `if sid == 0x50` branches is a
   table of small describers keyed by service. The output is identical for every first byte
   with eight tails, compared against the old code.
3. **Hex-text matching** (`fix(live)`). The SKM relay wake and the tester loop read adapter
   text line by line through the new `uds.parse.reply_lines()` and
   `uds.nrc.is_negative_response()`, so a `6F` or `7F 2F 78` inside another reply's data no
   longer counts. The coverage audit names the negative-response byte.
4. **KWP2000 `0x30` names** (`fix(uds)`). `0x30` has its own SSF 14230-3 table (`0x00`-`0x08`),
   pinned by the `KwpIoControlParameter` test. `0x09` stays unnamed until its name is checked
   against the standard. No current output names a `0x30` value.
5. **UDS-only codes on KWP2000 paths** (`fix(scan)`). The routine-results scan now reads
   KWP2000's `0x80` as "wrong session" as the `0x30` scan already did, instead of recording
   every LID as a hit; `0x7F` stays for ECUs that mix the codes. `0x24` left the named hints:
   any NRC `classify()` does not name counts as a hit, so it still is one.

## Phase 4 — close the ratchet (done, 2026-11-05)

- The `protocol_literals` baseline key is gone, and
  `test_no_protocol_byte_is_written_as_a_literal` fails on any match.
- The scanner also catches `0x40` as an ordering bound (`b < 0x40`), names with `UDS`, `KWP`
  or `SESSION` in them, and `range()` constants between two hex literals. That found
  `cli/commands/bix.py`'s response-SID range tests, the blocklist's session constants
  (`_UDS_SUPPRESS_BIT`, `_UDS_PROGRAMMING`, `_UDS_SAFETY_SYSTEM`, `_UDS_OEM_BAND`, `_KWP_SAFE`,
  `_KWP_PROGRAMMING`) and the session scanner's mode tuples. They now read
  `RESPONSE_SID_OFFSET`/`NEGATIVE_RESPONSE_SID`, `SessionType`/`KwpSessionType`, and the new
  `subfunctions.SUPPRESS_POSITIVE_RESPONSE` and `OEM_SESSION_TYPES`.
- `uds/parse.py`'s `0x10 <= … <= 0x3E` request-SID test, which no pattern sees, reads the new
  `services.REQUEST_SID_RANGE`. `interop/frame_log.py`'s rejected-SID test was already named in
  phase 2.
- A later pass added two patterns: a hex literal passed to a keyword named for a protocol byte
  (`expected_sid=0x22`, `service=0x2F`), and requests into a standard session (`"1001"`,
  `"1003"`, `"1081"`-`"1085"`). Their sites read the registry names and the new
  `DEFAULT_SESSION_REQUEST`/`EXTENDED_SESSION_REQUEST`.
- Requests built in an f-string get **no scanner pattern** (decided 2026-11-05). Of 30 such
  f-strings, one was an ISO-TP flow-control frame (`f"3000{…}"`), so the pattern would have
  false positives, and naming the bytes of a read request (`f"22{did:04X}"`) makes it harder to
  read without making it safer: echo validation catches a wrong SID, and the blocklist judges
  every request. Instead `uds/requests.py` builds the requests whose fields matter: sessions
  (`session_request`, six copies of `f"10{mode}"`), UDS and KWP2000 I/O control,
  routine control and DTC clear. Two IOControl sites keep their templates for now, for lack of
  room under their module size ceilings: `ecus/pids.py`'s implied release and
  `yaml_edit/pids/hits.py`'s promoted `on`/`off`.
- `_HKMC_PROGRAMMING_MODE` (`0x05`) stays in the blocklist: it is a Hyundai/Kia session, and
  make-specific values live with the code that handles the make.

## Out of scope

- Test payloads and fixtures. A test that reads `62 01 01 …` checks the constants independently.
- Bit masks (`sub & 0x7F`), ASCII and byte ranges, padding bytes, CAN ids and addressing
  defaults (`0x08`, `0xF1`). These are not diagnostic-service facts.
- Profile data. `ecus/` YAML writes requests as hex text by design.
