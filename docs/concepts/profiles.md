# Profiles

A **profile** is a directory bundling everything canair knows about one vehicle.
The tooling is vehicle-agnostic; a profile is where the vehicle-specific
knowledge lives. The repo ships one or more profiles under
[`profiles/`](https://github.com/philipkocanda/canair/tree/main/profiles) — see
[Bundled profiles](../profiles/index.md) for the current list. The most developed
is `profiles/ioniq-2017/` (a 2017 Hyundai Ioniq Electric); see [the Ioniq 2017
profile](../profiles/ioniq-2017.md) for what a mature profile looks like.

## Bundle layout

```
<profile>/
  profile.yaml     # profile-wide settings: car_model, ELM327 init, timeouts, …
  vehicle_states.yaml      # the vehicle's power-state vocabulary (sleep/acc/ready/…)
  ecus/            # ONE FILE PER ECU — the single source of truth
    bms.yaml       #   identity, scan log, DTC meanings, PIDs, parameters, research
    igpm.yaml
    …
  captures/        # recorded UDS payloads, split by date (never hand-edited)
    can/           # (optional) imported raw broadcast-CAN frame logs + index.yaml
  references/      # external reference material (other cars' logs, spreadsheets)
  signals/         # (optional) broadcast signal maps, one <bus>.yaml per CAN bus
  out/             # generated AutoPID JSON (never hand-edited; regenerate)
```

`ecus/` is the heart of it: each `ecus/<name>.yaml` fully describes one ECU — its
identity, the history of what's been probed on it, DTC meanings, its PIDs and
decoded parameters, and a `research:` backlog of what's left to do. Each ECU also
records its `id_protocol` (UDS vs KWP2000) — see
[ECU protocols & PID prefixes](ecu-protocols.md).

The optional `signals/` and `captures/can/` directories belong to the raw-CAN
**broadcast** domain (passively-observed frames and their DBC-style linear signal
maps), which is separate from the diagnostic request/response `captures/`. Both
are absent unless you import broadcast data; `canair validate signals` and
`canair validate can` check them and quietly skip when they don't exist.

## `profile.yaml` settings

`profile.yaml` holds the profile-wide, vehicle-level settings. Only `car_model`
and `init` are required; everything else is optional with a sensible default.

| Field | Type | Meaning |
|---|---|---|
| `car_model` | str | Human description (required). |
| `init` | str | ELM327 AT init string, `;`-separated (required). Applies to the `wican-ws` (ELM327) transport only; the `slcan-tcp` transport ignores it and drives ISO-TP directly. A fresh profile scaffolds `ATSP6;ATS0;ATAL;` (ISO 15765-4 11-bit/500 kbit). |
| `can_bitrate` | int | Vehicle bus speed in bit/s. Set it when the diagnostic bus isn't 500 kbit/s (e.g. `250000`). Precedence: config `transport.bitrate` > this > device config > `500000`. |
| `addressing` | map | CAN diagnostic addressing rule. `mode` selects how the arbitration IDs are formed — `normal_11bit` (default), `normal_29bit` (arbitrary explicit `tx_id`/`rx_id`), `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention used by Ford/VAG/etc.), `normal_extended_11bit` (ISO-TP extended/mixed 11-bit: an 11-bit header plus a per-ECU target-address extension byte in the payload — BMW `0x6F1` / PSA), or `extended_29bit`. `rx_offset` sets the 11-bit response address as `tx_id + rx_offset` (default `0x08`, the Hyundai/Kia convention; `0x80` for XPeng `0x704`→`0x784`; **may be negative**, e.g. PSA `-0x20`). For `normal_fixed_29bit` the response ID is derived by swapping the address bytes, so `rx_offset` doesn't apply. `target_address`/`source_address` are profile-default ISO-TP extension bytes for the extended modes (`source_address` is the tester address, default `0xF1`). A single irregular ECU overrides the mode with a per-ECU `addressing.mode`, the response address with its own `rx_id`, or the flow-control address with `addressing.fc_id` (see ECU fields). |
| `response_timeout_ms` | int | ELM327 response timeout (applied as `ATSTxx`; `--elm-timeout` overrides). Raise it for slow ECUs (the Ioniq needs `614`), lower it to speed up cycles. |
| `multi_did_batching` | bool | Profile default for per-ECU service-22 multi-DID batching. |
| `failure_types` | map | DTC failure-type byte meanings, profile-wide (`{0xNN: "meaning"}`). |
| `quirks` | list | Make-specific behavior toggles the profile opts into (make-neutral profiles omit). Known: `hk_f1xx_minus_one` — Hyundai/Kia identity DIDs answer one less than requested (`22F188` → `62F187`); echo validation tolerates it for F1xx only when this quirk is set. |
| `isotp` | map | Client-side ISO-TP tuning for the `slcan-tcp` transport (see below). |

The `isotp:` block overrides the client-side ISO-TP flow-control / padding /
CAN-FD parameters. The defaults suit most 11-bit / classic-CAN vehicles; override
per this car's needs (e.g. an ECU that pads frames with `0x00` instead of `0xAA`,
or a CAN-FD bus). The accepted keys — `tx_padding`, `blocksize`, `stmin`,
`rx_flowcontrol_timeout`, `rx_consecutive_frame_timeout`, `can_fd`,
`tx_data_length` — and their defaults are defined in
[`canlib/transport/isotp_params.py`](https://github.com/philipkocanda/canair/blob/main/canlib/transport/isotp_params.py).
`canair validate` type-checks every field and rejects an unknown `isotp:` key.

## Editing rules

The bundle has strict edit disciplines that keep it valid and reviewable:

| Path | How you edit it |
|---|---|
| `ecus/` | `canair pids …` and `canair discover --register` — **never by hand** |
| `captures/` | written by `canair … --save` — **never by hand** |
| `out/` | generated by `canair wican autopid write` — **never by hand** |
| `profile.yaml`, `vehicle_states.yaml` | edit directly, then `canair validate` |

`canair pids` edits are surgical, comment-preserving, and schema-validated (they
auto-revert on a validation failure), so you get safe edits without losing the
file's structure or comments.

## Selecting a profile

Precedence (first match wins):

1. `--profile NAME|PATH` (global flag, before the subcommand)
2. `CANAIR_PROFILE` environment variable
3. `default_profile` in your config (set it with `canair profile use NAME`)
4. the single discovered profile, if there's only one

## Where profiles are discovered

canair searches, in order: `--profiles-dir`, `$CANAIR_PROFILES_DIR`,
`profiles_dir` in config, `~/.config/canair/profiles/` (your uncommitted
profiles), then the repo's bundled `profiles/`. **User profiles shadow bundled
ones by name** and are not committed to the repo.

```bash
canair profile               # interactive picker on a TTY (set the default); plain list when piped
canair profile list          # discovered profiles; active one marked
canair profile show [NAME]    # a profile's paths and settings
canair profile use NAME       # set NAME as the default profile
canair profile create NAME --car-model "…" [--set-default]
```

## During development: which `canair` sees your edits

The repo-bundled `profiles/` is resolved **relative to the running canair code**,
not your current directory — so whether your edits to a bundled profile are live
depends on *which copy of canair you run*:

| You run | Runs the code in | Bundled `profiles/` it reads |
|---|---|---|
| `uv run canair` (from the repo root) | your git working tree | the repo's `profiles/` — **edits are live** |
| a bare `canair` (`uv tool install .`) | a frozen snapshot in uv's tool venv | a copy baked in at install time — **edits are not seen** |

**So when contributing to a bundled profile, run `uv run canair` from the repo
root.** A bare `canair` reads the snapshot taken when you last installed it;
edits to `profiles/` in your checkout won't appear until you reinstall
(`uv tool install . --reinstall`). `canair profile list` and `canair status`
warn when you're running the snapshot copy.

There's no config flag that makes a bare `canair` run your checkout's *code* —
that's what `uv run` is for. You *can* point any canair at your checkout's
profile *data* by setting `profiles_dir` (e.g.
`canair config set profiles_dir /path/to/canair/profiles`), the persistent
equivalent of `--profiles-dir`, but a bare `canair` still runs the snapshot code.
