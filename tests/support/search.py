"""Capture builders and a runner for the search tests."""

from __future__ import annotations

import argparse
import json

import canair.captures.store as capture_store
from canair.cli import _inject_default_subcommand
from canair.cli.commands import search
from canair.profile import active


def payload(did: str, body: bytes) -> str:
    return (bytes.fromhex("62" + did) + body).hex().upper()


def write(tmp_path, captures, *, session=None, name="2026-07-24"):
    doc = {
        "sessions": [
            {
                "date": name,
                "vehicle_states": ["ready"],
                "keep_mode": "unique",
                **(session or {}),
                "captures": captures,
            }
        ]
    }
    (tmp_path / f"{name}.json").write_text(json.dumps(doc))


def cap(ecu, pid, payload, **extra):
    return {"ecu": ecu, "pid": pid, "payload": payload, "time": "09:00:00", **extra}


def run(tmp_path, monkeypatch, argv):
    orig = capture_store.load_all_captures
    monkeypatch.setattr(
        "canair.cli.commands.search.uds.load_all_captures",
        lambda *a, **k: orig(tmp_path, profile=active()),
    )
    parser = search.add_parser(argparse.ArgumentParser().add_subparsers())
    args = parser.parse_args(_inject_default_subcommand(["search", *argv])[1:])
    return args.func(args)
