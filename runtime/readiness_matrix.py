"""Offline readiness matrix for all 22 counties.

The matrix answers four separate acceptance questions per county:

1. does row-level data exist (historical periods, geography, candidates)?
2. is the source/promotion evidence qualified for the runtime?
3. is the record applicable to the requested ``as_of`` date?
4. did the runtime actually read it (cache vs promoted knowledge fallback)?

It never relaxes the gate to make a county READY.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .county_knowledge import COUNTIES
from .data_readiness import DataReadinessGate
from .models import ElectionTask, parse_date, utc_now_iso


class ReadinessMatrix:
    def __init__(
        self,
        repo_root: Optional[Path] = None,
        runtime_config_path: Optional[Path] = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.runtime_config_path = runtime_config_path
        self.gate = DataReadinessGate(self.repo_root, runtime_config_path=runtime_config_path)
        self.gate.requirements = self.gate._requirements_for_task_type("county_mayor")

    def run(
        self,
        *,
        election_type: str = "county_mayor",
        target_year: int = 2026,
        as_of: Optional[str] = None,
        counties: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        as_of_date = parse_date(as_of)
        self.gate.requirements = self.gate._requirements_for_task_type(election_type)
        selected = list(counties or COUNTIES)
        rows: List[Dict[str, Any]] = []
        for county in selected:
            task = ElectionTask(
                election_type=election_type,
                target_year=int(target_year),
                jurisdiction=county,
            )
            report = self.gate.check(task, now=as_of_date)
            candidate_info = dict(report.required.get("current_candidate_list") or {})
            profile_info = dict(report.required.get("candidate_profiles") or {})
            poll_info = dict(report.required.get("current_polls") or {})
            local_info = dict(report.required.get("local_knowledge") or {})
            source_counts = dict(candidate_info.get("candidate_source_counts") or {})
            hard_items = [
                key
                for key, item in report.required.items()
                if str(item.get("level")) == "hard"
                and item.get("applicable", True) is not False
                and not item.get("satisfied", True)
            ]
            rows.append(
                {
                    "county": county,
                    "status": report.status,
                    "hard_unsatisfied": hard_items,
                    "historical_years": report.available.get("historical_same_type", {}).get(
                        "found_years", []
                    ),
                    "current_candidate_list": {
                        "satisfied": bool(candidate_info.get("satisfied")),
                        "count": int(candidate_info.get("count") or 0),
                        "verified_count": int(candidate_info.get("verified_count") or 0),
                        "registered_count": int(candidate_info.get("registered_count") or 0),
                        "registration_confirmed": bool(
                            candidate_info.get("registration_confirmed")
                        ),
                        "source_counts": source_counts,
                        "rejection_reasons": dict(
                            candidate_info.get("rejection_reasons") or {}
                        ),
                        "runtime_read_promoted_knowledge": bool(
                            source_counts.get("knowledge_local")
                        ),
                    },
                    "candidate_profiles": {
                        "satisfied": bool(profile_info.get("satisfied")),
                        "count": int(profile_info.get("count") or 0),
                    },
                    "current_polls": {
                        "count": int(poll_info.get("count") or 0),
                        "fresh_count": int(poll_info.get("fresh_count") or 0),
                    },
                    "local_knowledge": {
                        "satisfied": bool(local_info.get("satisfied")),
                        "count": int(local_info.get("count") or 0),
                    },
                    "missing": list(report.missing),
                    "warnings": [
                        warning
                        for warning in report.warnings
                        if "candidate" in warning or "knowledge" in warning
                    ],
                }
            )
        summary = {
            "county_count": len(rows),
            "ready": sum(1 for row in rows if row["status"] == "READY"),
            "partial": sum(1 for row in rows if row["status"] == "PARTIAL"),
            "insufficient": sum(1 for row in rows if row["status"] == "INSUFFICIENT"),
            "current_candidates_satisfied": sum(
                1 for row in rows if row["current_candidate_list"]["satisfied"]
            ),
            "runtime_read_promoted_knowledge": sum(
                1
                for row in rows
                if row["current_candidate_list"]["runtime_read_promoted_knowledge"]
            ),
            "registration_confirmed": sum(
                1 for row in rows if row["current_candidate_list"]["registration_confirmed"]
            ),
            "cache_empty_but_knowledge_usable": [
                row["county"]
                for row in rows
                if row["current_candidate_list"]["satisfied"]
                and row["current_candidate_list"]["runtime_read_promoted_knowledge"]
                and not row["current_candidate_list"]["source_counts"].get("cache")
            ],
        }
        return {
            "version": "1.4.0",
            "generated_at": utc_now_iso(),
            "mode": "offline",
            "election_type": election_type,
            "target_year": int(target_year),
            "as_of": as_of or "",
            "summary": summary,
            "counties": rows,
            "interpretation_boundary": (
                "upstream官方事實、候選人登記或已晉升地方知識被讀取，不代表當選機率、"
                "支持度、動員能力或選舉結果；缺口以 unknown 保留。"
            ),
        }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--type", default="county_mayor")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--as-of", default="")
    parser.add_argument("--counties", default="")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    counties = (
        [item.strip() for item in args.counties.split(",") if item.strip()]
        if args.counties
        else None
    )
    matrix = ReadinessMatrix(args.repo_root).run(
        election_type=args.type,
        target_year=args.year,
        as_of=args.as_of or None,
        counties=counties,
    )
    text = json.dumps(matrix, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        matrix["output_path"] = str(args.output)
        text = json.dumps(matrix, ensure_ascii=False, indent=2)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
