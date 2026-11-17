"""Scope pass-through, input normalization, default dates, shared proofs, CLI object input and proof enforcement."""

import datetime as dt
import json
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ownership_rules import InputError, evaluate, load_rule, run_vectors
from ownership_rules.cli import main
from ownership_rules.vectors import check_case, summary

from reference import naive_cascade
from test_engine import NODES, _edge_strategy, _listed, _to_dicts, _to_ref


# ------------------------------------------------------------------ scope

def test_bis_ownership_passes_through_a_us_intermediary():
    ev = evaluate([{"owner": "A", "owned": "US1", "pct": 100}, {"owner": "US1", "owned": "CN1", "pct": 100}],
                  [{"id": "A", "lists": ["EL"]}], "bis-affiliates", entities={"US1": {"country": "US"},
                                                                              "CN1": {"country": "CN"}})
    assert ev.verdict("US1") == "not"
    assert ev["US1"].reasons == ("out_of_scope",)
    assert ev.verdict("CN1") == "definitely"
    assert ev["CN1"].proof_edges == [("A", "US1"), ("US1", "CN1")]
    us_step = [s for s in ev["CN1"].proof if s["entity"] == "US1"][0]
    assert us_step["out_of_scope"] is True and us_step["verdict"] == "definitely"
    assert ev["CN1"].proof[-1]["via"][0]["owner_out_of_scope"] is True
    assert "out of the rule's scope" in ev["CN1"].explain()
    assert "outside the rule's scope" in ev["US1"].explain()


def test_us_territories_are_not_foreign_under_bis():
    ents = {"PR1": {"country": "pr"}, "VI1": {"country": "VI"}, "SG1": {"country": "SG"}}
    edges = [{"owner": "A", "owned": e, "pct": 100} for e in ents]
    ev = evaluate(edges, [{"id": "A", "lists": ["EL"]}], "bis-affiliates", entities=ents)
    assert (ev.verdict("PR1"), ev.verdict("VI1"), ev.verdict("SG1")) == ("not", "not", "definitely")


def test_multiplicative_mode_also_passes_through_out_of_scope_entities():
    rule = {"id": "m", "threshold": 50, "attribution": "multiplicative",
            "subject": {"scope": "foreign", "home_country": "US"}}
    ev = evaluate([{"owner": "A", "owned": "US1", "pct": 80}, {"owner": "US1", "owned": "CN1", "pct": 80}],
                  ["A"], rule, entities={"US1": {"country": "US"}})
    assert ev.verdict("US1") == "not"
    assert ev.verdict("CN1") == "definitely"
    assert ev["CN1"].share == pytest.approx((64.0, 64.0))


COUNTRIES = st.dictionaries(st.sampled_from(NODES), st.sampled_from(["US", "CN", "PR"]), max_size=8)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.tuples(st.lists(_edge_strategy(), max_size=20), st.sets(st.sampled_from(NODES), max_size=3)), COUNTRIES)
def test_scope_only_masks_the_reported_verdict(g, countries):
    """With a foreign-only rule, every in-scope entity gets exactly the scope-free cascade verdict, and every
    home-country entity that is not a seed is reported as not."""
    edges, seeds = g
    rule = {"id": "f", "threshold": 50, "subject": {"scope": "foreign", "home_country": ["US", "PR"]}}
    ents = {k: {"country": v} for k, v in countries.items()}
    ev = evaluate(_to_dicts(edges), _listed(seeds), rule, entities=ents)
    ref = naive_cascade(_to_ref(edges), seeds)
    for n, v in ref.items():
        if n not in seeds and countries.get(n) in ("US", "PR"):
            assert ev.verdict(n) == "not", n
        else:
            assert ev.verdict(n) == v, (n, v)


# ------------------------------------------------------------------ input normalization and validation

def test_list_codes_and_programs_ignore_case():
    assert evaluate([{"owner": "X", "owned": "A", "pct": 100}], [{"id": "X", "lists": ["sdn"]}]).verdict("A") == \
        "definitely"
    ev = evaluate([{"owner": "X", "owned": "A", "pct": 100}],
                  [{"id": "X", "lists": ["sdn"], "programs": ["russia-eo14024"], "entry_type": "Entity"}],
                  "bis-affiliates")
    assert ev.verdict("A") == "definitely"
    rule = {"id": "r", "threshold": 50, "seeds": [{"lists": ["el"]}]}
    assert evaluate([{"owner": "X", "owned": "A", "pct": 50}], [{"id": "X", "lists": ["EL"]}], rule).verdict("A") == \
        "definitely"


