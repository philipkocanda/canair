# High-latency link hardening: request/response correlation and cellular tolerance

Status: **IN PROGRESS** (2026-08-08). Everything is done except item 9 (device-side SLCAN
acceptance filters), which needs hardware verification before it can ship — see its entry.
Items 7 and 15 were dropped with reasons recorded below. Shipped in `ba9fb09`, `bc45bd1`,
`ef1b3c7`, `eb9662a`, `85b0ba4`, `cb070c3`. Follow-up to
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

1. **Prompt accounting in `Elm327Terminal._send_command_locked`** *(done)*
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
2. **Flush the ISO-TP stack on abandonment** *(done)* in `RawUdsClient.poll`
   (`canlib/transport/uds_raw.py:239-242`) and in `read` on timeout
   (`canlib/transport/uds_raw.py:116-135`): drain `while st.available(): st.recv()` before the
   next round, and tally the discards.
3. **Echo-validate the raw path.** *(done)* `poll` validated nothing; its responses now go
   through `request_echo` + the same `CAT_STALE` classification the ELM path uses, backed by a
   per-ECU owed-response ledger (`_MAX_OWED_RESPONSES`) that catches the case echo validation
   structurally cannot — a repeated single-PID poll running one cycle behind.
4. **Widen `request_echo` coverage** *(done)* beyond single-identifier `0x21`/`0x22`
   (`canlib/uds_parse.py`): the multi-DID `0x22` batch form now validates against its **first**
   DID, which is the one byte pair that sits at a fixed offset at any batch size.
5. **Demote `_LINK_LATENCY_MARGIN`** *(done)* (`canlib/transport/elm327_terminal.py`) to a
   *floor*, and drive the window from measured RTT. New leaf `canlib/link_latency.py` holds an
   RFC-6298 smoother (`srtt + 4*rttvar` — TCP's retransmit-timer problem is this problem, and a
   plain mean is an underestimate about half the time, each one orphaning another reply). Fed
   **only** by adapter-only AT commands (`_LINK_PROBE_CMDS`), never by a UDS read, which mixes
   link and ECU and cannot be decomposed. The command timeout no longer vetoes the window; a
   separate `_RESYNC_QUIET_MAX` bounds it.
6. **Extend `_resync` to `decode`** *(done)*, so a connect banner or garbage in the slot
   recovers — `_RESYNC_ON = {CAT_STALE, CAT_DECODE}`. This is the case prompt accounting
   structurally cannot catch: a banner carries its own `>`, so the ledger reads it as a paid
   debt and only its *content* betrays it. `no_data` deliberately stays out (a silent ECU is
   normal while scanning; realigning on every miss costs a round trip per unmapped PID), and so
   does a bare `?`, which `canlib/uds_parse.py` classifies as `CAT_BUS` — it is a complete,
   prompt-terminated reply in the right slot, so the pipe is aligned and the fix is to send less
   (item 8), not to realign. The warning at `canlib/transport/channel.py` was rewritten
   accordingly.
7. ~~**Opportunistic command-echo validation on `elm327-tcp`**~~ — **dropped**, see
   *Deliberately not done*.
8. **Clamp the multi-DID batch size to what the transport can send.** *(done)* The ceiling is
   an ELM327-protocol fact, not a WiCAN one, so it is expressed as a transport capability —
   `Terminal.max_request_bytes` (`canlib/transport/protocol.py`), `7` on `Elm327Terminal` and
   `0xFFF` on `RawTerminal` — and applied once in `_run_query_plan` via `_clamp_cap`
   (`canlib/modes/multi_exec.py`), which both the `multi` pipeline and the monitor funnel
   through. `transport_did_cap` (`canlib/modes/multi_batch.py`) converts bytes to DIDs.
   Demotion is no longer silent: `BatchState.disable` logs the reason once per ECU, and the
   clamp itself logs once per ECU.

### Phase B — make `slcan-tcp` latency-proof

