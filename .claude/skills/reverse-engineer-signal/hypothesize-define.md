# Steps 6–8: Hypothesize, test, define

Scope: forming an expression, testing it without committing, writing it to `ecus/`, and
seeding from third-party definitions. Byte-index and PCI rules are in
`.claude/skills/reverse-engineer-signal/byte-indexing-expressions.md`.

### 6. Hypothesize — form an expression

Hypothesizing is not guessing a byte offset. Reason from *domain knowledge* about what the
signal must physically be, then confirm it in the data. **Load
[`signal-reasoning.md`](signal-reasoning.md) for this step.** It covers the ECU's job, typed
signals, physics/EE, computer science, statistics and thematic grouping. Typed signals (mode,
flag and date bytes) get a `type:` plus `values:`/`bits:` map and categorical stats, never
Pearson (`docs/concepts/typed-signals.md`). Existing `verified` signals can be wrong, so
re-check them.

Cross-reference external signal maps for this car or a close relative (Ioniq: the Kia
Soul/Niro sheets in `profiles/hyundai-ioniq-ae-ev/references/`), and mind the PCI-boundary rule
when a value spans a frame (`byte-indexing-expressions.md`).

### 7. Test the expression WITHOUT committing

`canair decode --try` evaluates a candidate against every capture with no YAML edit (a
bad expression shows `ERROR` rather than hiding):

```bash
canair decode MCU 2102 --try "MOTOR_RPM:RPM=[S10:S11]"           # value range
canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100" --stats    # distribution
canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM   # validate by correlation
canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --state DRIVING
canair align HVAC:2201A2:HVAC_PTC_LOAD HVAC:2201A2:B41 BMS:2101:BATTERY_POWER --state READY
canair decode MCU 2102 --try "T=[S12:S13]/100" --state DRIVING --stats   # scope one drive
canair decode ESC 22C101 --param REAL_SPEED_KMH --state DRIVING --compact --changes-only
```

`hunt`/`correlate` give the *coefficient and fit*; `align` shows the *series* they were
computed from (its first selector sets the row cadence and the rest nearest-join onto
it, so a column reads `—` where a signal wasn't co-polled — a quick way to *see* which
window overlaps; `--csv`/`--json` export a slice). Scope with the step-5 flags so a
candidate is judged on the relevant drive, not the whole history; `--stats --group-by
state` contrasts segments and `--compact --changes-only` collapses stationary runs.
Iterate until the range is physical, the distribution makes sense (constant? enum?
continuous?), and — where a relationship should exist — the correlation confirms it.

**`canair decode <ECU> <PID> --plot`** is the fastest way to *find* a signal when you have no
candidate expression yet. It works on a not-yet-defined PID (raw payloads only). It sweeps byte
offsets × interpretation type × endianness, and shows the **equivalent WiCAN expression** for
the current view (copy it into step 8). It also shows whether the byte is **already mapped** and
warns when a multi-byte read crosses a PCI byte. Press `?` for the keymap.

### 8. Define — write it to ecus/

Use `canair pids` (surgical, comment-preserving, auto-validated and auto-reverted on
schema failure) rather than hand-editing:

```bash
canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" \
    --unit RPM --min -10500 --max 10500 --unverified \
    --source "Kia Soul VMCU CSV" --notes "signed 16-bit BE at B10:B11 (ISO-TP 0x07:0x08)"
```

Found the byte with `canair hunt`? Skip the manual step: `hunt … --promote NAME` writes
the top hit through the same validated path with the r/n, linear fit and unit guess
auto-filled into `notes`. After a write, `upsert-param` echoes the new expression's
decoded **range across existing captures** — a `constant` where you expected variation
usually means the offset landed on a PCI byte.

New signals start **`--unverified` and shipping** (the default): a shipping, unverified
candidate goes into the WiCAN profile with `--include-unverified` and streams live, so it
is easy to test against reality — the whole point of a candidate. Only use `--no-ship` when a
byte is *proven* bogus or redundant and you're keeping it for the research trail
(padding, a counter, an exact mirror of a mapped signal). Write `--notes` as bare facts
(`pid-cleanup` rule 2): byte offset, observed range/per-state values, the evidence.

#### Seeding from third-party definitions (OBDb, OVMS, Torque sheets)

A new profile is often seeded device-free from someone else's definitions. Do it so the result
reads as if each page were written by hand, once:

1. **Convert offsets with the framing in mind.** OBDb `bix` and Torque letters count from the
   first data byte after the DID echo (`src/canair/uds/byteindex.py`); the WiCAN index then depends
   on whether the reply is single- or multi-frame (see `byte-indexing-expressions.md`). Take the
   reply length from a recorded response, never a guess. Multi-byte values that straddle a frame
   boundary need shift-and-or expressions; OBDb's `blsb` means little-endian.
2. **Check every expression against recorded replies before writing anything.** OBDb's
   `test_cases/` directories hold real multi-frame replies with expected values: frame them in the
   WiCAN layout, run `canair.expression.evaluate_expression`, and compare. A signal that
   disagrees on more than a stray corrupt frame or a clamped value is wrong — fix or drop it.
3. **Write each PID's shared fields and note first.** `pids add-pid --status active`, then
   `set-pid-defaults` with `verified: false`, the `source`/`source_links`, and every
   `unit`/`min`/`max` most of its signals share; then `set-pid-notes` with what holds
   for the whole page (where it came from, how it was checked, which recorded replies disagreed,
   a rename table for a block of look-alike signals such as cell voltages).
4. **Then write the signals with only what differs.** A signal states a field only when its value
   differs from the PID's (`""` opts a flag out of an inherited unit). Its note is a fact about
   that signal alone — a no-data value, a bit's meaning, a source that disagrees — or absent.
   Never repeat the source, a description that restates the name, or a byte order the
   expression already shows. Finish with `pids hoist-defaults --dry-run` reporting nothing.
5. **Keep provenance honest.** Definitions for the car itself are `active` and unverified; a DID
   only a sibling car or another project defines is `draft` until this car answers it; sources
   that disagree become a `research:` entry instead of a guess. Take facts (DIDs, offsets,
   scalings) only — never copy a source's recorded payloads into `captures/` unless its licence
   allows it and you know which car (variant) they came from.
