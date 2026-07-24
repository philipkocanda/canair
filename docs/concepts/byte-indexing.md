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
- **Torque / OBDb bix** — the index into the actual **UDS** payload, which is
  offset by one or two bytes depending on the service subfunction (`21 01` vs
  `21 01 xx`).

Because each notation includes or excludes different framing, an expression
correct in one is off-by-one-or-two in another.

## canair expressions use WiCAN `Bnn`

Parameter expressions reference bytes as `[Bnn]` (and bits as `[Bnn:k]`), using
the WiCAN byte index. So `"[B12]"` extracts WiCAN byte 12.

## Let the tool do the conversion

Don't convert by hand — `canair bix` is the byte-index converter and, crucially,
annotates a real payload so you can *see* which byte is which:

```bash
canair bix w9                    # quick lookup for WiCAN byte 9
canair bix --table               # the full conversion table
canair bix --annotate 62B004…    # map a raw payload: WiCAN/ISO-TP/Torque/bix per byte
```

Add `--ecu ECU --pid PID` to `--annotate` to overlay which defined parameter maps
each byte and flag `unmapped` data bytes — the fastest way to catch a wrong
offset in an expression:

```bash
canair bix --annotate 62B004… --ecu MyECU --pid B004
```

## Further reading

The full conversion table and worked examples live in the repo's local reference
note `docs-ignored/wican-iso-tp-index-conversion.md`, and upstream discussion is
in [meatpiHQ/wican-fw#514](https://github.com/meatpiHQ/wican-fw/issues/514).
