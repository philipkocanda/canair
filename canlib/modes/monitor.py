"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

On a TTY this runs a Textual app (:mod:`canlib.modes._monitor_tui`): the latest
values render into a widget that updates *in place* inside a scrollable
container, so the scroll position stays put while values refresh — mouse wheel,
scrollbar and keys all scroll natively and nothing ever freezes. When stdout is
not a TTY (piped/scripted) it polls silently until Ctrl+C and prints the final
values.

Usage (via canair query --monitor):
    canair query "session BCM --wake" "query BCM:C00B,B00E" --monitor
    canair query "query BMS:2101" --monitor 2.0
    canair query "session IGPM --wake" "query IGPM:BC03,BC06" --monitor

The --monitor flag applies to the last 'query' step in the pipeline. If
there are multiple query steps, all of them are repeated each cycle.

The polling / decoding / capture-saving logic lives in :class:`MonitorController`
(reused by both the TUI and the non-interactive path); only the presentation
layer differs.
"""

import asyncio
import contextlib
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from ..formatting import (
    _HIGHLIGHT_STYLE,
    _bytes_to_ascii,
    _render_hex_line,
    render_byte_rulers,
    render_param_table,
)
from ..session_manager import SessionManager

# _HIGHLIGHT_STYLE, _bytes_to_ascii and _render_hex_line moved to canlib.formatting;
# re-exported here for backward-compatible imports (e.g. tests/test_monitor.py).
__all__ = [
    "_HIGHLIGHT_STYLE",
    "MonitorController",
    "_bytes_to_ascii",
    "_render_hex_line",
    "_render_results",
    "mode_monitor",
    "query_ecu_error",
]

_console = Console(highlight=False)


def query_ecu_error(query_steps: list[dict], pids_data: dict) -> str | None:
    """Return an error message if any query step names an unknown ECU, else None.

    Guards against typos like ``query ESC ECS`` (the second selector is a
    non-existent ECU) that would otherwise be silently skipped every poll cycle.
    ECU names are matched case-insensitively against the active profile.
    """
    from ..ecus import build_canonical_name_index, canonical_ecu_name_safe
    from ..pids import build_ecu_index

    ecu_index = build_ecu_index(pids_data)
    try:
        name_index = build_canonical_name_index()
    except FileNotFoundError:
        name_index = None
    seen: set[str] = set()
    unknown: list[str] = []
    for step in query_steps:
        key = canonical_ecu_name_safe(step["ecu"], name_index).upper()
        if key not in ecu_index and key not in seen:
            seen.add(key)
            unknown.append(step["ecu"])
    if not unknown:
        return None
    available = ", ".join(sorted(ecu_index.keys()))
    return (
        f"unknown ECU(s) in query: {', '.join(unknown)}.\n"
        f"  Available ECUs: {available}"
    )



# Cap how many history rows a single PID renders per cycle. With --keep-all a
# long drive accrues thousands of payloads per PID; rendering them all every
# cycle is O(cycles²·PIDs) and unbounded. The full history still lives in the
# journal (--save) — this only bounds the on-screen buffer to the newest rows.
_RENDER_MAX_ROWS = 200


def _render_results(
    queries: list[tuple[str, list]],
    verbose: bool,
    cycle: int,
    elapsed: float,
    interval: float,
    prev_hex: dict[tuple[str, str], str] | None = None,
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
    show_rulers: bool = False,
    footer: bool = True,
) -> Text:
    """Render all ECU query results as a Rich Text object for display.

    ``footer`` appends the "Press Ctrl+C to stop" hint (kept for callers that
    render a single static block). The scrolling monitor passes ``footer=False``
    and draws its own fixed status line below the scroll viewport.
    """
    text = Text()

    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {interval:.1f}s)\n",
        style="dim",
    )

    if prev_hex is None:
        prev_hex = {}

    for ecu_label, pid_results in queries:
        if not pid_results:
            continue

        text.append("\n  ")
        text.append(ecu_label, style="bold cyan")
        text.append("\n")

        for entry in pid_results:
            pid = entry["pid"]
            error = entry.get("error")
            params = entry.get("params", [])
            raw_hex = entry.get("raw_hex", "")
            decode = entry.get("decode")
            unmapped = entry.get("unmapped", False)

            # Detect change from previous cycle
            hex_key = (ecu_label, pid)
            changed = cycle > 1 and raw_hex and hex_key in prev_hex and prev_hex[hex_key] != raw_hex

            text.append("    ")
            text.append(pid, style="yellow")
            if changed:
                text.append(" ●", style="bright_green")
            if unmapped:
                text.append(" (unmapped)", style="dim")
            # Show history count when keeping history
            if hex_history and hex_key in hex_history:
                n_entries = len(hex_history[hex_key])
                if raw_hex and raw_hex not in [h for h, _ts in hex_history[hex_key]]:
                    n_entries += 1  # current not yet added
                if n_entries > 1:
                    text.append(f"  ({n_entries} entries)", style="dim")
            if error:
                text.append(f"  {error}\n", style="red")
                continue
            text.append("\n")

            if params:
                # With rulers on, annotate each param with the payload byte
                # index(es) it maps to (e.g. "16-17"), matching the diff view.
                n_bytes = len(raw_hex) // 2 if (show_rulers and raw_hex) else None
                text.append_text(
                    render_param_table(params, verbose=verbose, n_bytes=n_bytes)
                )
            elif decode:
                text.append(f"      {decode}\n")

            if raw_hex:
                hex_key = (ecu_label, pid)
                # Byte-index ruler, once per PID, above the hex lines.
                if show_rulers:
                    ruler_pw = 16 if hex_history is not None else 6
                    text.append_text(
                        render_byte_rulers(len(raw_hex) // 2, params, prefix_width=ruler_pw)
                    )
                if hex_history and hex_key in hex_history:
                    # Show all unique payloads chronologically, each diffed against predecessor
                    history = hex_history[hex_key]  # list of (hex, timestamp)
                    history_hexes = [h for h, _ts in history]
                    # Include current if not yet in history (first cycle edge case)
                    if raw_hex not in history_hexes:
                        all_entries = [*history, (raw_hex, "")]
                    else:
                        all_entries = list(history)
                    # Bound the rendered rows to the newest _RENDER_MAX_ROWS so a
                    # long --keep-all run stays cheap to render (full data is in
                    # the journal). Older rows are summarized, not walked.
                    if len(all_entries) > _RENDER_MAX_ROWS:
                        omitted = len(all_entries) - _RENDER_MAX_ROWS
                        all_entries = all_entries[-_RENDER_MAX_ROWS:]
                        text.append(
                            f"      … {omitted} earlier entries omitted (in journal)\n",
                            style="dim",
                        )
                    for i, (payload, ts) in enumerate(all_entries):
                        prev_raw = all_entries[i - 1][0] if i > 0 else ""
                        prefix = f"      {ts}  " if ts else "                "
                        text.append_text(
                            _render_hex_line(
                                payload,
                                params,
                                unmapped,
                                prev_raw=prev_raw,
                                prefix=prefix,
                                prefix_style="dim" if ts else "",
                            )
                        )
                else:
                    prev_raw = prev_hex.get(hex_key, "") if prev_hex and cycle > 1 else ""
                    text.append_text(_render_hex_line(raw_hex, params, unmapped, prev_raw=prev_raw))

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text


def _merge_history(
    hex_history: dict[tuple[str, str], list[tuple[str, str]]],
    prev_hex: dict[tuple[str, str], str],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Merge the latest ``prev_hex`` snapshot into the payload history.

    Returns a ``{(ecu_label, pid): [(hex, timestamp), ...]}`` map. A PID whose
    current payload isn't already the last history entry gets it appended with a
    fresh timestamp, so a bare snapshot (no history kept) still yields one row
    per PID.
    """
    all_keys = set(hex_history.keys()) | set(prev_hex.keys())
    merged: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in all_keys:
        entries = list(hex_history.get(key, []))
        cur = prev_hex.get(key, "")
        if cur and cur not in [h for h, _ts in entries]:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            entries.append((cur, ts))
        if entries:
            merged[key] = entries
    return merged


