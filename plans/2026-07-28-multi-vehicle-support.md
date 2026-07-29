# Generalizing canair for other vehicles & makes (incl. 29-bit)

## Goal

Make canair drive **any** modern CAN vehicle purely from a profile, removing the
Hyundai/Ioniq-specific hardcoding that currently leaks through the raw
(`slcan-tcp`) transport and a handful of defaults. Target scope is **full
generalization including 29-bit diagnostic addressing** (`18DAF1xx`), delivered
**incrementally** — foundation first, 29-bit last — so each phase is
independently shippable and the bundled `ioniq-2017` profile keeps working
unchanged at every step.

## Status (2026-07-28)

- **Survey follow-up gaps G-I / G-J / G-K / G-L — DONE** (2026-07-29). All four
  addressing gaps from the WiCAN-corpus survey are closed:
  - **G-I (extended/mixed 11-bit):** new `AddressingMode.NORMAL_EXTENDED_11BIT` →
    isotp `Extended_11bits`, with a per-ECU `addressing.target_address` extension
    byte + tester `source_address` (default `0xF1`, `DEFAULT_TESTER_ADDRESS`).
    Covers BMW `0x6F1` / PSA.
  - **G-J (functional-TX + physical-RX flow control):** a per-ECU
    `addressing.fc_id` override, applied by a `NotifierBasedCanStack` subclass
    (`canlib/transport/isotp_stack.py::build_isotp_stack` /
    `_FcAddressStack._make_flow_control`) that redirects FC frames to the ECU's
    physical id. Both raw clients build stacks through it. (Note: canair addresses
    ECUs *physically* on the raw path, so a plain `normal_fixed_29bit` profile
    already sends FC correctly; `fc_id` covers the functional-request-only edge.)
  - **G-K (non-`0x18` 29-bit priority):** confirmed + tested that `normal_29bit`
    with explicit `tx_id`/`rx_id` is the escape hatch for GM `0x14…`, VW `0x17…`,
    Volvo `0x1D…` (arbitrary priority + non-derivable RX baked into the ids). No
    `priority:` knob added — `build_isotp_address` for `normal_fixed_29bit`
    already preserves the priority bits, and the non-derivable-RX cases can't use
    fixed mode regardless, so the knob would add surface with no coverage.
  - **G-L (negative `rx_offset`):** confirmed representable and now schema-tested
    (PSA `-0x20`).
  - **Architecture:** the resolved addressing is consolidated into a single
    `EcuAddress` dataclass (`resolve_ecu_address`), stored on the registry and
    threaded through the raw path (`RawTerminal.addr_map` /
    `RawUdsClient(addresses=…)`), replacing the parallel rx/mode maps.
  - **Editors:** `canair pids set-addressing` (new) + `canair ecu add` addressing
    flags + `register_ecu(mode=/target_address=/source_address=/fc_id=)`; the
    offline id-range check widened to 29-bit ids.
  - **Tests:** `tests/test_addressing.py` (EcuAddress/extended-11bit/G-K/G-L),
    `tests/test_isotp_stack.py` (FC override), `tests/test_ecus_edit.py` +
    `tests/test_pids_edit_cli.py` + `tests/test_ecu_add.py` (editors),
    `tests/test_validate_meta.py` + `tests/test_validate_pids.py` (schema).

