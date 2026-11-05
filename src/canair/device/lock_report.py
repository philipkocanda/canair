"""The words of a failed connection-lock acquire (the text of an ``error:`` message).

Kept apart from :mod:`canair.device.lock` so the mutex itself stays small. The level prefix and
the continuation indent belong to the printer (``canair.render.messages``), which this layer
cannot import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from canair.device.lock import AcquireResult


def describe_acquire_failure(result: AcquireResult) -> str:
    """What happened (first line) and what to do (the lines after it)."""
    if result.outcome == "permission":
        return (
            f"{result.detail}.\n"
            "Another user's canair session appears to hold the device connection.\n"
            "Ask them to stop it (or clear it as that user / with sudo)."
        )
    if result.outcome == "contended":
        who = result.holder.describe() if result.holder else "another session"
        return (
            f"another canair session is already using the WiCAN — {who}.\n"
            "Only one canair session can talk to the device at a time.\n"
            "Inspect it with `canair lock`, or re-run with --force to ask it to stop."
        )
    if result.outcome == "timeout":
        who = result.holder.describe() if result.holder else "the holder"
        pid = result.holder.pid if result.holder else None
        kill = f"kill {pid}" if pid else "kill <pid>"
        hard = f"kill -9 {pid}" if pid else "kill -9 <pid>"
        return (
            f"the session holding the connection did not release it within "
            f"{result.waited_s:.0f}s — {who}.\n"
            "It may predate canair's cooperative release, or be wedged. It still holds\n"
            "the device's single connection, so a new session would time out.\n"
            f"Stop it with:  {kill}    (if it ignores that:  {hard})\n"
            "Any --save data it recorded is safe: `canair captures uds --recover`."
        )
    return f"could not acquire the connection lock ({result.outcome})."
