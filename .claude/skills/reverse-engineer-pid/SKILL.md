---
name: reverse-engineer-pid
description: Reverse-engineer / decode a NEW Ioniq PID or DID end to end — discover via scans, capture payloads, analyze bytes (WiCAN Bnn / ISO-TP / PCI boundaries, expression syntax, conversion table), hypothesize and test expressions, write & validate parameter definitions, verify, integrate. Use when adding a new PID or parameter, decoding an unknown/undecoded payload, writing or fixing a WiCAN expression, working out a byte offset, or working the research: backlog. For general device/tool/ECU-status reference use the ioniq-reverse-engineering skill.
---

# Reverse-engineering a new Ioniq PID

This is the end-to-end workflow for taking a PID/DID from "unknown" to a
verified, decoded parameter in the profile's `pids/`. It assumes the broader project context
from the **ioniq-reverse-engineering** skill — load that too for vehicle facts,
the ECU status table, full `canair`/`wican-cli` flag reference, and MQTT/
profile details. This skill owns the *decoding* procedure and reference.

## Safety first (non-negotiable)

- **NEVER** use UDS programming session (`10 02`) or any firmware write/upload.
  This is a real, un-brickable car.
- Be gentle: old, slow ECUs. **One `canair` connection at a time, any transport**
  — canair enforces a `flock` mutex (`/tmp/wican-connection.lock`); a second
  `slcan-tcp` client hangs unserved and a second `wican-ws` WebSocket can lock up
  the WiCAN (power-cycle to recover). No concurrent requests to the same ECU.
- **Never reboot the WiCAN without asking.** Using the WebSocket terminal
  overrides AutoPID; ask before rebooting to restore the MQTT feed.
- Treat `0x22Fxxx` (flash/cal) as read-only. `2E` writes and `2F` IOControl can
  brick or actuate hardware — out of scope for PID decoding.
- Disable device sleep during a session: `wican sleep --disable` (re-enable
  after).

## The lifecycle

```
orient → prerequisites → discover → capture → inspect → hypothesize
       → define → decode/validate → verify → integrate
```

Progress is tracked per-ECU in the `research:` block of the profile's `pids/<ecu>.yaml`
(schema in `canlib/schema/pids_schema.yaml`), graduating:
`pending → captured → (decoded) → verify → done`, at which point a real
`parameters:` entry exists and is marked `verified: true`.

### 1. Orient — pick a target

```bash
canair research --summary                 # backlog counts by status/type/priority
canair research --priority P1             # highest-value open items
canair research --ecu MCU                 # one ECU's backlog
canair coverage --no-capture              # params defined but never captured
canair coverage --unmapped                # captured PIDs with undecoded bytes
```

`canair research` surfaces *planned* work; `canair coverage` surfaces *undecoded
bytes* in PIDs you already capture. The ECU status table (parent skill) shows
which ECUs are worth probing.

### 2. Prerequisites — power state & access

Decide the car power state the PID needs (`availability`/`prerequisite`:
`sleep, plugged, acc, acc2, ready, charging`) and whether the ECU needs waking /
an extended session. IGPM (0x770) and BCM (0x7A0) wake from CAN activity;
powertrain ECUs (BMS/VCU/MCU) generally need ACC/ignition or charging.

```bash
canair discover                  # which ECUs are answering right now
```

### 3. Discover — which DIDs respond

```bash
# service 21 (live data), service 22 (DID read; often needs session+wake)
canair scan MCU --service 21 --range 01-FF --save
canair scan IGPM --service 22 --range BC00-BCFF --session --wake --save
```

Record a research lead as you go:

```bash
canair pids add-research MCU --type decode --target 2102 \
    --status captured --priority P1 --prereq charging --notes "62 bytes, undecoded"
```

**Always record the scan outcome — a discovered DID must never be lost:**

- **New responding DID → register it immediately** as an `enabled: false` placeholder
  PID in `pids/<ecu>.yaml`, with the raw payload pasted into `notes` (and an empty
  `expression`), then add a `decode` research lead. This is the established project
  convention (e.g. `ESC 22C102`, `EPS 220101/220102`, `CLU 22B001/B003` are all such
  placeholders). Keeping it `enabled: false` means it is tracked and captured but stays
  out of the generated WiCAN profile until it's actually decoded. Before adding, always
  check whether the DID is already registered — a re-scan of a known DID should just
  refresh the payload/notes, not create a duplicate.
