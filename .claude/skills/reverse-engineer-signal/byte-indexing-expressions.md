# Reference: WiCAN byte indexing, expression syntax, UDS conventions

Scope: Bnn/ISO-TP/PCI byte notation, `canair bix`, expression syntax, and a pointer to the
Hyundai/Kia DID conventions. The "body above" in the last section refers to
`.claude/skills/reverse-engineer-signal/orient-discover.md` (step 3).

## Reference: WiCAN byte index notation

Full treatment: `docs/concepts/byte-indexing.md` (plus
`docs/concepts/wican-byte-index.md` for the firmware-grounded detail).

WiCAN expressions index the **raw CAN frame data including ISO-TP PCI bytes** — the
firmware's ELM327 parser copies all 8 CAN data bytes per frame sequentially into a flat
array. For a *multi-frame* response to `2101` on BMS (0x7E4):

```
Frame 0 (First Frame):  [10 3B] [61 01 FF FF FF FF]  → B00-B07
Frame 1 (Consecutive):  [21]    [d  d  d  d  d  d  d] → B08-B15
Frame 2 (Consecutive):  [22]    [d  d  d  d  d  d  d] → B16-B23
```

- `B00`/`B01` = First-Frame PCI, `B02` = response SID (`0x61`), `B03` = PID echo, `B04`
  = first data byte; PCI bytes recur at B08, B16, B24, …
- A **single-frame** (≤7-byte payload) response carries only **one** PCI byte, so its
  SID sits at `B01` — the WiCAN↔ISO-TP offset is length-dependent. `canair bix` resolves
  this from the payload; assuming multi-frame on a short response shifts every byte by
  one.
- The header after the SID is a property of the *service*, not a fixed width: `0x22` →
  2-byte DID, `0x21` → 1-byte LID, `0x01` → 1-byte OBD PID, `0x31` → SF *before* the
  RID, `0x2F` → CTRL *after* the DID, `0x7F` → rejected SID + NRC.

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` / `[Snn:Smm]` (multi-byte), `Bnn:k`
(bit k, 0 = LSB). Operators `+ - * / << >> & | ^`. Full reference:
`expression_parser.c` in `wican-fw/`.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes — it does NOT skip PCI bytes.** A
value spanning a frame boundary (B07-B08, B15-B16, …) swallows the PCI byte and
produces garbage; shift manually instead — `(B07 << 8) | B09`. Always check a range
with `canair bix`. (Exception: the byte-run types `ascii`/`date` take a plain range and
their decoder skips PCI, since a 17-char VIN cannot fit in one frame.)

```bash
canair bix                  # guided overview: legend + compact 2-frame table
canair bix w9               # WiCAN B09 → ISO-TP 0x06, Torque E, bix 32, CAN frame 1
canair bix --table          # full conversion table, grouped by CAN frame
canair bix -a 62BC03… --ecu IGPM --pid 22BC03   # annotate a payload; roles from the SID
canair bix -a 7F2231        # refused read: REJ SID + NRC, named
```

`--annotate` (`-a`) takes the **reassembled UDS payload** (SID-first, PCI stripped —
what the transport and `captures/` hold); `--raw` annotates an already-framed CAN
payload. It reconstructs the WiCAN frame, prints each byte's WiCAN Bnn / ISO-TP index /
Torque letter / bix / role — labelling every header byte from the payload's own response
SID — and warns when a `-1`/`-2` override or `--pid` contradicts it.

**Never convert a byte index by hand — run `canair bix`.** It is the only trustworthy
source, because the WiCAN ↔ ISO-TP ↔ Torque ↔ bix mapping is not a fixed table: it
depends on the response's *length* (one vs two PCI bytes) and its *service* (header
width and field order), both of which `bix` reads off the actual payload. A static
table is therefore wrong for a large share of real payloads and goes stale whenever the
notation model changes — a hand-maintained one used to live beside this skill and was
deleted for exactly that reason. Use `canair bix --table` when you want the table
itself, `canair bix w9` for one index, and `--annotate` for a real payload.


## Reference: UDS decoding conventions (Hyundai/Kia example)

Marque DID conventions for the bundled Ioniq — PID categories, DID paging vs indexing,
where Hyundai/Kia put record, IOControl and identity DIDs, the record-DID families to sweep,
and the identity-DID `-1` echo — are in the sibling
[`hyundai-kia-uds-conventions.md`](hyundai-kia-uds-conventions.md). **These are
marque-specific, not universal**; for another car expect a different scheme and
re-derive it. The generic UDS-`22`-vs-KWP2000-`21` distinction (per each ECU's
`id_protocol`) is in the body above and `docs/concepts/ecu-protocols.md`.

