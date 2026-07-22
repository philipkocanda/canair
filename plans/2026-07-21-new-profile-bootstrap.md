# Plan: Streamline creating a new vehicle profile from scratch

## Goal

A new user should be able to point `canair` at an unknown vehicle and go from
**nothing** to a **working, self-populating profile** with minimal manual YAML
editing. Concretely:

1. `canair profile create <name>` scaffolds a valid profile bundle.
2. `canair discover` **auto-registers** responding ECUs into `ecus.yaml`
   (with placeholder names) instead of only printing/logging them.
3. `canair identity` **fills in missing ECU metadata** (VIN, part numbers,
   versions, dates) by writing decoded DIDs back into `ecus.yaml`.
4. `canair scan` **seeds `pids/`** with `research:` leads (and optionally stub
   PID definitions) for the PIDs/DIDs an ECU actually answers.

The unifying idea: **scanning/discovery is a write path, not just a report.**
Today it is read-only + capture logging.

---

## Current state (what exists today)

- **Profile bundle** = a dir with `pids/`, `ecus.yaml`, `captures/`, `out/`,
  `logs/`. Minimum to be *discoverable*: a `pids/` dir **or** an `ecus.yaml`
  (`canlib/profile.py:72`). Minimum to be *usable*: `pids/_meta.yaml`
  (`car_model` + `init` are schema-required), per-ECU `pids/*.yaml`, and
  `ecus.yaml`.
- **`canair profile`** only has `list` / `show` / `path`
  (`canlib/commands/profile.py`). **No `create`/`init`.** The "no profiles
  found" hint points at `canair profile` as if it could scaffold — it can't.
- **`ecus.yaml`** is keyed by hex TX id (`0x770`), RX is always `TX + 8`
  (`canlib/ecus.py:49`). It is **hand-edited, has no schema, and no writer**.
- **`canair discover`** (`canlib/modes/discover.py`) sweeps TX ids, sends
  `1001`, classifies alive/silent, and *reads* names from `ecus.yaml` — but
  only prints and (with `--save`) appends a `broadcast` capture. It **never
  writes `ecus.yaml`**.
- **`canair scan`** (`canlib/modes/scan.py`) sweeps one service/range on one
  ECU, prints responders, and (with `--save`) writes a **lossy summary**
  capture (`build_scan_session`, truncated ≤80 hex). It **never seeds `pids/`**.
- **`canair identity`** (`canlib/modes/identity.py`) reads 21 standard UDS
  `22 F1xx` identity DIDs (VIN, part numbers, versions, dates) and **only
  prints them**. These fields map 1:1 onto `ecus.yaml` fields that are hand
  transcribed today. No KWP2000 `1Axx` path for powertrain ECUs.
- **`canair pids`** (`canlib/commands/pids.py` → `canlib/pids_edit.py`) can
  surgically `upsert-param` / `add-research` / `set-status`, comment-preserving,
  with re-validate-and-revert (`_safe_write`, `pids_edit.py:845`). But
  `find_ecu_file()` **requires the ECU block to already exist** — there is no
  "create ECU file" helper.
- **Validation assumes ECUs pre-exist**: `canair validate captures` errors if a
  capture's RX address isn't in `ecus.yaml` (`canlib/commands/validate.py:668`).
- **`tx_id` lives in two places** that must agree: `ecus.yaml` (keyed by TX) and
  each `pids/<ecu>.yaml` (`tx_id:` field). Any auto-registration must keep both
  aligned.

### Gap summary

| Capability | Today | Needed |
|---|---|---|
| Scaffold a profile | manual dirs + YAML | `canair profile create` |
| Register an ECU | hand-edit `ecus.yaml` | writer + auto-add from discover |
| `ecus.yaml` schema/validation | none | schema + `validate ecus` |
| Populate ECU metadata | hand transcribe | `identity --write` |
| Seed PID definitions | manual `upsert-param` | `scan --seed` → research leads |
| Create `pids/<ecu>.yaml` | manual | writer helper |

---

## Proposed new-user workflow

