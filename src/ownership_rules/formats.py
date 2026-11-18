"""Read ownership graphs from the formats screening and registry data already come in.

Two readers, standard library only. Each returns a :class:`Graph` whose ``edges``, ``listed`` and ``entities`` go
straight into :func:`ownership_rules.evaluate`:

>>> from ownership_rules import evaluate
>>> from ownership_rules.formats import from_ftm
>>> g = from_ftm([
...     {"id": "x", "schema": "Company", "datasets": ["us_ofac_sdn"], "properties": {"name": ["X"]}},
...     {"id": "x-s", "schema": "Sanction", "properties": {"entity": ["x"], "program": ["RUSSIA-EO14024"]}},
...     {"id": "a", "schema": "Company", "properties": {"name": ["A"], "country": ["cy"]}},
...     {"id": "o", "schema": "Ownership", "properties": {"owner": ["x"], "asset": ["a"], "percentage": ["50%"]}},
... ])
>>> evaluate(g.edges, g.listed, "ofac-50", entities=g.entities)["a"].verdict
'definitely'

- :func:`from_ftm`: FollowTheMoney entities (OpenSanctions, yente, Aleph): ``Ownership`` links, ``Sanction``
  records and the entities' countries.
- :func:`from_bods`: Beneficial Ownership Data Standard statements, versions 0.2 to 0.4 (Open Ownership, the UK
  PSC register in BODS, GLEIF in BODS).

Neither reader guesses. A percentage it cannot read becomes an unknown share (``possibly`` when a covered party
holds it), and each such value is reported in ``Graph.notes``.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from .model import InputError, program_tag

__all__ = ["Graph", "from_ftm", "from_bods", "parse_percentage", "OPENSANCTIONS_LISTS", "OPENSANCTIONS_PROGRAM_IDS"]

Pct = Union[float, List[float], None]

# OpenSanctions dataset names and Sanction programIds that map onto the list codes the built-in rules start from.
OPENSANCTIONS_LISTS: Dict[str, str] = {
    "us_ofac_sdn": "SDN",
    "us_bis_mieu": "MEU",
}
OPENSANCTIONS_PROGRAM_IDS: Dict[str, str] = {
    "US-BIS-EL": "EL",
    "US-BIS-MEU": "MEU",
}

# FtM ownershipType values that restate a holding already reached through a chain of direct links. Counting them as
# well would add the same stake twice, so they are skipped unless indirect=True.
_INDIRECT = {"indirect", "ultimate"}


@dataclass
class Graph:
    """Edges, listed parties and entity facts read from another format, ready for ``evaluate``."""

    edges: List[Dict[str, Any]] = field(default_factory=list)
    listed: List[Dict[str, Any]] = field(default_factory=list)
    entities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


_NUM = r"(\d+(?:\.\d+)?)"
# "Less than 50" never meets a 50 percent test. The engine compares with a 1e-9 tolerance and prints six decimals,
# so a strict upper bound is stored this far below the stated number.
_BELOW = 1e-6


def parse_percentage(value: Any) -> Tuple[Pct, bool]:
    """Read a share as written in registries and FtM ``percentage`` values.

    Returns ``(pct, understood)`` where ``pct`` is a number, a ``[min, max]`` interval or ``None`` (unknown).
    ``"50"``, ``"50%"``, ``"50,5 %"`` (a bare number is a percentage: ``"0.5"`` is half a percent). Ranges:
    ``"25-50%"``, ``"25 to 50"``, ``"ownership-of-shares-25-to-50-percent"`` (UK PSC), ``"more than 25%"``,
    ``"over 75%"``, ``">50"``, ``"at least 50"``, ``"less than 10%"``, ``"up to 25"``.

    >>> parse_percentage("25-50%")
    ([25.0, 50.0], True)
    >>> parse_percentage("ownership-of-shares-75-to-100-percent")
    ([75.0, 100.0], True)
    >>> parse_percentage("majority")
    (None, False)
    """
    if value is None:
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        return (float(value), True) if 0 <= value <= 100 else (None, False)
    if not isinstance(value, str):
        return None, False
    t = value.strip().lower().replace(",", ".")
    if not t:
        return None, True
    t = t.replace("percent", "%").replace("per cent", "%")
    m = re.fullmatch(_NUM + r"\s*%?", t)
    if m:
        v = float(m.group(1))
        return (v, True) if v <= 100 else (None, False)
    m = re.search(_NUM + r"\s*%?\s*(?:-\s*to\s*-|-|–|to)\s*" + _NUM, t)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return ([lo, hi], True) if lo <= hi <= 100 else (None, False)
    m = re.fullmatch(r"(?:more than|over|above|greater than|>=?|at least|min(?:imum)?)\s*" + _NUM + r"\s*%?", t)
    if m:
        v = float(m.group(1))
        return ([v, 100.0], True) if v <= 100 else (None, False)
    m = re.fullmatch(r"(less than|under|below|<|<=|up to|at most|max(?:imum)?)\s*" + _NUM + r"\s*%?", t)
    if m:
        v = float(m.group(2))
        strict = m.group(1) in ("less than", "under", "below", "<")
        return ([0.0, max(0.0, v - _BELOW) if strict else v], True) if v <= 100 else (None, False)
    return None, False


def _props(e: Mapping[str, Any]) -> Mapping[str, Any]:
    p = e.get("properties")
    return p if isinstance(p, Mapping) else {}


def _vals(p: Mapping[str, Any], key: str) -> List[Any]:
    v = p.get(key)
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _ref(v: Any) -> Optional[str]:
    """An FtM entity reference: an id string, or a nested entity object (OpenSanctions nested exports)."""
    if isinstance(v, str) and v:
        return v
    if isinstance(v, Mapping) and isinstance(v.get("id"), str) and v["id"]:
        return v["id"]
    return None


def _date(v: Any) -> Optional[str]:
    """FtM and BODS dates may be partial (``2021`` or ``2021-05``); only full, valid days become validity bounds."""
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
        try:
            return _dt.date.fromisoformat(v[:10]).isoformat()
        except ValueError:
            return None
    return None


# Reverse properties under which nested exports put a link inside one of its ends, and the end they stand for.
_NESTED_END = {"ownershipOwner": ("owner", "Ownership"), "ownershipAsset": ("asset", "Ownership"),
               "directorshipDirector": ("director", "Directorship"),
               "directorshipOrganization": ("organization", "Directorship"), "sanctions": ("entity", "Sanction")}


def _walk_ftm(items: Iterable[Any]) -> Iterable[Tuple[Mapping[str, Any], Optional[Tuple[str, str]]]]:
    """Every entity, including those nested inside properties (OpenSanctions ``targets.nested.json``), with the
    ``(property, id)`` of the entity it was nested in."""
    stack: List[Tuple[Any, Optional[Tuple[str, str]]]] = [(e, None) for e in items]
    stack.reverse()
    while stack:
        e, ctx = stack.pop()
        if not isinstance(e, Mapping):
            continue
        yield e, ctx
        eid = e.get("id")
        nested: List[Tuple[Any, Optional[Tuple[str, str]]]] = []
        for key, vs in _props(e).items():
            if isinstance(vs, list):
                nested.extend((v, (key, eid) if isinstance(eid, str) else None)
                              for v in vs if isinstance(v, Mapping) and "schema" in v)
        stack.extend(reversed(nested))


def _with_end(e: Mapping[str, Any], ctx: Optional[Tuple[str, str]]) -> Mapping[str, Any]:
    """A nested link often leaves out the end it is nested in; put it back."""
    if ctx is None or ctx[0] not in _NESTED_END:
        return e
    prop, schema = _NESTED_END[ctx[0]]
    p = _props(e)
    if e.get("schema") != schema or _vals(p, prop):
        return e
    return dict(e, properties=dict(p, **{prop: [ctx[1]]}))


def from_ftm(entities: Iterable[Any], *,
             lists: Optional[Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], Iterable[str]]] = None,
             indirect: bool = False, directorships: bool = False) -> Graph:
    """Read FollowTheMoney entities (dicts, as in ``entities.ftm.json`` or a yente/Aleph export).

    - ``Ownership`` becomes an edge ``owner -> asset``. ``percentage`` is read with :func:`parse_percentage`;
      ``startDate`` / ``endDate`` become ``valid_from`` / ``valid_to`` (the end date is the first day the holding no
      longer counts). OFAC's "Owned or Controlled By" links carry no percentage and become shares of unknown size.
      ``ownershipType`` ``indirect`` or ``ultimate`` restates a stake that direct links already reach, so it is
      skipped unless ``indirect=True``.
    - ``Directorship`` becomes a control edge (``control: true``, no share) only with ``directorships=True``: a
      listed director controls but does not own (OFAC FAQ 398), so the company is reported ``control_only``.
    - A party is listed when a ``Sanction`` names it. Its list codes come from ``lists(entity, sanctions)`` when
      given; by default from the entity's OpenSanctions ``datasets`` (:data:`OPENSANCTIONS_LISTS`, so
      ``us_ofac_sdn`` is ``SDN``) and the sanctions' ``programId`` (:data:`OPENSANCTIONS_PROGRAM_IDS`, so
      ``US-BIS-EL`` is ``EL``). The sanctions' ``program`` values are the program tags (``RUSSIA-EO14024``).
    - ``country`` (or ``jurisdiction``) of each entity fills ``entities`` for rules limited to foreign companies.
    """
    by_id: Dict[str, Mapping[str, Any]] = {}
    sanctions: Dict[str, List[Mapping[str, Any]]] = {}
    links: List[Mapping[str, Any]] = []
    for e, ctx in _walk_ftm(entities):
        e = _with_end(e, ctx)
        eid, schema = e.get("id"), e.get("schema")
        if not isinstance(schema, str):
            continue
        p = _props(e)
        if schema == "Sanction":
            for t in _vals(p, "entity"):
                r = _ref(t)
                if r:
                    sanctions.setdefault(r, []).append(e)
        elif schema in ("Ownership", "Directorship"):
            links.append(e)
        elif isinstance(eid, str) and eid:
            prev = by_id.get(eid)
            if prev is None or len(_props(e)) > len(_props(prev)):
                by_id[eid] = e
            elif "datasets" in e and "datasets" not in prev:
                by_id[eid] = e

    g = Graph()
    for e in links:
        p = _props(e)
        lid = e.get("id", "?")
        if e["schema"] == "Directorship":
            if not directorships:
                continue
            owners, assets = _vals(p, "director"), _vals(p, "organization")
        else:
            types = {str(t).strip().lower() for t in _vals(p, "ownershipType")}
            if types & _INDIRECT and not indirect:
                g.notes.append(f"{lid}: skipped ownershipType {sorted(types & _INDIRECT)} (indirect=True keeps it)")
                continue
            owners, assets = _vals(p, "owner"), _vals(p, "asset")
        for o in filter(None, map(_ref, owners)):
            for a in filter(None, map(_ref, assets)):
                if o == a:
                    g.notes.append(f"{lid}: skipped self-ownership of {o}")
                    continue
                edge: Dict[str, Any] = {"owner": o, "owned": a, "source": f"ftm:{lid}"}
                if e["schema"] == "Directorship":
                    edge.update(pct=None, control=True)
                else:
                    pcts = _vals(p, "percentage")
                    pct, ok = parse_percentage(pcts[0] if pcts else None)
                    if not ok:
                        g.notes.append(f"{lid}: percentage {pcts[0]!r} not understood, share left unknown")
                    edge["pct"] = pct
                    roles = _vals(p, "role")
                    if roles:
                        edge["role"] = str(roles[0])
                vf = _date((_vals(p, "startDate") or [None])[0])
                vt = _date((_vals(p, "endDate") or [None])[0])
                if vf and vt and vt <= vf:
                    vf = vt = None
                if vf:
                    edge["valid_from"] = vf
                if vt:
                    edge["valid_to"] = vt
                g.edges.append(edge)

    for eid, sans in sanctions.items():
        ent = by_id.get(eid, {"id": eid})
        if lists is not None:
            codes = list(dict.fromkeys(str(c) for c in lists(ent, sans)))
        else:
            codes = [OPENSANCTIONS_LISTS[d] for d in _vals(ent, "datasets") if d in OPENSANCTIONS_LISTS]
            for s in sans:
                codes += [OPENSANCTIONS_LISTS[d] for d in _vals(s, "datasets") if d in OPENSANCTIONS_LISTS]
                codes += [OPENSANCTIONS_PROGRAM_IDS[x] for x in _vals(_props(s), "programId")
                          if x in OPENSANCTIONS_PROGRAM_IDS]
            codes = list(dict.fromkeys(codes))
        programs = list(dict.fromkeys(program_tag(str(x)) for s in sans for x in _vals(_props(s), "program") if x))
        party: Dict[str, Any] = {"id": eid, "lists": codes, "programs": programs}
        schema = ent.get("schema")
        if schema == "Person":
            party["entry_type"] = "individual"
        elif schema == "Vessel":
            party["entry_type"] = "vessel"
        elif schema == "Airplane":
            party["entry_type"] = "aircraft"
        names = _vals(_props(ent), "name")
        if names and isinstance(names[0], str):
            party["name"] = names[0]
        g.listed.append(party)

    for eid, ent in by_id.items():
        p = _props(ent)
        c = (_vals(p, "country") or _vals(p, "jurisdiction") or _vals(p, "mainCountry") or [None])[0]
        if isinstance(c, str) and c.strip():
            g.entities[eid] = {"country": c.strip().upper()}
    return g


# BODS interest types read as ownership (with the interest's share, or of unknown size). Every other type
# (appointment-of-board, other-influence-or-control, senior-managing-official, trustee-of-trust, ...) is control.
_BODS_OWNERSHIP = {"shareholding", "voting-rights", "rights-to-surplus-assets-on-dissolution",
                   "rights-to-profit-or-income", "rights-to-share-capital", "beneficial-ownership-or-control",
                   "unknown-interest", "unpublished-interest", ""}


def _bods_share(share: Any) -> Pct:
    if not isinstance(share, Mapping):
        return None
    ex = share.get("exact")
    if isinstance(ex, (int, float)) and not isinstance(ex, bool):
        return float(ex)
    lo = share.get("minimum", share.get("exclusiveMinimum"))
    hi = share.get("maximum", share.get("exclusiveMaximum"))
    if lo is None and hi is None:
        return None
    try:
        lo_f = 0.0 if lo is None else float(lo)
        hi_f = 100.0 if hi is None else float(hi)
    except (TypeError, ValueError):
        return None
    # An exclusive maximum of 50 means "less than 50", which never meets a ">= 50" test; keep the interval but
    # step just below it so a 50 percent threshold reads it correctly.
    if "maximum" not in share and share.get("exclusiveMaximum") is not None:
        hi_f = max(lo_f, hi_f - _BELOW)
    return [lo_f, hi_f]


def _details(s: Mapping[str, Any]) -> Mapping[str, Any]:
    """BODS 0.4 keeps a statement's fields in ``recordDetails``; 0.2 and 0.3 keep them at the top level."""
    d = s.get("recordDetails")
    return d if isinstance(d, Mapping) else s


