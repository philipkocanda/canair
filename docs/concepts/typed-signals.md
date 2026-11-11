# Typed (multi-modal) signals

Most vehicle signals are **continuous linear numbers**, such as a temperature, a speed or a state
of charge. The default signal model of canair describes these with a WiCAN `expression` (a formula
over byte indices) and an optional `unit`/`min`/`max`. The whole analysis suite
(`decode`/`correlate`/`hunt`/`investigate`) reasons about them as scalar floats.

But some signals are **not numbers on a line**:

- a **mode / enum** — fan level 1-8, gear P/R/N/D, a climate mode
- a **flag set / bitmask** — a day-of-week schedule mask, a set of door bits
- **text** — a part number or serial read as ASCII
- a **date** — a manufacture or preheat-schedule date
- a **struct** — a whole record (for example, schedule = day-mask + hour + minute)

For these signals, min/max and Pearson correlation have no meaning. The numeric spacing between
`Drive=3` and `Neutral=2` carries no physical meaning. canair models them with an optional signal
**`type:`** and a small companion map.

## The parallel decoder

The WiCAN `expression` **stays a pure float**. The WiCAN device ships it, and numeric analysis
consumes it. `type:` attaches a **parallel typed decoding** on top
(`src/canair/expression/decode_value.py`). The expression yields the raw integer, and the typed
layer interprets it for display and categorical analysis. This never changes the device output or
the numeric path.

| `type:`   | Companion field | Renders as |
|-----------|-----------------|------------|
| `numeric` | *(default)*     | the float value + unit (unchanged) |
| `enum`    | `values:` `{raw: label}` | `drive (40)` |
| `bitmask` | `bits:` `{bit_index: label}` (0=LSB) | `rear_left_locked\|rear_right_locked` |
| `ascii`   | —               | the decoded text |
| `date`    | —               | `2017-06-06` (BCD or binary) |
| `bcd`     | —               | the decoded decimal |
| `struct`  | `fields:` (ordered sub-signals) | `{days=tue, hour=7, minute=30}` |

## Defining a typed signal

Use `canair pids upsert-param`:

```bash
# An enum: the gear byte on VCU 2101 (B10 reads 0x21 in Park, 0x28 in Drive)
canair pids upsert-param VCU 2101 GEAR B10 \
    --type enum --value 0x21=park --value 0x22=reverse \
    --value 0x24=neutral --value 0x28=drive --unverified

# A bitmask: the door byte on IGPM 22BC03 (B10; bit 6 has never been set, so stays unlabelled)
canair pids upsert-param IGPM 22BC03 DOOR_STATUS B10 \
    --type bitmask --bit 0=rear_left_open --bit 1=rear_left_locked \
    --bit 2=rear_right_open --bit 3=rear_right_locked --bit 4=passenger_open \
    --bit 5=driver_open --bit 7=trunk_open --unverified
```

The bundled Ioniq profile already decodes both bytes. It decodes VCU `GEAR`. It decodes IGPM B10
bit by bit as `DOOR_*_OPEN`, `DOOR_LOCK_RL`/`DOOR_LOCK_RR` and `TRUNK_OPEN`. These two commands
therefore show the syntax. They do not show a signal that is still to be found. With the rear
doors locked and every door shut, `DOOR_STATUS` would render as
`rear_left_locked|rear_right_locked`.

`--value` and `--bit` are repeatable `KEY=LABEL` pairs. `KEY` may be decimal or `0x`-hex. If you
pass only `--value`, canair infers `--type enum`. If you pass only `--bit`, canair infers
`--type bitmask`.

You currently define `struct` signals (ordered `fields:`) in the YAML directly. Each sub-field is
itself a typed mini-signal over the same payload.

## Byte-run types: `ascii` and `date`

`ascii` and `date` are the two types that read a **run of bytes** and not a scalar. Their
`expression` is a plain range, such as `[B04:B22]`, and not a formula:

```bash
# The VIN: KWP2000 record 1A90 returns SID + record byte + 17 ASCII chars
canair pids upsert-param VCU 1A90 VIN "[B04:B22]" --type ascii --unverified
```

Two consequences follow:

- **The range may cross a frame boundary of the controller area network (CAN).** Byte indices are in
  [WiCAN notation](byte-indexing.md). This notation *includes* the ISO 15765-2 transport protocol
  (ISO-TP) protocol control information (PCI) bytes at `B00`/`B01`, then `B08`, `B16`, … The run
  decoder drops those bytes. `[B04:B22]` therefore yields 17 consecutive *data* bytes, even though
  `B08` and `B16` sit inside it. This is the only way to express a 17-character vehicle
  identification number (VIN).

    For this reason, `canair validate pids` does not raise its "range spans a PCI byte" warning
    for `ascii` and `date`. It still raises the warning for numeric types, which read consecutive
    raw bytes.

- **You may omit a range, but the result is imprecise.** With no `[Bn:Bm]` expression, the
  decoder uses the whole payload minus its Unified Diagnostic Services (UDS) header. It takes the
  header width from the service of the response (`0x5A` → 2 bytes, `0x62` → 3). Prefer an
  explicit range. The fallback can only guess where your data begins.

canair strips trailing `AA`/`00`/`FF` padding. A run that decodes to mostly non-printable bytes
renders as hex and not as mojibake. A wrong offset therefore shows up as obvious garbage and not
as silently plausible text.

## Analyzing categorical signals

- **`canair decode ECU PID`** renders typed signals as their labels. It shows the set of distinct
  decoded values and not a numeric range.
- **`canair decode ECU PID --discriminate state`** scores an enum or bitmask signal with
  **Cramér's V** (nominal association) instead of the interval-scale F. This answers "which byte
  separates the power states?" correctly.
- **`canair correlate … --method cramers_v|mutual_info`** (also on `decode --corr`) ranks by
  *categorical* association. It answers "which byte tracks this known mode?" Pearson correlation
  cannot answer this question for a fan, mode or flag byte.
- **`canair investigate ECU PID --events --field NAME`** collapses a typed field into **one
  logical signal**. It prints one transition per decoded-value change (`park (33) → drive (40)`
  at a timestamp). It does not print scattered per-byte edges. This suits schedule, mode and date
  fields.

See the [analyze journey](../bring-your-own-car/06-analyze.md) for a worked example. See the
[write-command workflow](../bring-your-own-car/06-analyze.md#decoding-set-commands-toggle-and-diff)
for how to reverse-engineer settings that the head unit *writes*.
