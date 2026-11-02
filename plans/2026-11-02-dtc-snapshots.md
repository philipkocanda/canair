# DTC snapshots: read, decode and log them with `canair dtc`

Status: **PHASES 1-4 DONE** (2026-11-02); phase 5 open. See "As built" for where the code
differs from the design below.

## Problem

An ECU that stores a fault code often stores a snapshot with it: the values of a few signals at
the moment the code was set. It is the only record of the car's state when the fault happened, and
clearing the code erases it. canair cannot use it:

- `dtc read` reads the codes (UDS `19 02`, KWP2000 `18`) and nothing else. Nobody is told that a
  snapshot exists, and `dtc read --clear` erases it unread.
- A snapshot can be fetched by hand (`read 'raw 733:1904582C00FF' --save`), but the reply is
  stored as an opaque payload. `bix --annotate` labels only its SID and sub-function, because
  `canlib/uds/layout.py` keys header layouts on the response SID alone.
- The reply is a list of records, each a list of `(DID, data)` pairs. The DID lengths are not in
  the reply (ISO 14229-1 leaves them to the client), so it cannot be split without knowing them.
- A snapshot DID has nowhere to be defined. On the Ioniq the CCM's C182C snapshots hold DID
  `4710`, which answers only from a snapshot (`22 4710` returns NRC 0x31 in every session). Its
  9-byte layout was decoded from the firmware and lives as prose in a `research:` entry
  (`ecus/ccm.yaml`), so nothing can evaluate it.

## Goal

`canair dtc read` also fetches the snapshots of every code it reports, decodes them with the
profile's definitions, prints them under the code and records them in the DTC log, all by default.
`dtc read --clear` reads and logs the snapshots before erasing them. A snapshot DID is defined
like any other PID, with the same editors, validation and coverage.

## Non-goals

- **Extended data records (`19 06`)** and **`19 0A` (reportSupportedDTC)**. They come after
  this, through the same seams. On the Ioniq every UDS ECU probed so far refuses both.
- **KWP2000 freeze frames (`0x12`) and OBD-II Mode 02** in the first phases. On the Ioniq the BMS
  and MCU answer the Mode 02 support bitmap, but no frame is stored to read. Phase 5 adds them
  behind a probe.
- **No time series.** A snapshot is a single reading taken when the fault was set, and the ECU
  does not say when that was. It never joins the time-aligned analyses.

## Design

### Modelling a snapshot DID: an ordinary PID

A snapshot DID is defined as a PID under its read form, `22<DID>`, with its signals written as
usual. To decode, the parser hands each `(DID, data)` pair to the existing evaluator as the reply a
live read would have given, `62 <DID> <data>`, framed the same way, so every `Bnn` expression means
the same thing for a snapshot as for a live read. If the DID ever becomes readable live, its
definitions already apply.

A new PID-level flag, **`snapshot_only: true`**, marks a DID the ECU serves only inside a snapshot:

- bare-ECU reads, `monitor` and sweeps skip it, as they skip `static`, and naming it explicitly
  still reads it;
- AutoPID export never ships it, whatever its `status:`;
- `coverage` counts snapshot captures as its evidence (see Storage) instead of reporting
  `NO CAPTURE`.

