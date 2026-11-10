# Connect your dongle

canair reaches the CAN bus *through* an adapter: a WiCAN dongle or a generic ELM327 clone. It
never talks to the CAN bus directly. This page connects your computer to the dongle and checks that
the dongle works.

## 1. Plug in and power up

1. Plug the WiCAN into the OBD-II port of the vehicle.
2. Switch the car to ignition or accessory mode, so the ECUs are awake.

The dongle takes its power from the port.

## 2. Get on the same network

You have two options:

- **Access-point mode.** The WiCAN broadcasts its own `WiCAN_xxxx` WiFi network. Join this
  network. The dongle is at `192.168.80.1`. This is the address that canair uses when you
  configure none.
- **Your LAN.** Use the WiCAN web UI to connect the dongle to your home WiFi. The dongle gets a
  normal LAN IP address, and you point canair at it. This setup is more convenient for repeated
  sessions.

## 3. Tell canair the address

Edit `~/.config/canair/config.yaml`, or use `canair config set`:

```bash
canair config set devices.home.host 192.168.1.100
canair config set default_wican home
```

Add `--wican home|vpn|<ip>` to any command to pick a device. Each device can have its own
transport:

```bash
canair config set devices.vpn.transport wican-ws
```

If the chosen device is unreachable, canair falls back to the other devices. Add `--no-fallback`
to a command to turn this off. `canair status` follows the same fallback, so it reports the device
that a live command would use.

Add `--wait` to any live command to wait until the device is online. With
`canair monitor @driving --save --wait`, canair also reconnects when the link drops. The
[config reference](../reference/config.md) documents every key.

## 4. Pro vs. classic

canair supports the **WiCAN Pro** and the regular **classic** (non-Pro) WiCAN. The default is
`pro`. If you have a classic, tell canair. Then canair refuses Pro-only features with a clear
message, instead of failing against the device:

```bash
canair config set wican_model classic
```

**Pro-only features:**

- AutoPID device sync (`wican autopid upload`, `download` and `diff`)
- `wican mode set`
- the `wican-ws` WebSocket transport

All the core reverse-engineering features work on **both** models over the default raw-SLCAN
transport. These features are query, scan, discover, decode, DTCs, sniff, and AutoPID JSON
generation.

## 5. Confirm it's working

```bash
canair config    # config locations, WiCAN model + addresses, resolved transport
canair status    # what am I talking to, in what mode, is it reachable?
```

If `canair status` reports a reachable device, you are ready.

- To try the bundled example car, go to [Read live data](first-read.md).
- To build a profile for your own car, go to
  [Bring your own car](../bring-your-own-car/overview.md).

If you use WiCAN firmware v6 or a generic ELM327 adapter, read the two sections below first.

## Firmware compatibility

canair is built and tested against **WiCAN firmware 4.x**, specifically **4.50 to 4.52**. Version
4.52 changes only the OBD chip firmware that it bundles (`V2.3.24`). canair has not measured that
chip firmware yet. A 4.x version outside this range still works, with a one-line warning.

**WiCAN Pro firmware v6** (6.0.0, in beta) is a rewrite, not an update. Before canair uses a
4.x-only interface, it asks the device which generation it runs. It sends read-only `GET`
requests: first `/api/info`, then `/check_status` (unless `/api/info` showed v6). If the interface
cannot work, canair refuses it or warns you, and it gives the fix.

| On firmware v6 | What canair does |
|---|---|
| `elm327-tcp` transport | **Works.** v6 serves a plain ELM327 socket on port 35000 by default. The socket is shared. AutoPID (if enabled) takes the chip back after 10 s without a command. canair notes this when `transport.firmware_check` is `always`. By default, canair does not probe `elm327-tcp` hosts, because they are usually clones |
| `wican-ws` transport | **Refused.** v6 has no `/ws` terminal. Use `elm327-tcp` |
| `slcan-tcp` transport (the default) | **Warns, then tries.** A stock v6 device serves no SLCAN until you enable three settings (below) |
| `sniff --listen-only` | **Refused.** v6 ignores the listen-only command of SLCAN. Set `can_manager.silent` on the device instead. If canair cannot confirm the generation, it warns that listen-only is not guaranteed |
| `wican mode show`/`set` | **Refused.** v6 has no protocol mode |
| `wican autopid upload`/`download`/`diff` | **Refused.** v6 changed the AutoPID format and API, and canair cannot sync them yet. `autopid write` also generates the v6 table (`autopid-v6.json`, for `6.0.0-beta.3` and later). You can load it through the web UI of the device ([Share](../bring-your-own-car/08-share.md)). If canair cannot confirm the generation, an upload goes ahead with a warning |
| `--reboot` | **Skipped.** v6 resumes AutoPID by itself |

On v6, point canair at the ELM327 socket:

```bash
canair config set devices.home.transport elm327-tcp
canair config set devices.home.port 35000
canair status                                        # confirm it's usable
```

To use `slcan-tcp` on v6, enable all three of these settings on the device. Then submit them. The
device reboots.

1. **`socket_manager`**: the `slcan0` server (TCP 3333).
2. **`bridge_manager`**: a bridge from `can` to `slcan0` with the translator `slcan`.
3. **`can_manager`**: enabled.

If the bridge is missing, or `can_manager` is off, the SLCAN port accepts the connection and then
discards everything without a message. Every ECU then looks dead.

`canair status` shows the generation, version and build that it found. It exits with code `2`
when the selected transport cannot work on that firmware.

`canair config set transport.firmware_check false` turns the check off for live commands.
`status` still reports it. The value `always` also checks `elm327-tcp` hosts. See
[the reference](../reference/config.md#firmware-generation-check).

!!! warning "Before upgrading a WiCAN to v6"
    An upgrade from 4.x **erases the saved configuration**, the stored files and the logs of the
    device. To go back, you upload the 4.x image by hand. The device then starts with factory
    defaults.

## Using a generic ELM327 clone (no WiCAN)

If you have no WiCAN, a generic **WiFi ELM327 adapter** works over the **`elm327-tcp`**
transport. Examples are Kiwi, vLinker, OBDLink, or any no-name $10 clone. The transport is a plain
TCP socket to the ELM327 terminal of the dongle. You do not need a WiCAN.

```bash
canair config set devices.clone.host 192.168.0.10   # the dongle's WiFi IP
canair config set devices.clone.transport elm327-tcp
canair config set devices.clone.port 35000           # most WiFi clones use 35000
canair config set default_wican clone
canair status                                        # confirm it's reachable
```

First join the WiFi network of the dongle. Many clones host their own access point.

`elm327-tcp` and `wican-ws` share one ELM327 protocol engine. Every canair command works the same
way, and the dongle does the ISO-TP. A generic clone has no HTTP config API. For this reason,
`canair status` probes the ELM socket directly to report whether the transport is reachable.

To point `elm327-tcp` at a WiCAN with [firmware v6](#firmware-compatibility), set
`transport.firmware_check` to `always`. Then `status` also shows the firmware.

!!! warning "Third-party dongles are best-effort"
    The **WiCAN is the recommended and best-tested adapter**. The firmware quality of generic
    ELM327 clones varies widely. Cheap clones often fail on **newer vehicles**, which use long
    multi-frame ISO-TP responses and **extended (29-bit) addressing**. If reads are flaky or
    truncated, or a clone cannot reach an ECU, the adapter is usually the cause, not canair. Try a
    WiCAN before you file a bug.

To try canair with **no hardware at all**, see
[Offline testing with ELM327-Emulator](../development/offline-testing.md).