def _write_merged(
    merged: dict[tuple[str, str], list[tuple[str, str]]],
    label: str,
    state: str,
    notes: str,
    captures_dir: Path,
) -> Path:
    """Build a query-capture session from merged payloads and save it to disk.

    The ECU label (e.g. "BMS") is resolved to its CAN response address; an
    unknown label falls back to its leading token verbatim.
    """
    from ..captures import build_query_session, save_session
    from ..ecus import build_name_tx_index, rx_from_name

    name_index = build_name_tx_index()
    results: list[tuple[str, str, str, str]] = []
    for (ecu_label, pid), entries in sorted(merged.items()):
        ecu_short = re.match(r"(\w+)", ecu_label).group(1)
        ecu_ref = rx_from_name(ecu_short, name_index) or ecu_short
        for hex_val, ts in entries:
            results.append((ecu_ref, pid, hex_val, ts))

    session = build_query_session(results, label, state, notes)
    return save_session(session, captures_dir)


def _raw_pid_result(pid_code, pid_info, unmapped, value, acquired_at):
    """Turn a raw-CAN poll result (bytes / Exception / None) into a result dict.

    Mirrors the ELM path's result shape so the renderer/decoder are unchanged.
    """
    from .multi import _decode_pid_result

    if value is None or isinstance(value, Exception):
        err = "timeout" if value is None or isinstance(value, TimeoutError) else str(value)
        return {"pid": pid_code, "error": err, "unmapped": unmapped, "acquired_at": acquired_at}
    resp = bytes(value)
    if not resp:
        return {
            "pid": pid_code,
            "error": "empty response",
            "unmapped": unmapped,
            "acquired_at": acquired_at,
        }
    if resp[0] == 0x7F:  # negative response: 7F <sid> <nrc>
        from ..uds_parse import nrc_abbrev

        nrc = resp[2] if len(resp) >= 3 else 0
        return {
            "pid": pid_code,
            "error": f"NRC 0x{nrc:02X} ({nrc_abbrev(nrc)})",
            "unmapped": unmapped,
            "acquired_at": acquired_at,
        }
    return _decode_pid_result(pid_code, pid_info, unmapped, resp.hex().upper(), resp, acquired_at)


