# Support bitmaps: detect them, declare them, let `coverage` read them

Status: **PROPOSED**

Several body and climate DIDs on the bundled Ioniq start their data with four constant bytes that
say which of the following bits (or bytes) are real signals. Found by hand-written scripts while
cross-checking the IGPM's DIDs against broadcast CAN data; the evidence is recorded in the
`research:` entries `22BC03-07 support bitmap (B05-B07, B09)` (IGPM),
`22B004-22B00E support bitmap …` (BCM), `22B006 byte map …` (SKM) and `220100 byte map …`
(HVAC).
This plan turns that into tooling.

## The finding

A response is `62 <DID>`, then four map bytes, then data. Bit 7 of map byte *j* marks bit 0 of the
data byte it covers, bit 6 marks bit 1, and so on (MSB first). Two granularities exist:

| Granularity | Map bit means | Where it holds | Fit over all stored captures |
|---|---|---|---|
| per-bit | one data bit is a signal (or the start of a multi-bit field) | IGPM 22BC03-07 | 39 of 40 set bits inside; 19-25 by chance |
| per-bit | same | BCM 22B004-22B00E | 34 of 41 inside; 12-20 by chance (shuffle 99th pct 20) |
| per-byte | one data byte is a signal | HVAC 220100, SKM 22B006, BCM 22B003 | 13/13, 6/6 and 11/12 varying bytes inside |

It is **not** general. The service-21 pages (BMS, VCU, MCU, OBC, AAF) and the chassis 22xxxx DIDs
(ESC, EPS, SCC) have similar-looking leading bytes that do not fit, and BCM 22B005 is a hard
exception: five bits outside its map move and are backed by a deliberate light-stalk test. So the
map is a per-DID property, never a per-make one.

## The gap

- **`coverage` overstates the work.** IGPM 22BC03 reports `BITS B12 … missing{0,1,6,7}`, but its
  map marks only bits 2-5, all already defined, so none of the four is a signal. 22BC04 reports
  `BITS B10 … missing{1,4,5,6,7}`, of which the map marks only 5 and 7. An analyst cannot tell
  real open signals from bits that carry nothing.
- **A definition outside the map goes unnoticed.** `BCM_B004_B12_5` and the 22B005 light-stalk
  bits read unmarked bits; nothing flags that this needs a second look.
- **The map bytes themselves are reported as `UNMAPPED`** data, inviting someone to decode them.
- **Detection was manual.** The scripts that found this lived in a temp directory; the next car's
  analyst would have to rediscover the idea.

## Non-goals

- No make-wide quirk and no automatic application: a map is declared per PID after review.
- No change to how signals decode. The map is metadata about the payload, not an expression input.
- No live-device work. Everything runs over stored captures.

## Design

### Layering

| Module | Role |
|---|---|
| `canlib/analysis/support_map.py` | **Leaf, numpy-free** detection, like `counters.py`/`triage.py`: rows of data bytes (header already stripped) in, scored candidates out. Knows nothing of WiCAN indices, PCI bytes or captures, so the broadcast domain can reuse it if a frame ever carries a map. |
| `canlib/commands/investigate/support_map.py` | Capture-model bridge: builds each PID's ISO-TP payload matrix, runs the leaf, renders human + `--json` output and the paste-ready declaration. |
| `canlib/support_map.py` (or beside `canlib/uds/layout.py`) | Reads a **declaration** from a PID definition and answers "is data bit/byte *n* a signal?" for a given payload. Shared by `coverage`, `bix` and `validate`. |

Offsets are modelled in **ISO-TP data space** (after the SID and DID echo), never WiCAN `Bnn`, so
the PCI byte that splits IGPM's map (`B05 B06 B07 · B09`) cannot leak into the model. Rendering
goes through the existing notation layer (`canlib/uds/notation.py`).

### Detection (`canair investigate <ECU> [<PID>] --support-map`)

A short-circuit view in `investigate`, following the `--counters` precedent (ECU and PID optional,
so it sweeps an ECU or the whole profile). For each PID and each candidate placement (map at data
offset *i*, *k* bytes, covering the *k* bytes that follow — or, per-byte, the next 8·*k* bytes):

1. **Map must be constant** across every row in scope, and not all `00` or all `FF`.
2. **Fit**: count set bits (per-bit) or varying bytes (per-byte) inside vs. outside the map, for
   both bit orders.
