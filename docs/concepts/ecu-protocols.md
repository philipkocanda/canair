# ECU protocols & PID prefixes

Some parameter identifiers (PIDs) in a profile look like `2101`. Others look like `22B002`. The
first has a two-hex-digit prefix. The second has a four-hex-digit one. The prefix is not
cosmetic. **It is the diagnostic service byte that canair sends as the first byte on the wire.** It
tells you which of two diagnostic protocols the electronic control unit (ECU) speaks.

## Two protocols, two read services

A single vehicle bus commonly runs a mix of two diagnostic protocols:

- Keyword Protocol 2000 (KWP2000, ISO 14230) is the older protocol. It identifies a data page with
  a local identifier (LID).
- Unified Diagnostic Services (UDS, ISO 14229) is the newer protocol. It identifies data with a
  data identifier (DID).

Both protocols do the same job: they read a data identifier from an ECU. They number their read
service differently. The service number is the service ID (SID).

| Protocol | Read service (SID) | Identifier width | PID looks like |
|---|---|---|---|
| **KWP2000** (ISO 14230, older) | `0x21` — *ReadDataByLocalIdentifier* | **1 byte** | `21` + LID → `2101`, `21F2` |
| **UDS** (ISO 14229, newer) | `0x22` — *ReadDataByIdentifier* | **2 bytes** | `22` + DID → `22B002`, `22BC07` |

!!! note "What canair calls a PID"
    Strictly, an On-Board Diagnostics II (OBD-II) *PID* is a 1-byte code that a client reads under
    service `0x01`. canair uses "PID" as an umbrella term for any read identifier. It can be an
    OBD-II PID, a KWP2000 **LID** or a UDS **DID**. For this reason, canair also calls `22B002` (a
    DID) a "PID".

    This follows the convention of WiCAN. Profiles compile to the WiCAN AutoPID format (see
    [`canair wican`](../reference/cli/wican.md)). That format keys **every** group of signals under
    `pid`, whatever the diagnostic service is.

The prefix **is** the SID, which is the literal first byte of the request:

- `BMS:2101` puts `21 01` on the wire. The service is `0x21` (KWP2000 read). The 1-byte local
  identifier is `01`.
- `CLU:22B002` puts `22 B0 02` on the wire. The service is `0x22` (UDS read). The 2-byte data
  identifier is `B002`.

This is also why the identifier widths differ:

- The `0x21` service of KWP2000 carries a **1-byte** LID. Only `01`–`FF` exist (255 pages). The
  PIDs look like `2101`, `2102` and `21F2`.
- The `0x22` service of UDS carries a **2-byte** DID (65 536 possible values). The PIDs look like
  `22B002` and `22BC07`.

## `id_protocol` records which one each ECU speaks

Every ECU in a profile carries an `id_protocol` field in its `ecus/<name>.yaml`:

- `id_protocol: UDS` — the ECU answers `22`/F1xx identity DIDs (for example CLU, IGPM, BCM, ESC).
- `id_protocol: KWP2000` — the ECU answers `21`/1Axx identity LIDs (for example the powertrain
  ECUs BMS, MCU, VCU and OBC on the bundled Ioniq).
- `id_protocol: none` — the ECU answers both with a negative response code (NRC). These are
  identity-only silent modules.

canair reads this field and **selects the right service** for you across `read`, `scan`,
`identity`, `routines` and `iocontrol`. It never sends a `0x22` UDS request to a KWP2000 ECU, which
would answer with an NRC. It never sends the reverse either. For this reason, a bare identifier
often works. The tool supplies the correct SID from the protocol of the ECU.

