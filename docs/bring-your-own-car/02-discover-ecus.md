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

## No car handy? (starting a profile offline)

`discover` needs a live vehicle. If you're bootstrapping a **blank profile for
contribution** — e.g. seeding one ECU you *know* exists from another model-year —
register it offline with `canair ecu add` (the offline counterpart to
`discover --register`; the write is validated and comment-preserving, never
hand-editing `ecus/`):

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
