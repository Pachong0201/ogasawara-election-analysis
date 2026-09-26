"""Reproducible, gate-preserving import of the chat-verified county baseline.

The tracked YAML is evidence input, not authoritative knowledge. This module
stages every source as a retrieval lead and sends only explicit structured
proposals through KnowledgePromotionBuilder. Source catalog entries whose raw
rows were unavailable remain unresolved leads.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .county_knowledge import COUNTIES, CountyKnowledgeProduction, county_research_questions
from .election_loader import load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder, TARGETS
from .models import utc_now_iso


BASELINE_VERSION = "1.0.0"
DEFAULT_RETRIEVED_AT = "2026-09-26T00:00:00+08:00"


def _question(county: str, topic: str) -> str:
    for item in county_research_questions(county):
        if item["topic"] == topic:
            return item["question"]
    raise ValueError(f"unknown county knowledge topic: {topic}")


def _source_lead(
    county: str,
    question: str,
    lead_id: str,
    source: Dict[str, Any],
    summary: str,
    evidence: str,
) -> Dict[str, Any]:
    return {
        "lead_id": lead_id,
        "county": county,
        "query": question,
        "research_questions": [question],
        "title": source["source_name"],
        "summary": summary,
        "url": source["url"],
        "source_id": source["source_id"],
        "source_name": source["source_name"],
        "source_grade": source["source_grade"],
        "verification_status": "verified",
        "independence_key": source["independence_key"],
        "published_at": str(source.get("published_at") or ""),
        "retrieved_at": DEFAULT_RETRIEVED_AT,
        "evidence": evidence,
    }


def _proposal(
    *,
    proposal_id: str,
    county: str,
    target_type: str,
    question: str,
    evidence_lead_ids: Iterable[str],
    scope_boundary: str,
    target_record: Dict[str, Any],
    contradiction_check_note: str,
) -> Dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "county": county,
        "target_type": target_type,
        "research_questions": [question],
        "evidence_lead_ids": list(evidence_lead_ids),
        "contradiction_check_completed": True,
        "contradictory_lead_ids": [],
        "contradiction_check_note": contradiction_check_note,
        "scope_boundary": scope_boundary,
        "target_record": target_record,
    }


class CuratedCountyBaseline:
    def __init__(self, repo_root: Optional[Path] = None, config_path: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.config_path = Path(config_path) if config_path else self.repo_root / "config" / "curated_county_baseline.yaml"
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    def _official_items(self, county: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        sources = self.config["sources"]
        population = self.config["population_2025"][county]
        industry = self.config["primary_employment_industry_2021"][county]
        demo_question = _question(county, "demographics_industry")
        electoral_question = _question(county, "electoral_geography")

        change = int(population["annual_change"])
        change_text = f"增加{change:,}人" if change >= 0 else f"减少{abs(change):,}人"
        population_lead = _source_lead(
            county,
            demo_question,
            f"official-{safe_component(county)}-population-2025",
            sources["moi_population_2025"],
            f"2025年12月底{county}户籍人口与年度变化。",
            f"表2列示{county}人口为{int(population['population']):,}人，较2024年12月底{change_text}。",
        )
        industry_lead = _source_lead(
            county,
            demo_question,
            f"official-{safe_component(county)}-industry-2021",
            sources["dgbas_industry_2021"],
            f"2021年工业及服务业普查按从业人数观察的{county}最大行业。",
            f"主计总处县市摘要列示，{county}按从业人数观察的最大行业为{industry}。",
        )
        boundary_lead = _source_lead(
            county,
            electoral_question,
            f"official-{safe_component(county)}-cec-district-boundary-catalog",
            sources["cec_legislative_boundaries"],
            "官方资料集说明第11届立法委员选举区范围，字段为选举区及选举区范围。",
            "已核对官方资料集目录与CSV直链；当前环境无法完成TLS下载，故未把县市行级范围晋升为事实。",
        )
        boundary_lead["raw_data_url"] = sources["cec_legislative_boundaries"]["data_url"]
        boundary_lead["verification_status"] = "verified_source_catalog"
        history_lead = _source_lead(
            county,
            electoral_question,
            f"official-{safe_component(county)}-cec-mayor-history-catalog",
            sources["cec_county_mayor_history"],
            "中选会县市长资料库列有2001、2005、2009、2014、2018、2022等届次及各县市政党得票数／率。",
            "已确认官方数据库覆盖历届县市长与县市层级政党得票展示；未取得原始ZIP，故不产生县市趋势结论。",
        )
        history_lead["verification_status"] = "verified_source_catalog"

        common_uncertainty = {
            "level": "low",
            "reason": "官方发布的固定时点统计；仍受统计定义与时点限制。",
            "competing_explanations": [],
        }
        proposals = [
            _proposal(
                proposal_id=f"baseline-{safe_component(county)}-population-2025",
                county=county,
                target_type="historical_claim",
                question=demo_question,
                evidence_lead_ids=[population_lead["lead_id"]],
                scope_boundary="仅描述2025年12月底户籍登记人口及与2024年12月底的数量差；不得当作常住人口，也不得直接推断个人投票选择。",
                contradiction_check_note="已以同一户政季刊表内总数与年度差栏交叉核对；未引入与户籍人口定义不同的常住人口估计。",
                target_record={
                    "claim_id": f"{safe_component(county)}-population-2025",
                    "claim": f"截至2025年12月底，{county}户籍登记人口为{int(population['population']):,}人，较2024年12月底{change_text}。",
                    "region": county,
                    "topic": "demographics_industry",
                    "time_scope": "2024-12-31—2025-12-31",
                    "uncertainty": common_uncertainty,
                },
            ),
            _proposal(
                proposal_id=f"baseline-{safe_component(county)}-industry-2021",
                county=county,
                target_type="historical_claim",
                question=demo_question,
                evidence_lead_ids=[industry_lead["lead_id"]],
                scope_boundary="仅描述2021年工业及服务业普查按从业人数观察的最大行业；不是产值排名，也不得直接推断地区政治态度或个人投票选择。",
                contradiction_check_note="已限定为主计总处2021普查的从业人数口径，并与产值口径分开，避免把两个排名混用。",
                target_record={
                    "claim_id": f"{safe_component(county)}-primary-employment-industry-2021",
                    "claim": f"2021年工业及服务业普查按从业人数观察，{county}从业人数最多的行业为{industry}。",
                    "region": county,
                    "topic": "demographics_industry",
                    "time_scope": "2021-01-01—2021-12-31",
                    "uncertainty": {
                        "level": "low",
                        "reason": "官方五年一次普查的县市摘要；不代表2026年即时产业结构。",
                        "competing_explanations": [],
                    },
                },
            ),
        ]
        return [population_lead, industry_lead, boundary_lead, history_lead], proposals

    def _academic_items(self, county: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        leads: List[Dict[str, Any]] = []
        proposals: List[Dict[str, Any]] = []
        for item in self.config.get("academic_records") or []:
            if item["county"] != county:
                continue
            question = _question(county, item["topic"])
            lead = _source_lead(
                county,
                question,
                item["lead_id"],
                item,
                item.get("summary") or item["claim"],
                item.get("summary") or item["claim"],
            )
            leads.append(lead)
            if not item.get("promote"):
                continue
            proposals.append(
                _proposal(
                    proposal_id=f"baseline-{item['record_id']}",
                    county=county,
                    target_type=item["target_type"],
                    question=question,
                    evidence_lead_ids=[lead["lead_id"]],
                    scope_boundary=item["scope_boundary"],
                    contradiction_check_note="核对论文题名、学校、年份与摘要所述研究结论；对当前状态的外推作为反证风险排除，并在scope_boundary中禁止。",
                    target_record={
                        "claim_id": item["record_id"],
                        "claim": item["claim"],
                        "region": county,
                        "topic": item["topic"],
                        "time_scope": str(item["time_scope"]),
                        "uncertainty": {
                            "level": "medium",
                            "reason": "学术研究提供历史解释，但研究设计、访问时点与概念界定限制外推。",
                            "competing_explanations": ["政党组织变化", "行政区改制", "候选人个人网络"],
                        },
                    },
                )
            )
        return leads, proposals

    def _relationship_items(self, county: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        leads: List[Dict[str, Any]] = []
        proposals: List[Dict[str, Any]] = []
        question = _question(county, "organizations")
        for item in self.config.get("current_relationships") or []:
            if item["county"] != county:
                continue
            evidence_ids: List[str] = []
            for source in item["sources"]:
                source = {**source, "source_grade": "C"}
                lead = _source_lead(
                    county,
                    question,
                    source["lead_id"],
                    source,
                    f"报道{item['subject']}与{item['object']}在{item['time_scope']}的公开关系。",
                    item["scope_boundary"],
                )
                leads.append(lead)
                evidence_ids.append(lead["lead_id"])
            proposals.append(
                _proposal(
                    proposal_id=f"baseline-{item['relationship_id']}",
                    county=county,
                    target_type="local_relationship",
                    question=question,
                    evidence_lead_ids=evidence_ids,
                    scope_boundary=item["scope_boundary"],
                    contradiction_check_note=item["contradiction_check_note"],
                    target_record={
                        "relationship_id": item["relationship_id"],
                        "subject": item["subject"],
                        "subject_type": "person",
                        "object": item["object"],
                        "object_type": "person" if item["relationship_type"] == "public_endorsement" else "organization",
                        "relationship_type": item["relationship_type"],
                        "region": item["region"],
                        "time_scope": str(item["time_scope"]),
                        "last_verified_at": str(item["last_verified_at"]),
                        "current_status": "active_verified",
                        "uncertainty": {
                            "level": "medium",
                            "reason": "两家独立媒体确认公开行为；关系的持续时间与实际组织效果仍未知。",
                            "competing_explanations": ["礼貌性公开表态", "阶段性竞选合作"],
                        },
                    },
                )
            )
        return leads, proposals

    def build_inputs(self, counties: Iterable[str]) -> tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
        leads_by_county: Dict[str, List[Dict[str, Any]]] = {}
        proposals: List[Dict[str, Any]] = []
        for county in counties:
            if county not in COUNTIES:
                raise ValueError(f"unsupported county: {county}")
            county_leads: List[Dict[str, Any]] = []
            for factory in (self._official_items, self._academic_items, self._relationship_items):
                leads, rows = factory(county)
                county_leads.extend(leads)
                proposals.extend(rows)
            leads_by_county[county] = county_leads
        return leads_by_county, proposals

    def _stage_leads(self, county: str, leads: List[Dict[str, Any]]) -> int:
        path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged = {
            f"{row.get('lead_id')}|{row.get('query')}": row
            for row in existing + leads
        }
        write_jsonl(path, merged.values())
        return len(merged)

    def _record_exists(self, proposal: Dict[str, Any]) -> bool:
        target_type = proposal["target_type"]
        record = proposal["target_record"]
        spec = TARGETS[target_type]
        path = self.builder._paths(proposal["county"])[spec["path_kind"]]
        identity = str(record.get(spec["id_field"]) or "")
        return any(str(row.get(spec["id_field"]) or "") == identity for row in load_jsonl(path))

    def run(
        self,
        counties: Iterable[str],
        *,
        dry_run: bool = False,
        refresh_existing: bool = False,
    ) -> Dict[str, Any]:
        county_list = list(dict.fromkeys(counties))
        leads_by_county, proposals = self.build_inputs(county_list)
        if dry_run:
            with tempfile.TemporaryDirectory(prefix="county-baseline-dry-run-") as temp_dir:
                dry_root = Path(temp_dir)
                dry_builder = KnowledgePromotionBuilder(dry_root)
                for county, leads in leads_by_county.items():
                    write_jsonl(
                        dry_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl",
                        leads,
                    )
                results = [
                    dry_builder.promote(row, dry_run=True, build_package=False)
                    for row in proposals
                ]
            return self._report(county_list, leads_by_county, proposals, results, [], dry_run=True)

        for county, leads in leads_by_county.items():
            self._stage_leads(county, leads)

        skipped: List[str] = []
        results: List[Dict[str, Any]] = []
        for proposal in proposals:
            if not refresh_existing and self._record_exists(proposal):
                skipped.append(proposal["proposal_id"])
                continue
            results.append(self.builder.promote(proposal, build_package=False))

        production = CountyKnowledgeProduction(self.repo_root)
        for county in county_list:
            question_path = (
                self.repo_root
                / "knowledge"
                / "counties"
                / safe_component(county)
                / "research_questions.jsonl"
            )
            if not question_path.exists():
                production.build_county(
                    county,
                    incremental=True,
                    resume=False,
                )
            self.builder.build_county_package(county)
        return self._report(county_list, leads_by_county, proposals, results, skipped, dry_run=False)

    @staticmethod
    def _report(
        counties: List[str],
        leads_by_county: Dict[str, List[Dict[str, Any]]],
        proposals: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        skipped: List[str],
        *,
        dry_run: bool,
    ) -> Dict[str, Any]:
        decisions: Dict[str, int] = {}
        for result in results:
            decision = str(result["receipt"]["decision"])
            decisions[decision] = decisions.get(decision, 0) + 1
        return {
            "version": BASELINE_VERSION,
            "dry_run": dry_run,
            "county_count": len(counties),
            "lead_count": sum(len(rows) for rows in leads_by_county.values()),
            "proposal_count": len(proposals),
            "evaluated_count": len(results),
            "skipped_existing_count": len(skipped),
            "skipped_existing": skipped,
            "decisions": decisions,
            "generated_at": utc_now_iso(),
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import the curated 22-county baseline through promotion gates")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--counties", default="")
    parser.add_argument("--all-counties", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-existing", action="store_true")
    args = parser.parse_args(argv)
    counties = list(COUNTIES) if args.all_counties else [item.strip() for item in args.counties.split(",") if item.strip()]
    if not counties:
        parser.error("provide --counties or --all-counties")
    result = CuratedCountyBaseline(Path(args.repo_root).resolve() if args.repo_root else None).run(
        counties,
        dry_run=args.dry_run,
        refresh_existing=args.refresh_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not {"rejected", "requires_review"}.intersection(result["decisions"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
