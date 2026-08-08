# High-latency link hardening: request/response correlation and cellular tolerance

Status: **PLANNED** (2026-08-08). Follow-up to
`plans/2026-08-08-elm327-pipe-desync-recovery.md`, which fixed the reported symptom on
`wican-ws` but left the same failure class open on the raw path and left the ELM327
detection resting on a hardcoded latency guess.

## Why

The desync fix landed in `485a37f`. Reviewing it against the link that produced the bug
(WiCAN Pro → iPhone personal hotspot → WireGuard → server) exposed a shared root cause
much broader than the one bug:

> Every timing constant in canair was chosen on a LAN, where round-trip time is ~1 ms and
> therefore negligible against the vehicle's own response time. On a cellular link RTT
> becomes the *dominant* term, and each of those constants turns into a latent bug.

The user's framing was right: the link is the trigger, not the root cause. Recovery must be
automatic, and correlation of a response to its request must not depend on the link being
fast.

Two decisions scope this plan:

- **`slcan-tcp` must be latency-proof too**, not just `wican-ws`. It is the *default*
  transport, and it currently fails harder over cellular than the transport that was
  reported broken.
- **`transport.reconnect_max_wait` default rises from 6.0 s to 60.0 s** for sessions
  without `--wait`. A radio re-attach after a tunnel takes 5–15 s, so the old default threw
  away recoverable sessions.

## What the audit found

### 1. The raw path has the identical desync bug, undefended

`RawUdsClient.poll` gives each request its own deadline, and on expiry
(`canlib/transport/uds_raw.py:239-242`) it does:

```python
elif now >= info["deadline"]:
    _finish(ecu, req, TimeoutError("no response"))
    del pending[ecu]
```

It **never flushes the ECU's ISO-TP stack**. The late reply stays queued, so the next round
finds `st.available()` true at `canlib/transport/uds_raw.py:217` and serves that stale
payload to the *next* request. That is exactly the one-slot offset from the ELM327 bug,
reached by a different route — and `poll` performs no echo validation at all, so it is
silent. This is the most serious finding: on the raw path the offset produces **wrong
decoded values with no warning**, where the ELM path at least logged `Echo mismatch`.

### 2. `slcan-tcp` streams the entire CAN bus over the mobile link

`SlcanTcpBus.__init__` opens the channel with `C` → bitrate → `O`
(`canlib/transport/slcan_tcp.py:133-135`) and never sets an SLCAN acceptance filter, so the
device relays every frame on the segment.

An 8-byte standard frame is 22 ASCII bytes (`t` + 3 id + 1 dlc + 16 hex + `\r`). A
500 kbit/s Hyundai P-CAN at moderate load is ~2500 frames/s ⇒ **~55 kB/s ≈ 440 kbit/s
sustained, ~200 MB/hour**, before WireGuard overhead, to answer perhaps 10 requests/s. On a
hotspot uplink that either saturates or induces bufferbloat — and once queue delay grows,
every timeout below fires. It is also a real data-cost problem.

The WiCAN implements **hardware** acceptance filtering, so this is fixable on the device
side, before the link: `M` → `can_set_filter()` (`wican-fw/main/slcan.c:479`) and `m` →
`can_set_mask()` (`wican-fw/main/slcan.c:496`), each taking 8 ASCII hex digits, parsed at
`wican-fw/main/slcan.c:399-410`.

### 3. `slcan-tcp` puts ISO-TP flow control in the latency path

**This is prior art, not a discovery.** `plans/2026-07-22-cellular-transport-timeouts.md`
diagnosed exactly this mechanism from a 2026-07-22 drive (VCU/MCU timing out over the same
hotspot link while ESC/EPS did not) and closed as **"ANALYSIS ONLY — no changes made, by
decision"**, with the recommendation "use `wican-ws` over cellular". Its conclusion was
blunt: *"Only the two device-side-ISO-TP modes fix the mechanism"*, and it explicitly said to
keep `slcan-tcp` for the home LAN and for `canair sniff`.

**The instruction to make `slcan-tcp` latency-proof reverses that decision.** That is a
legitimate call — but the earlier reasoning still holds physically, so this plan pursues
*tolerance*, not parity, and says so in Deliberately not done. Anyone reading the two plans
together should see the reversal recorded rather than inferred.

