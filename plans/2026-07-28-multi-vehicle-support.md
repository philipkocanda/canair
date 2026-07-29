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
  handles frame counting). **Step 8 (seed `profiles/xpeng-g6/`) remains.**
- **Phases 2 (step 8) – 4 — remaining.** The next concrete driver is the **XPeng
  G6** seed (step 8, now unblocked), then 29-bit (Phase 3).

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
| A | **11-bit only** on active UDS path — no 29-bit (`18DAF1xx`) | Blocks many non-Hyundai makes (Ford/VAG/etc.) | `uds_raw.py:87`, `raw_terminal.py:191` | open (Phase 3) |
| B | **`RX = TX + 8` hardcoded**, no per-ECU `rx_id` | Breaks irregular RX (e.g. **XPeng G6 `+0x80`**) & 29-bit | `canlib/addressing.py`, `ecus.py`, `pids.py`, raw path, `discover.py` | ✅ done (addressing abstraction; seed step 8 remains) |
| C | **No per-profile bitrate** | Must edit global config per car | `config.py`, `profile.yaml` | ✅ done (`a0a0a80`) |
| D | **Hardcoded ISO-TP params** (STmin/blocksize/FC/padding/CAN-FD) | Can't tune makes using `0x00`/`0xCC` padding or CAN-FD | `canlib/transport/isotp_params.py` | ✅ done (`a0a0a80`) |
| E | **Hyundai defaults leak into scaffolding** — `DEFAULT_INIT` w/ `ATST96` | Every new car starts Ioniq-tuned/slow | `commands/profile.py:25`, `_live.py`, `terminal.py` | ✅ done (`a0a0a80`) |
| F | **HK F1xx `-1` offset** lint heuristic hardcoded | Silently tolerates misfiled frames on non-HK | `uds_parse.py:238-251` | open (Phase 4) |
| G | **HK-flavored identity labels** + `0xAA` padding-strip assumption | Cosmetic / trailing garbage on odd padding | `modes/identity_records.py`, `multi_batch.py:69`, `identity_decode.py:33` | open (Phase 4) |
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
8. **Seed `profiles/xpeng-g6/`** — once 5–7 land, seed the G6 device-free from the
   upstream JSON (ECU TX `0x704`, `rx_id: 0x784` or `addressing.rx_offset: 0x80`,
   `can_bitrate: 500000`, neutral init) as the regression fixture for non-`+8`
   addressing. All PIDs `--unverified`/`draft`. **PID convention resolved** (see
   Status): PID keys are `22`+DID (e.g. `221101`, `221122`); the upstream 7th
   digit is the ELM327 response-frame count, dropped for canair's ISO-TP path.

## Phase 3 — 29-bit addressing (gap A)

9. **Profile/ECU `addressing` block** — support `mode: normal_11bit |
   extended_29bit | normal_fixed_29bit` and the 29-bit diagnostic convention
   (`0x18DA{ecu}{tester}` / `18DB33F1` functional). Map to
   `isotp.AddressingMode.NormalFixed_29bits` / `Extended_29bits` in both raw
   clients (replace the hardwired `Normal_11bits`).
10. **Discovery** (`modes/discover.py`) — support sweeping a 29-bit diagnostic
    range + computing 29-bit RX; register with the right addressing mode.
11. **SLCAN frame TX/RX** — verify the extended-ID send path (receive already
    parses extended IDs, `slcan_tcp.py:67-69`); ensure 29-bit ISO-TP frames
    transmit with the extended flag.

## Phase 4 — De-Hyundai the heuristics (gaps F, G)

12. **F1xx `-1` offset** (`uds_parse.py:238-251`) — gate behind a profile flag
    (e.g. `identity.quirks: [hk_f1xx_minus_one]`); default off for new profiles,
    on for `ioniq-2017`.
13. **Identity DID labels** (`modes/identity_records.py`) — split ISO-standard
    from HK-flavored labels; per-profile overrides or clear marking. Low priority
    (cosmetic).
14. **`0xAA` padding strip** (`multi_batch.py:69`, `identity_decode.py:33`) —
    drive the pad byte from profile `isotp.tx_padding` instead of assuming `0xAA`.

## Verification (per phase)

- `canair validate all` — new profile schema passes; Ioniq unchanged.
- **Ioniq regression:** `uv run canair query "query BMS:2101"` and
  `canair identity BMS` produce identical results (defaults reproduce `+8` /
  11-bit / `0xAA` / 500k).
- **New-profile smoke:** `canair profile create testcar` → scaffold has neutral
  (non-`ATST96`) init. ✅ (Phase 1 done.)
- **Phase 2 / XPeng G6:** with `profiles/xpeng-g6/` seeded (TX `0x704`, `rx_id:
  0x784`), the `EcuAddress` resolver returns `0x784` for the G6 ECU while still
  returning `tx+8` for every Ioniq ECU. Unit test both. On a bench/car,
  `canair --profile xpeng-g6 query 704:2211011` reads SOC over the raw path.
- **29-bit (Phase 3):** synthetic ISO-TP test (mock `can.BusABC`) asserting
  `18DAF1xx` framing, and/or a bench/other-car profile.
- **Unit tests** for the `EcuAddress` resolver: explicit rx_id (G6 `+0x80`),
  profile offset, default +8, 29-bit fixed.

## Docs to update (per AGENTS.md policy)

- `docs/` bring-your-own-car pages: new `can_bitrate` / `addressing` / `isotp`
  profile fields, per-ECU `rx_id`, 29-bit setup.
- `config.example.yaml` + `profile.yaml` comments; `AGENTS.md` profile/ECU field
  references; `canair profile create` scaffolding notes.
- Keep `README.md` pointer-only.

## Notes / open questions

- **XPeng G6 is the preferred first non-Hyundai test profile** (replaces the old
  Kia-sheet idea): a real upstream profile, 11-bit/500k so no 29-bit dependency,
  and a genuine non-`+8` RX offset that exercises the Phase 2 addressing
  abstraction. Seed it as soon as per-ECU `rx_id` (step 5) exists.
- **Open — 7-digit PID convention.** The upstream G6 PIDs (`2211011`, `221122`,
  `22011A1`) are longer than a plain `22`+DID. Decode how WiCAN parses the
  trailing digit(s) (read the in-repo `wican-fw/` source) before transcribing, or
  the seeded DIDs will be wrong. Until resolved, keep them as `draft`/unverified.
- **Open — G6 flow control.** The upstream `init` sets an explicit FC header
  (`ATFCSH704;ATFCSM1`); on `wican-ws` the ELM327 firmware handles it, but confirm
  `can-isotp`'s auto-generated flow control suffices on the raw `slcan-tcp` path
  (a per-ECU FC-address override may be a small follow-up if not).
- A Kia Soul/Niro-seeded profile (11-bit, still `+8`) remains a possible *second*
  fixture but adds little over the G6 for exercising the addressing code.
- 29-bit (Phase 3) is the single largest effort — Phase 2's addressing
  abstraction is sequenced first precisely as its foundation.
