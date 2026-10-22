# Explorer data view: plot captured signals and find what is hiding in them

Status: **PHASES 1 AND 2 BUILT** (2026-10-22); phase 3 open. Builds on
`plans/2026-10-21-profile-explorer.md`, whose non-goals ruled out captured values; this plan
reverses that for decoded numeric signals only, with the privacy decision below settled.

The explorer shows what a profile *defines*. The Ioniq profile also holds 119,770 timestamped
readings across 121 drive, charge and idle sessions, and that is where the interesting things
are: how power sags as the pack warms, which ECU's temperature follows the coolant pump, a byte
that only moves when a door opens. A reader should be able to open a session, put a handful of
signals from different ECUs on one timeline, and be pointed at the parts that deserve a
second look, without installing canair or knowing the CLI.

## What a visitor is trying to do

Three questions, in the order people usually ask them. Every feature below serves one of them.

1. **"What happened in this drive or charge?"** Needs a session picker and a plot that is
   pleasant to read with 3 to 8 signals from different ECUs on it.
2. **"What moves with this?"** Pick a signal, get a ranked list of the other signals that
   follow it, at what delay, and with what caveats.
3. **"What changed right here?"** Brush a moment (charging starts, the pedal goes down, a relay
   clicks) and get the signals that reacted, including ones nobody thought to put on the plot.

The CLI already answers these (`align`, `correlate`, `hunt`, `investigate`). The page is the
shop window for the same method: it shows the equivalent `canair` command for every view, so a
reader who likes what they see knows where to go next.

## Data: decode at build time, ship numbers

### Why not ship payloads and decode in the browser

The expression language (`Bnn`, `[B18:B19]`, `S15`, shifts, bit accessors, typed decoding) has
one authoritative evaluator in Python. A JavaScript port would be a second implementation to
keep identical, with bugs that look like findings. Shipping decoded numeric series keeps one
evaluator, makes the exporter testable in pytest, and means a refined expression re-decodes
history on the next docs build, same as `canair decode`.

The cost is that the browser cannot try a new expression. That stays a CLI job
(`canair decode --try`), and the page prints the command.

### Measured size

Decoding every timed capture of `ioniq-2017` with `decode_param_rows`:

| | |
|---|---|
| Timed captures with a payload | 119,770 in 121 sessions (42 sessions have 500+) |
| Numeric values decoded | 1,667,558 in 12,385 (session, signal) series |
| Decode time | 4.7 s |
| Values as JSON | about 10 MB raw, **0.5 MB gzipped** (values repeat heavily) |
| Timestamps | about 120k, shared by all signals of a PID |

GitHub Pages serves gzip, so the whole profile is under about 1 MB over the wire. It is still
split per session, because nobody opens all 121 at once.

### Files

Written by `scripts/mkdocs_hooks/explorer.py` next to the existing definitions export:

- `data/<profile>/sessions.json`: eager, small. Per session: id, date, label, states,
  start/duration, transport, `keep_mode`, quality, a note, and per `ECU:PID` the sample count and
  which signals actually vary (a constant signal is listed but flagged, so pickers can hide it).
- `data/<profile>/s/<id>.json`: lazy, on opening a session:

```json
{
  "t0": "2026-08-08T14:02:11.480",
  "streams": [
    {"ecu": "BMS", "pid": "2101", "t": [0, 998, 1003],
     "sig": {"BATTERY_POWER": [-3.1, -3.2, -3.2], "SOC_BMS": [61.5, 61.5, 61.5]}}
  ],
  "spans": [{"state": "CHARGING", "t0": 0, "t1": 5400000}]
}
```

`t` is milliseconds since the session start, delta-encoded. Values are rounded to 6
significant digits, with `null` where a decode failed. One time axis per `ECU:PID`, because
the signals of one response share a timestamp.

### What never ships

Only decoded values of signals the export allow-list already covers. Never a payload,
`scan_results`, identity responses, notes on individual captures, or untimed captures (scans,
probes, identity reads). `ignored` PIDs are skipped. Typed signals (enum, bitmask, ASCII) are
out of scope for the first slice; only numeric decodes ship.

### Honest plotting of this data

- **Run-length sessions** (`keep_mode`) store a row only when a value changed. Lines are drawn
  as steps (hold until the next sample), and the session header says "stored on change only".
  A diagonal line there would invent data.
- **Gaps** longer than ten times the stream's median interval break the line.
- **ECU skew.** One connection polls ECUs in turn, so two ECUs' samples differ by 0.3 to 3 s.
  The tooltip shows each signal's own last sample time. Analysis uses a 1 s grid with a bounded
  forward fill, like `--join-tol`/`--fill`/`--max-hold`.
