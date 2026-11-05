"""The help text of ``canair captures uds``: the views, the QUERY language and the scoping flags."""

EPILOG = """Query captured UDS payloads across all capture files.

QUERY selects the ECU(s) and PID(s) to show (see the mini-language below).
By default the matching captures are listed (most recent --limit, default 50);
add --diff or --step to change how they are rendered. --summary and --sessions
are aggregate modes that take no QUERY.

  QUERY                 List matching captures (default view; latest --limit)
  QUERY --diff          Monitor-style view (decoded signals + colored byte-diff),
                        one block per ECU+PID (unique payloads only; --all = all)
  QUERY --step          Interactive: step through captures with arrow keys,
                        decoded signals + byte-diff vs the previous capture of the
                        same PID; e adds/edits a note, d deletes a capture.
                        A QUERY selecting SEVERAL PIDs stacks them underneath
                        each other in one time-joined frame (--join-tol), so
                        they can be cross-compared; PIDs, tolerance and view are
                        all editable inside the TUI (a/t/V), s jumps between
                        sessions and noted captures, ? for help
  QUERY --latest        Most recent payload per PID for the QUERY selection
  --latest              Most recent payload per PID (all ECUs; no QUERY)
  QUERY --delete        Delete the captures matching QUERY (and scope filters);
                        --dry-run previews, confirms before deleting unless --yes
  --set-state STATES    Set vehicle_states on the scope-selected sessions
  --set-notes TEXT      Replace the notes on the scope-selected sessions ('' clears)
  --set-transport NAME  Correct the recorded transport of the scope-selected sessions
  --set-variant ID      Tag the scope-selected sessions with the car's variant
  --summary             Overview: captures per ECU, per date, total payloads
  --sessions            Session table of contents: date/time-span/state/label/
                        notes/ECUs per session (no payloads); --json for machine
                        output. Honors the scope filters.

Step views (--view; default auto — stacked for up to 6 PIDs, else interleaved):
  stacked               One block per PID per frame: signals + byte-diff hex
  signals               Params only (no hex) — fits more PIDs on one screen
  changed               Only signals whose decoded value moved, per block
  interleaved           One capture per frame, chronologically across the PIDs

Output size (default list view):
  --limit N             Show only the most recent N captures (default 50; 0 =
                        no cap). A loud footer reports any hidden history — use
                        --limit 0 or a tighter scope (--since/--last-session) to
                        see the rest.

QUERY mini-language (see src/canair/query/__init__.py):
  ECU PID               one PID (bare ECU + PID)       e.g. BMS 2102
  ECU                   all PIDs for an ECU            e.g. VCU
  ECU:PID               one PID                        e.g. VCU:2101
  ECU:PID,PID           several PIDs                   e.g. VCU:2101,22BC03
  "ECU:PID ECU:PID"     cross-ECU (quote the space)    e.g. "VCU:2101 BMS:2101"
  ECU:22                prefix PID match (22xxxx)      e.g. BCM:22
  ECU:BC03              suffix PID match (->22BC03)    e.g. IGPM:BC03

Date scoping (inclusive, YYYY-MM-DD; combines with any mode):
  --since DATE          captures on or after DATE
  --until DATE          captures on or before DATE
  --date DATE           captures on DATE only (--since DATE --until DATE)

State/label scoping (state by token, label by substring; combines with any mode):
  --state SUBSTR        only sessions whose vehicle_states contain SUBSTR (e.g. driving)
  --label SUBSTR        only sessions/captures whose label contains SUBSTR

Examples (a bare `canair captures …` is shorthand for `canair captures uds …`):
  canair captures uds BMS 2102              # ECU + PID (most useful)
  canair captures uds BMS                   # All BMS captures
  canair captures uds "BMS:2102,2103"       # Several PIDs
  canair captures uds IGPM 22BC03 --diff    # Byte-diff for one ECU+PID
  canair captures uds "BMS:2102,2103" --diff  # Byte-diff, one block per PID
  canair captures uds BMS 2102 --step       # Step through one PID
  canair captures uds "BMS:2102,2103" --step  # Stack two PIDs, time-joined
  canair captures uds "HVAC:220100,2201A0,2201A2" --step  # Cross-compare three PIDs
  canair captures uds "VCU:2101 BMS:2101" --step  # Cross-ECU compare
  canair captures uds "VCU:2101 BMS:2101" --step --join-tol 1.0  # Tighter join
  canair captures uds "HVAC:220100,2201A0" --step --view signals # Params only
  canair captures uds BMS --step --view interleaved  # Browse every BMS PID
  canair captures uds "BMS:2102,2103" --step --json --limit 5  # Frames as data
  canair captures uds --diff VCU:2101 --all  # One PID, every payload
  canair captures uds --summary             # Overview stats
  canair captures uds --sessions            # Session table of contents
  canair captures uds --sessions --state driving # Index of every drive
  canair captures uds --sessions --json      # Machine-readable TOC
  canair captures uds BMS --latest          # Latest payload per BMS PID
  canair captures uds --latest              # Latest payload per PID (all ECUs)
  canair captures uds OBC 2101 --delete --dry-run  # Preview a delete
  canair captures uds OBC 2101 --delete --yes      # Delete (non-interactive)
  canair captures uds --set-notes 'Car off.' --label sweep --dry-run  # Fix a note
  canair captures uds BMS 2102 --limit 200  # Widen the default 50-row cap
  canair captures uds BMS 2102 --limit 0    # Every matching capture (no cap)
  canair captures uds --summary --since 2026-04-19        # Stats since a date
  canair captures uds BMS 2101 --diff --date 2026-04-19   # One day only
  canair captures uds VCU --since 2026-04-14 --until 2026-04-21  # Range
  canair captures can                       # List imported raw broadcast-CAN frame logs
"""
