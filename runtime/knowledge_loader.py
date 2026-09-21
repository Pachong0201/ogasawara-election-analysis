"""Minimum sufficient local knowledge loader and retrieval trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .election_loader import load_jsonl, safe_component, write_jsonl
from .freshness import historical_relationship_is_current, is_fresh, needs_revalidation
from .models import parse_date, utc_now_iso
from .source_registry import OfflineBackend, OfflineRetrievalError, RetrievalBackend

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


class KnowledgeLoader:
    def __init__(self, repo_root: Optional[Path] = None, retrieval_backend: Optional[RetrievalBackend] = None, mode: str = "auto"):
        self.repo_root = Path(repo_root) if repo_root else DEFAULT_REPO_ROOT
        self.retrieval_backend = retrieval_backend or OfflineBackend()
        self.mode = mode

    def _paths(self, county: str) -> Dict[str, Path]:
        safe = safe_component(county)
        return {
            "historical_claims": self.repo_root / "knowledge" / "historical" / safe / "claims.jsonl",
            "relationships": self.repo_root / "knowledge" / "local" / safe / "relationships.jsonl",
            "candidates": self.repo_root / "knowledge" / "local" / safe / "candidates.jsonl",
            "issues": self.repo_root / "knowledge" / "local" / safe / "issues.jsonl",
            "retrieval_cache": self.repo_root / "cache" / "retrieval" / f"{safe}.jsonl",
        }

    def _load_file(self, path: Path) -> List[Dict[str, Any]]:
        return load_jsonl(path) if path.exists() else []

    def load_local_knowledge(
        self,
        county: str,
        regions: Optional[Iterable[str]] = None,
        research_questions: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        region_set = {str(region) for region in (regions or []) if str(region).strip()}
        paths = self._paths(county)
        historical = self._load_file(paths["historical_claims"])
        relationships = self._load_file(paths["relationships"])
        candidates = self._load_file(paths["candidates"])
        issues = self._load_file(paths["issues"])

        def in_scope(record: Dict[str, Any]) -> bool:
            if not region_set:
                return True
            return str(record.get("region") or record.get("jurisdiction") or "") in region_set

        historical = [record for record in historical if in_scope(record)]
        relationships = [record for record in relationships if in_scope(record)]
        candidates = [record for record in candidates if in_scope(record)]
        issues = [record for record in issues if in_scope(record)]

        warnings: List[str] = []
        for record in relationships:
            if record.get("current_status") == "active_verified" and not historical_relationship_is_current(record):
                record["current_status"] = "historical_only"
                record["verification_note"] = "active_verified lacked recent last_verified_at; downgraded to historical_only"
        for record in relationships:
            if not is_fresh(record, kind="local_relationship"):
                warnings.append(f"local relationship may need revalidation: {record.get('relationship_id')}")

        sufficient = bool(historical or relationships or candidates or issues)
        result = {
            "county": county,
            "regions": sorted(region_set),
            "research_questions": list(research_questions or []),
            "historical_claims": historical,
            "relationships": relationships,
            "candidates": candidates,
            "issues": issues,
            "sufficient": sufficient,
            "warnings": warnings,
            "missing": [],
        }
        if not sufficient:
            result["missing"].append("no local knowledge records found")
        return result

    def build_research_questions(self, anomalies: Iterable[Dict[str, Any]]) -> List[str]:
        questions: List[str] = []
        for anomaly in anomalies or []:
            metric = anomaly.get("metric") or anomaly.get("metric_name") or "anomaly"
            region = anomaly.get("region") or ""
            questions.append(f"为什么 {region} 出现 {metric} 异常？")
        return questions or ["当地是否存在可解释票型异常的地方政治机制？"]

    def search_and_cache(self, county: str, research_questions: Iterable[str]) -> Dict[str, Any]:
        questions = [str(question) for question in (research_questions or []) if str(question).strip()]
        leads: List[Dict[str, Any]] = []
        warnings: List[str] = []
        if self.mode == "offline":
            return {"leads": [], "warnings": ["OFFLINE mode: local knowledge retrieval was not performed"], "questions": questions}

        for question in questions:
            try:
                results = self.retrieval_backend.search(question)
            except OfflineRetrievalError as exc:
                warnings.append(str(exc))
                continue
            for result in results or []:
                if not isinstance(result, dict):
                    result = {"summary": str(result)}
                lead = {
                    "lead_id": result.get("lead_id") or result.get("url") or f"lead-{len(leads) + 1}",
                    "county": county,
                    "query": question,
                    "summary": result.get("summary") or result.get("title") or "",
                    "url": result.get("url", ""),
                    "retrieved_at": utc_now_iso(),
                    "source_id": result.get("source_id", "retrieval_backend"),
                    "source_grade": result.get("source_grade", "E"),
                    "verification_status": result.get("verification_status", "lead_only"),
                    "evidence": result.get("evidence", ""),
                }
                leads.append(lead)

        if leads:
            cache_path = self._paths(county)["retrieval_cache"]
            existing = self._load_file(cache_path)
            write_jsonl(cache_path, existing + leads)
        return {"leads": leads, "warnings": warnings, "questions": questions}

    @staticmethod
    def can_promote_to_long_term(record: Dict[str, Any]) -> bool:
        grade = str(record.get("source_grade") or "").upper()
        if grade in {"A", "B"}:
            return True
        independent = int(record.get("independent_source_count") or 0)
        if grade == "C" and independent >= 2:
            return True
        return False

    def load(
        self,
        county: str,
        regions: Optional[Iterable[str]] = None,
        research_questions: Optional[Iterable[str]] = None,
        allow_online: bool = True,
    ) -> Dict[str, Any]:
        local = self.load_local_knowledge(county, regions=regions, research_questions=research_questions)
        if local["sufficient"] or not allow_online or self.mode == "offline":
            return local
        questions = list(research_questions or local["research_questions"]) or self.build_research_questions([])
        retrieval = self.search_and_cache(county, questions)
        local["retrieval"] = retrieval
        if retrieval.get("leads"):
            local["sufficient"] = False  # leads are not long-term knowledge until verified
            local["warnings"].append("retrieved leads are lead_only and were not promoted to long-term knowledge")
        return local
