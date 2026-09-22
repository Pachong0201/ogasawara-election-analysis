"""Local-first election data loader with optional injected source adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import yaml

from .election_normalizer import normalize_records, validate_poll_record, validate_records
from .models import DataQuery, ValidationReport
from .source_registry import OfflineBackend, SourceRegistry

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
    status: str = "missing"
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

    The loader never invents records. If no adapter can supply data, it
    returns status=missing and records remain empty.
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
        return bool(
            self.source_registry.adapters
            or self.source_registry.candidate_adapters
            or self.source_registry.poll_adapters
        )

    def _known_regions(self) -> Optional[Set[str]]:
        """Load the geography registry when present."""
        base = self.repo_root / "data" / "geography" / "administrative_areas"
        if not base.exists():
            return None
        names: Set[str] = set()
        for path in sorted(list(base.glob("*.yaml")) + list(base.glob("*.yml")) + list(base.glob("*.json"))):
            try:
                if path.suffix == ".json":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                else:
                    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payload = payload.get("regions") or payload.get("administrative_areas") or [payload]
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                for field_name in ("name", "region_id"):
                    value = str(item.get(field_name) or "").strip()
                    if value:
                        names.add(value)
        return names or None

    def _validate(self, records: List[Dict[str, Any]], require_provenance: bool = False) -> ValidationReport:
        return validate_records(
            records,
            known_regions=self._known_regions(),
            require_provenance=require_provenance,
        )

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
        validation = self._validate(records, require_provenance=False)
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


    def _sync_geography_from_records(self, query: DataQuery, records: List[Dict[str, Any]]) -> None:
        """Persist minimal official geography discovered from validated election records.

        The geography layer stores every observed official name as a time-scoped
        record. This allows harmless renames (for example 鎮→市) to coexist
        across historical terms without making older cached election files fail
        validation on later runs.
        """
        if not records:
            return
        base = self.repo_root / "data" / "geography" / "administrative_areas"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"cec_{safe_component(query.jurisdiction)}.yaml"

        existing: Dict[str, Any] = {"version": "1.2.0", "source": "cec_open_data", "regions": []}
        if path.exists():
            try:
                existing = yaml.safe_load(path.read_text(encoding="utf-8")) or existing
            except Exception:
                existing = {"version": "1.2.0", "source": "cec_open_data", "regions": []}

        rows = list(existing.get("regions") or [])
        seen = {
            (
                str(item.get("name") or ""),
                str(item.get("region_id") or ""),
                str(item.get("valid_from") or ""),
            )
            for item in rows
            if isinstance(item, dict)
        }

        boundary_version = str(records[0].get("boundary_version") or "cec-township-2014-2024-v1")
        source_version = str(records[0].get("source_version") or "")
        verified_at = str(records[0].get("verified_at") or "")
        official_parent = str(records[0].get("official_parent_jurisdiction") or query.jurisdiction)

        county_entry = {
            "region_id": f"cec-parent:{query.jurisdiction}",
            "name": query.jurisdiction,
            "official_name": official_parent,
            "level": "county_city",
            "parent": "TW",
            "valid_from": f"{query.year}-01-01",
            "valid_to": None,
            "boundary_version": boundary_version,
            "source_id": "cec_open_data",
            "source_grade": "A",
            "source_version": source_version,
            "last_verified_at": verified_at,
        }
        key = (county_entry["name"], county_entry["region_id"], county_entry["valid_from"])
        if key not in seen:
            rows.append(county_entry)
            seen.add(key)

        for record in records:
            if record.get("level") != "township_district":
                continue
            name = str(record.get("jurisdiction") or "").strip()
            region_id = str(record.get("region_id") or "").strip()
            if not name:
                continue
            if not region_id:
                region_id = f"observed:{safe_component(query.jurisdiction)}:{safe_component(name)}"
            item = {
                "region_id": region_id,
                "name": name,
                "level": "township_district",
                "parent": query.jurisdiction,
                "valid_from": f"{query.year}-01-01",
                "valid_to": None,
                "boundary_version": str(record.get("boundary_version") or boundary_version),
                "source_id": "cec_open_data",
                "source_grade": "A",
                "source_version": str(record.get("source_version") or source_version),
                "last_verified_at": str(record.get("verified_at") or verified_at),
            }
            key = (item["name"], item["region_id"], item["valid_from"])
            if key not in seen:
                rows.append(item)
                seen.add(key)

        payload = {
            "version": "1.2.0",
            "source": "cec_open_data",
            "jurisdiction": query.jurisdiction,
            "regions": rows,
        }
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

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
        if local.status == "invalid":
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
            except Exception as exc:
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
                "normalization_version": "v1.2.0",
            }
            normalized = normalize_records(raw_records, defaults=defaults)
            normalized = [record for record in self._filter_level(normalized, level) if self._matches_query(record, query)]
            validation = self._validate(normalized, require_provenance=False)
            if not validation.passed:
                errors.extend(issue.message for issue in validation.issues if issue.severity == "error")
                continue

            persist = bool(normalized) and self.mode != "offline"
            if persist:
                self._persist(query, normalized)
                self._sync_geography_from_records(query, normalized)
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

    @staticmethod
    def _persistence_key(record: Dict[str, Any]) -> str:
        record_id = str(record.get("record_id") or "").strip()
        if record_id:
            return record_id
        return "{}|{}|{}|{}|{}".format(
            record.get("election_type", "unknown"),
            record.get("election_year", "unknown"),
            record.get("parent_jurisdiction") or record.get("jurisdiction", "unknown"),
            record.get("jurisdiction", "unknown"),
            record.get("candidate_id") or record.get("candidate_name", "unknown"),
        )

    def _persist(self, query: DataQuery, records: List[Dict[str, Any]]) -> None:
        path = election_file_path(self.repo_root, query.election_type, query.year, query.jurisdiction)
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for record in existing + records:
            merged[self._persistence_key(record)] = record
        write_jsonl(path, merged.values())

    def load_current_candidates(self, jurisdiction: str) -> List[Dict[str, Any]]:
        path = current_candidates_path(self.repo_root, jurisdiction)
        if not path.exists():
            return []
        return load_jsonl(path)

    @staticmethod
    def _candidate_record_valid(record: Dict[str, Any]) -> bool:
        return all(
            [
                str(record.get("name") or record.get("candidate_name") or "").strip(),
                str(record.get("candidate_status") or "").strip(),
                str(record.get("source_grade") or "").strip(),
                str(record.get("last_verified_at") or "").strip(),
                str(record.get("election_type") or "").strip(),
                record.get("election_year") is not None,
            ]
        )

    def refresh_current_candidates(
        self,
        jurisdiction: str,
        election_type: str,
        target_year: int,
    ) -> LoadResult:
        """Refresh the current candidate cache from registered official sources."""
        if not self.network_allowed():
            return LoadResult(
                status="missing",
                source="offline",
                warnings=["OFFLINE mode: current candidate refresh was not performed"],
            )

        warnings: List[str] = []
        for adapter in self.source_registry.current_candidate_sources_for(
            jurisdiction, election_type, int(target_year)
        ):
            try:
                fetched = adapter.fetch(jurisdiction, election_type, int(target_year))
            except Exception as exc:
                warnings.append(
                    f"candidate adapter {getattr(adapter, 'source_id', '?')} fetch failed: {exc}"
                )
                continue

            warnings.extend(list(fetched.warnings or []))
            normalized: List[Dict[str, Any]] = []
            for raw in fetched.records or []:
                record = dict(raw)
                record.setdefault("source_id", getattr(adapter, "source_id", ""))
                record.setdefault("source_grade", getattr(adapter, "source_grade", "C"))
                record.setdefault("evidence_grade", record.get("source_grade"))
                record.setdefault("retrieved_at", fetched.retrieved_at)
                record.setdefault("last_verified_at", fetched.retrieved_at)
                record.setdefault("election_type", election_type)
                record.setdefault("election_year", int(target_year))
                record.setdefault("jurisdiction", jurisdiction)
                record.setdefault("candidate_status", "announced")
                record.setdefault("name", record.get("candidate_name", ""))
                record.setdefault("candidate_name", record.get("name", ""))
                if self._candidate_record_valid(record):
                    normalized.append(record)
                else:
                    warnings.append(
                        f"candidate record failed minimum validation: {record.get('name') or record.get('candidate_name') or '?'}"
                    )

            if not normalized:
                continue

            path = current_candidates_path(self.repo_root, jurisdiction)
            existing = load_jsonl(path) if path.exists() else []
            keep = [
                item
                for item in existing
                if not (
                    str(item.get("election_type") or "") == election_type
                    and int(item.get("election_year") or 0) == int(target_year)
                )
            ]
            write_jsonl(path, keep + normalized)
            return LoadResult(
                records=normalized,
                status="filled",
                source=fetched.source_id or getattr(adapter, "source_id", "candidate_adapter"),
                persisted=True,
                warnings=warnings,
            )

        return LoadResult(
            status="missing",
            source="online_no_data",
            warnings=warnings or [
                f"no registered current-candidate source supports {jurisdiction} {election_type} {target_year}"
            ],
        )

    def refresh_polls(
        self,
        jurisdiction: str,
        election_type: str,
        target_year: int,
    ) -> LoadResult:
        """Refresh poll cache from registered primary poll sources.

        Invalid or method-incomplete records are not persisted.
        """
        if not self.network_allowed():
            return LoadResult(
                status="missing",
                source="offline",
                warnings=["OFFLINE mode: poll refresh was not performed"],
            )

        warnings: List[str] = []
        collected: List[Dict[str, Any]] = []
        source_names: List[str] = []

        for adapter in self.source_registry.poll_sources_for(
            jurisdiction, election_type, int(target_year)
        ):
            try:
                fetched = adapter.fetch(jurisdiction, election_type, int(target_year))
            except Exception as exc:
                warnings.append(
                    f"poll adapter {getattr(adapter, 'source_id', '?')} fetch failed: {exc}"
                )
                continue

            warnings.extend(list(fetched.warnings or []))
            for raw in fetched.records or []:
                record = dict(raw)
                record.setdefault("source_id", getattr(adapter, "source_id", ""))
                record.setdefault("source_grade", getattr(adapter, "source_grade", "C"))
                record.setdefault("retrieved_at", fetched.retrieved_at)
                record.setdefault("last_verified_at", fetched.retrieved_at)
                record.setdefault("jurisdiction", jurisdiction)
                record.setdefault("election_type", election_type)
                record.setdefault("election_year", int(target_year))
                report = validate_poll_record(record)
                if not report.passed:
                    warnings.append(
                        "poll record failed validation "
                        + str(record.get("poll_id") or "?")
                        + ": "
                        + "; ".join(
                            issue.message
                            for issue in report.issues
                            if issue.severity == "error"
                        )
                    )
                    continue
                collected.append(record)
            if fetched.records:
                source_names.append(fetched.source_id or getattr(adapter, "source_id", ""))

        if not collected:
            return LoadResult(
                status="missing",
                source="online_no_data",
                warnings=warnings or [
                    f"no registered poll source returned valid records for {jurisdiction}"
                ],
            )

        base = self.repo_root / "cache" / "polls"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{safe_component(jurisdiction)}.jsonl"
        existing = load_jsonl(path) if path.exists() else []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in existing + collected:
            key = str(item.get("poll_id") or "").strip()
            if not key:
                continue
            merged[key] = item
        write_jsonl(path, merged.values())

        return LoadResult(
            records=collected,
            status="filled",
            source=",".join(sorted(set(source_names))) or "poll_adapter",
            persisted=True,
            warnings=warnings,
        )

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
