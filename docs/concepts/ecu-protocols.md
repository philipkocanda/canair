# ECU protocols & PID prefixes

You'll notice that some PIDs in a profile are written like `2101` and others like
`22B002` — a two-hex-digit prefix vs a four-hex-digit one. That prefix isn't
cosmetic: **it's the diagnostic service byte sent as the first byte on the wire**,
and it tells you which of two diagnostic protocols the ECU speaks.

## Two protocols, two read services

A single vehicle bus commonly runs a mix of two diagnostic protocols. They do the
same job — "read a data identifier from an ECU" — but number their read service
differently:

| Protocol | Read service (SID) | Identifier width | PID looks like |
|---|---|---|---|
| **KWP2000** (ISO 14230, older) | `0x21` — *ReadDataByLocalIdentifier* | **1 byte** | `21` + LID → `2101`, `21F2` |
| **UDS** (ISO 14229, newer) | `0x22` — *ReadDataByIdentifier* | **2 bytes** | `22` + DID → `22B002`, `22BC07` |

!!! note "What canair calls a PID"
    Strictly, an OBD-II *PID* is a 1-byte code read under service `0x01`. canair uses "PID" as
    an umbrella for any read identifier: an OBD-II PID, a KWP2000 **LID** or a UDS **DID**, so
    `22B002` (a DID) is called a "PID" too. This follows WiCAN's convention: profiles compile to
    the WiCAN AutoPID format (see [`canair wican`](../reference/cli/wican.md)), which keys
    **every** parameter group under `pid` regardless of the diagnostic service.

So the prefix **is** the service ID (SID) — the literal first byte of the
request:

- `BMS:2101` puts `21 01` on the wire: service `0x21` (KWP2000 read), 1-byte local
  identifier `01`.
- `CLU:22B002` puts `22 B0 02` on the wire: service `0x22` (UDS read), 2-byte data
  identifier `B002`.

That's also why the identifier widths differ. KWP2000's `0x21` carries a **1-byte**
LID (only `01`–`FF`, 255 pages) → PIDs like `2101`, `2102`, `21F2`. UDS's `0x22`
carries a **2-byte** DID (65 536 possible) → PIDs like `22B002`, `22BC07`.

## `id_protocol` records which one each ECU speaks

Every ECU in a profile carries an `id_protocol` field in its `ecus/<name>.yaml`:

- `id_protocol: UDS` — answers `22`/F1xx identity DIDs (e.g. CLU, IGPM, BCM, ESC).
- `id_protocol: KWP2000` — answers `21`/1Axx identity LIDs (e.g. the powertrain
  ECUs BMS, MCU, VCU, OBC on the bundled Ioniq).
- `id_protocol: none` — NRCs both (identity-only silent modules).

canair reads this field and **auto-selects the right service** for you across
`read`, `scan`, `identity`, `routines`, and `iocontrol` — so it never blind-sends
a `0x22` UDS request to a KWP2000 ECU (which would NRC), and vice versa. This is
why a bare identifier often just works: the tool supplies the correct SID from the
ECU's protocol.