class MonitorController:
    """Polls a set of ECU PIDs on an interval and renders/records the results.

    Holds all monitor state and the CAN-facing logic (session setup, polling,
    history bookkeeping, capture saving). The presentation layer — the Textual
    TUI or the non-interactive fallback — drives it via :meth:`poll_once` and
    :meth:`render`, so the two share identical behaviour.
    """

    def __init__(
        self,
        terminal,
        query_steps: list[dict],
        pids_data: dict,
        verbose: bool,
        interval: float = 5.0,
        keep_mode: str | None = None,
        keep_n: int | None = None,
        save: bool = False,
        show_rulers: bool = False,
        raw_client=None,
        include_static: bool = False,
    ):
        self.terminal = terminal
        self.raw_client = raw_client  # transport.RawUdsClient when using the raw backend
        self.raw = raw_client is not None
        self.query_steps = query_steps
        self.pids_data = pids_data
        self.verbose = verbose
        self.interval = interval
        self.keep_mode = keep_mode
        self.keep_n = keep_n
        self.save = save
        self.show_rulers = show_rulers
        self.include_static = include_static

        self.sm = SessionManager(terminal, verbose=verbose) if not self.raw else None
        self._ecu_index: dict | None = None
        self._batch_state = None  # multi.BatchState, created in setup()
        # Raw-backend multi-DID batching state (learned per-DID lengths + ECUs
        # that rejected batching this session).
        self._raw_lengths: dict[tuple[str, str], int] = {}
        self._raw_nobatch: set[str] = set()

        # Live state (read by the renderer).
        self.cycle = 0
        self.elapsed = 0.0
        self.last_cmds = 0  # ELM commands issued during the last poll cycle
        self.last_elm_time = 0.0  # seconds spent in ELM commands last cycle
        self.last_queries: list[tuple[str, list]] = []
        self.prev_hex: dict[tuple[str, str], str] = {}
        # Payloads as of the *previous* poll cycle, snapshotted before prev_hex is
        # overwritten each cycle. Rendering diffs against this so byte-level change
        # highlighting works in the single-frame view too (prev_hex already holds
        # the freshly-recorded current payload by render time).
        self.prev_snapshot: dict[tuple[str, str], str] = {}
        # Where on-demand ('s' key in the TUI) / end-of-run captures are written.
        # Set by mode_monitor; resolved lazily if left None.
        self.captures_dir: Path | None = None
        self.hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if keep_mode else None
        )
        self.save_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if save else None
        )
        self.disconnected = False
        # Write-ahead journal (durability): when --save is on, every polled
        # payload is appended here as it arrives and reconciled into a capture
        # file on exit. Set by mode_monitor. A dropped connection or crash leaves
        # the journal on disk for `canair captures --recover`.
        self.journal = None
        self._name_index: dict | None = None
        # Auto-suggest state: latest decoded {ECU.PARAM: value} + responded ECUs,
        # evaluated against the profile's states.yaml rules (lazy-loaded).
        self.decoded_values: dict[str, float] = {}
        self.responded: set[str] = set()
        self._state_rules: list | None = None
        # True once the user sets a non-empty state via the TUI save dialog, so
        # the end-of-run auto-suggest fallback doesn't clobber their choice.
        self._state_explicit = False

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Build the ECU index, run one-shot session setup, start keepalives."""
        from ..pids import build_ecu_index

        self._ecu_index = build_ecu_index(self.pids_data)

        if self.raw:
            # Raw backend: no ELM sessions/keepalive. Sessions (10 03) for ECUs
            # that need them are opened best-effort before polling.
            from .multi import build_query_plan

            for step in session_steps or []:
                if step["type"] == "session":
                    tgt = step["target"].upper()
                    if tgt in self._ecu_index:
                        with contextlib.suppress(Exception):
                            self.raw_client.read(tgt, bytes.fromhex("1003"), timeout=1.0)
            # Warm each ECU up: the first diagnostic request after idle is slow
            # (the ECU/gateway has to wake). Prime with one throwaway read per ECU
            # on a longer timeout so the first *monitored* cycle is already warm.
            for step in self.query_steps:
                info = self._ecu_index.get(step["ecu"].upper())
                if not info:
                    continue
                plan = build_query_plan(info, step.get("pids", []), quiet=True,
                                        include_static=self.include_static) or []
                if plan:
                    with contextlib.suppress(Exception):
                        self.raw_client.read(step["ecu"].upper(), bytes.fromhex(plan[0][0]), timeout=3.0)
            return

        from .multi import BatchState, _exec_session, _exec_skm_wake, build_query_plan

        self._batch_state = BatchState()
        for step in session_steps or []:
            stype = step["type"]
            if stype == "skm-wake":
                print(f"  SKM wakeup ({step['level']})...")
                await _exec_skm_wake(self.sm, step["level"], self.verbose)
            elif stype == "session":
                print(f"  Opening session on {step['target']}...")
                await _exec_session(
                    self.sm, step["target"], step.get("wake", False), self._ecu_index
                )
        self.sm.start_background_keepalive(interval=2.0)
        # Prime each ECU once (parity with the raw path): the first request after
        # idle is slow, so warm the path before the first *displayed* cycle. The
        # retry (retries=1) also rides out a cold NO-DATA on this throwaway read.
        for step in self.query_steps:
            info = self._ecu_index.get(step["ecu"].upper())
            if not info:
                continue
            plan = build_query_plan(info, step.get("pids", []), quiet=True,
                                    include_static=self.include_static) or []
            if plan:
                with contextlib.suppress(Exception):
                    await self.sm.terminal.set_header(info["tx_id"])
                    await self.sm.terminal.send_uds(plan[0][0], retries=1)

    def _record(self, new_queries: list[tuple[str, list]]) -> None:
        """Record freshly-polled payloads into prev_hex / display / save history."""
        # Refresh the decoded-value snapshot used for state auto-suggestion.
        from ..states import collect_values

        values, responded = collect_values(new_queries)
        self.decoded_values.update(values)
        self.responded |= responded
        # Snapshot the prior cycle's payloads before overwriting them, so the
        # renderer can diff current-vs-previous (prev_hex is about to become the
        # current values).
        self.prev_snapshot = dict(self.prev_hex)
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                raw = entry.get("raw_hex", "")
                if not raw:
                    continue
                key = (ecu_label, entry["pid"])
                self.prev_hex[key] = raw
                # Per-PID acquisition timestamp (moment the response arrived),
                # millisecond precision, so sequentially-polled PIDs keep skew.
                acq = entry.get("acquired_at")
                ts = (
                    datetime.fromtimestamp(acq).strftime("%H:%M:%S.%f")[:-3]
                    if acq
                    else datetime.now().strftime("%H:%M:%S.%f")[:-3]
                )
                if self.save_history is not None:  # --save
                    if self.journal is not None:
                        # Journaled: the write-ahead log is the source of truth on
                        # exit, so skip the redundant in-memory save_history growth.
                        with contextlib.suppress(Exception):
                            self.journal.append(self._ecu_ref(ecu_label), entry["pid"], raw, ts)
                    else:
                        self.save_history.setdefault(key, []).append((raw, ts))
                if self.hex_history is not None:  # --keep display history
                    if self.keep_mode in ("all", "last"):
                        self.hex_history.setdefault(key, []).append((raw, ts))
                        if (
                            self.keep_mode == "last"
                            and self.keep_n
                            and len(self.hex_history[key]) > self.keep_n
                        ):
                            self.hex_history[key] = self.hex_history[key][-self.keep_n :]
                    else:  # "unique": store only if not seen before
                        existing = [h for h, _ts in self.hex_history.get(key, [])]
                        if raw not in existing:
                            self.hex_history.setdefault(key, []).append((raw, ts))
        # One durable flush per cycle instead of an fsync per payload — keeps the
        # poll loop (and TUI) off N serial fsync syscalls when saving many PIDs.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.flush()

    async def poll_once(self) -> None:
        """Run every query step once, updating live state. Sets ``disconnected``."""
        self.cycle += 1
        t0 = time.monotonic()
        if self.raw:
            await self._poll_raw()
        else:
            await self._poll_elm()
        self.elapsed = time.monotonic() - t0
        self._record(self.last_queries)

    async def _poll_elm(self) -> None:
        from .multi import _exec_query

        cmds0 = self.terminal.cmd_count
        elm0 = self.terminal.cmd_time
        new_queries: list[tuple[str, list]] = []
        for step in self.query_steps:
            try:
                result = await _exec_query(
                    self.sm,
                    step["ecu"],
                    step.get("pids", []),
                    self._ecu_index,
                    self.pids_data,
                    self.verbose,
                    return_results=True,
                    quiet=True,
                    batch_state=self._batch_state,
                    include_static=self.include_static,
                )
            except ConnectionError:
                self.disconnected = True
                return
            if result is not None:
                new_queries.append(result)
        self.last_queries = new_queries
        self.last_cmds = self.terminal.cmd_count - cmds0
        self.last_elm_time = self.terminal.cmd_time - elm0

    def _build_raw_submissions(self):
        """Plan this cycle's raw requests, batching multi-DID ECUs.

        Returns ``(submissions, plan_by_ecu)``. Each submission is a dict with
        ``ecu``, ``req`` (bytes to send), ``members`` [(pid_code, pid_info,
        unmapped)], and ``lengths`` ([(did4, data_len)] for a batch, else None).
        Consecutive service-22 DIDs on a ``multi_did`` ECU whose lengths are
        already learned are combined (≤3, single-frame request); everything else
        is a single request (and 22-DID lengths are learned from single reads).
        """
        from .multi import _is_did22, build_query_plan

        submissions: list[dict] = []
        plan_by_ecu: list[tuple[str, int, list]] = []
        for step in self.query_steps:
            ecu = step["ecu"].upper()
            info = self._ecu_index.get(ecu)
            if info is None:
                continue
            plan = build_query_plan(info, step.get("pids", []), quiet=True,
                                    include_static=self.include_static) or []
            plan_by_ecu.append((ecu, info["tx_id"], plan))
            batchable = info.get("multi_did", False) and ecu not in self._raw_nobatch
            i, n = 0, len(plan)
            while i < n:
                code = plan[i][0]
                if batchable and _is_did22(code) and (ecu, code[2:]) in self._raw_lengths:
                    group = []
                    while (
                        i < n
                        and len(group) < 3
                        and _is_did22(plan[i][0])
                        and (ecu, plan[i][0][2:]) in self._raw_lengths
                    ):
                        group.append(plan[i])
                        i += 1
                    if len(group) > 1:
                        dids = [g[0][2:] for g in group]
                        submissions.append(
                            {
                                "ecu": ecu,
                                "req": bytes.fromhex("22" + "".join(dids)),
                                "members": group,
                                "lengths": [(d, self._raw_lengths[(ecu, d)]) for d in dids],
                            }
                        )
                        continue
                    g = group[0]
                    submissions.append(
                        {"ecu": ecu, "req": bytes.fromhex(g[0]), "members": [g], "lengths": None}
                    )
                    continue
                submissions.append(
                    {"ecu": ecu, "req": bytes.fromhex(code), "members": [plan[i]], "lengths": None}
                )
                i += 1
        return submissions, plan_by_ecu

    async def _poll_raw(self) -> None:
        """Pipelined UDS read over raw CAN (blocking client run in a thread).

        Multi-DID ECUs are batched (one ISO-TP request per group); results are
        split back per-DID. Per-ECU 22-DID lengths are learned from single reads,
        and an ECU that rejects batching (NRC 0x13/0x31) or returns an
        unsplittable response is dropped to single reads for the session.
        """
        import time as _t

        from .multi import _did_data_len, _is_did22, split_multi_did

        submissions, plan_by_ecu = self._build_raw_submissions()
        requests = [(s["ecu"], s["req"]) for s in submissions]

        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(None, self.raw_client.poll, requests)
        except Exception:
            self.disconnected = True
            return

        acquired = _t.time()
        by_pid: dict[tuple[str, str], dict] = {}
        for s in submissions:
            ecu = s["ecu"]
            val = results.get((ecu, s["req"]))
            resp = bytes(val) if isinstance(val, (bytes, bytearray)) else None

            if s["lengths"] is not None:  # batched request
                split = None
                if resp and resp[0] != 0x7F:
                    split = split_multi_did(resp.hex().upper(), s["lengths"])
                elif resp and resp[0] == 0x7F and (resp[2] if len(resp) >= 3 else 0) in (0x13, 0x31):
                    self._raw_nobatch.add(ecu)  # ECU can't batch — fall back next cycle
                if split is None:
                    if resp and resp[0] != 0x7F:
                        self._raw_nobatch.add(ecu)  # positive but unsplittable
                    for code, pi, un in s["members"]:
                        by_pid[(ecu, code)] = _raw_pid_result(
                            code, pi, un, val if resp is None else resp, acquired
                        )
                else:
                    for code, pi, un in s["members"]:
                        sub = bytes.fromhex(split[code[2:]])
                        by_pid[(ecu, code)] = _raw_pid_result(code, pi, un, sub, acquired)
                continue

            code, pi, un = s["members"][0]
            by_pid[(ecu, code)] = _raw_pid_result(code, pi, un, val, acquired)
            if _is_did22(code) and resp and resp[0] != 0x7F:  # learn length for batching
                dlen = _did_data_len(resp.hex().upper(), code[2:])
                if dlen is not None:
                    self._raw_lengths[(ecu, code[2:])] = dlen

        new_queries: list[tuple[str, list]] = []
        for ecu, tx_id, plan in plan_by_ecu:
            pid_results = [by_pid[(ecu, c)] for c, _pi, _un in plan if (ecu, c) in by_pid]
            new_queries.append((f"{ecu} (0x{tx_id:03X})", pid_results))

        self.last_queries = new_queries
        self.last_cmds = len(requests)
        self.last_elm_time = 0.0

    def render(self) -> Text:
        """The current view as a Rich Text (rendered by the TUI / printed on exit)."""
        return _render_results(
            self.last_queries,
            self.verbose,
            self.cycle,
            self.elapsed,
            self.interval,
            self.prev_snapshot,
            self.hex_history,
            show_rulers=self.show_rulers,
            footer=False,
        )

    def has_captures(self) -> bool:
        """True when there's at least one payload available to save."""
        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        return bool(history) or bool(self.prev_hex)

    def _ecu_ref(self, ecu_label: str) -> str:
        """Resolve a monitor ECU label (e.g. "BMS") to its CAN response address.

        Falls back to the label's leading token when it isn't a known ECU name.
        Caches the name→TX index for repeated calls during polling.
        """
        from ..ecus import build_name_tx_index, rx_from_name

        if self._name_index is None:
            self._name_index = build_name_tx_index()
        ecu_short = re.match(r"(\w+)", ecu_label).group(1)
        return rx_from_name(ecu_short, self._name_index) or ecu_short

    def query_label(self) -> str:
        """Short summary of the polled selectors, e.g. ``BCM VCU:2101``.

        Reconstructs the query mini-language from the active steps (ECU name,
        with its PID list attached by a colon when filtered). Used to pre-fill
        the on-demand save dialog's label.
        """
        parts: list[str] = []
        for step in self.query_steps:
            ecu = step["ecu"].upper()
            pids = step.get("pids") or []
            parts.append(f"{ecu}:{','.join(pids)}" if pids else ecu)
        return " ".join(parts)

    def suggested_state(self) -> str | None:
        """Auto-suggest the vehicle state from the latest decoded values.

        Evaluates the active profile's states.yaml rules against the accumulated
        ``decoded_values``/``responded`` snapshot. Returns None when no rule
        matches or the profile declares no states.
        """
        from ..states import StatePredicateError, load_states, suggest_state

        if self._state_rules is None:
            try:
                self._state_rules = load_states()
            except StatePredicateError:
                self._state_rules = []
        if not self._state_rules:
            return None
        return suggest_state(self._state_rules, self.decoded_values, self.responded)

    def save_now(self, label: str, state: str | None = None, notes: str | None = None) -> str:
        """Save the payloads captured so far (on-demand save from the TUI).

        Uses the richest history available — the full ``--save`` history if
        enabled, else the display (``--keep``) history, else just the latest
        per-PID snapshot — merged with the current values. Returns a one-line
        summary for display. Never writes to stdout (the TUI owns the screen).
        """
        import contextlib
        import io

        # Journal active (--save): payloads are already durably journaled and
        # reconciled on exit. The on-demand save just updates the metadata that
        # the reconciled session will carry (label/state/notes), applied live.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.update_meta(label, state, notes)
            if state:
                self._state_explicit = True
            return f"Metadata set (label={label!r}); session auto-saves on exit."

        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        merged = _merge_history(history, self.prev_hex)
        if not merged:
            return "No payloads captured yet — nothing to save."

        captures_dir = self.captures_dir
        if captures_dir is None:
            from ..profile import active

            captures_dir = active().captures_dir

        n_pids = len(merged)
        n_payloads = sum(len(v) for v in merged.values())
        with contextlib.redirect_stdout(io.StringIO()):
            path = _write_merged(merged, label, state or "", notes or "", captures_dir)
        return f"Saved {n_payloads} payload(s) across {n_pids} PID(s) → {path.name}"

    async def close(self) -> None:
        """Stop keepalives and close all open sessions / the raw client (best-effort)."""
        if self.raw:
            print("  Closing raw CAN client...")
            with contextlib.suppress(Exception):
                self.raw_client.close()
            return
        self.sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(self.sm.close_all(), timeout=3.0)
        except (TimeoutError, Exception):
            pass


