"""The variants of one car: ``variants.yaml``.

A profile describes one model generation with one drivetrain; a *variant* is a version
of that car the owner can name — a battery size, a facelift, a market with different
diagnostics. ``variants.yaml`` gives each variant a stable ID and typed metadata::

    variants:
      28kwh:
        name: "Ioniq Electric 28 kWh (pre-facelift)"
        model_code: AEEV
        production: ["2016", "2019-05"]
        battery: {usable_kwh: 28, series_cells: 96}
        status: verified

The file is optional. Beside its metadata a variant may say which ECU revisions the car
can carry (``revisions: {BMS: [KWP]}``, see :mod:`canair.variants.revisions`) and which
``profile.yaml`` settings differ for it (``profile_overrides:``, a whitelist). The field
lists and the check live here once, shared by the loader, ``canair validate variants`` and the
editor (:mod:`canair.yaml_edit.variants`), so a field one accepts is never rejected by another.
The check runs ``src/canair/schema/variants_schema.yaml`` for the structure.

Belongs: loading ``variants.yaml``, ECU revisions, fingerprints and the variant selection.
Not here: editing them (``yaml_edit/``) and probing the car (``live/revision_probe.py``).
Imports: ``profile``, ``ecus``, ``uds`` and ``core``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from canair.core import yaml_io
from canair.core.json_schema import schema_errors
from canair.core.paths import VARIANTS_SCHEMA_FILE

if TYPE_CHECKING:
    from canair.profile import Profile

# Local to its profile, so it names only what differs (``28kwh``, not ``ioniq-ev-28``).
# At least one letter: a bare number would read back as an integer YAML key.
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REVISION_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
# A battery-named ID (``28kwh``, ``38kwh-us``) states its usable capacity.
_BATTERY_ID_RE = re.compile(r"^(\d+)kwh(?:-|$)")

STATUSES = ("verified", "community", "seed")
VariantStatus = Literal["verified", "community", "seed"]

FieldKind = Literal[
    "text",
    "texts",
    "sentences",
    "years",
    "year_months",
    "status",
    "battery",
    "revisions",
    "overrides",
]
# Every field a variant may carry, in canonical render order, with its value kind.
VARIANT_FIELDS: dict[str, FieldKind] = {
    "name": "text",
    "also_known_as": "texts",
    "model_code": "text",
    "designation": "text",
    "generation": "text",
    "production": "year_months",
    "model_years": "years",
    "markets": "texts",
    "battery": "battery",
    "trims": "texts",
    "revisions": "revisions",
    "profile_overrides": "overrides",
    "status": "status",
    "sources": "sentences",
    "notes": "text",
}
REQUIRED_FIELDS = ("name",)
BATTERY_FIELDS: dict[str, Literal["number", "count", "text"]] = {
    "usable_kwh": "number",
    "gross_kwh": "number",
    "nominal_voltage": "number",  # volts
    "series_cells": "count",
    "parallel_cells": "count",
    "modules": "count",
    "chemistry": "text",
    "cell_format": "text",
    "cell_supplier": "text",
    "cooling": "text",
}
# The profile.yaml settings a variant may replace (shallow), and the type each must have.
# A whitelist, not a second profile.yaml: anything else describes the whole profile.
OverrideKind = Literal["text", "positive_int", "mapping", "texts"]
PROFILE_OVERRIDE_FIELDS: dict[str, OverrideKind] = {
    "car_model": "text",
    "physical_bands": "mapping",
    "response_timeout_ms": "positive_int",
    "init": "text",
    "multi_did_max": "positive_int",
    "quirks": "texts",
}


class VariantsError(ValueError):
    """Raised when ``variants.yaml`` is structurally invalid."""


@dataclass(frozen=True)
class Battery:
    usable_kwh: float | None = None
    gross_kwh: float | None = None
    nominal_voltage: float | None = None
    series_cells: int | None = None
    parallel_cells: int | None = None
    modules: int | None = None
    chemistry: str | None = None
    cell_format: str | None = None
    cell_supplier: str | None = None
    cooling: str | None = None


@dataclass(frozen=True)
class Variant:
    """One variant of the car, as declared in ``variants.yaml``."""

    id: str
    name: str
    also_known_as: tuple[str, ...] = ()
    model_code: str | None = None
    designation: str | None = None
    generation: str | None = None
    production: tuple[str, str] | None = None
    model_years: tuple[int, int] | None = None
    markets: tuple[str, ...] = ()
    battery: Battery = field(default_factory=Battery)
    trims: tuple[str, ...] = ()
    status: VariantStatus | None = None
    sources: tuple[str, ...] = ()
    notes: str | None = None
    # ECU name -> the revisions of that ECU this car can carry (an unlisted ECU: any).
    revisions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # profile.yaml settings that differ for this car (PROFILE_OVERRIDE_FIELDS only).
    profile_overrides: Mapping[str, Any] = field(default_factory=dict)

    def allowed_revisions(self, ecu: str) -> tuple[str, ...] | None:
        """The revisions of ``ecu`` this car can carry, or None when it doesn't say."""
        return self.revisions.get(ecu)


