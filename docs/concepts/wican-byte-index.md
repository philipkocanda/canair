# The WiCAN byte index vs. the raw CAN index

!!! warning "Why this matters — read first"

    Every `expression:` in this project (for example `B09/2`, `[B04:B05]`, `B10:5`) addresses
    bytes with the **WiCAN `Bnn` index**. Most tools show you the reassembled controller area
    network (CAN) and Unified Diagnostic Services (UDS) data. The `Bnn` index is **not** the index
    into that data. It also counts the framing bytes of the ISO 15765-2 transport protocol
    (ISO-TP). These framing bytes are called protocol control information (PCI) bytes.

    A wrong index is the most common reverse-engineering mistake in this repo. An expression that
    is correct in ISO-TP, Torque or OBDb terms is **off by one or two** when you write it as
    `Bnn`. No error tells you. The error grows with every additional CAN frame. This document is
    the authoritative, source-grounded explanation.

## Provenance — grounded in current `wican-fw` truth

The file and line references on this page are to the `wican-fw` ESP32 firmware **v4.52p** (commit
`fab54ad`), the version canair is tested against. They are not about the firmware of the OBD
co-processor chip. Line numbers move between firmware releases, so read them as pointers to the
named function.

## TL;DR

The firmware builds a flat buffer. It concatenates every data byte of every CAN frame of the
response, including the ISO-TP PCI (framing) bytes. The WiCAN `Bnn` index is the position of a
byte in this buffer. It is **one running index** and does not reset for each frame.

- **Against the reassembled ISO-TP / UDS payload** (PCI stripped): the two indices **differ** by
  exactly the PCI bytes. The raw `slcan-tcp` transport returns this payload. SavvyCAN and ImHex
  show it too. The offset is **+2** at the start and grows **+1 at every 8-byte frame boundary**.
  It is *not* a constant offset.
- **Against the literal raw CAN *frame* bytes** (all 8 data bytes per frame, PCI kept): the two
  indices are **essentially identical**, because the WiCAN buffer *is* that concatenation. Only
  two nuances apply. The WiCAN index is a running index, not a per-frame index. canair also pads
  the reconstructed buffer.

## See it at a glance (`canair bix`)

Do not take the layout on faith. `canair bix` with no arguments prints a plain-language legend
and then a compact two-frame table. By default, it shows the two indices that you use most:
**WiCAN** and **ISO-TP**. It shows them next to the framing `Role` of each byte, so you can *see*
where the PCI bytes fall:

```text
$ canair bix        # (a plain-language legend is printed first, omitted here)

| WiCAN | ISO-TP | Role   |
|-------|--------|--------|
|── Frame 0 ───────────────|
|   B00 |        | FF PCI |
|   B01 |        | FF PCI |
|   B02 |   0x00 | SID    |
|   B03 |   0x01 | PID    |
|   B04 |   0x02 |        |
|   B05 |   0x03 |        |
|   B06 |   0x04 |        |
|   B07 |   0x05 |        |
|── Frame 1 ───────────────|
|   B08 |        | CF PCI |
|   B09 |   0x06 |        |
|   B10 |   0x07 |        |
|   B11 |   0x08 |        |
|   B12 |   0x09 |        |
|   B13 |   0x0A |        |
|   B14 |   0x0B |        |
|   B15 |   0x0C |        |
```

Read **down** the `Role` column to see the framing. **B00/B01** are the First-Frame PCI. **B08**
(and B16, B24, …) is a Consecutive-Frame PCI byte. The ISO-TP index runs smoothly from `0x05` to
`0x06`. The WiCAN index jumps from **B07 to B09** across the same boundary. The skipped `B08` is
the framing byte.

`canair bix` shows WiCAN and ISO-TP by default. Two other columns are opt-in. Request the one you
need for cross-references, or both:

- `--torque` adds the **Torque** letter column. The Torque app, Car Scanner and similar on-board
  diagnostics (OBD-II) apps use this notation.