- **Unverified signals** are drawn dashed with an "unverified" mark in the legend. A guess is
  not a measurement, and the page should not dress it up as one.

## The page

### Where it lives

Same Explorer page, same hash router, one more mode. A **Definitions | Data** switch sits at the
top, and a **signal tray** is docked above both, so you can browse an ECU's definitions, press
**＋ plot** on a signal or on a whole PID, and switch to Data without losing your picks. Hash
state: `#ioniq-2017/plot/<session>?s=BMS:2101:BATTERY_POWER,OBC:2101:AC_V&x=1200-3400&m=stack`,
so any view, zoom and layout is a link.

### Choosing a session

A list, newest first, each row a one-line story: date, label, state chips (CHARGING, DRIVING),
duration, and how many signals vary. Filters by state and text. The tray matters here: signals
only exist in sessions where their PID was polled, so with signals in the tray each row shows
"3 of 4 present" and sessions that have none are dimmed. Without that, adding an HVAC and a BMS
signal and then opening a session with only BMS looks like a bug.

### The plot

Stacked by default. Signals with the same unit share a lane, different units get their own, all
on one time axis with a synchronized crosshair. One keypress (`o`) switches to **overlay**:
every series min-max normalized onto a single axis, which is what you want when the question
is "do these have the same shape?" and the units differ. Tooltips always show true values.

- Hover: crosshair across all lanes and a readout table, one row per signal, true value and
  unit.
- Click pins a cursor; a second click shows the delta (time, value, rate), which answers "how
  fast does it charge" without exporting to a spreadsheet.
- Drag selects and zooms the time axis; double-click resets. A session-wide minimap below
  doubles as the brush.
- Vehicle state bands from the session's `state_spans` tint the background, so "this is where
  CHARGING began" is visible without reading a legend.
- Each tray chip shows its colour, the signal name, ECU and PID, an eye toggle and a remove button.
- A footer shows the equivalent `canair align ... --date ... --label ...` command, copyable.

### Discovery tools

These are the point of the page. Each is a button or a gesture, never a mode to learn first.

**Session overview (the heat-strip).** Before plotting anything, one picture of the whole
session: every varying signal as a thin row, time across, colour is the value normalized to that
signal's own range. Rows are ordered by hierarchical clustering on correlation, so signals that
behave alike sit next to each other and block structure appears by itself (the charge block, the
cooling block, the drive block). Constant rows are collapsed into a count. Hover names a row,
click adds it to the tray, dragging on the strip sets the plot's time range. This answers "what
is even in here?" faster than any list.

**Related signals.** Select a series in the tray and get a ranked list of the other signals in
the session that follow it: correlation, best lag within ±30 s (so you can see that coolant
temperature trails power by 40 s, not just that they match), sample count, and a sparkline.
Computed on the 1 s grid in a Web Worker. Defaults to correlating first differences, because two
things that both merely drift upward over a session correlate perfectly and mean nothing; a
toggle shows the raw level. Pairs at 0.999 or above are labelled "mirror" rather than celebrated
as a discovery. The panel says plainly that correlation is a lead, not an explanation.

**What changed here?** Brush any window. Every signal in the session is scored by how much its
mean or variance inside the window differs from outside it, in units of its own session-wide
spread, and the top results are listed with before/after values. This is how you find the
signal that reacted to something you were not looking at. Bit-like signals that flip only inside
the window rank first and are flagged.

**Scatter.** With two signals in the tray, an X-Y view coloured by time: charge curves
(voltage against SOC), efficiency (power against speed), hysteresis loops. A relationship that
is invisible on a time axis is often a clean curve here.

**Compare sessions** (later). The same signal across sessions of one kind, aligned to the start
or to an anchor event ("first time SOC crosses 50%"), to see how charge curves differ with
temperature or charger.

**Unmapped bytes** (later). The biggest hidden gems are not decoded signals but response bytes
no signal reads (`canair coverage` lists them). The exporter emits a series for each *varying*
unmapped byte, tagged "unmapped", appearing in the heat-strip and related-signals results with
a "decode this" link to the CLI recipe (`canair investigate ECU PID`) and the YAML line. This
turns browsing into contribution: the reader sees a mystery byte that tracks vehicle speed and
knows exactly what to do about it.

## Implementation

### Charting

Vendor **uPlot** (MIT, about 50 KB minified) into `docs/assets/explorer/vendor/` with its
licence. It is built for dense time series on canvas, has stepped paths, multiple scales and
synchronized cursors, and writing those axes, zoom and cursor behaviours by hand is several
hundred lines of fiddly code. The heat-strip and scatter are small custom canvases. No bundler,
consistent with the existing "data file plus vanilla JS" approach; a vendored file is the only
new dependency.

