---
# Paths are relative to the repository root; references/can/ is gitignored (local only).
title: "Functional (0x7DF) UDS requests: routing and ECU handling"
subject: >-
  How the 11-bit functional request 0x7DF reaches each bus of the 2017 Ioniq Electric and how its
  UDS and KWP2000-style ECUs answer, time and suppress replies to it
created: 2026-10-30
updated: 2026-10-30
status: active
verification: >-
  Unverified on this car. Firmware of three Ioniq ECUs (CCM, VESS, AVN), third-party Ioniq and
  Kona logs, Hyundai/Kia's ES95486-00 and patents; section 7 is the on-car test.
sections:
  - section: "7"
    status: open
    note: >-
      Needs a functional-request command that shows every responder, or the ELM327 REPL with
      headers on.
relations:
  expands_on:
    - path: profiles/ioniq-2017/research/IGPM_FINDINGS.md
      note: Section 4 (diagnostics through the gateway, the Kona routing table).
    - path: profiles/ioniq-2017/research/IONIQ_AVN_FW_FINDINGS.md
      note: The AVN MICOM's diagnostic stack.
  expanded_by:
    - path: profiles/ioniq-2017/research/HKMC_UDS_SPEC_FINDINGS.md
      note: What ES95486 says beyond functional addressing.
  followed_up_by:
    - path: plans/2026-10-30-functional-addressing-followups.md
      note: Responder sweep, separate request padding, functional keepalive.
sources:
  - name: CCM code flash (uhi22 readout)
    kind: Ioniq firmware, MPC5605B AUTOSAR stack, statically analysed
    location: references/can/ccm-ghidra/
  - name: VESS flash (uhi22 readout)
    kind: Ioniq firmware, MC9S12G, statically analysed
    location: references/can/vess-ghidra/
  - name: AVN head-unit MICOM
    kind: Ioniq firmware, RH850 EU image
    location: sibling repository ioniq-avn-fw (tmp/functional-7df-notes.md)
  - name: uhi22 Ioniq28Investigations
    kind: 2018 Ioniq 28 P-CAN trace with an OBD-II poller; EPCU bench logs with Torque Pro
    url: https://github.com/uhi22/Ioniq28Investigations
    files:
      - ioniq2018_motorCAN_500k_HPC.asc
      - EPCU_torquePro*.csv
  - name: Kona EV isolated-IGPM sweep and Car Scanner D-CAN logs
    location: references/can/igpm-research/
  - name: ES95486-00 "UDS on CAN", revision 30 (2025-10-16)
    kind: Hyundai/Kia specification, local uncommitted copy only
  - name: Patents
    kind: design intent only
    url:
      - https://patents.google.com/patent/KR20200136751A
      - https://patents.google.com/patent/KR20170141877A
      - https://patents.google.com/patent/US10129124B2
---

# Functional (0x7DF) UDS requests: how they reach each bus and how ECUs handle them

This document collects what firmware reverse-engineering and third-party bus logs say about UDS
*functional* requests on the 2017 Ioniq Electric: the 11-bit broadcast request ID `0x7DF`, sent by a
tester on the OBD-II port (D-CAN) and answered by every ECU that accepts it on its own physical
response ID. It is written for canair to triage, the same way as `IGPM_FINDINGS.md` and
`IONIQ_AVN_FW_FINDINGS.md`. Nothing here has been checked on this car yet; section 7 is the test
that would do it.

## Provenance and how much to trust it

Evidence classes, strongest first:

- **Ioniq firmware, statically analysed (2026-10-30).** Three ECUs of this car's generation:
  - **CCM** (charge control module, `0x733`/`0x73B`, P-CAN): MPC5605B, an AUTOSAR stack. uhi22's
    code-flash readout, in the local Ghidra project `references/can/ccm-ghidra/`.
  - **VESS** (`0x736`/`0x73E`, P-CAN): MC9S12G, a hand-written stack. uhi22's flash readout, in
    `references/can/vess-ghidra/` (`NOTES.md` has the addresses).
  - **AVN head unit MICOM** (`0x780`/`0x788`, MM-CAN): RH850, EU image, in the sibling repo
    `ioniq-avn-fw` (`tmp/functional-7df-notes.md`).
