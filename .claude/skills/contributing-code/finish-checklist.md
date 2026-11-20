# Finish checklist

The pre-completion command checklist and local gate setup.

## Before you finish

```bash
uv run pytest -q                 # green (parallel by default; -n0 when iterating on one file)
uv run ruff check . && uv run ruff format --check .
uv run ty check                  # src/canair/ — must be clean
make structure                   # import contracts + structure gates (seconds)
uv run canair <yourcmd> --help   # parser sane
uv run python scripts/gen/gen_cli_reference.py   # if you touched a parser; --check is what CI runs
uv run canair validate all       # if you touched ecus/captures/schema
```

Enable the mirrored local gates once per clone (`make setup`, or `git lfs install --force` and
then `uv run pre-commit install --install-hooks` — in that order, or `pre-commit` overwrites
git-lfs's upload hook and a push publishes pointers whose objects never arrive) so
ruff/`ty`, the Conventional Commits check, and the `gen_*.py --check` currency checks run before CI
does — CI stays the hard gate. Then confirm the docs and README reflect any user-facing change and
that internal links still resolve.
