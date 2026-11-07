"""Which definitions declare ``sensitive: true``.

A PID or a signal in ``ecus/`` says ``sensitive: true`` when its value identifies the car or its
owner (``canair pids set-sensitive``). These predicates only read that flag. What follows from it
(masking on screen, never saving, never sharing) is :mod:`canair.privacy.sensitive`, and every
display or save path goes through that module, never through these predicates alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FIELD = "sensitive"


def _flag(defn: Any) -> bool:
    return isinstance(defn, Mapping) and defn.get(FIELD) is True


def whole_pid_sensitive(pid_def: Any) -> bool:
    """Does the PID itself say ``sensitive: true`` (not only one of its signals)?"""
    return _flag(pid_def)


def pid_sensitive(pid_def: Any) -> bool:
    """Is a PID's reply sensitive: the PID says so, or any of its signals does."""
    if _flag(pid_def):
        return True
    params = pid_def.get("parameters") if isinstance(pid_def, Mapping) else None
    return isinstance(params, Mapping) and any(_flag(p) for p in params.values())


def param_sensitive(pid_def: Any, param: Any) -> bool:
    """Is one signal's value sensitive: it says so, or its PID does."""
    return _flag(pid_def) or _flag(param)
