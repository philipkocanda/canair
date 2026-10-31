#!/usr/bin/env python3
"""Round-trip a corpus of real-world DBCs through `canair import dbc` / `export dbc`.

An opt-in, device-free check of the broadcast-signal model against DBCs canair did
not write. Ground truth is cantools' own parse of each source DBC. For every DBC:

  1. `import dbc` into a throwaway profile (one bus per DBC), then compare each
     signal's start bit, length, byte order, signedness, float type, scale, offset,
     min/max, unit and value table, and each message's length and frame format
     (standard/extended, classic/CAN FD)
  2. `validate signals`
  3. `export dbc`, reload the result, and compare the same fields again
  4. decode random frames with the source and the round-tripped database and
     compare every value

Outside the model, so reported as counts rather than failures: messages without
signals (not stored in signals/), messages canair refuses to store (e.g. a signal
overrunning its frame), value tables on scaled or float signals (not carried),
multiplexed messages (flattened on import; not decoded), and messages cantools
cannot decode from the source DBC either. Ground truth for a DBC cantools rejects as
written is its parse after `import dbc`'s dialect repairs (canlib/dbc_repair.py); one
that does not parse even then, or whose standard and extended frames share a number,
is listed and skipped.

The default corpus is commaai/opendbc (MIT), fetched on demand into the gitignored
references/can/ like scripts/fetch_can_corpus.py. Its DBCs that pull in shared
files are built with opendbc's own generator, which means running Python from the
fetched clone; pass --no-generated to skip that.

Usage:
    uv run python scripts/check_dbc_corpus.py --fetch     # clone/update opendbc, then check
    uv run python scripts/check_dbc_corpus.py             # check the fetched opendbc
    uv run python scripts/check_dbc_corpus.py car.dbc …   # check specific DBCs
"""

from __future__ import annotations

import argparse
import contextlib
import io
import math
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENDBC_DIR = REPO_ROOT / "references" / "can" / "opendbc"
GENERATED_DIR = REPO_ROOT / "references" / "can" / "opendbc-generated"
OPENDBC_URL = "https://github.com/commaai/opendbc.git"
FRAMES_PER_MESSAGE = 50


def fetch_opendbc() -> None:
    if (OPENDBC_DIR / ".git").is_dir():
        subprocess.run(["git", "-C", str(OPENDBC_DIR), "pull", "-q", "--ff-only"], check=True)
    else:
        OPENDBC_DIR.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "-q", "--depth", "1", OPENDBC_URL, str(OPENDBC_DIR)]
        subprocess.run(cmd, check=True)


def generate_opendbc() -> None:
    generator = OPENDBC_DIR / "opendbc" / "dbc" / "generator"
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    # From the clone root, so the per-make generator scripts can `import opendbc`.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import generator; generator.create_all(sys.argv[2])"
    )
    cmd = [sys.executable, "-c", code, str(generator), str(GENERATED_DIR)]
    subprocess.run(cmd, cwd=OPENDBC_DIR, check=True)


def opendbc_corpus(generated: bool) -> list[Path]:
    paths = sorted((OPENDBC_DIR / "opendbc" / "dbc").glob("*.dbc"))
    if generated:
        paths += sorted(GENERATED_DIR.glob("*.dbc"))
    return paths


def _carries_choices(sig) -> bool:
    """canair keeps a value table only on an unscaled integer signal (see import dbc)."""
    return not sig.is_float and sig.scale == 1 and not sig.offset


def _fields(sig) -> dict:
    choices = None
    if sig.choices and _carries_choices(sig):
        choices = {int(k): str(getattr(v, "name", v)) for k, v in sig.choices.items()}
    return {
        "start_bit": sig.start,
        "length": sig.length,
        "byte_order": "big" if sig.byte_order == "big_endian" else "little",
        "signed": bool(sig.is_signed),
        "float": bool(sig.is_float),
        "scale": sig.scale,
        "offset": sig.offset,
        "min": sig.minimum,
        "max": sig.maximum,
        "unit": sig.unit or None,
        "choices": choices,
    }


def _stored(sig: dict) -> dict:
    return {
        "start_bit": sig["start_bit"],
        "length": sig["length"],
        "byte_order": sig.get("byte_order", "little"),
        "signed": bool(sig.get("signed", False)),
        "float": bool(sig.get("float", False)),
        "scale": sig.get("scale", 1),
        "offset": sig.get("offset", 0),
        "min": sig.get("min"),
        "max": sig.get("max"),
        "unit": sig.get("unit"),
        "choices": sig.get("values") if sig.get("type") == "enum" else None,
    }


def _frame(m) -> dict:
    """A message's frame layout as cantools sees it; FD is implied above 8 bytes."""
    return {
        "length": m.length,
        "extended": bool(m.is_extended_frame),
        "fd": bool(m.is_fd) or m.length > 8,
    }


