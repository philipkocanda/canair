# Identity record tables: check the labels against the standards, then add the missing records

Status: **DONE** (2026-11-06). Phases 1–4 are implemented; the outcome is in "Phase 1
results" and "Outcome" below. It follows review items 4 and 5 of the identity review
(`plans/2026-11-06-identity-probe-handoff.md`, "Out of scope"), after that hand-off's items 1–3.

## Why

`canair identity` reads a fixed list of records per protocol and prints each one under a label
(`src/canair/live/identity/records.py`). The labels are what a user files a value under: they
decide whether a reading lands in `part_number`, `sw_version`, `serial` or `ecu_id` of the
ECU's `identity:` block, and which record the software-version fingerprint reads
(`live/identity/software.py`, used by ECU revision detection). A comparison with the standards
shows:

- **Wrong labels.** UDS `F188` is printed as "ECU Part Number", but ISO 14229-1 names it the
  vehicle manufacturer's ECU *software* number. KWP2000 `9A` is printed as "Repair Shop /
  Tester", which is `98`'s meaning in SSF 14230-3; `98` itself is printed as "Firmware
  Identifier". `F1A2` is printed as "HW Version", but Hyundai/Kia use it for the application
  version.
- **Labels with no source.** `F100`, `F101`, `F110`, `F17E` and `F1A4` lie in ranges the
  standards leave to the manufacturer, and none of the references in the repo names them.
- **Missing records.** `F189` (the vehicle manufacturer's ECU software *version*) is the first
  record the software-version fingerprint reads, but `canair identity` never shows it. The
  boot and application software identifications (`F180`–`F185`), the programming date
  (`F199`), KWP2000 `89` and `93`, and others are missing too.
- **Schema comments that say the wrong record.** `src/canair/schema/pids_schema.yaml` says
  `part_number` comes "from F187/F188" (F188 is a software number) and `serial` "from
  F18C/F18B" (F18B is the manufacturing date).

## Sources, and the rules for using them

The copies are in `references/`, which is gitignored. Extract text with `pdftotext -layout`
into the session's temp directory, never into the repo.

| Source | What it covers | Where |
|---|---|---|
| ISO 14229-1:2013, Annex C.1 (dataIdentifier definitions) | UDS `F180`–`F19F`, and which ranges are left to the manufacturer (`F100`–`F17F`, `F1A0`–`F1EF`) or the supplier (`F1F0`–`F1FF`) | `references/ISO Standards/ISO_14229-1_2013_…pdf` |
| SSF 14230-3 Issue 2, section 6.6.3 (identificationOption table) | KWP2000 `1A` records `80`–`9F` | `references/ISO Standards/SSF-14230-3s.pdf` |
| DaimlerChrysler KWP2000 2002, table 3.7.2-1 | the same `1A` records with **different** meanings (`88` VIN original, `89` diagnostic variant code, `9A` code fingerprint, …) | `references/ISO Standards/OBDII Specifications - KWP2000 DaimlerChrysler 2002.pdf` |
| Hyundai/Kia ES95486-00 Rev 30, Annex C | how Hyundai/Kia fill the UDS ranges: `F187` 10–15 ASCII bytes, `F18B` 4 bytes `YYYYMMDD`, `F1A0` software version, `F1A1` supplier code, `F1A2` application version, `F1B0`–`F1BF` software units | `references/HYUNDAI Unified Diagnostic Services (UDS) on CAN Standard/` |
| The car | what the bundled Ioniq's ECUs actually answer | `profiles/hyundai-ioniq-ae-ev/ecus/*.yaml` (`identity:`), its captures |

**ES95486 is a proprietary document**, marked for internal use with no reproduction, and each
page carries the licensee's name. In anything that lands in the repo (code, comments, tests,
docs, plans, commit messages):

- cite it by number, annex and DID only ("ES95486 Annex C, F1A2"), as the repo already does;
- never quote its text or copy its tables;
- never write the watermark's name or company (it identifies a person).

The ISO and SSF short names (`vehicleManufacturerECUSoftwareNumber`) are standard identifiers
and may be used as labels.

**When the standards disagree, the car decides.** SSF 14230-3 and the DaimlerChrysler spec
assign `1A` records differently, and Hyundai/Kia follow neither exactly: the Ioniq's OBC and
VCU answer `1A 8C` with an 8-character module code ending in a version (recorded as `ecu_id`
in `obc.yaml` and `vcu.yaml`), where SSF 14230-3 has `ECUSerialNumber`.

