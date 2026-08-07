# Captured strings are unfindable without already knowing the ECU and PID

Status: **PROPOSED** (2026-08-07) — found while asking "is the VIN / a model code
anywhere in my captures?" and discovering there is no command that can answer it.

**Scope of this change:** a new `canair search` command, string-only, over
diagnostic (domain-A) captures. A `--hex` byte-pattern needle and a `--vin`
shortcut ride along because they are nearly free. Numeric-value search, date/BCD
search, raw-CAN (`search can`) and auto-promotion are **deferred** with the
measurement that defers each — see *Follow-ups*.

## The problem

Every offline command requires a QUERY that names a target: `captures`, `decode`,
`investigate` and `hunt` all start from "which ECU, which PID". So a part number,
an ECU name string, a software version or a VIN is only findable if you already
know which of ~55 identity DIDs it landed in. There is no way to ask the corpus a
question about *content*.

`canair coverage` is the closest thing and does not help — it reports which bytes
are **undecoded**, never what those bytes *say*.

### Measured impact

The data is already captured and already unreadable. On `profiles/ioniq-2017`
(74,197 captures across 22 files, 14 MB), 21 real automotive strings sit in the
corpus right now with no defined signal covering any of them:

```
BCM   22F188  '95400G7470'              BCM   22F18D  '1705310070'
DDM   F187    '93570G2110'              ADM   F187    '93575G2160'
GSA   F187    '46700-G7100'             WPC   F187    '95560G7000'
CCM   F187    '91950G7200'              RCAM  F187    '95760G2000'
BSD-L F191    '95821G2000'              BSD-R F191    '95811G2000'
BMS   1A80    'AEEV__ BMS '             OBC   1A8C    'AEEOBC51'
OBC   1A80    '36400-0E150AEEOES08R0'   VCU   1A8C    'AEVLDC53'
AAF   1A80    'M3062000307 EAEAAFP201'  AVN   F101    'EURSOP'
BCM   22C013  '0x0620'                  MCU   21F2    'EHEL-MS2'
MFC   F100    'AEE MFC  AT EUR LHD 1.00 1.00 95740-G2200 161014'
```

Part numbers, ECU name codes, a region/trim/build string with a build date.
Finding them required a throwaway script, which is the gap.

## Measured findings that shape the design

These four measurements are the load-bearing part of this plan. Each was run
against the bundled profile before any code was designed.

### 1. Performance is a non-issue — do not build an index

```
load_all_captures(ioniq-2017)      0.15 s    74,197 entries
full needle scan (74k payloads)    0.02 s    2,645,529 payload bytes
full printable-run sweep           0.04 s
```

Capture kinds: 74,012 `payload`, 76 `response`, 108 `scan_results`, 1 neither.
51,236 distinct payloads. A linear scan through `capture_store.load_all_captures`
is the whole implementation; no cache, no fingerprint, no `lru_cache`.

### 2. A naive `strings(1)` sweep is useless — the plausibility filter IS the feature

Numeric telemetry bytes land in printable ASCII (0x20–0x7E covers a large share of
plausible sensor values), so an unfiltered scan drowns:

| Filter | Distinct runs | Total hits |
|---|---|---|
| `[ -~]{4,}` (naive) | **1,243** | 14,292 |
| `[ -~]{5,}` | 222 | 3,910 |
| `[ -~]{6,}` | 118 | 2,722 |
| `[ -~]{8,}` | 64 | 2,543 |
| `[ -~]{6,}` + plausibility | **21** | 113 |

The noise is not marginal — it dominates: `'&H&H'` ×6874, `'hOy*iJy(n'` ×2316,
`']??H'` ×65. Pure numeric coincidence.

The filter that works: **≥60 % of characters in `[A-Z0-9]`, and ≤1 character
outside `[A-Z0-9 ._/-]`**. It kept all 21 real strings and dropped every noise run.
This is not a nice-to-have knob; without it the inventory mode ships unusable.

### 3. Grouping collapses the report by 680×

`EHEL-MS2` matches 95 captures but is **one** finding, at **one** offset, on
**one** PID. Keying findings on `(ecu, pid, isotp_offset, string)`:

```
14,292 raw run hits  ->  113 plausible  ->  21 grouped rows
```

The report unit is therefore the *location*, not the capture, carrying a hit count
and a first/last-seen span.

### 4. The emitted expression round-trips exactly

Every offset the sweep found, rendered as a `[Bn:Bm]` WiCAN expression and fed
back through `decode_value.decode_typed({'type': 'ascii', ...})`:

