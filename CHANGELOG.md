# Changelog

## 0.1.0 (2026-11-17)

- Rule engine: ownership edges with exact, interval or unknown shares and a control flag; rules as JSON data
  (`ofac-50`, `bis-affiliates`, `any-list-50`); cascade and multiplicative attribution; verdicts `listed`,
  `definitely`, `possibly`, `control_only`, `not`, each with a proof path; cycle-safe least fixed point;
  `as_of` evaluation of edges and listings; most-restrictive owner reporting for the BIS rule.
- Conformance vectors: 11 OFAC cases (FAQs 398, 399, 401 examples 1-5, 402, 2014 guidance), 11 BIS cases
  (application examples 1 and 2, FAQs 25, 41, 43, 44, 52, 744.8 program scope, 744.11 address entries, foreign
  entities only) and 7 engine cases, each quoting its source.
- `ownership-rules` command: `evaluate`, `explain`, `vectors` (with `--engine` for any external engine and
  `--binary` for yes/no engines), `case` (the protocol reference engine) and `rules`.
- Property tests against independent reference implementations (naive fixed point, FAQ 401 definition on DAGs,
  path products) and fuzzing of untrusted input.
