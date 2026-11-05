# ReadMemoryByAddress: map, dump and poll ECU memory

Status: **DRAFT** (2026-11-05). Phase 0 is done (see "Phase 0 results"): five ECUs on the
reference car answer `0x23` in the default session. Nothing is built.

## Problem

Some of what an ECU knows is not behind any DID: internal RAM variables, calibration tables and
constants. ReadMemoryByAddress (service `0x23`, UDS and KWP2000) reads it directly, and canair
cannot use it:

- `scan` sweeps DIDs, local identifiers, routines, IOControl and sessions. It has no way to find
  which address ranges an ECU lets a tester read.
- A memory read can be sent by hand (`read 'raw 7E4:2324…'`), but nothing builds the request,
  checks the reply or keeps the result in a form other tools can use.
- A query token cannot name a memory read: `query/__init__.py::inferred_request` accepts only
  `21`/`22` reads and the `SERVICE/ID` forms. So a RAM variable cannot be polled in `monitor`,
  saved, decoded or correlated.
- The `0x63` reply carries no echo of the address it answers (UDS). The stale-reply check in
  `uds/echo.py` cannot validate it, so a desynchronised pipe would file one address's bytes as
  another's without any error.
- `bix --annotate` gives the reply's bytes no roles: `uds/layout.py` leaves `0x63` out.

RequestUpload (`0x35`) is not an alternative. It starts a `35`/`36`/`37` transfer that most ECUs
accept only in programmingSession, which canair hard-blocks.

## Goal

1. `canair scan memory ECU --range …` maps which address ranges an ECU answers, and writes the map
   into the ECU's definition.
2. `canair memory dump ECU START-END` reads a range to a local file, and `canair memory diff`
   compares two dumps, so a RAM variable can be found by dumping in two vehicle states.
3. A memory read is a PID like any other (`ECU:23/0001A2B0+04`): `read`, `monitor`, `--save`,
   `decode`, `hunt`, `correlate`, `coverage` and the editors work on it with no special case.
4. Every memory reply is checked, so a desync cannot pass one address's bytes off as another's.

## Non-goals

- **Writing memory.** WriteMemoryByAddress (`0x3D`) stays blocked. Nothing in this plan sends it.
- **RequestUpload (`0x35`) and the transfer services.** They stay blocked.
- **SecurityAccess (`0x27`).** canair never unlocks an ECU to read memory. A region that answers
  NRC `0x33` (securityAccessDenied) is reported as locked, and the run stops asking for it. See the
  security-key rules in the **contributing-code** skill.
- **Searching dumps for keys, seeds or algorithms.** No such feature, in any form.
- **Firmware analysis.** canair reads bytes. Disassembly and calibration-map recovery belong in
  tools built for them (Ghidra, binwalk), which can open an exported dump.
- **Whole-RAM time series.** Repeated dumps of a large region into captures are out of scope. A
  dump is a single reading. A variable becomes a time series once it is defined as a memory PID.

## Protocol facts

