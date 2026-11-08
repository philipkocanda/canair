# Response frame counts

How canair learns, stores and uses the number of CAN frames in a PID's response. Read this page
before you change the ELM327 request path, the AutoPID request strings, or the `response_frames:`
field of a PID.

## Why the count exists

An ELM327 request whose hex length is odd has its final nibble read as an **expected-response
frame count**, not as data. With the count the adapter returns as soon as that many frames have
arrived. Without it the adapter waits out its `ATST` budget to be sure that no further frame is
coming. On a WiCAN Pro this is the dominant per-read cost: about 206 ms without the count and
about 53 ms with it, and the variance falls too (measurements in
`plans/2026-08-09-wican-ws-throughput-ceiling.md`).

`transport.expected_responses` (default true) is the kill switch for the ELM327 transports.

## The count is learned, never guessed

The two possible errors are not equal. An **over**count only costs time. An **under**count
leaves the tail of the response queued in the adapter, and that tail then answers the **next**
request. That is the pipe desync that [link health](link-health.md) describes. The WiCAN firmware
cannot recover from it, because it accumulates into one static buffer that it clears only after a
parse. So four rules hold, and a change to this code must keep them:

1. **Learn only from a plain request whose reply is `ok`.** A reply to a request that carried a
   digit holds exactly as many frames as the digit asked for. Learning from it would confirm its
   own premise.
2. **`learn()` opts out; it never clamps.** Above `MAX_REQUESTABLE_FRAMES` (9) the PID gets no
   count. Clamping would be a deliberate undercount. The Ioniq's `0x7EA:21F2` needs 13 frames, so
   it stays unoptimized.
3. **A mismatch realigns the pipe and retries plain, without charging the caller's `retries`.**
   The mismatch categories in `_DIRTY_PIPE` (`drop`, `stale`, `decode`) realign the pipe,
   because a truncated reply is exactly the queued-tail case. The optimization can therefore cost
   latency, but never a reading.
4. **The length of the plain retry decides what the mismatch meant.**
    - A retry of a **different** complete length proves that the response genuinely varies. The
      count is retired (`opt_out`), and `response_frames:` is cleared from the profile.
    - A **same-length** retry, or an NRC, means that the digit-bearing read lost a frame in
      transit. The resync already repaired that dirty pipe. Only the session's digit is dropped
      (`disable_digit`), and the durable count stays. Deleting a verified count over one
      transient drop on a flaky link was a real bug.
    - A transiently **silent** ECU (the retry brings neither `ok` nor an NRC) proves nothing, and
      the optimization stays.
    - An **NRC counts as held**: it is a complete answer that occupies fewer frames. Otherwise
      every PID that an ECU refuses while a session is closed would opt out.

## A memory read is keyed by its size

The count is learned per `CountKey`, the request header and the request string. A memory read
(`0x23`) has a different string at every address, so it would never learn a count. Its reply is
`63` and exactly the bytes asked for, so the size alone fixes the length.
`uds/frame_counts.py::count_key` therefore keys a memory read as the same read at address 0.
One plain read of a size teaches the count for every address, and the key is still a real,
even-length request, so `requestable()` applies to it unchanged. Both terminals and the profile
seed use `count_key`. A 61-byte memory read is the largest whose 62-byte reply fits 9 frames.

## Who owns what

| Piece | Module | Job |
|---|---|---|
| Wire rules | `transport/elm327/frame_count.py` | `requestable()` and `annotate_request()` are the single home of the wire rules: an even-length request, `1..MAX_REQUESTABLE_FRAMES`, never clamped. `FrameCountCache` holds the learned counts for one connection. `CountAttempt` and `CountVerdict` make the per-request decision. `Elm327Terminal.send_uds` keeps only the I/O. |
| Evidence | `uds/frame_counts.py` | `FrameCountLedger`, `FrameCountRecord`, `frames_for_payload` and `CountKey`. Transport-neutral and dependency-free. |
| Profile bridge | `ecus/response_frames.py` | `stored_count()` reads the field, `seed_counts()` builds the seed, `resolve_edits()` diffs a ledger against the profile. |
| Write-back | `yaml_edit/response_frames.py` | `persist()` applies the edits through the `ecus/` editors. |
| Session seam | `live/session.py` | `run_session_guarded` calls `_persist_frame_counts` in a `finally`. |
| AutoPID | `interop/autopid/profile.py` | `request_with_count` appends a proven count to the request string that the device polls. |

