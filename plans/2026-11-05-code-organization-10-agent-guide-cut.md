# Code organization, part 10: cut the agent guide

Status: **PROPOSED** (2026-11-05). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plans 5,
6, 7 and 9, which move most of the code that `AGENTS.md` names. This is left-over item 5 of
`plans/2026-11-04-package-layout-and-layering.md`.

## Goal

`AGENTS.md` holds what an agent needs in every session: the rules, a short map of the commands,
and a short map of the code. Design detail moves next to the code it describes, where an agent
reads it when it changes that code.

## Findings (at `0ce4f742`)

- `AGENTS.md` is **about 1,370 lines**, with **187 `src/canair/…` path references**. Its header
  says it carries "the rules, a one-line-per-command map, and the code/data map — not a flag
  reference and not a workflow guide".
- Lines per section:

  | Section | Lines |
  |---|---:|
  | Running the CLI, formatting, tooling, vocabulary, Boy Scout, code structure, docs | 133 |
  | Tools: intro | 24 |
  | Tools: live device | 167 |
  | Tools: analysis | 118 |
  | Tools: authoring | 251 |
  | Tools: import, export and sharing | 91 |
  | Tools: profiles | 109 |
  | Key files | 189 |
  | WiCAN access | 181 |
  | Transports | 90 |

- Many "one-line" command entries are 10 to 40 lines. They describe how the code works: the
  frame-count policy (four rules and a ledger), the reconnect and desync mechanisms, the
  variant and revision resolution, the layered-profile read and write rules, the contribute
  pipeline, the DTC log's storage. That text is accurate and useful, but an agent needs it only
  when it changes that code.
- The text repeats the module docstrings. For example, the frame-count rules are in
  `AGENTS.md`, in `transport/elm327/frame_count.py` and in a plan.
- Sub-plan 1 found six stale module names. Every move in sub-plans 3 to 9 adds more paths to
  correct, unless the paths leave `AGENTS.md` first.

## What stays

A fact stays in `AGENTS.md` if one of these is true:

1. An agent needs it before it knows which file to open. Examples: run `uv run canair` from
   the repo root, pass `--profile` on every write, never hand-write `captures/` or `signals/`.
2. It prevents a mistake that no gate catches. Examples: bind a PID with a colon
   (`IGPM:22BC07`), `--save` on every device read, `escape` never quits a TUI.
3. It is the map: one or two lines per command (what it is for), and one line per package
   (pointing at the generated architecture page).

Everything else moves to one of these homes:

| Kind of text | New home |
|---|---|
| How one module works | that module's docstring |
| How one package works, or a rule for its code | that package's charter, or a "Design" section in its `__init__.py` docstring |
| A design that spans packages (frame counts, reconnect and desync, variants and revisions, layered profiles) | a page in `docs/development/`, linked from `AGENTS.md` in one line |
| Device and firmware ground truth | the wican-hardware-and-protocol skill |
| A workflow | the skill that owns it |
| Flags and defaults | `--help` and `docs/reference/cli/` (already generated) |

Before deleting a paragraph, check that its new home says the same thing. If the home already
has the text, delete the copy. If the two disagree, find out which one is true from the code.

## Steps

1. **Measure and mark.** For each paragraph, record its kind and its new home in a work list
   (in this plan's status line or a scratch file that is not committed).
2. **Write the development pages.** One page for each design that spans packages:
   `docs/development/frame-counts.md`, `docs/development/link-health.md` (reconnect, desync,
   link latency), `docs/development/variants-and-revisions.md`,
   `docs/development/layered-profiles.md`. Add them to `mkdocs.yml`. `mkdocs --strict` must
   pass.
3. **Move the module-level text** into docstrings, one package per commit. A docstring that
   grows past what a reader wants at the top of a file is a sign that the text belongs on a
   development page instead.
4. **Rewrite `AGENTS.md`** to the shape of "What stays". Each command entry has at most three
   lines. Each design has one line with a link.
5. **Add a ratchet.** Add `agents_md_lines` and `agents_md_code_paths` to `baseline.json` as
   ceilings, with a test in `tests/contracts/`. They stop the guide from growing back without a
   decision.
6. **Check the skills.** The contributing-code and reverse-engineer-signal skills point at
   `AGENTS.md` sections by name ("`AGENTS.md` → Transports"). Update every such pointer.

## Acceptance

- `AGENTS.md` is under 450 lines, with fewer than 60 `src/canair` paths.
- `scripts/check/doc_paths.py` (sub-plan 1) reports nothing.
- `mkdocs --strict` passes, and every internal link resolves.
- No fact is lost: each deleted paragraph is in the work list of step 1, with its new home.

## Risks

- **Agents read less.** Text in a docstring is read only by an agent that opens the file. This
  is the intended trade, but it can hide a rule that matters before the file is opened. The
  "What stays" test is the guard. When in doubt, keep one line in `AGENTS.md` that names the
  rule and points at its home.
- **Concurrent edits.** Other sessions edit `AGENTS.md` often (the cli-tui-standards work changed
  it on 2026-11-05). Do step 4 in one short session on a quiet tree, and rebase the work list
  onto the latest text first.
