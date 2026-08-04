# Per-device transport config + auto-fallback across devices

Status: **DONE** — every item in the `## Status` checklist is ticked, including a
live verification against the WiCAN (fallback crosses to the reachable device over
both `slcan-tcp` and `wican-ws`). Deliberately excluded: mid-session reconnect
(since shipped separately in `plans/2026-08-03-monitor-reconnect-and-wait.md`) and
a `config migrate-devices` subcommand (auto-migration only, no CLI surface).

Two complementary transport features, plus the config-model rework they need:

1. **Auto-fallback** — when the selected WiCAN/address is unreachable, try the
   other configured devices automatically (config flag, default **on**), using a
   **configurable, shorter** connect-probe timeout than today's 4-5 s pre-checks.
2. **Per-device transport** — let each configured device carry its own transport
   type (and port/bitrate), so a user with several devices can bind e.g. the home
   LAN device to `slcan-tcp` and a cellular/VPN device to `wican-ws`. (Exactly the
   "bind `vpn` → `wican-ws` while keeping `home` → `slcan-tcp`" idea foreshadowed
   in `plans/2026-07-22-cellular-transport-timeouts.md`.)

Decisions were settled interactively; this doc is the authoritative record.

## Background — how transport is selected today

- `canlib/transport/config.py::resolve_transport(args)` returns a **single**
  `TransportConfig{type,host,port,bitrate}`. Precedence: `--transport`/`--wican` >
  global `transport:` block > `DEFAULT_TRANSPORT` (`slcan-tcp`).
- `wican_addresses:` is a flat `alias → IP-string` map (`config.py::wican_settings`),
  consumed by two host resolvers that must stay consistent:
  `transport/config.py::_resolve_host` and `wican_api.py::resolve_wican_url`.
  Transport `type` is **decoupled** from the chosen alias today.
- **No fallback / reconnect exists.** On connect failure the code classifies +
  prints + exits 1 (`commands/_live.py:531`, `modes/raw_ops.py:87`). The only
  "retry" is same-request-same-connection on silence.
- Two client construction sites keyed on `transport.is_raw`: the ELM path in
  `commands/_live.py::async_main` (`WiCANTerminal`) and the raw path in
  `modes/raw_ops.py::run_raw` (`RawTerminal`). Both share fast pre-flight probes
  `wican_mode.require_ws_reachable(host, timeout=4.0)` /
  `require_slcan_reachable(host, port, timeout=5.0)` — the natural per-candidate
  liveness test.
- Latent bug: `config.py:237-238` resolves port/bitrate with `_int(a) or _int(b)`,
  so a legitimate `0` falls through. Fix while here (Boy Scout).

## Config model (final)

Introduce a `devices:` block as the canonical device definition; the legacy flat
`wican_addresses:` is auto-migrated into it (see below).

```yaml
devices:
  ap:   { host: "192.168.80.1" }
  home: { host: "192.168.1.100", transport: slcan-tcp, port: 3333 }
  vpn:  { host: "10.0.0.100",    transport: wican-ws }
default_wican: home                 # name kept for back-compat; resolves in devices

transport:
  fallback: true                    # NEW, default true
  connect_timeout: 2.0              # NEW, configurable short probe timeout (s)
  fallback_order: [home, vpn, ap]   # NEW, optional
  # type/host/port/bitrate: unchanged global defaults/fallbacks
```

**Device-namespace resolution** — new `config.py::wican_devices()`:

- `devices:` present → use it (normalized); **`wican_addresses` ignored entirely**
  (no merge, no ambiguity).
