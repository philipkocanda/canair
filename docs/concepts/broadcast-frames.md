# Broadcast CAN frames (domain B)

canair handles **two kinds of controller area network (CAN) data**. It analyzes and defines them
in different ways:

| Domain | What it is | Identity | Signal definition |
|---|---|---|---|
| **A. Diagnostics** | request/response Unified Diagnostic Services (UDS) or Keyword Protocol 2000 (KWP2000) over the ISO 15765-2 transport protocol (ISO-TP) | `(ECU, PID/DID)` | a freeform **WiCAN `expression`** over `Bnn` bytes, in `ecus/` |
| **B. Broadcast frames** | periodic frames that no request elicits | **arbitration ID** | a linear signal in `signals/<bus>.yaml`, compatible with the CAN database (DBC) format |

In domain A, the identity `(ECU, PID/DID)` is an electronic control unit (ECU) plus a parameter ID
(PID) or data identifier (DID).

Most of canair is domain A (the [bring-your-own-car](../bring-your-own-car/overview.md) journey).
This page covers **domain B**: the passively broadcast traffic. This traffic carries drive-mode,
regen, thermal and body signals. Often these signals are *only* on the internal bus. An on-board
diagnostics (OBD-II) diagnostic read never exposes them.

> On many cars (the bundled Ioniq included) the **OBD-II port is gateway-isolated**. Passive
> sniffing there sees almost no broadcast traffic. The realistic source of frames is an
> **imported log**, not `canair sniff` on the OBD port. The log comes from SavvyCAN, another tool,
> or a device wired to an internal bus. Import is the front door.

## The workflow

```
import a frame log → correlate / hunt → define signals → export (DBC)
```

### 1. Import a frame log

`canair import can` reads a raw frame log. It stores the log **verbatim** under the
`captures/can/` directory of the profile. It indexes the metadata of the log in
`captures/can/index.yaml` (frame count, distinct arbitration IDs, bitrate, …). High-volume logs
stay native. canair does **not** explode them into the `captures/*.json` schema.

```bash
canair import can drive.blf --label "drive fwd/N/rev" --bitrate 500000
canair import can drive.asc            # .asc / .blf / candump .log / .trc
canair import can savvycan.csv         # SavvyCAN GVRET (.csv, auto-detected)
canair captures can                    # list imported logs
```

canair reads these formats: `.asc`, `.blf`, python-can `.csv`, candump `.log`, `.trc` and
**SavvyCAN GVRET `.csv`**. canair detects GVRET from its header. To force it, use
`--format gvret`.

### 2. Find signals — correlate / hunt

Frame bytes go into the *same* analysis engine as diagnostic captures. You reference a frame byte
as **`0xID:rN`**. This is raw-CAN space. It has no ISO-TP framing and no protocol control
information (PCI) bytes, and it is distinct from WiCAN `Bnn`.

```bash
# Which broadcast bytes move together across a drive?
canair correlate can drive.blf --min-r 0.9

# Which byte of arbitration ID 0x220 tracks a byte you already know (e.g. wheel
# speed on 0x386)?  Sweeps every byte × interpretation and ranks by correlation.
canair hunt can drive.blf --id 0x220 --against 0x386:r0

# Which positions are the SAME signal broadcast on two arbitration IDs?
# (e.g. wheel speed mirrored on 0x386 and 0x331). --bits for bit-level,
# --allow-offset to also accept a different zero/scale.
canair correlate can drive.blf --find-mirrors
```

Both commands reuse the correlation, interpretation-sweep and linear-fit machinery of the
diagnostic `correlate uds` and `hunt uds`. The output (ranked `|r|`, fit, unit guess) therefore
reads the same.

- `correlate can` takes `--bits`, `--min-r` and `--json`.
- `hunt can` takes the required `--id` and `--against`, plus `--min-n`, `--top` and `--json`.

The `can` kind is the domain-B counterpart of the default `uds` kind. A bare `canair correlate …`
or `hunt …` still targets diagnostic captures.

### 3. Define signals

