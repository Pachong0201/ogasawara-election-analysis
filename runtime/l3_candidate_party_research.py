"""Batch current candidate-party relationship research for L3.

This runner uses the existing bounded Web Search + body-reading research system.
It only promotes relationships when CurrentRelationshipVerifier obtains both:
1) the CEC A-grade registration fact, and
2) an independent C-grade media body quote naming the candidate and party.

Search results that fail the gate remain unresolved; they are never promoted.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .auto_research import ResearchCoordinator, ResearchWorker, public_result
from .county_knowledge import COUNTIES
from .current_relationship_verifier import CurrentRelationshipVerifier
from .models import ElectionTask, utc_now_iso


TERMINAL = {
    "completed",
    "insufficient_evidence",
    "partial",
    "failed",
    "configuration_error",
    "disabled",
    "historical_replay",
}


class L3CandidatePartyResearchBatch:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.worker = ResearchWorker(self.repo_root)
        self.coordinator = ResearchCoordinator(
            self.repo_root,
            self.worker.store,
            self.worker.config,
            worker_factory=lambda: self.worker,
        )

    def _research_sync(
        self,
        county: str,
        questions: List[str],
        candidate_names: List[str],
    ) -> Dict[str, Any]:
        task = ElectionTask("county_mayor", 2026, county)
        result = self.coordinator.research(task, questions, candidate_names)
        job_id = result.get("job_id")
        if not job_id or result.get("status") in TERMINAL:
            return result

        deadline = time.monotonic() + max(60, float(self.worker.config.job_seconds) + 30)
        while time.monotonic() < deadline:
            state = public_result(self.worker.store.result(job_id))
            if state.get("status") in TERMINAL:
                state["cache_ttl_seconds"] = self.worker.config.cache_seconds
                return state
            time.sleep(2)
        state = public_result(self.worker.store.result(job_id))
        state.setdefault("status", "partial")
        state["wait_timeout"] = True
        return state

    def run_county(self, county: str, *, apply: bool = False) -> Dict[str, Any]:
        verifier = CurrentRelationshipVerifier(self.repo_root)
        candidates = verifier._candidate_records(county)
        eligible = [
            row
            for row in candidates
            if str(row.get("recommended_by_party") or "").strip()
        ]
        questions = [
            (
                f"2026年{county}縣市長候選人"
                f"{row.get('name') or row.get('candidate_name')}"
                f"是否由{row.get('recommended_by_party')}推薦登記？"
            )
            for row in eligible
        ]
        if not questions:
            return {
                "county": county,
                "candidate_count": len(candidates),
                "eligible_candidate_count": 0,
                "research_status": "not_needed",
                "proposal_count": 0,
                "promoted_count": 0,
                "unresolved_count": 0,
            }

        research = self._research_sync(
            county,
            questions,
            [
                str(row.get("name") or row.get("candidate_name") or "")
                for row in eligible
            ],
        )
        verified = verifier.run(county, research, apply=apply)
        promoted = sum(
            1
            for receipt in verified.get("decisions") or []
            if receipt.get("decision") in {"promoted", "unchanged"}
        )
        return {
            "county": county,
            "candidate_count": len(candidates),
            "eligible_candidate_count": len(eligible),
            "research_status": research.get("status"),
            "search_count": int(research.get("search_count") or 0),
            "body_count": int(research.get("body_count") or 0),
            "finding_count": len(research.get("findings") or []),
            "proposal_count": int(verified.get("proposal_count") or 0),
            "promoted_count": promoted,
            "unresolved_count": int(verified.get("unresolved_count") or 0),
            "unresolved": verified.get("unresolved") or [],
            "last_error": research.get("last_error") or "",
        }

    def run_many(
        self,
        counties: Optional[Iterable[str]] = None,
        *,
        apply: bool = False,
        manifest_name: str = "l3_candidate_party_research_latest.json",
    ) -> Dict[str, Any]:
        selected = list(counties or COUNTIES)
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for county in selected:
            try:
                results.append(self.run_county(county, apply=apply))
            except Exception as exc:
                failures.append({
                    "county": county,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        output = {
            "generated_at": utc_now_iso(),
            "county_count": len(selected),
            "eligible_candidate_count": sum(
                int(row.get("eligible_candidate_count") or 0) for row in results
            ),
            "proposal_count": sum(int(row.get("proposal_count") or 0) for row in results),
            "promoted_count": sum(int(row.get("promoted_count") or 0) for row in results),
            "unresolved_count": sum(int(row.get("unresolved_count") or 0) for row in results),
            "failure_count": len(failures),
            "results": results,
            "failures": failures,
            "apply": apply,
        }
        path = self.repo_root / "data" / "manifests" / manifest_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output["manifest_path"] = str(path.relative_to(self.repo_root))
        return output


def _parse_counties(value: str) -> List[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    selected = items or list(COUNTIES)
    unknown = [item for item in selected if item not in COUNTIES]
    if unknown:
        raise ValueError(f"unsupported counties: {', '.join(unknown)}")
    return selected


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--counties", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest-name", default="l3_candidate_party_research_latest.json")
    args = parser.parse_args(argv)
    result = L3CandidatePartyResearchBatch(args.repo_root).run_many(
        _parse_counties(args.counties),
        apply=args.apply,
        manifest_name=args.manifest_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failure_count"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
