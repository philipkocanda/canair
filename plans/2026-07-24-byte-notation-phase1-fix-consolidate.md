# Byte Notation — Phase 1: Fix + Consolidate the WiCAN-byte Boundary

Status: **DONE** — see the `## Status` checklist near the end for the per-item
verification. Phase 2 is `2026-07-24-byte-notation-phase2-isotp-canonical.md`.

Tighten the existing WiCAN-byte plumbing without changing which byte space the
analysis engine reasons in. This is the **low-risk, ship-now** foundation for the
larger Phase 2 (ISO-TP-canonical + switchable display notation, see
`2026-07-24-byte-notation-phase2-isotp-canonical.md`).

## Why (the concern)

WiCAN's PCI-interleaved byte notation (`B0=PCI`, `B1=len_lo`, `B8/B16/…=CF PCI`,
first real CF data byte at `B9`) is a **firmware-transport artifact** of how
WiCAN AutoPID copies all 8 CAN data bytes per frame — PCI included — into its
buffer. Expressions (`B09`, `[S10:S11]`) are indexed against that layout and
*must* stay WiCAN to feed the firmware. The problem: that fringe notation has
become the codebase's de-facto canonical byte space, including in analysis/
display code where it conflates with raw-CAN / ISO-TP offsets and confuses
contributors. Concrete smells already in-tree (three locations, two code + one doc):

- `canlib/commands/bix.py:203` prints WiCAN as **"(raw CAN frame index)"** — but
  WiCAN bytes are *not* raw CAN (no CAN ID/DLC; PCI re-inserted over ISO-TP). The
  same string is echoed in the new deep-reference doc
  (`docs/concepts/wican-byte-index.md:251`, a `bix w9` example) — reword both.
- `canlib/commands/_decode_plot.py:399` comments "Raw WiCAN frames per capture".

