# canair — Functionality Review

Status: analysis / discussion doc (not an implementation plan). Captures a review of
canair's functionality: what's missing, what could be added, what's best delegated to
existing open-source tooling, and what's already covered / better removed.

## What canair genuinely is (its niche)

canair is not "another CAN tool." Its defensible niche is a **documentation-first,
iterative UDS/KWP2000 reverse-engineering loop over a WiFi/VPN OBD dongle**, with a
tight feedback cycle:

`discover → capture (--save) → decode/coverage/research → define (pids) → verify → generate WiCAN profile`

That capture-corpus + historical-replay + coverage-audit + research-backlog workflow
(`captures`, `decode --plot`, `coverage`, `research`, `pids`) is the part with little
direct open-source equivalent. Almost everything else in the tool is either plumbing or
overlaps with mature projects. That framing is the lens for every recommendation below.

## 1. What's missing (gaps worth filling — on-strategy)

These extend the RE-loop that is canair's actual differentiator:

- **Broadcast-frame signal decoding.** The single biggest gap. `sniff` shows only raw
  bytes per CAN ID (`sniff.py` `SniffStats`); the whole decode/expression engine
  (`decoding.py`) applies *only* to UDS request/response payloads. Yet the TODO
  drive-mode/regen investigation concludes the signal is likely **only broadcast on
  internal CAN**, never exposed via OBD reads. Right now canair cannot decode the exact
  data the open research questions need. Highest-value addition. **Decision: pursue this
  in canair (extend the loop), not via external tools.**
- **Capture ingestion from the sniffer.** `sniff` can *write* `.asc/.blf/.csv` (via
  `can.Logger`) but nothing reads them back into the analysis tools. The RE loop
  dead-ends at passive data.
- **Signal-inference assistance.** `decode --plot` is manual byte-sweeping. The stated
  idea "use known PIDs to deduce vehicle state to help understand new PIDs" (AGENTS.md)
  and the `--corr` feature hint at auto-correlating unknown bytes against known signals
  / labeling captures by inferred state. Natural, on-niche enhancement.
- **A second real profile.** The framework is multi-vehicle but ships one car. Nothing
  validates the abstraction until a second profile exists (bootstrap plan exists;
  no proof yet).
- **Memory read scanning (0x23 ReadMemoryByAddress)** as a scan kind — there are
  iocontrol/routine/DID scanners but not memory, which is often where unmapped
  calibration lives.

## 2. What could be added (nice-to-have, lower priority)

- **Web UI for captures** (already in TODO, referencing cabana) — but see §3; lean
  toward *export to* existing viewers rather than building one.
- **DBC export of decoded UDS PIDs** — so results are consumable by the wider ecosystem
  (cantools, SavvyCAN, Wireshark CAN dissector).
- **Security-access dictionary expansion** — the homegrown `SECURITY_ALGORITHMS` bank in
  `multi.py` is solid and Hyundai-specific; growing it is cheap, on-niche, and hard to
  delegate elsewhere.

## 3. Best left to other open-source tooling (don't build)

canair already correctly delegates the CAN layer (`python-can`) and ISO-TP
(`can-isotp`). Extend that instinct:

- **Deep broadcast-CAN RE GUI / DBC editing / replay** → **SavvyCAN** and comma.ai
  **cabana**. Don't build the web UI from scratch. Instead make canair *interoperate*:
  import/export SavvyCAN CSV / gvret and DBC. The TODO already gestures at this — resolve
  it toward **interop, not a new GUI**.
- **DBC-based signal decode/plot/monitor** → **cantools** (`cantools decode/plot/monitor`).
  If broadcast decoding lands (§1), consider expressing broadcast signals as DBC and
  reusing cantools rather than reimplementing signal math for the broadcast case.
- **Generic UDS pentest / fuzzing / bulk service+DID+session enumeration** → **gallia**
  (Fraunhofer) and **caringcaribou**. canair's scanners are safe and RE-focused; resist
  growing them into a fuzzing framework.
- **ISO-TP / low-level UDS state machine** → **udsoncan** exists, but do *not* switch.
  canair's homegrown UDS layer (`elm327.py`, `uds_raw.py`) is deliberately intertwined
  with dongle quirks (ELM327 text parsing, ResponsePending loops, dual UDS/KWP2000 via
  `id_protocol`). udsoncan models neither KWP2000 nor the ELM path; migrating adds risk
  for little gain. Keep homegrown, but consider extracting it as an internal module
  boundary.

## 4. Already covered / candidates for removal or delegation