## The check, record by record

The verdicts below are from a first reading of the sources. Phase 1 confirms each one and
fills in the "Car" column.

### UDS (`22 F1xx`)

| DID | Label today | ISO 14229-1 name | ES95486 (Hyundai/Kia) | Verdict |
|---|---|---|---|---|
| F186 | Active Diagnostic Session | ActiveDiagnosticSession | same | keep; decode the byte through `SUBFUNCTION_NAMES` |
| F187 | ECU Part Number (F187) | vehicleManufacturerSparePartNumber | mandatory, ASCII | rename "Spare part number" |
| F188 | ECU Part Number (UDS) | vehicleManufacturerECUSoftwareNumber | same | **wrong**: "VM ECU software number" |
| F18A | System Supplier ID | systemSupplierIdentifier | same | keep |
| F18B | Manufacture Date | ECUManufacturingDate | mandatory, 4 bytes `YYYYMMDD` | keep; check `decode_date` on the 4-byte form |
| F18C | ECU Serial / Cal ID | ECUSerialNumber | mandatory, ASCII | drop "Cal ID" |
| F18D | Supported Functional Units | supportedFunctionalUnits | same | keep |
| F190 | VIN | VIN | same | keep (PII, see below) |
| F191 | HW Version Number | vehicleManufacturerECUHardwareNumber | used as the vehicle HW version | "VM ECU hardware number", note the Hyundai/Kia use |
| F192–F195 | Supplier HW/SW number/version | systemSupplierECU… | same | keep |
| F196 | Exhaust Regulation / SW | exhaustRegulationOrTypeApprovalNumber | same | drop "/ SW" |
| F197 | System / Engine Name | systemNameOrEngineType | mandatory | keep |
| F100 | OEM ID (Hyundai/Kia: CAN DB Version) | manufacturer range | not named in Rev 30 | **no source**: find one or label "manufacturer-specific" |
| F101 | App SW ID | manufacturer range | not named | **no source** |
| F110 | ECU Identification | manufacturer range | not named | **no source** |
| F17E | SW Install Date | manufacturer range | not named | **no source** |
| F1A0 | OEM ID (Hyundai/Kia: SW Version) | manufacturer range | software version | keep the meaning; drop "OEM ID" |
| F1A2 | HW Version | manufacturer range | application version, a structured record | **wrong** |
| F1A4 | HW Part 2 | manufacturer range | reserved | **no source** |
| — | missing | F180–F185 boot/application software and data identification and fingerprints (first byte: number of modules) | same | add |
| — | missing | **F189** vehicleManufacturerECUSoftwareVersionNumber | same | add |
| — | missing | F18E kit assembly part number, F198 repair shop code or tester serial, F199 programming date, F19A–F19D calibration and installation | same | add F18E, F199, F19B, F19D; decide F198, F19A (see PII) |
| — | missing | manufacturer range | F1A1 supplier code (mandatory), F1B0 number of software units (mandatory), F1B1… unit versions | add, for Hyundai/Kia only (decision 2) |

### KWP2000 (`1A xx`)

| Record | Label today | SSF 14230-3 name | DaimlerChrysler | Verdict |
|---|---|---|---|---|
| 80 | General ECU Identification | ECUIdentificationDataTable (records 82–BF in one reply) | reserved | rename; decoding the table is out of scope |
| 86 | DCS ECU Identification | vehicleManufacturerSpecific | DCS ECU identification | the label is DaimlerChrysler's: "manufacturer-specific" |
| 87 | Spare Part Number | vehicleManufacturerSparePartNumber | DCX/MMC ECU identification | keep |
| 88 | ECU Software Number | vehicleManufacturerECUSoftwareNumber | VIN (original) | keep (SSF) |
| 8A | System Supplier ID | systemSupplier | supplier-specific | keep |
| 8B | Manufacture / Version Date | ECUManufacturingDate | supplier-specific | drop "/ Version" |
| 8C | ECU Software / ID | ECUSerialNumber | supplier-specific | **car decides**: on the Ioniq a module code with a version, not a serial |
| 8D, 8E | Software / Calibration Identifier | systemSupplierSpecific | supplier-specific | **no source**: "supplier-specific" unless the car shows otherwise |
| 90 | ECU Name / VIN | VIN | VIN (current) | "VIN" (PII) |
| 91 | Firmware Version / Part No. | vehicleManufacturerECUHardwareNumber | — | **wrong** |
| 92 | Hardware Version | systemSupplierECUHardwareNumber | — | "Supplier HW number" |
| 94, 95 | Supplier SW Number/Version | systemSupplierECUSoftwareNumber/VersionNumber | — | keep |
| 96 | Supplier SW / Regulation | exhaustRegulationOrTypeApprovalNumber | calibration identification | **wrong** (SSF) |
| 97 | System / Engine Name | systemNameOrEngineType | calibration verification number | keep (SSF) |
| 98 | Firmware Identifier | repairShopCodeOrTesterSerialNumber | — | **wrong** |
| 99 | Programming Date | programmingDate | — | keep |
| 9A | Repair Shop / Tester | calibrationRepairShopCodeOrCalibrationEquipmentSerialNumber | ECU code fingerprint | **wrong**: the label is 98's |
| — | missing | 89 vehicleManufacturerECUSoftwareVersionNumber, 93 systemSupplierECUHardwareVersionNumber, 9B calibrationDate, 9C calibrationEquipmentSoftwareNumber, 9D ECUInstallationDate | | add 89, 93, 9B, 9D |

