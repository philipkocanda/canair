# UDS specification alignment: canair checked against udsoncan

Status: **PHASE 0 DONE** (2026-11-06); phases 1-8 proposed. Checked against `3e652f46`. Paths
are relative to `src/canair/` unless they start with `plans/`, `docs/` or `tests/`.

## Summary

udsoncan (<https://udsoncan.readthedocs.io/>) is a Python implementation of Unified Diagnostic
Services (UDS, ISO 14229-1). Its documentation lists, per service, the request fields, the reply
fields, the echoes a client must check and the timing rules. This plan uses that documentation as
a checklist for canair's own UDS layer.

The verdict:

- **The read paths match the standard.** Envelope, positive-reply SID, negative-reply format,
  identifier echoes, ReadDataByIdentifier (`0x22`, multi-DID too), ReadMemoryByAddress (`0x23`,
  with the addressAndLengthFormatIdentifier), the DTC status bits, DTC snapshots, session types
  and ISO-TP framing all agree with udsoncan.
- **The safety blocklist has gaps.** It passes any service ID (SID) it does not list. Through
  `canair raw` or the REPL, `85 02` (DTC logging off), SecuredDataTransmission (`0x84`, which
  carries an inner request the blocklist cannot see) and the ISO safety-system routines
  (`31 01 E201`, the pyrotechnic deploy loop) go out with no question. This is the most important
  finding (phase 0).
- **Timing is fixed, not negotiated.** canair ignores the P2 and P2\* values in the
  DiagnosticSessionControl reply. The ResponsePending (NRC `0x78`) wait differs per transport, and
  the ELM327 path has no total cap.
- **Some replies are checked less than the standard allows.** RoutineControl sends pass no
  `expected_sid`, which breaks a rule in `AGENTS.md`. No sub-function echo is checked for `0x10`
  (on UDS), `0x31`, `19 02` or `0x3E`.
- **Some read-only DTC reports are missing.** canair never reads the DTCFormatIdentifier
  (`19 01`), so it formats every 3-byte DTC as SAE J2012 (`P0123-45`), even when the ECU uses
  another format. Extended data records (`19 06`) are not read.
- **Small items:** missing negative response codes (NRCs), no retry on busyRepeatRequest
  (`0x21`), no knowledge of the suppressPosRspMsgIndicationBit, no IOControl controlEnableMask,
  and the ISO-TP library refuses a flow-control WAIT frame.

## udsoncan as a reference, and its limits

`plans/2026-07-22-functionality-review.md` decided not to depend on udsoncan: it models neither
KWP2000 nor the ELM327 path. That decision stands. This plan uses the documentation only.

Use it with care:

- Its author says it follows ISO 14229-1:2006, with "many additions from the 2013 and 2020
  version", "not exhaustively". Where the two disagree, the ISO text wins.
- Its `Routine.DeployLoopRoutineID` is `0xE200`. ISO 14229-1 Annex F gives `E200` to
  "Execute SPL" and `E201` to DeployLoopRoutineID.
- Its NRC table gives `0x38`, `0x39` and `0x3A` two names each (for example
  SecureDataTransmissionRequired and GeneralSecurityViolation are both `56`). ISO 14229-1:2013
  reserves `0x38`–`0x4F` for the extended data-link security document (ISO 15764). Do not copy
  udsoncan's names for that range.
- The parameter texts of its `Dtc.Status` class describe each bit as *cleared* ("test_failed:
  DTC is no longer failed at the time of the request"). canair's names in `live/dtc/codes.py` are
  the ISO names. Do not copy the udsoncan texts.
- It has no KWP2000 (ISO 14230 / SSF 14230-3) model. Every KWP2000 item below comes from canair's
  own tables and the KWP2000 plan (`plans/2026-11-05-kwp2000-protocol-aware-lookups.md`).

canair already uses the same author's ISO-TP library, `can-isotp` (2.0.7), on the `slcan-tcp`
path, so the ISO-TP items below are about how canair configures that library.

## Where canair matches

| Area | canair | udsoncan / ISO 14229-1 |
|---|---|---|
| Positive reply SID = request + `0x40` | `uds/echo.py::echo_mismatch` | same |
| Negative reply `7F <SID> <NRC>`, SID must match | `uds/parse.py` (a mismatch is `stale`) | `UnexpectedResponseException` |
| `0x22` DID echo | `uds/echo.py::reply_check` (first DID of a batch) | every DID, by codec length |
| `0x22` multi-DID | opt-in batches of 3, split by learned lengths (`uds/multi_did.py`) | DID list, split by codec length |
| `0x23` ALFID: high nibble size width, low nibble address width | `uds/memory.py::format_from_alfid` | `MemoryLocation` |
| `0x23` reply length = requested size | `uds/parse.py` | `memory_block` |
| `0x19 02` status mask and availability mask | `live/dtc/reading.py` | `get_dtc_by_status_mask` |
| `0x19 03` / `0x19 04` snapshots, DID lengths supplied by the caller | `dtc/snapshot.py`, lengths from the profile | `dtc_snapshot_did_size`, `didconfig` |
| statusOfDTC bit names | `live/dtc/codes.py::_STATUS_BITS` | ISO names |
| `0x14` group of 3 bytes, `FFFFFF` = all | `uds/requests.py::clear_dtc_request` | `clear_dtc(group=0xFFFFFF)` |
| Session types `01`–`04` | `uds/subfunctions.py::SessionType` | `DiagnosticSessionControl.Session` |
| ECUReset types `01`–`05` | names only, service blocked | `ECUReset.ResetType` |
| RoutineControl `01`/`02`/`03` | `uds/requests.py::routine_control_request` | `RoutineControl.ControlType` |
| IOControl parameters `00`–`03` | `uds/subfunctions.py::IoControlParameter` | `ControlParam` |
| SecurityAccess: odd level = seed, even = key | `uds/describe.py::_security_access` | same |
| `0x78`: keep waiting for the final reply | every transport | `p2_star_timeout`, `request_timeout` |
| TesterPresent before S3 expires (5 s) | every 2 s (`transport/session_manager.py`) | caller's job |
| Standard routine ids `FF00`, `FF01` blocked | `safety/blocklist.py::BLOCKED_ROUTINES` | `udsoncan.Routine` |
| ISO-TP framing, STmin, block size, FF escape over 4095 bytes | `can-isotp` | `can-isotp` |

## Findings

### 1. The blocklist passes services and routines that change state

`safety/blocklist.py::check_command_safety` refuses only the SIDs in `BLOCKED_UDS_SERVICES`. An
unlisted SID passes. `safety/actuation.py::classify` fails closed, but only `canair io` and
`canair scan range` call it (the funnel is `plans/2026-10-22-actuation-safety-interlock.md`,
PROPOSED). So `canair raw` and the REPL send these with no question:

| Request | What it does | Today |
|---|---|---|
| `85 02` ControlDTCSetting off | The ECU stops recording DTCs until `85 01` or a session change | passes (named in the registry, not blocked) |
| `84 …` SecuredDataTransmission | Carries an inner request, which can be any blocked service | passes (not in the registry) |
| `86 …` ResponseOnEvent | The ECU sends unrequested replies on an event, which can desync the reply ledger | passes |
| `2C …` DynamicallyDefineDataIdentifier | Creates a DID in the ECU. `defineByMemoryAddress` reads memory past the `0x23` rules | passes |
| `83 04 …` AccessTimingParameter set | Changes the ECU's timing parameters | passes |
| `29 …` Authentication | Certificate exchange; can change access rights | passes |
| `2A …` ReadDataByPeriodicIdentifier | The ECU streams `0x6A`-family replies on its own | passes |
| `31 01 E200`–`E2FF` | ISO 14229-1 Annex F: Execute SPL (`E200`), DeployLoopRoutineID (`E201`) and the safety-system (ISO 26021) routine ids, i.e. airbag and pretensioner deployment | passes on any ECU |
| `31 01 FF02` | eraseMirrorMemoryDTCs | passes |

`85 02` is a common step before reflashing, and a user may type it without knowing it outlives
the command. The `E200`–`E2FF` routines exist so that a scrap yard can fire the pyrotechnics. A
wrong request there can hurt a person. Of all the findings, these two need a fix first.

`0x27` SecurityAccess also passes. Repeated wrong keys draw exceededNumberOfAttempts (`0x36`)
and a lock-out delay. `plans/2026-11-06-memory-upload-and-security-access.md` owns the `0x27`
policy, so this plan does not change it.

### 2. Timing: P2 and P2\* are not read; the ResponsePending wait differs per transport

ISO 14229-1:2013 and later put the server's timing in the DiagnosticSessionControl reply:
`50 <type> <P2server_max: 2 bytes, 1 ms> <P2*server_max: 2 bytes, 10 ms>`. udsoncan reads it
(`use_server_timing`) and uses it for the next waits. canair ignores it
(`uds/describe.py::_session` names only the sub-function).

The `0x78` waits today:

| Path | Wait after each `0x78` | Total cap | SID checked |
|---|---|---|---|
| raw, `transport/raw/uds_client.py` | 5.0 s (`PENDING_RECV_TIMEOUT`) | 20.0 s | no |
| raw, `transport/raw/terminal.py` | 5.0 s | 20.0 s | no |
| ELM327, `transport/elm327/terminal.py` with `pipe.py` | the full request timeout again | **none** | no (`PENDING_RE` matches any SID) |
| functional, `transport/functional.py` | 5.0 s | 20.0 s | per responder |

Three defects follow:

- On the ELM327 path, an ECU that keeps sending `7F xx 78` holds the exchange open for ever.
- On the raw path, `is_response_pending(resp)` is called without the SID, so a stale
  `7F <other SID> 78` extends the wait for the current request.
- The 5 s wait is the ISO default P2\*server_max. An ECU that announces a longer P2\* (a slow
  routine) times out early; one that announces a shorter value makes canair wait longer than
  needed when a reply is lost.

On the ELM327 path the adapter handles `0x78` itself and its `ATST` timer governs, so the server
timing can only set canair's own cap there.

### 3. Replies checked less than the standard allows

| Request | Check today | Check the standard allows |
|---|---|---|
| `31 <type> <RID>` (`live/actuate/routines_execute.py`, `live/actuate/routines.py`) | none: no `expected_sid` | SID `0x71`, type echo, RID echo |
| `canair scan range` (`live/scan/range.py`) | none when no echo can be derived | at least the SID |
| `10 <type>` on UDS | SID only (`transport/session_manager.py`) | type echo (`session_echo`) |
| `19 02 <mask>` | SID only | sub-function echo |
| `3E 00` | SID only | sub-function echo `00` |
| `22 <DID1> <DID2> …` | first DID echo | every DID echo (the split may check it; confirm) |

`AGENTS.md` says: "Pass `expected_sid` and `expected_echo` on any new `send_uds`. Without them a
desync is undetectable." The routines paths break the first half. The `0x10` echo is skipped on
purpose because some KWP2000 ECUs do not echo it. The check can stay off for KWP2000 and an
unknown protocol and go on for UDS.

### 4. Negative response codes

`uds/nrc.py::NRC_CODES` lacks these ISO 14229-1:2020 codes:

- `0x34` authenticationRequired;
- `0x50`–`0x5D`, the Authentication (`0x29`) and certificate codes;
- `0x94` resourceTemporarilyNotAvailable, which canair now reads as part of the reserved range
  `0x94`–`0xEF`.

`0x38`–`0x4F` has no label. Label it "reservedByExtendedDataLinkSecurityDocument".

Behaviour:

- **`0x21` busyRepeatRequest is never retried.** Every `send_uds` returns an NRC as final
  (`transport/elm327/terminal.py`, `transport/raw/terminal.py`). udsoncan does not retry either,
  but ISO 14229-1 says the client should repeat the request. A busy gateway ECU then reads as a
  refusal in a scan.
- **`live/scan/sessions.py::classify` expects only `0x7F`** for "not in this session". A UDS ECU
  answers a session type it supports only from another session with `0x7E`
  (subFunctionNotSupportedInActiveSession), and a KWP2000 ECU with `0x80`. Both fall into "not
  supported" today. `uds/nrc.py::NOT_IN_ACTIVE_SESSION_NRCS` already holds `0x7F` and `0x80`.

### 5. The suppressPosRspMsgIndicationBit

Bit 7 of a sub-function byte asks the ECU to send no positive reply. udsoncan models it
(`suppress_positive_response(wait_nrc=…)`). canair defines the bit
(`uds/subfunctions.py::SUPPRESS_POSITIVE_RESPONSE`), and only the session blocklist reads it.

canair never sets the bit itself, which is correct: a reply is its proof that the ECU is alive.
But a user can send `3E 80` or `10 83` through `canair raw` or the REPL, and then:

- the transport waits for a reply that never comes, and reports a timeout;
- on the raw path, the owed-reply ledger (`transport/raw/uds_client.py`) may count a reply as
  owed and drain or misfile the next one.

### 6. DTCs

- **DTCFormatIdentifier.** `19 01 <mask>` (reportNumberOfDTCByStatusMask) returns
  `59 01 <availability> <format> <count: 2 bytes>`. The format byte says how to read a 3-byte DTC:
  `00` SAE J2012-DA format 00 (ISO 15031-6), `01` ISO 14229-1, `02` SAE J1939-73, `03`
  ISO 11992-4, `04` SAE J2012-DA format 04. canair never sends `19 01` and always formats as
  SAE J2012 (`live/dtc/codes.py::format_dtc`). For format `01` or `02` the letter and digits are
  wrong. The read is cheap and changes nothing in the ECU.
- **Extended data records** (`19 06 <DTC> <record>`, `FF` = all) hold the occurrence counter,
  the aging counter and the fault detection counter on many ECUs. They are read-only and useful
  for reverse engineering. Their sizes are not standard, so, as for snapshots, the profile must
  supply them (udsoncan's `extended_data_size`).
- **Other read-only reports** that canair names but never sends: `19 0A` reportSupportedDTCs,
  `19 14` reportDTCFaultDetectionCounter, `19 15` reportDTCWithPermanentStatus, and the severity
  reports `19 07`–`19 09`. None is needed now. `19 0A` would make `dtc read --all` cheaper on an
  ECU that keeps many codes.
- **Padding and partial records.** `live/dtc/codes.py::decode_dtc_records` drops a trailing
  partial record without a word, and keeps an all-zero record (`000000 00`). udsoncan has
  `tolerate_zero_padding` and `ignore_all_zero_dtc` for this. ISO-TP reports the true length, so
  padding should not reach the decoder, but a `000000` record from the ECU is still not a fault.

### 7. IOControl controlEnableMaskRecord

For a packeted IOControl DID (one DID that holds several outputs), ISO 14229-1 puts a
controlEnableMaskRecord after the controlOptionRecord. The mask says which outputs the request
controls. udsoncan models it (`ioconfig` with `mask` and `mask_size`). canair reads every byte
after the control parameter as state (`safety/actuation.py::parse_io_request`). Effects:

- a profile cannot say that the last bytes of a request are a mask, so a user writes them by
  hand;
- the release request `2F <DID> 00` may need the mask too on a packeted DID. Check this against
  the ISO text before relying on it, because the `canair io` release interlock assumes the bare
  form.

### 8. ISO-TP configuration

- `transport/isotp_params.py` does not set `wftmax`. The library default is `0`, so an ECU that
  sends a flow-control WAIT frame makes `can-isotp` fail the transfer
  (`UnsupportedWaitFrameError`). ISO 15765-2 lets the receiver send up to N_WFTmax WAIT frames.
  A small value (for example 5) costs nothing.
- `uds/addressing.py` calls extended addressing "extended (mixed)" in four places. ISO 15765-2
  has both modes, and they differ: extended puts a target address in byte 1 of every frame,
  mixed puts an address extension there and uses the `18CE` 29-bit ids. canair implements only
  extended. Fix the wording; mixed addressing itself is out of scope.
- `transport/functional.py` accepts 29-bit replies only for tester address `0xF1`, though the
  tester address is configurable. Confirm whether this is meant.

### 9. Items for other plans

- **KWP2000 session close.** `transport/session_manager.py::close_session` sends `10 01` to every
  ECU. A KWP2000 ECU expects `10 81` (standardDiagnosticSession). This belongs to phase 5 of
  `plans/2026-11-05-kwp2000-protocol-aware-lookups.md` (`default_session_type(protocol)`).
- **KWP2000 TesterPresent.** KWP2000 on CAN may expect `3E 01` (responseRequired) instead of
  `3E 00`. The bundled KWP2000 ECUs keep their sessions with `3E 00`, so this needs the standard
  text and a car check before any change.
- **Identity DIDs.** `live/identity/records.py` lacks `F180`–`F185`, `F189`, `F18E`,
  `F198`–`F19F` and `F1A1`, and some labels differ from ISO 14229-1 Annex C (`F187` is
  vehicleManufacturerSparePartNumber, `F188` vehicleManufacturerECUSoftwareNumber). This is
  `plans/2026-11-06-identity-record-labels.md`.
- **SecurityAccess.** `plans/2026-11-06-memory-upload-and-security-access.md`. canair ships no
  seed/key algorithm (the **contributing-code** skill forbids it), and this plan adds none.

## Decisions to make

| # | Question | Recommendation |
|---|---|---|
| 1 | Hard block or soft block (`--unsafe` lifts it) for the new services? | Hard block `31 01`/`31 02` with RID `E200`–`E2FF`, as for the programming sessions. Soft block `0x84`, `0x85` (`85 02` only, or the whole service), `0x86`, `0x2C`, `0x83 04`, `0x29`, `0x2A`. |
| 2 | Block all of `0x85`, or only `85 02`? | Only `85 02` (off) and unknown setting types. `85 01` (on) restores the default and is safe. |
| 3 | Use the ECU's P2\* for the pending wait? | Yes, on the raw and functional paths, between 1 s and the 20 s cap, plus the link budget. Without a `50` reply, keep 5 s. |
| 4 | How many `0x21` retries? | Three, with a growing wait (100, 200, 400 ms). Only for reads; never repeat an actuating request. |
| 5 | What does a transport do with a suppress-bit request? | Wait for an NRC up to P2, then return a "suppressed, no reply expected" result, not a timeout, and do not add the request to the owed ledger. |
| 6 | Is `19 01` sent on every `dtc read`? | No: once per ECU, and the result is stored as `dtc_format` on the ECU's `identity:` block, so later reads know the format. |
| 7 | Where do extended-data sizes live? | Next to the snapshot DID lengths in the ECU file (`dtc_extended_data:` keyed by record number), edited through `canair pids`, never by hand. |

## Phases

One commit per phase. Each phase runs `make structure` and the tests. No baseline may rise
without the user's approval (phase 0 raised one, with approval: see below).

### Phase 0 — close the blocklist gaps (done, 2026-11-06)

As built, with decisions 1 and 2 taken as recommended:

- `safety/blocklist.py::SAFETY_SYSTEM_ROUTINES` is `E200`–`E2FF`. ISO 14229-1 Annex F (as
  listed by the py-uds knowledge base) names `E200` "Execute SPL" (the scrapping program
  loader), `E201` DeployLoopRoutineID and `E202`–`E2FF` safetySystemRoutineIDs. udsoncan's
  `Routine.DeployLoopRoutineID = 0xE200` is off by one. `check_safety_system_routine` refuses a
  start or stop (`31 01`/`31 02`, suppress bit ignored) on any ECU not known to speak KWP2000.
  `unliftable_block` joins it with the broadcast rule, and `enforce_command_safety` refuses both
  without asking, even with `--unsafe`. "Hard" here is stronger than `SessionBlock.hard`, which
  only stops a profile allowance: `--unsafe` still lifts `10 02`.
- `BLOCKED_ROUTINES` gains `FF02` (eraseMirrorMemoryDTCs, ISO 14229-1:2006; the 2013 and 2020
  editions reserve the id), as a soft block.
- `BLOCKED_UDS_SERVICES` gains `0x29`, `0x2C` and `0x84` (whole service, soft).
- `GUARDED_SERVICES` refuses a service unless the byte after the SID only reads or undoes it
  (soft): `85` except `01`; `83` except `01`/`03`; `2A` except `04` (judged on the whole byte,
  since a transmissionMode has no suppress bit); `86` except `00`/`04` (the storageState bit
  counts, so `86 44` is refused). A request that stops at the SID is refused.
- `uds/services.py` registers `0x24`, `0x29`, `0x2A`, `0x83`, `0x84`, `0x86` and `0x87`, and
  `0x85` is now `BOTH` (ISO 14230-3 has the same service). `uds/subfunctions.py` has
  `PeriodicTransmissionMode`, `TimingParameterAccessType`, `DtcSettingType` and
  `ResponseOnEventType`, with their name tables.
- `safety/actuation.py::classify` is unchanged: none of the new services is in its read set,
  so it still fails closed for them. `0x24` stays out of it until its KWP2000 meaning is
  checked.
- `safety/blocklist.py` grew to 618 lines. The user approved a ceiling for it in
  `tests/contracts/baseline.json` instead of a split.
- Tests: `tests/safety/test_command_safety.py` (refused and passed forms, the frame-count digit,
  KWP2000), `tests/safety/test_safety.py` (`--unsafe` does not prompt for `31 01 E201`, and still
  prompts for `85 02`), and the registry and name-table tests in `tests/uds/`.
- `docs/concepts/safety.md` lists the new refusals.

The original design:
1. `safety/blocklist.py`:
   - add `E200`–`E2FF` as a hard-blocked RID range for start and stop, on any ECU not known to
     speak KWP2000, and `FF02` to `BLOCKED_ROUTINES`. Check both against ISO 14229-1 Annex F
     first: this plan takes `E200` from udsoncan's `Routine.DeployLoopRoutineID` and the rest
     of the range from memory of the standard;
   - block the services of decision 1 and 2.
2. `uds/services.py`: add rows for `0x24`, `0x29`, `0x2A`, `0x83`, `0x84`, `0x86` and `0x87`, with
   `actuates` set where the request changes state. Then `safety/actuation.py::classify` and the
   blocklist read one table.
3. Tests in `tests/safety/`: each new request is refused, `--unsafe` lifts only the soft blocks,
   `85 01` passes, and a KWP2000 `31 E2 …` is judged by the KWP2000 rules.
4. `docs/concepts/safety.md` lists the new blocks. The commit body names them for the release
   notes.

### Phase 1 — NRC names

Add the codes of finding 4 to `uds/nrc.py`, with abbreviations in `NRC_ABBREV`, and the
`0x38`–`0x4F` range label. Labels only, no behaviour change.

### Phase 2 — reply checks

1. Pass `expected_sid` and the type and RID echo on the routines sends, and `expected_sid` on
   every `scan range` send.
2. Check the type echo of `10 xx` on UDS ECUs only, and the sub-function echo of `19 02` and
   `3E 00`.
3. Pass the SID to `is_response_pending` on the raw paths.
4. Give the ELM327 pending wait the same 20 s total cap as the raw path.
5. Confirm that the multi-DID split checks each DID header. If it does not, make it.
6. Tests with a stale `7F <other> 78`, an endless `7F 22 78` on the ELM327 fake, and a wrong
   routine echo.

### Phase 3 — server timing

1. `uds/`: an I/O-free parser for the `50` reply that returns P2 and P2\* or `None` (a 2006-style
   or KWP2000 reply).
2. `transport/session_table.py` keeps the values per ECU. `SessionManager` stores them when it
   opens a session.
3. The raw and functional pending waits read P2\* from the table (decision 3).
4. `canair status` or `canair identity` shows the values, so a user can see them.
5. On the car: open a session on a UDS ECU (IGPM) with `--save` and record the `50 03` reply.

### Phase 4 — NRC behaviour

1. Retry `0x21` on reads (decision 4) in one place under both terminals.
2. `live/scan/sessions.py::classify` reads `0x7E` and `NOT_IN_ACTIVE_SESSION_NRCS` as "exists,
   but not from this session".

### Phase 5 — suppress bit

Decision 5, in the shared send path, for requests from `raw`, the REPL and query steps. Tests on
both transports: `3E 80` with no reply returns the suppressed result, and `3E 80` answered by
`7F 3E 12` returns the NRC.

### Phase 6 — DTC format and extended data

1. `19 01` once per ECU (decision 6). `live/dtc/codes.py` formats by the format byte: SAE J2012 for
   `00` and `04`, plain hex for `01` and `03`, SPN and FMI for `02`.
2. Skip all-zero records, and warn on a trailing partial record.
3. `canair dtc read --extended` reads `19 06 <DTC> FF` for each code found, split by the sizes of
   decision 7. Without sizes it prints the raw records.
4. Help, `docs/concepts/dtcs.md` and the generated `docs/reference/cli/dtc.md` change in the same
   commit.
5. On the car: `dtc read` on an ECU with a stored code, with and without `--extended`.

### Phase 7 — IOControl mask

1. Check the ISO 14229-1 text for when the mask is present, including on returnControlToECU.
2. An optional `enable_mask` (bytes) on an IOControl entry in the ECU schema, edited through
   `canair pids`. `safety/actuation.py::parse_io_request` splits state and mask by it, and the
   release request carries the mask when the standard requires it.
3. Docs: `docs/concepts/profiles.md`.

### Phase 8 — ISO-TP

1. Set `wftmax` in `transport/isotp_params.py` (a profile `isotp:` field, default 5).
2. Correct the "extended (mixed)" wording in `uds/addressing.py`.
3. Answer the `0xF1` question in `transport/functional.py`.

## Out of scope

- Depending on udsoncan. See "udsoncan as a reference".
- Implementing `0x24`, `0x29`, `0x2A`, `0x83`, `0x84`, `0x86`, `0x87` or the transfer services.
  Phase 0 only names and blocks them.
- A `DidCodec`-like layer. canair's expressions over the byte buffer are its codec.
- Mixed addressing, and CAN-FD on `slcan-tcp` (the SLCAN codec carries only classic frames).
- ClearDiagnosticInformation's memorySelection byte and the user-defined DTC memory reports
  (`19 17`–`19 19`).

## Idea, not planned

DynamicallyDefineDataIdentifier `defineByIdentifier` (`2C 01`) can combine slices of several DIDs
into one DID. On the ELM327 path, where each request costs a round trip, one dynamic DID could
replace several reads in `canair monitor`. The DID lives in the ECU's memory until the session
ends, and phase 0 soft-blocks `0x2C`. It would need its own plan, a guarded action and a car test.

## User-facing docs

Phase 0 changes what `canair raw` and the REPL send, so `docs/concepts/safety.md` and the commit
body change. Phase 6 adds a flag. Phase 7 adds a profile field. Phases 1–5 and 8 change no flag,
default or file format; their commits name any output change (new NRC names, a suppressed-reply
result, the server timing in `canair status`).
