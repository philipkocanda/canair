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
  offset depends on the subfunction width: **Torque 1** for `21xx` PIDs (1-byte
  subfunction) and **Torque 2** for `22xxxx` DIDs (2-byte). `canair bix` selects
  the variant with `-1` (default) / `-2`, and names the active one so it's clear
  the Torque mapping is *not* fixed.

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
canair bix w9                    # quick lookup for WiCAN byte 9
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

Add `--ecu ECU --pid PID` to `--annotate` to overlay which defined parameter maps
each byte and flag `unmapped` data bytes — the fastest way to catch a wrong
offset in an expression:

```bash
canair bix --annotate 62B004… --ecu MyECU --pid B004
```

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
