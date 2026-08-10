# `wican-ws` throughput ceiling: why 30 PIDs take 8.9 s, and what can actually be reclaimed

Status: **ITEM A IMPLEMENTED** (2026-08-09). Started as a firmware-level audit of the `wican-ws`
request path, recording the fixes it makes available so a later change does not have to re-derive
any of it; item A (the expected-response-count digit, ~3.3–3.9× per read) has since landed. Items
B–E remain unimplemented, with B and C deliberately rejected. `wican-fw/` was moved onto the correct
branch during the audit (see "Correction" below).

**Items A–C were measured on the live device** — see "Verified on the live device". That testing
re-ranked the items and retracted item C; the pre-test reasoning is kept, marked, so the corrections
are auditable.

Prompted by three user questions in one session: whether capture dedup keys on raw payloads or
decoded signals, why recorded PIDs show 5–20 s gaps when RTT was 60 ms, and what the theoretical
maximum throughput of `wican-ws` is. This plan covers the third and the firmware audit it
triggered. The first two were answered in-session and are not written up: dedup keys on the raw
payload hex only (`canlib/capture_journal.py:281-290`), and the gaps were the 5.0 s `--interval`
default on the charging session and a genuine 8.9 s cycle time on the drive session.

## Correction: the first audit read the wrong firmware

The initial analysis was performed against `wican-fw/` while it sat on a personal fork branch
(`ioniq-2017-profile-update`, based on `origin/main`). **`origin/main` is the classic-WiCAN
firmware, and its ELM327 is a software emulation.** The Pro firmware is a separate branch,
`origin/wican-pro`, whose `wican-fw/main/elm327.c` is 3539 lines against main's 1196 — 2408
insertions of difference.

This matters because `wican-ws` is Pro-only, so every conclusion drawn from `origin/main` was
about a code path this project never executes. The checkout is now on `origin/wican-pro` at tag
`v4.51p_beta-01` (commit `1067262` "idf v6.0.2"). The fork branch with the two Ioniq
vehicle-profile commits (`164f617`, `3234043`) is untouched.

Conclusions that survived the branch switch, conclusions that were invalidated, and one that was
retracted are all marked below. **Anyone extending this audit must confirm which branch they are
reading.**

## Verified on the live device (2026-08-09, same session)

Everything below this heading is **measured**, not inferred, via `canair repl --wican home
--transport wican-ws --timings` with commands piped on stdin (the REPL is scriptable that way —
useful, and not documented). Two ECUs answered with the car parked and asleep: IGPM `0x770` and
`0x7A0` (`BCM`). The BMS `0x7E4` and engine `0x7E0` did not, so no wake ritual was attempted.

**The device runs fw 4.50 (`v4.50p`), and `wican-fw/main/elm327.c` +
`wican-fw/components/ws_server/ws_router.c` are byte-identical between `v4.50p` and the
`v4.51p_beta-01` checkout** (`git diff --stat` empty).
Every line reference in this document therefore describes the firmware actually running.

Exact basis for every citation below: `wican-fw` branch `wican-pro`, commit
`10672628cc662665e8ad0d993e6a71d7f1d8813f` (`1067262`, "idf v6.0.2"), tag `v4.51p_beta-01`; device
firmware `v4.50p`; co-processor `MIC3624 V2.3` carrying `STN2120 v5.8.1`; schematic
`wican-fw/sch/wican_obd_pro_sch_v151.pdf` rev V1.51 (sheet 3 of 3 only). Re-check these before
trusting a line number.

### The chip is an STN2120, not an "OBDLink MX"

| command | reply |
|---|---|
| `STI` | `STN2120 v5.8.1` |
| `STDI` | `MIC3624 V2.3` |
| `ATI` | `ELM327 v2.3` |
| `AT@1` | `OBDII to RS232 Interpreter` |
| `AT@2` | `?` |

This settles open question 1: the silicon is an **STN2120 running firmware v5.8.1**, the top-end
ScanTool part, with the full ST command set. `"OBDLink MX"` at `wican-fw/main/elm327.c:71` is the
*software emulation's* `ATI` string and is never sent on this path.

**Defect found:** `canlib/transport/elm327_terminal.py:88-92` claims "the WiCAN answers ATI with
`OBDLink MX`". On `wican-ws` it answers `ELM327 v2.3`. Any adapter-identification logic keyed on
that comment's assumption is wrong. (`AT@2` returning `?` is the reference for what an unsupported
command looks like — useful for capability probing.)