async def _monitor_noninteractive(controller: MonitorController) -> None:
    """No TTY: poll silently until SIGINT/disconnect (piped/scripted runs)."""
    stop_flag = {"v": False}

    def _handle_sigint(_sig, _frame):
        stop_flag["v"] = True

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        while not stop_flag["v"] and not controller.disconnected:
            t0 = time.monotonic()
            await controller.poll_once()
            if controller.disconnected:
                return
            remaining = controller.interval - (time.monotonic() - t0)
            while remaining > 0 and not stop_flag["v"] and not controller.disconnected:
                await asyncio.sleep(min(remaining, 0.1))
                remaining = controller.interval - (time.monotonic() - t0)
    finally:
        signal.signal(signal.SIGINT, old_handler)


async def mode_monitor(
    terminal,
    query_steps: list[dict],
    pids_data: dict,
    verbose: bool,
    interval: float = 5.0,
    session_steps: list[dict] | None = None,
    keep_mode: str | None = None,
    keep_n: int | None = None,
    save: bool = False,
    show_rulers: bool = False,
    label: str | None = None,
    state: str | None = None,
    notes: str | None = None,
    raw_client=None,
    include_static: bool = False,
):
    """Live-refresh ECU parameter monitor.

    On a TTY this launches the Textual monitor app (scrollable, in-place value
    updates, mouse + keyboard). Otherwise it polls silently until Ctrl+C and
    prints the final values. Sessions are opened once (from session_steps) and
    kept alive with background keepalives.

    Args:
        terminal:       Connected WiCANTerminal.
        query_steps:    list of {'type': 'query', 'ecu': ..., 'pids': [...]} dicts.
        pids_data:      Loaded PID definitions.
        verbose:        Show expressions.
        interval:       Seconds between poll cycles (default: 5.0).
        session_steps:  Optional list of session/skm-wake steps to run once before
                        the first poll cycle.
        keep_mode:      None = no history, "unique" = deduped unique payloads,
                        "all" = every payload from every cycle,
                        "last" = sliding window of last N payloads (see keep_n).
        keep_n:         For keep_mode="last": number of recent payloads to display.
        save:           Journal every polled payload as it arrives and reconcile
                        into captures/ on stop (crash/disconnect leaves a
                        recoverable journal). Metadata comes from label/state/
                        notes (auto-suggested when omitted) and the TUI 's' key.
        show_rulers:    Show byte-index rulers (idx/wican) once per PID.

    TUI keys: ↑/↓ or j/k scroll, PgUp/PgDn page, g/Home top, G/End bottom,
    f toggle follow-tail, space pause/resume polling, s save captures, q or
    Ctrl+C stop.
    """
    from ..profile import active

    captures_dir = active().captures_dir
    controller = MonitorController(
        terminal,
        query_steps,
        pids_data,
        verbose,
        interval=interval,
        keep_mode=keep_mode,
        keep_n=keep_n,
        save=save,
        show_rulers=show_rulers,
        raw_client=raw_client,
        include_static=include_static,
    )
    controller.captures_dir = captures_dir

    # --save: open the write-ahead journal up front so every polled payload is
    # durably recorded as it arrives. On a clean stop we reconcile it into a
    # capture file; a disconnect/crash leaves it on disk for `--recover`.
    if save:
        from ..capture_journal import CaptureJournal

        journal_label = label or controller.query_label() or "Monitor session"
        keep = "unique" if keep_mode == "unique" else None
        controller.journal = CaptureJournal.open(
            captures_dir,
            label=journal_label,
            state=state,
            notes=notes,
            source="monitor",
            keep_mode=keep,
        )
        print(
            f"  --save: journaling to {controller.journal.path.name} "
            f"(label: {journal_label!r}); auto-saves on stop. "
            "Press 's' to edit label/state/notes."
        )

    try:
        await controller.setup(session_steps)

        if sys.stdout.isatty():
            from ._monitor_tui import run_monitor_app

            await run_monitor_app(controller)
        else:
            await _monitor_noninteractive(controller)

        if controller.disconnected:
            link = "raw CAN bus" if controller.raw else "WebSocket"
            _console.print(f"\n  [bold red]✖ {link} disconnected[/bold red]")
            _console.print(f"  [red]Stopped after {controller.cycle} cycles.[/red]\n")
            raise ConnectionError(f"{link} disconnected")

        # Print the final values so a stopped session leaves them in scrollback.
        _console.print(controller.render())
        print("  Monitoring stopped.")

    finally:
        # Reconcile the journal even on disconnect/exception (this is the fix for
        # the old bug where a dropped connection lost the whole --save session).
        if controller.journal is not None:
            with contextlib.suppress(Exception):
                # If no state was set explicitly (flag or the TUI dialog), fall
                # back to the auto-suggested state from decoded PID values.
                if not state and not controller._state_explicit:
                    suggested = controller.suggested_state()
                    if suggested:
                        controller.journal.update_meta(state=suggested)
                written = controller.journal.reconcile()
                if written is not None:
                    _console.print(f"  → Saved journaled captures to {written.name}")
        await controller.close()