9. **Device-side acceptance filters.** *(blocked on hardware verification — the only item
   not shipped.)* Emit `M`/`m` between the bitrate command and `O` in `SlcanTcpBus.__init__`
   (`canlib/transport/slcan_tcp.py`), computed from the profile's ECU response addresses.
   `sniff` must opt out and stay unfiltered.

   Firmware reading (`wican-fw/`) settled three of the four unknowns:

   - **Ordering is mandatory, not merely preferable.** `can_set_filter`
     (`wican-fw/main/can.c:197`) and `can_set_mask` (`wican-fw/main/can.c:207`) both
     `return` immediately when `bus_state == ON_BUS`. They only stash into `can_cfg`, which
     `can_enable` copies into `f_config.acceptance_code`/`acceptance_mask`
     (`wican-fw/main/can.c:127-129`) at driver install. So `M`/`m` after `O` are silently
     ignored.
   - **Mask polarity: a mask bit of `1` means *don't care*.** The vendor's own idle default is
     `.mask = 0xFFFFFFFF, .filter = 0` (`wican-fw/main/can.c:71`), matching
     `TWAI_FILTER_CONFIG_ACCEPT_ALL()` (`wican-fw/main/can.c:84`). Acceptance is therefore
     `(id ^ code) & ~mask == 0`.
   - **Wire format**: eight ASCII hex digits, MSB first, assembled at
     `wican-fw/main/slcan.c:470-500`; command letters parsed at `wican-fw/main/slcan.c:399-410`
     (`M` → `SL_ACP_CODE`, `m` → `SL_MASK_REG`).
   - **Still unverified: where an 11-bit ID sits inside the 32-bit word.** `single_filter = 1`
     means the SJA1000-derived single-filter layout, in which the ID is *not* right-aligned —
     it occupies the high bits, with RTR and the first data bytes below it. That layout is an
     ESP32 TWAI hardware detail, not present anywhere in `wican-fw/`, so it cannot be confirmed
     from the sources available here.

   **Why that blocks shipping:** the failure mode of a wrong bit position is that *no frame
   matches*, i.e. total silence with no error — indistinguishable from a dead bus, and it would
   send canair into a reconnect loop. Unlike everything else in this plan, it cannot be
   validated by unit tests, because what is in doubt is the peripheral's behaviour rather than
   our arithmetic. It needs one bench check against a real WiCAN: set a filter, confirm the
   expected IDs still arrive, confirm others stop.

   Design notes for when it is picked up:

   - The ESP32 TWAI peripheral offers a *single* code+mask pair, so disjoint response addresses
     get a **superset** mask, not an exact set. For a typical Hyundai/Kia response set
     (`0x7C0`–`0x7FF`) the superset is 64 IDs — still a very large win, because the broadcast
     traffic that dominates the byte count lives well below `0x7C0`.
   - Consider a self-healing fallback rather than trusting the filter blindly: if nothing is
     received in the first few exchanges, reopen with accept-all and log it. That keeps the
     failure mode "slow" instead of "silent".
10. **Latency-adaptive ISO-TP budgets** *(done)* — `build_isotp_params(config, link_budget)`
    **adds** the measured allowance to `rx_flowcontrol_timeout` and
    `rx_consecutive_frame_timeout`. Additive, not replacing: the configured value is the ECU's
    share and the measurement is the network's, and the two delays are sequential, so a profile
    that needs 3000 ms from a slow ECU still gets it. Measurement comes from the TCP handshake,
    seeded in `SlcanTcpBus.__init__` — one round trip by construction, and available before any
    CAN traffic exists, which is when the stack params must be chosen.
11. **Raise the raw per-request default** *(done)* — resolved by making it adaptive rather
    than by bumping the magic number: `RawUdsClient._budget` adds the measured allowance to the
    per-ECU/global timeout. A caller-forced `--timeout` is an *instruction*, not a budget, so it
    is left exactly as given.
12. **Warn on `blocksize > 0` on a slow link** *(done)* — `isotp_params._warn_blocksize_cost`,
    once per session at INFO. Driven by the **measurement**, not a hostname/is-local heuristic:
    the real question is whether round trips are expensive, which is now directly known. Left
    out of `canair validate`, which has no link to measure.

### Phase C — defaults, escalation, guidance

13. **`_DEFAULT_RECONNECT_MAX_WAIT` 6.0 → 60.0** *(done)*, with `config.example.yaml`,
    `docs/reference/config.md` and `AGENTS.md` updated. Fixes the ping-induced regression in
    finding 8.
14. **Raise `_DEFAULT_CONNECT_TIMEOUT` 2.0 → 5.0** *(done)*, so a cellular radio's
    idle-to-connected transition does not read as a dead device.
    `TestDefaultsAreSizedForAMobileLink` pins the *direction* (floors) rather than the exact
    values, since each was originally a LAN number that failed on cellular.
15. ~~**A single link-latency knob**~~ — **dropped**, see *Deliberately not done*.
16. **Warn once when the measured link makes `slcan-tcp` the limiting factor** *(done)* —
    `RawUdsClient._hint_transport_choice`, above `_TRANSPORT_HINT_RTT_S`. Again measured rather
    than inferred from the host. It quotes the measured **round trip**, not the internal
    allowance, so the number means what the user thinks it means.
17. **`docs/concepts/remote-and-cellular.md`** *(done)* — the transport tradeoff from finding
    6 as a comparison table, what canair measures and what it feeds, the two failure modes
    (link dies vs link stays open but stops being useful), the data-usage arithmetic from
    finding 2, and a settings table. Linked from `mkdocs.yml`, `docs/reference/config.md` and
    `AGENTS.md`.

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
- **No opportunistic ELM327 command-echo validation** (item 7). It is impossible on the
  transport that actually had the bug: `elm327_config.echo` is assigned in the WiCAN firmware
  (`wican-fw/main/elm327.c:122,187,191`) and never read, so the dongle answers `OK` to `ATE1`
  and then never echoes. It would only work on `elm327-tcp` clones and the emulator — and
  prompt accounting (item 1) already solves correlation generically on *both* ELM transports.
  Building it would add a second, transport-specific mechanism that does nothing for the
  reported failure.
