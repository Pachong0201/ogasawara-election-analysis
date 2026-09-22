"""Fixture helpers for V1.1 runtime behavioral tests."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from runtime.data_readiness import DataReadinessGate
from runtime.election_loader import election_file_path, write_jsonl
from runtime.models import DataQuery, SourceFetchResult
from runtime.source_registry import ElectionDataSource

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_RUNTIME_CONFIG = REPO_ROOT / "config" / "runtime.yaml"

AREA_A = "甲鄉"
AREA_B = "乙鄉"
DEFAULT_JURISDICTION = "新竹縣"


@contextmanager
def temp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def make_task(
    election_type: str = "county_mayor",
    target_year: int = 2026,
    jurisdiction: str = DEFAULT_JURISDICTION,
    candidates: Optional[List[str]] = None,
    analysis_level: str = "township_district",
):
    from runtime.models import ElectionTask

    return ElectionTask(
        election_type=election_type,
        target_year=target_year,
        jurisdiction=jurisdiction,
        analysis_level=analysis_level,
        candidates=candidates or [],
    )


def expected_years(election_type: str, target_year: int = 2026, minimum: int = 3) -> List[int]:
    gate = DataReadinessGate(REPO_ROOT, runtime_config_path=REAL_RUNTIME_CONFIG)
    return gate.expected_years(election_type, target_year, minimum)


def geography_payload(jurisdiction: str = DEFAULT_JURISDICTION, boundary_version: str = "v1") -> Dict[str, Any]:
    return {
        "version": "1.1.0",
        "regions": [
            {
                "region_id": "county-1",
                "name": jurisdiction,
                "level": "county_city",
                "parent": "TW",
                "valid_from": "2010-01-01",
                "valid_to": None,
                "boundary_version": boundary_version,
                "predecessor_regions": [],
                "successor_regions": [AREA_A, AREA_B],
            },
            {
                "region_id": "town-1",
                "name": AREA_A,
                "level": "township_district",
                "parent": jurisdiction,
                "valid_from": "2010-01-01",
                "valid_to": None,
                "boundary_version": boundary_version,
                "predecessor_regions": [],
                "successor_regions": [],
            },
            {
                "region_id": "town-2",
                "name": AREA_B,
                "level": "township_district",
                "parent": jurisdiction,
                "valid_from": "2010-01-01",
                "valid_to": None,
                "boundary_version": boundary_version,
                "predecessor_regions": [],
                "successor_regions": [],
            },
        ],
    }


def write_geography(root: Path, jurisdiction: str = DEFAULT_JURISDICTION, boundary_version: str = "v1") -> Path:
    path = root / "data" / "geography" / "administrative_areas" / "administrative_area.yaml"
    write_yaml(path, geography_payload(jurisdiction, boundary_version))
    return path


def election_records_for_year(
    election_type: str,
    year: int,
    jurisdiction: str = DEFAULT_JURISDICTION,
    boundary_version: str = "v1",
    party_a: str = "甲黨",
    party_b: str = "乙黨",
) -> List[Dict[str, Any]]:
    areas = [
        (AREA_A, {party_a: 600, party_b: 400}),
        (AREA_B, {party_a: 450, party_b: 550}),
    ]
    records: List[Dict[str, Any]] = []
    for area, votes in areas:
        valid_votes = sum(votes.values())
        for candidate, vote_count in votes.items():
            records.append(
                {
                    "record_id": f"{election_type}-{year}-{area}-{candidate}",
                    "election_type": election_type,
                    "election_year": year,
                    "election_date": f"{year}-01-01",
                    "jurisdiction": area,
                    "parent_jurisdiction": jurisdiction,
                    "level": "township_district",
                    "candidate_name": candidate,
                    "party": candidate,
                    "votes": vote_count,
                    "valid_votes": valid_votes,
                    "vote_share": round(vote_count / valid_votes, 6),
                    "turnout": 0.70,
                    "source": "fixture",
                    "source_grade": "A",
                    "source_id": "fixture",
                    "boundary_version": boundary_version,
                    "time_scope": str(year),
                    "retrieved_at": f"{year}-01-02T00:00:00+00:00",
                    "verified_at": f"{year}-01-03T00:00:00+00:00",
                    "source_version": "fixture-v1",
                    "raw_reference": "fixture",
                    "normalization_version": "v1.1.0",
                }
            )
    return records


def write_election_records(root: Path, election_type: str, year: int, jurisdiction: str, records: Iterable[Dict[str, Any]]) -> Path:
    path = election_file_path(root, election_type, year, jurisdiction)
    write_jsonl(path, list(records))
    return path


def write_current_candidates(root: Path, jurisdiction: str = DEFAULT_JURISDICTION) -> Path:
    path = root / "cache" / "candidates" / f"{jurisdiction}.jsonl"
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    records = [
        {
            "candidate_id": "c1",
            "candidate_name": "甲候選人",
            "name": "甲候選人",
            "party": "甲黨",
            "election_type": "county_mayor",
            "election_year": 2026,
            "candidate_status": "registered",
            "valid_until": "2026-11-28",
            "source": "fixture",
            "source_grade": "A",
            "retrieved_at": now,
            "last_verified_at": now,
            "birth_place": "新竹縣",
            "offices": [{"title": "議員", "organization": "新竹縣議會", "current": True}],
        },
        {
            "candidate_id": "c2",
            "candidate_name": "乙候選人",
            "name": "乙候選人",
            "party": "乙黨",
            "election_type": "county_mayor",
            "election_year": 2026,
            "candidate_status": "registered",
            "valid_until": "2026-11-28",
            "source": "fixture",
            "source_grade": "A",
            "retrieved_at": now,
            "last_verified_at": now,
            "birth_place": "新竹縣",
            "offices": [{"title": "鄉長", "organization": "甲鄉公所", "current": True}],
        },
    ]
    write_jsonl(path, records)
    return path


def populate_full_repo(
    root: Path,
    jurisdiction: str = DEFAULT_JURISDICTION,
    target_year: int = 2026,
    include_legislator: bool = True,
    include_president: bool = True,
    include_current_candidates: bool = True,
    boundary_version: str = "v1",
) -> Dict[str, Any]:
    write_geography(root, jurisdiction=jurisdiction, boundary_version=boundary_version)
    written: Dict[str, List[int]] = {"county_mayor": [], "president": [], "regional_legislator": []}
    for year in expected_years("county_mayor", target_year):
        write_election_records(root, "county_mayor", year, jurisdiction, election_records_for_year("county_mayor", year, jurisdiction, boundary_version))
        written["county_mayor"].append(year)
    if include_president:
        for year in expected_years("president", target_year):
            write_election_records(root, "president", year, jurisdiction, election_records_for_year("president", year, jurisdiction, boundary_version))
            written["president"].append(year)
    if include_legislator:
        for year in expected_years("regional_legislator", target_year):
            write_election_records(root, "regional_legislator", year, jurisdiction, election_records_for_year("regional_legislator", year, jurisdiction, boundary_version))
            written["regional_legislator"].append(year)
    if include_current_candidates:
        write_current_candidates(root, jurisdiction=jurisdiction)
    return {"written": written, "jurisdiction": jurisdiction}


class FixtureElectionDataSource(ElectionDataSource):
    """Deterministic data source adapter for loader tests."""

    def __init__(self, records_by_key: Dict[Tuple[str, int, str, str], List[Dict[str, Any]]], source_id: str = "fixture_source", source_grade: str = "A", priority: int = 1):
        self.records_by_key = records_by_key
        self.source_id = source_id
        self.source_grade = source_grade
        self.priority = priority
        self.calls: List[DataQuery] = []

    @staticmethod
    def key(election_type: str, year: int, jurisdiction: str, level: str) -> Tuple[str, int, str, str]:
        return (election_type, int(year), jurisdiction, level)

    def supports(self, query: DataQuery) -> bool:
        return self.key(query.election_type, query.year, query.jurisdiction, query.level) in self.records_by_key

    def fetch(self, query: DataQuery) -> SourceFetchResult:
        self.calls.append(query)
        key = self.key(query.election_type, query.year, query.jurisdiction, query.level)
        return SourceFetchResult(
            source_id=self.source_id,
            source_grade=self.source_grade,
            records=list(self.records_by_key.get(key, [])),
            source_version="fixture-v1",
            raw_reference=f"fixture:{key}",
        )
