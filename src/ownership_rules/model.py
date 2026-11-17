"""Input records: ownership edges and listed parties.

Every record is validated when it is built, because edges usually come from outside data (registries, vendor
files, a user's JSON). Bad input raises :class:`InputError` with the field that failed, never a bare
``KeyError`` or ``TypeError``.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "InputError",
    "Share",
    "Edge",
    "ListedParty",
    "parse_date",
    "edges_from",
    "listed_from",
    "entities_from",
]


class InputError(ValueError):
    """An edge, listed party, rule or vector failed validation."""


def parse_date(value: Any, what: str) -> Optional[_dt.date]:
    """Parse ``YYYY-MM-DD`` (or a full ISO timestamp, whose date part is used). ``None`` and ``""`` give ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if not isinstance(value, str):
        raise InputError(f"{what}: expected a YYYY-MM-DD string, got {type(value).__name__}")
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        raise InputError(f"{what}: {value!r} is not a YYYY-MM-DD date") from None


def _pct(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{what}: expected a number from 0 to 100, got {value!r}")
    v = float(value)
    if math.isnan(v) or v < 0 or v > 100:
        raise InputError(f"{what}: {value!r} is outside 0..100")
    return v


@dataclass(frozen=True)
class Share:
    """An ownership share in percent, exact (``lo == hi``) or known only as an interval ``[lo, hi]``."""

    lo: float
    hi: float

    def __post_init__(self) -> None:
        if self.lo > self.hi:
            raise InputError(f"share interval [{self.lo}, {self.hi}] has lo > hi")

    @classmethod
    def parse(cls, value: Any, what: str = "pct") -> Optional["Share"]:
        """Accept ``None`` (unknown), a number, ``[lo, hi]``, or ``{"min": lo, "max": hi}``."""
        if value is None:
            return None
        if isinstance(value, Mapping):
            unknown = set(value) - {"min", "max"}
            if unknown:
                raise InputError(f"{what}: unexpected keys {sorted(unknown)}")
            lo = _pct(value.get("min", 0), what + ".min")
            hi = _pct(value.get("max", 100), what + ".max")
            return cls(lo, hi)
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise InputError(f"{what}: an interval needs exactly [lo, hi]")
            return cls(_pct(value[0], what + "[0]"), _pct(value[1], what + "[1]"))
        v = _pct(value, what)
        return cls(v, v)

    @property
    def exact(self) -> bool:
        return self.lo == self.hi

    def to_json(self) -> Any:
        return _num(self.lo) if self.exact else [_num(self.lo), _num(self.hi)]


def _num(v: float) -> Any:
    return int(v) if float(v).is_integer() else round(v, 6)


def _str_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{what}: expected a non-empty string id, got {value!r}")
    return value


@dataclass(frozen=True)
class Edge:
    """One ownership or control relationship: ``owner`` holds ``share`` of ``owned``.

    ``share`` is ``None`` when the relationship is known but its percentage is not. ``control`` marks a
    relationship that gives control (board, consolidation, management) whether or not a share is known. An edge
    with no share and ``control=True`` is a control-only edge; an edge with no share and ``control=False`` is
    ownership of unknown size.
    """

    owner: str
    owned: str
    share: Optional[Share] = None
    control: bool = False
    source: Optional[str] = None
    as_of: Optional[_dt.date] = None
    valid_from: Optional[_dt.date] = None
    valid_to: Optional[_dt.date] = None
    extra: Tuple[Tuple[str, Any], ...] = field(default=(), compare=False)

    KNOWN = ("owner", "owned", "pct", "control", "source", "as_of", "valid_from", "valid_to")

    def __post_init__(self) -> None:
        if self.owner == self.owned:
            raise InputError(f"edge {self.owner!r} -> {self.owned!r}: an entity cannot own itself")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise InputError(f"edge {self.owner!r} -> {self.owned!r}: valid_from must be before valid_to")

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], where: str = "edge") -> "Edge":
        if not isinstance(d, Mapping):
            raise InputError(f"{where}: expected an object, got {type(d).__name__}")
        control = d.get("control", False)
        if not isinstance(control, bool):
            raise InputError(f"{where}.control: expected true or false")
        source = d.get("source")
        if source is not None and not isinstance(source, str):
            raise InputError(f"{where}.source: expected a string")
        extra = tuple(sorted((k, v) for k, v in d.items() if k not in cls.KNOWN))
        return cls(
            owner=_str_id(d.get("owner"), where + ".owner"),
            owned=_str_id(d.get("owned"), where + ".owned"),
            share=Share.parse(d.get("pct"), where + ".pct"),
            control=control,
            source=source,
            as_of=parse_date(d.get("as_of"), where + ".as_of"),
            valid_from=parse_date(d.get("valid_from"), where + ".valid_from"),
            valid_to=parse_date(d.get("valid_to"), where + ".valid_to"),
            extra=extra,
        )

    @property
    def control_only(self) -> bool:
        return self.share is None and self.control

    @property
    def unknown_share(self) -> bool:
        return self.share is None and not self.control

    def active(self, on: Optional[_dt.date]) -> bool:
        """True when the edge holds on ``on`` (``valid_from`` inclusive, ``valid_to`` exclusive)."""
        if on is None:
            return self.valid_to is None
        if self.valid_from and on < self.valid_from:
            return False
        if self.valid_to and on >= self.valid_to:
            return False
        return True

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"owner": self.owner, "owned": self.owned,
                               "pct": self.share.to_json() if self.share else None}
        if self.control:
            out["control"] = True
        if self.source:
            out["source"] = self.source
        for k in ("as_of", "valid_from", "valid_to"):
            v = getattr(self, k)
            if v:
                out[k] = v.isoformat()
        out.update(dict(self.extra))
        return out


