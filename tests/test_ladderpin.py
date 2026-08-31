"""What a pin holds, what a check settles, and what it refuses to settle.

The comparison tests below run on synthetic documents and are fast. The end-to-end tests
drive a real `assay` over a real tree and skip when it is not installed — a skip and a
pass are identical in a tally, so CI asserts they ran.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ladderpin import (AMBIGUOUS, ARITY, CHANGED, EXPIRED, HELD, MISSING,  # noqa: E402
                       UNPINNED, UNPROBEABLE, BundleError, compare, exit_code,
                       first_difference, parse, summarise)
from ladderpin.bundle import normalise, relative_ref, split_command        # noqa: E402
from ladderpin.pin import UNCHECKED, accept                                # noqa: E402


def has_assay():
    try:
        import assay  # noqa: F401
    except ImportError:
        return False
    return True


def has_nondet():
    try:
        import nondet  # noqa: F401
    except ImportError:
        return False
    return True


ASSAY_CMD = f"{sys.executable} -m assay.cli"

EXAMPLE = """\
def total(items):
    return sum(item["price"] * item["quantity"] for item in items)


def slugify(text):
    return "-".join(str(text).lower().split())


def truncate(text, limit):
    return text[:limit]


def label(text):
    return str(text).upper()
"""

# assay probes this happily — it returns a string, which the interlingua can state — and
# the string's contents depend on `PYTHONHASHSEED`. It is the whole argument for the
# `nondet` edge in one function.
FLAKY = """\
def fingerprint(text):
    return str(set(str(text)))


def label(text):
    return str(text).upper()
"""


def entry(ref, ladder="cross1/v3/aaaa", vector=("V:1", "V:2"), arity=1, name=None):
    return {"ref": ref, "name": name or ref.rpartition("::")[2], "arity": arity,
            "language": "python", "ladder": ladder, "vector": list(vector),
            "determinism": "deterministic", "determinism_detail": ""}


def record(ref, ladder="cross1/v3/aaaa", vector=("V:1", "V:2"), arity=1):
    return {"ref": ref, "arity": arity, "ladder": ladder, "vector": list(vector),
            "language": "python", "error": None, "assay_probe": 1}


def bundle_of(records, skipped=None):
    return {"assay_bundle": 1, "language": "python", "error": None,
            "records": list(records),
            "census": {"skipped_refs": dict(skipped or {})}}


def pin_of(entries):
    return {"ladderpin": 1, "entries": list(entries), "not_pinned": {}}


class MakingRefsPortable(unittest.TestCase):
    """The one thing between an assay bundle and a document somebody can commit."""

    def test_a_ref_under_the_root_becomes_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = os.path.join(tmp, "src", "a.py") + "::f"
            self.assertEqual(relative_ref(ref, tmp), "src/a.py::f")

    def test_a_ref_outside_the_root_is_left_alone_rather_than_made_into_dot_dots(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.realpath("/tmp/definitely-elsewhere/a.py") + "::f"
            self.assertEqual(relative_ref(outside, os.path.join(tmp, "deep", "root")),
                             outside)

    def test_a_census_path_outside_the_root_is_left_alone_like_a_ref(self):
        """The census goes through the same rule the refs do.

        A `../../vendor/x.py` key names a different file wherever the pin is read, and the
        bare `relpath` this used to call raises across Windows drives — which would take a
        whole `pin` run down over a key nothing compares.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            os.makedirs(os.path.join(root, "src"))
            inside = os.path.join(root, "src", "a.py")
            outside = os.path.join(tmp, "vendor", "b.py")
            bundle = {"assay_bundle": 1, "records": [],
                      "census": {"unloadable_paths": {inside: "syntax error",
                                                      outside: "syntax error"}}}
            paths = normalise(bundle, root)["census"]["unloadable_paths"]
            self.assertEqual(sorted(paths), sorted(["src/a.py", outside]))

    def test_a_windows_assay_path_keeps_its_backslashes(self):
        """POSIX splitting reads `C:\\venv\\Scripts\\assay.exe` as `C:venvScriptsassay.exe`
        and then reports that this tool cannot run a path nobody typed."""
        command = r"C:\venv\Scripts\assay.exe"
        with unittest.mock.patch("ladderpin.bundle.os.name", "nt"):
            self.assertEqual(split_command(command), [command])
        self.assertEqual(split_command("assay"), ["assay"])
        self.assertEqual(split_command(f"{sys.executable} -m assay.cli"),
                         [sys.executable, "-m", "assay.cli"])

    def test_a_document_that_is_not_a_bundle_is_refused(self):
        for text in ('{"hello": 1}', "not json at all",
                     '{"assay_bundle": 99, "records": []}',
                     '{"assay_bundle": 1, "records": [], "error": "boom"}'):
            with self.subTest(text=text[:30]), self.assertRaises(BundleError):
                parse(text)