- **Bus logs of other Ioniqs:** uhi22's 2018 Ioniq 28 P-CAN trace with an OBD-II poller on the
  port (`ioniq2018_motorCAN_500k_HPC.asc`), and his EPCU (VCU + MCU) bench logs with Torque Pro
  connected straight to the EPCU's CAN (`EPCU_torquePro*.csv`).
- **Kona EV (same gateway supplier and generation):** Pelland-Coupe's isolated-IGPM ID sweep and the
  Car Scanner D-CAN logs in `references/can/igpm-research/` (see `IGPM_FINDINGS.md` section 4).
- **Hyundai/Kia's own diagnostic spec, ES95486-00 "UDS on CAN" (revision 30, 2025-10-16).** A
  Hyundai/Kia internal document, found as a PDF in a third-party GitHub repo; kept as a local,
  uncommitted copy only. It is the rulebook the UDS ECUs are built to, not a description of this
  car, and revision 30 is about ten years newer than the car (see section 4).
- **Hyundai/Kia/Yura patents** (KR20200136751A, KR20170141877A, US10129124B2): design intent only.

Firmware images, the Kona logs and the spec carry no redistribution licence: cite facts, never
commit them or copy their text and tables.
No Hyundai/Kia gateway firmware is public, so the gateway's own routing table is known only from the
Kona bench, not from code. Tags: **confirmed** (code or this generation's bus data), **probable**,
**speculative**.

## 1. The gateway forwards 0x7DF unchanged; there is no per-bus translation

- **No translation (confirmed, Kona; probable, Ioniq).** The Kona IGPM forwards `0x7DF` from D-CAN
  to P-, C- and B-CAN with ID and payload unchanged, frame by frame, about 0.5 ms later (median),
  even with a payload that is not valid ISO-TP. It answers the request itself on `0x778`. It does
  not reassemble, re-address or rewrite anything.
- **Every endpoint listens for 0x7DF itself (confirmed).** The CCM's CanIf RX table, the VESS's
  MSCAN ID table and the AVN's RS-CAN RX table all list `0x7DF` next to their own physical request
  ID. None has any other functional or gateway-diagnostic ID. KR20200136751A states the 11-bit
  functional ID "is fixed at 0x7DF", and ES95486 lists `0x7DF` as the only functional request ID
  and requires every ECU to accept it. A per-bus functional ID would show up in these tables; none
  does.
- **0x7DF reaches P-CAN on an Ioniq (probable).** uhi22's 2018 Ioniq P-CAN trace carries an OBD-II
  poller's `7DF 02 01 00` / `02 01 0D` (DLC 8, `00` padding) and the replies. The EV's P-CAN is not
  wired to the OBD-II connector, so the frames came through the gateway, assuming the poller was in
  the port (the trace does not say).
- **0x7DF probably reaches MM-CAN too.** In a Kona Car Scanner log one functional `19 02 08`
  is answered within 27 ms by 16 IDs, including `0x788` and `0x78B` (the Ioniq ID plan's AVN and
  AMP, both MM-CAN), which appear in none of the Kona D→P/C/B routing tables. The AVN firmware
  accepting `0x7DF` fits; it cannot itself prove the route.
- **Routing is per ID and per bus.** A module can answer `0x7DF` on its own bus and still be
  invisible from the OBD port if the gateway has no route for its response ID (Kona `0x71D`,
  `0x74E`). A functional sweep therefore finds exactly the ECUs whose *response* IDs are routed to
  D-CAN.

## 2. What the ECUs do with a functional request

| | CCM (AUTOSAR) | AVN MICOM | VESS (S12) | KWP2000-style powertrain ECUs |
|---|---|---|---|---|
| Profile ECUs | CCM; probably the other UDS ECUs on newer stacks | AVN | VESS | VCU, MCU, BMS, OBC, AAF (`id_protocol: KWP2000`) |
| Multi-frame request on 0x7DF | rejected (FF dropped) | rejected | **accepted** | not tested |
| DLC must be 8 | yes | yes | yes | — |
| Response ID | own (`0x73B`) | own (`0x788`) | own (`0x73E`) | own (`0x7EA`–`0x7ED`) |
| Flow control for a multi-frame reply | on the physical request ID (`0x733`) | on `0x780`; FC on `0x7DF` times out | accepted on either ID | on the physical ID (Torque sent `0x7E2`/`0x7E3`) |
| Suppresses NRC 0x11/0x12/0x31 | yes | yes | yes | **no** (`7F 13 12`, `7F 18 12`, `7F 19 12` on the EPCU bench; `7F 19 11/12/31` on the Kona) |
| Suppresses NRC 0x7E/0x7F | yes | no (ISO 14229-1:2006 behaviour) | never generates them | — |
| Honours the suppressPosRsp bit | yes | yes (sub-function services) | yes (sub-function services) | — |
| Functional request while busy | dropped, except `3E 80` | dropped silently | no busy state: overwrites | — |
| Functional `3E 80` | refreshes S3, no reply, even while busy | refreshes S3 (≈5 s), no reply | refreshes S3 (≈5 s), no reply | — |
| Service refused only because functional | none | none | none | — |
| TX padding | `0xAA` | `0xAA` | `0xAA` | `0x00` |
| Checks request padding | no | no | — | — |

Addresses for every CCM row: CanIf RX table `0x29A8C` (10-byte records; `0x7DF` → CanTp RX
N-PDU 1, `0x733` → N-PDU 0); CanTp RX N-SDU config `0x29B3A` (6-byte records, byte 4 = functional,
byte 5 = padding required), frame check `0x37BA4`; DCM busy/`3E 80` path `0x3D186`/`0x3D332`; NRC
suppression `0x3DB7E`/`0x3E240`; manufacturer-notification hook `0x30416`, which accepts every
request. The CCM bootloader (`0x0`–`0x4FFF`) also filters `0x7DF` in a FlexCAN mailbox. That fits
the GDS reflash pattern of sending the preparation steps (session, communication control, DTC
setting) functionally, though that pattern is not documented for this car.

**Two diagnostic families answer the same broadcast differently.** The KWP2000-style powertrain
ECUs answer OBD-II services (`01`, `03`, `07`, `09`, `0A`) and KWP DTC reads (`18 00 FF` → `58`)
on `0x7DF`, and return NRCs that ISO 14229 says to suppress. The UDS ECUs stay silent on anything
they don't support. OBD-II `01 00` is answered by the MCU (`0x7EB`) and the BMS (`0x7EC`) only,
on the bench and in the car trace, and nobody answers `01 0D` (speed).

## 3. Timing and contention

- **Replies to one broadcast overlap.** In the Kona log 16 replies arrive within 27 ms; on the EPCU
  bench two multi-frame DTC replies interleave and the tester answers each ECU's first frame with
  its own flow control within 0.3–1 ms. A tester must track one ISO-TP receive per response ID.
- **A burst can silence the slow buses (observation, Kona).** After one functional `19 02 08`, the
  next two (85 ms apart) went unanswered by the IGPM and every B-CAN and MM-CAN responder. Part is
  explained: the IGPM and one C-CAN ECU had sent a first frame that never got flow control and were
  waiting out the timeout. Why single-frame responders also went quiet is not explained (the IGPM
  pausing its 100 kbit/s relays, or those ECUs being busy: speculative). Space functional requests
  at least about 1 s apart.
- **The VESS mis-sends long replies** (confirmed in code): one consecutive frame per flow control
  and the sequence number stuck at 1, so a reply over 13 bytes breaks a standard ISO-TP receiver.
  That applies to physical requests as well.
- **The gateway is not always a passive relay** (patents, design intent): US10129124B2 has it adjust
  the STmin in relayed flow control under bus load; KR20200136751A has an in-car diagnostic client
  (telematics) stop diagnosing until ignition-off once an external tester starts; KR20170141877A
  (Yura) blocks routed writes and actuations once security mode is on. The AE has no security
  gateway (see `IGPM_FINDINGS.md` section 4).

## 4. What Hyundai/Kia's diagnostic spec says (ES95486-00)

Facts from the spec, paraphrased. **Which revision applies:** the Ioniq's ECUs were designed
around 2015-2016, i.e. to roughly revisions 5-10. The revision history shows that the
external-tester start-up rule (revision 3, 2014-01) and the gateway-related STmin rules (revisions
3-7, 2014-2015) predate the car. Functional addressing was "clarified" in revision 12 (2017-05), so
details of the response rules may differ slightly on this car; the ECU firmware above is the
evidence for what it actually does.

**Addressing and frames**

- Physical request IDs are `0x700`-`0x7DE` (plus `0x580`-`0x5FF` for CAN-FD), response = request +
  8. The powertrain block is fixed: `0x7E2` VCU/HCU, `0x7E3` MCU, `0x7E4` BMS, `0x7E5` OBC "from
  AE/DE EV/PHEV", `0x7E6` shift-by-wire unit, `0x7E7` integrated brake on newer cars. Every other
  ECU's ID lives in internal databases the spec only refers to, so it cannot identify
  `Unknown-746` or `Unknown-7C0`.
- Functional addressing is for single frames only; the reply comes from the ECU's own response ID
  and may be multi-frame.
- DLC is always 8. A frame with a shorter DLC is ignored by the network layer, exactly what the
  CCM, AVN and VESS firmware do.
- **Requests are padded with `0x55`, responses with `0xAA`.** No ECU firmware checked tests the
  request padding, so a tester padding with `0xAA` (canair's default) works, but it is not to spec.
- ISO-TP timeouts are 1000 ms (N_As, N_Ar, N_Bs, N_Cr); block size at most 8.

**Response rules for a functional request** (the ISO 14229-1 tables, adopted unchanged)

- No reply for NRCs 0x11, 0x12, 0x31, 0x7E and 0x7F. A response-pending reply (NRC 0x78) is always
  followed by a final reply, whatever the suppress bit says.
- Every ECU must support every diagnostic service on both its physical ID and `0x7DF`. That matches
  the firmware: no service in any of the three is refused only because it was functional.

**Concurrency and keepalive**

- An ECU handles one request at a time; anything arriving meanwhile, physical or functional, is
  not processed.
- The one exception is a functional `3E 80` (TesterPresent with the suppress bit). It is handled by
  a bypass so that it never blocks the ECU and may be sent at any time. All three ECU firmwares
  implement this bypass.
- Testers should send TesterPresent functionally rather than per ECU, every 2 s (4 s at most).
  An ECU leaves a non-default session after 5 s without a request (tolerance +200 ms).

**Timing**

- An ECU starts its reply within 50 ms, or within 5000 ms after a response-pending NRC.
- The tester's wait (P2 client) adds the delay of every gateway in the path.
- Before the next functional request, the tester waits for the slowest ECU's 50 ms. It waits longer
  if it does not know which ECUs will answer.
- Behind a gateway on high-speed CAN, the flow-control STmin may be 0.3-12 ms (0-8 ms with no
  gateway). An ECU told less than 0.3 ms still leaves 0.3 ms between consecutive frames.
- If normal traffic cannot be switched off, the tester may stretch STmin to 10-12 ms.
- In practice this means: no back-to-back functional requests.

**A functional request stops in-car testers (section 2.6 of the spec)**

- Some ECUs poll other ECUs themselves (the spec calls this on-board diagnostics, ECU to ECU), and
  could collide with an external tester using the same request IDs.
- So a tester's first message after connecting should be a functional `10 81` (default session,
  no reply wanted). Any ECU that receives any functional request must stop sending diagnostic
  requests of its own.
- This rule dates from revision 3 (2014-01), so it applies to this car. It is the rule behind the
  KR20200136751A behaviour in section 3, and it means a single functional frame from canair quiets
  whatever in-car poller the Ioniq has. Which of its ECUs poll is not known.

**Reprogramming sequence (section 10)**

- The preparation steps go functionally to all ECUs, with no replies:
  - `10 81`, then `10 90` (extended session; `10 03` also allowed);
  - `85 82` (DTC setting off);
  - `28 02` (stop normal messages);
  - `3E 80` repeated as the keepalive.
- Programming session `10 85`, security access, download and reset go physically to the target.
- Normal traffic is restored with a functional `29 02` within 15 ms of the reset's positive reply,
  or 400-500 ms when the gateway itself was reprogrammed.
- This is why the gateway must copy `0x7DF` onto every bus: `28 02` has to reach every ECU.
- It also explains the CCM's service table: `0x20` and `0x29` (the KWP-era "stop session" and
  "enable normal messages", kept for compatibility), session codes `0x81`, `0x85` and `0x90`
  (`0x90` being the KWP-compatible extended session).

