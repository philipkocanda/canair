# WiCAN firmware v6: compatibility audit and a firmware-generation guard

Status: **PHASE 1 IMPLEMENTED** (2026-10-20), **not yet verified on a device**. Phase 1 (detect
and warn, §1a–§1e) is in `canlib/wican/firmware.py` and the surfaces in §1b; it has been tested
only against stubbed HTTP replies, never against a real v4.52 or v6 device.

Phase 2 status:

- **Read-only v6 support (items 1, 2's read half and 5) is implemented on the unmerged branch
  `feat/wican-fw-v6-support`** (commit `eb4c9aa`). That branch also records its progress in
  this file.
- **AutoPID (item 3) is planned** in `plans/2026-10-31-wican-fw-v6-autopid-profile.md`.
- The rest still needs the open questions below answered on a v6 device.

Every finding comes from reading the source. They were re-checked on 2026-10-31 against
`v6.0.0p-beta.5` and meatpi-components `aa4c2b3`; see "Source basis".

WiCAN Pro firmware **6.0.0-beta.1** (tag `v6.0.0p-beta.1`, 2026-10-20) is a **ground-up
rewrite, not an incremental release**. Almost every device interface canair depends on has
changed or gone:

- the HTTP config API,
- the WebSocket ELM327 terminal,
- the global protocol mode,
- the SLCAN dialect,
- the AutoPID config format.

The one canair transport that works on a stock v6 device is **`elm327-tcp`**. v6 ships a plain
ELM327 TCP server on port 35000, enabled by default, and that is canair's default port for the
transport.

canair today has **no idea which firmware it is talking to**. It reads `fw_version` only to
display it in `canair status`. On v6 the failures range from a clean connect error to a silent
dead socket and a reboot that reports success but never happens. This plan:

1. records the audit,
2. adds a firmware-generation probe with refuse/warn policy (Phase 1),
3. scopes the follow-up work for actual v6 support (Phase 2).

## Source basis

Line citations below are relative to these trees. **Re-check them before trusting a citation**,
as the wican-hardware-and-protocol skill's Rule 0 warns.

| Tree | Ref | Note |
|---|---|---|
| `wican-fw` (v4) | `v4.52p` = `fab54ad` | `wican-pro` branch; current stable |
| `wican-fw` (v6 app) | `v6.0.0p-beta.1` = `e35b1d8` | branched from `1067262` (`v4.51p_beta-01`) |
| `meatpi-components` | `2ef6d0f` (2026-10-19) | **where v6's code actually lives** |
| `meatpi-components` | `dc649d7` (2026-10-27) | read for the `beta.3`+ byte-indexing correction in §6 |
| `meatpi-components` | `aa4c2b3` (2026-10-31) | re-check of §4, §6 and the defaults in §1–§2; newer than any release. `v6.0.0p-beta.5` (2026-10-27) is the latest tag |

**Unless a citation says otherwise, `mpc/` line numbers are at `2ef6d0f`.** The 2026-10-31
re-check confirmed these defaults are unchanged at `aa4c2b3`:

- the `/ws/*` channels,
- the socket servers,
- `can_manager.enabled = false`,
- `autopid.enabled = false`.

The line numbers around them have moved. Where `aa4c2b3` changed a finding, the text says so and
cites that commit.

**v6 moved almost all of its code out of `wican-fw`.** The `v6` tag's `components/` holds only
`HTTP_API.md`. Its `main/` is a thin composition layer. Everything else is in
[meatpi-components](https://github.com/meatpiHQ/meatpi-components), which the top-level
`CMakeLists.txt` clones on first configure. **The release tag does not pin a components commit**,
so `2ef6d0f` (the latest commit, a day before the beta tag) is a best guess at what the beta
was built from, not a proven match. Paths below prefixed `mpc/` are relative to
`meatpi-components/components/`.

**v4.52p versus v4.51p** (stable since 2026-10-12): the only change is the bundled MIC3624
chip firmware, which goes from `V2.3.22` to `V2.3.24`:

- `wican-fw/main/CMakeLists.txt:113-114`
- `wican-fw/main/elm327.c:1370-1374`
- `wican-fw/main/elm327.h:29`

No ESP32-side code path canair uses changed. canair has only been measured on `v4.50p`, which
runs chip firmware `V2.3.22`. So **v4.52p is very likely compatible, but its chip firmware is
unverified**.

## Findings by interface

### 1. `wican-ws` — breaks (clean failure)

**v4.52:**

- One route, `/ws` (`wican-fw/components/ws_server/ws_server.c:200`).
- canair's `{"ws_mode":"terminal","terminal_type":"elm327"}` switches the session to the ELM327
  terminal and pauses AutoPID (`ws_router.c:161-186`, `:304-322`).
- Raw text frames go to `elm327_process_cmd`.

**v6:**

- There is **no `/ws` route**. WebSocket channels are settings-defined and must start with
  `/ws/` (`mpc/websocket_manager/websocket_manager_policy.c:74-78`).
- The defaults are in `mpc/websocket_manager/websocket_manager_settings.c:53-61`:
  - `/ws/obd` (binary, enabled). This is the ELM327 terminal, through the default bridge
    `br_obd = obd <-raw-> ws_obd` (`mpc/bridge_manager/bridge_manager_settings.c:58-60`).
  - `/ws/log` (enabled).
  - `/ws/can` and `/ws/cli` (disabled).
- `/ws/obd` is a **raw byte pipe**. It has no handshake, and frame type is ignored on input
  (`mpc/websocket_manager/websocket_manager_ws.c:180-254`, 4 KB per frame). Output is BINARY
  frames sent to every client (`:365`).
- The strings `ws_mode`, `terminal_type` and `term_out` appear nowhere in v6.

**What canair sees:**

- `GET /ws` with `Upgrade` falls to the asset catch-all, which returns **404**
  (`mpc/http_server_manager/http_server_manager_serve.c:246-250`).
- `require_ws_reachable` (`canlib/wican/mode.py:90`) passes, because port 80 is open.
- `websockets.connect` then raises `InvalidStatus`, which canair already classifies as a
  transport error (`canlib/transport/errors.py:89-91`, via `WebSocketException`).
- The result is an accurate but unhelpful connect error.

Simply pointing `WebSocketChannel` at `/ws/obd` would still fail:

- `recv` drops binary frames (`canlib/transport/channel.py:134-135`), so no `>` prompt ever
  arrives.
- The JSON handshake would reach the chip as raw text and prefix the first command.

### 2. `slcan-tcp` (the default) — breaks out of the box, can fail silently

**v4.52:**

- One global `protocol`. `slcan` serves SLCAN on the configured TCP port
  (`wican-fw/main/main.c:285`, `:995-1010`).
- On the Pro, the ESP32's own CAN controller (TWAI, the ESP32's built-in classic-CAN
  peripheral) is enabled only in the raw modes.

**v6:**

- **There is no `protocol` key any more.** It is replaced by an endpoint + translator + bridge
  model (`mpc/bridge_manager/README.md:3-9`).
- A stock or freshly migrated device (4.x config is erased on upgrade) exposes:
  - ELM327 on TCP 35000,
  - `/ws/obd`,
  - USB,
  - **nothing on 3333**.
- Serving SLCAN needs three settings changes, then a reboot via `POST /api/settings/submit`:
  1. `socket_manager.servers[slcan0].enabled = true`. It defaults to false
     (`mpc/socket_manager/socket_manager_settings.c:45`, `:55`).
  2. A bridge row `{a:"can", b:"slcan0", translator:"slcan", enabled:true}` in
     `bridge_manager.bridges[]`, which allows at most 6 rows
     (`mpc/translator_slcan/README.md:18-23`).
  3. `can_manager.enabled = true`. It defaults to false
     (`mpc/can_manager/can_manager_settings.c:38`). While it is off, the transceiver stays in
     standby (`mpc/can_manager/can_manager.c:160-165`).

**Silent-failure trap:** with the server enabled but no bridge row (or the bridge failing
because `can_manager` is off), the socket **accepts the connection and discards all data**
(`mpc/socket_manager/socket_manager_net.c:170-172`). The only sign is a device-side log warning
(`:225-236`). canair's `require_slcan_reachable` passes, and every ECU then looks dead.

**SLCAN dialect.** v6 is a pure codec (`mpc/translator_slcan/translator_slcan_codec.c:209-251`):

- Only `t`/`T`/`r`/`R` (DLC ≤ 8) are acted on.
- `O`, `C`, `L`, `Z`, `M`/`m`, `V`, `N`, `F` and `Y` are **ignored with no reply** (`:224`).
- `S<n>` takes effect only when `can_manager.baud == "auto"` and the bus is silent
  (`:211-223`; `mpc/can_manager/README.md:40-49`). The default is fixed `"500"`.
- Received frames carry **no timestamp**, although the device stamps them internally.

What this means for canair:

- **`canair sniff --listen-only` is not listen-only on v6.** `SlcanTcpBus` sends `L`
  (`canlib/transport/slcan_tcp.py:144`), v6 ignores it, and the device stays an
  acknowledging, transmit-capable node unless `can_manager.silent = true`. This is a safety
  regression against what `--listen-only` promises.
- **A car that isn't 500 kbit/s cannot be selected from canair.** The `S` command is ignored
  under a fixed bitrate. Every bundled profile is 500k, so nothing breaks today.
- **Parsing is fine.** canair splits on `\r` and ignores non-frame lines. The missing
  timestamps only blur `sniff` timing.
- **Transmit gating:** the controller starts listen-only and switches to normal after two
  frames are read, or after 300 ms of silence at a fixed bitrate
  (`mpc/can_manager/README.md:29-39`). A transmit refused in that window is not reported to
  the client, and the codec drops the rest of that TCP chunk (`translator_slcan_codec.c:267-269`).
- **A slow client gets dropped.** The device closes a client whose send stalls for 100 ms
  (`mpc/socket_manager/socket_manager_net.c:52`, `:540-555`). On a cellular or VPN link during
  a busy `sniff`, this shows up as a peer disconnect, and the monitor reconnect then handles it.

### 3. The bus is shared now — new in v6, affects every transport

In v4 the device mode decided which silicon was on the bus: the TWAI controller *or* the
MIC3624 chip. **In v6 both can be active at once**, and "traffic policy … is the user's call in
v1" (`mpc/can_manager/README.md:272-274`). `obd_gate` serialises the chip and ESP-side request
engines only. SLCAN bridge transmits bypass it (`mpc/obd_gate/README.md:3-14`, `:59-61`;
`mpc/bridge_endpoints/bridge_endpoints_can.c:81-91`). The firmware authors themselves measured 12
mis-attributed responses in 16 s without the gate.

canair's echo validation (`expected_sid` + `expected_echo`) should catch a reply that lands in
the wrong slot. It cannot prevent an ISO-TP abort or a session clash with a concurrent requester
on the same ECU.

### 4. `elm327-tcp` — works, with caveats

**What v6 provides:**

- `obd0` is TCP **35000**, **enabled by default**
  (`mpc/socket_manager/socket_manager_settings.c:54`).
- It is bridged raw to the chip
  (`br_tcp_obd`, `mpc/bridge_manager/bridge_manager_settings.c:61-62`).
- No envelope and no banner. Two clients by default, `TCP_NODELAY` on.
- `--transport elm327-tcp --wican <host>` needs no canair code change.

**What it loses compared with v4.52's command queue** (`wican-fw/main/elm327.c:1628-1700`,
`:1816-1834`):