- **Negative probe (NRC / no response) → close the scan lead** with
  `canair pids set-status <ECU> "<target>" nrc --type scan` so nobody re-probes it.
  Use `nrc` for "probed, ECU said no / silent" and `done` for "scan complete, responders
  found and registered". Powertrain ECUs (BMS/VCU/MCU/LDC) are KWP2000/service-21 and
  reliably NRC every `22 xxxx` (Ioniq-5-sourced) DID — confirm once, mark `nrc`, move on.

### 4. Capture — record real payloads across states

```bash
# Preferred: decoded, session-managed, saved
canair query "query MCU:2102" --save --label "MCU 2102 driving" \
    --state "ready, driving" --notes "hard launches + regen"
# Capture change across time (values that move reveal what a byte means)
canair query "query MCU 2102" --monitor 1 --keep-all --save
```

Capture the SAME PID in DIFFERENT states (park vs drive, cold vs warm, charging
vs ready) — contrast is what lets you separate signal bytes from constants.
**Never hand-edit `captures/`.** Saves are journaled to `captures/.journal/` and
reconciled on exit (a killed/disconnected `--monitor` session is recoverable with
`canair captures --recover`); in `--monitor` the `state` is auto-suggested from
decoded values (press `s` to edit metadata live). After saving, run
`canair captures --summary`.

### 5. Inspect — see the bytes

```bash
canair captures MCU 2102                      # list captures + decoded
canair captures MCU:2102 --diff               # unique payloads, byte-diff
canair captures MCU:2102 --diff --since 2026-07-19   # scope by date
canair captures MCU:2102 --step               # interactive step-through
canair bix -1 --annotate 6101FFFF...          # map each byte -> Bnn/ISO-TP/Torque/role
```

Byte-diff highlights which bytes moved between states — your candidate signal
bytes. `canair bix --annotate` tells you each byte's WiCAN index and flags the PCI
bytes you must not read across (see Reference below).

### 6. Hypothesize — form an expression

Cross-reference the Kia Soul/Niro sheets and the Obsidian vault; watch the
PCI-boundary caution for `[Bnn:Bmm]`. See the Byte Index & Expression reference
at the bottom.

### 7. Test the expression WITHOUT committing

`canair decode --try` evaluates a candidate against every capture — no YAML edit:

```bash
canair decode MCU 2102 --try "MOTOR_RPM:RPM=[S10:S11]"        # value range across captures
canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100" --stats  # mean/median/stdev/distinct
# Validate by correlation against a known signal (the key RE lever):
canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM
# Or hunt visually: interactive plot, sweep byte interpretations + transforms.
canair decode MCU 2102 --plot                      # sweep interpretations, find the signal
canair decode MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r
```

**Scope the captures** so a candidate is judged on the relevant drive/state, not
the whole history (shared with `canair captures`): `--since`/`--until`/`--date`,
`--state SUBSTR`/`--label SUBSTR` (case-insensitive; the natural unit of drive
analysis, e.g. `--state 'MT->KW'`), and `--first N`/`--last N`. Combine with
`--stats --group-by state` to contrast a candidate across drive segments, or
`--compact --changes-only` to watch it evolve with stationary runs collapsed:

```bash
canair decode MCU 2102 --try "T=[S12:S13]/100" --state 'MT->KW' --stats  # one drive
canair decode MCU 2102 --stats --group-by state --state driving          # per-segment
canair decode ESC 22C101 --param REAL_SPEED_KMH --state 'MT->KW' --compact --changes-only
```

Iterate until the range is physical, the distribution makes sense (constant?
enum? continuous?), and — where a relationship should exist — the correlation
confirms it. A bad expression shows `ERROR` rather than hiding.

**`--plot` (interactive signal explorer)** is the fastest way to *find* a signal
when you don't yet have a candidate expression. It works even on a not-yet-defined
PID (raw payloads only). Keys:
- `←`/`→` move the byte offset (byte mode) or switch parameter (param mode)
- `t`/`T` cycle the interpretation type (`u8 i8 u16 i16 u24 i24 u32 i32 u64 i64 f16 f32 f64`)
- `e` toggle endianness · `f` cycle post-transform (`raw delta abs cumsum normalize smooth`)
- `+`/`-` zoom the x-axis · `,`/`.` pan · `0` reset x-range
- `i` toggle a modal listing the captures behind the current view (date/time, state, label, notes, file)
- `m` toggle byte↔param source · `o` overlay the `--corr` reference (with live Pearson r) · `q` quit

