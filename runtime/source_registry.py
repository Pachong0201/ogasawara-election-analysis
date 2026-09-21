"""Source registry and retrieval backends for V1.1.

The runtime deliberately does not hard-code web APIs.  Concrete web/search
capabilities are injected by the host environment as data-source adapters or
as a RetrievalBackend implementation.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .models import DataQuery, SourceFetchResult


class OfflineRetrievalError(RuntimeError):
    """Raised when OFFLINE mode is asked to perform network operations."""


class ElectionDataSource(ABC):
    """Unified adapter interface for election data sources."""

    source_id: str = "unknown"
    source_grade: str = "C"
    priority: int = 100

    @abstractmethod
    def supports(self, query: DataQuery) -> bool:
        """Return True when the adapter can answer the query."""

    @abstractmethod
    def fetch(self, query: DataQuery) -> SourceFetchResult:
        """Fetch raw records for a query."""

    def normalize(self, raw: Any) -> List[Dict[str, Any]]:
        """Normalize raw adapter output into election-record dictionaries."""
        if raw is None:
            return []
        if isinstance(raw, SourceFetchResult):
            return list(raw.records)
        if isinstance(raw, dict):
            if "records" in raw:
                return list(raw.get("records") or [])
            return [raw]
        if isinstance(raw, list):
            return list(raw)
        raise TypeError(f"unsupported raw type: {type(raw)!r}")

    def metadata(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_grade": self.source_grade,
            "priority": self.priority,
        }


class RetrievalBackend(ABC):
    """Abstract search/fetch backend injected by the running environment."""

    @abstractmethod
    def search(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, url: str) -> Any:
        raise NotImplementedError


class OfflineBackend(RetrievalBackend):
    """Backend used when no network/search capability is available."""

    def search(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        raise OfflineRetrievalError("OFFLINE mode: no retrieval backend is available")

    def fetch(self, url: str) -> Any:
        raise OfflineRetrievalError("OFFLINE mode: no retrieval backend is available")


class FixtureBackend(RetrievalBackend):
    """Deterministic backend for tests and local dry runs.

    ``responses`` may be keyed by a search query or by URL.  Values are
    returned as-is; callers are responsible for normalizing them.
    """

    def __init__(self, responses: Optional[Dict[str, Any]] = None, default: Any = None):
        self.responses = responses or {}
        self.default = default
        self.calls: List[Dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append({"operation": "search", "query": query, "kwargs": kwargs})
        if query in self.responses:
            value = self.responses[query]
        elif "*" in self.responses:
            value = self.responses["*"]
        else:
            value = self.default
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def fetch(self, url: str) -> Any:
        self.calls.append({"operation": "fetch", "url": url})
        if url in self.responses:
            return self.responses[url]
        if "*" in self.responses:
            return self.responses["*"]
        return self.default


class SourceRegistry:
    """Loads ``config/data_sources.yaml`` and stores runtime adapters."""

    def __init__(self, config_path: Optional[Path] = None, adapters: Optional[Iterable[ElectionDataSource]] = None):
        self.config_path = Path(config_path) if config_path else None
        self.config: Dict[str, Any] = {}
        self.adapters: List[ElectionDataSource] = []
        if self.config_path and self.config_path.exists():
            with self.config_path.open(encoding="utf-8") as fh:
                self.config = yaml.safe_load(fh) or {}
        if adapters:
            for adapter in adapters:
                self.register(adapter)

    def register(self, adapter: ElectionDataSource) -> None:
        self.adapters.append(adapter)

    def sources_for(self, query: DataQuery) -> List[ElectionDataSource]:
        supported = [adapter for adapter in self.adapters if getattr(adapter, "supports", lambda q: False)(query)]
        return sorted(supported, key=lambda adapter: getattr(adapter, "priority", 100))

    def metadata(self) -> Dict[str, Any]:
        registered = [adapter.metadata() for adapter in self.adapters]
        return {
            "source_registry_version": self.config.get("version"),
            "registered_adapters": registered,
            "configured_election_sources": self.config.get("election_results", {}),
            "adapters": self.config.get("adapters", {}),
        }


class JSONFileElectionDataSource(ElectionDataSource):
    """Small local adapter useful for tests and injected fixtures.

    It reads a JSON/JSONL file whose path is mapped by query in
    ``path_map``.  This is not a web crawler.
    """

    def __init__(self, source_id: str, path_map: Dict[str, str], source_grade: str = "B", priority: int = 50):
        self.source_id = source_id
        self.source_grade = source_grade
        self.priority = priority
        self.path_map = path_map

    def _key(self, query: DataQuery) -> str:
        return f"{query.election_type}|{query.year}|{query.jurisdiction}|{query.level}"

    def supports(self, query: DataQuery) -> bool:
        return self._key(query) in self.path_map

    def fetch(self, query: DataQuery) -> SourceFetchResult:
        path = Path(self.path_map[self._key(query)])
        records: List[Dict[str, Any]] = []
        if not path.exists():
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=[f"fixture path not found: {path}"],
                source_version="fixture",
                raw_reference=str(path),
            )
        text = path.read_text(encoding="utf-8").strip()
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    records.append(json.loads(line))
        else:
            payload = json.loads(text or "[]")
            records = payload if isinstance(payload, list) else payload.get("records", [])
        return SourceFetchResult(
            source_id=self.source_id,
            source_grade=self.source_grade,
            records=records,
            source_version="fixture",
            raw_reference=str(path),
        )

    def normalize(self, raw: Any) -> List[Dict[str, Any]]:
        return super().normalize(raw)
