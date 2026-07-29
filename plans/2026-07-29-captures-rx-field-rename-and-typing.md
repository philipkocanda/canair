# Captures: rename `ecu` → `rx` and add on-disk TypedDicts

Date: 2026-07-28

## Motivation

The persisted capture record has a field named `ecu` that actually holds the ECU's
**CAN response address** (RX = request TX + 8, e.g. `"0x7EC"`; or the `"broadcast"`
sentinel for multi-ECU scans). The name `ecu` is misleading in two ways:

1. It reads like it holds an ECU *name/object* (e.g. `"BMS"`), when it holds an
   *address*. In fact the in-memory loader (`load_all_captures()`) builds entry dicts
   whose own `ecu` key **does** hold the resolved short name — so the same word means
   two different things at two layers, which is exactly the confusion this change kills.
2. It doesn't say whether it's the request (TX) or response (RX) address. It is the
   **RX** address (schema description and `profiles/ioniq-2017/captures/AGENTS.md`
   both say "ECU CAN **response** address (RX = request TX + 8)").

Rename the persisted field to **`rx`**. Separately, the captures are plain `dict`
everywhere (no `TypedDict`), despite a clear codebase pattern (`UdsResponse`,
`CoverageEntry`, `_SessionGroup`, `ResultEntry`) and the contributing skill's rule to
type-hint capture/schema records. Add `TypedDict`s for the on-disk capture shapes.

## Scope decisions (confirmed with user)

- **Target name: `rx`** (not `tx`, not `rx_addr`). The field holds the RX/response address.
- **On-disk field only.** Rename the *persisted* `ecu` field. Do **not** rename the
  in-memory `load_all_captures()` entry key `ecu` — that key holds the resolved *short
  name* (`"BMS"`), not an address, so `rx` would be a poor fit, and it has ~30 downstream
  consumers that are deliberately out of scope. Its sibling `ecu_addr` will simply read
  its value from the new `rx` field.
- **Migrate the 20 existing capture files + add a read-time fallback.** Rewrite every
  file's `ecu` → `rx` (capture level *and* `scan_results.responding[]`), and add a
  read fallback (`cap.get("rx") or cap.get("ecu", "")`) so an un-migrated file or a stale
  journal still reads. Belt-and-suspenders for append-only, publicly-shared data.