> Re-evaluated against the current tree (uncommitted `bix.py` + docs work): the
> user added `docs/concepts/wican-byte-index.md` (firmware-grounded WiCAN-index
> reference, linked from `byte-indexing.md` + `mkdocs.yml`) and reworked `bix.py`
> (added `--torque/--obdb`; Torque/bix columns default-hidden; `--annotate` now
> derives every notation from the byte's ISO-TP index). Line numbers below reflect
> the shifted `bix.py`. None of the bugs/targets here were touched by that work.

Phase 1 does **not** re-home the canonical space (that's Phase 2). It fixes real
bugs, removes a duplicate implementation, and makes the byte-space boundary
explicit in code + docs so the eventual migration is cheap and contributors stop
conflating the two.

## Byte-space model (reference)

Protocol stack: **CAN → ISO-TP → UDS**. Two layouts in play today:

| Space | What it is | Where it's legitimate |
|---|---|---|
| **ISO-TP** | reassembled UDS payload, no framing (`SID DID data…`) — what every transport returns | the natural analysis unit |
| **WiCAN** | ISO-TP with PCI bytes re-inserted (`B0/B1`, `B8`, `B16`, …) | firmware-bound expressions only |

The expression evaluator (`canlib/expression.py:9`) is **byte-space-agnostic** —
it just reads `data[idx]`. The WiCAN-indexing contract is imposed *entirely* by
callers passing PCI-reinserted bytes. That invariant is currently implicit; Phase
1 makes it explicit.

## Scope (locked)

| Decision | Choice |
|---|---|
| Re-home canonical space to ISO-TP? | **No** — Phase 2 |
| User-facing byte labels | **Keep WiCAN `Bn`** (label == promotable expression) |
| CLI surface changes | **None** |
| Fix the `hunt_byte` PCI bug + unify `bix` detector | **Yes** (hunt = real bug; bix = consistency cleanup) |
| Consolidate the two PCI-reinsertion impls | **Yes**, into one + equivalence test |
| Name the boundary in code + docs | **Yes** |

## Work

### 1a. Fix the `hunt_byte` PCI detector (real bug) + unify `bix` (cleanup)

Two paths hand-roll a *simplified* PCI test (`idx % 8 == 0` / `range(0, w+10, 8)`),
which flags WiCAN indices 0, 8, 16, … but **misses the first-frame's second PCI
byte at index 1**. Every other path uses the exact
`byteindex.wican_to_isotp(idx) is None` detector.

- `canlib/xanalysis.py:394` — `hunt_byte`: `pci = {i for i in range(max_len) if
  i % 8 == 0}`. **Real, observable bug:** a byte window that includes WiCAN index
  1 (e.g. a `u8`/`u16` read whose value tracks the length-low byte at B1) survives
  the guard and can be surfaced/promoted, caught only later by `check_pci_bytes`
  (`canlib/commands/validate.py:79`). Replace with the canonical
  `wican_to_isotp(i) is None`. **Red-green testable.**
- `canlib/commands/bix.py:216` — `_print_result`: `pci_indices = set(range(0, w +
  10, 8))`. **Benign inconsistency, not an observable bug** — verified: the only
  index it mis-classifies is B1, and the only lookup whose neighbor is B1 is `bix
  w2`, which emits no warning either way (B0 is also PCI, so the shift-composition
  `before` index goes negative and nothing prints). Still switch it to the shared
  `wican_to_isotp` detector for **consistency + future-safety** — an isolated
  hand-rolled heuristic is exactly the copy-paste trap that propagated the
  `hunt_byte` bug. This is a cleanup, not a fix; a **characterization test** locks
  the existing good behavior (`bix w9` warns about B8) rather than a red-green.

Correct paths to mirror (leave as-is, cite in the shared test): `build_byte_series`
(`xanalysis.py:180`), `build_bit_series` (`xanalysis.py:221`),
`_byte_state_buckets` (`decode.py:700`), `_iter_edges` (`investigate.py:375`),
`mappable_data_indices` (`coverage.py:134`), `check_pci_bytes`
(`validate.py:91`).

**Tests:**
- **`hunt_byte` (red-green):** add to `tests/test_xanalysis.py` a multiframe target
  whose only varying byte is the length-low byte at WiCAN B1 (a PCI byte) tracking
  the reference — assert no returned hit's window includes a PCI offset (incl.
  index 1). Confirm it **fails on current code** (offset 1 surfaces) before the
  fix. Mirrors the existing `test_skips_pci_offsets` (which covers
  `build_byte_series` but **not** `hunt_byte` — the gap that let this bug survive).
- **`bix` (characterization):** add to `tests/test_bix_command.py` a lock that
  `bix w9` still warns `B08 … PCI` after the detector swap.

### 1b. Consolidate the two PCI-reinsertion implementations (drift risk)

`wican_bytes.uds_hex_to_wican_bytes()` (`canlib/wican_bytes.py:11`) and
`byteindex.payload_to_wican_bytes()` (`canlib/byteindex.py:322`, via
`payload_to_wican_frame` at `:287`) are **independent implementations of the
identical transform** (ISO-TP hex → WiCAN bytes). They must stay byte-identical
but share no test — a latent drift hazard.

- Keep `byteindex.payload_to_wican_bytes` as the single source of truth (it
  already backs `payload_to_wican_frame`, which carries the ISO-TP↔WiCAN linkage
  the analysis + `bix` need).
- Reimplement `uds_hex_to_wican_bytes` as a thin wrapper that delegates the
  PCI-insertion math to `payload_to_wican_bytes` and then **re-applies the
  multi-frame padding** (its current contract — see the note below), so its output
  is unchanged from today. Keep the name + signature + `canlib/__init__.py:40,80`
  re-export for back-compat. Its live-decode callers are unaffected:
  `decoding.py:35`, `captures.py:466,480`, `modes/status.py:56`,
  `modes/param.py:70`, `modes/ecu.py:65`, `modes/interactive.py:126,157`.
- **Test:** assert `uds_hex_to_wican_bytes(hex)` equals `payload_to_wican_bytes(hex)`
  up to the latter's length, and differs only by trailing `0x00` multi-frame
  padding — across the boundary cases: single-frame (≤7 B, no padding),
  exactly-7-byte, exact-multiple multi-frame (no padding needed), and
  partial-final-frame multi-frame (`>7 B` → padded). The existing
  `test_wican_bytes.py::test_multi_frame_padding` already locks the padded
  contract; extend it with the cross-check against `payload_to_wican_bytes`.

> Note: `uds_hex_to_wican_bytes` right-pads the final consecutive frame with
> `0x00` to a full 7-byte chunk (`wican_bytes.py:54-55`), whereas
> `payload_to_wican_frame` does not pad. This nuance is now documented
> authoritatively in `docs/concepts/wican-byte-index.md:167-171` (the
> reconstruction "zero-pads the trailing consecutive frame" and only *padding
> byte values* — never indices — can differ). Verify whether any caller relies on
> the padding (it affects only trailing bytes past the real payload). If so, make
> the wrapper pad to preserve behavior; the equivalence test must pin this down
> for the exact-boundary and short-final-CF cases before the swap lands.

### 1c. Name the boundary explicitly (contributor clarity)

No behavior change — docstrings/comments only, targeting the "confusing to
contributors" concern:

- `canlib/expression.py:9` — state loudly that the evaluator is
  **byte-space-agnostic** and that *callers must pass WiCAN-layout bytes*; the
  WiCAN indexing is a caller contract, not an evaluator feature.
- `canlib/byteindex.py` module docstring — add a one-paragraph "canonical space
  is ISO-TP; WiCAN is a firmware-specific *view*; convert at the edges" note so
  the intended direction (and Phase 2) is discoverable.
- Fix the two conflation smells: reword `bix.py:203` ("raw CAN frame index" →
  "WiCAN AutoPID frame index (ISO-TP + PCI)"), the identical string in the
  `bix w9` example at `docs/concepts/wican-byte-index.md:251`, and the
  `_decode_plot.py:399` comment. (Boy-Scout; all three are actively misleading.)

### 1d. Docs — mostly done by the user; verify only

The user already landed the doc work this section originally proposed:
`docs/concepts/wican-byte-index.md` (the firmware-grounded WiCAN-index reference),
a "Further reading" link to it from `docs/concepts/byte-indexing.md`, and the
`mkdocs.yml` nav entry. So 1d downgrades from authoring to verification (no
command/flag/default changed → **no README change**):

- Confirm the `bix w9` example in `docs/concepts/wican-byte-index.md:250-258`
  still matches actual `bix` output *after* the 1c reword and the 1a `bix.py:216`
  detector fix (the example shows the `⚠ B08 is a PCI byte` warning — re-run
  `uv run canair bix w9` and diff).
- Sanity-check internal links resolve: `byte-indexing.md` → `wican-byte-index.md`,
  and the existing refs in `docs/bring-your-own-car/06-analyze.md:35` and
  `docs/concepts/ecu-protocols.md:59`.
- Optionally add a one-line pointer from `wican-byte-index.md` to the Phase 2
  direction of travel (switchable notation), if you want the doc to foreshadow it.

## Cross-cutting

- **No CLI/TUI surface change**, no profile/schema change → per AGENTS.md,
  `README.md` and the skills are untouched (confirm, don't assume).
- **Tests:** `uv run pytest tests/test_xanalysis.py tests/test_bix_command.py
  tests/test_byteindex.py tests/test_wican_bytes.py` green; full `uv run pytest`
  green. `uv run canair validate all` unaffected.
- Follow the `contributing` skill for code changes.

## Risks / call-outs

- **The `hunt_byte` fix can change results:** a multiframe hunt window at WiCAN
  offset 1 that previously slipped through is now correctly rejected. This is a
  bugfix, but it means a (wrong) prior hit could disappear — desirable, worth a
  CHANGELOG line.
- **Padding subtlety in 1b** (see the note) is the only real trap; the
  equivalence test is the guardrail.

## Status

**DONE.** Verified 2026-08-04 against the tree — the boxes below were never
ticked during implementation, but every item is in place:

- [x] 1a — fix `hunt_byte` PCI bug (red-green) + unify `bix` detector (characterization); tests
      — `xanalysis.py` now uses `byteindex.wican_to_isotp` as the sole PCI
      detector (`:312`, `:341`, `:478`); `bix` single-frame vs multi-frame PCI
      layout covered by `tests/test_bix_command.py:144-158`.
- [x] 1b — consolidate to one PCI-reinsertion impl; equivalence test
      — `byteindex.payload_to_wican_bytes` (`:439`) is the single canonical
      converter; `commands/decode.py:163` is a thin re-export and
      `autopid_layout.uds_hex_to_wican_bytes` delegates to it and only re-applies
      the multi-frame final-frame zero padding (exactly as the plan specified).
- [x] 1c — docstrings/comments naming the boundary; reword the three conflation smells
      — `byteindex.py:5` carries the "**Which space is canonical:** ISO-TP" note.
- [x] 1d — verify `wican-byte-index.md` example + cross-links post-1a/1c (doc already authored)
- [x] full `uv run pytest` + `uv run canair validate all` green; CHANGELOG line
