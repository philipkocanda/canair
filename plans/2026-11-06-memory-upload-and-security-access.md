# RequestUpload and SecurityAccess: faster memory dumps, unlocked regions

Status: **PROPOSED** (2026-11-06). No code changed.

Builds on `plans/2026-11-05-one-session-owner.md` (DONE, `7858cbea`..`e21df1ea`): the I/O-free
`transport/session_table.py::SessionTable`, `transport/session_manager.py::SessionManager` with
one transaction per exchange, `SessionOutcome`, `held()`, and `live/hold.py::held_session` for
the single-ECU commands. Paths are relative to `src/canair/`.

Supersedes two non-goals of `plans/2026-11-05-read-memory-by-address.md`: "RequestUpload (`0x35`)
and the transfer services stay blocked" and "canair never unlocks an ECU to read memory".

## Problem

`canair memory dump` reads with ReadMemoryByAddress (`0x23`): one request and one reply per
block. The ECU and the transport cap the block. On the Ioniq that is 254 bytes on the BCM and 128
on the HVAC, and a larger read hung the HVAC's diagnostics until its supply was cycled. So a dump
pays one link round trip per 128-254 bytes.

RequestUpload (`0x35`) starts a transfer: the ECU answers each TransferData (`0x36`) request
with a block of up to 4093 bytes, and RequestTransferExit (`0x37`) ends it. The ECU writes
nothing. canair cannot use it:

- `safety/blocklist.py::BLOCKED_UDS_SERVICES` blocks `0x35`, `0x36` and `0x37`, because `0x36`
  and `0x37` are also the second and third steps of a reflash (`0x34` RequestDownload).
- `uds/services.py` marks all three `actuates=True`, and `uds/describe.py` prints an upload
  reply as `WARNING: … ECU ready to send memory` and an exit as "firmware transfer completed".

Some regions answer NRC `0x33` (securityAccessDenied), to `0x23` and probably to `0x35`.
canair records them as locked and stops. SecurityAccess (`0x27`) unlocks them, and canair has
no way to send it, by design: the **contributing-code** skill says canair ships no seed→key
solver, no algorithm bank and no `security` pipeline step (commit `5019e724` removed all three).

## Goal

1. `canair memory dump ECU RANGES --method upload` reads a range with RequestUpload into the same
   dump store and manifest as a `0x23` dump, so `memory ls`/`show`/`diff`/`export` need no
   change.
2. `--security LEVEL` on `memory dump` (both methods) and `scan memory` unlocks the ECU first.
   The key comes from a **key provider**: a program that the user configures on their own
   machine. canair passes it the seed and sends the key it returns. canair ships no algorithm, no
   provider and no pointer to one.
3. `SessionManager` owns the security level as part of the session: unlocking, keeping it
   alive, and knowing when it is lost.
4. No seed, key or TransferData reply is ever saved, journaled, logged or shared.

## Non-goals

- **Programming sessions.** `10 02`, KWP2000 `10 85` and Hyundai/Kia `10 05` stay hard-blocked
  (decided 2026-11-06). On many ECUs the programming session jumps to the bootloader and stops
  the application. Upload works only on an ECU that accepts it in the default, extended or a
  quirk-allowed OEM session. Phase 0 checks whether any Ioniq ECU does.
- **Writes.** RequestDownload (`0x34`), a TransferData that carries data, WriteMemoryByAddress
  (`0x3D`) and the other write services stay blocked. An unlock does not change the blocklist.
- **Keys in canair.** No algorithm, no seed→key solver, no example provider that computes a key,
  no list of where a key comes from, no stored key. The forbidden list in the skills does not
  change.
- **Compressed or encrypted transfers.** canair sends dataFormatIdentifier `00` only.
- **Unlocked `read`/`monitor`.** A `--security` option on the query `session` step is open
  question 1, not part of this plan's phases.

## Protocol facts

