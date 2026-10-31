# 2. Discover ECUs

`canair discover` sweeps a range of CAN addresses, sends each a diagnostic
session request (`10 01`), and classifies which ones respond. Crucially, with
`--register` it **writes** the responders into your profile's `ecus/` — so
discovery is how your empty profile gets its first content.

## Sweep and register

```bash
canair discover --register
```

This probes the default address range (`700–7EF`, where most OBD-II ECUs live),
then adds a file under `ecus/` for each newly-found ECU. Preview without writing:

```bash
canair discover --register --dry-run
```

Already-registered ECUs are skipped, so it's safe to re-run as you find more.

## Also read identity in one pass

Add `--identify` to run [identity decoding](03-identity.md) on each live ECU
right after the sweep — pulling part numbers, hardware/software versions, and the
VIN where available:

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

Each newly-registered ECU becomes an `ecus/<name>.yaml` file with a placeholder
name (derived from its address), its TX/RX addresses, and a note recording when
and how it was discovered. You'll rename and enrich these as you learn what each
one is — the [identity](03-identity.md) step is the fastest way to start.

Confirm what landed:

```bash
canair ecu            # list registered ECUs
canair validate all   # everything still valid
```

## Map which buses the gateway reaches

On a car with a central gateway, the OBD-II port sits on its own diagnostic bus and the gateway
forwards requests to the others. A *functional* (broadcast) request, on `0x7DF` (or `0x18DB33F1`
in a 29-bit profile), is answered by every ECU the gateway forwards it to, each on its own
response ID. One such request shows which ECUs, and so which buses, are reachable from the port:

```bash
canair discover --functional
```

It sends `3E 00` (TesterPresent, harmless and answered by both UDS and KWP2000 ECUs), listens for
300 ms (`--window`), and lists every responder grouped by the `can_bus:` your profile gives it.
Responders the profile does not know are flagged, and known ECUs that stayed silent are listed per
bus: a whole bus that stays silent is probably not routed. `--register` and `--identify` work as in
the physical sweep.

- **Allowed requests:** a functional frame reaches every ECU at once, so only these reads can be
  sent with `--request`, and `--unsafe` does not widen the list: `3E00`, `22xxxx` (one DID),
  `1Axx`, `1902xx`, `1800FF00`. Session control, resets, DTC clears and anything that writes or
  actuates are refused.
- **Not every ECU answers everything:** UDS ECUs stay silent for a request they do not support,
  while KWP2000-style ECUs often answer with an NRC. Try a second request such as `--request
  22F18C` (serial number) or `--request 190208` (DTCs) before concluding a bus is unreachable.
- **Repeats:** `--repeat N --interval S` sends the request again, at least 1 s apart, and reports
  how many rounds each ECU answered, which shows a bus that drops out under load.
- **Side effect:** on Hyundai/Kia any functional request also tells ECUs that poll other ECUs
  themselves to stop, as the manufacturer's diagnostic spec requires. That changes what the car
  does, so it is not a purely passive read.
- **Transport:** `slcan-tcp` sees every frame and sends each multi-frame responder its flow
  control on that ECU's own request ID. An ELM327 (`wican-ws`, `elm327-tcp`) is best-effort: the
  adapter handles flow control and decides when to stop listening, and overlapping multi-frame
  replies are where it is weakest.

`--save` records each round as a discover-style capture, one entry per responder.

## No car handy? (starting a profile offline)

`discover` needs a live vehicle. If you're bootstrapping a **blank profile for
contribution** — e.g. seeding one ECU you *know* exists from another model-year —
register it offline with `canair ecu add` (the offline counterpart to
`discover --register`; the write is validated and comment-preserving):

```bash
canair --profile my-car ecu add 7C6 --name CLU \
    --description "Cluster (instrument panel)" \
    --notes "Shares 0x7C6 with another model-year; no PIDs decoded yet."
```

Flags: `--name` (defaults to `Unknown-<TX>`), `--description`, `--id-protocol`
(`UDS`/`KWP2000`), `--notes`, `--overwrite` (replace existing identity fields),
and `--dir` (target a specific `ecus/`). Re-running with the same TX is
idempotent. Everything after this step assumes you'll enrich the ECU with a live
car.

---

Next: **[3. Read identity →](03-identity.md)**