**Two protocol families**

- ES95486 is the UDS spec. It keeps `0x90` and `0x29` "to be compatible with ES95485-00", the older
  KWP-on-CAN spec. That is the split seen in section 2: VCU, MCU, BMS, OBC and AAF follow the KWP
  family and return NRCs to functional requests; the UDS ECUs stay silent.
- The end-of-line batch DTC clear sends both families' clears on `0x7DF`:
  - UDS: `10 03`, then `14 FF FF FF`;
  - KWP: `10 90`, then `14 FF 00`.
  It expects ECUs of the other family to answer with NRC 0x12, 0x13 or 0x31 rather than stay
  silent. Factory testers therefore live with NRCs on functional requests, at least in that mode.

**What the spec does not cover**

- How the gateway copies `0x7DF` onto each bus. That belongs to the gateway's own spec. This one
  only requires every ECU to accept `0x7DF`, which presumes the gateway delivers it to every bus.
- The routing table itself. The test in section 7 is still the only way to confirm the MM-CAN
  route on this car.

## 5. Rules for a tester on this car

1. Send functional requests as single frames, DLC 8, on `0x7DF`. Pad with `0x55` to follow the spec;
   the ECUs examined accept any padding.
2. Collect replies on every physical response ID; send each ECU's flow control on its own physical
   request ID (response − 8), never on `0x7DF`.