### Measured timings, `0x7A0` `22C00B` (17-byte, 3-frame response)

Interleaved A/B within one session to cancel warm-up and drift:

| variant | n | mean ms | max ms | vs baseline |
|---|---|---|---|---|
| `22C00B` (what canair sends today) | 8 | 205.8 | 653.7 | — |
| `22C00B3` (response-count digit) | 8 | **52.7** | **60.0** | **3.9× faster** |

Reproduced on a second ECU, `0x770` `22BC01` (9-byte, 2-frame): 255.4 ms → 77.8 ms, **3.3×**. And
in a 3-way interleave, `STPX H:7A0, D:22C00B, R:3` came in at 65.9 ms against `22C00B3`'s 68.0 ms —
**statistically identical**.

Two things follow. The count digit brings a request to roughly the link RTT, i.e. the floor. And
**the entire gain comes from the early-return mechanism, not from `STPX`** — which reverses the
A/B priority ordering below.

Note also the variance collapse: max drops from 653.7 ms to 60.0 ms. Today's cost is not a slow
ECU, it is the adapter *waiting to be sure no more frames are coming*.

### `ATAT` is already optimal — item C is confirmed but worthless

Separate sessions, `0x7A0` `22C00B`, n=6:

| setting | mean ms | max ms |
|---|---|---|
| `ATAT0` (fixed, honours `ATST`) | 685.5 | 753.4 |
| `ATAT1` (adaptive — the unset default) | 217.3 | 681.9 |
| `ATAT2` (aggressive adaptive) | 394.2 | 686.4 |

`ATAT0`'s 685 ms **independently confirms the `ATST96` ≈ 614 ms calculation**. But the default
`ATAT1` is already the best of the three, and `ATAT2` is *worse* and erratic. The item-C hypothesis
("the init never sets `ATAT`, so the default applies") is true and the remedy is a dead end.

### `ATST` lowering: real but modest and risky

`0x7A0` `22C00B`, n=8 per setting, `ATST96` is the current Ioniq init value:

| `ATST` | wait | mean ms | max ms |
|---|---|---|---|
| `0A` | 40 ms | 120.1 | 160.3 |
| `14` | 80 ms | 131.4 | 153.9 |
| `19` | 100 ms | 134.2 | 174.7 |
| `32` | 200 ms | 143.9 | 265.9 |
| `96` | 600 ms | 199.7 | 686.1 |

`ATST` caps the adaptive ceiling, so it mostly cuts the tail (max 686→~155 ms). But **`ATST0A`
returned `NO DATA` in an earlier run and not in this one** — it sits right at the edge, and the
floor is ECU- and link-dependent. Worth at most a cautious `ATST19`, and strictly inferior to the
count digit. Do not tune this per-profile by guesswork.

### `STPX` needs `ATCRA`, which is why it first appeared broken

`STPX` initially returned `NO DATA` where `ATSH` + request worked. Isolating it:

| sequence | result |
|---|---|
| `STPX H:7A0, D:22C00B, R:3` in a fresh session | `NO DATA` |
| `ATFCSH7A0` first | `NO DATA` — flow control is not the missing piece |
| `ATSH7A0` first | `NO DATA` — `ATSH` is not the missing piece either |
| `STCRA7A8` first | `?` — not a valid command on this firmware |
| **`ATCRA7A8` first** | **works** |
| `STPX H:07A0, …` (4-digit header) | `?` — 11-bit headers must be 3 digits |

So **`STPX` does not inherit the receive filter that `ATSH` implicitly sets; `ATCRA` must be set
explicitly.** This halves rather than eliminates the per-ECU-switch config cost (`ATCRA` alone vs
`ATSH` + `ATFCSH`), and combined with the identical timing above it means `STPX` is not worth the
complexity. Multi-frame reassembly worked without any `ATFCSH` in that session, so the STN appears
to answer flow control from the received first frame's address on its own.

### The count digit's hazard: an undercount desynchronises the pipe

This is the reason item B cannot be implemented naively. Actual frame count for `0x7A0` `22C00B`
is 3:

