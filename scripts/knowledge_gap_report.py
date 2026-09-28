#!/usr/bin/env python3
"""Generate the county x topic knowledge gap matrix and depth-sample plans.

This report is descriptive.  It never promotes a record: academic leads stay
leads until their full text is read and a structured proposal passes the
KnowledgePromotionBuilder gates.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO = Path(__file__).resolve().parents[1]
COUNTIES = sorted(p.name for p in (REPO / "knowledge" / "counties").iterdir() if p.is_dir())


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def topic_evidence_counts(county: str, questions: List[Dict[str, Any]]) -> Dict[str, int]:
    evidence = load_jsonl(REPO / "knowledge" / "counties" / county / "evidence_index.jsonl")
    counts: Dict[str, int] = {}
    for question in questions:
        topic = question["topic"]
        text = str(question["question"])
        count = 0
        for record in evidence:
            linked = record.get("research_questions") or []
            if isinstance(linked, str):
                linked = [linked]
            if any(str(item) == text or str(item) in text or text in str(item) for item in linked):
                count += 1
        counts[topic] = count
    return counts


def county_detail(county: str) -> Dict[str, Any]:
    root = REPO / "knowledge" / "counties" / county
    questions = load_jsonl(root / "research_questions.jsonl")
    unresolved = load_jsonl(root / "unresolved_questions.jsonl")
    evidence = load_jsonl(root / "evidence_index.jsonl")
    historical = load_jsonl(REPO / "knowledge" / "historical" / county / "claims.jsonl")
    relationships = load_jsonl(REPO / "knowledge" / "local" / county / "relationships.jsonl")
    candidates = load_jsonl(REPO / "knowledge" / "local" / county / "candidates.jsonl")
    issues = load_jsonl(REPO / "knowledge" / "local" / county / "issues.jsonl")
    receipts = load_jsonl(REPO / "cache" / "knowledge_promotion" / f"{county}.jsonl")
    return {
        "county": county,
        "questions": len(questions),
        "unresolved_questions": len(unresolved),
        "promoted_evidence_records": len(evidence),
        "historical_claims": len(historical),
        "relationships": len(relationships),
        "candidate_profiles": len(candidates),
        "current_issues": len(issues),
        "candidate_counts": {
            "county_mayor": sum(1 for c in candidates if c.get("election_type") == "county_mayor"),
            "councilor": sum(1 for c in candidates if c.get("election_type") == "councilor"),
        },
        "promotion_receipts": {
            "total": len(receipts),
            "promoted": sum(1 for r in receipts if r.get("decision") in {"promoted", "unchanged"}),
            "rejected": sum(1 for r in receipts if r.get("decision") == "rejected"),
            "requires_review": sum(1 for r in receipts if r.get("decision") == "requires_review"),
            "contradiction_checked": sum(
                1 for r in receipts if r.get("contradiction_check_completed")
            ),
        },
        "topic_status": {
            q["topic"]: {
                "question": q["question"],
                "status": q.get("status"),
                "promotion_required": q.get("promotion_required"),
            }
            for q in questions
        },
        "topic_evidence_records": topic_evidence_counts(county, questions),
        "unresolved_detail": [
            {
                "topic": row.get("topic"),
                "status": row.get("status"),
                "lead_ids": row.get("lead_ids") or [],
                "grades": row.get("grades") or [],
                "query": row.get("query"),
            }
            for row in unresolved
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", default="台北市,宜蘭縣,高雄市")
    parser.add_argument("--matrix-output", default="data/manifests/knowledge_gap_matrix.json")
    parser.add_argument("--samples-output", default="data/manifests/knowledge_depth_samples.json")
    args = parser.parse_args()

    academic = load_yaml(REPO / "config" / "county_academic_research.yaml").get("academic_records") or []
    academic_by_county: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for record in academic:
        academic_by_county[str(record.get("county") or "")].append(record)

    counties = [county_detail(county) for county in COUNTIES]
    topic_summary: Dict[str, Dict[str, int]] = {}
    target_types: Dict[str, int] = {}
    for county in counties:
        for topic, status in county["topic_status"].items():
            row = topic_summary.setdefault(
                topic, {"counties": 0, "unresolved": 0, "promoted_evidence_available": 0, "evidence_records": 0}
            )
            row["counties"] += 1
            if status["status"] == "unresolved":
                row["unresolved"] += 1
            else:
                row["promoted_evidence_available"] += 1
            row["evidence_records"] += int(county["topic_evidence_records"].get(topic, 0))
        for record in load_jsonl(REPO / "knowledge" / "counties" / county["county"] / "evidence_index.jsonl"):
            key = str(record.get("target_type") or "unknown")
            target_types[key] = target_types.get(key, 0) + 1

    matrix = {
        "generated_at": "2026-09-27",
        "county_count": len(counties),
        "question_count": sum(c["questions"] for c in counties),
        "unresolved_question_count": sum(c["unresolved_questions"] for c in counties),
        "topic_summary": topic_summary,
        "evidence_target_type_totals": target_types,
        "counties": counties,
        "interpretation_boundary": (
            "未解决只表示尚无通过晋升门禁的证据。学术线索必须读取正文并由结构化提案晋升，"
            "不得据目录、摘要或任职事实推断支持度或动员能力。"
        ),
    }
    matrix_path = REPO / args.matrix_output
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sample_names = [name.strip() for name in args.samples.split(",") if name.strip()]
    samples = {
        "generated_at": "2026-09-27",
        "auto_research_status": {
            "enabled": False,
            "configuration_error": "missing_research_credentials",
            "pending_jobs_not_counted_as_success": True,
            "backoff": "ProviderError.retryable with attempts<3 and retry_after; pending/retry_pending never counted as success",
        },
        "external_fulltext_status": "not_read_in_this_run: local .gov.tw / NDLTD network unavailable and auto-research credentials absent",
        "samples": [],
    }
    for name in sample_names:
        detail = next((row for row in counties if row["county"] == name), None)
        if detail is None:
            continue
        leads = []
        for record in academic_by_county.get(name, []):
            leads.append(
                {
                    "lead_id": record.get("lead_id"),
                    "source_name": record.get("source_name"),
                    "source_grade": record.get("source_grade"),
                    "published_at": record.get("published_at"),
                    "url": record.get("url"),
                    "topic": record.get("topic"),
                    "promote": bool(record.get("promote")),
                    "full_text_read": False,
                    "required_before_promotion": [
                        "read article/PDF body from an accessible copy",
                        "record page numbers for each promoted claim",
                        "record applicable time_scope and geographic scope",
                        "run contradiction check and store proposal_id/receipt",
                    ],
                }
            )
        samples["samples"].append(
            {
                "county": name,
                "inventory": {
                    key: detail[key]
                    for key in [
                        "questions",
                        "unresolved_questions",
                        "promoted_evidence_records",
                        "historical_claims",
                        "relationships",
                        "candidate_profiles",
                        "current_issues",
                        "candidate_counts",
                        "promotion_receipts",
                    ]
                },
                "topic_evidence_records": detail["topic_evidence_records"],
                "unresolved_detail": detail["unresolved_detail"],
                "academic_leads": leads,
                "depth_actions": [
                    "補候選人經歷、地方經營區域與組織互動：只以可核 A/B 或雙 C 正文產生 proposal。",
                    "補歷史政治結構：先讀学术线索全文，逐页記錄時間與地理範圍。",
                    "关键治理议题與相反证据：检索时同时导出反证/更正报道，矛盾进入 requires_review。",
                ],
            }
        )
    samples_path = REPO / args.samples_output
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "matrix": str(matrix_path),
        "samples": str(samples_path),
        "county_count": matrix["county_count"],
        "questions": matrix["question_count"],
        "unresolved": matrix["unresolved_question_count"],
        "topic_summary": topic_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
