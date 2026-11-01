"""Which logged DTC entries speak for the car you are looking at.

A logged code belongs to the car it was read from and to the revision of the ECU that
reported it: another variant can mean another code table, and a swapped ECU (another
revision) starts with its own fault memory. So every DTC-log entry is tagged like a
capture session (``variant`` + ``revisions: {ECU: id}``, see
:mod:`canlib.variants.tags`), and a reader only trusts the entries its :class:`Car`
admits. An untagged entry reads as the selected car, as an untagged capture does — but
with several variants and none named, nothing says which car an entry is, so none is
admitted.

The revision tag names ECUs (as a capture session's does); the entries themselves name
them by address (:mod:`canlib.dtc.refs`), so a reader resolves the name to check it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from canlib.variants.tags import SCOPE_ALL, in_scope, revisions_in_scope

ALL_SCOPE = "all"


@dataclass(frozen=True)
class Car:
    """The car (variant + determined ECU revisions) a DTC-log reader looks at."""

    variant: str | None = None
    several: bool = False
    revisions: Mapping[str, str] = field(default_factory=dict)
    everything: bool = False

    @property
    def unnamed(self) -> bool:
        """Several variants and none selected: no entry can be attributed to this car."""
        return not self.everything and self.several and not self.variant

    def admits(self, entry: Mapping[str, Any], ecus: Iterable[str] | None = None) -> bool:
        """Whether ``entry`` speaks for this car; ``ecus`` narrows the revision check."""
        if self.everything:
            return True
        if self.unnamed or not in_scope(entry, self.variant, several=self.several):
            return False
        resolved: Mapping[str, str] = self.revisions
        if ecus is not None:
            wanted = {e.upper() for e in ecus}
            resolved = {k: v for k, v in self.revisions.items() if k.upper() in wanted}
        return revisions_in_scope(entry, resolved)


ANY_CAR = Car(everything=True)


def current_car() -> Car:
    """The active profile's car, or :data:`ANY_CAR` when no profile resolves."""
    from canlib.profile import ProfileError, active
    from canlib.variants import VariantsError, load_variants

    try:
        prof = active()
        several = len(load_variants(prof)) > 1
        revisions = prof.revisions
    except (ProfileError, VariantsError):
        return ANY_CAR
    if prof.variant == SCOPE_ALL:
        return ANY_CAR
    return Car(variant=prof.variant, several=several, revisions=revisions)


def entry_revision(entry: Mapping[str, Any], ecu: str) -> str | None:
    """The revision ``entry`` records for ECU name ``ecu`` (case-insensitive), or None."""
    tags = entry.get("revisions")
    if not isinstance(tags, Mapping):
        return None
    return next((str(v) for k, v in tags.items() if str(k).upper() == ecu.upper()), None)
