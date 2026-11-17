import random

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ownership_rules import evaluate, load_rule
from ownership_rules.engine import VERDICTS

from reference import effective_dag, naive_cascade, ofac_blocked_dag

RANK = {v: i for i, v in enumerate(VERDICTS)}
NODES = [f"N{i}" for i in range(8)]


def _edge_strategy():
    share = st.one_of(
        st.none(),
        st.integers(0, 100),
        st.tuples(st.integers(0, 100), st.integers(0, 100)).map(lambda t: [min(t), max(t)]),
    )
    return st.tuples(st.sampled_from(NODES), st.sampled_from(NODES), share, st.booleans()).filter(
        lambda e: e[0] != e[1])


graphs = st.tuples(
    st.lists(_edge_strategy(), max_size=20),
    st.sets(st.sampled_from(NODES), max_size=3),
)


def _to_dicts(edges):
    out = []
    for o, t, pct, ctl in edges:
        out.append({"owner": o, "owned": t, "pct": pct, "control": ctl})
    return out


def _to_ref(edges):
    out = []
    for o, t, pct, ctl in edges:
        if pct is None:
            out.append((o, t, None, None, ctl))
        elif isinstance(pct, list):
            out.append((o, t, float(pct[0]), float(pct[1]), ctl))
        else:
            out.append((o, t, float(pct), float(pct), ctl))
    return out


def _listed(seeds):
    return [{"id": s, "lists": ["SDN"]} for s in sorted(seeds)]


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(graphs)
def test_matches_naive_reference(g):
    edges, seeds = g
    ev = evaluate(_to_dicts(edges), _listed(seeds), "ofac-50")
    ref = naive_cascade(_to_ref(edges), seeds)
    for n, v in ref.items():
        assert ev.verdict(n) == v, (n, v, ev[n].to_json())


@settings(max_examples=300, deadline=None)
@given(graphs, _edge_strategy(), st.sampled_from(NODES))
def test_monotone_in_edges_and_seeds(g, extra_edge, extra_seed):
    edges, seeds = g
    # A second report of a pair already present is not a new holding: merge_edges lets a known percentage refine
    # an earlier "unknown size" report, which can lower a verdict by design. New pairs must never lower one.
    assume(all((o, t) != extra_edge[:2] for o, t, *_ in edges))
    before = evaluate(_to_dicts(edges), _listed(seeds), "ofac-50")
    after = evaluate(_to_dicts(edges + [extra_edge]), _listed(seeds | {extra_seed}), "ofac-50")
    for n in NODES:
        assert RANK[after.verdict(n)] >= RANK[before.verdict(n)], n


@settings(max_examples=200, deadline=None)
@given(graphs, st.randoms(use_true_random=False))
def test_edge_order_does_not_matter(g, rnd):
    edges, seeds = g
    a = evaluate(_to_dicts(edges), _listed(seeds), "ofac-50").to_json()
    shuffled = list(edges)
    rnd.shuffle(shuffled)
    b = evaluate(_to_dicts(shuffled), _listed(seeds), "ofac-50").to_json()
    assert a == b


@settings(max_examples=300, deadline=None)
@given(graphs)
def test_proofs_are_valid_and_acyclic(g):
    edges, seeds = g
    ev = evaluate(_to_dicts(edges), _listed(seeds), "ofac-50")
    edge_set = {(o, t) for o, t, *_ in edges}
    rule = load_rule("ofac-50")
    for res in ev.flagged():
        steps = {(s["entity"], s["verdict"]): s for s in res.proof}
        assert (res.entity, res.verdict) in steps
        for s in res.proof:
            if s["verdict"] == "listed":
                assert s["entity"] in seeds
                continue
            lo = sum((v["pct"] if not isinstance(v["pct"], list) else v["pct"][0])
                     for v in s["via"] if v["pct"] is not None and RANK[v["owner_verdict"]] >= RANK["definitely"])
            if s["verdict"] == "definitely":
                assert rule.meets(min(lo, 100.0))
            for v in s["via"]:
                assert (v["owner"], s["entity"]) in edge_set
                cited = steps[(v["owner"], v["owner_verdict"])]
                assert cited["round"] < s["round"]


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 7), st.integers(0, 7), st.integers(0, 100)), max_size=20),
       st.sets(st.integers(0, 7), max_size=3))
def test_exact_dag_matches_faq401_definition(raw, seed_idx):
    # Build a DAG: edges only from a lower index to a higher index.
    edges = [(NODES[min(a, b)], NODES[max(a, b)], p) for a, b, p in raw if a != b]
    seeds = {NODES[i] for i in seed_idx}
    blocked = ofac_blocked_dag(edges, seeds, NODES)
    ev = evaluate([{"owner": o, "owned": t, "pct": p} for o, t, p in edges], _listed(seeds), "ofac-50")
    for n in NODES:
        assert ev[n].covered == (n in blocked), n
        if len({(o, t) for o, t, _ in edges}) == len(edges):  # no conflicting reports: no intervals either
            assert ev.verdict(n) in ("listed", "definitely", "not")


MULT = {"id": "mult-50", "threshold": 50, "attribution": "multiplicative", "seeds": [{"lists": ["SDN"]}]}


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 7), st.integers(0, 7), st.integers(0, 100)), max_size=20),
       st.sets(st.integers(0, 7), max_size=3))
