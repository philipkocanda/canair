# UDS and KWP2000: protocol-aware lookups, and protocol facts out of `live/`

Status: **PROPOSED** (2026-11-06). Phases 0 (`c56f2507`), 0b (`b0b8d28a`), 0c and 0d are done. No
other code changed. Checked against `c56f2507`, after the session-owner work
(`plans/2026-11-05-one-session-owner.md`).

## Summary

UDS (ISO 14229) and KWP2000 (ISO 14230 / SSF 14230-3) facts share the `canair.uds` package. That
is correct, and this plan keeps it. These things are wrong:

1. `canair routines` built a UDS RoutineControl request for every ECU. On a KWP2000 ECU the same
   bytes start a routine, and nothing refused them (phase 0, done).
2. `canair scan range --service routine` swept `31 F0 00`–`31 F0 FF` with no question. On a
   KWP2000 ECU each request starts routine `0xF0` (phase 0b, done).
3. An ECU whose profile records no protocol was treated as UDS by `canair routines` and `canair
   scan routines`, which sent it `31 03 …` without asking (phase 0c, done).
4. Three lookups key on the service ID (SID) alone, although a few SIDs mean different things on
   each protocol. The registry then holds one protocol's meaning, and callers must remember the
   other. `uds/nrc.py::nrc_description(nrc, protocol)` already solves this for negative response
   codes (NRCs). This plan gives the other lookups the same optional `protocol` argument
   (phases 2 and 3).
5. Some pure protocol facts live in `live/`, where layers below `live` cannot reuse them
   (phase 4).
6. A session request with no `--mode` is UDS extendedDiagnosticSession (`10 03`) on every ECU,
   which the Ioniq BMS and the Kona VCU refuse (phase 5).

## Why one package, not a `kwp/` package

- The `uds` package names the *domain* (request/response diagnostics), not the protocol. Its
  charter in `src/canair/uds/__init__.py` lists UDS, KWP2000 and OBD-II.
- The two protocols share the envelope: request SID, the `+0x40` positive reply, the
  `7F <SID> <NRC>` negative reply, ISO-TP framing, and most SIDs (`0x10`, `0x14`, `0x22`, `0x27`,
  `0x2E`, `0x2F`, `0x3E`, …).
