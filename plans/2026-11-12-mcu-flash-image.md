# Read the motor controller's flash (MCU) on the car

Status: **IN PROGRESS** (2026-11-12). Phases 0 and 1 are done; phase 2 needs the car.

Paths are relative to `src/canair/`. Point at code by file and symbol: line numbers drift.
Related: `plans/2026-11-05-read-memory-by-address.md` (the live tests),
`plans/2026-11-08-memory-image.md` (the `memory image` design),
`plans/2026-11-09-memory-chip-identification.md` (the chip catalogue), and the `research:` entry
"ReadMemoryByAddress (0x23) survey stopped the AC charge" in
`profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml`.

## Goal

A complete and byte-exact image of the programmed flash of the Ioniq's motor controller (the
profile's `MCU` ECU, `0x7E3`, software `8D:EAEEHEL-MS2-D000`). canair reads it through the OBD
port with ReadMemoryByAddress (`0x23`) only. The read must not stop a charge, drop the car out of
READY, or make an ECU store a fault code.

**Why.** Most MCU signals in `2101` and `2102` are unverified. The VCU image gave the source of
every record byte (`profiles/hyundai-ioniq-ae-ev/research/VCU_FIRMWARE_FINDINGS.md`). The MCU
image can do the same for the MCU.

## Non-goals

- Any service other than `0x23`, any session other than the default one, any write.
- SRAM. On the VCU, the KWP2000 `0x23` server cannot address `0x40000000`
  (`VCU_FIRMWARE_FINDINGS.md` section 12.2). The MCU runs the same boot code, so this is probably
  true for the MCU too.
- Any address above `0x3FFFFF`. The read that came before the stopped charge was at `0x864000`.
- A bench unit. This plan is about the car.

## What is known

### The ECU and its chip

| Fact | Value | Source |
|---|---|---|
| Protocol, memory format | KWP2000, 3-byte address, 1-byte size (`format: kwp`) | `ecus/mcu.yaml` `memory:` |
| Sessions | only `81` (standard) | `ecus/mcu.yaml` `sessions:` |
| Largest block | 64 bytes. 128 and 192 came back truncated at 111 bytes on `wican-ws`, 254 drew no answer, 255 drew NRC `0x53` | phase 0, 2026-11-05 |
| Boot block | the first 4 KiB match the VCU's and the OBC's except for 3 bytes | dumps of 2026-11-12 |
| SRAM | the start-up code clears SRAM up to `0x4003FFFF`, so 256 KiB, the same as the VCU | the 2 differing bytes |
| Application entry | the reset code calls `0x100000` (the VCU calls `0x180000`) | the third differing byte |
| Chip | MPC5674F class (e200z7, 4 MiB of flash), **inferred**: same boot code and SRAM size as the VCU, whose chip is itself only probable | `VCU_FIRMWARE_FINDINGS.md` section 14.1 |

### The survey of 2026-11-12

`memory image MCU --range 000000-3FFFFF --plan-only`, in READY, over `slcan-tcp` through the
`vpn` device. The map is recorded in `ecus/mcu.yaml` (`0x000000`-`0x3FFFFF` readable for this
software). The survey found no aliasing and these data areas:

| Area | Size |
|---|---|
| `0x000000`-`0x0062BF` | 24.7 KiB (boot blocks) |
| `0x00FFC0`-`0x010CBF` | 3.2 KiB (where the VCU keeps its ecu_id, app-valid word and identity header) |
| `0x011140`-`0x013F3F` | 11.5 KiB |
| `0x062100`-`0x0653FF` | 12.8 KiB |
| `0x080000`-`0x0A57BF` | 149.9 KiB |
| `0x100000`-`0x1637FF` | 398.0 KiB (the application entry is at its start) |
| `0x164940`-`0x167CBF` | 12.9 KiB |

Everything else up to `0x3FFFFF` read as erased. The plan came to 632 KiB read in full and 215 KiB
sampled.

