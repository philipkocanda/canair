# profile.yaml settings

`profile.yaml` holds the settings that apply to a whole profile. Examples are the ELM327 init
string, the bus speed and the addressing mode. This page lists every field. For the layout of a
profile directory, see [Profiles](profiles.md).

Only `car_model` and `init` are required. A capture layer (`extends`) needs neither. Every other
field is optional and has a default.

`car_model` (str)
:   Human description. Required unless `extends` is set.

`extends` (str)
:   Makes this bundle a capture layer over the named profile (`canair profile overlay`). See
    [Layered profiles](../development/layered-profiles.md).

`aliases` (map)
:   Former names of the profile. They still resolve to it. See [Renamed
    profiles](profiles.md#renamed-profiles).

`also_known_as` (list)
:   Other names that owners call the car, so that people can find it: `[Classic Ioniq, Ioniq EV]`.
    See [Other names for a car](profiles.md#other-names-for-a-car).

`init` (str)
:   ELM327 AT init string, separated with `;`. Required unless `extends` is set. Only the ELM327
    transports (`wican-ws`, `elm327-tcp`) use it. `slcan-tcp` ignores it.

    A new profile gets `ATSP6;ATS0;ATAL;` (ISO 15765-4, 11-bit, 500 kbit).

    Leave out `ATSH`, `ATFCSH`, `ATFCSD` and `ATFCSM`. canair sets those for each electronic
    control unit (ECU) from `addressing`, and `canair validate` warns about them. An `ATCRA` filter
    that you set here stays in force for conventional (`tx + 0x08`) ECUs.

    canair sends the init on every connect. To protect the EEPROM of the adapter (rated for about
    10,000 writes), canair does two things. It turns the memory function off (`ATM0`) after every
    reset. It sends `ATSP h` as `ATTP h`, which selects the same protocol without saving it.
    `canair validate` warns about init commands that would still write the EEPROM (`ATPP`, `ATSD`,
    `ATCV`, `STWBR`, `STSAVCAL`, `ATSP00`).

`can_bitrate` (int)
:   Vehicle bus speed in bit/s. The raw `slcan-tcp` transport and `canair sniff` use it. An ELM327
    takes its speed from the `ATSP` protocol in `init`. Set it when the diagnostic bus is not 500
    kbit/s (for example, `250000`). Precedence: the device's config entry or `transport.bitrate`,
    then this field, then the WiCAN's own setting, then `500000`.

`addressing` (map)
:   How canair forms arbitration IDs: `mode`, `rx_offset`, and the extended-mode address bytes.
    See [Addressing](#addressing) below.

`response_timeout_ms` (int)
:   ELM327 response timeout. canair applies it as `ATSTxx`, and `--elm-timeout` overrides it.
    Raise it for slow ECUs (the Ioniq needs `614`). Lower it to speed up cycles.

`multi_did_batching` (bool)
:   Profile default for per-ECU service-22 batching of several data identifiers (DIDs).

`multi_did_max` (int)
:   Profile default for the maximum number of service-22 DIDs that canair combines into one
    request (default `3`). A `multi_did_max` in an ECU file overrides it.

`dtcs` (map)
:   Diagnostic trouble code (DTC) meanings that every ECU shares, in the shape of the `dtcs:` of an
    ECU. An entry in an ECU file wins. See [DTCs](dtcs.md#where-meanings-come-from).

`quirks` (list)
:   Toggles for make-specific behavior. Make-neutral profiles omit it. The values are:

    - `skm_wakeup`: enables the Ioniq Smart Key Module relay wake.
    - `nissan_c0_session`: permits the Nissan session `10 C0` on Keyword Protocol 2000 (KWP2000)
      ECUs. Without it, canair blocks this session (see [Safety](safety.md)). The quirk also
      adapts the `scan iocontrol`, `scan sessions` and `scan routines` probes to the session.
    - `hk_90_session`: permits the Hyundai/Kia session `10 90` on KWP2000 ECUs. The KWP2000
      actuator tests of these makes run in this session. `scan sessions` then probes it too.
    - `hk_forced_sleep`: lets a guarded profile action send the Hyundai/Kia functional `10 D1`
      sleep request. It is not a session allowance (see
      [Safety](safety.md#profile-actions-guarded-make-specific-requests)).

    The retired `hk_f1xx_minus_one` does nothing. It let the Hyundai/Kia identity DIDs answer one
    lower. Those answers were ECUs that repeated their previous reply. `canair validate` warns
    about it.

`isotp` (map)
:   Client-side ISO-TP tuning for `slcan-tcp` (see below). `stmin` also sets the ELM327
    flow-control data (`ATFCSD 30 00 {stmin}`). The block size there is always `0`.

`physical_bands` (map)
:   Vehicle-axis overrides for the reference-free physical-value scan (see below).

`unit_guess_candidates` (list)
:   Make-specific scalings for the slope-to-unit sniffer (`hunt`, `investigate`). Each entry has a
    `factor` and a `label`. It can also have an `offset`, a `hint` and a `dimension`.


## Editor validation

Every `profile.yaml` starts with a modeline that names its JSON Schema. canair publishes the schema
at <https://philipkocanda.github.io/canair/schema/profile.json>:

```yaml
# yaml-language-server: $schema=https://philipkocanda.github.io/canair/schema/profile.json
```

Editors built on `yaml-language-server` then validate the file and autocomplete its fields. These
editors include the YAML extension of VS Code, Zed, Neovim and Helix. Without the modeline, they
pick a schema by file name from SchemaStore. SchemaStore matches `profiles/*/profile.yaml` to an
unrelated tool, and the editor reports `car_model` as not allowed.

The other profile files have a schema too. canair publishes it next to the profile schema:

| File | Schema |
|---|---|
| `vehicle_states.yaml` | `schema/vehicle_states.json` |
| `can_buses.yaml` | `schema/can_buses.json` |
| `groups.yaml` | `schema/groups.json` |
| `variants.yaml` | `schema/variants.json` |
| `signals/<bus>.yaml` | `schema/signals.json` |
| `ecus/<name>.yaml` | `schema/ecus.json` |

`canair validate` checks each file against the same schema. The editor and canair therefore report
the same problems with the same message.

In an ECU file, an unknown field is a warning. It is an error only inside `identity:`, `wake:`,
`addressing:` and `isotp:`. A file that still holds an old field stays editable.

`canair validate` also makes the checks that a schema cannot make. Examples are references to other
files, expressions and predicate syntax.

`canair profile create`, `canair profile overlay`, `canair ecu add` and the editors that create one
of these files write the modeline. Add it by hand to a file that you created before that.

## Addressing

`addressing:` tells canair how to build the request and response IDs of each ECU.

- **`mode`** — one of these values:

    - `normal_11bit` (default)
    - `normal_29bit` (explicit `tx_id`/`rx_id` for each ECU)
    - `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention, common on Ford and VAG).
      The response ID is the byte-swapped request, so `rx_offset` does not apply.
    - `normal_extended_11bit` (an 11-bit header plus a target-address byte for each ECU in the
      payload, for example BMW `0x6F1` and PSA)
    - `extended_29bit`
- **`rx_offset`** — the 11-bit response is `tx_id + rx_offset`. The default is `0x08` (the ISO
  15765-4 convention, `0x7E0`→`0x7E8`). XPeng uses `0x80` (`0x704`→`0x784`). The offset may be
  negative (PSA: `-0x20`).
- **`target_address` / `source_address`** — profile-default extension bytes for the extended
  modes. `source_address` is the tester address. Its default is `0xF1`.

One irregular ECU can override these values in its own file. It can set `addressing.mode`, its own
`rx_id`, or `addressing.fc_id` for the flow-control address (see ECU fields).

On the ELM327 transports and in the generated AutoPID profile, canair derives the receive filter
(`ATCRA`) and the flow-control ID of each ECU from this block. An ECU that does not follow the
`+0x08` rule (Nissan Leaf `0x79B`→`0x7BB`) needs no `init` commands. The same is true for an ECU
with a functional-request `fc_id`.

Two ECUs can share one `tx_id` and answer on different IDs. The live ELM327 path cannot tell them
apart, because it treats that ID as a broadcast. `canair validate` warns about this case. AutoPID
still addresses each PID from its own ECU.

## ISO-TP tuning

The `isotp:` block overrides the client-side flow-control, padding and CAN-FD settings of the
ISO-TP (ISO 15765-2) transport protocol. The defaults suit most 11-bit, classic-CAN vehicles.
Override them to fit your car. An example is a car whose ECUs pad their replies with `0x00` instead
of `0xAA`. Another example is a CAN-FD bus.

The accepted keys are `tx_padding`, `rx_padding`, `blocksize`, `stmin`, `rx_flowcontrol_timeout`,
`rx_consecutive_frame_timeout`, `wftmax`, `can_fd` and `tx_data_length`. The file
[`src/canair/transport/isotp_params.py`](https://github.com/philipkocanda/canair/blob/main/src/canair/transport/isotp_params.py)
defines the keys and their defaults. `canair validate` type-checks every field and rejects an
unknown `isotp:` key.

The two padding keys set different bytes:

- **`tx_padding`** is the byte that canair uses to fill its own short frames (default `0xAA`). Only
  the `slcan-tcp` transport uses it. An ELM327 adapter (`wican-ws`, `elm327-tcp`) pads requests
  itself, normally with `0x00`, and canair leaves that alone. The ELM327 and STN chips set their
  CAN filler byte only through a programmable parameter, which they store in non-volatile memory.
- **`rx_padding`** is the byte that the ECUs use to pad their replies. canair strips it when it
  splits a multi-DID reply. It defaults to `tx_padding` when that is set, and otherwise to `0xAA`.

The diagnostic specification of Hyundai/Kia uses different bytes (requests `0x55`, replies
`0xAA`). The bundled Ioniq profile sets these bytes.

### One ECU with its own flow control

Some ECUs need different flow control from the rest of the car. The electric water pump of the
Ioniq sends the first frame of a multi-frame reply and then stops. It continues only if the
FlowControl frame grants a BlockSize above `0`. A profile-wide `blocksize` would cost every other
ECU a round trip per block. For this reason, the file of an ECU can carry an `isotp:` block. canair
lays this block over the profile block for that ECU only:

```yaml
EWP:
  tx_id: 0x746
  isotp:
    blocksize: 8
```

The ECU block accepts `blocksize`, `rx_flowcontrol_timeout` and `rx_consecutive_frame_timeout`.
These are the fields that the `slcan-tcp` transport applies for each ECU. `canair validate` rejects
the other fields, which stay profile-wide.

Set or clear the fields with `canair pids set-isotp EWP --blocksize 8` (or `--blocksize clear`).
The ELM327 transports are not affected. The adapter keeps one flow-control setting for the whole
session, with a block size of `0`.

## Physical bands

The `physical_bands:` block tunes the **vehicle axis** of the reference-free physical-value scan
(`canair hunt --physical` / `canair investigate`). The scan flags a raw byte when its scaled value
lands in a named physical range.

The block is a mapping of band key to `[low, high]`:

- A key that matches a built-in band (`hv_pack`, `rail_12v`) **replaces** the range of that band.
- Any other key **adds** a custom band.
- Built-in bands that you do not name keep their defaults.

The built-in `hv_pack` is for a ~400 V EV (`[300, 450]`). An **800 V architecture**
(E-GMP/PPE/Taycan) needs a wider band. Declare only what differs:

```yaml
physical_bands:
  hv_pack: [450, 850]      # 800 V pack
  hv_pack_peak: [600, 900] # custom band (added)
```

The **grid axis** (mains voltage, line frequency) depends on *where the car charges*. It does not
depend on the car. For this reason, it lives in the user config as
[`grid_region`](../reference/config.md) and not in the profile. A shared profile then works in any
region.

Precedence per band, from highest to lowest:

1. `physical_bands` (final say)
2. `grid_region` preset (grid bands only)
3. built-in default

`canair validate` checks that each override is a 2-element `[low, high]` with `low < high`.