| | UDS (ISO 14229-1) | KWP2000 (ISO 14230-3) |
|---|---|---|
| RequestUpload | `35 DFI ALFID address size` | `35 address(3) DFI size(3)` (not checked against the standard text) |
| Positive reply | `75 LFID maxNumberOfBlockLength`: the LFID's high nibble is the width of the length field | `75` plus a block length (not checked) |
| TransferData | `36 BSC` → `76 BSC data` | `36` → `76 data`; a block counter is OEM-specific (not checked) |
| RequestTransferExit | `37` → `77` | `37` → `77` |
| SecurityAccess | `27 L` (odd L) → `67 L seed`; `27 L+1 key` → `67 L+1` | same layout |

Notes the design depends on:

- **maxNumberOfBlockLength counts the whole TransferData message**, the SID and the block
  sequence counter (BSC) included, so a block carries at most `maxNumberOfBlockLength - 2` data
  bytes. The ECU sets it. The tester cannot ask for smaller blocks.
- **The tester can bound a transfer by its size.** An upload of `size` bytes smaller than one
  block comes back as one short block. This is the only way to fit a transport that cannot
  receive a full block, and it costs three round trips per chunk, which is slower than `0x23`.
- **The BSC is an echo.** It starts at `01` and wraps from `FF` to `00`. A reply with the wrong
  counter is stale, so a TransferData reply gets a check that a `0x63` reply cannot get.
- **A lost reply.** ISO 14229-1 lets the client repeat the last counter after a lost reply. I have
  not checked how the standard says the server answers that on an upload (Phase 1 reads the
  text; Phase 0 tries it on the car if an ECU accepts uploads).
- **Upload NRCs:** `13`, `22`, `24` requestSequenceError, `31`, `33`, `70`
  uploadDownloadNotAccepted, `71` transferDataSuspended, `72` generalProgrammingFailure, `73`
  wrongBlockSequenceCounter, `78` responsePending, `7E`/`7F` not in this session.
- **SecurityAccess NRCs:** `12`, `22`, `24` (a key without a seed, or a request between them on
  some ECUs), `35` invalidKey, `36` exceededNumberOfAttempts, `37` requiredTimeDelayNotExpired.
  The Ioniq BCM locks after about two wrong keys
  (`plans/2026-07-22-security-access-and-bms-session.md`).
- **An all-zero seed means the level is already unlocked.** No key is sent.
- **Level `0x5F`/`0x60` is for ISO 26021-2**, the end-of-life activation of pyrotechnic devices
  (airbags), as far as I know. Phase 1 confirms the range against the standard text.
- **An unlock lasts as long as the session.** A session change, an S3 timeout or an ECU reset
  locks the ECU again.

### Is it faster? An estimate

These numbers are estimates, not measurements. Assume a 500 kbit/s bus, about 0.26 ms per
8-byte frame (7 data bytes), an ECU that honours canair's flow control with STmin 0, and a link
round trip `L`. A block of `B` bytes costs about `L + B × 0.037 ms`.

| Method, block | L = 30 ms | L = 100 ms |
|---|---|---|
| `0x23`, 128 bytes (HVAC) | 3.7 KB/s | 1.2 KB/s |
| `0x23`, 254 bytes (BCM) | 6.5 KB/s | 2.3 KB/s |
| Upload, 4093 bytes | 23 KB/s | 16 KB/s |

So upload is about 3-7 times faster, and the bus caps both methods at about 28 KB/s. The gain
exists only on a transport that receives a 4095-byte ISO-TP message whole:

- `slcan-tcp` does ISO-TP on the client. It probably receives 4095 bytes, but the WiCAN must
  forward about 4000 frames/s over TCP. Not measured.
- On `wican-ws`, Phase 0 of the `0x23` plan saw `FCRXTIMEOUT` at 256 bytes on the BCM and a
  truncation at 111 bytes on the MCU. Upload over an ELM327 transport is probably not useful.

Phase 0 measures this. If the transport cannot take a full block, `--method upload` refuses
before it sends `0x35`, instead of chunking into something slower than `0x23`.

## Design

### Safety: what the blocklist allows

The rules stay stateless and live in `safety/blocklist.py::check_command_safety`, so `raw` and
the REPL get them too:

- **`0x35`** is allowed to a physical id when DFI is `00` and the request is well-formed for its
  ALFID (UDS) or the fixed form (KWP2000). Any other DFI stays blocked.
