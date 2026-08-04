# Expression evaluator: compile once, evaluate many

`canlib.expression.evaluate_expression` re-parses its expression string on
**every** call — character by character, plus two `re.match` probes for the
`[Bn:Bm]` / `[Sn:Sm]` forms. But an expression is *constant* across a whole
series: the analysis engine evaluates one expression against thousands of
payloads. Splitting parse from evaluate makes the parse happen once.

Prototyped and measured (2026-08-04), not implemented. **Read the "Is this worth
doing?" section before starting** — the microbenchmark win is large, the
end-to-end win is modest, and there is a bigger fish in `align.join_prepared`.

## Why (the mechanism)

`evaluate_expression(expr, data, V)` is a single function that interleaves
tokenizing and evaluating in one `while i < len(expr)` loop (a faithful port of
`wican-fw/main/expression_parser.c`). Per call it re-does:

- a character-by-character scan of the expression (hence ~5.2M `str.isdigit`
  calls in an `investigate --bits` profile);
- `int()` reconstruction of every byte index from its digits;
- up to two `re.match` calls per `[` token;
- `precedence()` dispatch through an if-ladder per operator.

None of that depends on `data`. The only per-payload work is the byte loads and
the arithmetic.

## Measured (bundled `ioniq-2017` profile, 2026-08-04)

Corpus: **331 parameter expressions, 191 distinct**, mean length 6.9 chars, max
49 (`(([B18:B19]/10) * (((S15 << 8) | S17)/10)) / 1000`). Shapes: 86 bare `Bn`,
46 `Bn:k`, 20 `Sn`, 5 `[…]` ranges, 174 compound.

Microbenchmark — 191 distinct expressions × 2000 payloads (a real series is
~2200 samples):

| | µs/call | speedup |
|---|---|---|
| current (re-parse on every call) | 0.85 | — |
| compiled once, reused directly | 0.09 | **9.5x** |
| compiled + `lru_cache` lookup per call (API-compatible) | 0.13 | **6.9x** |

Per shape (10k evaluations, current → compiled):

| shape | example | µs before | µs after | speedup |
|---|---|---|---|---|
| bare byte | `B9` | 0.39 | 0.04 | 9.3x |
| bit | `B9:3` | 0.44 | 0.06 | 7.8x |
| signed byte | `S9` | 0.37 | 0.05 | 7.7x |
| byte + scale | `B9/2` | 0.74 | 0.08 | 9.3x |
| 16-bit range | `[B18:B19]/10` | 1.04 | 0.15 | 6.9x |
| compound | `(B03*256+B04)/10` | 1.91 | 0.21 | 9.1x |
| longest real | the 49-char one above | 4.57 | 0.57 | 8.1x |

**End-to-end, which is what actually matters** (API-compatible wrapper, so no
call sites changed):

| command | before | after | speedup |
|---|---|---|---|
| `blind.select_targets` (random draw) | 0.85s | 0.44s | **1.94x** |
| `decode --try` | 0.46s | 0.39s | 1.18x |
| `investigate uds IGPM 22BC03 --bits` | 3.49s | 3.04s | 1.15x |
| `align` (3 signals) | 0.16s | 0.14s | 1.10x |
| `correlate uds` (ranked, 24s) | 24.02s | 23.65s | 1.02x |
| `decode --compact` / `--corr` | 0.47s | 0.47s | ~1.00x |

The gap between 6.9x and 1.0-1.9x is the point: expression evaluation is no
longer most of any command's time. The `LoadedPid.decoded` change (commit
`f30e0ea`) removed the redundant hex decoding that used to sit *around* these
calls, and `correlate`/`hunt`'s **byte sweeps** (`build_byte_series` /
`build_bit_series` / `hunt_byte`) read the decoded frames by index and build no
expression at all — only the single reference/param series still goes through
`extract_series` (`xanalysis.py:271`, `:903`), so the per-command call volume is
a fraction of what it was.

## The change

Split the one function into **compile** and **execute**, keeping the existing
entry point as a cached wrapper.

```python
def compile_expression(expression: str) -> CompiledExpression:
    """Parse once; return something callable as (data, V) -> float."""

_compile_cached = lru_cache(maxsize=512)(compile_expression)

def evaluate_expression(expression: str, data: bytes, V: float = 0.0) -> float:
    return _compile_cached(expression)(data, V)
```

Representation: a **tree of closures**. Each operand compiles to a
`(data, V) -> float` closure with its index/limits bound as default arguments;
each binary operator compiles to a closure over its two operand closures. The
prototype measured 9.5x this way; an RPN list + interpreter loop was not
benchmarked but is expected to be slower (per-step dispatch in Python).

