"""``canair validate pids|ecus``: run every ECU-file check, then the profile-wide ones.

The checks themselves are in the sibling modules: :mod:`.ecu_file` (one ECU file),
:mod:`.meta` (``profile.yaml``) and :mod:`.cross_file` (across files).
"""

from dataclasses import dataclass
from pathlib import Path

from canair.profile.model import Profile

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
    lines = [f"warning: {w}" for w in warnings]
    lines += [f"error: {e}" for e in errors]
    if errors:
        lines.append(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return (not errors, "\n".join(lines))


@dataclass(frozen=True)
class PidsReport:
    """What ``canair validate pids`` found: the findings and the counts behind its summary."""

    errors: list[str]
    warnings: list[str]
    stats: dict
    n_files: int


def check_pids(files: list[Path] | None, profile: Profile) -> PidsReport:
    """Check the given ECU files, or (with none) every ecus/ file plus the profile-wide settings.

    The profile-wide checks are ``profile.yaml``, the names of ECUs and signals across files, and
    the variants and ELM327 settings. They run only when no file subset is given.
    """
    from canair.ecus.files import iter_ecu_files

    prof = profile
    if files:
        file_paths = list(files)
    else:
        # include_disabled: a parked `_name.yaml` is still reported on, so
        # re-enabling it doesn't surface errors that were never flagged.
        file_paths = list(iter_ecu_files(profile=prof, include_disabled=True))

    all_errors, all_warnings, total_stats = collect_pids_validation(file_paths)

    if not files:
        schema = load_schema()
        profile_yaml = prof.meta_file
        if profile_yaml.exists():
            all_errors.extend(validate_meta(profile_yaml))
            dtcs_fields = set((schema.get("dtcs_fields", {}) or {}).get("optional", []))
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

    n_files = len([f for f in file_paths if not f.name.startswith("_")])
    return PidsReport(all_errors, all_warnings, total_stats, n_files)
