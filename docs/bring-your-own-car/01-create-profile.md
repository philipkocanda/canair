# 1. Create a profile

A **profile** is a directory that bundles everything canair knows about one vehicle: its ECUs, its
PID and signal definitions, its captured payloads, and its settings. See
[Profiles](../concepts/profiles.md) for the full layout.

## Scaffold it

```bash
canair profile create my-car --car-model "VW e-Golf 2019" --set-default
```

- `my-car` is the profile name. canair uses it as the directory name. Any name works for your own
  car. If you plan to share the profile, follow the
  [naming convention](../concepts/profiles.md#naming-a-profile): `make-model-code-drivetrain`
  (like `hyundai-ioniq-ae-ev`), and never a year.
- `--car-model` is a description for people. canair stores it in `profile.yaml`.
- `--set-default` makes this profile the default for all commands. It writes `default_profile` to
  your config. Without it, select the profile for each command with `canair --profile my-car …`.
  You can also set a default later.

By default, canair creates the bundle in `~/.config/canair/profiles/my-car/`. This is a *user*
location, and no repository tracks it. Use `--path DIR` to put the bundle somewhere else.

!!! tip "Contributing a profile back?"
    To share the profile, create it **inside the bundled `profiles/` directory of the canair
    repository**. Then git tracks it:

    ```bash
    canair profile create hyundai-ioniq-5-ne-ev --car-model "Hyundai Ioniq 5 (NE)" \
        --path profiles/hyundai-ioniq-5-ne-ev
    canair config set profiles_dir /path/to/canair/profiles
    ```

    canair still finds a profile in that directory by name (`--profile hyundai-ioniq-5-ne-ev`).
    You also open your pull request from there. The `profiles_dir` setting does two things. It
    lets a globally installed `canair` find the profile. It also keeps everything that you record
    inside the checkout. Without it, canair records into the install snapshot, and a reinstall
    erases that data (see
    [Profiles → Where your writes land](../concepts/profiles.md#where-your-writes-land)).

    If you have **more than one** profile and no `default_profile`, every command needs
    `--profile NAME` (or `CANAIR_PROFILE=NAME`). canair does not guess. If you work on one
    profile for a while, set a default with `canair profile use NAME`. This is an alias for
    `canair config set default_profile NAME`.

!!! tip "Starting from a car that's already bundled?"
    If canair already ships a profile for a *related* car, do not scaffold a blank profile. Take
    a writable copy and edit it:

    ```bash
    canair profile adopt hyundai-ioniq-ae-ev --set-default
    ```

    The copy goes to `~/.config/canair/profiles/hyundai-ioniq-ae-ev/`. It shadows the bundled
    profile of the same name. The copy stops tracking upstream. To *contribute back* to that
    profile, set `profiles_dir` to your clone instead.

    If the bundled profile already fits your car and you only want to **record against it**, take
    a layer and not a copy. `canair profile overlay hyundai-ioniq-ae-ev` keeps the definitions
    tracking upstream. Your captures go to your own directory. See
    [Layering your captures](../concepts/profiles.md#layering-your-captures-over-someone-elses-definitions).

## What you get

The command creates a valid, empty bundle:

```
my-car/
  profile.yaml         # car_model + ELM327 init string, optional settings commented
  vehicle_states.yaml  # starter vehicle power-state vocabulary (SLEEP/ACC/RUN/…)
  can_buses.yaml       # CAN bus segment vocabulary — only the gateway's ALL to start
  groups.yaml          # named selector groups (@charging, …) — empty
  variants.yaml        # the car's variants (battery, facelift, market) — empty
  ecus/                # per-ECU definitions — empty, populated in the next steps
  captures/            # recorded payloads — empty
  out/                 # generated AutoPID JSON — empty
```

The header of each file says what the file holds and which `canair` command edits it.

`profile.yaml` holds `car_model` and an `init` string. The `init` string is the ELM327
initialization that canair sends to the dongle. The default, `ATSP6;ATS0;ATAL;`, suits most cars
(ISO 15765-4, 11-bit, 500 kbit). You can usually leave it as it is.

Change the settings in these cases:

- Your car uses a different bitrate, non-standard response addresses, or 29-bit addressing, or it
  needs ISO-TP tuning. Set `can_bitrate:`, `addressing:` or `isotp:`. See
  [`profile.yaml` settings](../concepts/profile-settings.md).
- Your car uses a different bitrate or 29-bit IDs. Also set the matching ELM327 protocol in
  `init`: `ATSP7` (29-bit, 500 kbit), `ATSP8` (11-bit, 250 kbit) or `ATSP9` (29-bit, 250 kbit).
- You port a WiCAN community profile. canair derives the header, the receive filter and the flow
  control of each ECU from `addressing`. Leave the `ATSH`, `ATCRA`, `ATFCSH`, `ATFCSD` and
  `ATFCSM` commands out of `init`.

`vehicle_states.yaml` starts with a generic power-state vocabulary. You refine it later. See
[Vehicle states](../concepts/vehicle-states.md).

## Confirm it's active

```bash
canair profile list          # your profiles; the active/default is marked
canair profile show my-car   # this profile's paths and settings
canair validate all          # the fresh bundle should validate clean
```

`ecus/` is empty. This is correct. The next step fills it by sweeping the bus.

---

Next: **[2. Discover ECUs →](02-discover-ecus.md)**
