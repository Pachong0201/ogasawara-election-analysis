"""Verify current county issues from body-grounded research findings."""

from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from .county_knowledge import COUNTIES, county_research_questions
from .election_loader import load_jsonl, safe_component, write_jsonl
from .knowledge_builder import KnowledgePromotionBuilder
from .models import utc_now_iso

def _question(county: str) -> str:
    for row in county_research_questions(county):
        if row["topic"] == "key_issues":
            return row["question"]
    raise ValueError(county)

class KeyIssueVerifier:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.builder = KnowledgePromotionBuilder(self.repo_root)

    def _stage(self, county: str, leads: Iterable[Dict[str, Any]]) -> None:
        path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []
        merged = {}
        for row in existing + list(leads):
            key = str(row.get("lead_id") or row.get("url") or "").strip()
            if key:
                merged[key] = row
        write_jsonl(path, merged.values())

    @staticmethod
    def _lead(county: str, question: str, statement: str, cite: Dict[str, Any]):
        if str(cite.get("source_grade") or "").upper() != "C":
            return None
        publisher = str(cite.get("publisher_id") or "").strip()
        url = str(cite.get("url") or "").strip()
        quote = str(cite.get("quote") or "").strip()
        if not publisher or not url or len(quote) < 16:
            return None
        lead_id = "key-issue-" + hashlib.sha1(
            f"{county}|{publisher}|{url}|{statement}".encode("utf-8")
        ).hexdigest()[:20]
        return {
            "lead_id": lead_id, "county": county, "query": question,
            "research_questions": [question], "title": str(cite.get("title") or ""),
            "summary": statement[:700], "evidence": quote, "url": url,
            "source_id": f"web_{publisher}", "source_name": publisher,
            "source_grade": "C", "verification_status": "verified",
            "independence_key": publisher,
            "published_at": str(cite.get("page_date") or ""),
            "retrieved_at": utc_now_iso(),
        }

    def build_proposals(self, county: str, research_result: Dict[str, Any],
                        contradiction_checked: bool = False,
                        contradiction_note: str = "") -> Dict[str, Any]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        question = _question(county)
        proposals, rejected, staged = [], [], []
        for finding in research_result.get("findings") or []:
            if not isinstance(finding, dict) or str(finding.get("question") or "").strip() != question:
                continue
            statement = str(finding.get("statement") or "").strip()
            if not statement:
                continue
            by_source = {}
            for cite in finding.get("citations") or []:
                if not isinstance(cite, dict):
                    continue
                lead = self._lead(county, question, statement, cite)
                if lead:
                    by_source[lead["independence_key"]] = lead
            leads = list(by_source.values())
            staged.extend(leads)
            if len(leads) < 2:
                rejected.append({"statement": statement, "reason": "two independent C sources required"})
                continue
            dates = [str(x.get("published_at") or "")[:10] for x in leads if x.get("published_at")]
            date = max(dates) if dates else utc_now_iso()[:10]
            token = hashlib.sha1(f"{county}|{statement}|{date}".encode("utf-8")).hexdigest()[:20]
            proposals.append({
                "proposal_id": f"verify-key-issue-{token}", "county": county,
                "target_type": "current_issue",
                "research_questions": [question],
                "evidence_lead_ids": [x["lead_id"] for x in leads],
                "contradiction_check_completed": contradiction_checked,
                "contradictory_lead_ids": [],
                "contradiction_check_note": contradiction_note.strip() if contradiction_checked else "counter-evidence check pending",
                "scope_boundary": "Only records that independent body-read sources reported the issue at this time.",
                "target_record": {
                    "claim_id": f"key-issue-{token}", "claim_text": statement,
                    "claim_type": "other", "date": date, "time_scope": date,
                    "last_verified_at": date, "verification_status": "reported_by_media",
                    "source": leads[0]["source_name"], "source_grade": "C",
                    "region": county,
                    "uncertainty": {"level": "medium", "reason": "two independent body-read media sources", "competing_explanations": []},
                },
            })
        if staged:
            self._stage(county, staged)
        return {"county": county, "question": question, "proposal_count": len(proposals),
                "rejected_count": len(rejected), "proposals": proposals, "rejected": rejected}

    def run(self, county: str, research_result: Dict[str, Any], *,
            contradiction_checked: bool = False, contradiction_note: str = "",
            apply: bool = False) -> Dict[str, Any]:
        built = self.build_proposals(county, research_result, contradiction_checked, contradiction_note)
        decisions = []
        for proposal in built["proposals"]:
            decisions.append(self.builder.promote(
                proposal, dry_run=not apply, build_package=apply
            )["receipt"])
        return {**built, "apply": apply, "decisions": decisions}

def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--county", required=True)
    p.add_argument("--research-result", type=Path, required=True)
    p.add_argument("--contradiction-checked", action="store_true")
    p.add_argument("--contradiction-note", default="")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    result = KeyIssueVerifier(a.repo_root).run(
        a.county, json.loads(a.research_result.read_text(encoding="utf-8")),
        contradiction_checked=a.contradiction_checked,
        contradiction_note=a.contradiction_note, apply=a.apply
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    blocked = any(x.get("decision") in {"rejected", "requires_review"} for x in result["decisions"])
    return 3 if blocked or result["rejected_count"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
