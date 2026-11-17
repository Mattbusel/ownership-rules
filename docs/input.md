# Input format

`evaluate(edges, listed, rule, entities=None, as_of=None)` takes plain dicts (or the dataclasses in
`ownership_rules.model`). The command line reads the same records from a JSON array or JSON Lines file.

## Edges

| Field | Required | Meaning |
|---|---|---|
| `owner` | yes | id of the owner |
| `owned` | yes | id of the owned entity (must differ from `owner`) |
| `pct` | no | `50`, `[40, 60]`, `{"min": 40, "max": 60}`, or `null` / missing for unknown |
| `control` | no | `true` when the link gives control (board, consolidation, management) |
| `source` | no | where the edge came from; copied into proofs |
| `as_of` | no | the day the source stated the edge (`YYYY-MM-DD`); copied into proofs |
| `valid_from`, `valid_to` | no | when the holding started and ended (`valid_to` is exclusive) |

Other fields are kept and returned by `Edge.to_json()`.

How the engine reads a link:

| `pct` | `control` | Reading |
|---|---|---|
| number or interval | false | ownership of that size |
| number or interval | true | ownership of that size that also gives control |
| null | false | ownership of unknown size |
| null | true | control with no known ownership (control-only) |

Percentages must be numbers from 0 to 100. Booleans, strings and NaN are rejected with `InputError`.

## Listed parties

| Field | Meaning |
|---|---|
| `id` | the party's id, matching edge ids |
| `lists` | list codes, such as `SDN`, `EL`, `MEU`, `BLOCKED`, or any code your rule names |
| `programs` | program tags, such as `RUSSIA-EO14024` |
| `entry_type` | `entity` (default), `individual`, `address`, `vessel`, `aircraft` |
| `country` | ISO country code |
| `restrictiveness` | a number, higher is stricter; used by rules with `most_restrictive` |
| `listed_from`, `listed_to` | listing period (`listed_to` exclusive) |
| `name` | shown in proofs |

A bare string is a party with no list codes; it is a seed only for rules with no seed selectors (such as
`any-list-50`). The same id on several lists is merged.

## Entities

Optional facts per entity id, today only `country`, used by rules limited to foreign entities:

```json
{"US1": {"country": "US"}, "MY1": {"country": "MY"}}
```

## Dates

Without `as_of`, edges with a `valid_to` and listings with a `listed_to` are treated as ended. With `as_of`, the
engine keeps the edges and listings in force on that day.

## Output

`Evaluation.to_json()` returns every entity whose verdict is not `not`:

```json
{
  "rule": "ofac-50",
  "rule_version": "2014-08-13",
  "as_of": null,
  "results": {
    "B": {
      "entity": "B",
      "verdict": "definitely",
      "share": [50, 50],
      "seeds": ["X"],
      "proof": [
        {"entity": "X", "verdict": "listed", "round": 0, "lists": ["SDN"]},
        {"entity": "A", "verdict": "definitely", "round": 1, "share": [50, 50],
         "via": [{"owner": "X", "owner_verdict": "listed", "pct": 50}]},
        {"entity": "B", "verdict": "definitely", "round": 2, "share": [50, 50],
         "via": [{"owner": "A", "owner_verdict": "definitely", "pct": 40},
                 {"owner": "X", "owner_verdict": "listed", "pct": 10}]}
      ],
      "proof_edges": [["A", "B"], ["X", "A"], ["X", "B"]]
    }
  }
}
```

`share` is the final covered share (low and high). Each proof step records the owners and verdicts that produced
that step, in the round it was reached; `via` lists every contributing link, not a minimal subset.
