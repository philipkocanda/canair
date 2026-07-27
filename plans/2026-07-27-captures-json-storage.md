# Convert the diagnostic capture store from YAML to JSON

## Why (the measured gap)

Parsing the per-day capture YAML is the dominant cost of nearly every read
command that touches historical data — `ecu`, `coverage`, `decode`,
`correlate`, `hunt`, `investigate`, `captures`, `validate captures`. On the
bundled `ioniq-2017` profile (37,115 records across 18 files, 5.5 MB) the parse
alone is **~726 ms** even with the libyaml `CSafeLoader`.

Measured on the real corpus (same data, re-serialized):

| Format | Parse | vs YAML |
|---|---|---|
| YAML (`CSafeLoader`, current) | 726 ms | baseline |
| **Nested JSON** (same structure, stdlib `json`) | **11 ms** | **66× faster** |
| JSONL (flat, one record/line, stdlib) | 69 ms | 10× faster |

YAML is slow because it does implicit per-scalar type resolution (is `"123"` an
int? is `2026-04-17` a date?) and richer grammar handling; JSON's types are
explicit and stdlib `json` is C-accelerated (~500 MB/s here). The bottleneck is
**parsing**, not I/O — so the storage format is the lever.

This is the root cause behind the `canair ecu --captures` opt-in added earlier:
that opt-in is a workaround for YAML being slow. At 11 ms the count/analysis
re-parse is a non-issue.

## Two facts that make this low-risk

1. **The write/append path already speaks JSON.** The crash-safe write-ahead
   journal (`captures/.journal/*.jsonl`, `canlib/capture_journal.py`) is already
   JSON Lines. Durable day files are **not** appended per-record — they're
   written *wholesale* at reconcile time via `captures.save_session`. So the
   "append cost" argument for JSONL is moot for the durable store; the journal
   already absorbs streaming.
2. **The schema is already format-agnostic.** `canlib/schema/captures_schema.json`
   is JSON Schema (draft 2020-12) applied to the *parsed* dict, so it validates
   JSON-sourced data unchanged.

## Decision: per-day **nested JSON**, not JSONL

Store each day as `captures/YYYY-MM-DD.json` mirroring the **exact current
structure**:

```
{"sessions": [
  {"date": "...", "label": "...", "vehicle_states": [...], "notes": "...",
   "keep_mode": "unique", "captures": [ {ecu,pid,payload,time,...}, ... ]}
]}
```

Rationale:

- **Fastest** (66×) and a **1:1 structural swap** — the loaders/writers/builders
  already produce and consume this `{"sessions": [...]}` dict, so the change is
  "parse/serialize with `json` instead of `ruamel`", not a data-model change.
- **Append cost is irrelevant** for day files (journal handles streaming; day
  files are read-modify-write at reconcile).
- Pretty-printed (`indent=2`, `ensure_ascii=False`, keys in builder order — do
  **not** sort) keeps files **git-diffable and PII-reviewable** (the tree is
  public; review depends on readability).

**JSONL considered and rejected** for the durable store: a flat one-record-per-line
form denormalizes session metadata onto every capture (~4× larger, 69 ms vs
11 ms), and its append/stream advantage is already provided by the journal.
Nested JSON is the cleaner, faster swap. (The loader seam leaves JSONL possible
later if a use-case appears.)

**Comments are lost** moving off YAML. Acceptable: capture files are
machine-written ("never hand-written" — AGENTS.md); the only human-authored
content is the `captures/SCHEMA.yaml` companion doc, which stays as a separate
human doc (renamed `SCHEMA.md`, or kept as-is documenting the JSON shape).

## Scope

**In scope:** the diagnostic capture store — `captures/YYYY-MM-DD.{yaml→json}` and
every reader/writer/validator/test that touches it.

**Out of scope (leave as YAML):**
- `ecus/*.yaml` — hand-curated, comment-rich, small, parse-cold. YAML is correct
  here.
- `profile.yaml`, `states.yaml`, `can_buses.yaml`, `signals/*.yaml` — small,
  human-authored, cold.
- `captures/can/index.yaml` (raw-CAN domain) — tiny index; separate concern.
- The journal (`*.jsonl`) — already JSON; unchanged.

## The write/read seams (where the work is)

Everything funnels through a small number of choke points — the reason this is
tractable:

