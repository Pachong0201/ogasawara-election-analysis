"""End-to-end orchestration for the V1.4 runtime.

The pipeline does not generate a political verdict. It prepares a validated,
traceable Analysis Context that a report writer may use under SKILL.md rules.
"""

from __future__ import annotations

from collections import defaultdict
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .analysis_context import AnalysisContextBuilder
from .campaign_event import CampaignEventResolver, campaign_event_research_questions
from .campaign_events import CampaignEventLoader
from .campaign_state import CampaignStateBuilder, campaign_research_questions
from .data_readiness import DataReadinessGate
from .election_loader import ElectionLoader, election_file_path
from .freshness import evaluate_records
from .knowledge_loader import KnowledgeLoader
from .event_importance import event_importance_signals
from .matrix_builder import build_cross_level_matrix, build_historical_matrix, build_same_day_matrix
from .metrics import candidate_residual, electoral_swing, spatial_variance, split_ticket_residual
from .models import AnalysisContext, ElectionTask, MetricResult, parse_date, utc_now_iso
from .source_registry import RetrievalBackend, SourceRegistry


class AnalysisPipeline:
    """Coordinate readiness, local-first loading, metrics and context creation."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        mode: str = "auto",
        source_registry: Optional[SourceRegistry] = None,
        retrieval_backend: Optional[RetrievalBackend] = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.package_root = Path(__file__).resolve().parents[1]
        self.mode = mode
        self.retrieval_backend = retrieval_backend

        runtime_config_path = self.repo_root / "config" / "runtime.yaml"
        if not runtime_config_path.exists():
            runtime_config_path = self.package_root / "config" / "runtime.yaml"
        source_config_path = self.repo_root / "config" / "data_sources.yaml"
        if not source_config_path.exists():
            source_config_path = self.package_root / "config" / "data_sources.yaml"

        self.runtime_config_path = runtime_config_path
        self.source_registry = source_registry or SourceRegistry(source_config_path)
        self.loader = ElectionLoader(
            self.repo_root,
            source_registry=self.source_registry,
            retrieval_backend=retrieval_backend,
            mode=mode,
        )
        self.gate = DataReadinessGate(self.repo_root, runtime_config_path=runtime_config_path)
        self.knowledge_loader = KnowledgeLoader(
            self.repo_root,
            retrieval_backend=retrieval_backend,
            mode=mode,
        )
        self.context_builder = AnalysisContextBuilder(self.repo_root, skill_version="1.4.0")
        self.campaign_event_loader = CampaignEventLoader(self.repo_root)
        self.runtime_config = self._load_runtime_config()
        policy_path = self.repo_root / self.runtime_config.get("campaign_state", {}).get("policy_file", "config/campaign_state.yaml")
        if not policy_path.exists():
            policy_path = self.package_root / "config" / "campaign_state.yaml"
        if policy_path.exists():
            with policy_path.open(encoding="utf-8") as fh:
                self.runtime_config["campaign_state"] = yaml.safe_load(fh) or {}
        self.campaign_state_builder = CampaignStateBuilder(
            self.repo_root,
            retrieval_backend=retrieval_backend,
            config=self.runtime_config,
            mode=mode,
        )
        event_config = self.runtime_config.get("campaign_event_resolution", {}) or {}
        self.campaign_event_resolver = CampaignEventResolver(
            corroboration_min_sources=int(event_config.get("corroboration_min_sources", 2)),
            max_excerpt_chars=int(event_config.get("max_excerpt_chars", 900)),
        )

    def _load_runtime_config(self) -> Dict[str, Any]:
        path = self.runtime_config_path
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _allow_online(self, allow_online: bool) -> bool:
        if not allow_online or self.mode == "offline":
            return False
        return bool(self.loader.network_allowed())

    def _load_periods(
        self,
        task: ElectionTask,
        readiness: Any,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], List[str]]:
        records_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        files_used: List[str] = []
        warnings: List[str] = []

        specs = [
            (task.election_type, readiness.required.get("historical_same_type", {})),
            ("president", readiness.required.get("presidential", {})),
            ("regional_legislator", readiness.required.get("regional_legislator", {})),
        ]
        seen: set = set()
        for election_type, info in specs:
            if info.get("applicable", True) is False:
                continue
            key = (election_type, tuple(info.get("found_years", [])))
            if key in seen:
                continue
            seen.add(key)
            for year in info.get("found_years", []):
                result = self.loader.load_election(
                    election_type,
                    int(year),
                    task.jurisdiction,
                    task.analysis_level,
                )
                if result.complete:
                    records_by_type[election_type].extend(result.records)
                    files_used.append(
                        str(election_file_path(self.repo_root, election_type, int(year), task.jurisdiction))
                    )
                else:
                    warnings.extend(result.warnings)
                    warnings.extend(result.errors)

        return dict(records_by_type), sorted(set(files_used)), warnings

    @staticmethod
    def _party_series(records: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Tuple[int, float]]]:
        grouped: Dict[Tuple[str, str, int], float] = defaultdict(float)
        for record in records:
            region = str(record.get("jurisdiction") or "")
            party = str(record.get("party") or "")
            year = int(record.get("election_year"))
            share = record.get("vote_share")
            if not region or not party or share is None:
                continue
            grouped[(region, party, year)] += float(share)

        series: Dict[Tuple[str, str], List[Tuple[int, float]]] = defaultdict(list)
        for (region, party, year), share in grouped.items():
            series[(region, party)].append((year, share))
        for key in series:
            series[key] = sorted(series[key])
        return dict(series)

    def _electoral_swings(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for (region, party), series in self._party_series(records).items():
            for previous, current in zip(series, series[1:]):
                prev_year, prev_share = previous
                year, share = current
                result = electoral_swing(
                    share,
                    prev_share,
                    region=region,
                    baseline_method=f"same_type_party:{party}:{prev_year}->{year}",
                )
                payload = result.to_dict()
                payload["party"] = party
                payload["periods"] = [prev_year, year]
                results.append(payload)
        return results

    def _split_ticket(
        self,
        president_records: List[Dict[str, Any]],
        legislator_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_year_pres: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        by_year_leg: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for record in president_records:
            by_year_pres[int(record.get("election_year"))].append(record)
        for record in legislator_records:
            by_year_leg[int(record.get("election_year"))].append(record)

        results: List[Dict[str, Any]] = []
        for year in sorted(set(by_year_pres) & set(by_year_leg)):
            rows = build_same_day_matrix(by_year_leg[year], by_year_pres[year]).get("rows", [])
            for row in rows:
                leg_share = row.get("legislator_vote_share")
                pres_share = row.get("president_vote_share")
                if leg_share is None or pres_share is None:
                    continue
                result = split_ticket_residual(
                    float(leg_share),
                    float(pres_share),
                    region=str(row.get("region") or ""),
                    baseline_method=f"same_day_president_vote:{year}:{row.get('party')}",
                )
                payload = result.to_dict()
                payload["party"] = row.get("party")
                payload["year"] = year
                results.append(payload)
        return results

    def _candidate_residuals(
        self,
        task: ElectionTask,
        same_type_records: List[Dict[str, Any]],
        president_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if task.election_type != "county_mayor" or not same_type_records or not president_records:
            return []

        latest_year = max(int(record.get("election_year")) for record in same_type_records)
        latest = [record for record in same_type_records if int(record.get("election_year")) == latest_year]
        president_series = self._party_series(president_records)

        results: List[Dict[str, Any]] = []
        for record in latest:
            region = str(record.get("jurisdiction") or "")
            party = str(record.get("party") or "")
            candidates = president_series.get((region, party), [])
            before = [item for item in candidates if item[0] < latest_year]
            after = [item for item in candidates if item[0] > latest_year]
            if not before or not after:
                continue
            left = max(before, key=lambda item: item[0])
            right = min(after, key=lambda item: item[0])
            baseline = (left[1] + right[1]) / 2.0
            result = candidate_residual(
                float(record.get("vote_share")),
                baseline,
                baseline_method=f"bracketing_president_average:{left[0]}+{right[0]}",
                region=region,
            )
            payload = result.to_dict()
            payload["candidate"] = record.get("candidate_name")
            payload["party"] = party
            payload["election_year"] = latest_year
            results.append(payload)
        return results

    def _spatial_anomalies(self, same_type_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not same_type_records:
            return []
        latest_year = max(int(record.get("election_year")) for record in same_type_records)
        latest = [record for record in same_type_records if int(record.get("election_year")) == latest_year]
        by_party: Dict[str, List[float]] = defaultdict(list)
        for record in latest:
            party = str(record.get("party") or "")
            share = record.get("vote_share")
            if party and share is not None:
                by_party[party].append(float(share))

        method = self.runtime_config.get("metrics", {}).get("spatial_variance_primary_method", "standard_deviation")
        results: List[Dict[str, Any]] = []
        for party, shares in sorted(by_party.items()):
            if len(shares) < 2:
                continue
            result = spatial_variance(shares, method=method, region=f"{party}:{latest_year}")
            payload = result.to_dict()
            payload["party"] = party
            payload["election_year"] = latest_year
            results.append(payload)
        return results

    @staticmethod
    def _triggered(metrics: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        triggered: List[Dict[str, Any]] = []
        for rows in metrics.values():
            for row in rows:
                if isinstance(row, dict) and row.get("local_explanation_required"):
                    triggered.append(row)
        return triggered

    @staticmethod
    def _source_summary(records_by_type: Dict[str, List[Dict[str, Any]]], extra: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for records in records_by_type.values():
            for record in records:
                key = (
                    str(record.get("source_id") or record.get("source") or ""),
                    str(record.get("source_grade") or ""),
                    str(record.get("raw_reference") or record.get("source_reference") or ""),
                )
                if key[0]:
                    seen[key] = {
                        "source_id": key[0],
                        "source_grade": key[1],
                        "reference": key[2],
                    }
        for record in extra:
            key = (
                str(record.get("source_id") or record.get("source") or ""),
                str(record.get("source_grade") or ""),
                str(record.get("url") or record.get("raw_reference") or ""),
            )
            if key[0]:
                seen[key] = {
                    "source_id": key[0],
                    "source_grade": key[1],
                    "reference": key[2],
                }
        return list(seen.values())

    def run(
        self,
        task: ElectionTask,
        allow_online: bool = True,
        write_manifest: bool = False,
        manifest_path: Optional[Path] = None,
        as_of: Optional[str] = None,
        _research_result: Optional[Dict[str, Any]] = None,
    ) -> AnalysisContext:
        live_request = as_of is None
        as_of = as_of or dt.datetime.now(dt.timezone.utc).isoformat()
        as_of_date = parse_date(as_of)
        if not as_of_date:
            raise ValueError("as_of must be an ISO date or timestamp")
        online = self._allow_online(allow_online)
        coordinator = getattr(self.retrieval_backend, 'research_coordinator', None)
        research_prepass = bool(coordinator and online and live_request and _research_result is None)
        readiness = self.gate.prepare(task, loader=self.loader, allow_online=online, now=as_of_date)

        records_by_type, files_used, load_warnings = self._load_periods(task, readiness)
        same_type = records_by_type.get(task.election_type, [])
        president = records_by_type.get("president", [])
        legislator = records_by_type.get("regional_legislator", [])

        historical_matrix = build_historical_matrix(same_type)
        cross_level_matrix = build_cross_level_matrix(records_by_type, level=task.analysis_level)

        metrics: Dict[str, List[Dict[str, Any]]] = {
            "electoral_swing": self._electoral_swings(same_type),
            "split_ticket": self._split_ticket(president, legislator),
            "candidate_residuals": self._candidate_residuals(task, same_type, president),
            "spatial_anomalies": self._spatial_anomalies(same_type),
        }
        triggered = self._triggered(metrics)

        # V1.4: build the current campaign state before deciding whether local
        # knowledge retrieval is needed. Current campaign change can now trigger
        # research independently of historical vote-pattern anomalies.
        polls_result = self.loader.load_polls(jurisdiction=task.jurisdiction)
        polls = [dict(item) for item in polls_result.get("records", [])]
        poll_freshness = evaluate_records(polls, kind="poll", now=as_of_date) if polls else {"fresh": 0, "stale": 0, "unknown": 0, "records": []}
        for status in poll_freshness.get("records", []):
            index = int(status.get("index", -1))
            if 0 <= index < len(polls):
                polls[index]["freshness_status"] = status.get("status")
                expires_at = status.get("expires_at")
                polls[index]["freshness_expires_at"] = expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at
        event_report = self.campaign_event_loader.load_cache(task.jurisdiction, as_of=as_of)
        cached_events = list(event_report.get("events", []))
        files_used.extend(event_report.get("files", []))
        current_candidates = readiness.available.get("current_candidates", {}).get("verified_candidates", [])

        campaign_leads, campaign_retrieval_warnings = self.campaign_state_builder.retrieve_current_leads(
            task.jurisdiction,
            task.target_year,
            allow_online=online,
            as_of=as_of,
            current_candidates=current_candidates,
        )
        campaign_event_resolution = self.campaign_event_resolver.extract(
            campaign_leads,
            jurisdiction=task.jurisdiction,
            current_candidates=current_candidates,
        )
        resolved_events = list(campaign_event_resolution.get("events") or [])
        resolve_events = getattr(self.retrieval_backend, "resolve_events", None)
        if callable(resolve_events):
            resolved_events = resolve_events(resolved_events, task.jurisdiction, task.target_year, task.election_type, as_of)
            campaign_event_resolution["events"] = resolved_events

        # Keep canonical verified/cache events and media-body research events distinct,
        # while deduplicating only when they expose the same explicit event id.
        events_by_id: Dict[str, Dict[str, Any]] = {}
        anonymous_events: List[Dict[str, Any]] = []
        for event in cached_events + resolved_events:
            event_id = str(event.get("event_id") or event.get("record_id") or "").strip()
            if event_id:
                events_by_id.setdefault(event_id, event)
            else:
                anonymous_events.append(event)
        events = list(events_by_id.values()) + anonymous_events

        campaign_state = self.campaign_state_builder.build(
            jurisdiction=task.jurisdiction,
            target_year=task.target_year,
            current_candidates=current_candidates,
            current_events=events,
            polls=polls,
            election_type=task.election_type,
            retrieval_leads=campaign_leads,
            online_expected=online,
            event_conflicts=list(event_report.get("conflicts", [])),
            as_of=as_of,
            persist=False if research_prepass else None,
        )

        historical_regions = {
            str(item.get("region") or "").split("|")[0]
            for item in triggered
            if str(item.get("region") or "").strip()
        }
        campaign_regions = {
            str(location)
            for event in resolved_events
            if str(event.get("verification_status") or "") == "corroborated_media"
            for location in (event.get("locations") or [])
            if str(location).strip()
        }
        regions = sorted(historical_regions | campaign_regions)
        historical_questions = self.knowledge_loader.build_research_questions(triggered) if triggered else []
        live_questions = campaign_research_questions(campaign_state)
        event_questions = campaign_event_research_questions(
            resolved_events,
            jurisdiction=task.jurisdiction,
        )
        questions = list(dict.fromkeys(historical_questions + live_questions + event_questions))
        research_triggered = bool(triggered) or bool(campaign_state.get("campaign_change_trigger"))
        local_knowledge = self.knowledge_loader.load(
            task.jurisdiction,
            regions=regions or None,
            research_questions=questions,
            allow_online=online and research_triggered,
            as_of=as_of,
        )
        importance_signals = event_importance_signals(events, local_knowledge)
        local_knowledge["event_importance_signals"] = importance_signals
        if research_prepass:
            try:
                result = coordinator.research(task, questions, [
                    str(row.get('candidate_name') or row.get('name') or row.get('姓名') or '')
                    for row in current_candidates
                ])
            except Exception as exc:
                result = {'status': 'failed', 'last_error': type(exc).__name__}
            # Freeze the evidence cutoff only AFTER bounded collection. Explicit
            # as_of replays never enter this branch and never advance their cutoff.
            return self.run(task, allow_online=allow_online, write_manifest=write_manifest,
                            manifest_path=manifest_path, _research_result=result)
        if _research_result is not None:
            local_knowledge['automatic_research'] = _research_result
        retrieval_metadata = (
            self.retrieval_backend.metadata()
            if self.retrieval_backend is not None
            and callable(getattr(self.retrieval_backend, "metadata", None))
            else {"backend": "disabled", "lead_only": True}
        )

        unknowns: List[str] = []
        if readiness.status == "INSUFFICIENT":
            unknowns.append("hard-required data are incomplete; full structural analysis is not allowed")
        if triggered and not local_knowledge.get("sufficient"):
            unknowns.append("local anomalies were detected but minimum sufficient local knowledge was not established")
        if campaign_state.get("campaign_change_trigger") and not local_knowledge.get("sufficient"):
            unknowns.append("current campaign change was detected but minimum sufficient current local knowledge was not established")
        if campaign_state.get("campaign_state_status") == "insufficient_current_data":
            unknowns.append("current campaign data are insufficient; output may describe historical structure but must not be labeled a current campaign-state assessment")
        elif campaign_state.get("campaign_state_status") == "partial_current_data":
            unknowns.append("only part of the current campaign state is verified; do not present an overall current campaign assessment")
        if not polls:
            unknowns.append("no validated poll cache is available; poll calibration is omitted")
        elif not campaign_state.get("fresh_verified_poll_count"):
            unknowns.append(
                "no fresh verified poll is available; stale or unverified poll records must not calibrate the current state"
            )

        warnings = list(readiness.warnings) + load_warnings + campaign_retrieval_warnings + list(event_report.get("warnings", [])) + list(local_knowledge.get("warnings", []))
        if polls_result.get("invalid"):
            warnings.append(f"{len(polls_result['invalid'])} cached poll record(s) failed validation")
        if poll_freshness.get("stale"):
            warnings.append(f"{poll_freshness['stale']} poll record(s) are stale and must not be presented as current polling")

        baseline_methods = {
            "historical_matrix": "same-type elections at aligned geographic level",
            "candidate_residual": "bracketing president average when both surrounding elections exist",
            "split_ticket": "same-day regional legislator minus president vote share",
        }

        sources = self._source_summary(records_by_type, current_candidates + polls + events + campaign_leads)
        research_urls = []
        for finding in (_research_result or {}).get('findings', []):
            for citation in finding.get('citations', []):
                if citation['url'] not in research_urls:
                    research_urls.append(citation['url'])
                    sources.append({'source_id': citation.get('publisher_id'),
                                    'source_grade': citation.get('source_grade'), 'reference': citation['url']})
        context = self.context_builder.build(
            task=task,
            readiness=readiness,
            records_by_type={
                "historical_baseline": {
                    "historical_matrix": historical_matrix,
                    "cross_level_matrix": cross_level_matrix,
                }
            },
            metrics=metrics,
            local_knowledge=local_knowledge,
            current_candidates=current_candidates,
            current_events=events,
            campaign_event_resolution=campaign_event_resolution,
            event_importance_signals=importance_signals,
            polls=polls,
            campaign_state=campaign_state,
            evidence_summary={
                "triggered_anomaly_count": len(triggered),
                "campaign_state_status": campaign_state.get("campaign_state_status"),
                "campaign_change_trigger": bool(campaign_state.get("campaign_change_trigger")),
                "campaign_change_reasons": campaign_state.get("campaign_change_reasons", []),
                "campaign_event_conflict_count": len(event_report.get("conflicts", [])),
                "campaign_event_raw_count": int(event_report.get("raw_count", 0)),
                "campaign_event_deduplicated_count": int(event_report.get("deduplicated_count", 0)),
                "campaign_event_resolution": campaign_event_resolution.get("stats", {}),
                "event_importance_signal_count": len(importance_signals),
                "campaign_retrieval_lead_count": len(campaign_leads),
                "retrieval": retrieval_metadata,
                "automatic_research": _research_result or {'status': 'historical_replay' if coordinator and not live_request else 'disabled'},
                "local_knowledge_sufficient": bool(local_knowledge.get("sufficient")),
                "poll_freshness": poll_freshness,
                "current_poll_calibration_available": int(campaign_state.get("fresh_verified_poll_count", 0)) > 0,
                "fresh_poll_count": int(campaign_state.get("fresh_verified_poll_count", 0)),
                "stale_poll_count": int(poll_freshness.get("stale", 0)),
            },
            unknowns=unknowns,
            warnings=warnings,
            sources=sources,
            web_sources_used=research_urls,
            files_used=files_used,
            baseline_methods=baseline_methods,
        )

        if write_manifest:
            self.context_builder.write_manifest(context, manifest_path)
        return context
