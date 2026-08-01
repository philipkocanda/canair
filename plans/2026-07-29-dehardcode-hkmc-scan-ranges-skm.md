# De-hardcode remaining HKMC/Ioniq-specific values (scan ranges, SKM wakeup)

## Goal

Remove the last raw Hyundai/Kia/HKMC assumptions still compiled into
otherwise vehicle-agnostic **tool code**, so a fresh profile for any make drives
canair correctly without inheriting Ioniq body-controller DID maps, an Ioniq
SKM relay procedure, or HKMC-flavored scan defaults. The bundled `ioniq-2017`
profile must behave **identically** at every stage (the HK behavior moves into
its profile data / a declared capability, it is not lost).

This is a direct follow-up to `plans/2026-07-28-multi-vehicle-support.md` and
reuses that effort's established pattern: make-specific behavior is declared by
the profile (`quirks:` / new per-ECU fields) and shared code gates on it, rather
than baking a make assumption into the tool.

## Background — findings (2026-07-29 codebase audit)

The codebase is largely well-factored: addressing, the `hk_f1xx_minus_one`
quirk, ISO-TP padding, the CAN-bus vocabulary, and the states vocabulary are all
profile-gated. The remaining raw hardcodes, by severity:

**High**
- `canlib/modes/iocontrol_scan.py:143-163` — `DEFAULT_ECU_RANGES`, a dict keyed by
  literal HK ECU names (`IGPM`/`BCM`/`HVAC`/`PSM`) → DID ranges "informed by the
  HKMC body-controller DID map". Fallback `default_range=(0xB000, 0xBFFF)`
  (call at L217) is the HK body-DID zone too.
- `canlib/modes/skm_wakeup.py` (whole 319-line module) — Ioniq Smart Key Module
  relay procedure: `SKM_RELAYS` DIDs `B108`–`B10B` (L11-16), `SKM_MAGIC = "0A0A05"`
  (L19), hardcoded addresses SKM `0x7A5` (L149-150), IGPM `0x770`+`22BC03` (L283),
  BCM `0x7A0`+`22B003` (L303), `ATST10` sleep-timer-tuned timing (L153), and
  byte-offset/power-mode parsing (`parse_igpm_bc03` L22-55, `parse_bcm_b003`
  L58-85, enum `{0x09,0x0A,0xF5}`). The SKM address leaks into session tracking:
  `modes/multi_exec.py:46` and `modes/multi_repl.py:148` (`sm._sessions[0x7A5]`).

**Medium**
- `canlib/scan_presets.py:52` — iocontrol preset `default_range="B000-B0FF"` (HK
  IOControl zone). HK ECU names in `summary`/`caution` help text (L39/46/63).

**Boy-Scout (not make-specific, spotted during the audit)**
- `canlib/commands/discover.py:29-30` — argparse `--range default="01-FF"` sentinel
  contradicts its own help/epilog ("default 700-7EF"); real default resolved in
  `_live.py:989-994`.
- `canlib/transport/uds_raw.py:36-40` — `response_id()` helper appears dead (no
  callers; real addressing goes through `resolve_rx`), with `RESPONSE_OFFSET`.

**Deferred (documented as future work, NOT touched by this plan)** — advisory or
graceful-degrade, low risk, tracked in the "Future work" section below:
- `states.py:53` EV-assuming base vocab (`PLUGGED`/`READY`/`CHARGING`).
- `identity_records.py` HK-flavored labels + `F187 "ECU Part Number (HK)"` DID.
- `dtc.py:42,47` `_MASK_FALLBACK` / `_KWP_READ_REQUESTS` HK-informed heuristics.
- `xanalysis.py:92-94` `raw/2−40 (HK temp)` / cell-V unit-guess candidates.

## Design decisions (settled)

- **iocontrol ranges:** new optional per-ECU profile field
  `iocontrol_scan_ranges:`; when absent, **derive** candidate ranges from the
  ECU's known `2F`/`22` PID/DID keys (reusing the `scan_presets._infer_from_pids`
  approach); final fallback is the generic full-DID space `(0x0000, 0xFFFF)`, **not**
  the HK `B000` zone.
- **skm_wakeup:** scope it as an **opt-in, profile-capability-gated Ioniq mode**.
  Keep the working logic; gate its dispatch on a profile-declared capability so it
  is refused for profiles that don't declare it. Do **not** extract the DIDs /
  magic / addresses / offsets into the profile this pass.
