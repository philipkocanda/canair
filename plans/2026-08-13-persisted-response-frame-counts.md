# Persisted per-PID response frame counts

Status: **IN PROGRESS** (2026-08-13).

Turns the ELM327 expected-response-count digit from a per-connection optimization that is
re-learned from scratch every session into a **durable profile fact**, so that:

- `read`/`monitor` are fast from the *first* poll cycle instead of paying one plain
  ~614 ms read per PID to re-learn what the last session already knew, and
- the generated WiCAN AutoPID profile can carry the digit, eliding the device's own
  `ATST96` wait on every unattended poll.

The optimization itself already exists — `canlib/transport/elm327_frame_count.py`, from
`plans/2026-08-09-wican-ws-throughput-ceiling.md`. This plan adds persistence, and only
persistence. It does not change how a count is learned or how a bad one is repaired.

## Why ~614 ms

An ELM327 waits out its `ATST` budget on every request, because it cannot know whether
another response frame is coming. Both ends of canair's world set the same budget:

- canair's own ELM path: `canlib/transport/elm327_terminal.py:134`, `ATST96` —
  150 × 4.096 ms ≈ 614 ms.
- the WiCAN firmware's AutoPID engine: `autopid.c:1518`,
  `"ati\rate0\rath1\ratl0\rats1\ratsp6\ratst96\r"`.

Supplying the expected-response-count nibble lets the adapter return the instant that many
frames have arrived. Measured on a WiCAN Pro: ~206 ms → ~53 ms per read, with the variance
collapsing too.

## The two things that make this non-trivial

**1. The firmware has no desync repair.** This is the safety crux, and it is worse on the
device than in canair.

`parse_elm327_response` (`autopid.c:1210`) counts frames but never compares the count to an
expectation — `frame_count` is used only at `autopid.c:1351` as a multi-responder heuristic.
`autopid_parser` (`autopid.c:1384`) appends every chunk to a **single static buffer**
(`auto_pid_buf`, `autopid.c:63`) and clears it only *after* a successful parse
(`autopid.c:1419`) — never *before* a request. So an undercount's queued tail prefixes the
**next** PID's parse, silently and permanently, on an unattended device with nobody
watching. canair has `Elm327Terminal._resync()`; the firmware has nothing.

Hence: the AutoPID nibble is **opt-in** (`--expected-responses`), and every guard below is
mandatory rather than best-effort.

The good news is that the nibble does reach the wire. The custom-PID command is built at
`autopid.c:1956-1961` with `malloc(strlen + 2)` + `strcpy` + `strcat("\r")` — verbatim, no
parity, length, or hex validation — and sent verbatim at `autopid.c:1666`. The firmware
never emits a digit itself (the standard path does `sprintf(cmd, "01%s\r", pid_hex)` at
`autopid.c:2101-2108`; the `// "01XX1\r\0" needs 8 bytes` comment at `autopid.c:2105` is
vestigial dead intent). **The profile's `pid` string is the only route.**

**2. Capture history is not sound evidence.** The obvious design — derive each count from
the stored payload length across `captures/` — was measured and rejected.

Over `profiles/ioniq-2017/captures/*.json`: 115,116 non-NRC captures, 114 distinct
`(rx, pid)` pairs. 94 yield a single frame count, 59 of those across ≥2 sessions — but
**20 self-contradict**:

| `(rx, pid)` | derived frame counts (payload lengths) |
|---|---|
| `0x778:22BC01` | 2 (9, 13), 4 (25), 8 (55) |
| `0x7A8:22C00B` | 2 (12), 4 (23, 27) |
| `0x7BB:220100` | 5 (34), 6 (38, 41), 7 (42) |
| `0x7D9:22C101` | 7 (42, 48), 10 (67) |

Multi-DID batching mutates the stored slice, historical truncated reads predate the
write-path length check, and some responses are genuinely variable. `elapsed_ms` is
documented as recorded only for single-DID reads and would discriminate — but it exists on
**4 of 115,116 captures**, so it is not a usable filter.

**Evidence comes from the wire, not from history.** Which is convenient, because the wire
already produces it and already validates it.

## Stage 1 — the fact

A new PID-level scalar in `ecus/`:

```yaml
2101:
  status: active
  response_frames: 4
```

Named for the transport-neutral measurement (how many CAN frames the positive response
occupies), not for the ELM327 optimization that consumes it — the raw path observes the
same fact without any digit involved.

- `canlib/schema/pids_schema.yaml` — add to `optional_pid_fields` (`:326-349`).
- `canlib/commands/validate/pids.py` — type check beside `period`'s (`:572-574`): integer
  ≥ 1. **Error when `response_frames` and `variable_length: true` are both set** — a
  response with no fixed length has no count, and shipping one would be a deliberate
  undercount.
- `canlib/pids_edit/params.py::set_response_frames` — modelled on `set_pid_variable_length`
  (`:624-667`).
- `canlib/commands/pids.py` — `set-response-frames ECU PID N|clear`, registered like
  `set-pid-variable-length` (`:823-836`).

**Store the true count even when it exceeds 9.** The 1..9 wire cap is consumer policy that
already has exactly one home (`MAX_REQUESTABLE_FRAMES`,
`canlib/transport/elm327_frame_count.py:39`); the YAML records a measurement. The Ioniq's
13-frame `0x7EA:21F2` stays honest in the profile and simply unoptimized.

## Stage 2 — a transport-neutral ledger, and what "verified" means

Verification is a **positive proof on the wire**, and it is nearly free because the
existing machinery already computes it.

New leaf module `canlib/frame_counts.py` (numpy-free, no transport imports, mirroring
`canlib/counters.py`):

