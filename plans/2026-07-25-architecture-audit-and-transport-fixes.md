# canair — Architecture Audit & Dual-Transport Bug Fixes

Status: implementation plan. Captures the findings of a 2026-07-25 code/architecture
audit and the immediate fixes for the confirmed dual-transport contract violations.
The larger structural items (monolith splits, two-domain symmetry gaps) are recorded
here as a backlog but are **not** part of the immediate fix set.

Baseline at audit time: 2089 tests pass, `ruff` clean, `ty` clean. None of the
findings block the build; finding #1 is a live regression on the *default* transport.

## The dual-transport contract (context)

canair reaches the bus through two transports exposing the **same** async terminal
surface, so mode handlers written against that surface work on both for free:

- `slcan-tcp` → `RawTerminal` (`canlib/transport/raw_terminal.py`) — the canonical default.
- `wican-ws` → `WiCANTerminal` (`canlib/terminal.py`) — Pro-only.

Live commands dispatch through the single shared `_live.py::dispatch_mode`. A method
that exists on one terminal but not the other (or with a different signature) silently
breaks every command routed through `dispatch_mode` on the affected transport.

## Fixes in this change (confirmed bugs)

### 1. `RawTerminal.enter_extended_session` is missing the `mode` param — CRITICAL

- `WiCANTerminal.enter_extended_session(wake=False, mode="03")` (`terminal.py:345`) builds
  `10<mode>`; `RawTerminal.enter_extended_session(wake=False)` (`raw_terminal.py:134`)
  hardcodes `1003` and rejects a `mode=` kwarg.
- Callers thread `mode=session_mode` unconditionally: `modes/discovery_scan.py:100,129`
  (reached from `scan range/routines/iocontrol --session`, and KWP `--session-mode 81`).
- Consequence: **any `scan … --session` over the default `slcan-tcp` transport raises
  `TypeError: enter_extended_session() got an unexpected keyword argument 'mode'`** — even
  for the default `mode="03"`, because the parameter is absent entirely.
- The test double already encodes the correct 3-arg surface
  (`tests/test_discovery_scan.py`), so `RawTerminal` drifted from the agreed contract.
- **Fix:** add `mode: str = "03"` to `RawTerminal.enter_extended_session`, normalize it
  the same way `WiCANTerminal` does, and send `10<mode>` instead of hardcoded `1003`.
- **Test:** a regression test proving the raw terminal accepts `mode=` and emits the
  requested `10<mode>` request, and that the scan path runs through `dispatch_mode` with
  a non-`"03"` mode on the raw transport.

### 2. `mode_skm_wakeup` reaches into `WiCANTerminal`-only internals — HIGH

- `skm_wakeup.py:197` calls `terminal._drain()` and `:220-221` reads `terminal.ws.recv()`
  — neither exists on `RawTerminal`. `mode_skm_wakeup` is dispatched via `dispatch_mode`
  (`_live.py`), so `skm-wake` over `slcan-tcp` will `AttributeError`. It further assumes
  ELM327 text framing that `RawTerminal.send_command` never produces.
- **Fix (minimal, safe):** explicitly gate `skm-wake` to the ELM (`wican-ws`) transport in
  `dispatch_mode` with a clear error on the raw path, rather than silently `AttributeError`.
  (A fuller fix — porting the multi-frame collection onto the shared `send_uds` surface so
  it works on both transports — is larger and deferred; recorded in the backlog below.)
- **Test:** dispatching `skm-wake` on a raw terminal yields the clean guard error, not an
  `AttributeError`.

### 3. `mode_tester_present` assumes ELM AT/text semantics — LOW (latent)

- `tester.py:19-25,41-45` sends `ATSH…`/`ATFCSH…` and parses ELM text. Harmless today:
  it is REPL-only and **not** routed through `dispatch_mode`. Left as-is; noted so a future
  change doesn't wire it into the shared dispatch without porting it first.

## Contract checks that passed (no change)

- Safety blocklist enforced on **both** terminals via the single
  `safety.py::enforce_command_safety` (`terminal.py:136`, `raw_terminal.py:100,123`).
- `dispatch_mode` is the single shared entry (ELM `_live.py`, raw `raw_ops.py`); the only
  intentional pre-dispatch fork is the pipelined `--monitor` fast path.
- No resurrected seed→key / SecurityAccess (`0x27`) solver — protocol awareness only.
  (Note: `plans/2026-07-22-security-access-and-bms-session.md` is marked "DONE" but
  describes a `--pair` solver absent from the tree — stale plan doc, consistent with the
  no-key-solver policy.)

