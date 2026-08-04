# De-hardcode remaining HKMC/Ioniq bias (scan ranges + analysis tooling)

Status: **DONE** — Stages 1–5 shipped (Stage 5.2 resolved as no-change; the SKM
relay-wake stage was dropped as already-shipped, see the Scope note below).
Verified 2026-08-04 against the tree:

- **Stage 1** — `iocontrol_scan_ranges:` in `pids_schema.yaml` +
  `_validate_iocontrol_scan_ranges` (`validate/pids.py:226`);
  `infer_iocontrol_ranges` in `scan_presets.py`; `DEFAULT_ECU_RANGES` gone from
  `modes/iocontrol_scan.py` (runtime resolution at `:173`); preset
  `default_range="0000-FFFF"`; `canair pids set-iocontrol-ranges` shipped and the
  Ioniq ranges migrated into `ecus/igpm.yaml` / `bcm.yaml` / `hvac.yaml` /
  `psm.yaml`.
- **Stage 2** — `canlib/unit_guess.py` (`DEFAULT_UNIT_CANDIDATES` +
  `resolve_unit_candidates`), threaded at `commands/hunt.py:507`;
  `_validate_unit_guess_candidates`; `tests/test_unit_guess.py`.
- **Stage 3** — `POWER_STATES = ("SLEEP", "ACC", "RUN", "CRANK")`
  (`states.py:69`); static `CLI_STATE_CHOICES` gone from `pids.py`/`research.py`
  (now `type=str.upper` + profile-aware validation at write time).
- **Stage 4** — `QUIRK_GATED_DIDS = {"F187": HK_F1XX_MINUS_ONE}`
  (`modes/identity_records.py:47`), `(HK)` dropped from the F187 label, gating
  covered by `tests/test_identity.py::test_f187_skipped_without_quirk`.
- **Stage 5** — `discover --range` now `default=None` with `_live.py` owning the
  real default; `response_id()` confirmed not orphaned (kept, per the inline
  outcome note).

**Residual (not blocking, Boy-Scout):** Stage 1 item 6 listed a
`tests/test_validate_pids.py` case for `iocontrol_scan_ranges` — the validator
exists and is exercised via `validate all`, but there is no dedicated unit test
for it. The **Future work** section at the end is still open (tracked
elsewhere).

## Goal

Remove the last raw Hyundai/Kia/HKMC assumptions still compiled into otherwise
vehicle-agnostic **tool code**, so a fresh profile for any make drives canair
correctly without inheriting Ioniq body-controller DID maps, HKMC-flavored scan
defaults, EV-only state vocabulary, or Hyundai-tuned analysis heuristics. The
bundled `ioniq-2017` profile must behave **identically** at every stage (the HK
behavior moves into its profile data / a declared capability, it is not lost).

This is a direct follow-up to `plans/2026-07-28-multi-vehicle-support.md` and
reuses that effort's established pattern: make-specific behavior is declared by
the profile (`quirks:` / new per-ECU or profile fields) and shared code gates on
it or resolves from it, rather than baking a make assumption into the tool.

**Scope note (2026-08-02 revision):** the SKM relay-wake stage from the original
draft is **dropped** — that gating already shipped (`SKM_WAKEUP` is in
`canlib/quirks.py::KNOWN_QUIRKS`; the `skm-wake` dispatch is gated in
`canlib/modes/multi.py`, and `profiles/ioniq-2017/profile.yaml` declares the
capability). In its place this plan now folds in the **analysis-tooling** HK bias
that the original draft had parked in "Future work": the unit-guess candidate
list, the EV-assuming state vocabulary, and the HK-flavored identity/DTC
heuristics. Those are the pieces most relevant to "de-hyundai-ify the analysis
tooling".

## Background — findings (2026-07-29 audit, refreshed 2026-08-02)

The codebase is largely well-factored: addressing, the `hk_f1xx_minus_one`
quirk, the `skm_wakeup` capability, ISO-TP padding, the CAN-bus vocabulary, the
physical-band scan, and the states vocabulary are all profile-gated/resolved.
The remaining raw hardcodes, by area:

