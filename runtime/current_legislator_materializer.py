"""Promote current regional legislators into county L3 relationships.

Current status comes from the Legislative Yuan current-member roster. County,
district and party are taken from the already materialized official CEC 2024
regional-legislator results. A record is promoted only when the CEC district
winner's name is still present in the Legislative Yuan current roster.

This avoids treating the 2024 result alone as proof of current office holding
and avoids one HTTP request per legislator profile.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .county_knowledge import COUNTIES
from .election_loader import election_file_path, load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .ly_current_legislators import LYCurrentLegislatorAdapter
from .models import utc_now_iso


def _norm_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("．", "‧")


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

    def _current_roster(self) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
        failures: List[Dict[str, str]] = []
        roster: Dict[str, str] = {}
        try:
            for url, name in self.adapter.member_links():
                key = _norm_name(name)
                if key:
                    roster[key] = name
        except Exception as exc:
            failures.append({
                "source": self.adapter.page_url,
                "error": f"{type(exc).__name__}: {exc}",
            })
        return roster, failures

    def _cec_winners(self, county: str) -> List[Dict[str, Any]]:
        path = election_file_path(
            self.repo_root, "regional_legislator", 2024, county
        )
        rows = load_jsonl(path) if path.exists() else []
        totals: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for row in rows:
            codes = row.get("cec_codes") or {}
            district = str(codes.get("election_district") or "").strip()
            name = str(row.get("candidate_name") or "").strip()
            party = str(row.get("party") or "").strip()
            if not district or not name:
                continue
            key = (district, name, party)
            bucket = totals.setdefault(
                key,
                {
                    "district_code": district,
                    "name": name,
                    "party": party,
                    "votes": 0,
                    "source": str(row.get("source") or ""),
                    "source_reference": str(
                        row.get("source_reference") or row.get("source") or ""
                    ),
                },
            )
            bucket["votes"] += int(row.get("votes") or 0)

        by_district: Dict[str, List[Dict[str, Any]]] = {}
        for row in totals.values():
            by_district.setdefault(row["district_code"], []).append(row)

        winners: List[Dict[str, Any]] = []
        for district, candidates in sorted(by_district.items()):
            ordered = sorted(
                candidates,
                key=lambda item: (-int(item["votes"]), item["name"]),
            )
            if not ordered:
                continue
            if len(ordered) > 1 and int(ordered[0]["votes"]) == int(ordered[1]["votes"]):
                continue
            winner = dict(ordered[0])
            winner["county"] = county
            winner["electoral_district"] = f"{county}第{int(district)}選舉區"
            winners.append(winner)
        return winners

    def _leads(self, row: Dict[str, Any], roster_name: str) -> List[Dict[str, Any]]:
        county = str(row["county"])
        name = str(row["name"])
        party = str(row.get("party") or "")
        district = str(row.get("electoral_district") or "")
        question = self._question(county)
        now = utc_now_iso()
        return [
            {
                "lead_id": f"ly-current-{safe_component(county)}-{safe_component(name)}",
                "county": county,
                "query": question,
                "research_questions": [question],
                "title": "立法院第11屆本屆立法委員名單",
                "summary": f"立法院本屆立委名單目前列有{roster_name}。",
                "evidence": f"第11屆立法委員名單：{roster_name}",
                "url": self.adapter.page_url,
                "source_id": "legislative_yuan_current_members",
                "source_name": "立法院",
                "source_grade": "A",
                "verification_status": "verified",
                "independence_key": "legislative_yuan",
                "published_at": "",
                "retrieved_at": now,
            },
            {
                "lead_id": f"cec-2024-regional-winner-{safe_component(county)}-{safe_component(name)}",
                "county": county,
                "query": question,
                "research_questions": [question],
                "title": "中選會2024區域立法委員選舉資料",
                "summary": f"中選會2024區域立委資料彙總顯示{name}在{district}得票最高。",
                "evidence": f"{district}；{name}；{party}；彙總得票{row['votes']}",
                "url": str(row.get("source_reference") or row.get("source") or ""),
                "source_id": "cec_open_data",
                "source_name": "中央選舉委員會",
                "source_grade": "A",
                "verification_status": "verified",
                "independence_key": "cec",
                "published_at": "2024-01-13",
                "retrieved_at": now,
            },
        ]

    def _proposal(
        self,
        row: Dict[str, Any],
        leads: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        county = str(row["county"])
        name = str(row["name"])
        district = str(row.get("electoral_district") or "")
        today = utc_now_iso()[:10]
        relation_id = f"ly11-office-{safe_component(county)}-{safe_component(name)}"
        return {
            "proposal_id": f"promote-{relation_id}",
            "county": county,
            "target_type": "local_relationship",
            "research_questions": [self._question(county)],
            "evidence_lead_ids": [lead["lead_id"] for lead in leads],
            "contradiction_check_completed": True,
            "contradictory_lead_ids": [],
            "contradiction_check_note": (
                "立法院本屆名單確認當次仍在任；中選會2024區域立委資料確認"
                "同名當選人的選區與政黨。只有兩個官方來源姓名一致時才晉升；"
                "未匹配的2024當選人留作待核，不以歷史結果推定現任。"
            ),
            "scope_boundary": (
                "僅確認當次立法院本屆名單中的第11屆區域立法委員，以及其"
                "2024中選會選舉資料中的選區與政黨；不推斷地方派系歸屬、"
                "支持轉移、競選動員能力或任何選舉結果。"
            ),
            "target_record": {
                "relationship_id": relation_id,
                "subject": name,
                "subject_type": "person",
                "object": "立法院第11屆立法委員",
                "object_type": "organization",
                "relationship_type": "office_holding",
                "region": county,
                "time_scope": "2024-02-01—current",
                "last_verified_at": today,
                "current_status": "active_verified",
                "party": str(row.get("party") or ""),
                "electoral_district": district,
                "uncertainty": {
                    "level": "low",
                    "reason": "立法院現任名冊與中選會2024區域立委資料雙重官方核對。",
                    "competing_explanations": [],
                },
            },
        }

    def materialize(self, *, apply: bool = False) -> Dict[str, Any]:
        roster, source_failures = self._current_roster()
        grouped: Dict[str, List[Dict[str, Any]]] = {county: [] for county in COUNTIES}
        unmatched_winners: List[Dict[str, Any]] = []

        for county in COUNTIES:
            for winner in self._cec_winners(county):
                roster_name = roster.get(_norm_name(winner["name"]))
                if not roster_name:
                    unmatched_winners.append({
                        "county": county,
                        "name": winner["name"],
                        "electoral_district": winner["electoral_district"],
                        "reason": "2024 CEC district winner not found in current LY roster",
                    })
                    continue
                winner["roster_name"] = roster_name
                grouped[county].append(winner)

        results: List[Dict[str, Any]] = []
        for county, rows in grouped.items():
            receipts: List[Dict[str, Any]] = []
            for row in rows:
                leads = self._leads(row, str(row["roster_name"]))
                self._stage(county, leads)
                result = self.builder.promote(
                    self._proposal(row, leads),
                    dry_run=not apply,
                    build_package=False,
                )
                receipts.append(result["receipt"])
            promoted = sum(
                1 for receipt in receipts
                if receipt.get("decision") in {"promoted", "unchanged"}
            )
            if apply and rows:
                self.builder.build_county_package(county)
            results.append({
                "county": county,
                "member_count": len(rows),
                "promoted_count": promoted,
                "status": (
                    "complete"
                    if rows and promoted == len(rows)
                    else ("missing" if not rows else "partial")
                ),
                "receipts": receipts,
            })

        output = {
            "generated_at": utc_now_iso(),
            "county_count": len(COUNTIES),
            "roster_member_count": len(roster),
            "covered_county_count": sum(1 for row in results if row["member_count"] > 0),
            "member_count": sum(int(row["member_count"]) for row in results),
            "promoted_count": sum(int(row["promoted_count"]) for row in results),
            "unmatched_2024_winner_count": len(unmatched_winners),
            "unmatched_2024_winners": unmatched_winners,
            "source_failure_count": len(source_failures),
            "source_failures": source_failures,
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
    if (
        result["source_failure_count"] == 0
        and result["member_count"] > 0
        and result["promoted_count"] == result["member_count"]
    ):
        return 0
    if args.allow_partial and result["member_count"] > 0:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
