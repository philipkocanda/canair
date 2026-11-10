"""The error a bad ``actions.yaml`` definition (or a reference to one) raises."""

from __future__ import annotations

from canair.core.edit_errors import EditError


class ActionError(EditError, ValueError):
    """An actions.yaml definition (or a reference to one) is invalid."""
