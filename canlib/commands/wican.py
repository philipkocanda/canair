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
  autopid write               Generate the v4 and v6 AutoPID JSON to the bundle's out/
  autopid upload              Generate + upload to the device (Pro only)
  autopid download            Download the device's current AutoPID JSON (Pro)
  autopid diff                Download + diff against the generated JSON (Pro)
  autopid stats               Per-ECU/PID statistics table
  mode show                   Show the device's active protocol
  mode set MODE               Switch the device protocol/mode + align transport (Pro)

Examples:
  canair wican autopid write                       # verified-only, both firmware files
  canair wican autopid write --firmware 6          # only the firmware v6 table
  canair wican autopid write --include-unverified  # also include unverified params
  canair wican autopid write --no-expected-responses  # omit the response-frame counts
  canair wican autopid upload --reboot             # upload + reboot to apply
  canair wican autopid diff --wican home           # compare device vs generated
  canair wican mode set slcan                 # raw CAN + set transport slcan-tcp
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from canlib.commands._group import group_help
from canlib.response_frames import stored_count
from canlib.transport.config import TransportType
from canlib.wican.api import resolve_wican_url
from canlib.wican.autopid_profile import (
    ProfileGenerationError,
    ShippedProfile,
    normalize_device_profile,
    render_v4,
    ship_profile,
    to_device_format,
)
from canlib.wican.autopid_v6 import to_v6
from canlib.wican.firmware import Surface

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
        "JSON still works — run `canair wican autopid write` to write it to out/.\n"
        "        If this device is actually a Pro, run: canair config set wican_model pro",
        file=sys.stderr,
    )
    return 2


def _require_v4_api(base_url: str, surface: Surface, *, writes: bool = False) -> int | None:
    """Return an error code if the device's firmware can't serve this command.

    These commands drive WiCAN 4.x HTTP routes that firmware v6 removed, so
    refuse them on v6 before any request is sent. A 4.x version outside the
    tested range only warns. A command that ``writes`` to the device also warns
    when the generation could not be confirmed at all (the check is off, or the
    device didn't identify itself) — a warning rather than a refusal, so an
    unusual or offline-configured device is not locked out; v6 answers the 4.x
    write routes with an error, so going ahead fails loudly, not silently.
    """
    from canlib.wican.firmware import (
        compatibility,
        detect,
        print_verdict,
        probes,
        unconfirmed,
    )

    firmware = detect(base_url, surface)
    verdict = compatibility(firmware, surface)
    print_verdict(verdict)
    if verdict.refused:
        return 2
    if writes and firmware.generation == "unknown":
        print_verdict(unconfirmed(surface, "unidentified" if probes(surface) else "check-off"))
    return None


# The generated file for each WiCAN firmware generation: the 4.x vehicle profile and
# the v6 AutoPID table are different formats, so each gets its own name.
FIRMWARE_FILES: dict[str, str] = {"4": "autopid-v4.json", "6": "autopid-v6.json"}


def _profile_out(firmware: str) -> Path:
    """Default output path for the generated AutoPID JSON for ``firmware`` (4 or 6).

    ``out/<variant>/autopid-v<N>.json`` on a profile with several variants — each car gets
    its own device profile — else ``out/autopid-v<N>.json``.
    """
    from canlib.profile import active
    from canlib.variants import load_variants
    from canlib.variants.tags import selected_variant

    prof = active()
    variant = selected_variant(prof)
    name = FIRMWARE_FILES[firmware]
    if variant and len(load_variants(prof)) > 1:
        return prof.out_dir / variant / name
    return prof.out_dir / name


def _require_resolved_revisions() -> None:
    """Refuse device-profile generation while an ECU's revision is undetermined.

    The device polls a fixed list and cannot detect a revision, and shipping both
    revisions' PIDs would double the poll list and publish one signal name from two PIDs.
    """
    from canlib.pids import revision_resolutions
    from canlib.profile import ProfileError, active

    open_ = [r for r in revision_resolutions().values() if r.revision is None]
    if not open_:
        return
    prof = active()
    res = open_[0]
    names = ", ".join(r.ecu for r in open_)
    raise ProfileError(
        f"the revision of {names} is undetermined, and the device can't detect it. "
        f"{res.ecu} could be {' or '.join(res.possible)}: run a `canair read {res.ecu}` once "
        f"to detect it, `canair identity {res.ecu}`, or pin it "
        f"(canair profile use {prof.name} --pin {res.ecu}={res.possible[0]})."
    )


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


