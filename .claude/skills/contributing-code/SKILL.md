---
name: contributing-code
description: Guidelines for agents making code or architecture changes to the canair codebase (CLI subcommands, transports, modes, library code, tests, releases). Load this whenever you are adding, refactoring, or removing canair Python code — NOT for contributing a vehicle profile/PIDs upstream (use contributing-profiles) or reverse-engineering signals (use ioniq-reverse-engineering / reverse-engineer-signal).
---

# Contributing code to canair

Guidelines for *engineering* canair itself (the `src/canair/` package: CLI, transports, modes,
library, tests). Related skills: **`contributing-profiles`** if your change is profile *data* rather
than Python; **`reverse-engineer-signal`** / **`ioniq-reverse-engineering`** for the RE workflow
itself, and **`cli-tui-standards`** for how output, prompts and TUIs must look and behave.

Flags and subcommand lists are authoritative in `canair <cmd> --help` and the generated
`docs/reference/cli/`; read them there rather than trusting any list.

## First principle — describe intent, not code that will drift

Snippets, signatures, and name lists go stale silently, and an agent that trusts them builds on a
false premise. Point at the file to read *now* ("the guard is in `src/canair/safety/blocklist.py`")
instead of pasting its contents; treat every specific below as a pointer to verify against the
tree, and fix it if it has drifted (Boy Scout). This applies to this skill too — keep it
intent-level.

## Non-negotiables

0. **Good design and architecture are CRUCIAL — long-term maintainability outranks short-term
   expedience.** Every change should leave the architecture *clearer*: right seams, separated
   concerns, single-purpose modules, no god objects, no leaky coupling across layers. When a quick
   hack and a clean shape diverge, choose the clean shape — or surface the tradeoff to the user
   rather than quietly accruing debt. Every rule below is an *instance* of this one; uphold the
   principle even where no rule spells out your case.
1. **canair is a CLI built for both human *and* agentic use.** Every capability is a composable,
   scriptable subcommand. New features must work non-interactively (a flag escape hatch for any
   prompt, e.g. `--yes`) and offer `--json` where a result is consumed programmatically. Never add a
   capability reachable *only* through an interactive TUI.
2. **Every transport must always work**, and **`slcan-tcp` is the canonical default** (it runs on
   both the WiCAN Pro and the classic). Don't reintroduce a transport-specific default or code path.
   See Transports (`.claude/skills/contributing-code/architecture.md`).
3. **Never break the real car.** No UDS programming session (`10 02`), no firmware/write/upload
   services. The blocklist in `src/canair/safety/blocklist.py` exists for this — extend it, never
   bypass it. Test against fakes or the loopback emulator; the approval policy for the car is in
   `AGENTS.md` → The car.
4. **Tests pass and cover the change** (see Test coverage,
   `.claude/skills/contributing-code/tests-refactor-style.md`).
5. **Two data domains, one tool.** canair analyzes diagnostic request/response traffic *and* raw
   broadcast CAN frames; treat frames as first-class and the WiCAN as a replaceable transport.
6. **Never commit personally identifiable or location data** — see No PII
   (`.claude/skills/contributing-code/safety-pii-keys.md`).
7. **Never share ECU security keys, seeds, or unlock algorithms** — see Never share security keys
   (`.claude/skills/contributing-code/safety-pii-keys.md`).

## Where to read next

| When you are...                                                                                              | Read                                                       |
|--------------------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| Touching transports, WiCAN access, raw CAN frames, or placing/moving code between layers                     | `.claude/skills/contributing-code/architecture.md`         |
| Adding a CLI subcommand, ECU/PID selectors, time/scope flags, or any mutative operation                      | `.claude/skills/contributing-code/cli-conventions.md`      |
| Changing generated files, schemas, or anything with a `--check` currency gate                                | `.claude/skills/contributing-code/data-artifacts.md`       |
| Handling captures, logs, profiles, fixtures or anything that could carry PII, location, or ECU security keys | `.claude/skills/contributing-code/safety-pii-keys.md`      |
| Writing tests, splitting a large module, or checking code style                                              | `.claude/skills/contributing-code/tests-refactor-style.md` |
| Updating docs/README, writing a commit message, committing alongside other agents, or releasing              | `.claude/skills/contributing-code/docs-commits-release.md` |
| About to declare the work done (commands to run, local gate setup)                                           | `.claude/skills/contributing-code/finish-checklist.md`     |
