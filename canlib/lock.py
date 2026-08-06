"""WiCAN connection mutex using flock(2), plus its cooperative steal protocol.

Only one canair process may talk to the device at a time (over either transport
— the WebSocket ELM327 terminal or the SLCAN TCP socket), because the device
serves a single connection. The mutex is an exclusive advisory lock on
:data:`LOCK_FILE`, which the kernel releases when the holder exits (clean or
crash), so a *dead* holder never leaves a stale lock.

A *live* holder is the interesting case: an orphaned session (a monitor whose
terminal vanished, say) keeps both the lock and the device connection. ``--force``
therefore doesn't just take the lock — it asks the holder to leave:

1. The contender records a **steal request** in :data:`STEAL_FILE`, naming the
   holder it targets (``target=``) and itself (``by=``).
2. The holder's watchdog (:mod:`canlib.lock_watchdog`) polls
   :meth:`WiCANLock.still_ours`, sees the request, and stops gracefully —
   reconciling any ``--save`` journal and closing the device connection.
3. The contender polls for the lock and takes it once freed.

canair never signals or kills another process: if the holder doesn't respond
within the wait window (an older canair without the watchdog, or a wedged
process), ``--force`` gives up with the holder's PID and the exact ``kill``
command to run. It never blocks indefinitely.

The request is *target-scoped* and TTL'd, so a leftover request can never abort
an unrelated later session.

Usage:
    lock = WiCANLock()
    lock.acquire(force=args.force)   # exits with an error message if contended
    try:
        ...
    finally:
        lock.release()
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

LOCK_FILE = Path("/tmp/wican-connection.lock")
#: Sibling of the lock file holding a pending steal request. Kept separate so the
#: lock file itself stays a bare PID — readable by any canair version.
STEAL_FILE = Path("/tmp/wican-connection.steal")

#: How long ``--force`` waits for the holder to notice a steal request and exit.
#: Covers a monitor's graceful teardown (journal reconcile + session close).
FORCE_WAIT_S = 10.0
#: A steal request older than this is ignored — its requester never followed
#: through (crashed between writing the request and taking the lock).
STEAL_TTL_S = 60.0

_POLL_S = 0.2


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still alive for our purposes.
        return True
    return True


def _parse_etime(value: str) -> float | None:
    """Parse ``ps -o etime`` (``[[DD-]HH:]MM:SS``) into seconds."""
    days = 0
    if "-" in value:
        head, _, value = value.partition("-")
        try:
            days = int(head)
        except ValueError:
            return None
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    return days * 86400 + seconds


@dataclass(frozen=True)
class HolderInfo:
    """What we can learn about a lock holder from the OS, for user-facing messages."""

    pid: int
    alive: bool
    cmdline: str | None = None
    age_s: float | None = None

    @property
    def is_canair(self) -> bool:
        """True when the command line looks like a canair process.

        Advisory only — canair never signals another process on its own, so this
        just sharpens the message.
        """
        return bool(self.cmdline and "canair" in self.cmdline.lower())

    @property
    def looks_foreign(self) -> bool:
        """True when we can *see* the process is not canair.

        Positive evidence, not absence of evidence: an unreadable command line
        (no ``ps``, a foreign-owned process) is unknown, not foreign — so a
        holder we simply can't inspect isn't declared innocent *or* guilty.
        """
        return self.cmdline is not None and not self.is_canair

    def describe(self) -> str:
        """One-line ``PID 123 (canair monitor …, 4m ago)`` for messages."""
        bits = []
        if self.cmdline:
            bits.append(self.cmdline if len(self.cmdline) <= 60 else self.cmdline[:57] + "…")
        if self.age_s is not None:
            bits.append(f"running {_format_age(self.age_s)}")
        if not self.alive:
            bits.append("no longer running")
        return f"PID {self.pid}" + (f" ({', '.join(bits)})" if bits else "")


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def holder_info(pid: int) -> HolderInfo:
    """Best-effort OS facts about ``pid`` (liveness, command line, age)."""
    alive = _pid_alive(pid)
    cmdline: str | None = None
    age: float | None = None
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime=,command="],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        line = proc.stdout.strip()
        if line:
            etime, _, command = line.partition(" ")
            age = _parse_etime(etime.strip())
            cmdline = command.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return HolderInfo(pid=pid, alive=alive, cmdline=cmdline, age_s=age)


def read_holder(path: Path | None = None) -> int | None:
    """The PID recorded in the lock file, or None if absent/empty/unreadable."""
    path = path or LOCK_FILE
    try:
        data = path.read_text().strip()
    except OSError:
        return None
    try:
        return int(data.splitlines()[0]) if data else None
    except (ValueError, IndexError):
        return None


@dataclass(frozen=True)
class StealRequest:
    """A pending "please release the connection" request.

    ``target`` is the PID being asked to leave, so a leftover request can only
    ever affect the process it was written for. ``at`` is a wall-clock stamp used
    to expire a request whose requester died before taking the lock.
    """

    target: int
    by: int
    at: float

    @property
    def expired(self) -> bool:
        return (time.time() - self.at) > STEAL_TTL_S

    def actionable_for(self, pid: int) -> bool:
        """True if ``pid`` should stand down for this request."""
        return self.target == pid and self.by != pid and not self.expired and _pid_alive(self.by)


def read_steal(path: Path | None = None) -> StealRequest | None:
    """Parse a pending steal request, or None if absent/malformed."""
    path = path or STEAL_FILE
    try:
        text = path.read_text()
    except OSError:
        return None
    fields: dict[str, str] = {}
    for token in text.split():
        key, _, value = token.partition("=")
        if value:
            fields[key] = value
    try:
        return StealRequest(
            target=int(fields["target"]), by=int(fields["by"]), at=float(fields["at"])
        )
    except (KeyError, ValueError):
        return None


def write_steal(target: int, path: Path | None = None) -> bool:
    """Record a steal request against ``target``. False if it can't be written."""
    path = path or STEAL_FILE
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    except OSError:
        return False
    try:
        os.write(fd, f"target={target} by={os.getpid()} at={time.time():.3f}\n".encode())
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def clear_steal(path: Path | None = None) -> None:
    """Remove a served/moot steal request (best-effort)."""
    path = path or STEAL_FILE
    try:
        path.unlink()
    except OSError:
        pass


