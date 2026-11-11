# Firmware modes, the WebSocket and HTTP API, sleep and power

Citations here are relative to the basis table in [`SKILL.md`](SKILL.md). Re-check that
basis before you trust a line number.

## Protocol modes

`#define`s, not an enum, at `wican-fw/main/config_server.h:51-55`; string mapping in
`config_server_protocol()` (`wican-fw/main/config_server.c:521-544`):

| config string | constant | inbound parse | bus route on Pro |
|---|---|---|---|
| `slcan` | `SLCAN` 0 | `slcan_parse_str` | ESP32 TWAI |
| `realdash66` | `REALDASH` 1 | `real_dash_parse_66` | ESP32 TWAI |
| `savvycan` | `SAVVYCAN` 2 | `gvret_parse` | ESP32 TWAI |
| `elm327` | `OBD_ELM327` 3 | `elm327_process_cmd` | **MIC3624** |
| `auto_pid` | `AUTO_PID` 4 | `elm327_process_cmd` + `autopid_init` | **MIC3624** |

Anything unrecognised falls back to `OBD_ELM327` (`wican-fw/main/config_server.c:543`). Default is
`"protocol":"elm327"` (`:232`). There is **no MQTT mode** — MQTT is an orthogonal `mqtt_en` flag.
Per-location keys `home_protocol`/`drive_protocol` also exist (`:229-230`).

### Mutual exclusions — each one is a real interaction bug source

1. **ELM327 WebSocket terminal ⟂ AutoPID.** Entering `terminal_type: elm327` **pauses AutoPID**
   (clears `DEV_AUTOPID_ENABLED_BIT` = `BIT11`, `wican-fw/main/dev_status.h:48`) and restores it on
   exit — `wican-fw/components/ws_server/ws_router.c:180-184`. Open *and* close handlers reset to
   monitor+console (`:266-277`), so an unclean disconnect cannot leave AutoPID permanently paused.
   **This is why canair's `--reboot` flag exists to restore AutoPID** — and why it is often
   unnecessary.
2. **AutoPID ⟂ an external ELM327 app.** An inbound ELM327 request clears
   `DEV_AUTOPID_ELM327_APP_BIT` and arms a **10-second inactivity timer** that re-sets it
   (`wican-fw/main/main.c:328-329`). A canair session polling faster than 10 s therefore holds
   AutoPID off continuously; a slower one lets it interleave.
3. **BLE ⟂ ELM327/AutoPID broadcast forwarding** (`wican-fw/main/main.c:449`).
4. **SmartConnect ⟂ `slcan`/`realdash66`/`savvycan`** — under SmartConnect the effective protocol is
   forced to `AUTO_PID` or `OBD_ELM327` (`wican-fw/main/main.c:847-858`), silently making the raw
   modes unreachable.
5. **Monitor-mode WS streaming ⟂ terminal mode** (`wican-fw/main/main.c:409`).

## WebSocket API (`/ws`)

Registered at `wican-fw/components/ws_server/ws_server.c:199-206` (`.is_websocket = true`,
`.handle_ws_control_frames = true`), started from `wican-fw/main/config_server.c:3774`.

**Only one connection slot** — `s_ctx` is a single static struct
(`wican-fw/components/ws_server/ws_server.c:44`), so a second client **overwrites the first's fd**.
Two concurrent canair sessions on `wican-ws` do not fail cleanly; they corrupt each other. The
device connection mutex (`canair lock`) is what prevents this, and it is load-bearing, not a
nicety.

Routing rule, `wican-fw/components/ws_server/ws_router.c:284-362`:

- **`payload[0] != '{'` → the router declines and the raw bytes go to the protocol stack**
  (`:291-294`). That is the entire mechanism separating control JSON from ELM327 traffic. It also
  means a payload that merely *looks* like JSON can never reach the ELM327 chip.
- `ws_mode` has exactly **two** values (`:78-91`): `monitor` (0, default) and `terminal` (1);
  `terminal_type` is `console` (0, default) or `elm327` (1). Anything else logs
  `Unknown ws_mode` and replies `{"type":"ws_mode","ok":false}` (`:342-347`).
- canair's handshake `{"ws_mode":"terminal","terminal_type":"elm327"}` matches `:307`+.
- Once in terminal mode, a JSON frame with **no** `ws_mode` and a `"cmd"` string is executed
  (`:350-358`).

Device→client framing: terminal output is **always** `{"type":"term_out","data":"…"}`
(`ws_router_send_term_out`, `:116-133`), sent as `HTTPD_WS_TYPE_TEXT`. Command execution:

```c
elm327_run_command(tmp, (uint32_t)n, 2000, NULL, ws_elm327_output_cb, false, 0);
```

`wican-fw/components/ws_server/ws_router.c:244` — **timeout hardcoded 2000 ms**,
`stop_after_first_frame = false`, `expected_frame_id = 0`. Commands are copied into `char tmp[256]`
with `strnlen(cmd, sizeof(tmp) - 2)` (`:235-244`), so they are **silently truncated at 254 bytes**,
and a CR is appended only if absent (`:239-243`).

Two behaviours worth knowing before designing a batching or pipelining scheme:

- `elm327_run_command` returns on the **first** prompt
  (`wican-fw/main/elm327.c:2659-2661`, `:2706-2708`, `:2772-2774`), so **multiple CR-separated
  commands in one frame do not work** — replies after the first are destroyed by the next command's
  `uart_flush_input`.
