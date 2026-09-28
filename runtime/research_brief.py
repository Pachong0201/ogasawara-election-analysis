"""Build a compact research brief for the analyst writer.

The brief is evidence organization, not a political verdict.  It preserves the
important facts, historical structure, regional signals, hypotheses and
uncertainties while deliberately leaving final synthesis and prose structure to
the writer model.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


_REGION_SUFFIXES = ("區", "区", "鄉", "乡", "鎮", "镇", "市")
_REGION_NOISE = (
    "股市", "品牌", "很多市", "帶動", "带动", "南非市", "印度股市",
    "市場", "市场", "都市", "城市", "全市",
)


def _strings(values: Iterable[Any], limit: int = 12) -> List[str]:
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _region_name(value: Any) -> str:
    return str(value or "").split("|")[0].strip()


def _valid_region(name: str, *, jurisdiction: str, known: set[str]) -> bool:
    name = _region_name(name)
    if not name or name == jurisdiction:
        return False
    if name in known:
        return True
    if any(token in name for token in _REGION_NOISE):
        return False
    return len(name) <= 8 and name.endswith(_REGION_SUFFIXES) and bool(known and name in known)


class ResearchBriefBuilder:
    """Convert validated pipeline outputs into a writer-facing research brief."""

    def build(
        self,
        *,
        task: Dict[str, Any],
        historical_baseline: Dict[str, Any],
        metrics: Dict[str, List[Dict[str, Any]]],
        local_knowledge: Dict[str, Any],
        current_candidates: List[Dict[str, Any]],
        current_events: List[Dict[str, Any]],
        campaign_event_resolution: Dict[str, Any],
        campaign_state: Dict[str, Any],
        polls: List[Dict[str, Any]],
        research_result: Dict[str, Any],
        assessment: Dict[str, Any],
        unknowns: List[str],
        warnings: List[str],
        sources: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        jurisdiction = str(task.get("jurisdiction") or local_knowledge.get("county") or "")
        resolved = [
            row for row in (campaign_event_resolution.get("events") or current_events or [])
            if isinstance(row, dict)
        ][:24]

        known_regions = {
            _region_name(row.get("region"))
            for rows in metrics.values()
            for row in (rows or [])
            if isinstance(row, dict) and _region_name(row.get("region"))
        }
        known_regions.update(
            _region_name(row.get("region") or row.get("location"))
            for key in ("relationships", "historical_claims", "candidates", "issues")
            for row in (local_knowledge.get(key) or [])
            if isinstance(row, dict)
        )
        known_regions.discard("")
        known_regions.discard(jurisdiction)

        core_facts: List[Dict[str, Any]] = []
        for row in current_candidates[:8]:
            if not isinstance(row, dict):
                continue
            core_facts.append({
                "kind": "candidate_record",
                "statement": str(
                    row.get("candidate_name") or row.get("name") or row.get("姓名") or ""
                ),
                "verification_status": row.get("verification_status") or "recorded",
                "source": row.get("source_id") or row.get("source"),
            })
        for row in resolved:
            status = str(row.get("verification_status") or "")
            if status not in {"official_verified", "verified", "corroborated_media", "single_source_media"}:
                continue
            core_facts.append({
                "kind": "campaign_event",
                "date": row.get("date") or row.get("page_date"),
                "event_type": row.get("event_type") or row.get("claim_type"),
                "actors": _strings(row.get("candidate_entities") or row.get("actors") or [], 8),
                "locations": [
                    loc for loc in _strings(row.get("locations") or [], 8)
                    if _valid_region(loc, jurisdiction=jurisdiction, known=known_regions)
                ],
                "statement": str(
                    row.get("summary") or row.get("evidence_excerpt")
                    or row.get("claim_value") or row.get("subject") or ""
                )[:700],
                "verification_status": status,
                "evidence_references": _strings(
                    row.get("source_urls") or row.get("urls")
                    or ([row.get("url")] if row.get("url") else []), 6
                ),
            })
            if len(core_facts) >= 16:
                break

        historical_structure: List[Dict[str, Any]] = []
        for metric_name, rows in metrics.items():
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                region = _region_name(row.get("region"))
                if region and not _valid_region(region, jurisdiction=jurisdiction, known=known_regions):
                    continue
                if not row.get("local_explanation_required") and metric_name != "electoral_swing":
                    continue
                historical_structure.append({
                    "metric": metric_name,
                    "region": region or jurisdiction,
                    "party": row.get("party"),
                    "candidate": row.get("candidate"),
                    "periods": row.get("periods") or row.get("year"),
                    "value": row.get("value"),
                    "baseline_method": row.get("baseline_method") or row.get("method"),
                    "interpretation_boundary": (
                        "This is a structural signal only; it is not personal vote, "
                        "vote transfer, faction vote, or a causal effect."
                    ),
                })
                if len(historical_structure) >= 18:
                    break
            if len(historical_structure) >= 18:
                break

        regional_patterns: List[Dict[str, Any]] = []
        assessment_regions = [
            row for row in (assessment.get("regional_assessments") or [])
            if isinstance(row, dict)
        ]
        for row in assessment_regions:
            region = _region_name(row.get("region"))
            if not _valid_region(region, jurisdiction=jurisdiction, known=known_regions):
                continue
            regional_patterns.append({
                "region": region,
                "historical_signals": row.get("historical_structure") or row.get("cross_level_signals") or [],
                "current_events": row.get("current_events") or [],
                "organization_signals": row.get("organization_signals") or [],
                "issue_signals": row.get("issue_signals") or [],
                "evidence_gaps": row.get("evidence_gaps") or [],
            })
            if len(regional_patterns) >= 12:
                break

        organization_changes = []
        for row in (assessment.get("organization_networks") or [])[:12]:
            if not isinstance(row, dict):
                continue
            region = _region_name(row.get("location"))
            if region and not _valid_region(region, jurisdiction=jurisdiction, known=known_regions):
                region = ""
            organization_changes.append({
                "relation_type": row.get("relation_type"),
                "actors": row.get("actors") or row.get("candidate_entities") or [],
                "organizations": row.get("organizations") or [],
                "region": region,
                "date": row.get("last_observed_at") or row.get("date"),
                "confidence": row.get("confidence"),
                "evidence_count": row.get("evidence_count"),
                "independent_source_count": row.get("independent_source_count"),
                "evidence_references": row.get("evidence_references") or [],
                "boundary": row.get("analytical_boundary"),
            })

        hypotheses: List[Dict[str, Any]] = []
        for row in regional_patterns:
            if row["historical_signals"] and row["current_events"]:
                hypotheses.append({
                    "question": f"{row['region']}的当前竞选活动是否与既有历史结构出现新的连接？",
                    "region": row["region"],
                    "supporting_evidence": [],
                    "counter_evidence": [],
                    "status": "to_be_interpreted_by_writer",
                })
        for row in organization_changes:
            if row.get("relation_type") not in (None, "", "single_visit"):
                hypotheses.append({
                    "question": "已观察到的组织协作是否具有持续性，以及它在地方层级意味着什么？",
                    "region": row.get("region"),
                    "supporting_evidence": row.get("evidence_references") or [],
                    "counter_evidence": [row.get("boundary")] if row.get("boundary") else [],
                    "status": "to_be_interpreted_by_writer",
                })
        for item in (assessment.get("follow_up_questions") or [])[:6]:
            if isinstance(item, dict) and item.get("question"):
                hypotheses.append({
                    "question": item["question"],
                    "region": item.get("region"),
                    "supporting_evidence": [],
                    "counter_evidence": [],
                    "status": item.get("status") or "unresolved",
                })

        research_findings = []
        for row in (research_result.get("findings") or [])[:8]:
            if not isinstance(row, dict):
                continue
            research_findings.append({
                "question": row.get("question"),
                "statement": str(row.get("statement") or "")[:800],
                "verification_status": row.get("verification_status") or "body_grounded_unverified",
                "citations": [
                    {
                        "url": c.get("url"),
                        "title": c.get("title"),
                        "source_grade": c.get("source_grade"),
                        "quote": str(c.get("quote") or "")[:300],
                    }
                    for c in (row.get("citations") or [])[:4]
                    if isinstance(c, dict)
                ],
            })

        poll_context = {
            "records": polls[:8],
            "same_series_changes": campaign_state.get("same_series_poll_changes") or [],
            "rule": (
                "Use polls only for calibration. Discuss change only when series are methodologically "
                "comparable; do not treat differences within an applicable margin of error as a clear lead."
            ),
        }

        return {
            "version": 1,
            "purpose": "writer_research_brief",
            "task": task,
            "as_of": campaign_state.get("as_of"),
            "core_facts": core_facts[:16],
            "historical_structure": historical_structure,
            "regional_patterns": regional_patterns,
            "candidate_resources": {
                "candidates": current_candidates[:8],
                "local_relationships": (local_knowledge.get("relationships") or [])[:16],
            },
            "organization_changes": organization_changes,
            "issue_dynamics": (assessment.get("issue_dynamics") or assessment.get("issue_signals") or [])[:12],
            "third_party": (assessment.get("third_party_signals") or [])[:8],
            "poll_context": poll_context,
            "research_findings": research_findings,
            "key_hypotheses": hypotheses[:8],
            "important_unknowns": _strings(
                list(unknowns or [])
                + list(research_result.get("unresolved") or [])
                + [str(x) for x in warnings or [] if "stale" in str(x).lower() or "conflict" in str(x).lower()],
                12,
            ),
            "recommended_focus": [
                str(x.get("question") or "")
                for x in (assessment.get("follow_up_questions") or [])[:6]
                if isinstance(x, dict) and x.get("question")
            ],
            "sources": sources[:24],
            "writer_contract": {
                "rule": (
                    "The brief organizes evidence; the writer must synthesize the argument and may choose "
                    "its own structure. Do not expose internal field names or debug state in the reader report."
                ),
                "political_boundary": (
                    "Do not rank candidates, recommend a political choice, estimate win probability, "
                    "or convert structural signals into vote-transfer claims."
                ),
            },
        }