| request | result | ms |
|---|---|---|
| `22C00B2` (under by one) | **truncated** — only frames 0 and 1 | 50.3 |
| `22C00B1` (under by two) | returned **`3:570100AAAAAAAA`** — a *leftover frame from the previous request* | 14.3 |
| `22C00B5` (over) | complete and correct | 154.4 |
| `22C00B9` (over) | complete and correct | 150.6 |

An undercount leaves unread frames in the buffer, and the next request returns them instead of its
own response — precisely the failure mode
`plans/2026-08-08-elm327-pipe-desync-recovery.md` exists to recover from. **The count digit can
cause the desync that plan hardens against.** An overcount is safe but forfeits most of the gain
(150 ms vs 53 ms), so "always send 9" is not a shortcut.

Implementation therefore requires an *exact* per-PID frame count. The viable source is learning it
from the first observed response and reusing it — monitor polls the same PIDs every cycle, and
`canlib/modes/multi_batch.py:141 BatchState.learn()` is the existing precedent for a first-cycle
learn. Two existing guards make this defensible rather than reckless: the ISO-TP first-frame length
check already rejects a truncated capture at write time, and `Elm327Terminal._resync()` already
exists to clear a dirty pipe. A length mismatch must trigger resync **and** invalidate the learned
count. A PID with a variable-length response must fall back to no digit permanently.

### Measurement caveat

These numbers were taken over LAN to `home` (`10.0.2.86`). The configured default device is `vpn`
(`192.168.3.2`), which was unreachable, and **the recorded session that prompted this audit ran
over that slower link** — hence its ~320 ms/request against the ~206 ms baseline measured here.
The ratios transfer; the absolute values do not. The count digit's win should be *larger* on the
slower link, since it removes a fixed adapter wait rather than a per-byte cost.

## The headline numbers

Measured from `profiles/ioniq-2017/captures/2026-08-09.json`, session 1 (13:51:37–14:08:08,
9 ECUs, 30 distinct `rx:pid`, `keep:changes`, `wican-ws`):

| bound | per request | requests/s | 30-PID cycle |
|---|---|---|---|
| Link RTT only (unachievable floor) | 60 ms | 16.7 | 1.8 s |
| **Observed** | **~320 ms** | **3.1** | **8.9 s** |
| ELM `ATST96` worst case | 614 ms | 1.6 | 18.4 s |
| Firmware outer timeout (`wican-fw/components/ws_server/ws_router.c:244`) | 2000 ms | 0.5 | 60 s |
| `retries=1` on a silent PID | up to 6.0 s | — | — |

The CAN bus is not remotely the constraint. Computed from the recorded payload lengths of that
session: 931 payload bytes → 150 receive frames (ISO-TP first frame + `ceil((n-6)/7)` consecutive
frames), plus 30 request frames and 29 flow-control frames. At 500 kbps (`ATSP6` in
`profiles/ioniq-2017/profile.yaml:46`) and ~130 bits/frame with stuffing, all 209 frames cost
**~54 ms of bus time for the entire cycle**. The ELM text back over the WebSocket is ~3150
characters with `ATS0`.

So the cycle spends 8.9 s to move 54 ms of bus traffic — about 0.6% efficiency. The cost is
entirely per-exchange latency multiplied by serialization.

## Architecture: the Pro proxies to a real STN chip

The Pro does not emulate ELM327 for the terminal path. It forwards commands over UART1 to a
physical OBDLink/ScanTool STN-family chip — **confirmed on the device as an STN2120 v5.8.1**, see
above. Evidence in the tree, all on `origin/wican-pro`:

- `wican-fw/main/elm327.c:71` — `const char *identify = "OBDLink MX";`
- ST-prefixed commands throughout: `STSBR`/`STWBR` baud rate (`wican-fw/main/elm327.c:2048`,
  `:2065`), `STSLEEP0` (`:2329`), and in `wican-fw/main/obd.c`: `STSLCS` (`:42`), `STSLVLW`/
  `STSLVLS` (`:293-294`), `STSLU` (`:301`), `STSLUIT` (`:311`).
- Chip lifecycle functions that only make sense against real silicon: `elm327_hardreset_chip`
  (`wican-fw/main/elm327.c:2098`), `elm327_set_baudrate` (`:2008`), `elm327_check_obd_device`
  (`:2971`), `elm327_update_obd` (`:3056`) — over-the-wire firmware update of the OBD chip.

