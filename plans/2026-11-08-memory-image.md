# `canair memory image`: read an ECU's whole memory image unattended, resumably and checked

Status: **IMPLEMENTED** (2026-11-08), except the two live measurements, which need the car:
the digit and `slcan-tcp` rates of phase 1 and the sparse VCU run of phase 7. "Implementation
notes" at the end records where the code settles a decision or an open question.

Paths are relative to `src/canair/`. Builds on `plans/2026-11-05-read-memory-by-address.md`
(the `0x23` protocol layer, `scan memory`, `memory dump`, the dump store) and on its "Live test
on the VCU" section, which is the evidence this plan starts from.

## Problem

Reading the Ioniq VCU's flash on 2026-11-07 took 1 h 45 min of hand-driven commands for 1.2 MiB
of data. canair has every read it needs, but the person (or agent) running it has to do the
planning, the pacing and the checking:

- **The block is chosen by hand, and the fast path is missed.** Over `wican-ws` a read cost about
  190 ms whatever its size (about 125 B/s at 32-byte blocks, about 330 B/s at 64). Almost all of
  that is the ELM327 waiting out its `ATST` budget: with an expected-frame-count digit the
  adapter returns about 4× sooner (`docs/development/frame-counts.md`, 206 ms against 53 ms).
  Memory reads never carry the digit. The count is learned per exact request string
  (`CountKey = (header, request)`), and every memory read has a different address, so nothing is
  ever learned. A 64-byte reply is also 10 CAN frames, one over `MAX_REQUESTABLE_FRAMES` (9); a
  61-byte block fits in 9.
- **The CLI throttles every read by 20 ms** (`cli/commands/memory/reads.py`), about 10 % at that
  rate. Nothing measures whether the ECU needs it.
- **A dump cannot be resumed or merged.** The pieces are held in memory and saved once, when the
  dump ends (`live/memory/dump.py::_finish`). A WebSocket drop cost a whole 64 KiB chunk, and a
  killed process would lose all of it. So the run had to be cut into 64 KiB commands by hand.
- **Silence is ambiguous.** The VCU refuses addresses above `0x400000` by not answering. The scan
  stops after three silent reads with "the ECU stopped answering", which is also what a hung ECU
  looks like. Telling the two apart took a manual health read.
- **Finding the data is manual and easy to get wrong.** A survey of 64 bytes every 16 KiB called
  `0x008000`-`0x00FFFF` erased, but the full read found data at its end. Only a second survey at
  1 KiB found where the blank areas really are.
- **Integrity and health are checked by hand.** The repeat dumps, the `memory diff`, the health
  read after each chunk, the fault-code baseline and the identity-string search were all
  separate commands. Every reconnect cost 3-5 s.
- **Aliasing is not detected.** The HVAC ignores its high address bits, so its whole space repeats
  one 255-byte image (phase 0). A tool that dumps "everything readable" would read it 65 times.

## Goal

1. One command reads an ECU's readable memory into one dump: it maps the space, finds the size
   and any aliasing, finds which areas hold data, and reads them.
2. The user chooses **sparse** (data areas in full, blank areas sampled) or **thorough** (every
   readable byte), and sees the plan and its time estimate before the first read.
3. It runs as fast as the ECU and the link allow, within a block ceiling the user controls, and
   slows down by itself when errors appear.
4. A dropped link or a silent ECU pauses the job instead of ending it. A job that did end can be
   resumed later, and a resume proves that it talks to the same ECU and the same software before
   it reads on.
5. Every step is checked: each reply, each extent's hash, a re-read of ROM-like ranges, the ECU's
   health and the vehicle state during the run, and the fault codes and identity strings at the
   end.
6. Re-reading the VCU with it gives the same bytes as the 2026-11-07 dumps, in well under half
   the time.

## Non-goals

