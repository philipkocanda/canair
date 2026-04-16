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

  python3 wican.py reboot                           # Reboot device
  python3 wican.py reboot --yes                     # Skip confirmation

Global flags:
  --wican home|vpn|<url>   Device address (default: home)
  --timeout <seconds>      Request timeout (default: 10)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip3 install requests", file=sys.stderr)
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIGS_DIR = SCRIPT_DIR / "configs"

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
        "batt_alert", "batt_alert_ssid", "batt_alert_pass",
        "batt_alert_volt", "batt_alert_protocol", "batt_alert_url",
        "batt_alert_port", "batt_alert_topic", "batt_alert_time",
        "batt_mqtt_user", "batt_mqtt_pass",
    ],
    "mqtt": [
        "mqtt_en", "mqtt_url", "mqtt_port", "mqtt_user", "mqtt_pass",
        "mqtt_tx_topic", "mqtt_rx_topic", "mqtt_status_topic",
    ],
    "wifi": [
        "ssid", "password", "ssid_st", "password_st", "sta_channel",
        "ap_ch",
    ],
    "can": [
        "port", "protocol", "can_datarate", "can_mode",
    ],
}


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


# ── Argument parsing ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="WiCAN CLI — manage WiCAN Pro device configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wican", default=DEFAULT_WICAN, metavar="ADDR",
        help="Device address: home, vpn, or URL (default: home)",
    )
    parser.add_argument(
        "--timeout", type=int, default=WICAN_TIMEOUT,
        help=f"Request timeout in seconds (default: {WICAN_TIMEOUT})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── config ──
    p_config = sub.add_parser("config", help="View device configuration")
    p_config.add_argument("--section", metavar="NAME",
                          help=f"Filter to section: {', '.join(CONFIG_SECTIONS.keys())}")
    p_config.add_argument("--json", action="store_true", help="Raw JSON output")
    p_config.add_argument("--save", action="store_true",
                          help="Save snapshot to configs/ directory")
    p_config.set_defaults(func=cmd_config)

    # ── sleep ──
    p_sleep = sub.add_parser("sleep", help="View or modify sleep settings")
    grp = p_sleep.add_mutually_exclusive_group()
    grp.add_argument("--enable", action="store_true", help="Enable sleep mode")
    grp.add_argument("--disable", action="store_true", help="Disable sleep mode")
    p_sleep.add_argument("--voltage", type=float, metavar="V",
                         help="Sleep voltage threshold (e.g. 12.5)")
    p_sleep.add_argument("--time", type=int, metavar="MIN",
                         help="Sleep delay in minutes")
    p_sleep.add_argument("--wakeup-interval", type=int, metavar="MIN",
                         help="Periodic wakeup interval in minutes")
    p_sleep.add_argument("--no-wakeup", action="store_true",
                         help="Disable periodic wakeup")
    p_sleep.add_argument("--dry-run", action="store_true",
                         help="Preview changes without applying")
    p_sleep.add_argument("--yes", "-y", action="store_true",
                         help="Skip confirmation prompt")
    p_sleep.set_defaults(func=cmd_sleep)

    # ── reboot ──
    p_reboot = sub.add_parser("reboot", help="Reboot the device")
    p_reboot.add_argument("--yes", "-y", action="store_true",
                          help="Skip confirmation prompt")
    p_reboot.set_defaults(func=cmd_reboot)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
