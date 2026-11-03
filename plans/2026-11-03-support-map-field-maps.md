# Support maps, second pass: field maps, map variants and confirmed absence

Status: **DONE** (phases 0-3; see "As built" for where it differs from the design)

Follow-up to `plans/2026-10-29-support-bitmap-coverage.md`. That plan built detection for the
constant leading bytes some Hyundai DIDs use to mark which data are signals, in two
granularities: per bit (IGPM, BCM) and per byte (HVAC). This plan records what a sweep of
every stored capture shows beyond that, and proposes the tooling it calls for.

## Findings

Every profile was swept with `investigate --support-map --map-all`, and the leading bytes and
field layouts of each DID were checked against the defined signals. Ioniq AE (37 capture days),
Kona OS (8 sessions from two cars, imported from public logs), Nissan Leaf ZE1 (47 PIDs) and
XPeng G6 (10 PIDs). The Ioniq 5 and Niro profiles hold no timed captures.

### 1. A third granularity: one map bit per field

The service-21 pages, and their service-22 successors on the Kona, also lead with a map. Each
bit marks one *field* of the page, in order, MSB first, whatever the field's width. Read per
byte, any multi-byte field before the end pushes the later live bytes "outside" the map, which
is why the 2026-10-29 detector rejects them all.

| DID | Map | Set bits | Fields in the known layout |
|---|---|---|---|
| Ioniq BMS `2101` | `FF FF FF FF` | 32 | 32, SOC through isolation resistance, ending at the last byte |
| Ioniq BMS `2102`-`2104`, Kona BMS `220102`-`220104` | `FF FF FF FF` | 32 | 32 one-byte cell voltages |
| Ioniq VCU `2101` | `FF E0 00 00` | 11 | 11 over 16 bytes (five of them 2-byte) |
| Kona VMCU `2101` | `FF F8 00 00` | 13 | the Ioniq's 11, plus two 1-byte fields in a payload 2 bytes longer |
| Kona BMS `220101` | `FF F7 E7 FF` | 29 | 32; the clear bits are fields 12, 19, 20 (below) |
| Ioniq/Kona CLU `22B002` | `E0 00 00 00` | 3 | `d4`, supply voltage, the 3-byte odometer |
| Ioniq OBC `2102` | `FF FC 00 00` | 14 | 28 constant bytes, read as 14 words (nothing moves, so untested) |

The Kona BMS row is the strongest single piece of evidence. Its three clear bits are field 12
(a fifth module temperature, constant `00`) and fields 19 and 20 (battery fan status and fan
feedback, both constant `00`). The Ioniq's air-cooled pack has a fan and sets all 32 bits; the
Kona's pack is liquid-cooled. So an unmarked field is one this unit does not have, and its bytes
hold fill.

The existing granularities are special cases of the same idea. Per byte is per field with every
field one byte wide (HVAC `220100`'s unmarked bytes are all `FF`, Kona HVAC `220102`'s too). Per
bit is positional rather than ordinal, and its "field continuation" is a multi-bit field whose
start bit is marked.

Not explained yet:

- MCU `2101` `00 00 03 FF` and MCU `2102` `07 FF FF FF`: the set bits sit at the LSB end, which
  fits an LSB-first count of 10 and 27 fields but is untested.
- VCU `2102` `F8 F8 00 00` (two runs of five): its only constant bytes, `d6`-`d8`, come right
  after two fields, not five, so the field reading fails as it stands. ESC `22C101`
  `5B D7 E7 60` was not examined.
- BCM `22B003` per byte: one live byte (`d13`, mirroring `d9`) falls outside.

### 2. What a field map gives an analyst

- **The number of fields.** *N* set bits over *W* data bytes means *W − N* bytes belong to
  multi-byte fields. For Ioniq VCU `2101` that is 16 − 11 = 5, and the page indeed has five
  2-byte fields (two accelerator sensor voltages, pedal depth, speed, supply voltage). That is a
  hard constraint on how a page splits into signals, before any correlation.
- **Which bytes are fill.** An unmarked field's bytes are fill, not a signal that never moved.
- **What differs between cars.** Comparing two cars' maps names the fields one lacks, as the
  Kona fan bits do.

### 3. Maps are fixed per unit, not per moment

No constant lead changed within any session on any profile. The maps are a property of the
module software, shared across models: Kona and Ioniq carry identical maps for HVAC `220100`
(`7E 50 07 C8`), IGPM `22BC03` (`FD EE 3C 73`), IGPM `22BC06` (`B4 80 00 00`) and BCM `22C00B`
(`FF FF 00 00`).

The one map that differs is Kona CLU `22B002`: `E0` on the 2021 car and `60` on the 2019 car,
and on the 2019 car the byte of the field it leaves unmarked reads `00`. So a map differs
between units (fitment or software), never over time. This answers the first plan's naming
question: the bytes mean *supported on this unit*, not *currently valid*.

The detector treats the two CLU maps as noise. It keeps rows carrying the modal map only when
98 % of rows share it, so two cars' maps (41 and 77 rows) yield "no map".

