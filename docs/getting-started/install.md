# Install

canair isn't on PyPI yet, so install it from a clone of the repository. You need
[`uv`](https://docs.astral.sh/uv/) (a fast Python package/tool manager).

## Install the CLI

```bash
git clone https://github.com/philipkocanda/canair.git
cd canair
uv tool install .    # installs the `canair` command globally
canair --help        # first run creates ~/.config/canair/ + a starter config.yaml
```

The first `canair` run scaffolds your user config directory and a starter
`config.yaml`. If you run an interactive command that needs a vehicle profile,
canair offers a **first-run chooser** — pick one of the bundled/discovered
profiles or create a new one. It tells you exactly where profiles live
(`~/.config/canair/profiles/`) and records your choice as `default_profile` so
later runs are non-interactive. The chooser never fires when piped/scripted, or
when you pass `--profile`/`CANAIR_PROFILE`. Next:
[connect your dongle](connect-device.md).

## Try it without installing

To poke around without a global install, run it straight from the checkout:

```bash
uv run canair --help
```

`uv run canair …` executes the code in the current repo checkout. This is also
what you'll use if you're hacking on canair itself.

## Staying up to date

canair checks GitHub once a day (in a background thread — it never blocks a
command, and any network failure is silently ignored) for a newer released
version. When one is available it prints a one-line notice with a link to the
[changelog](https://github.com/philipkocanda/canair/blob/main/CHANGELOG.md) and
how to upgrade.

Upgrade with a single command — it fast-forwards your clone and reinstalls the
CLI from it (keeping the git-clone install intact):

```bash
canair update            # check, confirm, then git pull + uv tool install . --reinstall
canair update --check    # report current/latest + changelog only, change nothing
canair update --yes      # skip the confirmation prompt (automation)
```

If canair can't find your clone or `uv` (e.g. a different install method), it
prints the exact manual commands instead. To silence the automatic check, set
`check_for_updates: false` in your config (or export `CANAIR_NO_UPDATE_CHECK=1`).

## Tab-completion (optional)

Completion covers subcommands, flags, and ECU/PID names from your active profile:

```bash
canair completion --install    # auto-detects your shell; open a new shell after
```

Completion hooks the literal `canair` command word, so it won't fire through
`uv run`. If you work from a checkout, activate the venv first:

```bash
uv sync && source .venv/bin/activate
canair completion --install
```
