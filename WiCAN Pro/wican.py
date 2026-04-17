#!/usr/bin/env python3
"""
WiCAN CLI — manage WiCAN Pro device configuration.

Subcommands:
  config   Download and display device configuration
  sleep    View or modify sleep/power saving settings
  reboot   Reboot the device

Usage:
  python3 wican.py config                          # Pretty-print full config
  python3 wican.py config --section sleep           # Show only sleep fields
  python3 wican.py config --json                    # Raw JSON output
  python3 wican.py config --save                    # Save snapshot to configs/

  python3 wican.py sleep                            # Show current sleep status
  python3 wican.py sleep --enable                   # Enable sleep mode
  python3 wican.py sleep --disable                  # Disable sleep mode
  python3 wican.py sleep --voltage 12.5             # Set voltage threshold
  python3 wican.py sleep --time 5                   # Set sleep delay (minutes)
  python3 wican.py sleep --wakeup-interval 120      # Set periodic wakeup interval (min)
  python3 wican.py sleep --no-wakeup                # Disable periodic wakeup
  python3 wican.py sleep --disable --dry-run        # Preview change without applying

  python3 wican.py status                            # Device status summary
  python3 wican.py status --json                    # Raw JSON from /check_status

  python3 wican.py reboot                           # Reboot device
  python3 wican.py reboot --yes                     # Skip confirmation

  python3 wican.py logs                              # List available log databases
  python3 wican.py logs --download                   # Download all log DBs to logs/
  python3 wican.py logs --download --db <filename>   # Download specific DB
  python3 wican.py logs --query SOC_BMS              # Query parameter from latest DB
  python3 wican.py logs --query SOC_BMS --limit 20   # Last 20 values
  python3 wican.py logs --params                     # List all logged parameters
  python3 wican.py logs --json                       # JSON output

  python3 wican.py protocol                          # Show current protocol
  python3 wican.py protocol --set slcan              # Switch to SLCAN mode
  python3 wican.py protocol --set auto_pid           # Switch back to AutoPID
  python3 wican.py protocol --set elm327 --port 35000  # ELM327 on custom port
  python3 wican.py protocol --set slcan --dry-run    # Preview without applying

Global flags:
  --wican home|vpn|<url>   Device address (default: home)
  --timeout <seconds>      Request timeout (default: 10)
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip3 install requests", file=sys.stderr)
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIGS_DIR = SCRIPT_DIR / "configs"
LOGS_DIR = SCRIPT_DIR / "logs"

WICAN_ADDRESSES = {
    "home": "http://10.0.2.86",
    "vpn": "http://192.168.3.2",
}
DEFAULT_WICAN = "home"
WICAN_TIMEOUT = 10  # seconds

# Sleep-related config keys
SLEEP_KEYS = [
    "sleep_status",
    "sleep_disable_agree",
    "periodic_wakeup",
    "sleep_volt",
    "sleep_time",
    "wakeup_interval",
]

# Human-readable labels for sleep fields
SLEEP_LABELS = {
    "sleep_status": "Sleep mode",
    "sleep_disable_agree": "Sleep disable confirmed",
    "periodic_wakeup": "Periodic wakeup",
    "sleep_volt": "Voltage threshold (V)",
    "sleep_time": "Sleep delay (min)",
    "wakeup_interval": "Wakeup interval (min)",
}

# Config sections for --section filter
CONFIG_SECTIONS = {
    "sleep": SLEEP_KEYS,
    "battery_alert": [
        "batt_alert",
        "batt_alert_ssid",
        "batt_alert_pass",
        "batt_alert_volt",
        "batt_alert_protocol",
        "batt_alert_url",
        "batt_alert_port",
        "batt_alert_topic",
        "batt_alert_time",
        "batt_mqtt_user",
        "batt_mqtt_pass",
    ],
    "mqtt": [
        "mqtt_en",
        "mqtt_url",
        "mqtt_port",
        "mqtt_user",
        "mqtt_pass",
        "mqtt_tx_topic",
        "mqtt_rx_topic",
        "mqtt_status_topic",
    ],
    "wifi": [
        "ssid",
        "password",
        "ssid_st",
        "password_st",
        "sta_channel",
        "ap_ch",
    ],
    "can": [
        "port",
        "protocol",
        "can_datarate",
        "can_mode",
        "port_type",
    ],
}

# Protocol modes (firmware: config_server_protocol() in config_server.c)
PROTOCOLS = {
    "auto_pid": "AutoPID — MQTT vehicle data polling (normal operation)",
    "slcan": "SLCAN — serial CAN adapter (for SavvyCAN, candump, etc.)",
    "elm327": "ELM327 — OBD-II emulation over TCP/UDP",
    "savvycan": "SavvyCAN — native SavvyCAN TCP protocol",
    "realdash66": "RealDash — RealDash protocol 66",
}

# Protocol config keys that are relevant
PROTOCOL_KEYS = ["protocol", "port_type", "port", "can_mode"]


# ── Helpers ────────────────────────────────────────────────────────────────


def resolve_wican_url(wican: str) -> str:
    """Resolve --wican argument to a base URL."""
    if wican in WICAN_ADDRESSES:
        return WICAN_ADDRESSES[wican]
    if not wican.startswith("http"):
        return f"http://{wican}"
    return wican


def get_config(base_url: str, timeout: int) -> dict:
    """GET /load_config and return parsed JSON."""
    url = f"{base_url}/load_config"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        print("  Is the device reachable? Try: --wican vpn", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"ERROR: Timeout connecting to {base_url}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)


def store_config(base_url: str, config: dict, timeout: int) -> None:
    """POST /store_config with full config JSON. Device auto-reboots."""
    url = f"{base_url}/store_config"
    try:
        resp = requests.post(url, json=config, timeout=timeout)
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        # Expected — device reboots before responding
        pass
    except Exception as e:
        print(f"ERROR: Failed to store config: {e}", file=sys.stderr)
        sys.exit(1)


def reboot_device(base_url: str, timeout: int) -> None:
    """POST /system_reboot to reboot the device."""
    url = f"{base_url}/system_reboot"
    try:
        resp = requests.post(url, data="reboot", timeout=timeout)
        resp.raise_for_status()
    except requests.Timeout:
        pass  # Expected — device reboots
    except Exception as e:
        print(f"ERROR: Failed to reboot: {e}", file=sys.stderr)
        sys.exit(1)


def confirm(prompt: str) -> bool:
    """Ask user for y/n confirmation."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


