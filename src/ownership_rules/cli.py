"""Command line: ``ownership-rules evaluate | explain | vectors | case | rules``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, List, Optional

from . import __version__
from .engine import evaluate
from .model import InputError
from .rules import builtin_rules, load_rule
from .vectors import builtin_engine, command_engine, run_vectors


def _read_records(path: str) -> List[Any]:
    """Read a JSON array, a JSON object with ``edges``/``listed`` arrays, or JSON Lines."""
    fh = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        text = fh.read()
    finally:
        if fh is not sys.stdin:
            fh.close()
    text = text.strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise InputError(f"{path}:{i}: {e}") from None
    return out


def _evaluate(args) -> Any:
    edges = _read_records(args.edges)
    listed = _read_records(args.listed)
    entities = None
    if args.entities:
        with open(args.entities, encoding="utf-8") as f:
            entities = json.load(f)
    return evaluate(edges, listed, args.rule, entities=entities, as_of=args.as_of)


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="ownership-rules",
                                description="OFAC 50 Percent Rule and BIS Affiliates Rule as data, with proof paths.")
    p.add_argument("--version", action="version", version=f"ownership-rules {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("evaluate", "verdicts for every flagged entity as JSON"),
                        ("explain", "plain-text proof path for one entity")):
        e = sub.add_parser(name, help=help_)
        e.add_argument("--rule", default="ofac-50", help="built-in rule id or path to a rule JSON file")
        e.add_argument("--edges", required=True, help="edges file: JSON array or JSON Lines ('-' for stdin)")
        e.add_argument("--listed", required=True, help="listed parties file: JSON array or JSON Lines")
        e.add_argument("--entities", help="optional JSON object {id: {country: ...}}")
        e.add_argument("--as-of", help="evaluate on this day (YYYY-MM-DD)")
        if name == "evaluate":
            e.add_argument("--all", action="store_true", help="include entities whose verdict is not")
        else:
            e.add_argument("entity")

    v = sub.add_parser("vectors", help="run the conformance vectors")
    v.add_argument("--engine", help="external engine command (case JSON on stdin, results JSON on stdout)")
    v.add_argument("--path", help="suite file or directory (default: the packaged suites)")
    v.add_argument("--suite", action="append", help="only this suite (ofac-50, bis-affiliates, engine)")
    v.add_argument("--binary", action="store_true", help="compare covered yes/no only")
    v.add_argument("--no-proofs", action="store_true", help="do not compare proof_edges")
    v.add_argument("--json", action="store_true", help="print the report as JSON")

    sub.add_parser("case", help="vector protocol: read one case as JSON on stdin, print results JSON")

    r = sub.add_parser("rules", help="list the built-in rules or print one")
    r.add_argument("rule", nargs="?")

    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        if args.cmd == "evaluate":
            json.dump(_evaluate(args).to_json(include_not=args.all), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        if args.cmd == "explain":
            print(_evaluate(args)[args.entity].explain())
            return 0
        if args.cmd == "case":
            case = json.load(sys.stdin)
            if not isinstance(case, dict):
                raise InputError("case: expected a JSON object")
            wanted = case.get("entities_of_interest") or list(case.get("expect", {}))
            case = dict(case, expect={e: {} for e in wanted})
            json.dump(builtin_engine(case), sys.stdout)
            sys.stdout.write("\n")
            return 0
        if args.cmd == "rules":
            if args.rule:
                json.dump(load_rule(args.rule).raw, sys.stdout, indent=2)
                sys.stdout.write("\n")
            else:
                for rid in builtin_rules():
                    print(f"{rid}\t{load_rule(rid).title}")
            return 0
        reports = run_vectors(command_engine(args.engine) if args.engine else None, path=args.path,
                              binary=args.binary, check_proofs=not args.no_proofs, suites=args.suite)
        failed = [x for x in reports if not x.passed]
        if args.json:
            json.dump({"passed": len(reports) - len(failed), "failed": len(failed),
                       "cases": [x.__dict__ for x in reports]}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            for x in reports:
                print(f"{'ok  ' if x.passed else 'FAIL'} {x.suite}/{x.case}")
                for f in x.failures:
                    print(f"      {f}")
            print(f"{len(reports) - len(failed)}/{len(reports)} cases passed")
        return 1 if failed else 0
    except (InputError, ValueError, OSError) as e:
        print(f"ownership-rules: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
