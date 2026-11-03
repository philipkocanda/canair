# One edit core and a router for the YAML authoring commands

Status: **DONE except phase 5** (2026-11-03, branch `refactor/yaml-edit`). The sections below are
the plan as written before the work; "As built" says where the code differs.

## As built

Decisions taken after the plan was written:

- **Everything lives in `canlib/yaml_edit/`**, not only the new core: the editors that used to sit
  in `pids_edit/`, `ecus/`, `states/`, `variants/`, `actions/` and `signals_edit.py`, plus
  `edit_echo.py` and `yaml_rt.py` (now `yaml_edit/echo.py` and `yaml_edit/rt.py`). The old paths
  are gone, with no shims. The package docstring is the map.
- **Phase 5 (`Transaction`) was skipped.** It would add a multi-file guarantee the code never had
  (the four hand-built rollbacks differ), so it is a feature, not a de-duplication. The four
  callers keep their own rollback but now write through `write_atomic` and `restore`.
- **The sequence comment repair was generalized**: `ruamel_ops.remove_item` and
  `insert_item_before_last` replace the `states` copies, and `remove_key` / `rename_key` /
  `place_in_order` / `place_after` cover mappings.
- **The router is `yaml_edit/targets.py`** (a registry of `Target`s plus `open_target`). Each
  `Target` is declared beside its rules in the editor module; a test fails if one is unlisted.
- **`--no-validate` is a context switch**, `commit.schema_validation(enabled)`, set by
  `commands/_edit.py::edit_scope`, instead of a `validate` argument on every editor.
- **Error handling is central**: `cli.py` catches `EditError` once (`  Error: …`, exit 1).
  `ActionError`, `StatePredicateError`, `StateRenameError` and `HexArgError` are `EditError`s.
- **The text engine writes through `commit_text` too.** The scanner-hit editors (`hits.py`) were
  unchecked before; they are now re-parsed and reverted like every other edit.
- **Found while building (latent bugs, fixed):** deleting the first entry of a mapping left its
  comment on the next entry; renaming a key (group, variant, revision) dropped every comment
  between entries; removing any entry whose predecessor ended in an inline comment lost the next
  entry's comment. `tests/test_yaml_edit_ruamel_ops.py` pins all of it.

Verification: every editing command (130 invocations, all engines) was run on a copy of the Ioniq
profile before and after. The final files are byte-identical except `groups.yaml`, where the
first-entry delete now keeps the right comments; command output differs only in the error text.


Scope decisions (confirmed with the owner):

- **Internal refactor only.** Every subcommand keeps its name, flags and `--help` text. No new
  user-facing verb.
- **Keep both edit engines** (text-line surgery and ruamel round-trip). Unify the layers around
  them, not the engines.
- **Unify error output.** One format and one exit code for every edit failure.

## Problem

Nine commands write YAML in a profile: `pids`, `states`, `groups`, `signals`, `variants`,
`action`, `ecu`, `ecu revisions` and the library callers (`discover`, `response_frames`). Each
one grew its own copy of the same machinery. The copies have drifted apart.

### What is duplicated

| Machinery | Copies | Where |
|---|---|---|
| Write, re-parse, check, restore on failure | 7 | `groups_edit`, `actions/edit`, `states/edit`, `variants/edit` (`_write`), `signals_edit`, `ecus/edit`, `pids_edit/_text` |
| `_load_doc` (scaffold an empty doc, check the shape) | 5 | `groups_edit`, `actions/edit`, `states/edit`, `variants/edit`, `ecus/edit` |
| `_xxx_path(profile)` resolver | 5 | same modules |
| `_find` (match a key after normalising it) | 6 | same, plus `revisions_edit` |
| `_flow_seq` / `_flow_list` / `_flow_map` / `_flow_states` | 6 | `actions`, `variants`, `revisions_edit`, `states`, `ecus/edit`, `groups_edit` |
| Rebuild a mapping to rename one key, keeping order | 6 | `groups_edit`, `variants/edit` (×2), `ecus/edit`, `revisions_edit` (×2) |
| Insert a key at its canonical position | 4 | `signals_edit._set_field`, `variants._place`, `revisions_edit._insert_after`, `states._put_relation` |
| "ECU → `pids:` → PID" locate chain in the text engine | 15 | `pids_edit/*` (27 `_find_ecu_block` calls, 27 `def checker`) |
| "Write a scalar straight after the PID header" | 7 | `status`, `variable_length`, `response_frames`, `period`, `sensitive`, `support_map`, `snapshot_only` |
| `_run_edit` (writable check → call → catch → echo) | 5 | `commands/{groups,states,variants,action,ecu_revisions}.py`, plus `pids._guarded` |
| `require_writable_definitions()` call | 17 | every authoring command |
| Per-command `except XxxEditError → SystemExit(red Error)` | 21 | `commands/*` |
| `--dir` / `--no-validate` arguments | 29 | `commands/pids.py::_add_common` |
| `_xxx_func` sub-command dispatch plus `run` | 6 | one per command module |

