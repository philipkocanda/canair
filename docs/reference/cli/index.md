# CLI reference

Every capability is a `canair <subcommand>`. This page is the map; the
**authoritative, always-current** details for any command are in its `--help`:

```bash
canair --help              # all subcommands
canair <cmd> --help        # one command's flags and examples
```

!!! note
    Rather than duplicate flag lists here (which drift), this reference points at
    `--help` as the source of truth. A generated per-command reference is planned
    — see the docs strategy plan in `plans/`.

## Commands by task

### Reading & talking to the car

| Command | Purpose |
|---|---|
| `canair query` | Send UDS/KWP2000 requests — parameter queries, pipelines, live `--monitor`. |
| `canair discover` | Sweep the bus for responding ECUs; `--register` writes them into the profile. |
| `canair identity` | Decode an ECU's identity DIDs (part no., version, serial, VIN). |
| `canair scan` | Probe DID/routine/iocontrol/session ranges for what an ECU answers. |
| `canair dtc` | Read/clear Diagnostic Trouble Codes. |
| `canair io` | Actuate hardware via IOControl (confirm-first; auto-releases). |
| `canair sniff` | Passive CAN-bus sniffer (raw SLCAN). |
| `canair status` | Snapshot of transport, device mode, reachability. |

### Analyzing captures

| Command | Purpose |
|---|---|
| `canair captures` | Search/diff/step through saved captures. |
| `canair decode` | Value-centric decoding: ranges, `--stats`, `--corr`, `--plot`, `--try`. |
| `canair correlate` | Rank the strongest cross-signal relationships across a drive. |
| `canair hunt` | Find which byte on a PID *is* a known reference signal. |
| `canair investigate` | One-shot per-byte report for an unknown PID. |
| `canair coverage` | Audit PID definitions for decoding gaps. |
| `canair research` | The open reverse-engineering backlog. |

### Editing & managing the profile

| Command | Purpose |
|---|---|
| `canair profile` | Create/list/show profile bundles. |
| `canair pids` | Add/update `ecus/` parameters and research entries (validated). |
| `canair ecu` | Inspect the ECU registry and per-ECU stats (`show`), or register an ECU offline (`add`). |
| `canair validate` | Validate `ecus/`, `profile.yaml`, `captures/` against schemas. |
| `canair wican` | Generate/sync the WiCAN AutoPID profile JSON. |
| `canair config` | View/manage user config. |
| `canair bix` | Byte-index converter (WiCAN ↔ ISO-TP ↔ Torque ↔ bix). |