## Backlog (recorded, NOT in this change)

Structural debt surfaced by the audit, to be tackled as separate, individually-scoped
changes:

- **Full `skm-wake` dual-transport port** — move its multi-frame collection onto the
  shared `send_uds`/pending-wait surface so it works on both transports (supersedes the
  gate in fix #2).
- **`sniff.py` WiCAN coupling** — `sniff.py:179-194` discovers port/bitrate/mode via
  `wican_api.resolve_wican_url` + `wican_mode.load_config` (WiCAN HTTP `/load_config`), and
  `modes/raw_ops.py:23` imports the private `sniff._resolve_device_defaults`. Route through
  the transport seam (`transport/config.py::is_wican_http`) so a non-WiCAN SLCAN gateway
  works.
- **Analysis-suite symmetry** — `correlate`/`hunt` have a `can` kind but `decode`,
  `investigate`, `coverage` do not; a raw-CAN log can be correlated/hunted but not
  decoded/investigated.
- **`sniff --save` doesn't feed the store** — writes a raw file that is never
  imported/indexed, unlike diagnostic `--save` (journal + reconcile). No crash-recovery
  parity for frames.
- **`signals` authoring weaker than `pids`** — no `rename-signal`; `check_signals_doc`
  validates structure only (no `start_bit+length ≤ DLC` fit check, no overlap check, no
  cross-message name-collision check).
- **Naming** — `wican_bytes.py` is a generic AutoPID-layout/analysis concern (imported by
  `modes/status.py`, `param.py`, `interactive.py`, `ecu.py`), not device management;
  consider renaming to `autopid_layout.py`.
- **Monolith splits (twelve files > 500 lines; six > 900), by value:**
  1. `pids_edit.py` (1716) → `pids_edit/` package (YAML-text primitives · params/research/
     identity API · hit/discovery appenders). **DONE** (`pids_edit/` = `_text`/`params`/`hits`).
  2. `validate.py` (1458) → split by domain; decompose the ~380-line `validate_ecu_file`.
     **DONE** — `validate/` package (`pids`/`captures`/`other`), and `validate_ecu_file`
     decomposed into `_validate_ecu_entry`/`_validate_pids`/`_validate_one_pid`/
     `_validate_parameters`/`_validate_iocontrol`/`_validate_research` + a `_SchemaFields`
     dataclass replacing the ~15 threaded field-set locals.
  3. `multi.py` (1197) → extract the batching/result kernel to `modes/multi_batch.py`
     (already imported by `monitor.py`; also dedupes the inline error-result dict).
     **DONE** — `multi_batch.py` (kernel), plus `multi_parse.py` (sub-command / ECU-PID
     parsing), `multi_exec.py` (per-step execution primitives), `multi_repl.py` (the REPL);
     `multi.py` is now the orchestrator (1075 → 344 lines), re-exporting the moved names.
   4. Consolidate stats — `_mean/_median/_stdev/compute_stats/_fmt_num` duplicated across
      `decode.py` + `_decode_plot.py` → `stats.py`; move `_discriminability`/
      `_byte_state_buckets`/`find_mirrors` from `decode.py` to `xanalysis.py` (fixes the
      `investigate.py` → `decode.py` import leak). **PARTIALLY DONE** (`stats.py` exists;
      `_discriminability`/`_byte_state_buckets` moved). `find_mirrors` was NOT moved at the
      time — it was later relocated to the command-adjacent `_decode_calc.py` (C5), then its
      generic positional core pushed down to `xanalysis.find_frame_mirrors` and the two
      duplicate time-aligned mirror primitives collapsed onto `align.mirror_aligned_count`
      under **C2 of `plans/2026-07-27-architecture-cleanup.md`** (completed there).
  5. Extract shared CLI scaffolding — `_group_help` (copy-pasted 6×) and the raw-CAN-log
     argparse block (3×).
  - God objects (higher-risk, later): `MonitorController` (`monitor.py`), `_IOControlTUI`
    (`iocontrol.py`). **DONE** — `MonitorController` sheds its raw-CAN poll backend to a
    `MonitorRawPoller` collaborator (`monitor_raw.py`, joins the existing `MonitorEditor`)
    and its ~150-line renderer to `_monitor_render.py` (1198 → 845). `_IOControlTUI` sheds
    its ~190-line renderer to `_iocontrol_render.py` (1111 → 916). Both keep the tested
    public surface via thin delegators; naming/`wican_bytes.py`→`autopid_layout.py` also done.

## Verification

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
```
