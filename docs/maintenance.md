# Maintenance

What a team taking on `ownership-rules` as a dependency can check for itself.

## Who and how

- Maintained by Matthew Busel (Tensorust LLC). Issues and pull requests on GitHub.
- Every push and pull request runs the tests on Python 3.9 to 3.13 (Linux), 3.12 on Windows and macOS, the
  conformance vectors through the installed command, `mypy --strict` on the package, and a wheel built and tested
  outside the source tree ([test.yml](../.github/workflows/test.yml)).
- A release is a `vX.Y.Z` tag. The [publish workflow](../.github/workflows/publish.yml) checks that the tag matches
  `__version__`, builds the wheel and sdist, runs the vectors against the built wheel, and attaches the wheel, the
  sdist and a zip of the vectors to the GitHub release. The same job uploads to PyPI with trusted publishing (no
  stored token) once the PyPI project exists.
- Every change is in [CHANGELOG.md](../CHANGELOG.md), with the observed effect when a fix changes verdicts.

## Releases so far

| Version | Date | What changed |
|---|---|---|
| 0.1.0 | 2026-11-17 | engine, OFAC and BIS rule files, 29 conformance vectors (11 OFAC, 11 BIS, 7 engine) |
| 0.1.1 | 2026-11-17 | BIS ownership through US intermediaries, US territories, validated rule files, shared proofs |
| 0.2.0 | 2026-11-18 | `ILLICIT-DRUGS-EO14059` program tag fix, FollowTheMoney and BODS readers |
| 0.2.1 | 2026-11-18 | holdings that ended on their start date count as ended |
| 0.3.0 | 2026-11-18 | `rule_status` (BIS stay as data), `mypy --strict` clean |

The project is new. The release history above is all of it; judge it by the tests, the vectors and the changelog.

## How the rules stay current

The rules are data files in `src/ownership_rules/rules/`, each citing the Federal Register documents, regulations
and FAQs it encodes (`authority`). When OFAC or BIS publishes something that changes a rule (a new FAQ, a stay, an
extension, a program tag), the rule file changes, a conformance vector is added for the new text where it gives
an example, and a release follows. The BIS Affiliates Rule stay (until January 10, 2027) is in the rule file as
`stays`, so `Evaluation.rule_status` changes on its own when the stay ends; an extension needs a new release.

The vectors in `vectors/` are the specification: each case quotes the OFAC or BIS text it encodes. An engine in
any language can run them (`ownership-rules vectors --engine "<command>"`), so a team can keep its own
implementation and use this package only to check it.

## Stability

- Public API: the names in `ownership_rules.__all__` and `ownership_rules.formats`. Within 0.x, a minor version
  (0.3 to 0.4) may change them and says so in the changelog; a patch version does not.
- Verdict values (`listed`, `definitely`, `possibly`, `control_only`, `not`) and the vector format are stable.
- A fix that changes verdicts on real data is called out in the changelog with the count of affected entities
  where it was measured.

## Taking it out again

No runtime dependencies, so removing it is one line in your requirements and the call sites of `evaluate`. The
verdicts are plain strings and the proofs plain JSON, so stored results stay readable without the package.
Vendoring `src/ownership_rules/` (Apache-2.0) is also an option if you would rather not depend on a release.

Results are evidence for a compliance review, not a legal determination.
