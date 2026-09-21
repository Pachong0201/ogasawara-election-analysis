"""Local-first election data loader with optional injected source adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .election_normalizer import normalize_records, validate_poll_record, validate_records
from .models import DataQuery, ValidationReport, utc_now_iso
from .source_registry import ElectionDataSource, OfflineBackend, SourceRegistry

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_MODES = {"auto", "online", "offline"}


def safe_component(value: str) -> str:
    return str(value).replace("/", "_").replace("\\", "_").strip() or "unknown"


def election_file_path(repo_root: Path, election_type: str, year: int, jurisdiction: str) -> Path:
    base = Path(repo_root) / "data" / "elections" / safe_component(election_type) / str(year)
    return base / f"{safe_component(jurisdiction)}.jsonl"


def current_candidates_path(repo_root: Path, jurisdiction: str) -> Path:
    return Path(repo_root) / "cache" / "candidates" / f"{safe_component(jurisdiction)}.jsonl"


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(payload, list):
            records.extend(payload)
        else:
            records.append(payload)
    return records


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


@dataclass
class LoadResult:
    records: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "missing"  # complete | filled | missing | invalid | insufficient
    source: str = "local"
    from_cache: bool = False
    persisted: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    validation: Optional[ValidationReport] = None

    @property
    def complete(self) -> bool:
        return self.status in {"complete", "filled"} and bool(self.records)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "from_cache": self.from_cache,
            "persisted": self.persisted,
            "records": self.records,
            "warnings": self.warnings,
            "errors": self.errors,
            "validation": self.validation.to_dict() if self.validation else None,
        }


class ElectionLoader:
    """Load local election records first; fetch only when local data is missing.

    The loader never invents records.  If no adapter can supply data, it
    returns ``status='missing'`` and records remain empty.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        source_registry: Optional[SourceRegistry] = None,
        retrieval_backend: Any = None,
        mode: str = "auto",
        config_path: Optional[Path] = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else DEFAULT_REPO_ROOT
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
        self.mode = mode
        self.retrieval_backend = retrieval_backend if retrieval_backend is not None else OfflineBackend()
        registry_path = Path(config_path) if config_path else self.repo_root / "config" / "data_sources.yaml"
        self.source_registry = source_registry or SourceRegistry(registry_path)

    def network_allowed(self) -> bool:
        if self.mode == "offline":
            return False
        if self.mode == "online":
            return True
        return bool(self.source_registry.adapters)

    def _load_local(self, query: DataQuery) -> LoadResult:
        path = election_file_path(self.repo_root, query.election_type, query.year, query.jurisdiction)
        if not path.exists():
            return LoadResult(status="missing", source="local", warnings=[f"local file not found: {path}"])
        try:
            records = load_jsonl(path)
        except ValueError as exc:
            return LoadResult(status="invalid", source="local", errors=[str(exc)])
        records = [record for record in self._filter_level(records, query.level) if self._matches_query(record, query)]
        if not records:
            return LoadResult(
                status="missing",
                source="local",
                warnings=[f"local file has no records for level={query.level}: {path}"],
            )
        validation = validate_records(records, require_provenance=False)
        if not validation.passed:
            return LoadResult(
                status="invalid",
                source="local",
                records=records,
                validation=validation,
                errors=[issue.message for issue in validation.issues if issue.severity == "error"],
            )
        return LoadResult(records=records, status="complete", source="local", from_cache=True, validation=validation)

    @staticmethod
    def _filter_level(records: List[Dict[str, Any]], level: Optional[str]) -> List[Dict[str, Any]]:
        if not level or level == "all":
            return records
        exact = [record for record in records if record.get("level") == level]
        if exact:
            return exact
        # Some local files may store county-level aggregates only; allow
        # national/county records when the caller explicitly asks for them.
        if level in {"national", "county_city"}:
            return [record for record in records if record.get("level") in {level, "national", "county_city"}]
        return exact

    @staticmethod
    def _matches_query(record: Dict[str, Any], query: DataQuery) -> bool:
        if record.get("election_type") != query.election_type:
            return False
        try:
            if int(record.get("election_year")) != int(query.year):
                return False
        except (TypeError, ValueError):
            return False
        region = str(record.get("jurisdiction") or "")
        parent = str(record.get("parent_jurisdiction") or "")
        return query.jurisdiction in {region, parent}

    def load_election(
        self,
        election_type: str,
        year: int,
        jurisdiction: str,
        level: str = "township_district",
    ) -> LoadResult:
        query = DataQuery(election_type=election_type, year=year, jurisdiction=jurisdiction, level=level)
        local = self._load_local(query)
        if local.complete:
            return local

        if not self.network_allowed():
            return LoadResult(
                records=[],
                status="missing",
                source="offline",
                warnings=[f"OFFLINE mode: no local data for {query.to_dict()}"],
            )

        warnings: List[str] = list(local.warnings)
        errors: List[str] = list(local.errors)
        for adapter in self.source_registry.sources_for(query):
            try:
                fetch_result = adapter.fetch(query)
            except Exception as exc:  # adapters are injected; do not crash the runtime
                warnings.append(f"adapter {getattr(adapter, 'source_id', '?')} fetch failed: {exc}")
                continue
            raw_records = list(fetch_result.records or [])
            warnings.extend(list(fetch_result.warnings or []))
            if not raw_records:
                warnings.append(f"adapter {getattr(adapter, 'source_id', '?')} returned no records")
                continue

            defaults = {
                "source_id": getattr(adapter, "source_id", ""),
                "source": getattr(adapter, "source_id", ""),
                "source_grade": getattr(adapter, "source_grade", "B"),
                "source_version": fetch_result.source_version or getattr(adapter, "source_version", ""),
                "raw_reference": fetch_result.raw_reference or "",
                "normalization_version": "v1.1.0",
            }
            normalized = normalize_records(raw_records, defaults=defaults)
            normalized = [record for record in self._filter_level(normalized, level) if self._matches_query(record, query)]
            validation = validate_records(normalized, require_provenance=False)
            if not validation.passed:
                errors.extend(issue.message for issue in validation.issues if issue.severity == "error")
                continue

            persist = bool(normalized) and self.mode != "offline"
            if persist:
                self._persist(query, normalized)
            return LoadResult(
                records=normalized,
                status="filled",
                source=fetch_result.source_id or getattr(adapter, "source_id", "adapter"),
                persisted=persist,
                warnings=warnings,
                errors=errors,
                validation=validation,
            )

        return LoadResult(
            records=[],
            status="missing",
            source="online_no_data" if self.network_allowed() else "offline",
            warnings=warnings,
            errors=errors,
        )

    def _persist(self, query: DataQuery, records: List[Dict[str, Any]]) -> None:
        path = election_file_path(self.repo_root, query.election_type, query.year, query.jurisdiction)
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for record in existing + records:
            key = str(record.get("record_id") or f"{record.get('candidate_name')}|{record.get('jurisdiction')}")
            merged[key] = record
        write_jsonl(path, merged.values())

    def load_current_candidates(self, jurisdiction: str) -> List[Dict[str, Any]]:
        path = current_candidates_path(self.repo_root, jurisdiction)
        if not path.exists():
            return []
        return load_jsonl(path)

    def load_polls(self, jurisdiction: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Load and validate cached polls; invalid records are marked failed."""
        base = self.repo_root / "cache" / "polls"
        valid: List[Dict[str, Any]] = []
        invalid: List[Dict[str, Any]] = []
        if not base.exists():
            return {"records": valid, "invalid": invalid}
        for path in sorted(base.glob("*.jsonl")):
            for record in load_jsonl(path):
                if jurisdiction:
                    region = str(record.get("region") or record.get("jurisdiction") or "")
                    if region and region != jurisdiction:
                        continue
                report = validate_poll_record(record)
                annotated = dict(record)
                annotated["validation_status"] = "passed" if report.passed else "failed"
                annotated["validation_errors"] = [issue.message for issue in report.issues if issue.severity == "error"]
                if report.passed:
                    valid.append(annotated)
                else:
                    invalid.append(annotated)
        return {"records": valid, "invalid": invalid}

    def local_years(self, election_type: str, jurisdiction: str, years: Iterable[int], level: str = "township_district") -> List[int]:
        found: List[int] = []
        for year in years:
            result = self._load_local(DataQuery(election_type=election_type, year=int(year), jurisdiction=jurisdiction, level=level))
            if result.complete:
                found.append(int(year))
        return sorted(found)