- **Read (bulk):** `canlib/commands/_captures_query.py::load_all_captures`
  (globs `captures/*.yaml`, `yaml_io.safe_load`). The single hot reader; `align.
  load_signal_captures`, `capture_dates`, `_captures_step`, `ecu`, `decode`,
  `correlate`, `hunt`, `investigate` all go through it.
- **Read (direct):** `canlib/commands/coverage.py`,
  `canlib/commands/validate/captures.py`, and the edit helpers below each open
  files directly.
- **Write (all paths):** `canlib/captures.py::save_session` +
  `_write_captures_file` (via `yaml_rt.dump` / ruamel round-trip). The journal
  reconcile (`capture_journal.reconcile_file`) calls `save_session`, so scan /
  raw / discover / monitor / import-uds all land here.
- **Edit-in-place:** `captures.py::set_capture_note` / `set_session_note` /
  `set_session_keep_mode` / `delete_capture` (each `_yaml().load` → mutate →
  `_write_captures_file`).
- **Schema/validate:** `canlib/schema/captures_schema.json` (unchanged);
  `validate/captures.py` (file read only).
- **Dev scripts:** `scripts/migrate_states_status.py` globs `captures/*.yaml`.

## Clean cutover — no YAML read fallback

**Decided (2026-07-27): no transitional read-both / dual-format loader.** The
loaders read `.json` only. This is simpler (no permanent dual path to carry or
later retire) and there is no need to support both on disk.

Consequence: the code flip and the on-disk migration must land **together** —
there must be no repo state where the loaders read `.json` but the files are
still `.yaml`. So the bundled profiles + test fixtures are converted in the same
stage that flips the readers/writers (Stage 2), keeping the tree green in one
coordinated change rather than across a window.

For **user profiles in the wild**: the loader **fails fast** with a clear,
actionable error when it finds legacy `captures/*.yaml` (and no `.json`) —
pointing at `canair captures migrate` — rather than silently ignoring them or
silently rewriting user data. A one-shot `canair captures migrate` performs the
conversion (with round-trip verification). No silent auto-migrate.

## Stages (each independently shippable + tested)

### Stage 0 — this plan / decision
Lock: nested per-day JSON, `.json` extension, pretty-printed, insertion-order,
`ensure_ascii=False`, **clean cutover (no YAML read fallback)**.

### Stage 1 — migration tooling (no behavior change yet)
- Add `scripts/migrate_captures_to_json.py`: for each discovered profile, for
  each `captures/*.yaml`, load via `yaml_io`, **round-trip-verify** (dump JSON →
  reload → assert structurally equal, catching any YAML→JSON type drift), write
  `YYYY-MM-DD.json`, and remove the `.yaml`. Idempotent; `--dry-run`; per-profile
  summary.
- Add a user-facing `canair captures migrate` subcommand wrapping the same logic
  (non-interactive, `--dry-run`, `--json`) — the supported path for existing
  profiles once the cutover lands.
- Factor a tiny `canlib/capture_io.py` with `load_capture_file(path)` (JSON) +
  `iter_capture_files(dir)` (globs `*.json`) + a `find_legacy_yaml(dir)` helper
  for the fail-fast check. The migrator + all readers use it, so the format lives
  in one seam (mirrors `yaml_io`). This stage only *adds* it; nothing flips yet,
  so the tree stays green on the existing YAML data.

### Stage 2 — the cutover (one coordinated change)
Land these together so the tree is green at the commit boundary:
- **Readers → JSON:** `load_all_captures`, `coverage`, `validate/captures` glob
  `*.json` via `capture_io`. On encountering legacy `*.yaml` capture files with
  no `.json`, raise a clear error naming `canair captures migrate` (fail fast).
- **Writers → JSON:** `captures.save_session` / `_write_captures_file` and the
  four edit helpers (`set_capture_note` / `set_session_note` /
  `set_session_keep_mode` / `delete_capture`) write `YYYY-MM-DD.json` via
  `json.dump(indent=2, ensure_ascii=False)`. Journal reconcile inherits it (calls
  `save_session`).
  - Simplification win: the ruamel round-trip existed to preserve comments; JSON
    drops that, so `_write_captures_file` becomes a plain `json.dump` and the
    `yaml_rt` dependency leaves the capture path.
