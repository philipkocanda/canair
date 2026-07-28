# Contributor profile resolution: docs + a staleness guardrail

## The question that prompted this

> When contributing to canair and running it as `uv run canair`, is it possible
> to configure it (not just a one-time flag) to still look in the git repo for
> profiles? How are the ergonomics for contributors in general?

## The finding: it already works — but the *why* is subtle and undocumented

Profile discovery (`canlib/profile.py::profiles_roots`, highest precedence first):

1. `--profiles-dir` flag (one-time)
2. `$CANAIR_PROFILES_DIR` env var
3. `profiles_dir:` in `~/.config/canair/config.yaml` (persistent config)
4. `~/.config/canair/profiles/` (user profiles, uncommitted — shadow bundled by name)
5. **`BUNDLED_PROFILES_DIR`** — the repo-bundled `profiles/`

The last root is defined in `canlib/constants.py:15` as `SCRIPT_DIR / "profiles"`
where `SCRIPT_DIR = Path(__file__).parent.parent` — i.e. **relative to the
location of the running `canlib` package**, NOT the current working directory.

Consequence — the answer hinges on *which copy of the code runs*, not on any
config flag:

| Invocation | Code that runs | `BUNDLED_PROFILES_DIR` resolves to |
|---|---|---|
| `uv run canair` (repo root) | working-tree source | `<repo>/profiles` ✅ the git repo |
| bare `canair` (`uv tool install .`) | venv snapshot copy | `~/.local/share/uv/tools/canair/lib/python*/site-packages/profiles` ❌ stale copy |

So **`uv run canair` from the repo root already looks in the git repo's
`profiles/`** — for free, no configuration needed — because it runs the source
tree. This is exactly why `AGENTS.md` mandates `uv run canair` for contributors:
edits to `profiles/ioniq-2017/ecus/*.yaml` are live under `uv run`, but a bare
`canair` reads a frozen copy physically baked into the tool venv on install.

"Configure it persistently" therefore has two different meanings:

- **Data:** to make even a *bare* `canair` (or a `uv run` from another directory)
  see the repo profiles, set `profiles_dir: <repo>/profiles` in the user config
  (`canair config set profiles_dir …`) — the persistent equivalent of the
  one-time `--profiles-dir`. But this only adds a discovery *root*; a bare
  `canair` still runs stale *code*.
- **Code + data:** run `uv run canair` from the repo root. Nothing to configure.

## Ergonomics assessment

**Good:**
- `uv run canair` gives live-editing of both code and the bundled profile, zero setup.
- Three persistence layers (`profiles_dir` config, `$CANAIR_PROFILES_DIR`, user
  `~/.config/canair/profiles/` shadowing) plus the one-time `--profiles-dir` flag.
- User profiles shadow bundled ones by name — override without touching the repo.
- `canair update` already reports install context and warns on out-of-sync
  snapshot vs source clone (`canlib/install_context.py`).

**Rough edges (footguns):**
1. **The `uv run` vs bare `canair` distinction is a silent trap.** Nothing at
   runtime tells a human contributor a bare `canair` reads a *frozen* copy of
   `profiles/`. Only documented in `AGENTS.md` (agent-facing), not human `docs/`.
2. **No "use my checkout" one-liner** for contributors who prefer bare `canair`,
   and even then the *code* is stale.
3. The rule — `BUNDLED_PROFILES_DIR` follows the *code*, not the CWD — is subtle
   and absent from human-facing `docs/`.

## Plan

Resolution logic is already correct; this is **documentation + a small
ergonomic guardrail**, not a behavior change.

### 1. Docs (primary, low-risk)

- Add a "How profiles resolve during development" note to the contributor-facing
  docs (the `contributing` skill and/or a `docs/` page), covering:
  - `uv run canair` (repo root) ⇒ repo `profiles/` is live, for free.
  - The bare-`canair` staleness trap (frozen snapshot; needs reinstall).
  - The persistent `profiles_dir` option for data (with the code-staleness caveat).
- Respect the README ↔ `docs/` split — keep README lean; detail lives in `docs/`.
- Verify internal doc links resolve.

### 2. Guardrail (optional, confirm scope)

- Surface install context in a human-facing spot using the existing
  `canlib/install_context.py`: have `canair profile list` and/or `canair status`
  note when the running code is a `uv tool install` snapshot rather than the
  checkout — e.g. "running the installed snapshot, not a source checkout;
  edits to repo `profiles/` won't be picked up until reinstall."
- No change to `profiles_roots` / `resolve_profile`.

### Out of scope

- Changing profile-resolution precedence or making CWD a discovery root
  (would surprise users running from arbitrary directories).

## Open questions (confirm before implementing)

- Docs-only, or docs + the `profile list`/`status` guardrail?
- Is the ask about the contributor's *own* extra profiles being picked up
  alongside the repo, or specifically the *bundled* profiles being live during
  development? (Changes emphasis of the docs note.)