- **TypedDicts for the on-disk shapes only** (mirroring the schema 1:1). No
  `LoadedCaptureEntry` for the in-memory loader (the larger scope we're not taking).

## Non-goals (deliberately out of scope)

- The in-memory `load_all_captures()` entry key `ecu` (resolved short name) and its
  consumers: `commands/captures.py`, `decode.py`, `correlate.py`, `ecu.py`,
  `_captures_step.py`, `align.py`, `query.py`, `_captures_query.py` (post-load reads at
  L54/L93/L255/L263/L268/L305), `ecu_addr`. These stay `ecu`.
- Command-**output** `ecu` keys (JSON/CSV/timing/routines/iocontrol/identity/scan_state/
  decode `_decode_render`), the ECU registry, the `--ecu` query flags / query
  mini-language `ecu` step, and `ecu_ref` *local variable / parameter* names (they name
  the value, not the on-disk key). All unrelated to the capture data structure.
- Historical `plans/*.md` mentions of the old shape — left as a historical record.

## Current-state verification (2026-07-28, against `c7900ff`)

- Persisted-field **write** sites: `canlib/captures.py` L156, L203, L262, L306, L315
  (the last two in `build_discover_session`: the `broadcast` capture + the
  `responding[].ecu`); `canlib/capture_journal.py` L144 (`append`, JSONL row) + its L18
  docstring; `canlib/commands/import_uds.py` L80 (`_build_capture`).
- Persisted-field **read** sites (direct, pre-resolver): `_captures_query.py` L156
  (`raw_ecu = cap.get("ecu", "")` — the canonical loader, feeds `ecu`/`ecu_addr`),
  `commands/coverage.py` L117, `commands/validate/captures.py` L70 + messages
  L241/L273/L304, `capture_journal.py` L295 (reconcile `build_session_from_records`).
- Schema: `canlib/schema/captures_schema.json` — `capture` def L48–58 (`required`,
  `ecu` property) + `responding_entry` def L88–102 (`ecu` property, `anyOf`, description)
  + L5 top description mention.
- `canlib/schema/can_index_schema.json` has **no** `ecu` field — nothing to change.
- Capture data: **20** files (`profiles/ioniq-2017/captures/*.json` ×19 +
  `profiles/ioniq-5-2022/captures/2026-07-25.json` ×1); all use `"ecu":`; 7 also carry
  `scan_results.responding[].ecu`.
- `capture_migrate.py` is YAML→JSON only and structure-opaque — it will **not** perform a
  field rename; a dedicated migration is required.
- Subcommand wiring: `commands/captures.py::add_parser` registers kinds via
  `kinds.add_parser(...)`; follow `_add_migrate_parser` (L932) as the template for a new
  rename-migration subcommand.

## Plan

### Part A — rename persisted `ecu` → `rx`

1. **Schema** (`canlib/schema/captures_schema.json`):
   - `capture` def: `required: ["ecu","pid"]` → `["rx","pid"]`; rename `ecu` property →
     `rx` (keep the `^(0x[0-9A-Fa-f]{3}|broadcast)$` pattern; reword description).
   - `responding_entry` def: rename `ecu` property → `rx`; update `anyOf`
     (`required: ["ecu"]` → `["rx"]`) and the "keyed by 'did' … or 'ecu'" description.
   - Update the L5 top-level description ("ecu address must resolve" → "rx address …").

2. **Writers** — change the dict-key literals (leave `ecu_ref` variable/param names):
   - `canlib/captures.py`: L156, L203, L262, L306, L315; update docstrings that name the
     `ecu` field.
   - `canlib/capture_journal.py`: L144 write `"rx": ecu_ref`; update the L18 docstring
     JSONL example.
   - `canlib/commands/import_uds.py`: L80 `{"rx": rx_addr_str(tx_id), …}`.

3. **Readers** — introduce a shared read helper with the legacy fallback and use it:
   - Add `capture_rx(cap)` (in `canlib/capture_io.py` or `canlib/captures.py`) returning
     `cap.get("rx") or cap.get("ecu", "")` — the single tolerant read point.
   - Apply at: `_captures_query.py` L156; `coverage.py` L117;
     `validate/captures.py` L70 (+ rename the field in the L75/L241/L273/L304 messages to
     `rx`); `capture_journal.py` L295 (`rec.get("rx") or rec.get("ecu", "")`).

4. **Data migration** for the 20 existing files:
   - Add a dedicated field-rename migration (e.g. `rename_ecu_to_rx()` in a new
     `canlib/capture_field_migrate.py`, or alongside `capture_migrate.py`) that rewrites
     `ecu` → `rx` at the capture level **and** inside `scan_results.responding[]`,
     preserves field order, is round-trip verified, idempotent (no-op if already `rx`),
     and writes atomically via `capture_io.dump_capture_file`.
   - Surface it as a subcommand mirroring `_add_migrate_parser` — e.g.
     `canair captures migrate-rx` (`--dry-run`, `--json`, `--dir`). Confirm the final
     subcommand name at implementation.
   - Run it over both profiles so the tree ships migrated. The read fallback (A3) covers
     any file/branch missed.

### Part B — TypedDicts for on-disk capture shapes

New single-purpose `canlib/capture_types.py` (imported by `captures.py`,
`capture_io.py`, `capture_journal.py`), mirroring the schema 1:1:

- `RespondingEntry` — `did`/`rx` NotRequired, `response`, `notes` NotRequired.
- `ScanResults` — `responding: list[RespondingEntry]`, `rejected`, `notes` (all NotRequired).
- `Quality` — `exchanges: int` + NotRequired error categories (`drop`/`stale`/`no_data`/
  `bus`/`decode`/`other`).
- `CaptureRecord` — `rx: str`, `pid: str | int`, NotRequired
  `payload`/`response`/`scan_results`/`label`/`time`/`notes`.
- `CaptureSession` — `date`/`label` required; NotRequired `vehicle_states`/`notes`/
  `keep_mode`/`transport`/`quality`; `captures: list[CaptureRecord]`.
- `CaptureFile` — `sessions: list[CaptureSession]`.

Annotate: `captures.py` builder return types → `CaptureSession` and built capture dicts →
`CaptureRecord`; `capture_io.load_capture_file`/`dump_capture_file` → `CaptureFile`;
`capture_journal.py` reconcile/build return → `CaptureSession`. Keep to on-disk shapes;
`ty` is CI-enforced, so annotations must type-check clean.

### Part C — docs & tests

Docs (field is user-visible):
- `canlib/schema/captures_schema.json` — Part A1.
- `docs/concepts/captures-and-states.md` L30 (JSON example) + L37–40 (`ecu` bullet) → `rx`.
- `profiles/ioniq-2017/captures/AGENTS.md` L13 (the `ecu` field paragraph) → `rx`.
- `.claude/skills/reverse-engineer-signal/SKILL.md` L253/L257 (describe the stored field) → `rx`.
- Root `AGENTS.md` captures section — note the field is `rx` and mention the new migration.
- `CHANGELOG.md` — `[Unreleased]` bullet: field rename + migration subcommand + TypedDicts.

Tests:
- Update on-disk-shape fixtures/dicts to `rx`: `tests/test_capture_io.py`,
  `tests/test_capture_migrate.py`, `tests/test_capture_provenance.py`, and the raw
  on-disk-dict cases in `tests/test_captures.py` (e.g. L445–447). Leave `_entry(ecu=...)`
  fixtures (they build in-memory loaded entries → short names → out of scope).
- New test: field-rename migration (`ecu` → `rx`, incl. `scan_results.responding[]`;
  round-trip; idempotency).
- New test: read fallback (a capture dict with legacy `ecu` still resolves through
  `load_all_captures`).

### Part D — verification gates

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair validate all
uv run canair captures uds --summary
```
