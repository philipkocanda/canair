"""Sensitive PIDs and signals: masked on screen, never saved, never shared.

A PID or a signal declares ``sensitive: true`` when its value identifies the car or
its owner in a way a VIN does — the Ioniq CCM's ``22F102`` is the PLC MAC address the
car sends a CCS charger as its EVCCID, which Autocharge chargers bill against. The
rule is one decision, applied at every seam rather than per command:

* **On screen** a sensitive value renders as :data:`MASK`. A sensitive *signal*
  also masks its PID's raw payload, since the bytes give the value away. Only
  ``canair read --reveal`` lifts the mask, for that one process
  (:func:`set_reveal`), and it cannot be combined with ``--save``.
* **On disk** a reply to a sensitive PID is never written — not to the journal, not
  to ``captures/`` — whatever the reveal setting (:func:`withhold`). Neither is a memory
  read over :data:`~canair.uds.memory.MEMORY_SIGNAL_MAX_BYTES`: that is a dump, which may
  hold OEM software or secrets, and stays in ``canair memory``'s local store. A save that
  dropped something says so once (:func:`take_withheld_note`).
* **Shared** copies (``contribute``, the blind-test strip, the docs-site explorer,
  the WiCAN AutoPID export) leave sensitive data out, and ``validate captures``
  errors on any reply that slipped into the store anyway.

The flag lives on the PID and on individual signals in ``ecus/`` (set with ``canair
pids set-sensitive``). A PID is sensitive when it, or any of its signals, says so.
This module is a leaf over the definitions; it never reaches into the commands.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canair.ecus.pids import load_raw_pids, register_derived_cache
from canair.ecus.registry import parse_ecu_ref
from canair.ecus.sensitivity import param_sensitive, pid_sensitive
from canair.profile.model import Profile, ProfileError, profile_for_path
from canair.uds.addressing import resolve_ecu_address
from canair.uds.memory import MEMORY_SIGNAL_MAX_BYTES, exceeds_signal_cap

MASK = "*****"

# Values a person already redacted by hand before the PID was flagged. A stored reply
# that holds only one of these discloses nothing, so it is kept, with the note that
# explains it. Hardcoded until a second profile needs one: the Ioniq's VCU VIN page
# (its WMI+VDS prefix, which names the model, not the car, then 13 `X`).
REDACTED_VALUES = frozenset({"KMHCXXXXXXXXXXXXX"})


def is_redacted(cap: Mapping[str, Any]) -> bool:
    """Is this stored capture row a hand-redacted value rather than a real reply?"""
    return not cap.get("payload") and str(cap.get("response") or "") in REDACTED_VALUES


def norm_pid(pid: Any) -> str:
    """A PID/request as compared here: upper-case hex with no separators."""
    return "".join(str(pid).split()).upper()


# ---------------------------------------------------------------------------
# Reveal (on-screen only)
# ---------------------------------------------------------------------------

_reveal = False


def set_reveal(on: bool) -> None:
    """Show sensitive values in clear for the rest of this process (``read --reveal``)."""
    global _reveal
    _reveal = bool(on)


def revealed() -> bool:
    return _reveal


def masked(sensitive: bool) -> bool:
    """Should a value of this sensitivity be hidden on screen right now?"""
    return sensitive and not _reveal


# ---------------------------------------------------------------------------
# The profile's sensitive set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensitiveSet:
    """The profile's sensitive PIDs, keyed by ECU name, request id and response id."""

    by_ecu: frozenset[tuple[str, str]] = frozenset()
    by_tx: frozenset[tuple[int, str]] = frozenset()
    by_rx: frozenset[tuple[int, str]] = frozenset()
    labels: Mapping[tuple[int, str], str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.by_ecu)

    def __or__(self, other: SensitiveSet) -> SensitiveSet:
        return SensitiveSet(
            self.by_ecu | other.by_ecu,
            self.by_tx | other.by_tx,
            self.by_rx | other.by_rx,
            {**self.labels, **other.labels},
        )

    def has(self, ecu: str, pid: Any) -> bool:
        return (str(ecu).upper(), norm_pid(pid)) in self.by_ecu

    def has_request(self, tx_id: int, request: Any) -> bool:
        return (int(tx_id), norm_pid(request)) in self.by_tx

    def has_capture(self, cap: Mapping[str, Any]) -> bool:
        """Is this stored or journaled capture row a reply to a sensitive PID?

        A hand-redacted row (:func:`is_redacted`) is not: it holds no reply.
        """
        if is_redacted(cap):
            return False
        rx = _rx_int(cap.get("rx") or cap.get("ecu"))
        return rx is not None and (rx, norm_pid(cap.get("pid", ""))) in self.by_rx

    def label(self, cap: Mapping[str, Any]) -> str:
        """``ECU:PID`` for a sensitive capture row, for the "not saved" note."""
        rx = _rx_int(cap.get("rx") or cap.get("ecu"))
        pid = norm_pid(cap.get("pid", ""))
        if rx is None:
            return pid
        return self.labels.get((rx, pid), f"0x{rx:03X}:{pid}")


