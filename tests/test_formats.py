import json

import pytest

from ownership_rules import InputError, ListedParty, evaluate, load_rule
from ownership_rules.formats import from_bods, from_ftm, parse_percentage
from ownership_rules.model import program_tag


@pytest.mark.parametrize("value, pct", [
    ("50", 50.0), ("50%", 50.0), ("50,5 %", 50.5), (" 100 ", 100.0), (40, 40.0), (None, None), ("", None),
    ("25-50%", [25.0, 50.0]), ("25 to 50", [25.0, 50.0]), ("25 – 50 %", [25.0, 50.0]),
    ("ownership-of-shares-25-to-50-percent", [25.0, 50.0]), ("voting-rights-75-to-100-percent-as-trust", [75.0, 100.0]),
    ("more than 25%", [25.0, 100.0]), ("over 75%", [75.0, 100.0]), (">50", [50.0, 100.0]),
    ("at least 50 percent", [50.0, 100.0]), ("less than 10%", [0.0, 10.0 - 1e-6]), ("up to 25", [0.0, 25.0]), ("<= 50", [0.0, 50.0]),
])
def test_parse_percentage(value, pct):
    assert parse_percentage(value) == (pct, True)


@pytest.mark.parametrize("value", ["majority", "120", "60-40", True, {"x": 1}, -5])
def test_parse_percentage_rejects(value):
    assert parse_percentage(value) == (None, False)


def test_program_tags_compare_without_brackets_spaces_or_underscores():
    assert program_tag("[ILLICIT-DRUGS-EO14059]") == "ILLICIT-DRUGS-EO14059"
    assert program_tag("illicit drugs-eo14059") == "ILLICIT-DRUGS-EO14059"
    assert program_tag(" russia_eo14024 ") == "RUSSIA-EO14024"


def test_bis_reaches_sdns_under_the_drug_program_as_the_sdn_list_spells_it():
    # The SDN List and the CSL write ILLICIT-DRUGS-EO14059. 0.1.1 named "ILLICIT DRUGS-EO14059" in the rule and
    # missed every SDN under that program (788 entries on 2026-11-17).
    rule = load_rule("bis-affiliates")
    for tag in ("ILLICIT-DRUGS-EO14059", "[ILLICIT-DRUGS-EO14059]", "ILLICIT DRUGS-EO14059"):
        assert rule.is_seed(ListedParty.from_dict({"id": "X", "lists": ["SDN"], "programs": [tag]})), tag
    ev = evaluate([{"owner": "X", "owned": "A", "pct": 50}],
                  [{"id": "X", "lists": ["SDN"], "programs": ["ILLICIT-DRUGS-EO14059"]}],
                  "bis-affiliates", entities={"A": {"country": "MX"}})
    assert ev.verdict("A") == "definitely"


def _sdn(eid, program="RUSSIA-EO14024", schema="Company", country="ru"):
    return [
        {"id": eid, "schema": schema, "datasets": ["us_ofac_sdn"],
         "properties": {"name": [eid.upper()], "country": [country]}},
        {"id": f"{eid}-s", "schema": "Sanction", "datasets": ["us_ofac_sdn"],
         "properties": {"entity": [eid], "program": [program], "programId": ["US-RUSHAR"]}},
    ]


def test_ftm_ownership_sanctions_and_countries():
    ents = _sdn("x") + [
        {"id": "a", "schema": "Company", "properties": {"name": ["A"], "country": ["cy"]}},
        {"id": "b", "schema": "Company", "properties": {"name": ["B"], "jurisdiction": ["de"]}},
        {"id": "o1", "schema": "Ownership", "properties": {"owner": ["x"], "asset": ["a"], "percentage": ["50%"]}},
        {"id": "o2", "schema": "Ownership",
         "properties": {"owner": ["x"], "asset": ["b"], "percentage": ["25-50%"], "startDate": ["2020-01-01"]}},
    ]
    g = from_ftm(ents)
    assert g.listed == [{"id": "x", "lists": ["SDN"], "programs": ["RUSSIA-EO14024"], "name": "X"}]
    assert g.entities == {"x": {"country": "RU"}, "a": {"country": "CY"}, "b": {"country": "DE"}}
    assert {(e["owner"], e["owned"]): e["pct"] for e in g.edges} == {("x", "a"): 50.0, ("x", "b"): [25.0, 50.0]}
    ev = evaluate(g.edges, g.listed, "ofac-50", entities=g.entities, as_of="2026-11-17")
    assert (ev.verdict("a"), ev.verdict("b")) == ("definitely", "possibly")
    assert "ftm:o1" in json.dumps(ev["a"].to_json())


