"""
Build and sync the WiCAN device's AutoPID vehicle profile.

`canair wican` groups two families of device operations. Nothing is written
until you ask for it — a bare `canair wican` just prints this help.

Terminology (deliberately disambiguated):
  * the canair *profile bundle* — the `profiles/<name>/` directory (ecus/,
    profile.yaml, captures/, …) managed by `canair profile`; and
  * the WiCAN *AutoPID profile* — the JSON generated from that bundle's
    ecus/ and stored on the device's AutoPID feature, managed here.

Subcommands:
  autopid write               Generate AutoPID JSON to the bundle's out/
  autopid upload              Generate + upload to the device (Pro only)
  autopid download            Download the device's current AutoPID JSON (Pro)
  autopid diff                Download + diff against the generated JSON (Pro)
  autopid stats               Per-ECU/PID statistics table
  mode show                   Show the device's active protocol
  mode set MODE               Switch the device protocol/mode + align transport (Pro)

Examples:
  canair wican autopid write                       # verified-only out/autopid.json
  canair wican autopid write --include-unverified  # also include unverified params
  canair wican autopid write --expected-responses  # faster reads where counts are known
  canair wican autopid upload --reboot             # upload + reboot to apply
  canair wican autopid diff --wican home           # compare device vs generated
  canair wican mode set slcan                 # raw CAN + set transport slcan-tcp
"""

import argparse
import json
import sys
import time
from pathlib import Path
from types import ModuleType

try:
    import yaml  # noqa: F401
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

from canlib.autopid_profile import (
    DuplicateParameterError,
    generate_profile,
    normalize_device_profile,
    to_device_format,
)
from canlib.commands._group import group_help
from canlib.response_frames import stored_count
from canlib.transport.config import TransportType


# Declared optional so the ImportError fallback to None is type-legal. The
# import is isolated in a helper so the module name isn't rebound to None
# (which would conflict with its module-typed import binding).
def _try_import_requests() -> ModuleType | None:
    try:
        import requests  # Only needed for upload/download

        return requests
    except ImportError:
        return None


requests: ModuleType | None = _try_import_requests()

NAME = "wican"

WICAN_TIMEOUT = 10  # seconds


def _require_pro(operation: str) -> int | None:
    """Return an error code if the configured WiCAN is not a Pro.

    AutoPID profile sync (upload/download/diff) and device protocol switching
    are WiCAN Pro-only features. The classic (non-Pro) WiCAN has no AutoPID
    support, so we refuse these device operations up front with a clear message
    instead of letting them fail obscurely against the device. Returns ``None``
    when the model is Pro (proceed) or an int exit code to abort.
    """
    from canlib.config import is_wican_pro

    if is_wican_pro():
        return None
    print(
        f"error: `canair wican {operation}` needs a WiCAN Pro — the classic "
        "(non-Pro) WiCAN has no AutoPID / vehicle-profile support.\n"
        "        Your config sets wican_model: classic. Generating the AutoPID "
        "JSON still works — run `canair wican autopid write` to write out/autopid.json.\n"
        "        If this device is actually a Pro, run: canair config set wican_model pro",
        file=sys.stderr,
    )
    return 2


def _profile_out() -> Path:
    """Default output path for the generated WiCAN profile JSON."""
    from canlib.profile import active

    return active().out_dir / "autopid.json"


def load_yaml() -> dict:
    """Load and return the YAML PID definitions."""
    from canlib.pids import load_pids

    return load_pids()


