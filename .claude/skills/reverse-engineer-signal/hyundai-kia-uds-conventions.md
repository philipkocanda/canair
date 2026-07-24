# Reference: UDS decoding conventions (Hyundai/Kia example)

Marque-specific reference for the `reverse-engineer-signal` skill. Load this when
working the **bundled Ioniq** (or another Hyundai/Kia) and you need the DID
conventions. **These are marque-specific, not universal** — they're a worked
example of the kind of manufacturer patterns worth discovering for *your* car; DID
range semantics, paging vs indexing, and identity-DID offsets all vary by OEM. For
another car, expect a different scheme and re-derive it.

Source: `KB/EV/Hyundai Ioniq/Reverse engineering/Hyundai Kia UDS DID Conventions.md`

The generic protocol distinction (UDS `0x22` vs KWP2000 `0x21`, tied to each ECU's
`id_protocol`) is explained in `docs/concepts/ecu-protocols.md` and, at a glance,
in the skill's own body.

## PID categories

The two-hex vs four-hex PID prefix is the **diagnostic service byte** (SID), and
it reflects which protocol the ECU speaks (its `id_protocol`) — not decoration.
canair auto-selects the service from `id_protocol`, so a bare identifier usually
works, but the prefix is the literal first request byte:

- **`0x21xx`** — **KWP2000** *ReadDataByLocalIdentifier* (SID `0x21`, 1-byte LID →
  `01`–`FF`). Fast live-data snapshots; no session/security; multiple params per
  response. The powertrain ECUs on the bundled Ioniq (BMS/MCU/VCU/OBC) are
  `id_protocol: KWP2000` and page this way (`2101`, `2102`, `21F2`).
- **`0x22xxxx`** — **UDS** *ReadDataByIdentifier* (SID `0x22`, 2-byte DID → 65 536
  possible). Structured; may need extended session (`10 03`); some DIDs writable
  via `2E` — handle with care. Body/cluster ECUs (CLU/IGPM/BCM/ESC…) are
  `id_protocol: UDS` and use `22xxxx` DIDs.

The differing identifier widths (1-byte LID vs 2-byte DID) follow directly from
the two services. Prefer writing PIDs with their full prefix (`2101`, `22B002`);
`canair bix -1`/`-2` and the response echo length (`61 01 …` vs `62 B0 02 …`)
follow the same 1- vs 2-byte-subfunction split. User-facing summary lives in
`docs/concepts/ecu-protocols.md`.

## DID paging vs indexing

- **Paging** (e.g. BMS): `2101`, `2102`, `2103`, `2104` each return a different
  block (the `xx` is a page number, not a DID).
- **Indexing**: `2101`, `2102` are sub-functions/pages within one dataset.

## DID range semantics

- `0x21xx` — live data, manufacturer-specific
- `0x22Bxxx` — cluster/display data
- `0x22Cxxx` — body/comfort (BCM, TPMS)
- `0x22Exxx` — powertrain (BMS, MCU, VCU, HVAC)
- `0x22Fxxx` — often flash/calibration — **do not write**

## Hyundai/Kia DID -1 offset (F1xx identity DIDs)

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
