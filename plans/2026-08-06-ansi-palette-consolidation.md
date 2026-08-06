# Consolidate the ANSI palette (and fix the colour-gating split it hides)

Status: **PLANNED** (2026-08-06). Flagged during
`plans/2026-08-06-command-packages-and-live-split.md`, where three of the modules
being split each carried their own copy of the palette. Deliberately deferred
there: consolidating it touches ~32 files and is its own change.

**The duplication is not the interesting part.** It is benign — measured below,
every constant is bound to the same escape code everywhere, so there is no latent
"two modules disagree" bug to fix. What makes this worth doing is that having no
single home for the palette is why **colour gating is inconsistent, and
`NO_COLOR` is unsupported** — and that part *is* user-visible.

---

## Measured, not assumed

**32 modules declare ANSI constants, 183 declaration lines, 7 distinct codes:**

| Name | Modules declaring it |
|---|---:|
| `_RESET` | 31 |
| `_DIM` | 29 |
| `_BOLD` | 28 |
| `_YELLOW` | 27 |
| `_CYAN` | 26 |
| `_GREEN` | 26 |
| `_RED` | 12 |

Spread across `canlib/commands/` (30 modules, including every analysis command and
five of the seven modules in `commands/correlate/` alone) plus `canlib/modes/dtc.py`.

**No divergence.** Every name maps to exactly one code across all 32 modules —
verified by collecting `name -> {codes}` over the tree. So this is copy-paste, not
drift, and a straight consolidation cannot change any output.

**One naming outlier:** `canlib/modes/dtc.py` uses `_C_DIM`/`_C_YEL`/`_C_GRN`/`_C_RST`
instead of the `_DIM`/`_YELLOW`/`_GREEN`/`_RESET` convention the other 31 use.

**The gating helper is duplicated too:** `bix.py`, `bus.py`, `groups.py` and
`states.py` each declare an identical `_use_color() -> bool` + `_c(text, code)`
pair. `bix.py` additionally has `_cerr` for stderr-gated warnings — the one piece
of genuine extra behaviour in the set.

## The actual defect: most commands ignore the pipe

Escape sequences counted in **piped** stdout (not a TTY), against the bundled
`ioniq-2017` profile so every command has real data to render:

| Command | Escapes when piped | |
|---|---:|---|
| `research` | 1048 | leaks |
| `correlate uds IGPM` | 252 | leaks |
| `ecu` | 184 | leaks |
| `hunt uds AAF 2181 --against …` | 106 | leaks |
| `decode BMS 2101` | 86 | leaks |
| `signals list` | 22 | leaks |
| `investigate uds IGPM 22BC03` | 16 | leaks |
| `coverage IGPM 22BC02` | 13 | leaks |
| `captures uds --summary` | 6 | leaks |
| `dtc --history` | 4 | leaks |
| `bus` | **0** | gated |
| `states` | **0** | gated |
| `groups` | **0** | gated |
| `bix -a 62BC0300` | **0** | gated |

Ten of fourteen emit raw escapes into a pipe. The four that behave are **exactly**
the four that each wrote their own `_use_color`/`_c` pair — which is the finding:
gating happened wherever someone thought of it, and nowhere else.

**`NO_COLOR` and `FORCE_COLOR` are unsupported anywhere** (grep: zero hits).
`NO_COLOR` is a widely-honoured convention and the natural thing for a user piping
canair into a log, or an agent consuming its output, to reach for.

This is *why* the duplication matters: there is nowhere to implement the fix once.

> **Measurement note.** A first pass ran these against the synthetic
> `single-frame` fixture and reported `research` as gated with 0 escapes. It has no
> research entries, so it printed one "no entries" line and coloured nothing —
> 0 escapes for lack of output, not for gating. Against a real profile it is the
> **worst** offender at 1048. Anything re-measuring this must use a profile that
> actually exercises the command, and check the line count alongside the escape
> count.


---

## Proposed shape

A new leaf module, `canlib/ansi.py` (peer of `canlib/formatting.py`, which renders
tables and would consume it):