- **Pure pass-through.** `obd_chip_send` (`mpc/obd_chip/obd_chip.c:447-509`) has no
  per-command timeout, no prompt wait, no input flush and no busy reply.
- **`ATZ` is a real reset** now. v4 rewrote it to `ATWS`.
- **Output fan-out.** Every chip output byte goes to **every** subscriber
  (`mpc/obd_chip/obd_chip.c:136-185`), so AutoPID's replies and another app's replies reach
  canair's socket.
- **AutoPID reclaims the chip after 10 s of client silence** (`AP_CLIENT_YIELD_MS`,
  `mpc/autopid/autopid_private.h:199` at `aa4c2b3`). It then sends its baseline prelude. At
  `aa4c2b3` that is `ATS1;ATH1;ATST96;ATTP<p>;ATSH7DF;ATCRA`, with **headers on** since
  `506e267` (2026-10-24, first released in `beta.3`; `mpc/autopid/autopid_vehicle_core.c:373-376`).
  At `2ef6d0f` it was `ATS1;ATH0;…`.
  - Either way this silently invalidates canair's cached `AdapterState`: spaces come back on,
    the header becomes `7DF`, and the receive filter and `ATST` are reset. On `beta.3`+, headers
    come on as well.
  - The stray `OK>` prompts also land in canair's prompt ledger.
  - At `aa4c2b3` the prelude is also re-sent whenever the chip driver's settings generation moves,
    for example after the CAN self-test's `ATD`.
  - This only happens when AutoPID polling or DTC scanning is enabled. Both default to off
    (`mpc/autopid/autopid_settings.c:42`, `:74`, unchanged at `aa4c2b3`), but Quick Setup
    probably turns AutoPID on.
  - A canair session holding a TesterPresent keepalive keeps the 10 s clock touched. Plain
    polling with gaps over 10 s does not.
