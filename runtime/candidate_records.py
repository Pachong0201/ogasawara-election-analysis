"""Shared candidate-record classification, scoping and freshness rules.

The runtime has two candidate tiers:

``cache``
    Records fetched by a registered official adapter and persisted under
    ``cache/candidates/<jurisdiction>.jsonl``.
``knowledge_local``
    Records promoted through ``KnowledgePromotionBuilder`` into
    ``knowledge/local/<county>/candidates.jsonl``.

Both tiers must be filtered by jurisdiction, election type, election year,
candidate status and applicability date before they may satisfy the
current-candidate readiness gate.  This module is deliberately deterministic
and side-effect free so the same rules are used by readiness, cache rebuild and
tests.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple

from .freshness import is_fresh
from .models import parse_date


STATUS_CLASS: Dict[str, str] = {
    "registered": "registered",
    "qualified": "qualified",
    "nominated": "nominated",
    "announced": "announced",
    "potential": "announced",
    "withdrawn": "inactive",
    "disqualified": "inactive",
    "rejected": "inactive",
}

REGISTRATION_CLASSES = {"registered", "qualified"}
ACTIVE_CLASSES = {"registered", "qualified", "nominated", "announced"}
INACTIVE_CLASSES = {"inactive"}

REASON_LABELS = {
    "ok": "符合要求",
    "missing_name": "缺少姓名",
    "unknown_status": "未知登记状态",
    "inactive_status": "已退选/失格/驳回",
    "wrong_election_type": "选举类型不符",
    "wrong_election_year": "选举年份不符",
    "wrong_jurisdiction": "县市不符",
    "unqualified_source": "来源等级不足",
    "not_promoted": "未通过知识晋升门禁",
    "missing_verification_date": "缺少验证日期",
    "not_yet_verified": "验证日期晚于目标日期",
    "stale": "超过时效",
    "invalid_registration_date": "登记日期无法解析",
    "not_yet_registered": "登记日期晚于目标日期",
}


def normalize_jurisdiction(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("臺", "台")


def candidate_status_class(status: Any) -> str:
    return STATUS_CLASS.get(str(status or "").strip().lower(), "unknown")


def candidate_name(record: Dict[str, Any]) -> str:
    return str(record.get("name") or record.get("candidate_name") or "").strip()


def source_grade_usable(record: Dict[str, Any]) -> bool:
    grade = str(record.get("source_grade") or record.get("evidence_grade") or "").upper()
    if grade in {"A", "B"}:
        return True
    if grade == "C" and int(record.get("independent_source_count") or 0) >= 2:
        return True
    return False


def is_promoted(record: Dict[str, Any]) -> bool:
    provenance = record.get("promotion_provenance")
    if not isinstance(provenance, dict):
        return False
    return bool(
        provenance.get("proposal_id")
        and (provenance.get("promoted_at") or provenance.get("builder_version"))
    )


def candidate_is_applicable(
    record: Dict[str, Any],
    *,
    jurisdiction: str,
    election_type: str,
    target_year: int,
    as_of: Optional[dt.date] = None,
    require_promotion: bool = False,
) -> Tuple[bool, str, str]:
    """Return ``(usable, reason, status_class)`` for a candidate record.

    Fails closed: unknown status, missing timestamps, other election types and
    other jurisdictions are rejected instead of being treated as current facts.
    """
    status_class = candidate_status_class(record.get("candidate_status") or record.get("status"))
    if not candidate_name(record):
        return False, "missing_name", status_class
    if status_class == "unknown":
        return False, "unknown_status", status_class
    if status_class == "inactive":
        return False, "inactive_status", status_class

    record_type = str(record.get("election_type") or "").strip()
    if record_type != str(election_type):
        return False, "wrong_election_type", status_class
    try:
        record_year = int(record.get("election_year"))
    except (TypeError, ValueError):
        return False, "wrong_election_year", status_class
    if record_year != int(target_year):
        return False, "wrong_election_year", status_class

    record_jurisdiction = normalize_jurisdiction(
        record.get("jurisdiction") or record.get("official_jurisdiction") or record.get("county")
    )
    if record_jurisdiction != normalize_jurisdiction(jurisdiction):
        return False, "wrong_jurisdiction", status_class

    if not source_grade_usable(record):
        return False, "unqualified_source", status_class
    if require_promotion and not is_promoted(record):
        return False, "not_promoted", status_class

    verified_date = parse_date(
        record.get("last_verified_at")
        or record.get("verified_at")
        or record.get("retrieved_at")
    )
    if verified_date is None:
        return False, "missing_verification_date", status_class
    if as_of is not None and verified_date > as_of:
        return False, "not_yet_verified", status_class

    registration_date = parse_date(record.get("registration_date"))
    if record.get("registration_date") and registration_date is None:
        return False, "invalid_registration_date", status_class
    if as_of is not None and registration_date is not None and registration_date > as_of:
        return False, "not_yet_registered", status_class

    freshness_kind = (
        "candidate_registration" if status_class in REGISTRATION_CLASSES else "candidate_profile"
    )
    if not is_fresh(record, kind=freshness_kind, now=as_of):
        return False, "stale", status_class
    return True, "ok", status_class


def strip_internal_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items() if not str(key).startswith("_")}