It is a flag rather than a `status:` value because the two are independent: a snapshot-only DID
still moves through `draft` → `active`, and many DIDs (Hyundai's included) appear in snapshots
*and* answer live. The editor is `pids set-snapshot-only ECU PID [--clear]`, and the schema adds the
field (`canlib/schema/pids_schema.yaml`). No flag is needed for DIDs that also answer live.

### Splitting a reply: knowing each DID's length

A `59 04` reply is `DTC(3) status(1)` followed by records:
`recordNumber(1) count(1) [DID(2) data(n)]×count`. The length `n` of each DID is resolved in this
order:

1. a declared **`snapshot_length:`** on the PID (written by the editor above, `--length N`);
2. the payload length of the DID's own live replies in the captures (a DID that answers live);
3. when the request asks for **one record** (`19 04 DTC RR`) and that record holds **one** DID,
   whatever remains of the reply.

Rule 3 is why canair asks for records one at a time (`19 03` first, then `19 04 DTC RR` for each
listed record) rather than `19 04 DTC FF`: it makes the common case, one DID per record,
self-describing. A pair whose length cannot be resolved is not guessed. The record is kept raw and
reported as undecoded, naming the DID, and the paste-ready `pids set-snapshot-only … --length`
command is printed when rule 3 measured it.

### Modules

`canlib/modes/dtc.py` is already 699 lines, so none of this goes into it, and it is split while we
are there (the read path, the clear path and the reporting are separate concerns).

| Module | Role |
|---|---|
| `canlib/dtc/snapshot.py` | **Leaf, I/O-free.** Parses `59 03` (record list) and `59 04` replies into `SnapshotRecord(number, dids=[(did, data)])`, given a length resolver. Knows nothing of profiles. |
| `canlib/dtc/snapshot_decode.py` | Bridge to the profile: the length resolver (rules 1–3), and decoding each `(DID, data)` through the PID's signals with the shared evaluator. |
| `canlib/modes/dtc_snapshot.py` | Device side: for each reported code, `19 03` (once per ECU, skipped for the rest of the run after an NRC), then `19 04` per record. Written against the `Terminal` surface only, so it runs through `dispatch_mode` on every transport. |
| `canlib/modes/dtc/` | `modes/dtc.py` split into read, clear and report modules, with the snapshot step called from read and clear. |

### Integration with `dtc read`

- **By default** every code `dtc read` reports gets its snapshots read. **`--no-snapshots`** opts
  out. The cost is one `19 03` per ECU that reported a code, plus one `19 04` per stored record;
  an ECU with no codes costs nothing, and one that answers `19 03` with an NRC (the BCM, HVAC and
  IGPM on the Ioniq) is not asked again in that run.
- **With `--all`, snapshots are a final step**, after every ECU's codes have been read and printed.
  The step reports its progress (`snapshots: 3/7 codes`), and on a TTY a keypress (`s` or
  `escape`) skips whatever remains of it. The scan is logged with the snapshots read so far and a
  line saying the step was skipped. Ctrl-C keeps its usual meaning (stop the command). Off a TTY
  there is nothing to press, so `--no-snapshots` is the way to skip. Running the step last means a
  slow snapshot read never delays the codes, which are what a sweep is for.
- **Output:** under each code, one block per record listing the decoded signals with units and
  enum labels, like `read`. An undecoded pair shows its DID and raw hex. `--json` adds a
  `snapshots` list to each code: record number, DIDs, raw hex, decoded values.
- **`--clear`:** before sending `14`, read the snapshots of every code it will erase and log them
  with the clear, since clearing erases them. This is part of the existing pre-clear read that
  records the codes an ECU held.
- **KWP2000 ECUs** get no snapshot step until phase 5. Their output says so only with
  `--verbose`.

### Storage

- **DTC log (always).** Each code in a scan entry gains `snapshots:`, a list of
  `{record, hex}` (the raw `59 04` reply per record), and a clear entry gains the same for the
  codes it erased. Following the log's rule ("only readings and clears are recorded"), the raw
  reply is stored and decoding happens when the log is read, so a later definition improves old
  entries. Because the DTC log is user state, a snapshot that holds something personal, like an
  odometer, stays private unless the user shares it.
- **Captures (only with `--save`).** `dtc read --save --label …` journals each snapshot reply as a
  payload capture under its request (`1904582C0001`), never under `224710`, so it can never pass
  for a live sample. The capture's time is when the snapshot was **read**, which the session notes
  say. This is what `contribute` can share and what `coverage` counts for a `snapshot_only` PID.
- **The DTC log format** goes from 2 to 3 (`snapshots:` is additive). The migration is a version
  bump with nothing to rewrite (`canlib/migrations/dtc_log.py`).

### Reading the log: `dtc log`

`dtc log` decodes and prints the stored snapshots under their codes. A snapshot identical to the
previous scan's for the same code prints as "unchanged since <timestamp>", so a code that stays
set does not repeat its snapshot on every scan.

### `bix --annotate` and the layout table

`canlib/uds/layout.py` gains sub-function-aware layouts for `0x59`: `03` (DTC, record number,
repeated) and `04` (DTC, status, then record number, count, DID, data). It becomes a lookup on
`(SID, sub-function)` that falls back to the SID-only entry, so every other service is unchanged.
`bix -a` then labels a snapshot reply's header bytes, and with `--ecu`/`--pid` its data bytes, via
the same resolver.

### Safety

`19 03` and `19 04` are reads: neither is in the `canlib/safety.py` blocklist, and neither is sent
functionally. Nothing new is mutative, and the clear path keeps its confirmation.

## Phases

1. **Parse and model.** `dtc/snapshot.py` with unit tests (one record, several records, several
   DIDs, an unresolvable length, truncated replies, an NRC). The `snapshot_only`/`snapshot_length`
   fields, schema, editor and validation. Skipping in sweeps and AutoPID. The `0x59` layouts in
   `layout.py` and `bix`.
2. **`dtc read` integration.** Split `modes/dtc.py`. Add `modes/dtc_snapshot.py`, the default
   snapshot step, `--no-snapshots`, the printed and `--json` output, and the
   transport-agnostic dispatch test (`tests/test_dtc.py::TestDispatchTransportAgnostic` is the
   template).
3. **Log and clear.** `snapshots:` in scan and clear entries, format 3, snapshots read before a
   clear, `dtc log` decoding and "unchanged since". `--save` writes captures, and `coverage` counts
   them for `snapshot_only` PIDs.
4. **The Ioniq's CCM.** Define `224710` (`snapshot_only`, `snapshot_length: 9`) with the signals
   the research entry lists, then retire the prose layout to a pointer. Verify it on the car with a
   real `dtc read CCM`.
5. **KWP2000 freeze frames and OBD-II Mode 02.** A probe of `0x12` on the Ioniq's KWP ECUs (BMS,
   VCU, MCU, OBC, AAF) and Mode 02 on the OBD responders, then a parser for whichever answers.
   Scoped once the probe shows the reply format.