```console
# 1. Scaffold (interactive prompts for car_model + init string, or flags)
canair profile create my-car --car-model "VW e-Golf 2019"
canair --profile my-car ...          # or set default_profile

# 2. Find the ECUs on the bus and register them automatically
canair discover --range 700-7EF --register
#   → writes ecus.yaml: 0x7E0 {name: Unknown-7E0}, 0x770 {name: Unknown-770}, ...

# 3. Pull identity data into the registry (names still manual, metadata auto)
canair identity --all --write
#   → fills part_number / sw_version / mfg_date / VIN per ECU in ecus.yaml

# 4. Scan an ECU's PID space and seed research leads into pids/
canair scan --tx 7E0 --service 22 --range F100-F1FF --seed
#   → creates pids/unknown-7e0.yaml (if missing) + research: entries for hits

# 5. Iterate with the existing tooling (decode, captures, pids upsert-param)
```

Everything auto-written should be **idempotent**, **comment-preserving**, and
**revert-on-validation-failure**, matching the existing `pids_edit._safe_write`
and `captures.py` ruamel round-trip patterns.

---

## Implementation plan

### Phase 0 — Foundations (writer + schema for `ecus.yaml`)

This unblocks everything else. `ecus.yaml` currently has no writer and no schema.

- **`canlib/ecus_edit.py`** (new) — a ruamel round-trip editor mirroring
  `canlib/pids_edit.py`:
  - `register_ecu(tx_id, name=None, **fields)` — insert/merge an entry under
    `ecus:` keyed by `0x{tx:03X}`; never clobber existing human-authored fields
    (merge only missing keys unless `overwrite=True`).
  - `set_ecu_fields(tx_id, **fields)` — update metadata (used by identity).
  - `append_scan_log(tx_id, service, range, hits, ...)` — structured writes to
    the existing `scan_log:` section (today hand-written).
  - Reuse the `_safe_write` (write → reparse → validate → revert) pattern.
- **`canlib/schema/ecus_schema.yaml`** (new, tool-owned) — codify the observed
  fields (`name`, `alias`, `description`, `part_number`, `mfg_date`,
  `hw_version`, `sw_version`, `serial`, `id_protocol`, `notes`, …). Only `name`
  required; everything else optional.
- **`canair validate ecus`** — new validator subcommand
  (`canlib/commands/validate.py`), plus fold into `canair validate` (all).
  Cross-check: every `pids/<ecu>.yaml` `tx_id` has a matching `ecus.yaml` key
  and vice-versa (surfaces the two-sources-of-`tx_id` drift).

### Phase 1 — `canair profile create`

- Add `create` (alias `init`) to `canlib/commands/profile.py`.
- Behavior: make `<root>/{pids,captures,out}/`, write `pids/_meta.yaml`
  (`car_model`, `init`; prompt or `--car-model`/`--init`, default init to the
  common `ATSP6`-style string), write an empty but schema-valid `ecus.yaml`
  (`{scan_log: [], ecus: {}}`).
- Target the **user** profiles dir (`~/.config/canair/profiles/<name>`) by
  default so it isn't mixed into the repo; `--path` to override.
- On success, print how to select it (`--profile` / `default_profile`).
- Update the "no profiles found" hint (`canlib/profile.py:129`) to suggest
  `canair profile create`.

### Phase 2 — `canair discover --register`

- `canlib/modes/discover.py` already builds the alive-TX list and enriches with
  `ecu_name()`. Add: for each alive TX **not** already in `ecus.yaml`, call
  `ecus_edit.register_ecu(tx, name=f"Unknown-{tx:03X}", id_protocol=<inferred>)`.
  - Infer `id_protocol` from response (positive `1001` UDS vs NRC vs KWP hint).
  - Record probe outcome via `append_scan_log`.
- New flag `--register` on `canlib/commands/discover.py` (keep default
  print-only for safety; `--register` opts into mutation). Combine cleanly with
  `--save`.
- Print a summary: "Registered 6 new ECUs, 3 already known."

### Phase 3 — `canair identity --write`

