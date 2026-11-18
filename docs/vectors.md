# Conformance vectors

The vectors are the worked examples OFAC and BIS published, written as JSON test cases. If your engine computes
OFAC 50 Percent Rule or BIS Affiliates Rule coverage, run them. They live in `vectors/` in this repository and in
the installed package (`ownership_rules.vectors.vector_dir()`, which is `ownership_rules/conformance/` in a wheel).

| Suite | File | Cases | Source |
|---|---|---|---|
| `ofac-50` | `vectors/ofac/ofac-50.json` | 11 | OFAC FAQs 398, 399, 401 (examples 1-5), 402; revised guidance of August 13, 2014 |
| `bis-affiliates` | `vectors/bis/bis-affiliates.json` | 11 | 90 FR 47201 application examples 1 and 2; BIS Entity List FAQs 25, 41, 43, 44, 52; 15 CFR 744.8(a)(1), 744.11(a)(1) |
| `engine` | `vectors/engine/engine.json` | 9 | cycles, interval shares, propagation of `possibly` and `control_only`, the BIS rule through a US intermediary and in US territories (this project's semantics, not agency text) |

## Case format

```json
{
  "id": "ofac-faq401-example3",
  "rule": "ofac-50",
  "citation": {
    "doc": "OFAC FAQ 401, Example 3",
    "url": "https://ofac.treasury.gov/faqs/401",
    "quote": "Blocked Person X owns 50 percent of Entity A and 10 percent of Entity B. Entity A also owns 40 percent of Entity B. Entity B is considered to be blocked."
  },
  "listed": [{"id": "X", "lists": ["SDN"]}],
  "edges": [
    {"owner": "X", "owned": "A", "pct": 50},
    {"owner": "X", "owned": "B", "pct": 10},
    {"owner": "A", "owned": "B", "pct": 40}
  ],
  "expect": {
    "A": {"verdict": "definitely"},
    "B": {"verdict": "definitely", "proof_edges": [["A", "B"], ["X", "A"], ["X", "B"]]}
  }
}
```

Optional case fields: `as_of` (evaluate on that day), `entities` (facts such as `country`), `notes`. Quotes are
copied from the source with typographic quotes, apostrophes and section signs written as plain ASCII.

An expectation always has `verdict` and may have `proof_edges` (the set of owner/owned links the proof uses),
`seeds` (every listed owner whose ownership reaches the entity) and `most_restrictive`. When an expectation has
one of these, your answer must include it with the same set; an answer that leaves it out fails the proof check.

## Running your engine

Write a command that reads one case as JSON on stdin and prints:

```json
{"results": {"A": {"verdict": "definitely", "proof_edges": [["X", "A"]]}, "B": {"verdict": "not"}}}
```

The case it receives has no `expect` key; `entities_of_interest` lists the ids that will be checked. Entities you
leave out count as `not`. Then:

```
pip install ownership-rules
ownership-rules vectors --engine "node my-engine.js"
```

Until the PyPI upload is live, install the tagged release from GitHub instead:

```
pip install "ownership-rules @ https://github.com/Mattbusel/ownership-rules/releases/download/v0.2.0/ownership_rules-0.2.0-py3-none-any.whl"
```

The last line of the report counts verdicts and proofs separately, for example
`31/31 cases passed; verdicts 31/31, proofs 14/14`, so an engine that returns verdicts only can state its verdict
conformance (`--no-proofs` makes that the pass criterion).

Options:

- `--binary`: your engine only says yes or no. Print `{"covered": true}` per entity (or a verdict); `listed` and
  `definitely` count as covered.
- `--no-proofs`: compare verdicts only (no `proof_edges`, `seeds` or `most_restrictive`).
- `--suite ofac-50`: one suite.
- `--json`: machine-readable report.

The exit code is 0 when every case passes. In Python you can also call
`ownership_rules.run_vectors(engine=my_function)` where `my_function(case) -> {"results": {...}}`.

`ownership-rules case` is this package's own engine speaking the protocol, useful as a reference to diff against.

## Adding cases

Every case in `ofac-50` and `bis-affiliates` must cite an agency text and quote it. If a regulator publishes a new
example, open an issue with the link; a case that reads more into the text than it says belongs in `engine` with a
note.