The caption under the chart shows the visible capture index range **and its
date/time span**, both tracking zoom/pan, so you always know which captures —
and when — the plotted segment came from. Byte mode shows the **equivalent
WiCAN expression** for the current interpretation (e.g. `[S10:S11]`) — copy it
straight into step 8 — and whether that byte is **already mapped** by a defined
parameter (`= mapped: NAME`, or `~ reads Bn: …` for a partial overlap, or
`unmapped`), so you don't re-decode known bytes. It also warns when a multi-byte
read crosses a PCI byte (garbage); endianness and float types with no direct
WiCAN expression are flagged as such. Zoom/pan (`+`/`-`/`,`/`.`) narrows the
x-axis to inspect a segment (e.g. a single launch or regen event); `i` lists the
exact captures in that segment.

### 8. Define — write it to pids/

Use `canair pids` (surgical, comment-preserving, auto-validated + auto-reverted
on schema failure) rather than hand-editing:

```bash
canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" \
    --unit RPM --min -10500 --max 10500 --unverified \
    --source "Kia Soul VMCU CSV" --notes "signed 16-bit BE at B10:B11 (ISO-TP 0x07:0x08)"
```

New params start `--unverified`. (Hand-editing `pids/` is allowed, but the tool
keeps field order/quoting correct and runs `canair validate pids` for you.)

### 9. Verify — confirm against reality

Confirm the decoded values across the full history and against known physical
state, then flip to verified:

```bash
canair decode MCU 2102                      # ranges (default) — sanity across captures
canair decode MCU 2102 --stats              # distribution / enum detection
canair coverage MCU 2102                    # any bytes still unmapped?
canair validate pids                        # schema + PCI-boundary checks

canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" --verified   # promote
canair pids set-status MCU 2102 done --type decode                       # close the lead
```

A parameter is `verified: true` only when validated against real data / known
state (physical correlation, matching a scan tool, or a definitive constant).

### 10. Integrate

```bash
canair wican                     # regenerate the profile's out/profile.json
canair wican --diff --wican home # compare to device (optional)
python3 -m pytest -q             # keep the suite green
```

Then consider an upstream wican-fw PR (see parent skill goals).

## Tool cheat-sheet (this workflow)

| Step | Tool |
|------|------|
| what to work on | `canair research`, `canair coverage` |
| talk to the car | `canair query`/`scan`/`discover` (`--monitor`, `--save`) |
| see captures | `canair captures` (`--diff`/`--step`/`--since`/`--until`) |
| map bytes | `canair bix --annotate` |
| test expressions | `canair decode --try` / `--stats` / `--corr` / `--plot` |
| write definitions | `canair pids upsert-param` / `add-research` / `set-status` |
| validate | `canair validate pids`, `canair coverage` |
| ship | `canair wican` |

---

## Reference: WiCAN byte index notation

WiCAN expressions index into the **raw CAN frame data including PCI bytes**. The
firmware's ELM327 parser (`parse_elm327_response()` in `autopid.c`) runs headers
ON and copies ALL 8 CAN data bytes per frame (including ISO-TP PCI bytes)
sequentially into a flat byte array.

### Byte layout (AutoPID internal format)

For a multi-frame response to `2101` on BMS (0x7E4):

```
Frame 0 (First Frame):  [10 3B] [61 01 FF FF FF FF]  → B00-B07
Frame 1 (Consecutive):  [21]    [d  d  d  d  d  d  d] → B08-B15
Frame 2 (Consecutive):  [22]    [d  d  d  d  d  d  d] → B16-B23
...
```

- `B00` = PCI high byte (0x10), `B01` = PCI low byte (length)
- `B02` = SID response (0x61), `B03` = PID echo (0x01)
- `B08` = PCI consecutive (0x21), `B09` = first actual data byte of frame 1
- PCI bytes occupy indices 0, 8, 16, 24, 32, 40, 48, 56, ...

### Byte indexing examples

For a `0x21` service request (PID `01`), the response starts `61 01 <data...>`:
- `B0` = `0x61` (service response ID)
- `B1` = `0x01` (PID echo)
- `B2` = first data byte

