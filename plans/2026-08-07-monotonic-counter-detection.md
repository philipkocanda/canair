# Monotonic counter detection (`investigate --counters`)

Status: **DONE**

Motivated by `plans/2026-08-02-blind-tooling-stress-test.md`, where a blind agent
recovered the CLU odometer (`[B12:B14]`, "24-bit BE monotonic counter") *by hand* —
reasoning "strictly non-decreasing over 12 months, plausible km, ~8.4k km/yr" — and
the stress test recorded "event/counter fingerprints were unambiguous" as an
**analyst** observation with no tooling behind it.

## The gap

Nothing in canair could find a counter.

- `canlib/triage.py::classify` has a `counter` class, but it scores entropy +
  flip-rate + `mean_abs_step`, and `mean_abs_step` uses `abs()` — it is
  **direction-blind**. It finds a fast rolling/alive counter's low byte. A slow
  accumulator polled every 5 s classifies as `constant`.
- The class was display-only: no flag filtered or ranked by it, and it contributed
  nothing to `investigate`'s ordering.
- `--transform cumsum` transforms the *reference*, never the candidate, so you could
  ask "does X track cumsum(Y)?" but not "is X itself cumulative".
- `align.detrend_by_session` / `--per-session` **removes** the cross-session level —
  actively deleting the signal a counter hunt needs.
- Multi-byte: `triage.detect_words` is pairs-only and range-shaped; a 3-byte
  odometer fails it outright.
- No synthetic time/index pseudo-signal exists to correlate against.

The underlying reason all of the above miss it: **within any single session a slow
counter does not move**, so it has no variance for correlation or discrimination to
work with. Only the whole-corpus behaviour identifies it.

## Design

