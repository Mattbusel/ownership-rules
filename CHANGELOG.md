# Changelog

## 0.1.1 (2026-11-17)

- BIS Affiliates Rule: ownership now passes through US entities. A foreign entity that a listed party owns
  through a US holding company was reported `not`; it is now reached ("directly or indirectly"). The US entity is
  still reported `not` (reason `out_of_scope`), and its proof step is marked `out_of_scope`. The multiplicative
  mode does the same.
- BIS Affiliates Rule: `home_country` is now US, PR, GU, VI, AS, MP and UM (15 CFR 772.1 defines the United States
  to include Puerto Rico and the territories). `subject.home_country` accepts a list.
- Without `as_of`, the engine now uses today's date (UTC). 0.1.0 dropped every edge with a `valid_to` and every
  listing with a `listed_to`, even future ones. `Evaluation.as_of` reports the day used.
- List codes and program tags are compared without regard to case (`sdn` matches `SDN`); `entry_type` too.
- Rule files are fully validated: `subject` must be an object, `seeds` an array of objects whose `lists`,
  `programs` and `entry_types` are arrays of strings, `home_country` a code or array of codes (required for the
  foreign scope), `notes` and `authority` arrays, `version` a string. Malformed rules raise `InputError` instead of
  `AttributeError` or a silent wrong seed set.
- Proofs: `to_json(proofs="shared")` and `evaluate --proofs shared|none`. Shared mode writes each step once and
  links steps by id; on a 100 percent chain of 3000 entities the output is about 1 MB instead of 770 MB. Proof
  steps are also shared in memory, and `evaluate` on that chain takes about 2.6 s instead of 8 s on the same
  machine.
- Edges may carry `basis` (what the share measures, such as `accounting_consolidation`); it is copied into proofs.
- Vectors: a case's expected `proof_edges`, `seeds` and `most_restrictive` are now required in the answer (an
  engine that printed verdicts only used to pass the proof checks). The report counts verdicts and proofs
  separately. Two new `engine` cases: BIS through a US intermediary, and US territories. 31 cases in all.
- CLI: `--edges` and `--listed` accept one JSON object with `edges` and `listed` arrays, as the help said.

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
