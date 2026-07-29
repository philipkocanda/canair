# Typing & test-coverage follow-ups for the extended-11-bit / functional-TX addressing work

## Context

Commit `b35af3a` ("addressing: extended-11-bit + functional-TX flow control
(gaps G-I…G-L)") closed the remaining multi-vehicle addressing gaps from
`plans/2026-07-28-multi-vehicle-support.md`:

- **G-I** — `AddressingMode.NORMAL_EXTENDED_11BIT` + per-ECU `target_address` /
  tester `source_address` (BMW `0x6F1` / PSA).
- **G-J** — per-ECU `addressing.fc_id` flow-control override via
  `canlib/transport/isotp_stack.py` (`build_isotp_stack` / `_FcAddressStack`).
- **G-K** — `normal_29bit` explicit-id escape hatch (GM/VW/Volvo), documented +
  tested.
- **G-L** — negative `rx_offset` (PSA `-0x20`).
- Consolidated the resolved addressing into a single `EcuAddress` bundle
  (`canlib/addressing.py::resolve_ecu_address`) threaded through the raw path,
  plus editors (`canair pids set-addressing`, `canair ecu add` flags,
  `register_ecu(...)` addressing kwargs).

This plan captures the **typing and test-coverage improvements** identified in a
follow-up review of that change. Everything here is additive hardening — no
behavior change intended. The current tree passes `pytest`, `ruff`, `ty`, and
`canair validate all`; these items raise the confidence floor around the new,
genuinely-new-runtime paths (the FC override and the raw-path stack construction)
that today are only shallowly exercised.

## Priority summary

| # | Item | Kind | Priority |
|---|------|------|----------|
| 5 | Real-stack FC-override test (not a stand-in return) | test | **High** |
| 6 | `RawTerminal` threads `fc_id` into the stack | test | **High** |
| 7 | `RawUdsClient` threads extended / `fc_id` addressing | test | **High** |
| 8 | `resolve_source_address` for `extended_29bit` returns `None` | test | **High** |
| 1 | `build_isotp_stack(params: dict)` → `dict[str, int \| bool]` | typing | Medium |
| 2 | `_FcAddressStack._make_flow_control` return annotation | typing | Medium |
| 9 | `build_isotp_address` `extended_29bit` derives target/source from id | test | Medium |
| 4 | De-duplicate hex-arg parsing across `ecu add` / `pids set-addressing` | typing/refactor | Medium |
| 3 | `build_ecu_index(...) -> dict[str, EcuIndexEntry]` | typing | Low |
| 10 | `set_addressing` no-op returns `False` | test | Low |
| 11 | Per-ECU `source_address` byte-range validation | test | Low |

---

## High priority

These cover the genuinely-new runtime behaviour (the FC override and the
raw-path stack construction from an `EcuAddress`) that is currently either tested
against a facsimile or not at all.

### 5. G-J FC override is only unit-tested against a *stand-in* return object

`tests/test_isotp_stack.py::TestFcAddressOverride` monkeypatches the base
`isotp.NotifierBasedCanStack._make_flow_control` to return a **python-can**
`can.Message`, so the test never exercises the real `isotp.protocol.CanMessage`
that the base actually returns (verified to carry the attribute `is_extended_id`,
init kwarg `extended_id`). If can-isotp renamed or restructured that object, the
override could set a spurious attribute and the test would still pass.

- **Fix:** add a test that drives a *real* stack over a mock bus — construct via
  `build_isotp_stack(bus, notifier, build_isotp_address(addr), params, fc_id=…)`,
  feed a multi-frame FirstFrame so the stack emits a Flow Control frame, and
  assert the transmitted frame's `arbitration_id == fc_id` and the extended flag.
  This proves the rewrite works on the genuine `CanMessage`, end to end.
- **Caveat:** `NotifierBasedCanStack` spins a notifier thread; keep the test
  deterministic (a mock bus whose `_recv_internal(timeout=…)` returns the queued
  FirstFrame once then `None`, a short poll, and a bounded wait). If thread
  timing proves flaky, fall back to a `CanStack` (bus-polled, no notifier) built
  through the same `_FcAddressStack` class so the real `_make_flow_control` path
  still runs.
- Keep the existing unit test as the fast, deterministic guard; add the
  integration test alongside it.

### 6. No test that `RawTerminal` threads `fc_id` into the stack

`tests/test_raw_terminal.py` covers `addr_map` RX/mode resolution
(`TestRawTerminalRxResolution` / `TestRawTerminalModeResolution`) but never an
`EcuAddress` carrying an `fc_id`. The `_stack` →
`build_isotp_stack(..., fc_id=addr.fc_id)` path is unverified.

- **Fix:** in the `capture_mode`-style fixture, monkeypatch
  `raw_terminal.build_isotp_stack` to record its `fc_id` kwarg, register an ECU
  whose `addr_map` entry has `fc_id` set, call `_stack(tx)`, and assert the
  recorded `fc_id` matches. Also assert an ECU *without* `fc_id` passes `None`.

### 7. No test that `RawUdsClient` threads extended / `fc_id` addressing

`tests/test_uds_raw.py::_addrs` only builds plain 11-bit `EcuAddress`es, so the
client's stack construction from an extended-11-bit or functional-TX
`EcuAddress` (the `build_isotp_stack(..., fc_id=address.fc_id)` call in
`RawUdsClient.__init__`) is untested.

- **Fix:** add a test that constructs a `RawUdsClient` with an `addresses` map
  containing an `EcuAddress` with `fc_id` set (and/or `NORMAL_EXTENDED_11BIT`),
  monkeypatching `uds_raw.build_isotp_stack` to capture the `fc_id` / address
  passed per ECU, and assert they thread through.

### 8. `resolve_source_address` for `extended_29bit` returns `None` (untested)

The follow-up deliberately narrowed the `DEFAULT_TESTER_ADDRESS` (`0xF1`) default
to `normal_extended_11bit` only — for the 29-bit modes the tester byte lives in
the arbitration id, so `resolve_source_address` returns `None` and
`build_isotp_address` derives it from the id. Nothing locks this in; a regression
that re-broadened the default would silently corrupt 29-bit extended source
addressing.

- **Fix:** add assertions in `tests/test_addressing.py`:
  - `resolve_source_address(None, {"tx_id": 0x18DA10F1}, AddressingMode.EXTENDED_29BIT) is None`
  - `resolve_source_address(None, {"tx_id": 0x6F1}, AddressingMode.NORMAL_EXTENDED_11BIT) == DEFAULT_TESTER_ADDRESS`
    (already partially covered by `test_extended_11bit_defaults_tester`, but make
    the mode-narrowing explicit).

---

## Medium priority

### 1. `build_isotp_stack(params: dict)` — tighten to `dict[str, int | bool]`

The value passed is always the result of
`canlib/transport/isotp_params.py::build_isotp_params`, whose return type is
`dict[str, int | bool]`. Both call sites (`raw_terminal._stack`,
`RawUdsClient.__init__`) pass that. Tighten the parameter annotation so a wrong
params shape is caught at the transport boundary (the contributing skill flags
transport-boundary dicts as high-value for precise hints). `ty` enforces it.

### 2. `_FcAddressStack._make_flow_control` return annotation

The override has no return type and takes untyped `*args, **kwargs`. It returns
an `isotp.protocol.CanMessage`. Annotate the return (and keep the comment noting
it overrides a can-isotp internal). At minimum add the return type; the `*args`
forwarding can stay variadic since the base signature is variadic across
can-isotp versions.

### 9. `build_isotp_address` `extended_29bit` derives target/source from the id

`tests/test_addressing.py::TestBuildIsotpAddress::test_extended_29bit` only
asserts the tx arbitration id. When `target_address`/`source_address` are unset,
`build_isotp_address` derives them from the tx id (target = bits 8–15, source =
bits 0–7). Add assertions on the derived bytes (and a second case where an
explicit `target_address` on the `EcuAddress` overrides the id-derived one).

### 4. De-duplicate hex-arg parsing across `ecu add` and `pids set-addressing`

`commands/ecu.py::cmd_add` grew a nested `_hex_or_die(value, label)` that raises
`SystemExit` and is then caught to convert to a return code — an awkward
control-flow shape, and it duplicates `commands/pids.py::_parse_hex_arg`. Extract
a single shared, typed helper (`-> int | None`) — e.g. into a small shared module
or `commands/_live.py`-adjacent util — and use it in both commands. Improves
consistency and removes the raise-then-catch dance.

---

## Low priority

### 3. `build_ecu_index(pids_data: dict) -> dict[str, EcuIndexEntry]`

The follow-up added the `address: EcuAddress` field to the `EcuIndexEntry`
`TypedDict`, but `build_ecu_index` still returns bare `dict`, so consumers
reading `info["address"]` (raw_ops, raw_monitor) get no type help. Tighten the
return annotation to `dict[str, EcuIndexEntry]`.

- **Caveat:** pre-existing looseness with several consumers; run `ty check`
  first to confirm it doesn't cascade errors in callers (some read keys not in
  the `TypedDict`). If it cascades, scope this to its own change rather than
  bundling it here.

