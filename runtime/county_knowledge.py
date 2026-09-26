"""Sustainable 22-county knowledge production orchestration.

This layer creates research plans and generated package indexes.  It never
creates political facts from prose and never bypasses KnowledgePromotionBuilder.
Automatic research findings remain retrieval leads until a human/host submits a
fully structured proposal through the existing promotion gates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .election_loader import load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .models import ElectionTask, utc_now_iso


PRODUCTION_VERSION = "1.4.0"

COUNTIES: tuple[str, ...] = (
    "基隆市", "台北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "台中市", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "台南市",
    "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "台東縣", "澎湖縣", "金門縣",
    "連江縣",
)

TOPICS: tuple[Dict[str, str], ...] = (
    {
        "topic": "historical_political_structure",
        "label": "历史政治结构",
        "question": "{county} 自 1990 年代以来的政党竞争、地方首长更替与政治结构经历了哪些可证实变化？",
        "target_type": "historical_claim",
    },
    {
        "topic": "people",
        "label": "关键人物",
        "question": "{county} 当前与近二十年有哪些可由公开资料确认的关键地方政治人物，其公职与经营地区为何？",
        "target_type": "candidate_profile",
    },
    {
        "topic": "organizations",
        "label": "政治与公共组织",
        "question": "{county} 有哪些与地方政治相关、且能由公开资料确认的人物—组织关系？",
        "target_type": "local_relationship",
    },
    {
        "topic": "political_networks",
        "label": "派系与政治网络",
        "question": "学术或可靠公开资料如何描述 {county} 的历史派系或政治网络，其有效时间范围及当前可验证状态为何？",
        "target_type": "local_relationship",
    },
    {
        "topic": "electoral_geography",
        "label": "选区与空间结构",
        "question": "{county} 的行政区、立委选区与历届地方选举空间差异中，哪些结构需要结合边界版本解释？",
        "target_type": "historical_claim",
    },
    {
        "topic": "civil_associations",
        "label": "地方社团",
        "question": "{county} 有哪些地方社团与公共事务网络可由可靠资料确认，且不得据其存在推断政治支持？",
        "target_type": "local_relationship",
    },
    {
        "topic": "farmers_fishermen_associations",
        "label": "农渔会",
        "question": "{county} 农会、渔会的组织分布与公开政治互动有哪些可核实记录，其时间范围为何？",
        "target_type": "local_relationship",
    },
    {
        "topic": "religious_organizations",
        "label": "宗教组织",
        "question": "{county} 宗教组织参与地方公共事务或公开政治活动的可验证记录有哪些，证据边界为何？",
        "target_type": "local_relationship",
    },
    {
        "topic": "key_issues",
        "label": "关键地方议题",
        "question": "{county} 当前与近十年哪些地方治理、建设、环境或产业议题具有持续公开证据？",
        "target_type": "current_issue",
    },
    {
        "topic": "demographics_industry",
        "label": "人口与产业背景",
        "question": "官方统计如何描述 {county} 的人口迁移、年龄结构、都市化与主要产业变化，且这些背景不得直接推断个人投票选择？",
        "target_type": "historical_claim",
    },
)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def county_research_questions(county: str) -> List[Dict[str, Any]]:
    """Return a stable, schema-shaped baseline research plan for one county."""
    if county not in COUNTIES:
        raise ValueError(f"unsupported county: {county}")
    output: List[Dict[str, Any]] = []
    for position, topic in enumerate(TOPICS, start=1):
        question_id = f"{safe_component(county)}-{position:02d}-{topic['topic']}"
        output.append(
            {
                "question_id": question_id,
                "county": county,
                "topic": topic["topic"],
                "label": topic["label"],
                "question": topic["question"].format(county=county),
                "target_type": topic["target_type"],
                "status": "unresolved",
                "priority": "baseline",
                "time_scope_required": True,
                "contradiction_search_required": True,
                "promotion_required": True,
                "notes": "检索结果只可进入 retrieval staging，必须另行提交 structured proposal。",
            }
        )
    return output


class CountyKnowledgeProduction:
    """Idempotent county plan/package production with resumable state."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    def _root(self, county: str) -> Path:
        return self.repo_root / "knowledge" / "counties" / safe_component(county)

    def _question_path(self, county: str) -> Path:
        return self._root(county) / "research_questions.jsonl"

    def _state_path(self, county: str) -> Path:
        return self._root(county) / "production_state.yaml"

    def _template_path(self, county: str) -> Path:
        return self._root(county) / "county_template.yaml"

    @staticmethod
    def _covered_questions(plan: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        linked = {
            str(question)
            for item in evidence
            for question in (item.get("research_questions") or [])
            if str(question).strip()
        }
        output: List[Dict[str, Any]] = []
        for item in plan:
            row = dict(item)
            question = str(row.get("question") or "")
            if question in linked:
                row["status"] = "promoted_evidence_available"
            output.append(row)
        return output

    def _plan(self, county: str, incremental: bool) -> List[Dict[str, Any]]:
        baseline = county_research_questions(county)
        path = self._question_path(county)
        if not incremental or not path.exists():
            return baseline
        existing = {str(row.get("question_id")): row for row in load_jsonl(path)}
        merged: List[Dict[str, Any]] = []
        for item in baseline:
            old = existing.get(item["question_id"], {})
            row = {**item, **{k: v for k, v in old.items() if k in {"status", "priority", "notes"}}}
            merged.append(row)
        for question_id, row in existing.items():
            if question_id not in {item["question_id"] for item in baseline}:
                merged.append(row)
        return merged

    def _template(self, county: str) -> Dict[str, Any]:
        return {
            "version": PRODUCTION_VERSION,
            "county": county,
            "schema": "schemas/county_knowledge_template.yaml",
            "authoritative_collections": {
                "historical_claims": f"knowledge/historical/{safe_component(county)}/claims.jsonl",
                "relationships": f"knowledge/local/{safe_component(county)}/relationships.jsonl",
                "candidates": f"knowledge/local/{safe_component(county)}/candidates.jsonl",
                "issues": f"knowledge/local/{safe_component(county)}/issues.jsonl",
            },
            "sections": [
                {
                    "topic": topic["topic"],
                    "label": topic["label"],
                    "required_fields": [
                        "time_scope", "last_verified_at_if_current", "source_evidence",
                        "uncertainty", "scope_boundary",
                    ],
                }
                for topic in TOPICS
            ],
            "rules": {
                "search_results_are_leads_only": True,
                "promotion_gate_required": True,
                "no_prediction_scoring_or_recommendation": True,
                "historical_relationships_are_not_current_by_default": True,
            },
        }

    def _stage_research_result(self, county: str, result: Dict[str, Any]) -> int:
        """Store grounded findings as unverified leads; never as knowledge facts."""
        leads: List[Dict[str, Any]] = []
        for position, finding in enumerate(result.get("findings") or [], start=1):
            citations = finding.get("citations") or []
            for citation in citations:
                url = str(citation.get("url") or "").strip()
                if not url:
                    continue
                question = str(finding.get("question") or "自动研究补查")
                leads.append(
                    {
                        "lead_id": "county-research-" + hashlib.sha256(
                            f"{county}|{url}|{position}".encode("utf-8")
                        ).hexdigest()[:20],
                        "county": county,
                        "query": question,
                        "research_questions": [question],
                        "title": str(finding.get("statement") or "自动研究线索"),
                        "summary": str(finding.get("statement") or ""),
                        "url": url,
                        "published_at": str(citation.get("published_at") or ""),
                        "retrieved_at": utc_now_iso(),
                        "source_id": str(citation.get("publisher_id") or "auto_research"),
                        "source_name": str(citation.get("publisher_id") or "auto_research"),
                        "source_grade": str(citation.get("source_grade") or "E"),
                        "verification_status": "body_grounded_unverified",
                        "independence_key": str(citation.get("publisher_id") or ""),
                        "evidence": str(citation.get("quote") or ""),
                    }
                )
        if not leads:
            return 0
        path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged = {
            f"{row.get('lead_id')}|{row.get('query')}": row
            for row in existing + leads
        }
        write_jsonl(path, merged.values())
        return len(leads)

    def _run_research(self, county: str, plan: List[Dict[str, Any]], year: int) -> Dict[str, Any]:
        from .auto_research import ResearchCoordinator, ResearchWorker

        worker = ResearchWorker(self.repo_root)
        coordinator = ResearchCoordinator(
            self.repo_root, worker.store, worker.config, worker_factory=lambda: worker
        )
        task = ElectionTask("county_mayor", year, county)
        result = coordinator.research(task, [row["question"] for row in plan], [])
        result = dict(result)
        result["staged_lead_count"] = self._stage_research_result(county, result)
        result["promotion_performed"] = False
        return result

    def build_county(
        self,
        county: str,
        *,
        incremental: bool = False,
        dry_run: bool = False,
        run_research: bool = False,
        resume: bool = True,
        year: int = 2026,
    ) -> Dict[str, Any]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        plan = self._plan(county, incremental=incremental)
        evidence_path = self._root(county) / "evidence_index.jsonl"
        evidence = load_jsonl(evidence_path) if evidence_path.exists() else []
        plan = self._covered_questions(plan, evidence)
        fingerprint = _fingerprint(
            {
                "template": self._template(county),
                "plan": plan,
                "run_research": run_research,
                "year": year,
            }
        )

        previous: Dict[str, Any] = {}
        state_path = self._state_path(county)
        if state_path.exists():
            previous = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
        required_outputs = [
            self._root(county) / "package_manifest.yaml",
            self._root(county) / "evidence_index.jsonl",
            self._root(county) / "unresolved_questions.jsonl",
            self._root(county) / "political_ecology.md",
            self._root(county) / "entity_relation_index.jsonl",
        ]
        if (
            resume
            and not dry_run
            and previous.get("status") == "completed"
            and previous.get("input_sha256") == fingerprint
            and all(path.exists() for path in required_outputs)
        ):
            return {"county": county, "status": "unchanged", "resumed": True, "state": previous}

        preview = {
            "county": county,
            "status": "dry_run" if dry_run else "running",
            "question_count": len(plan),
            "input_sha256": fingerprint,
            "run_research": run_research,
        }
        if dry_run:
            return preview

        root = self._root(county)
        root.mkdir(parents=True, exist_ok=True)
        self._template_path(county).write_text(
            yaml.safe_dump(self._template(county), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        write_jsonl(self._question_path(county), plan)
        state: Dict[str, Any] = {
            **preview,
            "status": "running",
            "started_at": utc_now_iso(),
            "attempt": int(previous.get("attempt") or 0) + 1,
            "last_error": "",
        }
        state_path.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            research = self._run_research(county, plan, year) if run_research else {"status": "not_requested"}
            package = self.builder.build_county_package(county)
            state.update(
                status="completed",
                completed_at=utc_now_iso(),
                research=research,
                manifest=package["manifest"],
            )
        except Exception as exc:
            state.update(status="failed", last_error=f"{type(exc).__name__}: {exc}")
            state_path.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
            raise
        state_path.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return {"county": county, "status": "completed", "state": state}

    def build_many(
        self,
        counties: Optional[Iterable[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        selected = list(counties or COUNTIES)
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for county in selected:
            try:
                results.append(self.build_county(county, **kwargs))
            except Exception as exc:
                failures.append({"county": county, "error": f"{type(exc).__name__}: {exc}"})
        return {
            "version": PRODUCTION_VERSION,
            "county_count": len(selected),
            "completed_count": len(results),
            "failed_count": len(failures),
            "results": results,
            "failures": failures,
        }

    def status(self, counties: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for county in list(counties or COUNTIES):
            state_path = self._state_path(county)
            state = yaml.safe_load(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            manifest_path = self._root(county) / "package_manifest.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            rows.append(
                {
                    "county": county,
                    "status": (state or {}).get("status", "not_started"),
                    "question_count": (state or {}).get("question_count", 0),
                    "record_count": sum(((manifest or {}).get("counts") or {}).values()),
                    "unresolved_question_count": (manifest or {}).get("unresolved_question_count", 0),
                    "last_error": (state or {}).get("last_error", ""),
                }
            )
        return {"version": PRODUCTION_VERSION, "counties": rows}
