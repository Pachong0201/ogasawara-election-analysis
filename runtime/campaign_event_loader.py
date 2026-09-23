"""Normalize, deduplicate and audit live campaign events for V1.4.0."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .election_loader import load_jsonl
from .models import parse_date


VERIFIED = {"verified", "confirmed"}
USABLE_GRADES = {"A", "B", "C"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(record: Dict[str, Any]) -> str:
    seed = "|".join(
        [
            _text(record.get("jurisdiction")),
            _text(record.get("date")),
            _text(record.get("claim_type")),
            _text(record.get("speaker")),
            _text(record.get("subject")),
            _text(record.get("url") or record.get("source_reference")),
            _text(record.get("summary") or record.get("claim")),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _conflict_key(record: Dict[str, Any]) -> str:
    entity = _text(record.get("subject") or record.get("entity") or record.get("speaker"))
    claim_type = _text(record.get("claim_type"))
    date = _text(record.get("date"))
    return "|".join([entity, claim_type, date])


def _claim_value(record: Dict[str, Any]) -> str:
    for key in ("claim_value", "position", "support_status", "status", "claim", "summary"):
        value = _text(record.get(key))
        if value:
            return value
    return ""


class CampaignEventLoader:
    """Load L4 events without silently upgrading leads into verified facts."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def normalize(
        self,
        record: Dict[str, Any],
        jurisdiction: str,
        source_kind: str = "cache",
    ) -> Optional[Dict[str, Any]]:
        row = dict(record)
        region = _text(
            row.get("jurisdiction")
            or row.get("county")
            or row.get("region")
            or jurisdiction
        )
        if region and region != jurisdiction:
            return None

        event_date = None
        for key in ("date", "event_date", "publish_date", "published_at"):
            parsed = parse_date(row.get(key))
            if parsed:
                event_date = parsed.isoformat()
                break
        if not event_date:
            return None

        claim_type = _text(row.get("claim_type") or row.get("event_type") or row.get("type"))
        row["jurisdiction"] = jurisdiction
        row["date"] = event_date
        row["claim_type"] = claim_type or "unspecified"
        row["speaker"] = _text(row.get("speaker") or row.get("actor") or row.get("candidate_name"))
        row["source_grade"] = _text(row.get("source_grade")).upper() or "E"
        row["verification_status"] = _text(row.get("verification_status")).lower() or "lead_only"
        row["layer_id"] = "L4"
        row["source_kind"] = source_kind
        row["event_id"] = _text(row.get("event_id") or row.get("record_id") or row.get("lead_id")) or _fingerprint(row)
        row["event_fingerprint"] = _fingerprint(row)
        row["usable_for_trigger"] = (
            row["source_grade"] in USABLE_GRADES
            and (
                row["source_grade"] in {"A", "B"}
                and row["verification_status"] in VERIFIED | {""}
                or row["source_grade"] == "C"
                and row["verification_status"] in VERIFIED
            )
        )
        return row

    def deduplicate(self, records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_fp: Dict[str, Dict[str, Any]] = {}
        rank = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
        for row in records:
            fp = _text(row.get("event_fingerprint")) or _fingerprint(row)
            previous = by_fp.get(fp)
            if previous is None:
                by_fp[fp] = row
                continue
            old_score = (
                1 if _text(previous.get("verification_status")).lower() in VERIFIED else 0,
                rank.get(_text(previous.get("source_grade")).upper(), 0),
            )
            new_score = (
                1 if _text(row.get("verification_status")).lower() in VERIFIED else 0,
                rank.get(_text(row.get("source_grade")).upper(), 0),
            )
            if new_score > old_score:
                by_fp[fp] = row
        return sorted(by_fp.values(), key=lambda item: (item.get("date", ""), item.get("event_id", "")))

    def detect_conflicts(self, records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, List[str]]] = {}
        for row in records:
            key = _conflict_key(row)
            value = _claim_value(row)
            if not key.strip("|") or not value:
                continue
            grouped.setdefault(key, {}).setdefault(value, []).append(_text(row.get("event_id")))
        conflicts: List[Dict[str, Any]] = []
        for key, values in grouped.items():
            if len(values) < 2:
                continue
            conflicts.append(
                {
                    "conflict_key": key,
                    "values": [
                        {"claim_value": value, "event_ids": sorted(ids)}
                        for value, ids in sorted(values.items())
                    ],
                    "resolution": "requires_review",
                }
            )
        return conflicts

    def load_cache(self, jurisdiction: str) -> Dict[str, Any]:
        base = self.repo_root / "cache" / "events"
        raw: List[Dict[str, Any]] = []
        files: List[str] = []
        if base.exists():
            for path in sorted(base.glob("*.jsonl")):
                rows = load_jsonl(path)
                if rows:
                    files.append(str(path))
                raw.extend(rows)

        normalized = [
            item
            for item in (
                self.normalize(row, jurisdiction=jurisdiction, source_kind="cache")
                for row in raw
            )
            if item is not None
        ]
        events = self.deduplicate(normalized)
        conflicts = self.detect_conflicts(events)
        return {
            "events": events,
            "conflicts": conflicts,
            "files": files,
            "raw_count": len(raw),
            "normalized_count": len(normalized),
            "deduplicated_count": len(events),
            "warnings": [
                f"{len(conflicts)} campaign event conflict set(s) require review"
            ] if conflicts else [],
        }