- **EEPROM guard** (`mpc/obd_chip/obd_chip_parse.c:328-437`):
  - `ATSP` → `ATTP` and `ATM1` → `ATM0`. Harmless: the Ioniq init's `ATSP6` becomes `ATTP6`.
  - `ATPP` (except `ATPPS`), `ATSD`, `ATCV`, `STWBR` and `STSAVCAL` are answered `?` by the
    device itself.
  - `ATCRA`, `ATFCSH`/`ATFCSD`/`ATFCSM`, `ATST`, `STPX` and the frame-count digit pass through
    untouched.
- **Chip firmware downgrade (released builds up to `beta.5`).** At `2ef6d0f`, v6 bundles chip
  firmware `V2.3.22` (`mpc/obd_chip/obd_chip_fw.c:67`) with `auto_update` on by default
  (`obd_chip_settings.c:51`). It skips the flash only if `VTVERS` contains exactly `"V2.3.22"`
  (`obd_chip_fw.c:211-221`). A chip that v4.52 moved to `V2.3.24` is therefore **downgraded** on
  the first v6 boot.
  - **Fixed upstream, not yet released.** `a91ac27` (2026-10-28, issue #1023) packages
    `V2.3.24` and leaves a `V2.3.22` chip alone, matching the v4.52p policy
    (`mpc/obd_chip/obd_chip_fw.c:44-53`, `:79` at `aa4c2b3`). It landed a day after `beta.5`,
    so until the next release a v6 upgrade still downgrades the chip.

**v6's chip README partly contradicts our measurements.** At `aa4c2b3`,
`mpc/obd_chip/README.md:58-66` claims:

- **"The `1` expected-responses hint ends a multi-frame response after its first frame when
  headers are on."** This *agrees* with canair's reading that the digit counts frames. It also
  matters to AutoPID, which reads the chip with headers on from `beta.3`.
- **"With headers off the hint counts messages."** This contradicts canair's measurement.
- **Multi-frame assembly and automatic flow control exist "only for the OBD physical range"**
  (`7E0–7E7`). This also contradicts canair's measurement.

canair's v4.50 measurements on chip firmware `V2.3.22` contradict the last two
(`plans/2026-08-09-wican-ws-throughput-ceiling.md`). `0x7A0 22C00B3` returned all 3 frames
with headers off. `22C00B2` truncated to 2 frames, which means the digit counted frames. The
README's bench used a PCAN memory ECU with the vehicle simulator off, so the claims are probably
specific to that bench. **Verify on a v6 device before relying on either reading** (open
question Q3).

**What the source does not settle:** whether a write sent while the chip is still busy (for
example the resync `ATI` after a timeout) aborts the running command. That is standard ELM327
interrupt-on-character behaviour, but there is no longer a firmware queue in front of the chip
to prevent it.

### 5. HTTP API — every legacy route is gone

v6's convention is "everything under `/api/`, websockets under `/ws/`"
(`wican-fw/components/HTTP_API.md` §1, at the `v6` tag).

**How v6 answers an unknown route:**

- The `/*` catch-all is GET-only and serves only the `web_ui_v2` asset table, with **no
  single-page-app fallback** (`mpc/http_server_manager/http_server_manager.c:372-379`;
  `mpc/web_ui_v2/web_ui_v2.c:62-98`).
- **An unknown GET returns 404.** Because `/*` matches every path, ESP-IDF's
  `httpd_find_uri_handler` turns **an unknown POST into 405**.
- No legacy route ever comes back as 200 + HTML, so canair never calls `.json()` on a page.

**Access gates, applied before every route, including WebSocket handshakes:**

- **403** `{"error":"configuration disabled on this network"}` when the request arrives on the
  station IP of a Wi-Fi network marked untrusted (`http_server_manager.c:90-96`, `:130-137`).
  The default is trusted.
- **401** with `WWW-Authenticate: Basic realm="WiCAN"` when an admin password is set (off by
  default).
- **The TCP socket servers are not gated.**

**canair's legacy routes on v6:**

| canair call | Site | v6 reply | Effect at the audit | Since Phase 1 |
|---|---|---|---|---|
| `GET /check_status` | `canlib/wican/api.py:57-65`, `canlib/commands/status.py:105` | 404 | `status` shows "HTTP not reachable" (wrong). `get_status()` calls `sys.exit(1)` | `status` probes `/api/info` first and names the generation. `get_status()` raises instead of exiting |
| `GET /load_config` | `canlib/wican/mode.py:28`, `canlib/transport/config.py:191-199` | 404 | `require_protocol` becomes a silent no-op (`mode.py:38-54`). Port and bitrate fall back to 3333 / 500k | skipped on v6 (`canlib/modes/raw_ops.py:80-86`) |
| `POST /store_config` | `canlib/wican/mode.py:268` (`wican mode set`) | never reached | The preceding `load_config` raises `HTTPError` first | `wican mode` is refused on v6 |
| `POST /system_reboot` | `canlib/wican/api.py:92-117` (`reboot_device`), `canlib/commands/wican.py:300-304` | 405 | **Prints "Rebooting WiCAN... (405)" and returns `True`; no reboot happens** | checks `resp.ok` (`api.py:113`); `--reboot` is skipped on v6 |
| `POST /store_car_data` | `canlib/commands/wican.py:291-293` | 405 | Fails loudly (`raise_for_status`) | `autopid upload` is refused on v6 |
| `GET /load_auto_pid_car_data` | `canlib/commands/wican.py:167` | 404 | `download` returns None. `diff` treats everything as changed | `autopid download`/`diff` are refused on v6 |

**v6 equivalents**, for Phase 2:

| Need | v6 route |
|---|---|
| Identity | `GET /api/info` (handler `mpc/api_http/api_http_system.c:347`, route at `:748`, at `aa4c2b3`) |
| Status bits, uptime, version | `GET /api/status` |
| Battery voltage | `GET /api/battery` → `{"voltage": 12.6}`. v4 sent the string `"12.6V"` |
| Sleep state | `GET /api/sleep` → `{"enabled","state","voltage","sleep_v","wake_v",…}` |
| OBD chip state | `GET /api/obd_chip` |
| Reboot | `POST /api/restart` (route at `api_http_system.c:756`, at `aa4c2b3`) |
| Read one settings component | `GET /api/settings/<name>` |
| Write one settings component | `PUT /api/settings/<name>`. Full-object replace, not applied until submit |
| Apply settings | `POST /api/settings/submit`. Reboots only if something changed |
| Settings schema | `GET /api/settings/<name>/schema` |
| AutoPID config | `GET`/`PUT /api/autopid/config`. Applied live, no reboot |
| AutoPID test | `POST /api/autopid/test` |
| AutoPID vehicle store | `GET /api/autopid/vehicles`; `PUT`/`POST`/`DELETE /api/autopid/vehicles/<key>`. The table belongs to the store's current car (see the AutoPID plan) |

The *reason* canair has `--reboot` (restoring AutoPID after a `wican-ws` session) **is gone in
v6**. AutoPID resumes by itself 10 s after the last client write
(`mpc/autopid/README.md:19-29`).

### 6. AutoPID (`canair wican autopid …`) — format changed, with a data-loss trap

The full v6 design for generating and syncing the AutoPID profile, re-checked at `aa4c2b3`, is
in `plans/2026-10-31-wican-fw-v6-autopid-profile.md`. This section keeps the audit's findings.

**The v6 schema** (`mpc/autopid/README.md:497-512`; `:528-544` at `aa4c2b3`):

```json
{"groups": […], "pids": […], "filters": […]}
```

Each entry in `pids` has `name`, `type` (`std`, `custom` or `specific`), `cmd`, `init`,
`rxheader`, `group`, `period_ms` and `enabled`. Each PID's `parameters` entries have `name`,
`expression`, `unit`, `class`, `min` and `max`.

The legacy `{"cars": […]}` shape is converted **client-side only**, by the web UI importer
(`mpc/web_ui_v2/web/index.html:1727-1743`; `profileToPids`, `:1911-1927` at `aa4c2b3`).

**⚠ Data-loss trap.** `ap_config_parse` (`mpc/autopid/autopid_config.c:270-345`) accepts any
JSON object and reads only `groups`, `pids` and `filters`. So PUTting canair's current payload to
`/api/autopid/config` saves an **empty table**, applies it live, and answers `200 {"ok":true}`.
The user's PIDs are wiped with no error. Any v6 upload path must convert the payload first and
check it after the write, not trust the status code.

**Field mapping:**

| v4 car-data field (canair today) | v6 |
|---|---|
| `pid` | `cmd` |
| `pid_init` | `init`. A receive filter is also available via `rxheader` |
| car-level `init` | `specific_init`: on the vehicle store's current car (live), else the `autopid` setting (applied on reboot) |
| per-parameter `period` (a string) | per-PID `period_ms` (a number) |

- Number fields must be JSON numbers. `get_num` ignores strings (`autopid_config.c:155-160`),
  so canair's `"period": "5000"` would quietly fall back to the 1000 ms group default. The same
  goes for `min`/`max`, which on a `specific` row are filters that drop out-of-range readings.
- The PID, not the parameter, is now the scheduling unit.
- **`send_to` / `type: MQTT_Topic` have no equivalent.** MQTT delivery moved to
  `data_destinations` (a cyclic snapshot, `period_s` ≥ 1) and `event_manager` rules. This
  affects `plans/2026-10-18-per-signal-mqtt-streaming.md`.

**Byte indexing: `6.0.0-beta.1`/`beta.2` differ from v4, `beta.3` and later match it:**

- v4 concatenates each frame's data **including its PCI byte**
  (`wican-fw/components/autopid/autopid.c:2114-2290`).
- `beta.1` and `beta.2` reassembled ISO-TP with the PCI bytes stripped, so `B0` was the service
  echo. The docs and importer shifted every index by −1, which is only right for single-frame
  replies.
- `beta.3` and later (issue #1014; `meatpi-components` `506e267`, 2026-10-24, in the
  `6.0.0-beta.3` release notes) read the chip with headers on again. Expressions index the
  frames as the chip prints them, PCI bytes included, `B0` being the first byte after the CAN
  id (`mpc/autopid/autopid_resp.c:34-41`, `mpc/autopid/README.md:577-594`). Published 4.x
  profiles "go in verbatim" and the importer no longer shifts. A custom PID written against a
  single-frame reply on an alfa, `beta.1` or `beta.2` reads one byte too far, and needs each
  `B` index moved up by one.
- So on `beta.3`+ canair's canonical WiCAN `Bnn` expression (`canlib/uds/notation.py`) is
  already the right index; a v6 generator needs no translation. It must refuse the alfas and
  `beta.1`/`beta.2`, where indexes are off by one or more beyond the first frame.
  `canlib/wican/firmware.py` currently treats v6 as one generation and does not distinguish the
  betas. Since `api_level` is `6` on every v6 build, only `fw_version` can.

**The expected-response digit:**

- It is tolerated: the cross-talk guard ignores a trailing hint nibble
  (`autopid_resp.c:92-146`; `:116-170` at `aa4c2b3`).
- It is documented as the main speed lever (`mpc/autopid/BENCHMARKS.md:31-58`).
- v6 also rejects a reply whose echo doesn't match the request (`autopid_runner.c:644-650`;
  `:488-495` at `aa4c2b3`). That shrinks the undercount-desync risk, the reason canair emits a
  count only once wire evidence has confirmed it.