def _key(m) -> tuple[int, bool]:
    return (m.frame_id, bool(m.is_extended_frame))


def _label(m) -> str:
    small_extended = m.is_extended_frame and m.frame_id <= 0x7FF
    return f"0x{m.frame_id:X}" + (" (extended)" if small_extended else "")


def _diff(want: dict, have: dict) -> dict:
    return {k: (want[k], have[k]) for k in want if want[k] != have[k]}


def _same_value(want, got) -> bool:
    """Equal decoded values; a float NaN (a random float frame's bits) equals itself."""
    try:
        w, g = float(want), float(got)
    except (TypeError, ValueError):
        return False
    if math.isnan(w) or math.isnan(g):
        return math.isnan(w) and math.isnan(g)
    return abs(w - g) <= 1e-6 * max(1, abs(w))


def _multiplexed(msg) -> bool:
    # Also catches a malformed mux (`m` signals without an `M` switch), which
    # cantools does not report through is_multiplexed().
    return msg.is_multiplexed() or any(s.multiplexer_ids for s in msg.signals)


def _canair(argv: list[str]) -> tuple[int, str]:
    from canlib.cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # a crash is a finding, not a stop
            print(f"crashed: {type(e).__name__}: {e}")
            rc = 99
    return rc or 0, out.getvalue().strip()


