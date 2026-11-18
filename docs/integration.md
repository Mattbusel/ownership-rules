# Integrating ownership-rules

This page is for teams adding OFAC 50 Percent Rule or BIS Affiliates Rule coverage to a screening product, a KYB
pipeline or a compliance data warehouse.

## What you take on

- **No runtime dependencies.** Standard library only, Python 3.9 to 3.13, tested on Linux, Windows and macOS.
- **Typed.** The package ships `py.typed`.
- **Apache-2.0.** The rule files and conformance vectors are in the wheel under the same license.
- **Small surface.** The names below are the public API. Anything else (modules or names starting with `_`,
  `engine` internals) can change in any release.
- **Maintained.** Matthew Busel (Tensorust LLC) maintains it and keeps the rule files current with OFAC and BIS
  notices. Release history, CI, how a Federal Register notice reaches a release, and how to take the package out
  again are in [maintenance.md](maintenance.md). Changes are listed in `CHANGELOG.md`.

## Install

```
pip install "ownership-rules @ https://github.com/Mattbusel/ownership-rules/releases/download/v0.3.0/ownership_rules-0.3.0-py3-none-any.whl"
```

The wheel URL is fixed per release and can be pinned with `--require-hashes` (the SHA-256 is on the release
page). `pip install ownership-rules` works once the PyPI upload is live. Because the package has no dependencies,
vendoring `src/ownership_rules/` is also an option.

## Five lines

```python
from ownership_rules import evaluate

ev = evaluate(edges, listed, "ofac-50", entities=countries)   # or "bis-affiliates"
for r in ev.flagged():                                        # definitely, possibly, control_only
    print(r.entity, r.verdict, r.explain())
```

`ev.rule_status` says whether the rule binds on the evaluated day: the BIS Affiliates Rule is `"stayed"` until
January 10, 2027, so a product can show its verdicts as advisory until then without changing code when the stay
ends or is extended (a rule-file update in a new release).

`edges` are `{"owner", "owned", "pct"}` dicts, `listed` are `{"id", "lists", "programs"}` dicts and `countries` is
`{id: {"country": "CN"}}` ([input.md](input.md)). `r.to_json()` gives the same result with its proof path, for
storing next to a screening hit.

## Data you already have

If the ownership data comes from OpenSanctions, yente, Aleph or another FollowTheMoney source, or from a
Beneficial Ownership Data Standard feed (Open Ownership, the UK PSC register, GLEIF in BODS), read it directly:

```python
from ownership_rules import evaluate
from ownership_rules.formats import from_ftm, from_bods

g = from_ftm(entities)                       # Ownership links, Sanction records, countries
ev = evaluate(g.edges, g.listed, "bis-affiliates", entities=g.entities)

g = from_bods(statements, listed=my_listed)  # BODS says who owns whom, not who is listed
ev = evaluate(g.edges, g.listed, "ofac-50", entities=g.entities)
```

```
ownership-rules evaluate --rule bis-affiliates --ftm us_ofac_sdn.entities.ftm.json --proofs none
ownership-rules evaluate --bods statements.json --listed listed.json
```

How the readers map fields:

| Source | Becomes |
|---|---|
| FtM `Ownership` `owner`, `asset`, `percentage` | edge; `"50%"`, `"25-50%"`, `"more than 75%"`, UK PSC bands such as `ownership-of-shares-25-to-50-percent` are read as numbers or intervals; anything else is an unknown share and is listed in `Graph.notes` |
| FtM `Ownership` with OFAC's role "Owned or Controlled By" and no percentage | ownership of unknown size (`possibly` when the owner is covered) |
| FtM `ownershipType` `indirect` or `ultimate` | skipped by default, since the direct links already carry the stake (`indirect=True` keeps them) |
| FtM `Directorship` | control edge only with `directorships=True` (gives `control_only`) |
| FtM `Sanction` | listed party; list code from the OpenSanctions dataset (`us_ofac_sdn` is `SDN`) or `programId` (`US-BIS-EL` is `EL`, `US-BIS-MEU` is `MEU`), program tags from `program`; pass `lists=` to map other lists |
| FtM `country` / `jurisdiction` | `entities[id].country` |
| BODS shareholding, voting rights and other ownership interests | edge with the interest's `share` (`exact`, or `minimum`/`maximum` as an interval; an exclusive maximum stays below the number) |
| BODS appointment, influence, trust and other control interests | control edge |
| BODS `startDate` / `endDate` | `valid_from` / `valid_to` |

On the OpenSanctions `us_ofac_sdn` export of November 17, 2026 (72,995 entities), `from_ftm` builds 5,204 edges
and 19,455 listed parties in about 1.4 seconds, and `evaluate` takes under half a second per rule.

## Engines in other languages

You do not need this package to check an engine written in Go, Java or anything else. Run the conformance
vectors against it ([vectors.md](vectors.md)):

```
ownership-rules vectors --engine "java -jar my-engine.jar"
```

or read the JSON cases from `vectors/` (also attached to every GitHub release as a zip) in your own test suite.

## As an optional dependency

Products that already screen names can add ownership coverage behind an extra, so nobody installs it who does
not use it:

```toml
[project.optional-dependencies]
ownership = ["ownership-rules>=0.2"]
```

(PyPI rejects packages whose dependencies are URLs, so a library can declare this extra once `ownership-rules` is
on PyPI. An application can point at the wheel URL today.)

```python
try:
    from ownership_rules import evaluate as ownership_evaluate
except ImportError:  # pip install yourpackage[ownership]
    ownership_evaluate = None
```

Results are evidence for a compliance review, not a legal determination.
