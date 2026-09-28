"""Build a neutral analytical scaffold from validated election evidence.

The assessment layer sits between evidence collection and prose generation.  It
does not rank candidates, estimate winning probabilities, or turn media claims
into verified facts.  Its job is to organize the evidence into analytical
dimensions so the report writer can explain relationships instead of merely
listing records.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Dict, Iterable, List


ORGANIZATION_EVENT_TYPES = {
    "endorsement",
    "party_cooperation",
    "alliance",
    "alliance_break",
    "campaign_org",
    "campaign_headquarters",
}
ISSUE_EVENT_TYPES = {
    "debate",
    "policy",
    "major_issue",
    "judicial_event",
    "controversy",
}
VERIFIED_STATUSES = {"verified", "official_verified"}
CORROBORATED_STATUSES = {"corroborated_media"}


def _strings(values: Iterable[Any], limit: int = 24) -> List[str]:
    if isinstance(values, str):
        values = [values]
    output: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _evidence_id(url: str) -> str:
    return "web-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _event_confidence(status: str) -> str:
    if status in VERIFIED_STATUSES:
        return "high"
    if status in CORROBORATED_STATUSES:
        return "medium"
    return "low"


def _research_confidence(citations: List[Dict[str, Any]]) -> str:
    independent = {
        str(item.get("independence_key") or item.get("publisher_id") or item.get("source_name") or "")
        for item in citations
        if isinstance(item, dict)
    }
    independent.discard("")
    grades = {str(item.get("source_grade") or "") for item in citations if isinstance(item, dict)}
    if "A" in grades and len(independent) >= 2:
        return "medium"
    if len(independent) >= 2:
        return "medium"
    return "low"


class ElectionAssessmentBuilder:
    """Create a bounded, traceable assessment object for the report writer."""

    def build(
        self,
        *,
        campaign_state: Dict[str, Any],
        current_events: List[Dict[str, Any]],
        campaign_event_resolution: Dict[str, Any],
        metrics: Dict[str, List[Dict[str, Any]]],
        polls: List[Dict[str, Any]],
        research_result: Dict[str, Any],
        unknowns: List[str],
        warnings: List[str],
    ) -> Dict[str, Any]:
        resolved_events = [
            item for item in (campaign_event_resolution.get("events") or current_events or [])
            if isinstance(item, dict)
        ]
        findings = [
            item for item in (research_result.get("findings") or [])
            if isinstance(item, dict)
        ]
        raw_pack = [
            item for item in (research_result.get("evidence_pack") or [])
            if isinstance(item, dict) and item.get("url")
        ]

        evidence_pack = []
        url_to_id: Dict[str, str] = {}
        for item in raw_pack[:12]:
            url = str(item.get("url") or "")
            evidence_id = _evidence_id(url)
            url_to_id[url] = evidence_id
            evidence_pack.append({
                "evidence_id": evidence_id,
                "url": url,
                "title": item.get("title"),
                "source_name": item.get("source_name"),
                "source_kind": item.get("source_kind"),
                "source_grade": item.get("source_grade"),
                "publisher_id": item.get("publisher_id"),
                "independence_key": item.get("independence_key"),
                "page_date": item.get("page_date") or item.get("published_at"),
                "excerpt": str(item.get("content") or "")[:1400],
                "content_truncated": bool(item.get("content_truncated")),
            })

        dynamics: List[Dict[str, Any]] = []
        organization_signals: List[Dict[str, Any]] = []
        issue_signals: List[Dict[str, Any]] = []
        review_items: List[Dict[str, Any]] = []
        location_counts: Counter[str] = Counter()

        for index, event in enumerate(resolved_events[:24]):
            status = str(event.get("verification_status") or "unknown")
            event_type = str(event.get("event_type") or event.get("claim_type") or "campaign_update")
            event_id = str(event.get("event_id") or event.get("record_id") or f"event-{index + 1}")
            locations = _strings(event.get("locations") or [])
            for location in locations:
                location_counts[location] += 1
            item = {
                "kind": "campaign_event",
                "event_id": event_id,
                "date": event.get("date") or event.get("page_date"),
                "event_type": event_type,
                "claim_class": (
                    "verified_fact" if status in VERIFIED_STATUSES
                    else "corroborated_report" if status in CORROBORATED_STATUSES
                    else "reported_event"
                ),
                "verification_status": status,
                "confidence": _event_confidence(status),
                "candidate_entities": _strings(event.get("candidate_entities") or []),
                "organizations": _strings(event.get("organizations") or event.get("organization_entities") or []),
                "locations": locations,
                "statement": str(
                    event.get("summary")
                    or event.get("evidence_excerpt")
                    or event.get("claim_value")
                    or event.get("subject")
                    or ""
                )[:700],
                "evidence_references": _strings(
                    event.get("source_urls")
                    or event.get("urls")
                    or ([event.get("url")] if event.get("url") else [])
                ),
            }
            dynamics.append(item)
            if event_type in ORGANIZATION_EVENT_TYPES:
                organization_signals.append(item)
            if event_type in ISSUE_EVENT_TYPES:
                issue_signals.append(item)
            if status == "requires_review":
                review_items.append(item)

        for index, finding in enumerate(findings[:12]):
            citations = [
                item for item in (finding.get("citations") or [])
                if isinstance(item, dict) and item.get("url")
            ]
            refs = [
                url_to_id.get(str(item.get("url")), str(item.get("url")))
                for item in citations
            ]
            item = {
                "kind": "research_finding",
                "finding_id": f"research-{index + 1}",
                "claim_class": "analytical_input",
                "verification_status": str(
                    finding.get("verification_status") or "body_grounded_unverified"
                ),
                "confidence": _research_confidence(citations),
                "question": finding.get("question"),
                "statement": str(finding.get("statement") or "")[:700],
                "evidence_references": refs,
            }
            dynamics.append(item)

        historical_signals: List[Dict[str, Any]] = []
        for metric_name, rows in metrics.items():
            for row in rows or []:
                if not isinstance(row, dict) or not row.get("local_explanation_required"):
                    continue
                historical_signals.append({
                    "metric": metric_name,
                    "region": row.get("region"),
                    "party": row.get("party"),
                    "candidate": row.get("candidate"),
                    "periods": row.get("periods") or row.get("year"),
                    "signal": row.get("signal") or row.get("status"),
                    "value": row.get("value"),
                    "baseline": row.get("baseline"),
                    "rule": (
                        "historical structural signal only; do not extrapolate it directly "
                        "to current support or electoral outcome"
                    ),
                })
                if len(historical_signals) >= 12:
                    break
            if len(historical_signals) >= 12:
                break

        geographic_signals = [
            {"location": location, "observed_event_count": count}
            for location, count in sorted(location_counts.items())
        ][:16]

        fresh_polls = [
            item for item in polls
            if isinstance(item, dict) and item.get("freshness_status") == "fresh"
        ]
        poll_context = {
            "record_count": len(polls),
            "fresh_record_count": len(fresh_polls),
            "same_series_changes": campaign_state.get("same_series_poll_changes") or [],
            "rule": (
                "polls are descriptive calibration only; do not combine incomparable series "
                "or convert them into a winner ranking or probability"
            ),
        }

        research_coverage = {
            "status": research_result.get("status") or "disabled",
            "search_count": int(research_result.get("search_count") or 0),
            "body_count": int(research_result.get("body_count") or 0),
            "evidence_pack_count": len(evidence_pack),
            "finding_count": len(findings),
            "coverage_complete": bool(research_result.get("coverage_complete")),
        }

        uncertainties = _strings(
            list(unknowns or [])
            + list(research_result.get("unresolved") or [])
            + [
                str(item) for item in (warnings or [])
                if "conflict" in str(item).lower() or "stale" in str(item).lower()
            ],
            limit=20,
        )

        return {
            "version": 1,
            "as_of": campaign_state.get("as_of"),
            "campaign_state_status": campaign_state.get("campaign_state_status"),
            "research_coverage": research_coverage,
            "evidence_pack": evidence_pack,
            "current_dynamics": dynamics[:24],
            "organization_signals": organization_signals[:12],
            "issue_signals": issue_signals[:12],
            "geographic_signals": geographic_signals,
            "historical_signals": historical_signals,
            "poll_context": poll_context,
            "counter_evidence": review_items[:8],
            "uncertainties": uncertainties,
            "writer_rule": (
                "Use this object as an analytical scaffold. Separate source-supported facts, "
                "reported claims and analytical interpretation. Explain relationships and "
                "alternative interpretations, but do not rank candidates, estimate winning "
                "probabilities, or turn low-confidence material into fact."
            ),
        }