On the raw path canair is the ISO-TP *receiver*, so it must send a Flow Control frame after
the ECU's First Frame. That FC traverses server → WireGuard → hotspot → dongle → bus while
the ECU's N_Bs timer (~1000 ms, in ECU firmware, not tunable) runs. Miss it and the ECU
aborts the transfer: every multi-frame response fails while single-frame ones work.

- `blocksize: 0` (`canlib/transport/isotp_params.py:26`) is the saving grace — one FC per
  message, so only one RTT is exposed. A profile setting `blocksize > 0` pays one RTT *per
  block* and will be unusable remotely. `profiles/xpeng-g6/profile.yaml` already sets
  `ATFCSM1;ATFCSD300000;` in its init string, so non-default flow control is not
  hypothetical.
- `rx_consecutive_frame_timeout: 1000` ms (`canlib/transport/isotp_params.py:29`) is our
  side: consecutive frames leave the bus back-to-back but are relayed over TCP, so a single
  retransmit stall over 1 s aborts reassembly and lands in the `drop` tally.

`canlib/transport/slcan_tcp.py:121-129` already documents a member of this bug family
(Nagle vs. the ECU's FC window, with the counter-intuitive symptom "fast wired times out,
WiFi doesn't"). This is the same failure with the sign flipped.

### 4. The raw path's per-request default is 1.0 s

`RawUdsClient.__init__` takes `timeout: float = 1.0`
(`canlib/transport/uds_raw.py:68`). That budget must cover the full round trip *plus* the
ECU. At 300 ms RTT it is marginal; a multi-frame read needs ≥2 RTTs and is hopeless. Over
cellular, `slcan-tcp` reports near-universal `no_data` that looks exactly like a sleeping
car.

The ELM path is more generous — `_ws_timeout = --timeout or 3.0`
(`canlib/commands/_live/connect.py:83`) — but still only ~2.4 s of link slack after the
adapter's own 614 ms `ATST96` wait. A cell handover mid-drive routinely exceeds that.

### 5. The 2 s liveness probe rejects a live device

`_DEFAULT_CONNECT_TIMEOUT = 2.0` (`canlib/config.py:45`) bounds `_tcp_open` in
`canlib/transport/fallback.py:50`. On cellular the first packet after radio idle pays RRC
connection setup — 0.5–2 s *before* the SYN leaves. The reachable device is judged dead and
canair silently fails over to another device, or reports nothing reachable. This bites
hardest at `monitor --wait` startup, precisely when the radio has been idle.

### 6. Keepalive contention makes cycle time superlinear in RTT

The ELM327 is strictly one command at a time, and `transaction()` (correctly) serialises
reads against keepalives. So a cycle costs `(N_ecus × M_pids) × (RTT + ATST)` — at 2 s RTT
and 12 PIDs, ~31 s. Meanwhile `keepalive_stale(threshold=1.5)`
(`canlib/session_manager.py:216`) fires for every ECU that went quiet, and each keepalive is
another serialised round trip. Past some latency, keepalives consume the whole cycle and
sessions still expire (S3 ≈ 5 s): a livelock where you cannot poll because you are busy
keeping alive.

The raw path is architecturally better here — `poll` pipelines across ECUs
(`canlib/transport/uds_raw.py:146-160`, one outstanding request per ISO-TP stack, all
concurrent), so latency amortises. Hence a genuine tradeoff: **`slcan-tcp` hides latency
but breaks on flow control and bandwidth; `wican-ws` is robust per-request but serialises
and so scales badly with RTT.** Multi-DID batching is the only latency lever on the ELM path
— but see finding 7, which is why it is currently not working there at all.

### 7. Multi-DID batching, the ELM path's only latency lever, is silently disabled

The WiCAN's ELM327 implementation **rejects any command longer than 7 data bytes**, replying
`?` (`wican-fw/main/elm327.c:805-814`):

```c
cmd_data_length = strlen(cmd)/2;
if(cmd_data_length > 7)
{
    // commands can't be longer than 7 bytes unless flow control is used
    strcat(rsp, "?\r>");
```

`MULTI_DID_MAX_DEFAULT = 3` (`canlib/modes/multi_batch.py:57`) respects that exactly — `22` +
3 two-byte DIDs = 7 data bytes — and the comment at `canlib/modes/multi_batch.py:52-55` says
so. But `profiles/ioniq-2017/profile.yaml:71` sets **`multi_did_max: 6`**, i.e. `22` + 6 DIDs
= **13 bytes**. That is fine on `slcan-tcp`, where client-side ISO-TP does multi-frame
transmit — and impossible on `wican-ws`, where the firmware refuses it. The comment at
`canlib/modes/multi_batch.py:52-55` anticipated only "adds request-side flow-control", not
outright rejection, and `resolve_multi_did_max`
(`canlib/modes/multi_batch.py:60-75`) is transport-unaware.

The failure is silent and self-inflicting: a rejected batch makes `_read_batch` add the ECU
to `batch_state.disabled` **permanently** for the session and fall back to single reads. So
on the transport that is *recommended* for cellular, the Ioniq profile very likely performs
no batching at all — losing the 3× round-trip reduction precisely where round trips are the
bottleneck. This needs confirming against a device (the `?` reply classifies as a decode
error, so it should be visible in `canair logs`), but the arithmetic is not in doubt.

Related firmware constraint from `plans/2026-07-22-cellular-transport-timeouts.md`: ELM327
multi-frame **transmit** is not implemented at all, so this 7-byte ceiling caps any future
long request, not just batches.

### 8. Weaknesses in the shipped fix

- **`_LINK_LATENCY_MARGIN = 0.5` is hardcoded** (`canlib/transport/elm327_terminal.py:43`).
  The resync quiet window is `min(self.timeout, ATST_budget + 0.5)` ≈ 1.11 s at `ATST96`. If
  RTT exceeds that, the drain finds nothing, the `ATI` probe then *consumes the orphan*
  instead of its own reply, and resync reports success with the pipe still offset. Each
  resync sends one command and consumes one, so queue depth stays at 1 indefinitely. It does
  not hang — `_check_liveness` forces a reconnect — but recovery degrades to the sledgehammer
  instead of the surgical repair. Worse, `--timeout` raises only the *cap*, not the margin,
  so the natural knob does not help; only raising `ATST` widens the window, which is the
  wrong-shaped knob (it is the adapter's CAN-side wait, per
  `canlib/timeouts.py:13-17`).
- **Resync fires only on `CAT_STALE`**, so a garbage-in-slot offset stays unrecoverable. A
  connect banner or truncated reply classifies as `decode`/`no_data` via `_ERROR_RULES`
  (`canlib/uds_parse.py:178-182`). Over cellular the banner is genuinely at risk:
  `SETTLE_SECONDS = 0.3` plus a 1.0 s drain (`canlib/transport/channel.py:45-46`) is only
  1.3 s to catch it, so the permanent-offset-from-connect warning at
  `canlib/transport/channel.py:30-46` still stands.
- **Enabling WebSocket pings can end sessions that previously survived.**
  `ping_timeout = ping_interval = 20 s` (`canlib/config.py:54`), so a dead zone longer than
  ~20 s now tears the socket down, and reconnect then gets only
  `_DEFAULT_RECONNECT_MAX_WAIT = 6.0` s (`canlib/config.py:48`) unless `--wait` is passed.
  Net effect on a long drive: without `--wait` a recording may now be *lost* where before it
  silently resumed. This is a regression introduced by the previous change and is the reason
  the default rises to 60 s.

## Is there an identifier that says which request a response belongs to?

This was the question that reshaped the plan. Answer, by layer:

- **CAN** — the arbitration ID identifies the *ECU*, not the request. That is why every
  mismatch in the report was on `0x7E4`: a response cannot cross ECUs, so a desync is always
  confined to one ECU's stream. No per-request field.
- **ISO-TP** — consecutive frames carry a 4-bit sequence number, but it orders frames
  *within* one message and wraps at 16. Nothing identifies a message across messages.
- **UDS (ISO 14229)** — the positive response echoes SID+0x40 then the DID. That is
  `request_echo` (`canlib/uds_parse.py:202-235`) and it produced the reported `Echo mismatch`
  lines. But it identifies the request's **content, not its instance**. The absence of a
  transaction ID is deliberate: UDS assumes an ordered transport with one outstanding request
  per channel, and delegates correlation to that transport.

**The content-vs-instance gap is a real hole.** `expected_echo` cannot detect an offset when
consecutive requests are identical. The reported bug was only visible because the cycle
walked `2102 → 2103 → 2104 → 2105 → 21F2`, so the lag surfaced as a mismatch. A monitor
polling a **single PID**, or an offset of exactly one **full cycle**, is byte-identical and
completely invisible — plausible stale values, recorded, with no warning. Neither
`expected_echo` nor `_resync` closes that.

### The identifier we do have: the `>` prompt

The ELM327 contract is **exactly one `>` per command**, and the WiCAN honours it — every
response path terminates with `\r>` or `\r\r>` (`wican-fw/main/elm327.c:757`, `:762`, `:811`,
`:1003`, `:1007`, `:1108`, `:1144`).

canair never uses it as a count. It tests presence only, at
`canlib/transport/elm327_terminal.py:349` (`if ">" in full:`), and then erases prompts
wholesale at `canlib/transport/elm327_terminal.py:246` (`raw.replace(">", "")`).

Counting prompts turns "which request is this?" from a heuristic into arithmetic: **N
prompts accumulated for 1 command ⇒ N−1 queued stale replies ⇒ discard all but the last.**
It works for identical repeated requests, needs no protocol support, and is a *positive*
recovery — you learn exactly how many replies to drop, so you can drop them and still answer
the current request correctly rather than failing it and retrying.

Caveat, so this is not oversold: a ResponsePending (`7F xx 78`) frame is itself
prompt-terminated (see `canlib/transport/elm327_terminal.py:352-364`), and multi-line
responses produce extra output. The invariant is "one prompt per *adapter response*", not per
*UDS answer*, so the counter must discount pending frames rather than assume 1. That is the
fiddly part of the implementation and where the tests must be thickest.

### The stronger identifier is unavailable on `wican-ws`

A real ELM327 defaults to `ATE1` and prefixes each reply with the literal command text — a
true per-instance tag. canair already tolerates it
(`canlib/transport/elm327_terminal.py:329-341`) and `canlib/uds_parse.py:7-8` documents
stripping "AT echoes". No profile sets `ATE0`/`ATE1` (`profiles/ioniq-2017/profile.yaml:46`
is `ATSP6;ATS0;ATAL;ATST96;`), so echo sits at the adapter default of 1.

But the WiCAN **stores the ATE flag and never reads it**: `elm327_config.echo` is assigned at
`wican-fw/main/elm327.c:122`, `:187`, `:191` and referenced nowhere else, so it answers `OK`
to `ATE1` and never echoes. The command echo is therefore usable on `elm327-tcp` clones and
the ELM327 emulator, but **not on `wican-ws`** — it cannot carry the primary defence.

## Design decisions

- **Prompt accounting is the primary ELM327 defence; the latency margin becomes a fallback.**
  A framing invariant the adapter guarantees beats a timing guess about the network. The
  margin keeps a residual role only for "nothing arrived at all", where there is no framing
  signal to count.
- **Flush-on-timeout is the primary raw-path defence.** The ISO-TP stack queue is the raw
  analogue of the ELM327's byte pipe, and draining it at the moment a request is abandoned is
  both cheaper and more certain than validating afterwards. Echo validation is added as a
  second layer, not the first.
- **Measured RTT, not configured RTT, drives adaptive budgets.** A user cannot be expected to
  know their WireGuard RTT, and it changes while driving. An EWMA over observed round trips
  is already half-built: `RawUdsClient` records per-request timings
  (`canlib/transport/uds_raw.py:233-235`) and the ELM path records `elapsed_ms`.
- **Filters are set on the device, not in the client.** Client-side filtering would not save
  a single byte of mobile data, which is the entire point.
- **`reconnect_max_wait` 6.0 → 60.0 rather than retry-forever-by-default.** Retrying forever
  changes `monitor`'s contract for everyone including scripted/piped use, where hanging
  indefinitely on a dead device is wrong. 60 s covers a tunnel and a radio re-attach while
  still terminating. `--wait` remains the explicit forever option.

## Work

### Phase A — correlate responses to requests

1. **Prompt accounting in `Elm327Terminal._send_command_locked`**
   (`canlib/transport/elm327_terminal.py:296-380`). Count prompts across the accumulation
   loop, discount ResponsePending frames, return the *last* complete block, and tally each
   discarded block as `stale` via `TransportStats`. Stop erasing prompt structure before the
   boundary is used — `canlib/transport/elm327_terminal.py:246` may only strip once the count
   is consumed.
   This also closes an open item carried by `plans/2026-07-22-cellular-transport-timeouts.md`:
   the read loop's **early-break truncation heuristic** (then `canlib/terminal.py:154-172`, now
   `canlib/transport/elm327_terminal.py:325-343`), flagged there as able to truncate a response
   when cellular delays a chunk past 1 s and left as "worth an empirical check on cellular".
   Prompt accounting replaces the guess it encodes — a block is complete when its prompt
   arrives, not when a hex-shape heuristic says it looks like an echo.
