# Install

canair is not on PyPI yet. Install it from a clone of the repository. You need
[`uv`](https://docs.astral.sh/uv/), a fast Python package and tool manager.

## Install the CLI

```bash
git clone https://github.com/philipkocanda/canair.git
cd canair
uv tool install .    # installs the `canair` command globally
canair --help        # first run creates ~/.config/canair/ + a starter config.yaml
```

The first `canair` run creates your user config directory and a starter `config.yaml`.

Some interactive commands need a vehicle profile. For these, canair shows a **first-run chooser**.
You pick a bundled or discovered profile, or you create a new one. The chooser tells you where
profiles live (`~/.config/canair/profiles/`). It saves your choice as `default_profile`, so later
runs do not ask again. The chooser does not appear when input is piped or scripted. It also does
not appear when you pass `--profile` or set `CANAIR_PROFILE`.

Next: [connect your dongle](connect-device.md).

## Try it without installing

To try canair without a global install, run it from the checkout:

```bash
uv run canair --help
```

`uv run canair …` runs the code in the current repository checkout. Use this command also when you
work on canair itself.

Inside a clone, `make install` (alias `make setup`) does the full bootstrap in one step. It sets
up Git LFS, runs `uv sync`, installs the git hooks, and runs `uv tool install --reinstall .`. Run
it again after a `git pull` to refresh the bare `canair` command.

`make install` first checks two things. uv must be version 0.13 or later. uv must also not choose a
pre-release Python. The clone pins Python 3.15 (`.python-version`). An older uv resolves that pin to
an old alpha, and the alpha fails to build PyYAML. If the check stops, it names the fix
(`uv self update`).

## Point your writes somewhere durable

`uv tool install` copies canair into the tool environment of uv. The copy includes the profiles
that ship with canair. The copy is **frozen**. A reinstall (`canair update`) replaces it
completely. If you record a capture or edit a PID in a *bundled* profile with a bare `canair`,
your next update deletes that data. Decide once, before you start:

```bash
# You have the clone (you just used it to install) — keep profile data in it:
canair config set profiles_dir /path/to/canair/profiles

# Or take your own copy of a bundled profile:
canair profile adopt hyundai-ioniq-ae-ev
```

A profile that you create yourself (`canair profile create`, or the first-run chooser) is already
in `~/.config/canair/profiles/`. This does not affect it.

canair warns you when a write lands in the snapshot. `canair update` lists what a reinstall would
delete. These warnings do not replace the one-time setting above. For details, see
[Profiles → Where your writes
land](../concepts/profiles.md#where-your-writes-land).

## Staying up to date

canair checks GitHub once a day for a newer release. The check runs in a background thread. It
never blocks a command, and canair ignores network failures without a message. When a new version
exists, canair prints a one-line notice. The notice links to the
[changelog](https://github.com/philipkocanda/canair/blob/main/CHANGELOG.md) and shows how to
upgrade.

To upgrade, run one command. It checks out the latest release tag in your clone and reinstalls
the CLI from it. The git-clone install stays intact.

```bash
canair update            # check, confirm, then checkout <tag> + uv tool install . --reinstall
canair update --check    # report current/latest + changelog only, change nothing
canair update --yes      # skip the confirmation prompt (automation)
canair update --python 3.13   # reinstall on Python 3.13 instead of the newest
```

The command checks out the advertised **release tag**. It does not fast-forward `main`. The
installed code is therefore exactly the released version, without unreleased commits from the
branch. If canair cannot find the latest release tag (for example, GitHub is unreachable),
`canair update` reports that it is offline. It makes no changes and does not guess a version.

The reinstall also moves canair to the newest Python that meets two conditions. uv must be able
to install it, and every dependency must have a prebuilt wheel for it. If you do not have that
Python, uv downloads it. Without the update, uv keeps a tool on the Python that it first used.
canair starts faster on Python 3.15 and later. A dependency without a wheel would build from
source. That needs a compiler and can make canair slower, so the update waits for the wheels. If
the reinstall on the new Python fails, the update tries again on your previous Python.

If canair cannot find your clone or `uv` (for example, you used a different install method), it
prints the exact manual commands. To stop the automatic check, set `check_for_updates: false` in
your config. You can also export `CANAIR_NO_UPDATE_CHECK=1`.

### The two installs can drift out of sync

If you run `uv tool install .` **and** work in a clone, you have *two* copies of canair on the
machine:

- A bare `canair` runs the **installed snapshot** (the tool environment of uv). It is the state
  at your last `uv tool install`.
- `uv run canair` runs the **repository working tree**. It is the code that you checked out or
  edited.

The two copies drift when you edit the repository or pull commits that change the version. A bare
`canair` then reports the old version, and `uv run canair` reports the new one.

`canair update` reports **which copy is running**. It warns when the version of the installed
snapshot differs from `pyproject.toml` in the clone. `canair status` shows the same warning. If no
newer release exists but the copies have drifted, `canair update` offers a **reinstall-only
resync** (`uv tool install <clone> --reinstall`). This needs no network access and no tag
checkout. `canair update --json` includes the full `install` block (`running_origin`,
`running_build`, `tool_version`, `clone_version`, `out_of_sync`).

### The version tells you which build you're running

The bare package version cannot tell builds apart. A release, a clone with local edits and a wheel
from a feature branch can all say `5.2.0`. For this reason, `canair --version` (or `-V`) names the
branch and the short commit, unless the code is exactly a release (a clean tree at the `v5.2.0`
tag). It also says where the copy came from:

```console
$ canair --version
# uv run in a clone, on `main` at commit 343b244 (".dirty" with uncommitted edits)
canair 5.2.0+main.343b244 (from source)
# uv tool install . from the same clone
canair 5.2.0+main.343b244 (local build 343b244, 2026-11-05 10:12 UTC)
# after canair update: a clean build of the release tag
canair 5.2.0 (local build 9f2c1d0, 2026-11-05 10:12 UTC)
# a wheel built by GitHub Actions
canair 5.2.0 (CI build 9f2c1d0, 2026-11-05 10:12 UTC)
```

A run from source reads its git checkout live. An installed copy reads the build stamp that the
build hook (`hatch_build.py`) writes into every wheel. The stamp holds the commit, branch, release
tag, dirty flag, build time and builder. A copy that you installed in another way has no stamp. It
says `installed, no build info`. `canair update --json` gives the structured form under
`install.build`. For a CI build, it includes the URL of the CI run.

The version string appears in `canair status` and `canair update`. It also appears in every
capture that you record. If a reading looks wrong, you can trace it to the exact code that made
it. Release comparisons ignore the suffix, so a development checkout still gets an accurate "up to
date" result.

## Tab-completion (optional)

Completion covers subcommands, flags, and the ECU and PID names of your active profile:

```bash
canair completion --install    # auto-detects your shell; open a new shell after
```

Completion hooks the literal `canair` command word, so it does not work through `uv run`. If you
work from a checkout, activate the virtual environment first:

```bash
uv sync && source .venv/bin/activate
canair completion --install
```

## Git LFS (for raw-CAN logs)

Git stores large raw-CAN broadcast logs (`.blf`, `.asc`, `.trc` and the `captures/can/` directory
of a profile) with [Git LFS](https://git-lfs.com). Install Git LFS once. Then a clone fetches the
real log contents instead of pointer files:

```bash
git lfs install    # one-time, per machine
```

Without Git LFS, the small bundled fixtures still work because they are plain git. A large
committed log appears as an LFS pointer. See
[Broadcast frames → Storing raw-CAN logs](../concepts/broadcast-frames.md) for what Git commits
and what canair fetches on demand.
