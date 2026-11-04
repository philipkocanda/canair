"""The central rotating diagnostics event log.

A single **size-rotated** file at ``~/.config/canair/logs/canair.log`` where transport
error/drop events and unexpected internal exceptions are recorded so they are
inspectable after the fact (``canair logs``). Rotation (a small ``maxBytes``
with a few backups) bounds its growth — it self-cleans and never needs manual
pruning. Every event carries a ``run=`` field naming the canair process that
wrote it, so one session's events can be told apart from every other
session's in the shared file. Everything here is best-effort: a filesystem
error must never break a command, so writes are guarded.
"""

import logging
import logging.handlers
import re
import secrets
import traceback
from datetime import UTC, datetime

from .paths import config_dir

# Keep the file small and self-cleaning: a handful of backups of a modest size
# is plenty for post-hoc inspection of transport drops/errors without ever
# growing unbounded. RotatingFileHandler collates/prunes automatically.
_EVENT_LOG_MAX_BYTES = 256 * 1024
_EVENT_LOG_BACKUPS = 3
_event_logger: logging.Logger | None = None
_RUN_ID = secrets.token_hex(4)

# Line format written to the log; parsed back by `canair logs` (fields optional).
# e.g. `2026-07-28T09:00:00.000Z WARNING drop transport=slcan-tcp ecu=BMS pid=2102 run=1a2b3c4d
# :: <detail>` (one line)
_EVENT_RE = re.compile(
    r"^(?P<ts>\S+)\s+(?P<level>\w+)\s+(?P<category>\S+)"
    r"(?P<fields>(?:\s+\w+=\S+)*)"
    r"(?:\s+::\s+(?P<detail>.*))?$"
)


def run_id() -> str:
    """The ``run=`` value this process stamps on its events (random, so PII-free)."""
    return _RUN_ID


def event_log_path():
    """Path to the central diagnostics log (may not exist yet)."""
    return config_dir() / "logs" / "canair.log"


def _get_event_logger() -> logging.Logger | None:
    """Lazily create the rotating central-log logger (None if it can't open)."""
    global _event_logger
    if _event_logger is not None:
        return _event_logger
    try:
        path = event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("canair.events")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=_EVENT_LOG_MAX_BYTES,
                backupCount=_EVENT_LOG_BACKUPS,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        _event_logger = logger
    except OSError:
        return None
    return _event_logger


def _fields(**kv: object) -> str:
    """Render ``key=value`` field pairs, skipping empties, spaces stripped."""
    parts = []
    for k, v in kv.items():
        if v in (None, ""):
            continue
        parts.append(f"{k}={str(v).replace(' ', '_')}")
    return (" " + " ".join(parts)) if parts else ""


def log_event(
    category: str,
    detail: str = "",
    *,
    level: int = logging.WARNING,
    transport: str | None = None,
    ecu: str | None = None,
    pid: str | None = None,
) -> None:
    """Append one diagnostics event (transport drop/error, etc.) to the central log.

    Best-effort: filesystem errors are swallowed so logging never breaks a
    command. ``category`` is a transport outcome bucket (see
    :data:`canlib.uds.response.ERROR_CATEGORIES`); the ``transport``/``ecu``/``pid``
    context is recorded as ``key=value`` fields, and ``detail`` is free text.
    """
    logger = _get_event_logger()
    if logger is None:
        return
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    level_name = logging.getLevelName(level)
    fields = _fields(transport=transport, ecu=ecu, pid=pid, run=_RUN_ID)
    suffix = f" :: {detail}" if detail else ""
    try:
        logger.log(level, f"{ts} {level_name} {category}{fields}{suffix}")
    except Exception:
        pass


def log_exception(message: str, exc: BaseException | None = None) -> None:
    """Record an unexpected internal error (with traceback) to the central log."""
    detail = message
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        detail = f"{message} | {tb}".replace("\n", " ⏎ ")
    log_event("internal", detail, level=logging.ERROR)


def read_event_log(lines: int | None = None, *, run: str | None = None) -> list[str]:
    """Return the central log's lines (oldest→newest), optionally last ``lines``.

    Reads the active file plus any rotated ``.1``/``.2`` backups so a recent
    burst that rolled over is still visible. ``run`` keeps only the events that
    process wrote (see :func:`run_id`), applied before the ``lines`` tail.
    Returns an empty list when the log doesn't exist.
    """
    path = event_log_path()
    if not path.exists():
        return []
    collected: list[str] = []
    # Rotated backups are older; read them oldest-first, then the active file.
    for idx in range(_EVENT_LOG_BACKUPS, 0, -1):
        backup = path.with_name(f"{path.name}.{idx}")
        if backup.exists():
            collected.extend(_read_lines(backup))
    collected.extend(_read_lines(path))
    if run is not None:
        collected = [ln for ln in collected if parse_event_line(ln).get("run") == run]
    if lines is not None and lines >= 0:
        return collected[-lines:]
    return collected


def _read_lines(path) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []


def parse_event_line(line: str) -> dict:
    """Parse a central-log line into its fields (best-effort, for ``--json``)."""
    m = _EVENT_RE.match(line)
    if not m:
        return {"raw": line}
    out: dict = {
        "time": m.group("ts"),
        "level": m.group("level"),
        "category": m.group("category"),
    }
    for pair in (m.group("fields") or "").split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k] = v
    if m.group("detail"):
        out["detail"] = m.group("detail")
    return out


def clear_event_log() -> int:
    """Delete the central log and its rotated backups. Returns files removed."""
    global _event_logger
    # Drop the cached logger + its open handlers so the files can be removed and
    # a subsequent write re-opens fresh ones.
    if _event_logger is not None:
        for h in list(_event_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            _event_logger.removeHandler(h)
        _event_logger = None
    path = event_log_path()
    removed = 0
    for candidate in [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, 20))]:
        try:
            if candidate.exists():
                candidate.unlink()
                removed += 1
        except OSError:
            pass
    return removed