@dataclass(frozen=True)
class LockState:
    """Read-only snapshot of the mutex, for ``canair status`` / ``canair lock``."""

    path: Path
    held: bool
    pid: int | None = None
    ours: bool = False
    holder: HolderInfo | None = None
    steal: StealRequest | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "held": self.held,
            "pid": self.pid,
            "ours": self.ours,
            "cmdline": self.holder.cmdline if self.holder else None,
            "age_s": self.holder.age_s if self.holder else None,
            "alive": self.holder.alive if self.holder else None,
            # One-line rendering of the holder, so every surface (status, lock,
            # the --force messages) describes it identically.
            "summary": self.describe(),
            "steal_pending": (
                {"target": self.steal.target, "by": self.steal.by, "at": self.steal.at}
                if self.steal
                else None
            ),
            "error": self.error,
        }

    def describe(self) -> str | None:
        """One-line description of the holder, or None when the lock is free."""
        if not self.held:
            return None
        if self.holder is not None:
            return self.holder.describe()
        return f"PID {self.pid}" if self.pid else "an unknown process"


def lock_state(lock_file: Path | None = None, steal_file: Path | None = None) -> LockState:
    """Probe the mutex without disturbing it (never creates the lock file).

    Held-ness is tested by trying the lock and immediately dropping it again, so
    a free lock reads as free and a held one as held — without waiting on either.
    """
    lock_file = lock_file or LOCK_FILE
    steal = read_steal(steal_file or STEAL_FILE)
    if not lock_file.exists():
        return LockState(path=lock_file, held=False, steal=steal)
    pid = read_holder(lock_file)
    try:
        fd = os.open(str(lock_file), os.O_RDONLY)
    except PermissionError:
        return LockState(
            path=lock_file,
            held=True,  # unreadable => assume another (foreign-owned) session holds it
            pid=pid,
            holder=holder_info(pid) if pid else None,
            steal=steal,
            error=f"lock file is owned by {_owner_name(lock_file)} — not readable by you",
        )
    except OSError as e:
        return LockState(path=lock_file, held=False, pid=pid, steal=steal, error=str(e))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            held = True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            held = False
    finally:
        os.close(fd)
    return LockState(
        path=lock_file,
        held=held,
        pid=pid if held else None,
        ours=bool(held and pid == os.getpid()),
        holder=holder_info(pid) if (held and pid) else None,
        steal=steal,
    )