!!! note "One ECU, two protocols"
    The protocol of an ECU can change between versions of the same car. Two examples:

    - A VCU changed from KWP2000 to UDS in a running change.
    - A facelift BMS answers `220101` where the older BMS answers `2101`.

    These are two [revisions](variants.md#ecu-revisions) of that ECU. Each revision sets its own
    `id_protocol`. The PIDs that only one revision answers are gated to that revision. canair uses
    the protocol of the revision that your car has.

## When the profile records no protocol

An ECU whose `id_protocol` is missing, `none` or `unknown` could speak either protocol. A read is
harmless either way. Routine control is not. The request `31 03 …` reads the results of a routine
on UDS. On KWP2000 it starts routine `0x03`. For this reason:

- `canair routines` and `canair scan routines` ask before they send routine control to such an
  ECU. `--yes` answers the question.
- `canair scan range` asks before any request that can change the state of the car, whatever the
  protocol. It refuses `0x31` on a KWP2000 ECU.
- `canair identity ECU` finds the protocol out. It probes both identity services (`22 F190` and
  `1A 90`). When one service returns fields and the other refuses, it offers to record the
  protocol in `id_protocol`. `--set-protocol` records it without asking.

A probe can be refused "in this session" (`0x7F` for UDS, `0x80` for KWP2000). This means that the
service exists in another session. Run the command again with `--session`.

```bash
canair identity IGPM                 # probe, read, and offer to record the protocol
canair identity IGPM --set-protocol  # record it without asking (scripts)
```

## How `canair identity` labels the records

`canair identity` names each record by its standard. For UDS (`22 F1xx`), it uses ISO 14229-1
Table C.1. For KWP2000 (`1A xx`), it uses SSF 14230-3. "VM" in a label is the vehicle manufacturer,
as in `VM ECU software number` (`F188`). The standard can leave a record to the manufacturer or
the supplier, with no known meaning. Such a record shows its range: `Manufacturer-specific F110`.

Some makes use a record in their own way. On a profile whose name starts with `hyundai-` or
`kia-`, canair uses the Hyundai/Kia meaning (ES95486 Annex C and the bundled cars):

- `F100` is the version of the Controller Area Network (CAN) database.
- `F1A0` is the software version.
- KWP2000 `8C` is a module code.
- `F1A1` is the supplier code.
- `F1B0` is the number of software units.
- `F1B1`… hold one version for each unit.

A record whose value fits no label still prints under its standard name. For example, the Ioniq BMS
answers `1A 90`, which SSF 14230-3 names the VIN, with its ECU name.

Check the record before you file a value in the `identity:` block of an ECU. On Hyundai/Kia, `F187`
is the part number and `F188` is a software number.

!!! tip "The prefix is optional but recommended"
    canair knows the protocol of each ECU. A UDS ECU therefore accepts a bare DID (`CLU:B002`) as
    well as the full `CLU:22B002`. **Prefer the full form** (`22B002`). It documents itself and
    matches the convention in the bundled Ioniq profile. By convention, a KWP2000 PID also has
    its `21` prefix (`2101`).

!!! note "OBD-II mode 01 needs its service spelled out"
    A standard OBD-II PID is written `01` + PID (`010D`). These are the same four hex digits as a
    UDS DID. canair reads an undefined four-digit token as a DID. Ask for mode 01 with the
    explicit `SERVICE/ID` form, `BMS:01/0D`. See
    [the query mini-language](query-mini-language.md#naming-the-service-serviceid).

## Negative response names follow the protocol too

The same code can mean different things. canair therefore names a negative response by the
`id_protocol` of the target ECU. For KWP2000, it uses SSF 14230-3. In that standard:

- `0x23` is `routineNotCompleteOrServiceInProgress`. The routine is still running and the ECU did
  not refuse it. Repeat requests then draw `0x21` busyRepeatRequest until the routine finishes.
- `0x72` is `transferAborted`.
- `0x80` is `serviceNotSupportedInActiveDiagnosticSession`.
- `0x22` covers both a wrong condition and a wrong request sequence.
- `0x81`–`0x8F` are reserved. canair does not read the `rpmTooHigh` code of ISO 14229-1 and the
  related codes into them.
- `0x90`–`0xF9` print as `vehicleManufacturerSpecific`.

Other rules apply to unknown protocols and to the `0x50`–`0x5D` range:

- When the protocol is unknown, canair shows a code that only KWP2000 defines with a `(KWP2000)`
  tag.
- On a UDS ECU, `0x50`–`0x5D` are the authentication refusals of ISO 14229-1:2020.
- On a KWP2000 ECU, `0x50`–`0x53` keep their upload meanings.

## Waiting, repeating and suppressed replies

These rules hold on every transport:

- **ResponsePending (`7F xx 78`)** means that the ECU needs more time. canair waits for the real
  answer. It waits up to 5 s after each pending frame, and 20 s for a whole run of them. If your
  `--timeout` is longer, canair uses that instead.
- **busyRepeatRequest (`7F xx 21`)** means that the ECU cannot take the request now. The ECU is
  often a gateway. canair repeats a request that only reads, up to three times (after 0.1, 0.2 and
  0.4 s). It never repeats a request that changes the state of the ECU.
- **The suppress-positive-response bit.** On a UDS ECU, bit 7 of a sub-function (`3E 80`, `10 83`)
  asks for no positive reply. canair then waits only for a refusal. It reports silence as "No
  reply, as asked" and not as a timeout. A `--save` stores nothing for it. canair does not judge an
  ECU with an unknown protocol this way, because `10 81` is a session that answers on KWP2000.
- **Reply checks.** A reply must answer the service of the request. On a UDS ECU, a reply to a
  sub-function service (`10`, `11`, `19`, `27`, `28`, `31`, `3E`, `85`) must also repeat the
  sub-function. A reply to RoutineControl must repeat the routine ID. canair treats a reply that
  does not as a late answer to an earlier request.

## The `-1` / `-2` in byte tooling

The service byte also shifts where the *data* starts in the response. For this reason, `canair bix`
has `-1` (1-byte subfunction, service `21`) and `-2` (2-byte subfunction, service `22`) modes. A
`21 01` response starts `61 01 <data…>`. A `22 B0 02` response starts `62 B0 02 <data…>`.

`canair bix --annotate` reads the service from the response SID. It labels the identifier itself:
`LID` for KWP2000 `0x21`, `DID` for UDS `0x22` and `PID` for OBD-II. The `-1` and `-2` modes are
therefore only an override. See [Byte indexing](byte-indexing.md) for how this feeds into `Bnn`
offsets.

## See it in a profile

```bash
canair ecu                 # every ECU with its protocol
canair ecu BMS             # one ECU: protocol, addresses, PID and signal counts
canair identity BMS        # reads identity, auto-picking 21 vs 22 by protocol
```

## Further reading

The `reverse-engineer-signal` skill has a UDS-conventions reference. It works through these
marque-specific DID conventions:

- range semantics (`22Bxxx` cluster, `22Cxxx` body, `22Fxxx` flash)
- paging vs indexing
- the Hyundai/Kia ECUs that repeat their previous reply to an identity DID they lack

These patterns vary by manufacturer. Expect to derive them again for a new car.