### What happened on each attempt

| Date, state, transport | What was read | Result |
|---|---|---|
| 2026-11-05, CHARGING, `wican-ws` | phase 0: a few reads at `0x000000` and `0x010000` | answers. The block limit in the table above |
| 2026-11-08, CHARGING, `wican-ws` | the map of 16 MiB, then a survey every 4 KiB from `0x000000` upwards | every sample up to `0x863000` answered. `0x864000` drew no answer, then the MCU, BMS and OBC went silent and the AC charge stopped. No ECU stored a code |
| 2026-11-12, READY, `slcan-tcp` | boot block 4 KiB, then the map, block search and survey of `0x000000`-`0x3FFFFF` | the MCU stayed up. It ignored 32 reads, and the 1-byte check after each of them answered |

The 32 ignored reads of 2026-11-12, from `canair logs`:

- **The 1-byte map probes:** 0 of 1,024. Each reply fits one CAN frame.
- **The boot block dump:** 2 of about 70 reads. One of them is the first request after idle, which
  `profile.yaml` already notes can time out.
- **The block search:** 1 read of 128 bytes. See C1.
- **The survey:** 29 of about 1,100 reads, so about 1 in 38. They are spread over 12 minutes and
  over the addresses. By size: 6 at 63 or 64 bytes, 3 at 31 or 32, 22 at 15 or 16, which is the
  size the block had dropped to.
- **The VCU and the OBC on the same link and in the same state:** 0 of about 133 reads at 61 to
  64 bytes. Their bytes equal the dumps made while charging. At the MCU's rate, 133 clean reads in
  a row have a chance of about 3 %.

## What is not known

1. **Why the MCU ignores multi-frame reads in READY.** Every ignored read had a reply of more than
   one CAN frame. The hypotheses:

   | # | Hypothesis | For | Against |
   |---|---|---|---|
   | H1 | **Flow control arrives late.** On `slcan-tcp`, canair sends the ISO-TP flow-control frame from the laptop, across the VPN. If it arrives after the MCU's N_Bs timeout, the MCU drops the reply | only multi-frame replies were lost; on `wican-ws` (flow control from the dongle) almost none were lost | the VCU and OBC lost none on the same link, so the MCU would need a shorter timeout than they have |
   | H2 | **The diagnostic task is starved in READY.** Motor control takes the time | the drop rate was high in READY and low while charging | single-frame replies were never lost |
   | H3 | **The 128-byte read degraded the diagnostic stack** (the climate unit hung after a 192-byte read) | the drop rate rose after it | 2 reads were lost before it |
   | H4 | **The reads came too fast** | none | 100 ms of throttle; the rate did not fall when the block shrank |

   The `slcan-tcp` path logs `no_data` both when no frame came and when a first frame came but no
   consecutive frame. So the log cannot tell H1 from H2 (see C3).

2. **Why the charge stopped at `0x864000`.** It stays out of scope: no read goes above
   `0x3FFFFF`. The static analysis (phase 4) may explain it from the MCU's TLB set-up.

3. **Is the flash 4 MiB?** The erased samples up to `0x3FFFFF` answered on 2026-11-08 and
   2026-11-12, and the boot code points to a 4 MiB part. The checksum in phase 4 can confirm it.

## Phases

### Phase 0: record what is known (offline)

- Add the 2026-11-12 results to the MCU `research:` entry with `canair pids`: the chip evidence,
  the layout, the drop pattern, and the silent 128-byte read.
- Correct the line in `docs/concepts/memory-reads.md` ("What the reference car answers") that says
  only "do not read it while the car charges".

