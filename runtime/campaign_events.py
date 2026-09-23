"""Normalize, deduplicate and audit live campaign events for V1.4.0."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import parse_date

SOURCE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "": 0}
VERIFICATION_RANK = {
    "official_record": 6,
    "verified_fact": 6,
    "verified": 6,
    "confirmed": 6,
    "reported_by_media": 4,
    "campaign_claim": 2,
    "lead_only": 1,
    "unverified": 1,
    "unknown": 0,
    "disputed": 0,
}

TYPE_ALIASES = {
    "registration": "candidate_registration",
    "campaign_office": "campaign_headquarters",
    "primary": "nomination",
    "local_event": "major_issue",
}

DIMENSION_BY_TYPE = {
    "nomination": "candidate_field",
    "candidate_registration": "candidate_field",
    "candidate_withdrawal": "candidate_field",
    "endorsement": "organization",
    "party_cooperation": "coalition",
    "alliance": "coalition",
    "alliance_break": "coalition",
    "campaign_org": "campaign_operations",
    "campaign_headquarters": "campaign_operations",
    "debate": "issue",
    "policy": "issue",
    "major_issue": "issue",
    "judicial_event": "legal_judicial",
    "controversy": "issue",
    "poll": "polling",
}


def _text(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _event_type(record: Dict[str, Any]) -> str:
    raw = _text(record, "claim_type", "event_type", "type").lower()
    return TYPE_ALIASES.get(raw, raw or "other")


def _event_date(record: Dict[str, Any]) -> Optional[dt.date]:
    for key in ("date", "event_date", "publish_date", "published_at"):
        parsed = parse_date(record.get(key))
        if parsed:
            return parsed
    return None


def _quality(record: Dict[str, Any]) -> Tuple[int, int]:
    grade = _text(record, "source_grade").upper()
    verification = _text(record, "verification_status").lower()
    return VERIFICATION_RANK.get(verification, 0), SOURCE_RANK.get(grade, 0)


def _fingerprint(record: Dict[str, Any], jurisdiction: str) -> str:
    explicit = _text(record, "event_id", "record_id")
    if explicit:
        return explicit
    payload = {
        "jurisdiction": jurisdiction,
        "date": str(_event_date(record) or ""),
        "type": _event_type(record),
        "speaker": _text(record, "speaker", "actor", "candidate_name"),
        "target": _text(record, "target", "subject"),
        "title": _text(record, "title", "claim_text", "summary"),
    }
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "campaign-event-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _evidence_ref(record: Dict[str, Any]) -> Dict[str, str]:
    return {
        "source": _text(record, "source", "source_id"),
        "source_grade": _text(record, "source_grade").upper(),
        "verification_status": _text(record, "verification_status").lower(),
        "reference": _text(record, "url", "source_reference", "raw_reference"),
    }


def standardize_campaign_events(
    records: Iterable[Dict[str, Any]],
    jurisdiction: str,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Return canonical L4 events plus dedupe/conflict audit metadata."""

    as_of_date = parse_date(as_of) if as_of else None
    selected: Dict[str, Dict[str, Any]] = {}
    evidence: Dict[str, List[Dict[str, str]]] = {}
    duplicate_count = 0
    excluded_future = 0
    invalid_count = 0
    contradiction_pairs: List[Dict[str, str]] = []

    for raw in records:
        if not isinstance(raw, dict):
            invalid_count += 1
            continue
        date = _event_date(raw)
        if not date:
            invalid_count += 1
            continue
        if as_of_date and date > as_of_date:
            excluded_future += 1
            continue

        event_id = _fingerprint(raw, jurisdiction)
        event_type = _event_type(raw)
        canonical = dict(raw)
        canonical.update(
            {
                "event_id": event_id,
                "jurisdiction": _text(raw, "jurisdiction", "county", "region") or jurisdiction,
                "date": date.isoformat(),
                "claim_type": event_type,
                "speaker": _text(raw, "speaker", "actor", "candidate_name"),
                "source_grade": _text(raw, "source_grade").upper(),
                "verification_status": _text(raw, "verification_status").lower() or "unknown",
                "affected_dimension": _text(raw, "affected_dimension")
                or DIMENSION_BY_TYPE.get(event_type, "other"),
            }
        )

        evidence.setdefault(event_id, []).append(_evidence_ref(raw))
        previous = selected.get(event_id)
        if previous is None:
            selected[event_id] = canonical
        else:
            duplicate_count += 1
            if _quality(canonical) > _quality(previous):
                selected[event_id] = canonical

        contradicted = _text(raw, "contradicts_event_id", "contradiction_of")
        if contradicted:
            contradiction_pairs.append(
                {
                    "event_id": event_id,
                    "contradicts_event_id": contradicted,
                }
            )

    normalized = []
    for event_id, event in selected.items():
        refs = []
        seen_refs = set()
        for ref in evidence.get(event_id, []):
            key = tuple(ref.values())
            if key not in seen_refs:
                refs.append(ref)
                seen_refs.add(key)
        event["evidence_refs"] = refs
        event["evidence_count"] = len(refs)
        normalized.append(event)

    normalized.sort(key=lambda row: (row.get("date", ""), row.get("event_id", "")))
    return {
        "records": normalized,
        "input_count": len(list(records)) if isinstance(records, list) else len(normalized) + duplicate_count + invalid_count + excluded_future,
        "output_count": len(normalized),
        "duplicates_removed": duplicate_count,
        "invalid_count": invalid_count,
        "future_events_excluded": excluded_future,
        "contradictions": contradiction_pairs,
    }
