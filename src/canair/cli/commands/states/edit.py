"""``canair states`` edits: add, rm, rename and the per-state fields."""

from __future__ import annotations

from pathlib import Path

from canair.cli.commands._edit import run_edit, set_or_clear
from canair.core.prompt import confirm
from canair.profile import active
from canair.render import ansi, messages
from canair.yaml_edit.echo import echo_edit


def _warn_unresolved_refs(state: str, expr: str | None) -> None:
    """Warn when a just-written ``when:`` predicate reads signals that don't exist.

    Authoring a predicate before its signal is defined is legitimate, so this
    never blocks the write — but an unresolvable reference makes the state
    permanently un-suggestable and the evaluator can't report it (see
    :mod:`canair.states.refs`), so it must not pass silently either.
    ``canair validate states`` is the hard gate.
    """
    if not expr:
        return
    try:
        from canair.states.refs import check_references_on_every_car

        issues = check_references_on_every_car([(state, expr)], active())
    except Exception:
        return  # never let an advisory check fail a successful write
    for issue in issues:
        messages.warning(f"{issue.ref} — {issue.message}")
    if issues:
        messages.note("this predicate can never match until the reference resolves")


def cmd_add(args) -> int:
    from canair.yaml_edit.states import add_state

    rc = run_edit(
        lambda: add_state(
            args.name,
            description=args.description,
            when=args.when,
            implies=args.implies,
            excludes=args.excludes,
            profile=active(),
        ),
        f"added state {args.name.upper()}",
    )
    _warn_unresolved_refs(args.name.upper(), args.when)
    return rc


def cmd_rm(args) -> int:
    from canair.yaml_edit.states import remove_state

    return run_edit(
        lambda: remove_state(args.name, profile=active()), f"removed state {args.name.upper()}"
    )


def cmd_rename(args) -> int:
    from canair.profile import require_writable_definitions
    from canair.yaml_edit.state_rename import apply_state_rename, plan_state_rename

    profile = require_writable_definitions(profile=active())
    plan = plan_state_rename(args.old, args.new, profile=profile)

    _print_rename_plan(plan, profile.root)
    journals = sorted((profile.captures_dir / ".journal").glob("*.jsonl"))
    if journals:
        messages.warning(
            f"{len(journals)} unreconciled journal(s) in "
            f"{profile.captures_dir / '.journal'} still say {plan.old}; recover them first "
            "(canair captures uds --recover) or they will bring it back"
        )
    if args.dry_run:
        print("  (--dry-run: nothing written)")
        return 0
    if plan.rewrites_history and not confirm("Rewrite these files?", yes=args.yes):
        return 1
    apply_state_rename(plan, profile=profile)
    echo_edit(f"renamed {plan.old} → {plan.new}", plan.states_file)
    return 0


def _print_rename_plan(plan, root: Path) -> None:
    """List every file a rename rewrites, grouped by store, with reference counts."""

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    print(f"\n  {ansi.c(f'Rename {plan.old} → {plan.new}', ansi.BOLD)}")
    print(f"    vocabulary   {_rel(plan.states_file)}")
    groups = (
        ("ecus/", plan.ecu_refs, "list"),
        ("captures/", plan.capture_refs, "list"),
        ("logs", plan.log_refs, "entry"),
    )
    for label, refs, unit in groups:
        if not refs:
            continue
        total = sum(refs.values())
        print(f"    {label:<12} {total} {unit}{'s' if total != 1 else ''} in {len(refs)} file(s)")
        for path, n in refs.items():
            print(f"      {ansi.c(_rel(path), ansi.DIM)}  {n}")


def cmd_set_description(args) -> int:
    from canair.yaml_edit.states import set_state_field

    return run_edit(
        lambda: set_state_field(args.name, "description", args.value, profile=active()),
        f"set description on {args.name.upper()}",
    )


def cmd_set_predicate(args) -> int:
    from canair.yaml_edit.states import set_state_field

    rc = run_edit(
        lambda: set_state_field(args.name, "when", args.value or None, profile=active()),
        f"{set_or_clear(args.value)} when: predicate on {args.name.upper()}",
    )
    _warn_unresolved_refs(args.name.upper(), args.value)
    return rc


def _set_relation(args, field: str) -> int:
    from canair.yaml_edit.states import set_state_field

    return run_edit(
        lambda: set_state_field(args.name, field, args.value or None, profile=active()),
        f"{set_or_clear(args.value)} {field}: on {args.name.upper()}",
    )


def cmd_set_implies(args) -> int:
    return _set_relation(args, "implies")


def cmd_set_excludes(args) -> int:
    return _set_relation(args, "excludes")
