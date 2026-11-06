#!/bin/sh
# Check the toolchain before `make install` builds anything.
#
# This clone pins Python 3.15 (.python-version). A uv release only knows the Pythons that were
# current when it shipped, so an old uv resolves "3.15" to an old pre-release (3.15.0a8 seen in
# the wild) and the dependency build fails with an error about Cython. This script stops there
# and says what to do instead.
#
# Plain POSIX sh with no canair or Python dependency: it runs before the environment exists.

MIN_UV=0.13.0   # the first uv release that can install Python 3.15.0 (not a pre-release)

fail() {
    printf 'error: %s\n' "$1" >&2
    shift
    for line in "$@"; do printf '  %s\n' "$line" >&2; done
    exit 1
}

command -v uv >/dev/null 2>&1 ||
    fail "uv is not installed." "Install it: https://docs.astral.sh/uv/getting-started/installation/"

# `uv --version` prints "uv 0.13.7 (...)". Compare the numbers one by one: `sort -V` is not
# on every system.
have=$(uv --version | awk '{print $2}')
if ! awk -v have="$have" -v min="$MIN_UV" 'BEGIN {
        split(have, a, "."); split(min, b, ".")
        for (i = 1; i <= 3; i++) {
            if (a[i] + 0 > b[i] + 0) exit 0
            if (a[i] + 0 < b[i] + 0) exit 1
        }
    }'; then
    fail "uv $have is older than $MIN_UV, the first release that can install Python 3.15." \
        "Update it: uv self update  (or update it the way you installed it: brew, apt, pip)"
fi

# Installs the Python that .python-version names, if it is missing. With a recent uv this is
# the newest stable 3.15.
if ! installed=$(uv python install 2>&1); then
    fail "uv could not install the Python in .python-version." "$installed"
fi

# What a project command would use now: the project's .venv when it exists, else the pinned
# Python. A pre-release here would fail the build, or leave an environment on an alpha.
python=$(uv python find) || fail "uv cannot find a Python for this project."
level=$("$python" -c 'import sys; print(sys.version_info.releaselevel)')
if [ "$level" != final ]; then
    shown=$("$python" -c 'import sys; print(sys.version.split()[0])')
    fail "uv would use Python $shown, a pre-release ($python)." \
        "Remove the environment and that Python, then run this again:" \
        "  rm -rf .venv" \
        "  uv python uninstall $shown"
fi
