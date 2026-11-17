import json
import os
import subprocess
import sys

import pytest

from ownership_rules import load_suites, run_vectors
from ownership_rules.cli import main
from ownership_rules.vectors import check_case, command_engine

HERE = os.path.dirname(__file__)
REPO = os.path.dirname(HERE)


def test_every_packaged_case_passes():
    reports = run_vectors()
    assert reports, "no vectors found"
    failed = [(r.suite, r.case, r.failures) for r in reports if not r.passed]
    assert not failed


def test_suites_are_complete_and_cited():
    suites = {s["suite"]: s for s in load_suites()}
    assert set(suites) == {"ofac-50", "bis-affiliates", "engine"}
    ids = [c["id"] for s in suites.values() for c in s["cases"]]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for name in ("ofac-50", "bis-affiliates"):
        for c in suites[name]["cases"]:
            cit = c["citation"]
            assert cit["url"].startswith("https://") and cit["quote"] and cit["doc"]
    for s in suites.values():
        for c in s["cases"]:
            assert c["expect"]
            for exp in c["expect"].values():
                assert exp["verdict"] in ("listed", "definitely", "possibly", "control_only", "not")


def test_vectors_are_ascii_dash_clean():
    for s in load_suites():
        with open(s["_file"], encoding="utf-8") as f:
            text = f.read()
        assert chr(0x2014) not in text


def test_check_case_reports_mismatches():
    case = {"expect": {"A": {"verdict": "definitely", "proof_edges": [["X", "A"]]}, "B": {"verdict": "not"}}}
    assert check_case(case, {"results": {"A": {"verdict": "definitely", "proof_edges": [["X", "A"]]}}}) == []
    fails = check_case(case, {"results": {"A": {"verdict": "possibly"}}})
    assert fails == ["A: expected definitely, got possibly", "A: proof_edges expected, missing from the answer"]
    assert check_case(case, {"results": {"A": {"verdict": "possibly"}}}, check_proofs=False) == [
        "A: expected definitely, got possibly"]
    fails = check_case(case, {"results": {"A": {"verdict": "definitely", "proof_edges": [["Y", "A"]]}}})
    assert "proof_edges" in fails[0]
    assert check_case(case, {"results": {"A": {"covered": True}}}, binary=True) == []
    assert check_case(case, {"results": {"A": {"verdict": "possibly"}}}, binary=True) == [
        "A: expected covered=True, got covered=False"]


def test_external_engine_protocol_with_this_package_as_the_engine():
    cmd = f'"{sys.executable}" -m ownership_rules.cli case'
    reports = run_vectors(command_engine(cmd))
    assert all(r.passed for r in reports), [r for r in reports if not r.passed]


def test_binary_engine_that_ignores_ownership_fails_loudly():
    reports = run_vectors(lambda case: {"results": {}}, binary=True)
    assert any(not r.passed for r in reports)
    assert any(r.passed for r in reports)


def test_cli_evaluate_explain_rules(tmp_path, capsys):
    edges = tmp_path / "edges.jsonl"
    edges.write_text('{"owner": "X", "owned": "A", "pct": 50}\n{"owner": "A", "owned": "B", "pct": 50}\n',
                     encoding="utf-8")
    listed = tmp_path / "listed.json"
    listed.write_text(json.dumps([{"id": "X", "lists": ["SDN"]}]), encoding="utf-8")
    assert main(["evaluate", "--edges", str(edges), "--listed", str(listed)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["results"]["B"]["verdict"] == "definitely"
    assert main(["explain", "--edges", str(edges), "--listed", str(listed), "B"]) == 0
    assert "A (definitely) owns 50% of B" in capsys.readouterr().out
    assert main(["rules"]) == 0
    assert "bis-affiliates" in capsys.readouterr().out
    assert main(["rules", "ofac-50"]) == 0
    assert json.loads(capsys.readouterr().out)["threshold"] == 50
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"owner": "X", "owned": "X"}\n', encoding="utf-8")
    assert main(["evaluate", "--edges", str(bad), "--listed", str(listed)]) == 2
    assert main(["vectors", "--suite", "engine"]) == 0


def test_console_script_vectors_json():
    p = subprocess.run([sys.executable, "-m", "ownership_rules.cli", "vectors", "--json"], capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stderr
    report = json.loads(p.stdout)
    assert report["failed"] == 0 and report["passed"] >= 29


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO, "vectors")), reason="needs the source checkout")
def test_repo_vectors_match_packaged_vectors():
    from ownership_rules.vectors import vector_dir
    packaged = sorted(os.path.relpath(os.path.join(d, f), vector_dir())
                      for d, _, fs in os.walk(vector_dir()) for f in fs)
    repo = sorted(os.path.relpath(os.path.join(d, f), os.path.join(REPO, "vectors"))
                  for d, _, fs in os.walk(os.path.join(REPO, "vectors")) for f in fs)
    assert packaged == repo