- One car mixes them (the Ioniq's powertrain ECUs speak KWP2000, its body ECUs UDS), and one ECU
  can change protocol between revisions (the VCU `KWP`/`UDS` revisions in `variants/`).
- A `kwp/` package would copy the shared part, and every caller would import both packages and
  choose one. That is the branching `uds/protocol.py::classify_protocol` exists to keep in one
  place.

So the protocol stays a column of each fact table, as in `uds/nrc.py`.

## Already done

`plans/2026-11-05-named-protocol-constants.md` (DONE) removed most of the copied facts:

- The NRC constants that five scan modules declared again now come from `uds/nrc.py`, including
  KWP2000's `0x80` (`NOT_IN_ACTIVE_SESSION_NRCS`).
- Sub-function values have one home, `uds/subfunctions.py`, with separate `SessionType` /
  `KwpSessionType` and `IoControlParameter` / `KwpIoControlParameter` enums. `0x30` has its own
  SSF 14230-3 name table.
- `live/dtc/clear.py` compares against the `KWP` constant (`kwp=proto == KWP`), not a string.
- `tests/contracts/test_structure.py` fails on any protocol byte written as a literal outside
  `uds/services.py`, `uds/nrc.py` and `uds/subfunctions.py`.

The session-owner work (`plans/2026-11-05-one-session-owner.md`, DONE) made two more things
protocol-aware that this plan would otherwise have to do:

- Every terminal passes the target ECU's protocol to `uds/parse.py::parse_uds_response`
  (`protocol=self.session_policy.protocol(header)`), on `wican-ws`, `elm327-tcp` and both raw
  paths (`transport/raw/terminal.py`, `transport/raw/client_terminal.py`).
- `uds/echo.py::reply_check(request, protocol)` takes the protocol, for memory reads
  (`uds/memory.py::parse_memory_request`).

## Today

### `canair routines` on a KWP2000 ECU (fixed in phase 0)

On UDS, `31 03 <RID>` is RoutineControl requestRoutineResults, which only reads. On KWP2000,
`0x31` is StartRoutineByLocalIdentifier: `31 03 00 05` starts routine local identifier (LID)
`0x03` with entry options `00 05`. Before `c56f2507`:

- `canair scan routines <KWP2000 ECU>` probes with `0x33` and writes each hit to the ECU's
  `routines:` with a 2-digit key (`live/scan/kwp_routines.py::_write_hit`, `key_width=2`).
- `canair routines <ECU> --rid <RID>` defaults to sub-function `0x03` and sends it with no
  prompt. Its help calls this "Request results (SF 0x03, safe)". The request is always
  `uds/requests.py::routine_control_request(sub_function, int(rid, 16))`, a UDS request. Neither
  `live/actuate/routines_execute.py` nor the routines TUI (`live/actuate/routines.py`) checked the
  ECU's protocol.
- `safety/blocklist.py::check_command_safety` skips its RoutineControl check when the target speaks
  KWP2000 ("On KWP2000, 0x31 is StartRoutineByLocalIdentifier: byte 2 is a local id"). Nothing
  else refused the request.
- `safety/actuation.py::classify` does classify a KWP2000 `0x31` as `actuate`, but only `canair
  io` calls it (`on_refusal`). `plans/2026-10-22-actuation-safety-interlock.md` (PROPOSED) would
  route the routines paths through it, but it is not implemented.

So after a KWP2000 routines scan, `canair routines <ECU> --rid <LID>` started routine `0x03` with
no confirmation. No bundled profile has a `routines:` section on a KWP2000 ECU, so no shipped
data triggered this. One scan created such a section.

### `canair scan range` with an actuating service (fixed in phase 0b)

Before phase 0b, `canair scan range ECU --service S` sent `S {id} [--append bytes]` for every id
in the range (`live/scan/range.py::mode_scan`,
`req = f"{service:02X}{pid_val:{did_fmt}}{append_bytes}"`).
Nothing between the flag and the bus asked first or checked the protocol:

- `cli/live/handlers/scans.py::handle_scan` parses the flags and calls `mode_scan`. It has no
  confirmation, unlike the routines and IOControl scans in the same module.
- The `routine` preset (`live/scan/presets.py`, `service=ROUTINE_CONTROL`, default range
  `F000-F0FF`) carries only a `caution` string. `presets_help` and the scan wizard print it; a
  run does not.
- The blocklist's RoutineControl check needs 8 hex digits and skips a KWP2000 target, so it does
  not see `31F000`.

On a KWP2000 ECU, `canair scan range BMS --service routine` sends `31 F0 00` to `31 F0 FF`: 256
starts of routine LID `0xF0`, each with a different entry option. On a UDS ECU the same requests
carry no valid routineControlType and should draw an NRC. The same command reaches the other
actuating services too, on either protocol: `--service iocontrol --append 03` (a help example)
sends shortTermAdjustment with no state bytes, and `--service 30 --append …` sends KWP2000
IOControl. `ServiceInfo.actuates` already marks every such service, but `scan range` does not
read it.

### The default session ignores the protocol

A session that names no mode opens UDS extendedDiagnosticSession, `10 03`. The value is written
out four times:

- `transport/session_manager.py::DEFAULT_SESSION_MODE` (`SessionType.EXTENDED_DIAGNOSTIC_SESSION`),
  which `live/hold.py`, `live/read/exec.py` and `live/monitor/sessions.py` use;
- `ecus/wake.py::DEFAULT_SESSION_MODE = "03"`, a wake ritual's session;
- `live/scan/kwp_iocontrol.py::DEFAULT_SESSION_MODE = "03"`, the KWP2000 IOControl scan's;
- `query/steps.py`, a pipeline `session` step's `mode = "03"`.

KWP2000 ECUs refuse it: `live/scan/discovery.py` notes that the BMS "rejects the lazy 10 03
escalation", and the Kona EV logs in `profiles/hyundai-kona-os-ev/captures/can/index.yaml` record
`10 03` drawing NRC `0x12` on the VCU. The bundled BMS, MCU and OBC `sessions:` record `81` as
supported. So every `--session` on a KWP2000 ECU needs `--mode 81`: the `scan iocontrol` and
`scan routines` help shows `--session --mode 81` for the BMS, and a pipeline step must read
`session BMS --mode 81`.

### Lookups that ignore the protocol

| Lookup | SID | What it says | What KWP2000 (or UDS) says |
|---|---|---|---|
| `services.service_info` | `0x31` | RoutineControl: `id_width=2`, `safe_discovery_sf=0x03` | KWP2000 StartRoutineByLocalIdentifier: a 1-byte LID, no safe sub-function, actuates |
| `services.service_info` | `0x2C` | DynamicallyDefineDataIdentifier | KWP2000 DynamicallyDefineLocalIdentifier: 1-byte target id, no sub-function |
| `services.service_info` | `0x38` | one row, both names in the label | RequestFileTransfer (UDS) / StartRoutineByAddress (KWP2000) |
| `layout.response_layout` | `0x71` | `SF` + `RID(2)` | KWP2000 `71 <LID> …` |
| `layout.response_layout` | `0x6C` | `SF` + `DID(2)` | KWP2000 `6C <LID>` |
| `describe.decode_uds_response` | `0x71`, `0x6C` | "RoutineControl: … routine 0x…", "…: defineByIdentifier, dynamic DID …" | the same KWP2000 shapes as above |
| `subfunctions.subfunction_name` | `0x10` | `0x81`–`0x83`, `0x85` always get the KWP2000 names | on a UDS ECU these bytes are `0x01`–`0x05` with the suppress-positive-response bit: `0x82` is programmingSession |

The `services.py` module docstring states the current policy: "where a byte means different
things in each protocol we keep the entry that matches how *this* project's ECUs actually use
it". That is the policy this plan replaces.

Apart from the routines defect above, these lookups only label output:

- The KWP2000 `0x71`/`0x6C` shapes reach the lookups from a reply canair did not ask for: `bix
  -a` on a pasted reply, an imported log, or a reply to another tool.
- A UDS ECU does not send `50 82` (the suppress bit suppresses the reply). The wrong session name
  shows only where canair names a *request*. `live/monitor/sessions.py` does this in the
  monitor's session picker, for every ECU.
- `safety/blocklist.py` and `safety/actuation.py` branch on the protocol themselves for the
  divergent SIDs.

### Who calls the lookups, and whether it knows the ECU

| Caller | Lookup | Knows the ECU |
|---|---|---|
| `cli/commands/bix/annotate.py` | `service_response_name`, and `response_layout` through `expression/byte_rows.py::resolve_annotate_layout` | with `--ecu` (`bix/entry.py` already passes the protocol to `nrc_description`) |
| `cli/commands/bix/subfunction.py` | `service_response_name` | through its caller |
| `live/read/raw.py`, `live/read/exec.py`, `live/read/batch.py` | `decode_uds_response` | yes (the request's TX id) |
| `live/monitor/sessions.py` | `SUBFUNCTION_NAMES[0x10]` | yes |
| `live/scan/sessions.py` | `SUBFUNCTION_NAMES[DIAGNOSTIC_SESSION_CONTROL]` | yes |
| `live/actuate/routines_execute.py` | `subfunction_name(ROUTINE_CONTROL, …)` | yes |
| `analysis/misfiled.py` | `layout_of` | yes (the reply's RX id) |
| `cli/commands/search/findings.py` | `layout_of` | yes (each finding's ECU) |
| `live/scan/range.py`, `live/scan/presets.py`, `captures/build.py` | `identifier_width` | the scan's ECU |
| `expression/decode_value.py` | `layout_of` | no (the evaluator has no profile) |
| `uds/echo.py::reply_check` | `identifier_width` | it takes `protocol` already, for memory reads |
| `safety/actuation.py`, `live/actuate/iocontrol_actuate.py` | `identifier_width`, for `0x2F`/`0x30` only | not needed: both SIDs mean one thing |

### Protocol facts in `live/`

`docs/development/architecture.md` places code that "describes protocol bytes with no I/O" in
`uds/`. These modules do no I/O but live in `live/`:

- `live/identity/records.py` is the table of UDS identity DIDs (`22 F1xx`) and KWP2000 identity
  records (`1A 8x/9x`), plus a per-protocol `PROTOCOLS` map of request prefix and payload offset.
  `live/identity/read.py` and `live/identity/software.py` read the payload offsets (`3`, `2`),
  which restate the header widths `uds/layout.py` already holds for `0x62` and `0x5A`.
  `IDENTITY_DIDS` is a compatibility alias that only `tests/live/identity/test_identity.py` reads.
- `live/dtc/codes.py` formats SAE J2012 codes and decodes UDS (4-byte) and KWP2000 (3-byte) DTC
  records. Only `resolve_protocol` there needs the ECU registry. The `dtc/` package already holds
  the I/O-free snapshot parser (`dtc/snapshot.py`) that sits next to this code.
- `contribute/pii.py` declares the VIN identifiers again (`_SENSITIVE_UDS_DIDS = {"F190"}`,
  `_SENSITIVE_KWP_RECORDS = {"90"}`) and strips the `22`/`1A` prefix by hand.

These stay where they are, because they are policy, not protocol facts:

- `live/identity/decode.py`: it reads the ECU registry and uses `expression`, which `uds` may not
  import.
- `live/dtc/reading.py`'s KWP2000 request forms (`_KWP_READ_REQUESTS`, `_KWP_SUPPORTED_REQUEST`)
  and their notes: the order to try them is reading policy.
- `live/scan/sessions.py`'s `UDS_SESSION_MODES`/`KWP_SESSION_MODES`: which sessions to probe is
  scan policy, and both read the `subfunctions` enums.
- `live/scan/kwp_iocontrol.py` and `live/scan/kwp_routines.py`: they send different probes under
  different safety rules.

### Small defects found while checking

- `uds/subfunctions.py` lines 158–159 say the `0x2F` names are "Mirrored by the KWP2000 0x30
  entry below (protocol-shared values)". Since phase 3 of the named-constants plan, `0x30` has its
  own table that shares only `0x00`. The comment is false.
- `live/monitor/sessions.py` lines 191 and 231 write `SUBFUNCTION_NAMES.get(0x10, …)`. The
  protocol-literal scanner does not match a literal passed to `.get()`.
- `uds/services.py` has `UDS = "UDS"` and `KWP2000 = "KWP2000"` family tags. `uds/protocol.py`
  has `UDS = "uds"` and `KWP = "kwp"`. Two different `canair.uds` constants named `UDS` hold
  different values. Only `tests/uds/test_uds_services.py` reads `ServiceInfo.family`.
- `uds/services.py`'s docstring calls it "Data-only module (like `identity_records.py`)". It also
  has functions now, and the module is `live/identity/records.py`.
- `live/identity/read.py` (lines 8–9) and `live/identity/records.py` (line 12) still name the old
  modules `identity_records` and `identity_decode`.

## Decisions to make

| # | Question | Recommendation |
|---|---|---|
| 1 | What does `canair routines` do on a KWP2000 ECU? | Decided (2026-11-06): refuse it, pointing at `canair scan routines` (the `0x33` read). A KWP2000 routine start (`31 <LID>`) is a different request with make-specific entry options, so it needs its own design. |
| 2 | What does a lookup return for an ECU of unknown protocol? | Today's merged row and names, so a caller that passes no protocol sees no change. The argument is `protocol: DiagProtocol \| None = None`, as in `nrc_description`. |
| 3 | How are the per-protocol rows written? | A second literal table in `services.py`, `_PROTOCOL_ROWS: dict[DiagProtocol, tuple[ServiceInfo, ...]]`, holding only the SIDs that differ. `SERVICES` stays the table for an unknown protocol. This keeps decision 4 of the named-constants plan (the registry stays literal). |
| 4 | The two `UDS` constants | Rename the family tags to `FAMILY_UDS`, `FAMILY_KWP2000`, `FAMILY_BOTH`, `FAMILY_OBD2`. |
| 5 | Where DTC record decoding goes | `dtc/records.py`, next to `dtc/snapshot.py`. The alternative is `uds/dtc_records.py`. |
| 6 | Does `contribute/pii.py` read the VIN identifiers from the identity table? | Yes: a `vin` flag on the two rows, so the PII check and `canair identity` cannot disagree. |
| 7 | What does `scan range` do with a service that can change the car's state? | Decided (2026-11-06): ask first (`core.prompt.confirm`, `--yes` answers), and refuse `0x31` outright on a KWP2000 ECU, where every request starts a routine. `canair scan iocontrol`/`routines` stay the safe paths, and the question names them. |
| 8 | Where does the default session come from? | One function, `uds/subfunctions.py::default_session_type(protocol)`: `SessionType.EXTENDED_DIAGNOSTIC_SESSION` for UDS and an unknown protocol, `KwpSessionType.STANDARD_DIAGNOSTIC_SESSION` for KWP2000. A profile `session_mode` or `--mode` still wins. |

## Phase 0 — refuse UDS RoutineControl on a KWP2000 ECU (done, 2026-11-06, `c56f2507`)

`live/actuate/routines_execute.py::check_routines_protocol` raises `SafetyBlocked` (exit 2) for
an ECU whose `id_protocol` is KWP2000, judged by `SessionPolicy`, or whose `routines:` holds a
key shorter than a UDS routine id. Three places call it:

- `cli/live/handlers/actuators.py::handle_routines`, first, so a refused ECU is never asked the
  startRoutine question;
- `mode_routines_execute` and `mode_routines_tui`, before they open a session, for any other
  caller.

The check runs after the device connects, on the definitions as revision detection left them,
because an ECU revision can change `id_protocol`. The rule lives in `routines_execute.py`, not in
a module of its own: a new module would raise the parser build's module count
(`test_parser_build_does_not_load_more_modules`). Tests:
`tests/live/actuate/test_routines_kwp2000_refusal.py` and
`tests/cli/commands/test_actuator_handlers.py`. `canair routines --help` and
`docs/reference/cli/routines.md` say a KWP2000 ECU is refused.

Both modes check before `SessionManager` opens a session: `mode_routines_execute` before its
`held(tx_id)` block, and `mode_routines_tui` before it builds the TUI, whose session opens in
`_RoutinesTUI` (`self.sm.open_session`).

## Phase 0b — ask before `scan range` sends an actuating service (done, 2026-11-06)

`live/scan/range.py` holds the rule (decision 7):

- `check_range_scan` raises `SafetyBlocked` (exit 2) for RoutineControl (`0x31`) when the ECU's
  protocol, as `terminal.session_policy` resolves it, is KWP2000. `mode_scan` calls it before it
  sends anything, so every caller is covered.
- `confirm_actuating_scan` runs that check, then builds each request of the scan
  (`scan_request`, which `mode_scan` now sends too) and asks once when any of them is `actuate`
  by `safety/actuation.py::classify` and passes the blocklist. A request the blocklist refuses
  is not asked about, because the blocklist refuses it at send time. A read or a release
  (`2F {DID} 00` on UDS) asks nothing. The question is `confirm(..., danger=True)`; the
  warning before it counts the requests, names the first, points at `canair scan iocontrol`
  and `canair scan routines`, and prints the preset's `caution`.

Two callers ask:

- `cli/live/handlers/scans.py::handle_scan`, with `--yes`/`-y` (new on `scan range`) as the
  answer. A decline exits 1.
- The `scan` step of the `read`/`monitor` pipeline (`live/read/exec.py::_exec_scan`). A pipeline
  has no `--yes`, so it asks on a terminal, and without one the error says to pass `--yes` to
  `canair scan range` instead. A decline skips the step, as its other errors do.

Tests: `tests/live/scan/test_range_scan_safety.py`. With the rule switched off, 8 of its 13
tests fail. `docs/reference/cli/scan.md` shows the new flag and tip.

## Phase 0c — ask about an ECU whose protocol is unknown (done, 2026-11-06)

An ECU whose `id_protocol` is missing, `none` or `unknown` fell into the UDS group of
`ecus/protocol.py::split_ecus_by_protocol` ("the historical default"), so `canair scan routines`
swept `31 03 F000`–`31 03 F0FF` without asking, and `canair routines --rid` sent `31 03 {RID}`.
On a KWP2000 ECU each of those starts routine `0x03`. In the bundled profiles that is every ECU
of the Ioniq 5 and 12 each of the Niro and the Leaf. `canair scan range` already asked, because
the classifier reads an unknown `0x31` as `actuate`.

- `cli/live/handlers/reads.py::confirm_unknown_protocol` warns, hints at `canair identity ECU`,
  and asks (`--yes` answers). It judges the protocol from the definitions in hand, through the
  same `SessionPolicy` as the phase 0 refusal. `handle_routines` asks it for every sub-function
  and before the TUI, ahead of the startRoutine question; `handle_routines_scan` asks it for the
  UDS group.
- `canair identity` now sends both probes (`22 F190`, `1A 90`) when the profile names no
  protocol. `live/identity/decode.py::decide_protocol` lets a positive reply outweigh an NRC, so
  a KWP2000 ECU that answers `22 F190` with requestOutOfRange is no longer read as UDS. When the
  reading returns fields on one protocol and the other's service is refused with
  serviceNotSupported (`0x11`), `mode_identity` returns a `ProtocolFinding`, and
  `cli/live/handlers/reads.py::offer_protocol_record` offers to write `identity.id_protocol`
  (`--set-protocol` writes it without asking; with no terminal a hint names the flag). It does
  not offer for an unregistered ECU, an ECU with `revisions:`, one without `identity:`, or a
  layered profile. `--json` adds the two probe replies as `probe` and keeps stdout clean.
- `uds/protocol.py::id_protocol_value` is the inverse of `classify_protocol`, and
  `yaml_edit/echo.py::echo_edit` takes a `file` for a `--json` command.

Tests: `tests/live/identity/test_identity_protocol.py`,
`tests/cli/commands/test_identity_protocol_record.py`, and new cases in
`tests/cli/commands/test_actuator_handlers.py` and `tests/live/scan/test_scan_safety.py`.
`docs/concepts/ecu-protocols.md` has a section on an ECU of unknown protocol.

## Phase 0d — the identity probe and read loop (done, 2026-11-06)

Three faults of the phase 0c reading, from `plans/2026-11-06-identity-probe-handoff.md`:

- A refusal for this session names its protocol. `0x7E`/`0x7F` exist only in UDS and `0x80` only
  in KWP2000, so `live/identity/decode.py::decide_protocol` takes the two probe NRCs
  (`ProbeResult.uds_nrc`, `kwp_nrc`) and reads `22 F190` refused with `0x7F` as UDS, and
  `1A 90` refused with `0x80` as KWP2000. When both probes say so, it decides nothing, and the
  reason names both replies; only two silent probes read as "asleep". The proof
  rule is unchanged: a `ProtocolFinding` still needs `0x11` from the other service. With no
  `--session`, a probe refused for the session skips the record loop, because the records draw
  the same NRC. `--json` `probe` carries `uds_nrc` and `kwp_nrc`.
- A silent ECU costs three reads, not the table. With a known protocol (no probe), the loop stops
  after `SILENT_READS_BEFORE_STOP = 3` silent reads while nothing has answered. A stale, dropped
  or undecodable reply is not silence. `--json` `summary` gains `not_asked`.
- The NRCs drawn pick a hint on stderr (`decode.py::refusal_hint`): `--session` for session
  refusals, "the session canair opened (10 03)" with `--session` (KWP2000: it may need
  `10 81`, which `canair identity` cannot open yet), security access for `0x33`, and "none of the
  standard records" for `0x31`/`0x12`/`0x11`. Some fields plus session refusals say "--session
  may return more fields". `summary` gains `nrc_codes`. The all-silent hint moved from stdout to
  stderr.

Tests: `tests/live/identity/test_identity_protocol.py` and `test_identity.py`. Two older tests
now script a leading record, because an ECU that ignores its first three records no longer gets
read to the end of the table.

## Phase 1 — small fixes

1. Correct the stale `0x2F`/`0x30` comment in `uds/subfunctions.py`.
2. `live/monitor/sessions.py`: read `DIAGNOSTIC_SESSION_CONTROL` instead of `0x10`.
3. Optional: add the `.get(<hex>` form to `scripts/check/protocol_literals.py`, if it finds no
   false positives in the tree.
4. Correct the `uds/services.py` docstring and the module names in `live/identity/read.py` and
   `live/identity/records.py`.

No behaviour change. One commit.

## Phase 2 — a protocol-aware service registry

1. `services.service_info`, `identifier_width`, `service_name` and `service_response_name` take
   `protocol: DiagProtocol | None = None`. A known protocol reads `_PROTOCOL_ROWS[protocol]`
   first, then `SERVICES`.
2. Rows (to be checked against ISO 14229-1 and SSF 14230-3 before they land):
   - KWP2000 `0x31` StartRoutineByLocalIdentifier: `id_width=1`, no `safe_discovery_sf`,
     `actuates=True`.
   - KWP2000 `0x2C` DynamicallyDefineLocalIdentifier: `id_width=1`.
   - KWP2000 `0x38` StartRoutineByAddress, and UDS `0x38` RequestFileTransfer: both
     `actuates=True`.
   - UDS `0x31` RoutineControl and UDS `0x2C` DynamicallyDefineDataIdentifier, with today's
     fields.
3. Replace the docstring's "keep the entry that matches this project's ECUs" policy with the rule
   above. Remove the `0x31` NOTE comment, because the KWP2000 row now states it.
4. Decision 4: rename the family tags.
5. Pass the protocol from `live/scan/range.py`, `live/scan/presets.py` and `captures/build.py`.
   Phase 0b already refuses a range scan of `0x31` on a KWP2000 ECU.
6. `uds/echo.py::reply_check` passes the `protocol` it already takes on to `identifier_width`,
   and `uds/parse.py::_sent_request` (which reads the echo of the command as sent) calls
   `reply_check(text, protocol)` instead of `request_echo(text)`. Every terminal already passes
   the protocol to `parse_uds_response`, so a KWP2000 `0x31` reply is then checked against its
   1-byte LID echo, on every transport. The callers that derive an `expected_echo` themselves
   through `request_echo` (eight call sites, for example `live/identity/read.py` and
   `live/monitor/poll_elm.py`) pass no protocol and keep today's check. None of them is a routine
   path.
7. Tests in `tests/uds/test_uds_services.py`:
   - every `_PROTOCOL_ROWS` SID also has a `SERVICES` row;
   - each protocol row's family matches its protocol;
   - `service_info(0x31, KWP).safe_discovery_sf is None`;
   - every lookup with no protocol returns what it returns today.

## Phase 3 — protocol-aware layout, description and names

1. `layout.response_layout(resp_sid, sub_function=None, protocol=None)` and `layout_of(payload,
   protocol=None)`. A per-protocol override table, like `_SUBFUNCTION_FIELDS`, gives KWP2000
   `0x71` → `(LID,)` and `0x6C` → `(LID,)`. The identifier-width fallback passes the protocol to
   `service_info`.
2. `describe.decode_uds_response(data, protocol=None)`. KWP2000 describers for `0x71`
   ("StartRoutineByLocalIdentifier: LID 0x…") and `0x6C`.
3. `subfunctions.subfunction_name(request_sid, value, protocol=None)` for `0x10`:
   - KWP2000: `KwpSessionType` names at or above `0x80`, the ISO 14229-1 names below (the
     blocklist's `_kwp_block` judges the low band the same way).
   - UDS: names `value & 0x7F` and adds "(suppressPosRsp)" when bit 7 is set, so `0x82` reads
     "programmingSession (suppressPosRsp)".
   - Unknown: today's merged table.
   `SUBFUNCTION_NAMES` stays the literal table. The protocol rule sits in `subfunction_name`.
4. Pass the protocol where the caller knows the ECU:
   - `expression/byte_rows.py::resolve_annotate_layout` takes it, and `bix/annotate.py` passes
     the one `bix/entry.py` already resolves;
   - `live/read/raw.py`, `live/read/exec.py`, `live/read/batch.py`;
   - `live/monitor/sessions.py`, `live/scan/sessions.py`;
   - `analysis/misfiled.py`, `cli/commands/search/findings.py`.
   `expression/decode_value.py` keeps the unknown protocol.
5. Tests: `tests/uds/test_uds_layout.py`, `test_uds_response_decode.py` and
   `test_uds_subfunctions.py` get KWP2000 and UDS cases for each divergent SID. The existing cases,
   which pass no protocol, must pass unchanged. The `bix` command tests get one KWP2000 `71 …`
   reply with `--ecu`.

Output changes, to name in the commit body:

- `bix -a 71… --ecu <KWP2000 ECU>` labels byte 1 `LID`, not `SF` + `RID`.
- The monitor's session picker on a UDS ECU names `81`/`83` as the suppress-bit forms of default
  and extended. It does not offer `82` on UDS today, because the blocklist refuses it.

## Phase 4 — protocol facts out of `live/`

1. Move the tables of `live/identity/records.py` to `uds/identity.py`:
   - derive each protocol's payload offset from `layout.response_layout(...).header_bytes`, for
     both `live/identity/read.py` and `live/identity/software.py`;
   - remove the `IDENTITY_DIDS` alias and update its test;
   - decision 6: mark the VIN rows, and have `contribute/pii.py` read them.
   `live/identity/decode.py` stays. Update the `live/identity/__init__.py` charter.
2. Move the formatting and record decoding of `live/dtc/codes.py` to `dtc/records.py`
   (decision 5). `resolve_protocol` stays in `live/dtc/`, because it reads the ECU registry.
   `live/dtc/__init__.py` re-exports the names it exports today and its module list changes.
3. Update the references to the moved modules:
   - `.claude/skills/contributing-code/SKILL.md` line 301 cites the `live/identity/read.py`
     split into `decode.py`/`records.py` as the pattern;
   - the `uds/services.py` docstring;
   - `tests/` imports; tests move to the directory that mirrors the new module.
4. `make structure` must pass, and no baseline may rise. `scripts/check/doc_paths.py` must pass.

## Phase 5 — the default session follows the protocol

This changes what canair sends, so it needs a check on the car (decision 8).

1. Add `default_session_type(protocol)` to `uds/subfunctions.py`.
2. Replace the four copies of `"03"` (see Today): `SessionManager` resolves a mode that is not
   given from the target's protocol (`terminal.session_policy.protocol(tx_id)`), and
   `ecus/wake.py`, `live/scan/kwp_iocontrol.py` and `query/steps.py` stop declaring one. An
   explicit `--mode`, a step's `--mode` and a profile's `session_mode` keep priority.
3. The Nissan quirk's `C0` stays as it is (`live/scan/kwp_iocontrol.py::NISSAN_SESSION_MODE`).
4. Update the help that tells a user to add `--mode 81` (`cli/commands/scan/probes.py`), say in
   `docs/concepts/query-mini-language.md` which session a `session` step opens by default, and
   regenerate `docs/reference/cli/`. `ecus/wake.py` then leaves `WakePlan.session_mode` empty
   when the profile names none, so `SessionManager.open_session` picks the mode.
5. Tests: a KWP2000 ECU with no mode gets `10 81`, a UDS or unregistered one `10 03`, and an
   explicit mode wins, through `SessionManager` and through a pipeline `session` step.
6. On the car: `--session` without `--mode` on the BMS and on a UDS ECU (IGPM), each with
   `--save`.

## Out of scope

- A KWP2000 routine-start command (see decision 1).
- Echo checks by protocol in the callers that build their own `expected_echo` (see phase 2).
- New describers for KWP2000 replies that have none today (`0x58`, `0x5A`, `0x70`, `0x73`). They
  are missing, not mislabelled.
- A `0x58` (KWP2000 DTC read) response layout.
- Make-specific session types (Hyundai/Kia `0x90`, Nissan `0xC0`). They stay with their quirks
  in `safety/quirks.py`.

## User-facing docs

Phase 0 changes `canair routines`: its help and the generated `docs/reference/cli/routines.md`
change in the same commit, and the commit body names the refusal for the release notes. Phase 0b
adds a question and `--yes` to `canair scan range`, and phase 5 changes the default session;
each updates its help and the generated reference, and names the change in its commit body.
Phases 1, 2 and 4 change no user-facing behaviour. Phase 3 changes labels in `bix` and the
monitor's session picker; no flag, default or file format changes, so `docs/` and `README.md`
need no update there.