- From `beta.3` AutoPID reads the chip with headers on, as 4.x AutoPID did. The digit's meaning
  there is the same as it was under 4.x AutoPID, which canair's persisted counts already ran
  against. The chip README's headers-on claim agrees (§4). The headers-off meaning still
  matters to canair's own live ELM327 path, which is open question Q3.

### 7. Firmware version formats

**v4 `/check_status`:**

- `fw_version` is built with `sscanf(version, "v%ld.%ld")` and printed with `"%ld.%02ld"`
  (`wican-fw/main/config_server.c:1748-1756`). So tag `v4.52p` reports **`"4.52"`**: the
  suffix is dropped, and an unparseable version gives `"0.00"`.
- `git_version` carries the raw `git describe` (`v4.52p`, or `v4.52p-3-gabc-dirty`).
- `hw_version` is `"WiCAN-OBD-PRO"`.
- **This response also contains plaintext Wi-Fi and MQTT passwords** (`sta_pass`, `mqtt_pass`,
  …; `remove_sensitive_info = false` at `:1945`). It must never be logged or echoed in
  `--json`.

**v6 `/api/info`:**

- `fw_version` is the release number from `version.txt`, e.g. `6.0.0-beta.1`. Pre-release
  words are spelled `alfa` and `beta`.
- `fw_build` is the `git describe`.
- `api_level` is **`6`** (`mpc/api_http/api_http_system.c:57`, unchanged at `aa4c2b3`). It "bumps
  only when the /api surface changes incompatibly" (`mpc/dev_status_manager/HTTP_API.md:120`).
  So it does not separate the betas, whose AutoPID byte indexing differs (§6).
