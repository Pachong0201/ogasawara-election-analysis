"""End-to-end orchestration for the V1.4 runtime.

The pipeline does not generate a political verdict. It prepares a validated,
traceable Analysis Context that a report writer may use under SKILL.md rules.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .analysis_context import AnalysisContextBuilder
from .campaign_state import CampaignStateBuilder, campaign_research_questions
from .data_readiness import DataReadinessGate
from .election_loader import ElectionLoader, election_file_path, load_jsonl
from .findings import FindingsBuilder
from .freshness import evaluate_records
from .knowledge_loader import KnowledgeLoader
from .matrix_builder import build_cross_level_matrix, build_historical_matrix
from .aggregates import ElectionAggregate, party_of, series_key
from .metrics import (
    candidate_residual,
    electoral_swing,
    geographic_concentration,
    local_swing,
    neighbor_divergence,
    spatial_variance,
    split_ticket_residual,
    turnout_change,
)
from .models import AnalysisContext, ElectionTask
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
        self.runtime_config = self._load_runtime_config()
        self.campaign_state_builder = CampaignStateBuilder(
            self.repo_root,
            retrieval_backend=retrieval_backend,
            config=self.runtime_config,
            mode=mode,
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
    def _party_series(
        records: Iterable[Dict[str, Any]],
        independents: str = "candidate",
    ) -> Dict[Tuple[str, str], List[Tuple[int, float]]]:
        grouped: Dict[Tuple[str, str, int], float] = defaultdict(float)
        for record in records:
            region = str(record.get("jurisdiction") or "")
            key = series_key(record, independents=independents)
            year = int(record.get("election_year"))
            share = record.get("vote_share")
            if not region or not key or share is None:
                continue
            grouped[(region, key, year)] += float(share)

        series: Dict[Tuple[str, str], List[Tuple[int, float]]] = defaultdict(list)
        for (region, key, year), share in grouped.items():
            series[(region, key)].append((year, share))
        for key in series:
            series[key] = sorted(series[key])
        return dict(series)

    def _metric_settings(self) -> Tuple[float, float]:
        metrics_cfg = self.runtime_config.get("metrics", {})
        return (
            float(metrics_cfg.get("min_valid_votes_for_strong_trigger", 5000)),
            float(metrics_cfg.get("min_county_share_for_screening", 0.05)),
        )

    def _apply_sample_guard(self, payload: Dict[str, Any], valid_votes: Optional[float]) -> Dict[str, Any]:
        min_valid, _ = self._metric_settings()
        payload["valid_votes"] = None if valid_votes is None else int(valid_votes)
        if valid_votes is not None and valid_votes < min_valid:
            payload["small_sample"] = True
            if payload.get("threshold_status") == "strong_trigger":
                payload["threshold_status"] = "observe"
            payload.setdefault("warnings", []).append(
                f"valid votes {int(valid_votes)} < {int(min_valid)}; percentage-point gaps are unstable"
            )
        else:
            payload["small_sample"] = False
        return payload

    def _screened_keys(self, aggregate: ElectionAggregate, years: Iterable[int]) -> List[str]:
        _, min_share = self._metric_settings()
        keys: set = set()
        for year in years:
            for key, share in aggregate.ranked_keys(year):
                if share >= min_share:
                    keys.add(key)
        return sorted(keys)

    def _electoral_swings(self, records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return (descriptive raw swings, LocalSwing rows relative to the county swing)."""
        aggregate = ElectionAggregate(records, independents="candidate")
        years = aggregate.years()
        raw_rows: List[Dict[str, Any]] = []
        local_rows: List[Dict[str, Any]] = []
        for prev_year, year in zip(years, years[1:]):
            prev_count = len(aggregate.ranked_candidates(prev_year))
            count = len(aggregate.ranked_candidates(year))
            for key in self._screened_keys(aggregate, [prev_year, year]):
                common = [
                    region
                    for region in aggregate.regions(year)
                    if aggregate.share(year, region, key) is not None and aggregate.share(prev_year, region, key) is not None
                ]
                if not common:
                    continue
                county_now = aggregate.county_share(year, key, common)
                county_prev = aggregate.county_share(prev_year, key, common)
                if county_now is None or county_prev is None:
                    continue
                county_swing = county_now - county_prev
                comparability = {
                    "previous_candidates": sorted(aggregate.candidates.get((prev_year, key), set())),
                    "current_candidates": sorted(aggregate.candidates.get((year, key), set())),
                    "candidate_changed": aggregate.candidates.get((prev_year, key), set())
                    != aggregate.candidates.get((year, key), set()),
                    "candidate_count": [prev_count, count],
                    "candidate_count_changed": prev_count != count,
                }
                extras = {
                    "party": party_of(key),
                    "series_key": key,
                    "periods": [prev_year, year],
                    "county_swing": round(county_swing, 6),
                    "comparability": comparability,
                }
                for region in common:
                    share = float(aggregate.share(year, region, key) or 0.0)
                    prev_share = float(aggregate.share(prev_year, region, key) or 0.0)
                    raw = electoral_swing(
                        share,
                        prev_share,
                        region=region,
                        baseline_method=f"same_type_series:{key}:{prev_year}->{year}",
                        screen=False,
                    ).to_dict()
                    raw.update(extras)
                    raw_rows.append(raw)
                    local = local_swing(
                        share - prev_share,
                        county_swing,
                        region=region,
                        baseline_method=f"county_weighted_mean_swing:{key}:{prev_year}->{year}",
                    ).to_dict()
                    local.update(extras)
                    local["raw_swing"] = round(share - prev_share, 6)
                    local["vote_share"] = round(share, 6)
                    local["previous_vote_share"] = round(prev_share, 6)
                    local_rows.append(self._apply_sample_guard(local, aggregate.valid(year, region)))
        return raw_rows, local_rows

    def _split_ticket(
        self,
        president_records: List[Dict[str, Any]],
        legislator_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        pres = ElectionAggregate(president_records, independents="exclude")
        leg = ElectionAggregate(legislator_records, independents="exclude")
        results: List[Dict[str, Any]] = []
        for year in sorted(set(pres.years()) & set(leg.years())):
            for key in sorted(set(self._screened_keys(leg, [year])) & set(pres.keys(year))):
                paired = [
                    region
                    for region in leg.regions(year)
                    if leg.share(year, region, key) is not None and pres.share(year, region, key) is not None
                ]
                mismatched = {
                    region for region in paired if self._electorate_mismatch(leg.valid(year, region), pres.valid(year, region))
                }
                common = [region for region in paired if region not in mismatched]
                if not common:
                    continue
                county_leg = leg.county_share(year, key, common)
                county_pres = pres.county_share(year, key, common)
                if county_leg is None or county_pres is None:
                    continue
                county_split = county_leg - county_pres
                for region in paired:
                    leg_share = float(leg.share(year, region, key) or 0.0)
                    pres_share = float(pres.share(year, region, key) or 0.0)
                    payload = split_ticket_residual(
                        leg_share,
                        pres_share + county_split,
                        region=region,
                        baseline_method=f"same_day_president_vote_plus_county_split:{year}:{key}",
                    ).to_dict()
                    payload["baseline_value"] = round(pres_share, 6)
                    payload["raw_residual"] = round(leg_share - pres_share, 6)
                    payload["county_residual"] = round(county_split, 6)
                    payload["relative_residual"] = payload["residual"]
                    payload["party"] = key
                    payload["year"] = year
                    payload["legislator_candidates"] = sorted(leg.candidates.get((year, key), set()))
                    payload = self._apply_sample_guard(payload, leg.valid(year, region))
                    if region in mismatched:
                        payload["threshold_status"] = "not_comparable"
                        payload["local_explanation_required"] = False
                        payload["electorate_mismatch"] = True
                        payload.setdefault("warnings", []).append(
                            f"legislator valid votes {leg.valid(year, region)} vs president {pres.valid(year, region)}: "
                            "different electorates (e.g. indigenous voters vote in separate legislator races); "
                            "split-ticket comparison is not valid"
                        )
                    results.append(payload)
        return results

    @staticmethod
    def _electorate_mismatch(valid_a: Optional[float], valid_b: Optional[float], tolerance: float = 0.15) -> bool:
        if not valid_a or not valid_b:
            return False
        return abs(valid_a - valid_b) / max(valid_a, valid_b) > tolerance

    def _candidate_residuals(
        self,
        task: ElectionTask,
        same_type_records: List[Dict[str, Any]],
        president_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if task.election_type != "county_mayor" or not same_type_records or not president_records:
            return []

        same = ElectionAggregate(same_type_records, independents="exclude")
        pres = ElectionAggregate(president_records, independents="exclude")
        results: List[Dict[str, Any]] = []
        pres_years = pres.years()
        for year in same.years():
            before = [y for y in pres_years if y < year]
            after = [y for y in pres_years if y > year]
            if not before or not after:
                continue
            left, right = max(before), min(after)
            for key in self._screened_keys(same, [year]):
                common = [
                    region
                    for region in same.regions(year)
                    if same.share(year, region, key) is not None
                    and pres.share(left, region, key) is not None
                    and pres.share(right, region, key) is not None
                ]
                if not common:
                    continue
                county_share = same.county_share(year, key, common)
                county_left = pres.county_share(left, key, common)
                county_right = pres.county_share(right, key, common)
                if county_share is None or county_left is None or county_right is None:
                    continue
                county_residual = county_share - (county_left + county_right) / 2.0
                candidates = sorted(same.candidates.get((year, key), set()))
                for region in common:
                    share = float(same.share(year, region, key) or 0.0)
                    baseline = (float(pres.share(left, region, key) or 0.0) + float(pres.share(right, region, key) or 0.0)) / 2.0
                    payload = candidate_residual(
                        share,
                        baseline + county_residual,
                        baseline_method=f"bracketing_president_average_plus_county_residual:{left}+{right}",
                        region=region,
                    ).to_dict()
                    payload["baseline_value"] = round(baseline, 6)
                    payload["absolute_residual"] = round(share - baseline, 6)
                    payload["county_residual"] = round(county_residual, 6)
                    payload["relative_residual"] = payload["residual"]
                    payload["candidate"] = "、".join(candidates)
                    payload["party"] = key
                    payload["election_year"] = year
                    payload["president_years"] = [left, right]
                    results.append(self._apply_sample_guard(payload, same.valid(year, region)))
        return results

    def _spatial_anomalies(self, task: ElectionTask, same_type_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not same_type_records:
            return []
        aggregate = ElectionAggregate(same_type_records, independents="candidate")
        latest_year = aggregate.years()[-1]
        method = self.runtime_config.get("metrics", {}).get("spatial_variance_primary_method", "standard_deviation")
        results: List[Dict[str, Any]] = []
        for key in self._screened_keys(aggregate, [latest_year]):
            regions = [r for r in aggregate.regions(latest_year) if aggregate.share(latest_year, r, key) is not None]
            if len(regions) < 2:
                continue
            shares = [float(aggregate.share(latest_year, r, key) or 0.0) for r in regions]
            weights = [aggregate.valid(latest_year, r) or 0.0 for r in regions]
            use_weights = all(w > 0 for w in weights)
            result = spatial_variance(
                shares,
                method=method,
                region=task.jurisdiction,
                weights=weights if use_weights else None,
            )
            payload = result.to_dict()
            payload["party"] = party_of(key)
            payload["series_key"] = key
            payload["election_year"] = latest_year
            payload["level"] = "county"
            ranked = sorted(zip(regions, shares), key=lambda item: item[1])
            payload["lowest_areas"] = [{"region": r, "vote_share": round(v, 6)} for r, v in ranked[:3]]
            payload["highest_areas"] = [{"region": r, "vote_share": round(v, 6)} for r, v in ranked[-3:][::-1]]
            results.append(payload)
        return results

    def _turnout_changes(self, same_type_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        aggregate = ElectionAggregate(same_type_records)
        years = aggregate.years()
        results: List[Dict[str, Any]] = []
        for prev_year, year in zip(years, years[1:]):
            common = [
                r for r in aggregate.regions(year)
                if (year, r) in aggregate.region_turnout and (prev_year, r) in aggregate.region_turnout
            ]
            county_now = aggregate.county_turnout(year, common)
            county_prev = aggregate.county_turnout(prev_year, common)
            if not common or county_now is None or county_prev is None:
                continue
            county_change = county_now - county_prev
            for region in common:
                now = aggregate.region_turnout[(year, region)]
                prev = aggregate.region_turnout[(prev_year, region)]
                payload = turnout_change(
                    now - prev,
                    county_change,
                    region=region,
                    baseline_method=f"county_turnout_change:{prev_year}->{year}",
                ).to_dict()
                payload["turnout"] = round(now, 6)
                payload["previous_turnout"] = round(prev, 6)
                payload["periods"] = [prev_year, year]
                payload["county_turnout"] = [round(county_prev, 6), round(county_now, 6)]
                results.append(self._apply_sample_guard(payload, aggregate.valid(year, region)))
        return results

    def _geographic_concentration(self, task: ElectionTask, same_type_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not same_type_records:
            return []
        aggregate = ElectionAggregate(same_type_records)
        latest_year = aggregate.years()[-1]
        regions = aggregate.regions(latest_year)
        if len(regions) < 4:
            return []
        electorate = {}
        for region in regions:
            valid = aggregate.valid(latest_year, region)
            turnout = aggregate.region_turnout.get((latest_year, region))
            if valid and turnout:
                electorate[region] = valid / turnout
        if len(electorate) != len(regions):
            return []
        top_n = int(self.runtime_config.get("metrics", {}).get("concentration_top_n", 3))
        results: List[Dict[str, Any]] = []
        for key in self._screened_keys(aggregate, [latest_year]):
            votes = {r: aggregate.region_votes.get((latest_year, r, key), 0.0) for r in regions}
            payload = geographic_concentration(votes, electorate, top_n=top_n, region=task.jurisdiction).to_dict()
            payload["party"] = party_of(key)
            payload["series_key"] = key
            payload["election_year"] = latest_year
            payload.setdefault("warnings", []).append("electorate approximated as valid_votes / turnout")
            results.append(payload)
        return results

    def _load_adjacency(self, jurisdiction: str) -> Dict[str, List[str]]:
        path = self.repo_root / "data" / "geography" / "adjacency" / f"{jurisdiction}.yaml"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        adjacency = payload.get("adjacency", {}) if isinstance(payload, dict) else {}
        return {str(k): [str(v) for v in (vals or [])] for k, vals in adjacency.items()}

    def _neighbor_divergence(self, task: ElectionTask, same_type_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        adjacency = self._load_adjacency(task.jurisdiction)
        if not adjacency or not same_type_records:
            return []
        aggregate = ElectionAggregate(same_type_records)
        latest_year = aggregate.years()[-1]
        results: List[Dict[str, Any]] = []
        for key in self._screened_keys(aggregate, [latest_year]):
            for region, neighbors in sorted(adjacency.items()):
                own = aggregate.share(latest_year, region, key)
                shares = [aggregate.share(latest_year, n, key) for n in neighbors]
                shares = [float(s) for s in shares if s is not None]
                if own is None or not shares:
                    continue
                payload = neighbor_divergence(
                    float(own),
                    sum(shares) / len(shares),
                    region=region,
                    neighbor="、".join(neighbors),
                    metric_method="vote_share_minus_neighbor_mean",
                ).to_dict()
                payload["party"] = party_of(key)
                payload["series_key"] = key
                payload["election_year"] = latest_year
                results.append(self._apply_sample_guard(payload, aggregate.valid(latest_year, region)))
        return results

    @staticmethod
    def _triggered(metrics: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        triggered: List[Dict[str, Any]] = []
        for rows in metrics.values():
            for row in rows:
                if isinstance(row, dict) and row.get("local_explanation_required"):
                    triggered.append(row)
        return triggered

    def _load_events(self, jurisdiction: str) -> List[Dict[str, Any]]:
        base = self.repo_root / "cache" / "events"
        events: List[Dict[str, Any]] = []
        if not base.exists():
            return events
        for path in sorted(base.glob("*.jsonl")):
            for record in load_jsonl(path):
                region = str(record.get("jurisdiction") or record.get("county") or record.get("region") or "")
                if not region or region == jurisdiction:
                    events.append(record)
        return events

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
    ) -> AnalysisContext:
        online = self._allow_online(allow_online)
        readiness = self.gate.prepare(task, loader=self.loader, allow_online=online)

        records_by_type, files_used, load_warnings = self._load_periods(task, readiness)
        same_type = records_by_type.get(task.election_type, [])
        president = records_by_type.get("president", [])
        legislator = records_by_type.get("regional_legislator", [])

        historical_matrix = build_historical_matrix(same_type)
        cross_level_matrix = build_cross_level_matrix(records_by_type, level=task.analysis_level)

        raw_swings, local_swings = self._electoral_swings(same_type)
        metrics: Dict[str, List[Dict[str, Any]]] = {
            "electoral_swing": raw_swings,
            "local_swing": local_swings,
            "split_ticket": self._split_ticket(president, legislator),
            "candidate_residuals": self._candidate_residuals(task, same_type, president),
            "spatial_anomalies": self._spatial_anomalies(task, same_type),
            "turnout_change": self._turnout_changes(same_type),
            "geographic_concentration": self._geographic_concentration(task, same_type),
            "neighbor_divergence": self._neighbor_divergence(task, same_type),
        }
        triggered = self._triggered(metrics)

        # V1.4: build the current campaign state before deciding whether local
        # knowledge retrieval is needed. Current campaign change can now trigger
        # research independently of historical vote-pattern anomalies.
        polls_result = self.loader.load_polls(jurisdiction=task.jurisdiction)
        polls = [dict(item) for item in polls_result.get("records", [])]
        poll_freshness = evaluate_records(polls, kind="poll") if polls else {"fresh": 0, "stale": 0, "unknown": 0, "records": []}
        for status in poll_freshness.get("records", []):
            index = int(status.get("index", -1))
            if 0 <= index < len(polls):
                polls[index]["freshness_status"] = status.get("status")
                expires_at = status.get("expires_at")
                polls[index]["freshness_expires_at"] = expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at
        events = self._load_events(task.jurisdiction)
        current_candidates = readiness.available.get("current_candidates", {}).get("verified_candidates", [])

        campaign_leads, campaign_retrieval_warnings = self.campaign_state_builder.retrieve_current_leads(
            task.jurisdiction,
            task.target_year,
            allow_online=online,
        )
        campaign_state = self.campaign_state_builder.build(
            jurisdiction=task.jurisdiction,
            target_year=task.target_year,
            current_candidates=current_candidates,
            current_events=events,
            polls=polls,
            retrieval_leads=campaign_leads,
        )

        regions = sorted({
            str(item.get("region") or "").strip()
            for item in triggered
            if str(item.get("region") or "").strip() and str(item.get("region")) != task.jurisdiction
        })
        findings = FindingsBuilder(self.runtime_config).build(
            task,
            records_by_type,
            metrics,
            campaign_state=campaign_state,
            current_candidates=current_candidates,
            polls=polls,
        )
        if findings["research_questions"]:
            historical_questions = list(findings["research_questions"])
        else:
            historical_questions = self.knowledge_loader.build_research_questions(triggered) if triggered else []
        live_questions = campaign_research_questions(campaign_state)
        questions = list(dict.fromkeys(historical_questions + live_questions))
        research_triggered = bool(triggered) or bool(campaign_state.get("campaign_change_trigger")) or bool(campaign_leads)
        local_knowledge = self.knowledge_loader.load(
            task.jurisdiction,
            regions=regions or None,
            research_questions=questions,
            allow_online=online and research_triggered,
        )

        unknowns: List[str] = []
        if readiness.status == "INSUFFICIENT":
            unknowns.append("hard-required data are incomplete; full structural analysis is not allowed")
        if triggered and not local_knowledge.get("sufficient"):
            unknowns.append("local anomalies were detected but minimum sufficient local knowledge was not established")
        if campaign_state.get("campaign_change_trigger") and not local_knowledge.get("sufficient"):
            unknowns.append("current campaign change was detected but minimum sufficient current local knowledge was not established")
        if not metrics["neighbor_divergence"]:
            unknowns.append(
                f"no adjacency table at data/geography/adjacency/{task.jurisdiction}.yaml; neighbor divergence was not computed"
            )
        if not polls:
            unknowns.append("no validated poll cache is available; poll calibration is omitted")
        elif int(poll_freshness.get("fresh", 0)) == 0:
            unknowns.append(
                "no fresh validated poll is available; stale poll records may be retained as dated campaign-period evidence but must not calibrate the current state"
            )

        warnings = list(readiness.warnings) + load_warnings + campaign_retrieval_warnings + list(local_knowledge.get("warnings", []))
        if polls_result.get("invalid"):
            warnings.append(f"{len(polls_result['invalid'])} cached poll record(s) failed validation")
        if poll_freshness.get("stale"):
            warnings.append(f"{poll_freshness['stale']} poll record(s) are stale and must not be presented as current polling")

        baseline_methods = {
            "historical_matrix": "same-type elections at aligned geographic level",
            "electoral_swing": "descriptive only: share(t) - share(t-1); independents compared per candidate, never pooled",
            "local_swing": "regional swing minus valid-vote-weighted county swing; the only swing metric that triggers research",
            "candidate_residual": "relative residual: (share - bracketing president average) - county-wide residual",
            "split_ticket": "relative residual: (legislator - president share) - county-wide split",
            "spatial_variance": "valid-vote-weighted dispersion across townships; region is the county",
            "turnout_change": "regional turnout change minus county turnout change",
            "geographic_concentration": "top-N area vote share minus their electorate share (electorate ≈ valid/turnout)",
            "small_sample_guard": "areas below min_valid_votes_for_strong_trigger are capped at observe",
        }

        sources = self._source_summary(records_by_type, current_candidates + polls + events + campaign_leads)
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
            polls=polls,
            campaign_state=campaign_state,
            findings=findings,
            evidence_summary={
                "triggered_anomaly_count": len(triggered),
                "campaign_change_trigger": bool(campaign_state.get("campaign_change_trigger")),
                "campaign_change_reasons": campaign_state.get("campaign_change_reasons", []),
                "campaign_retrieval_lead_count": len(campaign_leads),
                "local_knowledge_sufficient": bool(local_knowledge.get("sufficient")),
                "poll_freshness": poll_freshness,
                "current_poll_calibration_available": int(poll_freshness.get("fresh", 0)) > 0,
                "fresh_poll_count": int(poll_freshness.get("fresh", 0)),
                "stale_poll_count": int(poll_freshness.get("stale", 0)),
            },
            unknowns=unknowns,
            warnings=warnings,
            sources=sources,
            files_used=files_used,
            baseline_methods=baseline_methods,
        )

        if write_manifest:
            self.context_builder.write_manifest(context, manifest_path)
        return context