- `--obdb` adds the OBDb **bix** (bit-index) column.

`canair bix --table` prints the whole table out to `--max`.

## What the WiCAN index actually is (firmware truth)

A UDS response arrives as one or more CAN frames that carry an ISO-TP payload. The firmware turns
the text response of the ELM327 adapter protocol into a byte buffer in `parse_elm327_response()`.
This function is at `wican-fw/components/autopid/autopid.c:2114`.

For each response line, the function strips the **arbitration-ID header** (the `7EC ` prefix). It
then copies **every remaining data byte of the frame, in order, into one flat buffer**
`response->data`. This code is at `wican-fw/components/autopid/autopid.c:2269-2289`.

The function does **not** strip the ISO-TP PCI bytes. For this loop, they are ordinary data
bytes. AutoPID runs with ELM327 headers ON and spaces ON, so each line is `7EC <b0> <b1> … `. The
parser needs that header and those spaces to locate the data.

For the PID kinds that this project defines (`PID_CUSTOM` / `PID_SPECIFIC`), the firmware
evaluates the expression **directly against that buffer**:

```c
// wican-fw/components/autopid/autopid.c:3996-3998
evaluate_expression((uint8_t*)param->expression, elm327_response.data, 0, &result)
```

Inside the evaluator, `Bnn` is a **direct array index** into that buffer:

```c
// wican-fw/main/expression_parser.c:168-182
} else if (expression[i] == 'B') {
    ...
    uint8_t value = data[index];      // <-- data[] IS elm327_response.data (PCI included)
    if (expression[i] == ':') {       // Bnn:k  -> bit k of that byte
        value = (value >> bit) & 1;
    }
```

The result: **`Bnn` = the n-th byte of the concatenated CAN frame payloads, ISO-TP PCI bytes
included.**

### Where the PCI bytes land

Each CAN frame contributes 8 data bytes. ISO-TP puts its framing at the front of each frame. The
PCI bytes therefore sit at these positions:

- **B00, B01** — First-Frame PCI: `0x1L LL` (frame-type nibble `1` plus 12-bit length). These
  bytes exist in *multi-frame responses only*.
- **B08, B16, B24, …** — one Consecutive-Frame PCI byte each: `0x2s` (sequence counter). It sits
  at the start of every later 8-byte block.
- Single-frame responses (≤7 payload bytes) have a **single** PCI byte at **B00** (`0x0L`, the
  length nibble). Real data starts at **B01**.

The actual UDS bytes therefore sit at these positions:

- Multi-frame: B02–B07 (the first 6 bytes), then B09–B15, B17–B23, … (7 bytes per consecutive
  frame).
- Single-frame: B01 onward.

## Exactly how it differs from the "raw CAN index"

"Raw CAN index" can mean two things. This section describes each one.

### 1. vs. the reassembled ISO-TP / UDS payload (PCI stripped) — THIS is the trap

Most tools and the raw `slcan-tcp` transport of this project give you this payload. It is the
ISO-TP payload with all PCI bytes removed. The WiCAN index includes exactly those removed bytes.
The mapping is therefore **not** a fixed offset. It starts at +2 and gains another +1 at each
frame boundary, because each consecutive frame adds one more PCI byte. The mapping comes from
`src/canair/uds/byteindex.py:27` (`wican_to_isotp`):

- Frame 0: WiCAN `pos 2..7` → ISO-TP `0..5` (`pos 0,1` are PCI → no ISO-TP index)
- Frame N>0: WiCAN `pos 1..7` → ISO-TP `6 + (N-1)*7 + (pos-1)` (`pos 0` is PCI)

Here `pos = wican_idx % 8` and `frame = wican_idx // 8`.

