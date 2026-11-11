# Configuration

canair reads the user config from `~/.config/canair/config.yaml`
(`$XDG_CONFIG_HOME/canair/config.yaml`). canair creates it on the first run. View and edit it from
the CLI. Do not edit it by hand:

```bash
canair config show          # config file locations + effective settings
canair config example        # print config.example.yaml (the authoritative reference)
canair config get KEY
canair config set KEY VALUE  # dotted keys create nested mappings
canair config unset KEY
canair config edit           # open in $EDITOR
```

!!! note "Authoritative source"
    `config.example.yaml` in the repo root documents every key with inline comments. Print it at
    any time with `canair config example`. If this page and that file disagree, the example file
    wins. It lives next to the code.

## Keys

| Key | Purpose |
|---|---|
| `default_profile` | The [profile](../concepts/profiles.md) to use when you give none. `--profile` or `CANAIR_PROFILE` overrides it. Optional if canair discovers only one profile. |
| `profile_settings.<profile>.variant` | The [variant](../concepts/variants.md) of that profile that your car is. canair remembers it per profile. Set it with `canair profile use NAME --variant ID`. `--variant` or `CANAIR_VARIANT` overrides it. Required once for a profile with several variants. Live commands refuse to run until you set it. |
| `profile_settings.<profile>.revisions.<ECU>` | The revision of an electronic control unit (ECU) in your car (one of its `revisions:` IDs). Set it for an ECU that your variant allows several revisions of. Set it with `canair profile use NAME --pin ECU=ID`. `--revision ECU=ID` overrides it for one command. Without it, canair skips the revision-specific parameter identifiers (PIDs) and signals of that ECU. See [ECU revisions](../concepts/variants.md#ecu-revisions). |
| `profiles_dir` | An extra directory to search for profiles. Git never commits it. |
| `devices` | Named devices for the `--wican` flag. Each alias has a `host` and an optional `transport`, `port` and `bitrate` for that device (see below). |
| `default_wican` | The device alias to use by default. |
| `wican_addresses` | Legacy flat `alias: host` map (host only). canair reads it only when no `devices:` block exists. canair **migrates it to `devices:` on the first run**. |
| `wican_model` | `pro` (default) or `classic`. `classic` makes canair refuse Pro-only features with a clear message. |
| `check_for_updates` | `true` (default) or `false`. `false` turns off the automatic once-a-day update check. `CANAIR_NO_UPDATE_CHECK` also turns it off. |
| `grid_region` | The charging-grid region for the physical-value scan: `EU`, `UK`, `US`, `JP`, `CN` or `AU` (case-insensitive). It sets the mains-voltage and line-frequency bands (see below). |
| `display.byte_notation` | The default byte-index notation for analysis output labels. It also applies to the byte ruler and the signal byte-reference column in `monitor` and `captures --diff/--step`. Values: `wican` (default), `isotp`, `torque` or `bix`. `--notation` overrides it per command. |
| `safety.allow_unsafe` | `false` (default) or `true`. Live commands refuse `--unsafe` until this is `true` in the user config file. While it is `true`, every command starts with a warning banner (see [Safety](../concepts/safety.md#enabling-unsafe)). |
| `safety.allow_reset` | `false` (default) or `true`. `canair reset` refuses to run until this is `true` in the user config file. When you set it, canair asks you to type `YES` at a terminal. No flag skips that prompt (see [Safety](../concepts/safety.md#resetting-an-ecu)). |
| `safety.allow_frame_injection` | `false` (default) or `true`. Profile actions that put a crafted broadcast Controller Area Network (CAN) frame on the bus (`canair action`, `slcan-tcp` only) refuse to run until this is `true` in the user config file. When you set it, canair asks you to type `YES` at a terminal. No flag skips that prompt (see [Safety](../concepts/safety.md#injecting-a-broadcast-frame)). |
| `transport` | Advanced: explicit CAN transport selection (see below). |

## `grid_region` — physical-scan grid bands

The reference-free physical-value scan (`canair hunt --physical` / `canair investigate`) flags a
raw byte when its scaled value lands in a named physical range. The **mains-voltage and
line-frequency** bands depend on *where the car charges*, not on the car. The same electric
vehicle (EV) charges from 230 V / 50 Hz in Berlin and from split-phase 120/240 V / 60 Hz in
Denver. So you set these bands once per location here, and they apply to every profile. They do
not belong in a shared vehicle profile.

If you leave `grid_region` unset, canair assumes **EU (230 V / 50 Hz)**. The first time a physical
scan runs, canair offers to set the region. On a terminal it asks once. When output is piped, it
prints one note on stderr. Presets:

| Region | Nominal | Line frequency |
|---|---|---|
| `EU` / `UK` / `AU` | 230 V | 50 Hz |
| `CN` | 220 V | 50 Hz |
| `US` | 120 / 240 V split-phase | 60 Hz |
| `JP` | 100 / 200 V | 50 Hz (east) + 60 Hz (west) |

The **vehicle-axis** bands (high-voltage (HV) pack voltage, 12 V rail) are a fact about the car
model. They live in the profile's [`physical_bands`](../concepts/profiles.md) instead. A profile's
`physical_bands` override has the final say over the `grid_region` preset.

## `devices` — named devices, one per line

Each alias maps to a device with a `host` (IP address or hostname). It can also have its own
`transport`, `port` and `bitrate`. With these keys, a setup with several devices can bind each
device to the transport that suits it. For example, bind a low-latency home local area network
(LAN) device to `slcan-tcp`. Bind a slow cellular or virtual private network (VPN) device to
`wican-ws`, which runs the ISO transport protocol (ISO-TP) on the device side (see
[Remote & cellular links](../concepts/remote-and-cellular.md)).

```yaml
devices:
  home:
    host: "192.168.1.100"
    transport: slcan-tcp     # optional; overrides transport.type for this device
    port: 3333               # optional
  vpn:
    host: "10.0.0.100"
    transport: wican-ws
  clone:
    host: "192.168.0.10"
    transport: elm327-tcp    # generic WiFi ELM327 dongle (no WiCAN)
    port: 35000
default_wican: home
```

Per-device values override the global `transport:` block. An explicit `--transport` or `--wican`
on the command line still wins over both.

Set them from the CLI:

```bash
canair config set devices.home.host 10.0.2.86
canair config set devices.home.transport wican-ws   # validated: slcan-tcp | wican-ws | elm327-tcp
```

!!! note "Legacy `wican_addresses`"
    The old flat `wican_addresses: {alias: host}` form still works when no `devices:` block
    exists. canair **migrates it into `devices:` on the next run**. The migration keeps comments
    and prints a one-line notice. Once a `devices:` block exists, canair ignores
    `wican_addresses`, and setting it prints a warning.

## Auto-fallback across devices

When the selected device is unreachable at connect time, canair tries the other configured devices
instead of failing. This is on by default. A configurable probe timeout sets how long each
candidate has to answer.

```yaml
transport:
  fallback: true                   # default true
  connect_timeout: 5.0             # seconds — per-device liveness probe
  fallback_order: [home, vpn, ap]  # optional; default = selected device, then the rest
  reconnect_max_wait: 60.0         # seconds — bounded mid-session reconnect window
```

- canair always tries the explicitly selected device **first**. That is `--wican X`, else
  `transport.host`, else `default_wican`. `fallback_order` sequences only the other devices.
- `--no-fallback` turns off the fallback for one command. `transport.fallback: false` turns it off
  globally.
- canair skips a `wican-ws` device as a fallback on a `classic` WiCAN, because that device cannot
  use the transport.
- Set the order from the CLI with a comma-separated value:
  `canair config set transport.fallback_order home,vpn,ap`.
- **`canair status` runs the same probe.** It reports the device that a live command would use.
  When the selected device is down but another one answers, it says *ready* and names the device
  it fell back to.
- **canair applies `connect_timeout` once per candidate.** The value trades two risks. A high
  value means canair never calls a reachable device dead. A low value means canair skips a dead
  device quickly. The `5.0` default suits a mobile link. A cellular radio can need a second or two
  to change from idle to connected, before the first connection packet (SYN) leaves. On a LAN,
  lower the value.

### Mid-session reconnect & `--wait`

`canair monitor` recovers a session that drops **mid-run**. It probes the reachable
**same-transport** devices again (raw↔raw, ws↔ws), reconnects, opens the sessions again and
resumes. A `--save` recording continues, and the gap shows in the timestamps.
`transport.reconnect_max_wait` limits this wait to that many seconds (default `60.0`). `--wait`
retries **forever** (press Ctrl-C to stop).

`--wait` also controls the *initial* connect of every live command. The command blocks until the
device comes online. For example, `canair monitor @driving --save --wait` starts to record the
moment the WiCAN appears. Only the initial connect can cross transports, through `fallback_order`.

### Link-health watchdogs

A session can stop working while nothing raises an error. Two cases are common:

- A half-open socket (mobile network address translation (NAT) eviction, VPN re-key) keeps
  accepting writes.
- A desynchronised ELM327 pipe keeps returning well-formed replies, but to the *previous*
  request.

Both look like "the car isn't answering". Without a watchdog, the monitor shows every signal as
`stale` until you restart it by hand.

```yaml
transport:
  ws_ping_interval: 20.0            # seconds — wican-ws keepalive ping (0 = off)
  stale_cycles_before_reconnect: 3  # all-stale poll cycles before reconnecting (0 = off)
```

- **`ws_ping_interval`** turns a silently dead `wican-ws` link into a real `ConnectionError`. The
  mid-session reconnect handles that error. The HTTP server of the WiCAN answers the ping itself,
  so no firmware support is needed. On a flaky mobile link, lower the value. `0` turns the ping
  off.
- **`stale_cycles_before_reconnect`** is the backstop. After this many consecutive poll cycles in
  which *nothing* answered coherently, the monitor reconnects, even though nothing raised an
  error. A negative response code (NRC) counts as an answer. The value is a cycle count, not a
  duration, so it scales with the poll rate.

On the ELM327 transports, canair also realigns the pipe itself before it escalates. A reply that
fails its echo check triggers a drain-and-probe resync. The health line of the monitor tallies it
as `resync`. A rising `stale` or `resync` count shows a marginal link that still copes.

canair also **measures** the link. It adds that allowance to its ISO-TP and per-request budgets.
These are the only latency-related keys you should need to change. See
[Remote & cellular links](../concepts/remote-and-cellular.md) to choose a transport and to see
what `slcan-tcp` costs in data.

### Expected response counts (ELM327 transports)

An ELM327 adapter cannot tell that a multi-frame reply has ended. By default it waits out its
whole `ATST` budget after the last frame. That wait is the largest per-request cost on `wican-ws`
and `elm327-tcp`.

The fix: canair reads the final nibble of a request with an *odd* hex length as the number of
response frames to expect. The adapter then returns the moment the reply is whole. On a WiCAN Pro
this is about **4x faster per read** (~206 ms → ~53 ms), and the timing varies much less.

```yaml
transport:
  expected_responses: true   # default true
```

canair **never guesses the count.** The first read of a request goes out in plain form. canair
learns the count from a reply that already passed its echo and ISO-TP length checks. Asking for
too few frames is the unsafe error. The unread frames stay queued and come back as the answer to
the *next* request. So when a reply does not match, canair takes these steps:

1. It realigns the pipe.
2. It retries in plain form.
3. It stops optimizing that request for the rest of the session.

The retry does not count against the retry budget of the caller. A wrong count can cost latency,
but never a reading.

Leave this key on. Set it to `false` to rule it out while you debug an adapter, or if a clone
rejects the extra nibble. It has no effect on `slcan-tcp`.


## The `transport` block

You choose the transport explicitly. canair never detects it automatically. `--transport` and
`--wican` override `type` and `host` per command. `port` and `bitrate` are config-only.

```yaml
transport:
  type: slcan-tcp      # slcan-tcp (default) | wican-ws (Pro-only) | elm327-tcp (direct ELM327)
  host: 192.168.3.2    # device host/IP (all transports)
  port: 35000          # slcan-tcp (Pro 35000, classic 3333) / elm327-tcp (usually 35000); auto for slcan-tcp if omitted
  bitrate: 500000      # slcan-tcp only; overrides all else (falls back to profile can_bitrate)
```

When you omit `transport`, canair defaults to `slcan-tcp` and takes the host from
`devices`/`default_wican`. A per-device `transport:` (see above) overrides `transport.type` for
that device. See [Architecture](../concepts/architecture.md) for what the transports are.

The **`elm327-tcp`** transport talks to a generic ELM327 adapter over a plain TCP socket. The
adapter can be a WiFi clone or the [ELM327-Emulator](../development/offline-testing.md). This
transport uses no WiCAN. The HTTP-only features do not apply: device mode, `wican` subcommands and
`--reboot`. Reachability is a direct probe of the ELM socket port. The firmware check is opt-in
(`firmware_check: always`).

`canair wican mode set MODE` keeps `transport.type` in step with the device mode:

- Switching to `slcan` sets `slcan-tcp`.
- Switching to `elm327` sets `wican-ws`.

The command prints the `old -> new` change, or says that the two are already aligned. Pass
`--no-transport` to switch the device mode and leave the config unchanged.

### Firmware generation check

Some WiCAN interfaces exist only in firmware 4.x. Before a live command uses one, canair asks the
device which firmware generation it runs. It refuses or warns on firmware v6 (see
[Firmware compatibility](../getting-started/connect-device.md#firmware-compatibility)). The probe
has no side effects. It sends `GET /api/info`, then `GET /check_status` unless the first answer
was v6. `transport.connect_timeout` bounds each request. The probe runs once per device per
command, after the fast reachability check. So an offline device fails without waiting for the
probe.

```yaml
transport:
  firmware_check: true   # default; false skips the probe, always adds elm327-tcp
```

| Value | Probed |
|---|---|
| `true` (default) | the WiCAN transports (`slcan-tcp`, `wican-ws`) and the `canair wican` device commands |
| `always` | those, plus `elm327-tcp` hosts (usually clones with no HTTP server, so off by default) |
| `false` | nothing; live commands neither refuse nor warn |

`canair status` is the diagnostic command. It reports the firmware of a WiCAN transport whatever
this key says. It also notes when live commands would skip the check.

## Example

```yaml
default_profile: my-car

devices:
  ap:
    host: "192.168.80.1"    # WiCAN AP (factory default)
  home:
    host: "192.168.1.100"   # device on your home LAN
default_wican: home

wican_model: classic     # regular / non-Pro WiCAN
```