# ── Subcommands ────────────────────────────────────────────────────────────


def cmd_config(args) -> None:
    """Download and display device configuration."""
    base_url = resolve_wican_url(args.wican)
    config = get_config(base_url, args.timeout)

    # Filter to section if requested
    if args.section:
        section = args.section.lower()
        if section not in CONFIG_SECTIONS:
            print(f"ERROR: Unknown section '{section}'", file=sys.stderr)
            print(f"  Available: {', '.join(CONFIG_SECTIONS.keys())}", file=sys.stderr)
            sys.exit(1)
        keys = CONFIG_SECTIONS[section]
        config = {k: v for k, v in config.items() if k in keys}

    if args.json:
        print(json.dumps(config, indent=2))
    else:
        # Pretty table output
        max_key = max(len(k) for k in config) if config else 0
        for key, value in config.items():
            print(f"  {key:<{max_key}}  {value}")

    # Save snapshot
    if args.save:
        CONFIGS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        path = CONFIGS_DIR / f"config_{timestamp}.json"
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        print(f"\nSaved to {path}")


def cmd_sleep(args) -> None:
    """View or modify sleep/power saving settings."""
    base_url = resolve_wican_url(args.wican)
    config = get_config(base_url, args.timeout)

    # Determine if we're modifying anything
    changes = {}

    if args.enable:
        changes["sleep_status"] = "enable"
        changes["sleep_disable_agree"] = "no"
    elif args.disable:
        changes["sleep_status"] = "disable"
        changes["sleep_disable_agree"] = "yes"

    if args.voltage is not None:
        changes["sleep_volt"] = str(args.voltage)

    if args.time is not None:
        changes["sleep_time"] = str(args.time)

    if args.wakeup_interval is not None:
        changes["periodic_wakeup"] = "enable"
        changes["wakeup_interval"] = str(args.wakeup_interval)

    if args.no_wakeup:
        changes["periodic_wakeup"] = "disable"

    # Display current status
    print("Sleep configuration:")
    max_label = max(len(v) for v in SLEEP_LABELS.values())
    for key in SLEEP_KEYS:
        label = SLEEP_LABELS.get(key, key)
        current = config.get(key, "?")
        if key in changes and changes[key] != current:
            print(f"  {label:<{max_label}}  {current} → {changes[key]}")
        else:
            print(f"  {label:<{max_label}}  {current}")

    if not changes:
        return

    # Check if anything actually changed
    effective_changes = {k: v for k, v in changes.items() if config.get(k) != v}
    if not effective_changes:
        print("\nNo changes needed — config already matches.")
        return

    if args.dry_run:
        print("\n[dry-run] Would apply changes and reboot device.")
        return

    # Confirm and apply
    print(f"\n⚠  Saving config will reboot the device ({base_url})")
    if not args.yes and not confirm("Apply changes?"):
        print("Aborted.")
        return

    # Apply changes to full config and POST
    new_config = dict(config)
    new_config.update(effective_changes)
    store_config(base_url, new_config, args.timeout)
    print("Config saved. Device is rebooting...")


