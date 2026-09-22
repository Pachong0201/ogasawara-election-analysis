"""Bridge host-agent web retrieval into the local Skill runtime.

The Skill does not bundle a generic search engine. A host such as ChatGPT,
Codex, or another agent may perform web retrieval using its own tools and
write structured results to a JSON/JSONL inbox. This backend exposes those
results through the existing RetrievalBackend interface.

Security / evidence rule:
- missing or invalid source grades are downgraded to E;
- records default to verification_status=lead_only;
- this backend never promotes a lead into knowledge/ by itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import utc_now_iso
from .source_registry import RetrievalBackend


VALID_GRADES = {"A", "B", "C", "D", "E"}


class HostRetrievalInboxError(ValueError):
    pass


def _load_payload(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise HostRetrievalInboxError(f"retrieval inbox does not exist: {path}")

    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("results") or payload.get("records") or [payload]
        if not isinstance(payload, list):
            raise HostRetrievalInboxError("JSON inbox must contain an object or list")
        return [dict(item) for item in payload if isinstance(item, dict)]

    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HostRetrievalInboxError(
                f"invalid JSONL at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise HostRetrievalInboxError(
                f"JSONL record at {path}:{line_number} must be an object"
            )
        records.append(dict(item))
    return records


def normalize_host_result(record: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(record)
    grade = str(item.get("source_grade") or "").upper()
    if grade not in VALID_GRADES:
        grade = "E"

    query = str(item.get("query") or item.get("research_question") or "").strip()
    questions = item.get("research_questions") or []
    if isinstance(questions, str):
        questions = [questions]
    questions = [str(value).strip() for value in questions if str(value).strip()]
    if query and query not in questions:
        questions.append(query)

    url = str(item.get("url") or item.get("reference") or "").strip()
    summary = str(item.get("summary") or item.get("title") or "").strip()

    return {
        **item,
        "query": query,
        "research_questions": questions,
        "url": url,
        "summary": summary,
        "source_id": str(item.get("source_id") or "host_web_retrieval"),
        "source_grade": grade,
        "verification_status": str(
            item.get("verification_status") or "lead_only"
        ),
        "retrieved_at": str(item.get("retrieved_at") or utc_now_iso()),
    }


class HostRetrievalBackend(RetrievalBackend):
    """Read host-provided web-search results from a JSON/JSONL inbox."""

    def __init__(
        self,
        inbox_path: Optional[Path] = None,
        records: Optional[Iterable[Dict[str, Any]]] = None,
    ):
        if inbox_path is None and records is None:
            raise HostRetrievalInboxError(
                "HostRetrievalBackend requires inbox_path or records"
            )
        raw = _load_payload(Path(inbox_path)) if inbox_path is not None else list(records or [])
        self.records = [normalize_host_result(record) for record in raw]
        self.calls: List[Dict[str, Any]] = []

    @staticmethod
    def _matches(record: Dict[str, Any], query: str) -> bool:
        declared = str(record.get("query") or "").strip()
        questions = record.get("research_questions") or []
        if isinstance(questions, str):
            questions = [questions]
        if declared == "*":
            return True
        if declared and declared == query:
            return True
        return query in {str(value).strip() for value in questions}

    def search(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append({"operation": "search", "query": query, "kwargs": kwargs})
        return [
            dict(record)
            for record in self.records
            if self._matches(record, str(query))
        ]

    def fetch(self, url: str) -> Any:
        self.calls.append({"operation": "fetch", "url": url})
        for record in self.records:
            if str(record.get("url") or "") != str(url):
                continue
            if "content" in record:
                return record["content"]
            if "evidence" in record:
                return record["evidence"]
            return dict(record)
        return None

    def metadata(self) -> Dict[str, Any]:
        return {
            "backend": "host_retrieval_inbox",
            "record_count": len(self.records),
            "lead_only_default": True,
        }