- **`0x36`** is allowed only with no data: `36 BSC` on UDS, `36` or `36 BSC` on KWP2000. A
  TransferData that carries data is a download and stays blocked.
- **`0x37`** is allowed only bare. A `37` with a parameter record (a download's checksum) stays
  blocked.
- **`0x34`** stays blocked. canair never starts a download, so a bare `36`/`37` can only end an
  upload or draw NRC `24`.
- **`27 5F`/`27 60`** (ISO 26021-2) are hard-blocked: `--unsafe` cannot lift them.
- **Functional ids:** none of these is in `FUNCTIONAL_READS`, so a broadcast stays refused.

`safety/actuation.py` classifies the three allowed shapes as `read`. `uds/services.py` drops
`actuates=True` from `0x35` and keeps it on `0x34`. The `0x36`/`0x37` rows stay `actuates=True`,
because the flag describes the service and the safety module judges the shape. `uds/describe.py`
describes a `75`/`76`/`77` reply as an upload, with no WARNING.

### Privacy: seeds, keys and transfer replies stay off disk

- **SecurityAccess.** `uds/security.py::is_security_exchange` recognises a `27` request or a `67`
  reply. `privacy/sensitive.py::withhold` drops every such row from every save path, the journal
  included. The verbose trace, `transport/stats.py` and the per-ECU response log mask every byte
  after the sub-function. The live layer prints "seed received (4 bytes)", never the seed.
- **TransferData replies are dumps.** `withhold` drops every `76` reply, whatever its size, as it
  drops an oversized `0x63` today. The bytes go to the dump store only.
- **Second line of defence.** `validate captures` errors on a stored `27`/`67`/`76` row, and
  `contribute` refuses a profile that has one. Phase 3 runs the check over every bundled profile
  first and reports what it finds as an incident, as the skills require.

### SecurityAccess: the key provider

The provider is user state. It lives in the user config, never in a profile:

```yaml
security_access:
  providers:
    - profile: hyundai-ioniq-ae-ev   # optional: any profile when absent
      ecu: BCM                       # required: a name or a TX id
      level: 0x01                    # optional: any level when absent
      command: [/path/to/program, --some-flag]
      timeout_s: 3                   # at most 3: see the S3 timer below
```

How canair runs it (`live/security.py`):

1. Find the one provider that matches the profile, the ECU and the level. No match, or two
   matches: refuse before any request. An entry must name an ECU, so a provider never receives a
   seed from an ECU the user did not name.
2. Send `27 L` and read the seed. An all-zero seed means already unlocked: stop.
3. Run the command as an argv list, with no shell. Write the seed as hex and a newline on stdin.
   Set `CANAIR_PROFILE`, `CANAIR_ECU`, `CANAIR_TX_ID`, `CANAIR_LEVEL` and `CANAIR_PROTOCOL` in
   its environment.
4. Read one line of hex from stdout as the key. A non-zero exit, a timeout, empty output or
   non-hex output means no key: canair does not send `27 L+1`. stderr goes to the user's terminal
   only when the provider fails, and is never logged.
5. Send `27 L+1 key`. A positive reply records the level as unlocked.

The rules that protect the ECU's lockout counter:

- **One key per ECU and level per run.** canair never retries a refused key, and after any
  refusal it never calls the provider for that ECU and level again in the process. The one
  exception is the upload driver's single re-unlock after an unlocked session was lost (step 6
  of the driver), because that key already worked once. The record of attempts lives in the
  provider runner, one per command run, not in a `SessionTable`: `live/hold.py::held_session`
  and `live/memory/reads.py::maybe_session` build a new `SessionManager`, and so a new table, on
  every call.
- **`35`, `36` or `37` stops the command** with the NRC and, for `37`, the advice to wait. The
  run does not try another level.
- **The confirmation names the unlock.** The prompt before the first request names the ECU, the
  level and the provider's program, and says that a wrong key counts toward the ECU's lockout.
  `--yes` skips it. Off a TTY the command refuses without `--yes`.

`config/security.py` reads and checks the block (`level` odd, not `0x5F`, `ecu` present,
`command` a non-empty list, `timeout_s` at most 3). `canair config example` and
`config.example.yaml` document it with a placeholder path. Nothing in the repository is a
working provider.