```
OK   BCM   22F188  [B5:B15]    -> '95400G7470'
OK   MFC   F100    [B5:B59]    -> 'AEE MFC  AT EUR LHD 1.00 1.00 95740-G2200 161014'
OK   VCU   1A8C    [B4:B12]    -> 'AEVLDC53'
OK   BCM   22C013  [B10:B15]   -> '0x0620'
OK   MCU   21F2    [B69:B77]   -> 'EHEL-MS2'
```

So each row can carry a **paste-ready `canair pids upsert-param --type ascii`
command**, closing find→define in one step — and that claim is verified against
the real corpus rather than hoped for.

`[B5:B59]` spans 48 data bytes and *looks* non-contiguous because WiCAN
interleaves CF PCI bytes at B08/B16/…; the expression is nonetheless correct,
because `decode_value._data_run` drops exactly those bytes for the byte-run types
(`ascii`/`date`). This is the same property that makes a 17-char VIN expressible
as a single range.

## Design

### Placement: a top-level `uds`/`can` group

`canair search` registered in `_GROUP_DEFAULTS` as `({"uds", "can"}, "uds")`, so a
bare `canair search VIN` means `canair search uds VIN`. Matches the existing
`correlate`/`hunt`/`investigate` spine. Only the `uds` kind ships; the `can` slot
is reserved because raw-frame search needs a genuinely different algorithm (an
8-byte frame splits a 17-char string across consecutive frames — see *Follow-ups*).

Rejected alternatives: `canair captures search` (sits awkwardly beside the `uds`/`can`
domain kinds, and longer to type for the corpus-wide question); `captures uds --grep`
(the QUERY-required design fights "search everything", and buries it in an already
large flag surface).

### One command, two modes

`PATTERN` is optional. With it, `search` finds a needle; without it, `search`
inventories every plausible string it can find. Same haystack, scope, grouping,
limit and JSON machinery — the split mirrors how `hunt --physical` (no reference)
coexists with `hunt --against` (reference) in one command.

The inventory mode is what answers "what version strings are even in here?", which
is the question you cannot ask if you must supply a needle first.

### Layering

Mirrors the `inspect_bytes.py` precedent (pure primitives in a leaf, consumers
import *down*):

```
canlib/textscan.py            NEW leaf — stdlib only, no canlib imports:
                                ascii_fold(bytes) -> str
                                printable_runs(bytes, min_len) -> [(start, str)]
                                is_plausible_token(str) -> bool
                                VIN_RE, looks_redacted        (lifted from pii.py)
                                find_literal / find_regex / find_hex

canlib/commands/search/       NEW package
  __init__.py                   NAME / ALIASES / add_parser, group wiring
  parser.py                     argparse surface only
  uds.py                        capture-store bridge + orchestration
  render.py                     table + JSON emitters

canlib/pii.py                 REFACTOR — import the shared primitives from
                                textscan instead of re-declaring them
```

The `pii.py` refactor is not incidental. `canair search --vin` is functionally the
`canair contribute` pre-flight's VIN scan pointed the other way; two copies of
"looks like a VIN" would silently diverge, and the one that matters is the gate.
`pii.py` imports `.profile`, so it cannot itself be the shared home — `textscan.py`
must be a true leaf.

### CLI surface

```
canair search [uds] [PATTERN] [options]

Match:
  PATTERN            literal substring, case-insensitive (omit -> inventory mode)
  --regex            treat PATTERN as a regular expression
  --hex BYTES        byte-pattern needle instead of a text one
  --vin              shortcut for the ISO-3779 VIN pattern
  --case-sensitive   literal match honours case

Haystack:
  --in {payload,text,all}   default all (payload bytes + labels/notes)
  --defs                    also search ecus/ identity fields (opt-in)
  --query QUERY             narrow by ECU/PID in the shared mini-language

Inventory tuning:
  --min-len N        minimum printable run (default 6)
  --all-runs         disable the plausibility filter (raw strings(1) behaviour)

Output:
  --limit N          cap rows (default 50, 0 = no cap) + loud truncation footer
  --json             object-with-metadata envelope
  --notation NAME    byte-label notation for the offset column
  <scope flags>      --since/--until/--date/--today/--last-session(s)/--state/--label
```

`--query` is a flag rather than a second positional: `PATTERN` owns the positional
slot, and two positionals would be ambiguous.

`--label` collides in *meaning* and must be documented in `--help`: `--label`
**narrows** to sessions/captures whose label contains a substring, while `PATTERN`
**searches** — and by default searches labels too. Not broken, but confusing
undocumented.

### Haystacks

Default is payload bytes + capture/session free text; definitions are opt-in.

