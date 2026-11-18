# Changelog

## 0.3.0 (2026-11-18)

- Rule status as data. A rule file may give `effective` (the day it took effect) and `stays`
  (`[{"from", "until", "cite"}]`, both days included). `Rule.status(as_of)` returns `in_force`, `stayed` or
  `not_yet_effective`, `Rule.stay(as_of)` the stay in force, and `Evaluation.rule_status` the status on the
  evaluated day; `evaluate` JSON output carries `rule_status`, and `explain` prints a note when the rule does not
  bind that day. The verdicts are computed the same way whatever the status.
- `bis-affiliates`: effective 2025-09-29 (FR Doc. 2025-19001), stayed from 2025-11-10 (FR Doc. 2025-19846) until
  2027-01-10 (FR Doc. 2026-22793). On 2026-11-18 its evaluations report `stayed`; from 2027-01-11 they report
  `in_force` with no code change. `ofac-50` and `any-list-50` have no dates and are always `in_force`.
- The package type-checks under `mypy --strict` (it ships `py.typed`); CI runs the check. `Result.explain()` no
  longer fails on a proof step without a share. The vector runner rejects an external engine that prints JSON
  that is not an object with a clear error instead of failing later.
- `docs/maintenance.md`: CI, release process, release history, how rule files follow OFAC and BIS notices, API
  stability, and how to remove the package. README: install from the release wheel first (the package is not on
  PyPI yet), CI and release badges.

## 0.2.1 (2026-11-18)

- `from_ftm` and `from_bods`: a holding whose end date is on or before its start date now keeps the end date, so it
  counts as ended. 0.2.0 dropped both dates, which made the holding count forever. OpenSanctions' Russian EGRUL
  data has such records for companies dissolved on the day they were registered (`startDate` equal to
  `endDate`); on the OpenSanctions `default` export of 2026-11-18, 0.2.0 reported 31 of those companies as 50 percent
  owned by an SDN under the OFAC rule.

## 0.2.0 (2026-11-17)

- BIS Affiliates Rule: the SDN program tag is `ILLICIT-DRUGS-EO14059`, as the SDN List and the trade.gov
  Consolidated Screening List write it. 0.1.x named `ILLICIT DRUGS-EO14059` (with a space), so SDNs under that
  program (788 entries in the CSL on 2026-11-17) were not seeds of the rule and the companies they own were not
  reached.
- Program tags are compared in a canonical form: upper case, brackets dropped, spaces and underscores read as
  hyphens (`[ILLICIT DRUGS-EO14059]` matches `ILLICIT-DRUGS-EO14059`). `ownership_rules.model.program_tag` exposes it.
- `ownership_rules.formats`: `from_ftm` reads FollowTheMoney entities (OpenSanctions, yente, Aleph exports,
  flat or nested) into edges, listed parties and countries; `from_bods` reads Beneficial Ownership Data Standard
  statements (0.2 to 0.4); `parse_percentage` reads shares written as text (`"25-50%"`, `"more than 75%"`, UK PSC
  bands). On the OpenSanctions `us_ofac_sdn` export (72,995 entities) `from_ftm` takes about 1.4 s.
- CLI: `evaluate` and `explain` take `--ftm FILE` or `--bods FILE` instead of `--edges`, and `--directorships`.
- `docs/integration.md`: install from the release wheel, the public API, a five-line example, the format readers
  and the optional-dependency pattern.
- Release workflow: a failed PyPI upload no longer marks the release run as failed; the GitHub release with the
  wheel, sdist and vectors is the release of record until the PyPI project exists.

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
