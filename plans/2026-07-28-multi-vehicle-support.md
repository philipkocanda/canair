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
