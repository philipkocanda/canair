# Raw-CAN Broadcast-Frame Support — Import, Data Model & Analysis

Status: **DRAFT — not started.** Design/scoping doc; decisions marked **(OPEN)**
need sign-off before implementation. Frame changes land per-stage, each
golden-gated so the existing diagnostic path stays byte-identical.

One plan for the whole **raw-CAN broadcast domain**: getting broadcast frames
*in* (import), storing/defining them (data model), and — the point of the
exercise — feeding them through the existing `decode` / `correlate` / `hunt` /
`align` / `xanalysis` tools. It **merges** the domain-B portions of
`2026-07-22-import-export-protocols.md` (which is left owning only domain-A
decoded-value export) and realises the "pursue broadcast decoding *in* canair"
decision from `2026-07-22-functionality-review.md` §1.

## Why (the gap)

canair's decode/expression engine applies **only to UDS request/response
payloads**. The bus is also full of **periodic broadcast frames**
(`arb_id, data[], timestamp`) that no diagnostic request elicits — and the open
drive-mode/regen research concludes those signals are **only broadcast on
internal CAN**, never exposed via OBD reads. So canair currently **cannot decode
the exact data the research needs**. This is the single biggest capability gap
(functionality review §1).

**Frame source (decided):** *imported external logs*. The DONE
`2026-07-21-raw-can-backend.md` established that the Ioniq **OBD-II port is
gateway-isolated — passive sniffing sees ~zero broadcast traffic** (only
diagnostic request/response reaches the port). So the realistic near-term source
is `.asc`/`.blf`/candump logs captured elsewhere (SavvyCAN, another tool, a
WiCAN wired to an internal bus, or another vehicle). **Import is the front door**,
not live `sniff`. `canair sniff` remains useful on buses that *do* broadcast, and
its `--save` logs become valid `import can` inputs.

## Test/reference corpus — the `uhi22/Ioniq28Investigations` data

A near-identical **Ioniq 28 kWh** internal-bus dataset exists and is our real
build/validation target (thanks to a WiCAN-less PCAN tap on the internal bus):
<https://github.com/uhi22/Ioniq28Investigations/tree/main/CAN>.

- **`hyundai_Ioniq28Motor.dbc`** — a broadcast **signal-definition** DBC (gear
  `Elect_Gear_Shifter`/`CF_Lvr_Gear`, `Accel_Pedal_Pos`/`Brake_Pedal_Pos`, wheel
  speeds `WHL_SPD*`, EPCU temps `0x523`, batt V/I `0x595`, SoC `0x542`, …) —
  exactly the drive-mode/regen/thermal signals OBD reads can't see. It is the
  Stage-4 **`import dbc`** input, not a hand-authored `signals/` file.
- **`IONIQ_PCAN_drive_fwd_neutral_drive_reverse_neutral.csv`**,
  **`…_leave_car_while_drive_ready_beep.csv`**, **`EPCU_torquePro.csv`** — SavvyCAN
  **GVRET** frame logs (`Time Stamp,ID,Extended,Dir,Bus,LEN,D1..D8`); the first is
  a labelled drive scenario (perfect for `correlate`/`hunt`), and `EPCU_torquePro`
  is **~38 MB** — a real "high-volume, keep native, don't explode into YAML" case.

**How they enter the tree (staged with the code that reads them — NOT dropped in
`signals/` verbatim):**

- **DBC → derived, not committed raw.** Keep the upstream `.dbc` as a reference
  input; Stage 4 `import dbc` (cantools → our linear model) generates
  `profiles/ioniq-2017/signals/*.yaml` with source attribution. We commit *our
  validated model*, not the third-party DBC wholesale (provenance + the
  linear-only mapping is reviewed via `--dry-run`).
- **CSV logs → `captures/can/` via `import can` (Stage 1), once it exists.** These
  are frame *data*, not definitions — they never go in `signals/`. GVRET needs the
  Stage-3 adapter (python-can has no native GVRET reader).
- **Size/provenance discipline:** do **not** commit the 38 MB CSV to git history.
  Options (decide at Stage 0): a **fetch script** pulling into a gitignored
  `references/can/` (like `wican-fw/`), and/or normalise-to-`.blf` under
  `captures/can/` (~10× smaller, lossless). Only **small trimmed slices** go into
  `tests/fixtures/can/` for unit tests (a few hundred frames covering the labelled
  scenario + a handful of IDs). The upstream repo's license must be checked before
  redistributing any of it; prefer fetch-on-demand + attribution over vendoring.
