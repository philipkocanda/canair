# Response frame counts

How canair learns, stores and uses the number of Controller Area Network (CAN) frames in the
response to a parameter identifier (PID). Read this page before you change the ELM327 request path,
the AutoPID request strings or the `response_frames:` field of a PID.

## Why the count exists

An ELM327 adapter reads the final nibble of an odd-length hex request as an **expected-response
frame count**, not as data. With the count, the adapter returns as soon as that many frames have
arrived. Without the count, the adapter waits until its `ATST` budget runs out, to be sure that no
further frame is coming. On a WiCAN Pro this is the main per-read cost. A read takes about 206 ms
without the count and about 53 ms with it. The variance also falls. The measurements are in
`plans/2026-08-09-wican-ws-throughput-ceiling.md`.

`transport.expected_responses` (default true) turns the feature off for the ELM327 transports.

## The count is learned, never guessed

The two possible errors differ in cost. An **over**count only costs time. An **under**count leaves
the tail of the response queued in the adapter. That tail then answers the **next** request. This is
the pipe desync that [link health](link-health.md) describes. The WiCAN firmware cannot recover from
it, because the firmware accumulates data in one static buffer and clears the buffer only after a
parse. Four rules therefore hold. A change to this code must keep them:

1. **Learn only from a plain request whose reply is `ok`.** A reply to a request that carried a
   digit holds exactly as many frames as the digit asked for. Learning from it would confirm its
   own premise.
2. **`learn()` opts out and never clamps.** Above `MAX_REQUESTABLE_FRAMES` (9) the PID gets no
   count. Clamping would be a deliberate undercount. The `21F2` request of the Ioniq battery
   management system (BMS) needs 18 frames, so it stays unoptimized.
3. **A mismatch realigns the pipe and retries plain, without charging the `retries` of the
   caller.** The mismatch categories in `_DIRTY_PIPE` (`drop`, `stale`, `decode`) realign the
   pipe, because a truncated reply is exactly the queued-tail case. The optimization can therefore
   cost latency, but it never costs a reading.
4. **The length of the plain retry decides what the mismatch meant.**

    - A retry of a **different** complete length proves that the response genuinely varies.
      canair retires the count (`opt_out`) and clears `response_frames:` from the profile.
    - A **same-length** retry, or a negative response code (NRC), means that the digit-bearing read
      lost a frame in transit. The resync already repaired that dirty pipe. canair drops only the
      digit of the session (`disable_digit`). The durable count stays. Deleting a verified count
      over one transient drop on a flaky link was a real bug.
    - A transiently **silent** electronic control unit (ECU) proves nothing. The retry brings
      neither `ok` nor an NRC. The optimization stays.
    - An **NRC counts as held.** It is a complete answer that occupies fewer frames. Otherwise
      every PID that an ECU refuses while a session is closed would opt out.

## A memory read is keyed by its size

canair learns the count per `CountKey`, which is the request header plus the request string. A
memory read (`0x23`) has a different string at every address, so it would never learn a count. Its
reply is `63` followed by exactly the bytes asked for. The size alone therefore fixes the length.

`uds/frame_counts.py::count_key` keys a memory read as the same read at address 0. One plain read
of a size teaches the count for every address. The key is still a real, even-length request, so
`requestable()` applies to it unchanged. Both terminals and the profile seed use `count_key`. A
61-byte memory read is the largest whose 62-byte reply fits 9 frames.

## Who owns what

| Piece | Module | Job |
|---|---|---|
| Wire rules | `transport/elm327/frame_count.py` | `requestable()` and `annotate_request()` are the only place for the wire rules: an even-length request, `1..MAX_REQUESTABLE_FRAMES`, never clamped. `FrameCountCache` holds the learned counts for one connection. `CountAttempt` and `CountVerdict` make the per-request decision. `Elm327Terminal.send_uds` keeps only the I/O. |
| Evidence | `uds/frame_counts.py` | `FrameCountLedger`, `FrameCountRecord`, `frames_for_payload` and `CountKey`. The module is transport-neutral and has no dependencies. |
| Profile bridge | `ecus/response_frames.py` | `stored_count()` reads the field, `seed_counts()` builds the seed, `resolve_edits()` diffs a ledger against the profile. |
| Write-back | `yaml_edit/response_frames.py` | `persist()` applies the edits through the `ecus/` editors. |
| Session seam | `live/session.py` | `run_session_guarded` calls `_persist_frame_counts` in a `finally`. |
| AutoPID | `interop/autopid/profile.py` | `request_with_count` appends a proven count to the request string that the device polls. |

