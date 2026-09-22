"""Live campaign-state assembly for V1.4.

This module turns time-sensitive campaign evidence into a dated snapshot.
It does not predict winners. It answers what changed in the campaign, which
changes require local follow-up, and whether same-series polling moved.

Host web/search results remain evidence leads unless they are explicitly
verified. D/E-grade material may trigger verification questions but cannot
become structural evidence by itself.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .election_loader import load_jsonl, safe_component, write_jsonl
from .models import ElectionTask, parse_date, utc_now_iso
from .source_registry import OfflineBackend, OfflineRetrievalError, RetrievalBackend


VALID_GRADES = {"A", "B", "C", "D", "E"}
SIGNIFICANT_EVENT_TYPES = {
    "nomination",
    "primary_result",
    "candidate_registration",
    "candidate_withdrawal",
    "party_cooperation",
    "alliance_change",
    "endorsement",
    "organization_change",
    "campaign_hq",
    "campaign_personnel",
    "policy_launch",
    "major_issue",
    "controversy",
    "judicial_event",
    "local_major_event",
    "debate",
}
KEYWORD_EVENT_TYPES = {
    "提名": "nomination",
    "初选": "primary_result",
    "登记": "candidate_registration",
    "登記": "candidate_registration",
    "退选": "candidate_withdrawal",
    "退選": "candidate_withdrawal",
    "合作": "party_cooperation",
    "联盟": "alliance_change",
    "聯盟": "alliance_change",
    "支持": "endorsement",
    "站台": "endorsement",
    "站臺": "endorsement",
    "竞选总部": "campaign_hq",
    "競選總部": "campaign_hq",
    "竞选团队": "campaign_personnel",
    "競選團隊": "campaign_personnel",
    "政策": "policy_launch",
    "争议": "controversy",
    "爭議": "controversy",
    "司法": "judicial_event",
    "起诉": "judicial_event",
    "起訴": "judicial_event",
    "辩论": "debate",
    "辯論": "debate",
}


def _date_from_record(record: Dict[str, Any]) -> Optional[dt.date]:
    for field in ("date", "event_date", "published_at", "publish_date", "field_end"):
        value = parse_date(record.get(field))
        if value is not None:
            return value
    return None


def _candidate_name(record: Dict[str, Any]) -> str:
    return str(record.get("candidate_name") or record.get("name") or "").strip()


def _candidate_key(record: Dict[str, Any]) -> str:
    return str(record.get("candidate_id") or _candidate_name(record)).strip()


def _support_value(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number > 1.0:
        number /= 100.0
    if number > 1.0:
        return None
    return number


def _normalized_question(value: Any) -> str:
    return " ".join(str(value or "").split())


def _poll_series_key(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(record.get("pollster") or "").strip(),
        str(record.get("method") or "").strip(),
        str(record.get("sample_frame") or "").strip(),
        _normalized_question(record.get("question_wording")),
    )


def build_same_series_poll_trends(polls: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compare polls only within the same pollster/method/frame/question series."""

    grouped: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for raw in polls or []:
        record = dict(raw)
        if record.get("campaign_claim"):
            continue
        key = _poll_series_key(record)
        if not all(key):
            continue
        grouped.setdefault(key, []).append(record)

    output: List[Dict[str, Any]] = []
    for key, records in grouped.items():
        records.sort(
            key=lambda item: (
                parse_date(item.get("field_end")) or dt.date.min,
                parse_date(item.get("publish_date")) or dt.date.min,
                str(item.get("poll_id") or ""),
            )
        )
        if len(records) < 2:
            continue

        observations: List[Dict[str, Any]] = []
        for record in records:
            support: Dict[str, float] = {}
            for item in record.get("candidate_support") or []:
                if not isinstance(item, dict):
                    continue
                candidate = str(item.get("candidate") or item.get("name") or "").strip()
                value = _support_value(item.get("support") if "support" in item else item.get("value"))
                if candidate and value is not None:
                    support[candidate] = value
            observations.append(
                {
                    "poll_id": record.get("poll_id"),
                    "field_start": record.get("field_start"),
                    "field_end": record.get("field_end"),
                    "publish_date": record.get("publish_date"),
                    "undecided": record.get("undecided"),
                    "candidate_support": support,
                    "freshness_status": record.get("freshness_status"),
                }
            )

        deltas: List[Dict[str, Any]] = []
        for previous, current in zip(observations, observations[1:]):
            candidates = sorted(
                set(previous["candidate_support"]) & set(current["candidate_support"])
            )
            changes: Dict[str, float] = {}
            for candidate in candidates:
                changes[candidate] = round(
                    (
                        current["candidate_support"][candidate]
                        - previous["candidate_support"][candidate]
                    )
                    * 100.0,
                    1,
                )
            if not changes:
                continue
            deltas.append(
                {
                    "from_poll_id": previous.get("poll_id"),
                    "to_poll_id": current.get("poll_id"),
                    "from_field_end": previous.get("field_end"),
                    "to_field_end": current.get("field_end"),
                    "candidate_change_pp": changes,
                    "interpretation_rule": (
                        "same pollster/method/sample-frame/question only; "
                        "point-estimate change is descriptive, not a win/loss forecast"
                    ),
                }
            )

        if not deltas:
            continue

        seed = "|".join(key)
        output.append(
            {
                "series_id": "poll-series-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16],
                "pollster": key[0],
                "method": key[1],
                "sample_frame": key[2],
                "question_wording": key[3],
                "observation_count": len(observations),
                "observations": observations,
                "deltas": deltas,
                "latest_poll_id": observations[-1].get("poll_id"),
                "latest_field_end": observations[-1].get("field_end"),
            }
        )
    return output


