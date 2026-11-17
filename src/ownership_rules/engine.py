"""The rule engine: listed parties + ownership edges + a rule -> a verdict and proof path per entity.

Verdicts, strongest first:

``listed``        the entity is itself a seed of the rule (named on a list the rule starts from).
``definitely``    the lower bound of covered ownership meets the threshold.
``possibly``      only the upper bound meets it (interval shares), or a covered party owns a share of unknown
                  size (BIS Red Flag 29: determine the percentage or treat the entity as covered).
``control_only``  a covered or possibly covered party controls the entity, or owns it through edges with no
                  percentage that could reach the threshold, but nothing shows ownership at the threshold.
                  OFAC FAQ 398 and BIS FAQ 43: control alone does not trigger the rule; it calls for review.
``not``           nothing reaches the threshold.

The cascade evaluation is the least fixed point of a monotone function over that lattice, computed in rounds
(each round reads only the previous round's verdicts). Ownership cycles therefore terminate and cannot justify
themselves, and every proof refers only to verdicts reached in earlier rounds, so proofs are finite and acyclic.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .model import Edge, ListedParty, Share, edges_from, entities_from, listed_from, parse_date
from .rules import Rule, load_rule

__all__ = ["evaluate", "merge_edges", "Evaluation", "Result", "VERDICTS", "COVERED", "MAX_ROUNDS"]

NOT, CONTROL_ONLY, POSSIBLY, DEFINITELY, LISTED = range(5)
VERDICTS = ("not", "control_only", "possibly", "definitely", "listed")
COVERED = ("definitely", "listed")
_LEVEL = {v: i for i, v in enumerate(VERDICTS)}
MAX_ROUNDS = 100_000


def _r(v: float) -> Any:
    v = round(v, 6)
    return int(v) if float(v).is_integer() else v


@dataclass
class Result:
    """The verdict for one entity under one rule."""

    entity: str
    verdict: str
    share: Tuple[float, float] = (0.0, 0.0)
    reasons: Tuple[str, ...] = ()
    owners: List[Dict[str, Any]] = field(default_factory=list)
    proof: List[Dict[str, Any]] = field(default_factory=list)
    seeds: Tuple[str, ...] = ()
    most_restrictive: Tuple[str, ...] = ()
    round: int = 0

    @property
    def covered(self) -> bool:
        """True for ``listed`` and ``definitely``."""
        return self.verdict in COVERED

    @property
    def proof_edges(self) -> List[Tuple[str, str]]:
        """Every (owner, owned) pair the proof path uses, sorted."""
        out: Set[Tuple[str, str]] = set()
        for step in self.proof:
            for v in step.get("via", ()):
                out.add((v["owner"], step["entity"]))
        return sorted(out)

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"entity": self.entity, "verdict": self.verdict}
        if self.verdict != "listed":
            d["share"] = [_r(self.share[0]), _r(self.share[1])]
        if self.reasons:
            d["reasons"] = list(self.reasons)
        if self.seeds:
            d["seeds"] = list(self.seeds)
        if self.most_restrictive:
            d["most_restrictive"] = list(self.most_restrictive)
        if self.proof:
            d["proof"] = self.proof
            d["proof_edges"] = [list(p) for p in self.proof_edges]
        return d

    def explain(self) -> str:
        """The proof path as plain text, one line per step."""
        if self.verdict == "not":
            return f"{self.entity}: not covered (covered share {_r(self.share[0])}-{_r(self.share[1])}%)"
        lines = []
        for step in self.proof:
            if step["verdict"] == "listed":
                lists = ", ".join(step.get("lists", ())) or "list"
                progs = step.get("programs")
                lines.append(f"{step['entity']}: listed ({lists}{'; ' + ', '.join(progs) if progs else ''})")
                continue
            parts = []
            for v in step["via"]:
                pct = v.get("pct")
                if pct is None:
                    amount = "controls" if v.get("control") else "owns an unknown share of"
                else:
                    amount = f"owns {pct if not isinstance(pct, list) else f'{pct[0]}-{pct[1]}'}% of"
                parts.append(f"{v['owner']} ({v['owner_verdict']}) {amount} {step['entity']}")
            share = step.get("share")
            lines.append(f"{step['entity']}: {step['verdict']} (covered share {share[0]}-{share[1]}%) because "
                         + "; ".join(parts))
        return "\n".join(lines)


class Evaluation:
    """Results of one rule over one graph. ``ev[entity]`` returns a :class:`Result` (``not`` for unknown ids)."""

    def __init__(self, rule: Rule, results: Dict[str, Result], as_of: Optional[_dt.date], rounds: int):
        self.rule = rule
        self.results = results
        self.as_of = as_of
        self.rounds = rounds

    def __getitem__(self, entity: str) -> Result:
        r = self.results.get(entity)
        return r if r is not None else Result(entity=entity, verdict="not")

    def __contains__(self, entity: str) -> bool:
        return entity in self.results

    def verdict(self, entity: str) -> str:
        return self[entity].verdict

    def flagged(self, include_listed: bool = False) -> List[Result]:
        """Entities whose verdict is not ``not`` (and, by default, not ``listed``), strongest first."""
        out = [r for r in self.results.values()
               if r.verdict != "not" and (include_listed or r.verdict != "listed")]
        out.sort(key=lambda r: (-_LEVEL[r.verdict], r.entity))
        return out

    def to_json(self, include_not: bool = False) -> Dict[str, Any]:
        return {
            "rule": self.rule.id,
            "rule_version": self.rule.version,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "results": {k: r.to_json() for k, r in sorted(self.results.items())
                        if include_not or r.verdict != "not"},
        }


def _edge_ref(e: Edge, owner_level: int) -> Dict[str, Any]:
    d: Dict[str, Any] = {"owner": e.owner, "owner_verdict": VERDICTS[owner_level],
                         "pct": e.share.to_json() if e.share else None}
    if e.control:
        d["control"] = True
    if e.source:
        d["source"] = e.source
    if e.as_of:
        d["as_of"] = e.as_of.isoformat()
    return d


def evaluate(edges: Iterable[Any], listed: Iterable[Any], rule: Any = "ofac-50", *,
             entities: Optional[Mapping[str, Any]] = None, as_of: Any = None,
             max_rounds: int = MAX_ROUNDS) -> Evaluation:
    """Apply ``rule`` to the ownership graph.

    ``edges`` are :class:`~ownership_rules.model.Edge` objects or dicts (``owner``, ``owned``, ``pct``, ``control``,
    ``source``, ``as_of``, ``valid_from``, ``valid_to``). ``listed`` are :class:`ListedParty` objects, dicts or bare
    ids. ``entities`` optionally maps ids to facts such as ``country`` (used by rules limited to foreign entities).
    ``as_of`` (``YYYY-MM-DD``) evaluates edges and listings in force on that day; without it, edges and listings
    that have a ``valid_to`` / ``listed_to`` are treated as ended.
    """
    r = load_rule(rule)
    day = parse_date(as_of, "as_of")
    es = [e for e in edges_from(edges) if e.active(day)]
    ls = [p for p in listed_from(listed) if p.active(day)]
    ents = entities_from(entities)
    if r.attribution == "multiplicative":
        return _evaluate_multiplicative(es, ls, r, ents, day, max_rounds)
    return _evaluate_cascade(es, ls, r, ents, day, max_rounds)


def merge_edges(es: Sequence[Edge]) -> List[Edge]:
    """One edge per (owner, owned) pair. Several reports of the same pair describe one holding: the share becomes
    the hull of the known shares (lowest lo, highest hi), ``control`` is true if any report says so, and a report
    with no percentage adds nothing when another report has one. Sum share classes yourself before passing them in.
    """
    groups: Dict[Tuple[str, str], List[Edge]] = defaultdict(list)
    for e in es:
        groups[(e.owner, e.owned)].append(e)
    out = []
    for (owner, owned), g in groups.items():
        if len(g) == 1:
            out.append(g[0])
            continue
        g.sort(key=_edge_key)
        known = [e.share for e in g if e.share is not None]
        share = Share(min(s.lo for s in known), max(s.hi for s in known)) if known else None
        sources = [e.source for e in g if e.source]
        dates = [e.as_of for e in g if e.as_of]
        out.append(Edge(owner=owner, owned=owned, share=share, control=any(e.control for e in g),
                        source="; ".join(dict.fromkeys(sources)) or None, as_of=max(dates) if dates else None,
                        extra=(("reports", len(g)),)))
    return out


def _edge_key(e: Edge):
    return json.dumps(e.to_json(), sort_keys=True, default=str)


def _index(es: Sequence[Edge]):
    incoming: Dict[str, List[Edge]] = defaultdict(list)
    outgoing: Dict[str, Set[str]] = defaultdict(set)
    for e in merge_edges(es):
        incoming[e.owned].append(e)
        outgoing[e.owner].add(e.owned)
    for v in incoming.values():
        v.sort(key=lambda e: e.owner)
    return incoming, outgoing


def _seed_map(ls: Sequence[ListedParty], r: Rule) -> Dict[str, ListedParty]:
    seeds: Dict[str, ListedParty] = {}
    for p in ls:
        if r.is_seed(p):
            prev = seeds.get(p.id)
            if prev is None:
                seeds[p.id] = p
            else:  # the same id on several lists: merge
                rank = [x for x in (prev.restrictiveness, p.restrictiveness) if x is not None]
                seeds[p.id] = ListedParty(
                    id=p.id, lists=tuple(dict.fromkeys(prev.lists + p.lists)),
                    programs=tuple(dict.fromkeys(prev.programs + p.programs)), entry_type=prev.entry_type,
                    country=prev.country or p.country, restrictiveness=max(rank) if rank else None,
                    name=prev.name or p.name)
    return seeds


def _assess(t: str, inc: Sequence[Edge], level: Dict[str, int], r: Rule, in_scope: bool):
    """New level for ``t`` from the owners' current levels, with the share bounds, reasons and justification."""
    lo = hi = review = 0.0
    unknown = control = False
    for e in inc:
        lv = level.get(e.owner, NOT)
        if lv >= DEFINITELY and e.share:
            lo += e.share.lo
        if lv >= POSSIBLY:
            if e.share:
                hi += e.share.hi
            elif not e.control:
                unknown = True
        if lv >= CONTROL_ONLY:
            review += e.share.hi if e.share else 100.0
            if e.control:
                control = True
    lo, hi = min(lo, 100.0), min(hi, 100.0)
    reasons: List[str] = []
    if not in_scope:
        new = NOT
        if lo or hi or unknown or control:
            reasons.append("out_of_scope")
    elif r.meets(lo):
        new = DEFINITELY
    elif r.meets(hi):
        new, reasons = POSSIBLY, ["share_interval"]
    elif unknown:
        new = POSSIBLY if r.unknown_share == "possibly" else CONTROL_ONLY
        reasons = ["unknown_share"]
    elif control or r.meets(review):
        new = CONTROL_ONLY
        reasons = (["control"] if control else []) + (["unknown_share_via_control"] if r.meets(review) and not control else [])
    else:
        new = NOT
    if new == DEFINITELY:
        via = [e for e in inc if level.get(e.owner, NOT) >= DEFINITELY and e.share]
    elif new == POSSIBLY:
        via = [e for e in inc if level.get(e.owner, NOT) >= POSSIBLY and (e.share or not e.control)]
    elif new == CONTROL_ONLY:
        via = [e for e in inc if level.get(e.owner, NOT) >= CONTROL_ONLY]
    else:
        via = []
    return new, (lo, hi), reasons, [_edge_ref(e, level.get(e.owner, NOT)) for e in via]


