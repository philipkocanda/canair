# The WiCAN byte index vs. the raw CAN index

!!! warning "Why this matters — read first"

    Every `expression:` in this project (e.g. `B09/2`, `[B04:B05]`, `B10:5`)
    addresses bytes with the **WiCAN `Bnn` index**. That index is **not** the index
    into the reassembled CAN/UDS data most tools show you — it counts the ISO-TP
    framing (PCI) bytes too. Getting this wrong is the single most common
    reverse-engineering mistake in this repo: an expression that is correct in
    ISO-TP/Torque/OBDb terms is silently **off by one or two** (and grows further
    off with every additional CAN frame) when written as `Bnn`. This document is
    the authoritative, source-grounded explanation.

## Provenance — grounded in current `wican-fw` truth

This analysis was read directly out of the WiCAN firmware source checked out at
`wican-fw/`, not from second-hand notes:

| | |
|---|---|
| Repo | `https://github.com/meatpiHQ/wican-fw.git` |
| Branch | `ioniq-2017-profile-update` (tracking `fork/ioniq-2017-profile-update`) |
| Commit | `164f617de4cb6192ffed7dc8144eaa8321b76bb1` (`164f617`) |
| `git describe` | `v4.21-41-g164f617` |
| Commit tip subject | *"Add vehicle speed and more BMS fields to Ioniq 2017 profile"* |
| Commit date | `2026-07-21T00:59:40+02:00` |
| Working tree | clean (nothing to commit) |
| Analysis written | `2026-07-24 20:50 CEST` |

If the firmware buffer/indexing logic changes upstream, re-verify against the
files and line numbers cited below and update this doc.

## TL;DR

The WiCAN `Bnn` index is the position of a byte in the **flat buffer the firmware
builds by concatenating every data byte of every CAN frame of the response,
ISO-TP PCI (framing) bytes included, as one running index that does not reset per
frame.**

- vs. the **reassembled ISO-TP / UDS payload** (PCI stripped — what the raw
  `slcan-tcp` transport returns, and what SavvyCAN / ImHex show): **they differ**,
  by exactly the PCI bytes. Offset is **+2** at the start and grows **+1 at every
  8-byte frame boundary**. It is *not* a constant offset.
- vs. the **literal raw CAN *frame* bytes** (all 8 data bytes/frame, PCI kept):
  **essentially identical** — the WiCAN buffer *is* that concatenation. Only
  nuances (running index instead of per-frame; reconstruction padding) apply.

## See it at a glance (`canair bix`)

Don't take the layout on faith. `canair bix` with no arguments prints a
plain-language legend followed by a compact two-frame table. By default it shows
the two indices you reach for most — **WiCAN** and **ISO-TP** — alongside each
byte's framing `Role`, so you can *see* exactly where the PCI bytes fall:

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

Read **down** the `Role` column and the framing jumps out: **B00/B01** are the
First-Frame PCI, then **B08** (and B16, B24, …) is a Consecutive-Frame PCI byte.
Notice how ISO-TP `0x05` → `0x06` is a smooth run, but the WiCAN index jumps
**B07 → B09** across that boundary — the skipped `B08` is the framing byte.

`canair bix` shows WiCAN and ISO-TP by default. Add `--torque` for the **Torque**
letter column (the notation the Torque app, Car Scanner, and similar OBD apps use)
or `--obdb` for the OBDb **bix** (bit-index) column — they're distinct notations,
so request whichever you're cross-referencing (or both). `canair bix --table`
prints the whole table out to `--max`.

## What the WiCAN index actually is (firmware truth)

A UDS response arrives as one or more CAN frames carrying an ISO-TP payload. The
firmware turns the ELM327 text response into a byte buffer in
`parse_elm327_response()` — `wican-fw/main/autopid.c:1210`.

For each response line it strips the **arbitration-ID header** (the `7EC ` prefix)
and then copies **every remaining data byte of the frame, in order, into one flat
buffer** `response->data` — `wican-fw/main/autopid.c:1326-1341`. It does **not**
strip the ISO-TP PCI bytes; they are ordinary data bytes as far as this loop is
concerned. (AutoPID runs with ELM327 headers ON and spaces ON, so each line is
`7EC <b0> <b1> … `; the parser requires that header/space to locate the data.)

For the PID kinds this project defines — `PID_CUSTOM` / `PID_SPECIFIC` — the
expression is then evaluated **directly against that buffer**:

```c
// wican-fw/main/autopid.c:1691-1693
evaluate_expression((uint8_t*)param->expression, elm327_response.data, 0, &result)
```

And inside the evaluator, `Bnn` is a **direct array index** into that buffer:

```c
// wican-fw/main/expression_parser.c:168-182
} else if (expression[i] == 'B') {
    ...
    uint8_t value = data[index];      // <-- data[] IS elm327_response.data (PCI included)
    if (expression[i] == ':') {       // Bnn:k  -> bit k of that byte
        value = (value >> bit) & 1;
    }
```

So, definitively: **`Bnn` = the n-th byte of the concatenated CAN frame payloads,
ISO-TP PCI bytes included.**

