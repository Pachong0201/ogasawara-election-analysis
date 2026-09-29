"""Valid-vote-weighted county aggregates shared by metrics and findings."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

INDEPENDENT_PARTIES = frozenset({"無黨籍及未經政黨推薦", "無黨籍", "无党籍", "無", "无", "未經政黨推薦"})
INDEPENDENT_SERIES_PREFIX = "無黨籍:"
MAJOR_PARTIES = ("民主進步黨", "中國國民黨")


def is_independent(party: Any) -> bool:
    return str(party or "").strip() in INDEPENDENT_PARTIES


def series_key(record: Dict[str, Any], independents: str = "candidate") -> Optional[str]:
    """Return the cross-election comparison key for a record.

    Party candidates are compared by party. Independents are never pooled into
    a single pseudo-party: ``independents="candidate"`` keys them by candidate
    name (only the same person is compared across elections) and
    ``independents="exclude"`` drops them.
    """
    party = str(record.get("party") or "").strip()
    if not party:
        return None
    if party in INDEPENDENT_PARTIES:
        if independents == "exclude":
            return None
        name = str(record.get("candidate_name") or "").strip()
        return f"{INDEPENDENT_SERIES_PREFIX}{name}" if name else None
    return party


def display_key(key: str) -> str:
    if key.startswith(INDEPENDENT_SERIES_PREFIX):
        return f"{key[len(INDEPENDENT_SERIES_PREFIX):]}（無黨籍）"
    return key


class ElectionAggregate:
    """Region and county-level shares for one election type across years."""

    def __init__(self, records: Iterable[Dict[str, Any]], independents: str = "candidate"):
        self.region_valid: Dict[Tuple[int, str], float] = {}
        self.region_turnout: Dict[Tuple[int, str], float] = {}
        self.region_votes: Dict[Tuple[int, str, str], float] = defaultdict(float)
        self.region_share: Dict[Tuple[int, str, str], float] = defaultdict(float)
        self.candidates: Dict[Tuple[int, str], Set[str]] = defaultdict(set)
        self.candidate_votes: Dict[Tuple[int, str], float] = defaultdict(float)
        self.candidate_key: Dict[Tuple[int, str], str] = {}
        self.source_grades: Dict[int, Set[str]] = defaultdict(set)
        self.source_ids: Dict[int, Set[str]] = defaultdict(set)
        for record in records:
            region = str(record.get("jurisdiction") or "").strip()
            year_raw = record.get("election_year")
            share = record.get("vote_share")
            if not region or year_raw is None or share is None:
                continue
            year = int(year_raw)
            valid = record.get("valid_votes")
            if valid is not None and float(valid) > 0:
                self.region_valid[(year, region)] = float(valid)
            turnout = record.get("turnout")
            if turnout is not None and float(turnout) > 0:
                self.region_turnout[(year, region)] = float(turnout)
            if record.get("source_grade"):
                self.source_grades[year].add(str(record["source_grade"]))
            if record.get("source_id"):
                self.source_ids[year].add(str(record["source_id"]))
            key = series_key(record, independents=independents)
            if key is None:
                continue
            votes = record.get("votes")
            if votes is None and valid is not None:
                votes = float(share) * float(valid)
            self.region_share[(year, region, key)] += float(share)
            self.region_votes[(year, region, key)] += float(votes or 0.0)
            name = str(record.get("candidate_name") or "").strip()
            if name:
                self.candidates[(year, key)].add(name)
                self.candidate_votes[(year, name)] += float(votes or 0.0)
                self.candidate_key[(year, name)] = key

    def years(self) -> List[int]:
        return sorted({year for year, _ in self.region_valid} | {year for year, _, _ in self.region_share})

    def regions(self, year: int) -> List[str]:
        return sorted({region for y, region, _ in self.region_share if y == year})

    def keys(self, year: int) -> List[str]:
        return sorted({key for y, _, key in self.region_share if y == year})

    def share(self, year: int, region: str, key: str) -> Optional[float]:
        value = self.region_share.get((year, region, key))
        return None if value is None else float(value)

    def valid(self, year: int, region: str) -> Optional[float]:
        return self.region_valid.get((year, region))

    def county_valid(self, year: int, regions: Optional[Iterable[str]] = None) -> float:
        wanted = set(regions) if regions is not None else None
        return sum(v for (y, r), v in self.region_valid.items() if y == year and (wanted is None or r in wanted))

    def county_share(self, year: int, key: str, regions: Optional[Iterable[str]] = None) -> Optional[float]:
        """Valid-vote-weighted share; falls back to the unweighted mean without valid votes."""
        wanted = set(regions) if regions is not None else set(self.regions(year))
        weighted = 0.0
        weight_total = 0.0
        plain: List[float] = []
        for region in wanted:
            share = self.region_share.get((year, region, key))
            if share is None:
                share = 0.0 if (year, region) in self.region_valid else None
            if share is None:
                continue
            plain.append(share)
            valid = self.region_valid.get((year, region))
            if valid:
                weighted += share * valid
                weight_total += valid
        if weight_total > 0:
            return weighted / weight_total
        return sum(plain) / len(plain) if plain else None

    def county_turnout(self, year: int, regions: Optional[Iterable[str]] = None) -> Optional[float]:
        """Approximate county turnout, weighting regions by electorate ≈ valid / turnout."""
        wanted = set(regions) if regions is not None else None
        voted = 0.0
        electorate = 0.0
        for (y, region), turnout in self.region_turnout.items():
            if y != year or (wanted is not None and region not in wanted):
                continue
            valid = self.region_valid.get((y, region))
            if not valid:
                continue
            voted += valid
            electorate += valid / turnout
        return voted / electorate if electorate > 0 else None

    def ranked_keys(self, year: int) -> List[Tuple[str, float]]:
        ranked = [(key, self.county_share(year, key) or 0.0) for key in self.keys(year)]
        return sorted(ranked, key=lambda item: item[1], reverse=True)

    def ranked_candidates(self, year: int) -> List[Dict[str, Any]]:
        total = self.county_valid(year)
        rows = []
        for (y, name), votes in self.candidate_votes.items():
            if y != year:
                continue
            key = self.candidate_key[(y, name)]
            share = votes / total if total > 0 else None
            rows.append({"candidate": name, "series_key": key, "party": party_of(key), "votes": round(votes), "vote_share": share})
        return sorted(rows, key=lambda item: item["votes"], reverse=True)

    def weight(self, year: int, region: str) -> Optional[float]:
        valid = self.region_valid.get((year, region))
        total = self.county_valid(year)
        return valid / total if valid and total > 0 else None


def party_of(key: str) -> str:
    return "無黨籍及未經政黨推薦" if key.startswith(INDEPENDENT_SERIES_PREFIX) else key
