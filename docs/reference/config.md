# Configuration

canair reads user config from `~/.config/canair/config.yaml`
(`$XDG_CONFIG_HOME/canair/config.yaml`). It's created on first run. View and edit
it from the CLI rather than by hand:

```bash
canair config show          # config file locations + effective settings
canair config get KEY
canair config set KEY VALUE  # dotted keys create nested mappings
canair config unset KEY
canair config edit           # open in $EDITOR
```

!!! note "Authoritative source"
    Every key is documented with inline comments in `config.example.yaml` in the
    repo root. If this page and that file ever disagree, the example file wins —
    it's kept next to the code.

## Keys

| Key | Purpose |
|---|---|
| `default_profile` | Which [profile](../concepts/profiles.md) to use when none is given. Overridden by `--profile` / `CANAIR_PROFILE`. Optional if only one profile is discovered. |
| `profiles_dir` | Extra directory to search for profiles (never committed). |
| `devices` | Named devices for the `--wican` flag: each alias has a `host` and optional per-device `transport`/`port`/`bitrate` (see below). |
| `default_wican` | Which device alias to use by default. |
| `wican_addresses` | Legacy flat `alias: host` map (host-only). Read only when no `devices:` block exists, and **auto-migrated to `devices:` on first run**. |
| `wican_model` | `pro` (default) or `classic`. `classic` makes canair cleanly refuse Pro-only features. |
| `check_for_updates` | `true` (default) or `false`. Disables the automatic once-a-day update check (also disabled by `CANAIR_NO_UPDATE_CHECK`). |
| `grid_region` | Charging-grid region for the physical-value scan: `EU`, `UK`, `US`, `JP`, `CN`, or `AU` (case-insensitive). Sets the mains-voltage / line-frequency bands (see below). |
| `display.byte_notation` | Default byte-index notation for analysis output labels: `wican` (default), `isotp`, `torque`, or `bix`. Overridden per-command by `--notation`. |
| `transport` | Advanced: explicit CAN transport selection (see below). |

## `grid_region` — physical-scan grid bands

The reference-free physical-value scan (`canair hunt --physical` /
`canair investigate`) flags a raw byte whose scaled value lands in a named
physical range. The **mains-voltage and line-frequency** bands depend on *where
the car charges*, not the car — the same EV charges from 230 V / 50 Hz in Berlin
and split-phase 120/240 V / 60 Hz in Denver — so they're set once per location
here (and apply across every profile), rather than in a shared vehicle profile.

Unset assumes **EU (230 V / 50 Hz)**; the first time a physical scan runs, canair
offers to set the region (a one-time prompt on a TTY, a single stderr note when
piped). Presets:

| Region | Nominal | Line frequency |
|---|---|---|
| `EU` / `UK` / `AU` | 230 V | 50 Hz |
| `CN` | 220 V | 50 Hz |
| `US` | 120 / 240 V split-phase | 60 Hz |
| `JP` | 100 / 200 V | 50 Hz (east) + 60 Hz (west) |

The **vehicle-axis** bands (HV pack voltage, 12 V rail) are a fact about the car
model, so they live in the profile's
[`physical_bands`](../concepts/profiles.md) instead. A profile's
`physical_bands` override has final say over the `grid_region` preset.

## `devices` — named devices, one per line

Each alias maps to a device with a `host` (IP or hostname) and, optionally, its
own `transport`, `port`, and `bitrate`. This lets a multi-device setup bind each
device to the transport that suits it — e.g. a low-latency home LAN device to
`slcan-tcp` and a laggy cellular/VPN device to the device-side-ISO-TP `wican-ws`
(see [cellular timeouts](../concepts/architecture.md)).

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

Per-device values override the global `transport:` block; an explicit
`--transport`/`--wican` on the command line still wins over both.

Set them from the CLI:

```bash
canair config set devices.home.host 10.0.2.86
canair config set devices.home.transport wican-ws   # validated: slcan-tcp | wican-ws | elm327-tcp
```

!!! note "Legacy `wican_addresses`"
    The old flat `wican_addresses: {alias: host}` form still works when no
    `devices:` block is present, and is **auto-migrated into `devices:` on the
    next run** (comment-preserving; a one-line notice is printed). Once a
    `devices:` block exists, `wican_addresses` is ignored — setting it warns.

## Auto-fallback across devices

When the selected device is unreachable at connect time, canair tries the other
configured devices instead of failing. It's on by default; a fast, configurable
probe timeout skips a dead device quickly.

```yaml
transport:
  fallback: true                   # default true
  connect_timeout: 2.0             # seconds — per-device liveness probe
  fallback_order: [home, vpn, ap]  # optional; default = selected device, then the rest
  reconnect_max_wait: 6.0          # seconds — bounded mid-session reconnect window
```

- The explicitly selected device (`--wican X`, else `transport.host`, else
  `default_wican`) is always tried **first**; `fallback_order` only sequences the
  rest.
- `--no-fallback` disables it for a single command; `transport.fallback: false`
  disables it globally.
- A `wican-ws` device is skipped as a fallback on a `classic` WiCAN (it can't use
  that transport).
- Set the order from the CLI with a comma-separated value:
  `canair config set transport.fallback_order home,vpn,ap`.

### Mid-session reconnect & `--wait`

`canair monitor` also re-homes a session that drops **mid-run**, rather than
giving up: on a disconnect it re-probes the reachable **same-transport** devices,
reconnects, re-opens any sessions, and resumes — a `--save` recording simply
continues (the gap shows in the timestamps). By default this is bounded to
`transport.reconnect_max_wait` seconds (default `6.0`); `--wait` makes it retry
**forever** (Ctrl-C to stop).

`--wait` also governs the *initial* connect for every live command: it blocks,
retrying indefinitely, and starts as soon as the device comes online — so
`canair monitor @driving --save --wait` waits for the WiCAN, then records the
moment it appears.

Mid-session re-home stays on the connected transport (raw↔raw / ws↔ws); the
initial connect can still cross transports via `fallback_order`.


## The `transport` block

Transport is chosen explicitly, never auto-detected. `type` and `host` are
overridden per-command by `--transport`/`--wican`; `port` and `bitrate` are
config-only.

```yaml
transport:
  type: slcan-tcp      # slcan-tcp (default) | wican-ws (Pro-only) | elm327-tcp (direct ELM327)
  host: 192.168.3.2    # device host/IP (all transports)
  port: 35000          # slcan-tcp (Pro 35000, classic 3333) / elm327-tcp (usually 35000); auto for slcan-tcp if omitted
  bitrate: 500000      # slcan-tcp only; overrides all else (falls back to profile can_bitrate)
```

When `transport` is omitted, canair defaults to `slcan-tcp` using
`devices`/`default_wican` for the host. A per-device `transport:` (see above)
overrides `transport.type` for that device. See
[Architecture](../concepts/architecture.md) for what the transports are.

The **`elm327-tcp`** transport talks to a generic ELM327 adapter (WiFi clones, or
the [ELM327-Emulator](../development/offline-testing.md)) over a plain TCP
socket — no WiCAN, so its HTTP-only affordances (device mode/firmware, `wican`
subcommands) don't apply; reachability is a direct probe of the ELM socket port.

`canair wican mode set MODE` keeps `transport.type` in step with the device's
mode: switching to `slcan` sets `slcan-tcp`, switching to `elm327` sets
`wican-ws` (it prints the `old -> new` change, or says it's already aligned).
Pass `--no-transport` to switch the device mode without touching the config.

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
