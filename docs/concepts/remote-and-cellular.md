# Remote & cellular links

canair does not have to run next to the car. A common setup is a WiCAN in the car, a phone
hotspot, a VPN, and canair on a server at home — which means every request and response crosses
a link with tens or hundreds of milliseconds of round-trip time (RTT), variable jitter, and
occasional multi-second outages while the phone hands over between cells.

This page covers what that link does to a session, which transport to pick, and the arithmetic
behind data usage. It is about the *network*; for what the **car** is allowed to do, see
[Safety](safety.md).

## The short version

- **Prefer `wican-ws` on a remote link** (WiCAN Pro only). It moves ISO-TP onto the dongle, so
  flow control stays on the car's side of the network.
- **`--wait` is what you want for a long recording.** It makes both the initial connect and any
  mid-session reconnect retry indefinitely instead of giving up.
- **Leave `isotp.blocksize` at `0`.** Any other value costs a round trip per block.
- **canair measures the link and adapts its own timeouts.** You should not need to tune them.
- **`slcan-tcp` streams the whole CAN bus to you.** Budget for it, or use `wican-ws`.

## Which transport on a slow link

The two WiCAN transports differ in *where ISO-TP runs*, and on a remote link that is the whole
story. A UDS response longer than 7 bytes is split into a First Frame, a flow-control (FC)
handshake, then Consecutive Frames.

| | `slcan-tcp` (default) | `wican-ws` (Pro only) |
|---|---|---|
| Who runs ISO-TP | canair, on your machine | the dongle |
| FC frame crosses the network | **yes** | no |
| Multi-frame read cost | request + **one extra RTT** + frames | request + frames |
| Requests in flight | pipelined across ECUs | one at a time |
| Bus traffic sent to you | **every frame on the bus** | only what you asked for |
| Works on a classic WiCAN | yes | no |

The tradeoff is not one-sided. `slcan-tcp` pipelines requests across ECUs, so it hides latency
well when responses are short; `wican-ws` is one-command-at-a-time, so a slow link stretches the
poll cycle in proportion to the number of signals. But `wican-ws` is the only one of the two
whose *multi-frame* reads do not pay for the network, and multi-frame reads are where a slow link
actually breaks things.

!!! warning "`slcan-tcp` cannot be made as robust as `wican-ws` for multi-frame reads"
    The receiving ECU starts an `N_Bs` timer (typically ~1 s) when it sends a First Frame and
    aborts the message if the FC frame does not arrive in time. That timer lives in **ECU
    firmware** and is not tunable from canair. Adaptive timeouts buy tolerance, not parity: they
    stop canair from giving up too early, but they cannot stop the ECU from giving up.

Switch per command with `--transport wican-ws`, or per device:

```yaml
devices:
  vpn:
    host: 192.168.3.2
    transport: wican-ws
```

On a link measured slower than 250 ms round trip, canair logs this recommendation once per
session (see `canair logs`).

## What canair measures, and what it does with it

canair does not ask you for the link's latency — it measures it and sizes its own windows.

- **The TCP handshake is one round trip by construction.** `slcan-tcp` times it at connect, which
  is before any CAN traffic exists, and that is exactly when the ISO-TP flow-control budgets have
  to be chosen.
- **On `wican-ws`, adapter-only AT commands are samples.** `ATI` and the header writes
  (`ATSH`/`ATFCSH`) are answered by the dongle without touching the bus, so their round trip is
  the network's alone. A UDS read is *not* used — it mixes the link with the car and cannot be
  decomposed afterwards.
- **The estimate is smoothed the way TCP smooths its retransmit timer** (RFC 6298:
  `srtt + 4 × rttvar`). A plain average is an underestimate about half the time, and on this link
  every underestimate abandons a reply that was still in flight.

What it feeds:

| Consumer | Effect |
|---|---|
| `isotp.rx_flowcontrol_timeout`, `isotp.rx_consecutive_frame_timeout` | measured allowance **added** to the configured value |
| raw per-request budget | measured allowance added to the per-ECU/global timeout |
| ELM327 pipe realignment window | sized from the adapter's own `ATST` wait **plus** the measured link |
| transport advice, `blocksize` advice | logged once when the measurement says it matters |

The configured values are the **car's** share and the measurement is the **network's**; the two
delays are sequential, so they add. A profile that needs 3000 ms from a slow ECU still gets it.

A measured round trip above 50 ms also appears as an `rtt` reading on the monitor's health line,
so "the car is not answering" and "the network is slow" stop looking identical.

## Recovering from a drop