canair already half-knew this: `canlib/transport/elm327_terminal.py:88-92` notes "the WiCAN answers
ATI with `OBDLink MX`, a real ELM327 with `ELM327 v1.5`". **That comment is wrong for this
transport** — the device answers `ELM327 v2.3`, and only `STI` reveals the STN part. See the
adjacent-defects list.

The `wican-ws` path is exactly:

```
canair  --WebSocket-->  ws_router.c:244  --UART1-->  STN chip  --CAN-->  ECU
```

`wican-fw/components/ws_server/ws_router.c:244`:

```c
elm327_run_command(tmp, (uint32_t)n, 2000, NULL, ws_elm327_output_cb, false, 0);
```

Note the hardcoded `timeout = 2000` ms, `stop_after_first_frame = false`, `expected_frame_id = 0`.

There is a second, software-emulation `elm327_process_cmd` still present in the Pro tree at
`wican-fw/main/elm327.c:1025`, carried over from main. **It is not on the `wican-ws` path** — the
terminal goes through `elm327_run_command` at `:2534`. Reading the wrong one of these two is the
same trap as reading the wrong branch.

**The same trap a third time, and it caught this document.** `ws_router.c` also exists twice:
`wican-fw/main/ws_router.c` is **dead code — it is not in the build** (`ws_router` appears nowhere
in `wican-fw/main/CMakeLists.txt`). The compiled copy is
`wican-fw/components/ws_server/ws_router.c`, registered at
`wican-fw/components/ws_server/CMakeLists.txt:4-7`. The two are near-identical in content and
**differ by roughly 40 lines in numbering**, so a citation to the dead copy looks plausible and
lands on the wrong line. Every `ws_router.c` reference in this document was corrected to the
component path. Verify any new one with
`rtk grep -rn ws_router wican-fw/main/CMakeLists.txt` returning nothing.

## Why pipelining is impossible — three independent reasons

The question "can a different ELM327 implementation support pipelining?" is answered no, and the
reasons stack. Replacing the firmware fixes none of them; replacing the *protocol* does.

### 1. Protocol: the `>` prompt is the only framing

Already documented in `canlib/transport/elm327_terminal.py:94-100`: the adapter emits exactly one
prompt per response, and UDS itself carries no transaction id, so a SID+DID echo identifies the
request's *content*, not the request. With N requests outstanding you get N prompt-terminated
blocks correlatable only by arrival order — precisely the assumption that fails when a reply is
lost, which is the failure the whole `_owed_prompts` ledger
(`canlib/transport/elm327_terminal.py:152-163`) exists to survive.

Add the single `ATSH` header register (`canlib/transport/elm327_terminal.py:534-550`, cached per
ECU) and two ECUs cannot even be addressed concurrently.

### 2. Firmware: one mutex around the whole command-and-response cycle

`wican-fw/main/elm327.c:2536` takes `xuart1_semaphore` before writing the command and holds it
until the prompt arrives or the timeout expires. `ELM327_CMD_MUTEX_TIMOUT` is 10000 ms
(`wican-fw/main/elm327.c:1329`).

### 3. Firmware: the UART is flushed before every command

`wican-fw/main/elm327.c:2597-2598`:

```c
uart_flush_input(UART_NUM_1);
xQueueReset(uart1_queue);
```

A second request does not merely fail to overlap the first — it **discards the first request's
response bytes**. Pipelining here is data-destructive, not just unhelpful.

*(This conclusion survived the branch switch. On `origin/main` the equivalent was `can_flush_rx()`
at the CAN layer; on `wican-pro` it is `uart_flush_input` at the UART layer. Same consequence,
different layer.)*

### There is no fourth reason involving canair

canair's own `_cmd_lock` (`canlib/transport/elm327_terminal.py:165`) serializes commands, but it is
not the binding constraint — removing it would just corrupt data against reasons 2 and 3.

## Reclaimable, in descending value

**Re-ranked after device testing.** The pre-test ordering had `STPX` first and the count digit
second; measurement showed the count digit carries the entire gain and `STPX` adds none. The old A
is now B and demoted to "not worth it". Read the "Verified on the live device" section above before
acting on any item here.

### A. The expected-response-count digit — the whole win, **~3.3–3.9× measured**. IMPLEMENTED