| | UDS (ISO 14229-1) | KWP2000 (ISO 14230-3) |
|---|---|---|
| Request | `23 ALFID address size` | `23 address(3) size(1)` |
| Address/size width | set by the `addressAndLengthFormatIdentifier` (ALFID): high nibble = size bytes, low nibble = address bytes (`24` = 2-byte size, 4-byte address) | fixed |
| Positive reply | `63 data` (exactly `size` bytes, no address echo) | `63 data` (exactly `size` bytes, no address echo; seen on the Ioniq's MCU) |
| Typical NRCs | `13` wrong length/format, `14` responseTooLong, `22` conditionsNotCorrect, `31` requestOutOfRange, `33` securityAccessDenied | `11` not supported, `31`, `33`, `53` size too large, `79` wrong request length (Ioniq) |

Notes the design depends on:

- **The ECU decides what is readable.** An AUTOSAR diagnostic stack answers `0x23` only inside
  ranges its configuration lists, and `31` elsewhere. canair relies on that, but does not trust
  it alone (see Safety).
- **A wrong ALFID and a wrong address can both give `31`.** The spec allows it, and the Ioniq's
  BCM does it (phase 0). The HVAC answers `13` to a wrong ALFID instead. So a format probe must
  treat `13` as "wrong format" and `31` as "wrong format or wrong address".
- **The KWP2000 reply has no address echo** on the Ioniq (phase 0). I have not checked this
  against the standard text.
- **A 3-byte KWP2000 address reaches only the low 16 MiB.** On a 32-bit microcontroller whose RAM
  sits higher, the KWP2000 form reads flash only.
- The size of one read is limited by the ECU (`max_block`), by ISO-TP (4095 bytes) and by the
  transport. The ELM327 frame-count digit covers at most `MAX_REQUESTABLE_FRAMES` frames, so a large
  block is read without it. Phase 0 found the transport limit well below the ECU's on two ECUs.

## Design

### Safety and data handling

- **The blocklist does not change.** `0x23` is already a read in `safety/actuation.py` and is not
  in `BLOCKED_UDS_SERVICES`. A memory read is never sent on a functional id: the functional
  allow-list in `safety/blocklist.py::check_functional_request` does not list `0x23`, and a test
  pins that.
- **Reading memory can have side effects.** On some microcontrollers a read of a peripheral
  register clears a flag or pops a FIFO. The ECU's range table should stop such reads, but not
  every ECU has one. So:
  - `scan memory` and `memory dump` have **no default range**. The user names the ranges, usually
    from the microcontroller's datasheet memory map.
  - A scan probes with **1-byte reads** at a step (default `0x1000`), and reads more only inside a
    region that answered.
  - Both commands confirm before the first read (`--yes` skips, and off a TTY they refuse without
    it). The prompt names the ranges and says that reads of peripheral registers can change ECU
    state.
- **Dumps stay on the machine.** A dump can hold OEM software (copyright) and secrets (immobilizer
  data, SecurityAccess constants). Dumps are **user state**, never profile data:
  `<config dir>/memory/<profile>/<ECU>/`, like the DTC log (`dtc/log.py::log_file`). They are never
  journaled, never captures, never shipped by `contribute`, the blind strip, the explorer or
  AutoPID.
- **Memory PIDs are small.** A saved memory read longer than `MEMORY_SIGNAL_MAX_BYTES` (64) is
  refused at save time, and `contribute` refuses one as a second line of defence. This keeps a dump
  from entering `captures/` through a big "PID".
- **No ECU is refused by kind.** `memory dump` runs on an immobilizer or airbag ECU like on any
  other. The confirmation, the local-only dumps and the ECU's own range table are the protection.

### Protocol layer: `uds/memory.py` (new, I/O-free)

- `MemoryFormat` (address width, size width) with the ALFID encoding, and the KWP2000 fixed form.
- `memory_request(fmt, address, size) -> bytes` and the reverse, `parse_memory_request(request)`,
  so any tool can tell a memory PID's address and size from its request key.
- The NRC meaning per region (`readable`, `out_of_range`, `locked`, `conditions`, `unsupported`)
  as one classifier, used by the scan and the dump.
- The service byte and ALFID names go in `uds/services.py`/`uds/subfunctions.py`, so
  `scripts/check/protocol_literals.py` stays green.

### Reply validation: the expected length becomes part of the echo

`uds/echo.py::request_echo` returns `(sid, echo)`. It grows into a `ReplyCheck(sid, echo, length)`
type, and `parse_uds_response` takes it. For a memory read, `length = 1 + size` and `echo` is empty
(UDS). Every existing caller passes the same `sid`/`echo` as today, and `length` is `None` for
them. One home, as now.

The length check catches a one-slot offset only between reads of **different** sizes. Two
mitigations:

- **Dumps alternate the block size** (`N`, `N-1`, `N`, …). A reply that answers the previous
  request then has the wrong length, and the existing resync path (`CAT_STALE` →
  `Elm327Terminal._resync`) repairs the pipe. The cost is about 1 byte in `N`.
- **`monitor`** cannot vary a PID's size, because the size is part of its key. The scheduler does
  not poll two memory PIDs of the same size back to back on one ECU. It places an echo-validated
  read between them, which brings the offset to light. Whether this rule is worth its complexity
  is open question 2.

### Query token and PID key

- A memory read is written `23/ADDRESS+SIZE`, both hex: `VCU:23/0001A2B0+04`. The address width
  comes from the ECU's `memory.format`, so the token pads or refuses (an address too wide for the
  format). Expansion needs the ECU, so `explicit_request` returns a memory spec that the planner
  resolves, not a finished request.
- The **PID key is the full request hex** (`23240001A2B00004`), as with every other PID. Captures,
  `decode`, `coverage`, the editors and the analysis tools then need no special case. Display
  code shows the token form, through `parse_memory_request`.
