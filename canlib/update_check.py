"""Background update checker for canair.

canair is installed from a git clone (``git clone`` + ``uv tool install .``), so
"is there a newer version?" is answered by comparing the local
:data:`canlib.__version__` against the latest **GitHub release** tag
(``vX.Y.Z``) of the repo. The actual upgrade is driven by ``canair update``
(see :mod:`canlib.commands.update`).

Design constraints (all satisfied here):

* **Never break or slow the CLI.** The one network call is short-timeout and
  every failure — offline, DNS failure, timeout, HTTP error, malformed JSON — is
  swallowed and treated as "no result". The auto-check runs in a *daemon thread*
  so a slow/hung connection can never add latency to a command; the notice
  simply appears on the next run once the cache is warm.
* **Cache-first, once/day.** The network fetch only runs when the cached result
  is stale (default 24h); the freshness check and the user-facing notice are
  purely local (no network).
* **Opt-out.** ``CANAIR_NO_UPDATE_CHECK=1`` (env) or ``check_for_updates: false``
  (config) disable the automatic check entirely; it is also skipped in
  non-interactive / CI contexts by the caller.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# GitHub repository the releases are published under.
REPO = "philipkocanda/canair"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# Human-facing links used in notices and by ``canair update``.
RELEASES_URL = f"https://github.com/{REPO}/releases"
CHANGELOG_URL = f"https://github.com/{REPO}/blob/main/CHANGELOG.md"

# Env var that disables the automatic check (any truthy value).
DISABLE_ENV = "CANAIR_NO_UPDATE_CHECK"
# Config key (in ~/.config/canair/config.yaml) that disables it.
DISABLE_CONFIG_KEY = "check_for_updates"

# Re-check the network at most this often (seconds).
DEFAULT_INTERVAL = 24 * 60 * 60
# Short connect/read timeout so a poor connection never stalls us.
_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 3.0


def _cache_file() -> Path:
    from .config import config_dir

    return config_dir() / "update_check.json"


def _parse_version(text: str | None) -> tuple[int, ...] | None:
    """Parse ``v1.2.3`` / ``1.2.3`` into a comparable numeric tuple.

    Returns ``None`` when nothing numeric can be extracted (e.g. the
    ``0+unknown`` sentinel used when the package isn't installed). Any
    pre-release/build suffix (``1.2.3-rc1``, ``1.2.3+local``) is truncated at the
    first non-numeric segment so releases still compare sanely.
    """
    if not text:
        return None
    core = text.strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for seg in core.split("."):
        if not seg.isdigit():
            break
        parts.append(int(seg))
    return tuple(parts) or None


def _is_newer(latest: str | None, current: str | None) -> bool:
    """True when ``latest`` is a strictly greater version than ``current``."""
    # The "not installed" sentinel has no meaningful version to compare against;
    # never nag in that case (dev / source-tree runs).
    if not current or current.startswith("0+"):
        return False
    lv = _parse_version(latest)
    cv = _parse_version(current)
    if lv is None or cv is None:
        return False
    return lv > cv


def is_disabled() -> bool:
    """Whether the automatic update check is opted out (env or config)."""
    if os.environ.get(DISABLE_ENV):
        return True
    try:
        from .config import get_config_key

        value = get_config_key(DISABLE_CONFIG_KEY)
    except Exception:
        return False
    return value is False


def read_cache() -> dict | None:
    """Return the cached check result, or ``None`` if absent/unreadable."""
    path = _cache_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_cache(latest_tag: str | None, html_url: str | None) -> None:
    """Persist the latest-known release (best-effort; errors are swallowed)."""
    from .config import ensure_config_dir

    payload = {
        "checked_at": time.time(),
        "latest_tag": latest_tag,
        "html_url": html_url,
    }
    try:
        ensure_config_dir(seed_config=False)
        _cache_file().write_text(json.dumps(payload))
    except OSError:
        pass


def fetch_latest_release(
    timeout: tuple[float, float] = (_CONNECT_TIMEOUT, _READ_TIMEOUT),
) -> dict | None:
    """Fetch the latest GitHub release, or ``None`` on *any* failure.

    This is the only network call in the module. It is fully offline-safe: a
    missing network, DNS failure, timeout, non-2xx status, or malformed body all
    yield ``None`` rather than raising. Returns a small dict with ``tag``,
    ``url``, and ``published_at`` on success.
    """
    try:
        import requests

        resp = requests.get(
            RELEASES_LATEST_URL,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # requests.RequestException, socket/OSError, JSON errors, ImportError —
        # anything at all. A failed check must never surface to the user.
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    return {
        "tag": tag,
        "url": data.get("html_url") or RELEASES_URL,
        "published_at": data.get("published_at"),
    }


def should_check_now(interval: int = DEFAULT_INTERVAL) -> bool:
    """Whether a fresh network check is due (disabled → never; stale cache → yes)."""
    if is_disabled():
        return False
    cache = read_cache()
    if not cache:
        return True
    checked_at = cache.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return True
    return (time.time() - checked_at) >= interval


def _refresh() -> None:
    """Fetch + cache; used by the background thread. Never raises."""
    try:
        result = fetch_latest_release()
        if result is not None:
            write_cache(result["tag"], result.get("url"))
        else:
            # Touch the timestamp on failure too, so a dead network isn't
            # re-hit on every single command (respect the interval).
            prev = read_cache() or {}
            write_cache(prev.get("latest_tag"), prev.get("html_url"))
    except Exception:
        pass


def maybe_check_in_background(interval: int = DEFAULT_INTERVAL) -> None:
    """Kick off a non-blocking daemon-thread check if one is due.

    Returns immediately. The daemon thread is abandoned at process exit, so a
    slow or hung connection can never delay the CLI. The fetched result is only
    surfaced on a subsequent run via :func:`pending_notice`.
    """
    try:
        if not should_check_now(interval):
            return
        threading.Thread(target=_refresh, name="canair-update-check", daemon=True).start()
    except Exception:
        pass


def pending_notice() -> str | None:
    """Return a one-line "update available" notice, or ``None``.

    Purely local (reads the cache, compares to :data:`canlib.__version__`); does
    no network I/O, so it is cheap to call on the hot path.
    """
    try:
        cache = read_cache()
        if not cache:
            return None
        latest = cache.get("latest_tag")
        from . import __version__

        if not _is_newer(latest, __version__):
            return None
        url = cache.get("html_url") or CHANGELOG_URL
        return (
            f"update available: canair {__version__} \u2192 {latest}  "
            f"(run: canair update  \u00b7  changelog: {url})"
        )
    except Exception:
        return None


def print_notice_if_any(stream=sys.stderr) -> None:
    """Print the pending update notice (if any) to ``stream``. Best-effort."""
    try:
        notice = pending_notice()
        if not notice:
            return
        from rich.console import Console

        Console(file=stream).print(f"[dim yellow]\u2191 {notice}[/dim yellow]")
    except Exception:
        pass
