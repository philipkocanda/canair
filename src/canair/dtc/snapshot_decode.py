"""Decode DTC snapshot records with the profile's PID definitions.

The bridge between the I/O-free parser (:mod:`canair.dtc.snapshot`) and the profile:

* :func:`length_resolver` answers the parser's "how long is this DID's data?" from a
  declared ``snapshot_length:``, else from the DID's own live replies in the captures
  (never for a ``variable_length:`` PID, whose live replies have no one length).
* :func:`snapshot_entries` turns a parsed record into the JSON-able shape ``canair dtc``
  prints, logs and emits with ``--json``, each DID decoded by its ``22<DID>`` PID's
  signals. A snapshot's data is decoded as the reply a live read would have given
  (``62 <DID> <data>``), so every ``Bnn`` expression means the same thing in both.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

from canair.dtc.snapshot import (
    SnapshotDid,
    SnapshotError,
    SnapshotRecord,
    parse_snapshot_reply,
    read_pid,
)
from canair.ecus.sensitivity import pid_sensitive
from canair.expression.decoding import decode_param_rows
from canair.profile import Profile

# The live reply header a snapshot DID's data is wrapped in: 62 <DID>.
_READ_REPLY_HEADER = 3


def length_resolver(
    pids: Mapping[str, Mapping[str, Any]],
    ecu: str,
    live_length: Callable[[str, str], int | None] | None = None,
    *,
    profile: Profile,
) -> Callable[[int], int | None]:
    """A ``length_of(did)`` for one ECU: its declared ``snapshot_length``, else its live length.

    ``pids`` is the ECU's ``pids`` index (:func:`canair.ecus.pids.build_ecu_index`). The live
    length (``live_length(ecu, pid)``, :func:`live_reply_length` of ``profile`` by default) is only
    consulted for a DID with no declaration, since it reads the capture history, and never
    for a ``variable_length:`` PID: its live replies have no one length, so their most
    common one would be a guess that silently mis-splits the rest of the record.
    """

    def lookup(ecu_name: str, pid: str) -> int | None:
        if live_length is not None:
            return live_length(ecu_name, pid)
        return live_reply_length(ecu_name, pid, profile=profile)

    def length_of(did: int) -> int | None:
        pid_info = pids.get(read_pid(did)) or {}
        declared = pid_info.get("snapshot_length")
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
            return declared
        if pid_info.get("variable_length") is True:
            return None
        return lookup(ecu, read_pid(did))

    return length_of


@lru_cache(maxsize=256)
def live_reply_length(ecu: str, pid: str, *, profile: Profile) -> int | None:
    """The data length of this DID's live replies in the captures (their most common), if any.

    Only replies that echo the DID count, so a misfiled payload never sets a length.
    """
    from canair.captures.store import load_pid_captures

    echo = f"62{pid[2:]}".upper()
    lengths: Counter[int] = Counter()
    for row in load_pid_captures(ecu, pid, profile=profile):
        payload = str(row.get("payload", "")).upper()
        if payload.startswith(echo) and len(payload) % 2 == 0:
            lengths[len(payload) // 2 - _READ_REPLY_HEADER] += 1
    if not lengths:
        return None
    return max(lengths.items(), key=lambda kv: (kv[1], kv[0]))[0]


def decode_did(snap: SnapshotDid, pids: Mapping[str, Mapping[str, Any]]) -> list[dict] | None:
    """The signals of ``snap``'s ``22<DID>`` PID decoded from its data, or None if undefined.

    A sensitive signal's value is masked (:func:`canair.privacy.sensitive.mask_rows`), like a live
    read's.
    """
    from canair.privacy.sensitive import mask_rows

    pid_info = pids.get(read_pid(snap.did)) or {}
    parameters = pid_info.get("parameters") or {}
    if not parameters:
        return None
    payload_hex = f"62{snap.did:04X}{snap.data.hex().upper()}"
    signals = []
    rows = mask_rows(decode_param_rows(payload_hex, parameters), pid_info)
    for name, value, unit, _expr, error, verified, display in rows:
        entry: dict[str, Any] = {"name": name, "value": value, "unit": unit}
        if display:
            entry["display"] = display
        if error:
            entry["error"] = error
        if verified:
            entry["verified"] = True
        signals.append(entry)
    return signals


def snapshot_entries(
    record: SnapshotRecord, reply_hex: str, pids: Mapping[str, Mapping[str, Any]]
) -> dict:
    """One record as ``{record, hex, dids: [{did, pid, data, signals}], …}``.

    ``signals`` is None for a DID the profile does not define. A record that could not be
    split completely also carries ``unresolved_did`` and the raw ``undecoded`` rest.

    A record holding a sensitive DID (:mod:`canair.privacy.sensitive`) is tagged ``sensitive``,
    which keeps it out of the DTC log and the captures, and unless revealed its raw bytes
    are masked on screen: the reply gives the value away.
    """
    from canair.privacy.sensitive import MASK, masked

    dids = []
    hidden = False
    for d in record.dids:
        pid_info = pids.get(read_pid(d.did)) or {}
        sensitive = pid_sensitive(pid_info)
        hidden = hidden or sensitive
        did: dict[str, Any] = {
            "did": f"{d.did:04X}",
            "pid": read_pid(d.did),
            "data": MASK if masked(sensitive) else d.data.hex().upper(),
            "signals": decode_did(d, pids),
        }
        if sensitive:
            did["sensitive"] = True
        dids.append(did)
    entry: dict[str, Any] = {
        "record": record.number,
        "hex": MASK if masked(hidden) else reply_hex.upper(),
        "dids": dids,
    }
    if hidden:
        entry["sensitive"] = True
    if not record.complete:
        assert record.unresolved_did is not None
        entry["unresolved_did"] = f"{record.unresolved_did:04X}"
        entry["undecoded"] = MASK if masked(hidden) else record.undecoded.hex().upper()
    return entry


def ecu_pids_index(ecu: str | None, *, profile: Profile) -> Mapping[str, Mapping[str, Any]]:
    """The active profile's ``pids`` index for ``ecu`` (empty when it has none or no profile)."""
    if not ecu:
        return {}
    from canair.ecus.pids import build_ecu_index, load_pids
    from canair.profile import ProfileError

    try:
        entry = build_ecu_index(load_pids(profile=profile)).get(ecu.upper()) or {}
    except (ProfileError, OSError):
        return {}
    return entry.get("pids") or {}


def decode_logged(
    ecu: str | None, records: list[Mapping[str, Any]], *, profile: Profile
) -> list[dict]:
    """Logged ``{record, hex}`` replies decoded with today's definitions, as ``snapshot_entries``.

    The DTC log keeps only the raw ``59 04`` replies, so an entry recorded before a DID was
    defined decodes once it is.
    """
    pids = ecu_pids_index(ecu, profile=profile)
    length_of = length_resolver(pids, ecu or "", profile=profile)
    out: list[dict] = []
    for logged in records:
        number, reply_hex = logged.get("record"), str(logged.get("hex") or "")
        try:
            parsed = parse_snapshot_reply(bytes.fromhex(reply_hex), length_of, single_record=True)
        except (SnapshotError, ValueError) as e:
            out.append({"record": number, "hex": reply_hex, "error": str(e)})
            continue
        if not parsed.records:
            out.append({"record": number, "hex": reply_hex, "dids": []})
            continue
        out.append(snapshot_entries(parsed.records[0], reply_hex, pids))
    return out
