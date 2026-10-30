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

The file is optional, and so far it is metadata only: nothing selects a variant, gates
a definition or tags a capture yet (see ``plans/2026-10-30-profile-variants-and-rename.md``
for what follows). The field lists and the structural check live here once, shared by
the loader, ``canair validate variants`` and the editor (:mod:`canlib.variants.edit`),
so a field one accepts is never rejected by another; ``canlib/schema/
variants_schema.yaml`` documents the same fields for humans.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from canlib import yaml_io

if TYPE_CHECKING:
    from canlib.profile import Profile

# Local to its profile, so it names only what differs (``28kwh``, not ``ioniq-ev-28``).
# At least one letter: a bare number would read back as an integer YAML key.
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2]))?$")
# A battery-named ID (``28kwh``, ``38kwh-us``) states its usable capacity.
_BATTERY_ID_RE = re.compile(r"^(\d+)kwh(?:-|$)")

STATUSES = ("verified", "community", "seed")
VariantStatus = Literal["verified", "community", "seed"]

FieldKind = Literal["text", "texts", "sentences", "years", "year_months", "status", "battery"]
# Every field a variant may carry, in canonical render order, with its value kind.
VARIANT_FIELDS: dict[str, FieldKind] = {
    "name": "text",
    "model_code": "text",
    "designation": "text",
    "generation": "text",
    "production": "year_months",
    "model_years": "years",
    "markets": "texts",
    "battery": "battery",
    "trims": "texts",
    "status": "status",
    "sources": "sentences",
    "notes": "text",
}
REQUIRED_FIELDS = ("name",)
BATTERY_FIELDS: dict[str, Literal["number", "count", "text"]] = {
    "usable_kwh": "number",
    "gross_kwh": "number",
    "series_cells": "count",
    "chemistry": "text",
    "cooling": "text",
}
# Keys a later stage of the design owns. Rejected until something reads them, so nobody
# writes data that silently does nothing.
RESERVED_FIELDS = ("revisions", "profile_overrides")


class VariantsError(ValueError):
    """Raised when ``variants.yaml`` is structurally invalid."""


@dataclass(frozen=True)
class Battery:
    usable_kwh: float | None = None
    gross_kwh: float | None = None
    series_cells: int | None = None
    chemistry: str | None = None
    cooling: str | None = None


@dataclass(frozen=True)
class Variant:
    """One variant of the car, as declared in ``variants.yaml``."""

    id: str
    name: str
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


def normalize_variant_id(value: str) -> str:
    """The canonical (lower-cased, stripped) form of a variant ID."""
    return str(value).strip().lower()


def id_problem(variant_id: str) -> str | None:
    """Why ``variant_id`` is not a valid ID, or ``None`` when it is."""
    if not _ID_RE.match(variant_id) or not re.search(r"[a-z]", variant_id):
        return (
            f"invalid variant ID {variant_id!r}: lower-case letters and digits in '-'-separated "
            "words, with at least one letter (e.g. 28kwh)"
        )
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value_problem(kind: FieldKind, value: Any) -> str | None:
    """Why ``value`` is not a valid value of ``kind``, or ``None``."""
    if kind == "text":
        return None if isinstance(value, str) and value.strip() else "must be non-empty text"
    if kind in ("texts", "sentences"):
        ok = isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
        return None if ok else "must be a list of text"
    if kind == "status":
        return None if value in STATUSES else f"must be one of {', '.join(STATUSES)}"
    if kind == "years":
        ok = (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        )
        if not ok:
            return "must be a [from, to] pair of years"
        return None if value[0] <= value[1] else "must not end before it starts"
    if kind == "year_months":
        ok = (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(v, str) and _YEAR_MONTH_RE.match(v) for v in value)
        )
        if not ok:
            return 'must be a ["YYYY[-MM]", "YYYY[-MM]"] pair (quoted, so YAML keeps it text)'
        return None if value[0] <= value[1] else "must not end before it starts"
    if kind == "battery":
        if not isinstance(value, dict):
            return "must be a mapping"
        for key, sub in value.items():
            sub_kind = BATTERY_FIELDS.get(str(key))
            if sub_kind is None:
                return f"has unknown field '{key}' (known: {', '.join(BATTERY_FIELDS)})"
            if sub_kind == "number" and not (_is_number(sub) and sub > 0):
                return f"'{key}' must be a positive number"
            if sub_kind == "count" and not (isinstance(sub, int) and sub > 0 and sub is not True):
                return f"'{key}' must be a positive whole number"
            if sub_kind == "text" and not (isinstance(sub, str) and sub.strip()):
                return f"'{key}' must be non-empty text"
        return None
    return None  # pragma: no cover - every kind is handled above


def check_variants_doc(data: Any) -> list[str]:
    """Every structural problem in a parsed ``variants.yaml`` (empty when valid)."""
    if data is None:
        return []
    if not isinstance(data, dict):
        return ["top-level must be a mapping with a 'variants:' key"]
    errors = [f"unknown top-level key '{k}'" for k in data if k != "variants"]
    variants = data.get("variants")
    if variants is None:
        return errors
    if not isinstance(variants, dict):
        return [*errors, "'variants:' must be a mapping keyed by variant ID"]

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
            errors.append(f"{label}: must be a mapping of metadata fields")
            continue
        errors += [
            f"{label}: missing required field '{f}'" for f in REQUIRED_FIELDS if f not in entry
        ]
        for key, value in entry.items():
            if key in RESERVED_FIELDS:
                errors.append(f"{label}: '{key}' is not supported yet")
                continue
            kind = VARIANT_FIELDS.get(str(key))
            if kind is None:
                errors.append(f"{label}: unknown field '{key}'")
            elif problem := _value_problem(kind, value):
                errors.append(f"{label}: '{key}' {problem}")
        errors += _battery_id_errors(label, vid, entry.get("battery"))
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
    )


def _variants_path(profile: Profile | None) -> Path:
    from canlib.profile import active

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