Counts come from `grep` over `canlib/`; they are close, not exact.

### What drifted

- **Seven unrelated error classes**: `GroupsEditError`, `StatesEditError`, `VariantsEditError`,
  `SignalsEditError`, `EcusEditError`, `PidsEditError`, `ActionError` (plus
  `StatePredicateError`). Code that crosses two engines catches two (`pids._gate_new_signal`,
  `_upsert_revision_override`, `cmd_set_revisions`).
- **Four error renderings** for the same event:
  - red `  Error: msg` through `SystemExit` (`groups`, `states`, `variants`, `action`, `pids`);
  - red text to stderr, `return 1` (`ecu_revisions`; `ecu add`/`rename` without the prefix);
  - plain `signals upsert: msg`, `return 1` (`signals`);
  - bare `print(str(e))` (`HexArgError`).
- **`--no-validate` is silently ignored** by `pids set-addressing`. It parses the flag through
  `_add_common` and never reads it. The ruamel ECU editors always validate.
- **`pids._guarded` reverts with a bare `write_text`.** It neither clears the definitions cache
  nor keeps CRLF line endings, which `pids_edit._text._safe_write` does.
- **Sequence indent is detected in four editors and ignored in two.** `variants.yaml` and
  `signals/*.yaml` are always re-dumped as `2/0`, so a hand-written `4/2` file reflows on its
  first edit.
- **Only the text engine keeps CRLF.** The ruamel editors write `\n`.
- **No crash safety on most writes.** Only `states/rename.py::_write_atomic` writes through a
  temp file. The rest open the target with `"w"`, which truncates it first.
- **Multi-file rollback is hand-built four times** with different guarantees:
  `revisions_edit._write_all`, `variants/references.py::retag_history`,
  `variants/references.py::rename_everywhere`, `states/rename.py::apply_state_rename`.
- **A message side channel**: `groups` and `states` stash the confirmation text on
  `args._groups_msg` / `args._states_msg` so `_run_edit` can read it back.
- **Stale references**: the `contributing-code` skill names `canlib/ecus_edit.py`, which is now
  `canlib/ecus/edit.py`. `commands/groups.py` has a comment ("ANSI colors") above a `TypedDict`.

### A latent bug the refactor will fix

Deleting the **first** entry of a mapping in ruamel leaves that entry's leading comment behind,
attached to the next entry. Probe (run on 2026-11-03 with `canlib.yaml_rt`):

```text
groups:                       groups:
  # about alpha                 # about alpha        <- orphaned
  alpha: ...        del alpha   # about bravo
  # about bravo      ----->     bravo: ...
  bravo: ...
```

Deleting a middle or last entry is correct. `states/edit.py::_delete_state_entry` already works
around this for sequences. `groups rm`, `action rm`, `variants rm` and `ecu revisions rm` all do
a bare `del` and have the bug. One shared `remove_key` fixes all four.

## Goals

1. One place owns "open a file section, change it, check it, write it, roll back".
2. One router maps an edit target (groups, states, a bus, an ECU, a PID) to its file and section.
3. The command layer has one edit pipeline: writable check, run, one error format, one echo.
4. Domain editors shrink to what is domain-specific: field order, name rules, post-edit checks.
5. No change to bytes written for any edit that works today, except the intentional changes
   listed under "Behavior changes".

## Non-goals

- No new subcommand and no new flag. `docs/reference/cli/` must not change
  (`scripts/gen_cli_reference.py --check` stays green).
- No move of the ECU files to one engine. The ruamel engine applies one sequence-indent style per
  file and can reflow hand-written notes; the text engine does not. See "Engine boundary".