**Torque / OBDb `bix`** differs further. It also skips the UDS **SID plus sub-function** (1 byte
for `21xx` PIDs, 2 bytes for `22xxxx` data identifiers (DIDs)). `Torque A` / `bix 0` is the first
UDS *data* byte. It is **WiCAN B04** for a `21xx` PID. It is **WiCAN B05** for a `22xxxx` DID,
because the SID is at B02 and the two DID bytes are at B03–B04.

### 2. vs. the literal raw CAN frame bytes (all 8/frame, PCI kept) — essentially the same

The WiCAN buffer *is* the concatenation of those frame data bytes, so the values line up 1:1.
Two nuances apply:

- The WiCAN index is a **single running index** across frames. It is not a `(frame, 0..7)` pair.
  The PCI bytes sit at 0/1, 8, 16, 24, … exactly as they do on the wire.
- canair **reconstructs** this buffer offline from the payload without PCI
  (`src/canair/uds/wican_layout.py`, `uds_hex_to_wican_bytes`). It **zero-pads** the trailing
  consecutive frame. A real bus pads with whatever the ECU emits (often `0xAA`, `0x00` or
  `0x55`). The *values* of trailing padding bytes can therefore differ, but the index never does.
  Do not reference padding bytes in expressions.

## Worked example: a real capture, byte by byte

`canair bix --annotate` maps a real payload. Add `--ecu` and `--pid` to overlay which defined
signal reads each byte and to flag data bytes that no signal maps yet. The example below is a
genuine `BMS 2101` battery-status response from the bundled Ioniq profile:

```text
$ canair bix -a 6101FFFFFFFF80264826480300050E32 --ecu BMS --pid 2101
  WiCAN | ISO-TP ‖  Hex | Dec | Role   | Signal
  ──────┼────────╫──────┼─────┼────────┼─────────────
  ── Frame 0 ─────────────────────────
    B00 |      — ‖ 0x10 |  16 | PCI    |
    B01 |      — ‖ 0x10 |  16 | PCI    |
    B02 |   0x00 ‖ 0x61 |  97 | SID    |
    B03 |   0x01 ‖ 0x01 |   1 | LID    |
    B04 |   0x02 ‖ 0xFF | 255 |        | support map
    B05 |   0x03 ‖ 0xFF | 255 |        | support map
    B06 |   0x04 ‖ 0xFF | 255 |        | support map
    B07 |   0x05 ‖ 0xFF | 255 |        | support map
  ── Frame 1 ─────────────────────────
    B08 |      — ‖ 0x21 |  33 | PCI    |
    B09 |   0x06 ‖ 0x80 | 128 |        | [SOC_BMS]
    B10 |   0x07 ‖ 0x26 |  38 |        | [AVAIL_CHARGE_POWER_MIRROR?]
    B11 |   0x08 ‖ 0x48 |  72 |        | [AVAIL_CHARGE_POWER_MIRROR?]
    B12 |   0x09 ‖ 0x26 |  38 |        | [AVAIL_DISCHARGE_POWER_MIRROR?]
    B13 |   0x0A ‖ 0x48 |  72 |        | [AVAIL_DISCHARGE_POWER_MIRROR?]
    B14 |   0x0B ‖ 0x03 |   3 |        | [BMS_MAIN_RELAY:0] [BMS_CONTROLLABLE_STATE:1] [BMS_WARNING?:2] [BMS_FAULT?:3] [BMS_WELD_FLAG?:4] [AC_CHARGER_CONNECTED:5] [CHARGING_DC:6] [CHARGING:7]
    B15 |   0x0C ‖ 0x00 |   0 |        | [BATTERY_POWER]
  ── Frame 2 ─────────────────────────
    B16 |      — ‖ 0x22 |  34 | PCI    |
    B17 |   0x0D ‖ 0x05 |   5 |        | [BATTERY_POWER]
    B18 |   0x0E ‖ 0x0E |  14 |        | [BATTERY_POWER]
    B19 |   0x0F ‖ 0x32 |  50 |        | [BATTERY_POWER]
```

