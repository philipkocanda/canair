# Byte indexing

The single most common reverse-engineering mistake is getting a **byte offset
wrong** — because WiCAN, ISO-TP, and Torque each count bytes differently. Porting
a known PID from another tool or car fails silently if you don't account for
this.

## Three ways to count the same payload

A UDS response arrives as ISO-TP frames on top of CAN. Depending on where you
start counting and whether you include the transport's framing (PCI) bytes, the
"same" byte has different indices:

- **WiCAN index** — the index into the raw CAN frame data, **including** the
  ISO-TP PCI byte(s). This is what the WiCAN AutoPID feature uses for addressing.
  It's the source of most of the confusion when porting PIDs.
- **ISO-TP index** — the index into the pure reassembled ISO-TP payload (PCI
  stripped). This is what SavvyCAN/ImHex-style tools show.
- **Torque / OBDb bix** — the index into the actual **UDS** payload, counted from
  the first data byte. Because it skips the service header (SID + subfunction), the
  offset depends on the header's size: **Torque 1** for `21xx` PIDs (1-byte
  subfunction) and **Torque 2** for `22xxxx` DIDs (2-byte). `canair bix` reads that
  from the payload's own service and names the active variant so it's clear the
  Torque mapping is *not* fixed (`-1`/`-2` override it).

## The header is not always "SID + 1 or 2 bytes"

How many bytes sit between the SID and the first *data* byte is a property of the
**service**, and it is not just a width — the fields have names and an order:

| Response | Header after the SID | First data byte at ISO-TP |
|---|---|---|
| `62 xx xx …` (`0x22` ReadDataByIdentifier) | `DID` (2) | 3 |
| `61 xx …` (`0x21` ReadDataByLocalIdentifier) | `LID` (1) | 2 |
| `41 xx …` (OBD-II mode `0x01`) | `PID` (1) | 2 |
| `6F xx xx yy …` (`0x2F` IOControl) | `DID` (2) + `CTRL` (1) | 4 |
| `71 ss xx xx …` (`0x31` RoutineControl) | `SF` (1) + `RID` (2) | 4 |
| `7F ss nn` (negative response) | `REJ SID` (1) + `NRC` (1) | — |

Note `0x31` puts its sub-function **before** the routine id while `0x2F` puts its
control parameter **after** the DID — a 1-vs-2-byte "subfunction width" cannot
express either, which is why `canair bix --annotate` labels each header field from
the service instead (see below). The table lives in `canlib/uds_layout.py`.

!!! warning "The WiCAN↔ISO-TP offset depends on the response's length"

    How many PCI bytes sit in front of the data is not fixed:

    | Response | PCI bytes | First data byte |
    |---|---|---|
    | **Single frame** (payload ≤ 7 bytes) | **one** (`0x0n`) at `B00` | `B01` is the SID |
    | **Multi frame** (payload > 7 bytes) | **two** (`0x1n nn`) at `B00`–`B01`, then one `0x2n` at `B08`, `B16`, … | `B02` is the SID |

    So the *same* WiCAN index is a different ISO-TP byte depending on the payload
    length — a 7-byte `22xxxx` response puts its first data byte at `B04` = ISO-TP
    3 = Torque `A`, where a long one puts ISO-TP 3 at `B05`. canair resolves this
    from the actual captured payload, so `--notation` labels and `canair bix
    --annotate` are length-aware. Keep it in mind when hand-converting: assuming
    the multi-frame layout on a short response shifts every byte by one.

Because each notation includes or excludes different framing, an expression
correct in one is off-by-one-or-two in another.

## canair expressions use WiCAN `Bnn`

Parameter expressions reference bytes as `[Bnn]` (and bits as `[Bnn:k]`), using
the WiCAN byte index. So `"[B12]"` extracts WiCAN byte 12.

## Let the tool do the conversion

Don't convert by hand — `canair bix` is the byte-index converter and, crucially,
annotates a real payload so you can *see* which byte is which. Run it with **no
arguments** for a guided overview — a plain-language legend for each notation and
the `PCI`/`SID`/`PID`/`DID` Role labels, plus a compact 2-frame table:

```bash
canair bix                       # guided overview: legend + a compact 2-frame table
canair bix B09                   # quick lookup for WiCAN byte 9 (w9 also works)
canair bix --table               # the full conversion table, grouped by CAN frame
canair bix --annotate 62B004…    # map a reassembled UDS payload (SID-first, PCI stripped)
canair bix --annotate 1012… --raw  # map an already-framed CAN payload (PCI present)
canair bix --annotate 62B004… --torque  # add the Torque letter column
canair bix --annotate 62B004… --obdb     # add the OBDb bix (bit-index) column
```

By default `--annotate` and `--table` show only the **WiCAN**, **ISO-TP**, and
**Role** columns — WiCAN and ISO-TP are the notations canair expressions use. The
**Torque** letter column (`--torque`) and the OBDb **bix** column (`--obdb`) are
distinct notations, each opt-in on its own flag, for cross-referencing third-party
PID sheets. Torque notation is what the Torque app, Car Scanner, and similar OBD
apps use; OBDb `bix` is a separate bit-index scheme.

