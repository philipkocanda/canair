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
| `transport` | Advanced: explicit CAN transport selection (see below). |

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

## Example

```yaml
default_profile: my-car

wican_addresses:
  ap: "192.168.80.1"     # WiCAN AP (factory default)
  home: "192.168.1.100"  # device on your home LAN
default_wican: home

wican_model: classic     # regular / non-Pro WiCAN
```