!!! note "One ECU, two protocols"
    An ECU's protocol can change between versions of the same car — a VCU that went from
    KWP2000 to UDS in a running change, or a facelift BMS that answers `220101` where the
    older one answers `2101`. Those are two [revisions](variants.md#ecu-revisions) of that
    ECU: each revision sets its own `id_protocol`, and the PIDs only one of them answers
    are gated to it. The protocol canair uses is the one of the revision your car has.

## When the profile records no protocol

An ECU whose `id_protocol` is missing, `none` or `unknown` could speak either protocol. A read
is harmless either way, but routine control is not: `31 03 …` reads a routine's results on UDS
and starts routine `0x03` on KWP2000. So:

- `canair routines` and `canair scan routines` ask before they send routine control to such an
  ECU. `--yes` answers.
- `canair scan range` asks before any request that can change the car's state, whatever the
  protocol, and refuses `0x31` on a KWP2000 ECU.
- `canair identity ECU` finds the protocol out. It probes both identity services (`22 F190` and
  `1A 90`), and when one returns fields while the other is refused, it offers to record the
  protocol in `id_protocol`. `--set-protocol` records it without asking. A probe refused "in
  this session" (`0x7F` for UDS, `0x80` for KWP2000) means the service exists in another
  session: run it again with `--session`.

```bash
canair identity IGPM                 # probe, read, and offer to record the protocol
canair identity IGPM --set-protocol  # record it without asking (scripts)
```

## How `canair identity` labels the records

`canair identity` names each record by its standard: ISO 14229-1 Table C.1 for UDS (`22 F1xx`)
and SSF 14230-3 for KWP2000 (`1A xx`). "VM" in a label is the vehicle manufacturer, as in
`VM ECU software number` (`F188`). A record that the standard leaves to the manufacturer or the
supplier, with no known meaning, shows its range: `Manufacturer-specific F110`.

Some makes use a record their own way. On a profile whose name starts with `hyundai-` or `kia-`,
canair uses the Hyundai/Kia meaning (ES95486 Annex C and the bundled cars): `F100` is the CAN
database version, `F1A0` the software version, KWP2000 `8C` a module code. It also reads `F1A1`
(the supplier code), `F1B0` (the number of software units) and one `F1B1`… version per unit.
A record whose value fits no label still prints under its standard name. For example, the Ioniq
BMS answers `1A 90`, which SSF 14230-3 names the VIN, with its ECU name.

Before you file a value in the ECU's `identity:` block, check the record it came from. `F187` is
the part number on Hyundai/Kia, `F188` is a software number.

!!! tip "The prefix is optional but recommended"
    Because canair knows each ECU's protocol, a UDS ECU accepts a bare DID
    (`CLU:B002`) as well as the full `CLU:22B002`. **Prefer the full form**
    (`22B002`) — it's self-documenting and matches the convention in the bundled
    Ioniq profile. A KWP2000 PID is conventionally written with its `21` prefix
    too (`2101`).

!!! note "OBD-II mode 01 needs its service spelled out"
    A standard OBD-II PID is written `01` + PID (`010D`), the same four hex digits as a UDS
    DID. canair reads an undefined four-digit token as a DID, so ask for mode 01 with the
    explicit `SERVICE/ID` form, `BMS:01/0D` — see
    [the query mini-language](query-mini-language.md#naming-the-service-serviceid).

## Negative response names follow the protocol too

The same code can mean different things, so canair names a negative response by the target ECU's
`id_protocol`, using SSF 14230-3 for KWP2000. There, `0x23` is
`routineNotCompleteOrServiceInProgress` (still running, not refused: repeat requests then draw
`0x21` busyRepeatRequest until it finishes), `0x72` is `transferAborted`, `0x80` is
`serviceNotSupportedInActiveDiagnosticSession` and `0x22` covers both a wrong condition and a
wrong request sequence. `0x81`–`0x8F` are reserved, so ISO 14229-1's `rpmTooHigh` and friends are
not read into them, and `0x90`–`0xF9` print as `vehicleManufacturerSpecific`. When the protocol
is unknown, a code only KWP2000 defines is shown with a `(KWP2000)` tag. On a UDS ECU,
`0x50`–`0x5D` are the ISO 14229-1:2020 Authentication refusals; on a KWP2000 ECU, `0x50`–`0x53`
keep their upload meanings.

## Waiting, repeating and suppressed replies

These rules hold on every transport:

- **ResponsePending (`7F xx 78`)** means the ECU needs more time. canair waits for the real answer:
  up to 5 s after each pending frame, and 20 s for a whole run of them (or your `--timeout`, when
  that is longer).
- **busyRepeatRequest (`7F xx 21`)** means the ECU, often a gateway, cannot take the request now.
  canair repeats a request that only reads up to three times (after 0.1, 0.2 and 0.4 s). It never
  repeats a request that changes the ECU's state.
- **The suppress-positive-response bit.** On a UDS ECU, bit 7 of a sub-function (`3E 80`,
  `10 83`) asks for no positive reply. canair then waits only for a refusal, and reports silence
  as "No reply, as asked", not as a timeout. A `--save` stores nothing for it. An ECU whose
  protocol is unknown is not judged this way, because on KWP2000 `10 81` is a session that
  answers.
- **Reply checks.** A reply must answer the request's service. On a UDS ECU, a sub-function
  service (`10`, `11`, `19`, `27`, `28`, `31`, `3E`, `85`) must also repeat its sub-function, and
  RoutineControl its routine id. A reply that does not is treated as a late answer to an earlier
  request.

## The `-1` / `-2` in byte tooling

The service byte also shifts where the *data* starts in the response, which is why `canair bix`
has `-1` (1-byte subfunction, service `21`) and `-2` (2-byte subfunction, service `22`) modes. A
`21 01` response starts `61 01 <data…>`; a `22 B0 02` response starts `62 B0 02 <data…>`.
`canair bix --annotate` reads the service from the response SID and labels the identifier
itself (`LID` for KWP2000 `0x21`, `DID` for UDS `0x22`, `PID` for OBD-II), so `-1`/`-2` are only
an override. See [Byte indexing](byte-indexing.md) for how this feeds into `Bnn` offsets.

## See it in a profile

```bash
canair ecu                 # every ECU with its protocol
canair ecu BMS             # one ECU: protocol, addresses, PID and signal counts
canair identity BMS        # reads identity, auto-picking 21 vs 22 by protocol
```

## Further reading

Marque-specific DID conventions — range semantics (`22Bxxx` cluster, `22Cxxx`
body, `22Fxxx` flash), paging vs indexing, and the Hyundai/Kia ECUs that repeat their
previous reply to an identity DID they lack — are worked through in the `reverse-engineer-signal`
skill's UDS-conventions reference. Those patterns vary by manufacturer; expect to re-derive them
for a new car.
