# How the rules are encoded

Each rule is a JSON file in `src/ownership_rules/rules/`. Print one with `ownership-rules rules ofac-50`. Load your
own with `evaluate(..., rule="path/to/rule.json")` or pass a dict.

## Fields

| Field | Values | Meaning |
|---|---|---|
| `threshold` | number in (0, 100] | the ownership test, in percent |
| `comparator` | `>=` (default), `>` | "50 percent or more" is `>=`; "more than 50 percent" is `>` |
| `attribution` | `cascade` (default), `multiplicative` | how indirect ownership counts (below) |
| `seeds` | list of selectors | which listed parties the rule starts from |
| `subject` | `{"scope": "any"}` or `{"scope": "foreign", "home_country": "US"}` | which entities the rule can reach |
| `unknown_share` | `possibly` (default), `control_only` | verdict when a covered party owns a share of unknown size |
| `most_restrictive` | bool | report which listed owners' requirements reach the entity, and the strictest |
| `authority`, `notes`, `version` | | citations and plain-language notes |

A seed selector is `{"lists": [...], "programs": [...], "entry_types": [...]}`. A listed party matches when it is
on one of the lists, and (if given) carries one of the programs and has one of the entry types. With no seeds,
every listed party passed in is a seed.

## Cascade attribution (OFAC and BIS)

OFAC FAQ 401 defines indirect ownership as ownership "through another entity or entities that are 50 percent or
more owned in the aggregate by the blocked person(s)". An entity that meets the test becomes a blocked person for
the entities it owns, and its whole stake counts. Stakes are not multiplied along the chain:

- Example 1: X owns 50% of A, A owns 50% of B. B is blocked. (Multiplying would give 25%.)
- Example 4: X owns 50% of A and 25% of B; A and B each own 25% of C. C is not blocked, because B is not blocked
  and so its 25% does not count.

BIS FAQ 52 applies the same chain to the Entity List. The engine computes the least fixed point of this rule in
rounds. Each round reads only the previous round's verdicts, so an ownership cycle with no listed owner stays
`not` (it cannot justify itself), and every proof cites only verdicts reached earlier.

## Multiplicative attribution

`"attribution": "multiplicative"` computes look-through ownership: the product of stakes along each path from a
seed, summed over paths. Neither OFAC nor BIS uses it. It is there for disclosure-style tests and to show the
difference (see `tests/test_engine.py::test_multiplicative_differs_from_cascade_on_faq401_example1`).

## Verdict computation

For each entity the engine sums, over its owners:

- `lo`: the low end of every known share held by owners that are `listed` or `definitely`;
- `hi`: the high end of every known share held by owners that are at least `possibly`;
- `review`: every share held by owners that are at least `control_only`, counting a link with no percentage as 100.

Then: `lo` meets the test -> `definitely`; `hi` meets it -> `possibly` (`share_interval`); a link of unknown size
from an owner that is at least `possibly` -> `possibly` (`unknown_share`); a control link from an owner that is at
least `control_only`, or `review` meets the test -> `control_only`; otherwise `not`. Sums are capped at 100.

Several edges for the same owner and owned entity are one holding: the share becomes the hull of the reported
shares and `control` is true if any report says so. Sum separate share classes before passing them in.

## OFAC 50 Percent Rule (`ofac-50`)

Seeds: lists `SDN` and `BLOCKED` (use `BLOCKED` for persons blocked without an SDN entry, such as a blocked
government). Stakes of persons blocked under different programs add up (FAQ 399). Control without 50 percent
ownership does not block (FAQ 398); the engine reports `control_only` because OFAC "urges caution". After a
divestment below 50 percent the entity is no longer blocked for new dealings (FAQ 402): give edges `valid_from` /
`valid_to` and evaluate with `as_of`. FAQ 402 also says property blocked earlier stays blocked; that is a question
about property, not ownership, and is out of scope.

## BIS Affiliates Rule (`bis-affiliates`)

Seeds: Entity List entries of type entity or individual (`EL`), the MEU List (`MEU`), and SDNs under the 15 CFR
744.8(a)(1) programs: BELARUS-EO14038, BELARUS, RUSSIA-EO14024, UKRAINE-EO13660, UKRAINE-EO13661,
UKRAINE-EO13662, UKRAINE-EO13685, FTO, SDGT, NPWMD, ILLICIT-DRUGS-EO14059, SDNT, SDNTK and TCO. An SDN under any
other program (for example CUBA) does not count under this rule, though it does under OFAC's.

Differences from OFAC that the rule file encodes:

- Only **foreign** entities are reached (`subject.scope = foreign`). `home_country` lists US, PR, GU, VI, AS, MP
  and UM, because 15 CFR 772.1 defines the United States to include Puerto Rico and all territories and
  possessions. Give entities a `country` to use this.
- Ownership **passes through** US entities. A US company owned by a listed party is not itself reached, but a
  foreign company it owns 50 percent or more is owned indirectly by the listed party and is reached (the
  regulation says "directly or indirectly"). The US intermediary is reported `not` with reason `out_of_scope`;
  in the foreign entity's proof its step carries `"out_of_scope": true`. This is this project's reading: no
  agency example covers the chain, so the vector for it (`engine-bis-indirect-through-us-intermediary`) is in
  the `engine` suite.
- Entities operating at an Entity List **address** entry do not count as owners unless they are named
  (744.11(a)(1)). Give such entries `entry_type: "address"`.
- **Most restrictive**: an affiliate takes the strictest requirements among its listed owners (FAQ 44). Give listed
  parties a numeric `restrictiveness` and the result names the strictest; `seeds` always lists every listed owner
  whose ownership reaches the entity (for the foreign direct product rules both apply, application example 2).
- **Unknown percentage**: Red Flag 29 (FAQ 41) requires the exporter to determine the percentage or apply for a
  license. The engine reports `possibly`.

Wording conflict: FAQ 44 says the rule applies when the combined ownership across lists "is greater than 50
percent". The regulation text (744.11(a)(1), 744.8(a)(2)) and every other FAQ say "50 percent or more". The rule
file follows the regulation (`>=`), and no vector depends on exactly 50 percent split across lists.

Status: effective September 29, 2025 (90 FR 47201), stayed from November 10, 2025 (90 FR 50857, "stayed until
November 9, 2026"), and further stayed until January 10, 2027 (91 FR 70789, FR Doc. 2026-22793). The rule file
encodes this as `"effective": "2025-09-29"` and one `stays` entry from 2025-11-10 until 2027-01-10 (the end day is
read as included; check the Federal Register before relying on that day). `Rule.status(as_of)` and
`Evaluation.rule_status` report `in_force`, `stayed` or `not_yet_effective`, and `evaluate` JSON output carries
`rule_status`. The verdicts are computed the same way whatever the status. When BIS extends or ends the stay, the
rule file changes in a new release.

## Not covered

- Ownership by individuals through trusts, nominees or bearer shares: model them as edges if you know them.
- OFAC programs with broader "owned or controlled" criteria (for example Cuba): add a rule with your own seeds.
- EU and UK rules use "more than 50 percent" and add control tests; write a rule file with `"comparator": ">"` and
  treat control separately.

Results are evidence for review, not a legal determination.