The `‖` divider is the key to the layout. Everything left of it is *where the byte sits*, which
is an index in one notation or another. Everything right of it is the *value* of the byte, plus
what the byte means. `Hex` and `Dec` are the same byte in two bases. Expressions index with the
WiCAN `Bnn` column and never with a `Hex` value.

This one screen shows everything that the rest of this document describes:

- **PCI bytes at B00, B01, B08, B16** — the `Role` column names them. No signal maps them.
- **`SOC_BMS = B09/2`** reads the state-of-charge byte at **B09**. This is the first byte *after*
  the Frame 1 PCI byte at B08. Here `B09 = 0x80 = 128`, so `128 / 2 = 64 %`.
- That same physical byte is **ISO-TP `0x06`**. With `--torque` it is **Torque `E`**. With
  `--obdb` it is **bix `32`**.

    - Suppose you copy a "byte 6" offset from a SavvyCAN or ISO-TP view and write `B06`.
    - `B06` reads `0xFF` (one of the `FF FF FF FF` bytes at B04–B07).
    - The result is a plausible-looking `127.5 %`.

    **This off-by-PCI shift is the trap that this document is about.** The overlay makes it
    obvious.

- Bit-level signals can share one byte. `B14` holds eight flags, one per bit, read with `B14:k`.
  A multi-byte value can span several bytes. `BATTERY_POWER` spans B15–B19.
- A data byte that no signal reads is labelled `unmapped`. This label marks the byte as open for
  reverse engineering. B04–B07 have the label `support map` instead. The PID declares them as
  layout bytes that hold no data.

For a `22xxxx` DID, the two identifier bytes are labelled `DID` (B03–B04). The first data byte is
**B05**, not B04. This is also where `Torque A` lands under `--torque`.

`bix` identifies the service from the response SID. It labels the header bytes as follows:

- `62 …` is labelled `DID`.
- `61 …` is labelled `LID`.
- An OBD-II `41 …` is labelled `PID`.
- A routine `71 …` is labelled `SF` + `RID`.
- A refused `7F …` is labelled `REJ SID` + `NRC`, spelled out by name.

`-1`/`-2` override an unrecognised service. `bix` warns about a flag that contradicts the
payload. A legend of the roles follows the table. `--no-legend` omits it.

## Multi-byte and bit forms (same buffer, same rule)

The same PCI-inclusive `data[]` buffer backs every accessor:

- `Bnn` — unsigned byte `data[nn]` (`expression_parser.c:168`).
- `Snn` — signed byte `(int8_t)data[nn]` (`expression_parser.c:183`).
- `Bnn:k` — bit `k` of `data[nn]` (`(value >> k) & 1`, `expression_parser.c:176`).
- `[Bn:Bm]` — **big-endian** multi-byte unsigned value across those indices:
  `sum |= data[j] << ((m - j) * 8)`. The first index is the most significant
  (`expression_parser.c:112-114`). The maximum span is 8 bytes (64-bit).
- `[Sn:Sm]` — the signed version. The firmware accumulates the value in a **native container**
  (`int8`, `int16`, `int32` or `int64`, picked by span) and sign-extends from that container
  (`expression_parser.c:118-160`).

!!! warning "`[Sn:Sm]` is only exact for a 1-, 2-, 4- or 8-byte span"

    The sign bit comes from the top bit of the *container*, not from the top bit of the value
    that the expression reads. A **3-, 5-, 6- or 7-byte** signed range is therefore sign-extended
    from the wrong position and can never produce a negative value. A 3-byte `[S5:S7]` over
    `FF FF FE` yields `16777214`, not `-2`.

    For those widths, write the arithmetic form with only the most-significant byte signed. This
    form is exact at any width:

    ```text
    S5*65536 + B6*256 + B7        # 3-byte big-endian signed
    ```

    canair emits this form automatically (`hunt --promote`, `decode --plot`). You need the rule
    only when you write an expression by hand.

