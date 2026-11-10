# Troubleshooting

Find your symptom in the list below. Each entry gives the likely cause and the next step.

Start with these two commands. They answer most questions:

```bash
canair status    # what am I talking to, and does it work?
canair logs      # transport drops, timeouts and decode failures
```

## The device is not reachable

**Symptom:** `canair status` exits with code `1`, or a live command cannot connect.

1. Check that the dongle has power. Switch the car to ignition or accessory mode.
2. Check that your computer is on the right network. In access-point mode, join the `WiCAN_xxxx`
   network. The dongle is then at `192.168.80.1`.
3. Check the address with `canair config`. To change it, run
   `canair config set devices.home.host <ip>`.
4. If you have several devices, canair tries the others when the chosen device is down. Run
   `canair status --no-fallback` to check only the chosen device.
5. If the dongle needs time to appear, add `--wait` to the command.

See [Connect your dongle](connect-device.md).

## `canair status` exits with code 2

Exit code `2` means that the configuration cannot work. Common causes:

- You use a `wican-ws` transport with `wican_model: classic`. A classic WiCAN has no
  `wican-ws`. Use `slcan-tcp`.
- You use `wican-ws` on WiCAN firmware v6. v6 has no `/ws` terminal. Use `elm327-tcp`.

The `status` output names the cause and the fix. See
[Firmware compatibility](connect-device.md#firmware-compatibility).

## Every ECU looks dead on firmware v6

**Cause:** The `slcan-tcp` transport on a stock v6 device accepts the connection and then discards
everything.

**Fix:** Either switch to `elm327-tcp` on port 35000, or enable `socket_manager`, `bridge_manager`
and `can_manager` on the device. See
[Firmware compatibility](connect-device.md#firmware-compatibility).

## `discover` finds no ECUs, or too few

1. Switch the car to ignition or accessory mode. Sleeping ECUs do not answer.
2. Run the sweep again with a longer pause: `canair discover --delay 0.5`.
3. Ask one ECU directly: `canair raw ECU:3E00`. An ECU can ignore a broadcast request and still
   answer a direct request.
4. On a car with a central gateway, check which buses you can reach from the port:
   `canair discover --functional`.
5. If you use a generic ELM327 clone, try a WiCAN. Clones often fail on newer vehicles that use
   long multi-frame ISO-TP or 29-bit addressing.

See [Discover ECUs](../bring-your-own-car/02-discover-ecus.md).

## An ECU answers, but a read fails or returns nothing

- **The ECU needs an extended session.** Add `--session` to `read`, `scan` or `identity`.
- **The ECU is asleep.** Add `--wake`.
- **The ECU answers this PID only in another vehicle state.** For example, a body module may answer
  only with the ignition on. Change the state and try again.
- **The ECU answers with a negative response code (NRC).** The NRC tells you why. For example,
  `requestOutOfRange` means that the ECU does not know this PID. `canair bix --annotate PAYLOAD`
  decodes the NRC.

## A read is slow, flaky or truncated

1. Run `canair logs`. Look for dropped frames, stale replies and timeouts.
2. If you use a generic ELM327 clone, the adapter is the likely cause. Try a WiCAN.
3. If the link is slow (VPN or cellular), use `--transport wican-ws` on a WiCAN Pro. It keeps the
   ISO-TP flow control on the car side of the network. See
   [Remote & cellular links](../concepts/remote-and-cellular.md).
4. For a long recording, add `--wait`. The monitor then reconnects after a drop and continues the
   same recording.

In the monitor, a PID that stops answering is polled less often until it answers again. The row
says `slow (not answering)`.

## Another session holds the connection

**Symptom:** A command says that another canair session holds the device connection.

The device serves one connection at a time. Run these commands in order. Stop when the problem is
gone.

```bash
canair lock          # who holds the connection?
canair lock steal    # ask that session to release it, then wait
canair lock kill     # signal a session that does not respond (asks first)
```

A command with `--force` asks the other session to release the connection, and then continues.
canair never kills another process without your confirmation.

## canair asks me to choose a profile or a variant

- If you have more than one profile, canair does not guess. Pass `--profile NAME`, set
  `CANAIR_PROFILE`, or run `canair profile use NAME`.
- If a profile covers several variants of a car, canair does not read or record data until you
  name yours: `canair profile use NAME --variant VARIANT`. Run `canair variants` to see the
  options. `discover` and `identity` work before you choose, because you use them to find out.

See [Profiles](../concepts/profiles.md) and [Variants](../concepts/variants.md).

## My captures or edits disappeared after an update

**Cause:** You wrote to a bundled profile with a bare `canair` that you installed with
`uv tool install`. That copy of the profile is frozen. A reinstall replaces it.

**Fix:** Keep your data outside the snapshot. Either run
`canair config set profiles_dir /path/to/canair/profiles`, or run
`canair profile adopt PROFILE` to take your own copy. See
[Point your writes somewhere durable](install.md#point-your-writes-somewhere-durable).

If a recording session crashed, recover its data with `canair captures uds --recover`.

## canair shows an old version or old behavior

You may have two copies of canair. A bare `canair` runs the installed snapshot. `uv run canair`
runs the code in your checkout. Run `canair --version` to see which copy runs. See
[The two installs can drift out of sync](install.md#the-two-installs-can-drift-out-of-sync).

## A feature is refused as Pro-only

You set `wican_model: classic`, or the device is a classic WiCAN. AutoPID device sync,
`wican mode set` and the `wican-ws` transport need a WiCAN Pro. Everything else works on both
models. See [Pro vs. classic](connect-device.md#4-pro-vs-classic).

## Still stuck?

1. Run `canair status --json` and `canair logs -n 200`.
2. Open a [GitHub issue](https://github.com/philipkocanda/canair/issues) and add the output.
   Remove any VIN first.
