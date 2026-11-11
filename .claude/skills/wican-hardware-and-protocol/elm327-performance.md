# Measured ELM327 performance, addressing and adapter housekeeping

Citations here are relative to the basis table in [`SKILL.md`](SKILL.md). Re-check that
basis before you trust a line number.

## The ELM327 ceiling

All measured 2026-08-09 over LAN (`10.0.2.86`), `canair repl --transport wican-ws --timings` with
commands piped on stdin (**`repl` is scriptable this way** — undocumented but the only practical
harness). Distinct command strings become distinct `--timings` rows, so A/B variants must differ
textually or run in separate sessions.

**The dominant cost is the adapter waiting to be sure no more frames are coming**, not the link and
not the ECU. `0x7A0` `22C00B` (3 frames), interleaved, n=8:

| request | mean | max |
|---|---|---|
| `22C00B` (what canair sends today) | 205.8 ms | 653.7 ms |
| `22C00B3` (ISO-TP frame-count digit) | **52.7 ms** | **60.0 ms** |

**3.9×**, and the variance collapses. Reproduced 3.3× on `0x770` `22BC01`. `STPX H:7A0, D:22C00B,
R:3` measured 65.9 ms vs `22C00B3`'s 68.0 ms — **statistically identical**, so the win is the
early-return mechanism, not `STPX`.

**The count digit is dangerous if wrong.** With the true count 3: `22C00B2` returned a **truncated**
2-frame payload; `22C00B1` returned **a leftover frame from the previous request**
(`3:570100AAAAAAAA`) — i.e. an undercount desynchronises the pipe and corrupts the *next* read,
exactly what `plans/2026-08-08-elm327-pipe-desync-recovery.md` exists to recover from. Overcounting
(`5`, `9`) is safe but forfeits most of the gain (~150 ms), so "always send 9" is not a shortcut.
Any implementation needs a learned, validated count, resync on mismatch, and a permanent opt-out for
variable-length responses. **Untested: behaviour above 9 frames** — `0x7EA:21F2` is 13.

Timing knobs, measured (n=6–8, `0x7A0` `22C00B`):

| setting | mean | max | verdict |
|---|---|---|---|
| `ATAT0` | 685.5 ms | 753.4 ms | worst; confirms `ATST96` ≈ 614 ms |
| `ATAT1` (unset default) | 217.3 ms | 681.9 ms | **already optimal** |
| `ATAT2` | 394.2 ms | 686.4 ms | *worse*, erratic |
| `ATST0A` (40 ms) | 120.1 ms | 160.3 ms | returned `NO DATA` in one run — marginal |
| `ATST19` (100 ms) | 134.2 ms | 174.7 ms | cautious tail cap |
| `ATST96` (600 ms, current) | 199.7 ms | 686.1 ms | current |

`STPX` needs **`ATCRA`**, not `ATSH`: it does *not* inherit `ATSH`'s receive filter. Isolated over
five probes — bare `STPX` → `NO DATA`, after `ATFCSH` → `NO DATA`, after `ATSH` → `NO DATA`, after
`ATCRA7A8` → works; `STCRA` is not a command (`?`); a 4-digit header (`H:07A0`) is rejected. It
therefore saves **one** command per ECU switch, not two. Multi-frame reassembly worked with no
`ATFCSH`, so the STN answers flow control from the received first frame's address.

**canair sets the receive filter and flow control itself, per ECU**
(`src/canair/transport/elm327/addressing.py`, applied by `Elm327Terminal.set_header`). The datasheet
facts it rests on: `ATFCSH` is honoured **only in flow-control mode 1** (`ATFCSM1`), and the adapter
rejects `ATFCSM1` with `?` until both `ATFCSH` and `ATFCSD` are set. In the default mode 0 the
adapter derives the flow-control id from the First Frame by the ISO convention, so the `ATFCSH`
canair sends on every switch is ignored there. A *conventional* ECU (11-bit `tx + 0x08`, or the
fixed-29-bit byte swap) keeps the filter the profile `init` left (normally auto) and whatever
flow-control mode is in force, so an all-conventional session sends exactly what it did before. Any
other ECU (a `+0x20` Leaf, the `+0x80` XPeng, a functional-request Renault) gets `ATCRA<rx>` plus
mode 1 with `ATFCSH<fc_id or tx>`/`ATFCSD`, because mode 0 would derive the wrong flow-control id
(`0x7BB` → `0x7B3`, not `0x79B`). Mode 1 then stays on for the session. Any `?` reply is logged
to `canair logs` (stderr with `--verbose`). A rejected command is normally not re-sent until a
reset, with two exceptions: a rejected `ATSH` (or an `ATFCSH` while mode 1 is in force) fails the
request with `AdapterRefusedError`, because the adapter keeps the *previous* header and the reply
of the previously addressed ECU would pass the echo check; and `ATFCSM1` rejected before its
prerequisites (an upstream-style init) is retried. A command whose reply never came, a resync, a
start-up banner in a reply slot (a late connect banner, or the adapter resetting itself) and an
idle gap of `IDLE_FORGET_SECONDS` (a WiCAN's own AutoPID resumes after 10 s and re-points
everything) all make the cached addressing unknown, so the next `set_header` restates it.
The `ATFCSD` block size is always 0 (stmin from `isotp:`): the datasheet has mode 1 send the user's
flow control only in answer to a First Frame. Plan: `plans/2026-10-20-elm327-ecu-addressing.md`.

> **Housekeeping rule, learned the hard way.** `ATCRA` and `ATAT` are **not in any profile init
> string** (the Ioniq's is `ATSP6;ATS0;ATAL;ATST96;`, in
> `profiles/hyundai-ioniq-ae-ev/profile.yaml`), so they **persist across sessions** and silently
> corrupt later recordings — a stale `ATCRA` pins the receive filter to one ECU. canair's own sessions start with `ATZ` (`init_elm`) and pin the filter
> only for non-conventional ECUs, so this bites manual experiments (`canair repl`, other tools).
> Reset with `ATZ` when done, and remember `ATZ` also clears `ATSP6`/`ATS0`, so the next request
> may `NO DATA` during protocol re-detection.
>
> **Verify a reset by interleaving two ECUs on their own rx addresses.** A single-ECU check is
> worthless: `0x770` replies on `0x778`, which is exactly the filter you may have left set.

> **Never let a connect write the chip's EEPROM.** `ATSP h` saves the protocol to EEPROM every
> time it is sent, whatever `ATM` says, and with the memory function on (`ATM1`, the STN2120's
> state after every reset) even an `ATTP h` the next request validates is stored. The EEPROM is
> rated for about 10,000 writes, and the init runs on every connect, so `init_elm` sends `ATM0`
> after each reset and every `ATSP` as `ATTP` (`src/canair/transport/elm327/eeprom.py`). 4.52p
> passes both to the chip unchanged (it only swaps `ATZ` for `ATWS`); v6 rewrites them itself, and only
> from `beta.7` on also switches the memory function off. In `canair repl`, prefer `ATTP`.

Absolute numbers are LAN-local; the config default `vpn` device measured ~320 ms for the same work.
**Ratios transfer, absolutes do not** — and a fix that removes a fixed wait helps *more* on a slow
link.
