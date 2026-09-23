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


def compare_campaign_snapshots(
    previous: Dict[str, Any] | None,
    current_candidates: List[Dict[str, Any]],
    current_events: List[Dict[str, Any]],
    polls: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return only observable changes; never infer benefit, harm or election outcome."""
    if not previous:
        return {
            "previous_snapshot_available": False,
            "candidate_changes": [],
            "new_event_ids": [],
            "removed_event_ids": [],
            "new_poll_ids": [],
            "removed_poll_ids": [],
            "change_status": "baseline_created",
        }

    before_candidates = set(previous.get("candidate_keys") or [])
    now_candidates = _candidate_keys(current_candidates)
    before_events = set(previous.get("event_ids") or [])
    now_events = _ids(current_events, "event_id", "record_id", "lead_id")
    before_polls = set(previous.get("poll_ids") or [])
    now_polls = _ids(polls, "poll_id", "record_id")

    candidate_changes: List[Dict[str, str]] = []
    for key in sorted(now_candidates - before_candidates):
        candidate_changes.append({"change": "candidate_added", "candidate_key": key})
    for key in sorted(before_candidates - now_candidates):
        candidate_changes.append({"change": "candidate_removed", "candidate_key": key})

    new_events = sorted(now_events - before_events)
    removed_events = sorted(before_events - now_events)
    new_polls = sorted(now_polls - before_polls)
    removed_polls = sorted(before_polls - now_polls)
    changed = bool(candidate_changes or new_events or removed_events or new_polls or removed_polls)

    return {
        "previous_snapshot_available": True,
        "previous_as_of": previous.get("as_of"),
        "candidate_changes": candidate_changes,
        "new_event_ids": new_events,
        "removed_event_ids": removed_events,
        "new_poll_ids": new_polls,
        "removed_poll_ids": removed_polls,
        "change_status": "changed" if changed else "unchanged",
        "interpretation_boundary": "delta records observable input changes only; no candidate ranking or outcome inference",
    }
