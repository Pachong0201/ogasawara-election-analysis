"""Live campaign-state snapshots for V1.4.

This module keeps current-campaign evidence separate from historical election
facts.  It does not predict winners.  It answers a narrower question:
what has changed in the active campaign, as of a traceable point in time?
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .campaign_delta import compare_campaign_snapshots
from .election_loader import safe_component
from .freshness import is_fresh
from .models import parse_date, utc_now_iso
from .source_registry import OfflineRetrievalError, RetrievalBackend


DEFAULT_WINDOWS = (7, 14, 30)
DEFAULT_TRIGGER_TYPES = {
    "nomination",
    "candidate_registration",
    "candidate_withdrawal",
    "endorsement",
    "party_cooperation",
    "alliance",
    "alliance_break",
    "campaign_org",
    "campaign_headquarters",
    "debate",
    "policy",
    "major_issue",
    "judicial_event",
    "controversy",
}


def _event_date(record: Dict[str, Any]) -> Optional[dt.date]:
    for key in ("date", "event_date", "page_date", "publish_date", "published_at", "first_seen_at", "field_end"):
        parsed = parse_date(record.get(key))
        if parsed:
            return parsed
    return None


def _stable_id(record: Dict[str, Any], prefix: str) -> str:
    for key in ("event_id", "record_id", "lead_id", "poll_id", "candidate_id", "url"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    seed = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _candidate_key(record: Dict[str, Any]) -> str:
    return str(
        record.get("candidate_id")
        or record.get("name")
        or record.get("candidate_name")
        or record.get("姓名")
        or ""
    ).strip()


def _event_type(record: Dict[str, Any]) -> str:
    return str(
        record.get("claim_type")
        or record.get("event_type")
        or record.get("type")
        or ""
    ).strip().lower()


def _source_usable_for_current_event(record: Dict[str, Any]) -> bool:
    if record.get("usable_for_trigger") is False or record.get("evidence_status") == "requires_review":
        return False
    if not (record.get("source") or record.get("source_id")) or not (
        record.get("url") or record.get("reference") or record.get("source_reference")
    ):
        return False
    grade = str(record.get("source_grade") or "").upper()
    verification = str(record.get("verification_status") or "").lower()
    if grade in {"A", "B"}:
        return verification in {"verified", "confirmed", "official_record", "verified_fact"}
    if grade == "C":
        return verification in {"verified", "confirmed", "official_record", "verified_fact"}
    # D/E may still be retained as campaign claims, but may not independently
    # trigger a structural interpretation.
    return False


def _corroborated_media_event(record: Dict[str, Any]) -> bool:
    return (
        str(record.get("record_type") or "") == "campaign_event"
        and str(record.get("verification_status") or "") == "corroborated_media"
        and int(record.get("independent_source_count") or 0) >= 2
        and str(record.get("structural_use") or "") == "research_trigger_only"
    )


def _verified_by_as_of(record: Dict[str, Any], as_of: dt.date) -> bool:
    verified_at = parse_date(record.get("last_verified_at") or record.get("verified_at"))
    return verified_at is not None and verified_at <= as_of


def _poll_series_key(record: Dict[str, Any]) -> Tuple[str, ...]:
    return (
        str(record.get("pollster") or ""),
        str(record.get("commissioner") or ""),
        str(record.get("method") or ""),
        str(record.get("sample_frame") or ""),
        str(record.get("question_wording") or record.get("question") or ""),
        str(record.get("weighting") or ""),
        str(record.get("sampling") or ""),
    )


def _poll_values(record: Dict[str, Any]) -> Dict[str, float]:
    """Extract candidate point estimates and normalize them to percentage points."""

    def normalize(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        # Poll adapters store vote shares as decimals (0.44); host-provided
        # records may already use percentage points (44.0). Normalize both.
        return number * 100.0 if -1.0 <= number <= 1.0 else number

    out: Dict[str, float] = {}
    containers = [
        record.get("candidate_support"),
        record.get("support"),
        record.get("results"),
    ]
    for container in containers:
        if isinstance(container, dict):
            for name, value in container.items():
                normalized = normalize(value)
                if normalized is not None:
                    out[str(name)] = normalized
        elif isinstance(container, list):
            for item in container:
                if not isinstance(item, dict):
                    continue
                name = item.get("candidate") or item.get("name")
                value = item.get("support") if "support" in item else item.get("value")
                normalized = normalize(value)
                if name is not None and normalized is not None:
                    out[str(name)] = normalized

    candidates = record.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            name = item.get("candidate") or item.get("name")
            value = item.get("support") if "support" in item else item.get("value")
            normalized = normalize(value)
            if name is not None and normalized is not None:
                out[str(name)] = normalized
    return out


class CampaignStateStore:
    """Persist comparable snapshots without turning them into long-term facts."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def _base(self, jurisdiction: str, target_year: Optional[int] = None,
              election_type: Optional[str] = None) -> Path:
        base = self.repo_root / "cache" / "campaign_state" / safe_component(jurisdiction)
        if election_type is not None:
            base = base / safe_component(election_type)
        return base / str(target_year) if target_year is not None else base

    def load_latest(self, jurisdiction: str, target_year: Optional[int] = None,
                    election_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        path = self._base(jurisdiction, target_year, election_type) / "latest.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, jurisdiction: str, snapshot: Dict[str, Any]) -> Path:
        election = snapshot.get("election") or {}
        base = self._base(jurisdiction, snapshot.get("target_year"),
                          election.get("election_type") or "unspecified")
        base.mkdir(parents=True, exist_ok=True)
        stamp = safe_component(str(snapshot.get("as_of") or utc_now_iso())).replace(":", "").replace("+", "_")
        archive = base / f"{stamp}.json"
        sequence = 1
        while archive.exists():
            archive = base / f"{stamp}.{sequence}.json"
            sequence += 1
        text = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
        archive.write_text(text, encoding="utf-8")
        (base / "latest.json").write_text(text, encoding="utf-8")
        return archive


class CampaignStateBuilder:
    """Build a neutral, time-bounded campaign snapshot and deltas."""

    def __init__(
        self,
        repo_root: Path,
        retrieval_backend: Optional[RetrievalBackend] = None,
        config: Optional[Dict[str, Any]] = None,
        mode: str = "auto",
    ):
        self.repo_root = Path(repo_root)
        self.retrieval_backend = retrieval_backend
        self.config = config or {}
        self.mode = mode
        self.store = CampaignStateStore(self.repo_root)

    def _campaign_config(self) -> Dict[str, Any]:
        return self.config.get("campaign_state", {}) or {}

    def build_retrieval_queries(self, jurisdiction: str, target_year: int) -> List[str]:
        templates = self._campaign_config().get("retrieval_queries") or [
            "{jurisdiction} {year} 選舉 候選人 最新動態",
            "{jurisdiction} {year} 選舉 地方人物 支持 組織",
            "{jurisdiction} {year} 選舉 政黨合作 競選總部",
            "{jurisdiction} {year} 選舉 政策 爭議 地方議題",
            "{jurisdiction} {year} 選舉 民調",
        ]
        return [
            str(template).format(jurisdiction=jurisdiction, year=target_year)
            for template in templates
        ]

    def retrieve_current_leads(
        self,
        jurisdiction: str,
        target_year: int,
        allow_online: bool,
        as_of: Optional[str] = None,
        current_candidates: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        if not allow_online or self.mode == "offline" or self.retrieval_backend is None:
            return [], []
        leads: List[Dict[str, Any]] = []
        warnings: List[str] = []
        cutoff = parse_date(as_of) if as_of else None
        seen_urls: set = set()
        query_rows: List[List[Dict[str, Any]]] = []
        candidate_names = [
            _candidate_key(row)
            for row in (current_candidates or [])
            if _candidate_key(row)
        ]
        for base_query in self.build_retrieval_queries(jurisdiction, target_year):
            query = f"{base_query} 截至{cutoff.isoformat()}" if cutoff else base_query
            try:
                results = self.retrieval_backend.search(
                    query,
                    recency_days=int(self._campaign_config().get("retrieval_recency_days", 30)),
                    purpose="live_campaign_state",
                    jurisdiction=jurisdiction,
                    candidate_names=candidate_names,
                )
            except OfflineRetrievalError as exc:
                warnings.append(str(exc))
                break
            except Exception as exc:  # retrieval is optional; fail closed
                warnings.append(f"campaign retrieval failed for {query!r}: {exc}")
                continue
            rows: List[Dict[str, Any]] = []
            for result in results or []:
                if not isinstance(result, dict):
                    result = {"summary": str(result)}
                lead = dict(result)
                published = _event_date(lead)
                if cutoff and published and published > cutoff:
                    continue
                url = str(lead.get("url") or "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                lead.setdefault("query", query)
                lead.setdefault("jurisdiction", jurisdiction)
                lead.setdefault("layer_id", "L4")
                lead["as_of"] = as_of
                lead["reported_verification_status"] = lead.get("verification_status")
                lead["verification_status"] = "lead_only"
                lead.setdefault("retrieved_at", utc_now_iso())
                lead.setdefault("lead_id", _stable_id(lead, "campaign-lead"))
                rows.append(lead)
            query_rows.append(rows)

        # Interleave topics so a limited article-body budget does not get consumed
        # by only the first campaign query.
        for index in range(max((len(rows) for rows in query_rows), default=0)):
            for rows in query_rows:
                if index < len(rows):
                    leads.append(rows[index])

        enrich = getattr(self.retrieval_backend, "enrich", None)
        if callable(enrich) and leads:
            try:
                enriched = enrich(leads)
                if enriched is not None:
                    leads = list(enriched)
            except Exception as exc:
                warnings.append(f"article body retrieval failed: {type(exc).__name__}")

        # Retrieval results remain leads even after body reading. Preserve any
        # backend-reported status separately, but never auto-promote them.
        filtered: List[Dict[str, Any]] = []
        for raw in leads:
            if not isinstance(raw, dict):
                continue
            lead = dict(raw)
            published = _event_date(lead)
            if cutoff and published and published > cutoff:
                continue
            lead.setdefault("query", "")
            lead.setdefault("jurisdiction", jurisdiction)
            lead.setdefault("layer_id", "L4")
            lead["as_of"] = as_of
            if "reported_verification_status" not in lead:
                lead["reported_verification_status"] = lead.get("verification_status")
            lead["verification_status"] = "lead_only"
            lead.setdefault("retrieved_at", utc_now_iso())
            lead.setdefault("lead_id", _stable_id(lead, "campaign-lead"))
            filtered.append(lead)
        return filtered, warnings

    @staticmethod
    def _window_events(
        events: Iterable[Dict[str, Any]],
        as_of_date: dt.date,
        days: int,
    ) -> List[Dict[str, Any]]:
        lower = as_of_date - dt.timedelta(days=days - 1)
        rows: List[Dict[str, Any]] = []
        for event in events:
            date = _event_date(event)
            if date and lower <= date <= as_of_date:
                rows.append(event)
        return rows

    def same_series_poll_changes(self, polls: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
        for poll in polls:
            if not isinstance(poll, dict):
                continue
            date = _event_date(poll)
            if not date:
                continue
            key = _poll_series_key(poll)
            if not all(key[:5]):
                continue
            grouped[key].append(poll)

        changes: List[Dict[str, Any]] = []
        campaign_config = self._campaign_config()
        poll_config = campaign_config.get("poll") or {}
        observe_pp = float(
            poll_config.get(
                "same_series_change_observe_pp",
                campaign_config.get("same_series_poll_change_observe_pp", 3.0),
            )
        )
        for key, rows in grouped.items():
            rows.sort(key=lambda row: (_event_date(row) or dt.date.min, str(row.get("publish_date") or "")))
            if len(rows) < 2:
                continue
            previous, current = rows[-2], rows[-1]
            before = _poll_values(previous)
            after = _poll_values(current)
            deltas: Dict[str, float] = {}
            for candidate in sorted(set(before) & set(after)):
                deltas[candidate] = round(after[candidate] - before[candidate], 3)
            if not deltas:
                continue
            changes.append(
                {
                    "series": {
                        "pollster": key[0],
                        "commissioner": key[1],
                        "method": key[2],
                        "sample_frame": key[3],
                        "question_wording": key[4],
                        "weighting": key[5],
                        "sampling": key[6],
                    },
                    "series_id": hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16],
                    "previous_poll_id": _stable_id(previous, "poll"),
                    "current_poll_id": _stable_id(current, "poll"),
                    "previous_date": str(_event_date(previous)),
                    "current_date": str(_event_date(current)),
                    "candidate_deltas": deltas,
                    "observe_threshold_pp": observe_pp,
                    "change_observed": any(abs(value) >= observe_pp for value in deltas.values()),
                    "interpretation_limit": (
                        "same-series point-estimate change only; it is not a win/loss prediction "
                        "and does not by itself establish statistical significance"
                    ),
                }
            )
        return changes

    def build(
        self,
        jurisdiction: str,
        target_year: int,
        current_candidates: List[Dict[str, Any]],
        current_events: List[Dict[str, Any]],
        polls: List[Dict[str, Any]],
        retrieval_leads: Optional[List[Dict[str, Any]]] = None,
        as_of: Optional[str] = None,
        persist: Optional[bool] = None,
        online_expected: bool = False,
        event_conflicts: Optional[List[Dict[str, Any]]] = None,
        election_type: str = "unspecified",
    ) -> Dict[str, Any]:
        as_of = as_of or utc_now_iso()
        as_of_date = parse_date(as_of)
        if as_of_date is None:
            raise ValueError("as_of must be an ISO date or timestamp")
        retrieval_leads = retrieval_leads or []
        event_conflicts = event_conflicts or []
        windows = self._campaign_config().get("windows_days") or list(DEFAULT_WINDOWS)

        current_events = [row for row in current_events if _event_date(row) and _event_date(row) <= as_of_date]
        polls = [row for row in polls if _event_date(row) and _event_date(row) <= as_of_date]
        window_payload: Dict[str, Any] = {}
        trigger_types = {
            str(value).lower()
            for value in (self._campaign_config().get("trigger_event_types") or DEFAULT_TRIGGER_TYPES)
        }
        verified_trigger_events: List[Dict[str, Any]] = []
        corroborated_trigger_events: List[Dict[str, Any]] = []
        for days in windows:
            rows = self._window_events(current_events, as_of_date, int(days))
            verified = [
                row for row in rows
                if _source_usable_for_current_event(row)
                and _verified_by_as_of(row, as_of_date)
                and is_fresh(row, kind="campaign_event", now=as_of_date)
                and (_event_type(row) in trigger_types or not _event_type(row))
            ]
            corroborated = [
                row for row in rows
                if _corroborated_media_event(row)
                and (_event_type(row) in trigger_types or not _event_type(row))
            ]
            window_payload[f"{int(days)}d"] = {
                "event_count": len(rows),
                "verified_trigger_event_count": len(verified),
                "corroborated_media_event_count": len(corroborated),
                "event_ids": [_stable_id(row, "event") for row in rows],
            }
            if int(days) <= 14:
                verified_trigger_events.extend(verified)
                corroborated_trigger_events.extend(corroborated)

        # Search results are discovery leads even if a backend claims verification.
        verified_retrieval_leads: List[Dict[str, Any]] = []

        verified_polls = [
            row for row in polls
            if str(row.get("source_grade") or "").upper() in {"A", "B", "C"}
            and str(row.get("verification_status") or "").lower()
            in {"verified", "confirmed", "official_record", "verified_fact"}
        ]
        poll_changes = [
            row for row in self.same_series_poll_changes(verified_polls)
            if is_fresh(next(p for p in polls if _stable_id(p, "poll") == row["current_poll_id"]),
                        kind="poll", now=as_of_date)
        ]
        previous = (self.store.load_latest(jurisdiction, target_year, election_type)
                    or self.store.load_latest(jurisdiction, target_year)
                    or self.store.load_latest(jurisdiction))
        previous_type = (previous or {}).get("election", {}).get("election_type")
        if previous and (previous.get("target_year") not in (None, target_year)
                         or previous_type not in (None, election_type)):
            previous = None
        snapshot_delta = compare_campaign_snapshots(
            previous,
            current_candidates,
            current_events,
            polls,
            retrieval_leads=retrieval_leads,
        )

        reasons: List[str] = []
        if verified_trigger_events:
            reasons.append("verified_recent_campaign_event")
        if corroborated_trigger_events:
            reasons.append("corroborated_recent_campaign_event")
        if any(item.get("change_observed") for item in poll_changes):
            reasons.append("same_series_poll_change")
        if snapshot_delta.get("candidate_changes") and self.mode != "offline" and any(
            _verified_by_as_of(row, as_of_date)
            and
            is_fresh(row, kind="candidate_profile", now=as_of_date)
            and str(row.get("source_grade") or "").upper() in {"A", "B", "C"}
            for row in current_candidates
        ):
            reasons.append("candidate_field_change")
        if snapshot_delta.get("new_event_ids") and any(
            row["event_id"] in snapshot_delta["new_event_ids"]
            and _source_usable_for_current_event(row)
            and _verified_by_as_of(row, as_of_date)
            and is_fresh(row, kind="campaign_event", now=as_of_date)
            for row in current_events
        ):
            reasons.append("new_event_since_previous_snapshot")

        verified_30d = int(window_payload.get("30d", {}).get("verified_trigger_event_count", 0))
        fresh_poll_count = sum(1 for poll in polls if is_fresh(poll, kind="poll", now=as_of_date)
                               and str(poll.get("source_grade") or "").upper() in {"A", "B", "C"}
                               and str(poll.get("verification_status") or "").lower() in
                               {"verified", "confirmed", "official_record", "verified_fact"})
        fresh_candidates = [row for row in current_candidates
                            if (str(row.get("source_grade") or "").upper() in {"A", "B"}
                                or (str(row.get("source_grade") or "").upper() == "C"
                                    and int(row.get("independent_source_count") or 0) >= 2))
                            and _verified_by_as_of(row, as_of_date)
                            and is_fresh(row, kind="candidate_registration" if
                                         str(row.get("candidate_status") or "").lower() == "registered"
                                         else "candidate_profile", now=as_of_date)]
        if self.mode == "offline":
            campaign_state_status = "insufficient_current_data"
            reasons = []
        elif verified_30d and fresh_candidates:
            campaign_state_status = "current"
        elif verified_30d or fresh_candidates or fresh_poll_count:
            campaign_state_status = "partial_current_data"
        else:
            campaign_state_status = "insufficient_current_data"

        snapshot = {
            "snapshot_version": "1.4.0",
            "as_of": as_of,
            "jurisdiction": jurisdiction,
            "target_year": int(target_year),
            "election": {"election_type": election_type, "target_year": int(target_year)},
            "campaign_state_status": campaign_state_status,
            "current_candidates": current_candidates,
            "current_events": current_events,
            "polls": polls,
            "candidate_count": len(current_candidates),
            "candidate_keys": sorted(
                {_candidate_key(row) for row in current_candidates if _candidate_key(row)}
            ),
            "event_ids": sorted({_stable_id(row, "event") for row in current_events}),
            "poll_ids": sorted({_stable_id(row, "poll") for row in polls}),
            "retrieval_lead_ids": sorted({_stable_id(row, "campaign-lead") for row in retrieval_leads}),
            "corroborated_event_ids": sorted({
                _stable_id(row, "event") for row in current_events
                if _corroborated_media_event(row)
            }),
            "corroborated_media_event_count": sum(
                1 for row in current_events if _corroborated_media_event(row)
            ),
            "fresh_verified_poll_count": fresh_poll_count,
            "windows": window_payload,
            "same_series_poll_changes": poll_changes,
            "snapshot_delta": snapshot_delta,
            "event_conflicts": event_conflicts,
            "campaign_change_trigger": bool(reasons),
            "campaign_change_reasons": reasons,
            "retrieval_lead_count": len(retrieval_leads),
            "verified_retrieval_lead_count": len(verified_retrieval_leads),
            "verified_retrieval_leads": verified_retrieval_leads,
            "retrieval_leads": retrieval_leads,
            "evidence": [ref for row in current_events for ref in row.get("evidence_refs", [])],
            "provenance": {"as_of": as_of, "source": "campaign_event_cache_and_host_retrieval"},
            "interpretation_boundary": (
                "campaign-state signals trigger further research; they do not rank candidates, "
                "predict the winner, or turn campaign claims into facts"
            ),
        }

        if persist is None:
            persist = bool(self._campaign_config().get("persist_snapshots", True))
        if persist:
            path = self.store.save(jurisdiction, snapshot)
            snapshot["snapshot_path"] = str(path)
        return snapshot


def campaign_research_questions(snapshot: Dict[str, Any]) -> List[str]:
    """Generate current-campaign research questions without asserting causes."""
    jurisdiction = str(snapshot.get("jurisdiction") or "该地区")
    questions: List[str] = []
    reasons = set(snapshot.get("campaign_change_reasons") or [])
    if "verified_recent_campaign_event" in reasons:
        questions.append(f"{jurisdiction} 最近14天的竞选事件是否改变地方组织、候选人整合或议题结构？")
    if "corroborated_recent_campaign_event" in reasons:
        questions.append(
            f"{jurisdiction} 最近14天经多家独立媒体正文相互印证的竞选事件，"
            "哪些还需要官方资料、当事人原始声明或更高等级来源进一步确认？"
        )
    if "same_series_poll_change" in reasons:
        questions.append(f"{jurisdiction} 同一调查系列出现变化时，是否有同期竞选事件或组织变化可验证其背景？")
    if "candidate_field_change" in reasons:
        questions.append(f"{jurisdiction} 候选人格局变化后，原有地方支持与竞选组织如何重新配置？")
    if "new_event_since_previous_snapshot" in reasons:
        questions.append(f"{jurisdiction} 自上一快照以来新增事件中，哪些具有可验证的结构意义？")
    if snapshot.get("retrieval_lead_count"):
        questions.append(f"{jurisdiction} 最新检索线索中，哪些经核实后可进入当前选举事件层 L4？")
    return questions