### 4. Bit order

MSB first holds. Read LSB first, five of the seven declared per-bit maps gain violations (IGPM
`22BC05`/`06`, BCM `22B004`/`07`, and `22BC03` gains one), and the other two lose evidence.

### 5. Confirmed absence

Of the Ioniq's 119 PIDs with timed captures, 9 have leading bytes that vary (HVAC
`2201A0`-`A6`, EPS `220101`, CCM `22C350`). No map can sit there, so their first data bytes are
signals. The rest of the no-map cases cannot be told apart from captures alone: constant `00` (7;
an empty map or zero data), constant `FF` (7; a full map or fill, which is exactly the field-map
case above), constant with nothing moving (8), too few captures (50).

### 6. Detector defects

- **A stub beat the whole map.** On Kona IGPM `22BC03` the 3-byte placement, which reads the
  fourth map byte as data and has a violation, scored a hair better than the consistent 4-byte
  one and was reported `REJECTED`. Fixed (phase 0).
- **Absence was opaque.** Every no-fit case printed "no constant leading map found". Fixed
  (phase 0).
- **Variants are dropped**, as above (phase 2).
- **Ioniq HVAC `220102`** is reported `REJECTED` per bit, but per byte its map `FF 80` marks all 9
  covered bytes, which is the 9-field reading. It is consistent and carries no evidence, but
  `best_fit` prefers the violating per-bit fit because units are not compared (deliberately: a
  per-byte reading excuses any stray bit of a real per-bit map, as BCM `22B005` showed).

### 7. Other makes

Nothing on the Nissan Leaf or the XPeng G6. Seen only on Hyundai so far (Ioniq AE, Kona OS).
The Kia Niro profile would be the next test once it has captures.

### 8. What Hyundai's diagnostics spec says

Hyundai/Kia's own diagnostics standard (a UDS-on-CAN engineering specification; not
redistributable, so it is paraphrased here and kept out of the tree) defines this mechanism
directly:

- A record identifier's parameters are numbered from 1. Parameter 0 is a 4-byte "supported
  parameters" bitmap: the first byte's bit 7 is parameter 1, the fourth byte's bit 0 is
  parameter 32, and a set bit means this ECU supports that parameter. It is the OBD-II "PIDs
  supported" idea applied inside one record, and its example record mixes 1-byte values,
  multi-byte values and single-bit flags after the bitmap.
- A record with more than 32 parameters carries another such bitmap as parameter 32, 64, and so
  on, each covering the next 31 parameters. The maps are spaced by parameter count, not by
  bytes. Ioniq BMS `21F2` bears this out: constant `FF FF FF FF` blocks sit at data offsets 0,
  48 and 110, with signals between them.
- The IOControl enable mask follows the same per-parameter, MSB-first order, with one rule for
  bitmapped identifiers: every bit position keeps its own mask bit, supported or not. That is the
  per-bit layout, and it is why a 2-bit field shows its mark on its first bit only.
- The ranges reserved for these record identifiers include `0100-A5FF`, `B000-B1FF` and
  `C000-C2FF`, which hold every Ioniq DID with a map.

So the three granularities are one rule: a bit per parameter, in order. The per-bit DIDs are
the bitmapped case, the per-byte DIDs have only 1-byte parameters, and the service-21 pages have
mixed widths. The spec gives the counting rule, not any record's layout, so widths still come
from the defined signals.

The MCU's `00 00 03 FF` and `07 FF FF FF` contradict the spec's order (the first 22 parameters
would be unsupported while their bytes are live), so that supplier may count from the other end.

## Phase 0 (done)

- `best_fit` sets a fit with violations aside when a violation-free fit of the same unit
  explains at least as many moving bits (`canlib/analysis/support_map.py`).
- `absence_reason` classifies a no-fit PID (`few-rows`, `varies`, `variants`, `empty`, `full`,
  `still`). `investigate --support-map` prints the reason per PID, carries it in `--json`
  (`absence`, `absence_text`), and a sweep's header counts each reason.
- `canair monitor` and `canair read` draw a declared map's bytes in a muted grey-blue
  (`formatting.MAP_BYTE_STYLE`; a signal's colour still wins, and a changed map byte still
  flashes). The monitor's `?` help gains a colour legend (`HelpMixin.HELP_LEGEND`, built from
  `formatting.BYTE_COLOUR_LEGEND` and the monitor's marker glyphs), which any other TUI can
  reuse.

## Design

### Phase 1: `unit: field`

- **Detection.** For a constant lead with *N* set bits and *W* covered bytes, segment the
  covered bytes into fields from the defined signals (a signal's byte span is one field; signals
  reading the same bytes merge), with each unclaimed byte a field of unknown width. Report:
  - *exact* when the defined fields plus the unclaimed bytes add up to *N*;
  - *consistent* when they add up to at least *N*, with *W − N* bytes still to join;
  - *broken* when even fully joined there are fewer fields than *N*.

  Every unmarked field must be constant. No probability score: the count is the test, so the
  verdicts differ from the per-bit ones and are not mixed with them.