class Checker:
    def __init__(self, profile: Path, work: Path) -> None:
        self.profile_args = ["--profile", str(profile)]
        self.signals_dir = profile / "signals"
        self.work = work
        self.totals: Counter = Counter()
        self.problems: dict[str, list[str]] = defaultdict(list)
        self.skipped: list[str] = []
        self.n_buses = 0

    def check(self, path: Path) -> None:
        import cantools

        from canlib.commands.import_ import DbcImportError, _dbc_to_signals
        from canlib.dbc_repair import DbcLoadError, load_dbc

        try:
            src, repairs = load_dbc(path)
        except DbcLoadError as e:
            self.skipped.append(f"{path.name}: cantools cannot parse it, even repaired ({e})")
            return
        try:
            expected = _dbc_to_signals(src, None, None, None)
        except DbcImportError as e:
            self.skipped.append(f"{path.name}: {e}")
            return
        self.totals["dbcs"] += 1
        self.totals["DBCs repaired on load (canlib/dbc_repair.py)"] += bool(repairs)
        self.totals["messages canair cannot store (skipped on import)"] += len(expected.skipped)
        self.totals["value tables on scaled/float signals (not carried)"] += len(
            expected.value_tables_dropped
        )
        # One bus per DBC: two corpus files with the same stem must not merge.
        self.n_buses += 1
        bus = f"{self.n_buses:04d}-{path.stem}"
        problems = self.problems[str(path)]
        stored_ids = set(expected.messages)

        rc, out = _canair([*self.profile_args, "import", "dbc", str(path), "--bus", bus])
        if rc:
            problems.append(f"import failed: {out[-200:]}")
            return
        self._check_import(src, bus, stored_ids, problems)

        rc, out = _canair([*self.profile_args, "validate", "signals"])
        if rc:
            problems.append(f"validate failed: {out[-300:]}")

        exported = self.work / f"{bus}.exported.dbc"
        argv = [*self.profile_args, "export", "dbc", "--bus", bus, "-o", str(exported)]
        rc, out = _canair(argv)
        if rc:
            problems.append(f"export failed: {out[-200:]}")
            return
        try:
            rt = cantools.database.load_file(str(exported), strict=False)
        except Exception as e:
            problems.append(f"exported DBC does not reload: {e}")
            return
        kept = [m for m in src.messages if m.signals and f"0x{m.frame_id:X}" in stored_ids]
        self._check_export(kept, rt, problems)
        self._check_decode(kept, rt, problems)

    def _check_import(self, src, bus: str, stored_ids: set[str], problems: list[str]) -> None:
        import yaml

        from canlib.validation.signals import message_is_extended, message_is_fd

        doc = yaml.safe_load((self.signals_dir / f"{bus}.yaml").read_text())
        messages = doc.get("messages") or {}
        for m in src.messages:
            mid = f"0x{m.frame_id:X}"
            if not m.signals:
                self.totals["signal-less messages (not stored)"] += 1
                continue
            if mid not in stored_ids:
                continue
            stored = messages.get(mid) or {}
            ln = stored.get("length")
            have = {
                "length": ln,
                "extended": message_is_extended(m.frame_id, stored),
                "fd": isinstance(ln, int) and message_is_fd(stored, ln),
            }
            if diff := _diff(_frame(m), have):
                problems.append(f"import {_label(m)}: {diff}")
            for s in m.signals:
                self.totals["signals"] += 1
                self.totals["signed signals"] += s.is_signed
                self.totals["float signals"] += s.is_float
                got = (stored.get("signals") or {}).get(s.name)
                if got is None:
                    problems.append(f"import {_label(m)}/{s.name}: missing")
                elif diff := _diff(_fields(s), _stored(got)):
                    problems.append(f"import {_label(m)}/{s.name}: {diff}")

    def _check_export(self, kept: list, rt, problems: list[str]) -> None:
        by_key = {_key(m): m for m in rt.messages}
        for m in kept:
            r = by_key.get(_key(m))
            if r is None:
                problems.append(f"export {_label(m)}: missing")
                continue
            if diff := _diff(_frame(m), _frame(r)):
                problems.append(f"export {_label(m)}: {diff}")
            rsigs = {s.name: s for s in r.signals}
            for s in m.signals:
                if s.name not in rsigs:
                    problems.append(f"export {_label(m)}/{s.name}: missing")
                elif diff := _diff(_fields(s), _fields(rsigs[s.name])):
                    problems.append(f"export {_label(m)}/{s.name}: {diff}")

    def _check_decode(self, kept: list, rt, problems: list[str]) -> None:
        by_key = {_key(m): m for m in rt.messages}
        for m in kept:
            r = by_key.get(_key(m))
            if r is None:
                continue
            if _multiplexed(m):
                self.totals["multiplexed messages (not decoded)"] += 1
                continue
            for _ in range(FRAMES_PER_MESSAGE):
                data = bytes(random.getrandbits(8) for _ in range(m.length))
                try:
                    want = m.decode(data, decode_choices=False, allow_truncated=True)
                except Exception:
                    self.totals["messages cantools cannot decode from the source"] += 1
                    break
                try:
                    got = r.decode(data, decode_choices=False, allow_truncated=True)
                except Exception as e:
                    problems.append(f"decode {_label(m)}: round trip fails ({e})")
                    break
                bad = [k for k, v in want.items() if not _same_value(v, got.get(k))]
                if bad:
                    k = bad[0]
                    problems.append(
                        f"decode {_label(m)}/{k}: {want[k]} vs {got.get(k)} (data {data.hex()})"
                    )
                    break
                self.totals["frames decoded"] += 1
            else:
                self.totals["messages decoded"] += 1

    def report(self, limit: int) -> int:
        failing = {k: v for k, v in self.problems.items() if v}
        for line in self.skipped:
            print(f"skipped  {line[:160]}")
        for name, problems in sorted(failing.items()):
            print(f"\n{name}: {len(problems)} problem(s)")
            for p in problems[: limit or None]:
                print(f"    {p[:220]}")
            if limit and len(problems) > limit:
                print(f"    … {len(problems) - limit} more (--limit 0 shows all)")
        print()
        for key, value in self.totals.items():
            print(f"{key:>50}: {value}")
        print(f"{'DBCs skipped (unparseable, or IDs clash)':>50}: {len(self.skipped)}")
        n = sum(len(v) for v in failing.values())
        print(f"{'problems':>50}: {n} in {len(failing)} DBC(s)")
        return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dbc", nargs="*", type=Path, help="DBCs to check (default: opendbc)")
    ap.add_argument("--fetch", action="store_true", help="Clone or update opendbc first")
    ap.add_argument(
        "--no-generated",
        action="store_true",
        help="Skip opendbc's generated DBCs (which run opendbc's generator)",
    )
    ap.add_argument("--limit", type=int, default=5, help="Problems shown per DBC (0 = all)")
    ap.add_argument("--seed", type=int, default=1, help="Random seed for the decoded frames")
    args = ap.parse_args(argv)

    random.seed(args.seed)
    os.environ.setdefault("CANAIR_NO_UPDATE_CHECK", "1")
    paths: list[Path] = args.dbc
    if paths and args.fetch:
        print("--fetch ignored: it fetches opendbc, and explicit DBCs were given.", file=sys.stderr)
    if not paths:
        if args.fetch:
            fetch_opendbc()
        if not OPENDBC_DIR.is_dir():
            print(f"No opendbc at {OPENDBC_DIR}; run with --fetch.", file=sys.stderr)
            return 2
        if not args.no_generated:
            generate_opendbc()
        paths = opendbc_corpus(generated=not args.no_generated)

    with tempfile.TemporaryDirectory(prefix="canair-dbc-corpus-") as tmp:
        work = Path(tmp)
        profile = work / "dbc-corpus"
        profile.mkdir()
        (profile / "profile.yaml").write_text('car_model: "DBC corpus check"\ninit: ""\n')
        checker = Checker(profile, work)
        for path in paths:
            checker.check(path)
        return checker.report(args.limit)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    sys.exit(main())