- A bare token never infers a memory read. `inferred_request` keeps refusing anything it does not
  know.

### Profile model

- A per-ECU **`memory:`** section in `ecus/<ecu>.yaml`:
  - `format:` the ALFID (`"24"`), or `kwp` for the fixed form;
  - `max_block:` the largest size the ECU answered;
  - `session:` the session the reads need, when not the default;
  - `regions:` the scan results, each `{start, end, result, nrc?, software?, scanned}`, where
    `software` is the ECU's software version at scan time.
- Editors: `pids set-memory ECU --format 24 --max-block N --session 03` and the scan's own writer,
  both in `yaml_edit/` through `edit_ecu_text`. Schema fields in `schema/pids_schema.yaml`.
- **An address is valid for one software build.** A dealer reflash moves variables, and a memory
  PID then returns plausible wrong bytes with no error. So a memory PID must be gated to a revision
  (`revisions: [..]`) whose fingerprint names the software version, and `validate pids` warns when
  it is not. Today live detection matches only `responds:` fingerprints
  (`live/revision_probe.py`). Phase 5 adds identity fingerprints (read the software-version DID)
  for ECUs that have memory PIDs. Without a determined revision, the existing rule already skips
  the gated PIDs.
- **AutoPID never ships a memory PID**, whatever its `status:`, `verified` or revision gate. The
  device cannot check the ECU's software build before each read, so a reflashed ECU would feed
  wrong values to the MQTT feed with no error. The rule lives once, in `ecus/pids.py::pid_ships`
  (as `snapshot_only` does), and `validate pids` errors on a `send_to:` or `ship: true` on a memory
  PID's signal.

### `canair scan memory`

`canair scan memory ECU --range START-END[,…] [--step N] [--session] [--yes]`

1. Resolve the format: the ECU's `memory.format`, else `--format`, else probe the usual ALFIDs
   (`44`, `24`, `14`, `22`, `12`) with a 1-byte read of the first address and report what each
   answered. If the answers cannot tell a format from an address (all `31`), stop and ask for
   `--format`.
2. Probe a 1-byte read at every `--step` across each range.
3. At each change between two results, bisect to the exact boundary address.
4. Inside one readable region, find `max_block` by bisection on the size (`13`/`14`/`31` mean too
   large), capped by the transport.
5. Print the region table and write `memory:` into the ECU's definition, with the software version
   read from the ECU's identity DID.
6. Stop the scan, and report, on `33` (locked), on `22` repeated in every probe (wrong session or
   vehicle state), or when the ECU stops answering.

The scan goes through `run_session_guarded` over the `Terminal` surface only, like the other scan
kinds (`live/routines_scan.py` is the template). `--throttle-ms` as in the other kinds.

### `canair memory`: dump, list, show, diff, export

A new command group, because a dump is an acquisition, not a discovery:

- `memory dump ECU START-END[,…] [--block N] [--label …] [--yes]` reads the ranges in blocks and
  writes a dump: one `.bin` per readable stretch plus a `dump.yaml` manifest (ECU, software
  version, variant and revision tag, format, ranges asked, ranges read, holes with their NRC,
  canair version, time, label). A hole is never filled with invented bytes. Progress on stderr, a
  keypress skips the rest (`core/skip_key.py`), and Ctrl-C keeps what was read.
- `memory ls [ECU]` lists dumps. `memory show DUMP [--range …]` prints a hex dump.
- `memory diff A B [--min-run N]` lists the address ranges whose bytes differ, with both values. A
  diff of two dumps from different software versions is refused.
- `memory export DUMP --format ihex|srec|bin` writes a file other tools open. Intel HEX and
  S-record keep the holes.
- Every subcommand offers `--json`.

The RE loop this supports: dump RAM in two states, `memory diff`, define the changed bytes as a
memory PID (`pids upsert-param ECU 23/…+02 …`), then `monitor --save` and the usual analysis.

### Code placement

| Code | Package | Layer |
|---|---|---|
| ALFID, request build/parse, NRC classification, reply length | `uds/memory.py` | 14 |
| `ReplyCheck` | `uds/echo.py` | 14 |
| `0x63` layout | `uds/layout.py` | 14 |
| `23/ADDR+SIZE` token | `query/` | 9 |
| `memory:` section loader, region model | `ecus/memory.py` | 11 |
| Dump store, manifest, diff, export | `memory/` (new package) | 7 |
| `memory:` editor | `yaml_edit/` | 5 |
| Validation (format, revision gate, size cap) | `validation/` | 6 |
| Scan and dump over a `Terminal` | `live/memory/` | 4 |
| Commands | `cli/commands/scan.py` (kind), `cli/commands/memory/` | 1 |

