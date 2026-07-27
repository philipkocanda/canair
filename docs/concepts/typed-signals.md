# Typed (multi-modal) signals

Most vehicle signals are **continuous linear numbers** — a temperature, a speed,
a state of charge. canair's default parameter model captures these with a WiCAN
`expression` (a formula over byte indices) plus an optional `unit`/`min`/`max`,
and the whole analysis suite (`decode`/`correlate`/`hunt`/`investigate`) is built
to reason about them as scalar floats.

But some signals are **not numbers on a line**:

- a **mode / enum** — fan level 1-8, gear P/R/N/D, a climate mode;
- a **flag set / bitmask** — a day-of-week schedule mask, a set of door bits;
- **text** — a part number or serial read as ASCII;
- a **date** — a manufacture or preheat-schedule date;
- a **struct** — a whole record (e.g. schedule = day-mask + hour + minute).

For these, min/max and Pearson correlation are meaningless: the numeric spacing
between `Drive=3` and `Neutral=2` carries no physical meaning. canair models them
with an optional parameter **`type:`** and a small companion map.

## The parallel decoder

The WiCAN `expression` **stays a pure float** — it is what the WiCAN device ships
and what numeric analysis consumes. `type:` attaches a **parallel typed
decoding** on top (`canlib/decode_value.py`): the expression yields the raw
integer, and the typed layer interprets it for display and categorical analysis.
This never changes device output or the numeric path.

| `type:`   | Companion field | Renders as |
|-----------|-----------------|------------|
| `numeric` | *(default)*     | the float value + unit (unchanged) |
| `enum`    | `values:` `{raw: label}` | `fanMAX (45)` |
| `bitmask` | `bits:` `{bit_index: label}` (0=LSB) | `mon\|tue\|sat` |
| `ascii`   | —               | the decoded text |
| `date`    | —               | `2017-06-06` (BCD or binary) |
| `bcd`     | —               | the decoded decimal |
| `struct`  | `fields:` (ordered sub-params) | `{days=tue, hour=7, minute=30}` |

## Defining a typed param

Use `canair pids upsert-param` — never hand-edit `ecus/`:

```bash
# An enum: fan level on HVAC 220100 byte B5
canair pids upsert-param HVAC 220100 HVAC_FAN_LEVEL B5 \
    --type enum --value 0x28=fan1 --value 0x2D=fanMAX --unverified

# A bitmask: preheat schedule day-of-week
canair pids upsert-param BCM 22B00C PREHEAT_DAYS B3 \
    --type bitmask --bit 0=mon --bit 1=tue --bit 2=wed \
    --bit 3=thu --bit 4=fri --bit 5=sat --bit 6=sun
```

`--value`/`--bit` are repeatable `KEY=LABEL` pairs (`KEY` may be decimal or
`0x`-hex). Passing only `--value`/`--bit` infers `--type enum`/`bitmask`.

`struct` params (ordered `fields:`) are defined in the YAML directly today; each
sub-field is itself a typed mini-param over the same payload.

## Analyzing categorical signals

- **`canair decode ECU PID`** renders typed params as their labels, and shows the
  set of distinct decoded values rather than a numeric range.
- **`canair decode ECU PID --discriminate state`** scores an enum/bitmask param
  with **Cramér's V** (nominal association) instead of the interval-scale F —
  answering "which byte separates the power states?" correctly.
- **`canair correlate … --method cramers_v|mutual_info`** (also on `decode
  --corr`) ranks by *categorical* association: "which byte tracks this known
  mode?" — the question Pearson can't answer for a fan/mode/flag byte.
- **`canair investigate ECU PID --events --field NAME`** collapses a typed
  field into **one logical signal** and prints one transition per decoded-value
  change (`fanMAX (45) → fan1 (40)` at a timestamp), instead of scattered
  per-byte edges — ideal for schedule/mode/date fields.

See the [analyze journey](../bring-your-own-car/06-analyze.md) for a worked
example, and the
[write-command workflow](../bring-your-own-car/06-analyze.md#decoding-set-commands-toggle-and-diff)
for reverse-engineering settings the head unit *writes*.