- On mutex contention the router replies the literal text `"Terminal busy\n"` and returns `ESP_OK`
  (`wican-fw/components/ws_server/ws_router.c:204-206`). **canair could parse that as a UDS
  response.** Check `classify_response` in `src/canair/uds/response.py` against that exact string.

Monitor mode streams **SLCAN ASCII** built by `slcan_parse_frame` from `can_receive()`
(`wican-fw/main/main.c:403-413`) — so on Pro under `auto_pid`/`elm327` it yields **nothing**,
because TWAI is off. Conversely, when a WS client is connected, inbound WS bytes are parsed as SLCAN
**regardless of the configured protocol** (`wican-fw/main/main.c:278-284`), coexisting with the
ELM327 branch rather than replacing it.

A second WebSocket, `/obd_logger_ws`, exists at
`wican-fw/components/obd_logger/obd_logger_iface.c:86-92`; its message format has not been examined.

## HTTP API

One `esp_http_server`; core handlers registered at `wican-fw/main/config_server.c:3490-3515`,
component handlers at `:3683-3688`, and the `/*` catch-all **deliberately last** (`:3690`, `:3716`)
to avoid shadowing (see the comment at `:3516`).

The ones canair uses or should care about:

| URI | Method | Purpose |
|---|---|---|
| `/check_status` | GET | status JSON (`check_status_handler` `:1943-1953`) |
| `/load_config` | GET | config get |
| `/store_config` | POST | config set (`protocol` required, 2–64 chars, `:2635-2645`) |
| `/system_commands` | POST | only `reboot`, `force_update_obd`, `set_rtc_time` (`:1331`+) |
| `/system_reboot` | POST | reboot |
| `/autopid_data` | GET | live AutoPID values |
| `/upload/ota.bin` | POST | ESP32 OTA (`esp_ota_*`, `:1964-2005`) |

Others: `/`, `/logo.svg`, `/store_canflt`, `/load_canflt`, `/store_auto_data`, `/load_auto_pid`,
`/load_auto_pid_car_data`, `/upload/car_data.json`, `/load_car_config`, `/api/destinations_stats`,
`/store_car_data`, `/scan_available_pids`, `/std_pid_info`, plus component routes `/ws`,
`/obd_logger_ws`, `/download_db`, `/obd_logs*`, `/cert_manager*`, `/vpn*`,
`/autopid/test_pid`, `/autopid/test_can_filter`, `/api/webhook`, `/restart_tracker*`.

`/check_status` fields include `fw_version`, `hw_version`, `git_version`, `protocol`, `can_datarate`
(live, from `can_get_bitrate()`, `:1789`), `batt_voltage`, `sleep_status`, `sleep_volt`,
`wakeup_volt`, **`obd_chip_status`** (`"Sleep"` `:1827` / `"Ready"` `:1831`), `uptime`, `device_id`,
`ecu_status`, `vpn_status`. Safe mode runs a **separate** server with `/`, `/upload_firmware`,
`/factory_reset` (`wican-fw/main/safemode.c:223-248`).

## Sleep and power

Completely different per generation — `wican-fw/main/sleep_mode.c:63` (`!= WICAN_PRO`) vs `:587`
(`== WICAN_PRO`).

**Pro is a soft sleep state machine, not ESP32 deep sleep.** States
`STATE_NORMAL / LOW_VOLTAGE / SLEEPING / WAKE_PENDING` (`wican-fw/main/sleep_mode.h:32-37`), driven
by `light_sleep_task` (`wican-fw/main/sleep_mode.c:837`+), voltage read every 3 s from ADC1 ch3
(`:918-919`), scaled `avg*11/1000 + 0.1` and rounded to 0.1 V (`:772-779`).

Two traps:

- **`wakeup_volt` from config is ignored by the ESP32 path** — it uses `sleep_voltage + 0.1f`
  (`wican-fw/main/sleep_mode.c:865`), with the config call commented out at `:861-864`. The config
  value is used only to program the MIC3624 (below). Fallback `sleep_volt` is **13.1 V** on the
  ESP32 path (`:856-859`) but **13.2 V** on the chip path (`wican-fw/main/obd.c:250-268`), with
  `wakeup_volt` fallback 13.5 V there.
- **`enter_deep_sleep()` is dead code** (`wican-fw/main/sleep_mode.c:798-835`) — no callers
  anywhere. Had it been wired, wake would have been EXT0 on `OBD_READY_PIN`. Wake is instead always
  a **full ESP32 restart** (`:1007-1014`), which is why a woken device has zero uptime and a fresh
  lock state.

Wake on Pro: voltage ≥ `sleep_volt + 0.1` held 1 s → restart; or `periodic_wakeup` +
`wakeup_interval` expiry when voltage > `CRITICAL_VOLTAGE 11.90f` (`:991-998`, `:613`).

**The chip sleeps on its own rules too**, programmed at boot by `obd_init()`
(`wican-fw/main/obd.c:362-387`, reading `STSLCS\r`, reprogramming at `:293-317`):
`STSLVLW >13.50, 1` (wake), `STSLVLS <13.20, N` (sleep), `STSLVl off,off`, `STSLU off, off`,
`STSLUIT 1200`, `ATZ`. Voltage is read with `ATRV` (`wican-fw/main/obd.c:342-360`).
`STSLEEP0\r` at `wican-fw/main/elm327.c:2332` sleeps it on demand. Note the local variable names at
`wican-fw/main/obd.c:293-317` are **swapped** relative to the commands they build — harmless,
confusing.

Device sleep *control* is `wican-cli`'s job, a separate package. canair only reads state.