@pytest.mark.parametrize("rule", [
    {"id": "r", "threshold": 50, "subject": "foreign"},
    {"id": "r", "threshold": 50, "subject": ["foreign"]},
    {"id": "r", "threshold": 50, "seeds": [{"lists": "SDN"}]},
    {"id": "r", "threshold": 50, "seeds": [{"lists": ["SDN"], "programs": "CUBA"}]},
    {"id": "r", "threshold": 50, "seeds": [{"lists": ["SDN"], "entry_types": "entity"}]},
    {"id": "r", "threshold": 50, "seeds": [{"lists": [1]}]},
    {"id": "r", "threshold": 50, "seeds": {"lists": ["SDN"]}},
    {"id": "r", "threshold": 50, "seeds": ["SDN"]},
    {"id": "r", "threshold": 50, "subject": {"scope": "foreign"}},
    {"id": "r", "threshold": 50, "subject": {"scope": "foreign", "home_country": 1}},
    {"id": "r", "threshold": 50, "subject": {"scope": "foreign", "home_country": ["US", 2]}},
    {"id": "r", "threshold": 50, "notes": "text"},
    {"id": "r", "threshold": 50, "version": 2},
])
def test_malformed_rules_raise_input_error(rule):
    with pytest.raises(InputError):
        load_rule(rule)


json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-5, 120) | st.text(max_size=6),
    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=6), c, max_size=3),
    max_leaves=10,
)
rule_like = st.fixed_dictionaries({"id": st.just("r"), "threshold": st.just(50)}, optional={
    "comparator": json_values | st.sampled_from([">=", ">"]),
    "attribution": json_values | st.sampled_from(["cascade", "multiplicative"]),
    "seeds": json_values | st.lists(st.dictionaries(st.sampled_from(["lists", "programs", "entry_types"]),
                                                    json_values | st.just(["SDN"]), max_size=3), max_size=2),
    "subject": json_values | st.fixed_dictionaries({}, optional={"scope": json_values | st.just("foreign"),
                                                                 "home_country": json_values | st.just("US")}),
    "unknown_share": json_values,
    "most_restrictive": json_values,
    "version": json_values,
    "notes": json_values,
    "authority": json_values,
})


@settings(max_examples=1500, deadline=None)
@given(rule_like)
def test_untrusted_rules_never_crash_with_anything_but_input_error(rule):
    try:
        ev = evaluate([{"owner": "X", "owned": "A", "pct": 60}], [{"id": "X", "lists": ["SDN"], "country": "CN"}],
                      rule, entities={"A": {"country": "CN"}})
    except InputError:
        return
    json.dumps(ev.to_json(include_not=True))


def test_cli_reports_a_bad_rule_without_a_traceback(tmp_path, capsys):
    rule = tmp_path / "rule.json"
    rule.write_text(json.dumps({"id": "r", "threshold": 50, "subject": "foreign"}), encoding="utf-8")
    edges = tmp_path / "e.json"
    edges.write_text("[]", encoding="utf-8")
    assert main(["evaluate", "--rule", str(rule), "--edges", str(edges), "--listed", str(edges)]) == 2
    assert "subject must be an object" in capsys.readouterr().err


# ------------------------------------------------------------------ dates

def test_without_as_of_today_is_used():
    listed = [{"id": "X", "lists": ["SDN"]}]
    future = (dt.date.today() + dt.timedelta(days=3650)).isoformat()
    assert evaluate([{"owner": "X", "owned": "A", "pct": 60, "valid_to": future}], listed).verdict("A") == "definitely"
    assert evaluate([{"owner": "X", "owned": "A", "pct": 60}],
                    [{"id": "X", "lists": ["SDN"], "listed_to": future}]).verdict("A") == "definitely"
    assert evaluate([{"owner": "X", "owned": "A", "pct": 60, "valid_from": future}], listed).verdict("A") == "not"
    assert evaluate([{"owner": "X", "owned": "A", "pct": 60, "valid_to": "2020-01-01"}], listed).verdict("A") == "not"
    ev = evaluate([], listed)
    assert ev.as_of is not None and ev.to_json()["as_of"] == ev.as_of.isoformat()


# ------------------------------------------------------------------ proofs

def test_basis_is_copied_into_proofs():
    ev = evaluate([{"owner": "X", "owned": "A", "pct": 100, "control": True, "basis": "accounting_consolidation"}],
                  [{"id": "X", "lists": ["SDN"]}])
    assert ev["A"].proof[-1]["via"][0]["basis"] == "accounting_consolidation"


def _rebuild(shared, result):
    """Every step reachable from a result's proof_step, with the added step links removed."""
    seen, stack, out = set(), [result["proof_step"]], []
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        st = dict(shared["steps"][sid])
        if "via" in st:
            stack += [v["step"] for v in st["via"]]
            st["via"] = [{k: x for k, x in v.items() if k != "step"} for v in st["via"]]
        out.append(json.dumps(st, sort_keys=True))
    return sorted(out)