def download_profile(base_url: str) -> dict | None:
    """Download current vehicle profile from WiCAN device (raw device format)."""
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


def _signal_map(profile: dict) -> dict[tuple[str, str], dict[str, tuple[str, str]]]:
    """``(pid_init, pid) -> {signal: (expression, send_to topic or "")}`` for diffing."""
    result = {}
    for entry in profile["pids"]:
        send_to = entry.get("send_to") or {}
        result[(entry["pid_init"], entry["pid"])] = {
            name: (expr, send_to.get(name, "")) for name, expr in entry["parameters"].items()
        }
    return result


def _describe(signal: tuple[str, str]) -> str:
    expr, topic = signal
    return f"{expr}  → {topic}" if topic else expr


def show_diff(current_raw: dict | None, generated: dict) -> bool:
    """Show signal-level diff between device and generated profile.

    Normalizes the device's flat format to grouped format for comparison. A signal
    differs if its expression or its ``send_to`` stream topic differs.
    Returns True if there are differences.
    """
    if current_raw is None:
        print("\n  No current config to compare (download failed)")
        return True

    cur_map = _signal_map(normalize_device_profile(current_raw))
    gen_map = _signal_map(generated)

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
            print(f"\n  + NEW PID: {pid_init} {pid} ({len(gen_params)} signals)")
            for name in gen_params:
                print(f"      + {name}: {_describe(gen_params[name])}")
            continue

        if cur_params and not gen_params:
            removed_pids += 1
            removed_params += len(cur_params)
            has_diff = True
            print(f"\n  - REMOVED PID: {pid_init} {pid} ({len(cur_params)} signals)")
            for name in cur_params:
                print(f"      - {name}: {_describe(cur_params[name])}")
            continue

        # Both exist — compare signals
        all_param_names = sorted(set(list(cur_params.keys()) + list(gen_params.keys())))
        pid_diffs = []
        for name in all_param_names:
            if name not in cur_params:
                pid_diffs.append(f"      + {name}: {_describe(gen_params[name])}")
                added_params += 1
            elif name not in gen_params:
                pid_diffs.append(f"      - {name}: {_describe(cur_params[name])}")
                removed_params += 1
            elif cur_params[name] != gen_params[name]:
                (cur_expr, cur_topic), (gen_expr, gen_topic) = cur_params[name], gen_params[name]
                if cur_expr != gen_expr:
                    pid_diffs.append(f"      ~ {name}: {cur_expr} → {gen_expr}")
                if cur_topic != gen_topic:
                    pid_diffs.append(
                        f"      ~ {name}: send_to {cur_topic or '(none)'} → {gen_topic or '(none)'}"
                    )
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
            f"+{added_params} signals, -{removed_params} signals, ~{changed_params} changed"
        )

    return has_diff