3. Expect silence, not an NRC, from UDS ECUs for unsupported requests; expect NRCs from the KWP2000
   powertrain ECUs.
4. Don't send a functional request while a physical transfer to any ECU is in flight, and leave
   about 1 s between functional requests.
5. Functional `3E 80` keeps every UDS ECU's non-default session alive with one frame and no
   replies. It is the spec's recommended keepalive; canair currently sends a physical `3E 00` per
   ECU.
6. Any functional frame tells in-car pollers to stop (section 4). Starting a session with a
   functional `10 81`, as the spec asks, is cheap and avoids collisions with them.

## 6. Consequences for canair

Follow-ups are planned in `plans/2026-10-30-functional-addressing-followups.md`.

- canair's raw path pads requests with `0xAA` (`isotp.tx_padding`) and its ELM327 path addresses
  flow control to the responder's physical ID, so both meet rules 1 and 2. To follow the spec, the
  request padding should be `0x55`. The same profile key is also used as the *response* padding to
  strip, so the two need separate keys first.
- `discover` sweeps physical IDs and skips `0x7DF`. A functional `3E 00` (or `22 F18C`) sweep would
  list every routed responder in one request: a cheap cross-check of `discover`, and the way to map
  which buses the gateway serves.
- A functional `3E 80` keepalive for multi-ECU sessions is possible. The KWP2000 ECUs' handling of
  it is not established (KWP2000's own TesterPresent sub-functions are `01`/`02`), so keep per-ECU
  keepalives for them until it is tested.

## 7. Test on this car

Read-only. Parked, ignition on (so IG3-fed ECUs are awake):

1. Functional `3E 00` once, with response IDs shown, and compare the responder set with the 30
   physical responders from the 2026-04-17 `discover`. Responders per bus show which buses the
   gateway forwards `0x7DF` to.
2. Functional `03 19 02 08` once: `0x788` (AVN) or `0x78B` (AMP) answering confirms the MM-CAN
   route.
3. The same request again more than 1 s later, then 100 ms later: whether the B-CAN/MM-CAN replies
   drop out only in the fast case.

canair has no functional request command that shows every responder yet (`raw` expects a single
reply; the REPL's `!tester` broadcasts `3E 00` but prints only counts), so this needs either that
feature or the REPL with ELM327 headers on.
