# Generalizing canair for other vehicles & makes (incl. 29-bit)

## Goal

Make canair drive **any** modern CAN vehicle purely from a profile, removing the
Hyundai/Ioniq-specific hardcoding that currently leaks through the raw
(`slcan-tcp`) transport and a handful of defaults. Target scope is **full
generalization including 29-bit diagnostic addressing** (`18DAF1xx`), delivered
**incrementally** — foundation first, 29-bit last — so each phase is
independently shippable and the bundled `ioniq-2017` profile keeps working
unchanged at every step.

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
(ELM327) transport (`_live.py:486`). On `slcan-tcp`, `RawTerminal.init_elm()` is a
no-op (`raw_terminal.py:89`) and any `AT*` command returns a fake `OK`. Their
*intent* is reimplemented natively with **separate hardcoded knobs**:

- `ATSP6` (11-bit/500k) → hardwired `Normal_11bits` (`uds_raw.py:91`,
  `raw_terminal.py:198`) + bitrate from `transport.bitrate`. The `6` is never
  parsed.
- `ATST96` (timeout) → *partially* mirrored by `response_timeout_ms`, but the raw
  path deliberately uses its **own** recv budget (2.0s / 1.0s), not the profile
  value (ATST is too tight for multi-frame reassembly — `timeouts.py:13-17`).
- `ATS0` / `ATAL` → display/formatting; irrelevant to raw.

Additionally, ISO-TP low-level params (STmin, blocksize, flow-control timeouts,
`tx_padding=0xAA`, `tx_data_length=8`, `can_fd=False`) are hardcoded identically
in both raw clients (`uds_raw.py:81-89`, `raw_terminal.py:73-81`) with no profile
plumbing (the `tx_padding` kwarg exists but is never wired).