When you identify a byte or bit field, record it in the **`signals/`** sidecar. This is the
domain-B analogue of the signals of a PID. A broadcast signal differs from the freeform WiCAN
`expression`. It is a **linear** model (`physical = raw*scale + offset` over a contiguous bit
range). It is deliberately DBC-compatible.

```bash
canair signals upsert powertrain 0x386 WHL_SPD_FL \
    --start-bit 0 --length 14 --byte-order little --scale 0.03125 --unit km/h \
    --source "IONIQ_PCAN_drive.csv (fetch: scripts/fetch/fetch_can_corpus.py)"
canair signals list                    # review
canair validate signals                # structural check
```

The edits (via `canair signals` and `src/canair/yaml_edit/signals.py`) change only the target
entry and keep comments. canair validates each edit and reverts it automatically on failure.

#### Bit numbering and value types

`--start-bit` follows DBC numbering. For `--byte-order big`, it names the *most significant* bit
of the field. For example, a 16-bit big-endian field in data bytes 4-5 is
`--start-bit 39 --length 16 --byte-order big`.

A raw value is an unsigned integer unless you pass one of these flags:

- `--signed` — two's complement (DBC `@0-`/`@1-`). Use it for anything that can go negative, such
  as a motor speed in reverse.
- `--float` — an IEEE 754 value, 32 or 64 bits long (DBC `SIG_VALTYPE_`).

#### Update an existing signal

If you upsert a signal that already exists, canair **changes only what you pass**. The rest of the
signal keeps its stored value: byte order, provenance, notes, verification and typed decoding. For
example, `canair signals upsert powertrain 0x386 WHL_SPD_FL --verified` only marks the signal as
verified.

- `--unsigned` and `--integer` undo `--signed` and `--float`.
- An empty `--unit ""`, `--source ""` or `--notes ""` removes that field.
- `--clear FIELD` (repeatable) removes `scale`, `offset`, `min` or `max` too.

#### Message options

Message options work with any upsert: `--msg-name`, `--tx-ecu`, `--msg-length` and the frame
format.

- `--msg-length` is the DBC data length code (DLC). It is 0-8 bytes, or a size for CAN with
  flexible data rate (CAN FD): 12, 16, 20, 24, 32, 48 or 64. It must be long enough for every
  signal.
- An ID above `0x7FF` is an extended (29-bit) frame.
- A frame longer than 8 bytes is CAN FD without further flags.
- `--extended` marks an extended frame that is numbered at or below `0x7FF`.
- `--fd` marks a CAN FD frame of 8 bytes or fewer.
- Arbitration IDs end at `0x1FFFFFFF`.

#### Provenance and evidence

Record **where the signal came from** in `--source`. Use a reproducible provenance string: the log
you used, a reference sheet, or `dbc:<file>` when the signal came from a DBC import. Keep the
supporting *evidence* (correlations, sample counts, reasoning) in `--notes`.

### 4. Interop — DBC import / export

Bootstrap `signals/` from an existing DBC. You can also share your DBC with SavvyCAN, cabana,
cantools or the Wireshark CAN dissector:

```bash
canair import dbc car.dbc --bus powertrain --dry-run   # preview
canair import dbc car.dbc --bus powertrain             # DBC → signals/
canair export dbc --bus powertrain -o mycar.dbc        # signals/ → DBC
```

#### DBC repairs

canair loads real-world DBCs (with overlapping signals) in non-strict mode. If cantools rejects a
DBC as written, canair repairs it in memory and parses it again. canair never changes the file
itself. It lists every repair with its line (and under `repairs` in `--json`).

The repairs cover dialect quirks that are common in hand-maintained DBCs such as those of opendbc:

- a malformed `CM_` comment (dropped, because canair does not import comments)
- a 29-bit ID written without the DBC extended flag
- a number without its leading zero (`.25`)
- a `VAL_` table without its closing `;`
- missing `BS_:`/`BU_:` header lines
- a message or signal name that starts with a digit. This is the one repair you see in the
  result: the name gains a `_` prefix (`0_COUNTER` → `_0_COUNTER`).

The full list is in `src/canair/interop/dbc/repair.py`.

#### What a round trip carries

A round trip carries:

