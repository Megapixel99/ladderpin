"""Getting a tree's behavioural vectors, and making them portable.

`assay bundle PATHS` already emits every probed function's cross-language vector as one
JSON document. This module runs it, checks the envelope it got back, and does the one
thing that stands between that document and a pin somebody can commit: **it makes the
references relative**.

WHY THAT IS THE WHOLE MODULE. `assay` writes `ref` as an absolute path --
`/home/seth/proj/src/format.py::humanize` -- because it is describing the tree in front of
it. A pin is read on a different machine, in a different checkout, six months later. An
absolute ref makes every entry miss, and a run in which every entry misses reports no
changes, which is the shape of a passing run.

THE TWO HALVES DO NOT INVOKE EACH OTHER, and that constraint is inherited rather than
worked around. `assay` ships a Python binary and a JavaScript one, neither able to assume
the other is installed; each reads a tree in its own language. So the command is named
(`--assay`) rather than guessed, and a JavaScript tree is pinned by pointing this at the
JavaScript binary. One implementation covers both languages because what it consumes is a
document, not an API.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

BUNDLE_VERSION = 1


class BundleError(Exception):
    """`assay` could not be run, or did not answer with a bundle. Never a clean result."""


def relative_path(path, root):
    """`/abs/proj/src/a.py` -> `src/a.py`, or the path unchanged when it is not under root.

    A path outside the root is returned unchanged and is a real case: `assay bundle` can be
    pointed at a vendored tree or a sibling checkout. It is left absolute rather than
    turned into a pile of `../`, and the pin then simply will not match on another
    machine -- which is true, and better than matching the wrong file. THE CENSUS GOES
    THROUGH HERE TOO: a `../../vendor/x.py` key names a different file wherever the pin is
    read, and on Windows the bare `relpath` raises across drives, which would take a whole
    `pin` run down over a key nothing compares.
    """
    try:
        relative = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:                       # different drives on Windows
        return path
    if relative.startswith(".." + os.sep) or relative == "..":
        return path
    return relative.replace(os.sep, "/")


def relative_ref(ref, root):
    """`/abs/proj/src/a.py::f` -> `src/a.py::f`, when it is under `root`."""
    path, sep, name = ref.rpartition("::")
    if not sep:
        return ref
    relative = relative_path(path, root)
    if relative == path:
        return ref
    return f"{relative}::{name}"


def name_of(ref):
    return ref.rpartition("::")[2]


def path_of(ref):
    return ref.rpartition("::")[0]


def parse(text):
    """Validate the envelope, or refuse. A document that is not a bundle is not a zero."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise BundleError(f"`assay bundle` did not print JSON: {exc}")
    if not isinstance(data, dict) or "records" not in data:
        raise BundleError("that JSON is not an assay bundle: it has no `records`")
    version = data.get("assay_bundle")
    if version != BUNDLE_VERSION:
        # A bundle envelope this does not understand is refused rather than read
        # optimistically. Comparing a new answer against the wrong earlier answer is
        # precisely the defect a difference checker exists to catch.
        raise BundleError(
            f"this is an assay_bundle v{version} and ladderpin reads v{BUNDLE_VERSION}; "
            f"upgrade ladderpin or pin with the matching assay"
        )
    if data.get("error"):
        raise BundleError(f"`assay bundle` reported an error: {data['error']}")
    return data


def normalise(bundle, root):
    """A bundle with every ref made relative to `root`, records and census alike."""
    out = dict(bundle)
    out["records"] = [dict(r, ref=relative_ref(r["ref"], root)) for r in bundle["records"]]
    census = dict(bundle.get("census") or {})
    census["skipped_refs"] = {relative_ref(k, root): v
                              for k, v in (census.get("skipped_refs") or {}).items()}
    census["unloadable_paths"] = {relative_path(k, root): v
                                  for k, v in (census.get("unloadable_paths") or {}).items()}
    out["census"] = census
    return out


def split_command(command):
    """`--assay` as a shell word list, without eating a Windows path's backslashes.

    `shlex.split` in POSIX mode reads `C:\\venv\\Scripts\\assay.exe` as
    `C:venvScriptsassay.exe` and the user is then told this tool cannot run a path they
    never typed. The venv-local binary is exactly the case `--assay` exists for.
    """
    if os.name == "nt":
        return [part.strip('"') for part in shlex.split(command, posix=False)]
    return shlex.split(command)


def collect(paths, assay_cmd="assay", root=".", timeout=900):
    """Run `assay bundle` over `paths` and return the normalised document."""
    # PATHS ARE RESOLVED WHERE THE USER TYPED THEM, not where `--root` points. `assay` runs
    # with `cwd=root` so the refs it writes are rooted there; a bare `src/` handed straight
    # through would then mean `root/src/`, a directory nobody named, and the run dies
    # reporting nothing to pin under a path that exists.
    command = split_command(assay_cmd) + ["bundle", *[os.path.abspath(p) for p in paths]]
    try:
        finished = subprocess.run(command, capture_output=True, text=True,
                                  timeout=timeout, cwd=root)
    except FileNotFoundError:
        raise BundleError(
            f"cannot run {command[0]!r}. `assay` is a separate install: "
            f"`pip install assay-checks` for a Python tree, `npm install -g assay-checks` "
            f"for a JavaScript one — and name it with --assay if it is not on PATH"
        )
    except subprocess.TimeoutExpired:
        raise BundleError(f"`{' '.join(command)}` did not finish within {timeout}s")
    if finished.returncode != 0 and not finished.stdout.strip():
        raise BundleError(
            f"`{' '.join(command)}` exited {finished.returncode} and printed no bundle:\n"
            f"{finished.stderr.strip()[:600]}"
        )
    return normalise(parse(finished.stdout), root)
