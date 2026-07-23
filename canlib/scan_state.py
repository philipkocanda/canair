"""Scan progress state files.

Scanners write a small JSON state file every N probes so that if the scan
is killed or interrupted, the next canair invocation can warn the user and
show where the scan left off.

File location: /tmp/wican-scan-<type>-<ecu>.json
  type: "iocontrol" | "routines"
  ecu:  ECU name (upper-case), e.g. "BCM"

State file format::

    {
        "type":      "iocontrol",    # or "routines"
        "ecu":       "BCM",
        "tx_id":     "0x7A0",
        "range":     "0xB000..0xB3FF",
        "current":   "0xB042",       # last probe attempted
        "hits":      12,
        "total":     1024,
        "pid":       123456,         # writer PID (for liveness check)
        "started":   "2026-04-21T10:00:00"
    }

The file is deleted by the scanner on clean completion (or successful
Ctrl+C handling in ``mode_iocontrol_scan`` / ``mode_routines_scan``).  If
it still exists when the next ``canair`` starts, it was abandoned mid-run.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

_STATE_DIR = Path("/tmp")
_STATE_PREFIX = "wican-scan-"


def _state_path(scan_type: str, ecu: str) -> Path:
    return _STATE_DIR / f"{_STATE_PREFIX}{scan_type}-{ecu.upper()}.json"


def _all_state_files() -> list[Path]:
    return sorted(_STATE_DIR.glob(f"{_STATE_PREFIX}*.json"))


class ScanStateWriter:
    """Write/update a scan state file every ``write_every`` probes.

    Usage::

        writer = ScanStateWriter("iocontrol", "BCM", tx_id=0x7A0,
                                 start=0xB000, end=0xB3FF)
        writer.open()
        for did in range(start, end + 1):
            ...probe...
            writer.update(did, hits=len(hits))
        writer.close()   # deletes the state file on clean exit

    The writer can also be used as a context manager::

        with ScanStateWriter(...) as w:
            for did in ...:
                w.update(did, hits=...)
    """

    def __init__(
        self,
        scan_type: str,
        ecu: str,
        tx_id: int,
        start: int,
        end: int,
        write_every: int = 16,
    ):
        self._path = _state_path(scan_type, ecu)
        self._type = scan_type
        self._ecu = ecu.upper()
        self._tx_id = tx_id
        self._start = start
        self._end = end
        self._write_every = write_every
        self._probe_count = 0
        self._started = datetime.now(UTC).isoformat(timespec="seconds")

    def open(self) -> None:
        """Write the initial state file."""
        self._write(current=self._start, hits=0)

    def update(self, current: int, hits: int) -> None:
        """Call after each probe. Writes to disk every write_every probes."""
        self._probe_count += 1
        if self._probe_count % self._write_every == 0:
            self._write(current=current, hits=hits)

    def _write(self, current: int, hits: int) -> None:
        total = self._end - self._start + 1
        state = {
            "type": self._type,
            "ecu": self._ecu,
            "tx_id": f"0x{self._tx_id:03X}",
            "range": f"0x{self._start:04X}..0x{self._end:04X}",
            "current": f"0x{current:04X}",
            "hits": hits,
            "total": total,
            "pid": os.getpid(),
            "started": self._started,
        }
        try:
            self._path.write_text(json.dumps(state, indent=2) + "\n")
        except OSError:
            pass  # non-fatal; state file is best-effort

    def close(self) -> None:
        """Delete the state file — scan completed cleanly."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> ScanStateWriter:
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.close()
        # On exception (including KeyboardInterrupt): leave the file in place
        return False


def find_aborted_scans() -> list[dict]:
    """Return a list of state dicts for any scan state files that exist.

    A state file that belongs to a still-running process is skipped
    (the lock file already handles the "another canair is running" case,
    but this is a belt-and-suspenders check).
    """
    results = []
    for path in _all_state_files():
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        # Skip if the original process is still alive
        writer_pid = state.get("pid")
        if writer_pid:
            try:
                os.kill(int(writer_pid), 0)
                # Process is alive — skip
                continue
            except (ProcessLookupError, PermissionError, ValueError):
                pass  # dead or inaccessible → stale file

        results.append(state)
    return results


def clear_aborted_scan(scan_type: str, ecu: str) -> None:
    """Delete a specific stale state file (e.g. after the user acknowledges it)."""
    _state_path(scan_type, ecu).unlink(missing_ok=True)
