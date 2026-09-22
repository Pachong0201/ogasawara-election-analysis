"""Shared runtime data models for V1.4."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.4"
NORMALIZATION_VERSION = "v1.2.0"


def utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp in ISO format."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_date(value: Any) -> Optional[dt.date]:
    """Best-effort parse for YYYY-MM-DD or ISO date-time strings."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def as_jsonable(value: Any) -> Any:
    """Convert dataclasses, dates and enums into JSON-friendly structures."""
    if dataclasses.is_dataclass(value):
        return {k: as_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): as_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [as_jsonable(v) for v in value]
    return value


@dataclass
class ElectionTask:
    election_type: str
    target_year: int
    jurisdiction: str
    analysis_level: str = "township_district"
    candidates: List[str] = field(default_factory=list)
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            year = self.target_year
            slug = self.jurisdiction.replace(" ", "_")
            self.task_id = f"{self.election_type}-{year}-{slug}"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ElectionTask":
        return cls(
            election_type=payload["election_type"],
            target_year=int(payload["target_year"]),
            jurisdiction=payload["jurisdiction"],
            analysis_level=payload.get("analysis_level", "township_district"),
            candidates=list(payload.get("candidates") or []),
            task_id=payload.get("task_id", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return as_jsonable(self)


@dataclass
class DataQuery:
    election_type: str
    year: int
    jurisdiction: str
    level: str = "township_district"
    candidate: Optional[str] = None

    @classmethod
    def from_task(cls, task: ElectionTask, year: int) -> "DataQuery":
        return cls(
            election_type=task.election_type,
            year=year,
            jurisdiction=task.jurisdiction,
            level=task.analysis_level,
        )

    def to_dict(self) -> Dict[str, Any]:
        return as_jsonable(self)


@dataclass
class ElectionRecord:
    record_id: str
    election_type: str
    election_year: int
    jurisdiction: str
    level: str
    candidate_name: str
    party: str
    votes: int
    valid_votes: int
    vote_share: float
    turnout: float
    source: str
    source_grade: str
    boundary_version: str
    time_scope: str = ""
    parent_jurisdiction: str = ""
    candidate_id: str = ""
    election_date: str = ""
    region_scope: str = ""
    party_alliance: str = ""
    candidate_count: Optional[int] = None
    electorate: Optional[int] = None
    elected: Optional[bool] = None
    source_reference: str = ""
    retrieved_at: str = ""
    verified_at: str = ""
    source_id: str = ""
    source_version: str = ""
    raw_reference: str = ""
    normalization_version: str = NORMALIZATION_VERSION
    notes: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ElectionRecord":
        known = {f.name for f in dataclasses.fields(cls)} - {"extra"}
        data = {k: payload.get(k) for k in known}
        extra = {k: v for k, v in payload.items() if k not in known}
        if data.get("record_id") is None:
            data["record_id"] = "{}|{}|{}|{}|{}".format(
                payload.get("election_type", "unknown"),
                payload.get("election_year", "unknown"),
                payload.get("parent_jurisdiction") or payload.get("jurisdiction", "unknown"),
                payload.get("jurisdiction", "unknown"),
                payload.get("candidate_id") or payload.get("candidate_name", "unknown"),
            )
        if data.get("vote_share") is None:
            valid = payload.get("valid_votes")
            votes = payload.get("votes")
            try:
                data["vote_share"] = float(votes) / float(valid) if valid else 0.0
            except (TypeError, ZeroDivisionError):
                data["vote_share"] = 0.0
        for numeric in ("votes", "valid_votes", "candidate_count", "electorate"):
            if data.get(numeric) is not None:
                try:
                    data[numeric] = int(data[numeric])
                except (TypeError, ValueError):
                    pass
        for numeric in ("vote_share", "turnout"):
            if data.get(numeric) is not None:
                try:
                    data[numeric] = float(data[numeric])
                except (TypeError, ValueError):
                    pass
        if data.get("elected") is not None:
            data["elected"] = bool(data["elected"])
        record = cls(**data)  # type: ignore[arg-type]
        record.extra = extra
        return record

    def to_dict(self) -> Dict[str, Any]:
        base = as_jsonable(self)
        extra = base.pop("extra", {}) or {}
        base.update(extra)
        return base

    def rounded(self, decimals: int = 6) -> Dict[str, Any]:
        payload = self.to_dict()
        for key in ("vote_share", "turnout"):
            if isinstance(payload.get(key), float):
                payload[key] = round(payload[key], decimals)
        return payload


@dataclass
class DataValidationIssue:
    code: str
    message: str
    record_id: str = ""
    severity: str = "error"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return as_jsonable(self)


@dataclass
class ValidationReport:
    passed: bool
    issues: List[DataValidationIssue] = field(default_factory=list)
    checked_records: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_status": "passed" if self.passed else "failed",
            "passed": self.passed,
            "checked_records": self.checked_records,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class SourceFetchResult:
    source_id: str
    source_grade: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    retrieved_at: str = field(default_factory=utc_now_iso)
    source_version: str = ""
    raw_reference: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return as_jsonable(self)


@dataclass
class MetricResult:
    metric: str
    region: str = ""
    observed_value: Optional[float] = None
    baseline_value: Optional[float] = None
    residual: Optional[float] = None
    baseline_method: str = ""
    threshold_status: str = "not_evaluated"
    local_explanation_required: bool = False
    warnings: List[str] = field(default_factory=list)
    metric_method: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return as_jsonable(self)


@dataclass
class ReadinessReport:
    task: ElectionTask
    status: str
    required: Dict[str, Any] = field(default_factory=dict)
    available: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    mode: str = "auto"
    generated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        payload = as_jsonable(self)
        if self.status == "INSUFFICIENT":
            payload["analysis_status"] = "insufficient_data"
        elif self.status == "PARTIAL":
            payload["analysis_status"] = "partial_analysis"
        else:
            payload["analysis_status"] = "ready"
        return payload

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class AnalysisContext:
    analysis_context: Dict[str, Any]
    analysis_manifest: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_context": as_jsonable(self.analysis_context),
            "analysis_manifest": as_jsonable(self.analysis_manifest),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
