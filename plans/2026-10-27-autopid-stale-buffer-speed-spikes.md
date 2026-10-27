# AutoPID stale-buffer speed spikes in Home Assistant

Status: **OPEN** (2026-10-27). Mechanism identified from the firmware source; the trigger that
shortens the replies is not yet observed on a live device. WiCAN v6 (`6.0.0-beta.3` and later)
no longer has the stale-byte path, but canair cannot target v6 yet, so the v4 device stays
affected.

Home Assistant's `sensor.ioniq_real_speed` (ESC `22C101` `REAL_SPEED_KMH`, expression `B12`)
shows isolated spikes of 130-250 km/h while the car is doing about 100. The stored canair
captures for the same weekend are correct. This plan records the evidence, the suspected
mechanism, what is still unknown and the options for containing it.

## Symptom

Home Assistant recorder (Postgres on the NUC), 22-26 Oct 2026:

| Day    | `real_speed` samples | above 130 km/h | max |
|--------|---------------------:|---------------:|----:|
| 10-10  |                  167 |             29 | 249 |
| 10-23  |                  118 |             31 | 250 |
| 10-24  |                  136 |             26 | 249 |

Over the same weekend `eps_vehicle_speed`, `vcu_vehicle_speed` and all four wheel speeds peak
at about 109 km/h. The spikes are single samples: the neighbouring samples and the other speed
sensors in the same snapshot read normally.

The wheel signals show the same fault in a form that is only recognisable when the stale value
happens to be 16: `wheel_speed_fl` reads 16 while the other wheels read about 100 (for
example 24 Oct 18:34:50 - `real_speed` 217, FL 16, FR 16, RL 34, RR 35). Counts of FL = 16:
10-18 10, 10-23 12, 10-24 13. Wheel speeds are `B18`-`B21`; a stale `B18`/`B19` of `0x10 0x10`
matches the bytes at those positions in an EPS `220101` reply
(`62010194981A0250FFE9FA2B01AC10101701`).

## What is not the cause

- **The definition.** `ESC:22C101:REAL_SPEED_KMH` is `B12` at 1 km/h per bit, verified against
  the four wheel speeds (r = +1.000) and against EPS and VCU speed.
- **The stored captures.** Decoded `REAL_SPEED_KMH` over all of `profiles/ioniq-2017/captures/`
  peaks at 118 km/h. On the 25 Oct drive it agrees with `EPS_VEHICLE_SPEED` and
  `VCU_VEHICLE_SPEED` sample by sample (for example 91 / 91 / 94 at 12:28:25). canair rejects a
  truncated multi-frame read at capture time, so a short reply is never stored.
- **The docs explorer export.** `canlib/explorer_series.py` over the weekend sessions also tops
  out at 91 km/h.
- **The default-on expected-response count** (`5dc0ebe`, 23 Oct). The spikes are already present
  on 10 Oct (29 of 167 samples), before `22C1017` was generated into `out/autopid.json`
  (`6c33c73`, 20 Oct). The count is correct for this PID in any case: a 42-byte reply is a
  First Frame plus six Consecutive Frames, 7 frames.
- **The ESC/EPB shared DID.** EPB (`0x7D5`) also answers `22C101`, with a 27-byte reply that has
  `0xFF` at the speed position, but those rows are recorded under the EPB response address
  (`0x7DD`) and are not decoded as ESC.

## Suspected mechanism

Read from the WiCAN firmware checkout in `wican-fw/` (tag `v4.52p`):

1. `autopid_parser` (`components/autopid/autopid.c:2746`) keeps a single `static response_t`
   that is reused for every reply. `parse_elm327_response` (`autopid.c:2114`) writes the
   parsed bytes into `response->data` from index 0 and sets `response->length = k`
   (`autopid.c:2298`). It never clears the bytes after `k`.
2. The `B<n>` operator in `main/expression_parser.c:175` is `data[index]` with no comparison
   against the reply length.
3. The device polls per parameter (loop at `autopid.c:3897`; each parameter has its own timer
   and its own request), and each reply is evaluated against only that parameter's expression
   (`autopid.c:3996`). Parameters of one PID therefore fail independently, which matches the
   independent spikes above.
4. The only guard is `pid_validation_en` (`autopid.c:2600`, applied at `autopid.c:2787`). It
   searches the reply for `62 C1 01` anywhere. A reply that has the First Frame but is missing
   later frames still passes.

So a `22C101` reply that arrives with fewer than 13 bytes returns the previous reply's byte 12
as `REAL_SPEED_KMH`; one with fewer than 19 bytes returns its byte 18 as `WHEEL_SPEED_FL`.
Values of 200-250 are then whatever the preceding reply held at that position.

This is consistent with the Home Assistant data but has not been seen directly. The firmware
version running on the device is also unverified: the source read is `v4.52p`. `v4.52p` is the
head of the `wican-pro` branch, so no later 4.x release changes this path.

## Does WiCAN v6 fix it?