**Scan ranges (High)**
- `canlib/modes/iocontrol_scan.py` — `DEFAULT_ECU_RANGES`, a dict keyed by
  literal HK ECU names (`IGPM`/`BCM`/`HVAC`/`PSM`) → DID ranges "informed by the
  HKMC body-controller DID map". Fallback `default_range=(0xB000, 0xBFFF)` is the
  HK body-DID zone too.
- `canlib/scan_presets.py` — iocontrol preset `default_range="B000-B0FF"` (HK
  IOControl zone) and HK ECU names in the preset `summary`/`caution` help text.

**Analysis tooling (High — the focus of this revision)**
- `canlib/xanalysis.py` `_UNIT_CANDIDATES` — a `raw/2−40 (HK temp)` unit-guess
  candidate (and a `raw×0.02 "cell V"` EV-flavored one) baked into the shared
  hunt/investigate unit sniffer.
- `canlib/states.py` `POWER_STATES` — the base power-state vocabulary embeds the
  EV states `PLUGGED`/`READY`/`CHARGING` into *every* profile (and into the
  static `--prereq`/`--vehicle-states` CLI choices), so an ICE profile inherits
  states it can never reach.

**Identity/DTC (Medium — diagnostic tooling, mostly cosmetic bias)**
- `canlib/modes/identity_records.py` — a `F187 "ECU Part Number (HK)"` UDS
  identity DID that only exists because of the Hyundai/Kia `-1` identifier echo,
  plus KWP labels documented as "Hyundai/Kia semantics".
- `canlib/modes/dtc.py` — `_MASK_FALLBACK` / `_KWP_READ_REQUESTS` heuristics whose
  *values* are ISO-standard but whose comments frame them as Hyundai-specific.

**Boy-Scout (not make-specific, spotted during the audit)**
- `canlib/commands/discover.py` — argparse `--range default="01-FF"` sentinel
  contradicts its own help/epilog ("default 700-7EF"); real default resolved in
  `_live.py`.
- `canlib/transport/uds_raw.py` — `response_id()` helper + `RESPONSE_OFFSET`
  appear dead (real addressing goes through `resolve_rx`).

## Design decisions (settled)

- **iocontrol ranges:** new optional per-ECU profile field
  `iocontrol_scan_ranges:`; when absent, **derive** candidate ranges from the
  ECU's known `2F`/`22` PID/DID keys (reusing the `scan_presets` inference); final
  fallback is the generic full-DID space `(0x0000, 0xFFFF)`, **not** the HK
  `B000` zone.
- **unit-guess candidates:** mirror the `physical_bands` precedent — make-neutral
  built-ins in the tool (`canlib/unit_guess.py`), profile-extensible via a
  `profile.yaml` `unit_guess_candidates:` list resolved by
  `resolve_unit_candidates(meta)` and threaded into `hunt`. Relabel `HK temp` to
  a make-neutral `½°C −40`; the generic scalings (`×1`, `/2`, `/10`, `/100`,
  `raw−40 °C`, mph↔km/h, `×0.02 cell V`) stay as neutral built-ins. The Ioniq
  needs no profile addition (its scalings are already generic).
