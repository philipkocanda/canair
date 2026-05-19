# Plan: Standalone `wican-cli` Package

Extract `wican.py` into a reusable, independently publishable CLI tool for managing WiCAN Pro devices. The goal is a `pip install wican-cli` that works for anyone with a WiCAN device, regardless of vehicle or use case.

## Current state

`wican.py` lives in `wican-pro/` alongside vehicle-specific research tools. It depends on:

- `canlib/constants.py` — only for `WICAN_ADDRESSES` and `DEFAULT_WICAN` (config.yaml loading)
- `requests` — HTTP calls to the device
- Standard library: `argparse`, `json`, `sqlite3`, `sys`, `tempfile`, `datetime`, `pathlib`

The coupling to the research project is minimal — just the config.yaml loader import.

## Tasks

### 1. Repository setup

- [ ] Create new repo (e.g. `github.com/philipkocanda/wican-cli`)
- [ ] Choose package name: `wican-cli` (PyPI), importable as `wican_cli`
- [ ] Standard Python package layout:
  ```
  wican-cli/
  ├── src/
  │   └── wican_cli/
  │       ├── __init__.py
  │       ├── __main__.py      # Entry point: python -m wican_cli
  │       ├── cli.py           # Argument parsing, main()
  │       ├── client.py        # HTTP client (get_config, store_config, reboot, etc.)
  │       ├── config.py        # Config file loading (config.yaml / ~/.config/wican-cli/)
  │       └── redact.py        # Credential redaction logic
  ├── tests/
  ├── pyproject.toml
  ├── README.md
  └── LICENSE
  ```

### 2. Decouple from `canlib/constants.py`

- [ ] Inline the config.yaml loading logic into the new package (it's 39 lines)
- [ ] Support multiple config file locations:
  1. `./wican-cli.yaml` (project-local)
  2. `~/.config/wican-cli/config.yaml` (user-global, XDG-compliant)
  3. Environment variable `WICAN_URL` as override
- [ ] Remove the `from canlib.constants import ...` dependency entirely

### 3. Make device address handling more generic

- [ ] Current: `--wican home|vpn|<url>` with named aliases from config.yaml
- [ ] Keep this pattern but make it the CLI's own config, not tied to a parent project
- [ ] Support `WICAN_URL` environment variable as the simplest zero-config path
- [ ] Default to `192.168.80.1` (WiCAN AP mode) when nothing is configured

### 4. Package metadata and distribution

- [ ] `pyproject.toml` with:
  - `[project.scripts]` entry: `wican = "wican_cli.cli:main"`
  - Dependencies: `requests`, `pyyaml` (for config.yaml)
  - Python >= 3.10 (no 3.12-specific features used)
  - Metadata: description, author, license (Unlicense), URLs, classifiers
- [ ] Publish to PyPI
- [ ] Consider also providing a single-file download (the script is self-contained enough)

### 5. Features to keep as-is

All current subcommands transfer directly — they're all device-management, not vehicle-specific:

- [x] `config` — view, save snapshots, `--redact`, `--section` filtering
- [x] `sleep` — view/modify sleep settings with `--dry-run`
- [x] `status` — device status summary
- [x] `protocol` — switch between auto_pid / slcan / elm327 / savvycan / realdash66
- [x] `logs` — list/download/query SD card OBD log databases
- [x] `autopid` — show cached AutoPID values
- [x] `reboot` — reboot with confirmation

### 6. Features to add for a general audience

- [ ] `wican discover` — mDNS/UDP broadcast discovery of WiCAN devices on the network
- [ ] `wican firmware` — check for / download firmware updates (optional, if MeatPi provides an API)
- [ ] `wican mqtt` — show MQTT configuration summary, maybe test connectivity
- [ ] `wican backup` / `wican restore` — full config backup and restore cycle
  - `backup` = current `config --save` (unredacted by default for restore purposes)
  - `restore` = POST a saved JSON back to the device
- [ ] `wican wifi` — view/edit WiFi station settings specifically (add/remove fallbacks)
- [ ] Tab completion via `argcomplete` or `click` shell completion
- [ ] `--verbose` / `--debug` flag for troubleshooting HTTP issues
- [ ] Colored output using `rich` (optional dependency) or plain ANSI fallback

### 7. Testing

- [ ] Unit tests for redaction logic (port existing tests if any)
- [ ] Unit tests for config file resolution
- [ ] Integration test fixtures with mocked HTTP responses (no real device needed)
- [ ] CI: GitHub Actions with pytest + ruff

### 8. Documentation

- [ ] README with: installation, quick start, all subcommands with examples
- [ ] Document config file format and locations
- [ ] Document environment variables (`WICAN_URL`, `WICAN_TIMEOUT`)
- [ ] Link to WiCAN Pro documentation / MeatPi resources

### 9. Migration path for this repo

- [ ] Replace `wican-pro/wican.py` with a thin wrapper or just `pip install wican-cli`
- [ ] Remove `canlib/constants.py` config-loading code (or keep it for the other tools)
- [ ] Update `wican-pro/pyproject.toml` to add `wican-cli` as a dependency
- [ ] Update docs (AGENTS.md, README.md) to reference the standalone package

### 10. Open questions

- **CLI framework:** Stay with `argparse` (zero deps, current approach) or switch to `click`/`typer` (nicer help output, less boilerplate, but adds dependencies)?
- **Package name:** `wican-cli`, `wican-tool`, `python-wican`, or just `wican`? Check PyPI availability.
- **Scope boundary:** Should the CLI include any ELM327 terminal features (interactive WebSocket session)? That's currently in `canreq.py` and uses `websockets`. Keeping it out keeps the dependency list small (`requests` + `pyyaml` only).
- **Config file format:** YAML (current, requires `pyyaml`) vs TOML (stdlib in 3.11+, reduces deps) vs INI?

## Priority order

1. Decouple + restructure into package layout
2. Tests with mocked HTTP
3. PyPI-ready pyproject.toml
4. README + docs
5. Publish v0.1.0
6. New features (discover, backup/restore, etc.) in subsequent releases