def normalize_variant_id(value: str) -> str:
    """The canonical (lower-cased, stripped) form of a variant ID."""
    return str(value).strip().lower()


def id_problem(variant_id: str, noun: str = "variant", example: str = "28kwh") -> str | None:
    """Why ``variant_id`` is not a valid ID, or ``None`` when it is.

    Revision IDs follow the same rule (``noun="revision"``): local to their ECU, naming
    only what differs. They may also use capitals (``UDS``), which keep their case.
    """
    if noun == "revision":
        if not _REVISION_ID_RE.match(variant_id) or not re.search(r"[A-Za-z]", variant_id):
            return (
                f"invalid revision ID {variant_id!r}: letters and digits in '-'-separated "
                f"words, with at least one letter (e.g. {example})"
            )
        return None
    if not _ID_RE.match(variant_id) or not re.search(r"[a-z]", variant_id):
        return (
            f"invalid {noun} ID {variant_id!r}: lower-case letters and digits in '-'-separated "
            f"words, with at least one letter (e.g. {example})"
        )
    return None


def check_variants_doc(data: Any) -> list[str]:
    """Every problem in a parsed ``variants.yaml`` (empty when valid).

    ``variants_schema.yaml`` checks the structure. The checks here are the ones it cannot make:
    the variant and revision IDs (compared lower-cased and stripped, as the loader reads them), a
    ``[from, to]`` pair that ends before it starts, and a battery-named ID against its battery.
    """
    errors = schema_errors(VARIANTS_SCHEMA_FILE, data) if data is not None else []
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, dict):
        return errors

    seen: set[str] = set()
    for raw_id, entry in variants.items():
        vid = normalize_variant_id(raw_id)
        label = f"variant '{raw_id}'"
        if vid in seen:
            errors.append(f"{label}: duplicate ID")
        seen.add(vid)
        if problem := id_problem(vid):
            errors.append(problem)
        if not isinstance(entry, dict):
            continue
        for key in ("production", "model_years"):
            pair = entry.get(key)
            if _is_comparable_pair(pair) and pair[0] > pair[1]:
                errors.append(f"{label}: '{key}' must not end before it starts")
        errors += _revision_id_errors(label, entry.get("revisions"))
        errors += _battery_id_errors(label, vid, entry.get("battery"))
    return errors


def _is_comparable_pair(value: Any) -> bool:
    """Two texts or two integers (the schema checks which a field must hold)."""
    if not (isinstance(value, list) and len(value) == 2):
        return False
    return all(isinstance(v, str) for v in value) or all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    )