- else `wican_addresses:` present → each `alias: ip` treated as a host-only device
  (today's behaviour, verbatim).
- else → built-in `{ap: 192.168.80.1}` fallback.

`default_wican` and `--wican X` resolve within whichever map is active. Both host
resolvers (`_resolve_host`, `resolve_wican_url`) read `.host` from this, so aliases
stay consistent across the transport and HTTP-API paths. `wican_settings()` is kept
as a thin back-compat shim (returns `{alias: host}` + default).

**Per-candidate transport precedence:** `--transport` CLI > device entry
`transport:` > global `transport.type` > `DEFAULT_TRANSPORT`. Same chain for
`port` / `bitrate`. This is the always-correct runtime foundation — it holds
whether or not migration ran, failed, or hit a read-only HOME.

## Auto-migration (`wican_addresses:` → `devices:`)

Makes `devices:` the canonical on-disk form without user action.

- New `canlib/devices_migrate.py::migrate_config(dry_run) -> result` — **pure**,
  ruamel comment-preserving: rewrites `wican_addresses:` → `devices:` (each
  `alias: {host: <ip>}`, per-alias inline comments carried over, placed where
  `wican_addresses` was), then **removes `wican_addresses` entirely**.
  `default_wican` untouched.
- **No sentinel needed** — detection is self-clearing: it only triggers when
  `wican_addresses` is present *and* `devices` is absent, false after one success.
- `maybe_auto_migrate()` wrapper hooked at `cli.py:191` (right after
  `ensure_config_dir()`): runs on **any** invocation once (TTY or piped),
  best-effort (any IO/parse error swallowed → runtime precedence still works),
  emits a **one-line stderr notice** (`migrated wican_addresses → devices: …`).
- **No `config migrate-devices` subcommand.** The migration is a one-time,
  transitional concern handled automatically; a permanent CLI surface for it would
  be lingering clutter. The pure `migrate_config()` is tested directly; there is no
  manual escape hatch because the tool works either way (runtime precedence).

## Fallback mechanism

- `resolve_transport_candidates(args) -> list[TransportConfig]` (new,
  `transport/config.py`): candidate[0] = the explicitly selected device
  (`--wican` > `transport.host` > `default_wican` — unchanged rules); when
  `fallback` is on, append the remaining known aliases ordered by `fallback_order`
  (else definition order). An explicit `--wican X` stays **first**, then the
  others. `resolve_transport(args)` becomes `candidates[0]` (signature preserved
  for `status` / `sniff` / `config show` / `repl`).
- `select_reachable_transport(args, *, connect_timeout) -> TransportConfig` (new
  shared helper), called **at the top of `async_main`, before the `is_raw`
  branch**, so a fallback can cross raw↔elm. Probes each candidate's liveness (TCP
  port 80 for WiCAN-HTTP devices — `is_wican_http` — else the data port) with the
  short `connect_timeout`, prints a stderr notice (`home unreachable → trying
  vpn`), and returns the first live candidate. If **all** fail, returns
  candidate[0] so the existing rich `describe_transport_error` path fires
  unchanged. The winner still flows through the current `require_*_reachable` +
  connect path (one extra fast TCP connect on the winner — acceptable).
- `--no-fallback` CLI flag (in `add_connection_args`) and `transport.fallback:
  false` opt out. Single-candidate configs skip probing entirely (today's
  behaviour). Notices → stderr so `--json` stdout stays clean; the chosen host is
  surfaced in `--json` output.
- **Out of scope:** mid-session reconnect (state/session complexity) — connect-time
  fallback only. Documented as such.

## `canair config` CLI impact

New dotted keys become settable: `devices.<alias>.{host,transport,port,bitrate}`
and `transport.{fallback,connect_timeout,fallback_order}`.

