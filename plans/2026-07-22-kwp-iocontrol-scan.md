# Plan: KWP2000 IOControl (SID 0x30) scanning + SID-name interpretation + scan-command consolidation

Status: DONE — code + tests green (1227 passed; `canair validate pids` clean).
        Stage 1 (registry) + Stage 2 (unified engine) landed first; KWP 0x30 built on
        the clean engine. CLI shape 3a (keep command names, auto-detect id_protocol).
        Remaining: on-car scan of the BMS fan LID (user-driven). Implementation notes
        + known follow-ups appended at the end of this file.
Motivation: manually control the BMS air-cooling fan (28 kWh air-cooled pack) by
discovering the KWP2000 InputOutputControlByLocalIdentifier local identifier that
actuates it, *safely* (enumerate without triggering). Along the way, name service
IDs in the scan UI and stop the scan-command family from sprawling.

---

## 0. Background — the crux

KWP2000 IOControl is a **different service** from UDS IOControl:

| Protocol | Service | Name | Identifier width | Request layout | Safe probe |
|----------|---------|------|------------------|----------------|------------|
| UDS (ISO 14229)   | `0x2F` | InputOutputControlByIdentifier      | 16-bit DID | `2F {DID_HI}{DID_LO} {IOCP}` | `2F {DID} 00` |
| KWP2000 (ISO 14230-3) | `0x30` | InputOutputControlByLocalIdentifier | **8-bit LID** | `30 {LID} {IOCP} [state…]` | `30 {LID} 00` |
| UDS RoutineControl | `0x31` | RoutineControl                      | 16-bit RID | `31 {SF} {RID_HI}{RID_LO}` | `31 03 {RID}` |

- BMS/VCU/MCU/LDC/AAF are `id_protocol: KWP2000` (see `profiles/ioniq-2017/ecus.yaml`).
  They almost certainly answer UDS `2F` with `NRC 0x11 serviceNotSupported`, so the
  existing `canair iocontrol-scan` (0x2F only) **cannot reach the BMS fan**. The
  backlog entry `bms.yaml` `type: iocontrol_scan target: "2F E000-E0FF"` is
  misdirected for that reason.
- Positive response to `30` is `0x70`.
- **Safety:** IOCP `0x00 returnControlToECU` is side-effect-free (hands control back
  to the ECU; drives nothing). This is the *same principle* the current
  `iocontrol_scan.py` relies on for UDS `2F` SF `0x00`. HKMC's own convention
  corroborates the encoding: existing YAML actuates with `…03` (shortTermAdjustment)
  and releases with `…00` (e.g. `igpm.yaml` `on: 2FBC0303` / `off: 2FBC0300`).
  So `30 {LID} 00` enumerates IOControl-capable LIDs without actuating.
- **Residual risk:** the "safe" claim rests on IOCP `0x00` being benign on these
  specific ECUs. Standard and consistent with the existing scanner, but KWP2000 ECUs
  can have quirks — so the scanner is *enumerate-only* (hard-refuses any IOCP ≠ 0x00),
  and any actual fan actuation stays a separate, deliberate, one-LID-at-a-time step
  (consult the Kingbolen note then if the empirical result is ambiguous).

---

## 1. Consolidation analysis (do this thinking BEFORE tacking on)

### 1.1 What exists today (the "scan family")

| Command | Mode | Service | Classify + writeback + resume? | Notes |
|---------|------|---------|-------------------------------|-------|
| `canair scan` | `modes/scan.py` | any (presets 21/22/2F/31) | **no** — dumb range dump | wizard, smart per-ECU plan, `--save` to captures |
| `canair iocontrol-scan` | `modes/iocontrol_scan.py` | 0x2F SF00 | yes → `iocontrol_discoveries:` | 16-bit DID |
| `canair routines-scan` | `modes/routines_scan.py` | 0x31 SF03 | yes → `routines:` | 16-bit RID |
| `canair discover` | `modes/discover.py` | 0x10 sweep | (own thing) | TX address sweep 700–7EF |

### 1.2 The mess

`iocontrol_scan.py` and `routines_scan.py` are ~95% identical scaffolding:
probe → `classify()` → hit list → `ScanStateWriter` → lazy extended-session upgrade
on NRC 0x7F → per-hit/end writeback → summary. Adding KWP 0x30 the naive way makes a
**third near-verbatim copy**. They differ only in:

