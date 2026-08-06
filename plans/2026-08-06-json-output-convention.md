# `--json` output has no convention — 30 surfaces, ~12 incompatible shapes

Status: **PROPOSED** (2026-08-06) — found while scripting `decode --dump-bytes --json`
during the IGPM/VCU bitfield analysis; two consecutive consumer attempts failed on
shape guesses, not on the data.

## The problem

`--json` is advertised throughout `AGENTS.md` and the docs as *the* machine-readable
surface, and agents are its primary consumer. But there is **no convention** for what
a JSON payload looks like, so a consumer cannot write a single generic reader — it
must know, per command, whether the top level is an array or an object, and which key
holds the payload.

30 `--json` flag registrations across 25 command modules
(`canlib/commands/*.py`, `canlib/commands/captures/*.py`) currently emit at least
**12 mutually incompatible shapes**.

### Measured inventory (2026-08-06, v1.14.1)

**Top level is a bare array** — a consumer cannot attach any metadata, and
`d["key"]` is a `TypeError`:

| Command | Shape |
|---|---|
| `coverage` | `[{ecu, pid, params, verified, capture, …}]` |
| `decode` (default view) | `[{date, vehicle_states, file, parameters}]` (n=6000) |
| `captures uds` (default / `--latest` / `--sessions`) | `[{ecu, ecu_addr, pid, date, …}]` |
| `captures can` | `[…]` |
| `align` | `[{date, time, values}]` (n=6360) |
| `research` | `[{type, target, status, priority, …}]` |

**Top level is an object, but the payload key differs every time:**

| Command | Envelope keys | Payload key |
|---|---|---|
| `decode --dump-bytes` | `ecu, pid, notation, include_pci, columns, offsets, rows` | `rows` |
| `correlate` | `reference, method, join_tol_s, fill, lag_scan, hits` | `hits` |
| `investigate` | `target, join_tol_s, fill, independent_of, keep_*, bytes, word_candidates` | `bytes` (+`word_candidates`) |
| `ecu` | `name, alias, …, stats, captures, pid_list` | `pid_list` |
| `states` | `states, undeclared, source` | `states` |
| `bus` | `buses, undeclared, unbussed_ecus, gateway_ecus, source` | `buses` |
| `groups` | `groups, source` | `groups` |
| `logs` | `path, events` | `events` |
| `config show` | `files, config, wican, devices, fallback, transport, profile` | — (no collection) |
| `status` | `exit, warnings, errors, canair_version, transport, device, lock, …` | — (no collection) |
| `signals list` | **`{<bus name>: …}`** | **the bus name itself** |

Two cases deserve calling out because they defeat *any* per-command memorisation:

1. **`signals list --json` keys the top level on the data** — it returned
   `{"powertrain": …}` on the bundled profile. Rename the bus and the top-level key
   changes. Nothing else in canair does this.
2. **One command emits two different top-level shapes depending on a flag.**
   `decode BMS 2101 --json` is a bare array; `decode BCM 22B004 --dump-bytes --json`
   is an object. Same subcommand, same `--json`.

### Why this is a real cost, not a papercut

The failure mode is silent and shape-dependent, so it costs a round trip *per
command* rather than being learned once:

```
# assumed a bare list (as coverage/align/research/decode return) → silently empty
… decode BCM 22B004 --dump-bytes --json | python -c "…[r for r in d if isinstance(r,dict)]…"
{}

# guessed the payload key as results/pairs (it is `hits`) → silently empty
… correlate … --json | python -c "…d.get('results') or d.get('pairs')…"
--- top 12 overall ---      (nothing)
```

Both printed **empty output and exit 0** — the worst possible failure for an agent or
a cron job, because it reads as "no findings" rather than "you addressed it wrong".
This is not hypothetical: it happened twice in one session and once got as far as
being written into a committed skill document before being caught.

## Root cause

There is no single writer and no written convention:

- No `docs/development/json-output.md`; no plan; nothing in `AGENTS.md` states a shape
  contract beyond "`--json` for machine output".
- Every command builds its own dict/list literal and calls `json.dumps` locally, so
  each of the 25 modules independently invented a shape. There is no
  `canlib/json_out.py` analogue of the single-writer modules the codebase already
  favours (`canlib/captures.py::saved_banner` for the save banner,
  `canlib/commands/_join.py::add_join_args` for shared flags,
  `canlib/profile.py::BUNDLE_MEMBERS` for bundle membership).