def cmd_reboot(args) -> None:
    """Reboot the WiCAN device."""
    base_url = resolve_wican_url(args.wican)

    print(f"Rebooting WiCAN at {base_url}")
    if not args.yes and not confirm("Continue?"):
        print("Aborted.")
        return

    reboot_device(base_url, args.timeout)
    print("Reboot command sent.")


def get_status(base_url: str, timeout: int) -> dict:
    """GET /check_status and return parsed JSON."""
    url = f"{base_url}/check_status"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        print("  Is the device reachable? Try: --wican vpn", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"ERROR: Timeout connecting to {base_url}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to get status: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args) -> None:
    """Show device status summary."""
    base_url = resolve_wican_url(args.wican)
    status = get_status(base_url, args.timeout)

    if args.json:
        print(json.dumps(status, indent=2))
        return

    # Curated summary
    sections = [
        (
            "Device",
            [
                ("Hardware", status.get("hw_version", "?")),
                (
                    "Firmware",
                    status.get("fw_version", "?") + " (" + status.get("git_version", "?") + ")",
                ),
                ("Device ID", status.get("device_id", "?")),
                ("Uptime", status.get("uptime", "?")),
                ("Battery", status.get("batt_voltage", "?")),
            ],
        ),
        (
            "Network",
            [
                ("WiFi mode", status.get("wifi_mode", "?")),
                ("WiFi status", status.get("sta_status", "?")),
                ("IP address", status.get("sta_ip", "?")),
                ("Connected SSID", status.get("sta_ssid", "?")),
                ("mDNS", status.get("mdns", "?")),
                ("VPN status", status.get("vpn_status", "?")),
                ("VPN IP", status.get("vpn_ip", "") or "-"),
            ],
        ),
        (
            "CAN / OBD",
            [
                ("Protocol", status.get("protocol", "?")),
                ("CAN datarate", status.get("can_datarate", "?")),
                ("CAN mode", status.get("can_mode", "?")),
                ("OBD chip", status.get("obd_chip_status", "?")),
                ("ECU status", status.get("ecu_status", "?")),
            ],
        ),
        (
            "Power",
            [
                ("Sleep mode", status.get("sleep_status", "?")),
                ("Sleep voltage", status.get("sleep_volt", "?") + "V"),
                ("Sleep delay", status.get("sleep_time", "?") + " min"),
                ("Periodic wakeup", status.get("periodic_wakeup", "?")),
                ("Wakeup interval", status.get("wakeup_interval", "?") + " min"),
                ("Wakeup voltage", status.get("wakeup_volt", "?")),
            ],
        ),
        (
            "MQTT",
            [
                ("Enabled", status.get("mqtt_en", "?")),
                ("Broker", status.get("mqtt_url", "?") + ":" + status.get("mqtt_port", "?")),
                ("Status topic", status.get("mqtt_status_topic", "?")),
            ],
        ),
        (
            "Logging",
            [
                ("SD logging", status.get("logger_status", "?")),
                ("Period", status.get("log_period", "?") + "s"),
                ("IMU threshold", status.get("imu_threshold", "?")),
            ],
        ),
    ]

    for section_name, fields in sections:
        print(f"\n{section_name}:")
        max_label = max(len(label) for label, _ in fields)
        for label, value in fields:
            print(f"  {label:<{max_label}}  {value}")


def get_log_index(base_url: str, timeout: int) -> dict:
    """GET /obd_logs and return parsed JSON index."""
    url = f"{base_url}/obd_logs"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"ERROR: Timeout connecting to {base_url}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to get log index: {e}", file=sys.stderr)
        sys.exit(1)


def download_log_db(base_url: str, filename: str, dest: Path, timeout: int) -> Path:
    """Download a log database file from the device."""
    url = f"{base_url}/obd_logs/{filename}"
    try:
        resp = requests.get(url, timeout=max(timeout, 60))  # Large files need more time
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
    except Exception as e:
        print(f"ERROR: Failed to download {filename}: {e}", file=sys.stderr)
        sys.exit(1)