def _rx_int(value: Any) -> int | None:
    return parse_ecu_ref(value)


def build_set(pids_data: Mapping[str, Any]) -> SensitiveSet:
    """The sensitive set of one set of definitions (as written: every revision)."""

    by_ecu: set[tuple[str, str]] = set()
    by_tx: set[tuple[int, str]] = set()
    by_rx: set[tuple[int, str]] = set()
    labels: dict[tuple[int, str], str] = {}
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, Mapping) or ecu_def.get("tx_id") is None:
            continue
        pids = [norm_pid(p) for p, d in (ecu_def.get("pids") or {}).items() if pid_sensitive(d)]
        if not pids:
            continue
        tx = int(ecu_def["tx_id"])
        rx = resolve_ecu_address(pids_data, ecu_def).rx_id
        for pid in pids:
            by_ecu.add((str(name).upper(), pid))
            by_tx.add((tx, pid))
            by_rx.add((rx, pid))
            labels[(rx, pid)] = f"{name}:{pid}"
    return SensitiveSet(frozenset(by_ecu), frozenset(by_tx), frozenset(by_rx), labels)


_sets: dict[str, SensitiveSet] = {}


def _clear_sets() -> None:
    _sets.clear()


def profile_set(profile: Profile) -> SensitiveSet:
    """``profile``'s sensitive set.

    Built from the definitions *as written* (every variant and revision), so a flag
    on any revision of a PID protects it on every car. Memoized per profile root and
    cleared with the definitions (:func:`canair.ecus.pids.register_derived_cache`).
    """

    prof = profile
    key = str(prof.root)
    cached = _sets.get(key)
    if cached is not None:
        return cached
    try:
        built = build_set(load_raw_pids(prof))
    except (ProfileError, OSError, ValueError, KeyError, TypeError):
        built = SensitiveSet()
    register_derived_cache(_clear_sets)
    _sets[key] = built
    return built


def set_for_captures(captures_dir: Path | None, *, profile: Profile) -> SensitiveSet:
    """The sensitive set governing writes into ``captures_dir``.

    The union of ``profile``'s set and that of the profile owning the directory (when it has
    definitions of its own), so an explicit ``--dir`` or a layered overlay is never judged
    against the wrong — or an empty — vocabulary.
    """

    out = profile_set(profile)
    if captures_dir is not None:
        owner = profile_for_path(captures_dir)
        if owner.ecus_dir.is_dir():
            out = out | profile_set(owner)
    return out


# ---------------------------------------------------------------------------
# Persistence gate
# ---------------------------------------------------------------------------

_withheld: Counter[str] = Counter()
_oversized: Counter[str] = Counter()


def _oversized_memory(row: Mapping[str, Any]) -> bool:
    """A memory reply bigger than a signal: a dump, which never enters ``captures/``.

    Only a row holding a reply counts; a refusal of a large read discloses nothing.
    """
    return bool(row.get("payload")) and exceeds_signal_cap(row.get("pid", ""))