- No table-driven argparse. The `pids` parser is 700 lines of declarations. Generating it risks
  `--help` drift for little gain. Only command *bodies* and the shared arguments are in scope.
- No rename of `param`/`parameters:` identifiers (separate plan:
  `plans/2026-08-06-signal-naming-convention.md`).
- No new editors for `profile.yaml` or `can_buses.yaml`. The router makes them cheap to add later.

## Design

### Layers

```text
commands/_edit.py        command pipeline: run_edit, shared args, dispatch      (CLI layer)
        │
yaml_edit/ router        TARGETS registry + edit_doc / edit_ecu_text             (routing)
        │
yaml_edit/ core          Doc, commit, Transaction, ruamel_ops, EditError          (mechanics)
        │
domain editors           groups_edit, states/edit, variants/edit, signals_edit,   (rules)
                         actions/edit, ecus/edit, revisions_edit, pids_edit/*
```

Module placement: a new package `canlib/yaml_edit/`. It sits next to `yaml_rt.py` (formatting)
and `yaml_io.py` (reading). It imports neither commands nor domain modules, so the dependency
points one way only.

```text
canlib/yaml_edit/
  __init__.py     re-exports the public names
  errors.py       EditError
  ruamel_ops.py   pure helpers on CommentedMap / CommentedSeq
  commit.py       commit_text, restore, atomic write, newline and indent rules
  doc.py          Target, Doc, edit_doc (the ruamel engine entry point)
  text.py         edit_ecu_text, Block, locate_pid, set_pid_scalar (text engine harness)
  targets.py      TARGETS registry and the ECU-file resolver
  transaction.py  Transaction (multi-file, all or nothing)
```

### `errors.py`

```python
class EditError(Exception):
    """An edit was rejected: bad input, a missing target, or a failed post-check."""
```

