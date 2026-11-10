# profile.yaml settings

`profile.yaml` holds the settings that apply to a whole profile, for example the ELM327 init string,
the bus speed and the addressing mode. This page lists every field. For the layout of a profile
directory, see [Profiles](profiles.md).

Only `car_model` and `init` are required, except in a capture layer (`extends`), which needs
neither. Every other field is optional and has a sensible default.

`car_model` (str)
:   Human description (required unless `extends` is set).

`extends` (str)
:   Makes this bundle a capture layer over the named profile (`canair profile overlay`). See
    [Layered profiles](../development/layered-profiles.md).

`aliases` (map)
:   Former names of the profile, which still resolve to it. See [Renamed
    profiles](profiles.md#renamed-profiles).

`also_known_as` (list)
:   Other names owners call the car, for finding it: `[Classic Ioniq, Ioniq EV]`. See [Other names
    for a car](profiles.md#other-names-for-a-car).

`init` (str)
:   ELM327 AT init string, `;`-separated (required unless `extends` is set). Used by the ELM327
    transports (`wican-ws`, `elm327-tcp`) only; `slcan-tcp` ignores it. A fresh profile scaffolds
    `ATSP6;ATS0;ATAL;` (ISO 15765-4, 11-bit, 500 kbit). Leave out `ATSH`/`ATFCSH`/`ATFCSD`/`ATFCSM`
    — canair sets those per ECU from `addressing`, and `canair validate` warns about them. An
    `ATCRA` filter set here stays in force for conventional (`tx + 0x08`) ECUs. The init is sent on
    every connect, so canair keeps it from writing the adapter's EEPROM (rated for about 10,000
    writes): it turns the memory function off (`ATM0`) after every reset and sends `ATSP h` as `ATTP
    h`, which selects the same protocol without saving it. `canair validate` warns about init
    commands that would still write it (`ATPP`, `ATSD`, `ATCV`, `STWBR`, `STSAVCAL`, `ATSP00`).

`can_bitrate` (int)
:   Vehicle bus speed in bit/s, used by the raw `slcan-tcp` transport and `canair sniff` (an ELM327
    takes its speed from the `ATSP` protocol in `init`). Set it when the diagnostic bus isn't 500
    kbit/s (e.g. `250000`). Precedence: the device's config entry or `transport.bitrate` > this >
    the WiCAN's own setting > `500000`.

`addressing` (map)
:   How arbitration IDs are formed: `mode`, `rx_offset`, and the extended-mode address bytes. See
    [Addressing](#addressing) below.

`response_timeout_ms` (int)
:   ELM327 response timeout (applied as `ATSTxx`; `--elm-timeout` overrides). Raise it for slow ECUs
    (the Ioniq needs `614`), lower it to speed up cycles.

`multi_did_batching` (bool)
:   Profile default for per-ECU service-22 multi-DID batching.

`multi_did_max` (int)
:   Profile default cap on how many service-22 DIDs are combined into one multi-DID request (default
    `3`). A per-ECU `multi_did_max` overrides it.

`dtcs` (map)
:   DTC meanings shared by every ECU, in the shape of an ECU's `dtcs:`; an ECU's own entry wins (see
    [DTCs](dtcs.md#where-meanings-come-from)).

`quirks` (list)
:   Make-specific behaviour toggles; make-neutral profiles omit it. `skm_wakeup`: enables the Ioniq
    Smart Key Module relay wake. `nissan_c0_session`: permits the Nissan session `10 C0` on KWP2000
    ECUs (otherwise blocked, see [Safety](safety.md)) and adapts the `scan iocontrol`, `scan
    sessions` and `scan routines` probes to it. `hk_90_session`: permits the Hyundai/Kia session `10
    90`, which their KWP2000 actuator tests run in, on KWP2000 ECUs (`scan sessions` then probes it
    too). `hk_forced_sleep`: lets a guarded profile action send the Hyundai/Kia functional `10 D1`
    sleep request; it is not a session allowance (see
    [Safety](safety.md#profile-actions-guarded-make-specific-requests)). The retired
    `hk_f1xx_minus_one` does nothing: the Hyundai/Kia identity DIDs it let answer one lower were
    ECUs repeating their previous reply. `canair validate` warns about it.

`isotp` (map)
:   Client-side ISO-TP tuning for `slcan-tcp` (see below). `stmin` also sets the ELM327 flow-control
    data (`ATFCSD 30 00 {stmin}`); the block size there is always `0`.

`physical_bands` (map)
:   Vehicle-axis overrides for the reference-free physical-value scan (see below).

`unit_guess_candidates` (list)
:   Make-specific scalings for the slope-to-unit sniffer (`hunt`, `investigate`). Each entry has a
    `factor` and a `label`, and optionally an `offset`, `hint` and `dimension`.


## Editor validation

Every `profile.yaml` starts with a modeline naming its JSON Schema, which canair publishes at
<https://philipkocanda.github.io/canair/schema/profile.json>:

```yaml
# yaml-language-server: $schema=https://philipkocanda.github.io/canair/schema/profile.json
```

Editors built on `yaml-language-server` (VS Code's YAML extension, Zed, Neovim, Helix) then
validate the file and autocomplete its fields. Without the modeline they pick a schema by filename
from SchemaStore, which matches `profiles/*/profile.yaml` to an unrelated tool and reports
`car_model` as not allowed.

The other profile files have a schema too, published next to it:

| File | Schema |
|---|---|
| `vehicle_states.yaml` | `schema/vehicle_states.json` |
| `can_buses.yaml` | `schema/can_buses.json` |
| `groups.yaml` | `schema/groups.json` |
| `variants.yaml` | `schema/variants.json` |
| `signals/<bus>.yaml` | `schema/signals.json` |
| `ecus/<name>.yaml` | `schema/ecus.json` |

`canair validate` checks each file against the same schema, so the editor and canair report the
same problems with the same message. In an ECU file, an unknown field is a warning (an error only
inside `identity:`, `wake:`, `addressing:` and `isotp:`), so a file that still holds an old field
stays editable. `canair validate` also makes the checks a schema cannot, such as references to
other files, expressions and predicate syntax. `canair profile create`, `canair profile overlay`,
`canair ecu add` and the editors that create one of these files write the modeline. Add it by hand
to a file created before that.

## Addressing

`addressing:` tells canair how to build each ECU's request and response IDs.

- **`mode`** — `normal_11bit` (default), `normal_29bit` (explicit `tx_id`/`rx_id` per ECU),
  `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention, common on Ford/VAG; the
  response ID is the byte-swapped request, so `rx_offset` doesn't apply), `normal_extended_11bit`
  (an 11-bit header plus a per-ECU target-address byte in the payload — BMW `0x6F1`, PSA) or
  `extended_29bit`.
- **`rx_offset`** — the 11-bit response is `tx_id + rx_offset`. Default `0x08` (the ISO 15765-4
  convention, `0x7E0`→`0x7E8`); XPeng uses `0x80` (`0x704`→`0x784`); it may be negative (PSA:
  `-0x20`).
- **`target_address` / `source_address`** — profile-default extension bytes for the extended
  modes. `source_address` is the tester address, default `0xF1`.

A single irregular ECU can override these in its own file: `addressing.mode`, its own `rx_id`, or
`addressing.fc_id` for the flow-control address (see ECU fields).

On the ELM327 transports and in the generated AutoPID profile, canair derives each ECU's receive
filter (`ATCRA`) and flow-control ID from this, so an ECU off the `+0x08` rule (Nissan Leaf
`0x79B`→`0x7BB`) or one with a functional-request `fc_id` needs no `init` commands.

Two ECUs sharing one `tx_id` but answering on different IDs can't be told apart by the live ELM327
path, which treats that ID as a broadcast. `canair validate` warns about it; AutoPID still
addresses each PID from its own ECU.

## ISO-TP tuning

The `isotp:` block overrides the client-side ISO-TP flow-control / padding /
CAN-FD parameters. The defaults suit most 11-bit / classic-CAN vehicles; override
per this car's needs (e.g. a car whose ECUs pad their replies with `0x00` instead of `0xAA`,
or a CAN-FD bus). The accepted keys — `tx_padding`, `rx_padding`, `blocksize`, `stmin`,
`rx_flowcontrol_timeout`, `rx_consecutive_frame_timeout`, `wftmax`, `can_fd`,
`tx_data_length` — and their defaults are defined in
[`src/canair/transport/isotp_params.py`](https://github.com/philipkocanda/canair/blob/main/src/canair/transport/isotp_params.py).
`canair validate` type-checks every field and rejects an unknown `isotp:` key.

The two padding keys are different bytes:

- **`tx_padding`** is the byte canair fills its own short frames with (default `0xAA`). Only the
  `slcan-tcp` transport uses it. An ELM327 adapter (`wican-ws`, `elm327-tcp`) pads requests
  itself, normally with `0x00`, and canair leaves that alone: the ELM327 and STN chips set their
  CAN filler byte only through a programmable parameter, which is stored in non-volatile memory.
- **`rx_padding`** is the byte the ECUs pad their replies with, which canair strips when it splits
  a multi-DID reply. It defaults to `tx_padding` when that is set, else `0xAA`.

Hyundai/Kia's diagnostic spec has them differ (requests `0x55`, replies `0xAA`), which is what the
bundled Ioniq profile sets.

### One ECU with its own flow control

Some ECUs need different flow control from the rest of the car. The Ioniq's electric water pump
sends the first frame of a multi-frame reply and then stops, unless the FlowControl frame grants a
BlockSize above `0`. A profile-wide `blocksize` would cost every other ECU a round trip per block,
so an ECU's own file can carry an `isotp:` block that is laid over the profile's for that ECU only:

```yaml
EWP:
  tx_id: 0x746
  isotp:
    blocksize: 8
```

It accepts `blocksize`, `rx_flowcontrol_timeout` and `rx_consecutive_frame_timeout`, the fields
the `slcan-tcp` transport applies per ECU; `canair validate` rejects the others, which stay
profile-wide. Set or clear them with `canair pids set-isotp EWP --blocksize 8` (or
`--blocksize clear`). The ELM327 transports are not affected: the adapter keeps one flow-control
setting for the whole session, with a block size of `0`.

## Physical bands

The `physical_bands:` block tunes the **vehicle axis** of the reference-free
physical-value scan (`canair hunt --physical` / `canair investigate`), which
flags a raw byte whose scaled value lands in a named physical range. It's a
mapping of band key → `[low, high]`: a key matching a built-in
(`hv_pack`, `rail_12v`) **replaces** its range, any other key **adds** a custom
band; unspecified built-ins keep their defaults. The built-in `hv_pack` is a
~400 V EV (`[300, 450]`) — an **800 V architecture** (E-GMP/PPE/Taycan) needs a
wider band, so declare only what differs:

```yaml
physical_bands:
  hv_pack: [450, 850]      # 800 V pack
  hv_pack_peak: [600, 900] # custom band (added)
```

The **grid axis** (mains voltage, line frequency) is a property of *where the
car charges*, not the car, so it lives in user config as
[`grid_region`](../reference/config.md), not here — that way a
shared profile works in any region. Precedence per band: `physical_bands` (final
say) > `grid_region` preset (grid bands only) > built-in default. `canair
validate` checks each override is a 2-element `[low, high]` with `low < high`.

