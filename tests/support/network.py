"""The error the suite-wide network guard raises (see tests/conftest.py)."""


class NetworkBlocked(ConnectionRefusedError):
    """A test tried to open a connection to a non-loopback address."""
