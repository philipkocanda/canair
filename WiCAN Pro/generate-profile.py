#!/usr/bin/env python3
"""
Generate WiCAN vehicle profile JSON from ioniq-2017-pids.yaml.

Produces the Vehicle Profile format (grouped parameters per PID) which is
the format accepted by the WiCAN web UI for upload via POST /store_car_data.

Can also download current config from WiCAN and show diff, or upload directly.

Usage:
  python3 generate-profile.py                    # Generate JSON file
  python3 generate-profile.py --verified-only     # Only include verified PIDs
  python3 generate-profile.py --download          # Download current config from WiCAN
  python3 generate-profile.py --diff              # Download + diff against generated
  python3 generate-profile.py --upload            # Generate + upload to WiCAN
  python3 generate-profile.py --upload --reboot   # Upload + reboot WiCAN
  python3 generate-profile.py --stats             # Show PID statistics table
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None  # Only needed for upload/download

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
YAML_SOURCE = SCRIPT_DIR / "ioniq-2017-pids.yaml"
PROFILE_OUT = SCRIPT_DIR / "Vehicle Profiles" / "ioniq-2017.json"

# ── WiCAN addresses ───────────────────────────────────────────────────────
WICAN_ADDRESSES = {
    "home": "http://10.0.2.86",
    "vpn": "http://192.168.3.2",
}
DEFAULT_WICAN = "home"
WICAN_TIMEOUT = 10  # seconds


def load_yaml() -> dict:
    """Load and return the YAML PID definitions."""
    with open(YAML_SOURCE) as f:
        return yaml.safe_load(f)


def make_pid_init(tx_id: int) -> str:
    """Generate AT header init string from TX ID."""
    hex_id = f"{tx_id:03X}"
    return f"ATSH{hex_id};ATFCSH{hex_id};"


def generate_profile(data: dict, verified_only: bool = False) -> dict:
    """Generate Vehicle Profile format JSON (grouped parameters per PID).

    This is the format accepted by WiCAN for upload. Each PID entry has a
    parameters dict of {"PARAM_NAME": "expression"} pairs.
    """
    profile = {
        "car_model": data["car_model"],
        "init": data["init"],
        "pids": [],
        "can_filters": [],
    }

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        pid_init = make_pid_init(tx_id)

        for pid_code, pid_data in ecu["pids"].items():
            if not pid_data.get("enabled", True):
                continue

            parameters = {}
            for param_name, param in pid_data["parameters"].items():
                if not param.get("enabled", True):
                    continue
                if verified_only and not param.get("verified", False):
                    continue
                parameters[param_name] = param["expression"]

            if not parameters:
                continue

            profile["pids"].append({
                "pid_init": pid_init,
                "pid": str(pid_code),
                "enabled": True,
                "period": str(pid_data.get("period", 5000)),
                "parameters": parameters,
            })

    return profile


def normalize_device_profile(device_data: dict) -> dict:
    """Normalize the flat device format back to grouped Vehicle Profile format.

    The device stores one parameter per entry. This groups them back by
    (pid_init, pid) so they can be compared against the generated profile.
    """
    if "cars" in device_data and device_data["cars"]:
        car = device_data["cars"][0]
    else:
        car = device_data

    from collections import OrderedDict
    groups = OrderedDict()

    for entry in car.get("pids", []):
        key = (entry.get("pid_init", ""), entry.get("pid", ""))
        if key not in groups:
            period = entry["parameters"][0].get("period", "5000") if entry.get("parameters") else "5000"
            groups[key] = {
                "pid_init": entry.get("pid_init", ""),
                "pid": entry.get("pid", ""),
                "enabled": entry.get("enabled", True),
                "period": period,
                "parameters": {},
            }
        for param in entry.get("parameters", []):
            groups[key]["parameters"][param["name"]] = param["expression"]

    return {
        "car_model": car.get("car_model", ""),
        "init": car.get("init", ""),
        "pids": list(groups.values()),
        "can_filters": [],
    }


def write_json(data: dict, path: Path) -> None:
    """Write JSON to file with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"  Written: {path} ({len(json.dumps(data))} bytes)")


