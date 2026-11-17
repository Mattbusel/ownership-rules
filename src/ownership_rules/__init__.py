"""Ownership rules as data: the OFAC 50 Percent Rule and the BIS Affiliates Rule over an ownership graph.

>>> from ownership_rules import evaluate
>>> ev = evaluate(
...     edges=[{"owner": "X", "owned": "A", "pct": 50}, {"owner": "A", "owned": "B", "pct": 50}],
...     listed=[{"id": "X", "lists": ["SDN"]}],
...     rule="ofac-50",
... )
>>> ev["B"].verdict
'definitely'
>>> ev["B"].proof_edges
[('A', 'B'), ('X', 'A')]
"""

__version__ = "0.1.1"

from .engine import COVERED, VERDICTS, Evaluation, Result, evaluate  # noqa: E402
from .model import Edge, InputError, ListedParty, Share  # noqa: E402
from .rules import Rule, builtin_rules, load_rule  # noqa: E402
from .vectors import load_suites, run_vectors  # noqa: E402

__all__ = [
    "__version__",
    "evaluate",
    "Evaluation",
    "Result",
    "VERDICTS",
    "COVERED",
    "Edge",
    "ListedParty",
    "Share",
    "InputError",
    "Rule",
    "load_rule",
    "builtin_rules",
    "load_suites",
    "run_vectors",
]
