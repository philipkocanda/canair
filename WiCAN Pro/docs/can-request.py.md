#### can-request.py

CLI tool for sending custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket ELM327 terminal mode. Connects to `ws://<ip>/ws`, sends `{"ws_mode": "terminal", "terminal_type": "elm327"}` to enter terminal mode. The firmware handles ISO-TP internally — no Python ISO-TP implementation needed.

```bash
can-request.py                                  # Interactive REPL
can-request.py --param SOC_BMS SOC_DISP         # Query specific parameters
can-request.py --ecu BMS                        # Query all BMS parameters
can-request.py --ecu BMS --pid 2101             # Query BMS PID 2101 only
can-request.py --raw 7E4:2101                   # Raw UDS request with hex dump
can-request.py --scan --tx 7E4 --service 21 --range 01-FF  # Scan PID range
can-request.py --identity --tx 7A0 --session    # Query UDS identity DIDs (part no, dates, versions)
can-request.py --identity --tx 770 --wake       # Identity query with deep-sleep wake
can-request.py --wican vpn --param SOC_BMS      # Use VPN address
can-request.py --verbose --ecu VCU              # Show raw WebSocket traffic
can-request.py --json --param SOC_BMS           # JSON output
can-request.py --raw 770:22BC03 --session       # Extended diagnostic session (10 03)
can-request.py --raw 770:22BC03 --wake          # Wake from deep sleep + session
can-request.py --raw 770:2FBC0103 --wake --hold # IOControl with session held open
```

##### `--multi` flag (multi-ECU pipeline)

Executes a sequence of sub-commands within a single WebSocket session, managing extended diagnostic sessions across multiple ECUs with interleaved TesterPresent keepalives. After the pipeline completes, drops into an interactive REPL with all sessions still active (suppress with `--no-repl`).

```bash
# Wake SKM, query IGPM, drop into REPL
can-request.py --multi "skm-wake acc" "query IGPM BC03 BC06"

# Wake SKM + BCM, raw query charge port, REPL
can-request.py --multi "skm-wake acc" "session BCM --wake" "raw 7A0:22B00E"

# Wake IGPM, query all PIDs, exit (no REPL)
can-request.py --multi "session IGPM --wake" "query IGPM" --no-repl

# Pipeline with explicit sleep between steps
can-request.py --multi "skm-wake acc" "sleep 1" "query BCM B00E" "repl"
```

**Sub-commands:**

| Sub-command                      | Description                                         |
|----------------------------------|-----------------------------------------------------|
| `skm-wake [level]`              | Wake SKM + activate relay (acc/ign1/ign2/start)     |
| `session <ECU\|TX_ID> [--wake]` | Enter extended session on ECU (add to session table) |
| `query <ECU> [PID ...]`         | Query ECU parameters (like `--ecu`/`--param`)       |
| `raw <TX:PID>`                  | Raw UDS request                                      |
| `scan <TX> <SVC> <RANGE> [APP]` | Scan PID range                                       |
| `sleep <seconds>`               | Pause between steps                                  |
| `repl`                          | Drop into interactive REPL (explicit)                |

ECU names are resolved from YAML definitions (e.g., `IGPM`, `BCM`, `SKM`) or can be hex TX IDs (`770`, `7A0`).

**Session management:** The SessionManager tracks all ECUs with active extended sessions and sends TesterPresent (`3E00`) keepalives to stale sessions before each foreground command. In the REPL, a background task sends keepalives every 2s. This allows querying one ECU while keeping sessions alive on others (e.g., keeping SKM ACC relay active while reading BCM charge port data).

**Multi-ECU REPL commands:** `!session <ECU>` (open new session), `!sessions` (list active), `!query <ECU> [PID ...]`, `!raw <TX:PID>`, `!skm [level]`, `!quit`.

##### `--identity` flag

Example:

```sh
./can-request.py --identity --tx 7A0 --wake --wican home
```

Queries standard UDS identity DIDs from an ECU and prints decoded results. Covers the common Hyundai/Kia identity DID set.

Requires `--tx`. Use `--session` for most ECUs; use `--wake` for deep-sleeping ECUs (IGPM). Silently skips unsupported DIDs (NRC responses). Use `!identity` in interactive mode after setting a header with `ATSH`.

Known results (deep sleep, no ACC):
- **BCM (0x7A0):** F18C=`1705310070`, F18B=`2017-05-31`, F100=`180`, F194=`100`, F195=`0880`, F196=`220`, F1A4=`620`
- **IGPM (0x770):** F18B=`2017-06-06`, F100=`20`, F101=`160205`, F110=`(empty)`, F194=`100`, F196=`109`

##### `--session` flag

Enters extended diagnostic session (`10 03`) before sending requests. Required for ECUs like IGPM (0x770) that only respond to `22BCxx` reads and `2FBCxx` IOControl in extended session. Starts a background TesterPresent (`3E 00`) keepalive every 2s to prevent session timeout. Works with all modes (`--raw`, `--param`, `--ecu`, `--scan`).

##### `--wake` flag

Wakes ECUs from deep sleep before entering extended session. Sends `10 01` (default session request) as a CAN wake-up frame — this triggers the CAN transceiver even when the ECU is in deep sleep. The first attempt may return NO DATA while the transceiver powers up; a 0.5s delay allows the ECU to initialize before the `10 03` extended session request. Implies `--session`.

Currently only the IGPM (0x770) is known to wake from deep sleep via this method. Other ECUs (SKM, BMS, VCU, BCM) are fully unpowered and do not respond.

##### `--hold` flag

Keeps the extended diagnostic session alive after the command completes, until Ctrl+C. Useful for IOControl commands (`2FBCxx03`) where the actuator releases as soon as the session drops. Implies `--session`. Only works with `--raw` mode.

**Interactive mode built-in commands:** `!decode` (decode last response), `!hexdump` (hex dump), `!info <ECU>` (show ECU info), `!list` (list ECUs), `!identity` (query identity DIDs for current header ECU), `!reboot` (reboot WiCAN), `!quit`.

**Dependencies:** `websockets`, `pyyaml`. Optional: `requests` (for `--reboot`). Imports `evaluate_expression()` from `decode-captures.py`.

**ALWAYS use `can-request.py` for any CAN/UDS communication with the vehicle. Never write your own Python code to open a WebSocket, send ELM327 commands, or talk to the WiCAN device. If `can-request.py` doesn't support a particular operation, that is intentional — discuss with the user before working around it.**

**IMPORTANT:** Using the WebSocket terminal overrides AutoPID mode. The WiCAN must be rebooted after a terminal session for AutoPID (MQTT data feed to Home Assistant) to resume (though user must be asked first).

**Never reboot the WiCAN without asking the user first.** Always ask whether they are done probing the CAN bus before suggesting or triggering a reboot. They may want to run more commands in the same session. Only use `--reboot` or `!reboot` when the user has confirmed they are finished.

**CRITICAL: Only one connection at a time.** The WiCAN has a single WebSocket endpoint. Never run multiple `can-request.py` commands in parallel — the second connection will either fail or lock up the device, requiring a power cycle to recover. Always wait for one command to finish before starting the next.

Please keep the `captures/YYYY-MM-DD.yaml` files up to date with any new captures. Also note that ALL requests/responses are automatically logged by this tool in the `logs/` directory with timestamped filenames.
