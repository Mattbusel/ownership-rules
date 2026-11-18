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

Rules limited to foreign entities (``subject.scope = "foreign"``) report ``not`` (reason ``out_of_scope``) for a
home-country entity, but ownership still passes through it: a foreign entity that a listed party owns through a
home-country holding company is owned indirectly and is reached (the regulation says "directly or indirectly").
Proof steps for such intermediaries carry ``"out_of_scope": true``.

The cascade evaluation is the least fixed point of a monotone function over that lattice, computed in rounds
(each round reads only the previous round's verdicts). Ownership cycles therefore terminate and cannot justify
themselves, and every proof refers only to verdicts reached in earlier rounds, so proofs are finite and acyclic.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

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
    proof_keys: List[str] = field(default_factory=list, repr=False)

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

    def to_json(self, proofs: str = "inline") -> Dict[str, Any]:
        """``proofs``: ``"inline"`` (full steps), ``"shared"`` (the id of the root step in
        :meth:`Evaluation.to_json`'s ``steps`` table) or ``"none"``."""
        d: Dict[str, Any] = {"entity": self.entity, "verdict": self.verdict}
        if self.verdict != "listed":
            d["share"] = [_r(self.share[0]), _r(self.share[1])]
        if self.reasons:
            d["reasons"] = list(self.reasons)
        if self.seeds:
            d["seeds"] = list(self.seeds)
        if self.most_restrictive:
            d["most_restrictive"] = list(self.most_restrictive)
        if self.proof and proofs == "inline":
            d["proof"] = self.proof
            d["proof_edges"] = [list(p) for p in self.proof_edges]
        elif self.proof and proofs == "shared":
            d["proof_step"] = _step_id(self.entity, self._root_step()["verdict"])
        return d

    def _root_step(self) -> Dict[str, Any]:
        for st in self.proof:
            if st["entity"] == self.entity and st["verdict"] in (self.verdict, "effective"):
                return st
        return self.proof[-1]  # pragma: no cover

    def explain(self) -> str:
        """The proof path as plain text, one line per step."""
        if self.verdict == "not":
            why = " (outside the rule's scope)" if "out_of_scope" in self.reasons else ""
            return f"{self.entity}: not covered{why} (covered share {_r(self.share[0])}-{_r(self.share[1])}%)"
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
                ov = v['owner_verdict'] + (", out of scope" if v.get("owner_out_of_scope") else "")
                parts.append(f"{v['owner']} ({ov}) {amount} {step['entity']}")
            share = step.get("share") or (0, 0)
            head = f"{step['entity']}: {step['verdict']}"
            if step.get("out_of_scope"):
                head += " as an owner (out of the rule's scope itself, so reported as not)"
            lines.append(f"{head} (covered share {share[0]}-{share[1]}%) because " + "; ".join(parts))
        return "\n".join(lines)


class Evaluation:
    """Results of one rule over one graph. ``ev[entity]`` returns a :class:`Result` (``not`` for unknown ids)."""

    def __init__(self, rule: Rule, results: Dict[str, Result], as_of: Optional[_dt.date], rounds: int):
        self.rule = rule
        self.results = results
        self.as_of = as_of
        self.rounds = rounds

    @property
    def rule_status(self) -> str:
        """Whether the rule binds on ``as_of``: ``"in_force"``, ``"stayed"`` or ``"not_yet_effective"``
        (see :meth:`Rule.status`). The BIS Affiliates Rule is ``"stayed"`` until January 10, 2027; its verdicts are
        still computed, so a caller can show them as advisory until the stay ends."""
        return self.rule.status(self.as_of)

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

    def to_json(self, include_not: bool = False, proofs: str = "inline") -> Dict[str, Any]:
        """``proofs="inline"`` repeats every step in every result's proof, which grows with the square of the chain
        depth on long ownership chains. ``"shared"`` writes each step once in a top-level ``steps`` table (keyed
        ``"entity|verdict"``, each ``via`` entry naming its owner's ``step``) and gives each result only its
        ``proof_step``; the proof is every step reachable from it. ``"none"`` leaves proofs out."""
        if proofs not in ("inline", "shared", "none"):
            raise ValueError("proofs must be inline, shared or none")
        chosen = [(k, r) for k, r in sorted(self.results.items()) if include_not or r.verdict != "not"]
        out: Dict[str, Any] = {
            "rule": self.rule.id,
            "rule_version": self.rule.version,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "rule_status": self.rule_status,
        }
        if proofs == "shared":
            # Each step once, keyed "entity|verdict"; every via entry names the owner's step, so a result's proof is
            # everything reachable from its proof_step. The output grows with the graph, not with chain depth squared.
            steps: Dict[str, Any] = {}
            for _, r in chosen:
                if not r.proof:
                    continue
                if _step_id(r.entity, r._root_step()["verdict"]) in steps:
                    continue  # its whole proof is already in the table
                by_entity = {st["entity"]: _step_id(st["entity"], st["verdict"]) for st in r.proof}
                for st in r.proof:
                    sid = _step_id(st["entity"], st["verdict"])
                    if sid in steps:
                        continue
                    copy = dict(st)
                    if "via" in st:
                        copy["via"] = [dict(v, step=_step_id(v["owner"], v["owner_verdict"]) if "owner_verdict" in v
                                            else by_entity[v["owner"]]) for v in st["via"]]
                    steps[sid] = copy
            out["steps"] = dict(sorted(steps.items()))
        out["results"] = {k: r.to_json(proofs) for k, r in chosen}
        return out


def _edge_ref(e: Edge, owner_level: int, out_of_scope: Optional[Set[str]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"owner": e.owner, "owner_verdict": VERDICTS[owner_level],
                         "pct": e.share.to_json() if e.share else None}
    if out_of_scope and e.owner in out_of_scope and owner_level != NOT:
        d["owner_out_of_scope"] = True
    if e.control:
        d["control"] = True
    if e.source:
        d["source"] = e.source
    basis = e.basis
    if basis:
        d["basis"] = basis
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
    ``as_of`` (``YYYY-MM-DD``) evaluates edges and listings in force on that day; without it, today's date (UTC) is
    used, so an edge whose ``valid_to`` is still in the future counts and one whose ``valid_from`` is in the future
    does not.
    """
    r = load_rule(rule)
    day = parse_date(as_of, "as_of") or _dt.datetime.now(_dt.timezone.utc).date()
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


def _edge_key(e: Edge) -> str:
    return json.dumps(e.to_json(), sort_keys=True, default=str)


def _index(es: Sequence[Edge]) -> Tuple[Dict[str, List[Edge]], Dict[str, Set[str]]]:
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


def _assess(t: str, inc: Sequence[Edge], level: Dict[str, int], r: Rule, oos: Optional[Set[str]] = None
            ) -> Tuple[int, Tuple[float, float], List[str], List[Dict[str, Any]]]:
    """New level for ``t`` from the owners' current levels, with the share bounds, reasons and justification.

    The level ignores the rule's subject scope: it is the level ``t`` passes on to what it owns. The caller reports
    ``not`` for entities outside the scope."""
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
    if r.meets(lo):
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
    return new, (lo, hi), reasons, [_edge_ref(e, level.get(e.owner, NOT), oos) for e in via]


def _evaluate_cascade(es: List[Edge], ls: List[ListedParty], r: Rule, ents: Dict[str, Dict[str, Any]],
                      day: _dt.date, max_rounds: int) -> Evaluation:
    incoming, outgoing = _index(es)
    seeds = _seed_map(ls, r)
    nodes = set(incoming) | set(outgoing) | set(seeds)
    oos = {n for n in nodes if n not in seeds and not r.in_scope(ents.get(n, {}))}
    # ``level`` is the level an entity passes on to what it owns, computed as if every entity were in scope.
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
            new, share, reasons, via = _assess(t, incoming.get(t, ()), level, r, oos)
            if new > level.get(t, NOT):
                updates[t] = (new, share, reasons, via)
        dirty = set()
        for t, (new, share, reasons, via) in updates.items():
            level[t] = new
            just[(t, new)] = (rnd, share, reasons, via)
            dirty |= outgoing.get(t, set())
    results: Dict[str, Result] = {}
    step_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for t in set(level) | set(incoming):
        lv = level.get(t, NOT)
        owners = [_edge_ref(e, level.get(e.owner, NOT), oos) for e in incoming.get(t, ())]
        if lv == NOT or t in oos:
            _, share, reasons, _ = _assess(t, incoming.get(t, ()), level, r, oos)
            reasons = ["out_of_scope"] if t in oos and (lv != NOT or share[1] > 0) else []
            results[t] = Result(entity=t, verdict="not", share=share, reasons=tuple(reasons), owners=owners)
            continue
        res = _build_result(t, lv, just, seeds, r, oos, step_cache)
        res.owners = owners
        if lv != LISTED:
            # The proof keeps the shares seen when each verdict was reached; the result reports the final ones.
            final, share, reasons, _ = _assess(t, incoming.get(t, ()), level, r, oos)
            if final != lv:  # pragma: no cover - would mean the iteration stopped before the fixed point
                raise RuntimeError(f"internal error: {t} settled at {VERDICTS[lv]} but assesses as {VERDICTS[final]}")
            res.share, res.reasons = share, tuple(reasons)
        results[t] = res
    return Evaluation(r, results, day, rnd)


def _step(node: str, nlv: int, just: Dict[Tuple[str, int], Any], seeds: Mapping[str, ListedParty],
          oos: Set[str]) -> Dict[str, Any]:
    nr, nshare, nreasons, via = just[(node, nlv)]
    if nlv == LISTED:
        p = seeds[node]
        step: Dict[str, Any] = {"entity": node, "verdict": "listed", "round": 0, "lists": list(p.lists)}
        if p.programs:
            step["programs"] = list(p.programs)
        if p.name:
            step["name"] = p.name
        return step
    step = {"entity": node, "verdict": VERDICTS[nlv], "round": nr, "share": [_r(nshare[0]), _r(nshare[1])], "via": via}
    if nreasons:
        step["reasons"] = list(nreasons)
    if node in oos:
        step["out_of_scope"] = True
    return step


def _build_result(t: str, lv: int, just: Dict[Tuple[str, int], Any], seeds: Mapping[str, ListedParty], r: Rule,
                  oos: Set[str], step_cache: Dict[Tuple[str, int], Dict[str, Any]]) -> Result:
    rnd, share, reasons, _ = just[(t, lv)]
    # Steps are keyed by (entity, level): each cites owners at the level they had one round earlier, so the
    # proof is a DAG ordered by round even when the ownership graph has cycles. Step dicts are shared between
    # results (treat them as read-only).
    keys: Set[Tuple[str, int]] = set()
    stack = [(t, lv)]
    while stack:
        k = stack.pop()
        if k in keys:
            continue
        keys.add(k)
        step = step_cache.get(k)
        if step is None:
            step = step_cache[k] = _step(k[0], k[1], just, seeds, oos)
        for v in step.get("via", ()):
            stack.append((v["owner"], _LEVEL[v["owner_verdict"]]))
    order = sorted(keys, key=lambda k: (step_cache[k]["round"], k[0], k[1]))
    proof = [step_cache[k] for k in order]
    seed_ids = tuple(sorted(s["entity"] for s in proof if s["verdict"] == "listed"))
    most: Tuple[str, ...] = ()
    if r.most_restrictive and lv != LISTED:
        ranked = [(float(rk), s) for s in seed_ids for rk in (seeds[s].restrictiveness,) if rk is not None]
        if ranked:
            top = max(x for x, _ in ranked)
            most = tuple(sorted(s for x, s in ranked if x == top))
    return Result(entity=t, verdict=VERDICTS[lv], share=share if lv != LISTED else (100.0, 100.0),
                  reasons=tuple(reasons), proof=proof, seeds=seed_ids if lv != LISTED else (),
                  most_restrictive=most, round=rnd, proof_keys=[_step_id(k[0], VERDICTS[k[1]]) for k in order])


def _step_id(entity: str, verdict: str) -> str:
    return f"{entity}|{verdict}"


def _evaluate_multiplicative(es: List[Edge], ls: List[ListedParty], r: Rule, ents: Dict[str, Dict[str, Any]],
                             day: _dt.date, max_rounds: int) -> Evaluation:
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
            if t in seeds:
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
            res.proof, res.seeds = _multiplicative_proof(t, incoming, seeds, lo, hi, rev,
                                                         lambda n: not r.in_scope(ents.get(n, {})))
        results[t] = res
    return Evaluation(r, results, day, rnd)


def _multiplicative_proof(t: str, incoming: Mapping[str, Sequence[Edge]], seeds: Mapping[str, ListedParty],
                          lo: Mapping[str, float], hi: Mapping[str, float], rev: Mapping[str, float],
                          out_of_scope: Callable[[str], bool] = lambda n: False
                          ) -> Tuple[List[Dict[str, Any]], Tuple[str, ...]]:
    """The ancestor subgraph that carries covered ownership into ``t``, each node with its effective share."""
    steps: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    stack = [t]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in seeds:
            steps.append({"entity": n, "verdict": "listed", "round": 0, "lists": list(seeds[n].lists)})
            continue
        via: List[Dict[str, Any]] = []
        for e in incoming.get(n, ()):
            if rev[e.owner] > 0:
                d = _edge_ref(e, LISTED if e.owner in seeds else NOT)
                d.pop("owner_verdict")
                d["owner_effective"] = [_r(lo[e.owner]), _r(hi[e.owner])]
                via.append(d)
                stack.append(e.owner)
        step: Dict[str, Any] = {"entity": n, "verdict": "effective", "share": [_r(lo[n]), _r(hi[n])], "via": via}
        if n != t and out_of_scope(n):
            step["out_of_scope"] = True
        steps.append(step)
    steps.reverse()
    return steps, tuple(sorted(s["entity"] for s in steps if s["verdict"] == "listed"))

