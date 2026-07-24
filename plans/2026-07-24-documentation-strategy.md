# Plan: canair documentation strategy

## Goal

Make canair genuinely welcoming to the two audiences it should optimize for
(decided 2026-07-24):

1. **The new *user* with a different car** (**priority 1**) — owns a vehicle that
   *isn't* the bundled Ioniq and wants to go from a fresh clone to a working
   profile of their own: discover ECUs, capture, decode, share. Today the
   README's "Getting started" gets you reading the *bundled Ioniq* PIDs, then
   drops off a cliff. The "create your own profile" path (`profile create` →
   `discover --register` → `identity` → `scan` → capture → decode → verify →
   share) is never told as one continuous story.
2. **The *PID/profile contributor*** (**priority 1**) — someone (often the same
   person) reverse-engineering signals and *contributing PID definitions* back:
   the **data path** (capture → decode/correlate/hunt → `pids upsert-param` →
   coverage → verify → share a profile). This is *not* changing canair's own
   Python.

**Deprioritized:** the **tool-code contributor** (changing `canlib/`). Their
guidance already exists in `.claude/skills/contributing/SKILL.md`; a human-facing
`CONTRIBUTING.md` is nice-to-have but explicitly **later**, after the two
priority-1 audiences are served.

**Skills stay as-is** — AGENTS.md and `.claude/skills/` remain the canonical
agent-facing surface; `docs/` is the human-facing rendering and references them
rather than replacing them.

This plan does **not** write all the docs yet. It decides *what* docs exist,
*where* they live, *what each covers*, and *in what order* to build them.

---

## What we have today (inventory)

- **`README.md`** (~250 lines) — dense and accurate, but doing four jobs at once:
  marketing/overview, architecture (mermaid + protocols), full command
  reference table, and getting-started. It's a good *landing page* but a poor
  *manual*: no task-oriented walkthroughs, no per-command deep dives, no
  new-vehicle end-to-end.
- **`AGENTS.md`** (~86 lines) — the canonical, exhaustive tool reference, but
  written *for agents* (terse, assumes `uv run canair`, repo-internal paths). Not
  something to point a human at.
- **`.claude/skills/`** — three RE skills + a `contributing` skill. Rich, but
  agent-facing and not discoverable by a human.
- **`plans/`** — design/implementation plans (git-tracked). Historical/decision
  record, not user docs.
- **local research notes** — freeform, un-tracked scratch space, not published docs.
- **`CHANGELOG.md`, `RELEASING.md`, `LICENSE`** — present and fine.
- **No `CONTRIBUTING.md`, no `docs/` site, no per-command reference.**

Gap summary: everything exists *somewhere*, but nothing is arranged as a
progressive, human-facing manual, and the new-vehicle journey is untold.

---

## README fixes (do these regardless of the docs site)

Findings from reviewing the README against the new-contributor lens. These are
small, high-value, and independent of the bigger docs effort:

1. **Add a "Bring your own car" section.** The single biggest gap. After
   "Getting started" (which reads the bundled Ioniq), add a short section that
   *names* the end-to-end for a new vehicle and links to the (future) full
   walkthrough:
   `profile create` → `discover --register` → `identity` → `scan` (seeds
   `research:`) → capture while driving/charging → `decode`/`correlate`/`hunt` →
   `pids upsert-param` → `verify` → `wican autopid write` → share. Right now
   `canair profile create` is mentioned twice almost in passing; the *workflow*
   it unlocks is never sketched.
2. **Split "Getting started" intent.** Make explicit that steps 1–4 get you
   reading the *example* Ioniq, and that a *different* car needs the
   bring-your-own-car flow. New users on a non-Ioniq currently follow steps that
   query Ioniq ECU names (`BMS:2101`) that may not match their car.
3. **Add a "Contributing" pointer.** One line linking to a real
   `CONTRIBUTING.md` (to be written) so the human-facing dev story exists on
   GitHub, distinct from the agent skill.
4. **Trim the command table's prose** or move the deep per-flag descriptions to
   a reference doc, keeping the README table to one crisp line per command. The
   `decode`/`correlate` rows are paragraph-length; that density belongs in a
   command-reference page, not the landing table.
5. **Tone pass for welcome.** Add an explicit "you don't need to be a CAN expert
   to start" note and a "safe by default (reads are free; mutations confirm)"
   reassurance near the top of getting-started, since the only current framing of
   risk is the scary (correct, keep it) Warning at the very bottom.

Keep: the mermaid diagram, the Pro-vs-classic clarity, the protocol note, the
Warning, the license. These are strengths.

---

## Target documentation architecture

Un-ignore a **real, git-tracked `docs/`** (separate from the un-tracked local
research scratch). Structure it task-first, not command-first. Proposed
layout (Markdown; can back a static site later — see "Tooling"):