def open_log_db(path: Path) -> sqlite3.Connection:
    """Open a log database, tolerating partial corruption."""
    db = sqlite3.connect(str(path))
    # WAL mode + ignore malformed pages where possible
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    return db


def cmd_logs(args) -> None:
    """List, download, or query OBD log databases."""
    base_url = resolve_wican_url(args.wican)

    # List log databases
    index = get_log_index(base_url, args.timeout)

    if args.download:
        # Download specific or all databases
        dbs = index.get("databases", [])
        if args.db:
            dbs = [d for d in dbs if d["filename"] == args.db]
            if not dbs:
                print(f"ERROR: Database '{args.db}' not found", file=sys.stderr)
                sys.exit(1)

        LOGS_DIR.mkdir(exist_ok=True)
        for db_info in dbs:
            fname = db_info["filename"]
            dest = LOGS_DIR / fname
            if dest.exists() and not args.force:
                print(f"  {fname} — already exists (use --force to overwrite)")
                continue
            print(f"  Downloading {fname}...", end=" ", flush=True)
            download_log_db(base_url, fname, dest, args.timeout)
            size_kb = dest.stat().st_size / 1024
            print(f"{size_kb:.0f} KB")
        return

    if args.params or args.query:
        # Download current DB to temp file and query it
        current_db = index.get("current_db")
        if not current_db:
            print("ERROR: No current database", file=sys.stderr)
            sys.exit(1)

        # Use local copy if available, otherwise download to temp
        local_path = LOGS_DIR / current_db
        if local_path.exists():
            db_path = local_path
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            db_path = Path(tmp.name)
            print(f"Downloading {current_db}...", flush=True)
            download_log_db(base_url, current_db, db_path, args.timeout)

        db = open_log_db(db_path)

        if args.params:
            # List all parameters
            try:
                rows = db.execute(
                    "SELECT pi.Id, pi.Name, pi.Data, COUNT(pd.rowid) as cnt "
                    "FROM param_info pi LEFT JOIN param_data pd ON pd.param_id = pi.Id "
                    "GROUP BY pi.Id ORDER BY pi.Name"
                ).fetchall()
            except sqlite3.DatabaseError:
                # Fallback without join if DB is partially corrupt
                rows = [
                    (r[0], r[1], r[2], "?")
                    for r in db.execute(
                        "SELECT Id, Name, Data FROM param_info ORDER BY Name"
                    ).fetchall()
                ]

            if args.json:
                result = []
                for row in rows:
                    meta = json.loads(row[2]) if row[2] else {}
                    result.append({"id": row[0], "name": row[1], "rows": row[3], **meta})
                print(json.dumps(result, indent=2))
            else:
                print(f"{'Parameter':<35} {'Rows':>7}  {'Period':>8}  Unit")
                print(f"{'─' * 35} {'─' * 7}  {'─' * 8}  {'─' * 6}")
                for row in rows:
                    meta = json.loads(row[2]) if row[2] else {}
                    period = meta.get("period", "")
                    unit = meta.get("unit", "")
                    if period:
                        period = f"{int(period) / 1000:.0f}s"
                    print(f"  {row[1]:<33} {row[3]!s:>7}  {period:>8}  {unit}")
            return

        if args.query:
            # Query a specific parameter
            param_row = db.execute(
                "SELECT Id, Data FROM param_info WHERE Name = ? COLLATE NOCASE", (args.query,)
            ).fetchone()
            if not param_row:
                # Try partial match
                param_row = db.execute(
                    "SELECT Id, Data FROM param_info WHERE Name LIKE ? COLLATE NOCASE",
                    (f"%{args.query}%",),
                ).fetchone()
            if not param_row:
                print(f"ERROR: Parameter '{args.query}' not found", file=sys.stderr)
                print(
                    "  Use: wican.py logs --params  to list available parameters", file=sys.stderr
                )
                sys.exit(1)

            param_id = param_row[0]
            limit = args.limit or 10

            try:
                rows = db.execute(
                    "SELECT timestamp, value FROM param_data "
                    "WHERE param_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (param_id, limit),
                ).fetchall()
            except sqlite3.DatabaseError:
                # DB partially corrupt — fall back to forward scan (oldest first)
                try:
                    rows = db.execute(
                        "SELECT timestamp, value FROM param_data WHERE param_id = ? LIMIT ?",
                        (param_id, limit),
                    ).fetchall()
                    if rows:
                        print(
                            "NOTE: Database partially corrupt, showing oldest available data",
                            file=sys.stderr,
                        )
                except sqlite3.DatabaseError as e:
                    print(f"ERROR: Database too corrupt to query: {e}", file=sys.stderr)
                    rows = []

            if args.json:
                result = [
                    {
                        "timestamp": r[0],
                        "time": datetime.fromtimestamp(r[0], tz=UTC).isoformat(),
                        "value": r[1],
                    }
                    for r in rows
                ]
                print(json.dumps(result, indent=2))
            else:
                meta = json.loads(param_row[1]) if param_row[1] else {}
                unit = meta.get("unit", "")
                print(f"Parameter: {args.query} (last {limit} values)")
                for row in rows:
                    ts = datetime.fromtimestamp(row[0], tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
                    print(f"  {ts}  {row[1]} {unit}")
            return

    # Default: list databases
    dbs = index.get("databases", [])
    current = index.get("current_db", "")

    if args.json:
        print(json.dumps(index, indent=2))
    else:
        print(f"Log databases ({len(dbs)}):")
        for db_info in dbs:
            fname = db_info["filename"]
            created = db_info.get("created", "?")
            size = db_info.get("size", 0)
            status = db_info.get("status", "")
            marker = " (active)" if fname == current or status == "active" else ""
            size_str = f"{size / 1024:.0f} KB" if size else "—"
            print(f"  {fname}  {created}  {size_str}{marker}")


def cmd_protocol(args) -> None:
    """View or switch the CAN protocol mode."""
    base_url = resolve_wican_url(args.wican)
    config = get_config(base_url, args.timeout)

    current = config.get("protocol", "unknown")
    port_type = config.get("port_type", "tcp")
    port = config.get("port", "3333")
    can_mode = config.get("can_mode", "normal")

    if not args.set:
        # Display current protocol and available options
        print(f"Current protocol: {current}")
        print(f"Port:             {port} ({port_type.upper()})")
        print(f"CAN mode:         {can_mode}")
        print()
        print("Available protocols:")
        for proto, desc in PROTOCOLS.items():
            marker = " ←" if proto == current else ""
            print(f"  {proto:<14} {desc}{marker}")
        print()
        print("Switch with: wican.py protocol --set <name>")
        return

    target = args.set.lower()

    # Accept common aliases
    aliases = {"autopid": "auto_pid", "realdash": "realdash66"}
    target = aliases.get(target, target)

    if target not in PROTOCOLS:
        print(f"ERROR: Unknown protocol '{args.set}'", file=sys.stderr)
        print(f"  Available: {', '.join(PROTOCOLS.keys())}", file=sys.stderr)
        sys.exit(1)

    if target == current:
        print(f"Already set to {target}")
        return

    # Build changes
    changes = {"protocol": target}

    # Set sensible defaults for port/type based on protocol
    if args.port:
        changes["port"] = str(args.port)
    if args.port_type:
        changes["port_type"] = args.port_type
    if args.can_mode:
        changes["can_mode"] = args.can_mode

    # Show what's happening and warn about consequences
    print(f"Protocol change: {current} → {target}")
    print()

    # Protocol-specific warnings
    warnings = []
    if current == "auto_pid" and target != "auto_pid":
        warnings.append(
            "AutoPID will STOP — no more MQTT data to Home Assistant. "
            "Vehicle parameters will not be polled or published until you switch back."
        )
    if target == "auto_pid" and current != "auto_pid":
        warnings.append(
            "AutoPID will START — vehicle profile must be configured. "
            "MQTT data feed to Home Assistant will resume."
        )
    if target == "slcan":
        warnings.append(
            f"SLCAN mode uses TCP port {changes.get('port', port)}. "
            "Connect SavvyCAN or socketcand to this port."
        )
    if target == "savvycan":
        warnings.append(
            f"SavvyCAN native mode uses TCP port {changes.get('port', port)}. "
            "Use 'Add Connection → Network' in SavvyCAN with this port."
        )
    if target == "elm327":
        warnings.append(
            f"ELM327 mode listens on {port_type.upper()} port {changes.get('port', port)}. "
            "Connect with any ELM327-compatible app (Torque, Car Scanner, etc.)."
        )
    if target == "realdash66":
        warnings.append(
            f"RealDash mode uses TCP port {changes.get('port', port)}. "
            "Configure RealDash to connect to this address and port."
        )

    # General warnings
    warnings.append("Protocols are MUTUALLY EXCLUSIVE — only one can be active at a time.")
    warnings.append("Device will REBOOT to apply the change.")

    for w in warnings:
        print(f"  ⚠ {w}")
    print()

    # Show diff
    print("Changes:")
    for key, new_val in changes.items():
        old_val = config.get(key, "")
        if old_val != new_val:
            print(f"  {key}: {old_val} → {new_val}")

    if args.dry_run:
        print("\n(dry run — no changes applied)")
        return

    print()
    if not args.yes and not confirm("Apply and reboot?"):
        print("Cancelled.")
        return

    # Apply: merge into full config and store (triggers reboot)
    for key, val in changes.items():
        config[key] = val

    print("Storing config...", end=" ", flush=True)
    store_config(base_url, config, args.timeout)
    print("device is rebooting.")


# ── Argument parsing ──────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WiCAN CLI — manage WiCAN Pro device configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wican",
        default=DEFAULT_WICAN,
        metavar="ADDR",
        help="Device address: home, vpn, or URL (default: home)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=WICAN_TIMEOUT,
        help=f"Request timeout in seconds (default: {WICAN_TIMEOUT})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── config ──
    p_config = sub.add_parser("config", help="View device configuration")
    p_config.add_argument(
        "--section", metavar="NAME", help=f"Filter to section: {', '.join(CONFIG_SECTIONS.keys())}"
    )
    p_config.add_argument("--json", action="store_true", help="Raw JSON output")
    p_config.add_argument("--save", action="store_true", help="Save snapshot to configs/ directory")
    p_config.set_defaults(func=cmd_config)

    # ── sleep ──
    p_sleep = sub.add_parser("sleep", help="View or modify sleep settings")
    grp = p_sleep.add_mutually_exclusive_group()
    grp.add_argument("--enable", action="store_true", help="Enable sleep mode")
    grp.add_argument("--disable", action="store_true", help="Disable sleep mode")
    p_sleep.add_argument(
        "--voltage", type=float, metavar="V", help="Sleep voltage threshold (e.g. 12.5)"
    )
    p_sleep.add_argument("--time", type=int, metavar="MIN", help="Sleep delay in minutes")
    p_sleep.add_argument(
        "--wakeup-interval", type=int, metavar="MIN", help="Periodic wakeup interval in minutes"
    )
    p_sleep.add_argument("--no-wakeup", action="store_true", help="Disable periodic wakeup")
    p_sleep.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    p_sleep.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_sleep.set_defaults(func=cmd_sleep)

    # ── reboot ──
    p_reboot = sub.add_parser("reboot", help="Reboot the device")
    p_reboot.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_reboot.set_defaults(func=cmd_reboot)

    # ── status ──
    p_status = sub.add_parser("status", help="Device status summary")
    p_status.add_argument("--json", action="store_true", help="Raw JSON output")
    p_status.set_defaults(func=cmd_status)

    # ── logs ──
    p_logs = sub.add_parser("logs", help="List, download, or query OBD log databases")
    p_logs.add_argument(
        "--download", action="store_true", help="Download log databases to logs/ directory"
    )
    p_logs.add_argument("--db", metavar="FILE", help="Specific database filename")
    p_logs.add_argument("--force", action="store_true", help="Overwrite existing files on download")
    p_logs.add_argument("--params", action="store_true", help="List all logged parameters")
    p_logs.add_argument("--query", metavar="PARAM", help="Query a parameter (e.g. SOC_BMS)")
    p_logs.add_argument(
        "--limit", type=int, default=10, help="Number of rows to return (default: 10)"
    )
    p_logs.add_argument("--json", action="store_true", help="JSON output")
    p_logs.set_defaults(func=cmd_logs)

    # ── protocol ──
    p_proto = sub.add_parser("protocol", help="View or switch CAN protocol mode")
    p_proto.add_argument(
        "--set", metavar="MODE", help=f"Switch to protocol: {', '.join(PROTOCOLS.keys())}"
    )
    p_proto.add_argument(
        "--port", type=int, metavar="PORT", help="TCP/UDP port number (default varies by protocol)"
    )
    p_proto.add_argument("--port-type", choices=["tcp", "udp"], help="Port type: tcp or udp")
    p_proto.add_argument(
        "--can-mode",
        choices=["normal", "silent"],
        help="CAN mode: normal (read/write) or silent (read-only)",
    )
    p_proto.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    p_proto.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_proto.set_defaults(func=cmd_protocol)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
