# A default memory space: `scan memory` and `memory image` with no `--range`

Status: **IMPLEMENTED** (2026-11-08).

Paths are relative to `src/canair/`. Builds on `plans/2026-11-08-memory-image.md` and
`plans/2026-11-05-read-memory-by-address.md`.

## Problem

`canair memory image OBC` and `canair memory image MCU` stop with "records no readable region":
neither ECU has a `memory:` section, because both were only probed by hand in phase 0 of the
read-memory plan. The VCU's recorded region names its software build, so the same stop follows a
dealer reflash. `scan memory` cannot help without a range either: `--range` is required, because
a read of a peripheral register can change a microcontroller's state, so the docs say to name the
ranges from the chip's memory map.

The image also reads at 64 bytes when an ECU records no `max_block`, without the 16 → 32 → 64
climb that `scan memory` uses as its guard against an ECU that hangs on a read larger than it
supports.

## Decisions

- **The default space is a property of the memory format, not of the protocol.** With no
  `--range`, a command maps the whole space the ECU's memory format addresses when its addresses
  are 3 bytes or fewer (at most 16 MiB). That covers every KWP2000 ECU (a fixed 3-byte address,
  `0x000000-0xFFFFFF`) and a UDS ECU whose ALFID has a 1- to 3-byte address (the Ioniq HVAC's
  `13`). A UDS ECU with 4-byte addresses (4 GiB, the BCM's `24`) still needs `--range`: a sweep
  would take hours and cross the chip's peripheral registers.
- **The evidence.** The VCU's whole 16 MiB was scanned on 2026-11-07 with no side effects: it
  refuses everything above `0x400000` by silence, and the silence classification of the image plan
  costs only a few probes there.
- **A format not known yet** is probed at address 0 first (the scan's format probe), and the rule
  then applies to the format found.
- **The step scales only for the default space.** With no `--range` and no `--step`, the step is
  the power of two that gives about 256 probes (64 KiB across 16 MiB). An explicit `--range`
  keeps the 4 KiB default step, so an existing command probes what it always probed.
- **The confirmation names the space.** The prompt before connecting says which range and about
  how many probes. `--yes` accepts it. A proposed command therefore never reads more than its
  printout says.
- **The image maps by itself and writes the map back.** When `ecus/` records no readable region
  for the current software build (none at all, or one from another build), `memory image` maps
  the default space (or `--range`) like `scan memory`, runs the block search when no `max_block`
  is recorded, and writes the regions, the format and `max_block` to the ECU's `memory:` section
  through the scan's editor (`yaml_edit/memory.py::record_scan`). `--dry-run` skips the write. The
  map is written whether or not the plan is confirmed afterwards: it is a map, as `scan memory`
  writes one.
- **The block search** climbs from 16 bytes up to `--block-limit` (default 128, the scan's), and
  stops at the first size that fails, before the survey. The run's ceiling is the size it found.

## Where the code goes

| Piece | Module |
|---|---|
| The default space and step of a format | `uds/memory.py` (`default_scan_space`, `default_scan_step`) |
| `scan memory` with no ranges | `live/memory/scan.py`, `cli/commands/scan/memory_kind.py` |
| The image's map, block search and write-back | `live/memory/survey.py`, `live/memory/image.py` |
| The prompt text that names the space | `cli/commands/memory/reads.py` |

## Tests

- The rule: KWP2000 and ALFID `13` default to their whole space, ALFID `24` has none, and the
  step gives about 256 probes.
- `scan memory` with no ranges maps a KWP2000 ECU's 16 MiB at 64 KiB steps; a 4-byte UDS ECU
  stops and asks for `--range`; an explicit range keeps its 4 KiB step.
- `memory image` with no recorded region maps, searches the block, writes the map (not with
  `--dry-run`) and plans; a region from another software build is mapped again; a 4-byte UDS ECU
  with nothing recorded stops and asks for `--range`.
