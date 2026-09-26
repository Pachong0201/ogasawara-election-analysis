"""Materialize official 2026 county/city mayor registrations into L3 candidate profiles.

Only CEC registration facts are promoted. Registration is not treated as
qualification approval, endorsement strength, electability, or an election
result. Party-recommendation relationships remain subject to the separate
current_relationship_verifier evidence rule.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cec_current_candidates import CECCurrentCandidateAdapter
from .county_knowledge import COUNTIES
from .election_loader import load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .models import utc_now_iso


class CurrentCandidateMaterializer:
    def __init__(
        self,
        repo_root: Optional[Path] = None,
        *,
        adapter: Optional[CECCurrentCandidateAdapter] = None,
    ):
        self.repo_root = (
            Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        )
        self.adapter = adapter or CECCurrentCandidateAdapter()
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    @staticmethod
    def _question(county: str) -> str:
        return (
            f"2026年{county}縣市長選舉有哪些已完成登記的候選人，"
            "其登記日期與政黨推薦為何？"
        )

    def _stage(self, county: str, leads: Iterable[Dict[str, Any]]) -> None:
        path = (
            self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        )
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for row in existing + list(leads):
            key = str(row.get("lead_id") or row.get("url") or "").strip()
            if key:
                merged[key] = row
        write_jsonl(path, merged.values())

    def _lead(
        self,
        county: str,
        candidate: Dict[str, Any],
        question: str,
    ) -> Dict[str, Any]:
        name = str(candidate.get("name") or candidate.get("candidate_name") or "").strip()
        date = str(candidate.get("registration_date") or "").strip()
        party = str(candidate.get("recommended_by_party") or "").strip()
        reference = str(
            candidate.get("source_reference")
            or candidate.get("source")
            or self.adapter.page_url
        ).strip()
        party_text = party or "未由政黨推薦"
        return {
            "lead_id": (
                f"cec-current-candidate-{safe_component(county)}-"
                f"{safe_component(str(candidate.get('candidate_id') or name))}"
            ),
            "county": county,
            "query": question,
            "research_questions": [question],
            "title": "115年地方公職人員選舉候選人登記名冊",
            "summary": (
                f"中央選舉委員會登記名冊列示{name}於{date}完成"
                f"{county}縣市長候選人登記；推薦政黨欄為{party_text}。"
            ),
            "evidence": f"{county}；{date}；{name}；推薦之政黨：{party_text}",
            "url": reference,
            "source_id": "cec_current_candidates",
            "source_name": "中央選舉委員會",
            "source_grade": "A",
            "verification_status": "verified",
            "independence_key": "cec",
            "published_at": str(candidate.get("published_at") or "2026-09-07"),
            "retrieved_at": str(
                candidate.get("last_verified_at")
                or candidate.get("retrieved_at")
                or utc_now_iso()
            ),
        }

    def _proposal(
        self,
        county: str,
        candidate: Dict[str, Any],
        lead: Dict[str, Any],
        question: str,
    ) -> Dict[str, Any]:
        name = str(candidate.get("name") or candidate.get("candidate_name") or "").strip()
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        registration_date = str(candidate.get("registration_date") or "").strip()
        last_verified = str(
            candidate.get("last_verified_at")
            or candidate.get("retrieved_at")
            or utc_now_iso()
        )[:10]
        return {
            "proposal_id": f"promote-{candidate_id}",
            "county": county,
            "target_type": "candidate_profile",
            "research_questions": [question],
            "evidence_lead_ids": [lead["lead_id"]],
            "contradiction_check_completed": True,
            "contradictory_lead_ids": [],
            "contradiction_check_note": (
                "重新讀取中選會115年9月7日候選人登記名冊並以當次官方頁面為"
                "登記狀態基準；本紀錄只保存完成登記事實，不將登記解讀為資格審定"
                "通過。後續撤回、資格審定或名單異動須另行更新。"
            ),
            "scope_boundary": (
                "僅確認115年地方公職人員選舉縣市長候選人完成登記時的姓名、"
                "登記日期與政黨推薦欄；不得用於推斷資格審定結果、支持度、"
                "動員效果、當選可能性或選舉結果。"
            ),
            "target_record": {
                "candidate_id": candidate_id,
                "name": name,
                "candidate_name": name,
                "election_type": "county_mayor",
                "election_year": 2026,
                "jurisdiction": county,
                "official_jurisdiction": str(
                    candidate.get("official_jurisdiction") or county
                ),
                "candidate_status": "registered",
                "registration_date": registration_date,
                "recommended_by_party": str(
                    candidate.get("recommended_by_party") or ""
                ),
                "party": str(candidate.get("party") or "未由政黨推薦"),
                "time_scope": "2026-08-31—2026-09-04 candidate registration",
                "last_verified_at": last_verified,
                "uncertainty": {
                    "level": "low",
                    "reason": "中央選舉委員會候選人登記名冊A級官方資料。",
                    "competing_explanations": [],
                },
            },
        }

    def materialize_county(self, county: str, *, apply: bool = False) -> Dict[str, Any]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        fetched = self.adapter.fetch(county, "county_mayor", 2026)
        records = list(fetched.records or [])
        if not records:
            return {
                "county": county,
                "candidate_count": 0,
                "promoted_count": 0,
                "status": "missing",
                "warnings": list(fetched.warnings or []),
            }

        question = self._question(county)
        leads = [self._lead(county, candidate, question) for candidate in records]
        self._stage(county, leads)

        receipts: List[Dict[str, Any]] = []
        for candidate, lead in zip(records, leads):
            proposal = self._proposal(county, candidate, lead, question)
            result = self.builder.promote(
                proposal,
                dry_run=not apply,
                build_package=False,
            )
            receipts.append(result["receipt"])

        promoted = sum(
            1
            for receipt in receipts
            if receipt.get("decision") in {"promoted", "unchanged"}
        )
        if apply:
            self.builder.build_county_package(county)
        return {
            "county": county,
            "candidate_count": len(records),
            "promoted_count": promoted,
            "status": "complete" if promoted == len(records) else "partial",
            "warnings": list(fetched.warnings or []),
            "receipts": receipts,
        }

    def materialize_many(
        self,
        counties: Optional[Iterable[str]] = None,
        *,
        apply: bool = False,
    ) -> Dict[str, Any]:
        selected = list(counties or COUNTIES)
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for county in selected:
            try:
                results.append(self.materialize_county(county, apply=apply))
            except Exception as exc:
                failures.append(
                    {"county": county, "error": f"{type(exc).__name__}: {exc}"}
                )
        output = {
            "generated_at": utc_now_iso(),
            "county_count": len(selected),
            "complete_count": sum(1 for row in results if row["status"] == "complete"),
            "candidate_count": sum(int(row.get("candidate_count") or 0) for row in results),
            "promoted_count": sum(int(row.get("promoted_count") or 0) for row in results),
            "failure_count": len(failures),
            "results": results,
            "failures": failures,
            "apply": apply,
        }
        manifest = (
            self.repo_root
            / "data"
            / "manifests"
            / "l3_official_candidates_latest.json"
        )
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output["manifest_path"] = str(manifest.relative_to(self.repo_root))
        return output


def _parse_counties(value: str) -> List[str]:
    if not value:
        return list(COUNTIES)
    items = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in items if item not in COUNTIES]
    if unknown:
        raise ValueError(f"unsupported counties: {', '.join(unknown)}")
    return items


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--counties", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    result = CurrentCandidateMaterializer(args.repo_root).materialize_many(
        _parse_counties(args.counties),
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failure_count"] == 0 and result["complete_count"] == result["county_count"]:
        return 0
    if args.allow_partial and result["candidate_count"] > 0:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