- **First-slice value:** this corpus lets Stages 1–3 be validated end-to-end
  offline (the labelled drive CSV → `correlate` a frame byte against a known VCU
  speed → confirm the DBC's `Elect_Gear_Shifter`/wheel-speed semantics on *our*
  car), and gives Stage 4 a real DBC to import.

## Guiding constraints (contributing skill — "two data domains")

- **Symmetric domains, one tool.** Frames flow through the *same*
  capture-record → analyze → define machinery as diagnostics, not a forked
  half-stack. Capture parity, analysis parity, definition parity.
- **WiCAN is a replaceable transport.** Frame parsing/signal-extraction is its
  own module (mirroring `uds_parse.py`); shared byte/bit/correlation logic stays
  in neutral helpers both domains call.
- **Generalize, don't special-case.** Where diagnostics assumptions leak into
  shared code (a loader assuming ISO-TP `payload` + `pid`), *generalize the
  shared layer* rather than branching on domain.
- Every stage: scriptable + `--json`, non-interactive escape hatches, tests, and
  `uv run canair validate all` + full `uv run pytest` green; docs current.

## The two domains (for reference)

| Domain | Shape | Storage | Signal def |
|---|---|---|---|
| **A. Diagnostics** (today) | `(ecu, pid, ISO-TP payload)` | `captures/*.yaml` | freeform WiCAN `expression` over `Bn` |
| **B. Broadcast frames** (this plan) | `(arb_id, data[], timestamp)` | `captures/can/` native logs + `index.yaml` | linear `signals/` map (DBC-compatible) |

## Data model

### Native frame store — not the YAML capture schema

Raw frames are **high-volume** and must **not** be exploded into `captures/*.yaml`
(whose schema is hard-locked to `ecu`+`pid`+`payload`/`response`/`scan_results`
with `additionalProperties:false`). Instead:

- **`profiles/<name>/captures/can/`** — the imported log files kept **native**
  (`.blf`/`.asc`; lossless, compact).
- **`profiles/<name>/captures/can/index.yaml`** — per-file metadata: `source`,
  `date`, `label`, `state`, `notes`, `frame_count`, `id_set`, `bitrate`.
  Comment-preserving via `yaml_rt`.
- **`canlib/schema/can_index_schema.json`** — new schema; wired into
  `canair validate`.

The analysis loader reads frames from the native logs on demand via
`can.LogReader` (the SavvyCAN/cantools model), not from YAML.

### Broadcast signal definitions — a `signals/` sidecar (decided)

A broadcast signal map is a **DBC-compatible linear** model
(`raw*scale+offset` over contiguous bits, single endianness) — deliberately
distinct from domain-A's freeform WiCAN arithmetic. It lives in a **per-profile
`signals/` sidecar** (e.g. `profiles/<name>/signals/<bus>.yaml`), keyed by
**arbitration ID**, *not* under a per-ECU `broadcast:` section — because a
broadcast ID has no request TX address (RX = TX+8), so forcing every ID under an
ECU key is unnatural. A transmitter ECU may be recorded as an optional
`tx_ecu:` annotation per ID.

Per-signal fields: `name`, `start_bit`, `length`, `byte_order` (big/little),
`scale`, `offset`, `min`, `max`, `unit`, `verified`, `notes`. New schema
`canlib/schema/signals_schema.yaml`; edited via a `canair pids`-style safe writer
(never hand-edited).

## The core: generalize the shared analysis seam (second consumer forces the abstraction)

The `TimePoint`-based analysis **core is already payload-shape-agnostic**
(`join_nearest`, `align_many`, `correlate_matrix`, `lag_scan`, `linear_fit`,
`sniff_unit`, and the `INSPECT_TYPES`/`interpret_bytes` interpretation sweep —
they only see `(dt, value)` and label strings). Only **six edges** couple the
engine to diagnostics:

| # | Coupling | Site |
|---|---|---|
| 1 | `LoadedPid.captures` = payload captures | `align.py:90` |
| 2 | grouping key `(ecu, pid)` | `align.py:116-127` |
| 3 | drops captures without a `payload` field | `align.py:129-130` |
| 4 | `extract_series` runs `payload_to_wican_bytes(cap["payload"])` | `align.py:165` |
| 5 | byte/bit/hunt builders: `cap["payload"]` + `payload_to_wican_bytes` + `wican_to_isotp` PCI-skip | `xanalysis.py:167-180`, `:203-221`, `:386-396` |
| 6 | label convention `ECU:PID:Bn` | `align.py:58`, `xanalysis.py:142/188/228/424` |

**Refactor (driven by the frame consumer):**

- **Signal source abstraction.** Generalize `LoadedPid`/`load_signal_captures`
  to a source keyed by `(ecu, pid)` for diagnostics **or** an **arbitration ID**
  for frames — a `LoadedSignal` with a `space`-tagged identity.