### Where the PCI bytes land

Because each CAN frame contributes 8 data bytes and ISO-TP puts its framing at the
front of each frame:

- **B00, B01** — First-Frame PCI: `0x1L LL` (frame-type nibble `1` + 12-bit
  length). *Multi-frame responses only.*
- **B08, B16, B24, …** — one Consecutive-Frame PCI byte each: `0x2s` (sequence
  counter), at the start of every subsequent 8-byte block.
- Single-frame responses (≤7 payload bytes) instead have a **single** PCI byte at
  **B00** (`0x0L`, the length nibble), and real data starts at **B01**.

That means the actual UDS bytes sit at: multi-frame → B02–B07 (first 6), then
B09–B15, B17–B23, … (7 per consecutive frame); single-frame → B01 onward.

## Exactly how it differs from the "raw CAN index"

"Raw CAN index" can mean two things; here is each, precisely.

### 1. vs. the reassembled ISO-TP / UDS payload (PCI stripped) — THIS is the trap

This is what most tooling and the project's own raw `slcan-tcp` transport give you:
the ISO-TP payload with all PCI bytes removed. The WiCAN index includes exactly
those removed bytes, so the mapping is **not** a fixed offset — it starts at +2 and
gains another +1 at each frame boundary (each consecutive frame injects one more
PCI byte). From `canlib/byteindex.py:27` (`wican_to_isotp`):

- Frame 0: WiCAN `pos 2..7` → ISO-TP `0..5` (`pos 0,1` are PCI → no ISO-TP index)
- Frame N>0: WiCAN `pos 1..7` → ISO-TP `6 + (N-1)*7 + (pos-1)` (`pos 0` is PCI)

where `pos = wican_idx % 8` and `frame = wican_idx // 8`.

**Torque / OBDb `bix`** differs further still: it also skips the UDS **SID +
subfunction** (1 byte for `21xx` PIDs, 2 for `22xxxx` DIDs). So `Torque A` /
`bix 0` — the first UDS *data* byte — is **WiCAN B04** for a `21xx` PID, but
**WiCAN B05** for a `22xxxx` DID (SID at B02, the two DID bytes at B03–B04).

### 2. vs. the literal raw CAN frame bytes (all 8/frame, PCI kept) — essentially the same

The WiCAN buffer *is* the concatenation of those frame data bytes, so the values
line up 1:1. Two nuances only:

- It is a **single running index** across frames, not a `(frame, 0..7)` pair; the
  PCI bytes sit at 0/1, 8, 16, 24, … exactly as they do on the wire.
- canair **reconstructs** this buffer offline from the PCI-less payload
  (`canlib/autopid_layout.py`, `uds_hex_to_wican_bytes`) and **zero-pads** the
  trailing consecutive frame, whereas a real bus pads with whatever the ECU emits
  (often `0xAA` / `0x00` / `0x55`). So trailing *padding byte values* can differ —
  never the index. Don't reference padding bytes in expressions anyway.

## Worked example: a real capture, byte by byte

`canair bix --annotate` maps a real payload; add `--ecu`/`--pid` to overlay which
defined parameter reads each byte and to flag data bytes nothing maps yet. Below is
a genuine `BMS 2101` battery-status response from the bundled Ioniq profile:

```text
$ canair bix -a 6101FFFFFFFF80264826480300050E32 --ecu BMS --pid 2101
  WiCAN |  Hex | ISO-TP | Role   | Param
  ──────┼──────┼────────┼────────┼─────────────
  ── Frame 0 ───────────────────
    B00 | 0x10 |      — | PCI    |
    B01 | 0x10 |      — | PCI    |
    B02 | 0x61 |   0x00 | SID    |
    B03 | 0x01 |   0x01 | PID    |
    B04 | 0xFF |   0x02 |        | unmapped
    B05 | 0xFF |   0x03 |        | unmapped
    B06 | 0xFF |   0x04 |        | unmapped
    B07 | 0xFF |   0x05 |        | unmapped
  ── Frame 1 ───────────────────
    B08 | 0x21 |      — | PCI    |
    B09 | 0x80 |   0x06 |        | [SOC_BMS]
    B10 | 0x26 |   0x07 |        | unmapped
    B11 | 0x48 |   0x08 |        | unmapped
    B12 | 0x26 |   0x09 |        | unmapped
    B13 | 0x48 |   0x0A |        | unmapped
    B14 | 0x03 |   0x0B |        | [BMS_MAIN_RELAY:0] [CHARGER_CONNECTED:5] [CHARGING_DC:6] [CHARGING:7]
    B15 | 0x00 |   0x0C |        | [BATTERY_POWER]
  ── Frame 2 ───────────────────
    B16 | 0x22 |      — | PCI    |
    B17 | 0x05 |   0x0D |        | [BATTERY_POWER]
    B18 | 0x0E |   0x0E |        | [BATTERY_POWER]
    B19 | 0x32 |   0x0F |        | [BATTERY_POWER]
```

