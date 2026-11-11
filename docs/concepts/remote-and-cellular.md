# Remote & cellular links

canair does not have to run next to the car. A common setup is a WiCAN in the car, a phone
hotspot, a VPN, and canair on a server at home. Every request and response then crosses a link
with these properties:

- tens or hundreds of milliseconds of round-trip time (RTT)
- variable jitter
- occasional outages of several seconds while the phone hands over between cells

This page covers what that link does to a session, which transport to pick, and the arithmetic
behind data usage. It is about the *network*. For what the **car** is allowed to do, see
[Safety](safety.md).

## The short version

- **Prefer `wican-ws` on a remote link** (WiCAN Pro only). It moves the ISO 15765-2 transport
  protocol (ISO-TP) onto the dongle, so flow control stays on the car's side of the network.
- **Use `--wait` for a long recording.** It makes both the initial connect and any mid-session
  reconnect retry indefinitely, and canair does not give up.
- **Leave `isotp.blocksize` at `0`.** Any other value costs a round trip per block. An electronic
  control unit (ECU) that needs a block size gets one in its own `isotp:` block, so only its reads
  pay for it.
- **canair measures the link and adapts its own timeouts.** You should not need to tune them.
- **`slcan-tcp` streams the whole controller area network (CAN) bus to you.** Budget for it, or
  use `wican-ws`.

## Which transport on a slow link

The two WiCAN transports differ in *where ISO-TP runs*. On a remote link, this is the whole
story. A Unified Diagnostic Services (UDS) response longer than 7 bytes is split into a First
Frame, a flow-control (FC) handshake, and then Consecutive Frames.

| | `slcan-tcp` (default) | `wican-ws` (Pro only) |
|---|---|---|
| Who runs ISO-TP | canair, on your machine | the dongle |
| FC frame crosses the network | **yes** | no |
| Multi-frame read cost | request + **one extra RTT** + frames | request + frames |
| Requests in flight | pipelined across ECUs | one at a time |
| Bus traffic sent to you | **every frame on the bus** | only what you asked for |
| Works on a classic WiCAN | yes | no |

The tradeoff is not one-sided. `slcan-tcp` pipelines requests across ECUs, so it hides latency
well when responses are short. `wican-ws` handles one command at a time, so a slow link stretches
the poll cycle in proportion to the number of signals. But `wican-ws` is the only one of the two
whose *multi-frame* reads do not pay for the network. Multi-frame reads are where a slow link
breaks things.

!!! warning "`slcan-tcp` cannot be made as reliable as `wican-ws` for multi-frame reads"
    The receiving ECU starts an `N_Bs` timer (typically ~1 s) when it sends a First Frame. It
    aborts the message if the FC frame does not arrive in time. That timer lives in **ECU
    firmware**, and canair cannot tune it. Adaptive timeouts buy tolerance, not parity. They stop
    canair from giving up too early, but they cannot stop the ECU from giving up.

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

canair does not ask you for the latency of the link. It measures the latency and sizes its own
windows.

- **The TCP handshake is one round trip by construction.** `slcan-tcp` times it at connect. This
  is before any CAN traffic exists, and canair must choose the ISO-TP flow-control budgets at
  exactly that time.
- **On `wican-ws`, adapter-only AT commands are samples.** The dongle answers `ATI` and the header
  writes (`ATSH`/`ATFCSH`) without touching the bus. Their round trip is therefore the network's
  alone. canair does not use a UDS read as a sample. A UDS read mixes the link with the car, and
  canair cannot separate them afterwards.
- **canair smooths the estimate the way TCP smooths its retransmit timer** (RFC 6298:
  `srtt + 4 × rttvar`). A plain average is an underestimate about half the time. On this link,
  every underestimate abandons a reply that was still in flight.

The measurement feeds these consumers:

| Consumer | Effect |
|---|---|
| `isotp.rx_flowcontrol_timeout`, `isotp.rx_consecutive_frame_timeout` | measured allowance **added** to the configured value |
| raw per-request budget | measured allowance added to the per-ECU/global timeout |
| ELM327 adapter pipe realignment window | sized from the `ATST` wait of the adapter itself **plus** the measured link |
| transport advice, `blocksize` advice | logged once when the measurement says it matters |

The configured values are the **car's** share, and the measurement is the **network's** share.
The two delays are sequential, so they add. A profile that needs 3000 ms from a slow ECU still
gets it.