- **Pluggable frame reconstructor.** `extract_series`/`build_*_series` take a
  reconstructor: diagnostic = `payload_to_wican_bytes` (ISO-TP → WiCAN, PCI
  reinserted); raw-CAN = **identity** (the frame's `data` bytes *are* the frame).
- **Pluggable skip-set.** diagnostic = `{i : wican_to_isotp(i) is None}` (PCI);
  raw-CAN = **empty** (no framing bytes).
- **Labels via `ByteRef`.** Frame byte/bit series read `data[k]` directly and
  render through the already-shipped `ByteRef.from_raw_can` → `rN` / `rN.k`
  (no WiCAN/PCI/firmware view). Diagnostic labels unchanged (`Bn`).

Raw frames are **simpler** than diagnostics here (identity byte map, no PCI), so
the generalization is a clarifying refactor, not added complexity. The
scaffolding already exists: `ByteSpace.RAW_CAN`, `ByteRef.from_raw_can`,
`parse_slcan_frame`, `SlcanTcpBus`.

**Golden-gating:** the diagnostic path (default reconstructor
`payload_to_wican_bytes` + WiCAN `Bn` labels) must produce **byte-identical**
output before/after — the same discipline as the byte-notation Phase 2b, on the
same seams. Land per-command, each gated by a before/after output diff.

## Stages

**Stage 0 — decisions & scaffolding.** Lock the (OPEN) items below. Add the
`captures/can/` store + `index.yaml` + `can_index_schema.json` and the `signals/`
sidecar + `signals_schema.yaml`; wire both into `canair validate`. Add `cantools`
dep (for DBC, Stage 4). Register `import` (module `import_.py`, `NAME="import"`)
command stub.

**Stage 1 — `import can` (the missing front door).** `can.LogReader` for
`.asc`/`.blf`/`.csv`/candump → `captures/can/` + an `index.yaml` entry, with
`--label`/`--state`/`--notes`/`--bitrate` and `--format auto`. No frame replay
exists today — this unblocks the whole loop. Teach `canair captures` to *list*
imported CAN logs (metadata only). Tests against tiny fixture logs under
`tests/fixtures/can/` (no hardware).

**Stage 2 — generalize the analysis seam.** The six edges above → pluggable
reconstructor + skip-set + arbitration-ID-keyed `LoadedSignal`. A frame
byte/bit-series builder that reads native logs (via `can.LogReader`, filtered to
one arb ID) into `TimePoint` series. Golden-gate the diagnostic path.

**Stage 3 — frames in the tools.** `correlate` / `hunt` / `align` cross-reference
a broadcast field against a known UDS signal — the "which broadcast byte tracks
speed/regen?" workflow the research needs (a frame series vs an `ECU:PID:PARAM`
anchor, time-aligned). `--notation` already renders `rN` for frame bytes.
`decode` / `coverage` operate against the `signals/` map.

**Stage 4 — definition parity + interop.** The `signals/` linear model +
`canair pids`-style safe editor (`upsert_broadcast_signal`: snapshot → validate →
rollback). `import dbc` (cantools → `signals/`, `--dry-run` diff) and
`export dbc` (`signals/` → cantools → file). Domain-A `export csv`/`export json`
(decoded-value time-series) rides along here — it's the remaining piece of the
old import/export plan.

**Stage 5 — docs.** README interop line (terse, links into `docs/`), a
`docs/bring-your-own-car/` broadcast walkthrough, `AGENTS.md` tool notes, the RE
skills, CHANGELOG.

## Reuse vs. bypass

- **Reuse (in tree):** `parse_slcan_frame`/`format_slcan_frame`/`SlcanTcpBus`
  (transport), `ByteSpace.RAW_CAN` + `ByteRef.from_raw_can` + `rN` render
  (`notation.py`), `INSPECT_TYPES`/`interpret_bytes` (`_decode_plot`), the entire
  `TimePoint` align/correlate/hunt core, `save_session` + note/delete editors
  (shape-agnostic), the metadata prompt/state auto-suggest.
- **Bypass for frames (ISO-TP/UDS-only):** `payload_to_wican_bytes`,
  `framed_to_wican_frame`, `wican_to_isotp` PCI-skipping, `wican_expr`.
- **Needs a frame variant:** `CaptureJournal.append`,
  `build_session_from_records`/`_dedup`, `build_query_session` (all `pid`/`payload`
  shaped) — but frames largely bypass the YAML store, so this is mostly N/A.

## Relationship to the byte-notation work (Phase 2b)

