"""What changed, what did not, and what this cannot say either way.

THREE KINDS OF ANSWER, AND THE THIRD IS THE ONE THAT MAKES THE TOOL USABLE.

    held        the vector is what it was
    changed     the vector moved, on the same ladder — a FINDING
    look        something happened that a vector comparison cannot settle

A tool that reported every `look` as a change would cry wolf on a file rename and be
deleted within a week; one that reported them as `held` would say "no behaviour changed"
about functions it never compared, which is `assay`'s own census rule — *a report that
says `differs none` while staying quiet about what was never compared is reporting we
never looked as we found none*.

THE FOUR LOOKS ARE ALL REAL AND ALL COST SOMEBODY AN AFTERNOON ONCE.

  * **expired** — the pin was taken on ladder `v3` and this tree probes on `v4`. The two
    vectors are answers to different questions. Not a finding: re-pin.
  * **arity** — pinned at one arity, now another. `cross1` and `cross2` are different
    documents with different lengths, so the vectors cannot be compared at all. The
    signature changed, which a person already knows; this says the pin no longer covers it.
  * **unprobeable** — pinned, and `assay` now refuses to probe it (it grew a call to
    `open()`, it lost its arguments). The behaviour may have changed and nothing here can
    tell. A pass here would be the worst outcome the tool can produce.
  * **missing** / **unpinned** — a pinned function is gone, or a new one has appeared.

MOVING A FILE IS NOT A BEHAVIOUR CHANGE. A pinned entry that is absent at its old path but
present at exactly one new path, under the same name and arity, is matched there and the
move is reported in the detail. If more than one candidate matches, that is `ambiguous`
and is a `look` — guessing which of two `parse` functions the pin meant is how a checker
reports a finding about code nobody was thinking of.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HELD = "held"
CHANGED = "changed"
EXPIRED = "expired"
ARITY = "arity"
UNPROBEABLE = "unprobeable"
MISSING = "missing"
UNPINNED = "unpinned"
AMBIGUOUS = "ambiguous"

# `changed` is the only state that fails a run. Everything else is either fine or is a
# question for a person, and a question that fails the build is a question nobody answers.
FINDINGS = (CHANGED,)
LOOKS = (EXPIRED, ARITY, UNPROBEABLE, MISSING, UNPINNED, AMBIGUOUS)


@dataclass
class Outcome:
    ref: str
    state: str
    detail: str = ""
    rung: int = -1
    was: str = ""
    now: str = ""
    moved_to: str = ""

    def __str__(self):
        head = f"  {self.state:12} {self.ref}"
        if self.state == CHANGED:
            return (f"{head}\n"
                    f"               rung {self.rung}: {self.was}  ->  {self.now}\n"
                    f"               {self.detail}")
        return f"{head} — {self.detail}" if self.detail else head


def _shape(key):
    """`cross2/v3/2055cb` -> ('cross2', 'v3'). The two halves that must both match."""
    parts = str(key).split("/")
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def first_difference(was, now):
    """(index, was, now) for the first rung that moved, or None.

    Reported rather than a bare "it changed", because the rung is the actionable part:
    `V:"aeioua"` becoming `V:"aeiou"` names the input that told the two apart.
    """
    for i, (a, b) in enumerate(zip(was, now)):
        if a != b:
            return i, a, b
    if len(was) != len(now):
        return min(len(was), len(now)), f"<{len(was)} rungs>", f"<{len(now)} rungs>"
    return None


def compare(pin, bundle):
    """Every pinned entry, plus everything in the tree the pin does not cover."""
    records = {r["ref"]: r for r in bundle["records"]}
    skipped = (bundle.get("census") or {}).get("skipped_refs") or {}
    by_name = {}
    for ref, record in records.items():
        by_name.setdefault((record["ref"].rpartition("::")[2], record["arity"]),
                           []).append(record)

    outcomes = []
    matched = set()
    for entry in pin["entries"]:
        ref = entry["ref"]
        record = records.get(ref)
        moved_to = ""
        if record is None:
            if ref in skipped:
                outcomes.append(Outcome(ref, UNPROBEABLE,
                                        f"assay now refuses to probe it: {skipped[ref]}"))
                continue
            candidates = by_name.get((entry["name"], entry["arity"]), [])
            free = [c for c in candidates if c["ref"] not in matched]
            if len(free) == 1:
                record = free[0]
                moved_to = record["ref"]
            elif len(free) > 1:
                outcomes.append(Outcome(
                    ref, AMBIGUOUS,
                    f"gone from here, and {len(free)} functions share its name and arity "
                    f"({', '.join(sorted(c['ref'] for c in free))}) — which one the pin "
                    f"meant is not something a vector can decide"))
                continue
            else:
                outcomes.append(Outcome(
                    ref, MISSING,
                    "not in this tree, and no function elsewhere shares its name and "
                    "arity — deleted, renamed, or its file is not in the paths given"))
                continue

        matched.add(record["ref"])
        moved = f" (moved to {moved_to})" if moved_to else ""
        pinned_shape, current_shape = _shape(entry["ladder"]), _shape(record["ladder"])
        if pinned_shape[0] != current_shape[0]:
            outcomes.append(Outcome(
                ref, ARITY,
                f"pinned at arity {entry['arity']} ({pinned_shape[0]}) and is now "
                f"{record['arity']} ({current_shape[0]}); the two ladders are different "
                f"documents, so the vectors cannot be compared{moved}",
                moved_to=moved_to))
            continue
        if pinned_shape[1] != current_shape[1]:
            outcomes.append(Outcome(
                ref, EXPIRED,
                f"pinned on ladder {entry['ladder']} and this tree probes on "
                f"{record['ladder']}; those are answers to different questions, so "
                f"re-pin rather than reading this as a change{moved}",
                moved_to=moved_to))
            continue

        difference = first_difference(entry["vector"], record["vector"])
        if difference is None:
            outcomes.append(Outcome(ref, HELD,
                                    (f"moved to {moved_to}" if moved_to else ""),
                                    moved_to=moved_to))
        else:
            index, was, now = difference
            outcomes.append(Outcome(
                ref, CHANGED,
                f"same ladder ({entry['ladder']}), different answer{moved} — READ it; "
                f"only a person decides whether this change was intended",
                rung=index, was=was, now=now, moved_to=moved_to))

    for ref in sorted(records):
        if ref not in matched:
            outcomes.append(Outcome(ref, UNPINNED,
                                    "probed by assay and not in the pin — `ladderpin pin` "
                                    "again to cover it"))
    return outcomes


def summarise(outcomes):
    counts = {}
    for outcome in outcomes:
        counts[outcome.state] = counts.get(outcome.state, 0) + 1
    return counts


def exit_code(outcomes, pinned):
    """0 clean · 1 a pinned function changed · 2 the run settled nothing.

    THE THIRD CASE IS NOT PEDANTRY. A pin holding no entries compares nothing and prints
    `0 changed`, which is the shape of a passing run and would stay green forever. So an
    empty pin is a could-not-measure, with its own code, exactly as `didrun` gives
    did-not-run one rather than folding it into failure.
    """
    if pinned == 0:
        return 2
    return 1 if any(o.state in FINDINGS for o in outcomes) else 0
