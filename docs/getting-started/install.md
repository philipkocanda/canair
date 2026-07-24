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
