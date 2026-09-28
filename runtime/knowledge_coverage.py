"""Audit actual 22-county knowledge coverage without treating catalogs as data.

The audit is intentionally descriptive.  It distinguishes committed/imported
row-level artifacts from source catalogs and research leads, so the runtime and
operators cannot mistake "we know where to get this" for "the local knowledge
has already been ingested and verified".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import yaml

from .county_knowledge import COUNTIES
from .election_loader import load_jsonl, safe_component


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


class KnowledgeCoverageAudit:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else DEFAULT_REPO_ROOT
        self.config_root = (
            self.repo_root / "config"
            if (self.repo_root / "config").exists()
            else DEFAULT_REPO_ROOT / "config"
        )
        self.context_config = self._yaml("county_context_sources.yaml")
        self.academic_config = self._yaml("county_academic_research.yaml")

    def _yaml(self, name: str) -> Dict[str, Any]:
        path = self.config_root / name
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    @staticmethod
    def _line_count(path: Path) -> int:
        if not path.exists():
            return 0
        try:
            return len(load_jsonl(path))
        except Exception:
            return 0

    def _election_inventory(self, county: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        root = self.repo_root / "data" / "elections"
        if not root.exists():
            return rows
        filename = f"{safe_component(county)}.jsonl"
        for path in sorted(root.glob(f"*/*/{filename}")):
            try:
                rel = path.relative_to(root)
                election_type, year = rel.parts[0], rel.parts[1]
            except Exception:
                continue
            count = self._line_count(path)
            if count:
                rows.append(
                    {
                        "election_type": election_type,
                        "year": int(year) if str(year).isdigit() else year,
                        "record_count": count,
                        "path": str(path.relative_to(self.repo_root)),
                    }
                )
        return rows

    def _academic_leads(self, county: str) -> List[Dict[str, Any]]:
        return [
            row
            for row in (self.academic_config.get("academic_records") or [])
            if str(row.get("county") or "") == county
        ]

    def _context_inventory(self, county: str) -> Dict[str, Any]:
        expected = []
        for source in (self.context_config.get("sources") or {}).values():
            expected.append(
                {
                    "source_id": str(source.get("source_id") or ""),
                    "kind": str(source.get("kind") or ""),
                    "source_name": str(source.get("source_name") or ""),
                }
            )

        root = self.repo_root / "data" / "context" / safe_component(county)
        imported_ids: Set[str] = set()
        files: List[Dict[str, Any]] = []
        if root.exists():
            for path in sorted(root.glob("*.jsonl")):
                rows = load_jsonl(path)
                ids = {
                    str(row.get("source_id") or "").strip()
                    for row in rows
                    if str(row.get("source_id") or "").strip()
                }
                imported_ids.update(ids)
                files.append(
                    {
                        "kind": path.stem,
                        "path": str(path.relative_to(self.repo_root)),
                        "record_count": len(rows),
                        "source_ids": sorted(ids),
                    }
                )

        sources = []
        for source in expected:
            source_id = source["source_id"]
            sources.append(
                {
                    **source,
                    "state": (
                        "row_data_available"
                        if source_id in imported_ids
                        else "source_catalog_only"
                    ),
                }
            )
        return {
            "files": files,
            "imported_source_ids": sorted(imported_ids),
            "sources": sources,
            "row_data_source_count": sum(
                1 for source in sources if source["state"] == "row_data_available"
            ),
            "catalog_only_source_count": sum(
                1 for source in sources if source["state"] == "source_catalog_only"
            ),
        }

    @staticmethod
    def _independence_keys(record: Dict[str, Any]) -> Set[str]:
        keys: Set[str] = set()
        for evidence in record.get("verification_evidence") or []:
            if not isinstance(evidence, dict):
                continue
            key = str(evidence.get("independence_key") or "").strip()
            if key:
                keys.add(key)
        return keys

    def county(self, county: str) -> Dict[str, Any]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        safe = safe_component(county)
        election_inventory = self._election_inventory(county)
        boundary = (
            self.repo_root
            / "data"
            / "geography"
            / "electoral_districts"
            / "cec_legislator_term11"
            / f"{safe}.jsonl"
        )
        matrix = self.repo_root / "data" / "matrices" / "cec" / f"{safe}.json"
        relationships_path = (
            self.repo_root / "knowledge" / "local" / safe / "relationships.jsonl"
        )
        issues_path = self.repo_root / "knowledge" / "local" / safe / "issues.jsonl"
        candidates_path = (
            self.repo_root / "knowledge" / "local" / safe / "candidates.jsonl"
        )
        unresolved_path = (
            self.repo_root
            / "knowledge"
            / "counties"
            / safe
            / "unresolved_questions.jsonl"
        )
        relationships = (
            load_jsonl(relationships_path) if relationships_path.exists() else []
        )
        issues = load_jsonl(issues_path) if issues_path.exists() else []
        candidates = load_jsonl(candidates_path) if candidates_path.exists() else []
        mayor_candidates = [
            row for row in candidates
            if row.get("election_type") == "county_mayor"
            and int(row.get("election_year") or 0) == 2026
            and row.get("candidate_status") in {"registered", "qualified", "nominated"}
        ]
        councilor_candidates = [
            row for row in candidates
            if row.get("election_type") == "councilor"
            and int(row.get("election_year") or 0) == 2026
            and row.get("candidate_status") in {"registered", "qualified", "nominated"}
        ]
        active = [
            row
            for row in relationships
            if row.get("current_status") == "active_verified"
        ]
        dual_source = [
            row for row in active if len(self._independence_keys(row)) >= 2
        ]
        relationship_types: Dict[str, int] = {}
        for row in active:
            relation_type = str(row.get("relationship_type") or "other")
            relationship_types[relation_type] = relationship_types.get(relation_type, 0) + 1
        academic = self._academic_leads(county)
        context = self._context_inventory(county)

        gaps: List[str] = []
        if not election_inventory:
            gaps.append("cec_election_rows_not_imported")
        if not boundary.exists():
            gaps.append("term11_boundary_rows_not_imported")
        if not matrix.exists():
            gaps.append("cec_spatial_matrix_not_built")
        for source in context["sources"]:
            if source["state"] == "source_catalog_only":
                gaps.append(f"context_catalog_only:{source['source_id']}")
        if not mayor_candidates:
            gaps.append("no_2026_mayor_candidate_profile")
        if not councilor_candidates:
            gaps.append("no_2026_councilor_candidate_profile")
        if not active:
            gaps.append("no_active_verified_local_relationship")
        if not issues:
            gaps.append("no_current_issue_record")

        expected_academic = set(
            self.academic_config.get("coverage_policy", {}).get(
                "expected_counties", []
            )
            or []
        )
        academic_state = (
            "research_leads_only"
            if academic
            else (
                "missing_research_lead"
                if county in expected_academic
                else "not_targeted_by_19_county_supplement"
            )
        )
        if academic_state == "missing_research_lead":
            gaps.append("academic_research_lead_missing")

        return {
            "county": county,
            "stable_local_baseline": {
                "cec_elections": {
                    "state": (
                        "row_data_available"
                        if election_inventory
                        else "no_local_artifact"
                    ),
                    "files": election_inventory,
                },
                "term11_legislative_boundaries": {
                    "state": (
                        "row_data_available" if boundary.exists() else "no_local_artifact"
                    ),
                    "path": str(boundary.relative_to(self.repo_root)),
                },
                "cec_spatial_matrix": {
                    "state": (
                        "row_data_available" if matrix.exists() else "no_local_artifact"
                    ),
                    "path": str(matrix.relative_to(self.repo_root)),
                },
                "academic_research": {
                    "state": academic_state,
                    "lead_count": len(academic),
                    "lead_ids": [row.get("lead_id") for row in academic],
                    "rule": (
                        "B-grade academic catalog entries remain research leads until "
                        "the relevant claims are extracted and promoted with time/scope boundaries"
                    ),
                },
                "official_social_context": context,
            },
            "dynamic_local_state": {
                "candidate_profile_count": len(candidates),
                "mayor_2026_candidate_count": len(mayor_candidates),
                "councilor_2026_candidate_count": len(councilor_candidates),
                "active_verified_relationship_count": len(active),
                "dual_source_relationship_count": len(dual_source),
                "relationship_types": dict(sorted(relationship_types.items())),
                "current_issue_count": len(issues),
                "candidate_path": str(candidates_path.relative_to(self.repo_root)),
                "relationship_path": str(relationships_path.relative_to(self.repo_root)),
                "issue_path": str(issues_path.relative_to(self.repo_root)),
            },
            "unresolved_question_count": self._line_count(unresolved_path),
            "gaps": gaps,
        }

    def build(self, counties: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        selected = list(counties or COUNTIES)
        rows = [self.county(county) for county in selected]
        return {
            "schema_version": "1.0.0",
            "county_count": len(rows),
            "counties": rows,
            "summary": {
                "with_cec_election_rows": sum(
                    1
                    for row in rows
                    if row["stable_local_baseline"]["cec_elections"]["state"]
                    == "row_data_available"
                ),
                "with_term11_boundaries": sum(
                    1
                    for row in rows
                    if row["stable_local_baseline"]["term11_legislative_boundaries"]["state"]
                    == "row_data_available"
                ),
                "with_spatial_matrix": sum(
                    1
                    for row in rows
                    if row["stable_local_baseline"]["cec_spatial_matrix"]["state"]
                    == "row_data_available"
                ),
                "with_2026_mayor_candidate_profiles": sum(
                    1
                    for row in rows
                    if row["dynamic_local_state"]["mayor_2026_candidate_count"] > 0
                ),
                "with_2026_councilor_candidate_profiles": sum(
                    1
                    for row in rows
                    if row["dynamic_local_state"]["councilor_2026_candidate_count"] > 0
                ),
                "total_candidate_profiles": sum(
                    row["dynamic_local_state"]["candidate_profile_count"]
                    for row in rows
                ),
                "total_2026_mayor_candidates": sum(
                    row["dynamic_local_state"]["mayor_2026_candidate_count"]
                    for row in rows
                ),
                "total_2026_councilor_candidates": sum(
                    row["dynamic_local_state"]["councilor_2026_candidate_count"]
                    for row in rows
                ),
                "with_active_verified_relationships": sum(
                    1
                    for row in rows
                    if row["dynamic_local_state"]["active_verified_relationship_count"]
                    > 0
                ),
                "with_current_issue_records": sum(
                    1
                    for row in rows
                    if row["dynamic_local_state"]["current_issue_count"] > 0
                ),
            },
            "interpretation_boundary": (
                "coverage states describe repository artifacts only; source_catalog_only "
                "and research_leads_only are not equivalent to verified row-level knowledge"
            ),
        }