def test_ftm_ofac_owned_or_controlled_by_is_an_unknown_share():
    ents = _sdn("x") + [
        {"id": "a", "schema": "Company", "properties": {"name": ["A"]}},
        {"id": "o", "schema": "Ownership", "properties": {"owner": ["x"], "asset": ["a"], "role": ["Owned or Controlled By"]}},
    ]
    g = from_ftm(ents)
    assert g.edges[0]["pct"] is None and "control" not in g.edges[0]
    assert evaluate(g.edges, g.listed, "ofac-50").verdict("a") == "possibly"


def test_ftm_skips_indirect_restatements_unless_asked():
    ents = _sdn("x") + [
        {"id": "o1", "schema": "Ownership", "properties": {"owner": ["x"], "asset": ["a"], "percentage": ["30"]}},
        {"id": "o2", "schema": "Ownership", "properties": {"owner": ["a"], "asset": ["b"], "percentage": ["100"]}},
        {"id": "o3", "schema": "Ownership",
         "properties": {"owner": ["x"], "asset": ["b"], "percentage": ["30"], "ownershipType": ["Indirect"]}},
    ]
    g = from_ftm(ents)
    assert len(g.edges) == 2 and any("o3" in n for n in g.notes)
    assert evaluate(g.edges, g.listed, "ofac-50").verdict("b") == "not"
    assert len(from_ftm(ents, indirect=True).edges) == 3


def test_ftm_directorships_are_opt_in_control():
    ents = _sdn("p", schema="Person") + [
        {"id": "d", "schema": "Directorship", "properties": {"director": ["p"], "organization": ["a"]}},
    ]
    assert from_ftm(ents).edges == []
    g = from_ftm(ents, directorships=True)
    assert g.listed[0]["entry_type"] == "individual"
    assert evaluate(g.edges, g.listed, "ofac-50").verdict("a") == "control_only"


def test_ftm_nested_export_fills_the_missing_end():
    nested = {
        "id": "a", "schema": "Company", "properties": {
            "name": ["A"], "country": ["kz"],
            "ownershipAsset": [{"id": "o", "schema": "Ownership", "properties": {
                "percentage": ["51"],
                "owner": [{"id": "x", "schema": "Company", "datasets": ["us_ofac_sdn"], "properties": {
                    "name": ["X"],
                    "sanctions": [{"id": "x-s", "schema": "Sanction", "properties": {"program": ["SDGT"]}}],
                }}],
            }}],
        },
    }
    g = from_ftm([nested])
    assert g.edges == [{"owner": "x", "owned": "a", "source": "ftm:o", "pct": 51.0}]
    assert g.listed[0]["lists"] == ["SDN"] and g.listed[0]["programs"] == ["SDGT"]
    assert evaluate(g.edges, g.listed, "ofac-50").verdict("a") == "definitely"


def test_ftm_entity_list_from_the_csl_program_id_feeds_the_bis_rule():
    ents = [
        {"id": "e", "schema": "Company", "datasets": ["us_trade_csl"], "properties": {"name": ["E"], "country": ["cn"]}},
        {"id": "e-s", "schema": "Sanction", "properties": {"entity": ["e"], "programId": ["US-BIS-EL"]}},
        {"id": "f", "schema": "Company", "properties": {"name": ["F"], "country": ["hk"]}},
        {"id": "u", "schema": "Company", "properties": {"name": ["U"], "country": ["us"]}},
        {"id": "o1", "schema": "Ownership", "properties": {"owner": ["e"], "asset": ["f"], "percentage": ["60"]}},
        {"id": "o2", "schema": "Ownership", "properties": {"owner": ["e"], "asset": ["u"], "percentage": ["60"]}},
    ]
    g = from_ftm(ents)
    assert g.listed[0]["lists"] == ["EL"]
    ev = evaluate(g.edges, g.listed, "bis-affiliates", entities=g.entities)
    assert (ev.verdict("f"), ev.verdict("u")) == ("definitely", "not")


def test_ftm_custom_list_mapping_and_bad_values():
    ents = [
        {"id": "x", "schema": "Company", "properties": {"name": ["X"]}},
        {"id": "x-s", "schema": "Sanction", "properties": {"entity": ["x"], "authority": ["HM Treasury"]}},
        {"id": "o", "schema": "Ownership",
         "properties": {"owner": ["x"], "asset": ["a"], "percentage": ["majority"], "endDate": ["2020"]}},
        {"id": "self", "schema": "Ownership", "properties": {"owner": ["a"], "asset": ["a"], "percentage": ["5"]}},
        "junk", {"schema": 3},
    ]
    g = from_ftm(ents, lists=lambda ent, sans: ["UK" for s in sans if "HM Treasury" in s["properties"]["authority"]])
    assert g.listed[0]["lists"] == ["UK"]
    assert g.edges == [{"owner": "x", "owned": "a", "source": "ftm:o", "pct": None}]
    assert any("majority" in n for n in g.notes) and any("self" in n for n in g.notes)