- **states:** the tool's base vocabulary becomes the powertrain-neutral
  ignition-switch ladder `SLEEP`/`ACC`/`RUN`/`CRANK` (the universal
  OFF/ACC/ON/START positions; `RUN`/`SLEEP` because `ON`/`OFF` are YAML 1.1
  booleans, and `RUN` reads unambiguously where a bare `IGN` invites "which IGN
  level?"); the EV modes `PLUGGED`/`READY`/`CHARGING` and finer vendor ignition
  rungs (Hyundai's numbered `IGN0-3`/split `IGN1`/`IGN2`, an `ACC2` sub-level)
  become **profile-declared** (`vehicle_states.yaml`). The Ioniq already declares
  its own, so `allowed_states()` is unchanged for it. The
  `--prereq`/`--vehicle-states` CLI flags stop using a static `choices=` and
  validate against the profile-aware `allowed_states()` instead (a bare profile
  then correctly offers only the neutral base).
- **identity/DTC:** gate the HK-only `F187` DID behind the `hk_f1xx_minus_one`
  quirk (so a non-HK profile doesn't probe it), relabel it neutrally, and
  neutralize the HK-framed comments in `dtc.py` (no behavior change — the values
  are ISO-standard fallbacks).
- **Editing the bundled Ioniq profile data:** use a small new `canair pids`
  editor subcommand for `iocontrol_scan_ranges:` (respects the "never hand-edit
  `ecus/`" rule).

---

## Stage 1 — iocontrol scan ranges become profile-driven / PID-derived

Kill `DEFAULT_ECU_RANGES` and the HK-zone fallbacks; resolve ranges from (in
precedence order) explicit arg → profile field → PID-derived → generic full-DID.

1. **Schema:** add optional per-ECU `iocontrol_scan_ranges:` (list of `START-END`
   hex strings, e.g. `["B000-BFFF", "C000-C0FF"]`) to
   `canlib/schema/pids_schema.yaml`. Validated by `canair validate pids`.
2. **Shared range helper:** add `infer_iocontrol_ranges(ecu_def)` to
   `canlib/scan_presets.py` that returns candidate `(start, end)` ranges from an
   ECU's `2F`/`22` PID/DID keys (wide-service high-byte spans); `None` if no keys.
3. **`canlib/modes/iocontrol_scan.py`:** delete `DEFAULT_ECU_RANGES`; build the
   per-ECU `default_ranges` map at runtime — profile `iocontrol_scan_ranges:` →
   PID-derived → `[(0x0000, 0xFFFF)]` — and pass `default_range=(0x0000, 0xFFFF)`.
4. **`canlib/scan_presets.py`:** change the `iocontrol` preset `default_range`
   from `"B000-B0FF"` to `"0000-FFFF"`, and strip HK ECU names from the
   `summary`/`caution` strings into make-neutral wording.
5. **Migration (bundled parity):** add `canair pids set-iocontrol-ranges ECU
   RANGE [RANGE …]` (surgical, comment-preserving, re-validated, auto-reverted).
   Write the current Ioniq ranges into `igpm`/`bcm`/`hvac`/`psm` so the bundled
   scan resolves identically:
   - IGPM: `B000-BFFF`, `BD00-BDFF`, `C000-C0FF`
   - BCM: `B000-B3FF`, `B400-B7FF`, `C000-C0FF`, `F000-F0FF`
   - HVAC: `F000-FFFF`
   - PSM: `B000-BFFF`
6. **Tests:** resolution precedence (`tests/test_iocontrol_scan.py`), neutral
   iocontrol default (`tests/test_scan_presets.py`), `set-iocontrol-ranges`
   (`tests/test_pids_edit_cli.py`), new schema field (`tests/test_validate_pids.py`).
7. **Docs:** `AGENTS.md` (per-ECU field + new `pids` subcommand), the relevant
   `docs/reference/` page, `CHANGELOG.md`.

---

## Stage 2 — analysis unit-guess candidates: neutral built-ins + profile-extensible

De-Hyundai the `hunt`/`investigate` physical-unit sniffer.

1. **New module `canlib/unit_guess.py`:** `DEFAULT_UNIT_CANDIDATES` (the current
   candidate tuples with `HK temp` relabelled `½°C −40`) + `resolve_unit_candidates(meta)`
   that appends a profile's `unit_guess_candidates:` entries (each
   `{factor, offset, label, hint?, dimension?}`), lenient like
   `physical_bands._parse_range`.
2. **`canlib/xanalysis.py`:** move `_UNIT_CANDIDATES` to the new module; give
   `sniff_unit(..., candidates=None)` and `hunt_byte(..., candidates=None)` an
   optional resolved list (default = built-ins, preserving today's behavior).
3. **`canlib/commands/hunt.py`:** resolve `resolve_unit_candidates(active().meta)`
   and thread it into `hunt_byte` (mirrors how `_run_physical` resolves bands).
4. **Validate:** optional soft-validation of `unit_guess_candidates:` in
   `canair validate` (shape check), matching the `physical_bands` treatment.
5. **Tests:** `tests/test_xanalysis.py` (neutral built-ins, profile extension,
   relabelled temp); `tests/test_unit_guess.py` (resolver).
6. **Docs:** `AGENTS.md` (profile field), `docs/` analysis page, `CHANGELOG.md`.

---

## Stage 3 — states: make-neutral base, EV states profile-declared

1. **`canlib/states.py`:** `POWER_STATES = ("SLEEP", "ACC", "RUN", "CRANK")`
   (universal ignition-switch ladder OFF/ACC/ON/START; `RUN`/`SLEEP` not
   `ON`/`OFF` — those are YAML booleans). Keep `allowed_states()` = base ∪ `ALL`
   ∪ profile names (so the Ioniq keeps `PLUGGED`/`READY`/`CHARGING`/`ACC2` via
   its `vehicle_states.yaml`).
2. **CLI:** make `--prereq`/`--vehicle-states` (`commands/pids.py`,
   `commands/research.py`) profile-aware — drop the static
   `choices=CLI_STATE_CHOICES`, keep `type=str.upper`, and validate the given
   tokens against `allowed_states()` after parse with a clear error.
3. **Fixups:** `scripts/migrate_states_status.py` `VOCAB`,
   `tests/test_status_vocab.py` (base assertion), `tests/test_states.py`,
   `commands/validate/pids.py` comment.
4. **Docs:** `AGENTS.md`/`docs/` state-vocabulary note; `CHANGELOG.md`.

---

## Stage 4 — identity/DTC: neutralize HK bias

1. **`canlib/modes/identity_records.py` + `identity.py`:** relabel `F187` from
   "ECU Part Number (HK)" to a neutral label, and gate probing `F187` behind the
   `hk_f1xx_minus_one` quirk (thread the profile/quirk into the record iteration
   so a non-HK profile skips the HK-only DID). Neutralize the "Hyundai/Kia
   semantics" comment on the KWP records.
2. **`canlib/modes/dtc.py`:** reword the `_MASK_FALLBACK` / `_KWP_READ_REQUESTS`
   comments to describe them as generic ISO fallback heuristics (no value/behavior
   change).
3. **Tests:** `tests/test_identity.py` — F187 gating (probed with the quirk,
   skipped without).

---

## Stage 5 — Boy-Scout cleanups

1. **`canlib/commands/discover.py`:** reconcile the `--range` argparse
   `default="01-FF"` sentinel with its help/epilog. Prefer `default=None` and let
   `_live.py` own the real default (11-bit `0x700-0x7EF` / 29-bit `0x00-0xFF`);
   verify `_live.py` handles `None` the same way. Update help text.
2. **`canlib/transport/uds_raw.py`:** confirm no callers of `response_id()`, then
   remove it and `RESPONSE_OFFSET` if orphaned. **(Outcome: kept — `response_id`
   is re-exported from `transport/__init__.py` and covered by
   `tests/test_uds_raw.py`, so it is NOT orphaned. Left in place.)**
3. **Tests/validation:** full suite; add/adjust a discover-default test if one exists.

---

## Cross-cutting verification (all stages)

- `uv run canair validate all --profile ioniq-2017`
- `uv run pytest -q` (full suite; specifically the touched test modules)
- `uv run ruff check . && uv run ruff format --check .`
- `uv run ty check`
- Confirm the bundled Ioniq scan/hunt/state behavior is equivalent to pre-change
  (iocontrol ranges resolve identically; hunt unit guesses unchanged for the
  Ioniq; `allowed_states(ioniq)` unchanged).

## Future work (explicitly deferred, tracked here)

- `canlib/xanalysis.py` / `hunt` follow-ups from
  `plans/2026-08-02-blind-tooling-stress-test.md` (band/anchor ranking for
  absolute levels; shift-form expressions for `<no-expr>` LE/PCI-skip winners) —
  robustness, not HK bias.
- Broader ICE support: profile-declarable *base* state set (vs. the fixed
  neutral ignition ladder), if a real ICE profile needs a different ladder.