- Other fields: `device_type` `"wican_pro"`, `model`, `hw_version` `"WiCAN-PRO"`, `partition`,
  `device_id`, `mac`.

**v4 `/api/info` returns 404.** v4's catch-all serves only `/wifi_scan` and static files
(`wican-fw/main/config_server.c:2468`, `:836-838`).

### 8. Upgrade-path facts users need

- **Going from 4.x to v6 erases the saved configuration, stored files and logs.** The first v6
  boot rewrites the partition table (release notes "Before you update";
  `mpc/partition_migrate/README.md:1-37`).
- That layout-changing update is **never rolled back automatically**
  (`mpc/fw_update/README.md:76-79`).
- Going back to v4 means uploading the legacy image via `/api/ota/upload`. The device then
  boots with factory defaults.
- Home Assistant needs the `meatpi-multi-device` branch of ha-wican.

## Phase 1 — detect the generation, refuse or warn

**Goal:** canair names the firmware it found and either refuses with a precise remedy or warns.
It never fails silently or misleadingly. No v6 functionality is added.

### 1a. The probe: `canlib/wican/firmware.py` (new leaf module)

The probe sends only GET requests, uses a short timeout, has no side effects on either firmware,
and is memoised per host for the process:

```python
@dataclass(frozen=True)
class FirmwareInfo:
    generation: Literal["v4", "v6", "unknown"]
    version: str | None      # "4.52" / "6.0.0-beta.1"
    build: str | None        # v4 git_version / v6 fw_build
    api_level: int | None    # v6 only
    gated: Literal["auth", "untrusted"] | None   # v6 401 / 403

def probe(host: str, *, timeout: float) -> FirmwareInfo: ...
def compatibility(info: FirmwareInfo, transport: str) -> Verdict: ...
```

