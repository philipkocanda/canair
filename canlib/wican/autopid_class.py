"""The AutoPID ``class`` of a signal, from its canair category.

Both firmware generations store the string and act on none of it: 4.x picks a dashboard icon
and section from it, v6 echoes it back with the config. Their tokens are Home Assistant's
device classes, so a category maps onto the closest one and is left out where Home Assistant
has none (a lamp is not ``light``, which means "light detected"; a state of health is not
``battery``, which means the charge left).
"""

from __future__ import annotations

from collections.abc import Mapping

from canlib.signal_category import category_of

_CLASS = {
    "state_of_charge": "battery",
    "charging": "battery_charging",
    "lamp": None,
    "state_of_health": None,
}


def autopid_class(param: Mapping) -> str:
    """The ``class`` token for one effective parameter, or ``""`` when there is none."""
    category = category_of(param)
    if category is None:
        return ""
    return _CLASS.get(category, category) or ""
