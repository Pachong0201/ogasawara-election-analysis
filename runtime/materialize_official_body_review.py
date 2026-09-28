"""Materialize a human-reviewed official-body L3 evidence batch.

The input is an audit artifact, not free-form model output.  This command
stages the reviewed body excerpts, builds narrowly scoped relationship and
issue proposals, dry-runs every gate, and only then writes promoted knowledge.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

from .election_loader import load_jsonl, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .knowledge_coverage import KnowledgeCoverageAudit
from .models import utc_now_iso


RELATION_SCOPE = (
    "僅確認來源所列日期的公開互動、共同辦理或職務行為；不得推定持續合作、"
    "派系歸屬、選舉支持、動員效果或選舉結果。"
)
ISSUE_SCOPE = (
    "僅確認來源所列日期的政策、活動或公開說法；不得把規劃、申請或單次活動"
    "解讀為已完成、具長期成效或已獲社會共識。"
)


def _question(county: str, kind: str) -> str:
    if kind == "relationship":
        return f"{county} 有哪些與地方公共事務相關、且能由公開正文確認的人物—組織或組織—組織互動？"
    return f"{county} 當前有哪些地方治理、建設、環境、社福或產業議題具近期公開正文證據？"


def _host_key(url: str) -> str:
    return (urlparse(url).hostname or "unknown").lower()


def _lead(
    source: Dict[str, Any],
    *,
    county: str,
    lead_id: str,
    reviewed_at: str,
    questions: List[str],
) -> Dict[str, Any]:
    url = str(source["url"])
    host = _host_key(url)
    grade = str(source.get("source_grade") or "A").upper()
    evidence = "；".join(
        value.strip()
        for value in (str(source.get("quote") or ""), str(source.get("extra_quote") or ""))
        if value.strip()
    )
    return {
        "lead_id": lead_id,
        "county": county,
        "query": questions[0],
        "research_questions": questions,
        "title": str(source.get("title") or ""),
        "summary": str(source.get("summary") or evidence)[:700],
        "evidence": evidence,
        "url": url,
        "source_id": str(source.get("source_id") or f"official_{host.replace('.', '_')}"),
        "source_name": str(source.get("source") or host),
        "source_grade": grade,
        "verification_status": "verified",
        "independence_key": str(source.get("independence_key") or host),
        "published_at": str(source.get("date") or ""),
        "retrieved_at": reviewed_at,
        "body_review_method": str(source.get("body_review_method") or "human_reviewed_body"),
    }


def build_artifacts(review: Dict[str, Any], stamp: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    reviewed_at = str(review.get("reviewed_at") or "").strip()
    if not reviewed_at:
        raise ValueError("reviewed_at is required")
    leads: List[Dict[str, Any]] = []
    proposals: List[Dict[str, Any]] = []

    for source_index, source in enumerate(review.get("sources") or []):
        county = str(source.get("county") or "").strip()
        date = str(source.get("date") or "").strip()
        if not county or not date or not source.get("url") or not source.get("quote"):
            raise ValueError(f"source {source_index} lacks county/date/url/quote")
        relation_rows = source.get("relations") or []
        issue_rows = source.get("issues") or []
        questions = []
        if relation_rows:
            questions.append(_question(county, "relationship"))
        if issue_rows:
            questions.append(_question(county, "issue"))
        if not questions:
            raise ValueError(f"source {source_index} contains no relationship or issue")

        primary_id = f"official-body-{stamp}-{source_index}"
        leads.append(
            _lead(
                source,
                county=county,
                lead_id=primary_id,
                reviewed_at=reviewed_at,
                questions=questions,
            )
        )
        evidence_ids = [primary_id]
        for support_index, support in enumerate(source.get("supporting_sources") or []):
            support_id = f"official-support-{stamp}-{source_index}-{support_index}"
            leads.append(
                _lead(
                    support,
                    county=county,
                    lead_id=support_id,
                    reviewed_at=reviewed_at,
                    questions=questions,
                )
            )
            evidence_ids.append(support_id)

        contradiction_note = (
            f"{str(source.get('note') or '').strip()} "
            f"反證查詢：{str(source.get('counter_query') or '').strip()}；查核日期：{reviewed_at}"
        ).strip()
        source_name = str(source.get("source") or _host_key(str(source["url"])))

        for relation_index, relation in enumerate(relation_rows):
            description = str(relation["description"]).strip()
            proposals.append({
                "proposal_id": f"promote-official-event-{stamp}-{source_index}-{relation_index}",
                "county": county,
                "target_type": "local_relationship",
                "research_questions": [_question(county, "relationship")],
                "evidence_lead_ids": evidence_ids,
                "contradiction_check_completed": True,
                "contradictory_lead_ids": [],
                "contradiction_check_note": contradiction_note,
                "scope_boundary": RELATION_SCOPE,
                "target_record": {
                    "relationship_id": f"official-event-{stamp}-{source_index}-{relation_index}",
                    "subject": str(relation["subject"]),
                    "subject_type": str(relation.get("subject_type") or "person"),
                    "object": str(relation["object"]),
                    "object_type": str(relation.get("object_type") or "organization"),
                    "relationship_type": str(relation.get("relationship_type") or "other"),
                    "description": description,
                    "evidence_summary": description,
                    "region": county,
                    "time_scope": date,
                    "event_date": date,
                    "last_verified_at": reviewed_at,
                    "current_status": str(relation.get("current_status") or "active_verified"),
                    "source_grade": str(source.get("source_grade") or "A").upper(),
                    "source": source_name,
                    "scope_boundary": RELATION_SCOPE,
                    "structural_use": "dated_interaction_only_no_continuity_inference",
                    "uncertainty": {
                        "level": "medium",
                        "reason": "正文只確認特定日期的公開互動，不確認關係延續或政治意涵",
                        "competing_explanations": [],
                    },
                },
            })

        for issue_index, issue in enumerate(issue_rows):
            statement = str(issue["statement"]).strip()
            proposals.append({
                "proposal_id": f"promote-official-issue-{stamp}-{source_index}-{issue_index}",
                "county": county,
                "target_type": "current_issue",
                "research_questions": [_question(county, "issue")],
                "evidence_lead_ids": evidence_ids,
                "contradiction_check_completed": True,
                "contradictory_lead_ids": [],
                "contradiction_check_note": contradiction_note,
                "scope_boundary": ISSUE_SCOPE,
                "target_record": {
                    "claim_id": f"official-issue-{stamp}-{source_index}-{issue_index}",
                    "claim_text": statement,
                    "title": str(issue["title"]),
                    "claim_type": str(issue.get("claim_type") or "policy"),
                    "date": date,
                    "time_scope": date,
                    "last_verified_at": reviewed_at,
                    "region": county,
                    "verification_status": "official_record",
                    "source_grade": str(source.get("source_grade") or "A").upper(),
                    "source": source_name,
                    "scope_boundary": ISSUE_SCOPE,
                    "uncertainty": {
                        "level": "medium",
                        "reason": "近期正文確認議題存在，但不據此判定長期成效、民意強度或選舉重要性",
                        "competing_explanations": [],
                    },
                },
            })
    return leads, proposals


def _stage_leads(root: Path, leads: Iterable[Dict[str, Any]]) -> None:
    by_county: Dict[str, List[Dict[str, Any]]] = {}
    for lead in leads:
        by_county.setdefault(str(lead["county"]), []).append(lead)
    for county, rows in by_county.items():
        path = root / "cache" / "retrieval" / f"{county}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged = {str(row.get("lead_id") or row.get("url")): row for row in existing + rows}
        write_jsonl(path, merged.values())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize(root: Path, review_path: Path, stamp: str) -> Dict[str, Any]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    leads, proposals = build_artifacts(review, stamp)
    _stage_leads(root, leads)

    proposal_path = root / "data" / "research" / f"l3_official_proposals_{stamp}.json"
    _write_json(proposal_path, proposals)
    builder = KnowledgePromotionBuilder(root)
    dry = builder.promote_inbox(proposal_path, dry_run=True, build_package=False)
    if dry["counts"]["dry_run_pass"] != len(proposals):
        failures = [
            result["receipt"] for result in dry["results"]
            if result["receipt"]["decision"] != "dry_run_pass"
        ]
        raise RuntimeError("promotion gate rejected reviewed batch: " + json.dumps(failures, ensure_ascii=False))

    applied = builder.promote_inbox(proposal_path, dry_run=False, build_package=True)
    if applied["counts"]["promoted"] != len(proposals):
        raise RuntimeError("reviewed batch was not fully promoted")

    generated_at = utc_now_iso()
    manifest = {
        "generated_at": generated_at,
        "reviewed_at": review["reviewed_at"],
        "method": review.get("method"),
        "scope": review.get("scope"),
        "source_count": len(review.get("sources") or []),
        "county_count": len(applied["counties"]),
        "proposal_count": len(proposals),
        "relationship_count": sum(p["target_type"] == "local_relationship" for p in proposals),
        "issue_count": sum(p["target_type"] == "current_issue" for p in proposals),
        "promoted_count": applied["counts"]["promoted"],
        "receipts": [result["receipt"] for result in applied["results"]],
    }
    manifest_path = root / "data" / "manifests" / f"l3_official_review_{stamp}.json"
    _write_json(manifest_path, manifest)
    _write_json(root / "data" / "manifests" / "knowledge_coverage_latest.json", KnowledgeCoverageAudit(root).build())
    return {
        "proposal_path": str(proposal_path.relative_to(root)),
        "manifest_path": str(manifest_path.relative_to(root)),
        **{key: manifest[key] for key in (
            "source_count", "county_count", "proposal_count", "relationship_count",
            "issue_count", "promoted_count",
        )},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    print(json.dumps(materialize(args.repo_root, args.review, args.stamp), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