Build it by **reusing the existing shunting-yard control flow verbatim**,
changing only what the operand/operator branches *push*: instead of pushing a
value onto `operand_stack`, push a closure; instead of applying an operator
immediately, emit a closure combining the top two. This is why the prototype
matched on every edge case (below) without any special-casing — the semantics
come from the unchanged control flow, not from a re-derivation.

Cache sizing: 512 entries against 191 distinct expressions in the bundled
profile leaves ample headroom; the cache key is the raw expression string.
`maxsize` bounds memory for a long-running `monitor` that sees `--try` sweeps.

### Optional second step (probably not worth it)

Hot loops could call `compile_expression` once outside their per-payload loop and
skip the per-call cache lookup — 6.9x → 9.5x on the microbenchmark. The only
places with the volume to notice are `align.extract_series` and
`blind.eval_series`. Given the end-to-end table above, this buys almost nothing
measurable; do it only if a profile says so.

## Fidelity — the hard constraint

The evaluator is a port of the firmware parser and its output is what
`--promote` persists and what the WiCAN device itself computes. **Any divergence
is a silently wrong decoded value, not a crash.** The current implementation has
several quirks that must survive; the prototype reproduces all of them, verified:

| expression | result | why |
|---|---|---|
| `B5:12` | bit 1 of B5, trailing `2` discarded | bit reads exactly one digit; the stray literal is left on the stack and `operand_stack[0]` is returned |
| `B1 B2` | value of **B1** | two operands, no operator — the second is silently dropped |
| `B-1` | `-1.0` | `B` with no digits → index 0 → `data[0]`, then `- 1` |
| `[B3:B1]` | `0.0` | reversed range → empty accumulation loop |
| `B1)` | `1.0` | stray closing paren tolerated |
| `-B1`, `+B1`, `B1+`, `(B1` | `IndexError` | no unary operators; unbalanced input underflows the operand stack |
| `[S1:S3]` (3-byte span) | 32-bit sign extension | matches the firmware's container ladder — see `SIGNED_RANGE_WIDTHS` |
| `VX`, `0x10`, `1.5.5`, `B1:1:1`, `''` | `ValueError` | invalid character / empty |

Several of these are arguably bugs (`B1 B2` dropping an operand; `B-1`
decoding as `-1`). **Changing them is out of scope** — this is a
performance-neutral-behaviour plan. If any should change, that is a separate
decision with its own tests, because a profile in the wild may already encode
one.

## Testing

1. **Differential fuzz test** — the load-bearing one. Generate random
   expressions from the grammar (including malformed ones) and random payloads,
   and assert the compiled path and a **preserved copy of the current
   implementation** agree on the returned float *or* the exception type. Keep
   the reference implementation in the test module (not in `canlib/`) so the
   comparison stays honest and the production module has one code path.
2. **Real-corpus equivalence** — assert agreement across every expression in
   every bundled profile's `ecus/` (191 distinct in `ioniq-2017`). The prototype
   passes this today.
3. **The edge-case table above**, as explicit parametrized cases, so a future
   refactor that "cleans up" a quirk fails loudly.
4. The existing 34 `tests/test_expression.py` cases must pass untouched — they
   exercise the public entry point, whose signature does not change.
5. **Golden analysis output** — `tests/test_analysis_golden.py` already pins
   byte-identical output for 24 analysis invocations; it must stay green with no
   regeneration. That is the end-to-end guard that decoded values didn't move.
6. A **cache-correctness** test: the same expression string evaluated against
   two different payloads must return two different values (guards against
   accidentally caching the *result* rather than the compiled form).

## Docs

Small surface — this is an internal optimisation with no CLI change:

- No `AGENTS.md` command-reference change (no flags, no commands, no profile
  fields). Confirm rather than assume.
- `CHANGELOG.md` under `### Changed`, noting the honest end-to-end numbers
  rather than the microbenchmark figure alone.
- The `canlib/expression.py` module docstring gains a short note that the
  byte-space contract is unchanged and that `compile_expression` is the
  bulk-evaluation entry point.
- `.claude/skills/reverse-engineer-signal/SKILL.md` documents expression syntax
  for authors; it needs no change unless the quirks table is promoted into it
  (worth considering separately — the `B1 B2` and `B-1` behaviours are traps for
  a human author).

## Is this worth doing?

**Honest assessment: low priority, but cheap and safe.**

For it:
- ~1 day including the fuzz test; no API change, no caller changes.
- Semantics-preserving by construction, with a strong differential test.
- 1.94x on `blind.select_targets`, and the win scales with corpus growth.
- Removes a genuine architectural wart (re-parsing a constant).

Against it:
- 1.0-1.2x on nearly every user-facing command.
- It is **not** the current bottleneck. That is `align.join_prepared`.

### The bigger fish — `align.join_prepared`

Profile of `correlate uds --until 2026-08-02` (55s under cProfile, 24s real):