### `SessionManager`: the security level is part of the session

What exists today, and what this plan must work with:

- `SessionTable` tracks a session from the moment its request goes out. It records the mode only
  when the ECU accepted it (`opened(tx_id, mode, accepted=…)`), and a refusal keeps the previous
  mode. `stale(threshold)` lists the ECUs due a `3E 00`, minus those that refused TesterPresent.
- `SessionManager.held()` always sends the session request (`force=True`), yields the
  `SessionOutcome` even when the ECU refused (a warning says "continuing anyway"), and on exit
  stops the background keepalive and calls `table.forget()` unless `close=True`.
- "Idle" means not answered recently: `keepalive_stale` first counts every answer in
  `terminal.diag.answered` (`TransportStats.answered`) as a refresh (`_note_answers`). So a
  running upload, whose every `36` is answered, gets no `3E 00`, with no change.
- `link_error` lives on `SessionManager`, not in the table. The background loop sets it when the
  link fails, and the next `keepalive_stale` raises it.

The additions:

- **`SessionTable`** gains `unlocked(tx_id, level)` and `security_level(tx_id) -> int | None`.
  An accepted `opened()`, a `track()` with a mode (the SKM relay wake) and `forget()` clear the
  level. A refused `opened()` does not, because the ECU stayed in its session.
- **`SessionTable.pause_keepalive(tx_id)` and `.resume_keepalive(tx_id)`:** `stale()` leaves out
  a paused ECU. `mark_active` is not enough here: the background loop runs every 2 s with a 1.5 s
  idle threshold, so a provider that takes longer than 1.5 s would still see a `3E 00` reach its
  ECU.
- **`SessionManager.unlock(tx_id, level, key_for, *, report=True) -> UnlockOutcome`.**
  `UnlockOutcome` has the shape of `SessionOutcome` (`tx_id`, `level`, `ok`, `nrc`, `nrc_desc`,
  `error`, `problem()`), and `report` works the same way, through `render.messages`. `key_for` is
  an async callable that takes the seed and the context and returns a key or None. The transport
  layer never runs a program: the live layer passes `live/security.py`'s provider in.
- **The seed and the key are two transactions.** The provider may take seconds, and holding the
  transaction would stop the keepalives of every other ECU. So `27 L` and `27 L+1` are each one
  `terminal.transaction()`, and the ECU's keepalive is paused from the seed reply until the key
  reply. Some ECUs may answer a request between seed and key with NRC `24`. I have not seen this
  on a car.
- **The S3 timer while paused.** With no `3E 00`, the session ends when the provider takes longer
  than the ECU's S3 timer (nominally 5 s). So `timeout_s` is at most 3. When the provider fails,
  the keepalive resumes at once.
- **`held(…, security=None, key_for=None)`** opens the session and, when `security` is set,
  unlocks before it yields. With `security` set, a refused session or a failed unlock raises
  (`SessionRefused`, `UnlockFailed`), because a key in the wrong session wastes an attempt, and the
  caller must send nothing. Without `security`, `held` keeps today's "continuing anyway".
- **`live/hold.py::held_session`** takes `security` and `key_for` and passes them on. It is the
  one seam for the single-ECU commands. `live/memory/reads.py::maybe_session` calls
  `SessionManager(terminal).held` directly today, so it moves to `held_session`.
- **A lost session is a lost unlock.** When the background loop sets `link_error`, it also clears
  every level. When a request inside an unlocked session draws `7E`/`7F` or `33`, the caller
  clears the level. The upload driver decides whether to re-open (below). Nothing unlocks again
  behind the caller's back, because each unlock is one key attempt.

### Protocol layer (I/O-free)

- **`uds/transfer.py`:** the upload request for both protocols (from `uds/memory.py`'s
  `MemoryFormat`), the `75` reply parse (LFID and maxNumberOfBlockLength), the BSC sequence,
  the TransferData request and the reply check (SID, counter echo, at most
  `maxNumberOfBlockLength - 2` data bytes, the sum equal to the size asked), the exit request,
  and one classifier for the NRCs above.
