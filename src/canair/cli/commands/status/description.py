"""The text shown at the top of ``canair status --help``."""

DESCRIPTION = """``canair status`` — show the configured transport, device state, and profile.

Read-only and side-effect-free: it never connects a session, never changes the
device mode. Designed to answer "what am I talking to, in what mode, and is it
reachable/usable?" at a glance, with actionable hints and Unix-style exit codes.
Also reports the running canair version — which, when canair is running from a
git checkout rather than an installed release, names that checkout's branch and
short commit (``1.15.0+main.343b244``) — and, when the WiCAN HTTP API answers,
the device's firmware generation, version and hardware version. On WiCAN
firmware v6 it also says which canair transports work there, and exits 2 when
the selected one cannot.

It reports the device a live command would actually use: like ``read``/
``monitor`` it resolves the whole candidate list and runs the same connect-time
fallback probe, so a setup where the selected device is down but another
configured one answers reads as ready here too (``--no-fallback`` to pin the
selected device).
"""