- **Anything but `0x23`.** No WriteMemoryByAddress, RequestUpload or transfer service, no
  SecurityAccess, no ECU reset, no programming session. The blocklist does not change, and the
  hard rules of the read-memory plan and the **contributing-code** skill hold unchanged. A
  locked region (NRC `0x33`) is recorded as locked and never asked for again.
- **Keeping the car in its state.** The user keeps the car charging or in READY. canair checks
  the state and pauses when it changes. It never acts on the car to restore the state.
- **RAM time series and RAM diffs.** This plan is for ROM-like ranges. A range that does not read
  back the same is reported, not collected.
- **Firmware analysis.** As before: canair reads bytes, and exports them for tools built for
  analysis. The only search in a dump is for the ECU's own identity strings.
- **A TUI.** The command prints a progress line and a final report. A Textual view can follow.

## Decisions to settle before phase 1

My recommendation is first in each.

1. **Frame count for memory reads.**
   - *(Recommended)* **Learn it per ECU, service and size.** The key becomes
     `(header, "23+SIZE")` for a memory read, so one plain read of a given size teaches the count
     for every address. This keeps the "learned, never guessed" rule of `frame-counts.md` as it
     stands, and the existing mismatch handling (realign, retry plain, retire on a different
     length) applies unchanged.
   - *Compute it from the size.* A positive reply is exactly `1 + size` bytes, so the count is
     known in advance. This saves one plain read per size, but changes a documented rule, and an
     ECU that answers with more bytes would leave a queued tail.
2. **What happens when the ECU goes silent or the link drops.**
   - *(Recommended)* **Wait in the run for a bounded time, then save and exit.** The job polls
     liveness gently (one 1-byte read every 30 s) for `--wait-minutes` (default 15), resumes by
     itself when the checks pass, and otherwise saves and prints the `--resume` command.
   - *Never wait.* The job saves and exits at once, and only the user resumes it. Simpler, and it
     never polls an ECU that may be hung, but an unattended run stops at the first hiccup.
3. **`slcan-tcp`.** With client-side ISO-TP there is no `ATST` wait and no 111-byte truncation, so
   larger blocks may work. It needs the device in `slcan` mode and one measured live run. Include
   the measurement in phase 1 and the result in the rate controller's defaults; do not make it a
   requirement.
4. **The command's name.** `canair memory image` (one command that plans and reads) with
   `--resume DUMP`. The alternative is more flags on `memory dump`, which would make one command
   do two jobs.

## Design

### The run

```text
preflight ─► map ─► size & aliases ─► survey ─► plan (show, confirm) ─► read ─► verify ─► report
                                                     ▲                    │
                                                     └──── pause/resume ◄─┘
```

1. **Preflight.** Read the ECU's health signals and evaluate the vehicle state the user named
   (`--state CHARGING`, through the `when:` predicate in `vehicle_states.yaml`; the predicate's
   own PIDs are read for it). Read the software version (`live/identity/software.py`). Read the
   fault codes of the ECU and of every ECU on the same `can_bus:` as the baseline. Stop if the
   state does not hold.
2. **Map.** The `scan memory` probes, with two changes:
   - **Silence is classified.** After a silent read, a liveness probe reads one byte at the last
     address that answered. If it answers, the silent address is `no_answer` (a refusal) and the
     map continues. If it does not, the ECU is offline and the run pauses.
   - **Past a refusing edge, the step grows.** Above the last readable address the probes go to
     the next power-of-two boundaries (for the VCU, `0x400000`, `0x800000`, `0xC00000`) instead
     of every step, so a large silent space costs a few timeouts, not hundreds.
3. **Size and aliases.** The readable span is matched against common flash sizes (powers of two
   from 64 KiB to 16 MiB, plus 1.5, 3 and 6 MiB). For each candidate size `S`, a few 64-byte
   samples at `A` and `A + S` are compared; identical samples at every point mean the ECU ignores
   the high address bits, and the plan reads only the first `S`. A sample that is all `FF` or all
   `00` on both sides does not count as evidence.
