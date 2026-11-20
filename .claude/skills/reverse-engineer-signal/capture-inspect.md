# Steps 4–5: Capture and inspect

Scope: recording real payloads across vehicle states and viewing the bytes. Reasoning for the
next step is in `.claude/skills/reverse-engineer-signal/signal-reasoning.md`.

### 4. Capture — record real payloads across states

```bash
canair read MCU:2102 --save --label "MCU 2102 driving" \
    --state "READY, DRIVING" --notes "hard launches + regen"  # bind PID with a colon
canair monitor MCU:2102 --schedule fixed --interval 1 --keep-all --save  # even time-series
```

Capture the SAME PID in DIFFERENT states (park vs drive, cold vs warm, charging vs
ready) — contrast separates signal bytes from constants. For step 6, **co-poll the
target together with an ECU carrying a known reference** (speed on ESC, RPM on MCU) in
one run so `hunt`/`correlate`/`align` can time-align them; save a recurring co-poll set
as a group and recall it with `@` (`canair monitor @driving CLU:220B`, managed by
`canair groups`).

**Never hand-edit `captures/`, and never read the raw capture JSON** — always go
through `canair captures`/`decode` (step 5), or you get undecoded payloads with no
byte-diffing, decoding or scoping. Saves are journaled and reconciled on exit, so a
killed session is recoverable with `captures uds --recover`. In `monitor`, `state` is
auto-suggested from decoded values; `s` edits the session's metadata, `n` starts a
fresh labelled segment, `● REC` blinks while recording. After saving, run
`captures uds --summary`.

**No device on hand? Steps 5–9 still work** — they run against the *existing* corpus;
a live car is needed only for *new* data. A reading pasted from a forum/issue is
onboarded device-free with `canair import uds`, filed through the same machinery as a
live `--save` (immediately queryable by `decode`/`captures`/`coverage`) — the
sanctioned alternative to hand-writing `captures/`:

```bash
canair import uds MCU:2102=6102... --label "forum: cold-soak" --state SLEEP \
    --notes "posted by <user>, 2017 Ioniq"   # ECU:PID=payload (SID-first, PCI stripped)
```

> **Keep modes change what "a capture" *means*.** `monitor --save` now records every poll
> (**`--keep-all`**, folded losslessly on disk), but much of the history was recorded
> **`--keep-changes`** (run-length): a payload stored only when it differs from the
> previous one for that PID. There **a stored-row count measures VOLATILITY, not
> sampling** — an ECU with 30 rows over 3 h was polled every cycle and merely didn't
> *change*, while another in the same run with one noisy ADC byte stores thousands.
> Never read a low count as "barely polled" or compare two ECUs' counts as sample
> sizes (that misreading produced a wrong conclusion about IGPM). Read the mode and span
> from `captures uds --sessions`.
>
> A run-length value stays valid until the next stored row, and the joins know it:
> `align`/`correlate`/`hunt`/`investigate`/`decode` **forward-fill** `keep:changes` rows
> (`--fill auto` default, `hold`/`none` to force/disable, `--max-hold` caps it), never
> across a session boundary, and always report what they carried. Choose deliberately:
> the default **`--keep-all`** for true timing/rate (dRPM, `--transform delta`, dwell);
> **`--keep-changes`** only when you want transition rows alone. Distrust timing on legacy
> **`keep:unique`** sessions — global dedup discarded return-to-previous transitions, so
> the run structure is unrecoverable and fill cannot reconstruct it.

### 5. Inspect — see the bytes

```bash
canair captures uds --sessions          # START HERE: TOC (date/span/state/label/notes/ECUs)
canair captures uds --summary           # captures per ECU / per date / totals
canair captures uds MCU 2102            # list + decoded (latest --limit; --limit 0 for all)
canair captures uds MCU:2102 --diff     # unique payloads, byte-diff (+ --all, --rulers)
canair captures uds MCU:2102 --diff --rulers --notation isotp   # re-label the byte ruler
canair captures uds MCU:2102 --step     # interactive stepper (e=note, d=delete, ?=keys)
canair captures uds "HVAC:220100,2201A2" --step   # several PIDs, time-joined in one frame
canair bix -1 --annotate 6101FFFF...    # map each byte → Bnn / ISO-TP / Torque / role
```

Full flag set: `canair captures uds --help`. The **QUERY mini-language** is shared with
`decode`: `MCU 2102`, `MCU:2102,2103`, `MCU` (all its PIDs), `"VCU:2101 BMS:2101"`
(cross-ECU — quote the space), `BCM:22` (prefix match) —
`docs/concepts/query-mini-language.md`.

Start with `--sessions` to see what data exists and pick a drive/state. Byte-diff
highlights which bytes moved between states — your candidate signal bytes. `captures`
and `decode` share the scoping flags (`--since`/`--until`/`--date`, `--state SUBSTR`/
`--label SUBSTR`, `--first`/`--last N`, `--last-session`), so isolate one drive
(`--state DRIVING`) before diffing. `--step` with a multi-PID QUERY stacks those PIDs
time-joined in one frame — how you cross-compare signals at the same instant.
`canair bix --annotate` gives each byte's WiCAN index and flags the PCI bytes you must
not read across (see Reference); `--ecu ECU --pid PID` overlays which parameter (and
bit) maps each byte and flags `unmapped` ones — the fastest way to catch a wrong offset.

!!! note "Scripting over captures — use `load_all_captures()`, mind the RX address"
    For a bulk edit/delete keyed on a predicate the QUERY can't express, go through
    `canair.captures.store.load_all_captures()`: flat entries with `ecu`
    already **resolved to the short name** (`OBC`), the raw `ecu_addr` (`0x7ED`), and
    `_session_idx`/`_capture_idx` locators for `canair.captures.session_edit.delete_capture` (delete
    in reverse `(file, session, capture)` order). **Do not re-derive the list with a raw
    `ecu == "OBC"` filter** — the stored `rx` field is the CAN **response address**, not
    the short name, so that scan silently matches nothing. Prefer
    `captures uds --delete` when the QUERY can express it.