## The ledger

The ledger takes two grades of evidence:

- `confirm()`: a digit was **held**. The request carried the count and came back whole. This is
  a direct test of the exact claim, so `CONFIRMATIONS_REQUIRED = 1`.
- `observe()`: a count was merely **seen**. It needs `OBSERVATIONS_REQUIRED = 3` agreeing
  sightings.

**Any disagreement retires the key permanently.** Further agreement cannot rehabilitate it. If
the length varies, every single count is an undercount for some response.

The ELM327 transports produce `confirm()` evidence. The raw `slcan-tcp` path produces only
`observe()` evidence. It has no digit to hold, and it must derive the count itself.
`parse_uds_response` counts response lines, and the raw stack hands it one reassembled message.
Every raw read therefore reported `isotp_frame_count: 1` until `raw/terminal.py::send_uds` began
to compute `frames_for_payload(len(resp_bytes))`. That function counts classic CAN only, because
CAN-FD carries up to 64 bytes per frame.

## Persistence: the count belongs to the car

The learned cache is per connection. A count measures one link, and a reconnect may re-home the
session onto another device. The **fact** about the response belongs to the car, so a confirmed
count is written to the PID's `response_frames:` in `ecus/` and re-seeded on the next connect.
A cold first read is then fast, and it does not pay one plain read per PID to learn the count
again. The plan is `plans/2026-08-13-persisted-response-frame-counts.md`.

- **The write-back seam is `Terminal.frame_counts`**, so one implementation covers every
  transport. `run_session_guarded` persists in a `finally`, so an interrupted or failed session
  still banks what it proved. Ctrl-C is the normal way that a long `monitor` ends.
  `--no-learn-frames` opts out.
- **A failure there never propagates.** The count is a by-product of the command the user ran.
  The write is deliberately **not** inside the `pids` validate-and-revert guard, because that
  guard would discard a correct edit over unrelated, pre-existing breakage in the same file.
- **`stored_count()` is the single reader of the field.** Its `bool` exclusion is load-bearing:
  `bool` is an `int` subclass, so a stray `response_frames: true` would read as a 1-frame count.
- **`seed_counts` does not apply `MAX_REQUESTABLE_FRAMES`.** The ceiling belongs to the layer
  that must emit the nibble. A profile fact is never silently rewritten by whoever reads it.
- **`resolve_edits` refuses a TX header that several ECUs share.** Under
  `normal_extended_11bit` they are told apart by a target-address byte that a `CountKey` does not
  carry. The refusal is reported, not guessed.
- **`variable_length: true` excludes a PID both ways.** `stored_count` and `seed_counts` yield
  nothing for it, and `resolve_edits` never writes or clears a count on it. `variable_length_keys()`
  also hands its keys to `FrameCountCache.seed_variable_length`, which opts them out of the digit
  for the whole session (the opt-out survives `reset()`). Without that, the cache learned a count
  in-session and paid a retry on the next read. A conflict seen on the wire never sets the flag.
  It only **clears** a stored count, because a clone that mangles the digit looks identical.
- **A revision routes its own count.** `response_frames.py::revision_target` writes to the base
  PID for the reference revision, to `per_revision.<r>` for another one, and nowhere when the
  revision is undetermined. One revision's count can then never seed, and undercount, a read of
  another. See [variants and revisions](variants-and-revisions.md).

## AutoPID

`canair wican autopid write` appends each PID's persisted count to the request string by
default (`--no-expected-responses` on `write`, `upload` and `diff` opts out). The device's
ELM327 co-processor then answers as soon as that many frames arrive, instead of waiting out its
`ATST96` budget of about 614 ms. This is safe by default only because the emit path goes through
`requestable()`. It skips anything unproven, anything `variable_length`, anything over the 9-frame
ceiling and any request of odd length. `autopid stats` shows a `Frames` column, so you can see
which PIDs have earned a count. The transport and the AutoPID writer share one rule set, so they
cannot disagree.