def upload_profile(base_url: str, device_payload: dict, reboot: bool = False) -> None:
    """Upload vehicle profile to WiCAN device via POST /store_car_data.

    Expects device_payload in the firmware's format:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}
    with parameters as array-of-objects. Use to_device_format() to convert.
    """
    n_pids = len(device_payload.get("cars", [{}])[0].get("pids", []))
    n_params = sum(
        len(p.get("parameters", [])) for p in device_payload.get("cars", [{}])[0].get("pids", [])
    )

    url = f"{base_url}/store_car_data"
    try:
        resp = requests.post(url, json=device_payload, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        print(f"  Uploaded to {url} — {resp.status_code} ({n_pids} PIDs, {n_params} signals)")
    except requests.RequestException as e:
        print(f"  FAILED to upload to {url}: {e}", file=sys.stderr)
        sys.exit(1)

    if reboot:
        from canlib.wican.api import reboot_device

        time.sleep(0.3)
        reboot_device(base_url, timeout=WICAN_TIMEOUT)


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

    # Resolved exactly like the live commands' --wican (wican_api.resolve_wican_url):
    # a device alias, else the value itself as the host — never another device.
    parser.add_argument(
        "--wican",
        default=None,
        help=f"WiCAN address: {', '.join(wican_addresses())}, an IP/host, or a URL "
        f"(default: config transport.host / default_wican={default_wican()})",
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
        "--no-expected-responses",
        dest="expected_responses",
        action="store_false",
        default=True,
        help="Do not append each PID's recorded response_frames count to its request "
        "(by default the count is appended, so the adapter answers as soon as that "
        "many frames arrive instead of waiting out its ~614ms budget)",
    )
    parser.add_argument(
        "--expected-responses",
        dest="expected_responses",
        action="store_true",
        help=argparse.SUPPRESS,  # deprecated no-op: expected responses are the default
    )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Build/sync the WiCAN AutoPID profile (autopid …, mode …)",
        description="Build and sync the WiCAN device's AutoPID profile.\n\n"
        "Nothing is written until you ask for it — a bare `canair wican` prints "
        "this help. Choose a subcommand:\n"
        "  autopid write     generate the v4 and v6 AutoPID JSON to the bundle's out/\n"
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
        help="Generate the AutoPID JSON for firmware 4.x and v6 to the bundle's out/",
        description="Generate the WiCAN AutoPID profile and write it to the active "
        "bundle's out/, once per firmware generation: autopid-v4.json is the 4.x vehicle "
        "profile, autopid-v6.json the v6 AutoPID table (from 6.0.0-beta.3; it also names "
        "the car's 'vehicle' and 'specific_init' device settings). On a profile with several "
        "variants both go to out/<variant>/, one device profile per car. Refused while an "
        "ECU's revision is undetermined: the device polls a fixed list and can't detect it.",
    )
    write.add_argument(
        "--firmware",
        choices=sorted(FIRMWARE_FILES),
        default=None,
        help="Write only the file for this WiCAN firmware generation (default: both)",
    )
    write.add_argument(
        "--out",
        metavar="PATH",
        type=Path,
        default=None,
        help="Write to PATH instead of the bundle's out/ (needs --firmware: one file)",
    )
    _add_generate_args(write)
    write.set_defaults(_wican_func=_cmd_autopid_write)

    upload = sub.add_parser(
        "upload",
        help="Generate + upload the AutoPID profile to the device (Pro)",
        description="Generate the AutoPID profile and upload it to the WiCAN "
        "device (POST /store_car_data). WiCAN Pro on firmware 4.x only.",
    )
    upload.add_argument("--reboot", action="store_true", help="Reboot the device after upload")
    _add_generate_args(upload)
    _add_wican_arg(upload)
    upload.set_defaults(_wican_func=_cmd_autopid_upload)

    download = sub.add_parser(
        "download",
        help="Download the device's current AutoPID profile (Pro)",
        description="Download the WiCAN device's current AutoPID profile "
        "(GET /load_auto_pid_car_data) and print it, normalized. "
        "WiCAN Pro on firmware 4.x only.",
    )
    _add_wican_arg(download)
    download.set_defaults(_wican_func=_cmd_autopid_download)

    diff = sub.add_parser(
        "diff",
        help="Download + diff the device profile against the generated one (Pro)",
        description="Download the device's current AutoPID profile and show a "
        "parameter-level diff against the freshly generated one. "
        "WiCAN Pro on firmware 4.x only.",
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
        "from the AutoPID profile. Firmware 4.x only: v6 has no protocol mode.",
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
            "WiCAN Pro on firmware 4.x only.\n\n"
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


def _ship(args) -> tuple[dict, ShippedProfile]:
    """Load the active bundle and decide what ships to the device, for any firmware."""
    from canlib.profile import active
    from canlib.variants.selection import require_variant

    require_variant(active(), "generate the AutoPID profile")
    _require_resolved_revisions()
    print(f"Loading {active().ecus_dir}")
    data = load_yaml()

    verified_only = _verified_only(args)
    label = "" if getattr(args, "include_unverified", False) else " (verified only)"
    counts = getattr(args, "expected_responses", True)
    print(f"\nGenerating AutoPID profile{label}...")
    try:
        shipped = ship_profile(data, verified_only, expected_responses=counts)
    except ProfileGenerationError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    n_groups = len(shipped.pids)
    n_params = sum(len(p.params) for p in shipped.pids)
    print(f"  {n_groups} PID groups, {n_params} signals")
    n_streams = sum(len(p.send_to) for p in shipped.pids)
    if n_streams:
        print(f"  {n_streams} stream(s) to their own MQTT topic on every poll (send_to)")
    if counts:
        n_counted = sum(1 for p in shipped.pids if len(p.request) % 2)
        print(f"  {n_counted}/{n_groups} carry an expected-response count")
        if n_counted < n_groups:
            print("  (the rest keep the unoptimized path — record more with canair monitor)")
    return data, shipped


def _generate(args) -> tuple[dict, dict]:
    """Load the active bundle and generate the 4.x AutoPID profile (grouped format)."""
    data, shipped = _ship(args)
    return data, render_v4(shipped)


def _render_v6(shipped: ShippedProfile) -> dict:
    """The v6 table document, or exit with every way it doesn't fit."""
    try:
        v6 = to_v6(shipped)
    except ProfileGenerationError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if v6.dropped_streams:
        print(
            f"warning: firmware v6 has no per-signal MQTT stream, so the send_to of "
            f"{', '.join(v6.dropped_streams)} is left out of the v6 table (a v6 event rule "
            "on autopid.param → mqtt.publish is the nearest equivalent)",
            file=sys.stderr,
        )
    return v6.document


def _cmd_autopid_write(args) -> int:
    firmware = getattr(args, "firmware", None)
    out = getattr(args, "out", None)
    if out is not None and firmware is None:
        print(
            "error: --out writes one file, but `autopid write` makes one per firmware "
            "generation: add --firmware 4 or --firmware 6",
            file=sys.stderr,
        )
        return 2
    _data, shipped = _ship(args)
    documents = {
        "4": lambda: render_v4(shipped),
        "6": lambda: _render_v6(shipped),
    }
    wanted = [firmware] if firmware else sorted(FIRMWARE_FILES)
    rendered = {fw: documents[fw]() for fw in wanted}
    print("\nWriting output...")
    for fw, document in rendered.items():
        write_json(document, out if out is not None else _profile_out(fw))
    if "6" in rendered:
        print(
            "  (v6: 'vehicle' and 'specific_init' are the device's autopid settings, "
            "not part of the table)"
        )
    print("\nDone.")
    return 0


def _cmd_autopid_upload(args) -> int:
    guard = _require_pro("autopid upload")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = _require_v4_api(base_url, "autopid-sync", writes=True)
    if guard is not None:
        return guard

    data, profile = _generate(args)

    print("\nConverting to device format...")
    device_payload = to_device_format(profile, data)
    n_pids = len(device_payload["cars"][0]["pids"])
    n_dev_params = sum(len(p["parameters"]) for p in device_payload["cars"][0]["pids"])
    print(f"  {n_pids} PID groups, {n_dev_params} signals (array-of-objects)")

    print(f"\nUploading to {base_url}...")
    upload_profile(base_url, device_payload, reboot=args.reboot)
    print("\nDone.")
    return 0


def _cmd_autopid_download(args) -> int:
    guard = _require_pro("autopid download")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = _require_v4_api(base_url, "autopid-sync")
    if guard is not None:
        return guard
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

    base_url = resolve_wican_url(args.wican)
    guard = _require_v4_api(base_url, "autopid-sync")
    if guard is not None:
        return guard

    _data, profile = _generate(args)
    print(f"\nDownloading current AutoPID profile from {base_url}...")
    current_raw = download_profile(base_url)
    show_diff(current_raw, profile)
    print("\nDone.")
    return 0


def _cmd_autopid_stats(args) -> int:
    print_stats(load_yaml())
    return 0


def _cmd_mode_show(args) -> int:
    from canlib.wican.mode import current_protocol

    base_url = resolve_wican_url(args.wican)
    guard = _require_v4_api(base_url, "wican-mode")
    if guard is not None:
        return guard
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
    from canlib.wican.mode import ModeError, current_protocol, set_protocol

    guard = _require_pro("mode set")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = _require_v4_api(base_url, "wican-mode")
    if guard is not None:
        return guard
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