| Haystack | Measured count | Handling |
|---|---|---|
| capture `payload` (hex → bytes) | 74,012 | primary. Guard with `captures/query.py::_is_hex_payload` — legacy rows store `"NO DATA"` under `payload` |
| capture `response` (text) | 76 | matched as text, never hex-decoded |
| `scan_results[].responding[].notes` | 108 | holds `"Raw: <hex…>"` **truncated at 80 chars upstream** — report as lossy, never reconstruct bytes from it |
| `session_label` / `session_notes` / `label` / `notes` | 280 / 420 distinct | free text |
| `ecus/*.yaml` `identity:` fields | opt-in `--defs` | where decoded part numbers/serials/VIN already live |

### Offset rendering

Report the run **start** in `--notation`, plus a byte length, plus the canonical
ISO-TP range. Do **not** render a WiCAN run label: `B5-B59` falsely implies
contiguity across the PCI bytes it spans, and the correct rendering
(`formatting._rendered_runs`, as the monitor does — `B5-B7,B9-B15,B17-…`) is
absurd for a 48-byte run. The *expression* `[B5:B59]` stays correct and is what
gets emitted for pasting.

`ByteDisplay` needs a `payload_len`, which varies per capture for a
variable-length PID; use the longest payload in the group (the same choice
`coverage.load_longest_payloads` makes) and note it in the row.

### Overlapping-run collapse

Found while measuring. `MCU 21F2` produces three rows for one string:

```
MCU 21F2 i57  n=63  ' #EHEL-MS2'
MCU 21F2 i59  n=30  'EHEL-MS2'
MCU 21F2 i57  n=2   '-.EHEL-MS2'
```

The leading bytes vary between captures while the tail is stable, so the run
boundary moves. Group by `(ecu, pid)` + overlapping byte region and report the
**stable core** with a variance note, instead of three near-duplicate rows.

### Output contracts

- **`--limit 50` default + a loud, non-TTY-gated truncation footer**, copying
  `captures/listing.py::cmd_list` (:44-92). A corpus-wide search can match far
  more than a QUERY-scoped one, so the context-window discipline matters more here,
  not less.
- **JSON is an object with metadata** — `{query, mode, matched, shown, truncated,
  limit, findings: [...]}` — per `plans/2026-08-06-json-output-convention.md`,
  rather than becoming a 13th incompatible bare-array shape.
- Colour gated on `sys.stdout.isatty()` via a module-local `_use_color`/`_c` pair,
  as `bus.py`/`states.py`/`groups.py`/`bix.py` do — not the unconditional-escape
  style that leaks in 10 of 14 commands today
  (`plans/2026-08-06-ansi-palette-consolidation.md`). If `canlib/ansi.py` lands
  first, use it instead.

## Build stages

Each stage independently green.

0. **Boy Scout: register `align`.** It is missing from `_categories.py::CATEGORIES`
   and `_domain.py::DOMAINS` — the commit that added it touched
   `commands/__init__.py` only. Consequences today: `docs/reference/cli/index.md`
   lists `canair align` under **Other** instead of Analysis, and `align --help`
   carries no `[UDS]` tag. The meta tests do not catch it (they assert only that
   every *mapped* name is registered, not the reverse). Two one-line edits, done
   before adding a third command that would repeat the omission.
1. **`canlib/textscan.py`** + `tests/test_textscan.py`. Pure primitives, no CLI.
2. **Refactor `pii.py`** onto the shared primitives.
3. **`canair search uds` needle mode** — `PATTERN`/`--regex`/`--hex`/`--vin`,
   payload + text haystacks, scope flags, `--query`, `--limit`, `--json`.
4. **Inventory mode** — bare invocation, `--min-len`, `--all-runs`, the
   plausibility filter, grouping + overlap collapse, offset/expression column.
5. **`--defs`** — `ecus/` identity fields.
6. **Registration + docs.**

**Oracle:**

- Stage 1: the counts in *Measured finding 2* become assertions
  (naive vs filtered), plus the 21-string result reproduced against
  `tests/fixtures/profiles/single-frame`.
- Stage 3: `tests/test_search.py`, modelled on `tests/test_align_command.py`
  (real parser, captures written into `tmp_path`, table/`--json` assertions,
  exit-code 1/2 assertions, no-ANSI-when-piped).
- Stage 4: **the round-trip test** — every emitted expression must decode back,
  via `decode_value.decode_typed`, to the string it was found from. This is the
  assertion that makes the paste-ready output trustworthy, and it is the one that
  would catch a WiCAN/ISO-TP off-by-one.
- Stage 6: `tests/test_command_categories.py`, `tests/test_domain_tags.py`,
  `tests/test_domain_kinds.py` (which pins `_GROUP_DEFAULTS`).
