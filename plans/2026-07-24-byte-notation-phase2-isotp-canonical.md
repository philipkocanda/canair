# Byte Notation — Phase 2: ISO-TP-Canonical Analysis + Switchable Display Notation

Status: **Phase 2a SHIPPED; Phase 2b DEFERRED** (see the `## Status` section).
The header caveat below ("design doc … nothing here is implemented yet") is
**stale** — it predates the 2a/2b split. Phase 2b is now an **optional cleanup**,
not a prerequisite: `plans/2026-07-24-raw-can-analysis.md` Stage 2 reached the
raw-CAN generalization from the frame side instead. Deferred items: re-home the
analysis engine to read ISO-TP directly, reimplement `bix` on `notation.py`, the
TUI live notation toggle, and the `ByteSpace.RAW_CAN` path.

Re-home the analysis engine onto a **typed, ISO-TP-canonical byte model** and add
a **switchable display notation** (WiCAN / ISO-TP / Torque / bix, and later
raw-CAN). WiCAN's PCI-interleaved notation becomes a *rendering* + a *firmware
persistence format*, produced only at the very last stage — never the internal
unit. Depends on Phase 1
(`2026-07-24-byte-notation-phase1-fix-consolidate.md`).

> **This is a design doc for close review, not an approved build.** Several
> decisions below are marked **OPEN** and need sign-off before coding. Nothing
> here is implemented yet.

> **Re-evaluated against the current tree.** The user's uncommitted `bix.py` +
> docs work *strengthens* this design rather than invalidating it:
> - `byteindex.framed_to_wican_frame()` + `NotAFrameError` now exist — they index
>   an **already-framed CAN payload** (PCI present, straight off the bus) into the
>   same `[(byte, isotp_index_or_None)]` shape as `payload_to_wican_frame`. That is
>   exactly the `ByteSpace.RAW_CAN` seam this doc anticipated — no longer purely
>   hypothetical.
> - `bix.py`'s `--annotate` was rewritten to derive **every** notation from the
>   byte's actual ISO-TP index (`pi`), and Torque/bix are now opt-in behind
>   `--torque/--obdb` (WiCAN + ISO-TP primary). That is a working proof-of-concept
>   of the exact pattern `notation.py` generalizes, and it sets the default-notation
>   precedent this plan follows.

## Goals

1. **Future-proof for raw CAN.** Stop tying the core to WiCAN's fringe notation
   so a future raw-CAN payload (single frames from `canair sniff` / broadcasts,
   no ISO-TP reassembly) can be analyzed without the WiCAN/PCI concept applying
   at all. Raw-CAN analysis is **out of scope to *implement* now**, but the
   architecture must not preclude it.
2. **Switchable display notation.** A `--notation` flag (+ TUI toggle) renders
   byte references in the user's chosen scheme. **Default stays `wican`** (agreed)
   to preserve muscle memory and the label==expression shortcut.