## Phase 1 results (2026-11-06)

Every ISO and SSF name in the tables above holds against its source (ISO 14229-1 Table C.1,
SSF 14230-3 Table 6.6.3, DaimlerChrysler Table 3.7.2-1). ES95486-00 Rev 30 and ES95486-02
Rev 23 give the same Annex C entries for the rows below. Corrections to the first reading:

- **F100 has a source.** The supplier's WPC specification and its test tool (see
  `profiles/hyundai-ioniq-ae-ev/research/HKMC_UDS_SPEC_FINDINGS.md`, sections 3 and 10) define
  it as the B-CAN database version on Hyundai/Kia. A Kona IGPM capture of a dealer tool
  (`IGPM_FINDINGS.md`) names `F100`–`F104` as CAN DB and RDB versions, in an order it does not
  show, so `F101` stays unsourced.
- **F1A4** lies in a Hyundai/Kia range that ES95486 Rev 30 marks reserved. "HW Part 2" has no
  source.
- **F110, F17E** came from a 2026-04 commit with no reference, and no ECU on the bundled
  profiles answered them.
- **8D, 8E** stay supplier-specific. On the Ioniq, `8D` holds a software identifier on VCU, MCU,
  OBC and AAF, but the BMS answers its ROM ID (`4604`) and the EWP `Z311`. The car shows no
  single meaning.
- **90**: the VCU answers the VIN, but the BMS answers its ECU name (`AEEV__ BMS`). The label
  stays "VIN" with that note.
- **91**: VCU, MCU, OBC and AAF answer their part number, which matches SSF's example ("e.g.
  Part Number of ECU"). The BMS answers `VER.2.3`.
- **98**: the AAF answers `EAEAAFP201`, a software identifier, where SSF has a repair shop code.
  The label follows SSF with that note.

- **F1A2**: ES95486 has given it an application-version list only since 2023 (Rev 26). The
  Ioniq's BCM (2017) answers `620`, which fits no such list, so the label stays generic on every
  make.

Car evidence, read on the Ioniq on 2026-11-06 while it charged (captures `identity F189` and
`identity 1A89`): IGPM and BCM refuse `22 F189` with NRC `0x31`; BMS, OBC, VCU and MCU refuse
`1A 89` with NRC `0x12`. So the fingerprint reading of these ECUs does not change.

### Final labels — UDS

