# Vehicle protocol & addressing notes

Cross-vehicle reference for the CAN / UDS **diagnostic protocol** differences,
quirks, and incompatibilities observed across makes and models. It documents
*how a car's ECUs are addressed and talked to* — CAN ID width, addressing scheme,
request/response IDs, flow control, and diagnostic service IDs — so profile
authors know what to expect from a given make before wiring one up.

Scope is deliberately narrow: **facts about the vehicles**, not about any tool.
It says nothing about what is or isn't supported anywhere — only what each car
does on the wire.

## Source & caveats

The addressing facts here are derived from the diagnostic initialisation strings
(ELM327 `AT*` / STN `ST*` commands) published in the community **WiCAN firmware**
`vehicle_profiles/` corpus —
<https://github.com/meatpiHQ/wican-fw/tree/main/vehicle_profiles> — cross-read
against ISO 15765-4 (CAN transport) and ISO 14229 (UDS). Each entry cites the
observed request/response CAN IDs and the directives that set them.

Caveats:

- These are **community-contributed** profiles and occasionally contain internal
  inconsistencies (e.g. an 11-bit protocol select `ATSP6` paired with a 29-bit
  header). Where seen, the literal directive is reported rather than "corrected".
- A profile proves a car answered *those* IDs for *those* signals; it is not an
  exhaustive ECU map. Absence here means "not observed", not "does not exist".
- Model/year coverage reflects the `car_model` label on each profile.

## How to read this

**CAN ID width**
- **11-bit** — standard OBD-II IDs like `0x7E0`. Selected with `ATSP6`.
- **29-bit** — extended IDs like `0x18DAF110`. Selected with `ATSP7`.

**Addressing schemes** (how the request/response arbitration IDs are formed):
- **Normal 11-bit** — a fixed request ID and a fixed response ID (e.g. request
  `0x7E0`, response `0x7E8`). The response is often (not always) request **+ 8**.
- **Normal-fixed 29-bit** — the ISO convention `0x18DA{target}{tester}` for a
  physical request, answered on `0x18DA{tester}{target}` (the two low address
  bytes swap). `0x18DB33F1` is the *functional* (broadcast-to-all) request ID.
- **Functional request → physical response** — the request is sent to the
  functional broadcast ID (`0x18DB33F1`) but the ECU answers from its physical
  ID (`0x18DAF1{target}`).
- **Extended / mixed 11-bit** — an 11-bit header (commonly `0x6F1`) plus a
  **target-address byte carried inside the ISO-TP payload** (`ATCEA{nn}`), with a
  tester address (`ATTAF1`). Each module is picked by its extension byte.

**Key directives**
- `ATSH{id}` — request (source/tx) header. `ATCRA{id}` — response filter (rx).
- `ATCP{nn}` — the 29-bit **priority/format** byte (top byte of the extended ID).
- `ATCEA{nn}` — ISO-TP **extended-address** (target) byte. `ATTA{nn}` — tester.
- `ATFCSH/ATFCSD/ATFCSM` — ISO-TP **flow-control** header / data / mode. The
  near-universal `ATFCSD300000` + `ATFCSM1` sets a flow-control frame of
  `30 00 00` (ClearToSend, BlockSize 0, STmin 0).
- `STCAFCP {req},{resp}` — an **STN1110** (not ELM327) command that sets the
  request/response CAN ID pair. Used by some Ford profiles instead of `ATCRA`.
- `ATSP0` — automatic protocol detection (no fixed width/scheme declared).

**Diagnostic service / mode IDs** (first byte of a request):
- `0x22` — UDS ReadDataByIdentifier (DID). `0x19`/`0x18` — read DTCs.
- `0x21` — manufacturer-specific "read data by local ID" (common on Nissan,
  Toyota, Mitsubishi, older PSA/Renault).
- `0x1A` — KWP2000 ReadEcuIdentification.
- `0x01` — legislated OBD-II mode 01 (live PIDs).

