"""Independent reference implementations used by the property tests.

They are deliberately naive: full recomputation every round, no worklist, no proofs. If the engine and these
disagree, one of them is wrong.
"""

from collections import defaultdict

NOT, CONTROL_ONLY, POSSIBLY, DEFINITELY, LISTED = range(5)
NAMES = ("not", "control_only", "possibly", "definitely", "listed")


def naive_cascade(edges, seeds, threshold=50.0):
    """Kleene iteration of the tri-state cascade over the whole graph.

    ``edges``: list of (owner, owned, lo, hi, control) where lo/hi are None for an unknown share.
    """
    merged = {}
    for o, t, lo, hi, ctl in edges:  # several reports of one pair: hull of known shares, control if any
        if (o, t) not in merged:
            merged[(o, t)] = [lo, hi, ctl]
            continue
        m = merged[(o, t)]
        if lo is not None:
            m[0] = lo if m[0] is None else min(m[0], lo)
            m[1] = hi if m[1] is None else max(m[1], hi)
        m[2] = m[2] or ctl
    edges = [(o, t, lo, hi, ctl) for (o, t), (lo, hi, ctl) in merged.items()]
    nodes = {o for o, *_ in edges} | {t for _, t, *_ in edges} | set(seeds)
    level = {n: (LISTED if n in seeds else NOT) for n in nodes}
    inc = defaultdict(list)
    for e in edges:
        inc[e[1]].append(e)
    eps = 1e-9
    while True:
        new = dict(level)
        for t in nodes:
            if t in seeds:
                continue
            lo = hi = review = 0.0
            unknown = control = False
            for o, _, elo, ehi, ctl in inc[t]:
                lv = level[o]
                known = elo is not None
                if lv >= DEFINITELY and known:
                    lo += elo
                if lv >= POSSIBLY:
                    if known:
                        hi += ehi
                    elif not ctl:
                        unknown = True
                if lv >= CONTROL_ONLY:
                    review += ehi if known else 100.0
                    control = control or ctl
            if lo >= threshold - eps:
                v = DEFINITELY
            elif hi >= threshold - eps or unknown:
                v = POSSIBLY
            elif control or review >= threshold - eps:
                v = CONTROL_ONLY
            else:
                v = NOT
            new[t] = max(v, level[t])
        if new == level:
            return {n: NAMES[v] for n, v in level.items()}
        level = new


def _dedupe(edges):
    out = {}
    for o, t, p in edges:
        out.setdefault((o, t), []).append(p)
    return out


def ofac_blocked_dag(edges, seeds, order, threshold=50.0):
    """Binary OFAC test on a DAG with exact shares, straight from FAQ 401: process entities in topological order;
    an entity is blocked when the stakes held by blocked owners add up to the threshold. Several reports of one
    pair count once, at the lowest reported share."""
    blocked = set(seeds)
    inc = defaultdict(list)
    for (o, t), ps in _dedupe(edges).items():
        inc[t].append((o, min(ps)))
    for t in order:
        if t in blocked:
            continue
        if sum(p for o, p in inc[t] if o in blocked) >= threshold - 1e-9:
            blocked.add(t)
    return blocked


def effective_dag(edges, seeds, order):
    """Look-through ownership on a DAG: sum over paths from any seed of the product of the stakes."""
    eff = {n: (100.0 if n in seeds else 0.0) for n in order}
    inc = defaultdict(list)
    for (o, t), ps in _dedupe(edges).items():
        inc[t].append((o, min(ps)))
    for t in order:
        if t in seeds:
            continue
        eff[t] = min(100.0, sum(pct * eff[o] / 100.0 for o, pct in inc[t]))
    return eff
