"""Command line: ``ownership-rules evaluate | explain | vectors | case | rules``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, List, Optional

from . import __version__
from .engine import evaluate
from .formats import from_bods, from_ftm
from .model import InputError
from .rules import builtin_rules, load_rule
from .vectors import builtin_engine, command_engine, run_vectors, summary


def _read_records(path: str, key: Optional[str] = None) -> List[Any]:
    """Read a JSON array, a JSON object holding a ``key`` array (``edges`` or ``listed``, so one file can serve
    both options), or JSON Lines."""
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
        return list(json.loads(text))
    if text[0] == "{":
        try:
            whole = json.loads(text)
        except ValueError:
            whole = None  # JSON Lines: several objects, one per line
        if isinstance(whole, dict):
            if key and key in whole:
                if not isinstance(whole[key], list):
                    raise InputError(f"{path}: {key} must be an array")
                return list(whole[key])
            if key and ("edges" in whole or "listed" in whole):
                raise InputError(f"{path}: the object has no {key} array")
            return [whole]  # a single record
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise InputError(f"{path}:{i}: {e}") from None
    return out


def _evaluate(args: argparse.Namespace) -> Any:
    sources = [x for x in (args.edges, args.ftm, args.bods) if x]
    if len(sources) != 1:
        raise InputError("give exactly one of --edges, --ftm or --bods")
    if not args.listed and not args.ftm:
        raise InputError("--listed is required with --edges and --bods")
    listed = _read_records(args.listed, "listed") if args.listed else []
    entities: Any = {}
    if args.ftm:
        g = from_ftm(_read_records(args.ftm), directorships=args.directorships)
        edges, listed, entities = g.edges, g.listed + listed, g.entities
    elif args.bods:
        g = from_bods(_read_records(args.bods), listed=listed)
        edges, entities = g.edges, g.entities
    else:
        edges = _read_records(args.edges, "edges")
    if args.entities:
        with open(args.entities, encoding="utf-8") as f:
            extra = json.load(f)
        if not isinstance(extra, dict):
            raise InputError(f"{args.entities}: expected a JSON object {{id: {{country: ...}}}}")
        entities = dict(entities, **extra)
    return evaluate(edges, listed, args.rule, entities=entities or None, as_of=args.as_of)


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="ownership-rules",
                                description="OFAC 50 Percent Rule and BIS Affiliates Rule as data, with proof paths.")
    p.add_argument("--version", action="version", version=f"ownership-rules {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("evaluate", "verdicts for every flagged entity as JSON"),
                        ("explain", "plain-text proof path for one entity")):
        e = sub.add_parser(name, help=help_)
        e.add_argument("--rule", default="ofac-50", help="built-in rule id or path to a rule JSON file")
        e.add_argument("--edges",
                       help="edges file: JSON array, JSON Lines, or an object with an edges array ('-' for stdin)")
        e.add_argument("--ftm", help="instead of --edges: FollowTheMoney entities (an OpenSanctions entities.ftm.json, "
                                     "a yente or Aleph export); Ownership links, Sanction records and countries")
        e.add_argument("--bods", help="instead of --edges: Beneficial Ownership Data Standard statements (0.2 to 0.4); "
                                      "give --listed with the same ids")
        e.add_argument("--directorships", action="store_true",
                       help="with --ftm: read Directorship links as control (control_only verdicts)")
        e.add_argument("--listed",
                       help="listed parties file: JSON array, JSON Lines, or an object with a listed array "
                            "(optional with --ftm, which reads Sanction records)")
        e.add_argument("--entities", help="optional JSON object {id: {country: ...}}")
        e.add_argument("--as-of", help="evaluate on this day (YYYY-MM-DD)")
        if name == "evaluate":
            e.add_argument("--all", action="store_true", help="include entities whose verdict is not")
            e.add_argument("--proofs", choices=("inline", "shared", "none"), default="inline",
                           help="inline: full proof per result; shared: each step once in a steps table (use for "
                                "deep ownership chains); none: verdicts only")
        else:
            e.add_argument("entity")

    v = sub.add_parser("vectors", help="run the conformance vectors")
    v.add_argument("--engine", help="external engine command (case JSON on stdin, results JSON on stdout)")
    v.add_argument("--path", help="suite file or directory (default: the packaged suites)")
    v.add_argument("--suite", action="append", help="only this suite (ofac-50, bis-affiliates, engine)")
    v.add_argument("--binary", action="store_true", help="compare covered yes/no only")
    v.add_argument("--no-proofs", action="store_true", help="compare verdicts only (no proof_edges, seeds, most_restrictive)")
    v.add_argument("--json", action="store_true", help="print the report as JSON")

    sub.add_parser("case", help="vector protocol: read one case as JSON on stdin, print results JSON")

    r = sub.add_parser("rules", help="list the built-in rules or print one")
    r.add_argument("rule", nargs="?")

    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        if args.cmd == "evaluate":
            json.dump(_evaluate(args).to_json(include_not=args.all, proofs=args.proofs), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        if args.cmd == "explain":
            ev = _evaluate(args)
            print(ev[args.entity].explain())
            if ev.rule_status != "in_force":
                st = ev.rule.stay(ev.as_of)
                note = f": {st.cite}" if st and st.cite else ""
                print(f"note: {ev.rule.id} is {ev.rule_status.replace('_', ' ')} on {ev.as_of}{note}")
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
        s = summary(reports)
        if args.json:
            json.dump(dict(s, failed=len(failed), cases=[x.__dict__ for x in reports]), sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            for x in reports:
                print(f"{'ok  ' if x.passed else 'FAIL'} {x.suite}/{x.case}")
                for f in x.failures:
                    print(f"      {f}")
            line = f"{s['passed']}/{s['cases']} cases passed; verdicts {s['verdicts_passed']}/{s['cases']}"
            if s["proofs_checked"]:
                line += f", proofs {s['proofs_passed']}/{s['proofs_checked']}"
            print(line)
        return 1 if failed else 0
    except (InputError, ValueError, OSError) as e:
        print(f"ownership-rules: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