**PID key convention.** Many profiles append a trailing digit to a `22`+DID key
(e.g. `2201019`, `2211011`) encoding the *expected ELM327 response frame count*;
it is **inconsistent within a single profile** (some keys omit it). A longer key
can also encode a **multi-DID** request — e.g. VW `221E3B1E3D` = request
`22 1E3B 1E3D` (two DIDs at once).

---

## Cross-make quick reference

### Addressing-scheme families

| Scheme | Makes/platforms observed |
|--------|--------------------------|
| Normal 11-bit (`0x7Ex`-style) | Hyundai/Kia/Genesis, BYD, MG, Chevrolet Bolt, Opel Ampere-e, Subaru, Toyota, Maxus, KGM, Jaguar, GWM, Geely |
| Normal-fixed 29-bit (`0x18DA…`) | Nissan Ariya, Honda Clarity, RAM Promaster, Renault Zoe Ph2 |
| Functional-request → physical-response (`0x18DB33F1`→`0x18DAF1xx`) | Renault (Megane/Scénic/R5/Master E-Tech family), Mitsubishi Outlander PHEV 2023/2025 |
| 29-bit, custom priority + explicit RX | GM Ultium/Global-A (`0x14…`), VW/Porsche MEB (`0x17…`), Volvo/Zeekr (`0x1D…`) |
| Extended / mixed 11-bit (`0x6F1` + `ATCEA`) | BMW (i3, 528i, M340d), Mini SE |

### 11-bit response-ID offsets (response − request)

The "+8" offset is common but far from universal:

| Offset | Example (request → response) | Makes |
|--------|------------------------------|-------|
| **+1** | `0x761 → 0x762`; `0x792 → 0x793` | Mitsubishi i-MiEV / Citroën C-Zero / Peugeot iON triplet; Smart EQ |
| **+8** | `0x7E0 → 0x7E8`; `0x7E4 → 0x7EC` | Hyundai/Kia, BYD, MG, Chevrolet Bolt, Ford, many others |
| **+0x20** | `0x79B → 0x7BB`; `0x743 → 0x763` | Nissan Leaf, Dacia Spring, Renault Twizy, Smart EQ |
| **+0x40** | `0x78A → 0x7CA` | GWM (Ora) |
| **+0x6A** | `0x710 → 0x77A`; `0x746 → 0x7B0` | VW / Porsche MEB (11-bit ECUs) |
| **+0x400** | `0x241 → 0x641` | Opel (EOBD body ECU) |
| **−0x20** | `0x6B4 → 0x694`; `0x6A2 → 0x682` | PSA / Stellantis (Fiat, Peugeot) |

Some cars use **several offsets across different ECUs in the same vehicle** (see
Smart EQ, VW MEB).

### 29-bit priority/format byte (`ATCP`)

| Priority | Platform | Request / response example |
|----------|----------|----------------------------|
| `0x14` | GM Ultium / Global-A | `0x14DACBF1` → `0x142AF1CB` |
| `0x17` | VW / Porsche MEB, Cupra | `0x17FC007B` → `0x17FE007B` |
| `0x18` | ISO standard (Nissan, Honda, RAM, Renault Zoe Ph2, Porsche 11-bit) | `0x18DA…` |
| `0x1D` | Volvo (SPA) / Zeekr | `0x1DD01635` → `0x1EC6AE80` |

---

## By make

### BMW / Mini
- **BMW i3, 528i, M340d; Mini SE Electric.** Extended/mixed **11-bit**
  addressing: common request header `0x6F1`, tester address `0xF1` (`ATTAF1`),
  and a per-module **ISO-TP extended-address byte** (`ATCEA{nn}`). The response
  filter is `0x600 + {extension byte}`:
  - i3 / Mini SE: extension `0x07` → response `0x607`.
  - 528i: extension `0x18` → response `0x618`.
  - M340d: extension `0x60` → response `0x660`.
  Flow-control data is prefixed with the extension byte (`ATFCSD{nn}300000`). All
  four profiles are labelled *WiCAN PRO only*. Services: `0x22`.