class TheComparison(unittest.TestCase):
    def test_an_identical_vector_is_held(self):
        outcomes = compare(pin_of([entry("src/a.py::f")]),
                           bundle_of([record("src/a.py::f")]))
        self.assertEqual([o.state for o in outcomes], [HELD])

    def test_a_moved_rung_is_a_finding_and_names_the_rung(self):
        outcomes = compare(pin_of([entry("src/a.py::f", vector=("V:1", "V:2"))]),
                           bundle_of([record("src/a.py::f", vector=("V:1", "V:9"))]))
        self.assertEqual(outcomes[0].state, CHANGED)
        self.assertEqual((outcomes[0].rung, outcomes[0].was, outcomes[0].now),
                         (1, "V:2", "V:9"))

    def test_first_difference_reports_a_length_change_rather_than_nothing(self):
        self.assertIsNone(first_difference(["a", "b"], ["a", "b"]))
        self.assertEqual(first_difference(["a"], ["a", "b"]),
                         (1, "<1 rungs>", "<2 rungs>"))

    def test_a_ladder_version_bump_is_expired_and_never_a_change(self):
        """Two vectors on different ladders are answers to different questions."""
        outcomes = compare(pin_of([entry("src/a.py::f", ladder="cross1/v3/aaaa")]),
                           bundle_of([record("src/a.py::f", ladder="cross1/v4/bbbb",
                                             vector=("V:9", "V:9"))]))
        self.assertEqual(outcomes[0].state, EXPIRED)
        self.assertIn("re-pin", outcomes[0].detail)

    def test_an_arity_change_is_its_own_look_because_the_vectors_are_different_lengths(self):
        outcomes = compare(
            pin_of([entry("src/a.py::f", ladder="cross1/v3/aaaa", arity=1)]),
            bundle_of([record("src/a.py::f", ladder="cross2/v3/cccc", arity=2)]))
        self.assertEqual(outcomes[0].state, ARITY)

    def test_a_file_that_moved_is_matched_by_name_and_is_not_a_change(self):
        outcomes = compare(pin_of([entry("src/a.py::f")]),
                           bundle_of([record("src/util/a.py::f")]))
        self.assertEqual(outcomes[0].state, HELD)
        self.assertEqual(outcomes[0].moved_to, "src/util/a.py::f")

    def test_a_move_that_also_changed_is_still_a_finding(self):
        """The move must not swallow the change; the control for the test above."""
        outcomes = compare(pin_of([entry("src/a.py::f", vector=("V:1", "V:2"))]),
                           bundle_of([record("src/util/a.py::f", vector=("V:1", "V:3"))]))
        self.assertEqual(outcomes[0].state, CHANGED)
        self.assertIn("moved to", outcomes[0].detail)

    def test_two_candidates_for_a_move_are_ambiguous_rather_than_guessed(self):
        """Guessing is how a checker reports a finding about code nobody meant."""
        outcomes = compare(pin_of([entry("src/a.py::f")]),
                           bundle_of([record("one/a.py::f"), record("two/a.py::f")]))
        states = [o.state for o in outcomes]
        self.assertIn(AMBIGUOUS, states)
        self.assertEqual(states.count(UNPINNED), 2)

    def test_a_function_assay_now_refuses_is_a_look_and_never_a_pass(self):
        """The worst outcome this tool could produce is calling that one clean."""
        outcomes = compare(pin_of([entry("src/a.py::f")]),
                           bundle_of([], skipped={"src/a.py::f": "calls open()"}))
        self.assertEqual(outcomes[0].state, UNPROBEABLE)
        self.assertIn("calls open()", outcomes[0].detail)

    def test_a_record_assay_errored_on_is_a_look_and_never_a_vector(self):
        """`pin` refuses these at pinning time; reading one here as though it held a
        vector dies on the missing `ladder`, and an uncaught KeyError leaves this process
        with exit 1 — which in this tool's contract means a pinned function changed."""
        errored = {"ref": "src/a.py::f", "arity": 1, "language": "python",
                   "error": "probe raised TypeError"}
        outcomes = compare(pin_of([entry("src/a.py::f")]), bundle_of([errored]))
        self.assertEqual([o.state for o in outcomes], [UNPROBEABLE])
        self.assertIn("TypeError", outcomes[0].detail)

    def test_a_deleted_function_is_not_matched_to_one_another_entry_pins(self):
        """Two files defining `f`, one of them deleted. Reading the entries in order, the
        deleted one used to claim the survivor as a move and report `held` — scoring a
        deleted function clean against somebody else's vector."""
        outcomes = compare(
            pin_of([entry("src/a.py::f"), entry("src/b.py::f")]),
            bundle_of([record("src/b.py::f")]))
        self.assertEqual([(o.ref, o.state) for o in outcomes],
                         [("src/a.py::f", MISSING), ("src/b.py::f", HELD)])
        self.assertEqual(exit_code(outcomes, 2), 0)

    def test_a_pinned_function_that_is_simply_gone_is_a_look(self):
        outcomes = compare(pin_of([entry("src/a.py::f")]), bundle_of([]))
        self.assertEqual(outcomes[0].state, MISSING)

    def test_a_new_function_is_reported_as_unpinned_rather_than_ignored(self):
        outcomes = compare(pin_of([]), bundle_of([record("src/a.py::new")]))
        self.assertEqual([o.state for o in outcomes], [UNPINNED])

    def test_only_a_change_fails_the_run(self):
        """Every look is a question for a person, and a question that fails a build is a
        question nobody answers."""
        for state_bundle in (bundle_of([], skipped={"src/a.py::f": "calls open()"}),
                             bundle_of([record("src/a.py::f", ladder="cross1/v4/b")]),
                             bundle_of([])):
            outcomes = compare(pin_of([entry("src/a.py::f")]), state_bundle)
            with self.subTest(state=outcomes[0].state):
                self.assertEqual(exit_code(outcomes, 1), 0)
        changed = compare(pin_of([entry("src/a.py::f", vector=("V:1",))]),
                          bundle_of([record("src/a.py::f", vector=("V:2",))]))
        self.assertEqual(exit_code(changed, 1), 1)

    def test_a_pin_with_no_entries_is_a_could_not_measure_and_not_a_pass(self):
        """It compares nothing and prints `0 changed`, which is the shape of a clean run."""
        self.assertEqual(exit_code([], 0), 2)
        self.assertEqual(exit_code([], 1), 0)

    def test_summarise_counts_every_state_it_was_given(self):
        outcomes = compare(pin_of([entry("src/a.py::f"), entry("src/a.py::g")]),
                           bundle_of([record("src/a.py::f"),
                                      record("src/a.py::g", vector=("V:9", "V:9")),
                                      record("src/a.py::h")]))
        self.assertEqual(summarise(outcomes),
                         {HELD: 1, CHANGED: 1, UNPINNED: 1})