def _evaluate_cascade(es, ls, r: Rule, ents, day, max_rounds) -> Evaluation:
    incoming, outgoing = _index(es)
    seeds = _seed_map(ls, r)
    level: Dict[str, int] = {s: LISTED for s in seeds}
    # (entity, level) -> (round, share, reasons, via)
    just: Dict[Tuple[str, int], Tuple[int, Tuple[float, float], List[str], List[Dict[str, Any]]]] = {}
    for s in seeds:
        just[(s, LISTED)] = (0, (100.0, 100.0), [], [])
    dirty: Set[str] = set()
    for s in seeds:
        dirty |= outgoing.get(s, set())
    rnd = 0
    while dirty:
        rnd += 1
        if rnd > max_rounds:
            raise RuntimeError(f"no fixed point after {max_rounds} rounds")
        updates = {}
        for t in sorted(dirty):
            if t in seeds:
                continue
            new, share, reasons, via = _assess(t, incoming.get(t, ()), level, r, r.in_scope(ents.get(t, {})))
            if new > level.get(t, NOT):
                updates[t] = (new, share, reasons, via)
        dirty = set()
        for t, (new, share, reasons, via) in updates.items():
            level[t] = new
            just[(t, new)] = (rnd, share, reasons, via)
            dirty |= outgoing.get(t, set())
    results: Dict[str, Result] = {}
    for t in set(level) | set(incoming):
        lv = level.get(t, NOT)
        if lv == NOT:
            new, share, reasons, _ = _assess(t, incoming.get(t, ()), level, r, r.in_scope(ents.get(t, {})))
            results[t] = Result(entity=t, verdict="not", share=share, reasons=tuple(reasons),
                                owners=[_edge_ref(e, level.get(e.owner, NOT)) for e in incoming.get(t, ())])
            continue
        res = _build_result(t, lv, just, seeds, incoming, level, r)
        if lv != LISTED:
            # The proof keeps the shares seen when each verdict was reached; the result reports the final ones.
            final, share, reasons, _ = _assess(t, incoming.get(t, ()), level, r, r.in_scope(ents.get(t, {})))
            if final != lv:  # pragma: no cover - would mean the iteration stopped before the fixed point
                raise RuntimeError(f"internal error: {t} settled at {VERDICTS[lv]} but assesses as {VERDICTS[final]}")
            res.share, res.reasons = share, tuple(reasons)
        results[t] = res
    return Evaluation(r, results, day, rnd)


