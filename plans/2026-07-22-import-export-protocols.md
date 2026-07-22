# Plan: Import/Export protocol support (interop with external CAN formats)

Status: DRAFT — not started. Design/scoping doc; decisions marked **(OPEN)** need
sign-off before implementation.

> **Interpretation of the request.** "Additional protocol support (import and export)"
> is read here as **file / data-interchange formats** — DBC, SavvyCAN/GVRET CSV,
> candump, python-can `.asc`/`.blf`/`.csv`/`.log` — i.e. the interop work from the
> functionality review (`plans/2026-07-22-functionality-review.md` §2–§3) and
> `TODO.md` line 12. It is **not** about new *diagnostic* protocols (J1939, OBD-II
> mode 01, CAN-FD, DoIP). If the intent was diagnostic protocols, stop here and
> re-scope.

## Motivation

canair's niche is the RE knowledge loop, but today it is a closed system: the
`captures/` corpus is a bespoke YAML format, and the passive sniffer
(`commands/sniff.py`) can only *write* raw-CAN logs (via python-can's `can.Logger`) —
nothing reads them back, and nothing exports decoded results to the wider ecosystem.

Two concrete payoffs:

1. **Unblock in-canair broadcast decoding** (the drive-mode/regen research in `TODO.md`,
   and the review's "broadcast decoding in canair" decision). That work needs a way to
   get broadcast CAN frames *in* — from the sniffer or from externally-captured logs —
   and analyze them. Import is the front door.
2. **Make canair's output consumable** by mature tools better suited to broadcast RE and
   plotting — SavvyCAN, comma.ai cabana, cantools, the Wireshark CAN dissector — via
   **DBC export** and **decoded-signal CSV/JSON export**. This is the review's
   "interop, not a new GUI" recommendation.

## The core design insight: two distinct data domains

canair holds **two fundamentally different kinds of data**, and import/export must treat
them separately:

| Domain | Shape | Current storage | Natural interchange formats |
|---|---|---|---|
| **A. UDS/KWP request→response captures** | `(ecu, pid, payload)` diagnostic exchanges | `captures/*.yaml` (existing schema) | decoded-signal **CSV/JSON** (time-series of parameter values); **not** DBC |
| **B. Broadcast CAN frames** | `(arb_id, data[], timestamp)` periodic frames | none (sniffer aggregates in memory, optionally writes a python-can log) | **DBC** (signal defs), candump/`.asc`/`.blf`/`.csv`/GVRET (raw frames) |

Key consequences:

- **DBC is a domain-B format.** DBC describes signals inside broadcast frames by CAN ID.
  It does **not** model UDS request/response. So "DBC export" means exporting **broadcast
  signal definitions**, which presupposes domain B exists in canair (it doesn't yet).
- Domain-A captures export naturally to a **decoded parameter time-series** (CSV/JSON) —
  one row per capture, columns per parameter — not to DBC.
- Raw broadcast logs are **high-volume** and must **not** be exploded into the YAML
  capture schema. They stay in their native/normalised log files on disk; canair indexes
  and decodes them on demand (the SavvyCAN/cantools model).

## Dependencies & what to lean on (don't reinvent)

- **`python-can`** (already a dep, ≥4.6.1) — provides readers *and* writers for
  `.asc`/`.blf`/`.csv`/`.log`(candump)/`.trc` via `can.LogReader` / `can.io.*`. Use these
  for domain-B raw-frame import/export. The sniffer already uses `can.Logger` for writing.
- **`cantools`** (**NEW dep, (OPEN)**) — DBC/KCD/SYM parse + generate, and signal
  encode/decode. Use for DBC import/export and (optionally) to decode broadcast frames.
  Rationale in the review: don't reimplement DBC parsing or linear-signal math.
- **SavvyCAN GVRET CSV** — python-can has no native GVRET reader/writer; add a small
  adapter (~1 file). This is the one format we must hand-roll.

## Proposed data-model additions (domain B — minimal)

This plan needs a *minimal* domain-B footprint; the full broadcast **decoding engine** is
a co-dependency tracked separately (see "Dependencies on other work").

1. **Raw-frame log store:** `profiles/<name>/captures/can/` holding the original or
   normalised log files (keep native `.blf`/`.asc` where possible — lossless, compact),
   plus `captures/can/index.yaml` recording per-file metadata (`source`, `date`, `label`,
   `state`, `notes`, `frame_count`, `id_set`, `bitrate`). Comment-preserving via
   `yaml_rt`. New schema `canlib/schema/can_index_schema.json`.
2. **Broadcast signal definitions:** a new section for domain-B signals, **(OPEN)** either
   - (a) a new top-level `broadcast:` mapping (keyed by CAN ID) inside the per-ECU
     `pids/*.yaml`, or
   - (b) a separate `pids/_broadcast.yaml` / `signals/` file.
   Fields per signal: `name`, `start_bit`, `length`, `byte_order` (big/little), `scale`,
   `offset`, `min`, `max`, `unit`, `verified`. This is a **DBC-compatible linear** model
   (deliberately — see lossiness below), distinct from the freeform WiCAN `expression`
   used for domain-A PIDs.

## CLI surface (proposed)

Two new command modules registered in `canlib/commands/__init__.py:COMMAND_NAMES`
(under a new "interop" group). Note `import` is a Python keyword → module file
`import_.py` with `NAME = "import"`; export module `export.py` with `NAME = "export"`.

```
# EXPORT
canair export dbc [--verified-only] [--ecu ECU...] [-o FILE]
    Broadcast signal defs (domain B) → DBC. cantools builds + dumps the Database.

canair export csv <QUERY> [--param NAME...] [--since/--until/--date] [-o FILE]
canair export json <QUERY> [...]
    Domain-A: decoded parameter time-series across captures → CSV/JSON.
    Reuses the existing query mini-language + decoding.py (values regenerated on the fly).

# IMPORT
canair import can <FILE> [--format auto|asc|blf|csv|log|gvret]
                         [--label --state --notes] [--bitrate N]
    Domain-B raw CAN log → captures/can/ store + index entry. Readers from python-can;
    GVRET via the new adapter. Normalises to .blf by default (lossless, compact).

canair import dbc <FILE> [--ecu ECU] [--ids ID,ID...] [--dry-run]
    DBC → broadcast signal defs. cantools parses; translate linear signals into the
    broadcast: model. --dry-run prints the diff without writing (mirrors `canair pids`).
```

Design notes:
- Follow the existing command contract (`NAME` / `add_parser` / `run`) and the
  `canair pids` **snapshot → edit → validate → rollback** safety pattern for any command
  that writes definitions (`import dbc`).
- Export-to-file is write-only and safe; import-of-defs is mutating → validated + reversible.
- Keep raw-frame import out of the YAML capture schema (store natively; index only).

## Format support matrix (target)

| Format | Domain | Import | Export | Backing library |
|---|---|:--:|:--:|---|
| DBC | B (signal defs) | ✅ | ✅ | cantools |
| candump `.log` | B (frames) | ✅ | ✅ | python-can |
| Vector `.asc` | B (frames) | ✅ | ✅ (exists in sniff) | python-can |
| Vector `.blf` | B (frames) | ✅ | ✅ (exists in sniff) | python-can |
| python-can `.csv` | B (frames) | ✅ | ✅ (exists in sniff) | python-can |
| SavvyCAN GVRET CSV | B (frames) | ✅ | ✅ | **new adapter** |
| Decoded param CSV | A (values) | ➖ n/a | ✅ | stdlib `csv` |
| Decoded param JSON | A (values) | ➖ n/a | ✅ | stdlib `json` |
| KCD / SYM | B | ⛔ (defer) | ⛔ | cantools (later) |

## Lossiness & limitations (must document in `--help`)

- **DBC is linear-only.** DBC signals are `raw * scale + offset` over contiguous bits with
  a single endianness. canair domain-A WiCAN `expression`s are arbitrary arithmetic
  (bit ops, cross-byte math). Therefore:
  - **`export dbc`** only emits domain-B signals (already linear by construction). It does
    **not** try to convert freeform domain-A PID expressions.
  - **`import dbc`** only imports signals expressible in the linear `broadcast:` model
    (all standard DBC signals qualify).
- Raw-frame logs are stored **natively**; re-export may not be byte-identical to the
  original file (timestamps/formatting normalise). The frame data is lossless.
- GVRET CSV variants differ across SavvyCAN versions — the adapter targets the current
  GVRET export columns; guard with tests + a clear error on unrecognised headers.

## Dependencies on other work

- **Broadcast decoding engine (co-dependency).** Import of domain-B frames is only fully
  useful once canair can *decode* them into named signals and feed them to
  `decode`/`captures`/`coverage`. That engine is called out in the review as an
  in-canair effort. This plan delivers the **I/O layer** and the **minimal `broadcast:`
  data model**; the decode/analysis wiring for domain B is a **sibling plan**. Sequence so
  the data model is agreed once and shared.
- Ordering: raw-frame **import** + **DBC import** can land before the full decode engine
  (they populate the store and the signal defs); **`export dbc`** needs the `broadcast:`
  model to exist but not the decoder.

## Implementation stages

1. **Stage 0 — decisions & scaffolding.** Lock the (OPEN) items. Add `cantools` dep. Add
   `captures/can/` + `index.yaml` + schema. Register `export`/`import` command modules
   (stubs + help).
2. **Stage 1 — domain-A export (lowest risk, immediately useful).** `export csv` / `export
   json`: iterate captures via the existing query + `decoding.py`, emit rows. No new data
   model. Ship first.
3. **Stage 2 — domain-B raw-frame import.** `import can` for python-can-supported formats;
   write to `captures/can/` + index; `--format auto` by extension. Wire `canair captures`
   to *list* imported CAN logs (metadata only).
4. **Stage 3 — GVRET adapter.** SavvyCAN GVRET CSV read/write; extend `import can` +
   sniffer `--save`/export path. Tests against sample GVRET files under `tests/fixtures/`.
5. **Stage 4 — `broadcast:` model + DBC import/export.** Add the signal-def model +
   schema + `canair pids`-style safe editing; `import dbc` (cantools → model, `--dry-run`)
   and `export dbc` (model → cantools → file).
6. **Stage 5 — docs.** README interop section, `AGENTS.md` tool notes, examples.

## Testing

- Unit: format round-trips (import→export→import) for candump/asc/blf/csv/GVRET; DBC
  round-trip via cantools; GVRET header parsing incl. malformed-input errors.
- Fixtures: small sample logs (`tests/fixtures/can/`) — a candump snippet, a GVRET CSV, a
  tiny DBC. No hardware needed.
- Schema: `canair validate` extended to cover `captures/can/index.yaml` and the
  `broadcast:` section; add to the existing pytest `validate` coverage.
- Follow repo convention: `uv run canair validate all` clean + full pytest green.

## Out of scope / explicitly delegated

- A capture-viewing **web UI** — use SavvyCAN / cabana (export is the bridge).
- Broadcast-RE **GUI/plotting** beyond canair's existing `decode --plot` — cantools /
  SavvyCAN.
- **DoIP, J1939, CAN-FD, KCD/SYM** — deferred; note as future format rows.
- Converting freeform domain-A expressions **to** DBC — not possible losslessly; won't
  attempt.

## Open questions (need sign-off)

1. **(OPEN)** Add `cantools` as a hard dependency, or make DBC import/export an optional
   extra (`canair[dbc]`) that errors helpfully if missing? (Lean: hard dep — small,
   pure-Python.)
2. **(OPEN)** Broadcast signal-def location: per-ECU `pids/*.yaml` `broadcast:` section vs
   a dedicated `pids/_broadcast.yaml`/`signals/` file.
3. **(OPEN)** Native storage format for imported raw logs: normalise everything to `.blf`
   (compact, lossless) vs keep the original file verbatim + index it.
4. **(OPEN)** CLI shape: dedicated `export`/`import` commands (this proposal) vs folding
   into existing commands (`canair captures --export`, `canair sniff --format gvret`).
5. **(OPEN)** Should `export csv`/`json` live under `canair decode` (value-centric) rather
   than a new `export` command, since it reuses the decode path?
