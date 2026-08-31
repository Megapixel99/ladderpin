r"""ladderpin — freeze what a function answers, and find out when that changes.

    from ladderpin import collect, build, compare

`assay bundle` emits every probed function's behavioural vector. `assay sweep --against`
compares two trees NOW and the finding is sameness, because the question is duplication.
Commit the bundle and the same substrate answers a different question — did this
function's behaviour change when nobody meant it to? — and the finding becomes difference.
"""

from .bundle import BundleError, collect, normalise, parse
from .compare import (AMBIGUOUS, ARITY, CHANGED, EXPIRED, FINDINGS, HELD, LOOKS,
                      MISSING, UNPINNED, UNPROBEABLE, Outcome, compare, exit_code,
                      first_difference, summarise)
from .pin import CHECKED, REFUSED, UNCHECKED, build, read, tally, write

__all__ = ["collect", "parse", "normalise", "BundleError", "build", "read", "write",
           "tally", "compare", "summarise", "exit_code", "first_difference", "Outcome",
           "HELD", "CHANGED", "EXPIRED", "ARITY", "UNPROBEABLE", "MISSING", "UNPINNED",
           "AMBIGUOUS", "FINDINGS", "LOOKS", "CHECKED", "UNCHECKED", "REFUSED"]
__version__ = "0.1.0"