Probe order:

1. `GET /api/info`:
   - **200** with an integer `api_level` → `v6`. Read `fw_version`, `fw_build`, `api_level`.
   - **401** with `WWW-Authenticate: Basic realm="WiCAN"` → `v6`, `gated="auth"`.
   - **403** whose JSON says `configuration disabled on this network` → `v6`,
     `gated="untrusted"`.
   - **404** → continue to step 2.
2. `GET /check_status`:
   - **200** JSON with `fw_version` matching `^\d+\.\d{2}$` → `v4`. Keep only `fw_version`,
     `hw_version` and `git_version`; **drop the rest of the body immediately** (it contains
     secrets).
3. Anything else → `unknown`. This is never an error: a non-WiCAN gateway is legitimate.

Keep the compatibility table as data in one place (`canlib/wican/firmware.py::SUPPORTED`):

| Generation | Range | Verdict |
|---|---|---|
| v4 | `4.50`–`4.52` | supported. `4.51`/`4.52` are source-identical on canair's paths; `4.52` adds chip firmware `V2.3.24`, unmeasured |
| v4 | > `4.52` | warn: "newer than tested" |
| v4 | < `4.50` | warn: "older than tested" |
| v6 | any `api_level` | per-transport verdict below |

**Do not key ELM327 *chip* capabilities on this.** The skill's rule still holds: the ESP32
reflashes the chip on its own schedule, so a chip feature must be gated on a chip probe.

