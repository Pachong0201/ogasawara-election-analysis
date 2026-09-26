"""Build a single Analysis Context for LLM reporting and a provenance manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import AnalysisContext, ElectionTask, ReadinessReport, as_jsonable, utc_now_iso


class AnalysisContextBuilder:
    def __init__(self, repo_root: Optional[Path] = None, skill_version: str = "1.4.0"):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.skill_version = skill_version

    def build(
        self,
        task: ElectionTask,
        readiness: ReadinessReport,
        records_by_type: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        local_knowledge: Optional[Dict[str, Any]] = None,
        current_candidates: Optional[List[Dict[str, Any]]] = None,
        current_events: Optional[List[Dict[str, Any]]] = None,
        campaign_event_resolution: Optional[Dict[str, Any]] = None,
        event_importance_signals: Optional[List[Dict[str, Any]]] = None,
        campaign_state: Optional[Dict[str, Any]] = None,
        polls: Optional[List[Dict[str, Any]]] = None,
        evidence_summary: Optional[Dict[str, Any]] = None,
        unknowns: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        files_used: Optional[List[str]] = None,
        web_sources_used: Optional[List[str]] = None,
        baseline_methods: Optional[Dict[str, str]] = None,
    ) -> AnalysisContext:
        records_by_type = records_by_type or {}
        metrics = metrics or {}
        local_knowledge = local_knowledge or {}
        current_candidates = current_candidates or []
        current_events = current_events or []
        campaign_event_resolution = campaign_event_resolution or {}
        event_importance_signals = event_importance_signals or []
        campaign_state = campaign_state or {}
        polls = polls or []
        evidence_summary = evidence_summary or {}
        unknowns = unknowns or []
        warnings = warnings or []
        sources = sources or []
        files_used = files_used or []
        web_sources_used = web_sources_used or []
        baseline_methods = baseline_methods or {}

        knowledge_views = {
            "stable_local_baseline": {
                "includes": [
                    "historical_baseline",
                    "local_knowledge.stable_local_baseline",
                ],
                "available": bool(
                    records_by_type.get("historical_baseline")
                    or local_knowledge.get("stable_local_baseline")
                ),
                "rule": (
                    "read this view first for election history, boundaries, spatial matrices, "
                    "historical claims and official social context"
                ),
            },
            "dynamic_campaign_state": {
                "includes": [
                    "local_knowledge.dynamic_local_state",
                    "current_candidates",
                    "current_events",
                    "campaign_state",
                    "polls",
                ],
                "as_of": campaign_state.get("as_of"),
                "status": campaign_state.get("campaign_state_status"),
                "rule": (
                    "use only time-valid current evidence; retrieval leads stay unverified, "
                    "and dynamic evidence must not be converted into a winner prediction"
                ),
            },
        }

        context: Dict[str, Any] = {
            "task": task.to_dict(),
            "readiness": readiness.to_dict(),
            "historical_baseline": records_by_type.get("historical_baseline", {}),
            "electoral_swing": metrics.get("electoral_swing", []),
            "split_ticket": metrics.get("split_ticket", []),
            "candidate_residuals": metrics.get("candidate_residuals", []),
            "spatial_anomalies": metrics.get("spatial_anomalies", []),
            "local_knowledge": local_knowledge,
            "knowledge_views": knowledge_views,
            "current_candidates": current_candidates,
            "current_events": current_events,
            "campaign_event_resolution": campaign_event_resolution,
            "event_importance_signals": event_importance_signals,
            "campaign_state": campaign_state,
            "as_of": campaign_state.get("as_of"),
            "campaign_state_status": campaign_state.get("campaign_state_status"),
            "campaign_windows": campaign_state.get("windows", {}),
            "snapshot_delta": campaign_state.get("snapshot_delta", {}),
            "campaign_change_trigger": campaign_state.get("campaign_change_trigger", False),
            "campaign_change_reasons": campaign_state.get("campaign_change_reasons", []),
            "event_conflicts": campaign_state.get("event_conflicts", []),
            "same_series_poll_changes": campaign_state.get("same_series_poll_changes", []),
            "polls": polls,
            "evidence_summary": evidence_summary,
            "unknowns": unknowns,
            "warnings": warnings,
            "sources": sources,
        }
        manifest = {
            "task_id": task.task_id,
            "created_at": utc_now_iso(),
            "skill_version": self.skill_version,
            "data_sources": sources,
            "files_used": files_used,
            "web_sources_used": web_sources_used,
            "metrics": metrics,
            "baseline_methods": baseline_methods,
            "freshness": {
                "readiness_status": readiness.status,
                "stale": readiness.stale,
                "campaign_as_of": campaign_state.get("as_of"),
            },
            "missing_data": readiness.missing,
            "unknowns": unknowns,
            "warnings": warnings,
            "campaign_event_resolution": campaign_event_resolution.get("stats", {}),
            "event_importance_signal_count": len(event_importance_signals),
        }
        return AnalysisContext(analysis_context=context, analysis_manifest=manifest)

    def write_manifest(self, context: AnalysisContext, output_path: Optional[Path] = None) -> Path:
        path = Path(output_path) if output_path else self.repo_root / "analysis_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(context.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def ensure_llm_input(context: AnalysisContext) -> Dict[str, Any]:
        """Return the only payload that should be handed to an LLM report writer."""
        return {
            "analysis_context": as_jsonable(context.analysis_context),
            "analysis_manifest_summary": {
                "task_id": context.analysis_manifest.get("task_id"),
                "skill_version": context.analysis_manifest.get("skill_version"),
                "missing_data": context.analysis_manifest.get("missing_data", []),
                "warnings": context.analysis_manifest.get("warnings", []),
            },
        }