An odd-length ELM327 data request treats its last digit as the number of response frames to expect,
letting the adapter return the instant it has them instead of waiting out its timeout. The
classic-firmware emulation implements this explicitly, with a comment naming the purpose
(`wican-fw/main/elm327.c:770-787`, early exit at `:954-956`), and the STN2120 implements it
natively — **confirmed on the device**: 205.8→52.7 ms on `0x7A0` `22C00B`, 255.4→77.8 ms on `0x770`
`22BC01`, both to roughly the link RTT floor.

Before this change canair sent the bare, always-even-length PID (`"2101"`, `"22BC01"`), so the digit
was never present and no request could exit early. That every canair request is whole-byte is
exactly what makes appending one nibble unambiguous.

**What landed.** `canlib/transport/elm327_frame_count.py::FrameCountCache` holds the policy;
`Elm327Terminal.send_uds` holds the control flow; `transport.expected_responses` (default true) is
the kill switch; `UdsResponse.isotp_frame_count` (`canlib/uds_parse.py`) is the new observation that
makes any of it checkable. The design follows from one asymmetry: **an overcount is merely slow
(150 ms vs 53 ms), an undercount leaves the response's tail queued and hands it back as the *next*
request's answer** — it manufactures the very desync
`plans/2026-08-08-elm327-pipe-desync-recovery.md` exists to recover from. So:

- **Learn only from a plain request whose reply is `ok`.** A digit-bearing reply can only ever
  confirm the count it asked for, so learning from one would let a truncation self-ratify. `ok`
  already means echo validation and the ISO-TP declared-length check passed.
- **Opt out rather than clamp** above `MAX_REQUESTABLE_FRAMES` (9). Clamping is a deliberate
  undercount; the ≥10-frame PIDs simply stay unoptimized.
- **A mismatch resyncs, then retries plain without charging the caller's `retries`.** The resync set
  is `_DIGIT_RESYNC_ON` = `_RESYNC_ON | {CAT_DROP}` — truncation is precisely the queued-tail case,
  and is *not* worth realigning for when no digit was in play. The feature can cost latency; it
  cannot cost a reading.
- **Opt-out is decided by attribution, not by blame.** Only when the plain retry actually answers.
  Otherwise a transiently silent ECU would permanently deoptimize a healthy PID.
- **An NRC counts as held** — a complete, valid answer that just occupies fewer frames than the
  positive response the count was measured from. Treating it as a failure would opt out every PID an
  ECU refuses while a session is closed, i.e. most of them.
- The cache is **per-connection**, so a count never outlives the link it was measured on.

Tests: `tests/test_elm327_frame_count.py` (18, device-free, driving the real engine through the
shared `QueuedChannel` fake — moved to `tests/_fakes.py` in this change so it is not a third copy).
They pin the safety properties rather than the speedup, including that the recovery retry is not
funded from the caller's retry budget.

Frame counts derived from existing captures: `0x7EC:2101` = 9 frames, `0x7EA:21F2` = 13,
`0x778:22BC02` = 1. The classic firmware caps the digit at 9 with a `FIXME`
(`wican-fw/main/elm327.c:783-785`), and the STN2120's own cap is **still untested** — the ≥10-frame
PIDs (`0x7EA:21F2`, `0x7EB:21F2`) need checking, and they are exactly the ones with the most to
gain. Still the top open question; they currently opt out and read at the old speed.

### B. `STPX` — works, but delivers nothing over item A. **Not worth it.**

`STPX` fuses header, data and expected-response count into one command, e.g.
`STPX H:7A0, D:22C00B, R:3`. It is supported (STN2120) and it works. It was ranked first on the
theory that it would save both the trailing silence *and* the `ATSH`+`ATFCSH` pair per ECU switch
(9 ECUs → 18 commands ≈ 1.1 s/cycle at 60 ms RTT). Both halves of that turned out weaker than
assumed:

- **Timing gain: none over item A.** 65.9 ms vs 68.0 ms in a 3-way interleave — the early return is
  the same mechanism, reached two ways.
