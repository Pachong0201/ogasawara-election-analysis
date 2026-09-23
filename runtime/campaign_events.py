"""Canonical L4 campaign event ingestion, evidence retention and conflict audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .election_loader import load_jsonl
from .models import parse_date

TYPES = set("nomination candidate_registration candidate_withdrawal endorsement party_cooperation alliance alliance_break campaign_org campaign_headquarters debate policy major_issue judicial_event controversy poll other".split())
ALIASES = {"registration": "candidate_registration", "campaign_office": "campaign_headquarters", "primary": "nomination", "local_event": "major_issue"}
DIMENSIONS = dict.fromkeys(("nomination", "candidate_registration", "candidate_withdrawal"), "candidate_field")
DIMENSIONS.update(endorsement="organization", party_cooperation="coalition", alliance="coalition", alliance_break="coalition", campaign_org="campaign_operations", campaign_headquarters="campaign_operations", debate="issue", policy="issue", major_issue="issue", controversy="issue", judicial_event="legal_judicial", poll="polling")
GRADE = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
VERIFIED = {"verified", "confirmed", "verified_fact", "official_record"}


def _get(row: Dict[str, Any], *keys: str) -> str:
    return next((str(row[k]).strip() for k in keys if row.get(k) is not None and str(row[k]).strip()), "")


def _identity(row: Dict[str, Any]) -> str:
    # Source URLs and supplied IDs identify evidence, not the underlying event.
    fields = ("jurisdiction", "date", "claim_type", "speaker", "subject", "claim_value")
    seed = json.dumps([str(row.get(k) or "").strip().casefold() for k in fields], ensure_ascii=False)
    return "campaign-event-" + hashlib.sha256(seed.encode()).hexdigest()[:20]


def _evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": _get(row, "source", "source_id"),
        "source_grade": _get(row, "source_grade").upper(),
        "verification_status": _get(row, "verification_status").lower(),
        "reference": _get(row, "url", "reference", "source_reference", "raw_reference"),
        "retrieved_at": _get(row, "retrieved_at"),
        "last_verified_at": _get(row, "last_verified_at"),
        "independent_source_count": row.get("independent_source_count", 0),
        "original_event_id": _get(row, "event_id", "record_id"),
    }


def _usable(row: Dict[str, Any]) -> bool:
    return (row.get("source_grade") in {"A", "B", "C"}
            and row.get("verification_status") in VERIFIED
            and bool(row.get("source") or row.get("source_id"))
            and bool(row.get("reference") or row.get("url") or row.get("source_reference")))


class CampaignEventLoader:
    """Normalize once, deduplicate same claims and quarantine conflicting claims."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def normalize(self, record: Dict[str, Any], jurisdiction: str, source_kind: str = "cache") -> Optional[Dict[str, Any]]:
        if not isinstance(record, dict):
            return None
        region = _get(record, "jurisdiction", "county", "region") or jurisdiction
        date = next((parsed for key in ("date", "event_date", "publish_date", "published_at")
                     if (parsed := parse_date(record.get(key)))), None)
        if region != jurisdiction or not date:
            return None
        raw_type = _get(record, "claim_type", "event_type", "type").lower()
        kind = ALIASES.get(raw_type, raw_type)
        kind = kind if kind in TYPES else "other"
        row = dict(record)
        row.update(jurisdiction=jurisdiction, date=date.isoformat(), claim_type=kind,
                   speaker=_get(record, "speaker", "actor", "candidate_name"),
                   subject=_get(record, "subject", "target"),
                   source=_get(record, "source", "source_id"),
                   reference=_get(record, "url", "reference", "source_reference", "raw_reference"),
                   retrieved_at=_get(record, "retrieved_at"),
                   last_verified_at=_get(record, "last_verified_at"),
                   claim_value=_get(record, "claim_value", "position", "support_status", "claim", "summary", "title"),
                   source_grade=_get(record, "source_grade").upper() or "E",
                   verification_status=_get(record, "verification_status").lower() or "lead_only",
                   affected_dimension=_get(record, "affected_dimension") or DIMENSIONS.get(kind, "other"),
                   layer_id="L4", source_kind=source_kind)
        row["event_id"] = _identity(row)
        row["event_fingerprint"] = row["event_id"]
        row["usable_for_trigger"] = _usable(row)
        return row

    def audit(self, records: Iterable[Dict[str, Any]], jurisdiction: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        raw = list(records)
        cutoff = parse_date(as_of) if as_of else None
        selected: Dict[str, Dict[str, Any]] = {}
        evidence: Dict[str, List[Dict[str, Any]]] = {}
        normalized_count = excluded_future = 0
        for record in raw:
            row = self.normalize(record, jurisdiction)
            if row is None:
                continue
            if cutoff and parse_date(row["date"]) > cutoff:
                excluded_future += 1
                continue
            normalized_count += 1
            key = row["event_id"]
            evidence.setdefault(key, []).append(_evidence(record))
            quality = lambda item: (int(item["verification_status"] in VERIFIED), GRADE.get(item["source_grade"], 0))
            if key not in selected or quality(row) > quality(selected[key]):
                selected[key] = row
        groups: Dict[tuple[str, ...], List[Dict[str, Any]]] = {}
        original_ids: Dict[str, str] = {}
        for key, row in selected.items():
            row["evidence_refs"] = list({json.dumps(ref, sort_keys=True): ref for ref in evidence[key]}.values())
            row["evidence_count"] = len(row["evidence_refs"])
            row["usable_for_trigger"] = any(_usable(ref) for ref in evidence[key])
            for ref in evidence[key]:
                if ref["original_event_id"]:
                    original_ids[ref["original_event_id"]] = key
            group = tuple(row[field] for field in ("jurisdiction", "date", "claim_type", "speaker", "subject"))
            groups.setdefault(group, []).append(row)
        conflicts: List[Dict[str, Any]] = []
        disputed: set[str] = set()
        for rows in groups.values():
            values = {row["claim_value"] for row in rows if row["claim_value"]}
            if len(values) > 1:
                ids = sorted(row["event_id"] for row in rows)
                conflicts.append({"event_ids": ids, "claim_values": sorted(values), "resolution": "requires_review"})
                disputed.update(ids)
        for record in raw:
            if not isinstance(record, dict):
                continue
            a = original_ids.get(_get(record, "event_id", "record_id"))
            b = original_ids.get(_get(record, "contradicts_event_id", "contradiction_of"))
            if a and b and a != b:
                ids = sorted((a, b))
                if not any(item["event_ids"] == ids for item in conflicts):
                    conflicts.append({"event_ids": ids, "resolution": "requires_review"})
                disputed.update(ids)
        for key in disputed:
            selected[key]["usable_for_trigger"] = False
            selected[key]["evidence_status"] = "requires_review"
        events = sorted(selected.values(), key=lambda row: (row["date"], row["event_id"]))
        return {"events": events, "conflicts": conflicts, "raw_count": len(raw),
                "normalized_count": normalized_count, "deduplicated_count": len(events),
                "excluded_future": excluded_future,
                "warnings": [f"{len(conflicts)} campaign event conflict set(s) require review"] if conflicts else []}

    def load_cache(self, jurisdiction: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        files: List[str] = []
        raw: List[Dict[str, Any]] = []
        for path in sorted((self.repo_root / "cache" / "events").glob("*.jsonl")):
            rows = load_jsonl(path)
            if rows:
                files.append(str(path))
                raw.extend(rows)
        report = self.audit(raw, jurisdiction, as_of=as_of)
        report["files"] = files
        return report