### Code layout

The current `explorer.js` is about 330 lines; this adds well over that, so it is split before
it grows:

| Piece | Job |
|-------|-----|
| `canlib/explorer_series.py` | Sessions in, columnar dicts out. Pure and unit-tested. |
| `scripts/mkdocs_hooks/explorer.py` | Writes the session index and per-session files. |
| `assets/explorer/plot.js` | Tray, session picker, lanes, crosshair, brush, hash state. |
| `assets/explorer/analysis.js` | Pure kernels: resample, correlate with lag, change score, clustering. No DOM. |
| `assets/explorer/worker.js` | Runs `analysis.js` off the main thread. |
| `assets/explorer/heatstrip.js`, `scatter.js` | Canvas views. |

`explorer.js` keeps definitions browsing and exposes a small `window.canairExplorer` surface
(the tray, the router) that `plot.js` uses.

### Build and deploy

The docs workflow path filter gains `profiles/*/captures/**`, so a capture commit redeploys the
site. Build time grows by about 5 s for the Ioniq. Sessions with fewer than 10 timed samples are
listed but not plottable. `captures/` is plain JSON, so nothing needs Git LFS.

## Testing

- pytest, exporter: deterministic output; values equal `decode_param_rows` on sampled
  captures; `t` strictly non-decreasing; every exported signal name exists in the profile's
  definitions; no payload, `scan_results`, identity or untimed capture reaches the JSON; spans
  lie inside the session.
- pytest, budget: per-profile gzipped total under 3 MB and each session file under 400 KB
  gzipped, so a future change cannot quietly bloat the site.
- `node --test` for `analysis.js`: the kernels are pure and carry the logic that can mislead
  (lag sign, differencing, mirror detection, constant signals), so they get tests with no
  dependency beyond Node. This adds one CI step; the earlier plan rejected a JS runner for
  about 300 lines of rendering, and this is different code.
- A headless-Chrome smoke script under `scripts/` (not CI): load, add to tray from definitions,
  open a session, zoom, round-trip the hash, instant navigation. It is how the first explorer
  was checked.

## Phases

1. **Plot.** Exporter, session picker, tray with ＋ buttons, stacked and overlay lanes, crosshair,
   pinned deltas, zoom and minimap, state bands, deep links, CLI footer. Already answers
   question 1.
2. **Discovery.** Heat-strip overview, related signals, what-changed brush. Answers questions 2
   and 3.
3. **Depth.** Scatter, session comparison, unmapped-byte series, enum signals.

Each phase deploys on its own.

## Build notes (2026-10-22)

Phases 1 and 2 exist in `canlib/explorer_series.py`, `scripts/mkdocs_hooks/explorer.py`,
`docs/assets/explorer/{plot,discover,analysis,worker}.js`, the vendored uPlot 1.6.32 and the
tests named above. Measured on the Ioniq: 98 plottable sessions, about 0.85 MB gzipped in total,
the largest session 73 KB gzipped, 5 s to export. Checked in headless Chrome: deep links,
zoom, pins, overlay, session switching, heat-strip brush and click, both discovery panels, and
dark mode.

Differences from the proposal:

- Sessions with fewer than 10 numeric samples are left out of the index rather than listed as
  not plottable.
- The session overview doubles as the minimap and the brush; there is no separate minimap strip.
- Every series is drawn as a step (hold until the next sample), not only run-length sessions.
  One rule is easier to explain and never invents a value between two samples.
- No per-series eye toggle yet; remove a signal from the tray instead.
- "What changed here?" works on the current zoom range, so zooming is the single gesture.

Not done (phase 3): scatter view, comparing sessions, series for unmapped bytes, enum signals.

Known limits worth knowing when reading results: a single large event, such as charging ending,
makes dozens of unrelated flags correlate at once in Related signals; the 1 s analysis grid
cannot resolve anything faster than a second; and the Leaf profile has only three short sessions.

## Decisions

- **Privacy:** publish all timed decoded telemetry as is, with session labels and notes (decided
  2026-10-22). The captures are already public in the repository.

## Open questions

- Whether the Leaf profile's 32 KB of captures is worth a picker at all, or the Data mode is
  hidden for profiles with fewer than a few sessions.
- Cross-ECU skew handling for the lag search: nearest-sample join, or the bounded forward fill
  the CLI uses. The fill is more faithful but changes what a "lag of 1 s" means near the grid
  step.
- Whether `canair profile explore` (phase 4 of the first plan) should also bundle captures,
  given that an unpublished profile has no privacy question to answer.
