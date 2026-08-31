# `ladderpin`

**Freeze what a function answers. Find out when that changes.**

```sh
pip install ladderpin

ladderpin pin src/ -o behaviour.pin.json     # commit this
ladderpin check behaviour.pin.json src/      # in CI, from then on
```

```
FINDINGS — 1:
  changed      src/prices.py::slugify
               rung 14: V:"hello,-world!"  ->  V:"hello,_world!"
               same ladder (cross1/v3/d3b2ba61ccb7), different answer — READ it; only a
               person decides whether this change was intended

3 pinned function(s): 2 held, 1 changed, 0 not settled
```

## `assay bundle`, read in the other direction

[`assay`](https://github.com/Megapixel99/assay-checks) already emits every probed
function's behavioural vector as one JSON document, and already compares a tree against
one. Both are **cross-sectional** — two trees, now — and the finding is **sameness**,
because the question `assay sweep` asks is duplication.

Commit that document and the same substrate answers a different question:

> **did this function's behaviour change when nobody meant it to?**

The polarity flips, and everything downstream flips with it. A refactor that keeps the
tests green and moves a vector is the finding. A rewrite in the other language is checked
against the original's *measured* behaviour rather than against tests somebody wrote twice.

**This is not a snapshot test.** Jest snapshots and `approvaltests` freeze *your* output,
in *your* language, against inputs you chose. `assay`'s ladder is a **shared, versioned
input document**, so a pin taken from a Python tree is comparable with a JavaScript one —
which is the property `assay cross` was built on and which no per-project snapshot can have.

## What is stored, and why the vectors are not hashed

A digest would make the pin small and the report useless: `check` could say a function
changed and not *which of its twenty-nine rungs* did. The rung is the actionable part —
`V:"hello,-world!"` becoming `V:"hello,_world!"` names the input that told the two apart —
so the vectors are kept.

The pin also records **what was not pinned and why**. Without that, `check` cannot tell a
function that was never pinnable from one that was pinned and has since stopped being
probeable, and those are a shrug and a finding respectively.

## The determinism gate is the reason `nondet` is a dependency

This is the part that decides whether the tool is usable, and it is not obvious.

`assay` will happily probe this:

```python
def fingerprint(text):
    return str(set(str(text)))
```

It returns a **string**, which the interlingua can state, so it goes straight into the
bundle. And the string's order depends on `PYTHONHASHSEED`:

```
$ assay bundle src/ | grep -A1 fingerprint     # run twice
    V:"{'u', 'o', 'e', 'a', 'i'}"
    V:"{'e', 'u', 'a', 'i', 'o'}"
```

A pin on that function is a check that fails at random on the next machine, and the blame
lands on this tool. So every candidate is put to [`nondet`](https://github.com/Megapixel99/nondet)
before it is pinned:

```
$ ladderpin pin src/ -o pin.json
pinned 1 function(s) into pin.json
      1  deterministic
  1 function(s) were not pinned; `ladderpin show pin.json` names them and why

$ ladderpin show pin.json
not pinned — 1:
  src/mod.py::fingerprint — nondet found a witness: [-1] -> V:"{'1', '-'}" then V:"{'-', '1'}"
```

`nondet` addresses a function as `FILE::NAME` and re-runs it in fresh interpreters — which
is exactly the address `assay` records. That matters: `undetermined`, another package in
this network, was offered a `nondet` edge and **rejected it** because its observables were
closures with no such address, and a dependency that looks like a guarantee and is not is
worse than no dependency. Here the guarantee runs against the thing it is about.

**The control is a test rather than a paragraph.** `test_without_the_gate_the_same_function_is_pinned_and_the_pin_is_flaky`
turns the gate off, pins `fingerprint`, changes not one byte of the tree, and asserts that
a check reports it as `changed`. If that ever stops happening, the premise of this
dependency is wrong and the suite says so in those words.

Without `nondet` installed the pin is still written and every entry is marked
`unchecked` — **which is not a pass**, and the report says so, because a silent skip and a
clean check look identical in a tally.

## When the change was on purpose

The answer is not to re-pin. `ladderpin pin` again rewrites the whole file, throws away
every other pin in it, and leaves no record of what was decided — which is why the second
time somebody sees a red `check` they stop reading it.

```sh
ladderpin accept behaviour.pin.json src/ \
    --ref src/prices.py::slugify \
    --reason "slugs use underscores now, per the URL scheme change"
```

```
accepted 1 change(s) into behaviour.pin.json, with the reason recorded beside each:
  src/prices.py::slugify
```

`--all` accepts every entry this run reports as `changed`, which is the shape of an
intended refactor. **`--reason` is required** — `assay accept` writes findings into a
baseline with a reason for the same argument, and an exemption nobody wrote a reason for
is one nobody can review. The reason lands in the pin, beside the entry, where
`ladderpin show` prints it and a reviewer reads it in the diff:

```
accepted changes — 1:
  src/prices.py::slugify — slugs use underscores now, per the URL scheme change  (2026-08-31T16:47:53Z)
```

**Only what actually changed.** Accepting a `held` entry would rewrite a vector with the
identical vector and leave a reason beside a decision nobody made; accepting a `missing` or
`unprobeable` one cannot work, because there is no new vector to take. Both are refused by
name rather than skipped, so the command never reports having done something it did not do.

**And the determinism gate runs again on the way in.** A function that has *become*
nondeterministic must not be re-pinned: it would be accepted today, report as changed
tomorrow, and carry a reason beside it saying the change was intended — the worst of both.

## Three kinds of answer, and the third is what makes it usable

| verdict | means | fails the run |
|---|---|---|
| `held` | the vector is what it was | no |
| `changed` | the vector moved, **on the same ladder** | **yes** |
| `expired` | pinned on ladder `v3`, this tree probes `v4` — different questions | no |
| `arity` | pinned at one arity, now another; the vectors are different documents | no |
| `unprobeable` | `assay` now refuses it (it grew a call to `open()`) | no |
| `missing` / `unpinned` | a pinned function is gone, or a new one appeared | no |
| `ambiguous` | it moved, and two functions share its name and arity | no |

A tool that called every one of those a change would cry wolf on a file rename and be
deleted in a week. One that called them `held` would say *no behaviour changed* about
functions it never compared — which is `assay`'s own census rule:

> a report that says `differs none` while staying quiet about what was never compared is
> reporting *we never looked* as *we found none*.

**`unprobeable` is the one that matters most.** A pinned function that grows a side effect
drops out of the bundle entirely; scoring that clean is the worst outcome this tool could
produce, and it is what a `diff` of two bundles would do.

**A moved file is not a behaviour change.** A pinned entry absent at its old path but
present at exactly one new path, under the same name and arity, is matched there and the
move is reported in the detail. Two candidates is `ambiguous`, not a guess: picking one of
two `parse` functions is how a checker reports a finding about code nobody was thinking of.

## Why one implementation covers both languages

`assay` ships two halves because it *probes* code in two languages, and neither half can
assume the other is installed. This compares the **document** those halves emit, and one
reader is enough for both:

```sh
ladderpin pin js/src --assay assay -o js.pin.json     # the npm binary
ladderpin pin src/ -o py.pin.json                     # the PyPI one
```

The command is **named rather than guessed**, because both packages install a binary
called `assay` and a tool that guessed would pin a tree with the wrong prober.

The second reason for one implementation is `nondet`: it is Python-only, and it is the gate
that makes a pin safe to take at all. A JavaScript half would have to ship without it or
claim a check it never ran.

## Exit codes

`0` nothing pinned changed · `1` a pinned function answers differently · `2` this tool
settled nothing — no `assay`, an unreadable pin, or **a pin with no entries**.

That last one is not pedantry. A pin holding nothing compares nothing and prints
`0 changed`, which is the shape of a passing run and would stay green forever. `pin`
refuses to write one:

```
ladderpin: nothing to pin — assay probed no functions under src/.
           A pin with no entries reports `0 changed` on every run, which is
           indistinguishable from a passing one.
```

**The denominator is always printed.** A pin covering three functions and a pin covering
three hundred otherwise print the same clean line, and they are not the same result.

`ladderpin check --json` prints the outcomes, the summary and **the exit code it is about
to return**, because something deciding what to do next should not have to run the process
again to learn what it decided:

```json
{"pinned": 3, "summary": {"held": 2, "changed": 1}, "exit": 1,
 "outcomes": [{"ref": "src/prices.py::slugify", "state": "changed", "rung": 14,
               "was": "V:\"hello,-world!\"", "now": "V:\"hello,_world!\"", ...}]}
```

## `nondet` takes its time, and says so

Every candidate is re-run in fresh interpreters, which on a large tree is minutes. A tool
that prints nothing for four minutes is a tool people kill and then distrust, so `pin`
reports what it is doing:

```
[ladderpin] asking nondet about 3 function(s)
[ladderpin] nondet answered about 3 function(s) in 0.8s
```

Interactively it rewrites one line as it goes; in a log it prints the header and the
footer and nothing between, because a progress bar in CI output is three hundred lines of
carriage returns nobody can read. `--no-determinism-check` skips the whole thing, and every
entry is then marked `unchecked` — which is not a pass.

## Prior art

Swept across both registries on 2026-08-31. `keywords:characterization-test` and
`keywords:approval-tests` return **0 packages each** on npm; `keywords:cross-language` (35)
returns RPC libraries and
[`@particle-academy/fancy-conformance`](https://www.npmjs.com/package/@particle-academy/fancy-conformance),
which is one product's fixture table rather than a tool.

The real neighbours are approval testing (`approvaltests`, `jest` snapshots, `insta`) and
behavioural diffing (`crosshair diffbehavior`). Approval tests freeze your own output
against inputs you wrote, in one language, and cannot compare across a rewrite in another.
`crosshair diffbehavior` compares two Python functions **symbolically and right now** — it
is the better tool for *is this refactor equivalent*, and it is not a thing you commit and
re-check for two years. This is the across-time, across-language case, and it is cheap
because `assay` already did the hard part.

## Limits

- **`same` is worth one character.** The vector is a finite ladder of inputs. Two functions
  with identical vectors are not proved equal, and a change no rung reaches is a change
  this will not see. `assay`'s README argues that at length and it applies here unchanged.
- **`assay` is a separate install** and is not vendored: `pip install assay-checks`, or
  `npm install -g assay-checks` for a JavaScript tree. It is run as a subprocess.
- **Zero-arity functions and anything `assay` refuses are never pinned.** On a real tree
  that is most of it — `assay`'s own census on `trainingResearch/tools` probed 9 of 41.
  The refused list is written into the pin so the coverage is visible rather than implied.
- **A pin is per-ladder-version.** `assay` bumping `v3` to `v4` expires every pin taken
  before it, by design: the vectors are answers to a different set of inputs. That is the
  cost of the one-document design working as intended.
- **Python 3.9+.** Two dependencies, `assay-checks` and `nondet`, both of which assert zero
  dependencies of their own.

## Tests

```sh
python3 -m unittest discover -s tests
```

32 tests. Seventeen run on synthetic documents and are instant; fifteen drive a real
`assay` over a real tree and skip when it is not installed — CI asserts they were not skipped,
because a skip and a pass are identical in a tally.

**The divergence gate is one test**: an unchanged tree must hold *and* a changed one must
not, asserted together, so that a checker which always said `held` and one which always
said `changed` both fail it.

**Fourteen mutations were applied to the source and all fourteen were caught**: an unprobeable
function scored as held, a ladder bump read as a change, an ambiguous move guessed instead
of refused, a moved file reported as missing, an empty pin exiting clean, the `nondet` gate
accepting a nondeterministic function, refs left absolute so every entry misses on another
machine, a bundle envelope of the wrong version read anyway, a pin written when nothing was
probed, `accept` rewriting an entry that never changed, `accept` recording no reason,
`accept` re-pinning a function `nondet` now refuses, `accept` never writing the change it
accepted, and `--json` reporting an exit code other than the one it returns.

**Two survived their first run and both were real gaps in the new `accept` command.**
Nothing asked what happens when a pinned function has *become* nondeterministic — it would
be accepted today, report as changed tomorrow, and carry a reason beside it saying the
change was intended. And the refusal test asserted the `accepted` map was still empty
rather than that the file was byte for byte what it was, which a version that wrote the
document anyway would have satisfied while rewriting somebody's diff for a command that
refused to do anything.

**A third was wrong rather than surviving**, for the second time in this round: writing an
*unmodified* document produces identical bytes, so `if accepted:` → `if True:` changed
nothing observable and scored as SURVIVED. Inverting the guard instead — never persisting
what was accepted — is caught immediately. A mutation that cannot change behaviour reads as
a test gap, which is a mutation suite lying in the flattering direction.

**One of them was found by the mutation harness breaking, which is the joke telling
itself.** The harness edits a source file, runs the suite and restores it in a `finally` —
and the run exceeded its timeout and was SIGKILLed mid-mutation, so `finally` never ran and
`compare.py` was left with `if False:` where the empty-pin guard belongs. That is
[`restore-verified`](https://github.com/Megapixel99/restore-verified)'s fourth row exactly:
*"SIGKILL / a timeout — nothing in-process can help; the file stays broken."*

Nothing silently scored against the broken tree, because the harness checks that the suite
is green **before** the first mutation and refuses when it is not: `BASELINE IS RED — every
verdict below would be meaningless`. That is rule 1 of a mutation harness working on its
author. The CI job below runs `git diff --exit-code` after the tool, for the same reason
one level out.

## License

MIT
