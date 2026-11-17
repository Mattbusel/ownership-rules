# ownership-rules

Sanctions lists name parties. Two US rules reach further, to the companies those parties own:

- **OFAC 50 Percent Rule**: an entity owned 50 percent or more, directly or indirectly, in the aggregate, by
  blocked persons is itself blocked.
- **BIS Affiliates Rule**: a foreign entity owned 50 percent or more by Entity List, MEU List or certain SDN
  parties takes their export restrictions. It is suspended through **January 10, 2027**
  ([FR Doc. 2026-22793](https://www.federalregister.gov/documents/2026/11/09/2026-22793/extension-of-the-suspension-of-expansion-of-end-user-controls-for-affiliates-of-certain-listed)).

`ownership-rules` writes both rules down as data, applies them to an ownership graph, and returns a verdict with
the proof path behind it. It also ships **conformance vectors**: every worked example in the OFAC FAQs and the
BIS rule and FAQs, as JSON, with the quoted text each case encodes. Any screening engine, in any language, can
run them.

```
pip install ownership-rules
```

Until the PyPI upload is live, install the tagged release from GitHub:

```
pip install "ownership-rules @ git+https://github.com/Mattbusel/ownership-rules@v0.1.1"
```

## Use

```python
from ownership_rules import evaluate

ev = evaluate(
    edges=[
        {"owner": "X", "owned": "A", "pct": 50},
        {"owner": "X", "owned": "B", "pct": 10},
        {"owner": "A", "owned": "B", "pct": 40},
    ],
    listed=[{"id": "X", "lists": ["SDN"]}],
    rule="ofac-50",
)
print(ev["B"].verdict)    # definitely
print(ev["B"].explain())
# X: listed (SDN)
# A: definitely (covered share 50-50%) because X (listed) owns 50% of A
# B: definitely (covered share 50-50%) because A (definitely) owns 40% of B; X (listed) owns 10% of B
```

That is OFAC FAQ 401, Example 3.

## Verdicts

| Verdict | Meaning |
|---|---|
| `listed` | the entity is itself on a list the rule starts from |
| `definitely` | known ownership by covered parties meets the threshold |
| `possibly` | only the upper end of a share interval meets it, or a covered party owns a share of unknown size (BIS Red Flag 29) |
| `control_only` | a covered party controls the entity, or owns it through links with no percentage, but nothing shows 50 percent ownership (OFAC FAQ 398, BIS FAQ 43: not covered by the rule, review it) |
| `not` | nothing reaches the threshold |

Shares can be exact (`50`), intervals (`[40, 60]`) or unknown (`null`). Cycles are safe. Each verdict carries the
edges and listed parties that produce it.

## Conformance vectors

```
ownership-rules vectors                                  # this package
ownership-rules vectors --engine "python my_engine.py"   # yours: case JSON on stdin, results JSON on stdout
ownership-rules vectors --engine "..." --binary          # engines that only answer covered yes/no
```

31 cases in three suites: `ofac-50` (FAQs 398, 399, 401 examples 1-5, 402 and the 2014 guidance), `bis-affiliates`
(the rule's application examples and FAQs 25, 41, 43, 44, 52, plus 744.8 and 744.11 scope), and `engine`
(cycles, intervals, propagation, and the BIS rule's foreign scope). The protocol is in [docs/vectors.md](docs/vectors.md).

## Docs

- [docs/rules.md](docs/rules.md): how each rule is encoded and where OFAC and BIS differ
- [docs/input.md](docs/input.md): edge, listed party and entity fields
- [docs/vectors.md](docs/vectors.md): vector format and the engine protocol

Results are evidence for a compliance review, not a legal determination.

Apache-2.0. Built by [Tensorust](https://tensorust.com). Restricted-party data that uses this engine:
[us-restricted-parties](https://github.com/Mattbusel/us-restricted-parties).
