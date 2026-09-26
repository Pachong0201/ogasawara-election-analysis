"""Batch body-grounded key-issue research for all 22 county packages.

The runner reuses the automatic Web Search worker and KeyIssueVerifier.  It
performs a second, explicit update/correction/reversal search before any issue
is submitted to KnowledgePromotionBuilder.  Findings that lack two independent
C-grade body citations, a completed counter-search, or the normal promotion
gates remain unresolved and are never written to L3.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .auto_research import ResearchCoordinator, ResearchWorker, public_result
from .county_knowledge import COUNTIES, county_research_questions
from .key_issue_verifier import KeyIssueVerifier
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
COUNTER_SEARCH_OK = {"completed", "insufficient_evidence"}


def _key_issue_question(county: str) -> str:
    return next(
        row["question"]
        for row in county_research_questions(county)
        if row["topic"] == "key_issues"
    )


class L3KeyIssueResearchBatch:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.worker = ResearchWorker(self.repo_root)
        self.coordinator = ResearchCoordinator(
            self.repo_root,
            self.worker.store,
            self.worker.config,
            worker_factory=lambda: self.worker,
        )

    def _research_sync(self, county: str, questions: List[str]) -> Dict[str, Any]:
        task = ElectionTask("county_mayor", 2026, county)
        result = self.coordinator.research(task, questions, [])
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

    @staticmethod
    def _bounded_primary(
        question: str,
        result: Dict[str, Any],
        max_findings: int,
    ) -> Dict[str, Any]:
        output = dict(result)
        output["findings"] = [
            row
            for row in (result.get("findings") or [])
            if isinstance(row, dict) and str(row.get("question") or "").strip() == question
        ][:max_findings]
        return output

    @staticmethod
    def _counter_questions(county: str, findings: List[Dict[str, Any]]) -> List[str]:
        return [
            (
                f"反证核验：截至目前，关于{county}的下述事项是否已有撤回、澄清、"
                f"停止、完成、裁判、政策变更或其他状态更新？事项："
                f"{str(row.get('statement') or '').strip()}"
            )
            for row in findings
            if str(row.get("statement") or "").strip()
        ]

    def run_county(
        self,
        county: str,
        *,
        apply: bool = False,
        max_findings: int = 3,
    ) -> Dict[str, Any]:
        question = _key_issue_question(county)
        primary = self._bounded_primary(
            question,
            self._research_sync(county, [question]),
            max_findings,
        )
        findings = list(primary.get("findings") or [])
        if not findings:
            return {
                "county": county,
                "primary_status": primary.get("status"),
                "counter_status": "not_needed",
                "finding_count": 0,
                "proposal_count": 0,
                "promoted_count": 0,
                "rejected_count": 0,
                "last_error": primary.get("last_error") or "",
            }

        counter_questions = self._counter_questions(county, findings)
        counter = self._research_sync(county, counter_questions)
        counter_updates = list(counter.get("findings") or [])
        # A grounded counter-search finding may describe a correction, changed
        # status or withdrawal.  Do not guess whether it is harmless: block
        # automatic promotion and leave the primary finding for review.
        counter_checked = (
            counter.get("status") in COUNTER_SEARCH_OK and not counter_updates
        )
        counter_note = (
            "已通过自动研究链逐项检索撤回、澄清、停止、完成、裁判及政策变更；"
            f"counter_status={counter.get('status')}; "
            f"search_count={int(counter.get('search_count') or 0)}; "
            f"body_count={int(counter.get('body_count') or 0)}; "
            f"grounded_update_findings={len(counter_updates)}; "
            f"checked_at={utc_now_iso()}."
        )
        verified = KeyIssueVerifier(self.repo_root).run(
            county,
            primary,
            contradiction_checked=counter_checked,
            contradiction_note=counter_note,
            apply=apply,
        )
        accepted = {"promoted", "unchanged"} if apply else {"dry_run_pass"}
        promoted = sum(
            1 for receipt in (verified.get("decisions") or [])
            if receipt.get("decision") in accepted
        )
        return {
            "county": county,
            "primary_status": primary.get("status"),
            "counter_status": counter.get("status"),
            "primary_search_count": int(primary.get("search_count") or 0),
            "primary_body_count": int(primary.get("body_count") or 0),
            "counter_search_count": int(counter.get("search_count") or 0),
            "counter_body_count": int(counter.get("body_count") or 0),
            "finding_count": len(findings),
            "proposal_count": int(verified.get("proposal_count") or 0),
            "promoted_count": promoted,
            "rejected_count": int(verified.get("rejected_count") or 0),
            "counter_check_completed": counter_checked,
            "counter_update_finding_count": len(counter_updates),
            "last_error": primary.get("last_error") or counter.get("last_error") or "",
        }

    def run_many(
        self,
        counties: Optional[Iterable[str]] = None,
        *,
        apply: bool = False,
        max_findings: int = 3,
        manifest_name: str = "l3_key_issue_research_latest.json",
    ) -> Dict[str, Any]:
        selected = list(counties or COUNTIES)
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for county in selected:
            try:
                results.append(
                    self.run_county(county, apply=apply, max_findings=max_findings)
                )
            except Exception as exc:
                failures.append({"county": county, "error": f"{type(exc).__name__}: {exc}"})
        output = {
            "generated_at": utc_now_iso(),
            "county_count": len(selected),
            "covered_county_count": sum(
                1 for row in results if int(row.get("promoted_count") or 0) > 0
            ),
            "finding_count": sum(int(row.get("finding_count") or 0) for row in results),
            "proposal_count": sum(int(row.get("proposal_count") or 0) for row in results),
            "promoted_count": sum(int(row.get("promoted_count") or 0) for row in results),
            "rejected_count": sum(int(row.get("rejected_count") or 0) for row in results),
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
    parser.add_argument("--max-findings", type=int, default=3)
    parser.add_argument("--manifest-name", default="l3_key_issue_research_latest.json")
    args = parser.parse_args(argv)
    result = L3KeyIssueResearchBatch(args.repo_root).run_many(
        _parse_counties(args.counties),
        apply=args.apply,
        max_findings=max(1, min(args.max_findings, 5)),
        manifest_name=args.manifest_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failure_count"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
