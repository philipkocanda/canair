# Per-signal MQTT streaming from the AutoPID profile

Status: **PROPOSED** (2026-10-18).

Lets a profile mark individual signals as "publish every poll to this MQTT topic", so a
high-rate signal such as `BMS:2101:BATTERY_POWER` reaches the broker at its poll rate instead of
at the device's 10 s batch rate.

The motivating use is a **mean power draw for range projection**: the mean of `BATTERY_POWER`
over a parked or driving window needs many samples, and today the broker sees one per ~9 s no
matter how fast the PID is polled.

## What was measured (2026-10-18)

BMS `2101` was changed from `period: 2500` to `period: 1000` in
`profiles/ioniq-2017/ecus/bms.yaml` and uploaded with `wican autopid upload
--expected-responses` (device read back identical to the generated profile; the request went out
as `21019`). Then `wican/ioniq/#` was subscribed for 60 s on the broker named in the device's
`/load_config`:

| Topic | Messages / 60 s | Mean interval |
|---|---|---|
| `wican/ioniq/pids` (raw WiCAN output) | 7 | 9.01 s |
| `wican/ioniq/filtered/battery_power` (Node-RED) | 6–7 | 9–10 s |

Two consecutive `pids` payloads carried `timestamp` values exactly 10 s apart and 231 keys each.
**The faster poll did not change the MQTT rate at all.** Whether the car was really being polled
at 1 s was *not* observed — MQTT cannot show it, and the power value was flat (~0.13 kW).

## Why: the batch timer, not the PID period

Everything below is from the wican-fw `v4.50p` tag (the running firmware), under
`components/autopid/`. Line numbers will move; re-check them before relying on a citation.

- **The `pids` message is a destination, on its own timer.** `/load_auto_pid` reports
  `destinations[0] = {type: Default, destination: wican/ioniq/pids, cycle: 0}`. The publish loop
  (`autopid.c:1126-1160`) sends the whole snapshot (`raw_json`) once `gd->publish_timer` expires.
- **`cycle: 0` means 10 000 ms.** `autopid_config.c:665-667` ("treat 0 as default 10000 ms");
  the same default appears at `autopid_config.c:573` and `autopid.c:1134`.
- **The snapshot holds only the latest value of each signal.** A signal polled ten times in a
  window is published once, with the last reading.
- **The destination is device config, not part of the car profile.** `wican autopid upload`
  writes `/store_car_data` (PIDs and signals); `destinations` and `cycle` live in
  `/load_auto_pid` / `/store_auto_data`, which canair does not touch. That is why the upload had
  no effect on the rate.

### Rejected: shorten the destination `cycle` to 1000 ms

It would work, but it would send the whole 231-key snapshot every second. The Node-RED flow
(`wican/ioniq/pids` → `wican/ioniq/filtered/*`) fans that out to ~231 retained topics per
second, each of which updates a Home Assistant entity. That is a large amount of churn to
obtain one signal's samples.

## The mechanism: per-signal `send_to`

The firmware already supports a per-signal destination, and it publishes on **every successful
poll**, independent of the batch:

- `publish_parameter_mqtt()` (`autopid.c:2983`) is called right after a signal's expression
  evaluates (`autopid.c:4010` for the custom/specific path, `autopid.c:4070` for the other).
- With `destination_type == DEST_MQTT_TOPIC` it publishes
  `{"<NAME>": <value>, "timestamp": <unix seconds>}` to the signal's `destination` topic
  (`autopid.c:2992-3010`, publish at `autopid.c:3033-3044`).
- Both parsers read it from the signal's `send_to` and `type` keys
  (`autopid_config.c:263-264` via `parse_parameter_object`, key names at
  `autopid_config.c:1032` and `:1183`). `type` must be the literal string `MQTT_Topic`
  (`destination_type_from_string`, `autopid_config.c:196-208`); anything else becomes
  `Default` and is never published.

canair currently hard-codes `"type": "Default", "send_to": ""` for every signal
(`canlib/autopid_profile.py::to_device_format`, ~lines 195-204), so this path is unused.

## Design

### 1. Profile field

A signal gains an optional destination topic. Two options; **decide before implementing**:

- **(a) Reuse `mqtt_topic`.** The schema (`canlib/schema/pids_schema.yaml`,
  `optional_param_fields`) and `pids upsert-param --mqtt-topic` already accept it, but it is
  used exactly once (`profiles/ioniq-2017/ecus/bcm.yaml:179`, value `bcm_doors_locked`), is read
  by no code, and holds a *bare name*, not a topic. Repurposing it means migrating that one
  value and redefining the field as a full topic.