class CampaignStateBuilder:
    """Build, compare and optionally persist a dated campaign snapshot."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        retrieval_backend: Optional[RetrievalBackend] = None,
        mode: str = "auto",
        config_path: Optional[Path] = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.retrieval_backend = retrieval_backend or OfflineBackend()
        self.mode = mode
        self.config_path = (
            Path(config_path)
            if config_path
            else self.repo_root / "config" / "runtime.yaml"
        )
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        with self.config_path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _settings(self) -> Dict[str, Any]:
        return self.config.get("campaign_state", {}) or {}

    def build_queries(self, task: ElectionTask, as_of: dt.date) -> List[Dict[str, Any]]:
        windows = self._settings().get("windows_days") or [7, 14, 30]
        query_template = (
            "{jurisdiction} {year} {election_type} 选战最近{days}日："
            "候选人、提名登记、竞选组织、地方人物公开支持、政党合作、"
            "政策发表、争议、司法事件、重大地方议题"
        )
        return [
            {
                "window_days": int(days),
                "as_of": as_of.isoformat(),
                "query": query_template.format(
                    jurisdiction=task.jurisdiction,
                    year=task.target_year,
                    election_type=task.election_type,
                    days=int(days),
                ),
            }
            for days in windows
        ]

    @staticmethod
    def _infer_claim_type(record: Dict[str, Any]) -> str:
        declared = str(record.get("claim_type") or record.get("event_type") or "").strip()
        if declared:
            return declared
        text = " ".join(
            str(record.get(field) or "")
            for field in ("title", "summary", "evidence", "claim")
        )
        for keyword, claim_type in KEYWORD_EVENT_TYPES.items():
            if keyword in text:
                return claim_type
        return "campaign_event"

    @staticmethod
    def _event_id(record: Dict[str, Any]) -> str:
        explicit = str(record.get("event_id") or record.get("claim_id") or "").strip()
        if explicit:
            return explicit
        seed = "|".join(
            [
                str(record.get("date") or record.get("published_at") or ""),
                str(record.get("url") or record.get("source_reference") or ""),
                str(record.get("title") or ""),
                str(record.get("summary") or record.get("claim") or ""),
            ]
        )
        return "event-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]

    def _normalize_event(
        self,
        raw: Dict[str, Any],
        task: ElectionTask,
        default_verified: bool = False,
    ) -> Optional[Dict[str, Any]]:
        record = dict(raw)
        date_value = _date_from_record(record)
        if date_value is None:
            return None

        grade = str(record.get("source_grade") or "E").upper()
        if grade not in VALID_GRADES:
            grade = "E"
        verification = str(
            record.get("verification_status")
            or ("verified" if default_verified else "lead_only")
        )
        claim_type = self._infer_claim_type(record)

        independent_sources = int(record.get("independent_source_count") or 0)
        structural_eligible = (
            verification == "verified"
            and (
                grade in {"A", "B"}
                or (grade == "C" and independent_sources >= 2)
            )
        )

        normalized = {
            **record,
            "event_id": self._event_id(record),
            "date": date_value.isoformat(),
            "jurisdiction": str(
                record.get("jurisdiction")
                or record.get("county")
                or record.get("region")
                or task.jurisdiction
            ),
            "election_type": str(record.get("election_type") or task.election_type),
            "election_year": int(record.get("election_year") or task.target_year),
            "claim_type": claim_type,
            "speaker": str(record.get("speaker") or ""),
            "source_id": str(record.get("source_id") or "campaign_event"),
            "source_grade": grade,
            "verification_status": verification,
            "structural_evidence_eligible": structural_eligible,
            "retrieved_at": str(record.get("retrieved_at") or utc_now_iso()),
        }
        return normalized

    def _retrieve_events(
        self,
        task: ElectionTask,
        as_of: dt.date,
        allow_online: bool,
    ) -> Dict[str, Any]:
        if not allow_online or self.mode == "offline":
            return {"verified_events": [], "leads": [], "warnings": [], "queries": []}

        verified_events: Dict[str, Dict[str, Any]] = {}
        leads: Dict[str, Dict[str, Any]] = {}
        warnings: List[str] = []
        queries = self.build_queries(task, as_of)

        for item in queries:
            query = item["query"]
            try:
                results = self.retrieval_backend.search(
                    query,
                    campaign_state=True,
                    window_days=item["window_days"],
                    as_of=item["as_of"],
                    jurisdiction=task.jurisdiction,
                )
            except OfflineRetrievalError as exc:
                warnings.append(str(exc))
                continue

            for raw in results or []:
                if not isinstance(raw, dict):
                    raw = {"summary": str(raw)}
                normalized = self._normalize_event(raw, task)
                if normalized is None:
                    warnings.append(
                        "campaign retrieval result lacked a usable event/publish date and was ignored"
                    )
                    continue
                if normalized["verification_status"] == "verified":
                    verified_events[normalized["event_id"]] = normalized
                else:
                    leads[normalized["event_id"]] = normalized

        return {
            "verified_events": list(verified_events.values()),
            "leads": list(leads.values()),
            "warnings": warnings,
            "queries": queries,
        }

    def _cache_verified_events(
        self,
        task: ElectionTask,
        events: Iterable[Dict[str, Any]],
    ) -> None:
        events = list(events or [])
        if not events:
            return
        path = (
            self.repo_root
            / "cache"
            / "events"
            / f"{safe_component(task.jurisdiction)}.jsonl"
        )
        existing = load_jsonl(path) if path.exists() else []
        merged: Dict[str, Dict[str, Any]] = {}
        for raw in existing + events:
            normalized = self._normalize_event(raw, task, default_verified=True)
            if normalized is None:
                continue
            merged[normalized["event_id"]] = normalized
        write_jsonl(path, merged.values())

    @staticmethod
    def _campaign_stage(candidates: Iterable[Dict[str, Any]]) -> str:
        statuses = {
            str(record.get("candidate_status") or "").strip()
            for record in candidates or []
            if str(record.get("candidate_status") or "").strip()
        }
        if "registered" in statuses:
            return "registered_general_campaign"
        if "nominated" in statuses:
            return "nomination_complete"
        if statuses & {"announced", "potential"}:
            return "candidate_formation"
        return "unknown"

    @staticmethod
    def _days_old(event: Dict[str, Any], as_of: dt.date) -> Optional[int]:
        event_date = _date_from_record(event)
        if event_date is None:
            return None
        return (as_of - event_date).days

    def _windowed_events(
        self,
        events: Iterable[Dict[str, Any]],
        as_of: dt.date,
        days: int,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for event in events or []:
            age = self._days_old(event, as_of)
            if age is None or age < 0 or age > days:
                continue
            rows.append(dict(event))
        rows.sort(key=lambda item: (item.get("date", ""), item.get("event_id", "")), reverse=True)
        return rows

    @staticmethod
    def _trigger_question(event: Dict[str, Any]) -> str:
        region = str(event.get("region") or event.get("jurisdiction") or "").strip()
        claim_type = str(event.get("claim_type") or "campaign_event")
        subject = str(
            event.get("candidate_name")
            or event.get("speaker")
            or event.get("title")
            or ""
        ).strip()
        focus = f"{region} {subject}".strip()
        return (
            f"{focus}近期出现{claim_type}变化；该变化是否改变候选人地方组织、"
            "联盟结构、议题动员或跨党支持？"
        )

    def _change_triggers(
        self,
        events_30d: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        triggers: List[Dict[str, Any]] = []
        for event in events_30d or []:
            claim_type = str(event.get("claim_type") or "")
            if claim_type not in SIGNIFICANT_EVENT_TYPES:
                continue
            triggers.append(
                {
                    "trigger_type": "campaign_change",
                    "event_id": event.get("event_id"),
                    "date": event.get("date"),
                    "claim_type": claim_type,
                    "region": event.get("region") or event.get("jurisdiction"),
                    "candidate_name": event.get("candidate_name"),
                    "source_grade": event.get("source_grade"),
                    "verification_status": event.get("verification_status"),
                    "structural_evidence_eligible": bool(
                        event.get("structural_evidence_eligible")
                    ),
                    "local_explanation_required": True,
                    "research_question": self._trigger_question(event),
                }
            )
        return triggers

    def _snapshot_dir(self, jurisdiction: str) -> Path:
        return (
            self.repo_root
            / "cache"
            / "campaign_state"
            / safe_component(jurisdiction)
        )

    def _load_previous_snapshot(self, jurisdiction: str) -> Optional[Dict[str, Any]]:
        path = self._snapshot_dir(jurisdiction) / "latest.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _event_brief(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "date": event.get("date"),
            "claim_type": event.get("claim_type"),
            "title": event.get("title"),
            "summary": event.get("summary"),
            "candidate_name": event.get("candidate_name"),
            "region": event.get("region") or event.get("jurisdiction"),
        }

    @staticmethod
    def _snapshot_delta(
        previous: Optional[Dict[str, Any]],
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not previous:
            return {
                "has_previous_snapshot": False,
                "previous_as_of": None,
                "candidate_added": [],
                "candidate_removed": [],
                "new_events": [],
                "poll_series_updated": [],
                "campaign_stage_changed": False,
            }

        prev_candidates = {
            str(item.get("candidate_id") or item.get("candidate_name") or item.get("name") or ""): item
            for item in previous.get("candidate_roster", [])
            if str(item.get("candidate_id") or item.get("candidate_name") or item.get("name") or "")
        }
        cur_candidates = {
            str(item.get("candidate_id") or item.get("candidate_name") or item.get("name") or ""): item
            for item in current.get("candidate_roster", [])
            if str(item.get("candidate_id") or item.get("candidate_name") or item.get("name") or "")
        }

        previous_events = {
            str(item.get("event_id") or "") for item in previous.get("events_30d", [])
            if str(item.get("event_id") or "")
        }
        new_events = [
            CampaignStateBuilder._event_brief(item)
            for item in current.get("events_30d", [])
            if str(item.get("event_id") or "") not in previous_events
        ]

        prev_series = {
            str(item.get("series_id")): str(item.get("latest_poll_id") or "")
            for item in previous.get("poll_same_series_trends", [])
            if item.get("series_id")
        }
        updated_series = [
            str(item.get("series_id"))
            for item in current.get("poll_same_series_trends", [])
            if item.get("series_id")
            and prev_series.get(str(item.get("series_id"))) not in {
                None,
                str(item.get("latest_poll_id") or ""),
            }
        ]

        return {
            "has_previous_snapshot": True,
            "previous_as_of": previous.get("as_of"),
            "candidate_added": [
                _candidate_name(cur_candidates[key]) or key
                for key in sorted(set(cur_candidates) - set(prev_candidates))
            ],
            "candidate_removed": [
                _candidate_name(prev_candidates[key]) or key
                for key in sorted(set(prev_candidates) - set(cur_candidates))
            ],
            "new_events": new_events,
            "poll_series_updated": updated_series,
            "campaign_stage_changed": (
                previous.get("campaign_stage") != current.get("campaign_stage")
            ),
            "previous_campaign_stage": previous.get("campaign_stage"),
            "current_campaign_stage": current.get("campaign_stage"),
        }

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, str]:
        directory = self._snapshot_dir(str(snapshot.get("jurisdiction") or "unknown"))
        directory.mkdir(parents=True, exist_ok=True)
        created_at = str(snapshot.get("created_at") or utc_now_iso())
        token = (
            created_at.replace(":", "")
            .replace("-", "")
            .replace("+", "_")
            .replace(".", "")
        )
        history_path = directory / f"{token}.json"
        latest_path = directory / "latest.json"
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
        history_path.write_text(payload, encoding="utf-8")
        latest_path.write_text(payload, encoding="utf-8")
        return {
            "latest": str(latest_path),
            "history": str(history_path),
        }

    def build(
        self,
        task: ElectionTask,
        current_candidates: Optional[Iterable[Dict[str, Any]]] = None,
        events: Optional[Iterable[Dict[str, Any]]] = None,
        polls: Optional[Iterable[Dict[str, Any]]] = None,
        allow_online: bool = True,
        persist_snapshot: bool = True,
        as_of: Optional[dt.date] = None,
    ) -> Dict[str, Any]:
        as_of = as_of or dt.date.today()
        candidate_rows = [dict(item) for item in (current_candidates or [])]
        event_rows: Dict[str, Dict[str, Any]] = {}

        for raw in events or []:
            normalized = self._normalize_event(raw, task, default_verified=True)
            if normalized is not None:
                event_rows[normalized["event_id"]] = normalized

        retrieval = self._retrieve_events(task, as_of, allow_online=allow_online)
        for event in retrieval["verified_events"]:
            event_rows[event["event_id"]] = event
        if retrieval["verified_events"]:
            self._cache_verified_events(task, retrieval["verified_events"])

        all_events = list(event_rows.values())
        events_7d = self._windowed_events(all_events, as_of, 7)
        events_14d = self._windowed_events(all_events, as_of, 14)
        events_30d = self._windowed_events(all_events, as_of, 30)
        poll_trends = build_same_series_poll_trends(polls or [])
        triggers = self._change_triggers(events_30d)
        research_questions = list(
            dict.fromkeys(
                str(item.get("research_question") or "")
                for item in triggers
                if str(item.get("research_question") or "")
            )
        )

        current: Dict[str, Any] = {
            "snapshot_version": "1.4",
            "task_id": task.task_id,
            "jurisdiction": task.jurisdiction,
            "election_type": task.election_type,
            "target_year": task.target_year,
            "as_of": as_of.isoformat(),
            "created_at": utc_now_iso(),
            "campaign_stage": self._campaign_stage(candidate_rows),
            "candidate_roster": candidate_rows,
            "window_counts": {
                "7d": len(events_7d),
                "14d": len(events_14d),
                "30d": len(events_30d),
            },
            "events_7d": events_7d,
            "events_14d": events_14d,
            "events_30d": events_30d,
            "retrieval_leads": retrieval["leads"],
            "retrieval_queries": retrieval["queries"],
            "campaign_change_triggers": triggers,
            "research_questions": research_questions,
            "poll_same_series_trends": poll_trends,
            "warnings": list(retrieval["warnings"]),
        }

        previous = self._load_previous_snapshot(task.jurisdiction)
        current["delta_from_previous_snapshot"] = self._snapshot_delta(previous, current)
        if persist_snapshot:
            current["snapshot_paths"] = self._persist_snapshot(current)
        return current