```
docs/
  index.md                     # what canair is, who it's for, pick-your-path
  getting-started/
    install.md                 # uv, clone, install, tab-completion
    connect-device.md          # WiCAN Pro vs classic, AP vs LAN, config, status
    first-read.md              # read the bundled Ioniq (the 5-min win)
  bring-your-own-car/          # ★ PRIORITY 1 — the headline new-user journey
    overview.md                # the whole arc, one page, with a flow diagram
    01-create-profile.md
    02-discover-ecus.md        # discover --register
    03-identity.md             # fill ECU metadata
    04-scan.md                 # seed research: leads
    05-capture.md              # --save, labels/states, driving/charging runs
    06-analyze.md              # decode / correlate / hunt / investigate
    07-define-and-verify.md    # ★ pids upsert-param, coverage, verify (PID contributor)
    08-share.md                # ★ wican autopid write, contributing a profile
  concepts/
    architecture.md            # transports, ISO-TP/UDS/KWP2000, the two data domains
    profiles.md                # bundle layout, ecus/ source of truth, discovery/precedence
    query-mini-language.md     # selectors, pipeline steps, sessions/keepalive
    byte-indexing.md           # WiCAN vs ISO-TP vs Torque vs bix
    captures-and-states.md     # capture model, journaling, states.yaml
    safety.md                  # blocklist, confirmations, what canair won't do
  reference/
    cli/                       # one page per subcommand (or generated) — the deep flags
    config.md                  # every config key (fold in config.example.yaml)
    schemas.md                 # ecus/ , captures, states schema shapes
  profiles/
    ioniq-2017.md              # the bundled reference profile — what it decodes, ECUs, IOControl
  # DEFERRED (tool-code contributor — not a priority-1 audience):
  #   contributing/ + repo-root CONTRIBUTING.md — humanize the contributing skill
  #   later. The agent-facing .claude/skills/contributing/SKILL.md already covers it.
```

The two ★ **priority-1** audiences map to: the whole `bring-your-own-car/`
journey (new-car user), with steps 07–08 specifically serving the **PID/profile
contributor** (define → verify → share). `getting-started/` and `concepts/` are
supporting material for both. `reference/` serves both on demand. The tool-code
`contributing/` tree is **deferred**.

Design principles:

- **Task-first over command-first.** The headline is "reverse-engineer *your*
  car," organized as a numbered journey; the exhaustive per-command flags live in
  `reference/` for when you need them.
- **Single source of truth, no drift.** Follow the `contributing` skill's first
  principle: docs describe *intent and where to look*, not pasted code/signatures.
  Where a fact already lives authoritatively (config keys in
  `config.example.yaml`, flags in `--help`, schema in `canlib/schema/`), the docs
  should *derive from or point at* it, ideally generated, rather than duplicate.
- **Humans and agents share the substance, differ in framing.** AGENTS.md and the
  skills stay the agent-facing surface; `docs/` is the human-facing rendering of
  the same knowledge. Decide per-topic which is canonical and have the other
  reference it, to avoid two copies drifting.

---

## Reuse existing material (don't write from scratch)

Much content already exists and can be adapted rather than invented:

| New doc | Source to adapt |
|---|---|
| `bring-your-own-car/*` | `plans/2026-07-21-new-profile-bootstrap.md` + the RE skills |
| `concepts/architecture.md` | README "How it connects" + `contributing` skill Transports section |
| `concepts/query-mini-language.md` | README "Query mini-language" section |
| `concepts/byte-indexing.md` | `canair bix --table` + local byte-index research notes |
| `concepts/captures-and-states.md` | AGENTS.md captures/states notes |
| `reference/cli/*` | `canair <cmd> --help` (generate) + AGENTS.md command notes |
| `reference/config.md` | `config.example.yaml` |

(The tool-code `contributing/*` reuse of the `contributing` skill is deferred.)

---

## Tooling decision — plain Markdown now, MkDocs later (decided 2026-07-24)

**Now:** write everything as **plain Markdown in `docs/`**. It renders on GitHub,
needs no build step, and the exact same files feed a site later — so this is not
a fork in the road, just a rendering choice deferred.

**Later (when the priority-1 journey + PID-contributor pages exist):** stand up
**MkDocs + Material**. Rationale for choosing it over the alternatives:

- **Full-text search** across pages — high value for a reference-heavy CLI.
- **Persistent sidebar nav + next/prev** — turns the numbered
  `bring-your-own-car/` steps into a guided flow, exactly the hand-holding a
  new-car user needs.
- **Publishes free to GitHub Pages** via one CI step — a linkable URL, not a
  GitHub file tree.
- **Python-native** (installs via `uv`; no Node), renders the README's mermaid
  diagram, has admonition callouts (good for safety warnings) and code-copy
  buttons.
- Rejected: **Docusaurus** (Node, heavier, overkill without versioning/blog);
  **bare GitHub Pages** (weakest nav/search).

Do **not** stand up MkDocs on an empty skeleton — add it once there's enough
content that navigation/search pays off.

