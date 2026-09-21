"""Freshness / TTL handling for dynamic runtime records."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .models import parse_date, utc_now_iso


DEFAULT_CONFIG = Path("config/freshness.yaml")


def _load_policy(config_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.is_absolute():
        # Prefer repository-root-relative path.
        candidate = Path(__file__).resolve().parents[1] / path
        if candidate.exists():
            path = candidate
    if not path.exists():
        return {"policies": {}}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"policies": {}}


def _record_type(record: Dict[str, Any], kind: Optional[str] = None) -> str:
    return str(
        kind
        or record.get("record_type")
        or record.get("kind")
        or record.get("cache_type")
        or "unknown"
    )


def _timestamp(record: Dict[str, Any]) -> Optional[dt.date]:
    for field in ("last_verified_at", "verified_at", "retrieved_at", "field_end", "publish_date"):
        parsed = parse_date(record.get(field))
        if parsed is not None:
            return parsed
    return None


def compute_expires_at(record: Dict[str, Any], kind: Optional[str] = None, config_path: Optional[Path] = None) -> Optional[dt.date]:
    """Return the expiry date for a record, or None when it never expires."""
    config = _load_policy(config_path)
    policies = config.get("policies", {})
    record_kind = _record_type(record, kind)
    policy = policies.get(record_kind, {})

    explicit = parse_date(record.get("valid_until") or record.get("expires_at"))
    if explicit is not None:
        return explicit

    ttl = policy.get("ttl")
    if ttl == "permanent":
        return None
    if ttl == "election_cycle":
        # Registration data stays valid for the current election cycle.
        # When no explicit valid_until exists, use the declared election year
        # plus one year as a conservative deterministic boundary.
        year = record.get("election_year")
        if year:
            return dt.date(int(year) + 1, 12, 31)
        return None

    ttl_days = policy.get("ttl_days")
    if ttl_days is None:
        return None
    base = _timestamp(record)
    if base is None:
        return None
    return base + dt.timedelta(days=int(ttl_days))


def is_fresh(record: Dict[str, Any], kind: Optional[str] = None, now: Optional[dt.date] = None, config_path: Optional[Path] = None) -> bool:
    """Return True when the record is still within its TTL.

    Permanent historical records are fresh.  Dynamic records without a
    verifiable timestamp are treated as not fresh.
    """
    now = now or dt.date.today()
    config = _load_policy(config_path)
    policies = config.get("policies", {})
    record_kind = _record_type(record, kind)
    policy = policies.get(record_kind, {})

    expires = compute_expires_at(record, record_kind, config_path)
    if expires is not None:
        return now < expires

    if policy.get("ttl") == "permanent":
        return True

    # Unknown dynamic type: require a timestamp and fail closed.
    return False if _timestamp(record) is None else False


def is_stale(record: Dict[str, Any], kind: Optional[str] = None, now: Optional[dt.date] = None, config_path: Optional[Path] = None) -> bool:
    return not is_fresh(record, kind=kind, now=now, config_path=config_path)


def needs_revalidation(record: Dict[str, Any], kind: Optional[str] = None, now: Optional[dt.date] = None, current_use: bool = False, config_path: Optional[Path] = None) -> bool:
    """Return True when a record should be re-fetched or re-verified."""
    now = now or dt.date.today()
    config = _load_policy(config_path)
    policies = config.get("policies", {})
    record_kind = _record_type(record, kind)
    policy = policies.get(record_kind, {})

    if policy.get("current_use_requires_revalidation") and current_use:
        return True
    if policy.get("revalidate") is False:
        return False
    return not is_fresh(record, kind=record_kind, now=now, config_path=config_path)


def evaluate_records(records: Iterable[Dict[str, Any]], kind: Optional[str] = None, now: Optional[dt.date] = None, config_path: Optional[Path] = None) -> Dict[str, Any]:
    now = now or dt.date.today()
    statuses: List[Dict[str, Any]] = []
    fresh = stale = unknown = 0
    for index, record in enumerate(records):
        record_kind = _record_type(record, kind)
        if record_kind == "unknown" and not _timestamp(record):
            status = "unknown"
            unknown += 1
        elif is_fresh(record, kind=record_kind, now=now, config_path=config_path):
            status = "fresh"
            fresh += 1
        else:
            status = "stale"
            stale += 1
        statuses.append(
            {
                "index": index,
                "record_id": record.get("record_id") or record.get("poll_id") or record.get("claim_id"),
                "record_type": record_kind,
                "status": status,
                "expires_at": compute_expires_at(record, record_kind, config_path),
            }
        )
    return {"fresh": fresh, "stale": stale, "unknown": unknown, "records": statuses}


def poll_moe_applicable(record: Dict[str, Any]) -> bool:
    """Return whether a traditional MOE may be applied to a poll record."""
    if record.get("moe_applicable") is False:
        return False
    method = str(record.get("method") or "").lower()
    if method in {"online_closed", "online", "web_closed"}:
        return False
    return record.get("moe") is not None


def historical_relationship_is_current(record: Dict[str, Any], now: Optional[dt.date] = None) -> bool:
    """Validate whether a historical relationship may be treated as current."""
    now = now or dt.date.today()
    if record.get("current_status") not in {"active_verified", "active"}:
        return False
    last = parse_date(record.get("last_verified_at"))
    if last is None:
        return False
    return (now - last).days <= 365 * 5