4. **Survey.** Read 64 bytes at a stride (default 4 KiB) across the space and sort each sample
   into erased (`FF` or `00`) or data. Then **bisect every boundary between an erased and a data
   sample at block granularity**, to the first block that holds data. A stride alone misses small
   islands; the 2026-11-07 VCU survey at 16 KiB did.
5. **Plan.** From the survey and the mode:

   | | Data areas | Erased areas | Stability check |
   |---|---|---|---|
   | sparse | read in full | 64 bytes every 1 KiB | re-read a sample (below) |
   | thorough | read in full | read in full | read everything twice |

   The plan prints its areas, the number of reads and a time estimate from the measured rate, and
   asks before the first read (`--yes` skips the prompt, as today). For the VCU, sparse is about
   1.2 MiB plus 200 KiB of samples; thorough is 4 MiB twice.
6. **Read.** The plan is split into extents of 4 KiB. Each extent is read with the dump's
   existing checked loop (alternating `N`/`N-1` sizes, halving, edge bisection), and written to
   disk as it completes (see "The job"). Health is read in the same session every
   `--health-every` seconds (default 30) without a reconnect.
7. **Verify** (see "Integrity").
8. **Report.** The region table, the areas read, the rate reached, every pause and its cause,
   every re-read, the stability result, the fault-code diff against the baseline, and which of
   the ECU's identity strings the image holds, with their addresses.

### The job: a dump that grows and resumes

A job is a dump directory whose manifest also holds its plan. `memory/store.py` gains the
fields, and `MANIFEST_VERSION` goes to 2 (a version-1 dump still loads):

- `plan:` the mode, the areas, the extent size, the block ceiling, the software version and the
  state the run needs.
- `extents:` one entry per finished extent: start, end, SHA-256, the read count and any events
  (re-read, halving, drop) seen while reading it.
- `pauses:` when the run paused, why, and how it resumed.

Each finished extent is appended to its stretch file and the manifest is rewritten atomically,
so a crash loses at most the extent in flight. A dump with a `plan:` and missing extents is
`incomplete`; `memory ls` shows it as resumable.

**A resume** (in the run after a pause, or `memory image --resume DUMP` later):

1. Connect, run the preflight again, and require the same software version as the plan. A
   different version stops the resume: the ECU was reflashed, or it is another ECU.
2. Re-read the last two finished extents and the first extent of the job, and compare their
   hashes. A mismatch stops the resume with a report.
3. Continue at the first missing extent with the block size the run had reached, never larger.

`memory dump` keeps its current behaviour, so the commands that use it do not change.

### Speed: the rate controller

A pure function in `memory/rate.py` takes the events of the last reads (ok, re-read, halving,
drop, truncation, silence) and returns the next block size and throttle:

- **The ceiling is the user's.** The block never exceeds the ECU's recorded `max_block` or
  `--block-limit`, whichever is smaller. The block search that raises `max_block` stays the
  existing one (doubling from 16, stopping at the first failure).
- **Prefer the digit.** On an ELM327 transport the controller prefers the largest size whose reply
  fits `MAX_REQUESTABLE_FRAMES` (61 bytes at classic CAN), when that is within the ceiling, since
  above it every read waits out `ATST`. On `slcan-tcp` it prefers the ceiling.
- **Back off fast, recover slow.** Two errors within 50 reads halve the block (to a floor of 16)
  and add 20 ms of throttle; 500 clean reads take one step back up. The throttle starts at 0.
- **Silence is never a rate signal.** A silent read goes to the liveness check, not to the
  controller.

Expected effect, not measured: about 1 kB/s on the VCU over `wican-ws` with the digit, against
330 B/s on 2026-11-07, so the sparse VCU job takes about 25 minutes.

### Integrity

Every level is checked, and a failed check never writes data:

