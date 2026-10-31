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
canair captures can                    # list imported logs
```

Formats: `.asc`, `.blf`, python-can `.csv`, candump `.log`, `.trc`, and **SavvyCAN
GVRET `.csv`** (auto-detected by header, or force with `--format gvret`).

### 2. Find signals — correlate / hunt

Frame bytes flow into the *same* analysis engine as diagnostic captures — a
frame byte is referenced as **`0xID:rN`** (raw-CAN space: no ISO-TP framing, no
PCI, distinct from WiCAN `Bnn`).

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

Both reuse the correlation / interpretation-sweep / linear-fit machinery of the
diagnostic `correlate uds` / `hunt uds`, so the output (ranked `|r|`, fit, unit
guess) reads the same. `--bits`, `--id`, `--min-r`, `--json`, etc. apply. (The
`can` kind is the domain-B counterpart of the default `uds` kind — a bare
`canair correlate …` / `hunt …` still targets diagnostic captures.)

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
(via `canair signals` / `canlib/signals_edit.py`).

`--start-bit` follows DBC numbering, so for `--byte-order big` it names the field's
*most significant* bit: a 16-bit big-endian field in data bytes 4-5 is
`--start-bit 39 --length 16 --byte-order big`. A raw value is an unsigned integer
unless you pass `--signed` (two's complement, DBC `@0-`/`@1-`) — use it for anything
that can go negative, such as a motor speed in reverse — or `--float` (an IEEE 754
value, 32 or 64 bits long; DBC `SIG_VALTYPE_`).

Upserting a signal that already exists **changes only what you pass**: the rest of
the signal (its byte order, provenance, notes, verification, typed decoding) keeps
its stored value, so `canair signals upsert powertrain 0x386 WHL_SPD_FL --verified`
just marks it verified. `--unsigned` and `--integer` undo `--signed` and `--float`,
an empty `--unit ""` / `--source ""` / `--notes ""` removes that field, and
`--clear FIELD` (repeatable) removes `scale`, `offset`, `min` or `max` too.

Message options ride along on any upsert: `--msg-name`, `--tx-ecu`, `--msg-length`
(the DBC DLC: 0-8 bytes, or a CAN FD size 12/16/20/24/32/48/64, and long enough for
every signal) and the frame format. An ID above `0x7FF` is an extended (29-bit)
frame and a frame longer than 8 bytes is CAN FD without saying so; `--extended`
marks an extended frame numbered at or below `0x7FF`, and `--fd` a CAN FD frame of 8
bytes or fewer. Arbitration IDs end at `0x1FFFFFFF`.

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

Real-world DBCs (with overlapping signals) load in non-strict mode. A DBC that
cantools rejects as written is repaired in memory and parsed again; the file itself
is never changed, and every repair is listed with its line (and under `repairs` in
`--json`). The repairs cover the dialect quirks common in hand-maintained DBCs such
as opendbc's: a malformed `CM_` comment (dropped, since comments are not imported),
a 29-bit ID written without the DBC extended flag, a number without its leading
zero (`.25`), a `VAL_` table without its closing `;`, missing `BS_:`/`BU_:` header
lines, and a message or signal name starting with a digit — the one repair you see
in the result, as the name gains a `_` prefix (`0_COUNTER` → `_0_COUNTER`). The
list is in `canlib/dbc_repair.py`.

A round trip carries:

- **each signal's** layout (start bit, length, byte order), signedness, integer or
  float type, scale, offset, min/max and unit, and its value table (DBC `VAL_`) when
  the signal is an unscaled integer — imported as `type: enum` with `values`;
- **each message's** name, data length (`length`, the DBC DLC) and frame format:
  standard or extended, classic or CAN FD (written with the DBC `VFrameFormat`
  attribute).

Not carried: messages without signals; multiplexing (a multiplexed message's
signals import as plain, overlapping ones); value tables on scaled or float signals
(a DBC table is keyed by the raw value, canair's `values` by the decoded one — the
import lists the signals affected); comments, senders/receivers, attributes such as
cycle times, and signal groups. A message `canair validate signals` would reject —
a signal overrunning its frame, a scale of 0 — is skipped and listed rather than
failing the whole import. One `signals/<bus>.yaml` keys messages by number, so a
DBC with a standard and an extended frame of the same number is refused; import its
two halves into separate buses.

`scripts/check_dbc_corpus.py --fetch` checks this against
[commaai/opendbc](https://github.com/commaai/opendbc): it imports, validates,
exports and re-decodes every DBC, comparing against cantools' own parse. It is
opt-in and fetches into the gitignored `references/can/`.

## Storing raw-CAN logs (what's committed vs. fetched)

Raw frame logs get large (a few minutes of one bus is megabytes), and some come
from other people's cars under unclear licenses. canair splits them by
**origin** and **license**:

| Origin | License | Where it lives | Committed? |
| --- | --- | --- | --- |
| **Your own capture** | yours | `profiles/<car>/captures/can/` | **Yes, fully** — via Git LFS when large |
| **Third-party, redistributable** (CC0/CC-BY/MIT/public-domain/explicit permission) | permits redistribution | `profiles/<car>/captures/can/` (or `references/can/`) with the license recorded next to it | **Yes** — via Git LFS when large |
| **Third-party, no / unclear license** (e.g. the uhi22 Ioniq-28 corpus) | none | gitignored `references/can/` | **No** — fetch on demand (`scripts/fetch_can_corpus.py`) + a tiny fair-use excerpt under `tests/fixtures/can/` |

The rule of thumb: **your data goes in; other people's data goes in only if the
license lets you redistribute it.** A partial slice of unlicensed data is still
unlicensed — what makes the committed `tests/fixtures/can/` excerpts acceptable
is that they are *minimal, hand-trimmed fair-use* samples, not that they're
"only part" of the log. Git LFS is a storage mechanism only; it does **not**
grant any redistribution right, so it never changes the license question.

### Git LFS

Large binary/high-volume frame logs are tracked by [Git LFS](https://git-lfs.com)
so the repository history stays small. The tracking rules live in
`.gitattributes`:

- `*.blf`, `*.asc`, `*.trc` and everything under `profiles/*/captures/can/**`
  are stored in LFS.
- the tiny `tests/fixtures/**` excerpts are explicitly kept in **plain git** so
  they stay diffable and inspectable.

Contributors need Git LFS installed (`git lfs install`) or a fresh clone will
show pointer files instead of the log contents. GitHub's free tier caps LFS
storage/bandwidth, another reason unlicensed corpora stay fetch-on-demand rather
than committed.

## Where things live

- **`captures/can/`** — imported frame logs, native, indexed by `index.yaml`
  (schema `canlib/schema/can_index_schema.json`). Large logs are stored via Git
  LFS (see [Storing raw-CAN logs](#storing-raw-can-logs-whats-committed-vs-fetched)).
- **`signals/<bus>.yaml`** — broadcast signal maps, one file per bus (schema
  `canlib/schema/signals_schema.yaml`).

Both are optional and absent until you import broadcast data; `canair validate
can` and `canair validate signals` check them and quietly skip when they don't
exist. See `plans/2026-07-24-raw-can-analysis.md` for the design.