## Tests

- Parser: table-driven cases from the two real CCM replies plus synthetic multi-DID and
  multi-record replies, and every error path.
- Decode: a fixture profile with a `snapshot_only` PID. A snapshot decodes to the same values a
  live `62 <DID> …` reply with the same bytes would.
- `dtc read`: a fake terminal answering `19 02`, `19 03` and `19 04`. Snapshots are fetched by
  default and skipped with `--no-snapshots`, an NRC to `19 03` stops further probes of that ECU,
  and `--json` has the right shape. With `--all` the snapshot step runs after every code is read,
  and a simulated keypress skips the rest of it while the codes and the snapshots already read
  are still logged.
- `--clear`: snapshots are read and logged before `14` is sent, and a declined confirmation reads
  nothing.
- DTC log: format 2 → 3 migration, round trip, and "unchanged since".
- `bix`: the `59 03`/`59 04` header roles, and the existing `0x59` layout unchanged for other
  sub-functions.

## Docs

`docs/reference/cli/dtc.md` is regenerated from `--help`. `docs/concepts/dtcs.md` gains a short
"Snapshots" section: what they are, that they are
fetched by default, how a snapshot DID is defined, and why a snapshot capture is not a live
sample. `AGENTS.md`'s `canair dtc` entry gets one line on snapshots and the new modules.

## Decisions

- **`--all` fetches snapshots too**, as a final step that a keypress can skip, with
  `--no-snapshots` to leave it out (Integration with `dtc read`).
- **`snapshot_only`** is the flag's name.

## Open questions

1. **Where `snapshot_length` lives** if a DID turns out to differ by ECU revision. It would go
   through the existing `per_revision:` mechanism, which needs it added to
   `PID_OVERRIDE_FIELDS`. That isn't needed until a car shows it.
2. **Record numbering.** AUTOSAR Dem usually means 01 = first occurrence and 02 = most recent, but
   it is configurable. The output prints the record number and says nothing more until a profile
   can declare it.

## As built

Phases 1-4 shipped on 2026-11-02; phase 5 (KWP2000 freeze frames, OBD-II Mode 02) is open.
Where the code differs from the design above:

- **The DTC log stays at format 2.** `snapshots:` is an optional key on an ECU's scan
  entry and on a clear, so an older canair reads the log as before and no migration is
  needed; bumping the format would have forced one for nothing.
- **The last DID of a one-record reply always takes the remaining bytes**, even when a
  `snapshot_length:` is declared: the reply's own length is the stronger evidence.
  Declared and live lengths serve the DIDs before it in a record.
- **`bix -a` labels the `59 04` header only.** Mapping a snapshot's data bytes onto the
  DID's signals in `bix` is not done; `canair dtc` and `canair dtc log` print the decoded
  values.
- **`--save` is a `dtc read` flag** with its own `--notes`; `--label`/`--state` serve both
  the log entry and the capture session.
- **Modules.** The device side is `canlib/modes/dtc/snapshots.py`, `--save` is
  `snapshot_capture.py` beside it, the text is `canlib/dtc/snapshot_report.py` (shared by
  `dtc read` and `dtc log`), and the keypress skip is `canlib/tui/skip_key.py`.
- **Phase 4:** CCM `224710` is defined with 29 signals from the firmware layout, and both
  stored C182C records decode to what was worked out by hand.