- Consequently every *new* `--json` surface adds a 13th shape at zero friction.

The individual shapes are symptoms. **The absence of a single emitter is the defect.**

## Proposal

### 1. One envelope, emitted by one function

```json
{
  "schema": 2,
  "canair": "1.15.0",
  "command": "decode dump-bytes",
  "profile": {"name": "ioniq-2017", "path": "/…/profiles/ioniq-2017"},
  "query":  {"ecu": "BCM", "pid": "22B004", "notation": "wican", "include_pci": false},
  "meta":   {"count": 893, "warnings": [], "fill": {"mode": "auto", "held": 0}},
  "data":   [ … ]
}
```

Contract:

1. **The top level is always a JSON object.** Never an array. This alone fixes the
   four-vs-eleven split and makes every payload extensible without a break.
2. **The payload is always `data`.** A collection → array; a singleton (`status`,
   `config show`, `ecu <ECU>`) → object. Consumers can always reach it as `d["data"]`.
3. **`schema`** is an integer a consumer can branch on. This is what makes any future
   change non-guessy, and is the piece most conspicuously missing today.
4. **Identity is mandatory** — `canair` (version), `command` (the resolved
   subcommand path), `profile` (name **and** resolved path). Second payoff: this
   directly mitigates the wrong-profile hazard `AGENTS.md` warns about at length,
   because output becomes self-describing about which car produced it.
5. **`query` = what was asked** (ECU/PID/selectors/scope filters/notation).
   **`meta` = how it was computed** (counts, `join_tol_s`, `fill`, `method`,
   `min_r`, and **`warnings`**). Today warnings are scattered: `status` has top-level
   `warnings`/`errors`, `correlate` prints the `keep:changes` banner to stderr only,
   `investigate` carries `keep_unique`/`keep_changes` booleans. Normalising to
   `meta.warnings: [str]` makes the soft-warning surface machine-visible everywhere.

Semantic keys (`rows`, `hits`, `buses`) are *nicer prose*, but this is a machine
format: a generic consumer is worth more than a pretty key, and the semantic name is
recoverable from `command` anyway.

### 2. `canlib/json_out.py` — the single writer

```python
def emit(command: str, data, *, profile=None, query=None, meta=None,
         warnings: list[str] | None = None) -> None:
    """Serialise the one canonical envelope to stdout."""
```

This is the actual fix. Policing 30 call sites with tests is the wrong shape — funnel
them through one emitter and conformance becomes **structural**. Then the test suite
needs only (a) unit tests on `emit`, and (b) one smoke test asserting every
`--json`-capable command's output parses and satisfies the contract.

### 3. Migration — needs a decision

Wrapping a bare array is inherently breaking; it cannot be done additively. Three
options, in increasing caution:

| | Approach | Cost | Risk |
|---|---|---|---|
| **A** | **Clean break, ship as 2.0.** All 30 surfaces move at once; `schema: 2` lets consumers detect. | Lowest effort, one CHANGELOG entry | Breaks any existing script on upgrade |
| **B** | **Dual-emit for one minor.** Envelope commands gain `data` *alongside* `rows`/`hits` (non-breaking); bare-list commands still break. | Medium; redundant output | Only *partially* solves it — the bare-list break still lands, so it buys little |
| **C** | **Opt-in during transition.** `CANAIR_JSON_SCHEMA=2` env var / `json_schema: 2` config key; default 1 for one release, default 2 at 2.0. | Highest effort, two code paths | Safest; but two shapes alive at once is exactly today's problem |

**Recommendation: A.** `--json` consumers here are agents and ad-hoc scripts, not a
published API with unknown downstreams; option B's redundancy buys nothing because the
bare-list break lands regardless; option C keeps two shapes alive, which is the
disease. Ship as **2.0** with `schema: 2`, a loud CHANGELOG entry, and the docs page
below. The `schema` field means this is the *last* migration that needs guessing.

*This is the open decision — see Open questions.*

## Sequencing

1. `canlib/json_out.py` + unit tests (`emit`, envelope shape, `warnings` normalisation).
2. `docs/development/json-output.md` — write the contract down **first** so the
   migration has a spec to check against.
3. Convert the **six bare-list** commands (`coverage`, `decode`, `captures uds`,
   `captures can`, `align`, `research`). Biggest consumer win; highest breakage.