### 1b. Policy per surface

| Surface | On v6 | Message points at |
|---|---|---|
| `wican-ws` connect (`canlib/commands/_live/runtime.py`, before `require_ws_reachable`) | **refuse** | `--transport elm327-tcp` (port 35000, works on a stock v6), or `canair config set` for the device's `transport` |
| `slcan-tcp` connect (`canlib/modes/raw_ops.py:40-90`; `canlib/commands/sniff.py:196`) | **warn**, continue | the three settings in §2, and that an open port with no bridge row discards data. Skip `require_protocol` on v6 rather than letting it no-op |
| `sniff --listen-only` on v6 | **refuse** | `L` is ignored; set `can_manager.silent = true` on the device and re-run without the flag, or accept active mode explicitly |
| `canair wican mode set/show` | **refuse** | v6 has no protocol mode; the bridge model in §2 |
| `canair wican autopid upload/download/diff` | **refuse** | not supported on v6 yet (Phase 2). Never PUT the legacy shape (§6 data-loss trap) |
| `--reboot` | **skip with a note** | not needed on v6: AutoPID resumes by itself |
| `canair status` | report generation, version, build, `api_level`, verdict; exit `_MISCONFIGURED` when the selected transport is refused | — |
| `elm327-tcp` to a host that answers `/api/info` | **one-line note** about AutoPID reclaiming the chip after 10 s idle and output fan-out (§4) | — |

Probe cost: one extra short GET per live connect on the WiCAN-HTTP transports. `elm327-tcp` has
`wican_http=False`, so it isn't probed by default. The note in the last row is an optional
best-effort probe of port 80, capped at the existing `transport.connect_timeout`. Add a
`transport.firmware_check: false` escape hatch for users who know what they're running.

### 1c. Fix these regardless of v6 (Boy Scout) — all done

- **Reboot reported success on any response.** It printed the status code and returned `True`
  even on a 405. **Done:** `canlib/wican/api.py::reboot_device` now checks `resp.ok` (`:113`),
  and `canlib/commands/wican.py:300-304` goes through it.
- **The sleep banner could end a live session.**
  `canlib/commands/_live/connect.py::_print_sleep_banner` called `get_status`/`get_config`,
  which called **`sys.exit(1)`** on any failure. That `SystemExit` escaped the banner's
  `except Exception` and killed an already-connected session. This applied to v4 too, for
  example an HTTP timeout right after a WebSocket connect. **Done** (`b45f690`): the fetches in
  `canlib/wican/api.py:50-65` raise instead, and the banner skips itself.
- **Error hints recommended `--transport wican-ws` or `wican mode set`,** both wrong on v6.
  **Done:** the hints in `canlib/wican/mode.py:38-68`, `:90-121` and `:160-195` are
  generation-aware, or skipped on v6.
- **`canlib/transport/elm327_terminal.py` claimed the WiCAN answers `ATI` with `OBDLink MX`.**
  **Done:** it now says `ELM327 v2.3`.

### 1d. Docs

- `docs/getting-started/connect-device.md`: a **Firmware compatibility** section with the
  supported range, what v6 breaks, and the `elm327-tcp` workaround.
