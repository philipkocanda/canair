"""
Generate WiCAN vehicle profile JSON from the ecus/ directory.

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
    import yaml  # noqa: F401
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None  # Only needed for upload/download

from canlib.constants import DEFAULT_WICAN, WICAN_ADDRESSES

NAME = "wican"

WICAN_TIMEOUT = 10  # seconds


def _require_pro(operation: str) -> int | None:
    """Return an error code if the configured WiCAN is not a Pro.

    AutoPID vehicle profiles (upload/download/diff) and device protocol
    switching are WiCAN Pro-only features. The classic (non-Pro) WiCAN has no
    AutoPID support, so we refuse these device operations up front with a clear
    message instead of letting them fail obscurely against the device. Returns
    ``None`` when the model is Pro (proceed) or an int exit code to abort.
    """
    from canlib.config import is_wican_pro

    if is_wican_pro():
        return None
    print(
        f"error: `canair wican {operation}` needs a WiCAN Pro — the classic "
        "(non-Pro) WiCAN has no AutoPID / vehicle-profile support.\n"
        "        Your config sets wican_model: classic. Generating profile JSON "
        "still works — run `canair wican` (no device flags) to write out/profile.json.\n"
        "        If this device is actually a Pro, run: canair config set wican_model pro",
        file=sys.stderr,
    )
    return 2


def _profile_out() -> "object":
    """Default output path for the generated WiCAN profile JSON."""
    from canlib.profile import active

    return active().out_dir / "profile.json"


def load_yaml() -> dict:
    """Load and return the YAML PID definitions."""
    from canlib.pids import load_pids

    return load_pids()


def make_pid_init(tx_id: int, session: bool = False) -> str:
    """Generate AT header init string from TX ID.

    If session=True, prepend a UDS extended diagnostic session request (10 03)
    before setting headers. This is only needed by ECUs that reject 22xx DID
    reads in the default session; on the Ioniq 2017, SKM is the known example.
    (IGPM was previously flagged here but its service-22 reads work fine in the
    default session — verified 2026-07-21.)
    """
    hex_id = f"{tx_id:03X}"
    init = f"ATSH{hex_id};ATFCSH{hex_id};"
    if session:
        init += "1003;"
    return init


def generate_profile(data: dict, verified_only: bool = False) -> dict:
    """Generate Vehicle Profile format JSON (grouped parameters per PID).

    Produces the upstream source format where parameters is a dict of
    {"PARAM_NAME": "expression"} pairs. This format is used for:
    - The output JSON file (upstream PR-compatible)
    - Input to to_device_format() for upload to WiCAN

    The firmware does NOT accept this format directly — use to_device_format()
    to convert before uploading.
    """
    profile = {
        "car_model": data["car_model"],
        "init": data["init"],
        "pids": [],
        "can_filters": [],
    }

    for _ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        session = ecu.get("session", False)
        pid_init = make_pid_init(tx_id, session=session)

        for pid_code, pid_data in (ecu.get("pids") or {}).items():
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

            profile["pids"].append(
                {
                    "pid_init": pid_init,
                    "pid": str(pid_code),
                    "enabled": True,
                    "period": str(pid_data.get("period", 5000)),
                    "parameters": parameters,
                }
            )

    return profile


def to_device_format(profile: dict, data: dict | None = None) -> dict:
    """Convert grouped profile to the device's expected format for upload.

    The firmware (autopid.c load_all_pids()) expects:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}

    Each PID entry must have parameters as an array of objects:
      [{"name": "SOC", "expression": "B09/2", "unit": "%", "class": "battery",
        "period": "5000", "min": "", "max": "", "type": "Default", "send_to": ""}]

    The web UI's build system (cars.js process_profile) does this same conversion
    when building vehicle_profiles.json from upstream source files.

    Args:
        profile: Grouped profile from generate_profile() (dict-format parameters)
        data: Optional YAML data for looking up unit/class/min/max per parameter
    """
    # Build parameter metadata lookup from YAML if provided
    param_meta = {}
    if data:
        for ecu in data["ecus"].values():
            for pid_data in (ecu.get("pids") or {}).values():
                for param_name, param in pid_data.get("parameters", {}).items():
                    param_meta[param_name] = param

    device_profile = {
        "car_model": profile["car_model"],
        "init": profile["init"],
        "pids": [],
    }

    for pid_entry in profile["pids"]:
        params_array = []
        for name, expression in pid_entry["parameters"].items():
            meta = param_meta.get(name, {})
            params_array.append(
                {
                    "name": name,
                    "expression": expression,
                    "unit": meta.get("unit", ""),
                    "class": meta.get("ha_class", "none") or "none",
                    "period": pid_entry.get("period", "5000"),
                    "min": str(meta.get("min", "")) if meta.get("min", "") != "" else "",
                    "max": str(meta.get("max", "")) if meta.get("max", "") != "" else "",
                    "type": "Default",
                    "send_to": "",
                }
            )

        device_profile["pids"].append(
            {
                "pid_init": pid_entry["pid_init"],
                "pid": pid_entry["pid"],
                "enabled": pid_entry.get("enabled", True),
                "parameters": params_array,
            }
        )

    return {"cars": [device_profile]}


def normalize_device_profile(device_data: dict) -> dict:
    """Normalize the device format back to grouped Vehicle Profile format.

    The device stores whatever is POSTed to /store_car_data verbatim.
    Depending on how the profile was uploaded, parameters may be:
    - An array of objects (from our upload or the web UI): [{name, expression, ...}]
    - A dict (if someone uploaded upstream source format): {NAME: expression}

    In both cases, the web UI creates one PID entry per parameter (flat), but
    our upload groups multiple parameters per PID entry. This normalizes
    everything to grouped dict format for diffing.
    """
    if device_data.get("cars"):
        car = device_data["cars"][0]
    else:
        car = device_data

    from collections import OrderedDict

    groups = OrderedDict()

    for entry in car.get("pids", []):
        key = (entry.get("pid_init", ""), entry.get("pid", ""))
        if key not in groups:
            groups[key] = {
                "pid_init": entry.get("pid_init", ""),
                "pid": entry.get("pid", ""),
                "enabled": entry.get("enabled", True),
                "period": "5000",
                "parameters": {},
            }

        params = entry.get("parameters", {})

        if isinstance(params, list):
            # Array-of-objects format: [{name, expression, period, ...}]
            for param in params:
                name = param.get("name", "")
                expr = param.get("expression", "")
                if name:
                    groups[key]["parameters"][name] = expr
                # Use period from first parameter if available
                if "period" in param and groups[key]["period"] == "5000":
                    groups[key]["period"] = str(param["period"])
        elif isinstance(params, dict):
            # Dict format: {NAME: expression}
            for name, expr in params.items():
                groups[key]["parameters"][name] = expr

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
    """Resolve WiCAN address name to HTTP base URL."""
    if address.startswith("http"):
        return address
    addr = WICAN_ADDRESSES.get(address, WICAN_ADDRESSES.get(DEFAULT_WICAN, address))
    if not addr.startswith("http"):
        return f"http://{addr}"
    return addr


def require_requests():
    """Check requests library is available."""
    if requests is None:
        print(
            "ERROR: 'requests' library not installed. Run: pip3 install requests",
            file=sys.stderr,
        )
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
        print(
            f"\n  Summary: +{added_pids} PIDs, -{removed_pids} PIDs, "
            f"+{added_params} params, -{removed_params} params, ~{changed_params} changed"
        )

    return has_diff


def upload_profile(base_url: str, device_payload: dict, reboot: bool = False) -> None:
    """Upload vehicle profile to WiCAN device via POST /store_car_data.

    Expects device_payload in the firmware's format:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}
    with parameters as array-of-objects. Use to_device_format() to convert.
    """
    require_requests()

    n_pids = len(device_payload.get("cars", [{}])[0].get("pids", []))
    n_params = sum(
        len(p.get("parameters", [])) for p in device_payload.get("cars", [{}])[0].get("pids", [])
    )

    url = f"{base_url}/store_car_data"
    try:
        resp = requests.post(url, json=device_payload, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        print(f"  Uploaded to {url} — {resp.status_code} ({n_pids} PIDs, {n_params} params)")
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

    print(
        f"\n{'ECU':<10} {'TX ID':<8} {'PID':<10} {'Period':<8} {'Params':<8} {'Verified':<10} {'Source Summary'}"
    )
    print("─" * 100)

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        for pid_code, pid_data in (ecu.get("pids") or {}).items():
            params = pid_data["parameters"]
            n_params = len(params)
            n_verified = sum(1 for p in params.values() if p.get("verified", False))
            n_unverified = n_params - n_verified
            total_params += n_params
            verified_count += n_verified
            unverified_count += n_unverified

            sources = {p.get("source", "?") for p in params.values()}
            source_str = "; ".join(sorted(sources))[:40]

            v_str = f"{n_verified}/{n_params}"
            print(
                f"{ecu_name:<10} 0x{tx_id:03X}    {pid_code!s:<10} {pid_data.get('period', '?')!s:<8} {n_params:<8} {v_str:<10} {source_str}"
            )

    print("─" * 100)
    print(
        f"{'TOTAL':<10} {'':8} {'':10} {'':8} {total_params:<8} {verified_count}/{total_params} verified ({unverified_count} unverified)"
    )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Generate/sync WiCAN vehicle-profile JSON",
        description="Generate WiCAN vehicle profile JSON from YAML definitions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--verified-only", action="store_true", help="Only include verified parameters"
    )
    parser.add_argument(
        "--download", action="store_true", help="Download current config from WiCAN"
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Download current config and show diff against generated",
    )
    parser.add_argument("--upload", action="store_true", help="Upload generated profile to WiCAN")
    parser.add_argument("--reboot", action="store_true", help="Reboot WiCAN after upload")
    parser.add_argument("--stats", action="store_true", help="Show PID statistics table")
    parser.add_argument(
        "--set-protocol",
        metavar="MODE",
        choices=("elm327", "slcan", "savvycan", "realdash66", "auto_pid"),
        default=None,
        help="Set the WiCAN device protocol/mode and reboot (explicit; e.g. "
        "'slcan' for raw CAN, 'auto_pid' to restore Home Assistant). Use --yes "
        "to skip the confirmation prompt.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Auto-confirm --set-protocol (no prompt)"
    )
    parser.add_argument(
        "--wican",
        default=DEFAULT_WICAN,
        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or URL (default: {DEFAULT_WICAN})",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="Don't write output files (dry run)"
    )
    parser.set_defaults(func=run)
    return parser


def _set_protocol(args) -> int:
    """Explicitly set the WiCAN device protocol/mode (reboots). Opt-in only."""
    from canlib.wican_mode import ModeError, current_protocol, set_protocol

    guard = _require_pro("--set-protocol")
    if guard is not None:
        return guard

    base_url = get_wican_url(args.wican)
    target = args.set_protocol
    try:
        cur = current_protocol(base_url)
    except Exception as e:
        print(f"error: cannot reach WiCAN at {base_url}: {e}", file=sys.stderr)
        return 1

    if cur == target:
        print(f"WiCAN already in '{target}' mode.")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print(
                f"error: refusing to switch '{cur}' -> '{target}' without --yes "
                f"(non-interactive).",
                file=sys.stderr,
            )
            return 2
        resp = input(
            f"Switch WiCAN from '{cur}' to '{target}'? This reboots the device "
            f"(~5s) and interrupts its current mode. [y/N] "
        )
        if resp.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    print(f"Switching WiCAN '{cur}' -> '{target}' (rebooting)...")
    try:
        set_protocol(base_url, target)
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        now = current_protocol(base_url)
    except Exception:
        now = "?"
    if now == target:
        print(f"WiCAN now in '{target}' mode.")
        return 0
    print(f"warning: WiCAN reports '{now}' after switch.", file=sys.stderr)
    return 1


def run(args) -> int:
    from canlib.profile import active

    # Explicit device-mode switch — self-contained, no profile generation.
    if args.set_protocol:
        return _set_protocol(args)

    # AutoPID device sync is Pro-only; refuse it on a classic WiCAN before we
    # do any work. Plain JSON generation (below) works regardless of model.
    if args.download or args.diff or args.upload:
        guard = _require_pro(
            "--upload" if args.upload else ("--diff" if args.diff else "--download")
        )
        if guard is not None:
            return guard

    profile_out = _profile_out()    # Load YAML
    print(f"Loading {active().ecus_dir}")
    data = load_yaml()

    if args.stats:
        print_stats(data)
        return 0

    # Generate profile
    label = " (verified only)" if args.verified_only else ""
    print(f"\nGenerating profile{label}...")

    profile = generate_profile(data, args.verified_only)

    n_groups = len(profile["pids"])
    n_params = sum(len(p["parameters"]) for p in profile["pids"])
    print(f"  {n_groups} PID groups, {n_params} parameters")

    # Write file
    if not args.no_write:
        print("\nWriting output...")
        write_json(profile, profile_out)

    # Download / diff
    base_url = get_wican_url(args.wican)

    if args.download or args.diff:
        print(f"\nDownloading current config from {base_url}...")
        current_raw = download_profile(base_url)

        if args.download and not args.diff:
            if current_raw:
                normalized = normalize_device_profile(current_raw)
                print("\n=== Current device profile (normalized) ===")
                print(json.dumps(normalized, indent=2))

        if args.diff:
            show_diff(current_raw, profile)

    # Upload
    if args.upload:
        print("\nConverting to device format...")
        device_payload = to_device_format(profile, data)
        n_pids = len(device_payload["cars"][0]["pids"])
        n_dev_params = sum(len(p["parameters"]) for p in device_payload["cars"][0]["pids"])
        print(f"  {n_pids} PID groups, {n_dev_params} parameters (array-of-objects)")

        print(f"\nUploading to {base_url}...")
        upload_profile(base_url, device_payload, reboot=args.reboot)

    print("\nDone.")
    return 0