- **WiCAN *device* management overlaps the separate `wican-cli` package.** The README
  (line 122) points users to `pip install wican-cli` for "config, sleep/power, protocol
  switching, status, reboots" — yet canair *still* implements exactly those against the
  device HTTP API (`wican_api.py`, `wican_mode.py`, `commands/wican.py`
  `--download/--upload/--diff/--set-protocol/--reboot`, plus `terminal.reboot_wican`).
  Duplicated surface area across two of the same author's packages. This is the clearest
  "already covered, better removed/consolidated" finding. See tradeoffs below.
- **`tester-present` as a top-level command** is arguably redundant — it already exists
  as a query-pipeline step and a session-keepalive.
- **`raw` / `repl`** overlap the query pipeline (which has `raw` and `repl` step verbs).
  Not harmful, but they're convenience aliases, not distinct capability.
- **Large-file decomposition** (per AGENTS.md >500-line rule): `multi.py` (1451),
  `decode.py` (1227), `pids_edit.py` (1206), `captures.py` (1133), `iocontrol.py`
  (1108), `validate.py` (981) all violate it. Not "remove," but the security-algorithm
  bank and session logic inside `multi.py` are separable concerns worth splitting.

## The canair ↔ wican-cli boundary — tradeoffs

Undecided. Three viable states; the current one (both implement it, README advertises
both) is the only clearly-wrong one because it's silent duplication.

**Option A — canair depends on wican-cli**
- Pros: single source of truth for device HTTP ops; canair sheds `wican_api.py`, most of
  `wican_mode.py`, and the reboot paths; bug fixes land once.
- Cons: adds a dependency to version-lock and release in lockstep; canair's device calls
  are tightly coupled to its own flows (`require_protocol` guarding transport selection,
  `--reboot` restoring AutoPID after a session, the sleep/battery banner). Likely end
  state: wican-cli owns the *primitives* (load/store config, set protocol, reboot,
  status), canair still owns the *orchestration* — clean split, but only if wican-cli's
  API is shaped for library use, not just CLI use.
- Best when: wican-cli is the real, maintained home for device management and exposes a
  stable Python API.

**Option B — keep it all in canair, stop advertising wican-cli**
- Pros: zero cross-package coupling; canair stays fully self-contained; working, tested
  code already exists. The device surface canair needs is small (config get/store,
  protocol, reboot, read status).
- Cons: if wican-cli has device features canair lacks (OBD-log queries, richer power
  control), users need two tools with overlapping-but-not-identical device commands.
- Best when: wican-cli is a separate audience/product and canair's needs are minimal
  enough that duplication is cheap.

**Option C — explicit division of labor, both advertised**
- canair = vehicle RE (profiles, captures, decode). wican-cli = device lifecycle
  (firmware config, power, protocol, OBD logs). canair keeps only the *minimum* device
  calls its RE flows require; README says "for device management beyond this, use
  wican-cli." No shared code, but documented, non-overlapping scope.
- Lowest-effort path to "not wrong": costs a README paragraph and maybe removing one or
  two redundant canair flags (does `canair wican --set-protocol` need to exist if
  wican-cli owns protocol switching?).

Read: unless there's active desire to consolidate code (Option A) and willingness to
shape wican-cli as a library, **Option C** is the pragmatic resolution — removes the
ambiguity without a risky refactor. The decision hinges on: **is wican-cli intended to
be a maintained product, or an extraction that's since drifted?** If the latter,
**Option B** (fold the mention, keep canair self-contained) is cleanest.

## Broadcast decoding in canair — design note

Decision: pursue in-tool (unblocks the drive-mode/regen research; squarely on-niche).
Main design consideration: the sniffer produces *broadcast* frames (fixed CAN ID →
periodic payload), a different shape from UDS *request/response* captures. The
`decoding.py`/`expression.py` engine and the `captures/` schema are both built around the
request/response model, so broadcast support likely means a **parallel capture type** and
a **signal-definition form** (bit offset + length + scale on a CAN ID) rather than
reusing the PID/DID expression path wholesale. Be deliberate about that split rather than
overloading the existing PID model.

## Summary recommendation

Double down on the RE-knowledge-loop (broadcast decoding, sniffer-capture ingestion,
signal inference, interop export) and shed/delegate the generic plumbing (device
management → wican-cli; broadcast GUI → SavvyCAN/cabana; DBC math → cantools; pentest
scanning → gallia/caringcaribou). Keep the homegrown UDS/KWP layer and the Hyundai
security bank — those are genuinely hard to source elsewhere. The one item needing a
decision first is the **canair ↔ wican-cli boundary**, since it's active duplication
between two packages the same author maintains.