- `README.md`: one line linking to that section, nothing more.
- `.claude/skills/wican-hardware-and-protocol/SKILL.md`:
  - the basis table is stale (the checkout is now `v4.52p`);
  - add a "v6 is a different firmware" Trap 4: code lives in `meatpi-components`, and the tag
    doesn't pin it;
  - the `fw_version` / `git_version` format correction (`"4.50"` vs `v4.50p`).
- `docs/reference/cli/` regenerates if any `--help` text changes.

### 1e. Tests

- `tests/test_wican_firmware.py`: the probe against a stubbed `requests`, covering v4 200,
  v6 200, v6 401, v6 403, both 404, and a non-JSON 200. Assert that the v4 body's secret fields
  are never kept on `FirmwareInfo`.
- Policy-table tests: each (generation, transport, surface) combination gives the expected
  verdict.
- A regression test for `reboot_device` on a 405, and one for the sleep banner when the HTTP
  calls fail.

## Phase 2 — v6 support (separate work, gated on the open questions)

Sketch only. Each item needs its own plan once Q1–Q5 are answered.

**Progress:** items 1, 2's detection half (the third bullet), and 5 are implemented, read-only,
on the unmerged branch `feat/wican-fw-v6-support` (commit `eb4c9aa`). That branch records the
details in a "Phase 2 progress" section of this file. Item 3 has its own plan.

1. **`wican-ws` on v6:** a `WebSocketChannel` variant for `/ws/obd`. It sends no handshake,
   decodes BINARY frames as ASCII and doesn't wrap them. The path is selected from
   `FirmwareInfo`. It also needs a story for output fan-out (a second client, such as the web
   UI's terminal, sees canair's traffic and vice versa) and for the 2-client limit.
2. **`slcan-tcp` on v6:**
   - a `canair wican` subcommand that enables `slcan0` + the `can`↔`slcan0` bridge +
     `can_manager` through `/api/settings` (full-object PUT, then submit), with the same
     consent and restore-on-exit care `mode set` has today;
   - bitrate and listen-only set through `can_manager` instead of SLCAN commands;
   - detecting the "accepted but no bridge" state via `/api/sockets` (`bridge: null`).
3. **AutoPID on v6** (planned in `plans/2026-10-31-wican-fw-v6-autopid-profile.md`):
   - a generator emitting the `groups/pids/filters` schema with the same `Bnn` expressions as
     v4 (valid from `6.0.0-beta.3`; refuse the earlier betas, see §6);
   - an upload path that verifies by reading back after the PUT;
   - `/api/autopid/test` as a pre-flight decode check;
   - mapping `send_to` onto `data_destinations` or event rules, or dropping it on v6.
4. **Adapter-state robustness on the shared chip.** Treat any unsolicited prompt or AutoPID
   baseline as an `AdapterState` invalidation, so `set_header` re-sends everything on the next
   switch. Consider keeping a TesterPresent-style touch under 10 s on `elm327-tcp` to v6 even
   outside a diagnostic session, or document disabling AutoPID polling.
5. **`canair status` on v6:** read `/api/battery`, `/api/sleep` and `/api/obd_chip` instead of
   the v4 fields.

## Open questions (need a v6 device)

- **Q1. Which `meatpi-components` commit is the beta built from?** Ask upstream, or read
  `fw_build` / the About page on a flashed device. Every `mpc/` citation assumes `2ef6d0f`,
  unless it names `aa4c2b3`.
- **Q2. Is `elm327-tcp` to v6 actually healthy on the Ioniq?** Run a `monitor @charging --save`
  soak over `elm327-tcp` with AutoPID off, then with AutoPID on. Watch `canair logs` for `stale`
  and `resyncs`.
- **Q3. Chip behaviour under v6.** Using `canair repl --transport elm327-tcp`, as in the
  throughput plan:
  - do `0x7A0 22C00B` and `0x770 22BC01` still reassemble?
  - does the frame-count digit still count frames with headers off? (The chip README at
    `aa4c2b3` says it counts messages; see §4.)
  - test on both `V2.3.22` and `V2.3.24`. Released v6 builds up to `beta.5` downgrade a chip
    to `V2.3.22`; `a91ac27`, unreleased, keeps or installs `V2.3.24` (§4).
- **Q4. What does Quick Setup enable by default?** AutoPID polling? `can_manager`? This decides
  how often the §4 baseline clobbering hits users.
- **Q5. Does a fixed-500 `can_manager` stay in normal mode on the Ioniq's gatewayed OBD port**
  between canair requests, or does it fall back to listen-only and refuse the first transmit of
  each burst?
- **Q6. v4.52's chip firmware `V2.3.24`:** re-measure the expected-response digit and the
  `ATCRA` / flow-control behaviour once, so the supported range in §1a can drop its "unmeasured"
  caveat.