def write_json(data: dict, path: Path) -> None:
    """Write JSON to file with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"  Written: {path} ({len(json.dumps(data))} bytes)")


def get_wican_url(address: str) -> str:
    """Resolve WiCAN address name to HTTP base URL."""
    from canlib.config import default_wican, wican_addresses

    if address.startswith("http"):
        return address
    addresses = wican_addresses()
    addr = addresses.get(address, addresses.get(default_wican(), address))
    if not addr.startswith("http"):
        return f"http://{addr}"
    return addr


def require_requests() -> ModuleType:
    """Check requests library is available; return the narrowed module."""
    if requests is None:
        print(
            "ERROR: 'requests' library not installed. Run: pip3 install requests",
            file=sys.stderr,
        )
        sys.exit(1)
    return requests


def download_profile(base_url: str) -> dict | None:
    """Download current vehicle profile from WiCAN device (raw device format)."""
    req = require_requests()
    url = f"{base_url}/load_auto_pid_car_data"
    try:
        resp = req.get(url, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        n_entries = len(data.get("cars", [{}])[0].get("pids", []))
        print(f"  Downloaded from {url} ({n_entries} entries)")
        return data
    except req.RequestException as e:
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
    req = require_requests()

    n_pids = len(device_payload.get("cars", [{}])[0].get("pids", []))
    n_params = sum(
        len(p.get("parameters", [])) for p in device_payload.get("cars", [{}])[0].get("pids", [])
    )

    url = f"{base_url}/store_car_data"
    try:
        resp = req.post(url, json=device_payload, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        print(f"  Uploaded to {url} — {resp.status_code} ({n_pids} PIDs, {n_params} params)")
    except req.RequestException as e:
        print(f"  FAILED to upload to {url}: {e}", file=sys.stderr)
        sys.exit(1)

    if reboot:
        time.sleep(0.3)
        url = f"{base_url}/system_reboot"
        try:
            resp = req.post(url, data="reboot", timeout=WICAN_TIMEOUT)
            print(f"  Rebooting WiCAN... ({resp.status_code})")
        except req.RequestException as e:
            print(f"  FAILED to reboot: {e}", file=sys.stderr)


def print_stats(data: dict) -> None:
    """Print a summary table of all PIDs and parameters."""
    total_params = 0
    verified_count = 0
    unverified_count = 0
    counted = 0
    total_pids = 0

    header = (
        f"\n{'ECU':<10} {'TX ID':<8} {'PID':<10} {'Period':<8} {'Frames':<8} "
        f"{'Params':<8} {'Verified':<10} {'Source Summary'}"
    )
    print(header)
    print("─" * 100)

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        for pid_code, pid_data in (ecu.get("pids") or {}).items():
            params = pid_data.get("parameters") or {}
            n_params = len(params)
            n_verified = sum(1 for p in params.values() if p.get("verified", False))
            n_unverified = n_params - n_verified
            total_params += n_params
            verified_count += n_verified
            unverified_count += n_unverified
            total_pids += 1

            sources = {p.get("source", "?") for p in params.values()}
            source_str = "; ".join(sorted(sources))[:40]

            # A dash means the PID still pays the adapter's full response-wait
            # budget; the count is only shown once wire evidence confirmed it.
            frames = stored_count(pid_data)
            if frames is not None:
                counted += 1
            frames_str = str(frames) if frames is not None else "—"

            v_str = f"{n_verified}/{n_params}"
            print(
                f"{ecu_name:<10} 0x{tx_id:03X}    {pid_code!s:<10} "
                f"{pid_data.get('period', '?')!s:<8} {frames_str:<8} "
                f"{n_params:<8} {v_str:<10} {source_str}"
            )

    print("─" * 100)
    print(
        f"{'TOTAL':<10} {'':8} {'':10} {'':8} {f'{counted}/{total_pids}':<8} "
        f"{total_params:<8} {verified_count}/{total_params} verified "
        f"({unverified_count} unverified)"
    )
    print(f"\n{counted}/{total_pids} PIDs carry a response_frames count.")


_PROTOCOLS = ("elm327", "slcan", "savvycan", "realdash66", "auto_pid")

# canair drives the bus in exactly two device modes; `mode set` aligns the
# config transport.type to match so switching the device doesn't leave the
# transport pointing at the wrong backend (the usual foot-gun). The other modes
# (auto_pid/savvycan/realdash66) have no request/response transport, so the
# transport is left untouched with a note.
_MODE_TO_TRANSPORT: dict[str, TransportType] = {
    "slcan": "slcan-tcp",
    "elm327": "wican-ws",
}


def _add_wican_arg(parser: argparse.ArgumentParser) -> None:
    from canlib.config import default_wican, wican_addresses

    default = default_wican()
    parser.add_argument(
        "--wican",
        default=default,
        help=f"WiCAN address: {', '.join(wican_addresses())} or URL (default: {default})",
    )


def _add_generate_args(parser: argparse.ArgumentParser) -> None:
    """Flags shared by every action that generates a profile from ``ecus/``."""
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="Include unverified parameters (default: verified only)",
    )
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help=argparse.SUPPRESS,  # deprecated no-op: verified-only is the default
    )
    parser.add_argument(
        "--expected-responses",
        action="store_true",
        help="Append each PID's recorded response_frames count to its request, so "
        "the adapter answers as soon as that many frames arrive instead of waiting "
        "out its ~614ms budget (opt-in: the firmware cannot recover from a wrong count)",
    )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Build/sync the WiCAN AutoPID profile (autopid …, mode …)",
        description="Build and sync the WiCAN device's AutoPID profile.\n\n"
        "Nothing is written until you ask for it — a bare `canair wican` prints "
        "this help. Choose a subcommand:\n"
        "  autopid write     generate AutoPID JSON to the bundle's out/\n"
        "  autopid upload    generate + upload to the device (Pro)\n"
        "  autopid download  download the device's current AutoPID JSON (Pro)\n"
        "  autopid diff      download + diff against the generated JSON (Pro)\n"
        "  autopid stats     per-ECU/PID statistics table\n"
        "  mode show         show the device's active protocol\n"
        "  mode set MODE     switch the device protocol/mode, reboot + align transport (Pro)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    groups = parser.add_subparsers(dest="wican_command", metavar="<command>")

    _add_autopid_parser(groups)
    _add_mode_parser(groups)

    parser.set_defaults(
        func=run, _wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser
    )
    return parser


# ---------------------------------------------------------------------------
# canair wican autopid …
# ---------------------------------------------------------------------------


def _add_autopid_parser(groups) -> argparse.ArgumentParser:
    parser = groups.add_parser(
        "autopid",
        help="Generate/sync the WiCAN AutoPID profile JSON",
        description="Build the WiCAN AutoPID profile from the active bundle's "
        "ecus/ and (optionally) sync it with the device.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="autopid_command", metavar="<action>")

    write = sub.add_parser(
        "write",
        help="Generate the AutoPID JSON to the bundle's out/autopid.json",
        description="Generate the WiCAN AutoPID profile JSON and write it to the "
        "active bundle's out/autopid.json.",
    )
    write.add_argument(
        "--out",
        metavar="PATH",
        type=Path,
        default=None,
        help="Write to PATH instead of the bundle's out/autopid.json",
    )
    _add_generate_args(write)
    write.set_defaults(_wican_func=_cmd_autopid_write)

    upload = sub.add_parser(
        "upload",
        help="Generate + upload the AutoPID profile to the device (Pro)",
        description="Generate the AutoPID profile and upload it to the WiCAN "
        "device (POST /store_car_data). WiCAN Pro only.",
    )
    upload.add_argument("--reboot", action="store_true", help="Reboot the device after upload")
    _add_generate_args(upload)
    _add_wican_arg(upload)
    upload.set_defaults(_wican_func=_cmd_autopid_upload)

    download = sub.add_parser(
        "download",
        help="Download the device's current AutoPID profile (Pro)",
        description="Download the WiCAN device's current AutoPID profile "
        "(GET /load_auto_pid_car_data) and print it, normalized. WiCAN Pro only.",
    )
    _add_wican_arg(download)
    download.set_defaults(_wican_func=_cmd_autopid_download)

    diff = sub.add_parser(
        "diff",
        help="Download + diff the device profile against the generated one (Pro)",
        description="Download the device's current AutoPID profile and show a "
        "parameter-level diff against the freshly generated one. WiCAN Pro only.",
    )
    _add_generate_args(diff)
    _add_wican_arg(diff)
    diff.set_defaults(_wican_func=_cmd_autopid_diff)

    stats = sub.add_parser(
        "stats",
        help="Show a per-ECU/PID statistics table",
        description="Print a per-ECU/PID statistics table for the active bundle.",
    )
    stats.set_defaults(_wican_func=_cmd_autopid_stats)

    parser.set_defaults(_wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser)
    return parser


# ---------------------------------------------------------------------------
# canair wican mode …
# ---------------------------------------------------------------------------


def _add_mode_parser(groups) -> argparse.ArgumentParser:
    parser = groups.add_parser(
        "mode",
        help="Show or set the WiCAN device protocol/mode",
        description="Inspect or switch the WiCAN device's operating protocol "
        "(elm327/slcan/auto_pid/…). This is the device's own mode, distinct "
        "from the AutoPID profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode_command", metavar="<action>")

    show = sub.add_parser(
        "show",
        help="Show the device's active protocol",
        description="Report the WiCAN device's currently active protocol/mode.",
    )
    _add_wican_arg(show)
    show.set_defaults(_wican_func=_cmd_mode_show)

    setp = sub.add_parser(
        "set",
        help="Switch the device protocol/mode and reboot (Pro)",
        description=(
            "Switch the WiCAN device to MODE and reboot it, then align canair's "
            "transport to match (slcan -> slcan-tcp, elm327 -> wican-ws). "
            "WiCAN Pro only.\n\n"
            "Valid modes:\n"
            "  slcan        raw CAN — canair drives ISO-TP client-side; enables sniff\n"
            "  elm327       ELM327 terminal — the dongle runs ISO-TP\n"
            "  auto_pid     restore Home Assistant AutoPID broadcasting\n"
            "  savvycan     stream frames to SavvyCAN\n"
            "  realdash66   stream frames to RealDash (CAN 66)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No metavar override: argparse then lists the valid modes in the usage line
    # (e.g. `{elm327,slcan,savvycan,realdash66,auto_pid}`) instead of a bare MODE.
    setp.add_argument("protocol", choices=_PROTOCOLS, help="Target protocol/mode")
    setp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    setp.add_argument(
        "--no-transport",
        action="store_true",
        help="Don't auto-align the config transport.type to the new mode",
    )
    _add_wican_arg(setp)
    setp.set_defaults(_wican_func=_cmd_mode_set)

    parser.set_defaults(_wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser)
    return parser


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _verified_only(args) -> bool:
    """Resolve whether to emit verified-only parameters.

    Verified-only is the default; ``--include-unverified`` opts out. The legacy
    ``--verified-only`` flag is accepted as a no-op for back-compat.
    """
    return not getattr(args, "include_unverified", False)


def _generate(args) -> tuple[dict, dict]:
    """Load the active bundle and generate the AutoPID profile (grouped format)."""
    from canlib.profile import active

    print(f"Loading {active().ecus_dir}")
    data = load_yaml()

    verified_only = _verified_only(args)
    label = "" if getattr(args, "include_unverified", False) else " (verified only)"
    counts = getattr(args, "expected_responses", False)
    print(f"\nGenerating AutoPID profile{label}...")
    try:
        profile = generate_profile(data, verified_only, expected_responses=counts)
    except DuplicateParameterError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    n_groups = len(profile["pids"])
    n_params = sum(len(p["parameters"]) for p in profile["pids"])
    print(f"  {n_groups} PID groups, {n_params} parameters")
    if counts:
        n_counted = sum(1 for p in profile["pids"] if len(p["pid"]) % 2)
        print(f"  {n_counted}/{n_groups} carry an expected-response count")
        if n_counted < n_groups:
            print("  (the rest keep the unoptimized path — record more with canair monitor)")
    return data, profile


def _cmd_autopid_write(args) -> int:
    _data, profile = _generate(args)
    out = args.out if getattr(args, "out", None) else _profile_out()
    print("\nWriting output...")
    write_json(profile, out)
    print("\nDone.")
    return 0


def _cmd_autopid_upload(args) -> int:
    guard = _require_pro("autopid upload")
    if guard is not None:
        return guard

    data, profile = _generate(args)
    base_url = get_wican_url(args.wican)

    print("\nConverting to device format...")
    device_payload = to_device_format(profile, data)
    n_pids = len(device_payload["cars"][0]["pids"])
    n_dev_params = sum(len(p["parameters"]) for p in device_payload["cars"][0]["pids"])
    print(f"  {n_pids} PID groups, {n_dev_params} parameters (array-of-objects)")

    print(f"\nUploading to {base_url}...")
    upload_profile(base_url, device_payload, reboot=args.reboot)
    print("\nDone.")
    return 0


def _cmd_autopid_download(args) -> int:
    guard = _require_pro("autopid download")
    if guard is not None:
        return guard

    base_url = get_wican_url(args.wican)
    print(f"Downloading current AutoPID profile from {base_url}...")
    current_raw = download_profile(base_url)
    if current_raw:
        normalized = normalize_device_profile(current_raw)
        print("\n=== Current device profile (normalized) ===")
        print(json.dumps(normalized, indent=2))
    print("\nDone.")
    return 0


def _cmd_autopid_diff(args) -> int:
    guard = _require_pro("autopid diff")
    if guard is not None:
        return guard

    _data, profile = _generate(args)
    base_url = get_wican_url(args.wican)
    print(f"\nDownloading current AutoPID profile from {base_url}...")
    current_raw = download_profile(base_url)
    show_diff(current_raw, profile)
    print("\nDone.")
    return 0


def _cmd_autopid_stats(args) -> int:
    print_stats(load_yaml())
    return 0


def _cmd_mode_show(args) -> int:
    from canlib.wican_mode import current_protocol

    base_url = get_wican_url(args.wican)
    try:
        proto = current_protocol(base_url)
    except Exception as e:
        print(f"error: cannot reach WiCAN at {base_url}: {e}", file=sys.stderr)
        return 1
    print(f"WiCAN protocol: {proto or '?'}")
    return 0


def _sync_transport_to_mode(mode: str, *, no_transport: bool) -> None:
    """Align the config ``transport.type`` to the device mode just set.

    canair drives the bus in ``slcan`` (raw ``slcan-tcp``) or ``elm327``
    (``wican-ws`` ELM327 terminal); switching the device mode without also
    pointing the transport at it is the usual foot-gun. Prints the ``old ->
    new`` transition, or reports the transport is already aligned. Modes with no
    request/response transport (auto_pid/savvycan/realdash66) are left untouched.
    """
    if no_transport:
        return

    desired = _MODE_TO_TRANSPORT.get(mode)
    if desired is None:
        print(
            f"  Transport unchanged — no canair transport maps to '{mode}' mode "
            "(canair drives the bus in 'slcan' or 'elm327' mode)."
        )
        return

    from canlib.config import get_config_key, set_config_key
    from canlib.transport.config import DEFAULT_TRANSPORT

    current = get_config_key("transport.type") or DEFAULT_TRANSPORT
    if current == desired:
        print(f"  Transport already '{desired}'.")
        return
    set_config_key("transport.type", desired)
    print(f"  Transport '{current}' -> '{desired}'.")


def _cmd_mode_set(args) -> int:
    """Explicitly set the WiCAN device protocol/mode (reboots). Opt-in only."""
    from canlib.wican_mode import ModeError, current_protocol, set_protocol

    guard = _require_pro("mode set")
    if guard is not None:
        return guard

    base_url = get_wican_url(args.wican)
    target = args.protocol
    try:
        cur = current_protocol(base_url)
    except Exception as e:
        print(f"error: cannot reach WiCAN at {base_url}: {e}", file=sys.stderr)
        return 1

    if cur == target:
        print(f"WiCAN already in '{target}' mode.")
        _sync_transport_to_mode(target, no_transport=args.no_transport)
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print(
                f"error: refusing to switch '{cur}' -> '{target}' without --yes (non-interactive).",
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
        _sync_transport_to_mode(target, no_transport=args.no_transport)
        return 0
    print(f"warning: WiCAN reports '{now}' after switch.", file=sys.stderr)
    return 1


def run(args) -> int:
    return args._wican_func(args)
