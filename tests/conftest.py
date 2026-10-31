"""Shared test fixtures.

The repo bundles more than one vehicle profile (e.g. ``hyundai-ioniq-ae-ev`` and
``hyundai-ioniq-5-ne-ev``), so profile auto-resolution is ambiguous. Pin the bundled
``hyundai-ioniq-ae-ev`` profile for the whole suite so tests that load the real profile
stay deterministic. Individual tests may still override this via
``monkeypatch.setenv("CANAIR_PROFILE", ...)`` or by passing an explicit profile.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _pin_bundled_profile():
    """Session-wide default so module-scoped fixtures see it too."""
    prev = os.environ.get("CANAIR_PROFILE")
    os.environ["CANAIR_PROFILE"] = "hyundai-ioniq-ae-ev"
    yield
    if prev is None:
        os.environ.pop("CANAIR_PROFILE", None)
    else:
        os.environ["CANAIR_PROFILE"] = prev


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_config(tmp_path_factory):
    """Point ``$XDG_CONFIG_HOME`` at a throwaway directory for the whole suite.

    Resolving the active profile (``canlib.profile.resolve_profile`` →
    ``load_config().get("profiles_dir")``) legitimately reads the user config
    file to see whether the profile search path was overridden — so *any* test
    that loads a real profile (most of them) reads ``config_dir()`` at some
    point, not just tests that exercise ``canlib.config`` directly. Left
    unpinned, the suite's outcome depends on whatever happens to be in the
    developer's real ``~/.config/canair/config.yaml`` (a malformed value there
    fails collection/tests that have nothing to do with config; a `default_
    profile`/`devices` block there silently changes which profile or transport
    a test resolves). Point every test at an empty, deterministic directory
    instead; a test that needs specific config content still writes into it or
    overrides ``XDG_CONFIG_HOME`` itself via ``monkeypatch.setenv`` (which wins
    for the duration of that test and is restored to this default afterward,
    not to the developer's real environment).
    """
    path = tmp_path_factory.mktemp("xdg_config_home")
    # The suite's bundled profile describes several cars; name the one its captures came
    # from, as a user does once with `profile use … --variant`. Per profile, so a test
    # that switches to another bundled profile meets it unselected, like a new user.
    (path / "canair").mkdir()
    (path / "canair" / "config.yaml").write_text(
        "profile_settings:\n  hyundai-ioniq-ae-ev:\n    variant: 28kwh\n"
    )
    prev = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(path)
    yield
    if prev is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = prev


@pytest.fixture(autouse=True)
def _reset_active_profile():
    """Clear the memoized active profile — and its derived caches — around each test.

    Nulling ``_active`` alone is not enough: ``profile.set_active`` only drops the
    definition caches when it sees a *different* previous profile, so a test that
    starts from ``_active is None`` takes the "first activation" path and inherits
    whatever the previous test's profile left in the memoized ECU definitions and
    everything derived from them (e.g. the capture views' decode index). That
    leaks across modules — a view decodes against the wrong vehicle and renders no
    parameters — and only shows up in a serial run, since ``--dist loadscope`` puts
    each module in its own worker.
    """
    import canlib.profile as profile
    from canlib.config import load_config
    from canlib.pids import clear_cache

    # The config is memoized too, and the active profile's variant is read from it: a
    # test that points XDG_CONFIG_HOME elsewhere must not leave its config cached for
    # the next test (which then meets the suite's car unselected).
    profile._active = None
    clear_cache()
    load_config.cache_clear()
    yield
    profile._active = None
    clear_cache()
    load_config.cache_clear()


@pytest.fixture(autouse=True)
def _no_firmware_probe(monkeypatch):
    """Keep the WiCAN firmware probe off the network.

    Every live connect and ``canair status`` now asks the device which firmware
    it runs. Left alone, a test that drives one of those paths would send a real
    HTTP request to whatever host it resolved (and wait out its timeout). The
    stub fails like an unreachable host, so the probe reports ``unknown`` and
    behaviour is exactly as before; tests of the probe itself install their own
    ``_fetch``. The memo is cleared so one test's device never leaks into another.
    """
    import requests

    import canlib.wican.firmware as wican_firmware

    def _unreachable(url, timeout):
        raise requests.ConnectionError(f"network disabled in tests: {url}")

    wican_firmware.clear_cache()
    monkeypatch.setattr(wican_firmware, "_fetch", _unreachable)
    yield
    wican_firmware.clear_cache()


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Fail any real HTTP request made through ``requests``.

    Several commands make best-effort calls to the WiCAN's HTTP API (``status``,
    the transport defaults, the sleep banner) and swallow the failure, so a test
    that drives one of them without stubbing it would quietly send a request to
    a real LAN host. Every ``requests.get``/``post``/``Session`` call funnels
    through ``Session.request``, so failing there turns such a test into an
    unreachable device. Tests that monkeypatch ``requests.get`` or a module-level
    helper are unaffected, since they never reach this method.
    """
    import requests

    def _blocked(self, method, url, *args, **kwargs):
        raise requests.ConnectionError(f"network disabled in tests: {method} {url}")

    monkeypatch.setattr(requests.Session, "request", _blocked)


class NetworkBlocked(ConnectionRefusedError):
    """A test tried to open a connection to a non-loopback address."""


def _is_local(host) -> bool:
    import ipaddress

    if not isinstance(host, str) or host in ("", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


@pytest.fixture(autouse=True)
def _no_real_sockets(monkeypatch):
    """Fail any test that connects a socket (or resolves a name) off the loopback.

    The ``requests`` guard above cannot see raw sockets: the liveness probes, the
    SLCAN and ELM327 sockets, and the WebSocket all connect directly. Every one of
    them ends in ``socket.socket.connect``/``connect_ex`` (``create_connection``
    and asyncio's ``sock_connect`` included) after a ``getaddrinfo``, so guarding
    those three seams covers the sync, asyncio and ``websockets`` paths alike.

    The blocked call raises :class:`NetworkBlocked` (a ``ConnectionRefusedError``)
    so it fails fast instead of waiting out a timeout, *and* the attempt is
    recorded: most of these call sites swallow ``OSError`` as "unreachable", so
    the test is failed at teardown rather than trusting the exception to surface.
    Loopback stays open for the local test servers (the ELM327 emulator).
    """
    import socket

    attempts: list[str] = []

    def _check(address) -> None:
        host = address[0] if isinstance(address, tuple) and address else None
        if not _is_local(host):
            attempts.append(repr(address))
            raise NetworkBlocked(f"network disabled in tests: connect to {address!r}")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode()
        if host is not None and not _is_local(host):
            try:
                import ipaddress

                ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError:
                # A name, not an address literal: resolving it is a DNS query.
                attempts.append(f"getaddrinfo({host!r})")
                raise socket.gaierror(
                    socket.EAI_NONAME, f"network disabled in tests: {host}"
                ) from None
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
    if attempts:
        pytest.fail(
            "test tried to reach the network (stub the probe instead): " + ", ".join(attempts),
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _isolate_event_log(tmp_path_factory):
    """Redirect the central diagnostics event log to a temp file for the suite.

    Transport exchanges log drops/errors to ``config_dir()/logs/canair.log``; left
    unpinned, the test suite would scribble into the developer's real user log.
    Point it at a throwaway path (and clear any handler a test opened).
    """
    import logging

    import canlib.log as clog

    path = tmp_path_factory.mktemp("eventlog") / "canair.log"
    clog.event_log_path = lambda: path  # type: ignore[assignment]
    clog._event_logger = None
    logging.getLogger("canair.events").handlers.clear()
    yield
