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
canair bix --annotate 62B004…    # map a raw payload: WiCAN/ISO-TP/Torque/bix per byte
```

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

The full conversion table is available any time via `canair bix --table`, and the
upstream discussion of the notation differences is in
[meatpiHQ/wican-fw#514](https://github.com/meatpiHQ/wican-fw/issues/514).