- **`_KNOWN_KEYS`** (`commands/config.py:33`) — add the `devices.<alias>.*` wildcard
  keys (documented like today's `wican_addresses.<alias>`) + the three
  `transport.*` keys. Feeds `--help`'s known-keys block, `_known_key()` namespace
  acceptance, and tab-completion.
- **`_enum_values()` / `_invalid_value()`** (`:185`, `:196`) — must also match the
  **wildcard-middle** key `devices.*.transport` and validate against
  `VALID_TRANSPORTS` (today they match exact keys only).
- **`transport.fallback_order` (a list)** — `config set transport.fallback_order
  home,vpn,ap` accepts a **comma-separated** value, split (trimmed) into a YAML
  list; a single value → 1-element list.
- **`transport.connect_timeout` (a float)** — extend `config.py::coerce_scalar`
  with **general float parsing** (after the int attempt), guarding out non-finite
  (`inf`/`nan`, which `float()` otherwise accepts) so they fall back to string.
  Zero-padded/ambiguous ids keep the `--string` escape hatch.
- **`wican_addresses.*` deprecation** — a `config set wican_addresses.*` warns,
  suggests `devices.<alias>.host`, and notes "ignored because `devices:` is
  defined" when applicable. Key stays readable/settable for back-compat.
- **`config show`** — `_gather()` (`:308`) sources devices from `wican_devices()`;
  **keeps `wican.addresses` as `{alias: host}` for `--json` back-compat** and
  **adds** a richer `devices` block (host + effective transport) plus a transport
  `fallback` block (`enabled`/`connect_timeout`/`order`/candidate list). `_render()`
  (`:371`) shows each device's host + effective transport (default marked) and a
  "Fallback: on (2.0 s; order: home → vpn → ap)" line under Transport.
- **`_SPECIAL_KEYS`** (`:368`) — add `devices` so the raw block isn't dumped into
  the generic "Settings" list.
- `config path` / `config edit` — unaffected.

## Files to change

**Code**

1. `canlib/config.py` — `wican_devices()`, `fallback_settings()` (enabled/timeout/
   order); `wican_settings()` kept delegating; general float in `coerce_scalar`.
2. `canlib/devices_migrate.py` — **new** pure migration module.
3. `canlib/cli.py` — `maybe_auto_migrate()` hook after `ensure_config_dir()`
   (`:191`).
4. `canlib/transport/config.py` — `resolve_transport_candidates()`, per-device
   type/port/bitrate resolution, `resolve_transport()` = candidate[0]; fix the
   `_int() or _int()` zero-falls-through bug (`:237-238`).
5. `canlib/commands/_live.py` — `--no-fallback` in `add_connection_args`;
   `select_reachable_transport()` at the top of `async_main`; chosen host into
   `--json`.
6. `canlib/wican_mode.py` — thread the configured `connect_timeout` through the
   probe helpers (already parameterized — just pass it).
7. `canlib/wican_api.py` + `transport/config.py::_resolve_host` — read host via
   `wican_devices()`.
8. `canlib/commands/config.py` — `_KNOWN_KEYS`, `_enum_values`/`_invalid_value`
   wildcard match, `fallback_order` comma-split, `wican_addresses` deprecation,
   `_gather`/`_render` devices + fallback, `_SPECIAL_KEYS`, `_SET_EPILOG` examples.

**Docs**

9. `config.example.yaml` (lead with `devices:`; show `wican_addresses` as the
   legacy/auto-migrated form), `canlib/config.py::_STARTER_CONFIG`,
   `docs/reference/config.md`, `docs/reference/cli/config.md`,
   `docs/getting-started/connect-device.md`, `AGENTS.md` (WiCAN Access section),
   `CHANGELOG.md` (`[Unreleased]`).

**Tests**

10. `tests/test_transport_config.py` — candidate ordering; explicit-`--wican`-first;
    per-device type/port/bitrate precedence; `devices` supersedes
    `wican_addresses`; string/mapping back-compat; fallback-on-connect-fail (fake
    probes); `--no-fallback`; all-down → rich error; `connect_timeout` plumbing;
    the `0`-port/bitrate regression.
11. `tests/test_devices_migrate.py` — **new**: round-trip, comment preservation,
    idempotency, dry-run, `wican_addresses` removal.
12. `tests/test_config.py` (or equivalent) — new-key set/validate,
    `devices.*.transport` enum rejection, general float coercion (`2.0`/`0.5`/
    `inf`→string), `fallback_order` comma-split, `wican_addresses` deprecation
    warning, `show --json` shape (back-compat `addresses` + new `devices`).

## Gates

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair validate all
uv run canair status --help
uv run canair config set --help          # new keys/examples present
```

## Status

- [x] Config model: `devices:` + `wican_devices()` runtime precedence
- [x] Auto-migration module + `cli.py` hook
- [x] `resolve_transport_candidates()` + per-device transport
- [x] `select_reachable_transport()` fallback + `--no-fallback`
- [x] `config` CLI (keys, validation, coercion, show, deprecation)
- [x] Docs (config.example, docs/, AGENTS.md, CHANGELOG)
- [x] Tests + gates green (3170 passed; ruff/ty/gen-check/validate clean)
- [x] Verified live against the WiCAN (fallback crosses to the reachable device
      over both slcan-tcp and wican-ws)