- **Each signal's** layout (start bit, length, byte order), signedness, integer or float type,
  scale, offset, min/max and unit. It also carries the value table (DBC `VAL_`) when the signal is
  an unscaled integer. canair imports that signal as `type: enum` with `values`.
- **Each message's** name, data length (`length`, the DBC DLC) and frame format. The frame format
  is standard or extended, classic or CAN FD. canair writes it with the DBC `VFrameFormat`
  attribute.

A round trip does not carry:

- messages without signals
- multiplexing (the signals of a multiplexed message import as plain, overlapping signals)
- value tables on scaled or float signals. A DBC table uses the raw value as its key. The `values`
  of canair use the decoded value. The import lists the signals that this affects.
- comments, senders/receivers, attributes such as cycle times, and signal groups

`canair validate signals` rejects some messages, for example a signal that overruns its frame or a
scale of 0. The import skips such a message and lists it. The import does not fail as a whole.

One `signals/<bus>.yaml` keys messages by number. canair therefore refuses a DBC with a standard
and an extended frame of the same number. Import the two halves of such a DBC into separate buses.

`scripts/check/check_dbc_corpus.py --fetch` checks this against
[commaai/opendbc](https://github.com/commaai/opendbc). It imports, validates, exports and
re-decodes every DBC. It compares the result against the parse of cantools. The check is
opt-in and fetches into the gitignored `references/can/`.

## Storing raw-CAN logs (what's committed vs. fetched)

Raw frame logs get large. A few minutes of one bus is megabytes. Some logs come from other
people's cars under unclear licenses. canair splits the logs by **origin** and **license**:

| Origin | License | Where it lives | Committed? |
| --- | --- | --- | --- |
| **Your own capture** | yours | `profiles/<car>/captures/can/` | **Yes, fully** — via Git LFS when large |
| **Third-party, redistributable** (CC0/CC-BY/MIT/public-domain/explicit permission) | permits redistribution | `profiles/<car>/captures/can/` (or `references/can/`) with the license recorded next to it | **Yes** — via Git LFS when large |
| **Third-party, no / unclear license** (e.g. the uhi22 Ioniq-28 corpus) | none | gitignored `references/can/` | **No** — fetch on demand (`scripts/fetch/fetch_can_corpus.py`) + a tiny fair-use excerpt under `tests/fixtures/can/` |

The rule of thumb: **your data goes in. Other people's data goes in only if the license lets you
redistribute it.** A partial slice of unlicensed data is still unlicensed. The committed
`tests/fixtures/can/` excerpts are acceptable because they are *minimal, hand-trimmed fair-use*
samples. They are not acceptable because they are "only part" of the log. Git LFS is a storage
mechanism only. It does **not** grant any redistribution right, so it never changes the license
question.

### Git LFS

[Git LFS](https://git-lfs.com) tracks large binary and high-volume frame logs, so the repository
history stays small. The tracking rules are in `.gitattributes`:

- `*.blf`, `*.asc`, `*.trc` and everything under `profiles/*/captures/can/**` are stored in LFS.
  The exception is `captures/can/index.yaml`. It stays in plain git, so `canair validate can` can
  read it in a checkout without the LFS objects, as in continuous integration (CI).
- The tiny `tests/fixtures/**` excerpts are explicitly in **plain git**, so they stay diffable and
  inspectable.

Contributors need Git LFS installed (`git lfs install`). Without it, a fresh clone shows pointer
files instead of the log contents. The free tier of GitHub caps LFS storage and bandwidth. This is
another reason that unlicensed corpora stay fetch-on-demand and are not committed.

## Where things live

- **`captures/can/`** — imported frame logs, native, indexed by `index.yaml` (schema
  `src/canair/schema/can_index_schema.json`). Git LFS stores large logs (see
  [Storing raw-CAN logs](#storing-raw-can-logs-whats-committed-vs-fetched)).
- **`signals/<bus>.yaml`** — broadcast signal maps, one file per bus (schema
  `src/canair/schema/signals_schema.yaml`).

Both are optional and absent until you import broadcast data. `canair validate can` and
`canair validate signals` check them. They skip the check without a message when the files do not
exist. See `plans/2026-07-24-raw-can-analysis.md` for the design.