def _owner_name(path: Path) -> str:
    try:
        import pwd

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (OSError, KeyError, ImportError):
        return "another user"


@dataclass(frozen=True)
class AcquireResult:
    """Outcome of :meth:`WiCANLock.try_acquire` — no printing, no exiting."""

    ok: bool
    #: Machine-readable outcome: free | stolen | contended | timeout | permission
    outcome: str
    holder: HolderInfo | None = None
    waited_s: float = 0.0
    detail: str | None = None


class WiCANLock:
    """Exclusive advisory lock on :data:`LOCK_FILE` via flock(2).

    The lock file records the holder's PID so contenders can name it. The
    cooperative steal protocol (see the module docstring) lets ``--force`` ask a
    live holder to leave instead of blocking on it forever.
    """

    def __init__(self, lock_file: Path | None = None, steal_file: Path | None = None):
        # Resolved per instance (not as an argument default) so the paths stay
        # redirectable — tests point them at a tmpdir instead of the real /tmp.
        self._path = lock_file or LOCK_FILE
        self._steal_path = steal_file or STEAL_FILE
        self._fd: int | None = None
        self._ident: tuple[int, int] | None = None

    # -- introspection -----------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def steal_path(self) -> Path:
        return self._steal_path

    @property
    def held(self) -> bool:
        return self._fd is not None

    def still_ours(self) -> str | None:
        """Why we no longer own the mutex, or None while we still do.

        The check an orphaned session runs (via :mod:`canlib.lock_watchdog`) so
        it stands down instead of holding the device connection hostage: a
        pending steal request aimed at us, another PID recorded as holder, or the
        lock file removed/replaced underneath us.
        """
        if self._fd is None:
            return None

        try:
            st = self._path.stat()
        except OSError:
            return "the connection lock file was removed"
        if self._ident is not None and (st.st_dev, st.st_ino) != self._ident:
            return "the connection lock file was replaced by another session"

        holder = read_holder(self._path)
        if holder is not None and holder != os.getpid():
            return f"the connection lock was taken over by PID {holder}"

        request = read_steal(self._steal_path)
        if request is not None and request.actionable_for(os.getpid()):
            return f"PID {request.by} requested the connection (canair --force)"
        return None

    # -- acquisition -------------------------------------------------------

    def try_acquire(
        self,
        force: bool = False,
        wait: float = FORCE_WAIT_S,
        report=None,
    ) -> AcquireResult:
        """Acquire the lock, returning the outcome instead of exiting.

        With ``force``, a live holder is asked to release (steal request) and
        polled for up to ``wait`` seconds. ``report`` is an optional
        ``callable(str)`` for progress lines (no printing when omitted).
        """
        say = report or (lambda _msg: None)
        try:
            self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)
        except PermissionError:
            self._fd = None
            pid = read_holder(self._path)
            return AcquireResult(
                ok=False,
                outcome="permission",
                holder=holder_info(pid) if pid else None,
                detail=(
                    f"the connection lock file {self._path} is owned by "
                    f"{_owner_name(self._path)} and not writable by you"
                ),
            )

        if self._try_lock():
            self._claim()
            return AcquireResult(ok=True, outcome="free")

        pid = read_holder(self._path)
        holder = holder_info(pid) if pid else None
        if not force:
            self._close()
            return AcquireResult(ok=False, outcome="contended", holder=holder)

        # Cooperative steal: ask the holder to stand down, then wait for it.
        if pid is not None and not write_steal(pid, self._steal_path):
            say(
                f"could not record a steal request in {self._steal_path} "
                "(owned by another user?) — waiting for the holder anyway"
            )
        elif holder is not None:
            say(f"asking the current session to release the connection — {holder.describe()}")

        start = time.monotonic()
        acquired = self._wait_for_lock(wait)
        waited = time.monotonic() - start
        if acquired:
            self._claim()
            return AcquireResult(ok=True, outcome="stolen", holder=holder, waited_s=waited)

        clear_steal(self._steal_path)
        self._close()
        current = read_holder(self._path)
        return AcquireResult(
            ok=False,
            outcome="timeout",
            holder=holder_info(current) if current else holder,
            waited_s=waited,
        )

    def acquire(self, force: bool = False, wait: float = FORCE_WAIT_S):
        """Acquire the lock, or print an actionable error and exit(1).

        The CLI-facing wrapper around :meth:`try_acquire` used by every live
        command.
        """

        def _say(msg: str) -> None:
            print(f"  {msg}", file=sys.stderr)

        result = self.try_acquire(force=force, wait=wait, report=_say)
        if result.ok:
            if result.outcome == "stolen":
                _say(f"connection released after {result.waited_s:.1f}s — continuing")
            return
        print(describe_acquire_failure(result), file=sys.stderr)
        sys.exit(1)

    def release(self):
        """Release the lock."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()

    # -- internals ---------------------------------------------------------

    def _try_lock(self) -> bool:
        assert self._fd is not None
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _wait_for_lock(self, wait: float) -> bool:
        deadline = time.monotonic() + max(wait, 0.0)
        while True:
            if self._try_lock():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_S)

    def _claim(self) -> None:
        """Record our PID as holder and drop any now-moot steal request."""
        assert self._fd is not None
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, str(os.getpid()).encode())
        try:
            st = os.fstat(self._fd)
            self._ident = (st.st_dev, st.st_ino)
        except OSError:
            self._ident = None
        request = read_steal(self._steal_path)
        # Any request in flight targeted a *previous* holder, so holding the lock
        # makes it moot. A request aimed at us (impossible right after start) is
        # left alone so our own watchdog can act on it.
        if request is not None and request.target != os.getpid():
            clear_steal(self._steal_path)

    def _close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None
        self._ident = None


def describe_acquire_failure(result: AcquireResult) -> str:
    """Human-facing explanation + next step for a failed acquire."""
    if result.outcome == "permission":
        return (
            f"ERROR: {result.detail}.\n"
            "  Another user's canair session appears to hold the device connection.\n"
            "  Ask them to stop it (or clear it as that user / with sudo)."
        )
    if result.outcome == "contended":
        who = result.holder.describe() if result.holder else "another session"
        return (
            f"ERROR: Another canair session is already using the WiCAN — {who}.\n"
            "  Only one canair session can talk to the device at a time.\n"
            "  Inspect it with `canair lock`, or re-run with --force to ask it to stop."
        )
    if result.outcome == "timeout":
        who = result.holder.describe() if result.holder else "the holder"
        pid = result.holder.pid if result.holder else None
        kill = f"kill {pid}" if pid else "kill <pid>"
        hard = f"kill -9 {pid}" if pid else "kill -9 <pid>"
        return (
            f"ERROR: The session holding the connection did not release it within "
            f"{result.waited_s:.0f}s — {who}.\n"
            "  It may predate canair's cooperative release, or be wedged. It still holds\n"
            "  the device's single connection, so a new session would time out.\n"
            f"  Stop it with:  {kill}    (if it ignores that:  {hard})\n"
            "  Any --save data it recorded is safe: `canair captures uds --recover`."
        )
    return f"ERROR: could not acquire the connection lock ({result.outcome})."
