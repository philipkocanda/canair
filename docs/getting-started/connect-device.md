# Connect your dongle

canair reaches the CAN bus *through* an adapter — a WiCAN dongle, or a generic
ELM327 clone — it never talks CAN directly. This page gets your computer talking
to the dongle and confirms it's usable.

## 1. Plug in and power up

Plug the WiCAN into the vehicle's OBD-II port and switch the car to
ignition/accessory (so the ECUs are awake). The dongle powers from the port.

## 2. Get on the same network

Two options:

- **Access-point mode** — the WiCAN broadcasts its own `WiCAN_xxxx` WiFi
  network. Join it; the dongle is reachable at `192.168.80.1` (canair's default
  when no address is configured).
- **Your LAN** — use the WiCAN web UI to put the dongle on your home WiFi. It
  then gets a normal LAN IP you point canair at. This is the more convenient
  setup for repeated sessions.

## 3. Tell canair the address

Edit `~/.config/canair/config.yaml` or use `canair config set`:

```bash
canair config set devices.home.host 192.168.1.100
canair config set default_wican home
```

Any command takes `--wican home|vpn|<ip>` to pick a device. Each device can carry its own
transport (`canair config set devices.vpn.transport wican-ws`), and if the chosen device is
unreachable canair falls back to the others (`--no-fallback` disables this per command).
`canair status` follows the same fallback, so it reports the device a live command would really
use. Add `--wait` to any live command to block until the device comes online; with
`canair monitor @driving --save --wait` it also keeps reconnecting if the link drops. Every key
is documented in the [config reference](../reference/config.md).

## 4. Pro vs. classic

canair supports both the **WiCAN Pro** and the regular **classic** (non-Pro)
WiCAN. The default is `pro`; if you have a classic, tell canair so it cleanly
refuses Pro-only features instead of failing against the device:

```bash
canair config set wican_model classic
```

**Pro-only features:** AutoPID device sync (`wican autopid upload`/`download`/
`diff`), `wican mode set`, and the `wican-ws` WebSocket transport. All the core
reverse-engineering — query, scan, discover, decode, DTCs, sniff, and generating
AutoPID JSON — works on **both** over the default raw-SLCAN transport.

## Firmware compatibility

canair is built and tested against **WiCAN firmware 4.x**, specifically
**4.50 to 4.52**. (4.52 only changes the OBD chip firmware it bundles, to
`V2.3.24`, which hasn't been measured yet.) A 4.x version outside that range
still works, with a one-line warning.

**WiCAN Pro firmware v6** (6.0.0, in beta) is a rewrite rather than an update.
Before using a 4.x-only interface, canair asks the device which generation it
runs — read-only `GET`s, `/api/info` and then (unless that was v6)
`/check_status` — and refuses or warns with the fix instead of failing
obscurely:

| On firmware v6 | What canair does |
|---|---|
| `elm327-tcp` transport | **Works.** v6 serves a plain ELM327 socket on port 35000 by default. The socket is shared: AutoPID (if enabled) takes the chip back after 10 s without a command. canair notes this when `transport.firmware_check` is `always` (`elm327-tcp` hosts aren't probed by default, since they're usually clones) |
| `wican-ws` transport | **Refused.** v6 has no `/ws` terminal; use `elm327-tcp` |
| `slcan-tcp` transport (the default) | **Warns, then tries.** A stock v6 device serves no SLCAN until you enable three settings (below) |
| `sniff --listen-only` | **Refused.** v6 ignores SLCAN's listen-only command; set `can_manager.silent` on the device instead. If the generation can't be confirmed, canair warns that listen-only isn't guaranteed |
| `wican mode show`/`set` | **Refused.** v6 has no protocol mode |
| `wican autopid upload`/`download`/`diff` | **Refused.** v6 changed the AutoPID format and API, which canair can't sync yet. `autopid write` still works. If the generation can't be confirmed, an upload goes ahead with a warning |
| `--reboot` | **Skipped.** v6 resumes AutoPID by itself |

So on v6, point canair at the ELM327 socket:

```bash
canair config set devices.home.transport elm327-tcp
canair config set devices.home.port 35000
canair status                                        # confirm it's usable
```

To use `slcan-tcp` on v6 anyway, enable all three of these in the device's
settings, then submit them (the device reboots):

1. **`socket_manager`**: the `slcan0` server (TCP 3333).
2. **`bridge_manager`**: a bridge from `can` to `slcan0` with translator `slcan`.
3. **`can_manager`**: enabled.

Without the bridge (or with `can_manager` off), the SLCAN port accepts the
connection and then silently discards everything, so every ECU looks dead.

`canair status` shows the generation, version and build it found, and exits `2` when the
selected transport can't work on that firmware. `canair config set transport.firmware_check
false` turns the check off for live commands (`status` still reports it); `always` also checks
`elm327-tcp` hosts. See [the reference](../reference/config.md#firmware-generation-check).

!!! warning "Before upgrading a WiCAN to v6"
    Upgrading from 4.x **erases the device's saved configuration**, stored files
    and logs. Going back means uploading the 4.x image by hand, after which the
    device starts with factory defaults.

## Using a generic ELM327 clone (no WiCAN)

Don't have a WiCAN? A generic **WiFi ELM327 adapter** (Kiwi, vLinker, OBDLink, or
any no-name $10 clone) works over the **`elm327-tcp`** transport — a plain TCP
socket to the dongle's ELM327 terminal, no WiCAN required:

```bash
canair config set devices.clone.host 192.168.0.10   # the dongle's WiFi IP
canair config set devices.clone.transport elm327-tcp
canair config set devices.clone.port 35000           # most WiFi clones use 35000
canair config set default_wican clone
canair status                                        # confirm it's reachable
```

Join the dongle's WiFi network first (many clones host their own access point).
The ELM327 protocol engine is shared with `wican-ws`, so every canair command
works the same — the dongle performs ISO-TP. There's no HTTP config API on a
generic clone, so `canair status` reports transport reachability by probing the
ELM socket directly. (Pointing `elm327-tcp` at a WiCAN running
[firmware v6](#firmware-compatibility)? Set `transport.firmware_check` to `always`
and `status` shows its firmware too.)

!!! warning "Third-party dongles are best-effort"
    The **WiCAN is the recommended and best-tested adapter**. Generic ELM327 clones vary
    widely in firmware quality, and cheap ones often struggle with **newer vehicles**, which
    rely on long multi-frame ISO-TP responses and **extended (29-bit) addressing**. If reads are
    flaky or truncated, or a clone can't reach an ECU, that's usually the adapter, not canair —
    try a WiCAN before filing a bug.

Want to try canair with **no hardware at all**? See
[Offline testing with ELM327-Emulator](../development/offline-testing.md).

## 5. Confirm it's working

```bash
canair config    # config locations, WiCAN model + addresses, resolved transport
canair status    # what am I talking to, in what mode, is it reachable?
```

If `canair status` reports a reachable device, you're ready. New to the bundled
example car? Go to [Read live data](first-read.md). Building a profile for your
own car? Jump to [Bring your own car](../bring-your-own-car/overview.md).