Everything the rest of this document claims is visible in one screen:

- **PCI bytes at B00, B01, B08, B16** — the `Role` column names them, and no
  parameter ever maps them.
- **`SOC_BMS = B09/2`** reads the state-of-charge byte at **B09** — the very first
  byte *after* the Frame 1 PCI byte at B08. Here `B09 = 0x80 = 128`, so
  `128 / 2 = 64 %`.
- That same physical byte is **ISO-TP `0x06`** (with `--torque`: **Torque `E`**;
  with `--obdb`: **bix `32`**). If you had copied a "byte 6" offset from a
  SavvyCAN/ISO-TP view and written `B06`, you would instead read `0xFF` — one of
  the `FF FF FF FF` bytes at B04–B07 — giving `127.5 %`. A silent, plausible-looking
  bug. **That off-by-PCI shift is the entire trap this document is about**, and the
  overlay makes it obvious.
- Bit-level params can share one byte (`B14`: four flags at bits 0/5/6/7 via
  `B14:k`), and a multi-byte value can span several (`BATTERY_POWER` over B15–B19).
  `unmapped` flags data bytes still open for reverse-engineering.

For a `22xxxx` DID, pass `-2` so the two subfunction bytes are labelled `DID`
(B03–B04); the first real data byte is then **B05**, not B04 (also where `Torque A`
lands under `--torque`).

## Multi-byte and bit forms (same buffer, same rule)

The same PCI-inclusive `data[]` buffer backs every accessor:

- `Bnn` — unsigned byte `data[nn]` (`expression_parser.c:168`).
- `Snn` — signed byte `(int8_t)data[nn]` (`expression_parser.c:183`).
- `Bnn:k` — bit `k` of `data[nn]` (`(value >> k) & 1`, `expression_parser.c:176`).
- `[Bn:Bm]` — **big-endian** multi-byte unsigned value across those indices:
  `sum |= data[j] << ((m - j) * 8)` — first index is most significant
  (`expression_parser.c:112-114`). Max span 8 bytes (64-bit).
- `[Sn:Sm]` — signed version, sized 8/16/32/64-bit by span
  (`expression_parser.c:118-160`).

Because the indices go straight into the PCI-inclusive buffer, **a multi-byte
range must not straddle a PCI byte** (B08/B16/B24/…) or it will fold a framing
byte into the value. `canair validate pids` flags ranges that span a PCI byte.

`canair bix` warns you at lookup time, too — and names the CAN frame each byte
lives in. Ask it about B09 and it reports the frame and points out that the
neighbouring B08 is a framing byte:

```text
$ canair bix w9
  WiCAN:     B09  (WiCAN AutoPID frame index: ISO-TP + PCI)
  CAN frame: 1   (B09 is in CAN frame 1: B08–B15, 8 bytes per frame)
  ISO-TP:    0x06  (payload index 6)
  Torque:    E  (data byte 4, sub=1; Torque app / Car Scanner)
  bix:       32  (OBDb bit index, sub=1)

  ⚠ B08 is a PCI byte — [B07:B09] would include it!
    Use (B07 << 8) | B09 instead of [B07:B09]
```

So to combine B07 and B09 into one 16-bit value, write `(B07 << 8) | B09` —
stepping over the framing byte — rather than the range form `[B07:B09]`.

## Note: standard OBD-II PIDs take a different path

For built-in `PID_STD` standard OBD PIDs the firmware instead uses
`extract_signal_value()` with `start_bit`/`bit_length` over merged frames /
`priority_data` (`wican-fw/main/autopid.c:1729-1746`). That path is **not** what
this project uses — all our vehicle PIDs are custom expressions, so the
`evaluate_expression` → PCI-inclusive `Bnn` rule above is the one that governs
every definition in `profiles/*/ecus/`.

## How canair keeps decoding faithful

canair's transports hand back the **PCI-stripped** payload, so before evaluating
any `Bnn` expression it **re-inserts** the PCI bytes to reconstruct the exact
buffer the firmware sees:

- `canlib/autopid_layout.py` — `uds_hex_to_wican_bytes()` (the reconstruction).
- `canlib/byteindex.py` — all four notations and their conversions
  (`wican_to_isotp`, `wican_to_torque`, `torque_*`, `conversion_table`).

Use the tooling instead of converting by hand:

```bash
canair bix                                     # guided overview: legend + 2-frame table
canair bix w9                                  # one lookup: every notation + the CAN frame it's in
canair bix --torque                            # add the Torque letter column (Torque app, Car Scanner)
canair bix --obdb                              # add the OBDb bix (bit-index) column
canair bix --table                             # the full conversion table
canair bix -a 6101FFFF… --ecu BMS --pid 2101   # annotate a payload + overlay defined params
```

## See also

- [Byte indexing](byte-indexing.md) — the task-first primer for new-car users and
  PID contributors (this file is the deeper, firmware-grounded reference).
- Upstream discussion of the notation differences:
  [meatpiHQ/wican-fw#514](https://github.com/meatpiHQ/wican-fw/issues/514).