- Golden: `tests/fixtures/golden/search-*.txt` via `CANAIR_REGEN_GOLDEN=1`, scoped
  to a frozen date or the fixture profile per
  `test_analysis_golden.py::test_cases_cannot_drift_as_captures_grow` (:254). Any
  view rendering free text must use the fixture profile
  (`test_captures_golden.py::test_free_text_views_use_a_fixture_profile`, :137).
  Note `test_analysis_golden.py::test_goldens_contain_byte_labels` (:293) — the
  offset column means `search`'s goldens fall under it, so they must survive a
  `--notation` sweep rather than hardcoding WiCAN.

## Registration & docs checklist

| File | Change |
|---|---|
| `canlib/commands/__init__.py` | `"search"` in the offline-analysis block of `COMMAND_NAMES` |
| `canlib/commands/_categories.py` | add to the `Analysis` tuple (+ `align`, stage 0) |
| `canlib/commands/_domain.py` | `"search": UDS` (+ `align`, stage 0) |
| `canlib/cli.py` | `_GROUP_DEFAULTS["search"] = ({"uds", "can"}, "uds")` |
| `docs/reference/cli/search.md`, `docs/reference/cli/index.md` | **generated** — `uv run python scripts/gen_cli_reference.py` |
| `mkdocs.yml` | nav entry in the CLI analysis block |
| `docs/concepts/analysis-commands.md` | a row in the `"I have X, I want Y → use Z"` table, plus a line in *The map* — its four quadrants are organised by grain (one PID / many signals) and both assume a *target*, so "no target at all" needs framing |
| `docs/bring-your-own-car/03-identity.md`, `06-analyze.md` | this is an orientation/identity tool as much as an analysis one |
| `README.md` | one row in the Analysis table |
| `AGENTS.md` | one bullet in `## Tools` |

Gates: `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`,
`uv run ty check`, `uv run canair search --help`,
`uv run python scripts/gen_cli_reference.py` then `--check`,
`uv run python scripts/gen_screenshots.py --check`,
`uv run mkdocs build --strict --site-dir /tmp/site`.

## Follow-ups

Each deferred with the measurement that defers it.

### Date / BCD search — do not ship the naive version

A blind 3- and 4-byte `decode_date` sweep over the corpus yields **2,348 distinct
"plausible dates" across 62,378 window hits**, because `decode_value`'s 1990–2099
plausibility window matches a `0x00` BCD year byte constantly:

```
10,714  BCM  22C001  i26  2000-01-01
 5,982  VCU  2101    i5   2000-09-21
 5,106  OBC  2101    i22  2043-10-20
 3,630  MCU  2102    i0   2061-02-07
```

Adding two constraints — **the window must be constant across every capture of
that PID** (a manufacture date never changes; a sensor byte does) and a **2005–2030
year window** — cuts it to **exactly 1 candidate**
(`SKM 22B00A` i8 → `2005-01-08`, itself probably spurious for a 2017 car).

So the mode is viable *only* with both constraints, and its yield on this corpus is
zero. The real dates here are ASCII inside identity strings (`MFC F100`'s trailing
`161014` = 2016-10-14), which the string mode already finds. Recorded so nobody
ships the naive sweep.

### Numeric-value search

`--value 12.6 --width u16 --scale 0.1` — "which byte anywhere in the corpus reads
the value I measured on a meter". Overlaps `hunt`, but is reference-free and
instant, and works for a one-off known reading with no time series behind it.
Wants its own plan alongside `hunt`.

### `search can` (domain B)

Feasible and cheap to stream via `can_logs.iter_frames`, but genuinely a different
algorithm: a broadcast frame carries at most 8 data bytes, so a 17-char VIN is
split across consecutive frames of the same arbitration ID and a useful search must
concatenate per ID before matching. Multi-frame ISO-TP is already reassembled in
domain A, so this problem is domain-B-only. The group registration reserves the slot.

### `--promote NAME`

`hunt`/`correlate` set the precedent for writing the top hit straight into
`ecus/`. Deliberately out of v1: emit the paste-ready `pids upsert-param` command
first and see whether the found strings are worth defining before automating the
write.

## One thing to flag

Making the corpus greppable for VINs is a mild privacy foot-gun. Net posture still
improves: it is the user's own data, `canair contribute` already gates what leaves
the machine, and stage 2 means `search --vin` and the contribute pre-flight share
one definition of "looks like a VIN" instead of two that can drift.

Measured today: **no VIN in the bundled corpus** — `F190` has zero captures and a
`KMHC` needle returns zero hits — so golden tests can pin real output from
`ioniq-2017` without a PII risk (subject to the fixture-profile rule for any view
that renders free text).