- `canlib/modes/identity.py` already decodes the fields `ecus.yaml` wants. Add a
  mapping `DID → ecus.yaml field` (F190→`vin`, F187/F188→`part_number`,
  F189→`sw_version`, F18C→`serial`, F18B→`mfg_date`, …).
- New flag `--write` (and `--all` to iterate every registered ECU): after
  decoding, `ecus_edit.set_ecu_fields(tx, ...)` for the non-empty decoded
  fields, **only filling blanks** unless `--overwrite`.
- Stretch: add a KWP2000 `1A 8x/9x` identity path for powertrain ECUs
  (BMS/VCU/MCU/LDC) that don't answer `22 F1xx` (noted in `ecus.yaml:8`).
- The ECU `name` stays human-curated (identity DIDs give part numbers, not
  friendly names) — but we can suggest a name from `description`/part-number.

### Phase 4 — `canair scan --seed`

- Add a "create ECU pids file" helper to `canlib/pids_edit.py`
  (`ensure_ecu_file(name, tx_id)`): if no `pids/<slug>.yaml` exists, write a
  minimal schema-valid stub (`NAME: {tx_id, pids: {}}`). This removes the
  `find_ecu_file` pre-existence requirement for bootstrapping.
- `canlib/modes/scan.py`: with `--seed`, for each **positive** responder, call
  `pids_edit.add_research_entry()` to record a `decode`/`verify` lead
  (type + target PID + status `captured`), so `canair research` immediately
  surfaces the new work. Keep captures `--save` behavior as-is.
- Optionally `--seed-stub` to also `upsert-param` a placeholder raw-byte
  parameter so `canair coverage`/`decode` have something to chew on.
- Ensure `ecus.yaml` and the new `pids/<ecu>.yaml` `tx_id` are written
  consistently (single code path).

### Phase 5 — Glue: a guided bootstrap command (optional)

- `canair profile bootstrap` — an orchestrator that runs create → discover
  --register → identity --write across a range, with confirmations. This is the
  "one command for new users" entry point; everything under it is the composable
  primitives from Phases 1–4.

---

## Cross-cutting requirements

- **Safety / idempotency**: all writers merge (never clobber human edits unless
  `--overwrite`), re-validate against schema, and revert on failure — reuse
  `pids_edit._safe_write` and the `captures.py` ruamel dumper (YAML 1.1,
  comment-preserving).
- **Dry-run**: `--dry-run` on every mutating command prints the diff instead of
  writing (important for trust on a first run).
- **Keep `tx_id` in sync**: a single registration path writes both `ecus.yaml`
  and the `pids/<ecu>.yaml` stub; `validate ecus` catches drift.
- **Mutation is opt-in**: discover/scan/identity stay read-only by default;
  `--register`/`--seed`/`--write` enable writes. Preserves current muscle memory
  and avoids surprising the user on a live bus.
- **Docs**: update `README.md` "getting started", the `ioniq-reverse-engineering`
  skill, and add a `reverse-engineer-pid`-style "bootstrap a new car" walkthrough.

---

## Open questions

1. **ECU naming**: auto placeholder `Unknown-7E0`, or attempt a lookup table of
   common OBD ECU addresses (e.g. `0x7E0`=ECM, `0x7E2`=TCM)? A small seed table
   would make discovery output far more useful out of the box.
2. **Protocol detection**: how aggressively should discover probe (UDS `1001`
   vs KWP `10C0` vs OBD-II mode 01) to classify `id_protocol` automatically?
3. **Where do created profiles live** — always `~/.config/canair/profiles/`, or
   allow scaffolding into the repo `profiles/` for contribution?
4. **Scope of `--seed`**: research leads only (safe), or also stub params
   (more immediately useful but noisier)?
5. Is an `ecus.yaml` **schema** worth it now, or defer until the writer lands?

---

## Suggested sequencing

Phase 0 (writer + schema) → Phase 1 (create) → Phase 2 (discover --register)
→ Phase 3 (identity --write) → Phase 4 (scan --seed) → Phase 5 (bootstrap glue).

Phases 0–2 alone already deliver the headline ask ("scanning should auto-add
ECUs"); 3–4 deliver "fill in missing information"; 5 is the polished new-user
front door.