Stage 2 **is** the shared-layer generalization Phase 2b would have performed —
reached from the frame side. After Stage 2, Phase 2b shrinks to an *optional*
cleanup: flip the *diagnostic* side's default reconstructor to ISO-TP-canonical
and retire the synthesized-`"B{bn}"`-expression trick in `build_*_series`. It is
**no longer a prerequisite** for raw-CAN or anything else. (See
`2026-07-24-byte-notation-phase2-isotp-canonical.md` §Phase 2b.)

## Testing

- Fixtures: tiny sample logs under `tests/fixtures/can/` — a candump snippet, a
  GVRET CSV, a small `.blf`/`.asc`, a tiny DBC. Prefer **trimmed slices of the
  `uhi22/Ioniq28Investigations` corpus** (a few hundred frames from the labelled
  drive CSV + a handful of IDs, and a cut-down DBC) over fully-synthetic data, so
  tests exercise real Ioniq-28 frame layouts. No hardware.
- Format round-trips (import→export→import) for candump/asc/blf/csv/GVRET; DBC
  round-trip via cantools.
- **Golden output tests** for every analysis command's diagnostic path
  before/after the Stage 2 seam refactor (prove byte-identical).
- Frame analysis: the labelled `…drive_fwd_neutral_drive_reverse_neutral.csv`
  (and/or a synthetic multi-ID log) where an arb-ID byte ramps → `correlate`/`hunt`
  surface it as `CANID:rN`, cross-referenced against a known VCU/wheel-speed anchor.
- Schema: `canair validate` extended for `captures/can/index.yaml` and `signals/`.

## Privacy note (contributing skill)

Imported logs and DBCs can carry VINs/serials (in payloads) and third-party IP.
Fixtures must be synthetic; `import` must not auto-commit real logs; scrub
before adding any real capture (identity DIDs, labels/notes). Never commit a DBC
that isn't the author's to share.

## Open questions (need sign-off before Stage 0)

1. **(OPEN)** `cantools` as a hard dep vs an optional extra (`canair[dbc]`).
   Lean: hard dep (small, pure-Python) — but DBC is only Stage 4, so it could be
   deferred/optional.
2. **(OPEN)** Native storage: normalise imported logs to `.blf` (compact,
   lossless) vs keep the original file verbatim + index it. Lean: keep verbatim,
   index (simplest, no conversion surprises).
3. **(OPEN)** `signals/` file granularity: one file per bus
   (`signals/<bus>.yaml`) vs one per profile (`signals.yaml`). Lean: per-bus
   (a profile may span powertrain/body/chassis buses).
4. **(OPEN)** Does frame analysis read the native log **live each run** (via
   `can.LogReader`, simplest) or build a cached per-ID time-series index (faster
   for large logs)? Lean: live first; add a cache only if needed.
5. **(OPEN)** `import` / `export` as dedicated commands vs folding into
   `captures`/`sniff`/`decode`. Lean: dedicated `import`/`export` (clear surface).
6. **(OPEN)** Redistribution of the `uhi22/Ioniq28Investigations` corpus: vendor
   trimmed slices into `tests/fixtures/can/` vs a **fetch script** into a
   gitignored `references/can/` (like `wican-fw/`). The upstream repo ships **no
   license file** (owner assessed acceptable to use); note that "no license" is
   technically all-rights-reserved, so the low-risk default remains
   **fetch-on-demand + attribution** rather than vendoring third-party data into
   this public repo. Either way the 38 MB `EPCU_torquePro.csv` must **not** enter
   git history verbatim (size); only tiny trimmed slices as fixtures.

## Absorbed / superseded

This plan absorbs the **domain-B** scope of
`2026-07-22-import-export-protocols.md` (raw-frame import, the `broadcast:`/now
`signals/` model, DBC import/export, `import can`). That plan is left owning only
its **domain-A** decoded-value export (`export csv`/`export json`), cross-linked
here (Stage 4). It realises the broadcast-decoding decision of
`2026-07-22-functionality-review.md` §1 and depends on the DONE transport work in
`2026-07-21-raw-can-backend.md`.

## Status

- [ ] Stage 0 — decisions locked; `captures/can/` + `signals/` stores + schemas + validate
- [ ] Stage 1 — `import can` (LogReader) + index + `captures` listing + fixtures
- [ ] Stage 2 — generalize the analysis seam (pluggable reconstructor/skip-set + arb-ID source); golden-gated
- [ ] Stage 3 — frames in `correlate`/`hunt`/`align`/`decode`/`coverage`
- [ ] Stage 4 — `signals/` editor + `import dbc`/`export dbc`; domain-A `export csv`/`json`
- [ ] Stage 5 — docs / README / AGENTS.md / skills / CHANGELOG