4. Convert the **eleven envelope** commands; drop bespoke keys in favour of
   `data`/`query`/`meta`. Fix `signals list`'s data-dependent top-level key.
5. Convert the remaining singleton/action surfaces (`status`, `config show`, `lock`,
   `update`, `contribute`, `import *`, `captures maint`/`merge-driver`, `logs`,
   `groups`, `states`, `bus`, `ecu`, `signals`, `hunt`, `correlate`, `investigate`).
6. Conformance smoke test over every `--json` surface.
7. Docs sweep (`AGENTS.md`, `README.md`, `docs/reference/cli/*.md`) + CHANGELOG.

Steps 3–5 are mechanical once step 1 exists and can land incrementally, but **all of
them must land in the same release** — a half-migrated interface is worse than either
end state.

## Testing

- **Unit** — `emit()` envelope shape; `data` as list vs object; `meta.warnings`
  normalisation; `profile` omitted for profile-less commands (`status`, `lock`,
  `update`, `config`).
- **Conformance (the important one)** — a registry-driven test that runs every
  `--json`-capable command against `tests/fixtures/profiles/` and asserts: top level
  is an object; `schema`/`canair`/`command` present; `data` present. This is what
  prevents the 13th shape. Model it on the existing bundle-member registry test.
- **Regression** — the 31 test files touching `json.loads` need review; only a
  handful index a payload key by name today (`hits` ×4, `states` ×6, `rows` ×1,
  `events` ×1, `buses` ×1), so coupling is much lighter than the file count suggests.
- **Golden** — one committed golden envelope per representative shape
  (collection / singleton / action-result) to catch silent drift.

## Docs (when implemented)

- **New** `docs/development/json-output.md` — the contract, the envelope, worked
  examples, and the rule that new `--json` surfaces must go through `emit()`.
- `AGENTS.md` — replace the per-command "`--json` for machine output" prose with one
  statement of the envelope + a pointer; note `schema` for consumers.
- `docs/reference/cli/*.md` — regenerate; several pages document the old shapes.
- `CHANGELOG.md` — breaking-change entry with a before/after and a one-line migration
  recipe (`d` → `d["data"]`).
- The `decode --dump-bytes` snippet in `.claude/skills/decode-bitfields/SKILL.md`
  hardcodes `['rows']` and must be updated (it was already wrong once).

## Adjacent nits spotted while surveying (Boy Scout, optional)

- **`decode --dump-bytes` timestamps degrade silently.** The CSV `time` column holds
  an absolute datetime for timed rows (`2026-04-18 22:26:53.000000`) but falls back to
  a **date only** (`2026-04-15`) for untimed captures — same column, two precisions,
  so a consumer parsing it as a datetime gets inconsistent values per row. The JSON
  path splits `date` + `time` correctly but emits `"time": ""` rather than `null`.
  (5 of 893 rows on `BCM 22B004`.) `validate captures` already soft-warns on untimed
  payload captures; the emitters should be honest too — `null`, and a `meta.warnings`
  entry noting how many rows are untimed.
- `status --json` uses `exit` as a key for the process exit code — reads like a verb.
  `exit_code` is clearer, and under the new contract it belongs in `meta`.
- `captures can --json` on a profile with no raw-CAN logs returns `[]`, which is
  indistinguishable from "no `captures/can/` at all". Under the envelope,
  `meta.count: 0` plus a `meta.warnings` entry disambiguates.

## Open questions

1. **Migration strategy — A, B, or C above?** Recommendation: **A** (clean break at
   2.0). This gates everything else and is the one call I do not want to make
   unilaterally, since it is a judgement about downstream consumers.
2. **`data` vs semantic keys** — confirmed as `data`? The alternative (keep `rows`/
   `hits`/`buses`, only guarantee "always an object") is a smaller change and keeps
   nicer prose, but leaves a consumer needing a per-command key lookup, which is
   ~60 % of the original pain.
3. **Should `data` always be a list**, wrapping singletons as a 1-element array for
   total uniformity? Cleaner for generic consumers; awkward for `status`/`config show`.
   Leaning object-or-list with `meta.count` present only for collections.
4. **Does `profile` belong in the envelope for profile-less commands** (`status`,
   `lock`, `update`, `config`)? Leaning: omit the key entirely rather than emit
   `null`, so presence is meaningful.
