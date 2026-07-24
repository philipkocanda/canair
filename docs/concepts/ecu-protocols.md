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
`query`, `scan`, `identity`, `routines`, and `iocontrol` — so it never blind-sends
a `0x22` UDS request to a KWP2000 ECU (which would NRC), and vice versa. This is
why a bare identifier often just works: the tool supplies the correct SID from the
ECU's protocol.

!!! tip "The prefix is optional but recommended"
    Because canair knows each ECU's protocol, a UDS ECU accepts a bare DID
    (`CLU:B002`) as well as the full `CLU:22B002`. **Prefer the full form**
    (`22B002`) — it's self-documenting and matches the convention in the bundled
    Ioniq profile. A KWP2000 PID is conventionally written with its `21` prefix
    too (`2101`).

## The `-1` / `-2` in byte tooling

The service byte also shifts where the *data* starts in the response, which is why
`canair bix` has `-1` (1-byte subfunction, service `21`) and `-2` (2-byte
subfunction, service `22`) modes. A `21 01` response starts `61 01 <data…>`
(1 echo byte); a `22 B0 02` response starts `62 B0 02 <data…>` (2 echo bytes). See
[Byte indexing](byte-indexing.md) for how that feeds into `Bnn` offsets.

## See it in a profile

```bash
canair ecu                 # every ECU with its protocol
canair ecu BMS             # one ECU: protocol, addresses, PID/param counts
canair identity BMS        # reads identity, auto-picking 21 vs 22 by protocol
```

## Further reading

Marque-specific DID conventions — range semantics (`22Bxxx` cluster, `22Cxxx`
body, `22Fxxx` flash), paging vs indexing, and the Hyundai/Kia identity-DID `-1`
offset — are worked through in the `reverse-engineer-signal` skill's UDS-conventions
reference. Those patterns vary by manufacturer; expect to re-derive them for a new
car.