**The new `memory/` package** sits at layer 7, beside `dtc/`, because it is the same kind of
thing: a user-state store with its own format, read by commands and fed by a live operation. No
existing package fits. `captures/` is the profile's shareable store, and dumps must never be in
it. Its charter: *Belongs:* the dump store, manifest, diff and export. *Not here:* reading the
car (`live/memory/`), the `memory:` definitions (`ecus/memory.py`). *Imports:* `ecus`, `variants`,
`profile`, `uds`, `core`. Export to Intel HEX/S-record could go in `interop/` instead (open
question 3).

## Phases

0. **Feasibility on the Ioniq (no code). DONE** — see "Phase 0 results". BCM, HVAC, VCU, MCU and
   OBC answer `0x23` in the default session.
1. **Protocol layer.** `uds/memory.py`, `ReplyCheck` with the length check in
   `parse_uds_response`, the `0x63` layout and description, the `23/ADDR+SIZE` token. Unit tests
   only.
2. **Profile model.** `memory:` section, schema, `pids set-memory`, validation (format, revision
   gate warning, size cap), `pid_ships` exclusion, the save-time and `contribute` size cap.
3. **`scan memory`.** Probe, boundary bisection, `max_block` search, region writer, stop rules,
   confirmation.
4. **`canair memory`.** The `memory/` package, `dump` with alternating block sizes, `ls`, `show`,
   `diff`, `export`.
5. **Memory PIDs end to end.** `read`/`monitor` over a memory PID, the scheduler rule for
   equal-size reads, and revision detection by software-version fingerprint.
6. **Reference car.** If phase 0 found a readable ECU: map it, find one RAM variable by diff,
   define it and verify it against a known signal with `hunt`.

## Tests

- `uds/memory.py`: ALFID encode/decode for every width pair, KWP2000 form, request round trip,
  addresses too wide for a format, NRC classification.
- Reply check: a reply of the wrong length is stale, a short reply is truncated, existing
  callers keep their behaviour (no `length`).
- A fake terminal that models a memory map (readable, `31`, `33` regions, a `max_block`) drives
  the scan: exact boundaries after bisection, the stop on `33`, the format probe, the written
  `memory:` section. Run it through its handler to prove it is transport-agnostic
  (`tests/dtc/test_dtc.py::TestDispatchTransportAgnostic` is the template).
- Dump: alternating sizes, a fake terminal that returns the previous reply once (the desync is
  caught, repaired and the block re-read), holes kept as holes, skip and Ctrl-C keep what was
  read.
- Safety: `0x23` is refused on a functional id, the confirmation is required off a TTY without
  `--yes`, a memory capture over the cap is refused at save and by `contribute`, dumps are never
  under the profile directory, and `wican autopid write --include-unverified` emits no memory PID
  even when it is `active`, `verified` and revision-gated.
- Validation: an ungated memory PID warns, a bad `format:` errors.
- `diff` and `export`: synthetic dumps only, never real ECU memory.

## Docs

- New `docs/concepts/memory-reads.md`: what `0x23` reads, the safety model, why dumps stay local,
  why an address belongs to one software build, the dump → diff → define loop.
- `README.md` command map: one line for `canair memory`.
- `docs/reference/cli/` regenerated from `--help`.
- `AGENTS.md`: the `scan` entry gains the `memory` kind, a `canair memory` entry, the `memory/`
  package in the code map.
- The **reverse-engineer-signal** skill: a short section on memory PIDs as a source of signals.

## Decisions

- **`MEMORY_SIGNAL_MAX_BYTES` is 64.**
- **No ECU is refused by kind.** `memory dump` runs on the SKM and the SRS like on any other ECU.
- **AutoPID never ships a memory PID.** Not now, and not after revision detection by software
  version exists.
- **The KWP2000 reply has no address echo** (phase 0), so the reply check is SID plus exact
  length on both protocols.

## Open questions

1. **Token syntax.** `23/ADDRESS+SIZE` reuses the `SERVICE/ID` form. Another option is a named
   form (`mem/…`).
2. **Equal-size reads in `monitor`.** Is the scheduler rule needed, or is the next echo-validated
   read enough?
