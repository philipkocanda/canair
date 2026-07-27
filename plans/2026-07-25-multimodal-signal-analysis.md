# Multi-Modal Signal Analysis — Typed Params, Categorical Stats, Structs & Commands

Status: **Stages 1-4 DONE.** One follow-up deferred by design: `signals upsert`
CLI flags to author typed broadcast signals (`--type`/`--value`/`--bit`) — the
`signals/` *schema* + `decode_value` already support typed fields, so typed
broadcast signals are authored in YAML for now (domain-B raw-CAN isn't observable
on the gateway-isolated bundled car, so this is low-priority).

The analysis suite (`decode`/`correlate`/`hunt`/`investigate`/`discriminate`/
`coverage`) is built end-to-end on one assumption: **every signal is a scalar
`float`**. `evaluate_expression` returns a `float`; every statistic (Pearson,
Spearman, ANOVA-F, mean/median/stdev, linear fit) consumes `list[float]`; the
param schema models only `expression + unit + min + max`. That is correct for
continuous *linear* signals (temperature, speed, SOC, voltage) but structurally
cannot reason about **multi-modal / categorical / structured / string / command**
signals — enum modes, bitmask flags, ASCII text, dates, multi-byte schedule
records, and write commands.

This is not hypothetical. The BCM preheat-schedule work (`22B00C` day-of-week
bitmask, `22B00D` departure time) was hand-pattern-matched, then flagged
**"LIKELY INCORRECT"** in `bcm.yaml` because there was no tool support to define
or verify a bitmask/date field. The HVAC `research:` notes repeatedly hit the
"is this byte the fan enum?" wall — Pearson can't answer a nominal question.

## Goal

Let canair **define, decode, analyze, and verify** categorical/structured
signals, keeping the WiCAN `expression` as the pure-float device ground truth.
Worked example / acceptance test: **isolate HVAC fan level (1-8) and climate
mode enums on `220100`/`220102`**.

## Non-negotiable constraints (from the contributing skill)

- `expression.py` stays a faithful float-only WiCAN-firmware port — its return
  type does **not** change. Typed values come from a **parallel decoder layer**.
- Two data domains stay symmetric: typed decoding works for diagnostic PID
  params (`ecus/`) **and** broadcast signals (`signals/`) via one neutral,
  leaf, dependency-free helper (like `stats.py`).
- All `ecus/`/`signals/` edits go through the surgical, validated
  `canair pids`/`canair signals` editors — never hand-edit.
- Every behavioral change ships tests; `pytest`/`ruff`/`ty`/`validate all` green.
- User-facing changes update `docs/` + `README` + `AGENTS.md` + skills.

## Stages

### Stage 1 — Typed param model + parallel decoder (`decode_value.py`)

- **Schema** (`pids_schema.yaml` `optional_param_fields`; mirror in
  `signals_schema.yaml`): add optional `type`
  (`numeric` default | `enum` | `bitmask` | `ascii` | `date` | `bcd` | `struct`),
  `values` (raw→label map for `enum`), `bits` (bit-index→label for `bitmask`),
  `fields` (ordered sub-fields for `struct`, Stage 3). `expression` stays
  required (yields the raw int the type interprets) — device shipping & numeric
  analysis untouched.
- **`canlib/decode_value.py`** — `decode_typed(param_def, wican_bytes)` →
  `DecodedValue` carrying `.raw: float` plus `.label`/`.text`/`.flags`/`.dt`.
  Reuses `evaluate_expression` for the raw int, then applies the type map. Date
  / BCD / ASCII logic is **lifted out of** `modes/identity_decode.py` into this
  shared module so identity DIDs and analysis use one decoder (de-silos it).
- **Wiring:** `decode`/`captures`/`investigate`/`monitor` render via
  `decode_value` when a param declares `type`; numeric params bypass entirely
  (`.raw` always available → every numeric tool keeps working).
- **Editor + validate:** `canair pids upsert-param --type/--value/--bit`
  (+ `canair signals upsert`); `validate pids` rules (enum keys int, bit indices
  0-63, `values`/`bits` only with matching `type`).

### Stage 2 — Categorical statistics

- **`stats.py`**: `cramers_v` (bias-corrected) + `mutual_information`
  (+ normalized), operating on discrete/binned series; a binning helper for
  low-cardinality bytes.
- **Wiring:** `decode --discriminate` reports Cramér's V vs `state` for
  enum/bitmask/low-cardinality bytes (F assumes interval scale); `correlate` /
  `decode --corr` gain `--method cramers_v|mutual_info`.

### Stage 3 — Struct decode + structured event timeline

- `struct` type decoded into an ordered set of labeled sub-values (models a
  day-bitmask + hour + minute schedule record as ONE field).
- `investigate --events --field ECU:PID:NAME` collapses a declared
  struct/byte-range into one logical transition (`{Mon 08:00}→{Tue 07:30}`)
  instead of scattered per-byte edges.

### Stage 4 — Command / write-signal capture workflow (docs + glue)

- Document the **toggle → re-read → diff** loop (change setting → re-read
  storing DID → `captures uds --diff` + `decode --try`) with typed decoding
  making the result interpretable. Broadcast writes route through the raw-CAN
  domain (`signals/`, `import can`, `correlate can`); the typed model applies to
  `signals/` too. No new capture path — reuse the journaled `--save` machinery.

## Risks

- Low-cardinality auto-detect needs a tunable threshold so continuous bytes
  aren't mislabeled categorical.
- `signals_schema.yaml` parity must land with `pids_schema.yaml` or the domains
  diverge.
- Lifting date/BCD/ASCII out of `identity_decode.py` must preserve
  `canair identity` output (regression test).