2. **Flush the ISO-TP stack on abandonment** in `RawUdsClient.poll`
   (`canlib/transport/uds_raw.py:239-242`) and in `read` on timeout
   (`canlib/transport/uds_raw.py:116-135`): drain `while st.available(): st.recv()` before the
   next round, and tally the discards.
3. **Echo-validate the raw path.** `poll` currently validates nothing; route its responses
   through `request_echo` + the same `CAT_STALE` classification the ELM path uses, so a raw
   desync is loud rather than silent.
4. **Widen `request_echo` coverage** beyond single-identifier `0x21`/`0x22`
   (`canlib/uds_parse.py:202-235`) — at minimum the multi-DID `0x22` batch form, which is the
   one that carries several signals per response and so does the most damage when misfiled.
5. **Demote `_LINK_LATENCY_MARGIN`** (`canlib/transport/elm327_terminal.py:43`) to the
   no-data-at-all fallback, and drive it from measured RTT with a configured floor.
6. **Extend `_resync` to `decode`**, so a connect banner or garbage in the slot recovers.
   Revisit the now-partly-stale warning at `canlib/transport/channel.py:30-46`.
7. **Opportunistic command-echo validation on `elm327-tcp`** — probe at init whether the
   adapter echoes; if it does, use it as an exact per-instance tag. Never assume it.
