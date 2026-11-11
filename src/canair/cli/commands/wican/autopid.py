"""``canair wican autopid``: the kinds and their handlers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from canair.cli.args.group import group_help
from canair.cli.args.query import parse_canonical_query
from canair.device.wican.api import resolve_wican_url
from canair.interop.autopid.profile import (
    normalize_device_profile,
    render_v4,
    to_device_format,
)
from canair.render import messages

from .common import add_wican_arg, require_pro, require_v4_api
from .diff import show_diff
from .generate import (
    FIRMWARE_FILES,
    add_generate_args,
    generate,
    load_yaml,
    profile_out,
    render_v6,
    ship,
    want_verified_only,
    write_json,
)
from .stats import print_stats
from .sync import download_profile, upload_profile


def add_parsers(groups) -> argparse.ArgumentParser:
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
        "variants both go to out/<variant>/, one device profile per car. With "
        "--include-unverified they are autopid-v4-unverified.json and "
        "autopid-v6-unverified.json instead, so the verified-only files stay as they are. "
        "Refused while an "
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
    add_generate_args(write)
    write.set_defaults(_wican_func=cmd_autopid_write)

    upload = sub.add_parser(
        "upload",
        help="Generate + upload the AutoPID profile to the device (Pro)",
        description="Generate the AutoPID profile and upload it to the WiCAN "
        "device (POST /store_car_data). WiCAN Pro on firmware 4.x only.",
    )
    upload.add_argument("--reboot", action="store_true", help="Reboot the device after upload")
    add_generate_args(upload)
    add_wican_arg(upload)
    upload.set_defaults(_wican_func=cmd_autopid_upload)

    download = sub.add_parser(
        "download",
        help="Download the device's current AutoPID profile (Pro)",
        description="Download the WiCAN device's current AutoPID profile "
        "(GET /load_auto_pid_car_data) and print it, normalized. "
        "WiCAN Pro on firmware 4.x only.",
    )
    add_wican_arg(download)
    download.set_defaults(_wican_func=cmd_autopid_download)

    diff = sub.add_parser(
        "diff",
        help="Download + diff the device profile against the generated one (Pro)",
        description="Download the device's current AutoPID profile and show a "
        "parameter-level diff against the freshly generated one. "
        "WiCAN Pro on firmware 4.x only.",
    )
    add_generate_args(diff)
    add_wican_arg(diff)
    diff.set_defaults(_wican_func=cmd_autopid_diff)

    show = sub.add_parser(
        "show",
        help="Print the AutoPID JSON that write would generate, for the PIDs a QUERY selects",
        description="Generate the AutoPID profile in memory and print it as one JSON "
        'document, {"v4": …, "v6": …}, narrowed to the PIDs the QUERY selects. Reads '
        "ecus/, never out/, and writes nothing. The progress lines go to stderr. The "
        "smallest way to check what one PID ships as.",
    )
    show.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="ECU/PID selection, e.g. 'BMS:2101' or 'VCU' (default: every shipped PID)",
    )
    show.add_argument(
        "--firmware",
        choices=sorted(FIRMWARE_FILES),
        default=None,
        help="Print only this WiCAN firmware generation's document (default: both)",
    )
    add_generate_args(show)
    show.set_defaults(_wican_func=cmd_autopid_show)

    stats = sub.add_parser(
        "stats",
        help="Show a per-ECU/PID statistics table",
        description="Print a per-ECU/PID statistics table for the active bundle.",
    )
    stats.set_defaults(_wican_func=cmd_autopid_stats)

    parser.set_defaults(_wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser)
    return parser


def cmd_autopid_write(args) -> int:
    firmware = getattr(args, "firmware", None)
    out = getattr(args, "out", None)
    if out is not None and firmware is None:
        messages.error(
            "--out writes one file, but `autopid write` makes one per firmware "
            "generation: add --firmware 4 or --firmware 6"
        )
        return 2
    _data, shipped = ship(args)
    documents = {
        "4": lambda: render_v4(shipped),
        "6": lambda: render_v6(shipped),
    }
    wanted = [firmware] if firmware else sorted(FIRMWARE_FILES)
    rendered = {fw: documents[fw]() for fw in wanted}
    print("\nWriting output…")
    for fw, document in rendered.items():
        write_json(
            document,
            out if out is not None else profile_out(fw, not want_verified_only(args)),
        )
    if "6" in rendered:
        print(
            "  (v6: 'vehicle' and 'specific_init' are the device's autopid settings, "
            "not part of the table)"
        )
    print("\nDone.")
    return 0


def cmd_autopid_upload(args) -> int:
    guard = require_pro("autopid upload")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = require_v4_api(base_url, "autopid-sync", writes=True)
    if guard is not None:
        return guard

    data, profile = generate(args)

    print("\nConverting to device format…")
    device_payload = to_device_format(profile, data)
    n_pids = len(device_payload["cars"][0]["pids"])
    n_dev_params = sum(len(p["parameters"]) for p in device_payload["cars"][0]["pids"])
    print(f"  {n_pids} PID groups, {n_dev_params} signals (array-of-objects)")

    print(f"\nUploading to {base_url}…")
    upload_profile(base_url, device_payload, reboot=args.reboot)
    print("\nDone.")
    return 0


def cmd_autopid_download(args) -> int:
    guard = require_pro("autopid download")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = require_v4_api(base_url, "autopid-sync")
    if guard is not None:
        return guard
    print(f"Downloading current AutoPID profile from {base_url}…", file=sys.stderr)
    current_raw = download_profile(base_url)
    if current_raw:
        print(json.dumps(normalize_device_profile(current_raw), indent=2))
    return 0 if current_raw else 1


def cmd_autopid_diff(args) -> int:
    guard = require_pro("autopid diff")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = require_v4_api(base_url, "autopid-sync")
    if guard is not None:
        return guard

    _data, profile = generate(args)
    print(f"\nDownloading current AutoPID profile from {base_url}…")
    current_raw = download_profile(base_url)
    show_diff(current_raw, profile)
    print("\nDone.")
    return 0


def cmd_autopid_show(args) -> int:
    """Print what ``write`` would generate, narrowed to the PIDs a QUERY selects.

    The files in ``out/`` run to tens of kilobytes. This renders the same documents in
    memory from ``ecus/`` (it never reads ``out/`` and writes nothing) and prints one JSON
    document keyed by firmware generation, ``{"v4": …, "v6": …}``, whatever ``--firmware``
    picks. The progress lines go to stderr.
    """
    _data, shipped = ship(args, log=sys.stderr)
    query = " ".join(args.query or [])
    if query:
        q = parse_canonical_query(query)
        pids, empty = q.filter(shipped.pids, ecu_of=lambda p: p.ecu, pid_of=lambda p: p.pid)
        if not pids:
            messages.error(
                f"no shipped PID matches {query!r} (an unverified one needs --include-unverified)"
            )
            return 1
        for sel in empty:
            messages.warning(f"no shipped PID matches '{sel}'")
        shipped = replace(shipped, pids=pids)
    renderers = {"4": render_v4, "6": render_v6}
    wanted = [args.firmware] if args.firmware else sorted(FIRMWARE_FILES)
    json.dump({f"v{fw}": renderers[fw](shipped) for fw in wanted}, sys.stdout, indent=2)
    print()
    return 0


def cmd_autopid_stats(args) -> int:
    print_stats(load_yaml())
    return 0