```python
# The palette, named as 31 of the 32 modules already name it.
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

def use_color(stream=sys.stdout) -> bool: ...   # TTY + NO_COLOR/FORCE_COLOR
def c(text: str, code: str, *, stream=sys.stdout) -> str: ...
def cerr(text: str, code: str) -> str: ...      # bix.py's stderr variant
```

Deliberate choices:

- **Public names (no leading underscore).** It is library API consumed by ~32
  modules; the underscore was only ever module-privacy.
- **`use_color` resolves policy, not just TTY-ness**: `FORCE_COLOR` wins, then
  `NO_COLOR`, then `stream.isatty()`. That is the whole point of the exercise —
  one place to answer "should this be coloured?".
- **A leaf.** It imports only `os`/`sys`. Nothing in `canlib/` may not import it.
- **`rich` is out of scope.** A few paths already use `rich.Console`
  (`_live/connect.py`'s sleep banner, the Textual TUIs). `rich` does its own
  terminal detection; this module is for the hand-rolled escapes only, and the two
  should not be merged in the same change.

## Sequencing

The awkward part is that consolidation and gating are separable, and mixing them
would make a 32-file diff also a behaviour change. So:

| # | Commit | Behaviour |
|---|---|---|
| 1 | `feat(ansi):` add `canlib/ansi.py` + tests for `use_color` policy | none (nothing imports it) |
| 2 | `refactor:` point the 32 modules at it, deleting 183 local declarations | **none** — same codes, still ungated |
| 3 | `refactor:` fold the four `_use_color`/`_c` pairs onto `ansi.c` | none |
| 4 | `fix(ansi):` gate every coloured write, honour `NO_COLOR` | **user-visible** |

Commit 2 is the big mechanical one and must be provably inert. Commit 4 is the
only one with a changelog entry, and it is the one to be careful about.

### Why commit 2 is safe to do wholesale

`tests/_golden.py::norm()` strips ANSI before comparing, so the 44 golden files
cannot detect a colour change — they will not catch a mistake here. The gate that
*will* is a direct one: assert the module-level constants are gone and that
rendered output is byte-identical before/after. Capture a corpus of command
outputs (raw, un-normalised, ANSI included) at commit 1, and diff it after
commit 2. That is a throwaway harness, not a committed test.

### Why commit 4 needs its own care

Gating changes output, so it must be pinned deliberately:

- **The goldens are blind to it** (`norm()` strips escapes), which is the right
  call for them — but it means colour needs its own assertions. Add a small
  `tests/test_ansi.py` that runs a handful of commands with a fake non-TTY stdout
  and asserts **zero** escape sequences, plus `NO_COLOR=1` on a TTY.
- **The screenshot suite is the opposite risk**: `scripts/gen_screenshots.py`
  renders through `freeze`/`vhs`, which *do* present a TTY. If gating accidentally
  keys off something else, every screenshot goes monochrome. Run
  `make screenshots-check` as part of commit 4.
- **`--json` paths must be unaffected** — they already shouldn't emit colour;
  worth asserting rather than assuming.

## Risks

- **Low risk, wide blast radius.** 32 files, but each edit is "delete 6 lines, add
  one import". The mechanical danger is a module that shadows a name locally, or
  one of the `_C_*` outliers in `dtc.py` being missed.
- **`commands/decode/format.py` already did this once, locally** — it is the single
  home for decode's palette after `298538d`. It becomes a thin re-export or goes
  away entirely; check its importers (`views`, `analysis`) rather than leaving a
  second-level indirection.
- **Import cycles: none plausible.** `canlib/ansi.py` importing only stdlib means
  every current holder can import it.

## Out of scope

- **Unifying `rich` and the hand-rolled escapes.** Two colour systems is a real
  question, but a different one.
- **Theming / 256-colour / truecolour.** The seven codes in use are enough.
- **The Textual TUIs.** They own their own styling.
- **`canlib/formatting.py`'s table rendering.** It should *consume* `ansi`, but
  reworking how it colours is separate.

## Prior art in-tree

`canlib/safety.py::enforce_command_safety` is the precedent the contributing-code
skill cites for exactly this: a policy duplicated across two transports, extracted
to one home that both call. The difference is that the blocklist had *diverged*
between its copies, which is what made it urgent. This one has not — so it is
lower-stakes housekeeping that happens to unblock a user-visible fix.
