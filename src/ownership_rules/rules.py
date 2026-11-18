"""Ownership rules as data.

A rule is a JSON document. The built-in ones live in ``ownership_rules/rules/*.json`` and load by id
(``load_rule("ofac-50")``); any other file loads by path. The fields:

``threshold`` / ``comparator``
    The ownership test, e.g. ``50`` and ``">="`` ("50 percent or more").
``attribution``
    ``"cascade"``: an entity that meets the test is treated like a listed party for the entities it owns, and its
    whole stake counts (OFAC FAQ 401, BIS FAQ 52). ``"multiplicative"``: stakes multiply along a chain (effective
    look-through ownership), which some disclosure regimes use; OFAC and BIS do not.
``seeds``
    Selectors for the listed parties the rule starts from. A party matches a selector when it is on one of the
    selector's ``lists`` and, if given, carries one of its ``programs`` and has one of its ``entry_types``.
``subject``
    ``{"scope": "foreign", "home_country": "US"}`` limits derived coverage to entities outside that country.
    ``home_country`` may be a list (the BIS rule lists the US and its territories, as 15 CFR 772.1 defines the
    United States). Ownership still passes through home-country entities to the foreign entities they own.
``unknown_share``
    Verdict for an ownership edge from a covered party whose percentage is unknown (``"possibly"``).
``most_restrictive``
    When true, results name the listed owners whose requirements reach the entity and the strictest of them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .model import InputError, ListedParty, _str_list, program_tag

__all__ = ["Rule", "SeedSelector", "load_rule", "builtin_rules"]

_RULE_DIR = os.path.join(os.path.dirname(__file__), "rules")

COMPARATORS = (">=", ">")
ATTRIBUTIONS = ("cascade", "multiplicative")
UNKNOWN_SHARE = ("possibly", "control_only")


@dataclass(frozen=True)
class SeedSelector:
    lists: Tuple[str, ...]
    programs: Tuple[str, ...] = ()
    entry_types: Tuple[str, ...] = ()

    def matches(self, p: ListedParty) -> bool:
        if not set(self.lists) & {x.upper() for x in p.lists}:
            return False
        if self.programs and not set(self.programs) & {program_tag(x) for x in p.programs}:
            return False
        if self.entry_types and p.entry_type.lower() not in self.entry_types:
            return False
        return True


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    threshold: float
    comparator: str = ">="
    attribution: str = "cascade"
    seeds: Tuple[SeedSelector, ...] = ()
    subject_scope: str = "any"
    home_country: Tuple[str, ...] = ()
    unknown_share: str = "possibly"
    most_restrictive: bool = False
    version: Optional[str] = None
    authority: Tuple[Dict[str, str], ...] = ()
    notes: Tuple[str, ...] = ()
    raw: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def meets(self, value: float) -> bool:
        # Shares are sums of decimal percentages; compare with a small tolerance so 33.3 + 16.7 meets 50.
        eps = 1e-9
        if self.comparator == ">=":
            return value >= self.threshold - eps
        return value > self.threshold + eps

    def is_seed(self, p: ListedParty) -> bool:
        if not self.seeds:
            return True
        return any(s.matches(p) for s in self.seeds)

    def in_scope(self, entity: Mapping[str, Any]) -> bool:
        if self.subject_scope != "foreign" or not self.home_country:
            return True
        country = entity.get("country")
        return not (isinstance(country, str) and country.strip().upper() in self.home_country)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Rule":
        if not isinstance(d, Mapping):
            raise InputError("rule: expected an object")
        rid = d.get("id")
        if not isinstance(rid, str) or not rid:
            raise InputError("rule.id: expected a non-empty string")
        thr = d.get("threshold")
        if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not 0 < thr <= 100:
            raise InputError(f"rule {rid}: threshold must be a number in (0, 100]")
        comparator = d.get("comparator", ">=")
        if comparator not in COMPARATORS:
            raise InputError(f"rule {rid}: comparator must be one of {COMPARATORS}")
        attribution = d.get("attribution", "cascade")
        if attribution not in ATTRIBUTIONS:
            raise InputError(f"rule {rid}: attribution must be one of {ATTRIBUTIONS}")
        unknown = d.get("unknown_share", "possibly")
        if unknown not in UNKNOWN_SHARE:
            raise InputError(f"rule {rid}: unknown_share must be one of {UNKNOWN_SHARE}")
        seeds = []
        raw_seeds = d.get("seeds")
        if raw_seeds is not None and not isinstance(raw_seeds, list):
            raise InputError(f"rule {rid}: seeds must be an array")
        for i, s in enumerate(raw_seeds or []):
            if not isinstance(s, Mapping):
                raise InputError(f"rule {rid}: seeds[{i}] must be an object")
            for key in ("lists", "programs", "entry_types"):
                if key in s and not isinstance(s[key], list):
                    raise InputError(f"rule {rid}: seeds[{i}].{key} must be an array of strings")
            lists = _str_list(s.get("lists"), f"rule {rid}: seeds[{i}].lists", upper=True)
            if not lists:
                raise InputError(f"rule {rid}: seeds[{i}] needs a non-empty lists array")
            programs = tuple(program_tag(t) for t in _str_list(s.get("programs"), f"rule {rid}: seeds[{i}].programs"))
            types = tuple(t.lower() for t in _str_list(s.get("entry_types"), f"rule {rid}: seeds[{i}].entry_types"))
            seeds.append(SeedSelector(lists, programs, types))
        subject = d.get("subject")
        if subject is None:
            subject = {}
        if not isinstance(subject, Mapping):
            raise InputError(f"rule {rid}: subject must be an object")
        scope = subject.get("scope", "any")
        if scope not in ("any", "foreign"):
            raise InputError(f"rule {rid}: subject.scope must be any or foreign")
        home = subject.get("home_country")
        if home is not None and not isinstance(home, (str, list)):
            raise InputError(f"rule {rid}: subject.home_country must be a country code or an array of them")
        home_country = _str_list(home, f"rule {rid}: subject.home_country", upper=True)
        if scope == "foreign" and not home_country:
            raise InputError(f"rule {rid}: subject.scope foreign needs a home_country")
        for key in ("authority", "notes"):
            if d.get(key) is not None and not isinstance(d[key], list):
                raise InputError(f"rule {rid}: {key} must be an array")
        version = d.get("version")
        if version is not None and not isinstance(version, str):
            raise InputError(f"rule {rid}: version must be a string")
        return cls(
            id=rid,
            title=str(d.get("title") or rid),
            threshold=float(thr),
            comparator=comparator,
            attribution=attribution,
            seeds=tuple(seeds),
            subject_scope=scope,
            home_country=home_country,
            unknown_share=unknown,
            most_restrictive=bool(d.get("most_restrictive", False)),
            version=version,
            authority=tuple(d.get("authority") or ()),
            notes=tuple(d.get("notes") or ()),
            raw=dict(d),
        )


def builtin_rules() -> List[str]:
    """Ids of the rules shipped with the package."""
    return sorted(f[:-5] for f in os.listdir(_RULE_DIR) if f.endswith(".json"))


def load_rule(rule: Any) -> Rule:
    """Load a rule from a :class:`Rule`, a dict, a built-in id, or a path to a JSON file."""
    if isinstance(rule, Rule):
        return rule
    if isinstance(rule, Mapping):
        return Rule.from_dict(rule)
    if not isinstance(rule, str):
        raise InputError(f"rule: expected an id, path, dict or Rule, got {type(rule).__name__}")
    path = rule
    if not os.path.exists(path):
        path = os.path.join(_RULE_DIR, rule + ".json")
        if not os.path.exists(path):
            raise InputError(f"rule {rule!r}: not a file and not one of {builtin_rules()}")
    with open(path, encoding="utf-8") as f:
        return Rule.from_dict(json.load(f))
