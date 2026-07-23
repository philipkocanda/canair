## PID coverage gaps (audit 2026-07-20)

Systematic pass over all ECU PIDs in `pids/`, cross-referenced against the
longest captured payload per PID. Byte indices are WiCAN Bnn (PCI + SID + DID
echo excluded). **Regenerate the authoritative, up-to-date list any time with
`canair coverage`** (`--bitfields`, `--unmapped`, `--no-capture`,
`--json`). The curated notes below add priority/context; the tool output is the
source of truth for exact byte lists.

## MCU / VCU 2102 decode — Phase 2 (warm synced drive)

Phase 1 (2026-07-20) mined the existing 332 MCU / 1347 VCU captures and added
**unverified** candidates (all `verified: false`; speculative ones `status: draft`):

- **MCU 2102**: `MCU_MOTOR_TORQUE_2` `[S14:S15]/100`, `MCU_PHASE_CURRENT_RMS` `[B17:B18]/10`
  (in addition to existing RPM ✓ / torque / temp1 / temp2). Calibration block B27–B33
  documented as static (resolver + U/V phase offsets), not polled.
- **VCU 2102**: `VCU_TORQUE_REQUEST` `[S12:S13]` (0 at park, ±31k driving; meaning/scale unknown).

Phase 2 needs the car. Goal: confirm scales/meanings and fill the remaining gaps
(re-run `python3 pid-coverage.py MCU 2102` / `VCU 2102` to see current gaps).

- [ ] **Capture** — dense, back-to-back, minimal skew, and long enough to **warm the inverter**:
  `canair query "query VCU:2101" "query VCU:2102" "query MCU:2102" --monitor 1 --keep-all --save --wican vpn`
  Drive profile: hard launches, lift-off/regen, braking, a reverse segment, sustained cruise
  to heat components; plus one charging capture.
- [ ] **MCU torque** — determine which of `[S12:S13]` / `[S14:S15]` is command vs estimated;
  correlate against pedal (VCU 2101 `ACCEL_PEDAL_DEPTH`) + dRPM incl. regen (signed must go negative).
- [ ] **MCU phase current** — confirm `[B17:B18]` scale (/10→A guess); ~0 at park, rises with |torque|.
- [ ] **MCU temps** — B20/B21 raw °C vs `value−40`; separate motor / inverter / heat-sink with a warm
  drive. Check B22/B25 (52..248 raw, track RPM) as a possible 3rd temp or a different quantity.
- [ ] **MCU B52** — identify the highly-dynamic byte (card=180) near the tail.
- [ ] **VCU torque/power** — pin down `VCU_TORQUE_REQUEST` `[S12:S13]` (torque request vs available torque
  vs power) by joining VCU 2101 speed/pedal + MCU 2102 torque.
- [ ] **VCU/MCU EWP** — look for electric water pump speed / target RPM (Soul VMCU field; 0..190 byte that
  spins up under load/charge; candidates B22/B25/B26). EPCU coolant loop exists despite air-cooled battery.
- [ ] **VCU temps** — B20/B27 (~51 raw cold ≈ 11°C if `value−40`) confirm with warm drive.

## Drive mode (Eco/Normal/Sport) + regen level — investigation

Status: NOT located in any polled PID as of 2026-07-20.

### Findings (from July 19 drive analysis)
- VCU 2101 `DEBUG_DRIVE_MODE_FLAGS` (B10) = constant `0x20` base + gear nibble only:
  P=0x21, R=0x22, N=0x24, D=0x28. Bits 0-3 = P/R/N/D; **bit5 always 1; bits 4/6/7 always 0**
  across all 577 samples -> no dynamic eco/regen captured.
- No 4-state (regen 0-3) byte in VCU 2101, MCU 2101, or MCU 2102. Low-cardinality bytes only
  track brake / park-vs-drive frame state.
- Kia Soul VMCU sheet maps the drive-mode byte as: bit4="B", bit5=Eco (INVERTED: (bit-1)*-1),
  bit6=Charge Timer (inverted). By that, our constant bit5=1 = "Eco off / Normal" the whole drive
  -- but unconfirmed (never toggled) and the Soul sheet has NO regen-level field either.

### Where it might be
- **CLU cluster 0x7C6** (dash shows mode + regen): only `22B002` (odo) decoded; `22B001`/`22B003`
  return live-but-undecoded bytes -> prime candidate.
- **SWRC-L/R 0x7A1/0x7A2** (steering-wheel controls): regen paddles are steering-wheel mounted ->
  strongest lead for regen level. Not yet scanned for paddle state.
- **GSA 0x7B6** (gear shift assembly): SCANNED 2026-07-20 — responds ONLY to `220100`. Bytes
  B19/B21/B22 drifted ~30 between two P-gear captures (likely lever hall sensors or temp, not gear).
  Holds gear/lever data; drive-mode button is centre-console (could route here or via BCM/IGPM),
  regen paddles are NOT here (steering wheel -> SWRC).
- Unscanned VCU/MCU `21 03-FF` or a 2017-specific 22xxxx PID. (Ioniq 5 `22E006` drive mode /
  `22E007` regen return NRC 0x12 here.)
- May not be exposed via OBD reads at all (only broadcast on internal CAN).

### Controlled capture plan (needs car, safe while parked in Ready)
1. Monitor VCU 2101 while pressing Drive Mode button Eco->Normal->Sport->Eco; watch B10 (e.g. D
   0x28->0x08?) or any bit flip -> confirms/locates the mode bit.
2. Monitor VCU 2101 + MCU 2101/2102 while cycling regen paddles 0->1->2->3; watch for a byte stepping 0-3.
3. If nothing there: scan CLU 0x7C6 `22B000-22B0FF` in each mode/regen state and diff to find the byte.
   Also scan SWRC-L/R (0x7A1/0x7A2) while pulling the regen paddles.
4. Also monitor GSA 0x7B6 `220100` while shifting P/R/N/D (decode lever sensors B19/B21/B22) and while
   pressing the drive-mode button.
5. Broaden: `21 03-FF` scan on VCU (0x7E2) and MCU (0x7E3).