def _revision_id_errors(label: str, revisions: Any) -> list[str]:
    """Each revision ID a variant names must be valid and listed once per ECU.

    That each ECU and revision exists is checked against the ECU files
    (:func:`canair.variants.revisions.variant_reference_errors`).
    """
    if not isinstance(revisions, dict):
        return []
    errors: list[str] = []
    for ecu, revs in revisions.items():
        if not isinstance(revs, list):
            continue
        ids = [str(r).strip() for r in revs]
        for rid in ids:
            if problem := id_problem(rid, "revision", "UDS"):
                errors.append(f"{label}: revisions '{ecu}': {problem}")
        if len({i.casefold() for i in ids}) != len(ids):
            errors.append(f"{label}: revisions '{ecu}' lists a revision twice")
    return errors


def _battery_id_errors(label: str, vid: str, battery: Any) -> list[str]:
    """A battery-named ID must agree with the battery it describes.

    The ID names the capacity the car is sold by, which makers quote as usable or as
    gross, so it is checked against ``usable_kwh`` when given, else ``gross_kwh``.
    """
    m = _BATTERY_ID_RE.match(vid)
    if not m or not isinstance(battery, dict):
        return []
    field_name = "usable_kwh" if "usable_kwh" in battery else "gross_kwh"
    kwh = battery.get(field_name)
    if not isinstance(kwh, (int, float)) or isinstance(kwh, bool) or int(kwh) == int(m.group(1)):
        return []
    return [f"{label}: the ID names {m.group(1)} kWh but battery.{field_name} is {kwh}"]


def _variant_from(vid: str, entry: Mapping[str, Any]) -> Variant:
    battery = entry.get("battery") or {}

    def pair(key: str) -> Any:
        value = entry.get(key)
        return tuple(value) if isinstance(value, list) else None

    return Variant(
        id=vid,
        name=str(entry["name"]),
        also_known_as=tuple(entry.get("also_known_as") or ()),
        model_code=entry.get("model_code"),
        designation=entry.get("designation"),
        generation=entry.get("generation"),
        production=pair("production"),
        model_years=pair("model_years"),
        markets=tuple(entry.get("markets") or ()),
        battery=Battery(**{k: battery[k] for k in BATTERY_FIELDS if k in battery}),
        trims=tuple(entry.get("trims") or ()),
        status=entry.get("status"),
        sources=tuple(entry.get("sources") or ()),
        notes=entry.get("notes"),
        revisions={
            str(ecu): tuple(str(r).strip() for r in revs)
            for ecu, revs in (entry.get("revisions") or {}).items()
        },
        profile_overrides=dict(entry.get("profile_overrides") or {}),
    )


def _variants_path(profile: Profile | None) -> Path:
    from canair.profile import active

    return (profile or active()).variants_file


def load_variants(profile: Profile | None = None) -> dict[str, Variant]:
    """The profile's variants by ID, in file order. Empty when there is no file.

    Raises :class:`VariantsError` (listing every problem) on a structurally invalid file.
    """
    path = _variants_path(profile)
    if not path.exists():
        return {}
    data = yaml_io.safe_load(path.read_text())
    errors = check_variants_doc(data)
    if errors:
        raise VariantsError(f"{path.name}: " + "; ".join(errors))
    raw = (data or {}).get("variants") or {}
    return {
        normalize_variant_id(k): _variant_from(normalize_variant_id(k), v) for k, v in raw.items()
    }


def describe(variant: Variant) -> str:
    """A one-line summary: name, production window and battery, as far as known."""
    parts = [variant.name]
    if variant.production:
        parts.append(f"built {variant.production[0]} – {variant.production[1]}")
    if variant.battery.usable_kwh is not None:
        parts.append(f"{variant.battery.usable_kwh:g} kWh usable")
    elif variant.battery.gross_kwh is not None:
        parts.append(f"{variant.battery.gross_kwh:g} kWh")
    if variant.status:
        parts.append(variant.status)
    return " · ".join(parts)