def _build_result(t, lv, just, seeds, incoming, level, r: Rule) -> Result:
    rnd, share, reasons, _ = just[(t, lv)]
    # Steps are keyed by (entity, level): each cites owners at the level they had one round earlier, so the
    # proof is a DAG ordered by round even when the ownership graph has cycles.
    steps: Dict[Tuple[str, int], Dict[str, Any]] = {}
    stack = [(t, lv)]
    while stack:
        node, nlv = stack.pop()
        if (node, nlv) in steps:
            continue
        nr, nshare, nreasons, via = just[(node, nlv)]
        if nlv == LISTED:
            p = seeds[node]
            step: Dict[str, Any] = {"entity": node, "verdict": "listed", "round": 0, "lists": list(p.lists)}
            if p.programs:
                step["programs"] = list(p.programs)
            if p.name:
                step["name"] = p.name
        else:
            step = {"entity": node, "verdict": VERDICTS[nlv], "round": nr,
                    "share": [_r(nshare[0]), _r(nshare[1])], "via": via}
            if nreasons:
                step["reasons"] = list(nreasons)
            for v in via:
                stack.append((v["owner"], _LEVEL[v["owner_verdict"]]))
        steps[(node, nlv)] = step
    proof = [steps[k] for k in sorted(steps, key=lambda k: (steps[k]["round"], k[0], k[1]))]
    seed_ids = tuple(sorted(s["entity"] for s in proof if s["verdict"] == "listed"))
    most: Tuple[str, ...] = ()
    if r.most_restrictive and lv != LISTED:
        ranked = [(seeds[s].restrictiveness, s) for s in seed_ids if seeds[s].restrictiveness is not None]
        if ranked:
            top = max(x for x, _ in ranked)
            most = tuple(sorted(s for x, s in ranked if x == top))
    return Result(entity=t, verdict=VERDICTS[lv], share=share if lv != LISTED else (100.0, 100.0),
                  reasons=tuple(reasons),
                  owners=[_edge_ref(e, level.get(e.owner, NOT)) for e in incoming.get(t, ())],
                  proof=proof, seeds=seed_ids if lv != LISTED else (), most_restrictive=most, round=rnd)