- `FrameCountLedger` — `observe(key, frames)`, `confirm(key, frames)`, plus read-only
  `confirmed()` and `retired()`.
- A **conflict** — an observation disagreeing with the recorded count — is permanently
  disqualifying and moves the key to `retired()`. This is what makes a wrong stored value
  self-healing rather than sticky.
- Confirmed means no conflict **and** either:
  - `confirmations ≥ 1` (ELM327 path): a request that *carried* the digit came back whole,
    i.e. the adapter honored the count. Nothing stronger is available.
  - `observations ≥ 3` (raw path): there is no digit to confirm with, so the bar is
    repetition with zero disagreements.

`FrameCountCache` (`canlib/transport/elm327_frame_count.py`) *owns* a ledger and keeps the
digit policy where it already lives — `annotate`, `attempt`, `learn`, `opt_out`,
`MAX_REQUESTABLE_FRAMES`. `CountAttempt.verdict` calls `confirm()` when `digit_held` is
true and marks a conflict on opt-out.

**Raw-path parity.** `slcan-tcp` is the default transport and never reports
`isotp_frame_count` today — the field is set only in the ELM text parser
(`canlib/uds_parse.py:498`). So without this, `slcan-tcp` users could never populate
`response_frames`, even though the generated AutoPID profile is precisely what they want it
for. `canlib/transport/uds_raw.py` already knows the reassembled payload, so it can report
the count directly; `frames(L) = 1 if L ≤ 7 else 1 + ceil((L − 6) / 7)`, validated against
the known 13-frame `0x7EA:21F2` (90-byte payload). `RawTerminal` owns a bare ledger —
observations only, no digit, since client-side ISO-TP has no adapter to hint.

`frame_counts: FrameCountLedger` joins the `Terminal` protocol
(`canlib/transport/protocol.py`) so Stage 4 never branches on transport type.

## Stage 3 — seed the cache from the profile at connect

Build `{(tx_id, request_hex): frames}` from the active profile and pass it into the terminal
constructor beside the existing `expected_responses` read at
`canlib/commands/_live/connect.py:86-107`. Skip PIDs flagged `variable_length` and counts
above `MAX_REQUESTABLE_FRAMES`.

This is the user-facing runtime win: every read is fast from the first poll cycle.

It is safe by construction. A stale seed takes the existing retry-plain → realign → opt-out
path — costing latency but never a reading — and lands in `retired()` so Stage 4 clears the
bad value. The verbose `[count]` note distinguishes *seeded* from *learned* so a wrong
profile value is visible rather than merely slow.

`FrameCountCache.reset()` on reconnect must **re-seed** rather than forget: the profile fact
outlives the link, unlike a value learned on it.

## Stage 4 — write-back on confirmation

New `canlib/commands/_live/frame_counts.py`, invoked at session teardown:

1. Read `terminal.frame_counts.confirmed()` and `.retired()`.
2. Map `CountKey` → (ECU, PID) through the ECU registry. **Guard:** under
   `normal_extended_11bit` (BMW `0x6F1`, PSA) several ECUs share one TX header, so a key can
   be ambiguous — skip and report rather than write to the wrong ECU.
3. Write via `pids_edit.set_response_frames`, echoed through
   `canlib/commands/_edit_echo.py::echo_edit` (which supplies the full path and the
   installed-snapshot warning for free). Gated by `require_writable_definitions()`.
4. **Clear** the field for `retired()` keys, and report corrections loudly. A stale count is
   the dangerous case.

Auto-write matches established precedent: `scan` writes `routines:`/`iocontrol_discoveries:`,
`discover --register` writes ECUs, `identity` writes `identity:`. A live command that
confirms a durable ECU fact writes it. Opt out with `--no-learn-frames`.

## Stage 5 — the generated WiCAN profile (opt-in)

`canlib/autopid_profile.py:101-109` — annotate the `"pid"` value (`:104`) when a usable
count exists, behind `--expected-responses` on `write`, `upload` and `diff`,
**off by default** pending field-testing, for the firmware reason above. `diff` needs the
flag too, or it reports every optimized PID as a difference against the device.

The three generating actions share one `_add_generate_args` helper, so the flag cannot be
registered on some of them and forgotten on others.

Guards, all mandatory:

- a `response_frames` value is present,
- `1 ≤ N ≤ MAX_REQUESTABLE_FRAMES`,
- the PID is not `variable_length`,
- **the request string is even-length.**

All four live in one predicate, `elm327_frame_count.requestable()`, read by both the live
transport and the generator so the two cannot drift apart about what is safe to ask for.
The field itself has a single reader, `response_frames.stored_count()`, which is also where
`bool` is excluded — `bool` is an `int` subclass, so `response_frames: true` would otherwise
read as a 1-frame count and truncate every multi-frame response.

That last guard also closes a latent bug at `canlib/autopid_profile.py:104`: `str()` of a
YAML mapping key loses a leading zero when the key parsed as an int (`0100` → `"100"`), and
appending a nibble to that would silently request a *different* PID. No profile trips it
today (38 int keys, 73 str keys across both bundled profiles, none odd-length), so it stays
a guard rather than a fix.

`autopid stats` reports how many PIDs ship optimized, and why the rest do not.

## Out of scope

- **`make_pid_init`'s session request** (`canlib/autopid_profile.py:32-45`) could ship
  `1003` as `10031` — one frame, another ~614 ms per cycle on every session ECU. Deferred:
  it is a different code path with a different failure mode (a bad session open, not a bad
  reading).
- **Deriving counts from capture history** — measured and rejected above.
- **A provenance sub-mapping under the field.** A scalar plus self-healing correction gives
  auditability without complicating the surgical editor or the generator.
- **Writing the count from the transport layer.** The transport produces evidence; the
  command layer owns profile writes.
