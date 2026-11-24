# Byte indexing and expression syntax

Scope: Bnn/ISO-TP/PCI byte notation, `canair bix` and expression syntax. Full treatment:
`docs/concepts/byte-indexing.md` (and `docs/concepts/wican-byte-index.md` for the
firmware-grounded detail).

## WiCAN byte index notation

WiCAN expressions index the **raw CAN frame data including ISO-TP PCI bytes**: B00/B01 are the
First-Frame PCI, and PCI bytes recur at B08, B16, B24, … A **single-frame** response (≤7 bytes)
has one PCI byte, so the offset between WiCAN and ISO-TP depends on the response length, and the
header after the SID depends on the service. `canair bix` reads both off the payload.

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` / `[Snn:Smm]` (multi-byte), `Bnn:k`
(bit k, 0 = LSB). Operators `+ - * / << >> & | ^`. Full reference:
`expression_parser.c` in `wican-fw/`.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes. It does NOT skip PCI bytes.** A
value spanning a frame boundary (B07-B08, B15-B16, …) swallows the PCI byte and
produces garbage. Shift manually instead: `(B07 << 8) | B09`. Always check a range
with `canair bix`. (Exception: the byte-run types `ascii`/`date` take a plain range and
their decoder skips PCI, since a 17-char VIN cannot fit in one frame.)

```bash
canair bix                  # guided overview: legend + compact 2-frame table
canair bix w9               # WiCAN B09 → ISO-TP 0x06, CAN frame 1 (add --torque/--obdb for more)
canair bix --table          # full conversion table, grouped by CAN frame
canair bix -a 62BC03… --ecu IGPM --pid 22BC03   # annotate a payload; roles from the SID
canair bix -a 7F2231        # refused read: REJ SID + NRC, named
```

`--annotate` (`-a`) takes the **reassembled UDS payload** (SID-first, PCI stripped: what the
transport and `captures/` hold). `--raw` annotates an already-framed CAN payload. It prints each
byte's WiCAN Bnn, ISO-TP index and role, labelling every header byte from the payload's own
response SID. `--torque` and `--obdb` add the Torque-letter and bix columns. It warns when a
`-1`/`-2` override or `--pid` contradicts the payload.

**Never convert a byte index by hand. Run `canair bix`.** The WiCAN ↔ ISO-TP ↔ Torque ↔ bix
mapping is not a fixed table: it depends on the response's *length* (one vs two PCI bytes) and
its *service* (header width and field order). A static table is wrong for many real payloads.

## UDS decoding conventions

Hyundai/Kia DID conventions (PID categories, DID paging vs indexing, record/IOControl/identity
DIDs, the identity-DID `-1` echo) are in
[`hyundai-kia-uds-conventions.md`](hyundai-kia-uds-conventions.md). **They are marque-specific,
not universal.** For another car, re-derive the scheme. Whether an ECU speaks UDS `22` or KWP2000
`21` is its `id_protocol` (`orient-discover.md` step 3, `docs/concepts/ecu-protocols.md`).