def _evaluate_multiplicative(es, ls, r: Rule, ents, day, max_rounds) -> Evaluation:
    """Effective (look-through) ownership: stakes multiply along chains and add across paths."""
    incoming, outgoing = _index(es)
    seeds = _seed_map(ls, r)
    nodes = sorted(set(incoming) | set(outgoing) | set(seeds))
    lo = {n: (100.0 if n in seeds else 0.0) for n in nodes}
    hi = dict(lo)
    rev = dict(lo)
    tol = 1e-9
    rnd = 0
    dirty = set()
    for s in seeds:
        dirty |= outgoing.get(s, set())
    while dirty:
        rnd += 1
        if rnd > max_rounds:
            raise RuntimeError(f"no fixed point after {max_rounds} rounds")
        nxt: Set[str] = set()
        for t in sorted(dirty):
            if t in seeds or not r.in_scope(ents.get(t, {})):
                continue
            nlo = nhi = nrev = 0.0
            for e in incoming.get(t, ()):
                if e.share:
                    nlo += e.share.lo * lo[e.owner] / 100.0
                    nhi += e.share.hi * hi[e.owner] / 100.0
                    nrev += e.share.hi * rev[e.owner] / 100.0
                else:
                    if not e.control:
                        nhi += hi[e.owner]
                    nrev += rev[e.owner]
            nlo, nhi, nrev = min(nlo, 100.0), min(nhi, 100.0), min(nrev, 100.0)
            if nlo > lo[t] + tol or nhi > hi[t] + tol or nrev > rev[t] + tol:
                lo[t], hi[t], rev[t] = max(lo[t], nlo), max(hi[t], nhi), max(rev[t], nrev)
                nxt |= outgoing.get(t, set())
        dirty = nxt
    results: Dict[str, Result] = {}
    for t in nodes:
        if t in seeds:
            p = seeds[t]
            results[t] = Result(entity=t, verdict="listed", share=(100.0, 100.0),
                                proof=[{"entity": t, "verdict": "listed", "round": 0, "lists": list(p.lists)}])
            continue
        inc = incoming.get(t, ())
        control = any(e.control and rev[e.owner] > 0 for e in inc)
        reasons: List[str] = []
        if not r.in_scope(ents.get(t, {})):
            v = "not"
            if any(rev[e.owner] > 0 for e in inc):
                reasons = ["out_of_scope"]
        elif r.meets(lo[t]):
            v = "definitely"
        elif r.meets(hi[t]):
            v = "possibly"
            reasons = ["unknown_share"] if any(e.share is None and not e.control for e in inc) else ["share_interval"]
        elif control or r.meets(rev[t]):
            v, reasons = "control_only", ["control"] if control else ["unknown_share_via_control"]
        else:
            v = "not"
        res = Result(entity=t, verdict=v, share=(lo[t], hi[t]), reasons=tuple(reasons),
                     owners=[_edge_ref(e, NOT) for e in inc])
        if v != "not":
            res.proof, res.seeds = _multiplicative_proof(t, incoming, seeds, lo, hi, rev)
        results[t] = res
    return Evaluation(r, results, day, rnd)


def _multiplicative_proof(t, incoming, seeds, lo, hi, rev):
    """The ancestor subgraph that carries covered ownership into ``t``, each node with its effective share."""
    steps = []
    seen = set()
    stack = [t]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in seeds:
            steps.append({"entity": n, "verdict": "listed", "round": 0, "lists": list(seeds[n].lists)})
            continue
        via = []
        for e in incoming.get(n, ()):
            if rev[e.owner] > 0:
                d = _edge_ref(e, LISTED if e.owner in seeds else NOT)
                d.pop("owner_verdict")
                d["owner_effective"] = [_r(lo[e.owner]), _r(hi[e.owner])]
                via.append(d)
                stack.append(e.owner)
        steps.append({"entity": n, "verdict": "effective", "share": [_r(lo[n]), _r(hi[n])], "via": via})
    steps.reverse()
    return steps, tuple(sorted(s["entity"] for s in steps if s["verdict"] == "listed"))