BODS_02 = [
    {"statementID": "ignored", "statementId": "es-x", "statementType": "entityStatement", "name": "X",
     "incorporatedInJurisdiction": {"name": "Russia", "code": "RU"}},
    {"statementId": "es-a", "statementType": "entityStatement", "name": "A",
     "incorporatedInJurisdiction": {"name": "Cyprus", "code": "CY"}},
    {"statementId": "es-b", "statementType": "entityStatement", "name": "B",
     "incorporatedInJurisdiction": {"name": "United Kingdom", "code": "GB-ENG"}},
    {"statementId": "ooc-1", "statementType": "ownershipOrControlStatement",
     "subject": {"describedByEntityStatement": "es-a"},
     "interestedParty": {"describedByEntityStatement": "es-x"},
     "interests": [{"type": "shareholding", "share": {"exact": 60}},
                   {"type": "voting-rights", "share": {"exact": 60}}]},
    {"statementId": "ooc-2", "statementType": "ownershipOrControlStatement",
     "subject": {"describedByEntityStatement": "es-b"},
     "interestedParty": {"describedByEntityStatement": "es-a"},
     "interests": [{"type": "shareholding", "share": {"exclusiveMinimum": 25, "maximum": 50}},
                   {"type": "appointment-of-board"}]},
]


def test_bods_02():
    g = from_bods(BODS_02, listed=[{"id": "es-x", "lists": ["SDN"]}])
    assert g.entities == {"es-x": {"country": "RU"}, "es-a": {"country": "CY"}, "es-b": {"country": "GB"}}
    assert [(e["owner"], e["owned"], e["pct"], e.get("control", False)) for e in g.edges] == [
        ("es-x", "es-a", 60.0, False), ("es-a", "es-b", [25.0, 50.0], False), ("es-a", "es-b", None, True)]
    ev = evaluate(g.edges, g.listed, "ofac-50", entities=g.entities)
    assert (ev.verdict("es-a"), ev.verdict("es-b")) == ("definitely", "possibly")


def test_bods_04_records_and_exclusive_maximum():
    stmts = [
        {"statementId": "s1", "recordId": "x", "recordType": "entity",
         "recordDetails": {"name": "X", "jurisdiction": {"code": "IR"}}},
        {"statementId": "s2", "recordId": "a", "recordType": "entity",
         "recordDetails": {"name": "A", "jurisdiction": {"code": "AE"}}},
        {"statementId": "s3", "recordId": "x-a", "recordType": "relationship",
         "recordDetails": {"subject": "a", "interestedParty": "x",
                           "interests": [{"type": "shareholding", "share": {"minimum": 25, "exclusiveMaximum": 50},
                                          "startDate": "2019-04-01"}]}},
        {"statementId": "s4", "recordId": "y-a", "recordType": "relationship",
         "recordDetails": {"subject": "a", "interestedParty": {"reason": "unknown"}, "interests": []}},
    ]
    g = from_bods(stmts, listed=[{"id": "x", "lists": ["SDN"]}])
    assert g.entities == {"x": {"country": "IR"}, "a": {"country": "AE"}}
    assert len(g.edges) == 1 and g.edges[0]["valid_from"] == "2019-04-01" and g.edges[0]["pct"][1] < 50
    assert any("s4" in n for n in g.notes)
    assert evaluate(g.edges, g.listed, "ofac-50").verdict("a") == "not"


def test_bods_relationships_without_parties_raise():
    with pytest.raises(InputError):
        from_bods([{"statementId": "s", "recordType": "relationship", "recordDetails": {"subject": "a"}}])


def test_cli_reads_ftm_and_bods(tmp_path, capsys):
    from ownership_rules.cli import main

    ftm = tmp_path / "entities.ftm.json"
    ftm.write_text("\n".join(json.dumps(e) for e in _sdn("x") + [
        {"id": "a", "schema": "Company", "properties": {"name": ["A"], "country": ["cy"]}},
        {"id": "o", "schema": "Ownership", "properties": {"owner": ["x"], "asset": ["a"], "percentage": ["50"]}},
    ]), encoding="utf-8")
    assert main(["evaluate", "--ftm", str(ftm)]) == 0
    assert json.loads(capsys.readouterr().out)["results"]["a"]["verdict"] == "definitely"

    bods = tmp_path / "bods.json"
    bods.write_text(json.dumps(BODS_02), encoding="utf-8")
    listed = tmp_path / "listed.json"
    listed.write_text(json.dumps([{"id": "es-x", "lists": ["SDN"]}]), encoding="utf-8")
    assert main(["explain", "--bods", str(bods), "--listed", str(listed), "es-b"]) == 0
    assert "possibly" in capsys.readouterr().out

    assert main(["evaluate", "--bods", str(bods)]) == 2
    assert main(["evaluate", "--ftm", str(ftm), "--edges", str(ftm)]) == 2