| Level | Check | On failure |
|---|---|---|
| Reply | SID and exact length; alternating sizes catch a stale reply (exists) | re-read, then halve |
| Extent | SHA-256 recorded when the extent completes | — |
| Stability (ROM-like) | thorough: a second full pass, every extent hash compared; sparse: re-read 5 % of extents at random, every extent that had an event, and the first and last extent of each area | read a third time; a third read that matches neither stops the job; one that matches marks the first read as a link error, which the report shows |
| Same ECU | software version at the start, at every resume and at the end | stop |
| ECU health | health signals and the state predicate every `--health-every` s | pause |
| End | fault-code diff on the ECU and its bus neighbours; the ECU's `identity:` part number, `sw_id`, calibration and `ecu_id` searched in the image (never the VIN) | reported; a new fault code is printed first |

A range whose extents differ between two reads is reported as **volatile** with its addresses.
In a ROM-like range that means RAM, a mapped peripheral or a link fault, so the job stops and
does not guess which.

### Safety

- **Only `0x23`, on the ECU's physical address**, in the default session unless the ECU's
  `memory.session` names one. Nothing in this plan touches the blocklist.
- **The block ceiling is the hang guard.** The controller never goes above it, and a resume never
  starts above the size the run had reached. The HVAC case (a too-large read hangs diagnostics
  until the supply is cycled) stays the reason.
- **Liveness polling is gentle and bounded.** One 1-byte read every 30 s, for at most
  `--wait-minutes`. The run never retries a large read against an ECU that went silent.
- **The vehicle state must hold.** A state change (a charge that ends, READY dropping) pauses the
  job and tells the user. canair never acts on the car to restore it.
- **A dump stays local.** It is the same store, under `<config dir>/memory/`, with the same
  "never shared" banner.
- **Agents.** The `AGENTS.md` rule applies: an agent proposes the exact `memory image` command and
  the car state, and waits for the user's "yes". The plan printout is what the user approves.

### Where the code goes

| Piece | Module | Kind |
|---|---|---|
| Memory-read count key | `transport/elm327/frame_count.py` (key), `live/memory/reads.py` (passes the size) | transport policy |
| Job manifest, extents, resume state | `memory/job.py`, `memory/store.py` | pure + store |
| Size candidates, alias test, survey, plan | `memory/plan.py` | pure |
| Rate controller | `memory/rate.py` | pure |
| Stability and re-read policy | `memory/verify.py` | pure |
| Silence classification, health, pause and wait | `live/memory/liveness.py` | live |
| The run | `live/memory/image.py` | live, orchestration only |
| Flags and report | `cli/commands/memory/` (new `image` kind) | CLI |

The pure modules take probe results and return decisions, so `tests/support/memory_ecu.py` can
drive them offline. `live/memory/image.py` composes the existing scan and dump loops with these
parts; it holds no policy of its own. Mid-session link drops reuse `live/monitor/reconnect.py`
(`docs/development/link-health.md`) rather than a second reconnect path.

## Phases

1. **Speed.** The memory-read count key (decision 1). The dump throttle default goes to 0.
   Measure, with the user's approval, on the VCU in `CHARGING`: 60/61-byte blocks with the digit
   over `wican-ws`, and the same over `slcan-tcp` (decision 3). Record the rates in the
   read-memory plan. *Useful alone: `memory dump` gets faster.*
2. **The job store.** Incremental writes, extent hashes, manifest version 2, `incomplete` dumps
   in `memory ls`. `memory dump` writes through it too, so a killed dump keeps what it read.
3. **Liveness.** Silence classification in `scan memory` and in the dump loop, in-session health
   reads, the state predicate, pause and bounded wait (decision 2), and `--resume`.
4. **The planner.** Size candidates, the alias test, the survey with boundary bisection, sparse
   and thorough plans, the time estimate.
5. **The rate controller and the verifier.**
6. **`canair memory image`.** The orchestration, the report, the fault-code baseline and diff, the
   identity-string check.