### BYD
- **Dolphin/Shark; Atto 2/3, Yuan Plus, Seal/Seal U, Sealion, M6, eMax 7, Denza
  D9/N7 (EU, pre-2024.10).** Normal 11-bit, request `0x7E7` → response `0x7EF`
  (+8). Service `0x22`.

### Chevrolet / Chrysler
- **Chevrolet Bolt.** 11-bit, request `0x7E4` (→ `0x7EC`, +8). Service `0x22`.
- **Chevrolet Volt/Ampera.** Legislated OBD-II **mode 01** only.
- **Chrysler Pacifica (2015+).** OBD-II **mode 01**.

### Ford
- **Focus RS Mk3; Transit (NA 2022).** 11-bit. Uses the **STN command
  `STCAFCP{req},{resp}`** to set the CAN ID pair instead of `ATCRA` — e.g.
  `STCAFCP726,72E` (`0x726 → 0x72E`) and `STCAFCP7E0,7E8` (`0x7E0 → 0x7E8`).
  Request headers are zero-padded (`ATSH000726`). CAN auto-formatting on
  (`ATCAF1`), short timeout (`ATST32`). Service `0x22`.

### Geely / GWM
- **Geely Geometry C.** **`ATSP0`** — automatic protocol detection (no fixed
  width declared). Request `0x7E2`. Service `0x22`.
- **GWM Ora (Good Cat / Funky Cat / ES11 / Haomao / Ora 03).** 11-bit, **+0x40**
  offset: `0x78A → 0x7CA`, `0x78B → 0x7CB`, `0x76C → 0x7AC`. Service `0x22`.

### GM (Ultium / Global-A) & GMC
- **BT1 / BEV3 platform:** Hummer EV, Silverado EV, Sierra (AV); Cadillac Lyriq,
  Celestiq; Chevrolet Blazer EV, Equinox EV; Honda Prologue; Acura ZDX. **GMC
  Sierra EV.** 29-bit, **priority `0x14`** (`ATCP14`): request `0x14DACBF1` →
  response `0x142AF1CB`. Note the request/response discriminator is the second
  byte (`DA` → `2A`) *and* the low address bytes swap (`CB F1` → `F1 CB`) — it is
  **not** the plain `0x18DA…` byte-swap. Service `0x22`.
- **GMC Sierra (2004+).** Functional 11-bit request `0x7DF` → `0x7E8`; also KWP
  service `0x1A`.

### Honda
- **Clarity (2018).** Normal-fixed **29-bit**: request headers `0x18DA01F1`,
  `0x18DA60F1` (target modules `0x01`, `0x60`; tester `0xF1`). Service `0x22`.
- **Prologue.** GM Ultium addressing (see GM): `0x14DACBF1` → `0x142AF1CB`.

### Hyundai / Kia / Genesis
- **Hyundai** Ioniq (2016–2019 HEV; 2017; Electric 38 kWh 2020–2021), Ioniq 5/6
  (72/77 kWh, 2021–2024), Ioniq 9, Kona; **Kia** EV6, Niro EV/PHEV, Niro/Soul;
  **Genesis** GV60, GV70 EV, G80 Electrified. Normal **11-bit**, **+8** offset.
  Typical headers: powertrain/battery `0x7E4` (→ `0x7EC`), `0x7E2`, `0x7E5`,
  `0x7B3`, `0x7A0`, `0x7C6`, `0x730`. Services `0x22` and `0x21` (e.g. `2101`).
  Quirk: some identity DIDs answer one *less* than requested (request `22F188` →
  response `62F187`). Kia EV6 profiles list only the init header (`0x7E4`).
  One Hyundai Kona profile contains a literal **`remove`** PID key (an upstream
  editing sentinel, not a real request).

### Jaguar
- **I-PACE.** 11-bit, service `0x22` (`0x2249xx` battery DIDs). Response IDs
  default (no explicit `ATCRA`).

### Maxus / KGM
- **Maxus T90 / EUNIC / EV30 / EG10 / eDeliver 3/9.** 11-bit, request `0x7E3`.
  Service `0x22`.
- **KGM (SsangYong) Torres EVX.** 11-bit, request `0x7E0`. Service `0x22`.