`GroupsEditError`, `StatesEditError`, `VariantsEditError`, `SignalsEditError`, `EcusEditError`
and `PidsEditError` become subclasses of `EditError`. They keep their names and import paths, so
tests and library callers do not change. Two classes stay out of the hierarchy because loaders
raise them too: `ActionError` and `StatePredicateError`. The domain editor converts them at its
boundary (`raise ActionsEditError(...) from None`, or the existing `ActionError` registered as
the target's error type). The converter is a three-line context manager, `translate(src, dst)`.

### `ruamel_ops.py` — pure helpers, no I/O

| Helper | Replaces |
|---|---|
| `flow_seq(items)`, `flow_map(pairs)` | 6 `_flow_*` copies |
| `find_key(mapping, wanted, norm=str)` | 6 `_find` copies |
| `rename_key(mapping, old, new)` (order-preserving, returns the rebuilt map) | 6 rebuild loops |
| `remove_key(mapping, key)` (re-homes the leading comment) | 4 bare `del`, fixes the bug above |
| `place_in_order(mapping, key, value, order)` (before the first present successor, else last) | `_set_field`, `_place`, `_put_relation` |
| `place_after(mapping, key, value, after)` (after the last present predecessor, else last) | `_insert_after` |

Two placement helpers, not one, because the two rules differ when a file has keys outside the
canonical list. The ECU file (`revisions:` goes after `tx_id`, `identity`, ...) needs
"after predecessors". The signal and variant field lists need "before successors". Each existing
caller maps onto exactly one. `place_in_order` uses "before the first present successor", which
is what `_put_relation` does today. `signals._set_field` and `variants._place` count lower-ranked
keys instead. Both rules agree on an ordered file and differ only on a hand-disordered one, where
the successor rule is the safer one. Pin this in a test before switching.

`states/edit.py` keeps `_insert_before_last`, `_delete_state_entry` and `_as_pre_comment`. They
repair comment slots of *sequence* items, which no other editor needs. If `remove_key` proves
sound, a later step can offer `remove_item` for sequences and drop them from `states`.

### `commit.py` — the one write path

```python
def commit_text(
    path: Path,
    new_text: str,
    original: str | None,
    *,
    check: Callable[[Path], None],      # raises EditError
    error: type[EditError],
    after_write: Callable[[], None] = _noop,
) -> None: ...
```

It does what the seven `_safe_write` copies do, once:

1. Create parent directories.
2. Write `new_text` through a temp file and `os.replace` (the `_write_atomic` rule), keeping the
   file's existing line ending (`_file_newline`).
3. Call `after_write` (cache invalidation).
4. Call `check(path)`.
5. On any failure: restore `original` (or delete a file that did not exist), call `after_write`
   again, re-raise as `error`. A non-`EditError` exception from `check` becomes
   `error("edit failed post-check, reverted: …")`.

The ruamel path dumps to a string first (`yaml_rt.dump` into `StringIO`), then calls
`commit_text`. The text path already holds a string. Both end at the same function.

Sequence indent rule (ruamel path): detect from the original text
(`detect_sequence_indent`), else the target's default.

### `doc.py` — the ruamel engine entry point

```python
@dataclass(frozen=True)
class Target:
    key: str                              # "groups"
    path: Callable[..., Path]             # (profile, **route) -> file
    section: str | None                   # top-level key this target owns; None = whole doc
    kind: type                            # CommentedMap or CommentedSeq, for the section
    error: type[EditError]
    check: Callable[[Any], None]          # parsed document -> raises error
    header: str = ""                      # written when the file is created
    sequence: tuple[int, int] = (4, 2)    # default indent when the file has no sequence
    after_write: Callable[[], None] = _noop

@contextmanager
def edit_doc(target, profile=None, *, create=False, validate=True, **route) -> Iterator[Doc]: ...
```

`Doc` exposes `.path`, `.data`, `.section` (the scaffolded `groups:` map, `states:` list, or an
ECU's own mapping), `.created`, and `.original`. Leaving the `with` block without an exception
commits through `commit_text`. An exception in the body writes nothing. `create=False` raises
`error("no groups.yaml to edit")` for a missing file, which replaces about eight copies of that
message. `validate=False` skips the target's heavy check (see `--no-validate`).

Example, `groups_edit.remove_group` after the change:

```python
def remove_group(name: str, *, profile: Profile | None = None) -> Path:
    name = normalize_name(name)
    with edit_doc(GROUPS, profile) as doc:
        key = find_key(doc.section, name, norm=normalize_group_name)
        if key is None:
            raise GroupsEditError(f"group {name!r} not found")
        remove_key(doc.section, key)
    return doc.path
```

### `targets.py` — the router

One registry says which file and section each target owns. This is the "central router".

| Target | File | Section | Engine | Post-edit check |
|---|---|---|---|---|
| `GROUPS` | `groups.yaml` | `groups:` map | ruamel | structure and selector syntax |
| `ACTIONS` | `actions.yaml` | `actions:` map | ruamel | `parse_actions_doc` |
| `STATES` | `vehicle_states.yaml` | `states:` list | ruamel | names, predicates, relations |
| `VARIANTS` | `variants.yaml` | `variants:` map | ruamel | `check_variants_doc` |
| `SIGNALS` (route `bus=`) | `signals/<bus>.yaml` | `messages:` map | ruamel | `check_signals_doc` |
| `ECU` (route `ecu=`) | `ecus/<name>.yaml` | the ECU's mapping | ruamel | `validate_pids_file` |
| ECU text edit (route `ecu=`) | `ecus/<name>.yaml` | a block inside the ECU | text | re-parse plus a local checker |

Two entry points share the registry and the commit path:

- `edit_doc(TARGET, ...)` for the ruamel engine (above).
- `edit_ecu_text(ecu, transform, checker, *, pids_dir=None, validate=False)` for the text engine.

File lookup already lives in `ecus/files.py` (`find_by_name`, `find_by_tx`). The router adds
one wrapper, `ecu_file(ref, ecus_dir) -> (path, name)`, that returns the file and the canonical
top-level key. It replaces `revisions_edit._ecu_file`, `pids_edit.find_ecu_file` and the five
copies of "load the file, take `data[name]`, check it is a mapping" in `ecus/edit.py`.

Adding a target later (for example `can_buses.yaml`) is one `Target(...)` entry and its rules.

### Engine boundary

The two ECU-file engines stay. The text engine exists because it rewrites only the bytes it
touches (comments, anchors, block-scalar style, hand-chosen order survive). The ruamel engine
exists for structural edits. They now share the file resolver, the commit path and the error
type. They do not share a parser. `pids upsert-param --revision` today runs one edit per engine
and can leave the first applied if the second fails. `Transaction` (phase 5) closes that gap.

### `text.py` — the text engine harness

Today each of the 27 PID-level editors repeats: find the file, read it, upper-case two keys,
define `transform`, define `checker`, call `_safe_write`. The harness removes that.

```python
def edit_ecu_text(ecu, transform, checker, *, pids_dir=None, validate=False) -> Path: ...

@dataclass(frozen=True)
class Block:            # the five-tuple _keyed_block returns, with names
    hdr: int; line_end: int; body: int; end: int; inline: str

def locate_pid(text, ecu, pid_u, *, require_params=False) -> Block: ...   # 15 copies
def locate_param(text, ecu, pid_u, param) -> tuple[Block, Block, Block]: ...  # pid, params, param

def set_pid_scalar(block_text, field, line, *, keep_position=False) -> str: ...  # 7 copies

def pid_checker(pid_u, assert_pid: Callable[[dict], None]) -> Callable[[dict], None]: ...  # ~10
```

`set_pid_scalar` has two modes because today's editors differ and bytes must not move:

- `keep_position=False` (status, variable_length, response_frames, sensitive, support_map,
  snapshot_only): remove the field, insert the new line straight after the PID header.
- `keep_position=True` (period): rewrite in place, so a re-tune diffs as one line.

`_keyed_block` keeps returning a tuple; `Block` is a `NamedTuple`, so existing unpacking in
`research.py`, `hits.py`, `defaults.py` and `categories.py` keeps working. They migrate on their
own schedule.

`defaults._locate_pid` is a one-off copy of `locate_pid` and is deleted.

### `transaction.py` — all or nothing across files

```python
with Transaction(profile) as tx:
    tx.write(path, text)                       # staged
    with tx.edit(VARIANTS, profile) as doc:    # a Doc whose commit is deferred
        ...
    # on exit: write all (atomic each), run each check, roll back all on any failure
```

It replaces `_write_all`, `retag_history`, the manual restores in `rename_everywhere` and
`rename_revision`, and `apply_state_rename`'s loop. Rollback restores every written file
atomically and clears the definitions cache once. ECU files are checked after the write
(`validate_pids_file` reads from disk), which is what `apply_state_rename` does today.

### `commands/_edit.py` — the command pipeline

```python
def run_edit(
    do: Callable[[], Path],
    message: str | Callable[[Path], str],
    *,
    writable: bool = True,         # False when --dir points outside the active profile
) -> int:
    ...
```

- Calls `require_writable_definitions()` once (skipped when the caller passes `--dir`).
- Runs `do()`. It does **not** catch `EditError`.
- Calls `echo_edit(message, path)` (so the install-snapshot note still prints).

`EditError` is caught **once**, in `canlib/cli.py::main`, next to `ProfileError`. It prints
`  Error: <msg>` (red when stdout is a TTY, through `ansi.c`) to stderr and returns 1. This
deletes the 21 per-command `except` blocks and `pids.run`'s wrapper. The exit code is 1 for
every edit failure today, so scripts that test it see no change.

Other shared pieces, all in `commands/_edit.py`:

- `add_edit_args(parser)` replaces `pids._add_common` (`--dir`, `--no-validate`).
- `subcommand_dispatch(attr, default)` replaces the six `run` functions.
- `set_or_clear(value)` returns `"cleared"` or `"set"`; replaces five copies.
- `clear_sentinel(text)` maps `"clear"` to `None` (`period`, `response_frames`).
- `pids._guarded`'s snapshot-validate-revert step becomes `validate=` on the core call, so the
  revert goes through `commit_text` (cache cleared, newline kept).

The `args._groups_msg` and `args._states_msg` side channels go away: `message` is an argument.

## Behavior changes (intentional)

Everything else must be byte-identical. Each item gets a test that pins the new behavior.

1. **Error format and exit code.** Every edit failure prints `  Error: <msg>` to stderr and exits 1
   (`signals` and `ecu`/`ecu revisions` change text, not code). Tests that match on old text are
   updated in phase 6.
2. **`pids set-addressing --no-validate`** now skips validation, as its `--help` says.
3. **`variants.yaml` and `signals/*.yaml`** keep the sequence indent they already use instead of
   being re-dumped as `2/0`.
4. **CRLF files** keep CRLF through the ruamel engine.
5. **First-entry delete** keeps comments with the right entry (`groups`, `action`, `variants`,
   `ecu revisions` removals).
6. **Writes are atomic** (temp file plus replace) for every editor.
7. **`pids._guarded` revert** clears the definitions cache and keeps CRLF.

## Phases

One commit per numbered step, Conventional Commit subjects (`refactor(edit): …`), because
release-please builds the changelog from them. Every step ends green on
`uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`
and `uv run python scripts/gen_cli_reference.py --check`. Commit only the paths you changed
(`git commit -o -- <paths>`); other sessions edit this tree.

### Phase 0 — Pin today's behavior (no refactor)

1. **Coverage audit.** List every public editor function (the `__all__` of `pids_edit`, plus the
   public functions in the other seven modules). Mark which have a byte-exact assertion
   (input text → output text). Existing tests are about 9.9k lines, so expect most are covered.
2. **Golden tests for the gaps.** One small fixture file per engine, one assertion per operation.
   Put them in `tests/test_edit_golden.py` with fixtures under `tests/fixtures/edit/`.
3. **Characterize the known bugs** as `xfail(strict=True)`: first-entry delete orphaning a
   comment, `variants`/`signals` re-indenting a `4/2` file, CRLF through ruamel,
   `set-addressing --no-validate`. Each flips to a normal test in the phase that fixes it.
4. **Differential harness (not committed).** A script that runs a fixed list of
   `(editor, args)` over a copy of a bundled profile and writes each output to `/tmp`. Run it on
   the pre-refactor commit, then after every phase, and `diff -r`. This is the check for "bytes
   did not move".

Done when: the coverage table is in the PR description and the xfails exist.

### Phase 1 — The core (`canlib/yaml_edit/`), no callers yet

1. `errors.py`, `ruamel_ops.py` with unit tests for every helper, including `remove_key` on
   first, middle and last entries, with and without leading comments.
2. `commit.py` with tests for: success, check failure, restore of an existing file, deletion of
   a file that did not exist, CRLF kept, cache hook called on both paths.
3. `doc.py` and `targets.py` (registry and resolver), tested with a throw-away `Target` on a
   `tmp_path` profile.

Done when: the package has its own tests, nothing else imports it.

### Phase 2 — Migrate the whole-file ruamel editors

One module per commit, smallest first. Public function names, signatures and error classes stay.

1. `ecus/groups_edit.py` (271 lines).
2. `actions/edit.py` (185).
3. `variants/edit.py` (431). Adds indent detection; flips its xfail.
4. `states/edit.py` (467). Keeps the sequence comment-slot helpers. `states/rename.py` imports
   `_find`, `_load_doc`, `normalize_name` from it; point those at the new helpers.
5. `signals_edit.py` (353). Adds indent detection; flips its xfail.

Each commit deletes that module's `_load_doc`, `_find`, `_restore`, `_safe_write`, `_path` and
`_flow_*`. Expected: 40–50% fewer lines per module (estimate, not measured).

### Phase 3 — The ECU files, ruamel side

1. `ecus/edit.py`: `_safe_write` and `_load_doc` become `edit_doc(ECU, …)`. The `Unknown-<TX>`
   create path uses `create=True`.
2. `ecus/revisions_edit.py`: `_edit_ecu` becomes a thin wrapper over `edit_doc(ECU, …)`; its
   `_insert_after`/`_flow_*`/`_find` move to `ruamel_ops`.
3. Replace `_ecu_file`, `find_ecu_file` and the repeated `data[name]` mapping checks with
   `ecu_file()` (the lookups themselves stay in `ecus/files.py`).

### Phase 4 — The text engine

1. Add `text.py` (`Block`, `locate_pid`, `locate_param`, `set_pid_scalar`, `pid_checker`,
   `edit_ecu_text`). `_text._safe_write` becomes a call into `commit_text`.
2. Migrate the seven PID-scalar editors (`status`, `variable_length`, `period`,
   `response_frames`, `sensitive`, `support_map`, `snapshot_only`) in one commit. The golden
   tests from phase 0 decide whether it is byte-identical. `set_pid_notes` inserts before
   `parameters:` and stays on its own path.
3. Migrate `params.py` (17 `transform`s), then `research.py`, `defaults.py`, `categories.py`,
   `hits.py`. One commit each. `hits.py` and `research.py` have their own item-span logic and
   may keep more of it.

`params.py` is 1,275 lines. Expect it to drop below 900 once the locate chain and checkers go.

### Phase 5 — Transactions (stretch; stop here if phases 1–4 already satisfied the goals)

1. `transaction.py`, with tests for partial failure at each position (first write, last write,
   check failure) and for cache invalidation.
2. Move `rename_revision`, `rename_everywhere`, `retag_history` and `apply_state_rename` onto it,
   one commit each. They have tests today; those are the safety net.
3. Make `pids upsert-param --revision` (text edit, then gate) one transaction.

### Phase 6 — The command layer

1. `commands/_edit.py` with `run_edit`, `add_edit_args`, `subcommand_dispatch`, `set_or_clear`,
   `clear_sentinel`.
2. `cli.py`: catch `EditError` once. Add a test that drives `main([...])` for one failing edit
   per engine and checks stderr and the exit code.
3. Migrate the command modules: `groups`, `action`, `variants`, `states`, `ecu_revisions`,
   `signals`, `ecu` (add, rename), then `pids`. One commit each. Delete the five `_run_edit`
   copies, `_guarded`, and the 21 `except` blocks. `pids` last: its 29 `cmd_*` bodies shrink to
   a `run_edit(...)` call each.
4. Update the tests that match old error text (`test_pids_edit_cli.py` uses
   `pytest.raises(SystemExit, match=…)` in about eight places; they now expect `EditError` when
   they call a `cmd_*` function directly).

### Phase 7 — Tidy and document

1. Update `AGENTS.md` "Authoring" pointers to the new homes and add one line naming
   `canlib/yaml_edit/` as the place every editor writes through. Do not copy API detail there.
2. Fix the `contributing-code` skill: `canlib/ecus_edit.py` → `canlib/ecus/edit.py`, and state the
   rule "a new editor registers a `Target` and writes through `edit_doc`; it never opens the
   file itself".
3. Boy Scout: delete the stale "ANSI colors" comment in `commands/groups.py`, the
   `# pragma: no cover` dead `except` in `commands/variants.py::cmd_set_revisions`.
4. Add a short `docs/development/` page ("How a profile edit is written") only if the
   `contributing-code` skill alone is not enough. `docs/reference/cli/` does not change.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A refactor changes bytes in a hand-tuned ECU file | Phase 0 differential harness; the text engine keeps its own formatting code; `set_pid_scalar` has an explicit `keep_position` mode |
| `place_in_order` and the old rank-count rule differ on a disordered file | Pin both behaviors in a test first; the new rule matches `_put_relation`, which is already the safest |
| Atomic write breaks a symlinked or read-only file | `os.replace` replaces a symlink with a file; resolve the real path before writing, and test a symlink |
| Cache not cleared on one path | `after_write` lives in `Target`; tests assert the cache is empty after success and after rollback |
| Central `except EditError` hides a stack trace during development | `cli.py` has no debug switch today; the library still raises, so tests and `python -c` see the trace. Add a switch only if this bites |
| Library callers (`discover`, `response_frames`) relied on the old exception types | The old class names stay and now subclass `EditError`; no import changes |
| Large diff blocks review | One module per commit; no commit mixes a move with a behavior change |
| Other sessions edit the same files | Commit only touched paths; re-read a file's diff for foreign hunks before committing |

## Open questions

1. **Traceback switch.** `cli.py` has none, and `ProfileError` has the same limit. Skip it
   unless someone asks.
2. **Symlinked ECU files.** Does any user profile symlink `ecus/*.yaml`? If yes, the atomic write
   must follow the link.
3. **Phase 5 scope.** Take it on now, or leave it after phases 1–4? It is the riskiest step and
   the only one that is not pure deduplication.
4. **`remove_item` for sequences.** Generalize the `states` comment-slot repair into
   `ruamel_ops`, or leave it in `states` (smaller change)?

## Definition of done

- Phases 1–4, 6 and 7 landed (phase 5 by decision).
- No module outside `canlib/yaml_edit/` defines its own `_safe_write`, `_restore`, `_load_doc`,
  `_flow_*` or `_run_edit`. `grep` for those names returns only `yaml_edit/`.
- Differential harness: zero diff against the pre-refactor commit, except the seven listed
  behavior changes.
- `docs/reference/cli/` unchanged; full test, lint, type and CLI-reference checks green.
- Net line count of the editor and command modules is lower. Report the measured number in the
  last PR; do not claim a figure before measuring it.
