"""Minimum sufficient local knowledge loader and retrieval trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .election_loader import load_jsonl, safe_component, write_jsonl
from .freshness import historical_relationship_is_current, is_fresh
from .models import utc_now_iso
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

    @staticmethod
    def _source_usable(record: Dict[str, Any]) -> bool:
        grade = str(record.get("source_grade") or "").upper()
        if grade in {"A", "B"}:
            return True
        if grade == "C" and int(record.get("independent_source_count") or 0) >= 2:
            return True
        return False

    @staticmethod
    def _question_coverage(record: Dict[str, Any], research_questions: List[str]) -> bool:
        if not research_questions:
            return True
        linked = record.get("research_questions") or record.get("research_question")
        if not linked:
            return False
        if isinstance(linked, str):
            linked = [linked]
        linked_text = " ".join(str(item) for item in linked)
        return any(question in linked_text or linked_text in question for question in research_questions)

    def load_local_knowledge(
        self,
        county: str,
        regions: Optional[Iterable[str]] = None,
        research_questions: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        region_set = {str(region) for region in (regions or []) if str(region).strip()}
        questions = [str(question) for question in (research_questions or []) if str(question).strip()]
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

        current_evidence: List[Dict[str, Any]] = []
        for record in relationships:
            if record.get("current_status") == "active_verified" and self._source_usable(record) and is_fresh(record, kind="local_relationship"):
                current_evidence.append(record)
            elif record.get("current_status") == "active_verified":
                warnings.append(f"local relationship not sufficient for current use: {record.get('relationship_id')}")

        for record in candidates:
            if self._source_usable(record) and is_fresh(record, kind="candidate_profile"):
                current_evidence.append(record)

        for record in issues:
            kind = str(record.get("record_type") or "political_claim")
            if self._source_usable(record) and is_fresh(record, kind=kind):
                current_evidence.append(record)

        question_covered = any(self._question_coverage(record, questions) for record in current_evidence) if questions else bool(current_evidence)
        sufficient = bool(current_evidence) and question_covered

        if historical and not current_evidence:
            warnings.append("historical knowledge exists but does not by itself satisfy current local explanation")
        if questions and current_evidence and not question_covered:
            warnings.append("current evidence exists but is not explicitly linked to the active research question")

        result = {
            "county": county,
            "regions": sorted(region_set),
            "research_questions": questions,
            "historical_claims": historical,
            "relationships": relationships,
            "candidates": candidates,
            "issues": issues,
            "current_evidence_count": len(current_evidence),
            "question_covered": question_covered,
            "sufficient": sufficient,
            "warnings": warnings,
            "missing": [],
        }
        if not sufficient:
            result["missing"].append("minimum sufficient current local knowledge not established")
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
                results = self.retrieval_backend.search(question, jurisdiction=county, purpose="local_knowledge")
            except OfflineRetrievalError as exc:
                warnings.append(str(exc))
                break
            except Exception as exc:
                warnings.append(f"local knowledge retrieval failed: {type(exc).__name__}")
                break
            for result in results or []:
                if not isinstance(result, dict):
                    result = {"summary": str(result)}
                lead = {
                    "lead_id": result.get("lead_id") or result.get("url") or f"lead-{len(leads) + 1}",
                    "county": county,
                    "query": question,
                    "research_questions": result.get("research_questions") or [question],
                    "title": result.get("title", ""),
                    "summary": result.get("summary") or result.get("title") or "",
                    "url": result.get("url", ""),
                    "published_at": result.get("published_at", ""),
                    "retrieved_at": result.get("retrieved_at") or utc_now_iso(),
                    "source_id": result.get("source_id", "retrieval_backend"),
                    "source_name": result.get("source_name", ""),
                    "source_grade": result.get("source_grade", "E"),
                    "verification_status": result.get("verification_status", "lead_only"),
                    "independence_key": result.get("independence_key", ""),
                    "independent_source_count": result.get("independent_source_count", 0),
                    "evidence": result.get("evidence", ""),
                }
                leads.append(lead)

        if leads:
            cache_path = self._paths(county)["retrieval_cache"]
            existing = self._load_file(cache_path)
            merged: Dict[str, Dict[str, Any]] = {}
            for item in existing + leads:
                key = "|".join(
                    [
                        str(item.get("lead_id") or item.get("url") or ""),
                        str(item.get("query") or ""),
                    ]
                )
                if not key.strip("|"):
                    key = f"anonymous-{len(merged) + 1}"
                merged[key] = item
            write_jsonl(cache_path, merged.values())
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
            local["sufficient"] = False
            local["warnings"].append("retrieved results remain retrieval leads and were not promoted to long-term knowledge")
        return local
