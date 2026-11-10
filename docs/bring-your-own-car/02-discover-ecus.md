# 2. Discover ECUs

`canair discover` sweeps a range of CAN addresses. It sends each address a diagnostic session
request (`10 01`) and classifies which addresses respond. With `--register`, it **writes** the
responders into the `ecus/` directory of your profile. Discovery is how your empty profile gets its
first content.

## Sweep and register

```bash
canair discover --register
```

The command probes the default address range (`700–7EF`, where most OBD-II ECUs live). It adds a
file under `ecus/` for each new ECU.

The sweep skips `7DF`, the functional (broadcast) ID in this range. A request on `7DF` reaches
every ECU at once. It has its own [functional sweep](#map-which-buses-the-gateway-reaches).

To preview the result without writing:

```bash
canair discover --register --dry-run
```

The command skips ECUs that are already registered. You can run it again when you find more.

## Also read identity in one pass

Add `--identify` to run [identity decoding](03-identity.md) on each live ECU after the sweep. It
reads part numbers, hardware and software versions, and the VIN where available:

```bash
canair discover --register --identify
```

## Useful options

| Flag | Why |
|---|---|
| `--range START-END` | Scan a different address range, e.g. `--range 600-6FF` |
| `--delay SECONDS` | Slow the pacing for finicky/slow buses (default `0.2`) |
| `--dry-run` | With `--register`, preview additions without writing |
| `--functional` | Send one broadcast read instead of sweeping (see [below](#map-which-buses-the-gateway-reaches)) |
| `--save` | Also record the sweep result to `captures/` |
| `--json` | Machine-readable output |

## What gets written

Each new ECU becomes an `ecus/<name>.yaml` file. The file has a placeholder name (derived from the
address of the ECU), the TX and RX addresses, and a note. The note records when and how canair
discovered the ECU. You rename and enrich these files when you learn what each ECU is. The
[identity](03-identity.md) step is the fastest way to start.

Check what canair wrote:

```bash
canair ecu            # list registered ECUs
canair validate all   # everything still valid
```

## Map which buses the gateway reaches

A car with a central gateway has a separate diagnostic bus at the OBD-II port. The gateway
forwards requests to the other buses.

A *functional* (broadcast) request goes to `0x7DF`. In a 29-bit profile, it goes to `0x18DB33F1`.
Every ECU that the gateway forwards the request to answers it, each on its own response ID. One
request therefore shows which ECUs, and so which buses, you can reach from the port:

```bash
canair discover --functional
```

The command sends `3E 00` (TesterPresent). This request is harmless, and both UDS and KWP2000
ECUs answer it. The command listens for 1 s (`--window`). It lists every responder, grouped by the
`can_bus:` that your profile gives it.

- It flags responders that the profile does not know.
- It lists the known ECUs that stayed silent, for each bus. A bus that is completely silent is
  probably not routed.
- `--register` and `--identify` work as in the physical sweep.

Some details:

- **Allowed requests.** A functional frame reaches every ECU at once. For this reason,
  `--request` accepts only these reads, and `--unsafe` does not widen the list:

    - `3E00`
    - `22xxxx` (one DID)
    - `1Axx`
    - `1902xx`
    - `1800FF00`
    - `01xx` (one OBD-II mode 01 PID, the request that a generic scan tool broadcasts)

    canair refuses session control, resets, DTC clears, and anything that writes or actuates.

- **Not every ECU answers every request.** A UDS ECU stays silent for a request that it does not
  support. A KWP2000-style ECU often answers with a negative response code (NRC). Before you
  decide that a bus is unreachable, try a second request. Examples are `--request 22F18C` (serial
  number) and `--request 190208` (DTCs).
- **Repeats.** `--repeat N --interval S` sends the request again, at least 1 s apart. canair
  prints one line for each round. Then it prints one map with one mark per round before each ECU:
  `+` for a positive response, `~` for a negative response, `-` for no reply. The map shows a bus
  that drops out under load.
- **Side effect.** On Hyundai and Kia vehicles, any functional request also tells the ECUs that
  poll other ECUs themselves to stop. The manufacturer's diagnostic specification requires this.
  The request changes what the car does, so it is not a purely passive read.
- **Transport.** `slcan-tcp` sees every frame. It sends the flow control to each multi-frame
  responder on the request ID of that ECU. An ELM327 (`wican-ws`, `elm327-tcp`) is
  best-effort. The adapter handles flow control and decides when to stop listening. It is weakest
  when multi-frame replies overlap. For this request, canair opens the receive filter of the
  adapter to every diagnostic reply ID, because the automatic filter passes only `0x7E8` to
  `0x7EF`. If an adapter refuses, the report names it.
- **Silent is not off.** An ECU can ignore a functional request and still answer a physical
  request. The cluster of the Ioniq does this. Its audio amplifier needs the 1 s window. Before you
  decide that an ECU is asleep or unreachable, ask it directly: `canair raw ECU:3E00`.
- **Slow answers.** An ECU that sent only ResponsePending (`7F xx 78`) by the end of the window
  is listed as `pending`, not as silent.
- **Limits.** canair refuses profiles on CAN-FD and profiles with `normal_29bit` addressing
  (free-form 29-bit reply IDs). `normal_fixed_29bit` (`0x18DAF1xx` replies) works.

`--save` records each round as a discover-style capture, with one entry for each responder.

## No car handy? (starting a profile offline)

`discover` needs a live vehicle. You can start a **blank profile for contribution** without one.
For example, you can seed one ECU that you *know* exists from another model year. Register it
offline with `canair ecu add`. This is the offline counterpart of `discover --register`. canair
validates the write and keeps the comments in the file.

```bash
canair --profile my-car ecu add 7C6 --name CLU \
    --description "Cluster (instrument panel)" \
    --notes "Shares 0x7C6 with another model-year; no PIDs decoded yet."
```

Flags:

- `--name` (default: `Unknown-<TX>`)
- `--description`
- `--id-protocol` (`UDS` or `KWP2000`)
- `--notes`
- `--overwrite` (replace existing identity fields)
- `--dir` (target a specific `ecus/` directory)

If you run the command again with the same TX, nothing changes. The steps after this one assume
that you enrich the ECU with a live car.

---

Next: **[3. Read identity →](03-identity.md)**
