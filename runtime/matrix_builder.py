"""Build election matrices for cross-election and cross-level analysis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _region(record: Dict[str, Any]) -> str:
    return str(record.get("jurisdiction") or record.get("region") or "")


def _candidate_key(record: Dict[str, Any]) -> str:
    return str(record.get("candidate_name") or record.get("party") or "unknown")


def build_historical_matrix(records: Iterable[Dict[str, Any]], value_field: str = "vote_share") -> Dict[str, Any]:
    """Return ``{region: {year: {candidate: value}}}``.

    The nested structure keeps the requested region × year × candidate
    dimensions explicit and machine-readable.
    """
    matrix: Dict[str, Dict[str, Dict[str, Optional[float]]]] = defaultdict(lambda: defaultdict(dict))
    for record in records:
        region = _region(record)
        year = str(record.get("election_year") or record.get("year") or "")
        candidate = _candidate_key(record)
        matrix[region][year][candidate] = _float(record.get(value_field))
    return {
        "value_field": value_field,
        "regions": {
            region: {year: dict(candidates) for year, candidates in years.items()}
            for region, years in matrix.items()
        },
    }


def build_cross_level_matrix(records_by_type: Dict[str, Iterable[Dict[str, Any]]], level: str = "township_district") -> Dict[str, Any]:
    """Return ``{region: {election_type: {year: {candidate: value}}}}``."""
    matrix: Dict[str, Dict[str, Dict[str, Dict[str, Optional[float]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for election_type, records in records_by_type.items():
        for record in records:
            if level and record.get("level") and record.get("level") != level:
                continue
            region = _region(record)
            year = str(record.get("election_year") or "")
            candidate = _candidate_key(record)
            matrix[region][election_type][year][candidate] = _float(record.get("vote_share"))
    return {
        "value_field": "vote_share",
        "level": level,
        "regions": {
            region: {etype: {year: dict(candidates) for year, candidates in years.items()} for etype, years in etypes.items()}
            for region, etypes in matrix.items()
        },
    }


def _group_by_region_party(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    grouped: Dict[str, Dict[str, Dict[str, Optional[float]]]] = defaultdict(lambda: defaultdict(dict))
    for record in records:
        region = _region(record)
        party = str(record.get("party") or "unknown")
        grouped[region][party]["vote_share"] = _float(record.get("vote_share"))
        grouped[region][party]["votes"] = _float(record.get("votes"))
    return {region: {party: dict(values) for party, values in parties.items()} for region, parties in grouped.items()}


def build_same_day_matrix(
    legislator_records: Iterable[Dict[str, Any]],
    president_records: Iterable[Dict[str, Any]],
    party_list_records: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build region × party rows for same-day legislator/president/party-list data."""
    party_list_records = party_list_records or []
    leg = _group_by_region_party(legislator_records)
    pres = _group_by_region_party(president_records)
    party = _group_by_region_party(party_list_records)
    regions = sorted(set(leg) | set(pres) | set(party))
    rows: List[Dict[str, Any]] = []
    for region in regions:
        parties = sorted(set(leg.get(region, {})) | set(pres.get(region, {})) | set(party.get(region, {})))
        for party_name in parties:
            leg_share = leg.get(region, {}).get(party_name, {}).get("vote_share")
            pres_share = pres.get(region, {}).get(party_name, {}).get("vote_share")
            party_share = party.get(region, {}).get(party_name, {}).get("vote_share")
            rows.append(
                {
                    "region": region,
                    "party": party_name,
                    "legislator_vote_share": leg_share,
                    "president_vote_share": pres_share,
                    "party_list_vote_share": party_share,
                    "split_ticket_residual": (
                        None if leg_share is None or pres_share is None else round(leg_share - pres_share, 6)
                    ),
                }
            )
    return {"rows": rows, "same_day": True}
