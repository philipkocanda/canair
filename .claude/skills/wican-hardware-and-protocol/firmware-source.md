# Reading the wican-fw source — Rule 0

Citations here are relative to the basis table in [`SKILL.md`](SKILL.md). Re-check that
basis before you trust a line number.

This cost a whole audit round. **Four separate traps, all of which produce plausible-looking but
wrong citations.**

## Trap 1: the branch

`wican-fw/` is a gitignored checkout. Its **`main` branch is the classic-WiCAN firmware and does not
describe the Pro.** Confirm before reading anything:

```
git -C wican-fw rev-parse --abbrev-ref HEAD    # want: wican-pro
git -C wican-fw describe --tags                # want: v4.52p (the basis) or a later v4 tag
```

A `v6.*` tag here means you are reading the other firmware generation — see Trap 4.

| | `main` | `wican-pro` |
|---|---|---|
| `HARDWARE_VER` | `WICAN_V300` | `WICAN_PRO` (`wican-fw/CMakeLists.txt:48`) |
| SoC | `esp32c3` | `esp32s3` (`wican-fw/sdkconfig:650`) |
| Tag suffix | `v4.21` | `p` suffix — `v4.50p`, `v4.51p_beta-01` |
| ELM327 | **software emulation** | **proxied to a hardware chip** |

`git diff --stat main wican-pro` = **361 files changed, 78 376 insertions**. Eleven whole components
exist only on `wican-pro` (`autopid`, `ws_server`, `obd_logger`, `cmdline`, `vpn_manager`,
`cert_manager`, `ha_webhooks`, `https_client_mgr`, `esp_wireguard`, `restart_tracker`,
`wican_common`), as do `wican-fw/main/obd.c`, `wican-fw/main/ws_router.c` and `wican-fw/main/web/`.

`wican-ws` is **Pro-only**, so anything read on `main` about ELM327 behaviour is about hardware this
project never talks to.

## Trap 2: two `elm327_process_cmd`, two `ws_router.c`

The Pro tree still carries the classic software emulation, and it is **compiled out**:

- `wican-fw/main/elm327.c:65` opens `#if HARDWARE_VER != WICAN_PRO` — the emulator, including
  `wican-fw/main/elm327.c:70-71` (`device_description = "ELM327 v1.3a"`, `identify = "OBDLink MX"`)
  and `elm327_process_cmd` at `wican-fw/main/elm327.c:1025`. **Not on the `wican-ws` path.**
- The live path is `elm327_run_command` at `wican-fw/main/elm327.c:2537`.
- The signature itself splits at `wican-fw/main/elm327.h:44-50` — the non-Pro variant takes a
  `twai_message_t*`, the Pro variant a response callback. **If the function you are reading takes a
  TWAI frame, you are in the emulator.**

Same trap again: **`wican-fw/main/ws_router.c` is dead code, not in the build** — `ws_router`
appears nowhere in `wican-fw/main/CMakeLists.txt`. The compiled copy is
`wican-fw/components/ws_server/ws_router.c` (`wican-fw/components/ws_server/CMakeLists.txt:4-7`).
The two are near-identical and **differ by ~40 lines in numbering**, so a wrong citation lands on a
real-looking line. Check with:

```
rtk grep -rn ws_router wican-fw/main/CMakeLists.txt      # must return nothing
```

## Trap 3: the checked-out tag is not necessarily the running firmware

Get the device's own version from `canair status` (with the user's approval, or ask the user for
its output), which names the tag. On 4.x, `/check_status`
reports it twice: **`fw_version` is only `"4.50"`** (`sscanf(version, "v%ld.%ld")` printed as
`"%ld.%02ld"`, so the `p` suffix and any beta/dirty marker are dropped, and an unparseable version
reads `"0.00"`; `wican-fw/main/config_server.c:1751-1756`), while **`git_version` carries the real
`git describe`**, e.g. `v4.50p`. Diff by the `git_version` tag, then diff the files you care about
across tags before trusting a line number:

```
git -C wican-fw diff --stat v4.50p v4.52p -- main/elm327.c components/ws_server main/config_server.c
```

For the 2026-10-20 re-base that showed only the chip-firmware swap in `wican-fw/main/elm327.c` (a
+3 line shift from 1370 on, already applied below) and nothing in the router or the HTTP server, so
the citations hold for a `v4.50p` device. Do not assume it for the next tag.

## Trap 4: v6 is a different firmware, and the tag does not pin its code

WiCAN Pro firmware **v6** (`v6.0.0p-beta.1`, 2026-10-20) is a ground-up rewrite branched from
`1067262` (`v4.51p_beta-01`). **Nothing in this skill describes it.** No `/ws` terminal, no
`protocol` mode, no `/check_status`/`/load_config`/`/system_reboot`, a pure-codec SLCAN, a new
AutoPID schema, and the bundled chip firmware pinned back to `V2.3.22` (so a chip 4.52 moved to
`V2.3.24` is downgraded on the first v6 boot).

- **Its code is not in `wican-fw`.** The v6 tag's `components/` holds only `HTTP_API.md` and `main/`
  is a thin composition layer; everything else is in
  [meatpi-components](https://github.com/meatpiHQ/meatpi-components), which the top-level
  `CMakeLists.txt` clones on first configure.
- **The release tag does not pin a components commit**, so no checkout proves what a given v6
  build contains. Read `fw_build` from `GET /api/info` on the device, and treat every
  meatpi-components citation as "at commit X", never "in v6".
- Identify it the way canair does (`src/canair/device/wican/firmware.py`): `GET /api/info` answers
  v6 with an integer `api_level` (or a 401/403 gate) and answers 4.x with 404.

The audit, what still works (`elm327-tcp`), and the follow-up work:
`plans/2026-10-20-wican-fw-v6-compatibility.md`.
