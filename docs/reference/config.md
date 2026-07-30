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
| `wican_addresses` | Named device addresses for the `--wican` flag (IPs or hostnames). |
| `default_wican` | Which `wican_addresses` alias to use by default. |
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

## The `transport` block

Transport is chosen explicitly, never auto-detected. `type` and `host` are
overridden per-command by `--transport`/`--wican`; `port` and `bitrate` are
config-only.

```yaml
transport:
  type: slcan-tcp      # slcan-tcp (default) | wican-ws (Pro-only)
  host: 192.168.3.2    # device host/IP (both transports)
  port: 35000          # slcan-tcp only (Pro 35000, classic 3333); auto if omitted
  bitrate: 500000      # slcan-tcp only; defaults to the profile's can_datarate
```

When `transport` is omitted, canair defaults to `slcan-tcp` using
`wican_addresses`/`default_wican` for the host. See
[Architecture](../concepts/architecture.md) for what the transports are.

`canair wican mode set MODE` keeps `transport.type` in step with the device's
mode: switching to `slcan` sets `slcan-tcp`, switching to `elm327` sets
`wican-ws` (it prints the `old -> new` change, or says it's already aligned).
Pass `--no-transport` to switch the device mode without touching the config.

## Example

```yaml
default_profile: my-car

wican_addresses:
  ap: "192.168.80.1"     # WiCAN AP (factory default)
  home: "192.168.1.100"  # device on your home LAN
default_wican: home

wican_model: classic     # regular / non-Pro WiCAN
```