### 10. `set_addressing` no-op returns `False`

Only the changed-`True` paths are covered. Add a test that calling
`set_addressing` (or `cmd_set_addressing`) twice with the same values returns
`False` / prints the "already as requested; nothing to change" message and does
not rewrite the file (assert mtime/content unchanged on the second call).

### 11. Per-ECU `source_address` byte-range validation

`tests/test_validate_pids.py` covers per-ECU `target_address` out-of-range and
`fc_id` positivity, but not `source_address` out-of-range (the second
`_validate_addressing_byte` call in `_validate_ecu_addressing`). Add the
symmetric case.

---

## Verification (when implemented)

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair validate all
```

New/changed tests concentrate in `tests/test_isotp_stack.py`,
`tests/test_raw_terminal.py`, `tests/test_uds_raw.py`, `tests/test_addressing.py`,
`tests/test_validate_pids.py`, plus the editor tests if #10 lands there. Typing
changes (#1–#4, #3) are enforced by `ty` and touch `canlib/transport/isotp_stack.py`,
`canlib/pids.py`, `canlib/commands/ecu.py`, and `canlib/commands/pids.py`.

## Notes

- None of these change runtime behaviour; they harden types and lock in the new
  addressing paths. Safe to land incrementally, high-priority set first.
- The G-I (extended 11-bit) and G-J (functional-TX FC) paths are still verified
  only against mock buses / the real isotp address+stack objects — a bench run
  against real BMW/Renault hardware remains the open confidence gap noted in
  `plans/2026-07-28-multi-vehicle-support.md` and is out of scope here.
