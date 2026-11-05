"""``canair validate pids|ecus``: run every ECU-file check, then the profile-wide ones.

The checks themselves are in the sibling modules: :mod:`.ecu_file` (one ECU file),
:mod:`.meta` (``profile.yaml``) and :mod:`.cross_file` (across files).
"""

from pathlib import Path

from .cross_file import (
    duplicate_name_errors,
    duplicate_param_errors,
    elm327_warnings,
    variants_yaml_errors,
)
from .dtcs import profile_dtc_findings
from .ecu_file import validate_ecu_file
from .meta import retired_quirk_warnings, validate_meta
from .profile_names import profile_name_findings
from .schema_fields import load_schema, new_ecu_stats
from .send_to import send_to_findings


def collect_pids_validation(files: list[Path], profile=None) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, total_stats) for the given ECU yaml files.

    Skips underscore-prefixed files; runs validate_ecu_file on the rest.
    ``profile`` scopes the accepted vehicle-state vocabulary (see
    :func:`validate_ecu_file`).
    """
    schema = load_schema()

    all_errors = []
    all_warnings = []
    total_stats = new_ecu_stats()

    for fpath in files:
        if fpath.name.startswith("_"):
            continue

        errors, warnings, stats = validate_ecu_file(fpath, schema, profile)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        for k in total_stats:
            total_stats[k] += stats[k]

    # Across the given files (so the single-file edit guard catches a bad topic too).
    errors, warnings = send_to_findings(files)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    return all_errors, all_warnings, total_stats


def validate_pids_file(fpath: Path, profile=None) -> tuple[bool, str]:
    errors, warnings, _ = collect_pids_validation([fpath], profile)
    lines = [f"  WARN: {w}" for w in warnings]
    lines += [f"  ERROR: {e}" for e in errors]
    if errors:
        lines.append(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return (not errors, "\n".join(lines))


def _run_pids(files: list[str] | None, stats: bool) -> int:
    from canair.ecus.files import iter_ecu_files
    from canair.profile import active

    prof = active()
    if files:
        file_paths = [Path(f) for f in files]
    else:
        # include_disabled: a parked `_name.yaml` is still reported on, so
        # re-enabling it doesn't surface errors that were never flagged.
        file_paths = list(iter_ecu_files(profile=prof, include_disabled=True))

    all_errors, all_warnings, total_stats = collect_pids_validation(file_paths)

    # Validate profile.yaml (profile-wide settings) unless a file subset was given.
    if not files:
        required_meta_fields = set(load_schema().get("required_meta_fields", []))
        profile_yaml = prof.meta_file
        if profile_yaml.exists():
            all_errors.extend(validate_meta(profile_yaml, required_meta_fields))
            dtcs_fields = set((load_schema().get("dtcs_fields", {}) or {}).get("optional", []))
            dtc_errors, dtc_warnings = profile_dtc_findings(profile_yaml, dtcs_fields)
            all_errors.extend(dtc_errors)
            all_warnings.extend(dtc_warnings)
            name_errors, name_warnings = profile_name_findings(prof)
            all_errors.extend(name_errors)
            all_warnings.extend(name_warnings)
            all_warnings.extend(retired_quirk_warnings(profile_yaml))

        # Cross-file: an ECU short name (or alias) must be unique across ecus/.
        all_errors.extend(duplicate_name_errors(file_paths))
        # Cross-file: a shipped parameter name must be unique across all PIDs
        # (the device turns each into a distinct signal). Caught here, not just
        # at `wican autopid write`.
        all_errors.extend(duplicate_param_errors(file_paths))
        all_errors.extend(variants_yaml_errors(prof))
        all_warnings.extend(elm327_warnings(prof.ecus_dir))

    # Print results
    if all_warnings:
        for w in all_warnings:
            print(f"  WARN: {w}")
        print()

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\n{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1

    n_files = len([f for f in file_paths if not f.name.startswith("_")])
    ignored = total_stats["ignored"]
    ignored_str = f", {ignored} ignored" if ignored else ""
    ioctl = total_stats["iocontrol"]
    ioctl_str = f", {ioctl} IOControl DIDs" if ioctl else ""
    research = total_stats["research"]
    research_str = f", {research} research items" if research else ""
    routines = total_stats["routines"]
    routines_str = f", {routines} routines" if routines else ""
    ioctl_disc = total_stats["iocontrol_discoveries"]
    ioctl_disc_str = f", {ioctl_disc} IO discoveries" if ioctl_disc else ""
    dtcs = total_stats["dtcs"]
    dtcs_str = f", {dtcs} DTCs" if dtcs else ""
    print(
        f"OK — {n_files} ECU files, {total_stats['pids']} PIDs{ignored_str}{ioctl_str}{research_str}{routines_str}{ioctl_disc_str}{dtcs_str}, "
        f"{total_stats['params']} parameters "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Ignored:    {total_stats['ignored']}")
        print(f"  Parameters: {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")
        print(f"  IOControl:  {total_stats['iocontrol']}")
        print(f"  Research:   {total_stats['research']}")
        print(f"  Routines:   {total_stats['routines']}")
        print(f"  IO Discoveries: {total_stats['iocontrol_discoveries']}")
        print(f"  Scan-log:   {total_stats['scan_log']}")
        print(f"  DTCs:       {total_stats['dtcs']}")
        print(f"  Sessions:   {total_stats['sessions']}")

    return 0


def _run_ecus() -> int:
    """`canair validate ecus` — the ECU files ARE the registry now, so this
    runs the same per-ECU validation as `validate pids`."""
    return _run_pids(None, stats=False)