- **Config-command saving: halved, not eliminated.** `STPX` needs an explicit `ATCRA` per ECU
  (it does not inherit `ATSH`'s receive filter), so it trades `ATSH`+`ATFCSH` for `ATCRA` — one
  command saved per ECU switch, not two.

So `STPX` buys ~0.5 s/cycle at the price of a second addressing model, a capability probe for
non-STN `elm327-tcp` clones, and abandoning the `_cur_header` cache invariant. Item A gets the same
throughput with no new commands. **Revisit only if the per-ECU-switch cost becomes dominant after
item A lands.**

Commands do reach the chip verbatim — `wican-fw/main/elm327.c:2615` writes the buffer unmodified and
`ws_router_handle_terminal_cmd` does not filter.

### C. `ATAT2` — **retracted as a remedy**, and it retracts a second claim

The Ioniq init string is `ATSP6;ATS0;ATAL;ATST96;` (`profiles/ioniq-2017/profile.yaml:46`) and never
sets `ATAT`, so the chip's default applies. `profiles/xpeng-g6/profile.yaml:8` documents an
`ATAT1`-containing init for another car, so the knob is understood in-tree but unused on the Ioniq.

Measurement (table above) killed this: the unset default `ATAT1` at 217 ms is already the best
setting, and `ATAT2` is **worse** (394 ms, erratic). There is nothing to reclaim here.

It also settles a second thing. This item was offered as "the most likely explanation for the
320 ms observation", since `ATST96` = 614 ms yet the median was ~320 ms. That explanation is now
**superseded**: `ATAT0` measured 685 ms and `ATAT1` 217 ms, so the observed value was simply the
default adaptive timing working — plus, per the measurement caveat, a slower VPN link. `ATST`
lowering is the residual knob and it is weak (table above).

### D. `slcan-tcp` — the structural answer, already implemented

`canlib/transport/uds_raw.py:292-307` documents the model: pipelined *across* ECUs, sequential
*within* an ECU, per-request deadlines, non-blocking multiplexed harvest. Cycle time becomes
`max over ECUs (that ECU's request count × per-request latency)` instead of the sum.

For session 1 the deepest per-ECU chain is 7 requests (`0x778`) against 30 total, so roughly a **4×
cycle reduction**, and the `ATSH`/`ATFCSH` round trips disappear entirely because the raw path
addresses every frame explicitly.

No ELM327 firmware change can match this, because the limit is the ELM327 command protocol, not its
implementation. Weigh against `plans/2026-08-08-high-latency-link-hardening.md` item 2 —
`slcan-tcp` streams the whole bus, which is the wrong trade on a metered link.

**It is a bigger switch than "another transport", and this is the strongest argument for it.** On
WiCAN Pro the two transports do not share a route to the vehicle: `wican-ws` talks to the MIC3624
co-processor over UART1, while `slcan-tcp` uses the ESP32-S3's own TWAI controller (GPIO2/GPIO1,
`wican-fw/main/hw_config.h:31-33`), whose single transceiver is wired to the connector's **HS-CAN**
pins. Under `protocol = auto_pid`/`elm327` the firmware **never calls `can_enable()`** — it is
commented out at `wican-fw/main/main.c:902-907` — so the TWAI controller is not merely unused, it is
*off*, and the TWAI→ELM327 frame hand-off is compiled out entirely at
`wican-fw/main/main.c:432-438`. That is why `canair wican mode set` has to change the device mode
rather than just the client transport, and it means the two transports have genuinely different
failure modes, different filtering behaviour and different silicon.

The OBD connector additionally breaks out **MS-CAN** (`MS_CAN_H`/`MS_CAN_L`) and **SW-CAN**
(`SW_CAN`), neither of which the ESP32 firmware drives at all — so switching transport changes which
silicon talks to HS-CAN, but no transport reaches the other two buses. See the
**wican-hardware-and-protocol** skill for the full topology and the evidence.

### E. Multi-DID batching — real but small, and capped lower than configured

`canlib/transport/elm327_terminal.py:53` caps an ELM request at 7 bytes, so `22` + 3×2 is the
ceiling: **3 DIDs**. `profiles/ioniq-2017/profile.yaml:77` sets `multi_did_max: 6`, which is
therefore silently clamped, and `canlib/modes/multi_batch.py:62-73 transport_did_cap()` does not
warn. **If you set 6 expecting 6, that expectation has been wrong since it was written.**

Worse, `canlib/modes/multi_batch.py:213-215` restricts batching to service 22:

```python
def _is_did22(pid_code: str) -> bool:
    """True for a full 6-char service-22 DID request like ``22BC03``."""
    return len(pid_code) == 6 and pid_code[:2] == "22"
```

Every `21xx` PID is structurally unbatchable — BMS `2101`/`2105`, VCU `2101`/`2102`/`21F2`, MCU
`2101`/`2102`/`2103`, AAF `2180`/`2181`: 11 of session 1's 30 PIDs, and the largest responses
(`0x7EA:21F2` is 86 bytes / 13 frames).

