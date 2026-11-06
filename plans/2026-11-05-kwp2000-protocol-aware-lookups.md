# UDS and KWP2000: protocol-aware lookups, and protocol facts out of `live/`

Status: **PROPOSED** (2026-11-06). Phase 0 is done. No other code changed. Checked against
`b0ecf7be`.

## Summary

UDS (ISO 14229) and KWP2000 (ISO 14230 / SSF 14230-3) facts share the `canair.uds` package. That
is correct, and this plan keeps it. Three things are wrong:

1. `canair routines` builds a UDS RoutineControl request for every ECU. On a KWP2000 ECU the same
   bytes start a routine, and nothing refuses them (phase 0).
2. Three lookups key on the service ID (SID) alone, although a few SIDs mean different things on
   each protocol. The registry then holds one protocol's meaning, and callers must remember the
   other. `uds/nrc.py::nrc_description(nrc, protocol)` already solves this for negative response
   codes (NRCs). This plan gives the other lookups the same optional `protocol` argument
   (phases 2 and 3).
3. Some pure protocol facts live in `live/`, where layers below `live` cannot reuse them
   (phase 4).

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

## Today

### `canair routines` on a KWP2000 ECU

On UDS, `31 03 <RID>` is RoutineControl requestRoutineResults, which only reads. On KWP2000,
`0x31` is StartRoutineByLocalIdentifier: `31 03 00 05` starts routine local identifier (LID)
`0x03` with entry options `00 05`.

- `canair scan routines <KWP2000 ECU>` probes with `0x33` and writes each hit to the ECU's
  `routines:` with a 2-digit key (`live/scan/kwp_routines.py::_write_hit`, `key_width=2`).
- `canair routines <ECU> --rid <RID>` defaults to sub-function `0x03` and sends it with no
  prompt. Its help calls this "Request results (SF 0x03, safe)". The request is always
  `uds/requests.py::routine_control_request(sub_function, int(rid, 16))`, a UDS request. Neither
  `live/actuate/routines_execute.py` nor the routines TUI (`live/actuate/routines.py`) checks the
  ECU's protocol.
- `safety/blocklist.py::check_command_safety` skips its RoutineControl check when the target speaks
  KWP2000 ("On KWP2000, 0x31 is StartRoutineByLocalIdentifier: byte 2 is a local id"). Nothing
  else refuses the request.
- `safety/actuation.py::classify` does classify a KWP2000 `0x31` as `actuate`, but only `canair
  io` calls it (`on_refusal`). `plans/2026-10-22-actuation-safety-interlock.md` (PROPOSED) would
  route the routines paths through it, but it is not implemented.

So after a KWP2000 routines scan, `canair routines <ECU> --rid <LID>` starts routine `0x03` with
no confirmation. No bundled profile has a `routines:` section on a KWP2000 ECU today, so no
shipped data triggers this. One scan creates such a section.

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
| `uds/echo.py` | `identifier_width` | no |
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
- `live/monitor/sessions.py` lines 155 and 192 write `SUBFUNCTION_NAMES.get(0x10, …)`. The
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

## Phase 0 — refuse UDS RoutineControl on a KWP2000 ECU (done, 2026-11-06)

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
   First check that a range scan cannot send `0x31` to a KWP2000 ECU today; if it can, that is a
   second phase-0 defect.
6. Tests in `tests/uds/test_uds_services.py`:
   - every `_PROTOCOL_ROWS` SID also has a `SERVICES` row;
   - each protocol row's family matches its protocol;
   - `service_info(0x31, KWP).safe_discovery_sf is None`;
   - every lookup with no protocol returns what it returns today.

`uds/echo.py` keeps calling `identifier_width` with no protocol. A KWP2000 `0x31` request would
get a 1-byte echo check with the protocol, but after phase 0 only `raw` and `read` can send one.
Passing the protocol there needs the ECU at every `send_uds` call site, which is out of scope.

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

## Out of scope

- A KWP2000 routine-start command (see decision 1).
- Echo validation by protocol (see phase 2).
- New describers for KWP2000 replies that have none today (`0x58`, `0x5A`, `0x70`, `0x73`). They
  are missing, not mislabelled.
- A `0x58` (KWP2000 DTC read) response layout.
- Make-specific session types (Hyundai/Kia `0x90`, Nissan `0xC0`). They stay with their quirks
  in `safety/quirks.py`.

## User-facing docs

Phase 0 changes `canair routines`: its help and the generated `docs/reference/cli/routines.md`
change in the same commit, and the commit body names the refusal for the release notes. Phases 1,
2 and 4 change no user-facing behaviour. Phase 3 changes labels in `bix` and the monitor's session
picker; no flag, default or file format changes, so `docs/` and `README.md` need no update there.