Largely yes, for this mechanism. Read from `meatpi-components` at `dc649d7` (2026-10-27), the
tree `wican-fw` `v6.0.0p-beta.5` builds; the release tag does not pin a components commit, so
this is the best available match, not a proven one. Nothing was run on a device.

- **No stale bytes.** The evaluator is bounds-checked: `expression_parser_eval` takes a payload
  length and a `B<n>` at or past it fails with `ESP_ERR_INVALID_SIZE`
  (`expression_parser/expression_parser.c:111`; the file header lists "references past the
  payload" as a hard error). `autopid_publish.c:80` skips that parameter when the evaluation
  fails, so no value is published. Each request also gets a fresh length: the runner hands
  `ap_runner_publish` the byte count of the reply it just parsed (`autopid_runner.c:507`).
- **Response Pending is dropped.** The line tokenizer discards `7F xx 78` lines
  (`autopid_resp.c:61-65`), so it can no longer be counted into the bytes, one of the candidate
  triggers in the open questions below. The 4.x code counts it (`autopid.c:2114`).
- **Wrong-PID replies are rejected.** `ap_payload_matches_cmd` (`autopid_runner.c:488`) compares
  the reply's service and DID echo with the request and drops a mismatch.
- **Byte indexing matches canair's profiles only from beta.3.** `506e267` (2026-10-24, issue
  #1014) makes expressions index the frames as the chip prints them, PCI bytes included, like
  4.x. `6.0.0-beta.1` and `beta.2` stripped the PCI bytes and shifted every `B<n>` beyond the
  first frame, so `B12` would have read a different byte on those.

What v6 does not guarantee:

- **A reply with a missing middle frame is still accepted.** `ap_resp_frames` concatenates the
  lowest responder's lines in arrival order (`autopid_resp.c:455-470`) with no check of the
  consecutive-frame sequence numbers or of the First Frame's declared length. If a frame drops
  but the reply is still long enough, later bytes shift and `B12` reads the wrong byte
  without an error. This only bites if open question 1 turns out to be lost Consecutive Frames
  rather than a short reply.
- **The range filter is no defence.** A custom row only drops readings outside its `min`/`max`
  (`autopid_bounds.c`); the profile's `max: 250` for `REAL_SPEED_KMH` would pass every observed
  spike.
- **canair cannot use v6 yet.** `wican-ws` and the 4.x AutoPID upload are gone and the AutoPID
  config format changed; `canair wican autopid upload|download|diff` refuse on v6 until Phase 2
  of `plans/2026-10-20-wican-fw-v6-compatibility.md`. Note that plan's §6 says the v6 docs and
  importer shift every index by -1; that was the `beta.1`/`beta.2` behaviour and was reverted in
  `beta.3` (release notes for `6.0.0-beta.3`, #1014).

So v6 is a plausible fix, not a verified one, and not something to move the car's device to
just for this.

## Open questions

1. **What shortens the reply?** About 20% of `22C101` polls while driving. Candidates: a
   Response Pending frame (`7F 22 78`) answered ahead of the data, lost Consecutive Frames on a
   busy bus, or the co-processor returning early at the end of its `ATST96` budget. canair's own
   sessions on 25 Oct logged 982 `no_data` of 1238 exchanges while the device was also polling
   (`quality` on the 10:45-13:04 session), so bus contention between the two is plausible.
2. **Is `pid_validation_en` on?** Not readable while the device is offline (10.0.2.86 and
   192.168.3.2 did not answer on 27 Oct).
3. **Which firmware is on the device?**
4. **Which earlier reply supplies the stale bytes?** The preceding request varies because
   periods differ (ESC 2500 ms, EPS 10000 ms), which would explain the varied spike values.

## Plan

1. **Observe it.** With the car on and the device reachable, read the device version and
   `pid_validation_en` from `/load_config`, and capture raw `22C101` replies from the AutoPID
   engine (device debug log, or the ELM327 text via the WiCAN terminal) until a short reply is
   caught. Record its frame count and which frames are missing.
2. **Turn on `pid_validation_en`** if it is off. It is weak (see mechanism step 4) but free.
3. **Report upstream** to `meatpiHQ/wican-fw` once step 1 confirms the trigger, and ask for a
   4.x backport of what v6 already does: clear `response->data` before each parse and
   bounds-check `B<n>`/`S<n>` against `length`, so a short reply fails instead of returning
   another PID's bytes. If step 1 shows lost Consecutive Frames instead, also ask v6 to check
   the sequence numbers and the First Frame's declared length in `ap_resp_frames`.
4. **canair side, only if step 1 shows a profile lever.** For example a PID whose reply needs
   more than the device can reliably collect could be left out of the generated AutoPID
   profile; `canair wican autopid stats` already lists the frame count per PID.

## Verification

- After a fix, re-run the weekend query against the recorder: the count of
  `real_speed` samples above 130 km/h on a day with a drive should be 0, and FL/FR = 16 should
  fall to the level of the other wheels.
- Cross-check `ESC REAL_SPEED_KMH` against `EPS_VEHICLE_SPEED` in Home Assistant history
  (`canair align` does the same on stored captures).