3. **Kill the conflation.** One typed model so ISO-TP, WiCAN, raw-CAN, and Torque
   indices can never be silently mixed — leveraging the type system heavily
   (user's explicit ask, given how central byte indexing is to the project).

## The core idea: a typed `ByteRef` (not bare ints + an enum)

Today the analysis engine abuses the **WiCAN expression string as the internal
identity** of a byte: `build_byte_series` synthesizes `"B{bn}"` and evaluates it
through the expression evaluator (`xanalysis.py:185` → `align.extract_series` →
`evaluate_expression`); `correlate._promote_top_byte` then uses that same label
string *directly* as the persisted expression (`correlate.py:679-690`,
`expr = parts[2]`). Label, internal key, and firmware expression are one and the
same string — which is exactly why the WiCAN notation leaked everywhere.

Phase 2 introduces a small **value object** as the single internal currency for
"a place in a decoded payload", carrying an **ISO-TP** offset:

```python
@dataclass(frozen=True)
class ByteRef:
    """A position in the reassembled ISO-TP UDS payload (canonical space).

    isotp_offset counts data bytes from 0 with NO PCI framing — the layout every
    transport actually returns. bit is None for a whole byte, 0-7 for a single
    bit. width/signed/endian describe the interpretation for multi-byte reads.
    WiCAN indices, Torque letters, bix, and raw-CAN offsets are all *views*
    derived on demand; none is stored.
    """
    isotp_offset: int
    bit: int | None = None
    width: int = 1
    signed: bool = False
    little: bool = False
    space: ByteSpace = ByteSpace.ISOTP   # ISOTP today; RAW_CAN reserved for future
```

Why a type, not `(int, NotationEnum)` threaded through print functions:

- **The unit of confusion is the *offset's meaning*, not its display.** A bare
  int can't tell you whether it's ISO-TP or WiCAN — precisely today's bug. A
  `ByteRef` makes the space part of the value, so mixing is a type error, not a
  silent off-by-PCI.
- It **co-locates identity + interpretation** (offset/bit/width/signed/endian),
  replacing the ad-hoc `(offset, spec, little)` tuples in `_decode_plot`/`hunt`
  and the `f"B{off}"` / `f"B{off}:{k}"` strings scattered across `xanalysis`,
  `correlate`, `investigate`, `decode`, `coverage`.
- Rendering, expression-generation, and series-extraction become **methods /
  pure functions over one type**, so there's exactly one conversion edge each.

### The two edges (convert only at the last stage)

Everything internal is ISO-TP `ByteRef`s. WiCAN appears at exactly two edges:

1. **Evaluating a *stored* param expression** (already WiCAN-indexed, from
   `ecus/` YAML) → keep today's path: reassembled payload → `payload_to_wican_bytes`
   → `evaluate_expression`. Unchanged; stored expressions are WiCAN by definition.
2. **Emitting a WiCAN expression** for `--promote` / display of the "as an
   expression" form → `ByteRef.to_wican_expression()`, the single ISO-TP→WiCAN
   conversion point, built on `byteindex.isotp_to_wican` + the existing
   `_decode_plot.wican_expr` shift-composition logic.

Crucially, **byte-level analysis no longer needs the expression evaluator or PCI
reinsertion at all**: to read byte `i` you index the reassembled ISO-TP payload
at `i` directly. ISO-TP has **no PCI bytes**, so:

- All the `skip_pci` / `wican_to_isotp(off) is None` filtering
  (`xanalysis.py:180,221`, `decode.py:700`, `investigate.py:375`,
  `coverage.py:134`) **disappears from the analysis engine** — there are no
  framing bytes to skip in ISO-TP space. (PCI-awareness survives only in the
  `to_wican_expression` edge and the `check_pci_bytes` validator.)
- **New capability:** a signal that straddles a WiCAN frame boundary (a
  multi-byte value whose bytes sit either side of a CF PCI byte) is *contiguous*
  in ISO-TP and therefore **findable** by `hunt`/`correlate` and expressible via
  the shift-composition form (`(B8 << 8) | B10`). Today `hunt_byte` simply skips
  any window crossing PCI (`xanalysis.py:401-404`) — it can't find such a signal
  at all. This is a genuine reverse-engineering win, not just a refactor.

## Display notation layer

New leaf module `canlib/notation.py`, built on the existing `byteindex.py`
conversions (which already implement all the math — `bix.py` is today's main
consumer and already derives its columns from the ISO-TP index):

```python
class ByteNotation(str, Enum):
    WICAN  = "wican"    # B9, [B10:B11]  — firmware view (default)
    ISOTP  = "isotp"    # i6, 0x06       — canonical payload index
    TORQUE = "torque"   # G, AA          — Torque/OBDb letter
    BIX    = "bix"      # 48             — Torque bit index
    RAW_CAN = "raw-can" # reserved; renders only for ByteSpace.RAW_CAN refs

def render(ref: ByteRef, notation: ByteNotation, *, sub_bytes: int) -> str: ...
```

- `render(ref, WICAN, …)` is the default and reproduces today's exact `Bn` /
  `Bn:k` labels — **byte-for-byte identical output when `--notation` is
  unset**, so existing behavior/tests/muscle-memory are preserved.
- `sub_bytes` (1 for `21xx`, 2 for `22xxxx`) is needed for Torque/bix, mirroring
  `bix.py`'s `-1/-2`.
- For a `RAW_CAN` ref, WiCAN/Torque/bix views are `None`/`—` (no ISO-TP layer);
  `render` degrades gracefully — this is the seam that keeps raw-CAN from
  breaking the model later. **Foundation already exists:**
  `byteindex.framed_to_wican_frame()` indexes a framed CAN payload into the shared
  `(byte, isotp_index)` currency, so a `ByteRef` built from a raw frame slots into
  the same renderer.

`canair bix` (`canlib/commands/bix.py`) gets **reimplemented on top of this
layer** rather than owning its own conversion calls — it becomes the reference
consumer, shrinking and de-duplicating. The recent `--annotate` rework already
took the first step (deriving every column from the ISO-TP index `pi` rather than
from WiCAN), so this is now a smaller lift than when first scoped.

## Routing every label through the layer

Central `f"B{off}"` / `f"B{off}:{k}"` construction sites to convert to
`ByteRef` + `render()` at print time (internal carrier becomes `ByteRef`):

| Site | File:line | Role |
|---|---|---|
| `build_byte_series` / `build_bit_series` — series keys `ECU:PID:Bn[:k]` | `xanalysis.py:188,228` | **origin** of most byte labels |
| `hunt_byte` hits (`HuntHit.expr`/`.offset`) | `xanalysis.py:422-436`; printed `hunt.py:207-214` | origin |
| `correlate` ranked list + `_promote_top_byte` | `correlate.py:679-690` | **consumes** series-key label; splits `parts[2]` as the promoted expr |
| `_byte_state_buckets` / `find_mirrors` | `decode.py:710,721,818,824` | origin |
| `investigate._iter_edges` + `ByteStat.label` | `investigate.py:141,393,411,418` | origin |
| `coverage` UNMAPPED/BITS output | `coverage.py:315-323` | origin |
| `_decode_plot` inspect/expr line | `_decode_plot.py:452,463` | origin (display) |

`SignalRef` (`align.py:42`) is the natural place to host or compose a `ByteRef`:
its `name_or_expr` currently doubles as param-name-or-WiCAN-expression. Proposed:
a `SignalRef` resolves to *either* a named param *or* a `ByteRef`, so byte
analysis stops piggybacking on synthesized WiCAN expression strings.

## `--notation` flag + TUI toggle

- Add `--notation {wican,isotp,torque,bix}` (default `wican`) to the analysis
  verbs: `correlate`, `hunt`, `investigate`, `coverage`, `decode`
  (`--bytes`/`--bits`/`--find-mirrors`/`--plot`). Shared via a helper in
  `capture_dates.add_scope_args` neighbor or a new `add_notation_arg`. **Align with
  the `bix` precedent** the user just set: WiCAN + ISO-TP are the primary
  notations; Torque/bix are cross-referencing aids (in `bix` they're now opt-in
  behind `--torque/--obdb`). Consider mirroring that split rather than treating all
  four notations as peers.
- **`--promote` always converts the `ByteRef` to a WiCAN expression regardless of
  display notation** — the "convert only at the last stage" principle. So you can
  read output in ISO-TP and still promote a valid firmware expression.
- **TUI** (`decode --plot`, `--monitor`): a key (e.g. `n`) cycles the notation
  live; the `_decode_plot` inspector and monitor hex view re-render labels.
- **Config default: OPEN** — likely a `display.byte_notation` key in
  `~/.config/canair/config.yaml` (via `canair config`) defaulting to `wican`.

## Open decisions (need sign-off before coding)

1. **`ByteRef` granularity.** One type carrying interpretation
   (width/signed/endian), or split `ByteRef` (identity) from `ByteInterp`
   (reading)? Leaning combined for ergonomics; flag if you prefer separation.
2. **`SignalRef` refactor depth.** Compose `ByteRef` into `SignalRef`, or keep
   `SignalRef` for named/expr signals and add a parallel `ByteRef` path? Affects
   `align.extract_series`'s signature and every analysis caller.
3. **Series extraction for plain bytes** should read the ISO-TP payload directly
   (no evaluator). Confirm we retire the synthesized-`"B{bn}"`-expression trick
   entirely rather than keep it as a fallback.
4. **Raw-CAN readiness depth now.** Introduce `ByteSpace` enum + graceful `render`
   degradation as scaffolding (recommended, cheap — `byteindex.framed_to_wican_frame`
   already provides the framed-payload→ISO-TP indexing a `RAW_CAN` `ByteRef` needs),
   but implement **no** raw-CAN *analysis* path until the `sniff` broadcast
   use-case is real. Confirm scaffolding is wanted vs. deferring the enum too.
5. **Config surface.** Add `display.byte_notation`, or flag-only for the first
   cut?

## Cross-cutting

- **Back-compat:** default `wican` rendering must be byte-identical to today.
  Golden-output tests on `correlate`/`hunt`/`investigate`/`coverage`/`decode
  --bytes` before/after to prove no drift when `--notation` is unset.
- **Tests:** `notation.render` round-trips vs `byteindex` for all schemes +
  boundary offsets (incl. multiframe tails, PCI-straddling ranges); `ByteRef.
  to_wican_expression` matches `_decode_plot.wican_expr` for single/multi/LE
  cases and yields shift-composition across PCI; promote still passes
  `check_pci_bytes`; a straddling multibyte signal is now *findable* by `hunt`.
- **Docs (user-facing surface changes → required):**
  - `docs/concepts/byte-indexing.md` — rewrite around "ISO-TP is canonical; WiCAN
    is a view + firmware format"; document `--notation`.
  - `docs/reference/cli/` — per-command `--notation` flag; `bix.md` cross-ref.
  - `README.md` — only if a command *map* line changes (likely just a terse note
    that analysis output notation is switchable); keep it lean per the docs split.
  - `AGENTS.md` — update the `decode`/`correlate`/`hunt`/`investigate`/`coverage`
    entries with `--notation`; note ISO-TP-canonical internals.
  - `.claude/skills/` (reverse-engineer-signal, ioniq-reverse-engineering) — the
    byte-index workflow guidance.
- **CHANGELOG.md** — new capability (switchable notation) + the straddling-signal
  find.

## Risks / call-outs

- **Largest blast radius in the analysis engine** — touches every analysis verb.
  Mitigated by: default-`wican` golden tests, and landing the `ByteRef` model +
  `notation.py` first (pure, unit-tested) before rewiring call sites one command
  at a time.
- **Removing `skip_pci` everywhere is correct only once series read ISO-TP
  directly.** If any path still reads a WiCAN frame, dropping PCI-skip
  re-introduces framing bytes as fake data. The migration must flip
  read-space and drop-skip *together* per call site.
- **`--promote` correctness** hinges entirely on `to_wican_expression` — it is
  the single firmware edge and must be the most heavily tested unit, with
  `check_pci_bytes` as the backstop (unchanged).
- Scope discipline: resist implementing raw-CAN analysis now; only keep the door
  open.

## Suggested sequencing

1. `ByteRef` + `ByteSpace` + `canlib/notation.py` (pure, fully unit-tested).
2. Reimplement `canair bix` on the layer (proves the renderer; low risk — the
   `--annotate` ISO-TP-`pi` rework already did most of it).
3. `ByteRef.to_wican_expression` + promote path (the firmware edge) with tests.
4. Rewire analysis verbs to carry `ByteRef` + `render()`, one command per PR,
   each gated by default-`wican` golden output.
5. Add `--notation` flag + TUI toggle + config key.
6. Docs + skills + CHANGELOG.

## Status

Split into **Phase 2a (shipped)** and **Phase 2b (deferred)** during
implementation, to keep each change golden-gated and manage the large blast
radius. 2a delivers the user-facing goals (typed model, switchable display
notation, raw-CAN-ready `ByteSpace`); 2b is the deeper internal re-homing that
changes every default label and is best done per-command with golden tests.

### Phase 2a — shipped

- [x] `ByteRef`/`ByteSpace`/`ByteNotation` + `render`/`to_wican_expression`/
  `relabel_signal` in `canlib/notation.py` + 35 unit tests (incl. parity with
  `_decode_plot.wican_expr` and PCI-straddling shift-composition).
- [x] `--notation {wican,isotp,torque,bix}` wired into `correlate`, `hunt`,
  `investigate`, `coverage`, `decode` (`--discriminate`/`--find-mirrors`).
  Default `wican` is byte-identical to before (golden-preserving); `--json` /
  `--promote` stay canonical WiCAN.
- [x] `display.byte_notation` config key (`resolve_notation`) + `config.example.yaml`.
- [x] Docs: `byte-indexing.md`, regenerated CLI reference, `AGENTS.md`, CHANGELOG.

### Phase 2b — partially shipped

**Shipped: length-aware labels (the WiCAN/ISO-TP conflation in the render layer).**
A WiCAN index only maps to an ISO-TP index if you know the payload's length — a
single-frame (≤7-byte) response has ONE PCI byte, a multi-frame response TWO. The
conversion assumed multi-frame, so every data byte of a single-frame PID resolved
one index too low and the ISO-TP/Torque/bix views named the wrong byte (the first
data byte rendered `—`, claiming no Torque position at all).

- [x] `ByteRef.from_wican` / `relabel_signal` take an optional `payload_len` (and
  a `{(ECU, PID): length}` map, for the ranked `correlate` output that mixes PIDs
  in one list); `align.longest_payload_len` / `align.payload_lengths` build them.
- [x] Threaded through every label renderer: `coverage`, `correlate` (ranked,
  clusters, `--against`, cross-ECU mirrors), `investigate` (report, `--events`,
  dwell), `decode` (`--discriminate`, `--find-mirrors`), `hunt`.
- [x] **Golden-output gate** (`tests/test_analysis_golden.py`, 24 cases) proving
  default-`wican` output is byte-identical — the plan's back-compat constraint.
- [x] `tests/fixtures/profiles/single-frame` — a synthetic profile with a
  *varying* single-frame payload. The bundled profile could not cover this: of its
  11 single-frame PIDs only one has capture volume and all its data bytes are
  constant, which is how this bug class survived.

**Still deferred:**

- [ ] **Re-home the analysis engine to read ISO-TP directly** — retire the
  synthesized-`"B{bn}"`-expression trick in `build_byte_series`/`build_bit_series`
  so series carry `ByteRef`s and PCI-skipping leaves the engine. Labels are now
  correct, but the *internal* model is still WiCAN-expression-keyed. Unlocks the
  PCI-straddling-signal find (`_promote` already accepts a skip-the-framing-byte
  composition, so the write side is ready) and true raw-CAN analysis.
- [x] **Type the in-memory capture entry** — done. `load_all_captures()` now
  returns `list[CaptureEntry]`. The invariance problem was solved by splitting the
  read path in two: precise `CaptureEntry` on the loader, `LoadedPid.captures` and
  the command signatures; a **type parameter** on the genuinely shape-agnostic
  helpers (the scope filters, `key_index`, `_dedupe_payloads`, `_capture_key`,
  `entry_date`/`entry_datetime`) so they accept both a full row and `decode`'s
  slimmer reshaped row while *preserving* the caller's row type. `list[Row]`
  (not `Sequence`) on the filters keeps their no-op fast path returning the same
  object rather than copying 50k+ rows.
- [ ] **Reimplement `canair bix` on `notation.py`** (pure internal dedup; deferred
  to avoid re-churning code Phase 1 just touched, and because the 35 notation
  tests already prove the renderer).
- [ ] **Re-home the analysis engine to read ISO-TP directly** — retire the
  synthesized-`"B{bn}"`-expression trick in `build_byte_series`/`build_bit_series`
  so series carry `ByteRef`s and PCI-skipping disappears from the engine. This
  changes internal series keys and is the largest blast radius → do it
  per-command, each gated by default-`wican` golden output. Unlocks the
  PCI-straddling-signal find and true raw-CAN analysis.
- [ ] **TUI live notation toggle** in `decode --plot` / `--monitor` (a key to
  cycle notation). Not a TUI-*only* capability — the scriptable `--notation` flag
  already covers non-interactive use — so this is a convenience, low priority.
- [ ] **Raw-CAN analysis path** (`ByteSpace.RAW_CAN` end-to-end) once the `sniff`
  broadcast-frame use-case is real; `byteindex.framed_to_wican_frame` +
  `ByteRef.from_raw_can` are the seams already in place.