| DID | Label | Decode | Make | Source | Car (bundled Ioniq) |
|---|---|---|---|---|---|
| F190 | VIN | ascii | | ISO C.1 | VCU (KWP2000 90); masked through `sensitive:` |
| F187 | VM spare part number | ascii | | ISO C.1; ES95486 Annex C: the part number, mandatory | IGPM, BCM, CCM, VESS, GSA, DDM, ADM, PSM, WPC → `part_number` |
| F188 | VM ECU software number | ascii | | ISO C.1 | no direct answer recorded |
| F189 | VM ECU software version | ascii | | ISO C.1 | not read yet |
| F18A | System supplier ID | ascii | | ISO C.1 | |
| F18B | ECU manufacturing date | date | | ISO C.1; ES95486 Annex C: 4 bytes | BCM `20170531` → 2017-05-31 (`decode_date` handles the 4-byte form) |
| F18C | ECU serial number | ascii | | ISO C.1 | BCM, EPB, EPS, HVAC, SCC, BSD → `serial` |
| F18D | Supported functional units | hex | | ISO C.1 | |
| F18E | VM kit assembly part number | ascii | | ISO C.1 | new |
| F186 | Active diagnostic session | session | | ISO C.1 | decoded through `SUBFUNCTION_NAMES` |
| F191 | VM ECU hardware number | ascii | | ISO C.1; ES95486 Annex C: the vehicle HW version | |
| F192 | Supplier ECU HW number | ascii | | ISO C.1 | |
| F193 | Supplier ECU HW version | ascii | | ISO C.1 | body ECUs → `hw_version` |
| F194 | Supplier ECU SW number | ascii | | ISO C.1 | BCM `0880` |
| F195 | Supplier ECU SW version | ascii | | ISO C.1 | body ECUs → `sw_version`; BCM fingerprint `F195:220` |
| F196 | Exhaust regulation / type approval no. | ascii | | ISO C.1 | |
| F197 | System name / engine type | ascii | | ISO C.1 | |
| F199 | Programming date | date | | ISO C.1 | new |
| F19B | Calibration date | date | | ISO C.1 | new |
| F19D | ECU installation date | date | | ISO C.1 | new |
| F180–F182 | Boot SW / application SW / application data identification | modules | | ISO C.1 | new; first byte is the number of modules |
| F183–F185 | Boot SW / application SW / application data fingerprint | hex | | ISO C.1 | new |
| F100 | Manufacturer-specific F100 | ascii | | ISO C.1 range | → `boot_sw` on 11 ECUs |
| F100 | CAN DB version | ascii | hyundai, kia | WPC supplier spec | as above |
| F101 | Manufacturer-specific F101 | ascii | | ISO C.1 range | IGPM, EPB, ESC, CCM → `app_sw` |
| F110 | Manufacturer-specific F110 | ascii | | ISO C.1 range | none |
| F17E | Manufacturer-specific F17E | ascii | | ISO C.1 range | none |
| F1A0 | Manufacturer-specific F1A0 | ascii | | ISO C.1 range | SKM → `diag_address` |
| F1A0 | Software version | ascii | hyundai, kia | ES95486 Annex C | as above |
| F1A1 | Supplier code | ascii | hyundai, kia | ES95486 Annex C, mandatory | new |
| F1A2 | Manufacturer-specific F1A2 | ascii | | ISO C.1 range; ES95486 Annex C since Rev 26 | BCM `620` |
| F1A4 | Manufacturer-specific F1A4 | ascii | | ISO C.1 range; reserved in ES95486 | none |
| F1B0 | Number of SW units | count | hyundai, kia | ES95486 Annex C, mandatory | new |
| F1B1… | SW unit N version | ascii | hyundai, kia | ES95486 Annex C | new; only as many as `F1B0` reports (at most 15) |

"VM" is the vehicle manufacturer, as in the ISO names. Not added: `F198` and `F19A` (decision 6),
`F19C`, `F19E`, `F19F`, and ES95486's `F18F` (gateway only) and `F1A3`.

### Final labels — KWP2000

| Record | Label | Decode | Make | Source | Car (bundled Ioniq) |
|---|---|---|---|---|---|
| 80 | ECU identification data table | auto | | SSF 6.6.3 | BMS, OBC, AAF answer one block |
| 86 | Manufacturer-specific 86 | auto | | SSF 6.6.3 | |
| 87 | VM spare part number | ascii | | SSF 6.6.3 | OBC answers `FF` |
| 88 | VM ECU software number | ascii | | SSF 6.6.3 | |
| 89 | VM ECU software version | ascii | | SSF 6.6.3 | new; not read yet |
| 8A | System supplier | ascii | | SSF 6.6.3 | |
| 8B | ECU manufacturing date | date | | SSF 6.6.3 | |
| 8C | ECU serial number | ascii | | SSF 6.6.3 | |
| 8C | Module code | ascii | hyundai, kia | the car | BMS, VCU, MCU, OBC, EWP → `sw_id`/`ecu_id` (`AEEOBC51`) |
| 8D | Supplier-specific 8D | ascii | | SSF 6.6.3 | software id on VCU, MCU, OBC, AAF; ROM ID on BMS |
| 8E | Supplier-specific 8E | ascii | | SSF 6.6.3 | VCU `C5210617`, EWP `Z311` |
| 90 | VIN | ascii | | SSF 6.6.3 | VCU: the VIN; BMS: its ECU name |
| 91 | VM ECU hardware number | ascii | | SSF 6.6.3 | part number on VCU, MCU, OBC, AAF; `VER.2.3` on BMS |
| 92 | Supplier ECU HW number | ascii | | SSF 6.6.3 | EWP `3.5` |
| 93 | Supplier ECU HW version | ascii | | SSF 6.6.3 | new |
| 94 | Supplier ECU SW number | ascii | | SSF 6.6.3 | |
| 95 | Supplier ECU SW version | ascii | | SSF 6.6.3 | |
| 96 | Exhaust regulation / type approval no. | ascii | | SSF 6.6.3 | |
| 97 | System name / engine type | ascii | | SSF 6.6.3 | |
| 98 | Repair shop code / tester serial | ascii | | SSF 6.6.3 | AAF: a software id |
| 99 | Programming date | date | | SSF 6.6.3 | |
| 9A | Calibration shop code / equipment serial | ascii | | SSF 6.6.3 | |
| 9B | Calibration date | date | | SSF 6.6.3 | new |
| 9D | ECU installation date | date | | SSF 6.6.3 | new |