def get_wican_url(address: str) -> str:
    """Resolve WiCAN address name to URL."""
    if address.startswith("http"):
        return address
    return WICAN_ADDRESSES.get(address, WICAN_ADDRESSES[DEFAULT_WICAN])


def require_requests():
    """Check requests library is available."""
    if requests is None:
        print("ERROR: 'requests' library not installed. Run: pip3 install requests",
              file=sys.stderr)
        sys.exit(1)


def download_profile(base_url: str) -> dict | None:
    """Download current vehicle profile from WiCAN device (raw device format)."""
    require_requests()
    url = f"{base_url}/load_auto_pid_car_data"
    try:
        resp = requests.get(url, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        n_entries = len(data.get("cars", [{}])[0].get("pids", []))
        print(f"  Downloaded from {url} ({n_entries} entries)")
        return data
    except requests.RequestException as e:
        print(f"  FAILED to download from {url}: {e}", file=sys.stderr)
        return None


def show_diff(current_raw: dict | None, generated: dict) -> bool:
    """Show parameter-level diff between device and generated profile.

    Normalizes the device's flat format to grouped format for comparison.
    Returns True if there are differences.
    """
    if current_raw is None:
        print("\n  No current config to compare (download failed)")
        return True

    current = normalize_device_profile(current_raw)

    # Build lookup: (pid_init, pid) -> {param: expression}
    def profile_map(profile):
        result = {}
        for entry in profile["pids"]:
            key = (entry["pid_init"], entry["pid"])
            result[key] = entry["parameters"]
        return result

    cur_map = profile_map(current)
    gen_map = profile_map(generated)

    all_keys = sorted(set(list(cur_map.keys()) + list(gen_map.keys())))

    has_diff = False
    added_params = 0
    removed_params = 0
    changed_params = 0
    added_pids = 0
    removed_pids = 0

    for key in all_keys:
        pid_init, pid = key
        cur_params = cur_map.get(key, {})
        gen_params = gen_map.get(key, {})

        if not cur_params and gen_params:
            added_pids += 1
            added_params += len(gen_params)
            has_diff = True
            print(f"\n  + NEW PID: {pid_init} {pid} ({len(gen_params)} params)")
            for name in gen_params:
                print(f"      + {name}: {gen_params[name]}")
            continue

        if cur_params and not gen_params:
            removed_pids += 1
            removed_params += len(cur_params)
            has_diff = True
            print(f"\n  - REMOVED PID: {pid_init} {pid} ({len(cur_params)} params)")
            for name in cur_params:
                print(f"      - {name}: {cur_params[name]}")
            continue

        # Both exist — compare parameters
        all_param_names = sorted(set(list(cur_params.keys()) + list(gen_params.keys())))
        pid_diffs = []
        for name in all_param_names:
            if name not in cur_params:
                pid_diffs.append(f"      + {name}: {gen_params[name]}")
                added_params += 1
            elif name not in gen_params:
                pid_diffs.append(f"      - {name}: {cur_params[name]}")
                removed_params += 1
            elif cur_params[name] != gen_params[name]:
                pid_diffs.append(f"      ~ {name}: {cur_params[name]} → {gen_params[name]}")
                changed_params += 1

        if pid_diffs:
            has_diff = True
            print(f"\n  ~ CHANGED: {pid_init} {pid}")
            for d in pid_diffs:
                print(d)

    if not has_diff:
        print("\n  No differences between device and generated profile")
    else:
        print(f"\n  Summary: +{added_pids} PIDs, -{removed_pids} PIDs, "
              f"+{added_params} params, -{removed_params} params, ~{changed_params} changed")

    return has_diff


def upload_profile(base_url: str, profile: dict, reboot: bool = False) -> None:
    """Upload vehicle profile to WiCAN device via POST /store_car_data."""
    require_requests()

    url = f"{base_url}/store_car_data"
    try:
        resp = requests.post(url, json=profile, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        print(f"  Uploaded to {url} — {resp.status_code}")
    except requests.RequestException as e:
        print(f"  FAILED to upload to {url}: {e}", file=sys.stderr)
        sys.exit(1)

    if reboot:
        time.sleep(0.3)
        url = f"{base_url}/system_reboot"
        try:
            resp = requests.post(url, data="reboot", timeout=WICAN_TIMEOUT)
            print(f"  Rebooting WiCAN... ({resp.status_code})")
        except requests.RequestException as e:
            print(f"  FAILED to reboot: {e}", file=sys.stderr)


def print_stats(data: dict) -> None:
    """Print a summary table of all PIDs and parameters."""
    total_params = 0
    verified_count = 0
    unverified_count = 0

    print(f"\n{'ECU':<10} {'TX ID':<8} {'PID':<10} {'Period':<8} {'Params':<8} {'Verified':<10} {'Source Summary'}")
    print("─" * 100)

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        for pid_code, pid_data in ecu["pids"].items():
            params = pid_data["parameters"]
            n_params = len(params)
            n_verified = sum(1 for p in params.values() if p.get("verified", False))
            n_unverified = n_params - n_verified
            total_params += n_params
            verified_count += n_verified
            unverified_count += n_unverified

            sources = set(p.get("source", "?") for p in params.values())
            source_str = "; ".join(sorted(sources))[:40]

            v_str = f"{n_verified}/{n_params}"
            print(f"{ecu_name:<10} 0x{tx_id:03X}    {pid_code!s:<10} {pid_data.get('period', '?')!s:<8} {n_params:<8} {v_str:<10} {source_str}")

    print("─" * 100)
    print(f"{'TOTAL':<10} {'':8} {'':10} {'':8} {total_params:<8} {verified_count}/{total_params} verified ({unverified_count} unverified)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate WiCAN vehicle profile JSON from YAML definitions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--verified-only", action="store_true",
                        help="Only include verified parameters")
    parser.add_argument("--download", action="store_true",
                        help="Download current config from WiCAN")
    parser.add_argument("--diff", action="store_true",
                        help="Download current config and show diff against generated")
    parser.add_argument("--upload", action="store_true",
                        help="Upload generated profile to WiCAN")
    parser.add_argument("--reboot", action="store_true",
                        help="Reboot WiCAN after upload")
    parser.add_argument("--stats", action="store_true",
                        help="Show PID statistics table")
    parser.add_argument("--wican", default=DEFAULT_WICAN,
                        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or URL (default: {DEFAULT_WICAN})")
    parser.add_argument("--no-write", action="store_true",
                        help="Don't write output files (dry run)")
    args = parser.parse_args()

    # Load YAML
    print(f"Loading {YAML_SOURCE}")
    data = load_yaml()

    if args.stats:
        print_stats(data)
        return

    # Generate profile
    label = " (verified only)" if args.verified_only else ""
    print(f"\nGenerating profile{label}...")

    profile = generate_profile(data, args.verified_only)

    n_groups = len(profile["pids"])
    n_params = sum(len(p["parameters"]) for p in profile["pids"])
    print(f"  {n_groups} PID groups, {n_params} parameters")

    # Write file
    if not args.no_write:
        print(f"\nWriting output...")
        write_json(profile, PROFILE_OUT)

    # Download / diff
    base_url = get_wican_url(args.wican)

    if args.download or args.diff:
        print(f"\nDownloading current config from {base_url}...")
        current_raw = download_profile(base_url)

        if args.download and not args.diff:
            if current_raw:
                normalized = normalize_device_profile(current_raw)
                print(f"\n=== Current device profile (normalized) ===")
                print(json.dumps(normalized, indent=2))

        if args.diff:
            show_diff(current_raw, profile)

    # Upload
    if args.upload:
        print(f"\nUploading to {base_url}...")
        upload_profile(base_url, profile, reboot=args.reboot)

    print("\nDone.")


if __name__ == "__main__":
    main()