Best case for that selector: the 19 service-22 PIDs batch to 8 requests (`0x778`: 7→3,
`0x7BB`: 6→2, `0x7CE`: 3→1, `0x7D9`: 2→1, `0x7DC`: 1→1) plus 11 unbatchable = **19 requests
instead of 30**, ~1.6×.
Only ESC/IGPM/MFC/SCC currently set `multi_did: true`, so most of that is not even enabled. And the
first cycle is always all-single, because a DID becomes batchable only after a prior single read
taught it its length (`canlib/modes/multi_batch.py:141 BatchState.learn()`).

## Invalidated by the branch correction: command batching in one write

The first audit proposed writing several CR-separated commands in one WebSocket message to collapse
N link round trips into 1, on the strength of `elm327_process_cmd` on `origin/main` looping over
every CR-terminated command in its input buffer (`wican-fw/main/elm327.c:1062-1168` on main).

**That does not work on the Pro.** `elm327_run_command` sets `terminator_received = true` on the
**first** prompt (`wican-fw/main/elm327.c:2656-2658`, `:2703-2705`, `:2769-2771`), returns, and
releases the mutex. The remaining replies sit in the UART buffer until the *next* command's
`uart_flush_input` (`:2597`) destroys them. So batching on Pro silently loses all but the first
response.

Two further constraints on the same idea, both real:

- `ws_router_handle_terminal_cmd` copies into `char tmp[256]` with
  `strnlen(cmd, sizeof(tmp) - 2)` (`wican-fw/components/ws_server/ws_router.c:235-244`) — commands
  are **truncated at 254 bytes**, silently.
- It appends a CR only when the last character is not already one (`:239-243`), so a
  multi-command string passes through unmodified and hits the first-prompt return above.

`STPX` (item A) is the correct way to get the same round-trip saving on this hardware.

## Retracted: the unsigned-underflow trailing-timeout bug

The first audit flagged `xwait_time -= (((esp_timer_get_time() - txtime)/1000)/portTICK_PERIOD_MS)`
as subtracting absolute elapsed-since-transmit rather than a delta from an unsigned `TickType_t`,
decaying quadratically and underflowing — and offered it as the explanation for 320 ms.

**That code is in the software emulation on `origin/main` and is not executed on the `wican-ws`
path.** The retraction is unconditional: it explains nothing about the observed timing. See item C
for the replacement hypothesis (adaptive timing).

The bug does appear to be real *on classic hardware over BLE/WiFi*, where the emulation runs. Out of
scope here; worth an upstream issue if anyone uses a classic WiCAN with canair.

## Adjacent defects noticed, not fixed

- **`multi_did_max` is silently clamped.** Item E. `transport_did_cap()` should warn when the
  profile asks for more than the transport allows, the way other resolved-vs-configured mismatches
  do.
- **"Terminal busy" can be parsed as a UDS response.** `ws_router_handle_terminal_cmd` takes
  `s_term_mutex` with timeout 0 and, on contention, replies with the literal text
  `"Terminal busy\n"` and returns `ESP_OK` (`wican-fw/components/ws_server/ws_router.c:204-206`).
canair's
  `_cmd_lock` prevents self-contention, and the firmware pauses AutoPID when the ELM327 terminal is
  entered (`wican-fw/components/ws_server/ws_router.c:180-184`), so this needs a third client to
  trigger. It would
  arrive as an unprompted non-hex line; `classify_response` in `canlib/uds_parse.py` should be
  checked against that exact string.
- **`elapsed_ms` is absent from monitor captures**, recorded only for single per-DID reads on the
  `read` path, so per-PID cost cannot be recovered from an existing recording — the audit had to
  infer it from inter-capture gaps. Recording it on the monitor path would make this class of
  question answerable from history instead of requiring a new drive.
- **`quality.exchanges` does not mean what it looks like** — 687 for a 9621-capture session, 168
  for a 1779-capture session. Whatever `canlib/transport_stats.py::TransportStats` counts, it is
  not the per-PID request count. Not chased; do not use it to reason about poll rate until it is.