- **`uds/security.py`:** level parity, the seed and key requests, the reply parse, the all-zero
  seed, `is_security_exchange`, the ISO 26021-2 levels and the NRC meanings. No transform of any
  kind. The level is an `int` like the session mode: `parse_security_level` and
  `format_security_level` go in `uds/subfunctions.py` beside `parse_session_type` and
  `format_session_type`.
- **`uds/echo.py`:** `request_echo` learns that a `36 BSC` reply repeats its counter, so the
  existing stale-reply path (`CAT_STALE`) handles a wrong counter with no new code in the
  transports. The length check stays in the driver, not in `reply_check`: that function is cached
  by request text, and `36 01` expects a different length in every upload.
- **`uds/layout.py`:** the `75`/`76`/`77` and `67` layouts for `bix --annotate`. The `67` seed
  bytes are labelled, not shown, in every annotation.

### The upload driver: `live/memory/upload.py`

For each range, in one `live/hold.py::held_session` (with `security` when asked):

1. Check the transport. When it cannot receive the ECU's recorded `max_block_length`, refuse
   (see "Is it faster?"). With no recorded length, the first `75` reply gives it. Then the check
   runs before the first `36`, and a failed check sends `37` and stops.
2. Send `35` for the range, capped at `--chunk` bytes per upload (default: the whole range).
3. Send `36` with counters `01`, `02`, … and append each block. A reply with the wrong counter is
   stale: resync and repeat the counter once. A second failure ends this upload.
4. Send `37`. An error here is reported, but the bytes already read are kept.
5. On a stop (skip key, Ctrl-C, an error, an NRC): send `37` once when a transfer is open. What
   was read becomes a stretch of the dump, and the rest becomes a gap with its NRC.
6. A lost session (`7E`/`7F`, or `link_error` from the background keepalive) re-opens it with a
   new `held_session` and, with `--security`, unlocks again. This happens at most once per dump,
   and the dump resumes at the first unread address. A second loss ends the dump.

It writes through `memory/store.py` like `dump.py`. The manifest gains `method: read|upload` and
`security_level` (the level number only). It never records a seed, a key or the provider.

### Profile model

`ecus/<ecu>.yaml`'s `memory:` section gains an optional `upload:` sub-section, written only by
the scan and by `canair memory set`:

```yaml
memory:
  format: "24"
  max_block: 254
  upload:
    session: "03"
    security_level: "01"       # the level only, never a seed or a key
    max_block_length: 4095     # as the ECU's 75 reply gave it
    answered: true
    software: "…"              # the software version at scan time
    scanned: 2026-11-06
```

The schema goes in `schema/pids_schema.yaml`. `validate pids` checks that the level is odd and
is not an ISO 26021-2 level. `memory.security_level` (same rule) records the level that unlocks
a `0x23` region that answered `33`.

### Commands

- `canair memory dump ECU RANGES --method read|upload [--security [LEVEL]] [--chunk N]`. The
  default stays `read` (open question 2). `--security` with no value uses the ECU's recorded
  level.
- `canair scan memory ECU --method upload` probes a 1-byte upload at the start of each readable
  region (then `37`), in the default session and then in `--mode`, and writes `memory.upload`.
  It stops on the same rules as the `0x23` scan. `--security LEVEL` unlocks first.
- `canair memory set ECU --upload-session 03 --upload-security 01 --security 01` edits the two
  sections by hand.
- Every one of these confirms before the first request, as `memory dump` does today.

### Code placement

| Code | Package |
|---|---|
| Upload, TransferData, exit: build, parse, reply check, NRCs | `uds/transfer.py` (new) |
| SecurityAccess requests, parse, levels, `is_security_exchange` | `uds/security.py` (new) |
| `75`/`76`/`77`/`67` layouts and descriptions | `uds/layout.py`, `uds/describe.py` |
| Shape rules for `35`/`36`/`37`, ISO 26021-2 block | `safety/blocklist.py` |
| Upload shapes as `read` | `safety/actuation.py` |
| Withhold `27`/`67`/`76` rows | `privacy/sensitive.py` |
| `security_access:` reader | `config/security.py` (new) |
| Security state, `unlock`, `held(security=)` | `transport/session_table.py`, `transport/session_manager.py` |
| `security`/`key_for` for the single-ECU commands | `live/hold.py`; `live/memory/reads.py::maybe_session` moves onto it |
| Key provider runner and its attempt record | `live/security.py` (new, beside `live/hold.py`) |
| Upload driver | `live/memory/upload.py` (new) |
| `memory.upload`, `memory.security_level` | `ecus/memory.py`, `yaml_edit/memory.py`, `schema/` |
| Manifest `method`, `security_level` | `memory/store.py` |
| Refuse stored `27`/`67`/`76` rows | `validation/`, `contribute/` |
| Flags | `cli/commands/memory/`, `cli/commands/scan/` |