- **Capability mechanism:** reuse the existing `quirks:` list (add a
  `skm_wakeup` token to `KNOWN_QUIRKS`) rather than introduce a parallel
  `capabilities:` concept — keeps the machinery small and consistent with
  `hk_f1xx_minus_one`.
- **Editing the bundled Ioniq profile data:** use a small new `canair pids`
  editor subcommand for `iocontrol_scan_ranges:` (respects the "never hand-edit
  `ecus/`" rule); declare the `skm_wakeup` quirk via `canair config`-style edit
  of `profile.yaml` (or the existing quirks-editing path if one exists — else a
  reviewed direct edit of `profile.yaml`, which is profile *config*, not `ecus/`).

---

## Stage 1 — iocontrol scan ranges become profile-driven / PID-derived

Kill `DEFAULT_ECU_RANGES` and the HK-zone fallbacks; resolve ranges from (in
precedence order) explicit arg → profile field → PID-derived → generic full-DID.

1. **Schema:** add optional per-ECU `iocontrol_scan_ranges:` (list of `START-END`
   hex strings, e.g. `["B000-BFFF", "C000-C0FF"]`) to
   `canlib/schema/pids_schema.yaml`. Validated by `canair validate pids`.
2. **Shared range helper:** factor a reusable function (extend/relocate the range
   inference already in `canlib/scan_presets.py::_infer_from_pids`) that, given an
   ECU definition, returns candidate `(start, end)` ranges from its `2F`/`22`
   PID/DID keys. Wide-service DIDs span the observed high-byte range; if no keys,
   return `None`.
3. **`canlib/modes/iocontrol_scan.py`:**
   - Delete `DEFAULT_ECU_RANGES` (L143-163) and its HKMC comment block.
   - Rework `mode_iocontrol_scan` to compute ranges per ECU: explicit `did_range`
     arg → profile `iocontrol_scan_ranges:` for that ECU → PID-derived (step 2) →
     `(0x0000, 0xFFFF)`.
   - Pass `default_range=(0x0000, 0xFFFF)` (not `0xB000, 0xBFFF`) to
     `mode_discovery_scan`; the per-ECU resolution supplies the smart ranges via
     `default_ranges` built at runtime from the profile/PIDs.
4. **`canlib/scan_presets.py`:**
   - Change the `iocontrol` preset `default_range` (L52) from `"B000-B0FF"` to a
     make-neutral value (`"0000-FFFF"`), used only as the no-PIDs fallback since
     `plan_scan` already PID-derives when it can.
   - Strip HK ECU names from `summary`/`caution` strings (L39/46/63) into neutral
     wording (they are illustrative, not derived from the profile).
5. **Migration (bundled profile parity):** add a `canair pids
   set-iocontrol-ranges ECU RANGE [RANGE …]` editor subcommand (surgical,
   comment-preserving, re-validated, auto-reverted — mirrors the other `pids`
   editors in `canlib/pids_edit.py`). Use it to write the current Ioniq ranges
   into `profiles/ioniq-2017/ecus/{igpm,bcm,hvac,psm}.yaml` so the bundled scan
   resolves identically to today:
   - IGPM: `B000-BFFF`, `BD00-BDFF`, `C000-C0FF`
   - BCM: `B000-B3FF`, `B400-B7FF`, `C000-C0FF`, `F000-F0FF`
   - HVAC: `F000-FFFF`
   - PSM: `B000-BFFF`
6. **Tests:** extend `tests/test_iocontrol_scan.py` for the resolution precedence
   (explicit arg > profile field > PID-derived > full-DID fallback);
   `tests/test_scan_presets.py` for the neutral iocontrol default;
   `tests/test_pids_edit_cli.py` for `set-iocontrol-ranges`;
   `tests/test_validate_pids.py` for the new schema field.
7. **Docs:** `AGENTS.md` (per-ECU field list + the new `pids` subcommand),
   the relevant `docs/reference/` page, and a note in this plan / the
   multi-vehicle plan. README only if a user-facing command line changed
   (the `pids` map already lists subcommands — add `set-iocontrol-ranges`).

**Verification:** `uv run canair validate pids --profile ioniq-2017`; a dry
inspection that IGPM/BCM/HVAC/PSM ranges resolve to the pre-migration tuples; the
Stage-1 tests pass.

---

## Stage 2 — skm_wakeup becomes an opt-in, profile-gated Ioniq mode

Keep the logic; refuse it for profiles that don't declare the capability.

1. **Quirk token:** add `SKM_WAKEUP: Final = "skm_wakeup"` to `canlib/quirks.py`
   and include it in `KNOWN_QUIRKS`. Add a helper read at dispatch
   (`has_quirk(pids_data, SKM_WAKEUP)`).
2. **Gate every dispatch/registration site** so the command errors cleanly
   ("skm-wake is not supported by profile `<name>` — it requires the
   `skm_wakeup` capability") when the profile doesn't declare it:
   - `canlib/commands/_live.py:626-637` (batch dispatch)
   - `canlib/modes/interactive.py:65`
   - `canlib/modes/multi_repl.py:147`
   - `canlib/modes/multi_exec.py:43`
   The hardcoded `sm._sessions[0x7A5]` writes (`multi_exec.py:46`,
   `multi_repl.py:148`) only run inside the now-gated path; optionally source
   `0x7A5` from a named constant beside `SKM_RELAYS` for clarity.
3. **Module docstring:** update `canlib/modes/skm_wakeup.py`'s header to state it
   is Ioniq/HKMC-specific and gated behind the profile `skm_wakeup` capability;
   the DIDs/magic/addresses/offsets stay in place (extraction is out of scope).
4. **Declare the capability** in `profiles/ioniq-2017/profile.yaml` under
   `quirks:` so the bundled profile keeps working. Validate the token via
   `canair validate` (quirk whitelist).
5. **Tests:** `tests/test_skm_wakeup.py` — add gating tests (refused when the
   profile lacks `skm_wakeup`; permitted when declared). Keep the existing
   parse/transport-guard tests.
6. **Docs:** `AGENTS.md` (note skm-wake is a profile-capability-gated Ioniq
   feature) and the relevant `docs/` page.

**Verification:** `skm-wake` still dispatches for `ioniq-2017`; a bare/other
profile refuses it with the clear message; `uv run canair validate all
--profile ioniq-2017` passes; Stage-2 tests pass.

---

## Stage 3 — Boy-Scout cleanups

Low-risk, not make-specific; fold in per the AGENTS.md Boy-Scout rule.

1. **`canlib/commands/discover.py:29-30`** — reconcile the `--range` argparse
   `default="01-FF"` sentinel with its help/epilog ("default 700-7EF"). Prefer
   `default=None` and let `_live.py:989-994` own the real default (11-bit
   `0x700-0x7EF` / 29-bit `0x00-0xFF`); verify `_live.py` handles `None` the same
   way it handled the `"01-FF"` sentinel. Update help text to match.
2. **`canlib/transport/uds_raw.py:36-40`** — confirm no callers of `response_id()`
   (audit found none), then remove it and `RESPONSE_OFFSET` if orphaned.
3. **Tests/validation:** run the full suite; add/adjust a discover-default test if
   one exists.

**Verification:** `uv run python -m pytest`; `uv run canair discover --help`
shows a consistent default; grep confirms `response_id` has no remaining
references.

---

## Cross-cutting verification (all stages)

- `uv run canair validate all --profile ioniq-2017`
- `uv run python -m pytest` (full suite; specifically
  `tests/test_iocontrol_scan.py tests/test_scan_presets.py
  tests/test_skm_wakeup.py tests/test_pids_edit_cli.py
  tests/test_validate_pids.py`)
- `ty` type-check clean on touched modules.
- Confirm the bundled Ioniq scan/skm behavior is byte-for-byte equivalent to
  pre-change (ranges resolve identically; skm-wake still available).

## Future work (explicitly deferred, tracked here)

Known remaining HK/EV bias, left in place this pass as advisory or
graceful-degrading; revisit when a non-EV / non-HK profile actually needs them:

- `canlib/states.py:53` — base `POWER_STATES` embeds EV states
  (`PLUGGED`/`READY`/`CHARGING`) into every profile; consider making the base set
  profile-declarable for ICE vehicles.
- `canlib/modes/identity_records.py` — HK-flavored KWP record labels and the
  `F187 "ECU Part Number (HK)"` identity DID; consider gating the HK DID behind
  `hk_f1xx_minus_one` and making labels profile-extendable.
- `canlib/modes/dtc.py:42,47` — `_MASK_FALLBACK` / `_KWP_READ_REQUESTS`
  HK-informed probe heuristics (values are ISO-standard; degrade gracefully).
- `canlib/xanalysis.py:92-94` — `raw/2−40 (HK temp)` / `×0.02 (cell V)` advisory
  unit-guess candidates; consider a profile-extendable candidate list.
