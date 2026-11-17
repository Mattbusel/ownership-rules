import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ownership_rules import Edge, InputError, ListedParty, Share, evaluate, load_rule


@pytest.mark.parametrize("pct", [-1, 101, float("nan"), "50", True, [60, 40], [1, 2, 3], {"lo": 1}])
def test_bad_shares_raise_input_error(pct):
    with pytest.raises(InputError):
        Edge.from_dict({"owner": "A", "owned": "B", "pct": pct})


@pytest.mark.parametrize("d", [
    {"owner": "A", "owned": "A", "pct": 10},
    {"owner": "", "owned": "B"},
    {"owner": 3, "owned": "B"},
    {"owned": "B"},
    {"owner": "A", "owned": "B", "control": "yes"},
    {"owner": "A", "owned": "B", "as_of": "17/11/2026"},
    {"owner": "A", "owned": "B", "valid_from": "2026-01-01", "valid_to": "2025-01-01"},
    {"owner": "A", "owned": "B", "source": 5},
    "not an object",
])
def test_bad_edges_raise_input_error(d):
    with pytest.raises(InputError):
        Edge.from_dict(d)


def test_share_forms():
    assert Share.parse(50) == Share(50, 50)
    assert Share.parse([10, 20]) == Share(10, 20)
    assert Share.parse({"min": 10}) == Share(10, 100)
    assert Share.parse(None) is None
    assert Share.parse([10, 20]).to_json() == [10, 20]
    assert Share.parse(33.3333333).to_json() == 33.333333


def test_extra_edge_fields_round_trip():
    e = Edge.from_dict({"owner": "A", "owned": "B", "pct": 50, "source": "gleif", "lei": "X"})
    assert e.to_json() == {"owner": "A", "owned": "B", "pct": 50, "source": "gleif", "lei": "X"}


@pytest.mark.parametrize("d", [
    {"id": "A", "entry_type": "branch"},
    {"id": "A", "lists": "SDN", "restrictiveness": "high"},
    {"id": "A", "lists": [1]},
    {"lists": ["SDN"]},
])
def test_bad_listed_parties(d):
    with pytest.raises(InputError):
        ListedParty.from_dict(d)


@pytest.mark.parametrize("rule", [
    {"id": "r"},
    {"id": "r", "threshold": 0},
    {"id": "r", "threshold": 50, "comparator": "=="},
    {"id": "r", "threshold": 50, "attribution": "max"},
    {"id": "r", "threshold": 50, "seeds": [{}]},
    {"id": "r", "threshold": 50, "subject": {"scope": "domestic"}},
    {"id": "", "threshold": 50},
    "no-such-rule",
    42,
])
def test_bad_rules(rule):
    with pytest.raises(InputError):
        load_rule(rule)


def test_strict_comparator():
    rule = {"id": "over-50", "threshold": 50, "comparator": ">"}
    edges = [{"owner": "X", "owned": "A", "pct": 50}, {"owner": "X", "owned": "B", "pct": 50.01}]
    ev = evaluate(edges, ["X"], rule)
    assert ev.verdict("A") == "not"
    assert ev.verdict("B") == "definitely"


json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-10**6, 10**6) | st.floats(allow_nan=True) | st.text(max_size=8),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
    max_leaves=12,
)
edge_like = st.fixed_dictionaries({}, optional={
    "owner": json_values | st.sampled_from(["A", "B"]),
    "owned": json_values | st.sampled_from(["B", "C"]),
    "pct": json_values | st.integers(0, 100),
    "control": json_values,
    "source": json_values,
    "as_of": json_values | st.just("2026-01-01"),
    "valid_from": json_values,
    "valid_to": json_values,
})


@settings(max_examples=1500, deadline=None)
@given(st.lists(edge_like, max_size=5), st.lists(json_values | st.just("A"), max_size=3))
def test_untrusted_input_never_crashes_with_anything_but_input_error(edges, listed):
    try:
        ev = evaluate(edges, listed, "ofac-50")
    except InputError:
        return
    json.dumps(ev.to_json(include_not=True))