!!! danger "In a hand-built word, only the top byte may be `Snn`"

    `(S15 << 8) | S17` sign-extends the low byte. Every value whose low byte is ≥ 0x80 therefore
    decodes 256 × the scale too low. For example, +15.0 A reads as −10.6 A. The expression is
    right across one band and wrong elsewhere. It can pass a check against captures that happen
    to sit in the good band.

    Write `(S15 << 8) | B17`. `canair validate pids` rejects a signed lower byte. It checks `|`,
    `+`, `<< 8n` and `* 256ⁿ` compositions, and `[Sn:Sm]` ranges that it finds used as a lower
    term.

The indices go straight into the PCI-inclusive buffer. A multi-byte range **must not straddle a
PCI byte** (B08/B16/B24/…). If it does, it folds a framing byte into the value. `canair validate
pids` flags ranges that span a PCI byte.

`canair bix` also warns you at lookup time and names the CAN frame of each byte. If you ask about
B09, it reports the frame and points out that the neighbouring B08 is a framing byte:

```text
$ canair bix B09
  WiCAN:     B09  (WiCAN AutoPID frame index: ISO-TP + PCI)
  CAN frame: 1   (B09 is in CAN frame 1: B08–B15, 8 bytes per frame)
  ISO-TP:    0x06  (payload index 6)
  Torque:    E  (data byte 4, sub=1; Torque app / Car Scanner)
  bix:       32  (OBDb bit index, sub=1)

  ⚠ B08 is a PCI byte — [B07:B09] would include it!
    Use (B07 << 8) | B09 instead of [B07:B09]
```

To combine B07 and B09 into one 16-bit value, write `(B07 << 8) | B09`. This steps over the
framing byte. Do not use the range form `[B07:B09]`.

## Note: standard OBD-II PIDs take a different path

For built-in `PID_STD` standard OBD PIDs, the firmware uses `extract_signal_value()` instead. It
works with `start_bit` and `bit_length` over merged frames and `priority_data`
(`wican-fw/components/autopid/autopid.c:4034-4042`).

This project does not use that path. All vehicle PIDs in this project are custom expressions.
The `evaluate_expression` rule with the PCI-inclusive `Bnn` index governs every definition in
`profiles/*/ecus/`.

## How canair keeps decoding faithful

The transports of canair return the payload **with the PCI bytes stripped**. Before canair
evaluates any `Bnn` expression, it **re-inserts** the PCI bytes. This rebuilds the exact buffer
that the firmware sees. These modules do the work:

- `src/canair/uds/wican_layout.py` — `uds_hex_to_wican_bytes()` (the reconstruction).
- `src/canair/uds/byteindex.py` — all four notations and their conversions
  (`wican_to_isotp`, `wican_to_torque`, `torque_*`, `conversion_table`).
- `src/canair/uds/layout.py` — which response bytes are header, and what each one is
  (`DID`/`LID`/`PID`/`RID`/`SF`/`CTRL`/`REJ SID`/`NRC`). It builds on the SID registry in
  `src/canair/uds/services.py`.

Use the tooling and do not convert by hand:

```bash
canair bix                                     # guided overview: legend + 2-frame table
canair bix B09                                 # one lookup: every notation + the CAN frame it's in
canair bix --torque                            # add the Torque letter column (Torque app, Car Scanner)
canair bix --obdb                              # add the OBDb bix (bit-index) column
canair bix --table                             # the full conversion table
canair bix -a 6101FFFF… --ecu BMS --pid 2101   # annotate a payload + overlay defined signals
canair bix -a 7F2231                           # a refused read: REJ SID + NRC, named
canair bix -a 710312A1…                        # a routine response: SF + RID
```

## See also

- [Byte indexing](byte-indexing.md) — the task-first primer for new-car users and PID
  contributors. This file is the deeper, firmware-grounded reference.
- Upstream discussion of the notation differences:
  [meatpiHQ/wican-fw#514](https://github.com/meatpiHQ/wican-fw/issues/514).
