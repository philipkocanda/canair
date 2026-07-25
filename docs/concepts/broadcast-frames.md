# Broadcast CAN frames (domain B)

canair handles **two kinds of CAN data**, and they're analysed and defined
differently:

| Domain | What it is | Identity | Signal definition |
|---|---|---|---|
| **A. Diagnostics** | request/response UDS/KWP2000 over ISO-TP | `(ECU, PID/DID)` | a freeform **WiCAN `expression`** over `Bnn` bytes, in `ecus/` |
| **B. Broadcast frames** | periodic frames no request elicits | **arbitration ID** | a DBC-compatible **linear** signal in `signals/<bus>.yaml` |

Most of canair is domain A (the [bring-your-own-car](../bring-your-own-car/overview.md)
journey). This page covers **domain B** — the passively-broadcast traffic that
carries drive-mode / regen / thermal / body signals which are often *only* on the
internal bus, never exposed to an OBD-II diagnostic read.

> On many cars (the bundled Ioniq included) the **OBD-II port is
> gateway-isolated** — passive sniffing there sees ~no broadcast traffic. So the
> realistic source of frames is an **imported log** (from SavvyCAN, another tool,
> or a device wired to an internal bus), not `canair sniff` on the OBD port.
> Import is the front door.

## The workflow

```
import a frame log → correlate / hunt → define signals → export (DBC)
```

### 1. Import a frame log

`canair import can` reads a raw frame log and stores it **verbatim** under the
profile's `captures/can/`, indexing its metadata in `captures/can/index.yaml`
(frame count, distinct arbitration IDs, bitrate, …). High-volume logs stay
native — they are **not** exploded into the `captures/*.yaml` schema.

```bash
canair import can drive.blf --label "drive fwd/N/rev" --bitrate 500000
canair import can drive.asc            # .asc / .blf / candump .log / .trc
canair import can savvycan.csv         # SavvyCAN GVRET (.csv, auto-detected)
canair captures --can                  # list imported logs
```

Formats: `.asc`, `.blf`, python-can `.csv`, candump `.log`, `.trc`, and **SavvyCAN
GVRET `.csv`** (auto-detected by header, or force with `--format gvret`).

### 2. Find signals — correlate / hunt

Frame bytes flow into the *same* analysis engine as diagnostic captures — a
frame byte is referenced as **`0xID:rN`** (raw-CAN space: no ISO-TP framing, no
PCI, distinct from WiCAN `Bnn`).

```bash
# Which broadcast bytes move together across a drive?
canair correlate --can-log drive.blf --min-r 0.9

# Which byte of arbitration ID 0x220 tracks a byte you already know (e.g. wheel
# speed on 0x386)?  Sweeps every byte × interpretation and ranks by correlation.
canair hunt --can-log drive.blf --id 0x220 --against 0x386:r0
```

Both reuse the correlation / interpretation-sweep / linear-fit machinery of the
diagnostic `correlate`/`hunt`, so the output (ranked `|r|`, fit, unit guess) reads
the same. `--bits`, `--id`, `--min-r`, `--json`, etc. apply.

### 3. Define signals

Once you've identified a byte/bit field, record it in the **`signals/`** sidecar —
the domain-B analogue of a PID's parameters. Unlike the freeform WiCAN
`expression`, a broadcast signal is a **linear** model (`physical = raw*scale +
offset` over a contiguous bit range), deliberately DBC-compatible.

```bash
canair signals upsert powertrain 0x386 WHL_SPD_FL \
    --start-bit 0 --length 14 --byte-order little --scale 0.03125 --unit km/h \
    --source "IONIQ_PCAN_drive.csv (fetch: scripts/fetch_can_corpus.py)"
canair signals list                    # review
canair validate signals                # structural check
```

Edits are surgical, comment-preserving, validated, and auto-reverted on failure
(via `canair signals` / `canlib/signals_edit.py`) — never hand-edit `signals/`.

Record **where the signal came from** in `--source` (a reproducible provenance
string — the log used, a reference sheet, or `dbc:<file>` when it came from a DBC
import); keep the supporting *evidence* (correlations, sample counts, reasoning)
in `--notes`.

### 4. Interop — DBC import / export

Bootstrap `signals/` from an existing DBC, or share yours with SavvyCAN / cabana /
cantools / the Wireshark CAN dissector:

```bash
canair import dbc car.dbc --bus powertrain --dry-run   # preview
canair import dbc car.dbc --bus powertrain             # DBC → signals/
canair export dbc --bus powertrain -o mycar.dbc        # signals/ → DBC
```

Real-world DBCs (with overlapping signals) load in non-strict mode; the linear
model round-trips losslessly.

## Where things live

- **`captures/can/`** — imported frame logs, native, indexed by `index.yaml`
  (schema `canlib/schema/can_index_schema.json`).
- **`signals/<bus>.yaml`** — broadcast signal maps, one file per bus (schema
  `canlib/schema/signals_schema.yaml`).

Both are optional and absent until you import broadcast data; `canair validate
can` and `canair validate signals` check them and quietly skip when they don't
exist. See `plans/2026-07-24-raw-can-analysis.md` for the design.