class WhatAcceptWritesBack(unittest.TestCase):
    """The document half of `accept`, which needs no assay."""

    def test_the_determinism_verdict_travels_with_the_vector(self):
        """It is a statement about the vector in the file, not about the entry's name. An
        accept that took the gate off leaving `deterministic` behind is the entry claiming
        a check that never ran against what it now holds."""
        pin = pin_of([entry("src/a.py::f", vector=("V:1", "V:2"))])
        self.assertEqual(pin["entries"][0]["determinism"], "deterministic")
        moved = record("src/a.py::f", vector=("V:1", "V:9"))
        self.assertTrue(accept(pin, "src/a.py::f", moved, "rounds up now",
                               determinism=(UNCHECKED, "the determinism check was "
                                                       "turned off")))
        self.assertEqual(pin["entries"][0]["vector"], ["V:1", "V:9"])
        self.assertEqual(pin["entries"][0]["determinism"], UNCHECKED)
        self.assertIn("turned off", pin["entries"][0]["determinism_detail"])
        self.assertEqual(pin["accepted"]["src/a.py::f"]["reason"], "rounds up now")

    def test_a_ref_that_is_not_in_the_pin_is_not_accepted(self):
        pin = pin_of([entry("src/a.py::f")])
        self.assertFalse(accept(pin, "src/gone.py::f", record("src/gone.py::f"), "no"))
        self.assertEqual(pin.get("accepted", {}), {})


