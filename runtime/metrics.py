"""Metric implementations for V1.1.

Every metric returns a ``MetricResult`` so that observed values, baseline
methods, thresholds and warnings travel together.
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .models import MetricResult

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "runtime.yaml"


def _load_thresholds(config_path: Optional[Path] = None) -> Dict[str, float]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    return dict(config.get("metrics", {}).get("thresholds", {}))


def _threshold_status(value: Optional[float], observe: Optional[float], strong: Optional[float]) -> str:
    if value is None:
        return "not_evaluated"
    magnitude = abs(value)
    if strong is not None and magnitude >= float(strong):
        return "strong_trigger"
    if observe is not None and magnitude >= float(observe):
        return "observe"
    return "below"


def _threshold_status_pp(value: Optional[float], observe: Optional[float], strong: Optional[float]) -> str:
    """Compare a decimal vote-share difference against percentage-point thresholds."""
    if value is None:
        return "not_evaluated"
    return _threshold_status(float(value) * 100.0, observe, strong)


def _local_explanation(status: str) -> bool:
    return status in {"observe", "strong_trigger"}


def _round(value: Optional[float], decimals: int = 6) -> Optional[float]:
    return None if value is None else round(float(value), decimals)


def electoral_swing(
    vote_share_t: float,
    vote_share_previous: float,
    region: str = "",
    baseline_method: str = "previous_same_type_election",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    thresholds = thresholds or _load_thresholds()
    residual = float(vote_share_t) - float(vote_share_previous)
    status = _threshold_status_pp(residual, thresholds.get("swing_gap_observe"), thresholds.get("swing_gap_strong_trigger"))
    return MetricResult(
        metric="electoral_swing",
        region=region,
        observed_value=_round(vote_share_t),
        baseline_value=_round(vote_share_previous),
        residual=_round(residual),
        baseline_method=baseline_method,
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
    )


def local_swing(
    swing: float,
    region_swing: float,
    region: str = "",
    baseline_method: str = "region_mean_swing",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    thresholds = thresholds or _load_thresholds()
    residual = float(swing) - float(region_swing)
    status = _threshold_status_pp(residual, thresholds.get("swing_gap_observe"), thresholds.get("swing_gap_strong_trigger"))
    return MetricResult(
        metric="local_swing",
        region=region,
        observed_value=_round(swing),
        baseline_value=_round(region_swing),
        residual=_round(residual),
        baseline_method=baseline_method,
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
    )


def split_ticket_residual(
    legislator_vote_share: float,
    president_vote_share: float,
    region: str = "",
    baseline_method: str = "same_day_president_vote",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    thresholds = thresholds or _load_thresholds()
    residual = float(legislator_vote_share) - float(president_vote_share)
    status = _threshold_status_pp(residual, thresholds.get("cross_level_gap_observe"), thresholds.get("cross_level_gap_strong_trigger"))
    return MetricResult(
        metric="split_ticket_residual",
        region=region,
        observed_value=_round(legislator_vote_share),
        baseline_value=_round(president_vote_share),
        residual=_round(residual),
        baseline_method=baseline_method,
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
        warnings=[],
    )


def candidate_residual(
    candidate_vote_share: float,
    reference_baseline: float,
    baseline_method: str,
    region: str = "",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    if not str(baseline_method or "").strip():
        raise ValueError("baseline_method is required for candidate_residual")
    thresholds = thresholds or _load_thresholds()
    residual = float(candidate_vote_share) - float(reference_baseline)
    status = _threshold_status_pp(
        residual,
        thresholds.get("candidate_residual_observe"),
        thresholds.get("candidate_residual_strong_trigger"),
    )
    return MetricResult(
        metric="candidate_residual",
        region=region,
        observed_value=_round(candidate_vote_share),
        baseline_value=_round(reference_baseline),
        residual=_round(residual),
        baseline_method=baseline_method,
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
    )


def spatial_variance(
    vote_shares: Iterable[float],
    method: str = "standard_deviation",
    region: str = "",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    values = [float(value) for value in vote_shares if value is not None]
    if not values:
        return MetricResult(
            metric="spatial_variance",
            region=region,
            metric_method=method,
            warnings=["no vote_share values supplied"],
        )
    thresholds = thresholds or _load_thresholds()
    warnings: List[str] = []
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if len(values) > 1 else 0.0
    sorted_values = sorted(values)
    if len(sorted_values) >= 4:
        q1, _, q3 = statistics.quantiles(sorted_values, n=4, method="inclusive")
        iqr = q3 - q1
    else:
        iqr = sorted_values[-1] - sorted_values[0] if len(sorted_values) > 1 else 0.0

    if method == "coefficient_of_variation":
        value = None if mean == 0 else sd / mean
        metric_method = "coefficient_of_variation"
        if mean < float(thresholds.get("spatial_variance_low_share_threshold", 0.20)):
            warnings.append("mean vote_share is low; CV is auxiliary only; prefer SD/IQR")
    elif method == "iqr":
        value = iqr
        metric_method = "iqr"
    else:
        value = sd
        metric_method = "standard_deviation"

    status = "not_evaluated"
    if metric_method == "coefficient_of_variation":
        status = _threshold_status(value, thresholds.get("spatial_variance_observe"), thresholds.get("spatial_variance_strong_trigger"))
    else:
        # Absolute SD/IQR use the same configured thresholds only when they are meaningful;
        # otherwise report below/above without over-claiming.
        status = _threshold_status(value, thresholds.get("spatial_variance_observe"), thresholds.get("spatial_variance_strong_trigger"))
    status = "below" if status == "not_evaluated" and value is not None else status
    return MetricResult(
        metric="spatial_variance",
        region=region,
        observed_value=_round(value),
        residual=None,
        baseline_method="within_region_dispersion",
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
        metric_method=metric_method,
        warnings=warnings,
        details={"sd": _round(sd), "cv": _round(None if mean == 0 else sd / mean), "iqr": _round(iqr), "mean": _round(mean)},
    )


def neighbor_divergence(
    area_value: float,
    neighbor_value: float,
    region: str = "",
    neighbor: str = "",
    metric_method: str = "vote_share_difference",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    thresholds = thresholds or _load_thresholds()
    residual = float(area_value) - float(neighbor_value)
    status = _threshold_status_pp(
        residual,
        thresholds.get("neighbor_divergence_observe"),
        thresholds.get("neighbor_divergence_strong_trigger"),
    )
    return MetricResult(
        metric="neighbor_divergence",
        region=f"{region}|{neighbor}" if neighbor else region,
        observed_value=_round(area_value),
        baseline_value=_round(neighbor_value),
        residual=_round(residual),
        baseline_method="adjacent_area_comparison",
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
        metric_method=metric_method,
    )


def geographic_concentration(
    candidate_votes_by_area: Dict[str, float],
    electorate_by_area: Dict[str, float],
    top_n: int = 3,
    region: str = "",
    thresholds: Optional[Dict[str, float]] = None,
) -> MetricResult:
    thresholds = thresholds or _load_thresholds()
    total_votes = sum(float(value) for value in candidate_votes_by_area.values())
    total_electorate = sum(float(value) for value in electorate_by_area.values())
    if total_votes <= 0 or total_electorate <= 0:
        return MetricResult(
            metric="geographic_concentration",
            region=region,
            warnings=["candidate votes and electorate must both be positive"],
            metric_method="excess_concentration",
        )
    ranked = sorted(candidate_votes_by_area.items(), key=lambda item: float(item[1]), reverse=True)
    top = ranked[: max(1, int(top_n))]
    candidate_share_top = sum(float(value) for _, value in top) / total_votes
    electorate_share_top = sum(float(electorate_by_area.get(area, 0.0)) for area, _ in top) / total_electorate
    excess = candidate_share_top - electorate_share_top
    status = _threshold_status(candidate_share_top, thresholds.get("geographic_concentration_observe"), thresholds.get("geographic_concentration_strong_trigger"))
    if status != "below" and float(excess) < float(thresholds.get("concentration_excess_min", 0.05)):
        status = "below"
    return MetricResult(
        metric="geographic_concentration",
        region=region,
        observed_value=_round(candidate_share_top),
        baseline_value=_round(electorate_share_top),
        residual=_round(excess),
        baseline_method="electorate_share_in_same_areas",
        threshold_status=status,
        local_explanation_required=_local_explanation(status),
        metric_method="excess_concentration",
        details={
            "top_n": int(top_n),
            "top_areas": [area for area, _ in top],
            "candidate_vote_share_top": _round(candidate_share_top),
            "electorate_share_top": _round(electorate_share_top),
            "excess_concentration": _round(excess),
        },
    )
