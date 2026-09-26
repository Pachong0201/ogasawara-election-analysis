"""Promote official current Legislative Yuan membership into county L3 relationships."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .county_knowledge import COUNTIES
from .election_loader import load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .ly_current_legislators import LYCurrentLegislatorAdapter
from .models import utc_now_iso


class CurrentLegislatorMaterializer:
    def __init__(
        self,
        repo_root: Optional[Path] = None,
        *,
        adapter: Optional[LYCurrentLegislatorAdapter] = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.adapter = adapter or LYCurrentLegislatorAdapter()
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    @staticmethod
    def _question(county: str) -> str:
        return f"{county}目前有哪些由立法院官方名冊確認的第11屆區域立法委員？"

    def _stage(self, county: str, leads: List[Dict[str, Any]]) -> None:
        path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for row in existing + leads:
            key = str(row.get("lead_id") or row.get("url") or "").strip()
            if key:
                merged[key] = row
        write_jsonl(path, merged.values())

    def _lead(self, row: Dict[str, Any]) -> Dict[str, Any]:
        county = str(row["county"])
        name = str(row["name"])
        party = str(row.get("party") or "")
        constituency = str(row.get("constituency") or "")
        onboard = str(row.get("onboard_date") or "")
        question = self._question(county)
        return {
            "lead_id": f"ly-current-{safe_component(county)}-{safe_component(str(row['member_id']))}",
            "county": county,
            "query": question,
            "research_questions": [question],
            "title": f"立法院第11屆立法委員：{name}",
            "summary": (
                f"立法院本屆立委資料列示{name}為第11屆立法委員，"
                f"黨籍為{party or '未列示'}，選區為{constituency}，"
                f"到職日期為{onboard or '未列示'}。"
            ),
            "evidence": f"{name}；黨籍：{party}；選區：{constituency}；到職日期：{onboard}",
            "url": str(row["source_reference"]),
            "source_id": "legislative_yuan_current_members",
            "source_name": "立法院",
            "source_grade": "A",
            "verification_status": "verified",
            "independence_key": "legislative_yuan",
            "published_at": "",
            "retrieved_at": str(row.get("last_verified_at") or utc_now_iso()),
        }

    def _proposal(self, row: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
        county = str(row["county"])
        name = str(row["name"])
        constituency = str(row.get("constituency") or "")
        last_verified = str(row.get("last_verified_at") or utc_now_iso())[:10]
        onboard = str(row.get("onboard_date") or "")
        relation_id = f"ly11-office-{safe_component(county)}-{safe_component(str(row['member_id']))}"
        return {
            "proposal_id": f"promote-{relation_id}",
            "county": county,
            "target_type": "local_relationship",
            "research_questions": [self._question(county)],
            "evidence_lead_ids": [lead["lead_id"]],
            "contradiction_check_completed": True,
            "contradictory_lead_ids": [],
            "contradiction_check_note": (
                "以立法院本屆立法委員名單與委員個人頁作為當次現任狀態基準；"
                "離職委員另列於官方離職名單。後續職務異動須重新抓取官方頁面。"
            ),
            "scope_boundary": (
                "僅確認立法院官方頁面當次列示的第11屆現任立法委員、黨籍與選區；"
                "不推斷地方派系歸屬、支持轉移、競選動員能力或選舉結果。"
            ),
            "target_record": {
                "relationship_id": relation_id,
                "subject": name,
                "subject_type": "person",
                "object": "立法院第11屆立法委員",
                "object_type": "organization",
                "relationship_type": "office_holding",
                "region": county,
                "time_scope": f"{onboard or '2024-02-01'}—current",
                "last_verified_at": last_verified,
                "current_status": "active_verified",
                "party": str(row.get("party") or ""),
                "electoral_district": constituency,
                "uncertainty": {
                    "level": "low",
                    "reason": "立法院本屆立委官方名冊及個人頁。",
                    "competing_explanations": [],
                },
            },
        }

    def materialize(self, *, apply: bool = False) -> Dict[str, Any]:
        fetched = self.adapter.fetch_all()
        grouped: Dict[str, List[Dict[str, Any]]] = {county: [] for county in COUNTIES}
        for row in fetched["records"]:
            county = str(row.get("county") or "")
            if county in grouped:
                grouped[county].append(row)

        results: List[Dict[str, Any]] = []
        for county, rows in grouped.items():
            if not rows:
                results.append({"county": county, "member_count": 0, "promoted_count": 0, "status": "missing"})
                continue
            leads = [self._lead(row) for row in rows]
            self._stage(county, leads)
            receipts = []
            for row, lead in zip(rows, leads):
                result = self.builder.promote(
                    self._proposal(row, lead),
                    dry_run=not apply,
                    build_package=False,
                )
                receipts.append(result["receipt"])
            promoted = sum(
                1 for receipt in receipts
                if receipt.get("decision") in {"promoted", "unchanged"}
            )
            if apply:
                self.builder.build_county_package(county)
            results.append({
                "county": county,
                "member_count": len(rows),
                "promoted_count": promoted,
                "status": "complete" if promoted == len(rows) else "partial",
                "receipts": receipts,
            })

        output = {
            "generated_at": utc_now_iso(),
            "county_count": len(COUNTIES),
            "covered_county_count": sum(1 for row in results if row["member_count"] > 0),
            "member_count": sum(int(row["member_count"]) for row in results),
            "promoted_count": sum(int(row["promoted_count"]) for row in results),
            "unassigned_current_member_count": len(fetched.get("unassigned") or []),
            "source_failure_count": int(fetched.get("failure_count") or 0),
            "source_failures": fetched.get("failures") or [],
            "results": results,
            "apply": apply,
        }
        path = self.repo_root / "data" / "manifests" / "l3_current_legislators_latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output["manifest_path"] = str(path.relative_to(self.repo_root))
        return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    result = CurrentLegislatorMaterializer(args.repo_root).materialize(apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["source_failure_count"] == 0 and result["member_count"] > 0:
        return 0
    if args.allow_partial and result["member_count"] > 0:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