3. **Export home.** Intel HEX/S-record writers in `memory/` or in `interop/`, which owns "formats
   canair does not own"?
4. **Ungated memory PIDs.** Warn (as designed) or error? An error is safer but blocks a profile
   whose ECU has only one known software build.
5. **The transport block limit.** Is it a property of the transport (measured once per link, like
   `LinkLatency`), or of the ECU-transport pair? Phase 0 saw the MCU truncate at 111 bytes on
   `wican-ws` while the BCM and HVAC delivered 128. `slcan-tcp` was not tested.

## Phase 0 results (2026-11-05)

Car charging (state `CHARGING`), transport `wican-ws`, default session unless stated. Every
request is saved in `captures/2026-11-05.json` under labels starting `phase0 0x23`.

**Step 1: which ECUs know the service.** A request with no address and size (UDS `23 24`,
KWP2000 `23`) reads nothing. An ECU that knows the service rejects the length; one that does not
answers `11`.

| Answer | ECUs |
|---|---|
| UDS `13` (knows the service) | BCM, HVAC |
| KWP2000 `79` (knows the service) | VCU, MCU, OBC |
| `11` serviceNotSupported | IGPM, CCM, DDM (also in the extended session `10 03`), ADM, PSM, AMP, BMS, Unknown-746 |
| no answer (asleep or not on while charging) | SKM, CLU, GSA, ESC, EPS, EPB, MFC, SCC, AVN, VESS, WPC, BSD-L/R, RCAM, SRS, AAF, Unknown-7C0 |

**Step 2: format and reply.**

| ECU | Format | Readable at | Out of range (`31`) | Wrong format |
|---|---|---|---|---|
| BCM | ALFID `24` (4-byte address, 2-byte size) | `0xFEDE0000`–`0xFEDFFFFF`, sampled at 5 addresses, all zeros | `0x0`, `0x100`, `0x20000000` | `31` for ALFID `13`/`14` (indistinguishable from a bad address), `13` for a short request |
| HVAC | ALFID `13` (3-byte address, 1-byte size) | `0x000000` (structured data), `0x000100` | — | `13` for every other ALFID |
| MCU | KWP2000 3-byte address | `0x000000`: `00 5A …` then PowerPC instructions, probably an MPC56xx boot header and code | — | — |
| VCU, OBC | KWP2000 3-byte address | `0x000000` (1 byte) | — | — |

- Neither protocol echoes the address. Every positive reply was `63` plus exactly `size` bytes.
- No ECU needed the extended session or SecurityAccess for these addresses.
- The MCU's flash returns firmware code. This confirms the rule that dumps stay local.

**Step 3: block size.**

| ECU | Worked | Failed |
|---|---|---|
| BCM | 1, 16, 32, 64, 128 bytes | 256: `FCRXTIMEOUT` from the adapter; 1024: a 1-byte `63` reply that canair saved as a valid payload |
| HVAC | 64, 128 | 255: no answer |
| MCU | 64 | 128, 192: truncated at 111 bytes (caught by the ISO-TP length check); 254: no answer; 255: NRC `53` |
| VCU, OBC | — | 255: NRC `53` |

The 1024-byte case is the failure this plan's length check exists for: a reply far shorter than
the request asked for passed every current check. The scan must find the largest block that
works over the transport in use, not only the one the ECU accepts.

**Side effects.** `dtc read` afterwards: no codes on BCM, HVAC, MCU or OBC. The VCU holds
U1112 (LIN communication error), which the profile already records from the 2026-11-01 scan.
Charging continued.

**Not settled.**

- No changing RAM value was found. The five BCM addresses read zero twice, 3 s apart. Finding one
  is phase 6 work, with a real scan.
- RAM on the MCU, VCU and OBC is probably above the 16 MiB a 3-byte address reaches, if they are
  32-bit parts. Their `0x23` may read flash only.
- `slcan-tcp` was not tested: the WiCAN is in `auto_pid` mode.

**Defects found in passing.**

- The `raw` step sent an odd-length request. `raw 7B3:2313000000040` went out with its last
  nibble read by the ELM327 as a response-frame count, and came back as "Empty response". Fixed
  the same day: `query/steps.py::parse_raw_spec` refuses it for the `raw` step, the `raw` command
  and `mode_raw`.
- A 1-byte reply to a 1024-byte memory request was stored as a valid capture (see above). That
  capture is deleted. The length check in phase 1 stops the next one.