## The ledger

The ledger takes two grades of evidence:

- `confirm()`: a digit was **held**. The request carried the count and came back whole. This
  directly tests the exact claim, so `CONFIRMATIONS_REQUIRED = 1`.
- `observe()`: canair merely **saw** a count. It needs `OBSERVATIONS_REQUIRED = 3` agreeing
  sightings.

**Any disagreement retires the key permanently.** Further agreement cannot rehabilitate it. If the
length varies, every single count is an undercount for some response.

The ELM327 transports produce `confirm()` evidence. The raw `slcan-tcp` path produces only
`observe()` evidence. It has no digit to hold, so it must derive the count itself.

`parse_uds_response` counts response lines, and the raw stack hands it one reassembled message.
`raw/terminal.py::send_uds` therefore computes `frames_for_payload(len(resp_bytes))`. Without that
call, every raw read would report `isotp_frame_count: 1`, even for a multi-frame reply. The
function counts classic CAN only, because CAN FD (flexible data-rate) carries up to 64 bytes per
frame.

## Persistence: the count belongs to the car

The learned cache is per connection. A count measures one link, and a reconnect may re-home the
session onto another device. The **fact** about the response belongs to the car. canair therefore
writes a confirmed count to `response_frames:` of the PID in `ecus/` and re-seeds it on the next
connect. A cold first read is then fast. It does not pay one plain read per PID to learn the count
again. The plan is `plans/2026-08-13-persisted-response-frame-counts.md`.

- **The write-back seam is `Terminal.frame_counts`**, so one implementation covers every
  transport. `run_session_guarded` persists in a `finally`, so an interrupted or failed session
  still saves what it proved. Ctrl-C is the normal end of a long `monitor`. `--no-learn-frames`
  turns the write-back off.
- **A failure there never propagates.** The count is a by-product of the command that the user
  ran. The write is deliberately **not** inside the `pids` validate-and-revert guard. That guard
  would discard a correct edit over unrelated, pre-existing breakage in the same file.
- **`stored_count()` is the only reader of the field.** It must exclude `bool`, because `bool` is a
  subclass of `int`. A stray `response_frames: true` would otherwise read as a 1-frame count.
- **`seed_counts` does not apply `MAX_REQUESTABLE_FRAMES`.** The ceiling belongs to the layer that
  must emit the nibble. A reader never silently rewrites a profile fact.
- **`resolve_edits` refuses a TX header that several ECUs share.** Under `normal_extended_11bit`, a
  target-address byte tells those ECUs apart, and a `CountKey` does not carry that byte. canair
  reports the refusal and does not guess.
- **`variable_length: true` excludes a PID both ways.**

    - `stored_count` and `seed_counts` yield nothing for the PID.
    - `resolve_edits` never writes or clears a count on the PID.
    - `variable_length_keys()` hands its keys to `FrameCountCache.seed_variable_length`. That call
      opts the keys out of the digit for the whole session, and the opt-out survives `reset()`.
      Without it, the cache learned a count in the session and paid a retry on the next read.
    - A conflict seen on the wire never sets the flag. It only **clears** a stored count, because a
      clone that mangles the digit looks identical.

- **A revision routes its own count.** `response_frames.py::revision_target` writes to the base PID
  for the reference revision. It writes to `per_revision.<r>` for another revision. It writes
  nowhere when the revision is undetermined. One revision's count can then never seed a read of
  another revision and undercount it. See [variants and revisions](variants-and-revisions.md).

## AutoPID

`canair wican autopid write` appends the persisted count of each PID to the request string by
default. The option `--no-expected-responses` on `write`, `upload` and `diff` turns this off. The
ELM327 co-processor of the device then answers as soon as that many frames arrive. Without the
count, it waits out its `ATST96` budget of about 614 ms.

This is safe by default only because the emit path goes through `requestable()`. That function
skips:

- anything unproven
- anything `variable_length`
- anything over the 9-frame ceiling
- any request of odd length

`autopid stats` shows a `Frames` column, so you can see which PIDs have earned a count. The
transport and the AutoPID writer share one rule set, so they cannot disagree.
