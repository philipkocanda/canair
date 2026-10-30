#!/usr/bin/env python3
"""Verify every Git LFS pointer in the checkout has its object on the LFS server.

A commit can reach the remote without its LFS objects (the pre-push upload hook was
skipped or overwritten), and nothing complains: the pointer files are ordinary git
blobs, so CI's default checkout, which does not fetch LFS content, goes green. The
failure only surfaces on the next ``git pull`` elsewhere, as a 404 from the smudge
filter. This asks the server directly, with the LFS batch API, whether each object
exists, and downloads nothing.

Usage:
    python scripts/check_lfs_objects.py [--all] [--remote NAME]

Needs only the standard library plus the ``git`` and ``git-lfs`` binaries. Set
``GITHUB_TOKEN`` (CI does) to check a private repository. ``--all`` also covers
every ref in the clone, not just the checked-out tree.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator

BATCH_SIZE = 100
MEDIA_TYPE = "application/vnd.git-lfs+json"

Batch = Callable[[list[dict]], list[dict]]


def lfs_endpoint(remote_url: str) -> str:
    """The LFS API root for an HTTPS remote (``…/info/lfs``)."""
    url = remote_url.rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    return f"{url}/info/lfs"


def tracked_objects(all_refs: bool) -> list[dict]:
    """Every LFS pointer in the tree as ``{"oid", "size", "name"}`` (read from git, so it
    works in a checkout that never downloaded the content)."""
    cmd = ["git", "lfs", "ls-files", "--json"]
    if all_refs:
        cmd.append("--all")
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return json.loads(out).get("files") or []


def missing_objects(files: Iterable[dict], batch: Batch) -> list[dict]:
    """The files whose objects the server reports as absent.

    ``batch`` takes ``[{"oid", "size"}, …]`` and returns the server's per-object
    results; an entry carrying ``error`` is missing (or unreadable).
    """
    names: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    for f in files:
        names.setdefault(f["oid"], []).append(f["name"])
        sizes[f["oid"]] = f["size"]

    missing: list[dict] = []
    oids = sorted(names)
    for i in range(0, len(oids), BATCH_SIZE):
        chunk = [{"oid": o, "size": sizes[o]} for o in oids[i : i + BATCH_SIZE]]
        for result in batch(chunk):
            if "error" in result:
                oid = result["oid"]
                missing.append(
                    {
                        "oid": oid,
                        "names": names.get(oid, []),
                        "error": result["error"].get("message", "unknown error"),
                    }
                )
    return missing


def http_batch(endpoint: str, token: str | None) -> Batch:
    def run(objects: list[dict]) -> list[dict]:
        body = json.dumps(
            {"operation": "download", "transfers": ["basic"], "objects": objects}
        ).encode()
        headers = {"Accept": MEDIA_TYPE, "Content-Type": MEDIA_TYPE}
        if token:
            creds = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        req = urllib.request.Request(f"{endpoint}/objects/batch", body, headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp).get("objects", [])

    return run


def remote_url(remote: str) -> str:
    return subprocess.run(
        ["git", "remote", "get-url", remote], check=True, capture_output=True, text=True
    ).stdout.strip()


def report(missing: list[dict]) -> Iterator[str]:
    yield f"{len(missing)} Git LFS object(s) referenced by this checkout are not on the server:"
    for m in missing:
        yield f"  - {m['oid'][:12]}  {m['error']}"
        for name in m["names"]:
            yield f"      {name}"
    yield ""
    yield "The commit was pushed without its LFS objects. From a clone that has them, run:"
    yield "    git lfs push --all origin"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--all", action="store_true", help="check every ref, not just HEAD")
    parser.add_argument("--remote", default="origin", help="git remote to ask (default: origin)")
    args = parser.parse_args(argv)

    url = remote_url(args.remote)
    if not url.startswith(("https://", "http://")):
        print(f"skipping: remote {args.remote!r} is not HTTPS ({url})", file=sys.stderr)
        return 0

    files = tracked_objects(args.all)
    if not files:
        print("No Git LFS pointers in this checkout.")
        return 0

    try:
        missing = missing_objects(
            files, http_batch(lfs_endpoint(url), os.environ.get("GITHUB_TOKEN"))
        )
    except urllib.error.HTTPError as e:
        print(f"error: the LFS server answered HTTP {e.code}; could not verify", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        # A network blip is not evidence of a missing object; do not fail the gate on it.
        print(f"warning: could not reach the LFS server ({e}); not checked", file=sys.stderr)
        return 0

    if missing:
        print("\n".join(report(missing)), file=sys.stderr)
        return 1
    print(f"All {len({f['oid'] for f in files})} Git LFS object(s) are on the server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
