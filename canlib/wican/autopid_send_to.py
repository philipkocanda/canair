"""Per-signal MQTT destinations — a signal's optional ``send_to`` topic.

The WiCAN firmware publishes a signal that has a ``send_to`` topic (and destination ``type``
``MQTT_Topic``) to that topic on **every successful poll**, as
``{"<NAME>": <value>, "timestamp": <unix seconds>}``. This is independent of the AutoPID batch
destination, whose ``cycle`` is device config canair does not manage. A signal without one is
only ever seen in the batch snapshot. Design and firmware citations:
``plans/2026-10-18-per-signal-mqtt-streaming.md``.

This module owns the topic rule shared by profile generation (``canlib.wican.autopid_profile``) and
``canair validate pids``, so the two cannot disagree on what is shippable.
"""

from __future__ import annotations

# The firmware matches the destination type by exact string; anything else parses as Default,
# which is never published per signal.
MQTT_TOPIC_TYPE = "MQTT_Topic"
DEFAULT_TYPE = "Default"


def topic_problem(topic: object) -> str | None:
    """Why ``topic`` cannot be a ``send_to`` destination, or ``None`` if it can.

    The firmware publishes to the string verbatim, so a wildcard or an empty level produces a
    topic no ordinary subscription matches. Whitespace is legal MQTT but almost always a typo.
    """
    if not isinstance(topic, str) or not topic:
        return "must be a non-empty string"
    if "#" in topic or "+" in topic:
        return "must not contain an MQTT wildcard (# or +)"
    if topic.startswith("/") or topic.endswith("/"):
        return "must not start or end with '/'"
    if "//" in topic:
        return "must not contain an empty topic level ('//')"
    if any(c.isspace() for c in topic):
        return "must not contain whitespace"
    return None