No new top-level package. The `live/` charter names `security` beside `session`, `hold`,
`status` and `revision_probe`; regenerate `docs/development/architecture.md`.

## Phases

One commit per phase. Each passes the gate list in the **contributing-code** skill.

### Phase 0: feasibility on the Ioniq (no code, needs the car)

Today `0x35` is blocked, so each probe goes through `canair raw --unsafe` with a typed `YES`.
Save every request with `--save --label "phase0 0x35 …"`. The probes send no key.

1. **Who knows the service.** Send a bare `35` to BCM, HVAC, VCU, MCU and OBC (they answer
   `0x23`) and to IGPM and CCM (they do not), in the default and then the extended session.
   `13` means the ECU knows it, `11` that it does not, `7E`/`7F` that it needs another session,
   `33` that it is locked.
2. **One small upload.** On an ECU that knows it: a 1-byte upload of a known-readable address
   (BCM `35 00 24 FEDE0000 0001`), then `36 01`, then `37`. Record the `75` reply's block length.
3. **Repeat a counter.** On the same ECU: a 2-block upload where the second `36` repeats `01`.
   Record what the ECU answers.
4. **One full block.** Read one full block over `slcan-tcp` and over `wican-ws`, and time a
   `0x23` dump of the same 8 KiB for comparison.
5. **SecurityAccess presence.** On each ECU that answered `33` in step 1: a bare `27 01`, no key.
   The BCM is already known: a fresh 4-byte seed on every request.
6. **Side effects.** `dtc read` on every probed ECU, and check that the HVAC still answers
   `22 0100`.

**Gate.** When no Ioniq ECU accepts `0x35` outside a programming session, the upload half has no
reference car. Then the user decides whether Phases 6-7 are built and tested on fakes only, or
wait for a car that accepts them. Phases 1-5 are useful either way: they unlock `0x23` regions.

### Phase 1: protocol layer

`uds/transfer.py`, `uds/security.py`, the echo, layouts and descriptions. Read the standard text
for the KWP2000 forms, the repeated counter and the ISO 26021-2 levels, and correct this plan
where it is wrong. Unit tests only.

### Phase 2: safety

The blocklist shape rules, the `actuation` classification, the `services.py` flag. Tests pin
every allowed and refused shape, the hard block on `27 5F`, and the functional refusal.

### Phase 3: privacy and policy

`withhold` for `27`/`67`/`76`, the masked traces, the `validate captures` and `contribute`
refusals. Run the check over every bundled profile and report any hit to the user before the
commit. Amend the "Never share security keys" sections of the **contributing-code** and
**contributing-profiles** skills: canair may call a user-configured key provider, and ships no
provider; the forbidden list stays as it is.

### Phase 4: `SessionManager` security

The table state (level, paused keepalive), `unlock` and `UnlockOutcome`, `held(security=)` and
its two refusals, `held_session`'s new arguments, `maybe_session` onto `held_session`, and the
clear on a lost session and on `link_error`. Tests on `tests/support/fakes.py::FakeTerminal` with
`serialize=True`, which shows which header each request went to.

### Phase 5: the key provider and `--security` on `0x23`

`config/security.py`, `live/security.py`, the confirmation, `--security` on `memory dump` and
`scan memory` for the `0x23` method, `memory.security_level`.

### Phase 6: the upload driver and `memory dump --method upload`

`live/memory/upload.py`, the transport check, the manifest fields, `--chunk`.

### Phase 7: `scan memory --method upload` and `memory.upload`