1. Service SID + safe sub-function + **request byte order**
   (`2F{id}{sf}` vs `31{sf}{id}` vs `30{id}{sf}`).
2. Identifier width (8-bit LID vs 16-bit DID/RID).
3. Small differences in the "exists vs absent" NRC sets.
4. Writeback section name + writer function + entry-key format.
5. Cosmetic labels ("DID"/"RID"/"LID", titles).

Service knowledge is *also* scattered: `BLOCKED_UDS_SERVICES` + `NRC_CODES`
(`elm327.py`), `SERVICE_PRESETS` (`scan_presets.py`), per-mode sub-function
constants (`SF_*`), and (soon) SID names. No single source of truth.

### 1.3 Recommendation (staged — least-regret first)

**Stage 1 — Service registry as the single source of truth (do now, small).**
New pure-data module `canlib/uds_services.py` (mirrors `identity_records.py`) holding,
per service byte: name, family (UDS/KWP2000), and — where relevant — id width, the
safe discovery sub-function, and actuation risk. `scan_presets.SERVICE_PRESETS`,
`elm327.BLOCKED_UDS_SERVICES`, the SID-name display (Part A), and the discovery-probe
configs (Stage 2) all *derive from* this one table instead of re-declaring facts.

**Stage 2 — One parametrized discovery-scan engine (do now, medium).**
New `canlib/modes/discovery_scan.py` with a `DiscoveryProbe` config
(service, safe SF, id width, request-layout builder, exists/absent NRC sets,
writeback section + writer, labels) and a single `scan_ecu()` / `mode_discovery_scan()`
loop. Reimplement iocontrol(0x2F) / routines(0x31) / kwp-iocontrol(0x30) as three
`DiscoveryProbe` instances — data, not copies. `ScanStateWriter` already takes an
arbitrary `scan_type` string, so resume works unchanged. Keep the existing mode
entrypoints as thin shims for back-compat during the transition; delete the
duplicated bodies once tests pass.

**Stage 3 — CLI shape (DECISION NEEDED — pick before building the KWP command).**
Three options for how the safe scanners are exposed:

- **3a. Keep command names, unify engine only (recommended default).** `iocontrol-scan`
  / `routines-scan` stay; add KWP by auto-detecting `id_protocol` in the
  `iocontrol-scan` dispatch (UDS→0x2F, KWP→0x30) so `canair iocontrol-scan BMS` "just
  works". Lowest risk; skills/docs keep working; no muscle-memory break.
- **3b. Smart-route through `canair scan`.** Make `canair scan BMS --service iocontrol`
  pick the safe *discovery* engine and the right service by protocol; turn the
  standalone `*-scan` commands into thin aliases. More unified surface, mild breakage.
- **3c. `canair scan` command-group with subcommands** (`canair scan iocontrol BMS`,
  `scan routines IGPM`, `scan range …`). Cleanest long-term taxonomy; biggest churn
  (docs, skills, tests, tab-completion).

Proposed: **3a now** (ship the fan capability fast on a de-duplicated engine),
revisit 3b/3c as a follow-up once the engine is unified and we can see the seams.

---

## 2. Part A — SID-name library + scan UI

1. `canlib/uds_services.py` (Stage-1 registry): `service_name(sid) -> str | None`
   and `service_response_name(resp_sid)` (handles `+0x40` positive response and
   `0x7F` negative). Covers ISO 14229 + the KWP2000 SIDs used here: `0x10, 0x14,
   0x18/0x19, 0x1A ReadEcuIdentification, 0x21 ReadDataByLocalIdentifier, 0x22,
   0x27, 0x2E, 0x2F, 0x30 InputOutputControlByLocalIdentifier, 0x31, 0x3B
   WriteDataByLocalIdentifier, 0x3E`, …
2. Enrich `scan_presets.service_label()` to fall back to `service_name()` so any SID
   is named: `service_label(0x30)` → `InputOutputControlByLocalIdentifier (0x30)`.
   Flows into the scan header (`modes/scan.py`), plan summary (`commands/scan.py`),
   and the wizard service table automatically.
3. Name the negative-response service byte (`nrc_service`) in `modes/scan.py` /
   `modes/raw.py` output.
4. Tests: update `tests/test_scan.py` (`service_label(0x18)` now returns a named
   form) + add `tests/test_uds_services.py`.