def _str_list(value: Any, what: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise InputError(f"{what}: expected a list of strings")
    return tuple(value)


@dataclass(frozen=True)
class ListedParty:
    """A party named on a restricted list.

    ``lists`` holds list codes (``SDN``, ``EL``, ``MEU``, or any code a rule's seed selector names) and
    ``programs`` the program tags (OFAC tags such as ``RUSSIA-EO14024``). ``entry_type`` lets a rule skip entries
    that are not parties, such as Entity List address-only entries. ``restrictiveness`` is an optional rank (higher
    is stricter) used to report which owner's requirements apply under a most-restrictive rule.
    """

    id: str
    lists: Tuple[str, ...] = ()
    programs: Tuple[str, ...] = ()
    entry_type: str = "entity"
    country: Optional[str] = None
    restrictiveness: Optional[float] = None
    listed_from: Optional[_dt.date] = None
    listed_to: Optional[_dt.date] = None
    name: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], where: str = "listed") -> "ListedParty":
        if not isinstance(d, Mapping):
            raise InputError(f"{where}: expected an object, got {type(d).__name__}")
        entry_type = d.get("entry_type", "entity")
        if entry_type not in ("entity", "individual", "address", "vessel", "aircraft"):
            raise InputError(f"{where}.entry_type: {entry_type!r} is not entity, individual, address, vessel or aircraft")
        r = d.get("restrictiveness")
        if r is not None and (isinstance(r, bool) or not isinstance(r, (int, float))):
            raise InputError(f"{where}.restrictiveness: expected a number")
        country = d.get("country")
        if country is not None and not isinstance(country, str):
            raise InputError(f"{where}.country: expected a string")
        name = d.get("name")
        if name is not None and not isinstance(name, str):
            raise InputError(f"{where}.name: expected a string")
        return cls(
            id=_str_id(d.get("id"), where + ".id"),
            lists=_str_list(d.get("lists"), where + ".lists"),
            programs=_str_list(d.get("programs"), where + ".programs"),
            entry_type=entry_type,
            country=country,
            restrictiveness=None if r is None else float(r),
            listed_from=parse_date(d.get("listed_from"), where + ".listed_from"),
            listed_to=parse_date(d.get("listed_to"), where + ".listed_to"),
            name=name,
        )

    def active(self, on: Optional[_dt.date]) -> bool:
        if on is None:
            return self.listed_to is None
        if self.listed_from and on < self.listed_from:
            return False
        if self.listed_to and on >= self.listed_to:
            return False
        return True


def edges_from(items: Iterable[Any]) -> List[Edge]:
    """Build edges from dicts (or pass :class:`Edge` objects through)."""
    out = []
    for i, d in enumerate(items):
        out.append(d if isinstance(d, Edge) else Edge.from_dict(d, f"edges[{i}]"))
    return out


def listed_from(items: Iterable[Any]) -> List[ListedParty]:
    """Build listed parties from dicts (a bare string is a party with no list codes)."""
    out = []
    for i, d in enumerate(items):
        if isinstance(d, ListedParty):
            out.append(d)
        elif isinstance(d, str):
            out.append(ListedParty(id=_str_id(d, f"listed[{i}]")))
        else:
            out.append(ListedParty.from_dict(d, f"listed[{i}]"))
    return out


def entities_from(value: Any) -> Dict[str, Dict[str, Any]]:
    """Validate the optional ``entities`` map: ``{id: {"country": "CN", ...}}``."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InputError("entities: expected an object keyed by entity id")
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in value.items():
        _str_id(k, "entities key")
        if not isinstance(v, Mapping):
            raise InputError(f"entities[{k!r}]: expected an object")
        out[k] = dict(v)
    return out

