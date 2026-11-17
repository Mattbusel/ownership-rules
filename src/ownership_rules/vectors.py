"""Conformance vectors: load the suites and check an engine against them.

A suite is a JSON file with ``suite``, ``version`` and ``cases``. Each case has ``id``, ``rule``, ``listed``,
``edges``, optional ``entities`` and ``as_of``, a ``citation`` (document, URL and the quoted text the case
encodes) and ``expect``: ``{entity: {"verdict": ..., "proof_edges": [[owner, owned], ...], "seeds": [...],
"most_restrictive": [...]}}``. Only ``verdict`` is required in an expectation; the other keys are checked when
present in both the expectation and the engine's answer.

Any engine can be checked, in any language, through a command: it receives one case (without ``expect``) as JSON
on stdin and prints ``{"results": {entity: {"verdict": ..., "proof_edges": [...]}}}``. Engines with a yes/no
answer can print ``"covered": true|false`` instead of a verdict and run in binary mode.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from .engine import COVERED, evaluate
from .model import InputError

__all__ = ["vector_dir", "load_suites", "iter_cases", "check_case", "run_vectors", "CaseReport", "command_engine",
           "builtin_engine"]


def vector_dir() -> str:
    """Directory holding the packaged suites (``ofac/``, ``bis/``, ``engine/``)."""
    here = os.path.join(os.path.dirname(__file__), "conformance")
    if os.path.isdir(here):
        return here
    # Source checkout: the suites live at the repository root.
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "vectors"))


def load_suites(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load every ``*.json`` suite under ``path`` (default: the packaged suites), sorted by file name."""
    root = path or vector_dir()
    files: List[str] = []
    if os.path.isfile(root):
        files = [root]
    else:
        for d, _, names in os.walk(root):
            files += [os.path.join(d, n) for n in names if n.endswith(".json")]
    suites = []
    for f in sorted(files):
        with open(f, encoding="utf-8") as fh:
            s = json.load(fh)
        if not isinstance(s, dict) or not isinstance(s.get("cases"), list):
            raise InputError(f"{f}: not a vector suite (needs a cases array)")
        s["_file"] = f
        suites.append(s)
    return suites


def iter_cases(suites: Iterable[Dict[str, Any]]):
    for s in suites:
        for c in s["cases"]:
            yield s.get("suite", ""), c


def builtin_engine(case: Dict[str, Any]) -> Dict[str, Any]:
    """This package's engine in the vector protocol."""
    for key in ("edges", "listed", "rule"):
        if key not in case:
            raise InputError(f"case {case.get('id', '?')}: missing {key}")
    if not isinstance(case["edges"], list) or not isinstance(case["listed"], list):
        raise InputError(f"case {case.get('id', '?')}: edges and listed must be arrays")
    ev = evaluate(case["edges"], case["listed"], case["rule"], entities=case.get("entities"), as_of=case.get("as_of"))
    out = {}
    for ent in case.get("expect", {}):
        r = ev[ent]
        out[ent] = {"verdict": r.verdict, "proof_edges": [list(p) for p in r.proof_edges],
                    "seeds": list(r.seeds), "most_restrictive": list(r.most_restrictive)}
    return {"results": out}


def command_engine(cmd: str, timeout: float = 30.0) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Wrap an external command as an engine: case JSON on stdin, results JSON on stdout."""
    # On Windows CreateProcess parses the command line itself; elsewhere split it like a POSIX shell would.
    argv: Any = cmd if os.name == "nt" else shlex.split(cmd)

    def run(case: Dict[str, Any]) -> Dict[str, Any]:
        payload = {k: v for k, v in case.items() if k != "expect"}
        payload["entities_of_interest"] = sorted(case.get("expect", {}))
        p = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"engine exited {p.returncode}: {p.stderr.strip()[:500]}")
        try:
            return json.loads(p.stdout)
        except ValueError:
            raise RuntimeError(f"engine printed invalid JSON: {p.stdout[:200]!r}") from None

    return run


@dataclass
class CaseReport:
    suite: str
    case: str
    passed: bool
    failures: List[str] = field(default_factory=list)


def check_case(case: Dict[str, Any], answer: Dict[str, Any], binary: bool = False,
               check_proofs: bool = True) -> List[str]:
    """Compare one engine answer with the case's expectations. Returns a list of failure messages."""
    fails = []
    results = (answer or {}).get("results") or {}
    for ent, exp in sorted(case.get("expect", {}).items()):
        got = results.get(ent)
        if got is None:
            got = {"verdict": "not", "covered": False}
        if binary:
            want = exp["verdict"] in COVERED
            have = got.get("covered")
            if have is None:
                have = got.get("verdict") in COVERED
            if bool(have) != want:
                fails.append(f"{ent}: expected covered={want}, got covered={bool(have)}")
            continue
        if got.get("verdict") != exp["verdict"]:
            fails.append(f"{ent}: expected {exp['verdict']}, got {got.get('verdict')}")
            continue
        for key in ("proof_edges", "seeds", "most_restrictive"):
            if key == "proof_edges" and not check_proofs:
                continue
            if key in exp and key in got:
                want_set = sorted(map(tuple, exp[key])) if key == "proof_edges" else sorted(exp[key])
                have_set = sorted(map(tuple, got[key])) if key == "proof_edges" else sorted(got[key])
                if want_set != have_set:
                    fails.append(f"{ent}: {key} expected {want_set}, got {have_set}")
    return fails


def run_vectors(engine: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None, path: Optional[str] = None,
                binary: bool = False, check_proofs: bool = True, suites: Optional[List[str]] = None
                ) -> List[CaseReport]:
    """Run every case through ``engine`` (default: this package) and report pass/fail per case."""
    engine = engine or builtin_engine
    reports = []
    for suite, case in iter_cases(load_suites(path)):
        if suites and suite not in suites:
            continue
        try:
            answer = engine(case)
            fails = check_case(case, answer, binary=binary, check_proofs=check_proofs)
        except Exception as e:  # report and keep going: one broken case must not hide the rest
            fails = [f"error: {type(e).__name__}: {e}"]
        reports.append(CaseReport(suite, case["id"], not fails, fails))
    return reports