### MG
- **MG5 / Marvel / ZS.** 11-bit, request `0x7E5` → response `0x7ED` (+8, explicit
  `ATCRA7ED`). Service `0x22`.

### Mitsubishi
- **i-MiEV / Outlander PHEV (older).** 11-bit, request `0x761` → response `0x762`
  (**+1**). Service `0x21` (`2101`). Shares addressing with the Citroën C-Zero /
  Peugeot iON triplet.
- **Outlander PHEV 2023 / 2025.** **29-bit functional**: request `0x18DB33F1` →
  response `0x18DAF1DB`. Service `0x22`.

### Nissan / Renault / Dacia (Alliance)
- **Nissan Ariya.** Normal-fixed **29-bit**: request `0x18DADBF1` → response
  `0x18DAF1DB`. Service `0x22`.
- **Nissan Leaf (ZE1).** 11-bit, request `0x79B` → response `0x7BB` (**+0x20**).
  Service `0x21` (`21018`, the battery controller).
- **Nissan Patrol / Armada Y62.** 11-bit, request `0x7E0`. Service `0x22`.
- **Renault Megane E-Tech family** (Scénic EV, R5, R4, Master EV; label also
  lists Ariya). **29-bit functional**: request `0x18DB33F1` → response
  `0x18DAF1DB`. Service **mode 01** (`015B1`).
- **Renault Zoe** Ph2 (2020–): **29-bit**, priority `0x18` (`ATSP7;ATCP18`),
  headers not pinned per-PID; service `0x22`. R110/R90: **11-bit** (`ATSP6`),
  service `0x22`.
- **Renault Kangoo Z.E.** 11-bit, service `0x22`, default response IDs.
- **Renault Twizy.** 11-bit, request `0x79B` → response `0x7BB` (**+0x20**).
  Service `0x21` (`21035`).
- **Dacia Spring / City K-ZE.** 11-bit, request `0x79B` → response `0x7BB`
  (**+0x20**), long timeout (`ATST64`). Service `0x22`.

### Opel
- **Ampere-e / Ampere-e 2019** (Bolt sibling). 11-bit, request `0x7E4`. Service
  `0x22`. (Init lists multiple `ATSH` headers.)
- **Astra.** 11-bit, request `0x79B`. Service `0x22`.
- **Opel EOBD (2004–present).** Mixed services: KWP `0x1A` (`1ADF`/`1A6D`) on
  `0x7E0 → 0x7E8` (+8); UDS `0x22` on `0x241 → 0x641` (**+0x400** offset).

### PSA / Stellantis
- **Fiat 600e, e-Ulysse; Peugeot e-208.** 11-bit, **negative** response offset:
  `0x6B4 → 0x694`, `0x6A2 → 0x682`, `0x6A6 → 0x686` (all **−0x20**). Several
  distinct `0x6xx` ECU headers per vehicle. Service `0x22`.
- **Citroën C-Zero; Peugeot iON.** 11-bit, request `0x761` → response `0x762`
  (**+1**). Service `0x21`. (Mitsubishi i-MiEV triplet.)

### Porsche / Volkswagen (MEB, and PPE via MEB profile)
- **VW MEB / Audi / Škoda / Cupra / Porsche Macan EV / Ford Explorer EV** (ID.3–7,
  ID.Buzz, Enyaq, Elroq, Q4/Q6 e-tron, Cupra Born/Tavascan, Capri EV). **Mixed
  widths within one vehicle:**
  - 29-bit ECUs, **priority `0x17`**: request `0x17FC007B` → response `0x17FE007B`
    (also `0x17FC0076` → `0x17FE0076`) — TX byte `FC`, RX byte `FE`.
  - 11-bit ECUs: `0x710 → 0x77A`, `0x746 → 0x7B0` (**+0x6A**).
  - Contains a **multi-DID** request key `221E3B1E3D` (`22 1E3B 1E3D`).
- **Porsche Taycan.** Same `0x17` 29-bit ECU (`0x17FC007B` → `0x17FE007B`) plus
  11-bit ECUs at priority `0x18`: `0x710 → 0x77A`, `0x744 → 0x7AE` (**+0x6A**).
