"""Checks of ``dtcs:`` sections: an ECU's, and the profile-wide one in ``profile.yaml``.

Both follow the ``dtcs`` definition of the ECU schema. An ECU file is checked against it whole;
the profile-wide section is checked against that definition here. What a schema cannot say is a
key that is neither a DTC code nor a field stated once for every code.
"""

from pathlib import Path

import yaml

from canair.core import yaml_io
from canair.core.json_schema import schema_findings
from canair.core.paths import ECUS_SCHEMA_FILE
from canair.ecus.dtc_definitions import SECTION_FIELDS as DTC_SECTION_FIELDS
from canair.ecus.dtc_definitions import is_dtc_code
from canair.ecus.dtc_definitions import split_section as split_dtc_section


def validate_dtcs(ecu_def, path, ecu_name, warnings, stats) -> None:
    """Warn about the keys of the ECU's ``dtcs:`` that are not codes, and count the codes."""
    dtcs = ecu_def.get("dtcs")
    if isinstance(dtcs, dict):
        stats["dtcs"] += _check_codes(dtcs, f"{path.name}/{ecu_name}/dtcs", warnings)


def _check_codes(dtcs: dict, label: str, warnings: list[str]) -> int:
    """Warn about each key of a ``dtcs:`` section that is not a code; returns the codes defined."""
    _shared, entries = split_dtc_section(dtcs)
    for code in entries:
        if not is_dtc_code(code):
            warnings.append(
                f"{label}/{code}: not a DTC code (a letter and four hex digits, without the -FTB "
                f"suffix) nor a field shared by every code ({', '.join(DTC_SECTION_FIELDS)})"
            )
    return sum(isinstance(entry, dict) for entry in entries.values())


def profile_dtc_findings(path: Path) -> tuple[list[str], list[str]]:
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
    dtcs = data.get("dtcs")
    if dtcs is not None:
        errs, warns = schema_findings(ECUS_SCHEMA_FILE, dtcs, definition="dtcs", at=["dtcs"])
        errors += [f"profile.yaml: {e}" for e in errs]
        warnings += [f"profile.yaml: {w}" for w in warns]
        if isinstance(dtcs, dict):
            _check_codes(dtcs, "profile.yaml/dtcs", warnings)
    if "failure_types" in data:
        warnings.append(
            "profile.yaml: 'failure_types' is no longer read — the failure-type byte is named "
            "from the standard table, and a manufacturer-specific one (0xF0-0xFF) is shown by "
            "value; remove it"
        )
    return errors, warnings