7. **Docs and the live test.** `docs/concepts/memory-reads.md` (the new command, the modes, resume),
   the generated CLI reference, and the **cli-tui-standards** check of the output. Then, with the
   user's approval, a sparse VCU run whose extents must hash the same as the 2026-11-07 dumps.

## Tests

- **Fake ECU** (`tests/support/memory_ecu.py`), extended so it can: refuse by silence above an
  edge; alias its address space; hang on a read above a size until "power-cycled"; drop or
  truncate a given reply; go offline for a period and come back; change one byte between two
  reads (a volatile range); report a different software version after a "reflash".
- **Pure modules:** the planner on synthetic surveys (including a data island smaller than the
  stride), the alias test with erased samples, the controller's backoff and recovery, the
  verifier's 2-of-3 rule.
- **Resume:** kill the run at every phase boundary and mid-extent; the resumed dump must equal an
  uninterrupted one byte for byte.
- **Safety:** no request other than `0x23` (and the health and identity reads) is ever sent; the
  block never exceeds the ceiling, including after a resume.

## Open questions

1. **Health signals per ECU.** Is the ECU's first `active` PID a good default, or should an ECU
   declare its health PID (a new `memory.health:` field)? The VCU's `2101` and its
   `VCU_POWER_STATE` worked well, but a generic default may pick a slow PID.
2. **Blank value.** Some flash erases to `00`, and some ECUs return `00` for unmapped addresses.
   Is "`FF` or `00`" safe as erased, or should the survey learn the erased value from the
   largest uniform run?
3. **Extent size.** 4 KiB is a guess: smaller extents lose less on a crash and re-read cheaper,
   larger ones keep the manifest small. Measure on the VCU in phase 2.

## Implementation notes

- **Decision 1, the count key.** `uds/frame_counts.py::count_key` keys a memory read as the same
  read at address 0, not as a `23+SIZE` string. The key is per ECU, service and size as
  recommended, and it is still an even-length request, so `requestable()` and the seed filter
  apply to it unchanged. Both terminals and `ecus/response_frames.py` use it.
- **Decision 2.** The run waits (`--wait-minutes`, default 15) and polls one byte every 30 s.
- **Decision 4.** `canair memory image` with `--resume DUMP`. The mode is `--thorough` (sparse
  is the default), since `--mode` is already the session mode of every memory read.
- **Open question 1, health.** `--health PID`, by default the ECU's first active PID that is not
  a memory read. The PID is stored in the plan, so a resume checks the same one. A
  `memory.health:` field is not added.
- **Open question 2, blank value.** Erased stays "all `FF` or all `00`".
- **Open question 3, extent size.** 4 KiB, for `memory dump` too. Not measured yet.
- **The map.** Without `--range`, the image takes the readable regions `ecus/` records for the
  current software build, so the VCU needs no new map. The alias test tries powers of two from
  256 bytes, so a small repeating window such as the HVAC's is also found.
- **Samples.** A sampled extent reads 64 and 63 bytes in turn (the stale-reply check), at fixed
  addresses, so a resumed job samples the same bytes as an uninterrupted one.
- **Store.** Manifest version 2 appends every extent to `data.bin` and names each stretch by
  file and offset. Version-1 dumps (one `.bin` per stretch) still load. The writer lives in
  `memory/store.py`, and the job's extent passes in `live/memory/extents.py`.
- **Reconnect.** A link drop reuses the monitor's reconnector through a small `Link` holder
  (`live/memory/liveness.py`). An ECU whose reads need a held session gets no in-run reconnect:
  the job is saved for `--resume`, which opens the session again.
- **Start-up cost.** `canair memory image` imports its modules when it runs, through one
  deferred import in `cli/commands/memory/parser.py`, so building the parser loads no new
  module (`tests/contracts/baseline.json`: `deferred_internal_imports` 86 → 87).