**Auto-generation to fight drift:** a small script that renders `canair <cmd>
--help` into `reference/cli/*.md` (checked in CI to stay current) would keep the
command reference from rotting — mirrors how the `contributing` skill warns
against pasting code that drifts.

---

## Proposed build order

1. **README fixes** (§"README fixes") — small, immediate, ship first.
2. **`docs/` skeleton** — create the tree with `index.md` + one-line stubs so the
   structure is reviewable before prose is written.
3. **Bring-your-own-car journey** (priority 1) — the headline; adapt the
   bootstrap plan + RE skills into the numbered walkthrough. Steps 07–08 (define
   → verify → share) carry the **PID-contributor** story.
4. **Concepts pages** — architecture, profiles, query language, byte indexing —
   as the journey references them.
5. **Reference** — CLI (generated), config, schemas.
6. **MkDocs** — once 3–5 have real content: wire MkDocs + Material, GitHub Pages
   publish, and the `--help` generation CI check.
7. **(Deferred) tool-code `CONTRIBUTING.md` + `contributing/`** — humanize the
   contributing skill for human devs, only after the above.

---

## Resolved decisions (2026-07-24)

- **Audience priority:** optimize for **new-car users** and **PID/profile
  contributors** (both priority 1). Tool-code contributors **deprioritized**.
- **Rendered site:** **plain GitHub Markdown now, MkDocs later** (see Tooling).
- **Canonical-source policy:** prefer **generating** the CLI reference (from
  `--help`) and config docs (from `config.example.yaml`) to fight drift.
- **Skills vs docs:** **keep** AGENTS.md/skills as the canonical agent-facing
  source; `docs/` references them rather than duplicating.

---

## Friction found while dogfooding the journey (2026-07-24)

Walking the `bring-your-own-car` flow end-to-end to create a blank
`profiles/ioniq-5-2022/` (one CLU ECU, no PIDs) surfaced tool gaps — **now
fixed** (2026-07-24):

1. **✅ FIXED — `canair ecu add`.** Added an offline ECU-registration command
   (`canair ecu add TX [--name --description --id-protocol --notes --overwrite
   --dir]`), a validated wrapper over `canlib.ecus_edit.register_ecu` and the
   counterpart to `discover --register` for blank/contributable profiles. `ecu`
   became a command group (`show` default + `add`), mirroring `scan`. Docs
   (`02-discover-ecus.md`) now show the command instead of the Python workaround.
2. **✅ FIXED — validation now scopes to the file's own profile.** ECU-file
   validation derives the vehicle-state vocabulary from the file's own profile
   (its `<root>/states.yaml`) instead of the globally-*active* profile, so writes
   to a non-active profile succeed even with several profiles discoverable (no
   spurious "Multiple profiles found"). A `tests/conftest.py` now pins
   `ioniq-2017` for the suite since the repo bundles >1 profile.
3. **✅ ADDRESSED — first-run profile chooser.** On a genuine first interactive
   run needing a profile, canair now offers to pick a discovered profile or
   create a new one, with explicit path messaging (`canlib/first_run.py`). It
   never fires when scripted/piped or when `--profile`/`CANAIR_PROFILE` is given.
   `profile create`'s logic was factored into a reusable `create_profile()`.

All shipped with tests (`test_ecu_add.py`, `test_first_run.py`) and green
`pytest`/`ruff`/`ty`.

---

## Status update (2026-07-24, later)

Beyond the initial docs pass, the following shipped:

- **CLI reference is now generated** from `--help` (`scripts/gen_cli_reference.py`
  → `docs/reference/cli/*.md`), with a `--check` gate in CI. No more hand-written
  per-command flag docs to drift.
- **MkDocs Material stood up** (`mkdocs.yml`, `docs` dependency group). Nav covers
  getting-started → bring-your-own-car → concepts → reference (incl. generated
  CLI pages) → bundled profile → contributing. CI builds `--strict`; a
  `.github/workflows/docs.yml` deploys to GitHub Pages
  (`philipkocanda.github.io/canair`). *(Requires enabling Pages in repo settings,
  source = GitHub Actions.)*
- **Contributing docs are no longer deferred.** Reframed around the priority-1
  **profile/PID contributor**: repo-root `CONTRIBUTING.md` (GitHub-surfaced) +
  `docs/contributing/index.md`, both leading with "contribute a profile/PIDs,"
  with code-contribution as a secondary section pointing at the contributing
  skill. Contribution encouragement is now repeated across README, docs index,
  the capture step, define-&-verify, and the Share page.
- **`reverse-engineer-pid` skill made explicitly generic** (vehicle- and
  PID-agnostic, whole-flow), with the `ioniq-reverse-engineering` skill reframed
  as the Ioniq-specific *data/context* companion.

Remaining open: `docs/reference/schemas.md` (planned, unwritten); a doc-numbers
freshness check for the bundled-profile stats; the emoji/anchor housekeeping is
done.