A row with a make replaces the row of the same identifier on a profile of that make. A make row
with no generic row is read only on that make.

## Decisions to make

| # | Question | Recommendation |
|---|---|---|
| 1 | What does a label say? | A short readable form of the standard name ("VM ECU software number"), with the make's use in the table's note column when the car or ES95486 shows it differs. One label per record, so output stays stable. |
| 2 | How do make-specific records and meanings get in? | A `make` column on the rows of the manufacturer ranges, matched against the profile name's make prefix (`hyundai-`, `kia-`; `profile/names.py` defines the form). Rows with no make are read on every profile. The alternative, a per-profile `identity_records:` field in `profile.yaml`, is more general but needs a schema change; leave it until a second make needs it. |
| 3 | SSF 14230-3 or DaimlerChrysler for KWP2000? | SSF 14230-3 by default. The Ioniq answers `8C` the SSF way in position and not in meaning, so the per-make note covers it; a DaimlerChrysler table waits for a profile that needs it. |
| 4 | Which records does the software fingerprint read? | UDS stays `F189`, `F195`, `F1A0`, `F194`. KWP2000 adds `89` first, and keeps `8C` only on Hyundai/Kia: per SSF it is a serial number, which would give every unit its own "software version". |
| 5 | How many reads may a reading cost? | About 15 records more per UDS ECU. A refused read costs one quick NRC; the silent case is bounded by the hand-off's item 2. Measure the time of a full reading on the emulator before and after, and report it. |
| 6 | PII of the new records | `F198`/`98`/`F19A`/`9A` hold a repair shop code or a tester serial: a business, not a person or a car, but check it against the **contributing-profiles** PII rules before adding them. `F190`/`90` stay masked through `sensitive:` as today. |

## Phases

### Phase 1 — verify, no code

1. Confirm every row of the two tables above against its source, and write the source next to
   the verdict (`ISO 14229-1 C.1`, `SSF 14230-3 6.6.3`, `ES95486 Annex C`). Correct any verdict
   that does not hold.
2. For the rows with **no source** (`F100`, `F101`, `F110`, `F17E`, `F1A4`, `8D`, `8E`), look
   for one: ES95486-02 and the Korean editions in `references/`, the commit that added each
   label (`git log -S`), the Ioniq's `identity:` values and notes, and public profiles of
   other tools (OBDb, OVMS) that name them. A row that stays unsourced gets the range name
   ("manufacturer-specific F110") instead of a guess.
3. Car evidence: `canair identity` prints only, so ask the user to run, on the Ioniq with
   ACC on, `uv run canair --profile hyundai-ioniq-ae-ev read <ECU>:22F189 --save --label
   "identity F189"` for two UDS ECUs (IGPM, BCM) and `…read <ECU>:1A89 --save …` for two
   KWP2000 ECUs (BMS, OBC). Do not run anything on the car yourself.
4. Record the result in this plan (the tables, with a Car column). Ask the user to approve the
   final labels before phase 2.

### Phase 2 — correct the labels

1. Change the labels in `records.py` to the approved ones. If phase 4 of
   `plans/2026-11-05-kwp2000-protocol-aware-lookups.md` has moved the tables to
   `uds/identity.py`, edit them there.
2. Correct the schema comments in `src/canair/schema/pids_schema.yaml` (`part_number`,
   `serial`, `ecu_id`, `boot_sw`) to the records they really come from.