def test_shared_proofs_in_multiplicative_mode():
    rule = {"id": "m", "threshold": 50, "attribution": "multiplicative"}
    ev = evaluate([{"owner": "X", "owned": "A", "pct": 80}, {"owner": "A", "owned": "B", "pct": 80}], ["X"], rule)
    shared = ev.to_json(proofs="shared")
    inline = ev.to_json()
    assert _rebuild(shared, shared["results"]["B"]) == sorted(json.dumps(s, sort_keys=True)
                                                              for s in inline["results"]["B"]["proof"])


def _chain(n):
    edges = [{"owner": f"E{i}", "owned": f"E{i + 1}", "pct": 100} for i in range(n)]
    return edges, [{"id": "E0", "lists": ["SDN"]}]


def test_shared_proofs_match_inline_proofs():
    edges = [{"owner": "X", "owned": "A", "pct": 50}, {"owner": "X", "owned": "B", "pct": 10},
             {"owner": "A", "owned": "B", "pct": 40}, {"owner": "B", "owned": "C", "control": True}]
    ev = evaluate(edges, [{"id": "X", "lists": ["SDN"]}])
    inline = ev.to_json(proofs="inline")
    shared = ev.to_json(proofs="shared")
    for k, r in inline["results"].items():
        assert _rebuild(shared, shared["results"][k]) == sorted(json.dumps(s, sort_keys=True) for s in r["proof"])
    assert "proof" not in json.dumps(ev.to_json(proofs="none")["results"])
    with pytest.raises(ValueError):
        ev.to_json(proofs="all")


def test_deep_chain_shared_output_is_linear():
    edges, listed = _chain(3000)
    t = time.perf_counter()
    ev = evaluate(edges, listed)
    elapsed = time.perf_counter() - t
    assert ev.verdict("E3000") == "definitely"
    shared = json.dumps(ev.to_json(proofs="shared"))
    # One step per entity: linear in the chain length (the inline form is about 770 MB here).
    assert len(shared) < 5 * 1024 * 1024
    assert elapsed < 60


def test_cli_proofs_option_and_object_input(tmp_path, capsys):
    doc = tmp_path / "graph.json"
    doc.write_text(json.dumps({"edges": [{"owner": "X", "owned": "A", "pct": 50}, {"owner": "A", "owned": "B",
                                                                                  "pct": 50}],
                               "listed": [{"id": "X", "lists": ["SDN"]}]}, indent=2), encoding="utf-8")
    assert main(["evaluate", "--edges", str(doc), "--listed", str(doc), "--proofs", "shared"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["results"]["B"]["proof_step"] == "B|definitely"
    assert out["steps"]["B|definitely"]["via"][0]["step"] == "A|definitely"
    assert "X|listed" in out["steps"]
    single = tmp_path / "one.json"
    single.write_text(json.dumps({"id": "X", "lists": ["SDN"]}, indent=2), encoding="utf-8")
    assert main(["explain", "--edges", str(doc), "--listed", str(single), "B"]) == 0
    assert "B: definitely" in capsys.readouterr().out
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"listed": []}), encoding="utf-8")
    assert main(["evaluate", "--edges", str(wrong), "--listed", str(doc)]) == 2


# ------------------------------------------------------------------ vector runner

def test_missing_proof_fails_when_the_case_expects_one():
    case = {"expect": {"A": {"verdict": "definitely", "proof_edges": [["X", "A"]], "seeds": ["X"]}}}
    fails = check_case(case, {"results": {"A": {"verdict": "definitely"}}})
    assert fails == ["A: proof_edges expected, missing from the answer", "A: seeds expected, missing from the answer"]
    assert check_case(case, {"results": {"A": {"verdict": "definitely"}}}, check_proofs=False) == []


def test_verdict_only_engine_gets_separate_verdict_and_proof_counts():
    from ownership_rules.vectors import builtin_engine

    def verdicts_only(case):
        return {"results": {e: {"verdict": r["verdict"]} for e, r in builtin_engine(case)["results"].items()}}

    reports = run_vectors(verdicts_only)
    s = summary(reports)
    assert s["verdicts_passed"] == s["cases"]
    assert s["proofs_checked"] > 0 and s["proofs_passed"] == 0
    assert s["passed"] == s["cases"] - s["proofs_checked"]
    assert summary(run_vectors(verdicts_only, check_proofs=False))["passed"] == s["cases"]


def test_cli_vectors_prints_both_counts(capsys):
    assert main(["vectors"]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "verdicts 31/31" in last and "proofs" in last