- **Cupra Seat Leon.** 29-bit, priority `0x17`: `0x17FC0076` → `0x17FE0076`.
- **VW e-Golf / e-Up.** 11-bit, request `0x7E5`. Service `0x22`.
- **Audi A3 (PQ/MQB).** 11-bit, request `0x714` → response `0x77E`. Service `0x22`.
- **Škoda Karoq.** 11-bit, request `0x7E0`. Service `0x22`.

### RAM
- **Promaster 3500 (2019).** Normal-fixed **29-bit**: request `0x18DA10F1`
  (target `0x10`, tester `0xF1`). Service `0x22`.

### Smart
- **Smart EQ.** 11-bit with **multiple offsets across ECUs**: `0x79B → 0x7BB`
  (+0x20), `0x743 → 0x763` (+0x20), `0x7E4 → 0x7EC` (+8), `0x792 → 0x793` (+1).
  Services `0x21` (`21083`) and `0x22`.
- **Smart #1.** Init mixes a functional 11-bit header (`ATSH7DF`) with a 29-bit
  protocol select (`ATSP7`); service `0x22` (`224801`).

### Subaru / Toyota
- **Subaru Outback 2.5 Gen6.** 11-bit, several headers (`0x7A3`, `0x753`,
  `0x7A2`). Service `0x22`.
- **Toyota Hilux.** 11-bit, request `0x7C0`, service `0x21` (`21291`).
- **Toyota Rav4.** OBD-II **mode 01** plus a header-scoped read (`0x7DF`).

### Volvo / Zeekr
- **Volvo XC40 (BEV & ICE), XC60 PHEV.** 29-bit, **priority `0x1D`** (`ATCP1D`):
  request `0x1DD01635` → response `0x1EC6AE80` (a second ECU: `0x1DD01637` →
  `0x1EC6EE80`). Response IDs bear **no simple offset relation** to the request.
  Service `0x22`.
- **Zeekr 001.** Same `0x1D` 29-bit scheme as Volvo (`0x1DD01635` → `0x1EC6AE80`).

### Generic
- **`generic.json`** — legislated OBD-II **mode 01** live PIDs on the functional
  broadcast; 11-bit.

---

## Notable cross-cutting quirks

- **Response ID is not reliably request + 8.** Observed 11-bit offsets span +1,
  +8, +0x20, +0x40, +0x6A, +0x400, and **−0x20** (PSA). Do not assume +8.
- **A single vehicle can mix schemes.** VW MEB and Porsche Taycan mix 11-bit and
  29-bit ECUs; Smart EQ mixes three different 11-bit offsets.
- **29-bit priority byte varies by make** (`0x14` GM, `0x17` VW group, `0x18`
  ISO-standard, `0x1D` Volvo/Zeekr) and, for GM and Volvo/Zeekr, the response ID
  is **not** the plain `0x18DA…` byte-swap — it must be taken as given.
- **Functional-request / physical-response** (`0x18DB33F1` → `0x18DAF1xx`) is used
  by Renault and Mitsubishi; flow-control frames for such requests belong on the
  physical response address.
- **Extended/mixed 11-bit** (BMW/Mini) selects the target module by an ISO-TP
  extension byte inside the payload, not by the CAN ID alone.
- **Non-`0x22` services are common**: `0x21` (Nissan, Toyota, Mitsubishi, older
  PSA/Renault, Smart, some Hyundai/Kia), OBD **mode 01** (Chevrolet Volt,
  Chrysler, Renault Megane, Toyota Rav4), KWP `0x1A` (Opel EOBD, GMC Sierra).
- **Command-set differences**: some Ford profiles rely on the **STN** `STCAFCP`
  command rather than ELM327 `ATCRA`; Geely Geometry uses **`ATSP0`** auto-detect.
- **PID key encodings**: a trailing frame-count digit appears on many keys but is
  inconsistent within a profile; longer keys may encode a **multi-DID** request
  (VW `221E3B1E3D`). One Hyundai Kona profile carries a literal `remove` sentinel.
