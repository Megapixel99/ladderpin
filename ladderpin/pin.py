"""Building the document that gets committed.

A pin is `assay bundle` read in the other direction. `assay sweep --against` compares two
trees NOW and the finding is **sameness**, because the question is duplication. Commit the
bundle and the same substrate answers a different question — *did this function's behaviour
change when nobody meant it to?* — and the finding becomes **difference**.

THE VECTORS ARE STORED, NOT DIGESTED. A hash would make the file small and the report
useless: `check` could say a function changed and not which of its twenty-nine rungs did.
The rung is the actionable part, so it is kept.

DETERMINISM IS A PRECONDITION OF PINNING AT ALL. A function that answers differently
across fresh processes -- one returning a `set`, one reading the clock, one whose output
depends on `PYTHONHASHSEED` -- produces a different vector on the next machine, and a pin
on it is a flaky check that will be blamed on this tool and deleted. `nondet` is what
answers that, and it is a dependency for exactly this reason.

When `nondet` is absent the pin is still written and every entry is marked
`determinism: "unchecked"`. UNCHECKED IS NOT A PASS, and the report says so in as many
words, because a silent skip and a clean check look identical in a tally.
"""

from __future__ import annotations

import json
import os
import sys
import time

from .bundle import name_of, path_of

PIN_VERSION = 1

CHECKED = "deterministic"
UNCHECKED = "unchecked"
REFUSED = "refused"


def _nondet():
    try:
        import nondet
    except ImportError:
        return None
    return nondet


def determinism_of(ref, arity, root, runs=None):
    """(state, detail) for one function, via `nondet`, or `unchecked` when it is absent.

    `nondet` addresses a function as `FILE::NAME` and re-runs it in fresh interpreters,
    which is exactly the address `assay` records — so unlike the edge `undetermined`
    rejected, the guarantee here can actually run against the thing it is about.
    """
    module = _nondet()
    if module is None:
        return UNCHECKED, "nondet is not installed, so nothing checked this"
    path = os.path.join(root, path_of(ref))
    if not path.endswith(".py"):
        # `nondet` is Python-only. Saying so beats reporting `deterministic` about a
        # JavaScript function nothing looked at.
        return UNCHECKED, "nondet is Python-only and this is not a Python function"
    kwargs = {"runs": runs} if runs else {}
    verdict = module.check(path, name_of(ref), arity, **kwargs)
    if verdict.state == "nondeterministic":
        witness = verdict.witness or {}
        return REFUSED, (f"nondet found a witness: {witness.get('args')} -> "
                         f"{witness.get('a')} then {witness.get('b')}")
    if verdict.state == "look":
        return UNCHECKED, f"nondet could not probe it: {verdict.detail}"
    return CHECKED, str(verdict.detail or f"no disagreement over {verdict.runs} runs")


class Progress:
    """A line on stderr, because `nondet` spawns fresh interpreters and takes its time.

    A tool that prints nothing for four minutes is a tool people kill and then distrust.
    Interactively this rewrites one line; in a log it prints the header and the footer and
    nothing in between, because a progress bar in CI output is 300 lines of carriage
    returns nobody can read.
    """

    def __init__(self, total, stream=None, enabled=True):
        self.total = total
        self.stream = stream or sys.stderr
        self.enabled = enabled and total > 0
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.done = 0
        self.started = time.monotonic()
        if self.enabled:
            self.stream.write(f"[ladderpin] asking nondet about {total} function(s)\n")
            self.stream.flush()

    def tick(self, ref):
        self.done += 1
        if self.enabled and self.tty:
            self.stream.write(f"\r[ladderpin] {self.done}/{self.total}  {ref[-58:]:58}")
            self.stream.flush()

    def finish(self):
        if not self.enabled:
            return
        if self.tty:
            self.stream.write("\r" + " " * 78 + "\r")
        seconds = time.monotonic() - self.started
        self.stream.write(f"[ladderpin] nondet answered about {self.done} function(s) "
                          f"in {seconds:.1f}s\n")
        self.stream.flush()


def build(bundle, root=".", check_determinism=True, runs=None, progress=None):
    """{pin document, refusals} — the entries that may be pinned, and why the rest may not."""
    entries = []
    refused = {}
    records = sorted(bundle["records"], key=lambda r: r["ref"])
    reporter = progress if progress is not None else Progress(
        len(records), enabled=check_determinism)
    for record in records:
        if record.get("error"):
            refused[record["ref"]] = f"assay recorded an error: {record['error']}"
            reporter.tick(record["ref"])
            continue
        state, detail = (determinism_of(record["ref"], record["arity"], root, runs)
                         if check_determinism else
                         (UNCHECKED, "the determinism check was turned off"))
        reporter.tick(record["ref"])
        if state == REFUSED:
            refused[record["ref"]] = detail
            continue
        entries.append({
            "ref": record["ref"],
            "name": name_of(record["ref"]),
            "arity": record["arity"],
            "language": record.get("language") or bundle.get("language"),
            "ladder": record["ladder"],
            "vector": list(record["vector"]),
            "determinism": state,
            "determinism_detail": detail,
        })
    reporter.finish()
    census = bundle.get("census") or {}
    return {
        "ladderpin": PIN_VERSION,
        "assay_bundle": bundle.get("assay_bundle"),
        "language": bundle.get("language"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": entries,
        # WHAT WAS NOT PINNED, AND WHY. Without this, `check` cannot tell a function that
        # was never pinnable from one that was pinned and has since stopped being
        # probeable — and those are a shrug and a finding respectively.
        "not_pinned": dict(sorted({**{k: v for k, v in
                                      (census.get("skipped_refs") or {}).items()},
                                   **refused}.items())),
        "census": census,
        # WHAT WAS ACCEPTED, AND WHY. An intended behaviour change re-pinned by
        # `ladderpin accept` leaves its reason here rather than vanishing into a diff of
        # twenty-nine strings. `assay accept` writes findings into a baseline with a
        # reason for the same argument: an exemption nobody wrote a reason for is an
        # exemption nobody can review.
        "accepted": {},
    }


def write(pin, path):
    """Sorted keys and a trailing newline: a pin lands in a diff, and diffs are read."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pin, fh, indent=2, sort_keys=True)
        fh.write("\n")


def read(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("ladderpin") != PIN_VERSION:
        raise ValueError(
            f"{path} is not a ladderpin v{PIN_VERSION} document "
            f"(it says {data.get('ladderpin') if isinstance(data, dict) else 'nothing'})"
        )
    return data


def accept(pin, ref, record, reason, when=None):
    """Replace one entry's vector with the one measured now, and say why.

    The alternative is `ladderpin pin` again, which is what people do and which throws
    away every other pin in the file along with the record of what was decided. This
    changes one entry and leaves a sentence beside it.
    """
    for entry in pin["entries"]:
        if entry["ref"] != ref:
            continue
        entry["vector"] = list(record["vector"])
        entry["ladder"] = record["ladder"]
        entry["arity"] = record["arity"]
        entry["ref"] = record["ref"]
        entry["name"] = name_of(record["ref"])
        pin.setdefault("accepted", {})[record["ref"]] = {
            "reason": reason,
            "when": when or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "was": ref,
        }
        return True
    return False


def tally(pin):
    """{state: count} over the entries' determinism, for the report's denominator."""
    out = {}
    for entry in pin["entries"]:
        out[entry["determinism"]] = out.get(entry["determinism"], 0) + 1
    return out