@unittest.skipUnless(has_assay(), "assay-checks is not installed")
class EndToEnd(unittest.TestCase):
    """A real assay over a real tree. Everything above is arithmetic on documents."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.module = os.path.join(self.src, "prices.py")
        with open(self.module, "w") as fh:
            fh.write(EXAMPLE)
        self.pin = os.path.join(self.tmp, "pin.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "ladderpin.cli", *args],
                              capture_output=True, text=True, cwd=ROOT,
                              env=dict(os.environ,
                                       PYTHONPATH=os.pathsep.join(
                                           [ROOT, os.environ.get("PYTHONPATH", "")])))

    def _pin(self, *extra):
        return self._run("pin", self.src, "-o", self.pin, "--root", self.tmp,
                         "--assay", ASSAY_CMD, *extra)

    def _check(self, *extra):
        return self._run("check", self.pin, self.src, "--root", self.tmp,
                         "--assay", ASSAY_CMD, *extra)

    def _accept(self, *extra):
        return self._run("accept", self.pin, self.src, "--root", self.tmp,
                         "--assay", ASSAY_CMD, *extra)

    def _break_slugify(self):
        with open(self.module, "w") as fh:
            fh.write(EXAMPLE.replace('"-".join', '"_".join'))

    def test_a_pin_holds_what_assay_probed_and_names_what_it_refused(self):
        out = self._pin()
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(self.pin) as fh:
            document = json.load(fh)
        self.assertEqual(sorted(e["name"] for e in document["entries"]),
                         ["label", "slugify", "truncate"])
        # `total` takes a list of dicts and no rung in the ladder discriminates it, so
        # assay never probed it. That is named, not silently absent.
        self.assertTrue(any("total" in ref for ref in document["not_pinned"]))

    def test_an_unchanged_tree_holds_and_a_changed_one_does_not(self):
        """THE DIVERGENCE GATE. Asserted together, because a checker that always said
        `held` would pass the first alone and one that always said `changed` the second."""
        self._pin()
        clean = self._check()
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn("3 held", clean.stdout)

        with open(self.module, "w") as fh:
            fh.write(EXAMPLE.replace('"-".join', '"_".join'))
        dirty = self._check("-q")
        self.assertEqual(dirty.returncode, 1)
        self.assertIn("changed", dirty.stdout)
        self.assertIn("slugify", dirty.stdout)
        self.assertIn("rung", dirty.stdout)

    def test_a_moved_file_is_not_a_behaviour_change(self):
        self._pin()
        moved = os.path.join(self.src, "util")
        os.makedirs(moved)
        shutil.move(self.module, os.path.join(moved, "prices.py"))
        out = self._check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("moved to", out.stdout)

    def test_a_function_assay_can_no_longer_probe_is_not_reported_as_clean(self):
        self._pin()
        with open(self.module, "w") as fh:
            fh.write(EXAMPLE.replace(
                "def label(text):\n    return str(text).upper()",
                'def label(text):\n    open("/dev/null", "w").write("")\n'
                "    return str(text).upper()"))
        out = self._check("-q")
        self.assertEqual(out.returncode, 0)
        self.assertIn("unprobeable", out.stdout)
        self.assertIn("1 not settled", out.stdout)

    def test_a_tree_with_nothing_probeable_refuses_to_write_a_pin(self):
        """A pin with no entries reports `0 changed` forever."""
        with open(self.module, "w") as fh:
            fh.write("def nothing():\n    return 1\n")
        out = self._pin()
        self.assertEqual(out.returncode, 2)
        self.assertIn("nothing to pin", out.stderr)
        self.assertFalse(os.path.exists(self.pin))

    def test_json_carries_the_outcomes_and_the_exit_code_it_will_return(self):
        """Something deciding what to do next should not have to run the process again
        to learn what it decided."""
        self._pin()
        self._break_slugify()
        out = self._check("--json")
        self.assertEqual(out.returncode, 1)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["exit"], 1)
        self.assertEqual(payload["pinned"], 3)
        self.assertEqual(payload["summary"]["changed"], 1)
        changed = [o for o in payload["outcomes"] if o["state"] == "changed"]
        self.assertEqual(len(changed), 1)
        self.assertIn("slugify", changed[0]["ref"])
        self.assertGreaterEqual(changed[0]["rung"], 0)

    def test_accept_records_the_reason_and_the_next_check_is_clean(self):
        """The alternative is re-pinning, which throws away every other pin in the file
        along with the record of what was decided."""
        self._pin()
        self._break_slugify()
        self.assertEqual(self._check("-q").returncode, 1)

        accepted = self._accept("--all", "--reason", "slugs use underscores now")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("slugify", accepted.stdout)

        after = self._check("-q")
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        with open(self.pin) as fh:
            document = json.load(fh)
        entry = next(iter(document["accepted"].values()))
        self.assertEqual(entry["reason"], "slugs use underscores now")
        # ...and the other two pins are untouched, which is the whole difference from
        # re-pinning.
        self.assertEqual(len(document["entries"]), 3)
        self.assertEqual(len(document["accepted"]), 1)

    def test_accept_refuses_an_entry_that_did_not_change(self):
        """Rewriting a vector with the identical vector leaves a reason beside a decision
        nobody made.

        The pin must come back BYTE FOR BYTE, not merely with an empty `accepted` map: a
        version that wrote the document anyway would satisfy the weaker assertion and
        would still have rewritten timestamps and ordering into somebody's diff for a
        command that refused to do anything.
        """
        self._pin()
        with open(self.pin) as fh:
            before = fh.read()
        self._break_slugify()
        out = self._accept("--ref", "src/prices.py::label", "--reason", "no")
        self.assertEqual(out.returncode, 1)
        self.assertIn("is `held`, not `changed`", out.stdout)
        with open(self.pin) as fh:
            self.assertEqual(fh.read(), before)

    @unittest.skipUnless(has_nondet(), "nondet is not installed")
    def test_accept_refuses_a_function_that_has_become_nondeterministic(self):
        """It would be accepted today and report as changed tomorrow, with a reason
        beside it saying the change was intended — the worst of both.

        `str(set(...))` is the shape: `assay` probes it happily because it returns a
        string, and the string's order depends on PYTHONHASHSEED.
        """
        self._pin()
        with open(self.module, "w") as fh:
            fh.write(EXAMPLE.replace(
                "def label(text):\n    return str(text).upper()",
                "def label(text):\n    return str(set(str(text)))"))
        # It reports as changed — assay compared it and got a different vector — and that
        # is exactly the entry a careless `--all` would sweep into the pin.
        self.assertEqual(self._check("-q").returncode, 1)
        with open(self.pin) as fh:
            before = fh.read()
        out = self._accept("--all", "--reason", "new fingerprint format")
        self.assertIn("nondet now refuses it", out.stdout)
        with open(self.pin) as fh:
            self.assertEqual(fh.read(), before)

    def test_the_control_a_deterministic_change_is_accepted(self):
        """If `accept` refused everything, the test above would pass on a broken gate."""
        self._pin()
        self._break_slugify()
        out = self._accept("--all", "--reason", "underscores now")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("refused", out.stdout)

    def test_accept_requires_a_reason(self):
        """An exemption nobody wrote a reason for is one nobody can review."""
        self._pin()
        self._break_slugify()
        out = self._run("accept", self.pin, self.src, "--root", self.tmp,
                        "--assay", ASSAY_CMD, "--all")
        self.assertEqual(out.returncode, 2)
        self.assertIn("--reason", out.stderr)

    def test_accept_on_a_clean_tree_says_so_rather_than_writing_anything(self):
        self._pin()
        with open(self.pin) as fh:
            before = fh.read()
        out = self._accept("--all", "--reason", "nothing to do")
        self.assertEqual(out.returncode, 0)
        self.assertIn("nothing to accept", out.stdout)
        with open(self.pin) as fh:
            self.assertEqual(fh.read(), before)

    def test_show_prints_the_entries_and_the_refusals(self):
        self._pin()
        out = self._run("show", self.pin)
        self.assertEqual(out.returncode, 0)
        self.assertIn("slugify", out.stdout)
        self.assertIn("not pinned", out.stdout)


@unittest.skipUnless(has_assay(), "assay-checks is not installed")
class TheDeterminismGate(unittest.TestCase):
    """The `nondet` edge, and what happens without it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "mod.py"), "w") as fh:
            fh.write(FLAKY)
        self.pin = os.path.join(self.tmp, "pin.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pin(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "ladderpin.cli", "pin", self.src, "-o", self.pin,
             "--root", self.tmp, "--assay", ASSAY_CMD, *extra],
            capture_output=True, text=True, cwd=ROOT,
            env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                [ROOT, os.environ.get("PYTHONPATH", "")])))

    @unittest.skipUnless(has_nondet(), "nondet is not installed")
    def test_a_function_whose_vector_moves_between_processes_is_refused(self):
        """assay probes `str(set(...))` happily: it returns a string, and the string's
        order depends on PYTHONHASHSEED. Pinning it makes a check that fails at random."""
        out = self._pin()
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(self.pin) as fh:
            document = json.load(fh)
        self.assertEqual([e["name"] for e in document["entries"]], ["label"])
        refusal = next(v for k, v in document["not_pinned"].items() if "fingerprint" in k)
        self.assertIn("witness", refusal)

    def test_without_the_gate_the_same_function_is_pinned_and_the_pin_is_flaky(self):
        """THE CONTROL, and it is the argument for the dependency.

        Turn the gate off and `fingerprint` is pinned. Nothing about the file then
        changes, and the very next check reports it as changed — because the vector was
        never a property of the code. That is the failure the edge exists to prevent, and
        it is reproduced here rather than described.
        """
        out = self._pin("--no-determinism-check")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(self.pin) as fh:
            document = json.load(fh)
        self.assertIn("fingerprint", [e["name"] for e in document["entries"]])
        self.assertTrue(all(e["determinism"] == "unchecked" for e in document["entries"]))
        self.assertIn("NOT checked for determinism", out.stdout)

        # Not a single byte of the tree changed between the pin and this check.
        states = set()
        for _ in range(6):
            check = subprocess.run(
                [sys.executable, "-m", "ladderpin.cli", "check", self.pin, self.src,
                 "--root", self.tmp, "--assay", ASSAY_CMD, "-q"],
                capture_output=True, text=True, cwd=ROOT,
                env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                    [ROOT, os.environ.get("PYTHONPATH", "")])))
            states.add(check.returncode)
            if 1 in states:
                break
        self.assertIn(1, states,
                      "six checks of an unchanged tree never reported the unpinnable "
                      "function as changed — either hash randomisation is off, or the "
                      "premise of the nondet gate is wrong and this test should say so")


if __name__ == "__main__":
    unittest.main()