8. **Make `resolve_multi_did_max` transport-aware** (`canlib/modes/multi_batch.py:60-75`):
   clamp to the 7-data-byte ELM327 command ceiling (⇒ 3 two-byte DIDs) on `wican-ws` /
   `elm327-tcp`, while letting `slcan-tcp` honour a larger profile value. Have
   `canair validate` warn when a profile's `multi_did_max` exceeds the ELM ceiling — the Ioniq
   profile's `multi_did_max: 6` (`profiles/ioniq-2017/profile.yaml:71`) is the live example.
   Also stop a single rejected batch from permanently disabling batching for the whole session,
   or at minimum log that demotion loudly — a silent fallback is what hid this.

### Phase B — make `slcan-tcp` latency-proof

8. **Device-side acceptance filters.** Emit `M`/`m` between the bitrate and `O` in
   `SlcanTcpBus.__init__` (`canlib/transport/slcan_tcp.py:133-135`), computed from the
   profile's ECU response addresses. Verify whether the WiCAN applies
   `can_set_filter`/`can_set_mask` live or only at driver start; if only at start, the
   ordering above is mandatory. `sniff` must opt out and stay unfiltered.
   Note the hardware constraint: the ESP32 TWAI peripheral offers a *single* code+mask pair,
   so a profile with disjoint response addresses gets a superset mask, not an exact set. A
   superset is still a very large reduction versus the whole bus.
