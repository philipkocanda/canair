# Connect your dongle

canair reaches the CAN bus *through* a WiCAN dongle — it never talks CAN
directly. This page gets your computer talking to the dongle and confirms it's
usable.

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
canair config set wican_addresses.home 192.168.1.100
canair config set default_wican home
```

Any command takes `--wican home|vpn|<ip>` to pick which device to use.
`config.example.yaml` in the repo documents every key; see also the
[config reference](../reference/config.md).

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

## 5. Confirm it's working

```bash
canair config    # config locations, WiCAN model + addresses, resolved transport
canair status    # what am I talking to, in what mode, is it reachable?
```

If `canair status` reports a reachable device, you're ready. New to the bundled
example car? Go to [Read live data](first-read.md). Building a profile for your
own car? Jump to [Bring your own car](../bring-your-own-car/overview.md).
