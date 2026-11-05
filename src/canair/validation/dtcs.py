"""Validation of ``dtcs:`` sections: an ECU's, and the profile-wide one in ``profile.yaml``."""

from pathlib import Path

import yaml

from canair.core import yaml_io
from canair.ecus.dtc_definitions import SECTION_FIELDS as DTC_SECTION_FIELDS
from canair.ecus.dtc_definitions import is_dtc_code
from canair.ecus.dtc_definitions import split_section as split_dtc_section

from ._common import validate_shared_fields


def validate_dtcs(ecu_def, path, ecu_name, dtcs_fields, errors, warnings, stats) -> None:
    """Validate the ECU's dtcs: map (manufacturer DTC meanings, plus fields stated once)."""
    dtcs = ecu_def.get("dtcs")
    if dtcs is None:
        return
    stats["dtcs"] += _check_dtc_section(
        dtcs, f"{path.name}/{ecu_name}/dtcs", dtcs_fields, errors, warnings
    )


def _check_dtc_section(dtcs, label: str, dtcs_fields, errors, warnings) -> int:
    """Check one ``dtcs:`` section (an ECU's or the profile-wide one); returns its codes."""
    if not isinstance(dtcs, dict):
        errors.append(f"{label.removesuffix('/dtcs')}: 'dtcs' must be a dict")
        return 0
    shared, entries = split_dtc_section(dtcs)
    validate_shared_fields(shared, label, errors)
    n = 0
    for code, entry in entries.items():
        where = f"{label}/{code}"
        if not is_dtc_code(code):
            warnings.append(
                f"{where}: not a DTC code (a letter and four hex digits, without the -FTB "
                f"suffix) nor a field shared by every code ({', '.join(DTC_SECTION_FIELDS)})"
            )
        if not isinstance(entry, dict):
            errors.append(f"{where}: entry must be a mapping")
            continue
        for field in entry:
            if field not in dtcs_fields:
                warnings.append(f"{where}: unknown field '{field}'")
        validate_shared_fields(entry, where, errors)
        n += 1
    return n


def profile_dtc_findings(path: Path, dtcs_fields) -> tuple[list[str], list[str]]:
    """Errors and warnings for the DTC settings in ``profile.yaml``: the profile-wide
    ``dtcs:``, and the retired ``failure_types:``."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        data = yaml_io.safe_load(path.read_text())
    except yaml.YAMLError:
        return errors, warnings  # validate_meta reports the parse error
    if not isinstance(data, dict):
        return errors, warnings
    if data.get("dtcs") is not None:
        _check_dtc_section(data["dtcs"], "profile.yaml/dtcs", dtcs_fields, errors, warnings)
    if "failure_types" in data:
        warnings.append(
            "profile.yaml: 'failure_types' is no longer read — the failure-type byte is named "
            "from the standard table, and a manufacturer-specific one (0xF0-0xFF) is shown by "
            "value; remove it"
        )
    return errors, warnings