For a `0x22` service request (DID `C00B`), the response starts `62 C0 0B <data...>`:
- `B0` = `0x62` (service response ID)
- `B1` = `0xC0` (DID high byte)
- `B2` = `0x0B` (DID low byte)
- `B3` = first data byte

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` (multi-byte unsigned),
`[Snn:Smm]` (multi-byte signed), `Bnn:k` (bit k, 0=LSB). Operators:
`+ - * / << >> & | ^`. See `expression_parser.c` for the full reference.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes — it does NOT skip PCI
bytes.** If a multi-byte value spans a CAN frame boundary (B07-B08, B15-B16,
etc.), the PCI byte at B08/B16/... is included, producing garbage. Use manual
bit-shifting instead: `(B07 << 8) | B09` to skip the PCI byte at B08. Always use
`canair bix` to check whether a byte range crosses a PCI boundary.

```bash
canair bix w9        # WiCAN B09 → ISO-TP 0x06, Torque E, bix 32
canair bix E         # Torque letter → all notations
canair bix -2 w5     # 2-byte subfunction mode (22xxxx DIDs)
canair bix --table   # Full conversion table
canair bix -2 --annotate 62B0047402990C0040A000AAAA   # annotate a real payload
canair bix --annotate 6101FFFF...                     # service 21 (1-byte PID)
```

`--annotate` (`-a`) reconstructs the WiCAN frame with PCI bytes inserted and
prints each byte's WiCAN Bnn, ISO-TP index, Torque letter, bix, and role. Use
`-1` (default) for service 21, `-2` for service 22 DIDs.

### Conversion table (WiCAN ↔ ISO-TP ↔ Torque ↔ bix)

Each CAN frame has 8 data bytes. PCI bytes (WiCAN indices 0, 8, 16, 24, ...) are
consumed by ISO-TP framing and have no ISO-TP/Torque/bix equivalent. Torque
1/bix 1 are for 1-byte subfunctions (service `21xx`), Torque 2/bix 2 for 2-byte
subfunctions (service `22xxxx`).

| WiCAN | ISO-TP | Torque 1 | bix 1 | Torque 2 | bix 2 |
| ----- | ------ | -------- | ----- | -------- | ----- |
| 0     |        |          |       |          |       |
| 1     |        |          |       |          |       |
| 2     | 0x00   |          |       |          |       |
| 3     | 0x01   |          |       |          |       |
| 4     | 0x02   | A        | 0     |          |       |
| 5     | 0x03   | B        | 8     | A        | 0     |
| 6     | 0x04   | C        | 16    | B        | 8     |
| 7     | 0x05   | D        | 24    | C        | 16    |
| 8     |        |          |       |          |       |
| 9     | 0x06   | E        | 32    | D        | 24    |
| 10    | 0x07   | F        | 40    | E        | 32    |
| 11    | 0x08   | G        | 48    | F        | 40    |
| 12    | 0x09   | H        | 56    | G        | 48    |
| 13    | 0x0A   | I        | 64    | H        | 56    |
| 14    | 0x0B   | J        | 72    | I        | 64    |
| 15    | 0x0C   | K        | 80    | J        | 72    |
| 16    |        |          |       |          |       |
| 17    | 0x0D   | L        | 88    | K        | 80    |
| 18    | 0x0E   | M        | 96    | L        | 88    |
| 19    | 0x0F   | N        | 104   | M        | 96    |
| 20    | 0x10   | O        | 112   | N        | 104   |
| 21    | 0x11   | P        | 120   | O        | 112   |
| 22    | 0x12   | Q        | 128   | P        | 120   |
| 23    | 0x13   | R        | 136   | Q        | 128   |
| 24    |        |          |       |          |       |
| 25    | 0x14   | S        | 144   | R        | 136   |
| 26    | 0x15   | T        | 152   | S        | 144   |
| 27    | 0x16   | U        | 160   | T        | 152   |
| 28    | 0x17   | V        | 168   | U        | 160   |
| 29    | 0x18   | W        | 176   | V        | 168   |
| 30    | 0x19   | X        | 184   | W        | 176   |
| 31    | 0x1A   | Y        | 192   | X        | 184   |
| 32    |        |          |       |          |       |
| 33    | 0x1B   | Z        | 200   | Y        | 192   |
| 34    | 0x1C   | AA       | 208   | Z        | 200   |
| 35    | 0x1D   | AB       | 216   | AA       | 208   |
| 36    | 0x1E   | AC       | 224   | AB       | 216   |
| 37    | 0x1F   | AD       | 232   | AC       | 224   |
| 38    | 0x20   | AE       | 240   | AD       | 232   |
| 39    | 0x21   | AF       | 248   | AE       | 240   |
| 40    |        |          |       |          |       |
| 41    | 0x22   | AG       | 256   | AF       | 248   |
| 42    | 0x23   | AH       | 264   | AG       | 256   |
| 43    | 0x24   | AI       | 272   | AH       | 264   |
| 44    | 0x25   | AJ       | 280   | AI       | 272   |
| 45    | 0x26   | AK       | 288   | AJ       | 280   |
| 46    | 0x27   | AL       | 296   | AK       | 288   |
| 47    | 0x28   | AM       | 304   | AL       | 296   |
| 48    |        |          |       |          |       |
| 49    | 0x29   | AN       | 312   | AM       | 304   |
| 50    | 0x2A   | AO       | 320   | AN       | 312   |
| 51    | 0x2B   | AP       | 328   | AO       | 320   |
| 52    | 0x2C   | AQ       | 336   | AP       | 328   |
| 53    | 0x2D   | AR       | 344   | AQ       | 336   |
| 54    | 0x2E   | AS       | 352   | AR       | 344   |
| 55    | 0x2F   | AT       | 360   | AS       | 352   |
| 56    |        |          |       |          |       |
| 57    | 0x30   | AU       | 368   | AT       | 360   |
| 58    | 0x31   | AV       | 376   | AU       | 368   |
| 59    | 0x32   | AW       | 384   | AV       | 376   |
| 60    | 0x33   | AX       | 392   | AW       | 384   |
| 61    | 0x34   | AY       | 400   | AX       | 392   |
| 62    | 0x35   | AZ       | 408   | AY       | 400   |
| 63    | 0x36   | BA       | 416   | AZ       | 408   |
| 64    |        |          |       |          |       |
| 65    | 0x37   | BB       | 424   | BA       | 416   |
| 66    | 0x38   | BC       | 432   | BB       | 424   |
| 67    | 0x39   | BD       | 440   | BC       | 432   |
| 68    | 0x3A   | BE       | 448   | BD       | 440   |
| 69    | 0x3B   | BF       | 456   | BE       | 448   |
| 70    | 0x3C   | BG       | 464   | BF       | 456   |
| 71    | 0x3D   | BH       | 472   | BG       | 464   |

## Reference: UDS decoding conventions (Hyundai/Kia)

Source: `KB/EV/Hyundai Ioniq/Reverse engineering/Hyundai Kia UDS DID Conventions.md`

### PID categories

- **`0x21xx`** — fast live-data snapshots; no session/security; multiple params
  per response; manufacturer function byte `0x21`.
- **`0x22xx`** — structured; may need extended session (`10 03`); standard UDS
  ReadDataByIdentifier (`22`); some DIDs writable via `2E` — handle with care.

### DID paging vs indexing

- **Paging** (e.g. BMS): `2101`, `2102`, `2103`, `2104` each return a different
  block (the `xx` is a page number, not a DID).
- **Indexing**: `2101`, `2102` are sub-functions/pages within one dataset.

### DID range semantics

- `0x21xx` — live data, manufacturer-specific
- `0x22Bxxx` — cluster/display data
- `0x22Cxxx` — body/comfort (BCM, TPMS)
- `0x22Exxx` — powertrain (BMS, MCU, VCU, HVAC)
- `0x22Fxxx` — often flash/calibration — **do not write**

### Hyundai/Kia DID -1 offset (F1xx identity DIDs)

HK ECUs shift identity DIDs by **-1** from the UDS spec. When reading identity
DIDs, use the HK DID:

| Standard UDS DID | HK DID | Field            |
|------------------|--------|------------------|
| F188             | F187   | ECU Part Number  |
| F18C             | F18B   | Manufacture Date |
| F192             | F191   | Supplier HW No   |

`canair identity` queries both. The ECU answers the HK DID (e.g. `22F187` →
`62F187 <part number>`) while the standard DID may NRC. If a scan finds data
echoing a DID one less than requested, try the -1 DID directly.