## 3. Part B — Safe KWP2000 IOControl (0x30) discovery (auto-detected)

Built as a `DiscoveryProbe` on the Stage-2 engine:

- Service `0x30`, safe IOCP `0x00`, hard-refuse any other IOCP.
- Request layout `30 {LID:02X} 00`; `expected_sid=0x30`; response `0x70`.
- 8-bit LID range (default `00–FF`).
- Reuse classification (positive / exists-hints 22/33/12/78 / absent 0x11,0x31 /
  wrong-session 0x7F), throttle, session/wake, `ScanStateWriter("iocontrol-kwp", …)`.
- Writeback to a KWP-aware `iocontrol_discoveries:` — LID keys are 2-hex-digit
  (`01`, `A0`). Extend `pids_edit.append_iocontrol_discoveries_block` + the validator
  scanner-section check (`commands/validate.py`) + the LID-span regex in `pids_edit.py`
  to accept 2-digit keys alongside 4-digit DIDs.
- Dispatch (Stage 3a): in `_live.dispatch_mode`, when `iocontrol_scan` targets a
  KWP2000 ECU (via `ecus.ecu_id_protocol`), run the 0x30 probe; else the 0x2F probe.
  Update `commands/iocontrol_scan.py` help to state protocol auto-selection.
- Add a `SERVICE_PRESETS` entry `iocontrol-kwp` (0x30, narrow LID range,
  `needs_session`, caution) so `canair scan BMS --service iocontrol-kwp` and the
  wizard list it; smart-plan can pick it for KWP ECUs.
- Repoint `bms.yaml` fan backlog entry from `2F E000-E0FF` to the 0x30 LID approach.

---

## 4. Verification

- `python3 -m pytest -q` and `canair validate pids` green.
- On-car (user-driven), car in a safe state / engine off:
  `canair iocontrol-scan BMS` → sends only `30 {LID} 00`. Hunt the fan by validating
  one candidate LID at a time and listening for the fan; scanner never sends IOCP 0x03.

## 5. Decisions (locked)

1. **Consolidation depth** — Stage 1 (service registry) + Stage 2 (unified discovery
   engine) land FIRST; iocontrol(0x2F)/routines(0x31) become `DiscoveryProbe` configs;
   KWP 0x30 is then added on the clean engine.
2. **CLI shape** — 3a: keep `iocontrol-scan` / `routines-scan` names; auto-detect
   `id_protocol` so `canair iocontrol-scan BMS` uses 0x30. Revisit 3b/3c later.

---

## 6. Implementation notes (as built)

- **New files:** `canlib/uds_services.py` (service registry), `canlib/modes/discovery_scan.py`
  (parametrized engine + `DiscoveryProbe`), `canlib/modes/kwp_iocontrol_scan.py`
  (0x30 probe), `tests/test_uds_services.py`, `tests/test_kwp_iocontrol_scan.py`.
- **Refactored:** `modes/iocontrol_scan.py` + `modes/routines_scan.py` now build a
  `DiscoveryProbe` and delegate to the shared engine (public API preserved, so their
  existing tests pass unchanged). `scan_presets.service_label()` falls back to the
  registry; added the `iocontrol-kwp` preset (+ `kwp-io`/`iokwp` aliases).
  `commands/_live.py` splits an `iocontrol-scan` ECU list by `id_protocol` and runs
  0x2F / 0x30 accordingly. `commands/iocontrol_scan.py` help updated.
  `modes/scan.py` names the rejecting `nrc_service` in verbose output.
- **Writeback:** `pids_edit.append_iocontrol_discoveries_block(..., key_width=2)` for
  8-bit LIDs; 2-digit keys are **quoted** (`"30":`) so all-digit LIDs don't parse as
  YAML ints/octal; the entry parser tolerates quoted/unquoted keys.
- **Data:** BMS fan backlog entry repointed `2F E000-E0FF` → `30 00-FF`.

## 7. Known follow-ups (not blocking)

- `pids_edit._find_iocontrol_did_span` still assumes 4-digit keys — extend to 2-digit
  for CLI editing of KWP discoveries.
- CLI Stage 3b/3c (fold the `*-scan` commands under `canair scan`) now that the engine
  is shared.
- Derive `SERVICE_PRESETS` / `elm327.BLOCKED_UDS_SERVICES` from `uds_services.py` to
  fully collapse the remaining scattered service knowledge.