- **The `ATI` identification comment is factually wrong for `wican-ws`.**
  `canlib/transport/elm327_terminal.py:88-92` says the WiCAN answers `ATI` with `OBDLink MX`; the
  device answers `ELM327 v2.3`. The `OBDLink MX` string belongs to the classic software emulation
  (`wican-fw/main/elm327.c:71`) and never reaches this path. `STI` is the command that identifies
  the silicon (`STN2120 v5.8.1`) and `STDI` the board (`MIC3624 V2.3`). Anything keyed on that
  comment's assumption should be re-examined; a capability probe should use `STI` with `AT@2`'s `?`
  as the negative reference.

## Verification

Items 1–4 of the original checklist were **executed on 2026-08-09** — results in "Verified on the
live device" above. `STPX` works but is redundant (item B), the count digit gives 3.3–3.9× (item A),
`ATAT` is already optimal (item C), and the device is on `v4.50p` with the audited files
byte-identical to the checkout.

Item A's safety case is covered device-free by `tests/test_elm327_frame_count.py`: an undercounted
reply realigns the pipe, retries plain, still returns the reading, and opts the request out — and the
retry is not funded from the caller's `retries`. A variable-length response needs no special
handling and no hunting for an example, because the generic mismatch path opts it out on first
occurrence.

What remains untested, and needs a car:

1. **The digit cap above 9 frames.** `0x7EA:21F2` (13 frames) and `0x7EB:21F2` are the PIDs with the
   most to gain and the ones the cap would break. The classic emulation caps at 9 with a `FIXME`
   (`wican-fw/main/elm327.c:783-785`); the STN2120's behaviour for a 13-frame response is unknown, so
   `MAX_REQUESTABLE_FRAMES = 9` is a conservative floor and these PIDs currently opt out and read at
   the old speed. Requires the BMS awake, so it needs a drive or a wake ritual.
2. **End-to-end cycle time.** The per-request numbers are from a 2-ECU parked car. Confirm the gain
   survives a real 9-ECU/30-PID drive cycle, where ECU-switch cost and timeouts also contribute, and
   that no PID opts out unexpectedly. The recording's `quality` provenance and `canair logs` are the
   places to look.
3. **`elm327-tcp` against a real clone.** The digit is a documented ELM327 feature rather than an ST
   extension, so it should be portable, but "should" is what this whole audit distrusts. A clone that
   answers `?` degrades to plain automatically (covered by a unit test); one that answers something
   *plausible* would not, and that is the case worth looking for.

## Open questions

Most of the original open questions are now **answered** and kept here for the record:

- ~~Which STN part, and does it support `STPX`?~~ **STN2120 v5.8.1, and yes** — but `STPX` is
  redundant given the count digit (item B).
- ~~Is `ATCRA` worth setting?~~ It is *mandatory* for `STPX` and irrelevant otherwise, since canair
  is not adopting `STPX`. The related resolution stands: on the Pro the chip does the filtering, so
  the classic emulation's `0x7E8`–`0x7EF` restriction (`wican-fw/main/elm327.c:619-652` on main)
  never applies — which explains why captures on `0x7BB`/`0x778`/`0x7CE` exist at all. A narrower
  `ATCRA` might still cut chip-side work on a busy bus; unmeasured.
- ~~Would `STPX` obsolete the `_cur_header` cache?~~ Moot — `STPX` is not being adopted, so the
  `transaction()`-held `set_header` + `send_uds` invariant stays exactly as it is.
- ~~Where should the learned frame count live?~~ **In memory, per connection**
  (`FrameCountCache`, held by the terminal). Persisting into `ecus/` would make the first cycle fast
  too, but a device- and firmware-dependent fact does not belong in a vehicle-definition file, and a
  count is only valid for the link it was measured on.
- ~~Does the digit interact with multi-DID batching (item E)?~~ Not harmfully: the cache is keyed on
  the *request as sent*, so a batched request learns its own count independently of the single-DID
  ones. Both optimisations still touch the same call site, so implementing E means re-reading the
  `send_uds` loop.

Still open:

- **Can the digit exceed 9?** See verification item 1. If the STN2120 accepts hex `A`–`F`, raising
  `MAX_REQUESTABLE_FRAMES` to 15 is a one-constant change that would optimize the largest responses
  in the profile — the ones where the saving is worth the most.
- **Should the profile's `response_timeout_ms` come down now?** The digit makes the `ATST` budget a
  tail-latency parameter rather than a per-read cost, so the argument for keeping it at 600 ms is
  stronger, not weaker. Measure before touching it.
