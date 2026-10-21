# Cross-ECU reply misfile: one ECU's answer saved under another ECU

Status: **OPEN** (reported 2026-10-21). Detection after the fact is implemented (fix 3); the
transport fix is not. Not reproduced; the root cause is a hypothesis.

A `monitor --save` session stored the VCU's `21F2` reply as an MCU `21F2` capture. The echo check
cannot catch this, because both ECUs answer the same request with the same `61 F2` header. Any
PID that two ECUs both support is exposed. On the Ioniq that is `21F2` (BMS, MCU and VCU) and
`2101`/`2102` (most of the KWP2000 powertrain ECUs).

## The report

- Profile `ioniq-2017`, `captures/2026-08-08.json`. The session ran 10:02:08 to 10:07:16 over
  `wican-ws` on canair v1.16.0, polling `IGPM BMS VCU MCU EPS ESC CLU`, with `keep:changes`.
- Transport quality was poor: 2065 drops and 633 errors in 3326 exchanges.
- The MCU `21F2` capture at 10:07:16.803 was 86 bytes, ending in the ASCII tag `6000`. Every
  genuine MCU `21F2` reply is 67 bytes and ends `EHEL-MS2`. The payload matched the VCU's
  10:02:12 `21F2` reply in its odometer and its `B75` counter.
- That capture was the last one in the session, 80 s after the one before it.
- It was found during analysis, as a second value of the MCU's constant `21F2` tag. It was
  deleted on 2026-10-21 with `captures uds --delete`.

## Likely cause

VCU `21F2` is polled shortly before MCU `21F2` (10:02:12.4 and 10:02:13.9 in this session). Its
reply is 13 frames, more than
`MAX_REQUESTABLE_FRAMES`, so the adapter waits out its full `ATST` budget. If that read timed
out, a complete reply could still have been queued in the adapter or the WebSocket channel when
canair re-addressed to the MCU.

The next request got that queued reply. `request_echo` checks the SID and the identifier, and
both matched, so the payload was accepted and saved under the expected `rx` (`0x7EB`), not the
address that actually sent it (`0x7EA`). The receive filter cannot reject it, because the bytes
were already past the adapter.

Under `keep:changes` the VCU did not store that reply for itself, since it had not changed. The
MCU did, because it differed from the MCU's previous value.

Not established: whether the resync work in `plans/2026-08-08-elm327-pipe-desync-recovery.md`
already prevents it. Probably not, because that resync only triggers on a *stale* category (an
echo or SID mismatch), which this reply never produced.

## Possible fixes

1. **Check the responding CAN id on the ELM327 transports.** With headers on (`ATH1`), every
   response line carries its arbitration id, so a reply from the wrong ECU can be rejected as
   stale and resynced like an echo mismatch. This costs some throughput and touches the response
   parser.
2. **Drain the pipe when switching to a different ECU** after a timed-out or dropped read. This
   is cheaper but only narrows the window; it cannot prove where a reply came from.
3. **Catch it after the fact — IMPLEMENTED 2026-10-21.** `validate captures` warns when a
   payload breaks its own ECU's established length for the PID but matches another ECU's, or is
   byte-identical to another ECU's reply to the same PID that has it at least as often. Detection
   is `canlib/misfiled.py`, the cross-file pass `canlib/commands/validate/misfiled.py`, tests
   `tests/test_misfiled.py`. A length is "established" only at 5+ samples with a 90 % share,
   identical replies must be 8+ bytes with 4+ distinct values, and negative responses are left
   out. It finds misfiles but does not prevent them.

The raw `slcan-tcp` path filters by arbitration id in software, so it should not be affected.

## Verification wanted

- An offline replay test: a queued complete `61 F2` reply from `0x7EA` is delivered to a request
  addressed to `0x7E3`. It must be rejected, not returned as the MCU's answer.
- ~~A sweep of existing captures for the same pattern.~~ Done 2026-10-21 by the new check: it
  flags the deleted capture when run on the pre-deletion file, and nothing in any bundled or
  fixture profile today.
