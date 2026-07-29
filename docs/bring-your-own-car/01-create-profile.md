# 1. Create a profile

A **profile** is a directory bundling everything canair knows about one vehicle:
its ECUs, PID/parameter definitions, captured payloads, and settings. See
[Profiles](../concepts/profiles.md) for the full layout.

## Scaffold it

```bash
canair profile create my-car --car-model "VW e-Golf 2019" --set-default
```

- `my-car` — the profile name (used as the directory name).
- `--car-model` — a human description; stored in `profile.yaml`.
- `--set-default` — makes this the profile all commands use by default (writes
  `default_profile` to your config). Without it, select the profile per-command
  with `canair --profile my-car …` or set a default later.

By default the bundle is created under `~/.config/canair/profiles/my-car/` — a
*user* location that isn't committed to any repo. Use `--path DIR` to put it
elsewhere.

!!! tip "Contributing a profile back?"
    If you intend to share the profile, create it **inside the canair repo's
    bundled `profiles/` directory** so it's tracked in git:

    ```bash
    canair profile create ioniq-5-2022 --car-model "Hyundai Ioniq 5 2022" \
        --path profiles/ioniq-5-2022
    ```

    A profile placed there is still discoverable by name (`--profile
    ioniq-5-2022`), and it's where you'd open a pull request from.

    Once you have **more than one** profile and no `default_profile` set, every
    command needs `--profile NAME` (or `CANAIR_PROFILE=NAME`) — canair won't
    guess. Set a default with `canair profile use NAME` (an alias for `canair
    config set default_profile NAME`) if you'll be working on one profile for a
    while.

## What you get

The command scaffolds a valid, empty bundle:

```
my-car/
  profile.yaml     # car_model + ELM327 init string
  vehicle_states.yaml      # starter vehicle power-state vocabulary (sleep/acc/ready/…)
  ecus/            # per-ECU definitions — empty, populated in the next steps
  captures/        # recorded payloads — empty
  out/             # generated AutoPID JSON — empty
```

`profile.yaml` holds `car_model` and an `init` string (the ELM327 initialization
sent to the dongle; the default `ATSP6;ATS0;ATAL;` suits most cars — ISO 15765-4
11-bit/500 kbit). If your car's diagnostic bus runs at a different speed, its
ECUs answer at a non-standard response address, uses 29-bit diagnostic addressing
(`0x18DA{target}{tester}`, common on Ford/VAG/etc. — set
`addressing: {mode: normal_fixed_29bit}`), or they need different ISO-TP tuning,
set `can_bitrate:` / `addressing:` / `isotp:` in `profile.yaml` — see
[Profiles → `profile.yaml` settings](../concepts/profiles.md#profileyaml-settings).
(For example, XPeng answers at `TX + 0x80`, so its profile sets
`addressing: {rx_offset: 0x80}` — this offset may also be negative (PSA/Stellantis
use `-0x20`); a single irregular ECU instead gets an `rx_id` or per-ECU
`addressing: {mode: …}` in its `ecus/*.yaml`, set via
`canair pids set-addressing`. Makes that carry a per-module target-address byte
inside the ISO-TP payload (BMW `0x6F1` / PSA) use
`addressing: {mode: normal_extended_11bit}` + a per-ECU `target_address`; makes
that request on a functional broadcast id but answer physically (some
Renault/Mitsubishi) use `normal_29bit` with an explicit `rx_id` and an
`addressing: {fc_id: …}` flow-control override. Non-`0x18` 29-bit priorities
(GM `0x14…`, VW `0x17…`, Volvo `0x1D…`) are handled by `normal_29bit` with
explicit `tx_id`/`rx_id`.)
`vehicle_states.yaml` starts with a generic power-state vocabulary you'll refine
later — see [Captures & states](../concepts/captures-and-states.md).

## Confirm it's active

```bash
canair profile list          # your profiles; the active/default is marked
canair profile show my-car   # this profile's paths and settings
canair validate all          # the fresh bundle should validate clean
```

`ecus/` is empty for now — that's expected. The next step fills it by sweeping
the bus.

---

Next: **[2. Discover ECUs →](02-discover-ecus.md)**
