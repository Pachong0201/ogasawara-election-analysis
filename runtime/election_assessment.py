"""Neutral, bounded assessment layer between evidence and report prose."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

ORG_TYPES = {"endorsement", "party_cooperation", "alliance", "alliance_break", "campaign_org", "campaign_headquarters"}
ISSUE_TYPES = {"debate", "policy", "major_issue", "judicial_event", "controversy"}
VERIFIED = {"verified", "official_verified"}
CORROBORATED = {"corroborated_media"}
ORG_LEVELS = (
    "single_visit", "public_endorsement", "support_group", "joint_campaign_event",
    "joint_headquarters", "formal_campaign_role", "repeated_coordination",
    "verified_grassroots_coordination",
)


def _strings(values: Iterable[Any], limit: int = 24) -> List[str]:
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


def _refs(row: Dict[str, Any]) -> List[str]:
    return _strings(row.get("evidence_references") or row.get("source_urls") or row.get("urls") or ([row.get("url")] if row.get("url") else []))


def _confidence(status: str) -> str:
    return "high" if status in VERIFIED else "medium" if status in CORROBORATED else "low"


def _region(row: Dict[str, Any], fallback: str = "") -> str:
    locations = row.get("locations") or []
    if isinstance(locations, str):
        locations = [locations]
    return str(row.get("region") or row.get("jurisdiction") or (locations[0] if locations else fallback) or "").strip()


class ElectionAssessmentBuilder:
    """Build v2 while retaining all v1 compatibility fields."""

    @staticmethod
    def _event(row: Dict[str, Any], index: int) -> Dict[str, Any]:
        status = str(row.get("verification_status") or "unknown")
        return {
            "kind": "campaign_event",
            "event_id": str(row.get("event_id") or row.get("record_id") or f"event-{index + 1}"),
            "date": row.get("date") or row.get("page_date"),
            "event_type": str(row.get("event_type") or row.get("claim_type") or "campaign_update"),
            "claim_class": "verified_fact" if status in VERIFIED else "corroborated_report" if status in CORROBORATED else "reported_event",
            "verification_status": status,
            "confidence": _confidence(status),
            "candidate_entities": _strings(row.get("candidate_entities") or row.get("actors") or []),
            "organizations": _strings(row.get("organizations") or row.get("organization_entities") or []),
            "locations": _strings(row.get("locations") or ([row.get("region")] if row.get("region") else [])),
            "statement": str(row.get("summary") or row.get("evidence_excerpt") or row.get("claim_value") or row.get("subject") or "")[:700],
            "evidence_references": _refs(row),
        }

    @staticmethod
    def _organization(raw: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        relation = str(raw.get("relation_type") or "")
        if relation not in ORG_LEVELS:
            relation = {"endorsement": "public_endorsement", "campaign_headquarters": "joint_headquarters", "campaign_org": "support_group"}.get(event["event_type"], "joint_campaign_event")
        refs = event["evidence_references"]
        independent = int(raw.get("independent_source_count") or len(set(refs)))
        confidence = event["confidence"]
        if relation == "single_visit" or independent < 2:
            confidence = "low"
        if relation == "verified_grassroots_coordination" and (independent < 2 or event["verification_status"] not in VERIFIED):
            relation, confidence = "repeated_coordination", "medium" if independent >= 2 else "low"
        return {
            **event, "relation_type": relation, "actors": event["candidate_entities"],
            "location": event["locations"][0] if event["locations"] else "unknown",
            "start_date": raw.get("start_date") or event.get("date"),
            "last_observed_at": raw.get("last_observed_at") or event.get("date"),
            "evidence_count": int(raw.get("evidence_count") or len(refs) or 1),
            "independent_source_count": independent, "confidence": confidence,
            "analytical_boundary": "Observed coordination does not establish grassroots mobilization, supporter preference, or vote effect.",
        }

    @staticmethod
    def _comparisons(metrics: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        kinds = {
            "candidate_residuals": ("county_mayor", "president"),
            "split_ticket": ("regional_legislator", "president"),
            "electoral_swing": ("county_mayor", "county_mayor"),
        }
        out = []
        for metric, (source, target) in kinds.items():
            for row in metrics.get(metric) or []:
                if not isinstance(row, dict):
                    continue
                out.append({
                    "comparison_type": metric, "source_election": source, "target_election": target,
                    "region": row.get("region"), "observed_difference": row.get("value"),
                    "baseline_method": row.get("baseline_method") or row.get("method") or "recorded metric baseline",
                    "warning": "Cross-election difference is not transferred votes, personal votes, faction votes, or a causal effect.",
                    "local_explanation_required": bool(row.get("local_explanation_required")),
                    "evidence_references": _strings(row.get("evidence_references") or []),
                })
                if len(out) >= 16:
                    return out
        return out

    @staticmethod
    def _research_pack(result: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
        out, ids = [], {}
        for row in (result.get("evidence_pack") or [])[:12]:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            url = str(row["url"])
            eid = "web-" + hashlib.sha1(url.encode()).hexdigest()[:12]
            ids[url] = eid
            out.append({
                "evidence_id": eid, "url": url, "title": row.get("title"), "source_name": row.get("source_name"),
                "source_kind": row.get("source_kind"), "source_grade": row.get("source_grade"),
                "publisher_id": row.get("publisher_id"), "independence_key": row.get("independence_key"),
                "page_date": row.get("page_date") or row.get("published_at"),
                "excerpt": str(row.get("content") or "")[:1400], "content_truncated": bool(row.get("content_truncated")),
            })
        return out, ids

    @staticmethod
    def _followups(regions, organizations, comparisons, result):
        answered = {str(x.get("question") or "") for x in result.get("findings") or [] if isinstance(x, dict)}
        candidates = []
        for item in organizations:
            if item["confidence"] == "low":
                candidates.append((item["location"], item["actors"], item["relation_type"], "high", ["官方竞选组织公告", "两家独立媒体正文", "连续活动记录"]))
        for item in comparisons:
            if item["local_explanation_required"]:
                candidates.append((item.get("region") or "unknown", [], "跨层级差异的地方机制与替代解释", "medium", ["官方分区得票", "地方研究", "可核验组织关系"]))
        if not candidates and regions:
            candidates.append((regions[0], [], "当前组织互动与议题变化", "medium", ["官方公告", "独立媒体正文"]))
        out = []
        for index, (region, actors, relation, priority, evidence_types) in enumerate(candidates[:6]):
            question = f"{region}的{'、'.join(actors) or '相关人物与组织'}在截至日前30日是否有可核验的{relation}？"
            out.append({
                "question_id": f"follow-up-{index + 1}", "question": question, "region": region,
                "actors": actors, "time_window": "as_of 前30日", "relationship_to_verify": relation,
                "preferred_evidence_types": evidence_types, "priority": priority,
                "status": "resolved" if question in answered else "unresolved",
            })
        return out

    def build(
        self, *, campaign_state, current_events, campaign_event_resolution, metrics, polls,
        research_result, unknowns, warnings, local_knowledge: Optional[Dict[str, Any]] = None,
        historical_baseline: Optional[Dict[str, Any]] = None,
        current_candidates: Optional[List[Dict[str, Any]]] = None,
        task: Optional[Dict[str, Any]] = None, draft_assessment: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        local, historical, task = local_knowledge or {}, historical_baseline or {}, task or {}
        raw_events = [x for x in (campaign_event_resolution.get("events") or current_events or []) if isinstance(x, dict)][:24]
        events, orgs, issues, review = [], [], [], []
        locations: Counter[str] = Counter()
        by_region = defaultdict(list)
        for index, raw in enumerate(raw_events):
            item = self._event(raw, index)
            events.append(item)
            for location in item["locations"]:
                locations[location] += 1
                by_region[location].append(item)
            if item["event_type"] in ORG_TYPES:
                orgs.append(self._organization(raw, item))
            if item["event_type"] in ISSUE_TYPES:
                issues.append(item)
            if item["verification_status"] == "requires_review":
                review.append(item)

        pack, url_ids = self._research_pack(research_result)
        findings = [x for x in research_result.get("findings") or [] if isinstance(x, dict)][:12]
        for index, finding in enumerate(findings):
            citations = [x for x in finding.get("citations") or [] if isinstance(x, dict) and x.get("url")]
            independent = {str(x.get("independence_key") or x.get("publisher_id") or "") for x in citations} - {""}
            events.append({
                "kind": "research_finding", "finding_id": f"research-{index + 1}", "claim_class": "analytical_input",
                "verification_status": str(finding.get("verification_status") or "body_grounded_unverified"),
                "confidence": "medium" if len(independent) >= 2 else "low", "question": finding.get("question"),
                "statement": str(finding.get("statement") or "")[:700],
                "evidence_references": [url_ids.get(str(x["url"]), str(x["url"])) for x in citations],
            })

        historical_signals = []
        for name, rows in metrics.items():
            for row in rows or []:
                if isinstance(row, dict) and row.get("local_explanation_required"):
                    historical_signals.append({
                        "metric": name, "region": row.get("region"), "party": row.get("party"),
                        "candidate": row.get("candidate"), "periods": row.get("periods") or row.get("year"),
                        "signal": row.get("signal") or row.get("status"), "value": row.get("value"),
                        "baseline": row.get("baseline"),
                        "rule": "Historical signal only; no direct extrapolation to current support or outcome.",
                    })
                    if len(historical_signals) >= 12:
                        break
        comparisons = self._comparisons(metrics)
        local_regions = defaultdict(lambda: defaultdict(list))
        for kind in ("historical_claims", "relationships", "candidates", "issues"):
            for row in local.get(kind) or []:
                if isinstance(row, dict):
                    local_regions[_region(row, str(local.get("county") or "unknown"))][kind].append(row)
        regions = sorted({*locations.keys(), *local_regions.keys(), *(str(x.get("region") or "").split("|")[0] for x in historical_signals)} - {""})[:16]
        if not regions and task.get("jurisdiction"):
            regions = [str(task["jurisdiction"])]

        third_party = []
        for item in orgs:
            if item["event_type"] == "party_cooperation":
                third_party.append({
                    "party_cooperation": item, "organization_coordination": item if item["organizations"] else None,
                    "supporter_preference_poll": None, "actual_vote_transfer_unknown": True,
                    "evidence_references": item["evidence_references"],
                })
        followups = self._followups(regions, orgs, comparisons, research_result)
        regional = []
        for region in regions:
            region_events = by_region.get(region, [])
            local_rows = local_regions.get(region, {})
            gaps = []
            if not local_rows.get("relationships"):
                gaps.append("no verified local relationship record for this region")
            if not region_events:
                gaps.append("no current event evidence; coverage gap does not prove no change")
            regional.append({
                "region": region,
                "historical_structure": [x for x in historical_signals if str(x.get("region") or "").split("|")[0] == region][:4],
                "current_events": region_events[:6], "organization_signals": [x for x in orgs if x["location"] == region][:4],
                "issue_signals": [x for x in issues if region in x["locations"]][:4], "third_party_signals": third_party[:3],
                "cross_level_signals": [x for x in comparisons if str(x.get("region") or "").split("|")[0] == region][:4],
                "structural_changes": [],
                "alternative_explanations": ["activity frequency may reflect scheduling or media coverage rather than durable change"],
                "evidence_gaps": gaps, "follow_up_questions": [x for x in followups if x["region"] == region][:3],
                "confidence": "medium" if region_events and local_rows else "low",
                "evidence_references": _strings((ref for x in region_events for ref in x["evidence_references"]), 8),
            })

        same_series = campaign_state.get("same_series_poll_changes") or []
        poll_context = {
            "record_count": len(polls),
            "fresh_record_count": sum(isinstance(x, dict) and x.get("freshness_status") == "fresh" for x in polls),
            "same_series_changes": same_series,
            "comparability_status": "same_series_only" if same_series else "no_comparable_trend",
            "margin_rule": "Differences within an applicable margin of error are not a clear lead.",
            "rule": "Polls are descriptive calibration only; incomparable series cannot establish a trend.",
        }
        variables = []
        specs = [
            ("regional_heterogeneity", "地区历史结构差异", historical_signals),
            ("cross_level_residual", "跨层级差异待地方解释", comparisons),
            ("organization_coordination", "组织协调的可核验层级", orgs),
            ("issue_dynamics", "当前议题变化", issues),
            ("third_party_dimension", "第三方政党与组织维度", third_party),
            ("poll_comparability", "民调可比性与误差边界", polls),
        ]
        for variable_id, name, evidence in specs:
            refs = _strings((ref for x in evidence if isinstance(x, dict) for ref in (x.get("evidence_references") or x.get("source_urls") or [])), 8)
            status = "supported" if len(refs) >= 2 else "partial" if evidence else "unresolved"
            variables.append({
                "variable_id": variable_id, "name": name, "regions": regions[:8], "actors": [],
                "evidence_for": refs or [str(x.get("event_id") or x.get("poll_id") or x.get("comparison_type") or "analytical_record") for x in evidence[:5] if isinstance(x, dict)],
                "counter_evidence": [], "alternative_explanations": ["coverage, timing, or source selection may explain part of the pattern"],
                "status": status, "confidence": "medium" if status == "supported" else "low",
                "next_evidence_needed": [x["question"] for x in followups[:2]],
            })

        uncertainties = _strings(list(unknowns or []) + list(research_result.get("unresolved") or []) + [str(x) for x in warnings or [] if "conflict" in str(x).lower() or "stale" in str(x).lower()], 20)
        gaps = [{"gap_id": f"gap-{i+1}", "description": text, "status": "unresolved"} for i, text in enumerate(uncertainties[:8])]
        if not local.get("relationships"):
            gaps.append({"gap_id": "gap-local-relationships", "description": "verified current local relationships are missing", "status": "unresolved"})
        coverage = {
            "status": research_result.get("status") or "disabled", "round_name": research_result.get("round_name") or "initial",
            "search_count": int(research_result.get("search_count") or 0), "body_count": int(research_result.get("body_count") or 0),
            "evidence_pack_count": len(pack), "finding_count": len(findings), "coverage_complete": bool(research_result.get("coverage_complete")),
        }
        return {
            "version": 2, "as_of": campaign_state.get("as_of"),
            "structural_context": {
                "jurisdiction": task.get("jurisdiction") or local.get("county"), "as_of": campaign_state.get("as_of"),
                "historical_baseline_available": bool(historical), "regions": regions,
                "interpretation_boundary": "Historical and social structure are context only; aggregate patterns cannot be attributed to individuals, organizations, or current voters without direct evidence.",
            },
            "regional_assessments": regional, "cross_level_comparisons": comparisons,
            "organization_networks": orgs[:12], "issue_dynamics": issues[:12], "key_variables": variables[:8],
            "research_gaps": gaps[:12], "follow_up_questions": followups, "counter_evidence": review[:8],
            "uncertainties": uncertainties,
            "writer_guidance": {
                "input_order": ["final_assessment", "historical_baseline", "bounded_evidence_excerpts", "local_knowledge_summary", "polls", "uncertainties"],
                "organization": ["key_variables", "overall", "regional", "organization", "issues", "research_gaps"],
                "rule": "Write continuous analysis, not a chronological news list; retain counter-evidence and unresolved gaps.",
            },
            "assessment_stage": "final" if draft_assessment is not None or research_result.get("round_name") == "assessment_follow_up" else "draft",
            "research_history": {"follow_up_round_count": 1 if research_result.get("round_name") == "assessment_follow_up" else 0, "draft_question_count": len((draft_assessment or {}).get("follow_up_questions") or []), "resolved_question_count": sum(x["status"] == "resolved" for x in followups)},
            # v1 compatibility
            "campaign_state_status": campaign_state.get("campaign_state_status"), "research_coverage": coverage,
            "evidence_pack": pack, "current_dynamics": events[:24], "organization_signals": orgs[:12],
            "issue_signals": issues[:12], "geographic_signals": [{"location": k, "observed_event_count": v} for k, v in sorted(locations.items())][:16],
            "historical_signals": historical_signals, "poll_context": poll_context, "third_party_signals": third_party[:6],
            "writer_rule": "Use final assessment as the scaffold; never forecast, rank, or infer vote transfer.",
        }
