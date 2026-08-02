#!/usr/bin/env python3
"""Blind-rediscovery eval harness — prepare a stripped sandbox, then grade guesses.

On-demand stress-test of canair's analysis tooling: can it rediscover an
already-solved signal *blindfolded*? The blindfold is a copy of a profile with
every answer-bearing field removed (see :mod:`canlib.blind`), so the leaky tools
(`investigate`/`coverage`/`decode`/`captures`) have no stored names to echo.

Workflow (run from the repo root):

    # 1. Build a sandbox + quests (curated 15 by default; ground truth kept out of it)
    uv run python scripts/blind_rediscovery.py prepare --run-dir rediscovery/run-1

    # 2. Launch one blind sub-agent per quest (see scripts/blind_prompt.md),
    #    each pointed ONLY at the sandbox profile; collect their expressions into
    #    an answers.json:  [{"id": "q01", "expression": "[S10:S11]"}, ...]

    # 3. Grade the guesses against the held-out ground truth
    uv run python scripts/blind_rediscovery.py grade --run-dir rediscovery/run-1 \
        --answers rediscovery/run-1/answers.json

`prepare --random --seed N --n K` draws a fresh difficulty-weighted set instead of
the curated corpus. `prepare --install` additionally `uv tool install .`s canair
onto PATH for a Tier-2 sandbox (agent runs from inside the sandbox, repo not
referenced); without it, agents use `uv run canair --profile <sandbox>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from canlib import blind
from canlib.profile import resolve_profile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_run_dir() -> Path:
    return REPO_ROOT / "rediscovery" / f"run-{datetime.now():%Y%m%d-%H%M%S}"


# ── prepare ──────────────────────────────────────────────────────────────────────
def cmd_prepare(args: argparse.Namespace) -> int:
    source = resolve_profile(args.profile) if args.profile else resolve_profile()
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir()
    if run_dir.exists() and any(run_dir.iterdir()):
        print(f"error: run-dir {run_dir} exists and is not empty", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)

    curated = not args.random
    print(f"Source profile: {source.name}  ({source.root})")
    print(f"Selecting targets ({'curated' if curated else f'random seed={args.seed} n={args.n}'})…")
    targets = blind.select_targets(
        source.root,
        curated=curated,
        n=args.n,
        seed=args.seed,
        min_captures=args.min_captures,
        min_distinct=args.min_distinct,
        max_per_pid=args.max_per_pid,
        max_per_ecu=args.max_per_ecu,
    )
    if not targets:
        print("error: no targets selected (check capture coverage / filters)", file=sys.stderr)
        return 1

    sandbox = run_dir / "profile"
    print(f"Stripping profile → {sandbox}  (scrub_labels={not args.keep_labels})")
    report = blind.strip_profile(source.root, sandbox, scrub_labels=not args.keep_labels)
    if report.residual_leaks:
        print(
            f"error: strip left residual answer content: {report.residual_leaks[:5]}",
            file=sys.stderr,
        )
        return 1
    print(
        f"  stripped {report.files} ECU files — removed {report.params_removed} params, "
        f"{report.notes_removed} pid-notes, {report.sections_removed} sections"
    )

    quests, answers = [], []
    for i, t in enumerate(targets, 1):
        qid = f"q{i:02d}"
        quests.append({"id": qid, **t.quest()})
        answers.append({"id": qid, **t.answer()})

    quests_path = run_dir / "quests.json"
    answers_path = run_dir / "answer_key.json"
    manifest_path = run_dir / "manifest.json"
    quests_path.write_text(json.dumps(quests, indent=2) + "\n")
    answers_path.write_text(json.dumps(answers, indent=2) + "\n")
    manifest_path.write_text(
        json.dumps(
            {
                "created": datetime.now().isoformat(timespec="seconds"),
                "source_profile": source.name,
                "source_root": str(source.root),
                "sandbox": str(sandbox),
                "mode": "curated" if curated else "random",
                "seed": args.seed,
                "scrub_labels": not args.keep_labels,
                "n_targets": len(targets),
            },
            indent=2,
        )
        + "\n"
    )

    if args.install:
        print("Installing canair on PATH (uv tool install . --reinstall) for a Tier-2 sandbox…")
        import subprocess

        rc = subprocess.run(
            ["uv", "tool", "install", str(REPO_ROOT), "--reinstall"], cwd=REPO_ROOT
        ).returncode
        if rc != 0:
            print(
                "warning: uv tool install failed; agents can still use `uv run canair`",
                file=sys.stderr,
            )

    print()
    print(f"Prepared {len(targets)} quests in {run_dir}")
    print(f"  quests:     {quests_path}")
    print(f"  answer key: {answers_path}  (grader-only — do NOT show the analyst)")
    print(f"  sandbox:    {sandbox}")
    print()
    print("Quests (blindfolded view):")
    for q in quests:
        print(f"  {q['id']}  {q['ecu']:5} {q['pid']:8}  n={q['n_captures']:<5}  {q['role_hint']}")
    print()
    invoke = "canair" if args.install else "uv run canair"
    print("Give each blind agent scripts/blind_prompt.md with its quest, and have it run e.g.:")
    print(f"  {invoke} --profile {sandbox} decode <ECU> <PID> --dump-bytes")
    print(f"Then collect answers into {run_dir / 'answers.json'} and run `grade`.")
    return 0


# ── grade ──────────────────────────────────────────────────────────────────────
def _load_answers(path: Path) -> dict[str, str]:
    """Map quest-id → guessed expression. Accepts a list or an {id: expr} dict."""
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return {
            str(k): str(v) if isinstance(v, str) else str(v.get("expression", ""))
            for k, v in data.items()
        }
    out: dict[str, str] = {}
    for row in data:
        qid = str(row.get("id", ""))
        if qid:
            out[qid] = str(row.get("expression", ""))
    return out


_LABELS = {
    blind.EXACT: "EXACT",
    blind.EQUIVALENT_SCALE: "EQUIV(scale)",
    blind.CATEGORICAL_MATCH: "ENUM-MATCH",
    blind.STRONG_PARTIAL: "PARTIAL",
    blind.MONOTONE_PARTIAL: "MONOTONE",
    blind.MISS: "MISS",
    blind.INSUFFICIENT: "INSUFFICIENT",
    blind.ERROR: "ERROR",
    "NO_ANSWER": "NO-ANSWER",
}


def cmd_grade(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    answers_path = Path(args.answers) if args.answers else run_dir / "answers.json"
    key_path = run_dir / "answer_key.json"
    sandbox = run_dir / "profile"
    if not key_path.exists():
        print(f"error: no answer key at {key_path} (run `prepare` first)", file=sys.stderr)
        return 2
    if not answers_path.exists():
        print(f"error: no answers file at {answers_path}", file=sys.stderr)
        return 2

    key = json.loads(key_path.read_text())
    guesses = _load_answers(answers_path)

    rows, report = [], []
    for target in key:
        qid = target["id"]
        guess = guesses.get(qid, "").strip()
        payloads = blind.load_target_payloads(sandbox, target["rx"], target["pid"])
        truth_param = {
            "expression": target["expression"],
            "type": target.get("type"),
            "values": target.get("values"),
            "bits": target.get("bits"),
        }
        if not guess:
            g = {"verdict": "NO_ANSWER", "n": len(payloads)}
        else:
            g = blind.grade_answer(guess, truth_param, payloads)
        rows.append((qid, target, guess, g))
        report.append(
            {
                "id": qid,
                "ecu": target["ecu"],
                "pid": target["pid"],
                "name": target["name"],
                "truth": target["expression"],
                "guess": guess,
                **g,
            }
        )

    # ── scorecard ──
    print(f"Blind-rediscovery scorecard — {run_dir.name}  ({len(rows)} signals)")
    print(f"{'id':4} {'ECU':5} {'PID':8} {'signal':26} {'verdict':13} {'guess':22} truth")
    print("-" * 110)
    npass = 0
    for qid, t, guess, g in rows:
        verdict = g["verdict"]
        if verdict in blind.PASS_VERDICTS:
            npass += 1
        metric = ""
        if "pearson" in g and g["pearson"] is not None:
            metric = f"r={g['pearson']:+.3f}"
        elif "cramers_v" in g and g["cramers_v"] is not None:
            metric = f"V={g['cramers_v']:.3f}"
        print(
            f"{qid:4} {t['ecu']:5} {t['pid']:8} {t['name'][:26]:26} "
            f"{_LABELS.get(verdict, verdict):13} {(guess or '—')[:22]:22} {t['expression']}  {metric}"
        )
    print("-" * 110)
    solid = sum(1 for _, _, _, g in rows if g["verdict"] in blind.PASS_VERDICTS)
    partial = sum(
        1 for _, _, _, g in rows if g["verdict"] in (blind.STRONG_PARTIAL, blind.MONOTONE_PARTIAL)
    )
    print(
        f"PASS (exact/scale/enum): {solid}/{len(rows)}   partial: {partial}   "
        f"other: {len(rows) - solid - partial}"
    )

    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("prepare", help="build a stripped sandbox + quests")
    pp.add_argument("--profile", help="source profile name or path (default: active)")
    pp.add_argument("--run-dir", help="output dir (default: rediscovery/run-<ts>)")
    pp.add_argument(
        "--random", action="store_true", help="seeded random draw instead of the curated set"
    )
    pp.add_argument("--n", type=int, default=15, help="number of random targets (with --random)")
    pp.add_argument("--seed", type=int, default=0, help="random seed (with --random)")
    pp.add_argument(
        "--keep-labels", action="store_true", help="do NOT scrub capture session labels/notes"
    )
    pp.add_argument("--min-captures", type=int, default=8, help="min captures for a random target")
    pp.add_argument(
        "--min-distinct",
        type=int,
        default=3,
        help="min distinct decoded values for a random target",
    )
    pp.add_argument("--max-per-pid", type=int, default=1, help="max random targets per ECU:PID")
    pp.add_argument("--max-per-ecu", type=int, default=2, help="max random targets per ECU")
    pp.add_argument(
        "--install", action="store_true", help="uv tool install canair for a Tier-2 sandbox"
    )
    pp.set_defaults(func=cmd_prepare)

    pg = sub.add_parser("grade", help="grade answers against the held-out ground truth")
    pg.add_argument("--run-dir", required=True, help="run dir created by `prepare`")
    pg.add_argument("--answers", help="answers file (default: <run-dir>/answers.json)")
    pg.set_defaults(func=cmd_grade)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