- **Migrate bundled data:** run the Stage-1 migrator on `profiles/*/captures/`,
  committing the `.json` and deleting the `.yaml`. Confirm `git diff` is
  reviewable and carries no PII regression.
- **Flip fixtures + tests:** every test that writes/reads capture files or globs
  `captures/*.yaml` (`tests/test_monitor.py`, `tests/test_captures*`,
  `tests/test_ecu_list.py` fixtures, `_captures_step`, journal reconcile, validate
  tests). Add:
  - a round-trip equality test (YAML fixture → migrate → JSON → `load_all_captures`
    yields identical records);
  - a fail-fast test (a dir with only legacy `.yaml` raises the migrate error);
  - a fixture proving `.json` is the on-disk format after a save.
- Check `.gitattributes`/`.gitignore` for capture-glob assumptions (the journal
  `.journal/` ignore stays; day files were tracked plain).

### Stage 3 — docs + housekeeping
- `captures/SCHEMA.yaml` → document the JSON shape (rename `SCHEMA.md` or retitle);
  `captures/AGENTS.md`; the root `AGENTS.md` (all `captures/*.yaml` mentions →
  `.json`, "NEVER hand-write" note, `--save`/journal wording, add `captures
  migrate`); `docs/` capture pages; the `reverse-engineer-signal` /
  `ioniq-reverse-engineering` skills.
- `scripts/migrate_states_status.py` capture glob → `.json` (or note it's a
  one-shot dev script).
- Reconsider the `canair ecu --captures` opt-in: with an 11 ms parse the counts
  are cheap again. Decide whether to **keep** opt-in (cleanliness — counts are
  secondary metadata) or **revert to always-on**. Recommend keeping the flag but
  noting it's no longer a perf necessity. (Small follow-up, not blocking.)

## Edge cases / correctness guards

- **Type drift:** the Stage-1 round-trip verifier must assert structural equality
  (payloads/ids stay strings; numbers stay numbers). YAML's implicit typing can
  differ from JSON on odd scalars — catch it at migration, not in production.
- **Deterministic field order:** dump in builder/insertion order (Python dicts
  preserve it), **not** `sort_keys`, so semantic grouping (date/label/… then
  `captures`) and clean diffs are preserved.
- **UTF-8:** `ensure_ascii=False` so non-ASCII notes stay human-readable.
- **Legacy `.yaml` in user profiles:** the loader **fails fast** with a clear
  error naming `canair captures migrate` — never silently ignored, never silently
  rewritten. (No dual-format read path exists.)
- **Atomic writes:** keep the existing write approach; consider temp-file +
  `os.replace` for the day file (small improvement, optional).
- **Cutover atomicity (in-repo):** because there's no read fallback, the reader/
  writer flip, bundled-data migration, and fixture flip must land in the same
  change (Stage 2) so no committed state has readers on `.json` but files on
  `.yaml`.

## Payoff

- `ecu`/`coverage`/`decode`/`correlate`/`hunt`/`investigate`/`captures`/`validate
  captures` all shed the ~0.7 s YAML parse → ~0.01 s. Whole-corpus `correlate`
  and `investigate` (already sped up separately) get another chunk back.
- Removes the need for a capture-count cache/index; makes `--captures` costless.
- Aligns the durable store with the journal (both JSON), one mental model, and
  drops the ruamel round-trip from the capture write path.

## Open questions (resolved)

1. **Extension/layout:** per-day `captures/YYYY-MM-DD.json` (keeps per-day
   locality, smallest diff surface, mirrors today). **Done.**
2. **`SCHEMA.yaml` fate:** renamed to `captures/SCHEMA.md` (a real doc format —
   a pure-comment doc has no place as JSON). **Done.**
3. **`ecu --captures` opt-in:** **kept.** Counts are secondary metadata and the
   flag is clean/documented; parsing is cheap now, so it's no longer a perf
   necessity, but keeping it is harmless and avoids churn.

## Decisions locked

- **Storage:** per-day nested JSON (`captures/YYYY-MM-DD.json`), pretty-printed
  (`indent=2`), insertion-order (no `sort_keys`), `ensure_ascii=False`.
- **No YAML read fallback** — clean cutover; loader fails fast on legacy `.yaml`
  and points at `canair captures migrate` (2026-07-27).
