"""Whether a TCP port answers, and why not.

Stdlib only: the device checks, the transport fallback and ``canair status`` all ask this before
they commit to a longer connection.
"""

from __future__ import annotations

import socket


def tcp_probe(host: str, port: int, timeout: float) -> OSError | None:
    """Try to open ``host:port``; return the ``OSError`` on failure, ``None`` on success.

    Callers that want the *reason* a connection failed (to show the user what
    actually happened — timed out vs refused vs no route vs bad name) inspect the
    returned exception; :func:`tcp_open` is the bool-only convenience wrapper.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as e:
        return e


def tcp_open(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds within ``timeout``."""
    return tcp_probe(host, port, timeout) is None