def test_multiplicative_matches_path_products_on_dags(raw, seed_idx):
    edges = [(NODES[min(a, b)], NODES[max(a, b)], p) for a, b, p in raw if a != b]
    seeds = {NODES[i] for i in seed_idx}
    eff = effective_dag(edges, seeds, NODES)
    ev = evaluate([{"owner": o, "owned": t, "pct": p} for o, t, p in edges], _listed(seeds), MULT)
    for n in NODES:
        if n in seeds or n not in ev:
            continue
        assert ev[n].share[0] == pytest.approx(eff[n], abs=1e-6)


def test_multiplicative_differs_from_cascade_on_faq401_example1():
    edges = [{"owner": "X", "owned": "A", "pct": 50}, {"owner": "A", "owned": "B", "pct": 50}]
    listed = [{"id": "X", "lists": ["SDN"]}]
    assert evaluate(edges, listed, "ofac-50").verdict("B") == "definitely"
    mult = evaluate(edges, listed, MULT)
    assert mult["B"].share == (25.0, 25.0)
    assert mult.verdict("B") == "not"


def test_multiplicative_cycle_terminates():
    edges = [{"owner": "X", "owned": "A", "pct": 50}, {"owner": "A", "owned": "B", "pct": 100},
             {"owner": "B", "owned": "A", "pct": 100}]
    ev = evaluate(edges, [{"id": "X", "lists": ["SDN"]}], MULT)
    assert ev.verdict("A") == "definitely"
    assert ev["A"].share[0] == pytest.approx(100.0)


def test_long_chain_and_large_graph():
    n = 3000
    edges = [{"owner": f"E{i}", "owned": f"E{i + 1}", "pct": 50} for i in range(n)]
    ev = evaluate(edges, [{"id": "E0", "lists": ["SDN"]}], "ofac-50")
    assert ev.verdict(f"E{n}") == "definitely"
    assert len(ev[f"E{n}"].proof) == n + 1
    rnd = random.Random(7)
    big = [{"owner": f"F{rnd.randrange(5000)}", "owned": f"F{rnd.randrange(5000)}", "pct": rnd.choice([10, 30, 50, 60])}
           for _ in range(20000)]
    big = [e for e in big if e["owner"] != e["owned"]]
    ev = evaluate(big, [{"id": f"F{i}", "lists": ["SDN"]} for i in range(50)], "ofac-50")
    assert ev.rounds < 5000


def test_seed_selection_and_non_seed_listed_party():
    # A CUBA-program SDN is not a seed under the BIS rule but is an ordinary entity that can itself be covered.
    listed = [{"id": "A", "lists": ["EL"]}, {"id": "S", "lists": ["SDN"], "programs": ["CUBA"]}]
    ev = evaluate([{"owner": "A", "owned": "S", "pct": 60}], listed, "bis-affiliates")
    assert ev.verdict("S") == "definitely"
    assert ev.verdict("A") == "listed"


def test_same_party_on_two_lists_is_merged():
    listed = [{"id": "A", "lists": ["EL"], "restrictiveness": 1}, {"id": "A", "lists": ["MEU"], "restrictiveness": 3}]
    ev = evaluate([{"owner": "A", "owned": "C", "pct": 50}], listed, "bis-affiliates")
    assert ev["C"].most_restrictive == ("A",)
    step = ev["C"].proof[0]
    assert step["lists"] == ["EL", "MEU"]


def test_as_of_without_date_drops_edges_ended_before_today():
    edges = [{"owner": "X", "owned": "A", "pct": 60, "valid_to": "2020-01-01"}]
    listed = [{"id": "X", "lists": ["SDN"]}]
    assert evaluate(edges, listed).verdict("A") == "not"
    assert evaluate(edges, listed, as_of="2019-06-30").verdict("A") == "definitely"


def test_shares_over_100_are_capped():
    listed = [{"id": "X", "lists": ["SDN"]}, {"id": "Y", "lists": ["SDN"]}]
    ev = evaluate([{"owner": "X", "owned": "A", "pct": 80}, {"owner": "Y", "owned": "A", "pct": 80}], listed)
    assert ev["A"].share == (100.0, 100.0)


def test_decimal_shares_meet_threshold():
    listed = [{"id": "X", "lists": ["SDN"]}, {"id": "Y", "lists": ["SDN"]}, {"id": "Z", "lists": ["SDN"]}]
    edges = [{"owner": "X", "owned": "A", "pct": 16.7}, {"owner": "Y", "owned": "A", "pct": 16.6},
             {"owner": "Z", "owned": "A", "pct": 16.7}]
    assert evaluate(edges, listed).verdict("A") == "definitely"


def test_explain_text():
    edges = [{"owner": "X", "owned": "A", "pct": [40, 60]}, {"owner": "X", "owned": "B", "control": True}]
    ev = evaluate(edges, ["X"], "any-list-50")
    assert "owns 40-60% of A" in ev["A"].explain()
    assert "controls B" in ev["B"].explain()
    assert ev["Z"].explain().startswith("Z: not covered")


def test_duplicate_reports_are_one_holding():
    listed = [{"id": "X", "lists": ["SDN"]}]
    same = [{"owner": "X", "owned": "A", "pct": 30, "source": "s1"}, {"owner": "X", "owned": "A", "pct": 30, "source": "s2"}]
    ev = evaluate(same, listed)
    assert ev.verdict("A") == "not"
    assert ev["A"].share == (30.0, 30.0)
    conflicting = [{"owner": "X", "owned": "A", "pct": 40}, {"owner": "X", "owned": "A", "pct": 60},
                   {"owner": "X", "owned": "A", "pct": None, "control": True}]
    r = evaluate(conflicting, listed)["A"]
    assert r.verdict == "possibly"
    assert r.proof[-1]["via"][0]["pct"] == [40, 60] and r.proof[-1]["via"][0]["control"] is True