3. Check the bundled profiles for values filed under the wrong field because of a wrong label
   (for example a `part_number` that came from `F188`). List them for the user; do not change
   profile data without approval, and change it only with `canair pids set-identity`.
4. Tests: one test that pins every row's label and decode hint, so a later change is
   deliberate. `tests/live/identity/test_identity.py` checks the tables today.

### Phase 3 — add the missing records

1. UDS: `F180`–`F185`, `F189`, `F18E`, `F199`, `F19B`, `F19D`, and on Hyundai/Kia `F1A1`,
   `F1B0` and the unit versions `F1B1`… (read only as many units as `F1B0` reports).
2. KWP2000: `89`, `93`, `9B`, `9D`.
3. Decode hints: `F180`–`F182` start with a number-of-modules byte; `F1A2` is a list of
   (application id, length, version). Either decode them in `live/identity/decode.py` or show
   them as hex with the structure named. Recommendation: hex first, structured decode as a
   separate change.
4. Decision 4: update `live/identity/software.py::SOFTWARE_RECORDS`.
5. Do not add a module that the parser build loads
   (`test_parser_build_does_not_load_more_modules` has no room).
6. Tests: each new record is read and labelled; a make-specific row is read only on a matching
   profile; `F1B0` limits the unit reads; the fingerprint order.

### Phase 4 — docs

- `docs/concepts/ecu-protocols.md`: a short note that `canair identity` labels records by
  ISO 14229-1 and SSF 14230-3, with the make's use where it differs.
- The output of `canair identity` changes (labels and more lines), so name it in the commit
  body for the release notes. No flag changes, so `docs/reference/cli/` stays.

## Outcome

- **Tables** (`src/canair/live/identity/records.py`): each row is an `IdentityRecord` with an
  optional `makes`. `records_for(protocol, make)` picks the rows for a profile, and
  `canair.profile.names.make_of` takes the make from the profile name. 37 UDS rows (35 read on a
  Hyundai/Kia profile, 33 on another make) and 24 KWP2000 rows (23 read on any profile).
- **Decode hints** (`decode.py`): `hex` now shows the bytes as they are; new `session` (F186,
  named through `SUBFUNCTION_NAMES`), `count` (F1B0) and `modules` (F180–F182, the module count
  then hex). Structured decodes of F180–F182 and F1A2 are left for a separate change.
- **Software units**: when F1B0 answers, the loop reads F1B1 up to its count (at most 15).
- **Fingerprint** (`software.py::software_records`): UDS `F189`, `F195`, `F1A0`, `F194`; KWP2000
  `89`, `95`, `88`, `8D`, then `8C` on Hyundai/Kia only. Callers pass the profile's make.
- **Schema comments**: `part_number`, `boot_sw`, `serial` and `ecu_id` name the records they come
  from.
- **Cost** (decision 5), measured on the car over `wican-ws`, wall-clock per command with the
  connection: IGPM 8.4 s → 7.8 s (22 → 35 reads), BCM 8.1 s → 8.9 s, BMS 5.4 s → 5.7 s and
  OBC 5.8 s → 5.6 s (19 → 23 reads). The refused reads answer at once, so the difference is in
  the noise. The emulator was not used.
- **Profile values filed under a misleading label** (phase 2.3; nothing changed, for review):
  - `boot_sw` holds `F100` (the old label was "Boot SW ID") on ADM, BCM, CLU, DDM, IGPM, PSM,
    RCAM, VESS, WPC, MFC and SCC. On Hyundai/Kia `F100` is the CAN database version, and no
    identity field names that.
  - `app_sw` holds `F101` ("App SW ID") on IGPM, EPB, ESC and CCM. `F101` has no known meaning.
  - AAF `firmware` holds `1A 98`, which SSF 14230-3 names the repair shop code. The value
    (`EAEAAFP201`) is a software identifier, so the field fits the value, not the record.
  - BMS `fw_version` holds `1A 91` (`VER.2.3`), the VM ECU hardware number in SSF 14230-3.
  - No `part_number` came from `F188`.
- **Tests**: `tests/live/identity/test_identity_records.py` pins every row's label, decode and
  makes, and tests the make rows, the unit reads and the fingerprint order;
  `test_identity_decode.py` tests the new hints.

## Out of scope

- Decoding KWP2000 `1A 80` (the whole identification table in one reply).
- A DaimlerChrysler (or other make's) KWP2000 record table.
- Writing identity values into a profile automatically. `canair identity` prints only, and the
  PII rules for `identity:` stay as they are.