A measured round trip above 50 ms also appears as an `rtt` reading on the health line of the
monitor. "The car is not answering" and "the network is slow" then no longer look identical.

## Recovering from a drop

A phone hotspot that changes cells, or a VPN that re-keys, takes a link out for seconds to tens of
seconds. canair must handle two different failures. Only one of them raises an error.

**The link dies.** `canair monitor` re-homes the session. It re-probes reachable same-transport
devices, rebuilds the client, re-opens any diagnostic sessions and resumes. A `--save` recording
continues on the same journal. The `link_events` of the session record the gap and its cause
([Capture file format and data quality](capture-format.md#gaps-link_events)).
`transport.reconnect_max_wait` (default 60 s) bounds this recovery. With `--wait`, canair retries
forever.

**The link stays open but stops being useful.** This failure is subtler. A half-open socket keeps
accepting writes. A desynchronised ELM327 pipe keeps returning well-formed replies to the
*previous* request, forever. Nothing raises an error, so nothing would trigger a reconnect.
canair therefore correlates every reply to its request and does not trust the arrival order:

- On `wican-ws` and `elm327-tcp`, canair counts the `>` prompt. The adapter owes exactly one
  prompt per response. A backlog is therefore arithmetic and not a timing guess. canair discards a
  reply to an abandoned command and does not serve it as the answer to the next command.
- On `slcan-tcp`, each ECU has an owed-response ledger plus service/identifier echo validation.
- If a response still lands in the wrong slot, canair realigns the pipe (drain, then probe the
  adapter) before the next request.
- As a backstop, `transport.stale_cycles_before_reconnect` (default 3) reconnects after that many
  poll cycles in which *nothing* answered coherently. A negative response counts as answered,
  because the reply reached the right slot.

canair tallies every discard, realignment and reconnect. You can see the tallies in `canair logs`
and in the `quality` provenance of a recording. Each disconnect and realignment has a timestamp in
`link_events`. A session that recovered without a message is therefore still auditable.

!!! tip "Recording a drive remotely"
    ```bash
    uv run canair monitor @driving --save --label "commute" --wait --transport wican-ws
    ```
    `--wait` blocks until the device appears, so you can start the command before the car. It also
    makes a mid-drive drop retry until the link returns, so the recording does not end.

## Data usage

This part surprises people. It is a property of the transport, not of how much you ask for.

**`slcan-tcp` sends you every frame on the bus.** It is a raw serial-line CAN (SLCAN) stream with
no acceptance filter. SLCAN is ASCII, so a single 8-byte frame is about 22 characters on the wire.
A busy Hyundai/Kia powertrain bus runs on the order of 2500 frames per second:

```
22 bytes × 2500 frames/s ≈ 55 kB/s ≈ 440 kbit/s ≈ 200 MB/hour
```

That is the *idle* cost. It is the same whether you poll one signal or fifty. On a metered
hotspot, an hour's drive is a couple of hundred megabytes.

**`wican-ws` sends only what you asked for.** The dongle runs ISO-TP and answers requests.
Throughput is therefore proportional to your poll rate: a few hundred bytes per request/response
pair, typically well under a megabyte per hour. The default schedule of `canair monitor` helps
here. It works per parameter ID (PID). A signal with a long `period:` is re-read only as often as
it can change, not on every cycle. A mixed monitor set therefore costs far less on a metered link
than polling everything at the rate of the fastest signal.

If you are on a metered link and do not need the whole bus, `wican-ws` reduces the data by three
orders of magnitude. If you *do* need the whole bus (`canair sniff`, or importing a broadcast
log), you need `slcan-tcp` and the bandwidth. Consider running canair in the car and copying the
log afterwards.

### Why canair can't filter this on the device

In principle, the dongle could send only the ECU response addresses that the profile uses. This
would cut the idle cost to near zero. The WiCAN ESP32 firmware does not allow it. The firmware
read here is 4.52p (commit `fab54ad`):

- **The SLCAN `M` (acceptance code) and `m` (acceptance mask) commands are accepted but have no
  effect.** They store a filter and a mask (`can_set_filter`/`can_set_mask` in
  `wican-fw/main/can.c`). But `can_enable()` ignores the stored values. It installs the CAN
  controller with `acceptance_code = 0` and `acceptance_mask = 0xFFFFFFFF`, which accepts every
  frame. The lines that would apply the configured filter are commented out.
- **So `slcan-tcp` always relays every frame on the segment.** canair cannot narrow this on the
  device. If canair sent `M`/`m`, the dongle would only look filtered while it is not.

To avoid the bus-wide stream on a metered link, use `wican-ws`. There the dongle runs ISO-TP and
sends only the replies to your requests.

This could change if a firmware release re-enables the filter. The v6 firmware does not do it
either: its SLCAN codec ignores `M` and `m` with no reply (see
`plans/2026-10-20-wican-fw-v6-compatibility.md`). If a release re-enables the filter, the earlier
design notes are in `plans/2026-08-08-high-latency-link-hardening.md`, item 9. They say:

- Set the filter while the channel is closed.
- A mask bit of `1` means "don't care".
- Use one code+mask pair, so the filter passes a superset of the response addresses.

## Settings that matter here

All of these live under `transport:` in `~/.config/canair/config.yaml`. See
[Configuration](../reference/config.md) for the full list and `canair config example` for the
annotated reference.

| Key | Default | Why it matters remotely |
|---|---|---|
| `connect_timeout` | `5.0` | canair pays this once per fallback candidate. A cellular radio can need 1-2 s to wake up before the SYN packet leaves. A value that is too short declares a live device dead. |
| `reconnect_max_wait` | `60.0` | Bounded mid-session reconnect window. Sized for a cell handover or VPN re-key. `--wait` makes it unbounded. |
| `ws_ping_interval` | `20.0` | WebSocket keepalive, so a dead `wican-ws` link raises an error and does not go quiet. Lower it on a flaky link. `0` disables it. |
| `stale_cycles_before_reconnect` | `3` | Correctness backstop. It is a cycle count and not a duration, so it scales with your poll rate. `0` disables it. |
| `expected_responses` | `true` | ELM327 transports only, and the biggest per-read lever. It tells the adapter how many frames to expect, so the adapter stops waiting out `ATST` after the last one. Each read is ~4x faster. Leave it on. |
| `fallback`, `fallback_order` | on | Try your other configured devices when the selected one is unreachable. |

Profile-level `isotp:` settings also matter:

| Key | Default | Why it matters remotely |
|---|---|---|
| `blocksize` | `0` | `0` means "send the whole response after a single FC frame". Any other value costs **one round trip per block**. canair logs a note if you set one on a slow link. |
| `rx_flowcontrol_timeout`, `rx_consecutive_frame_timeout` | `1000` ms | The **ECU's** share. canair adds the measured network allowance on top. You should not need to pad these by hand. |

`response_timeout_ms` in `profile.yaml` is the **car's** budget. On `wican-ws`, it becomes the
`ATST` wait of the adapter, which is how long the dongle waits for the ECU. It is deliberately not
a network setting. Do not inflate it to compensate for a slow link.

`expected_responses` (above) keeps you from *paying* this budget on every read. Once canair has
learned how many frames a request answers with, the adapter returns as soon as the reply is
complete. It does not sit out the budget to be sure that no further frame is coming.

canair **saves learned counts to your profile** as the `response_frames:` of each PID. A fresh
connection is therefore fast from its first read. You do not need to configure anything. Any
session that confirms a count writes it back (`--no-learn-frames` opts out). canair withdraws a
count if the response length turns out to vary.

To see which PIDs have earned a count and how much of the profile is covered, run:

```bash
uv run canair wican autopid stats     # a Frames column, plus an N/M coverage line
```

On a slow link, this is the difference between ~600 ms per PID and roughly the network round trip.
It applies to the ELM327 transports (`wican-ws`, `elm327-tcp`). On raw `slcan-tcp`, canair runs
ISO-TP itself and already knows when a response is complete.

## Diagnosing a bad session

```bash
uv run canair status                  # transport, device reachability, versions
uv run canair logs                    # drops, stale/discarded replies, realignments, advice
uv run canair captures uds --sessions # per-session transport + quality provenance
```

The health line of the monitor carries `drops`, `stale`, `resync`, `errs` and `rtt`. Read it as
follows:

- A rising `stale` with a healthy `rtt` points at correlation (and should self-heal).
- A high `rtt` with few errors points at the transport choice.
- `drops` that climb with neither point at the bus or the device.

Press `R` to force a reconnect.

## See also

- [Configuration](../reference/config.md) — every key, including the `transport:` block.
- [ECU protocols & PID prefixes](ecu-protocols.md) — why a response is multi-frame at all.
- [Broadcast CAN frames](broadcast-frames.md) — the `slcan-tcp`-only sniffing path.
- [Capture file format and data quality](capture-format.md) — where a session's `quality`
  provenance is recorded.
