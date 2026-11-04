#!/usr/bin/env bash
# Upload the Git LFS objects a push is about to publish.
#
# Run as a pre-commit pre-push hook. `pre-commit install` generates the pre-push
# hook script, and when the git-lfs one was not installed first it replaces it, after
# which every push sends commits whose LFS objects never reach the server. The pull
# on another machine then fails with "Object does not exist on the server". This hook
# does that upload itself, so the push cannot silently skip it.
#
# pre-commit provides the push's remote and branch in the environment; it is not
# given git's stdin, so `git lfs pre-push` cannot be used here.
set -euo pipefail

remote="${PRE_COMMIT_REMOTE_NAME:-}"
ref="${PRE_COMMIT_LOCAL_BRANCH:-}"

# Not a branch push (a tag, a deletion, or a manual `pre-commit run`): nothing to upload.
if [[ -z "$remote" || -z "$ref" ]]; then
    exit 0
fi

exec git lfs push "$remote" "$ref"
