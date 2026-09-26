"""Normalization and validation for election records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .models import (
    NORMALIZATION_VERSION,
    DataValidationIssue,
    ElectionRecord,
    ValidationReport,
    utc_now_iso,
)

REQUIRED_FIELDS = [
    "election_type",
    "election_year",
    "jurisdiction",
    "level",
    "candidate_name",
    "party",
    "votes",
    "valid_votes",
    "turnout",
    "source",
    "source_grade",
    "boundary_version",
]

VALID_SOURCE_GRADES = {"A", "B", "C", "D", "E"}
VALID_LEVELS = {"national", "county_city", "township_district", "village", "polling_station", "legislative_constituency"}


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _record_identity(record: Dict[str, Any]) -> str:
    """Build a stable ID that is unique at the actual geographic record level.

    Parent jurisdiction alone is not enough for township/village records:
    the same candidate appears in many child regions.  Include both parent
    and concrete jurisdiction (and candidate_id/name) to avoid persistence
    deduplication collapsing multiple townships into one record.
    """
    return "{}|{}|{}|{}|{}".format(
        record.get("election_type", "unknown"),
        record.get("election_year", "unknown"),
        record.get("parent_jurisdiction") or record.get("jurisdiction", "unknown"),
        record.get("jurisdiction", "unknown"),
        record.get("candidate_id") or record.get("candidate_name", "unknown"),
    )


def normalize_record(raw: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize a raw record without inventing missing election facts."""
    defaults = defaults or {}
    record = dict(raw)
    for key, value in defaults.items():
        record.setdefault(key, value)

    votes = _to_int(record.get("votes"))
    valid_votes = _to_int(record.get("valid_votes"))
    if votes is not None:
        record["votes"] = votes
    if valid_votes is not None:
        record["valid_votes"] = valid_votes

    if record.get("vote_share") is None and votes is not None and valid_votes:
        record["vote_share"] = round(votes / valid_votes, 6)
    else:
        share = _to_float(record.get("vote_share"))
        if share is not None:
            record["vote_share"] = share

    turnout = _to_float(record.get("turnout"))
    if turnout is not None:
        record["turnout"] = turnout

    if record.get("election_year") is not None:
        year = _to_int(record.get("election_year"))
        if year is not None:
            record["election_year"] = year

    if not record.get("parent_jurisdiction"):
        if record.get("level") in {"township_district", "village", "polling_station"}:
            record["parent_jurisdiction"] = record.get("country") or record.get("county") or ""
        else:
            record["parent_jurisdiction"] = record.get("jurisdiction", "")

    if not record.get("record_id"):
        record["record_id"] = _record_identity(record)

    if not record.get("time_scope"):
        record["time_scope"] = str(record.get("election_year", ""))

    record.setdefault("normalization_version", NORMALIZATION_VERSION)
    record.setdefault("retrieved_at", utc_now_iso())
    record.setdefault("verified_at", utc_now_iso())
    record.setdefault("source_id", record.get("source", ""))
    record.setdefault("source_version", "")
    record.setdefault("raw_reference", "")
    return record