- **Phase 1 (foundation) — DONE**, commit `a0a0a80` ("profile: make CAN bitrate +
  ISO-TP tuning profile-driven"). Gaps **C, D, E, H** are resolved: per-profile
  `can_bitrate`, the optional `isotp:` block (shared `canlib/transport/isotp_params.py`),
  a de-Hyundai neutral scaffold init (`DEFAULT_INIT = "ATSP6;ATS0;ATAL;"`, no
  `ATST96`; missing `init:` now fails loud instead of substituting the Ioniq
  string), and `profile.yaml` schema validation.
- **`ecu`→`rx` capture-field rename — DONE**, commit `dbd928a`. Capture records
  now store the response address under `rx` (read via `canlib/capture_io.py::capture_rx`,
  which still tolerates the legacy `ecu` key), and the on-disk shapes are typed.
  This **moves the Phase 2 step-7 / gap-B touch points**: capture-reference
  resolution now flows through `capture_rx()` rather than a bare dict `["ecu"]`.
- **Phase 2 addressing abstraction (steps 5–7) — DONE** (this change). Gap **B**
  is resolved: a new `canlib/addressing.py` owns TX→RX resolution
  (`resolve_rx`/`resolve_rx_offset`, `DEFAULT_RX_OFFSET`); per-ECU `rx_id` and a
  profile-level `addressing.rx_offset` are schema-validated; the ECU registry
  (`load_ecus`/`build_ecu_index`) resolves and stores each ECU's `rx_id`; and the
  hardcoded `+8` sites (`ecus.py` rx_addr_str/build_rx_index/build_rx_tx_index/
  resolve_tx, the raw path `RawTerminal.rx_map`/`raw_ops`/`raw_monitor`,
  `discover.py`) all route through it. Ioniq unchanged (defaults reproduce `+8`).
  The **7-digit XPeng PID convention is resolved**: `22`+4-hex-DID + an optional
  trailing ELM327 response-frame-count digit (`2211011` = request `22 1101`,
  "expect 1 frame"); canair PID keys drop the count digit (ISO-TP reassembly
  handles frame counting). **Phase 2 is COMPLETE** — `profiles/xpeng-g6/` is
  seeded (step 8).
- **Phases 3–4 — DONE** (this change).
  - **Phase 3 (29-bit addressing, gap A):** `canlib/addressing.py` grew an
    `AddressingMode` enum (`normal_11bit`/`normal_29bit`/`normal_fixed_29bit`/
    `extended_29bit`), a mode-aware `resolve_rx` (fixed-29-bit derives RX by
    byte-swap), `resolve_mode` (per-ECU `addressing.mode` → profile → 11-bit
    default), and `build_isotp_address` — the single home turning `(tx, rx, mode)`
    into an `isotp.Address` so both raw clients (`RawTerminal`/`RawUdsClient`) stop
    hardwiring `Normal_11bits`. The registry (`load_ecus`/`build_ecu_index`) stores
    each ECU's resolved `mode` **as the `AddressingMode` enum** (typed end to end —
    consumers read `info["mode"]` directly, no stringify/re-parse), and
    `build_isotp_address` is exhaustive via `assert_never`, so adding a mode that
    isn't mapped is a type error, not a runtime surprise (commit `0e6bc9f`). The raw
    path threads a `mode_map`. `canair discover`
    sweeps a 29-bit target-address range (`0x18DA{target}{tester}`) and computes
    29-bit RX, width-aware in all output. SLCAN already transmits extended frames
    (`format_slcan_frame` `T`-prefix); verified 29-bit ISO-TP frames carry the
    extended flag. Schema + `canair validate` accept `addressing.mode` (profile &
    per-ECU) and widen `tx_id`/`rx_id` to 29-bit when a 29-bit mode is in effect.
  - **Phase 4 (de-Hyundai heuristics, gaps F/G):** the HK F1xx `-1` echo tolerance
    (`uds_parse.parse_uds_response`/`payload_echo_mismatch`) is gated behind the
    profile `quirks: [hk_f1xx_minus_one]` flag (new `canlib/quirks.py`), default off
    for new profiles, declared on for `ioniq-2017`; terminals resolve + forward it,
    `canair validate captures` too. The multi-DID padding-strip/split
    (`multi_batch.py`) is driven by `isotp.tx_padding` (`resolve_tx_padding`) instead
    of assuming `0xAA`. Gap G's identity-label split (step 13) is satisfied by the
    existing "clear marking" (`(HK)`/`(UDS)` suffixes + docstring notes) — a
    per-profile override table was judged not worth the complexity for a cosmetic
    probe-hint; `identity_decode`'s display strip already handles `AA`/`00`/`FF`
    generically. **All phases complete.**

## Worked example / first non-Hyundai driver: XPeng G6