A phone hotspot changing cells, or a VPN re-keying, takes a link out for seconds to tens of
seconds. Two different failures have to be handled, and only one of them raises an error.

**The link dies.** `canair monitor` re-homes the session: it re-probes reachable
same-transport devices, rebuilds the client, re-opens any diagnostic sessions and resumes. A
`--save` recording continues on the same journal, with the gap visible in the timestamps. This is
bounded by `transport.reconnect_max_wait` (default 60 s) — or retries forever with `--wait`.

**The link stays open but stops being useful.** This is the subtler one. A half-open socket keeps
accepting writes, and a desynchronised ELM327 pipe keeps returning well-formed replies — to the
*previous* request, forever. Nothing raises, so nothing would trigger a reconnect. canair
therefore correlates every reply to its request rather than trusting arrival order:

- On `wican-ws`/`elm327-tcp`, the `>` prompt is counted. The adapter owes exactly one prompt per
  response, so a backlog is arithmetic rather than a timing guess, and a reply to an abandoned
  command is discarded instead of being served as the next one's answer.
- On `slcan-tcp`, each ECU has an owed-response ledger plus service/identifier echo validation.
- If a response still lands in the wrong slot, the pipe is realigned (drain, then probe the
  adapter) before the next request.
- As a backstop, `transport.stale_cycles_before_reconnect` (default 3) reconnects after that many
  poll cycles in which *nothing* answered coherently. A negative response counts as answered —
  the reply reached the right slot.

Every discard, realignment and reconnect is tallied and visible in `canair logs` and in a
recording's `quality` provenance, so a session that recovered silently is still auditable.

!!! tip "Recording a drive remotely"
    ```bash
    uv run canair monitor @driving --save --label "commute" --wait --transport wican-ws
    ```
    `--wait` blocks until the device appears, so you can start the command before the car, and
    makes a mid-drive drop retry until the link returns rather than ending the recording.

## Data usage

This is the part that surprises people, and it is a property of the transport, not of how much
you ask for.

**`slcan-tcp` sends you every frame on the bus.** It is a raw SLCAN stream with no acceptance
filter, and SLCAN is ASCII: a single 8-byte frame is about 22 characters on the wire. A busy
Hyundai/Kia powertrain bus runs on the order of 2500 frames per second:

```
22 bytes × 2500 frames/s ≈ 55 kB/s ≈ 440 kbit/s ≈ 200 MB/hour
```

That is the *idle* cost — it is the same whether you poll one signal or fifty. On a metered
hotspot, an hour's drive is a couple of hundred megabytes.

**`wican-ws` sends only what you asked for.** The dongle runs ISO-TP and answers requests, so
throughput is proportional to your poll rate: a few hundred bytes per request/response pair,
typically well under a megabyte per hour.

If you are on a metered link and do not need the whole bus, `wican-ws` is a three-orders-of-
magnitude reduction. If you *do* need the whole bus — `canair sniff`, or importing a broadcast
log — you need `slcan-tcp` and you need the bandwidth; consider running canair in the car and
copying the log afterwards.

### Why canair doesn't filter this on the device (yet)

The WiCAN's SLCAN interface *does* expose the hardware acceptance filter, so in principle canair
could ask the dongle to send only the ECU response addresses the profile cares about and cut the
idle cost to near zero. It does not do this today, deliberately. The firmware reading is worth
recording so the next attempt starts from facts rather than repeating it:

- **The filter must be set while the channel is closed.** `can_set_filter` and `can_set_mask`
  (`wican-fw/main/can.c`) return immediately when the bus state is `ON_BUS`; they only stash
  into a config struct that is copied into the driver at install time. So `M`/`m` have to be
  sent between the bitrate command and `O`, and after `O` they are silently ignored.
- **A mask bit of `1` means *don't care*.** The firmware's idle default is
  `mask = 0xFFFFFFFF, filter = 0`, i.e. accept everything. Acceptance is
  `(id ^ code) & ~mask == 0`.
- **Wire format** is eight ASCII hex digits, most significant first, for each of `M`
  (acceptance code) and `m` (mask).
- **The peripheral offers a single code+mask pair**, so a set of disjoint response addresses can
  only be covered by a *superset*. For a typical Hyundai/Kia diagnostic set that superset is
  `0x7C0`–`0x7FF` — 64 IDs, and still a very large win, because the broadcast traffic that
  dominates the byte count lives well below `0x7C0`.

What is **not** established is where an 11-bit identifier sits inside that 32-bit acceptance
word. Single-filter mode uses the SJA1000-derived layout, in which the ID is not right-aligned —
RTR and the leading data bytes share the word below it — and that is an ESP32 peripheral detail,
not something the WiCAN sources state.