def from_bods(statements: Iterable[Any], *, listed: Iterable[Any] = (),
              countries: bool = True) -> Graph:
    """Read Beneficial Ownership Data Standard statements (0.2, 0.3 or 0.4) as edges.

    Each relationship statement (``ownershipOrControlStatement`` in 0.2/0.3, ``recordType: relationship`` in 0.4)
    becomes one edge per interest from the interested party to the subject. Ids are the ``recordId`` (0.4) or
    the ``statementId`` of the entity or person statement (0.2/0.3). Shareholding, voting rights and the other
    ownership interests carry their ``share`` (``exact``, or ``minimum``/``maximum`` as an interval); appointment,
    influence and other control interests become control edges with no share. ``startDate``/``endDate`` of the
    interest become ``valid_from``/``valid_to``.

    Shares, votes and other measures of one holding are separate interests in BODS; ``evaluate`` merges edges of
    the same pair into the hull of their shares (``shareholding`` 30 and ``voting-rights`` 60 read as 30 to 60),
    never their sum. A relationship with no interests is ownership of unknown size.

    BODS does not say who is sanctioned. Pass ``listed`` (the same records ``evaluate`` takes), keyed by those ids.
    With ``countries=True`` each entity's ``incorporatedInJurisdiction.code`` (or ``jurisdiction.code``) fills
    ``entities``.
    """
    g = Graph(listed=list(listed))
    stmt_to_record: Dict[str, str] = {}
    rels: List[Mapping[str, Any]] = []
    ents: List[Mapping[str, Any]] = []
    for s in statements:
        if not isinstance(s, Mapping):
            continue
        st = s.get("statementType")
        rt = s.get("recordType")
        if st == "ownershipOrControlStatement" or rt == "relationship":
            rels.append(s)
        elif st in ("entityStatement", "personStatement") or rt in ("entity", "person"):
            ents.append(s)
            sid, rid = s.get("statementId"), s.get("recordId")
            if isinstance(sid, str) and isinstance(rid, str):
                stmt_to_record[sid] = rid

    def party_id(x: Any) -> Optional[str]:
        if isinstance(x, str):
            return stmt_to_record.get(x, x) or None
        if isinstance(x, Mapping):
            for k in ("describedByEntityStatement", "describedByPersonStatement"):
                if isinstance(x.get(k), str):
                    return stmt_to_record.get(x[k], x[k])
        return None

    if countries:
        for s in ents:
            d = _details(s)
            eid = s.get("recordId") if isinstance(s.get("recordId"), str) else s.get("statementId")
            j = d.get("incorporatedInJurisdiction") or d.get("jurisdiction")
            code = j.get("code") if isinstance(j, Mapping) else None
            if isinstance(eid, str) and isinstance(code, str) and code.strip():
                g.entities[eid] = {"country": code.strip().split("-")[0].upper()}

    seen: Set[Tuple[str, str, Any, bool, Optional[str], Optional[str]]] = set()
    for s in rels:
        d = _details(s)
        sid = s.get("statementId", "?")
        subj, owner = party_id(d.get("subject")), party_id(d.get("interestedParty"))
        if not subj or not owner:
            g.notes.append(f"{sid}: no subject or interested party id (unspecified or anonymous), skipped")
            continue
        if subj == owner:
            continue
        interests = d.get("interests") if isinstance(d.get("interests"), list) else []
        if not interests:
            interests = [{}]  # a relationship with no interest data: ownership of unknown size
        for it in interests:
            if not isinstance(it, Mapping):
                continue
            kind = str(it.get("type", ""))
            ownership = kind in _BODS_OWNERSHIP
            pct = _bods_share(it.get("share")) if ownership else None
            control = not ownership
            vf = _date(it.get("startDate"))
            vt = _date(it.get("endDate"))
            if vf and vt and vt <= vf:
                vf = vt = None
            key = (owner, subj, tuple(pct) if isinstance(pct, list) else pct, control, vf, vt)
            if key in seen:  # BODS lists shares and votes of one holding as two interests; count it once
                continue
            seen.add(key)
            edge: Dict[str, Any] = {"owner": owner, "owned": subj, "pct": pct, "source": f"bods:{sid}"}
            if control:
                edge["control"] = True
            if kind:
                edge["basis"] = kind
            if vf:
                edge["valid_from"] = vf
            if vt:
                edge["valid_to"] = vt
            g.edges.append(edge)
    if not g.edges and rels:
        raise InputError("bods: relationship statements were found but none named both parties")
    return g