The probe, the writer, the schema, `canair memory set` flags, validation.

### Phase 8: KWP2000 upload (only when Phase 0 finds a KWP2000 ECU that accepts it)

The KWP2000 request forms in the driver and the scan.

## Tests

- **Protocol:** every ALFID and LFID width, BSC wrap from `FF` to `00`, the reply checks (wrong
  counter, too many bytes, a short last block, a sum that does not match), the all-zero seed,
  level parity, NRC classification.
- **Safety:** `35` with DFI `00` passes and any other DFI is refused, `36` with data and `37`
  with a record are refused, `34` stays refused, `27 5F` is refused with `--unsafe`, and all
  of these are refused on a functional id.
- **Privacy:** a `27`/`67`/`76` row never reaches the journal or `captures/` on any save path, a
  trace line masks the seed, `validate captures` and `contribute` refuse a planted row.
- **Session:** an accepted `opened()`, a `track()` with a mode, `forget()` and `link_error`
  clear the level, and a refused `opened()` does not; no `3E 00` reaches the ECU between a seed
  and a key that take 2.5 s, while another ECU's keepalives continue; `held(security=…)` raises
  on a refused session before any `27`; the provider runner never calls the provider again after
  a refused key, and allows exactly one re-unlock after a lost unlocked session.
- **Provider:** an in-process fake returns a **fixed invented key** and never reads the seed's
  value. One subprocess test runs `sys.executable -c` that prints a fixed invented key, to cover
  argv, environment, timeout, non-zero exit and non-hex output. No test computes a key from a
  seed.
- **Upload driver:** a fake terminal that models a memory map and an upload state machine
  (`75` length, counter echo, NRCs, a stale reply once, a session lost once). It proves the dump
  matches the map, `37` is sent on every stop, holes stay holes, the resume happens once, and the
  transport check refuses before `35`. Run it through its handler on the fake terminal
  (`tests/dtc/test_dtc.py::TestDispatchTransportAgnostic` is the template).

## Docs

- `docs/concepts/memory-reads.md`: an upload section (when it is faster, why the block size is
  the ECU's choice, why programming sessions stay blocked) and a SecurityAccess section (the
  provider, the lockout rules, what never touches the disk).
- `config.example.yaml`: the `security_access:` block, with a placeholder path.
- `AGENTS.md`: the `canair memory` entry says `0x23` and RequestUpload.
- The **contributing-code** and **contributing-profiles** skills (Phase 3).
- `plans/2026-11-05-read-memory-by-address.md`: point its two superseded non-goals at this plan.
- `docs/reference/cli/` regenerated from `--help`; `docs/development/architecture.md`
  regenerated.

## Decisions (2026-11-06)

1. **The key comes from a user-configured external provider.** canair ships no algorithm and no
   provider.
2. **Programming sessions stay hard-blocked.** Upload runs only where the ECU accepts it in a
   session canair already allows.

## Open questions

1. **Unlocked `read` and `monitor`.** Should the query `session` step take `--security LEVEL`?
   It would let a DID that answers `33` be read and polled. It reverses more of the "no `security`
   pipeline step" rule than this plan does.
2. **The default method.** Should `memory dump` use upload when the ECU's `memory.upload` says
   it answered and the transport can take a full block (`--method auto`)?
3. **Upload regions.** An ECU may accept `0x35` over other ranges than `0x23`. Does the scan
   record separate upload regions, or only whether upload works at the start of each `0x23`
   region (as designed)?
4. **A lost TransferData reply.** Repeat the counter (as designed, if Phase 0 shows the ECU
   accepts it) or end the upload and start a new one at the first unread address?
5. **Provider matching.** Is a required `ecu:` on every provider entry too strict for a user with
   one provider for every ECU of a make?

## Needs the car, not an agent

- All of Phase 0.
- After Phase 6, on an ECU that accepts upload: an 8 KiB dump with `--method read` and with
  `--method upload` over `slcan-tcp`, compared with `memory diff` (expected: no difference) and
  timed.
- After Phase 5, only with a provider the user is entitled to use: an unlock of the BCM at level
  `01`, and a `0x23` read of a region that answered `33` before.