def withhold(rows: Iterable[Mapping[str, Any]], sset: SensitiveSet) -> list[Any]:
    """``rows`` minus every reply to a sensitive PID or an oversized memory read, counted."""
    kept: list[Any] = []
    for row in rows:
        if (sset and sset.has_capture(row)) or row.get("payload") == MASK:
            _withheld[sset.label(row)] += 1
            continue
        if _oversized_memory(row):
            _oversized[sset.label(row)] += 1
            continue
        kept.append(row)
    return kept


def withhold_session(session: dict, sset: SensitiveSet) -> dict:
    """A copy of ``session`` without its withheld captures (the same object if none)."""
    caps = session.get("captures")
    if not isinstance(caps, list):
        return session
    kept = withhold(caps, sset)
    if len(kept) == len(caps):
        return session
    return {**session, "captures": kept}


def take_withheld_note() -> str:
    """The "not saved" note for what the gate dropped since the last call (one line each)."""
    lines: list[str] = []
    if _withheld:
        what = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in sorted(_withheld.items()))
        lines.append(f"  ⚠ Not saved: {what} — sensitive, never written to captures.")
        _withheld.clear()
    if _oversized:
        what = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in sorted(_oversized.items()))
        lines.append(
            f"  ⚠ Not saved: {what} — a memory read over {MEMORY_SIGNAL_MAX_BYTES} bytes is a "
            "dump; `canair memory dump` keeps one off the profile."
        )
        _oversized.clear()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def request_hidden(tx_id: int, request: Any, *, profile: Profile) -> bool:
    """Should the reply to this raw request be masked on screen right now?"""
    return masked(profile_set(profile).has_request(tx_id, request))


def masked_response(response: Mapping[str, Any]) -> Any:
    """A copy of a UDS response dict with its payload masked, for JSON output."""
    out = dict(response)
    for key in ("hex", "bytes", "raw", "data"):
        if key in out:
            out[key] = MASK
    out["sensitive"] = True
    return out


def mask_result(
    entry: Any,
    target: int | str,
    pid_info: Any,
    sset: SensitiveSet | None = None,
    *,
    profile: Profile,
):
    """Mask one live read result in place when it answers a sensitive PID.

    ``entry`` is a :data:`~canair.expression.results.ResultEntry`; ``target`` the
    request id (or the ECU's name); ``pid_info`` the PID's index entry (its own
    ``sensitive`` flag and ``parameters``). The entry is tagged ``sensitive`` either
    way, and — unless revealed — its raw payload, UDS decode and sensitive signal
    values are replaced by :data:`MASK` *before* any renderer or recorder sees them,
    so no display path can leak the bytes and no save path can persist them.
    """
    if "raw_hex" not in entry:
        return entry
    sset = profile_set(profile) if sset is None else sset
    pid = entry.get("pid", "")
    hit = sset.has_request(target, pid) if isinstance(target, int) else sset.has(target, pid)
    if not hit:
        return entry
    entry["sensitive"] = True
    if _reveal:
        return entry
    entry["raw_hex"] = MASK
    if entry.get("decode"):
        entry["decode"] = MASK
    entry["params"] = mask_rows(entry.get("params") or [], pid_info)
    return entry


def mask_rows(rows: list, pid_def: Any) -> list:
    """Decoded signal rows with every sensitive value replaced by :data:`MASK`.

    Rows are ``(name, value, unit, expression, error, verified[, display])`` tuples
    (:func:`canair.expression.decoding.decode_param_rows`). A masked row keeps its
    name and mark but drops the unit and display formatting, which would otherwise
    print beside the mask.
    """
    if _reveal:
        return rows
    params = (pid_def or {}).get("parameters") or {}
    out = []
    for row in rows:
        if param_sensitive(pid_def, params.get(row[0])):
            row = (row[0], MASK, "", row[3], None, row[5], "")
        out.append(row)
    return out