9. **Latency-adaptive ISO-TP budgets** — scale `rx_flowcontrol_timeout` and
   `rx_consecutive_frame_timeout` (`canlib/transport/isotp_params.py:28-29`) by measured RTT.
10. **Raise the raw per-request default** from `1.0` s
    (`canlib/transport/uds_raw.py:68`) and make it latency-adaptive.
11. **Warn on `blocksize > 0` with a non-local host** in `canair validate` — one RTT per
    block is a cliff, not a slope.

### Phase C — defaults, escalation, guidance

12. **`_DEFAULT_RECONNECT_MAX_WAIT` 6.0 → 60.0** (`canlib/config.py:48`), with
    `config.example.yaml` and `docs/reference/config.md` updated. This is the agreed fix for
    the ping-induced regression in finding 8.
13. **Raise or adapt `_DEFAULT_CONNECT_TIMEOUT`** (`canlib/config.py:45`) so radio wake-up
    does not read as a dead device.
14. **A single link-latency knob** distinct from `response_timeout_ms`/`ATST` (which is the
    *car's* budget, per `canlib/timeouts.py:13-17`), so one setting covers a slow link.
15. **Warn once when a remote host is paired with `slcan-tcp`**, pointing at `wican-ws`.
16. **`docs/concepts/remote-and-cellular.md`** — the transport tradeoff from finding 6, the
    knobs, batching as the latency lever, and the data-usage arithmetic from finding 2.
    Precedent: `plans/2026-07-22-cellular-transport-timeouts.md`.

## Deliberately not done, and hard limits

- **`slcan-tcp` cannot be made as latency-robust as `wican-ws` for multi-frame reads.** The
  ECU's N_Bs flow-control timer lives in ECU firmware and cannot be extended from our side,
  so at sufficiently high RTT multi-frame responses will abort no matter what we tune.
  Phase B makes the path *tolerant* (fewer bytes, so less bufferbloat; adaptive budgets;
  loud instead of silent failures) — it does not repeal the protocol. Terminating ISO-TP on
  the device is the only real answer, and that is what ELM327 mode already is. The
  documentation must say this plainly rather than imply parity.
- **No client-side request queue depth > 1 per ECU.** ISO-TP permits one outstanding request
  per channel; pipelining across ECUs (already done) is the only legitimate concurrency.
- **Not reusing `response_timeout_ms` as the link budget.** It maps to `ATST`
  (`canlib/commands/_live/connect.py:121-122`), the adapter's CAN-side wait. Conflating the
  car's budget with the network's is precisely the trap that makes finding 8 hard to
  diagnose.
- **No retry-forever default** — see Design decisions.

## Verification

- **Unit**: prompt accounting against a scripted channel with 2 and 3 queued
  prompt-terminated replies, including a ResponsePending interleaved, asserting the *last*
  block is returned and the rest are tallied `stale`. Raw-path flush asserting a late reply
  is discarded rather than served to the next request. Echo validation on `poll`.
- **The regression that matters most**: a **single-PID** monitor with a one-cycle offset —
  the case echo validation cannot see and prompt accounting can. This must fail before the
  change and pass after.
- **Latency simulation**: a channel/fake bus that injects a configurable delay, run at 0 ms,
  300 ms and 2000 ms RTT, asserting no desync survives a cycle at any of them.
- **Batching**: confirm on a device that a 6-DID request is rejected with `?` on `wican-ws`
  and accepted on `slcan-tcp`, and that after the transport-aware clamp the Ioniq profile
  actually batches on `wican-ws` (visible as fewer exchanges per cycle in `TransportStats`).
- **Offline**: the ELM327 emulator (`docs/development/offline-testing.md`) exercises the
  `elm327-tcp` echo path, which the WiCAN cannot.
- **On-vehicle**: a drive over the same hotspot + WireGuard path with `--save`, then
  `canair captures uds --sessions` to confirm the `quality` line shows resyncs/stale
  recovered rather than a dead session, and a tunnel to confirm the 60 s reconnect budget
  brings the recording back.
- `uv run ruff check canlib tests`, `uv run ruff format --check canlib tests`,
  `uv run ty check canlib`, `uv run pytest -q`.

## Open questions

- Should the acceptance mask be derived automatically from the profile's ECU set, or
  declared per profile? Automatic is friendlier; declared is predictable when the TWAI
  single-pair constraint forces a superset.
- Does `canair sniff` need a partial filter mode (e.g. one bus segment) for cellular use, or
  is unfiltered-only acceptable given it is an explicitly bandwidth-heavy command?