**Done (2026-11-12):** a new MCU `research:` entry ("ReadMemoryByAddress (0x23) flash image
0x000000-0x3FFFFF"), new notes on the 2026-11-08 entry, and the MCU line in the docs.

### Phase 1: code changes before the full read

Follow the **contributing-code** skill. Each change gets tests on the fake ECU
(`tests/support/memory_ecu.py`).

- **C1. The block search obeys the help text.** `memory image --help` says that the largest read
  is the ECU's `max_block`, else 64. When no `max_block` is recorded, the map step in
  `live/memory/image.py` passes `DEFAULT_BLOCK_LIMIT` (128, from `live/memory/scan.py`) to
  `find_block`. On 2026-11-12 that sent one 128-byte read to the MCU. Make the image search stop
  at 64, as the help says, and correct `docs/concepts/memory-reads.md`, which gives 128. `scan
  memory` keeps its own `--block-limit` default.
- **C2. A lost reply is not a size signal.** `BlockReader._silence` in `live/memory/reads.py`
  halves the block after a silent read when the 1-byte check answers. The rate controller then
  counts that `halving` as an error (`memory/rate.py`, `ERRORS`). Its docstring says "silence is
  never a rate signal", but in practice silence is one. On the MCU the losses do not depend on
  size: the survey fell to 16 bytes, and the estimate grew from about 14,000 reads to about 65,000
  (5 h 12 min). The change:
  - When the size is at or below the recorded `max_block`, read the same size again once. Halve
    only when that read is silent too.
  - Report the extra read as its own event (for example `retry`). It is not an error for the
    rate controller.
  - Above `max_block` (an ECU that drops too-large reads, as the MCU did at 254 bytes), keep the
    halving as it is now.
- **C3. Tell the two kinds of silence apart on `slcan-tcp`.** Log whether a first frame came
  before the timeout, for example as a `drop` with "first frame, no consecutive frame". First
  check how can-isotp reports a reception timeout (`transport/raw/isotp_stack.py`). This gives
  the evidence that separates H1 from H2.

**Done (2026-11-12):**

- C1: `live/memory/image.py` searches up to `block_for` (`--block-limit`, else 64).
- C2: `BlockReader._retry` in `live/memory/reads.py` (the `retry` event, `reader.retries`,
  `DumpRun.retries`). `memory/rate.py` counts a retry as a read and nothing else. The image sets
  `reader.known_block` from its block search, so a first run retries too.
- C3: `ReceptionFaults` in `transport/raw/isotp_stack.py` records can-isotp's reception errors.
  `RawTerminal.send_uds` then reports the read as a `drop` ("truncated ISO-TP: a first frame
  came, then ConsecutiveFrameTimeoutError", or "… incomplete at the deadline"). A drop of a known
  size goes straight to the C2 retry, without the 1-byte check. The pipelined `RawUdsClient`
  (the monitor) raises a `BrokenReplyError` (a `TimeoutError`, counted as a `drop`) for the same
  case, and no longer counts a reply that broke off as one the ECU still owes.

### Phase 2: find the conditions with the fewest lost replies (on the car)

**Every step needs the user's approval of the exact command.** For every session, the car is in
P with the parking brake on, nobody is in it, and the climate is off. Run `dtc read` on the MCU,
VCU, OBC and BMS before and after each session.

- **E1. READY, `slcan-tcp`, after C2 and C3: loss rate by reply shape.** All reads go to data
  areas, with 100 ms of throttle. Each line is about 500 reads.

  | Read | Reply | Shows |
  |---|---|---|
  | `memory dump MCU 100000-100BFF --block 6` | 7 bytes, one frame | is a single-frame reply with data also safe? |
  | `memory dump MCU 101000-101BFF --block 7` | 8 bytes, two frames | does one consecutive frame already fail? |
  | `memory dump MCU 102000-109FFF` | 64 bytes, 10 frames | the rate at full size |
  | `memory dump VCU 180000-187FFF` | 64 bytes, 10 frames | the control: the VCU on the same link |

  Record the link round trip at the start (`canair status`). C2 is needed here: without it, the
  first lost reply halves `--block 7` into single-frame reads.
- **E2. READY, flow control from the dongle.** Run the 64-byte MCU line again over
  `--transport wican-ws`, or over `--wican home` if the car is on the home network. If the losses
  go away, H1 holds. If they stay, H2 holds. The 2026-11-12 session used `slcan-tcp` by the
  user's choice, so this step needs the user's decision.
- **E3. CHARGING, optional.** Run the 64-byte MCU line while AC charging, on the transport that
  E2 chose. This separates the state from the link. The range stays far below `0x864000`.

**The decision:** use the state, transport and throttle with the lowest loss rate. If two
conditions are equal, prefer READY: a stop then drops the car out of READY, but it does not end a
charge.

### Phase 3: the full read (on the car)

```bash
uv run canair --profile hyundai-ioniq-ae-ev memory image MCU --block-limit 64 \
  --state <STATE> --throttle-ms <N> --health-every 30 --wait-minutes 2 --label "MCU flash"
```

- The run uses the regions recorded for this software, so it needs no `--range` and maps
  nothing again. It surveys again, then asks before it reads.
- **Size and time.** Sparse mode reads 632 KiB in full and samples 215 KiB, about 14,000 reads at
  64 bytes. The time depends on the rate that E1 measures. The OBC image on `wican-ws` ran at
  296 B/s, which for this plan comes to about 50 min.
- **`--health-every 30`, not 10.** Each check reads the READY predicate on the BCM, IGPM, SKM and
  VCU. At 10 s these reads take a large share of the link. The run pauses at the first silent MCU
  read anyway.
- **Stop rules.** The run stops when one of these happens, and it can resume later with
  `--resume` on the same software:
  - The state no longer holds.
  - The MCU stays silent for more than 2 min.
  - The report shows a new fault code.
  - The verify step finds a volatile range.

  Press `s` to stop and save between sessions.

### Phase 4: check and analyse (offline)

- **The image's own check:** `memory image` reads again 5 % of the extents, plus the first and
  last of each area.
- **The header checksum.** The VCU stores a byte sum over `0x4000`-`0x3FFFFF`, less its 128-byte
  identity header, at `0x10078` (`VCU_FIRMWARE_FINDINGS.md` section 14.2). The MCU has data at
  `0x00FFC0`-`0x010CBF`, where that header sits on the VCU. If the MCU's header has the same
  layout, recompute the sum. A match proves that the image is byte-exact and that the flash is
  4 MiB. The same algorithm does not match the OBC's stored sum, so a mismatch alone does not
  prove a bad image.
- **Static analysis** into `profiles/hyundai-ioniq-ae-ev/research/MCU_FIRMWARE_FINDINGS.md`, with
  the method of the VCU findings:
  - the source of each `2101` and `2102` byte, so the MCU signals can be verified;
  - the `0x23` handler and the TLB set-up: why the MCU answers all 16 MiB, and maybe why
    `0x864000` stopped it;
  - the fault-code table and the CAN frame layouts.
- **Record the results** with the tools: the `research:` entry, the MCU line in
  `docs/concepts/memory-reads.md`, and the MCU row of the chip plan. The image itself stays local.

## Safety

- Only `0x23`, in the default session. canair writes nothing to the car.
- No address above `0x3FFFFF`, and no read larger than 64 bytes.
- Every live step needs the user's approval of the exact command (`AGENTS.md`, "The car"). A
  subagent never talks to the car.
- The dumps stay in `~/.config/canair/memory/`. They never go into `captures/`, a profile, or a
  pull request: they hold OEM software and possibly secrets.

## Open questions

1. What are the MCU's ISO-TP timeouts (N_Bs, N_Cr)? Phase 4 may find them in the image.
2. Is the chip really an MPC5674F? The plan needs only "a 4 MiB part", and the checksum can
   confirm that.
3. If E1 to E3 give no clear winner, is a 5-hour read in READY acceptable, split into several
   sessions with `--resume`?