`--annotate` expects the **reassembled UDS response payload** — SID-first, with
the ISO-TP PCI bytes already stripped (what the transport and captures hand back);
it reconstructs the framing to show the WiCAN indices. If instead you have a **raw
CAN frame** straight off the bus (PCI bytes still present), pass `--raw` and it
indexes the bytes as-is. `bix` reliably warns when the input's first byte
contradicts the chosen mode — a UDS response SID (`0x40`–`0x7F`) and an ISO-TP PCI
first byte (`0x00`–`0x3F`) occupy disjoint ranges — so a raw frame fed without
`--raw` (or vice versa) is caught rather than silently mislabelled.

`--table` groups its rows by 8-byte CAN frame with `── Frame N ──` dividers and a
`Role` column that marks the ISO-TP framing (`FF PCI` / `CF PCI`) and UDS header
(`SID` / `PID` / `DID`) bytes, so you can see exactly where the raw CAN frame
boundaries fall and which rows are framing rather than data. `--annotate` marks the
same frame boundaries on a concrete payload.

### `--annotate` names each header byte from the service

`--table` has no payload, so it can only assume a generic `SID` + `PID`/`DID`
header. `--annotate` **does** have one, and the response SID identifies the
service — so every header byte is labelled for what it actually is:

| Role | Meaning |
|---|---|
| `PCI` | ISO-TP framing byte — never data |
| `SID` | Service Identifier (request SID + `0x40`) |
| `SF` | sub-function byte, selecting the mode within the service |
| `DID` | Data Identifier — 2-byte UDS identifier (`0x22`/`0x2E`/`0x2F`) |
| `LID` | Local Identifier — 1-byte KWP2000 identifier (`0x21`/`0x30`/`0x33`); canair writes these as `21xx` "PIDs" elsewhere |
| `PID` | Parameter ID — 1-byte OBD-II parameter (modes `0x01`/`0x02`) |
| `RID` | Routine Identifier — 2-byte UDS routine id (`0x31`) |
| `CTRL` | inputOutputControlParameter — what the ECU was told to do |
| `REJ SID` | the rejected service's SID, echoed in a negative response |
| `NRC` | Negative Response Code — why the request was refused |
| *(blank)* | real data — the bytes your expression reads |

A definition list of just the roles the payload used is printed underneath the
table (suppress it with `--no-legend`). A negative response also spells out the
code, e.g. `NegativeResponse rejecting 0x22 ReadDataByIdentifier — NRC 0x31
requestOutOfRange`, so a refused read explains itself instead of showing the NRC as
a data byte.

An unrecognised service falls back to the generic `SID` + `PID`/`DID` labelling
using the width from `-1`/`-2` or `--pid`.

Add `--ecu ECU --pid PID` to `--annotate` to overlay which defined parameter maps
each byte and flag `unmapped` data bytes — the fastest way to catch a wrong
offset in an expression:

```bash
canair bix --annotate 62B004… --ecu MyECU --pid 22B004
```

`--pid` also settles the **subfunction width** when the payload's own service can't:
a `22xxxx` DID has a 2-byte echo, a `21xx` PID a 1-byte one. You rarely need it now
that the response SID is read directly — it matters for a payload whose service
`canair` doesn't recognise. An explicit `-1`/`-2` overrides everything, and `bix`
warns when it contradicts the payload's service (or, failing that, the PID):

```
⚠ WARNING  -1 contradicts the payload: SID 0x62 is ReadDataByIdentifier (response),
           whose header is SID + DID(2B) (2 byte(s) after the SID).
```

Write the PID with its full service prefix (`22B004`, not the short `B004`) if you
are relying on it: a short-form DID doesn't state its service, so it can't be told
from a 1-byte PID.

![canair bix --annotate with --ecu/--pid — per-byte notations, roles, and mapped params](../screenshots/bix-annotate.svg)

## Switch the notation in analysis output

The analysis commands — `correlate`, `hunt`, `investigate`, `coverage`, and
`decode` (`--discriminate`/`--find-mirrors`) — label raw bytes as WiCAN `Bnn` by
default. Pass **`--notation`** to re-render those labels in whichever notation you
find easiest to read or need for cross-referencing:

```bash
canair correlate --against ESC:22C101:REAL_SPEED_KMH --bytes   # B10, B14 … (default)
canair correlate --against ESC:22C101:REAL_SPEED_KMH --bytes --notation isotp   # i7, i11 …
canair coverage BMS 2101 --unmapped --notation torque          # A, B, F …
```

`--notation` takes `wican` (default), `isotp`, `torque`, or `bix`. It only
changes **display** — named parameters are untouched, and the machine-readable
`--json` output and `--promote` always use the canonical WiCAN form (the
promotable/firmware expression). Set a persistent default with:

```bash
canair config set display.byte_notation isotp
```

Internally canair models a byte position in **ISO-TP** space (the canonical,
framing-free payload index) and derives the WiCAN / Torque / bix views from it —
so WiCAN is treated as one *view* of the byte, not the tool's native unit.

## Further reading

For the deep, firmware-grounded reference — exactly how the WiCAN `Bnn` index maps
onto the raw CAN frame buffer, verified against `wican-fw` source with file/line
citations — see [WiCAN byte index (firmware reference)](wican-byte-index.md).

The full conversion table is available any time via `canair bix --table`, and the
upstream discussion of the notation differences is in
[meatpiHQ/wican-fw#514](https://github.com/meatpiHQ/wican-fw/issues/514).
