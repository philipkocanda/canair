#### can-request.py

CLI tool for sending custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket ELM327 terminal mode. Connects to `ws://<ip>/ws`, sends `{"ws_mode": "terminal", "terminal_type": "elm327"}` to enter terminal mode. The firmware handles ISO-TP internally — no Python ISO-TP implementation needed.

```bash
python3 can-request.py                                  # Interactive REPL
python3 can-request.py --param SOC_BMS SOC_DISP         # Query specific parameters
python3 can-request.py --ecu BMS                        # Query all BMS parameters
python3 can-request.py --ecu BMS --pid 2101             # Query BMS PID 2101 only
python3 can-request.py --raw 7E4:2101                   # Raw UDS request with hex dump
python3 can-request.py --scan --tx 7E4 --service 21 --range 01-FF  # Scan PID range
python3 can-request.py --identity --tx 7A0 --session    # Query UDS identity DIDs (part no, dates, versions)
python3 can-request.py --identity --tx 770 --wake       # Identity query with deep-sleep wake
python3 can-request.py --wican vpn --param SOC_BMS      # Use VPN address
python3 can-request.py --verbose --ecu VCU              # Show raw WebSocket traffic
python3 can-request.py --json --param SOC_BMS           # JSON output
python3 can-request.py --raw 770:22BC03 --session       # Extended diagnostic session (10 03)
python3 can-request.py --raw 770:22BC03 --wake          # Wake from deep sleep + session
python3 can-request.py --raw 770:2FBC0103 --wake --hold # IOControl with session held open
```

##### `--identity` flag

Queries standard UDS identity DIDs from an ECU and prints decoded results. Covers the common Hyundai/Kia identity DID set:

| DID  | Field                    | Format    |
|------|--------------------------|-----------|
| F190 | VIN                      | ASCII     |
| F188 | ECU Part Number          | ASCII     |
| F18C | ECU Serial / Cal ID      | ASCII     |
| F18B | Manufacture Date         | BCD date  |
| F18D | ECU Manufacturing Date   | BCD date  |
| F191 | HW Version Number        | ASCII     |
| F100 | Boot SW ID               | ASCII     |
| F101 | App SW ID                | ASCII     |
| F110 | ECU Identification       | ASCII     |
| F17E | SW Install Date          | BCD date  |
| F18A | System Supplier ID       | ASCII     |
| F192 | Supplier HW Number       | ASCII     |
| F193 | Supplier HW Version      | ASCII     |
| F194 | Supplier SW Number       | ASCII     |
| F195 | Supplier SW Version      | ASCII     |
| F196 | Exhaust Regulation / SW  | ASCII     |
| F197 | System / Engine Name     | ASCII     |
| F1A0 | Diagnostic Address       | hex       |
| F1A2 | HW Version               | ASCII     |
| F1A4 | HW Part 2                | ASCII     |

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

**IMPORTANT:** Using the WebSocket terminal overrides AutoPID mode. The WiCAN must be rebooted after a terminal session for AutoPID (MQTT data feed to Home Assistant) to resume.

**CRITICAL: Only one connection at a time.** The WiCAN has a single WebSocket endpoint. Never run multiple `can-request.py` commands in parallel — the second connection will either fail or lock up the device, requiring a power cycle to recover. Always wait for one command to finish before starting the next.

**Never reboot the WiCAN without asking the user first.** Always ask whether they are done probing the CAN bus before suggesting or triggering a reboot. They may want to run more commands in the same session. Only use `--reboot` or `!reboot` when the user has confirmed they are finished.

Please keep the `captures.yaml` file up to date with any new captures. Also note that ALL requests/responses are automatically logged by this tool (see logs directory).
