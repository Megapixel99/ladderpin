r"""`ladderpin` — freeze what a function answers, and find out when that changes.

    ladderpin pin src/ -o behaviour.pin.json
    ladderpin check behaviour.pin.json src/
    ladderpin accept behaviour.pin.json src/ --ref src/a.py::f --reason "rounds up now"
    ladderpin show behaviour.pin.json

    # a JavaScript tree: same document, the other assay binary
    ladderpin pin js/src --assay assay-js -o js.pin.json

Exit codes: 0 nothing pinned changed · 1 a pinned function answers differently · 2 this
tool could not settle anything (no assay, no entries, an unreadable pin). Two is never
folded into one: "your behaviour changed" and "nothing was compared" send you to opposite
ends of a repository.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import pin as pinning
from .bundle import BundleError, collect
from .compare import (CHANGED, FINDINGS, HELD, LOOKS, compare, exit_code,
                      summarise)

USAGE_EPILOG = """\
A pin is `assay bundle` read in the other direction: sameness is what `assay sweep`
looks for across two trees, and difference is what this looks for across time.

`assay` is a separate install and is not vendored:
  pip install assay-checks        # a Python tree
  npm install -g assay-checks     # a JavaScript tree, then --assay assay
"""


def _add_common(parser):
    parser.add_argument("paths", nargs="+", help="what to bundle")
    parser.add_argument("--assay", default="assay",
                        help="the assay command to run (default: assay). Name the "
                             "JavaScript binary here to pin a JavaScript tree")
    parser.add_argument("--root", default=".",
                        help="refs are stored relative to this (default: .)")
    parser.add_argument("--timeout", type=float, default=900.0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ladderpin",
        description="Freeze what a function answers; report when that changes.",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    p_pin = sub.add_parser("pin", help="write a pin from the tree as it is now")
    _add_common(p_pin)
    p_pin.add_argument("-o", "--out", required=True, help="where to write the pin")
    p_pin.add_argument("--no-determinism-check", action="store_true",
                       help="do not ask nondet whether each function is safe to pin. "
                            "The entries are then marked `unchecked`, which is not a pass")
    p_pin.add_argument("--runs", type=int, default=None,
                       help="fresh processes per function for the nondet check")

    p_check = sub.add_parser("check", help="compare the tree against a pin")
    p_check.add_argument("pin", help="the pin to check against")
    _add_common(p_check)
    p_check.add_argument("-q", "--quiet", action="store_true",
                         help="print findings and looks, not every held entry")
    p_check.add_argument("--json", action="store_true",
                         help="print the outcomes as JSON instead of the prose report")

    p_accept = sub.add_parser(
        "accept", help="re-pin a function whose behaviour changed on purpose")
    p_accept.add_argument("pin", help="the pin to update in place")
    _add_common(p_accept)
    p_accept.add_argument("--ref", action="append", default=[],
                          help="a pinned reference to accept; repeatable")
    p_accept.add_argument("--all", action="store_true",
                          help="accept every entry this run reports as changed")
    p_accept.add_argument("--reason", required=True,
                          help="why this change was intended. Required: an exemption "
                               "nobody wrote a reason for is one nobody can review")
    p_accept.add_argument("--no-determinism-check", action="store_true",
                          help="do not re-ask nondet about the accepted functions")
    p_accept.add_argument("--runs", type=int, default=None)

    p_show = sub.add_parser("show", help="what a pin holds")
    p_show.add_argument("pin")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    if args.command == "show":
        return _show(args)
    try:
        if args.command == "pin":
            return _pin(args)
        if args.command == "accept":
            return _accept(args)
        return _check(args)
    except BundleError as exc:
        sys.stderr.write(f"ladderpin: {exc}\n")
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ladderpin: {exc}\n")
        return 2


def _pin(args):
    bundle = collect(args.paths, args.assay, args.root, args.timeout)
    document = pinning.build(bundle, args.root,
                             check_determinism=not args.no_determinism_check,
                             runs=args.runs)
    entries = document["entries"]
    if not entries:
        # A pin with no entries compares nothing and reports `0 changed` forever, which is
        # the shape of a passing run. Refusing to write one is cheaper than explaining it
        # to whoever inherits the green build.
        sys.stderr.write(
            "ladderpin: nothing to pin — assay probed no functions under "
            f"{', '.join(args.paths)}.\n"
            "           A pin with no entries reports `0 changed` on every run, which is\n"
            "           indistinguishable from a passing one. Run `assay bundle` on these\n"
            "           paths to see which gate refused them.\n")
        return 2

    pinning.write(document, args.out)
    counts = pinning.tally(document)
    print(f"pinned {len(entries)} function(s) into {args.out}")
    for state in sorted(counts):
        print(f"  {counts[state]:5}  {state}")
    if counts.get(pinning.UNCHECKED):
        # UNCHECKED IS NOT A PASS. A silent skip and a clean check look identical in a
        # tally, so this says which it was, every time.
        print(f"  note: {counts[pinning.UNCHECKED]} entr(ies) were NOT checked for "
              f"determinism. A function that answers differently across fresh processes "
              f"makes a pin that fails at random and gets deleted — `pip install nondet` "
              f"and re-pin.")
    if document["not_pinned"]:
        print(f"  {len(document['not_pinned'])} function(s) were not pinned; "
              f"`ladderpin show {args.out}` names them and why")
    return 0


def _check(args):
    document = pinning.read(args.pin)
    bundle = collect(args.paths, args.assay, args.root, args.timeout)
    outcomes = compare(document, bundle)
    counts = summarise(outcomes)
    pinned = len(document["entries"])
    compared = counts.get(HELD, 0) + counts.get(CHANGED, 0)
    if pinned and not compared:
        # NOTHING WAS COMPARED, WHICH IS NOT THE SAME AS NOTHING CHANGED. A pin whose
        # every entry comes back a look prints `0 changed` and exits clean, which is the
        # shape of a passing run — and is what a `check` pointed at a renamed directory
        # does forever. Only a change may fail the run, so this says so instead.
        sys.stderr.write(
            f"ladderpin: NOTHING WAS COMPARED — all {pinned} pinned function(s) came back "
            f"as looks, so this run settled nothing about behaviour.\n"
            f"           A run that compares nothing prints `0 changed` like a clean one. "
            f"Check that\n           the paths given still hold the pinned tree.\n")

    if getattr(args, "json", False):
        # The numbers, for whatever reads this instead of a person. The exit code is in
        # the document too: something deciding what to do next should not have to run the
        # process again to learn what it decided.
        json.dump({
            "pin": args.pin,
            "pinned": pinned,
            "summary": counts,
            "exit": exit_code(outcomes, pinned),
            "outcomes": [{"ref": o.ref, "state": o.state, "detail": o.detail,
                          "rung": o.rung, "was": o.was, "now": o.now,
                          "moved_to": o.moved_to} for o in outcomes],
        }, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return exit_code(outcomes, pinned)

    findings = [o for o in outcomes if o.state in FINDINGS]
    looks = [o for o in outcomes if o.state in LOOKS]
    if findings:
        print(f"FINDINGS — {len(findings)}:")
        for outcome in findings:
            print(outcome)
    if looks:
        print(f"\nLOOK — {len(looks)}, which never fail the run:")
        for outcome in looks:
            print(outcome)
    if not args.quiet:
        held = [o for o in outcomes if o.state == HELD]
        if held:
            print(f"\nHELD — {len(held)}:")
            for outcome in held:
                print(outcome)

    # THE DENOMINATOR IS ALWAYS PRINTED. A pin covering three functions and a pin covering
    # three hundred otherwise report the same clean line, and they are not the same result.
    print(f"\n{pinned} pinned function(s): "
          f"{counts.get(HELD, 0)} held, {counts.get(CHANGED, 0)} changed, "
          f"{sum(counts.get(state, 0) for state in LOOKS)} not settled")
    return exit_code(outcomes, pinned)


def _accept(args):
    """Re-pin the entries whose behaviour changed on purpose, with the reason recorded.

    ONLY WHAT ACTUALLY CHANGED. Accepting an entry that is `held` would rewrite a vector
    with the identical vector and leave a reason beside a decision nobody made; accepting
    one that is `missing` or `unprobeable` cannot work, because there is no new vector to
    take. Both are refused by name rather than skipped, so the command never reports
    having done something it did not do.
    """
    if not args.ref and not args.all:
        sys.stderr.write("ladderpin accept: name --ref REF (repeatable) or --all\n")
        return 2
    document = pinning.read(args.pin)
    bundle = collect(args.paths, args.assay, args.root, args.timeout)
    outcomes = {o.ref: o for o in compare(document, bundle)}
    records = {r["ref"]: r for r in bundle["records"]}

    wanted = list(args.ref) if args.ref else [o.ref for o in outcomes.values()
                                              if o.state == CHANGED]
    if not wanted:
        print("nothing to accept: no pinned function reports as changed")
        return 0

    accepted, refused = [], []
    for ref in wanted:
        outcome = outcomes.get(ref)
        if outcome is None:
            refused.append((ref, "not in this pin"))
            continue
        if outcome.state != CHANGED:
            refused.append((ref, f"is `{outcome.state}`, not `changed` — "
                                 f"only a changed vector can be accepted"))
            continue
        record = records.get(outcome.moved_to or ref)
        if record is None:
            refused.append((ref, "assay did not probe it in this run"))
            continue
        determinism = (pinning.UNCHECKED, "the determinism check was turned off")
        if not args.no_determinism_check:
            # A FUNCTION THAT BECAME NONDETERMINISTIC MUST NOT BE RE-PINNED. It would be
            # accepted today and report as changed tomorrow, with a reason beside it
            # saying the change was intended — the worst of both.
            state, detail = pinning.determinism_of(record["ref"], record["arity"],
                                                   args.root, args.runs)
            if state == pinning.REFUSED:
                refused.append((ref, f"nondet now refuses it — {detail}"))
                continue
            determinism = (state, detail)
        # The verdict goes in beside the vector it is about. Leaving the old one there
        # would have the entry claim a check that never ran against what is now in the file.
        if pinning.accept(document, ref, record, args.reason, determinism=determinism):
            accepted.append(ref)

    if accepted:
        pinning.write(document, args.pin)
        print(f"accepted {len(accepted)} change(s) into {args.pin}, with the reason "
              f"recorded beside each:")
        for ref in accepted:
            print(f"  {ref}")
    for ref, why in refused:
        print(f"  refused  {ref} — {why}")
    # ANY REFUSAL IS A NON-ZERO EXIT, including one beside a success. A typo in one
    # `--ref` of four, or one entry of an `--all` that nondet now refuses, is a thing the
    # command did not do, and a script that reads only the exit code must not be told the
    # whole accept went through.
    return 1 if refused else 0


def _show(args):
    try:
        document = pinning.read(args.pin)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ladderpin: {exc}\n")
        return 2
    print(f"{args.pin}  [ladderpin v{document['ladderpin']}, "
          f"{document.get('language')}, taken {document.get('created')}]")
    for entry in document["entries"]:
        print(f"  {entry['ref']:50} arity {entry['arity']}  {entry['ladder']}  "
              f"{entry['determinism']}")
    if document["not_pinned"]:
        print(f"\nnot pinned — {len(document['not_pinned'])}:")
        for ref, why in document["not_pinned"].items():
            print(f"  {ref} — {why}")
    accepted = document.get("accepted") or {}
    if accepted:
        print(f"\naccepted changes — {len(accepted)}:")
        for ref, entry in sorted(accepted.items()):
            print(f"  {ref} — {entry['reason']}  ({entry['when']})")
    counts = pinning.tally(document)
    print(f"\n{len(document['entries'])} entr(ies): "
          + ", ".join(f"{counts[state]} {state}" for state in sorted(counts)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