That matters because of the failure mode. Get the bit position wrong and *no frame matches*: the
bus goes completely silent, which is indistinguishable from a dead bus or an asleep car, and
canair would spend the drive reconnecting. A wrong filter is worse than no filter, and it cannot
be caught by a unit test — what is in doubt is the hardware's behaviour, not the arithmetic. So
it waits on a bench check against a real device. Tracked in
`plans/2026-08-08-high-latency-link-hardening.md` (item 9), which also sketches a self-healing
variant: apply the filter, and reopen unfiltered if the first few exchanges return nothing, so
the worst case is "slow" rather than "silent".

## Settings that matter here

All of these live under `transport:` in `~/.config/canair/config.yaml`; see
[Configuration](../reference/config.md) for the full list and
`canair config example` for the annotated reference.

| Key | Default | Why it matters remotely |
|---|---|---|
| `connect_timeout` | `5.0` | Paid once per fallback candidate. A cellular radio can burn 1-2 s waking up before the SYN leaves; too short declares a live device dead. |
| `reconnect_max_wait` | `60.0` | Bounded mid-session reconnect window. Sized for a cell handover or VPN re-key. `--wait` makes it unbounded. |
| `ws_ping_interval` | `20.0` | WebSocket keepalive, so a dead `wican-ws` link raises instead of going quiet. Lower it on a flaky link; `0` disables. |
| `stale_cycles_before_reconnect` | `3` | Correctness backstop. A cycle count, not a duration, so it scales with your poll rate; `0` disables. |
| `expected_responses` | `true` | ELM327 transports only, and the biggest per-read lever: tells the adapter how many frames to expect so it stops waiting out `ATST` after the last one. ~4x faster per read; leave it on. |
| `fallback`, `fallback_order` | on | Try your other configured devices when the selected one is unreachable. |

Profile-level `isotp:` settings also matter:

| Key | Default | Why it matters remotely |
|---|---|---|
| `blocksize` | `0` | `0` means "send the whole response after a single FC frame". Any other value costs **one round trip per block**. canair logs a note if you set one on a slow link. |
| `rx_flowcontrol_timeout`, `rx_consecutive_frame_timeout` | `1000` ms | The **ECU's** share. canair adds the measured network allowance on top; you should not need to pad these by hand. |

And `response_timeout_ms` in `profile.yaml` is the **car's** budget — on `wican-ws` it becomes the
adapter's `ATST` wait, i.e. how long the dongle waits for the ECU. It is deliberately not a
network setting; do not inflate it to compensate for a slow link. What keeps you from *paying* it
on every read is `expected_responses` above: once canair has learned how many frames a request
answers with, the adapter returns as soon as the reply is whole instead of sitting out the budget
to be sure no further frame is coming.

Those learned counts are **saved to your profile** as each PID's `response_frames:`, so the speedup
survives the session that measured it — a fresh connection is fast from its first read instead of
paying one slow read per PID to re-learn. Nothing to configure: any session that confirms a count
writes it back, and `--no-learn-frames` opts out. A count is only recorded once the wire proved it,
and a response whose length turns out to vary has its count withdrawn rather than guessed at.

You can see which PIDs have earned one, and how much of the profile is covered, with:

```bash
uv run canair wican autopid stats     # a Frames column, plus an N/M coverage line
```

On a slow link this is the difference between a first poll cycle at ~600 ms per PID and one at
roughly the network round trip. It applies to the ELM327 transports (`wican-ws`, `elm327-tcp`); the
raw `slcan-tcp` path measures the same counts as a by-product, since canair runs ISO-TP itself there
and already knows when a response is complete.

## Diagnosing a bad session

```bash
uv run canair status                  # transport, device reachability, versions
uv run canair logs                    # drops, stale/discarded replies, realignments, advice
uv run canair captures uds --sessions # per-session transport + quality provenance
```

In the monitor, the health line carries `drops`, `stale`, `resync`, `errs` and `rtt`. A rising
`stale` with a healthy `rtt` points at correlation (and should self-heal); a high `rtt` with few
errors points at the transport choice; `drops` climbing with neither points at the bus or the
device. Press `R` to force a reconnect.

## See also

- [Configuration](../reference/config.md) — every key, including the `transport:` block.
- [ECU protocols & PID prefixes](ecu-protocols.md) — why a response is multi-frame at all.
- [Broadcast CAN frames](broadcast-frames.md) — the `slcan-tcp`-only sniffing path.
- [Captures & states](captures-and-states.md) — where a session's `quality` provenance is recorded.
