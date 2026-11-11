"""Generating the AutoPID profile from the active bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from canair.cli.car import require_car
from canair.cli.profile_context import active
from canair.ecus.pids import load_pids, revision_resolutions
from canair.interop.autopid.profile import (
    ProfileGenerationError,
    ShippedProfile,
    render_v4,
    ship_profile,
)
from canair.interop.autopid.v6 import to_v6
from canair.profile.model import ProfileError
from canair.render import messages
from canair.variants import load_variants
from canair.variants.tags import selected_variant
from canair.yaml_edit.echo import echo_edit

# The generated file for each WiCAN firmware generation: the 4.x vehicle profile and
# the v6 AutoPID table are different formats, so each gets its own name.
FIRMWARE_FILES: dict[str, str] = {"4": "autopid-v4.json", "6": "autopid-v6.json"}


def profile_out(firmware: str, include_unverified: bool = False) -> Path:
    """Default output path for the generated AutoPID JSON for ``firmware`` (4 or 6).

    ``out/<variant>/autopid-v<N>.json`` on a profile with several variants — each car gets
    its own device profile — else ``out/autopid-v<N>.json``. A build that includes the
    unverified signals is ``autopid-v<N>-unverified.json`` beside it, so it never replaces
    the verified-only file.
    """
    prof = active()
    variant = selected_variant(prof)
    name = FIRMWARE_FILES[firmware]
    if include_unverified:
        name = name.replace(".json", "-unverified.json")
    if variant and len(load_variants(prof)) > 1:
        return prof.out_dir / variant / name
    return prof.out_dir / name


def require_resolved_revisions() -> None:
    """Refuse device-profile generation while an ECU's revision is undetermined.

    The device polls a fixed list and cannot detect a revision, and shipping both
    revisions' PIDs would double the poll list and publish one signal name from two PIDs.
    """
    open_ = [r for r in revision_resolutions(profile=active()).values() if r.revision is None]
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
    return load_pids(profile=active())


def write_json(data: dict, path: Path) -> None:
    """Write JSON to file with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    echo_edit(f"wrote {len(json.dumps(data))} bytes", path)


def want_verified_only(args) -> bool:
    """Resolve whether to emit verified-only parameters.

    Verified-only is the default; ``--include-unverified`` opts out. The legacy
    ``--verified-only`` flag is accepted as a no-op for back-compat.
    """
    return not getattr(args, "include_unverified", False)


def ship(args, log: TextIO | None = None) -> tuple[dict, ShippedProfile]:
    """Load the active bundle and decide what ships to the device, for any firmware.

    The progress lines go to ``log`` (stdout by default), so a command whose stdout is a
    JSON document can send them to stderr.
    """
    log = log or sys.stdout
    require_car("generate the AutoPID profile")
    require_resolved_revisions()
    print(f"Loading {active().ecus_dir}", file=log)
    data = load_yaml()

    verified_only = want_verified_only(args)
    label = "" if getattr(args, "include_unverified", False) else " (verified only)"
    counts = getattr(args, "expected_responses", True)
    print(f"\nGenerating AutoPID profile{label}…", file=log)
    try:
        shipped = ship_profile(data, verified_only, expected_responses=counts)
    except ProfileGenerationError as e:
        messages.fail(str(e))
    n_groups = len(shipped.pids)
    n_params = sum(len(p.params) for p in shipped.pids)
    print(f"  {n_groups} PID groups, {n_params} signals", file=log)
    n_streams = sum(len(p.send_to) for p in shipped.pids)
    if n_streams:
        print(f"  {n_streams} stream(s) to their own MQTT topic on every poll (send_to)", file=log)
    if counts:
        n_counted = sum(1 for p in shipped.pids if len(p.request) % 2)
        print(f"  {n_counted}/{n_groups} carry an expected-response count", file=log)
        if n_counted < n_groups:
            print(
                "  (the rest keep the unoptimized path — record more with canair monitor)",
                file=log,
            )
    return data, shipped


def generate(args) -> tuple[dict, dict]:
    """Load the active bundle and generate the 4.x AutoPID profile (grouped format)."""
    data, shipped = ship(args)
    return data, render_v4(shipped)


def render_v6(shipped: ShippedProfile) -> dict:
    """The v6 table document, or exit with every way it doesn't fit."""
    try:
        v6 = to_v6(shipped)
    except ProfileGenerationError as e:
        messages.fail(str(e))
    if v6.dropped_streams:
        messages.warning(
            f"firmware v6 has no per-signal MQTT stream, so the send_to of "
            f"{', '.join(v6.dropped_streams)} is left out of the v6 table (a v6 event rule "
            "on autopid.param → mqtt.publish is the nearest equivalent)"
        )
    return v6.document


def add_generate_args(parser: argparse.ArgumentParser) -> None:
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