- **No configurable link-latency knob** (item 15). Every consumer is now driven by a
  *measurement* (`canlib/link_latency.py`, seeded from the TCP handshake and refined by
  adapter-only AT commands). A config key duplicating a measurement is a number the user cannot
  know better than canair does, and it would drift out of agreement with the thing it shadows.
  The measurement stays; the knob is not added.
- **No `canair validate` warning for a `multi_did_max` above the ELM327 ceiling** (dropped from
  item 8 during implementation). The Ioniq's `multi_did_max: 6`
  (`profiles/ioniq-2017/profile.yaml:71`) is *correct* for `slcan-tcp`, the default transport,
  so warning about it would fire on the bundled profile on every `validate all` including CI —
  a false alarm about a portable setting. The profile value states the car's capability; which
  part of it is reachable is the transport's business, and the runtime clamp plus a one-line
  log per ECU is the honest place to say so.

## Verification

Done:

- **Unit**: prompt accounting against a scripted channel with queued prompt-terminated
  replies, including a ResponsePending interleaved, asserting the *last* block is returned and
  the rest tallied `stale` (`tests/test_elm327_prompt_accounting.py`). Raw-path flush and echo
  validation on `poll` (`tests/test_uds_raw_correlation.py`). Realignment sizing, probe
  failure, transaction re-entrancy (`tests/test_terminal.py`). Escalation thresholds
  (`tests/test_monitor_reconnect.py`). Measurement behaviour (`tests/test_link_latency.py`).
- **The regression that matters most** — a repeated **single-PID** poll running one cycle
  behind, the case echo validation structurally cannot see — is covered on both paths:
  `test_the_same_pid_twice_still_recovers` (ELM, via the prompt ledger) and
  `test_the_same_pid_recovers_via_the_ledger` (raw, via the owed-response ledger).
- `uv run ruff check canlib tests`, `uv run ruff format --check canlib tests`,
  `uv run ty check canlib`, `uv run pytest -q` — clean, 5517 passed.

Still outstanding (needs a device or a drive):

- **Latency simulation**: a channel/fake bus injecting a configurable delay, run at 0 ms,
  300 ms and 2000 ms RTT, asserting no desync survives a cycle at any of them. The unit tests
  cover the *mechanisms* at zero latency; this would cover their interaction under delay.
- **Batching**: confirm on a device that a 6-DID request is rejected with `?` on `wican-ws`
  and accepted on `slcan-tcp`, and that after the transport-aware clamp the Ioniq profile
  actually batches on `wican-ws` (visible as fewer exchanges per cycle in `TransportStats`).
- **Offline**: the ELM327 emulator (`docs/development/offline-testing.md`) exercises the
  `elm327-tcp` echo path, which the WiCAN cannot.
- **On-vehicle**: a drive over the same hotspot + WireGuard path with `--save`, then
  `canair captures uds --sessions` to confirm the `quality` line shows resyncs/stale
  recovered rather than a dead session, and a tunnel drop to confirm the 60 s reconnect budget
  brings the recording back.
- **Item 9's bench check** — set an acceptance filter on a real WiCAN and confirm the expected
  response IDs still arrive while others stop. This is the gate on shipping item 9 at all.

## Open questions

- Should the acceptance mask be derived automatically from the profile's ECU set, or
  declared per profile? Automatic is friendlier; declared is predictable when the TWAI
  single-pair constraint forces a superset.
- Does `canair sniff` need a partial filter mode (e.g. one bus segment) for cellular use, or
  is unfiltered-only acceptable given it is an explicitly bandwidth-heavy command?

## Adjacent defects noticed, not fixed here

Both were spotted while working this plan and left alone because fixing either is a behaviour
change rather than a tidy-up, and neither is on this plan's path.

- **A dead classification rule.** `canlib/uds_parse.py`'s `_ERROR_RULES` contains
  `("unknown command", "decode")`, but the parser returns `_fail(CAT_BUS, "Unknown command")`
  for a bare `?` before any rule is consulted, so the rule never fires and the two
  classification models disagree about what a `?` is. Changing it moves an outcome between
  `decode` and `bus`, and `error_kind` feeds both a capture's `quality` provenance and the
  monitor's counters — so it needs a deliberate decision about which model is right, not a
  silent edit. (This plan's item 6 depends on the answer being `bus`, which is why the
  disagreement surfaced.)
- **`MonitorController.diag()` and `diag_recorder()` are near-duplicates**
  (`canlib/modes/monitor.py`), both resolving `raw_client or terminal` then `getattr(..., "diag")`.
  Deduplicating touches a duck-typed seam (`canlib/modes/_monitor_record.py` probes for
  `diag_recorder` with `getattr`), a fake in `tests/test_monitor_stop.py`, and several
  assertions in `tests/test_monitor_diag.py`.
