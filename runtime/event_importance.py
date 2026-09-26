"""Match L4 campaign events to promoted county knowledge without prediction."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


def _strings(values: Iterable[Any]) -> List[str]:
    output: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if len(text) >= 2 and text not in output:
            output.append(text)
    return output


def _event_terms(event: Dict[str, Any]) -> List[str]:
    values: List[Any] = []
    for key in ("candidate_entities", "locations", "actors", "organizations"):
        item = event.get(key) or []
        values.extend(item if isinstance(item, list) else [item])
    values.extend(
        event.get(key)
        for key in ("speaker", "subject", "target")
        if event.get(key)
    )
    return _strings(values)


def _event_text(event: Dict[str, Any]) -> str:
    fields = [
        event.get("claim"), event.get("summary"), event.get("evidence_excerpt"),
        event.get("speaker"), event.get("subject"), event.get("target"),
    ]
    fields.extend(event.get("candidate_entities") or [])
    fields.extend(event.get("locations") or [])
    return " ".join(str(item or "") for item in fields)


def _mechanism(record_type: str, record: Dict[str, Any]) -> str:
    relation = str(record.get("relationship_type") or "")
    if relation in {"faction", "family", "patron_client", "competition"}:
        return "political_network_context"
    if relation in {
        "organization_membership", "civic_association", "farmers_association",
        "fishermen_association", "religious_association", "public_service_network",
        "campaign_cooperation", "public_endorsement", "electoral_support",
    }:
        return "local_organization_context"
    if relation == "geographic_base":
        return "geographic_context"
    if record_type == "candidate_profile":
        return "candidate_background"
    if record_type == "current_issue":
        return "issue_context"
    return "historical_context"


def event_importance_signals(
    events: Iterable[Dict[str, Any]],
    local_knowledge: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return deterministic match signals, not causal or electoral conclusions."""
    records: List[tuple[str, Dict[str, Any], str]] = []
    for record in local_knowledge.get("relationships") or []:
        records.append(("local_relationship", record, str(record.get("relationship_id") or "")))
    for record in local_knowledge.get("candidates") or []:
        records.append(("candidate_profile", record, str(record.get("candidate_id") or "")))
    for record in local_knowledge.get("issues") or []:
        records.append(("current_issue", record, str(record.get("claim_id") or "")))
    for record in local_knowledge.get("historical_claims") or []:
        records.append(("historical_claim", record, str(record.get("claim_id") or "")))

    signals: List[Dict[str, Any]] = []
    for event in events or []:
        text = _event_text(event)
        terms = _event_terms(event)
        matches: List[Dict[str, Any]] = []
        mechanisms: Set[str] = set()
        entities: Set[str] = set()
        for record_type, record, record_id in records:
            candidates = _strings(
                [
                    record.get("subject"), record.get("object"), record.get("name"),
                    record.get("region"), record.get("jurisdiction"), record.get("speaker"),
                    record.get("target"), record.get("organization"),
                ]
            )
            matched = sorted(
                {
                    candidate
                    for candidate in candidates
                    if candidate in text or candidate in terms
                }
            )
            if not matched:
                continue
            mechanism = _mechanism(record_type, record)
            mechanisms.add(mechanism)
            entities.update(matched)
            current_status = str(record.get("current_status") or "")
            matches.append(
                {
                    "record_type": record_type,
                    "record_id": record_id,
                    "matched_entities": matched,
                    "mechanism": mechanism,
                    "time_scope": record.get("time_scope", ""),
                    "last_verified_at": record.get("last_verified_at", ""),
                    "current_status": current_status,
                    "source_grade": record.get("source_grade") or record.get("evidence_grade") or "",
                    "scope_boundary": record.get("scope_boundary", ""),
                    "uncertainty": record.get("uncertainty") or {"level": "unknown"},
                    "use_boundary": (
                        "current_context_only"
                        if current_status == "active_verified"
                        else "historical_background_only"
                    ),
                }
            )
        if not matches:
            continue
        signals.append(
            {
                "event_id": event.get("event_id") or event.get("record_id") or "",
                "event_type": event.get("event_type") or event.get("claim_type") or "",
                "matched_entities": sorted(entities),
                "mechanisms": sorted(mechanisms),
                "matches": matches,
                "why_important": (
                    "该事件提及的实体与已晋升县市知识记录相匹配，可用于定位需要核查的"
                    "历史结构、地方组织、地区基础或议题脉络；匹配本身不证明因果关系、"
                    "组织动员、支持转移或选举优势。"
                ),
                "interpretation_limit": (
                    "仅为证据导航信号；必须依各记录 time_scope、last_verified_at、"
                    "current_status 与 source/evidence 使用，不得用于胜负预测、候选人评分或政治推荐。"
                ),
            }
        )
    return signals