- **(b) Add a new field `send_to`** (the firmware's own name), leaving `mqtt_topic` as the
  legacy/inert documentation field. No migration; one more field to explain.

Recommendation: **(b)**. A name that differs from the firmware's is a trap for whoever reads
the C source, and the two fields do mean different things (a bare Home Assistant topic suffix
vs. a full device-published topic).

The value is a full topic, e.g. `wican/ioniq/stream/battery_power`. It must live **outside**
the `wican/ioniq/#` space that Node-RED's `pids` flow filters — or the flow must be told to
ignore it — so the stream is not re-published as a retained `filtered/*` topic by accident.
(`wican/ioniq/stream/…` would still match a `wican/ioniq/#` subscription; pick a prefix that
the existing flow does not consume, and check `flows_current.json` before choosing.)

### 2. Device format

`to_device_format()` already receives `data` and builds a `param_meta` lookup per signal name.
When a signal has `send_to`, emit `"type": "MQTT_Topic"` and `"send_to": <topic>`; otherwise
unchanged (`Default` / `""`).

### 3. Everything that compares profiles must learn the new keys

`wican autopid diff` normalizes both sides to `{name: expression}`
(`canlib/autopid_profile.py`, the normalizer around lines 240-260). It will **not notice** a
changed `send_to`, so a stale device would report "No differences". Extend the comparison (and
the normalized download view) to include `type`/`send_to`, and print a changed destination as
`~ CHANGED`.

### 4. Validation

- `validate pids`: `send_to` must be a non-empty string without wildcards (`#`, `+`) or
  leading/trailing `/`, and — like `name` — must be **unique** across shipped signals, because
  two signals sharing a topic would interleave on one stream.
- It is meaningful only on a shipped signal; warn on `send_to` under a `draft`/`ignored`/
  `static` PID or an unverified signal in a verified-only build.

### 5. CLI

`pids upsert-param ... --send-to TOPIC` (and clearing with an empty value), through the
existing validate-and-revert guard in `canlib/commands/pids.py`. Help text says *signal*, not
parameter. Regenerate `docs/reference/cli/pids.md` with `python3 scripts/gen_cli_reference.py`.

### 6. First use

Set `send_to: wican/ioniq/stream/battery_power` on `BMS:2101:BATTERY_POWER` (topic subject to
the prefix check above), keep BMS `2101` at `period: 1000`, upload with
`--expected-responses`.

## Things to verify, not assume

1. **The poll really runs at 1 s.** The poll budget is shared: the device polls 22
   PIDs serially, and a plain read costs 200–600 ms. Measure the stream's inter-message
   interval; if it is well above the period, the loop is saturated and the period (or the other
   PIDs' periods) needs work, independent of this plan.
2. **`timestamp` is whole seconds.** At ~1 Hz there will be bursts and gaps at 1 s resolution.
   For a mean, stamp on receipt (Node-RED/Home Assistant) or weight by the *received* interval;
   do not trust the payload timestamp for sub-second spacing.
3. **Publishing is not gated by a change filter.** `webhook_data_mode: changed` applies to
   HTTP/webhook destinations; this plan assumes it does not suppress `publish_parameter_mqtt`.
   Confirm by watching a stream where the value is constant (it was flat at 0.13 kW).
4. **QoS and retain.** `mqtt_publish(topic, payload, 0, 0, 1)` — the argument order and the
   meaning of the trailing `1` should be read in `mqtt.c`; if it retains, each topic keeps only
   its last sample, which is harmless for streaming but visible in a broker UI.
5. **MQTT backlog under load.** One extra publish per second is small next to the 10 s batch
   (~231 keys), but measure that the batch cadence does not slip.

## Measuring it

A throwaway subscriber is enough (no new canair dependency): read `mqtt_url`/`mqtt_user`/
`mqtt_pass` from `http://<device>/load_config`, subscribe to the new topic for a fixed window
(≥ 60 s), and report message count and mean/min/max inter-arrival time of *receive* times.
Pass criterion: mean inter-arrival ≈ the PID period (1.0 s), max well under the 10 s batch.
If this becomes a recurring need, promote it to a `canair` subcommand rather than a script.

## Tests

- `tests/test_autopid_profile.py`: a signal with `send_to` → `type: MQTT_Topic` and the topic
  in `to_device_format`; a signal without → `Default`/`""` (regression guard for the current
  output); the diff reports a changed destination.
- Schema/validate tests for the uniqueness, wildcard and non-shipped-signal rules.
- CLI: `upsert-param --send-to` round-trips and reverts on an invalid topic.

## Docs

User-facing, so the docs change in the same PR: `docs/reference/cli/pids.md` (generated), the
profile-field documentation in `docs/`, and `AGENTS.md` under `canair wican` — one line saying
a signal can stream to its own topic, and that the **batch cycle is device config canair does
not manage**. `README.md` stays untouched.

## Out of scope

- Managing the AutoPID **destinations** (`cycle`, topic, auth) from canair. This is the real
  reason the period change was invisible, and worth its own plan, but it is a different feature
  with its own device API (`/store_auto_data`) and risk.
- Changing the Node-RED flow or Home Assistant entities.
- Per-signal rate limiting; a signal publishes at its PID's period.