3. **Evidence against chance**: log10 probability that the inside hits arise from a map of the
   same density placed at random, plus a shuffled-pairing baseline (pair each data byte with
   another byte's map). The plan's validation numbers come from exactly this test.
4. **Field-start tolerance**: a set bit directly above a marked bit, with no marked bit of its own,
   is reported as a **field continuation**, not a violation. That is what IGPM 22BC06 `B11` (the
   2-bit turn-signal field) and BCM 22B004 `B12:5` look like.
5. **Reject** a candidate with too little evidence (default: fewer than 4 explained set bits or a
   chance above 1 %; both flags) or with any violation that is not a continuation. A rejected PID
   is still listed when asked about directly, with the reason, so 22B005's failure is visible.

Output per accepted candidate: granularity, placement, the decoded list of marked signals, which of
them are defined, moving-but-undefined, always 1, never set, any defined signal outside the map,
and the score. `--json` carries the same, and a paste-ready `pids set-support-map` command.

### Declaration (profile field)

A new optional PID field, authored with a new editor `canair pids set-support-map ECU PID …`
(validated, comment-preserving, auto-reverted, like `set-pid-variable-length`). Sketch:

```yaml
22BC04:
  support_map: {at: 0, length: 4, unit: bit, order: msb}
```

- `at` and `length` are ISO-TP data offsets; the covered data starts right after the map.
- `unit: bit | byte`; `order: msb` is the only order observed, kept explicit so a future car with
  LSB-first maps needs no schema change.
- The map **values** are not stored. They are read from each payload, so a declaration survives an
  ECU software update that adds signals. Coverage warns if the representative payload's map
  differs from the most common one in scope.

Declaring rather than auto-detecting at coverage time keeps `coverage` cheap and deterministic, and
puts a human in front of exceptions like 22B005.

### Consumers

1. **`canair coverage`** — with a declaration:
   - the map bytes get role `MAP` and leave the `UNMAPPED` list;
   - `BITS` (per-bit) or `UNMAPPED` (per-byte) only counts marked positions, and gains a compact
     `NOT SIGNALS` count for the rest;
   - a new `OUTSIDE MAP` finding lists defined signals reading unmarked positions, with field
     continuations shown separately.
   Without a declaration, output stays **byte-identical** (pinned by a test).
2. **`canair bix --annotate --ecu --pid`** — the Role column shows `MAP` for map bytes and marks
   unmarked bits/bytes, so a payload explains itself.
3. **`canair validate pids`** — a non-blocking warning for a signal outside its PID's declared
   map, the same style as the `states` predicate-reference warning. Malformed fields are schema
   errors. A map that runs past the payload needs captures to see, so `coverage` reports it.

## Validation

Golden expectations on the bundled profile, **scoped to a frozen date range** so the test cannot
drift as captures grow (`tests/test_analysis_golden.py` rule):

- IGPM 22BC03, BC04, BC05, BC07 detected per-bit with no violations; BC06 detected with one field
  continuation (`B11:1`).
- BCM 22B004-22B00E detected per-bit, except 22B005 rejected (four violations).
- HVAC 220100 detected per-byte with all 12 defined signals inside; SKM 22B006 and BCM 22B003
  per-byte.
- BMS/VCU/MCU/OBC 21xx pages and ESC/EPS/SCC 22xxxx DIDs **not** detected.

Unit tests on synthetic payloads in the leaf: both orders, both granularities, constant-map and
all-FF rejection, continuation vs violation, the chance score on a tiny matrix. Coverage tests on
literal payloads for each new finding and for unchanged output without a declaration.

## Rollout

Each phase is shippable on its own.

1. Leaf module + `investigate --support-map` (read-only; nothing persisted).
2. Schema field, `pids set-support-map`, and declarations for the bundled Ioniq's IGPM, BCM, HVAC
   and SKM DIDs that pass detection.
3. `coverage` and `bix` consumers.
4. `validate pids` warning.

## Docs

- `docs/concepts/analysis-commands.md`: what a support map is, and when to trust it.
- `docs/bring-your-own-car/07-define-and-verify.md`: the coverage table gains `MAP`,
  `NOT SIGNALS` and `OUTSIDE MAP`; one paragraph on running `--support-map` on a new car.
- The per-PID field reference beside `variable_length`/`response_frames`
  (`canlib/schema/pids_schema.yaml` comments, and wherever the docs list PID fields).
- `AGENTS.md`: one line under `investigate` and `coverage`; the CLI reference regenerates itself.

## Open questions

- **Field widths.** The map marks a field's start bit, not its width. Should a declaration carry
  known widths (e.g. `fields: {B11: 2}`) so coverage can stop calling a continuation bit a gap, or
  is reporting continuations enough?
- **Name.** `support_map` vs `signal_map` vs `validity_map`. The bytes' purpose (supported, valid
  now, or present in this variant) is not known; a neutral name avoids asserting one.
- **Mixed DIDs.** IGPM 22BCFD reads `FF FF FF FF` then non-bit values; is it a per-byte map with
  every byte marked, or a different structure? Detection rejects all-`FF` maps by design, so it
  stays undeclared until something else explains it.
- **Other makes.** Worth checking against another manufacturer's captures before documenting this
  as anything beyond "seen on Hyundai body/climate modules".
- **`--promote`.** Should `investigate --support-map --promote` write the declaration directly, as
  `correlate --promote` does for signals, or keep the separate editor as the only write path?