| | tottime | calls |
|---|---|---|
| `align.join_prepared` | **18.95s** (30.09s cumulative) | 49,441 |
| `bisect.bisect_left` | 7.08s | **94,520,585** |
| `math.fsum` (Pearson) | 4.54s | 120,792 |
| `stats` genexprs + `math.isfinite` | 9.76s | ~229M |
| `evaluate_expression` | 1.93s (3.5%) | 687,458 |

`correlate` ranks every signal pair, so it runs a nearest-timestamp join
**49,441 times**, each bisecting per sample — 94.5M bisects. The join alone is
**55% of the profiled runtime** (30.09s cumulative of 55.17s), with the Pearson
accumulation on top of that; expressions are 3.5%. Options worth a separate
plan: hoist a shared join per *PID pair* instead of per signal pair, cache
prepared/sorted series, cheap pre-filters to prune pairs before the full join, or
move the inner accumulation to `numpy`/`statistics` primitives.

**Recommendation:** do the evaluator work when convenient (it is small and
self-contained, good "clear the wart" work), but plan the `join_prepared` /
Pearson optimisation first if the goal is making analysis feel fast.

## Out of scope

- Changing any evaluator *semantics*, including the quirks above.
- Byte-space/PCI handling — the caller contract in the module docstring is
  unchanged.
- Compiling to Python source + `eval`/`compile`. Faster still, but it turns a
  profile-supplied string into executed code; the closure tree gets most of the
  win with none of that exposure.
- `numpy` vectorisation of expression evaluation across a whole series. Bigger
  change, and the per-call cost is not the bottleneck.
- The `join_prepared` / Pearson work above — its own plan.

## Status

Prototyped and benchmarked 2026-08-04 (`/tmp/expr_bench.py`, not committed);
**implemented 2026-08-04.**

- [x] `compile_expression` (closure tree) + `lru_cache(512)` wrapper keeping
      `evaluate_expression`'s signature; no call site changed.
- [x] Differential fuzz test against a preserved copy of the pre-change
      implementation, held in `tests/test_expression_compile.py` so the
      production module has one code path (480k random + mutated comparisons
      across 12 seeds pass; 8k run in CI).
- [x] Real-corpus equivalence over all 382 distinct expressions in the bundled
      profiles, × 8 payloads × two `V` values.
- [x] The quirks table as explicit parametrized cases, plus the two behaviours
      the fuzz surfaced (below).
- [x] `tests/test_expression.py` (34 cases) and `tests/test_analysis_golden.py`
      (27 pinned analysis invocations) pass untouched, no regeneration.
- [x] Cache-correctness tests: same expression / different payloads and `V`, and
      a parse-once assertion on `cache_info()`.
- [x] CHANGELOG + module docstring. No CLI surface changed, so no
      `AGENTS.md` / `docs/` / skill change (confirmed, not assumed).
- [x] Gates green: 4223 passed, ruff/ty/`validate all` clean.

Measured after implementation (382 expressions × 2000 payloads): **7.0x** per
call (1.07 → 0.15 µs), **9.4x** reusing the compiled form directly — matching the
prototype. End-to-end: `blind.select_targets` ~2x, `investigate uds IGPM 22BC03
--bits` 3.43s → 2.84s (1.21x), `align` within noise. The "optional second step"
(hoisting `compile_expression` out of `align.extract_series` /
`blind.eval_series`) was **not** done, per this plan's own assessment.

### Two divergences the differential fuzz found

Both were missed by the prototype's 24 hand-picked edge cases, which is the
argument for the fuzz test:

1. **A dropped operand's byte reads still happen.** The scan-as-you-go evaluator
   read *every* operand eagerly, so an out-of-range index inside an operand the
   expression silently discards (`B1 B99`) raised `IndexError` even though the
   value was thrown away. A closure tree evaluates only the returned root, so the
   compiler wraps the root to also evaluate the dropped subtrees
   (`expression._with_discarded`). A dropped entry is always pushed *after* the
   root stops being combined, so root-then-dropped reproduces the scan's error
   ordering exactly. Preserved, per this plan's semantics-preserving constraint.

2. **A parse error now precedes a payload-dependent one.** For input that is
   *both* unparseable and evaluates a bad operand (`B6/V:5` against a 1-byte
   payload), the old evaluator raised whatever it hit first while scanning — the
   `IndexError`/`ZeroDivisionError` — whereas compiling raises the `ValueError`
   up front. **Accepted deliberately**, rather than deferring parse errors to
   evaluation: which error the old code reported was itself payload-dependent
   (the same expression against a longer payload raised the `ValueError`), so it
   was never a contract, and a syntax error is a property of the expression, not
   of the data. No caller branches on the type (all catch `Exception` to surface
   the message). Anything that *compiles* decodes identically — which is the
   property that matters. Documented in `compile_expression`'s docstring and
   pinned by `test_parse_error_now_precedes_a_bad_operand`.

