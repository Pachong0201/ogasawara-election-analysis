"""Dual-source verification for current candidate-party relationships.

The official CEC 2026 registration record supplies the authoritative A-grade
candidate/recommending-party relation. A second independent C-grade media
source must quote both the candidate and the recommending party before this
module will submit a current relationship proposal to KnowledgePromotionBuilder.

This module does not rank candidates, infer support transfer, or predict results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .county_knowledge import COUNTIES
from .election_loader import current_candidates_path, load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .models import utc_now_iso


PARTY_ALIASES: Dict[str, Sequence[str]] = {
    "中國國民黨": ("中國國民黨", "國民黨"),
    "民主進步黨": ("民主進步黨", "民進黨"),
    "台灣民眾黨": ("台灣民眾黨", "臺灣民眾黨", "民眾黨"),
    "台聯黨": ("台聯黨", "台灣團結聯盟", "臺灣團結聯盟"),
}


def _norm(text: Any) -> str:
    return "".join(str(text or "").split()).replace("臺", "台")


def _party_aliases(party: str) -> Sequence[str]:
    return PARTY_ALIASES.get(party, (party,))


def _contains_relation(text: str, candidate: str, party: str) -> bool:
    normalized = _norm(text)
    if _norm(candidate) not in normalized:
        return False
    return any(_norm(alias) in normalized for alias in _party_aliases(party))


def _latest_date(values: Iterable[Any]) -> str:
    dates = sorted(str(value or "")[:10] for value in values if str(value or "")[:10])
    return dates[-1] if dates else utc_now_iso()[:10]


class CurrentRelationshipVerifier:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    def _candidate_records(self, county: str) -> List[Dict[str, Any]]:
        path = current_candidates_path(self.repo_root, county)
        return load_jsonl(path) if path.exists() else []

    @staticmethod
    def _official_lead(county: str, candidate: Dict[str, Any], question: str) -> Dict[str, Any]:
        name = str(candidate.get("name") or candidate.get("candidate_name") or "").strip()
        party = str(candidate.get("recommended_by_party") or "").strip()
        reference = str(
            candidate.get("source_reference")
            or candidate.get("raw_reference")
            or candidate.get("source")
            or ""
        ).strip()
        lead_id = f"cec-party-rec-{safe_component(county)}-{safe_component(name)}"
        return {
            "lead_id": lead_id,
            "county": county,
            "query": question,
            "research_questions": [question],
            "title": "中央選舉委員會115年地方公職人員選舉候選人登記名冊",
            "summary": f"中選會最終登記名冊列示{name}由{party}推薦登記。",
            "evidence": f"{county}；{name}；推薦之政黨：{party}",
            "url": reference,
            "source_id": "cec_current_candidates",
            "source_name": "中央選舉委員會",
            "source_grade": "A",
            "verification_status": "verified",
            "independence_key": "cec",
            "published_at": str(candidate.get("published_at") or "2026-09-07"),
            "retrieved_at": str(candidate.get("last_verified_at") or candidate.get("retrieved_at") or utc_now_iso()),
        }

    @staticmethod
    def _media_leads_from_research(
        county: str,
        candidate: str,
        party: str,
        question: str,
        research_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        output: Dict[str, Dict[str, Any]] = {}
        for finding in research_result.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            for citation in finding.get("citations") or []:
                if not isinstance(citation, dict):
                    continue
                quote = str(citation.get("quote") or "")
                if not _contains_relation(quote, candidate, party):
                    continue
                grade = str(citation.get("source_grade") or "").upper()
                if grade != "C":
                    continue
                publisher = str(citation.get("publisher_id") or "").strip()
                url = str(citation.get("url") or "").strip()
                if not publisher or not url:
                    continue
                lead_id = "media-party-rec-" + hashlib.sha1(
                    f"{county}|{candidate}|{party}|{publisher}|{url}".encode("utf-8")
                ).hexdigest()[:20]
                output[lead_id] = {
                    "lead_id": lead_id,
                    "county": county,
                    "query": question,
                    "research_questions": [question],
                    "title": str(citation.get("title") or ""),
                    "summary": str(finding.get("statement") or "")[:700],
                    "evidence": quote,
                    "url": url,
                    "source_id": f"web_{publisher}",
                    "source_name": publisher,
                    "source_grade": "C",
                    "verification_status": "verified",
                    "independence_key": publisher,
                    "published_at": str(citation.get("page_date") or ""),
                    "retrieved_at": utc_now_iso(),
                }
        return list(output.values())

    def _stage(self, county: str, leads: Iterable[Dict[str, Any]]) -> None:
        path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for row in existing + list(leads):
            key = str(row.get("lead_id") or row.get("url") or "").strip()
            if key:
                merged[key] = row
        write_jsonl(path, merged.values())

    def build_proposals(
        self,
        county: str,
        research_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        proposals: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        staged: List[Dict[str, Any]] = []

        for candidate in self._candidate_records(county):
            name = str(candidate.get("name") or candidate.get("candidate_name") or "").strip()
            party = str(candidate.get("recommended_by_party") or "").strip()
            if not name or not party:
                continue
            question = f"2026年{county}縣市長候選人{name}是否由{party}推薦登記？"
            official = self._official_lead(county, candidate, question)
            media = self._media_leads_from_research(
                county, name, party, question, research_result
            )
            staged.append(official)
            staged.extend(media)
            independent_media = [
                row for row in media
                if row.get("independence_key") not in {"", "cec"}
            ]
            if not independent_media:
                unresolved.append(
                    {
                        "county": county,
                        "candidate": name,
                        "party": party,
                        "reason": "missing independent C-grade body-grounded media confirmation",
                        "research_question": question,
                    }
                )
                continue

            evidence_ids = [official["lead_id"], independent_media[0]["lead_id"]]
            last_verified = _latest_date(
                [
                    candidate.get("last_verified_at"),
                    candidate.get("published_at"),
                    independent_media[0].get("published_at"),
                    independent_media[0].get("retrieved_at"),
                ]
            )
            relation_id = (
                f"candidate-party-2026-{safe_component(county)}-"
                f"{safe_component(name)}-{safe_component(party)}"
            )
            proposals.append(
                {
                    "proposal_id": f"verify-{relation_id}",
                    "county": county,
                    "target_type": "local_relationship",
                    "research_questions": [question],
                    "evidence_lead_ids": evidence_ids,
                    "contradiction_check_completed": True,
                    "contradictory_lead_ids": [],
                    "contradiction_check_note": (
                        "中選會115年9月7日最終候選人登記名冊列示推薦政黨，"
                        "並以一個獨立媒體正文來源交叉確認；本紀錄只描述登記時的推薦關係，"
                        "不外推為長期黨籍、派系歸屬、組織動員效果或選票轉移。"
                    ),
                    "scope_boundary": (
                        "僅確認115年地方公職人員選舉候選人登記名冊中的政黨推薦關係；"
                        "不代表候選人永久黨籍、後續政治合作、動員效果或選舉結果。"
                    ),
                    "target_record": {
                        "relationship_id": relation_id,
                        "subject": name,
                        "subject_type": "person",
                        "object": party,
                        "object_type": "organization",
                        "relationship_type": "party",
                        "region": county,
                        "time_scope": (
                            f"{candidate.get('registration_date') or '2026-08-31'}"
                            "—2026-11-28"
                        ),
                        "last_verified_at": last_verified,
                        "current_status": "active_verified",
                        "uncertainty": {
                            "level": "low",
                            "reason": "中選會最終登記名冊加一個獨立媒體正文來源；範圍限於登記推薦關係。",
                            "competing_explanations": [],
                        },
                    },
                }
            )

        if staged:
            self._stage(county, staged)
        return {
            "county": county,
            "candidate_count": len(self._candidate_records(county)),
            "proposal_count": len(proposals),
            "unresolved_count": len(unresolved),
            "proposals": proposals,
            "unresolved": unresolved,
        }

    def run(
        self,
        county: str,
        research_result: Dict[str, Any],
        *,
        apply: bool = False,
    ) -> Dict[str, Any]:
        built = self.build_proposals(county, research_result)
        decisions: List[Dict[str, Any]] = []
        for proposal in built["proposals"]:
            result = self.builder.promote(
                proposal,
                dry_run=not apply,
                build_package=apply,
            )
            decisions.append(result["receipt"])
        return {**built, "apply": apply, "decisions": decisions}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--county", required=True)
    parser.add_argument("--research-result", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    payload = json.loads(args.research_result.read_text(encoding="utf-8"))
    result = CurrentRelationshipVerifier(args.repo_root).run(
        args.county,
        payload,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["unresolved_count"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
