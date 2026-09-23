"""Comparable campaign-state delta helpers for V1.4.0."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


def _candidate_keys(rows: Iterable[Dict[str, Any]]) -> Set[str]:
    keys: Set[str] = set()
    for row in rows:
        value = str(
            row.get("candidate_id")
            or row.get("candidate_name")
            or row.get("name")
            or row.get("姓名")
            or ""
        ).strip()
        if value:
            keys.add(value)
    return keys


def _ids(rows: Iterable[Dict[str, Any]], *fields: str) -> Set[str]:
    values: Set[str] = set()
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                values.add(value)
                break
    return values


def _is_corroborated_media(row: Dict[str, Any]) -> bool:
    return (
        str(row.get("record_type") or "") == "campaign_event"
        and str(row.get("verification_status") or "") == "corroborated_media"
        and int(row.get("independent_source_count") or 0) >= 2
        and str(row.get("structural_use") or "") == "research_trigger_only"
    )


def compare_campaign_snapshots(
    previous: Dict[str, Any] | None,
    current_candidates: List[Dict[str, Any]],
    current_events: List[Dict[str, Any]],
    polls: List[Dict[str, Any]],
    retrieval_leads: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Return only observable changes; never infer benefit, harm or election outcome."""
    retrieval_leads = retrieval_leads or []
    if not previous:
        return {
            "previous_snapshot_available": False,
            "candidate_changes": [],
            "new_event_ids": [],
            "removed_event_ids": [],
            "new_poll_ids": [],
            "removed_poll_ids": [],
            "new_retrieval_lead_ids": [],
            "new_corroborated_event_ids": [],
            "dimension_changes": {},
            "change_status": "baseline_created",
        }

    before_candidates = set(previous.get("candidate_keys") or [])
    now_candidates = _candidate_keys(current_candidates)
    before_events = set(previous.get("event_ids") or [])
    now_events = _ids(current_events, "event_id", "record_id", "lead_id")
    before_polls = set(previous.get("poll_ids") or [])
    now_polls = _ids(polls, "poll_id", "record_id")
    before_retrieval = set(previous.get("retrieval_lead_ids") or [])
    now_retrieval = _ids(retrieval_leads, "lead_id", "record_id", "url")
    before_corroborated = set(previous.get("corroborated_event_ids") or [])
    now_corroborated = {
        str(row.get("event_id") or row.get("record_id") or "").strip()
        for row in current_events
        if _is_corroborated_media(row)
        and str(row.get("event_id") or row.get("record_id") or "").strip()
    }

    candidate_changes: List[Dict[str, str]] = []
    for key in sorted(now_candidates - before_candidates):
        candidate_changes.append({"change": "candidate_added", "candidate_key": key})
    if now_candidates:
        for key in sorted(before_candidates - now_candidates):
            candidate_changes.append({"change": "candidate_removed", "candidate_key": key})

    new_events = sorted(now_events - before_events)
    removed_events = sorted(before_events - now_events)
    new_polls = sorted(now_polls - before_polls)
    removed_polls = sorted(before_polls - now_polls)
    new_retrieval = sorted(now_retrieval - before_retrieval)
    new_corroborated = sorted(now_corroborated - before_corroborated)
    missing_candidates = bool(before_candidates and not now_candidates)
    changed = bool(
        candidate_changes or new_events or removed_events or new_polls
        or removed_polls or new_retrieval or new_corroborated
    )
    dimensions: Dict[str, List[str]] = {}
    for row in current_events:
        event_id = str(row.get("event_id") or "")
        if event_id in new_events:
            dimension = str(row.get("affected_dimension") or "other")
            dimensions.setdefault(dimension, []).append(event_id)
    conflicts = [row for row in current_events if row.get("evidence_status") == "requires_review"]

    return {
        "previous_snapshot_available": True,
        "previous_as_of": previous.get("as_of"),
        "candidate_changes": candidate_changes,
        "new_event_ids": new_events,
        "removed_event_ids": removed_events,
        "new_poll_ids": new_polls,
        "removed_poll_ids": removed_polls,
        "new_retrieval_lead_ids": new_retrieval,
        "new_corroborated_event_ids": new_corroborated,
        "dimension_changes": dimensions,
        "removed_event_interpretation": "absence from current input; resolution requires verification" if removed_events else "",
        "change_status": "contradictory" if conflicts else "uncertain" if removed_events or missing_candidates else "changed" if changed else "unchanged",
        "interpretation_boundary": "delta records observable input changes only; no candidate ranking or outcome inference",
    }