**CAN bus speed is a small profile addition — but it's currently *config*-scoped,
not *profile*-scoped.** Bitrate resolves from the global `transport:` block (or a
WiCAN's `can_datarate`), fallback 500k (`config.py:140`). `profile.yaml` has no
bitrate field, so switching profiles won't switch bitrate.

## Design principle: profile-driven addressing + transport params

Two new **optional** blocks, both make-agnostic, with defaults that reproduce
today's Ioniq behavior:

- **`profile.yaml`**: `can_bitrate`, `addressing` (default RX rule + CAN ID
  width), `isotp` (flow-control / padding / CAN-FD). `init` stays (ELM-only).
- **per-ECU `ecus/*.yaml`**: optional `rx_id` and `addressing` overrides, falling
  back to profile default → the conventional `tx+8`.

Introduce a single **`EcuAddress` resolver** — `(tx_id, rx_id, mode)` — threaded
through the raw path so `RX = TX + 8` and `Normal_11bits` stop being module
constants.

## The gaps (priority order)

| # | Gap | Impact | Location |
|---|-----|--------|----------|
| A | **11-bit only** on active UDS path — no 29-bit (`18DAF1xx`) | Blocks many non-Hyundai makes (Ford/VAG/etc.) | `uds_raw.py:91`, `raw_terminal.py:198` |
| B | **`RX = TX + 8` hardcoded**, no per-ECU `rx_id` | Breaks irregular RX & 29-bit | `uds_raw.py:30-36`, `ecus.py:135/170/334`, `discover.py:104`, `validate/captures.py`, `raw_terminal.py` |
| C | **No per-profile bitrate** | Must edit global config per car | `config.py:120-140`, `profile.yaml` |
| D | **Hardcoded ISO-TP params** (STmin/blocksize/FC/padding/CAN-FD) | Can't tune makes using `0x00`/`0xCC` padding or CAN-FD | `uds_raw.py:81-89`, `raw_terminal.py:73-81` |
| E | **Hyundai defaults leak into scaffolding** — `DEFAULT_INIT` w/ `ATST96` (3 places) | Every new car starts Ioniq-tuned/slow | `commands/profile.py:22`, `_live.py:428`, `terminal.py:273` |
| F | **HK F1xx `-1` offset** lint heuristic hardcoded | Silently tolerates misfiled frames on non-HK | `uds_parse.py:238-251` |
| G | **HK-flavored identity labels** + `0xAA` padding-strip assumption | Cosmetic / trailing garbage on odd padding | `modes/identity_records.py`, `multi_batch.py:69`, `identity_decode.py:33` |
| H | **No `profile.yaml` schema** — only `car_model`+`init` validated | New knobs won't be validated | `pids_schema.yaml:295`, `validate/pids.py` |

## Phase 1 — Foundation: per-profile config plumbing

Additive; Ioniq keeps working because defaults reproduce today's values.

1. **`profile.yaml` schema (gap H)** — validate `profile.yaml` (extend
   `validate/pids.py:validate_meta` or add a small `profile_schema.yaml`).
   Declare/validate `car_model`, `init`, `response_timeout_ms`,
   `multi_did_batching`, `failure_types`, plus new `can_bitrate`, `addressing`,
   `isotp`.
2. **Per-profile bitrate (gap C)** — add `can_bitrate:` to `profile.yaml`. Thread
   the loaded profile into `TransportConfig.resolve_device_defaults()`
   (`config.py:120-140`); precedence: config `transport.bitrate` → profile
   `can_bitrate` → WiCAN `can_datarate` → 500k. Keep the S0–S8 map.
3. **De-Hyundai the scaffolding defaults (gap E)** — drop `ATST96` from the
   generic `DEFAULT_INIT`; `profile create` emits a neutral 11-bit init and a
   commented `response_timeout_ms` tunable. Remove the duplicated hardcoded init
   fallbacks (`_live.py:428`, `terminal.py:273`); read strictly from profile,
   error clearly if absent.
4. **Per-profile ISO-TP params (gap D)** — add optional `isotp:` block (`stmin`,
   `blocksize`, `rx_flowcontrol_timeout`, `rx_consecutive_frame_timeout`,
   `tx_padding`, `tx_data_length`, `can_fd`). Thread into the two `_params` dicts
   and the `tx_padding` kwargs. Defaults = today's values.

## Phase 2 — Addressing abstraction: per-ECU rx_id + centralize the offset (gap B)

Prerequisite for 29-bit; also fixes irregular 11-bit RX mappings.

5. **Per-ECU `rx_id`** — add to `optional_ecu_fields` (`pids_schema.yaml:76`) and
   surface in `load_ecus` (`ecus.py:29-43`).
6. **`EcuAddress` resolver** — one helper computing `(tx_id, rx_id, mode)`:
   explicit `rx_id` → profile `addressing.rx_offset` → `tx+8`. Replace the ~8
   hardcoded `+8` sites (`ecus.py:135/170/334`, `uds_raw.py:30-36`,
   `discover.py:104/121`, `validate/captures.py:16/76`, `raw_terminal.py`) with
   calls to it. `RawUdsClient` already takes `(tx, rx)` tuples — feed resolved rx.
7. **Capture-reference resolution** (`parse_ecu_ref`) resolves RX→ECU via the
   resolver, not a bare `+8`, so historical captures still map.

## Phase 3 — 29-bit addressing (gap A)

8. **Profile/ECU `addressing` block** — support `mode: normal_11bit |
   extended_29bit | normal_fixed_29bit` and the 29-bit diagnostic convention
   (`0x18DA{ecu}{tester}` / `18DB33F1` functional). Map to
   `isotp.AddressingMode.NormalFixed_29bits` / `Extended_29bits` in both raw
   clients (replace the hardwired `Normal_11bits`).
9. **Discovery** (`modes/discover.py`) — support sweeping a 29-bit diagnostic
   range + computing 29-bit RX; register with the right addressing mode.
10. **SLCAN frame TX/RX** — verify the extended-ID send path (receive already
    parses extended IDs, `slcan_tcp.py:67-69`); ensure 29-bit ISO-TP frames
    transmit with the extended flag.

## Phase 4 — De-Hyundai the heuristics (gaps F, G)

11. **F1xx `-1` offset** (`uds_parse.py:238-251`) — gate behind a profile flag
    (e.g. `identity.quirks: [hk_f1xx_minus_one]`); default off for new profiles,
    on for `ioniq-2017`.
12. **Identity DID labels** (`modes/identity_records.py`) — split ISO-standard
    from HK-flavored labels; per-profile overrides or clear marking. Low priority
    (cosmetic).
13. **`0xAA` padding strip** (`multi_batch.py:69`, `identity_decode.py:33`) —
    drive the pad byte from profile `isotp.tx_padding` instead of assuming `0xAA`.

## Verification (per phase)

- `canair validate all` — new profile schema passes; Ioniq unchanged.
- **Ioniq regression:** `uv run canair query "query BMS:2101"` and
  `canair identity BMS` produce identical results (defaults reproduce `+8` /
  11-bit / `0xAA` / 500k).
- **New-profile smoke:** `canair profile create testcar` → scaffold has neutral
  (non-`ATST96`) init.
- **29-bit:** synthetic ISO-TP test (mock `can.BusABC`) asserting `18DAF1xx`
  framing, and/or a bench/other-car profile.
- **Unit tests** for the `EcuAddress` resolver: explicit rx_id, profile offset,
  default +8, 29-bit fixed.

## Docs to update (per AGENTS.md policy)

- `docs/` bring-your-own-car pages: new `can_bitrate` / `addressing` / `isotp`
  profile fields, per-ECU `rx_id`, 29-bit setup.
- `config.example.yaml` + `profile.yaml` comments; `AGENTS.md` profile/ECU field
  references; `canair profile create` scaffolding notes.
- Keep `README.md` pointer-only.

## Notes / open questions

- A second test profile seeded from the Kia Soul/Niro reference sheets could
  exercise the generalized code without a physical car (11-bit, still `+8`).
  Worth doing once Phase 2 lands.
- 29-bit (Phase 3) is the single largest effort — Phase 2's addressing
  abstraction is sequenced first precisely as its foundation.
