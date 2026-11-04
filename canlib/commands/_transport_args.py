"""The transport-selection flags (``--transport``/``--wican``/``--no-fallback``) as values.

The transport layer takes a :class:`~canlib.transport.config.TransportOverrides`; a command
builds it here from its parsed namespace.
"""

import argparse

from canlib.transport.config import TransportOverrides

__all__ = ["transport_overrides"]


def transport_overrides(args: argparse.Namespace) -> TransportOverrides:
    """The transport overrides on ``args`` (each optional: a parser may expose only some)."""
    return TransportOverrides(
        transport=getattr(args, "transport", None),
        wican=getattr(args, "wican", None),
        port=getattr(args, "port", None),
        bitrate=getattr(args, "bitrate", None),
        no_fallback=bool(getattr(args, "no_fallback", False)),
    )