`canair investigate <ECU> <PID> --counters`, a short-circuit view like
`--events`/`--dwell`. Chosen as an `investigate` flag rather than a new command,
following the precedent in `plans/2026-07-27-re-analysis-enhancements.md` ("triage
folds into `investigate`; no standalone `canair triage`").

### Layering

| Module | Role |
|---|---|
| `canlib/counters.py` | **leaf, numpy-free** pure detection (like `triage.py`/`stats.py`): value columns + a timestamp vector in, candidates out. Knows nothing of WiCAN/PCI/ISO-TP/captures, so a domain-B raw-CAN path can reuse it unchanged. |
| `canlib/commands/investigate/counters.py` | the capture-model bridge: row-aligned ISO-TP payload matrix, ISO-TP→WiCAN expression rendering, mapped-param overlay, human + `--json` reporting. |

Enabling refactor: `linear_fit` moved `xanalysis.py` → `stats.py` (its declared home
for hand-rolled numpy-free coefficients), re-exported from `xanalysis` so all six
existing call sites are unchanged. This let `counters.py` stay leaf.

`DecodedCapture.payload_bytes` was added to `align.py` — the ISO-TP-space
counterpart to `.frame`, so the bridge doesn't re-parse hex ad hoc.

### Scoring: bits of evidence

Under the null "this series has no preferred direction", each of the k *moving*
steps points up with p=0.5, so a clean all-up run is worth exactly k bits
(one-sided binomial tail, computed in log space — a 4000-step series overflows a
float binomial coefficient outright).

This is the crux of the design. A fixed "minimum number of up-steps" threshold
discards precisely the counters worth naming: the odometer read six times in a year
scores 2 bits while a cumulative-Ah register scores 4000, and both must be rankable
on one scale. Flat steps are excluded (they carry no directional information), so a
mostly-stationary counter is judged on its rises, not diluted by its stillness.

### Three fingerprints

Grouped by *where* the monotonicity lives, because that is what names the signal:

| Kind | Behaviour | Typically |
|---|---|---|
| `accumulator` | rises across the corpus **and** within sessions | odometer, operating-seconds, cumulative Ah/Wh |
| `cycle` | rises across the corpus, **flat inside every session** | ignition/power-cycle/trip count |
| `timer` | ramps per session, **resets to ~0**, slope snaps to a wall-clock tick | uptime, seconds-since-key-on |

### Rejection rules (what makes the sweep usable)

A brute-force width × endianness sweep produces far more spurious windows than real
ones. In rough order of how much each one matters:

- **`msb_jump`** = `max_step / 256^(w-1)`. A window shifted off the true counter
  boundary swallows a *neighbouring* counter's byte, so when that neighbour ticks the
  window leaps by hundreds of MSB units. A real counter advances its MSB by ≤1.
- **`step_ratio`** = `max_step / max_value`. A running counter's value is vastly
  larger than one increment. This kills the false-positive class that actually
  appeared in the bundled profile: a block of HVAC bytes flipping `0x000000` →
  `0x010001` is monotone and looks like a 2-step counter, but its "increment" *is* its
  whole value — that's flags turning on.
- **A-overlap suppression.** A wide accumulator's low bytes wrap every 256 ticks,
  which is indistinguishable from a resetting run-timer viewed alone. Two BMS
  `OPERATING_TIME` low-byte aliases were reported as 1 s timers before this.
- **reset-to-~0 + tick snapping** for timers (a real uptime starts each session near
  zero and ticks at a sane clock division).
- **canonical window** — the LSB must move (else the window is over-extended right:
  a 3-byte odometer read as 4 bytes = value × 256) and the MSB must move or be a
  *non-zero* constant (a constant zero high byte is padding; a constant `0x01` is
  real magnitude).

### Two alignment decisions that cost real data during development

- **Prefix, not modal, payload length.** BCM `22C011` returns both 12- and 13-byte
  payloads (trailing `0xAA` ISO-TP padding). Aligning the matrix on the *modal*
  length silently discarded 478 of 3524 captures — **three months** of the horizon,
  cutting the span from 112 days to 14. Regression-tested.
- **ISO-TP, not WiCAN, window space.** PCI framing bytes interleave every 7 data
  bytes in WiCAN space, so a 4-byte window can straddle one. Windows are formed in
  ISO-TP space (contiguous by construction) and rendered via `ByteRef`, which emits
  a shift composition for a straddling read rather than a wrong `[Bn:Bm]`.

### Clustering

One physical counter makes every *prefix* window monotonic (dropping the low byte
just divides by 256), so nested hits are collapsed to one representative: the widest
**canonical** window. Width is deliberately preferred over narrowness because it
recovers the readable magnitude, which is the whole diagnostic — `[B12:B14] = 72982`
is instantly an odometer; `B14 = 88` says nothing. Accumulator and cycle candidates
cluster together (so a counter's high half can't resurface as a separate "cycle
count"); timers cluster separately.

### UX

`--min-bits` defaults to 4 (p = 1/16). Rather than pick a default that is either too
strict for sparse counters or too noisy, an **empty report names the best
sub-threshold window and the exact `--min-bits` that would surface it**, plus the
chance-probability so the user reads it as a lead. The sweep runs at a floor and the
caller filters, so this costs nothing. Alias suppression uses its own constant
(`_ALIAS_MIN_BITS`) rather than the display threshold — whether a timer is really an
accumulator's wrapping low byte is a property of the data, not of how much the user
chose to see.

## Validation

Recovered all six known counters in the bundled profile at the correct windows —
BMS `2101`'s five cumulative registers (`OPERATING_TIME` at 5986 bits) and CLU
`22B002`'s `[B12:B14]` odometer — and found three previously-undecoded ones, two of
which were written up:

- **`VCU 21F2` `VCU_ODOMETER`** — a 1/256 km (3.9 m) odometer, 256× finer than the
  cluster's. Confirmed by a constant +4.4…+4.7 km offset vs the *verified* CLU
  `ODOMETER`, lock-step freezing while parked, a wrapping fractional low byte, and a
  non-constant ratio against `OPERATING_TIME` (distance, not time).
- **`BCM 22C011` `BCM_EVENT_COUNTER_C011`** — 9835 → 10092 over 112 days, 13 clean
  rises, flat in all 20 sessions, ~7.24 km per count. Ignition/trip count pending a
  controlled key-cycle test (research lead filed). This **corrected** the PID's own
  header note, which claimed the tail bytes were static.

Run-timers found nothing in this corpus — verified as a true negative against a
synthetic seconds-since-key-on series (detected at tick = 1 s, cv 0 %).

### Whole-corpus sweep (all 114 captured ECU:PID pairs)

Sweeping every `(ECU, PID)` pair with captures — 65 with timed data, 49 untimed
one-shot identity reads — returned **8 windows at the default `--min-bits 4`, and
every one was already mapped**: the five BMS `2101` accumulators, `OPERATING_TIME`,
`VCU_ODOMETER`, `BCM_EVENT_COUNTER_C011`, and (sub-threshold) the CLU odometer. No
*new* counter cleared the default bar, which is the expected steady state once the
obvious registers are decoded.

Two things the sweep exposed:

1. **`--unmapped-only` hid the sweep's most valuable finding.** It filtered on *any*
   mapping, so a window covered by an **unverified** guess was suppressed — even
   though monotonicity is frequently the evidence that *refutes* such a guess. The
   `verified` flag was already plumbed into the `mapped` overlay as
   `dict[int, tuple[str, bool]]` but `_mapped_by` discarded it. Fixed: the flag is
   honoured, `--unmapped-only` hides only *settled* windows (every covering param
   verified), the renderer draws the byte view's three-way
   `unmapped` / `[NAME]` / `[NAME?]` distinction, and `--json` carries
   `mapped_verified`.

   The finding it was hiding: **`VCU 21F2` `CHARGE_TIMER_WKND_END_HOUR` (`B75`,
   unverified) is very likely not an hour at all.** It was inferred from a single
   before/after coincidence (`0x14` = 20 matched a 20:50 weekend-end timer) and its
   own note asked for "a second value to verify". Four more arrived: the byte reads
   16 → 20 → 21 → 22 → 23 across 07-22 → 08-05, strictly rising, flat within all 19
   sessions, with no charge-timer reprogramming after 07-28. A user-set hour does not
   ratchet; a counter does — and it is now *at* its declared `max: 23`, so the next
   increment breaks the declared range. Mechanism undetermined (the ~0.65/day rate is
   compatible with a charge- or drive-cycle count).

2. **`canonical` only tests the outermost bytes**, so a window with a constant
   *non-zero* high byte or a constant interior byte still passes while inflating the
   magnitude 256× per byte. `VCU 21F2` `[B73:B75]` reports `5963792 → 5963799` where
   the informative reading is `B75 = 16 → 23` (`vary=1/3`); `SKM 22B00B` `[B28:B31]`
   reports 423 million for a byte stepping `0x42 → 0x4C`. The doc claim that "the
   width recovers the readable magnitude" inverts here. Not changed in the detector
   (the narrower window is still emitted and listed under `subsumed`, and widening is
   right for a true multi-byte counter); instead `_print_one` now prints a hint
   whenever `n_varying < width` telling the reader to read the varying byte(s) alone.

Two sub-threshold leads, both **weak and not promoted**: `BMS 21F2` `B53`
(11 → 12 → 13, but the four untimed 07-22 captures read **12**, so the full series is
non-monotonic — a good illustration of why untimed rows are dropped, and of what
their absence costs) and `SKM 22B00B` (5 captures, 3 bits).

## Untimed captures: enforced at write, history grandfathered

`load_signal_captures` drops untimed captures. Measured impact on this feature: it
costs 3 of CLU `22B002`'s 9 readings and halves that odometer's evidence (4 bits →
2). It is **not** fatal — the odometer is still found — because the *prefix*
alignment fix mattered far more. That reframed the fix: rather than teach every
analysis command about day-resolution ordering (which would make each one reason
about ordering certainty), make an untimed payload capture **impossible to create**
and leave the existing rows alone.

Audit found exactly **two** live holes; everything else already stamped time
(journal `append`, `build_query_session`, `_capture_stamp`):

| Path | Was | Now |
|---|---|---|
| `canair raw --save` → `captures.build_raw_session` | set `payload`, never `time` | stamps acquisition time on the payload branch |
| `canair import uds` (no `--time`) | `--time` had no default, key omitted | defaults to the import instant, like `--date` defaults to today |

Deliberately **not** done:

- **No `save_session` backstop.** It would catch future builders, but every
  note/state/delete editor re-saves through nearby paths — a backstop risks
  silently stamping a *legacy* untimed row with "now", inventing evidence. The two
  narrow fixes cannot touch history.
- **No schema `if payload then require time`.** Expressible, but it would hard-fail
  `validate captures` on the 284 grandfathered rows. Enforcement stays in the
  validator's `--strict` gate (already exits 1; non-strict warns and exits 0).
- **No backfill or purge of the 284 legacy rows.** Backfilling would write
  approximate timestamps into evidence files; purging would destroy the oldest CLU
  odometer reading (70047 km) and other one-shot history.

A non-answer (NRC/error) capture is *not* a time-series sample and stays
deliberately untimed — the validator already exempts it.

## `pids set-pid-notes` — closing the hand-edit gap this work exposed

Correcting the BCM `22C011` header note (which wrongly claimed the tail bytes were
static) had to be done **by hand**: `canair pids` reached every other field of a PID
but not its `notes:`. Per the contributing-code rule ("if a field of `ecus/` can only
be changed by hand because no `canair pids` subcommand reaches it, the fix is to add
the surgical editor"), that is a bug, so the editor now exists:

```
canair pids set-pid-notes <ECU> <PID> "…"    # set (or replace in place)
canair pids set-pid-notes <ECU> <PID>        # omit the text to clear
```

Behaviour: an existing note keeps its position; a new one is inserted **above
`parameters:`**, so the short metadata stays on top and the prose sits between it
and the parameter list — matching how the hand-authored files read. Rendering uses
the shared note policy (short → inline, long → wrapped folded `>-`).

Two defects surfaced while building it:

- **Clearing a block scalar.** `_remove_field_line` drops only the `notes:` header,
  which orphans a folded body and yields invalid YAML. (The `_safe_write` re-parse
  guard caught it and restored the file — the safety net works.) Clearing now goes
  through `_replace_field_in_block_at` with an empty replacement, which already
  knows how to skip block-scalar continuation.
- **A pre-existing whitespace bug in `_replace_field_in_block_at`** (shared by
  `set_identity_field`, `set_can_bus`, …): `splitlines()` + `"\n".join()` collapses
  a block's trailing blank line, so every edit silently ate one blank separator
  between sibling blocks and the loss compounded across edits. The block's exact
  trailing newline run is now re-attached. Regression-tested.

## Follow-up

**`plans/2026-08-07-analysis-tooling-followups.md`** collects everything this
work surfaced but did not fix — including three defects in the shipped
`--counters` view (`--notation` ignored, no scoped-run warning, no keep-mode
banner), the fact that `validate --strict` cannot actually serve as the CI gate
the untimed-capture decision assumed, and the reusable ideas (the data-derived
threshold hint, a corpus-wide `investigate`, the Δ-ratio "tracks a known signal"
test).

Not implemented here: a `can` (raw broadcast frame) counterpart.
`canlib/counters.py` is byte-space-agnostic specifically so that stays a thin
addition.