def normalize_records(records: Iterable[Dict[str, Any]], defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return [normalize_record(record, defaults=defaults) for record in records]


def _add_issue(issues: List[DataValidationIssue], code: str, message: str, record: Dict[str, Any], severity: str = "error") -> None:
    issues.append(
        DataValidationIssue(
            code=code,
            message=message,
            record_id=str(record.get("record_id") or ""),
            severity=severity,
        )
    )


def validate_record(
    record: Dict[str, Any],
    known_regions: Optional[Set[str]] = None,
    require_provenance: bool = False,
) -> ValidationReport:
    issues: List[DataValidationIssue] = []
    for field_name in REQUIRED_FIELDS:
        if record.get(field_name) in (None, ""):
            _add_issue(issues, "MISSING_FIELD", f"missing required field: {field_name}", record)

    if require_provenance:
        for field_name in ("retrieved_at", "verified_at", "source_id", "source_version", "raw_reference", "normalization_version"):
            if not record.get(field_name):
                _add_issue(issues, "MISSING_PROVENANCE", f"missing provenance field: {field_name}", record, "warning")

    votes = _to_int(record.get("votes"))
    valid_votes = _to_int(record.get("valid_votes"))
    if votes is None or votes < 0:
        _add_issue(issues, "INVALID_VOTES", "votes must be a non-negative integer", record)
    if valid_votes is None or valid_votes < 0:
        _add_issue(issues, "INVALID_VALID_VOTES", "valid_votes must be a non-negative integer", record)
    if votes is not None and valid_votes is not None and valid_votes < votes:
        _add_issue(issues, "VALID_VOTES_TOO_SMALL", "valid_votes must be >= votes", record)

    share = _to_float(record.get("vote_share"))
    if share is None or not (0 <= share <= 1):
        _add_issue(issues, "INVALID_VOTE_SHARE", "vote_share must be between 0 and 1", record)
    elif votes is not None and valid_votes:
        expected = votes / valid_votes
        if abs(share - expected) > 0.001:
            _add_issue(issues, "VOTE_SHARE_MISMATCH", f"vote_share {share:.6f} != votes/valid_votes {expected:.6f}", record)

    turnout = _to_float(record.get("turnout"))
    if turnout is None:
        _add_issue(issues, "MISSING_TURNOUT", "turnout is required", record)
    elif not (0 <= turnout <= 1):
        _add_issue(issues, "INVALID_TURNOUT", "turnout must be between 0 and 1", record)

    if not str(record.get("candidate_name") or "").strip():
        _add_issue(issues, "EMPTY_CANDIDATE", "candidate_name must be non-empty", record)
    if not str(record.get("party") or "").strip():
        _add_issue(issues, "EMPTY_PARTY", "party must be non-empty", record)

    grade = str(record.get("source_grade") or "").upper()
    if grade not in VALID_SOURCE_GRADES:
        _add_issue(issues, "INVALID_SOURCE_GRADE", f"source_grade must be one of {sorted(VALID_SOURCE_GRADES)}", record)

    if not str(record.get("boundary_version") or "").strip():
        _add_issue(issues, "MISSING_BOUNDARY_VERSION", "boundary_version is required for cross-election comparison", record)

    level = record.get("level")
    if level and level not in VALID_LEVELS:
        _add_issue(issues, "INVALID_LEVEL", f"invalid level: {level}", record, "warning")

    if known_regions is not None:
        region = str(record.get("jurisdiction") or "")
        parent = str(record.get("parent_jurisdiction") or "")
        level = str(record.get("level") or "")
        if level in {"township_district", "village", "polling_station"}:
            if region not in known_regions:
                _add_issue(issues, "UNKNOWN_REGION", f"child region not found in geography registry: {region}", record)
        elif region not in known_regions and parent not in known_regions:
            _add_issue(issues, "UNKNOWN_REGION", f"region not found in geography registry: {region} / {parent}", record)

    errors = [issue for issue in issues if issue.severity == "error"]
    return ValidationReport(passed=not errors, issues=issues, checked_records=1)


def _group_key(record: Dict[str, Any]) -> tuple:
    # Regional-legislator contests may split one administrative district across
    # multiple electoral constituencies. Candidate votes from different contests
    # must never be summed against one constituency's valid-vote denominator.
    contest = ""
    if record.get("election_type") == "regional_legislator":
        codes = record.get("cec_codes") or {}
        if isinstance(codes, dict):
            contest = str(codes.get("election_district") or "")
        if not contest:
            contest = str(record.get("electoral_district") or "")
    return (
        record.get("election_type"),
        record.get("election_year"),
        record.get("parent_jurisdiction") or record.get("jurisdiction"),
        record.get("jurisdiction"),
        record.get("level"),
        contest,
    )


def validate_records(
    records: Sequence[Dict[str, Any]],
    known_regions: Optional[Set[str]] = None,
    require_provenance: bool = False,
) -> ValidationReport:
    all_issues: List[DataValidationIssue] = []
    seen_ids: Set[str] = set()
    for record in records:
        report = validate_record(record, known_regions=known_regions, require_provenance=require_provenance)
        all_issues.extend(report.issues)
        record_id = str(record.get("record_id") or "")
        if record_id:
            if record_id in seen_ids:
                _add_issue(all_issues, "DUPLICATE_RECORD_ID", f"duplicate record_id: {record_id}", record)
            seen_ids.add(record_id)

    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[_group_key(record)].append(record)

    for key, group in groups.items():
        valid_votes_values = [_to_int(item.get("valid_votes")) for item in group]
        valid_votes_values = [value for value in valid_votes_values if value is not None]
        if not valid_votes_values:
            continue
        reference_valid = max(set(valid_votes_values), key=valid_votes_values.count)
        total_votes = sum(_to_int(item.get("votes")) or 0 for item in group)
        tolerance = max(1, int(round(reference_valid * 0.01)))
        if abs(total_votes - reference_valid) > tolerance:
            first = group[0]
            _add_issue(
                all_issues,
                "AREA_VOTE_TOTAL_MISMATCH",
                f"sum(candidate votes)={total_votes} differs from valid_votes={reference_valid} for {key}",
                first,
            )

    errors = [issue for issue in all_issues if issue.severity == "error"]
    return ValidationReport(passed=not errors, issues=all_issues, checked_records=len(records))


POLL_REQUIRED_FIELDS = [
    "pollster",
    "commissioner",
    "method",
    "sample_size",
    "sample_frame",
    "sampling",
    "weighting",
    "field_start",
    "field_end",
    "publish_date",
    "moe_applicable",
    "undecided",
    "question_wording",
    "cross_tabs_available",
    "source",
    "source_grade",
]


def validate_poll_record(record: Dict[str, Any]) -> ValidationReport:
    """Validate a poll against the V1.1 poll schema/rules without inventing values."""
    issues: List[DataValidationIssue] = []
    for field_name in POLL_REQUIRED_FIELDS:
        if record.get(field_name) in (None, ""):
            _add_issue(issues, "MISSING_POLL_FIELD", f"missing required poll field: {field_name}", record)

    method = str(record.get("method") or "").lower()
    moe_applicable = record.get("moe_applicable")
    if method in {"online_closed", "online", "web_closed"}:
        if moe_applicable is True:
            _add_issue(issues, "MOE_INAPPLICABLE", "online_closed poll cannot set moe_applicable=true", record)
        if record.get("moe") not in (None, ""):
            _add_issue(issues, "MOE_INAPPLICABLE", "online_closed poll must not force a traditional MOE", record, "warning")
    else:
        if record.get("moe") in (None, "") and moe_applicable is True:
            _add_issue(issues, "MISSING_MOE", "moe_applicable=true requires a moe value", record)

    sample_size = _to_int(record.get("sample_size"))
    if sample_size is None or sample_size <= 0:
        _add_issue(issues, "INVALID_SAMPLE_SIZE", "sample_size must be a positive integer", record)

    undecided = _to_float(record.get("undecided"))
    if undecided is None or not (0 <= undecided <= 1):
        _add_issue(issues, "INVALID_UNDECIDED", "undecided must be between 0 and 1", record)

    grade = str(record.get("source_grade") or "").upper()
    if grade not in VALID_SOURCE_GRADES:
        _add_issue(issues, "INVALID_SOURCE_GRADE", f"source_grade must be one of {sorted(VALID_SOURCE_GRADES)}", record)

    errors = [issue for issue in issues if issue.severity == "error"]
    return ValidationReport(passed=not errors, issues=issues, checked_records=1)
