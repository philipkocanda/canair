"""Checks across the ecus/ files and against ``variants.yaml``, run by ``canair validate pids``."""

from pathlib import Path

import yaml

from canair.core import yaml_io
from canair.ecus.pids import pid_ships
from canair.ecus.sensitivity import param_sensitive
from canair.ecus.shipping import param_ships


def _disjoint(ecu_a: str, revs_a: frozenset[str], ecu_b: str, revs_b: frozenset[str]) -> bool:
    """Two definitions of one signal that can never both exist: one ECU, no shared revision."""
    return ecu_a == ecu_b and bool(revs_a) and bool(revs_b) and not revs_a & revs_b


def variants_yaml_errors(prof) -> list[str]:
    """Every ECU or revision a variant's ``revisions:`` names that ecus/ lacks."""
    from canair.ecus.pids import load_raw_pids
    from canair.variants import VariantsError, load_variants
    from canair.variants.revisions import variant_reference_errors

    try:
        variants = load_variants(prof)
        ecus = load_raw_pids(prof).get("ecus") or {}
    except (VariantsError, OSError, yaml.YAMLError):
        return []  # reported by the per-file checks and `validate variants`
    return [f"variants.yaml: {e}" for e in variant_reference_errors(variants, ecus)]


def elm327_warnings(ecus_dir: Path) -> list[str]:
    """ELM327/AutoPID warnings for the loaded profile; [] if unloadable.

    Addressing (:mod:`.addressing`) and EEPROM wear (:mod:`.init_eeprom`).
    """
    from canair.ecus.pids import load_pids

    from .addressing import addressing_warnings
    from .init_eeprom import init_eeprom_warnings

    try:
        pids_data = load_pids(ecus_dir)
    except (OSError, yaml.YAMLError):
        # A broken file is already reported as an error by the per-file checks.
        return []
    return init_eeprom_warnings(pids_data.get("init")) + addressing_warnings(pids_data)


def duplicate_name_errors(file_paths: list[Path]) -> list[str]:
    """Flag any ECU short name / alias claimed by more than one ecus/ file."""
    errors: list[str] = []
    seen: dict[str, str] = {}
    for fpath in file_paths:
        if fpath.name.startswith("_"):
            continue
        try:
            with open(fpath) as f:
                data = yaml_io.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for ecu_name, ecu_def in data.items():
            names = [ecu_name]
            if isinstance(ecu_def, dict):
                alias = (ecu_def.get("identity") or {}).get("alias")
                if alias:
                    names.append(alias)
            for nm in names:
                key = str(nm).upper()
                if key in seen and seen[key] != fpath.name:
                    errors.append(
                        f"duplicate ECU name/alias '{nm}' in {fpath.name} (also {seen[key]})"
                    )
                else:
                    seen[key] = fpath.name
    return errors


def duplicate_param_errors(file_paths: list[Path]) -> list[str]:
    """Flag a parameter NAME shipped by more than one PID (a device collision).

    Mirrors the wican profile-generation gate (shipped PID + a signal that is
    not ``ship: false``): those params become distinct signals on the device and
    their names must be globally unique. Catching it here means CI/`validate` fails before
    `wican autopid write` does. See ``canair.interop.autopid.profile.generate_profile``.
    """
    from canair.variants.revisions import owner_revisions

    errors: list[str] = []
    # name -> every (ECU, PID, revisions) that ships it
    origin: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    for fpath in file_paths:
        if fpath.name.startswith("_"):
            continue
        try:
            with open(fpath) as f:
                data = yaml_io.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for ecu_name, ecu_def in data.items():
            if not isinstance(ecu_def, dict):
                continue
            for pid_code, pid_def in (ecu_def.get("pids") or {}).items():
                if not isinstance(pid_def, dict) or not pid_ships(pid_def):
                    continue
                for pname, param in (pid_def.get("parameters") or {}).items():
                    if not isinstance(param, dict) or not param_ships(param):
                        continue
                    if param_sensitive(pid_def, param):
                        continue  # never shipped (canair.privacy.sensitive)
                    where = f"{ecu_name} {pid_code}"
                    revs = owner_revisions(ecu_def, pid_def, param)
                    clash = next(
                        (
                            f"{e} {p}"
                            for e, p, r in origin.get(pname, [])
                            if f"{e} {p}" != where and not _disjoint(e, r, ecu_name, revs)
                        ),
                        None,
                    )
                    if clash:
                        errors.append(
                            f"duplicate shipped parameter name '{pname}' in {where} "
                            f"(also {clash}) — each shipped signal name must be "
                            f"unique across all PIDs (rename one via `canair pids rename-param`), "
                            "unless both are on one ECU and apply to disjoint revisions"
                        )
                    else:
                        origin.setdefault(pname, []).append((ecu_name, str(pid_code), revs))
    return errors