The upstream WiCAN profile
([`meatpiHQ/wican-fw` `vehicle_profiles/xpeng/xpeng_g6.json`](https://github.com/meatpiHQ/wican-fw/blob/main/vehicle_profiles/xpeng/xpeng_g6.json),
`car_model: "Xpeng: P5/P7/G6/G9/X9"`) proves the G6's OBD port exposes standard,
third-party-readable UDS — no gateway lockout. Its `init` string
`ATH1;ATSP6;ATS0;ATM0;ATAT1;ATSH704;ATCRA784;ATFCSH704;ATFCSM1` decodes to:

| Fact | Value | canair fit |
|------|-------|------------|
| Protocol | `ATSP6` = ISO 15765-4, **11-bit, 500 kbit/s** | ✅ default — **no Phase 3 (29-bit) needed** |
| Request header (TX) | `ATSH704` → `0x704` | ✅ ordinary |
| Response addr (RX) | `ATCRA784` → `0x784` | ❌ **RX = TX + 0x80, not the hardcoded +8** → Phase 2 |
| Flow control | `ATFCSH704; ATFCSM1` | ELM327 fw handles it; verify `can-isotp` auto-FC on the raw path |
| PIDs | `22` DIDs, e.g. `2211011`, `221122` (192-cell block), `221123` (35 temps) | ⚠️ non-standard trailing-digit form — resolve WiCAN's parse convention before transcribing |

**Why it's the ideal Phase 2 test:** it isolates the `RX ≠ TX+8` problem (gap B)
without dragging in 29-bit framing (Phase 3), and it can be **seeded device-free**
from the upstream JSON — so the addressing abstraction gets a real, verifiable
target. Seed it as `profiles/xpeng-g6/` (following the `ioniq-5-2022` seed
pattern) once Phase 2's per-ECU `rx_id` lands; before then, an `rx_id: 0x784`
field has nowhere to live, so the ECU's non-standard RX must be captured in
`identity.notes` as a placeholder. All transcribed PIDs stay `--unverified`
(cross-model profile, never confirmed on this specific car).

## What's already portable (no work needed)

A lot of the design is already make-agnostic, and we should not disturb it:

- **`id_protocol`** (UDS vs KWP2000) — per-ECU, schema-enumerated
  (`valid_id_protocols`), drives protocol-aware branching everywhere
  (identity/DTC/scans) with a live-probe fallback. `ecus.py:55-67`,
  `pids_schema.yaml:125-130`.
- **`can_buses.yaml`** — per-profile bus vocabulary; the schema already
  documents Ford/BMW/VW naming. `canlib/can_buses.py`,
  `canlib/schema/can_buses_schema.yaml`.
- **DTC structural decode** — SAE J2012 / ISO 14229 Annex D.3 based; manufacturer
  *meanings* live per-profile (`dtcs:` + `failure_types:`). `canlib/dtc_describe.py`.
- **`vehicle_states.yaml`**, ECU definitions, per-ECU `response_timeout_ms` /
  `multi_did`, and the layered timeout precedence (`canlib/timeouts.py`).
- **SLCAN bitrate S0–S8 mapping** — all standard rates already implemented
  (`slcan_tcp.py:27-38`).
- **Security access** — intentionally absent (no seed/key algorithm); writes are
  hard-blocked in `canlib/safety.py`. Nothing per-make to add here.

## Two things the user asked about — confirmed findings

**CAN request timing / ELM327 init options do NOT carry over to slcan.** The
`init:` string (`ATSP6;ATS0;ATAL;ATST96;`) is applied **only** on the `wican-ws`
(ELM327) transport (`_live.py:496`). On `slcan-tcp`, `RawTerminal.init_elm()` is a
no-op (`raw_terminal.py:89`) and any `AT*` command returns a fake `OK`. Their
*intent* is reimplemented natively with **separate hardcoded knobs**:

- `ATSP6` (11-bit/500k) → hardwired `Normal_11bits` (`uds_raw.py:87`,
  `raw_terminal.py:191`) + bitrate from `transport.bitrate`. The `6` is never
  parsed.
- `ATST96` (timeout) → *partially* mirrored by `response_timeout_ms`, but the raw
  path deliberately uses its **own** recv budget (2.0s / 1.0s), not the profile
  value (ATST is too tight for multi-frame reassembly — `timeouts.py:13-17`).
- `ATS0` / `ATAL` → display/formatting; irrelevant to raw.

Additionally, ISO-TP low-level params (STmin, blocksize, flow-control timeouts,
`tx_padding=0xAA`, `tx_data_length=8`, `can_fd=False`) **used to be** hardcoded
identically in both raw clients. **Resolved in Phase 1 (`a0a0a80`):** they now
come from the shared `canlib/transport/isotp_params.py`, overridable per profile
via the `isotp:` block (the `tx_padding` kwarg is wired).

**CAN bus speed is now profile-scoped.** ~~Currently *config*-scoped~~ —
**resolved in Phase 1 (`a0a0a80`):** `profile.yaml` gained `can_bitrate:` and
`resolve_device_defaults()` resolves config `transport.bitrate` → profile
`can_bitrate` → WiCAN `can_datarate` → 500k, so switching profiles switches
bitrate. (The XPeng G6, also 500k, needs no override here.)

## Design principle: profile-driven addressing + transport params

Two new **optional** blocks, both make-agnostic, with defaults that reproduce
today's Ioniq behavior:

- **`profile.yaml`**: `can_bitrate` ✅ + `isotp` ✅ (both landed in Phase 1),
  plus a still-to-add `addressing` (default RX rule + CAN ID width). `init` stays
  (ELM-only).
- **per-ECU `ecus/*.yaml`**: optional `rx_id` and `addressing` overrides (Phase 2),
  falling back to profile default → the conventional `tx+8`. (XPeng G6: `rx_id:
  0x784` on TX `0x704`.)

Introduce a single **`EcuAddress` resolver** — `(tx_id, rx_id, mode)` — threaded
through the raw path so `RX = TX + 8` and `Normal_11bits` stop being module
constants.

## The gaps (priority order)

| # | Gap | Impact | Location | Status |
|---|-----|--------|----------|--------|
| A | **11-bit only** on active UDS path — no 29-bit (`18DAF1xx`) | Blocks many non-Hyundai makes (Ford/VAG/etc.) | `addressing.py`, `uds_raw.py`, `raw_terminal.py`, `discover.py` | ✅ done (Phase 3) |
| B | **`RX = TX + 8` hardcoded**, no per-ECU `rx_id` | Breaks irregular RX (e.g. **XPeng G6 `+0x80`**) & 29-bit | `canlib/addressing.py`, `ecus.py`, `pids.py`, raw path, `discover.py` | ✅ done (addressing abstraction; seed step 8 remains) |
| C | **No per-profile bitrate** | Must edit global config per car | `config.py`, `profile.yaml` | ✅ done (`a0a0a80`) |
| D | **Hardcoded ISO-TP params** (STmin/blocksize/FC/padding/CAN-FD) | Can't tune makes using `0x00`/`0xCC` padding or CAN-FD | `canlib/transport/isotp_params.py` | ✅ done (`a0a0a80`) |
| E | **Hyundai defaults leak into scaffolding** — `DEFAULT_INIT` w/ `ATST96` | Every new car starts Ioniq-tuned/slow | `commands/profile.py:25`, `_live.py`, `terminal.py` | ✅ done (`a0a0a80`) |
| F | **HK F1xx `-1` offset** lint heuristic hardcoded | Silently tolerates misfiled frames on non-HK | `uds_parse.py`, `quirks.py` | ✅ done (Phase 4 — profile `quirks:`) |
| G | **HK-flavored identity labels** + `0xAA` padding-strip assumption | Cosmetic / trailing garbage on odd padding | `multi_batch.py`, `identity_decode.py`, `isotp_params.py` | ✅ done (Phase 4 — `tx_padding`-driven; labels marked) |
| H | **No `profile.yaml` schema** — only `car_model`+`init` validated | New knobs won't be validated | `validate/pids.py` | ✅ done (`a0a0a80`) |
| G-I | **Extended (mixed) 11-bit addressing** (`ATCEA<nn>`), per-ECU extension byte | Blocks **BMW** (i3/528i/M340d), **Mini**, PSA | `addressing.py` (new `NORMAL_EXTENDED_11BIT`), schema, editors | ✅ done |
| G-J | **Functional-TX 29-bit + physical-RX flow control** (`0x18DB33F1`→`0x18DAF1xx`) | Blocks/undermines **Renault**, **Mitsubishi Outlander** on raw path | per-ECU FC-address override, raw path | ✅ done (`isotp_stack.py` FC override) |
| G-K | **Non-`0x18` 29-bit priority / non-derivable RX** (GM `14`, VW `17`, Volvo `1D`) | Workable via `normal_29bit` explicit ids | `addressing.py` (optional `priority:` on fixed mode); docs | ✅ done (documented + tested escape hatch) |
| G-L | **Negative `rx_offset`** (PSA `−0x20`) | PSA/Stellantis 11-bit RX | `validate/pids.py`, `tests/test_addressing.py` | ✅ done (confirmed + tested) |

## Phase 1 — Foundation: per-profile config plumbing — ✅ DONE (`a0a0a80`)

Landed additively; Ioniq unchanged because defaults reproduce the old values.

1. ✅ **`profile.yaml` schema (gap H)** — `validate/pids.py` type-checks
   `car_model`/`init`, `response_timeout_ms`, `multi_did_batching`,
   `failure_types`, plus the new `can_bitrate` and `isotp` block (range-checked,
   unknown keys rejected).
2. ✅ **Per-profile bitrate (gap C)** — `can_bitrate:` in `profile.yaml`;
   `resolve_device_defaults()` precedence config `transport.bitrate` → profile
   `can_bitrate` → WiCAN `can_datarate` → 500k. Threaded through the raw
   query/monitor/sniff paths. S0–S8 map kept.
3. ✅ **De-Hyundai the scaffolding defaults (gap E)** — `DEFAULT_INIT =
   "ATSP6;ATS0;ATAL;"` (no `ATST96`); scaffold emits commented `can_bitrate` /
   `response_timeout_ms` / `isotp` tunables. Runtime init fallback removed — a
   profile with no `init:` fails loud.
4. ✅ **Per-profile ISO-TP params (gap D)** — optional `isotp:` block
   (`stmin`/`blocksize`/`rx_flowcontrol_timeout`/`rx_consecutive_frame_timeout`/
   `tx_padding`/`tx_data_length`/`can_fd`), built once in
   `canlib/transport/isotp_params.py` and shared by both raw clients. Defaults =
   today's values.

## Phase 2 — Addressing abstraction: per-ECU rx_id + centralize the offset (gap B)

Prerequisite for 29-bit; also fixes irregular 11-bit RX mappings. **The XPeng G6
(`RX = TX + 0x80`) is the concrete, seedable driver for this phase** (see the
worked example above).

5. ✅ **Per-ECU `rx_id`** — added to `optional_ecu_fields` and surfaced in
   `load_ecus` (stored as a resolved `rx_id` on each entry). Profile-level
   `addressing.rx_offset` default added + schema-validated in `validate_meta`.
6. ✅ **`EcuAddress` resolver** — `canlib/addressing.py` (`resolve_rx`,
   `resolve_rx_offset`, `DEFAULT_RX_OFFSET`): explicit `rx_id` → profile
   `addressing.rx_offset` → `+8`. The hardcoded `+8` sites now route through it:
   `ecus.py` (`rx_for_tx`/`rx_addr_str`/`build_rx_index`/`build_rx_tx_index`/
   `resolve_tx`), `pids.build_ecu_index` (stores resolved `rx_id`), the raw path
   (`RawTerminal.rx_map`/`rx_offset`, `raw_ops`, `raw_monitor.query_ecu_addresses`),
   and `discover.py`. `RawUdsClient` already took explicit `(tx, rx)`; the
   `uds_raw.RESPONSE_OFFSET` constant now derives from `addressing`.
7. ✅ **Capture-reference resolution** — `build_rx_index`/`build_rx_tx_index`
   (used by `validate/captures.py::load_valid_rx_addrs`, `_captures_query`,
   `coverage`) and `resolve_tx` resolve RX↔ECU via the resolved `rx_id`, so
   historical captures written under the profile's offset still map. Capture
   writes (`captures.py`, `import_uds.py`, `multi_exec.py`) go through
   `rx_addr_str`, which now honors the resolved `rx_id`.
8. ✅ **Seed `profiles/xpeng-g6/`** — seeded device-free from the upstream JSON
   through the sanctioned writers (`ecus_edit`/`pids_edit`): one ECU `BMS` at TX
   `0x704`, profile `addressing.rx_offset: 0x80` (→ RX `0x784`), `can_bitrate:
   500000`, neutral init. 11 PIDs / 236 params, all `draft` + unverified. PID
   keys are `22`+DID (trailing ELM327 frame-count digit dropped). A per-ECU
   `rx_id` editor was added (`canair ecu add --rx-id` / `register_ecu(rx_id=…)`)
   to close the CLI-coverage gap for the new field.

## Phase 3 — 29-bit addressing (gap A) — ✅ DONE

9. ✅ **Profile/ECU `addressing` block** — `mode: normal_11bit | normal_29bit |
   normal_fixed_29bit | extended_29bit` (the 29-bit diagnostic convention
   `0x18DA{ecu}{tester}` / `18DB33F1` functional). `build_isotp_address` maps
   each to `isotp.AddressingMode.NormalFixed_29bits` / `Extended_29bits` /
   `Normal_29bits` / `Normal_11bits` in both raw clients (the hardwired
   `Normal_11bits` is gone). Per-ECU `addressing.mode` overrides the profile.
10. ✅ **Discovery** (`modes/discover.py`) — `discovery_targets` sweeps a 29-bit
    target-address range into `0x18DA{target}{tester}` request ids and computes
    29-bit RX; output is width-aware (`fmt_id`), and register uses the right mode.
11. ✅ **SLCAN frame TX/RX** — extended-id send already works
    (`format_slcan_frame` emits a `T`-prefixed frame); verified 29-bit ISO-TP
    frames transmit with the extended flag (`tests/test_multi_vehicle.py`).

## Phase 4 — De-Hyundai the heuristics (gaps F, G) — ✅ DONE

12. ✅ **F1xx `-1` offset** (`uds_parse.py`) — gated behind the profile
    `quirks: [hk_f1xx_minus_one]` flag (`canlib/quirks.py`); default off for new
    profiles, on for `ioniq-2017`. Terminals + `validate captures` forward it.
13. ✅ **Identity DID labels** (`modes/identity_records.py`) — the "clear marking"
    branch is satisfied (`(HK)`/`(UDS)` suffixes + docstring notes distinguish
    ISO-standard from HK-flavored). A per-profile override table was judged not
    worth the complexity for cosmetic probe hints (low priority as scoped).
14. ✅ **`0xAA` padding strip** (`multi_batch.py`) — driven by
    profile `isotp.tx_padding` via `resolve_tx_padding` instead of assuming `0xAA`
    (the functional multi-DID split path; `identity_decode`'s display strip stays
    generic across `AA`/`00`/`FF`).

## Verification (per phase) — landed

- ✅ `canair validate all` — the bundled `ioniq-2017` and seeded `xpeng-g6`
  profiles both validate; new `addressing.mode` / per-ECU `addressing` / `quirks`
  fields are schema-checked (`tests/test_validate_meta.py`,
  `tests/test_validate_pids.py`).
- ✅ **Ioniq regression:** the resolver reproduces `+8` / 11-bit / `0xAA` / 500k —
  `load_ecus()` returns `rx 0x7EC`, `mode normal_11bit` for BMS; unchanged behaviour.
- ✅ **New-profile smoke:** `canair profile create` scaffold has the neutral
  (non-`ATST96`) init (Phase 1).
- ✅ **Phase 2 / XPeng G6:** `resolve_rx`/`build_ecu_index` return `0x784` for the
  G6's `0x704` (profile `rx_offset: 0x80`) while every Ioniq ECU still resolves
  `tx+8` (`tests/test_addressing.py`).
- ✅ **29-bit (Phase 3):** `tests/test_multi_vehicle.py` drives a mock `can.BusABC`
  and asserts a 29-bit ISO-TP send emits an extended (`is_extended_id`) frame on
  `0x18DA10F1`, serialized by SLCAN as an uppercase-`T` frame; plus
  `discovery_targets` forming `0x18DA{target}{tester}` ids and `resolve_rx`
  byte-swapping the fixed-29 RX.
- ✅ **`EcuAddress` resolver unit tests** (`tests/test_addressing.py`): explicit
  `rx_id` (G6 `+0x80`), profile offset, default `+8`, and fixed-29 byte-swap; mode
  resolution precedence (per-ECU → profile → default) and `build_isotp_address`
  for every mode.
- ✅ **Phase 4:** `hk_f1xx_minus_one` quirk on/off flips the echo-mismatch
  tolerance (`tests/test_uds_parse.py`, `tests/test_validate_captures_echo.py`);
  the multi-DID split honours a non-`0xAA` `tx_padding` (`tests/test_multi_batching.py`);
  `RawTerminal` threads the mode map + quirk (`tests/test_raw_terminal.py`).

## Docs updated (per AGENTS.md policy) — landed

- ✅ `docs/concepts/profiles.md` — `addressing.mode` (11-bit vs the 29-bit modes),
  `quirks:`, and the existing `can_bitrate`/`rx_offset`/`isotp` fields; per-ECU
  `addressing.mode` note.
- ✅ `docs/bring-your-own-car/01-create-profile.md` — 29-bit (`normal_fixed_29bit`)
  setup pointer alongside the XPeng `rx_offset` example.
- ✅ `AGENTS.md` profile/ECU field references (`addressing.mode`, per-ECU
  `addressing`, `quirks`, `tx_padding`-driven padding, the echo-mismatch quirk
  wording) and `canlib/schema/pids_schema.yaml` comments; `CHANGELOG.md`
  `[Unreleased]`.
- `config.example.yaml` needs nothing — addressing/quirks live in `profile.yaml`,
  not user config. `README.md` stays pointer-only (unchanged).

## Notes / open questions

- **XPeng G6 is the preferred first non-Hyundai test profile** (replaces the old
  Kia-sheet idea): a real upstream profile, 11-bit/500k so no 29-bit dependency,
  and a genuine non-`+8` RX offset that exercises the Phase 2 addressing
  abstraction. Seed it as soon as per-ECU `rx_id` (step 5) exists.
- **Resolved — 7-digit PID convention.** The upstream G6 PIDs (`2211011`,
  `221122`, `22011A1`) are `22`+4-hex-DID + an optional trailing ELM327
  response-frame-count digit; canair PID keys drop the count digit (ISO-TP
  reassembly handles frame counting). Seeded G6 PIDs stay `draft`/unverified until
  confirmed on a car.
- **Open — G6 flow control (bench verification).** The upstream `init` sets an
  explicit FC header (`ATFCSH704;ATFCSM1`); on `wican-ws` the ELM327 firmware
  handles it, but `can-isotp`'s auto-generated flow control on the raw `slcan-tcp`
  path is untested against the G6 (a per-ECU FC-address override may be a small
  follow-up if it doesn't suffice).
- **Open — real-vehicle 29-bit confirmation.** Phase 3 is verified by synthetic
  ISO-TP/SLCAN tests (mock bus); a bench/car run against a real 29-bit
  (`normal_fixed_29bit`) vehicle to confirm end-to-end framing is still pending —
  no such profile is seeded yet.
- A Kia Soul/Niro-seeded profile (11-bit, still `+8`) remains a possible *second*
  fixture but adds little over the G6 for exercising the addressing code.
- 29-bit (Phase 3) is the single largest effort — Phase 2's addressing
  abstraction is sequenced first precisely as its foundation.

## Further compatibility findings (2026-07-29 WiCAN profile survey)

Surveyed the **full upstream corpus** (`wican-fw/vehicle_profiles/`, 79 profiles /
38 makes) — extracting each profile's `init` + per-PID `pid_init` addressing
directives — to pressure-test the addressing/transport abstractions above against
real-world makes beyond Hyundai/Kia/XPeng. Tags: **[code gap]** (needs new code),
**[authoring]** (representable today, a profile-modelling nuance), **[note]**
(informational / graceful degradation). This section is the authoritative backlog;
the physical-band subset is handled in
`plans/2026-07-29-configurable-physical-bands.md`.

### New code gaps (ranked)

- **G-I · ISO-TP extended (mixed) 11-bit addressing, per-ECU extension byte.
  [code gap]** The recurring blocker. BMW **i3** (`ATCEA07`), **528i**
  (`ATCEA18`), **M340d** (`ATCEA60`) and **Mini SE** (`ATCEA07`) all use the F-CAN
  `0x6F1` tester scheme: an 11-bit header (`ATSH6F1`/`ATCRA6xx`) **plus a
  target-address byte carried inside the ISO-TP payload** (`ATCEA<nn>`), with a
  tester address (`ATTAF1`) and an extension-prefixed FC (`ATFCSD<nn>300000`).
  The **extension byte varies per module** (07/18/60), so it must be a **per-ECU**
  field. canair's `AddressingMode` has `extended_29bit` but **no 11-bit
  extended/mixed mode** → `build_isotp_address` (`canlib/addressing.py:171`) can't
  express it. Fix: add `NORMAL_EXTENDED_11BIT` mapping to isotp
  `Extended_11bits`/`Mixed_11bits` with a per-ECU `target_address`/extension byte
  (+ schema + validate + a `pids`/`ecu add` editor field). Also used by
  **PSA/Stellantis** diagnostics. All BMW/Mini profiles are **WiCAN-PRO-only**.

- **G-J · Functional-TX 29-bit + physical-RX flow control. [code gap]**
  Requests sent on the **functional broadcast** id (`0x18DB33F1`), responses (and
  ISO-TP flow control) on a **physical** id (`0x18DAF1xx`). Seen in **Renault**
  (Megane/Scenic/R5/Master E-Tech family) and **Mitsubishi Outlander PHEV
  2023/2025**. Expressible today as `normal_29bit` with explicit
  `tx_id: 0x18DB33F1` / `rx_id: 0x18DAF1DB`, **but can-isotp addresses FC to
  `txid`** (the functional broadcast), which is wrong — FC must go to the ECU's
  physical address. Needs a **per-ECU FC-address override** on the raw path (same
  knob already flagged open for the XPeng G6). Recurs alliance-wide, so worth
  doing properly.

- **G-K · Non-`0x18` 29-bit priority + non-derivable RX. [code gap (nicety) /
  authoring]** Several makes use a 29-bit priority prefix other than `0x18` and an
  RX that is **not** the `normal_fixed` byte-swap:
  - **GM Global-A / Ultium** (`ATCP14`): TX `0x14DACBF1` → RX `0x142AF1CB` — note
    the request/response discriminator is `DA`→`2A` (not the fixed-29 `0x18DA`
    both-ways), so `normal_fixed_29bit` **cannot** model it. Spans a big family:
    **bt1** (Hummer EV, Silverado EV, Cadillac Lyriq/Celestiq, Chevrolet Blazer
    EV/Equinox EV, Acura ZDX), **GMC Sierra EV**, **Honda Prologue**.
  - **VW/Porsche MEB** (`ATCP17`): TX `0x17FC007B` / RX `0x17FE007B` (also
    **Cupra Seat Leon**, **VW ID/e-Golf/e-Up**).
  - **Volvo SPA/CMA + Zeekr** (`ATCP1D`): fully arbitrary — TX `0x1DD01635`, RX
    `0x1EC6AE80`, FC `0x1DD01635` (no derivable relation at all).
  All are representable **today** via per-ECU `addressing.mode: normal_29bit` +
  explicit `tx_id`/`rx_id` (the arbitrary-id path bakes in the priority), so
  `normal_29bit` is the essential escape hatch — **document it as the go-to for
  non-`0x18` makes.** Optional nicety: a `priority:` knob on `normal_fixed_29bit`.

### RX-offset findings (per-ECU `rx_id` is mandatory, and offsets go negative)

The `+8` assumption is Hyundai/Kia-specific; the corpus shows a **wide, per-ECU,
sometimes-negative** spread — validating the Phase-2 per-ECU `rx_id` design and
exposing one schema gap:

| offset | makes / example |
|--------|-----------------|
| **+1** | Mitsubishi i-MiEV / Peugeot iON / Citroën C-Zero triplet (`0x761→0x762`); Smart EQ (`0x792→0x793`) |
| **+8** | standard (Hyundai/Kia/BYD/MG/Chevy Bolt/Smart `0x7E4→0x7EC`) |
| **+0x20** | Nissan Leaf, Dacia Spring, Twizy, Smart (`0x79B→0x7BB`; `0x743→0x763`) |
| **+0x6A** | VW MEB 11-bit ECUs (`0x710→0x77A`, `0x746→0x7B0`) |
| **+0x400** | Opel EOBD (`0x241→0x641`) |
| **−0x20** | **PSA/Stellantis** — Fiat 600e/e-Ulysse, Peugeot e-208 (`0x6B4→0x694`, `0x6A2→0x682`, `0x6A6→0x686`) |

- **Offsets vary *within one car*** — Smart EQ mixes `+1` / `+8` / `+0x20` across
  its ECUs; PSA cars use several `0x6xx` headers all at `−0x20`. → a profile-wide
  `addressing.rx_offset` is insufficient for these; **per-ECU `rx_id` is
  required** (already supported). A uniform-offset make (PSA `−0x20`, Nissan
  `+0x20`) can still use the profile default.
- **[code gap — small] Negative `rx_offset`.** `resolve_rx` computes
  `tx_id + rx_offset` (negative works arithmetically) and `resolve_rx_offset`
  accepts any non-bool int, so `−0x20` is representable — but confirm
  `_validate_addressing` (`validate/pids.py`) doesn't reject a negative
  `rx_offset` and add a test (`tests/test_addressing.py`).

### Transport / command-set findings

- **STN/OBDLink `ST*` commands (Ford). [note / import]** Ford Focus RS / Transit
  configure the CAN ID pair with `STCAFCP726,72E` (an STN1110 command, **not**
  ELM327 AT) rather than `ATCRA`. Irrelevant to the raw `slcan-tcp` path
  (tx=`0x726`, rx=`0x72E`, i.e. `+8`), but **profile-import tooling must parse
  `STCAFCP <req>,<resp>`** to recover RX — it won't find an `ATCRA`. Also note
  Ford's zero-padded 11-bit header `ATSH000726` (= `0x726`).
- **`ATSP0` protocol auto-detect (Geely Geometry). [note]** canair has no
  auto-detect; a profile must pin the mode (11-bit default is right for Geely).
- **Custom FC cadence already matched. [note]** The near-universal `ATFCSD300000`
  + `ATFCSM1` (`30 00 00` = CTS, BlockSize 0, STmin 0) equals canair's ISO-TP
  defaults (`blocksize:0, stmin:0`, `isotp_params.py:26-27`) — no work. Hyundai
  Ioniq9 uniquely uses `ATFCSM0`; minor.
- **CAN-FD. [note / known limit]** Ultium (GM), MEB (VW), SPA2 (Volvo/Zeekr) run
  CAN-FD on internal buses, but the **OBD diagnostic bus stays classic 500 k**, so
  UDS diagnostics work unchanged; only passive FD-bus sniffing is unsupported.
- **ELM-only directives** (`ATCEA`/`ATTAF1`/`ATCP`/`ATCERF1`/`ATCAF1`/`ATH1`/
  `ATSP*`) are honoured only on `wican-ws`; on `slcan-tcp` their intent is
  reimplemented natively — except **`ATCEA`/`ATTAF1` (extended 11-bit) which has
  no raw-path equivalent yet** (gap G-I).

### Service-ID / PID-key findings

- **Services beyond `0x22`. [note]** `0x21` group reads (Toyota Hilux `21291`,
  Smart `21083`, Nissan Leaf `21018`, Kia Niro/Soul `21014`, Renault Twizy
  `21035`, Mitsubishi/PSA triplet `2101`), OBD mode `0x01` (Chevy Volt, Chrysler
  Pacifica, Renault Megane, Toyota Rav4, `generic.json`), and KWP `0x1A`
  ReadEcuId (Opel EOBD `1ADF`/`1A6D`). Raw query sends the bytes fine;
  identity/DTC auto-probing (UDS `0x22` / KWP `0x1A`) **degrades gracefully** (NRC
  → "not supported") — worth a doc note that discovery helpers are UDS/KWP-centric.
- **PID-key conventions. [authoring]** The trailing ELM327 frame-count digit is
  **corpus-wide** (`2201019`, `2211011`, `2227C42`) and **inconsistent within a
  profile** (VW mixes `22028C` and `2227C42`), so import can't blindly strip the
  7th char — disambiguate by DID validity. VW's `221E3B1E3D` is a **multi-DID**
  request (`22 1E3B 1E3D`). Kia Kona has a literal **`remove`** PID key (an
  upstream edit sentinel) — importers must skip it.

### Cross-reference — physical bands (800 V roster)

The corpus strongly reinforces the 800 V case in
`plans/2026-07-29-configurable-physical-bands.md`: **Porsche Taycan**, **Zeekr
001**, the **E-GMP** cars (Hyundai Ioniq 5/6/9, Kia EV6, Genesis GV60/GV70/G80),
and 800 V-capable **GM Ultium / VW PPE** (Macan EV, Q6 e-tron) all run packs above
the built-in `HV pack V` 300–450 band. Confirms `physical_bands.hv_pack` as a
real, in-catalog need — not a hypothetical.

### Net

The abstractions from Phases 1–4 hold up well across 38 makes: `normal_29bit`
(explicit ids) absorbs GM/VW/Volvo/Zeekr, per-ECU `rx_id` absorbs the wild offset
spread, `id_protocol`/graceful-probe absorbs non-`0x22` services, and the FC
cadence already matches. The **two genuine new code gaps** — **G-I (extended
11-bit — BMW/Mini/PSA)** and **G-J (functional-TX flow control —
Renault/Mitsubishi)** — plus **G-K (documented `normal_29bit` escape hatch)** and
the **negative-`rx_offset` check (G-L)** are all now **implemented** (see the
Status header, 2026-07-29). Remaining follow-ups are import-time niceties:
parsing of `STCAFCP` / the `remove` sentinel / multi-DID PID keys.