- **Declaration.** `support_map: {at, length, unit: field, order: msb}`, widths taken from the
  defined signals at read time rather than stored, so the declaration does not go stale as the
  page gets decoded. Open: whether an unclaimed run needs a declared width at all.
- **Consumers.** `coverage` reports the field count against the defined fields (`FIELDS 9 of
  11`), marks the bytes of an unmarked field as fill (no `UNMAPPED`), and flags a signal that
  spans two fields. `bix --annotate` numbers the fields.
- **Order.** `order: lsb` becomes a real option if MCU `2101`/`2102` bear it out.

### Phase 2: map variants

Group rows by their lead before fitting, so each car's or session's map is fitted on its own
rows, and report the variants and the fields one has that the other lacks. A declaration
already reads the mask off each payload, so a declared PID needs no change. Tie-in to
`plans/2026-10-30-profile-variants-and-rename.md`: a map difference is evidence for a variant or
an ECU revision, and could become a revision fingerprint.

### Phase 3: smaller follow-ups

- `captures uds --step` and `--diff` colour map bytes too (they need the PID definition, not
  only its signals).
- Should a confirmed absence be declarable (`support_map: none`)? It is re-derivable from
  captures, and nothing would read it today. Proposed answer: no; record it as a `research:`
  result when it matters.

## As built

- **Solver** (`canlib/analysis/support_fields.py`). A search over every split of the covered
  bytes, memoised per (byte, parameter, bitmap) state, counting splits up to two. The rules: a
  defined field is never cut or shared; an undefined run forms parameters of 1-4 bytes and may
  widen a defined neighbour up to 4; an unmarked parameter's bytes are constant; no set bit may
  name a parameter past the end; parameter 32 of a block either ends the record or is the next
  4-byte constant bitmap (which its own bit must mark). It reports the fewest and most data
  parameters over all splits, the bytes that are fill on every split, every bitmap any split
  places, and the split itself when it is unique.
- **Defined fields.** `known_fields` drops a signal reading non-contiguous bytes, or a run its
  narrower neighbours already cover (`BATTERY_POWER`, `OBC_EFFICIENCY`), and merges the rest
  where they overlap. A byte of flags is one parameter: that matched VCU `2101`; the spec's
  example allows one parameter per flag, which this does not model.
- **Strict trailing bits.** A set bit past the record's end breaks the split. That is what
  gives an all-`FF` map its meaning, and it is why the BMS `21F2` block at data+110 is read as
  data: as a bitmap it would need 32 more parameters in 7 bytes.
- **Order.** MSB first is kept whenever it fits; LSB first is reported only where MSB first is
  broken (Ioniq MCU `2101`/`2102`, Kona BMS `220105`). `order: lsb` is accepted on field maps
  only. No LSB map is declared: the MCU reading is consistent, not exact.
- **When the field reading runs.** On a constant non-zero 4-byte lead followed by more than 4
  bytes (4 or fewer is the bitmapped layout). Precedence: `strong`/`corroborated` per-bit or
  per-byte fits keep the headline; an exact split beats anything else; a consistent one
  replaces only a rejected fit or none. A broken field reading is a one-line note, except
  under a per-bit fit, where it would only be noise.
- **Sweep noise.** A consistent split whose signals read under half the bytes is hidden in a
  sweep unless it pins fill or a further bitmap (`--map-all` shows it).
- **Variants.** When the absence reason is `variants`, rows are grouped by their 4-byte lead and
  each group of 20+ rows is fitted on its own; the report lists the map bits that differ.
- **Coverage** splits the one representative payload, so constancy cannot be tested and only a
  unique split settles fill and `OUTSIDE MAP`. Lines: `FIELDS m marked of n, d defined`,
  `FIELDS BROKEN`, `OPEN FIELDS`.
- **bix** labels every declared bitmap's bytes but does not number the parameters.
- **Capture views.** `captures.store.PidDefs` became a named tuple carrying the PID's map
  bytes, so `captures uds --step` and `--diff` colour them like the monitor.
- **Declared:** Ioniq BMS `2101`-`2104`, VCU `2101`, BCM `22C00B`, HVAC `220102`; Kona BMS
  `220101`-`220104`, BCM `22C00B`. Left for review: Ioniq OBC `2101`, BMS `2105`, `21F2`, CLU
  `22B002`, MCU `2101`/`2102` (all consistent, not exact).

## Validation

Golden expectations stay frozen-range (`tests/test_analysis_golden.py`):
`investigate-support-map-fields` (the Ioniq VCU sweep: `2101` exact, `2102` and `21F2`
broken), and `coverage-multi-frame` now shows BMS `2101`'s declared field map. The solver's
unit tests use synthetic pages shaped like VCU `2101` (exact, one open parameter), Kona BMS
`220101` (fill, and a moving unmarked parameter that breaks it), BMS `21F2` (a second bitmap
at parameter 32) and an LSB-first map; the bridge tests cover precedence, `--json`, the declare
command and a two-variant page shaped like Kona CLU `22B002`.
