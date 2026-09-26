"""Command line entry point for the V1.4 data/runtime/knowledge layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analysis_context import AnalysisContextBuilder
from .data_readiness import DataReadinessGate
from .election_loader import ElectionLoader
from .metrics import (
    candidate_residual,
    electoral_swing,
    geographic_concentration,
    local_swing,
    neighbor_divergence,
    spatial_variance,
    split_ticket_residual,
)
from .host_retrieval import HostRetrievalBackend
from .knowledge_builder import KnowledgePromotionBuilder
from .knowledge_coverage import KnowledgeCoverageAudit
from .county_knowledge import COUNTIES, CountyKnowledgeProduction
from .models import ElectionTask
from .pipeline import AnalysisPipeline


def _task_from_args(args: argparse.Namespace) -> ElectionTask:
    candidates = []
    if getattr(args, "candidates", None):
        candidates = [item.strip() for item in args.candidates.split(",") if item.strip()]
    return ElectionTask(
        election_type=args.type,
        target_year=int(args.year),
        jurisdiction=args.county,
        analysis_level=getattr(args, "level", "township_district"),
        candidates=candidates,
    )


def _cmd_readiness(args: argparse.Namespace) -> int:
    gate = DataReadinessGate(Path(args.repo_root).resolve() if args.repo_root else None)
    report = gate.check(_task_from_args(args))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"STATUS: {report.status}")
    return 0


def _cmd_build_matrix(args: argparse.Namespace) -> int:
    task = _task_from_args(args)
    gate = DataReadinessGate(Path(args.repo_root).resolve() if args.repo_root else None)
    loader = ElectionLoader(gate.repo_root, mode="offline")
    years = [int(item) for item in args.years.split(",")] if args.years else gate.expected_years(task.election_type, task.target_year, 3)
    records_by_type: Dict[str, List[Dict[str, Any]]] = {}
    all_records: List[Dict[str, Any]] = []
    for year in years:
        result = loader.load_election(task.election_type, year, task.jurisdiction, task.analysis_level)
        records_by_type.setdefault(task.election_type, []).extend(result.records)
        all_records.extend(result.records)
    from .matrix_builder import build_cross_level_matrix, build_historical_matrix

    output = {
        "task": task.to_dict(),
        "years": years,
        "historical_matrix": build_historical_matrix(all_records),
        "cross_level_matrix": build_cross_level_matrix(records_by_type, level=task.analysis_level),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _require(args: argparse.Namespace, name: str) -> float:
    value = getattr(args, name, None)
    if value is None:
        raise SystemExit(f"metrics --metric requires --{name.replace('_', '-')}")
    return float(value)


def _cmd_metrics(args: argparse.Namespace) -> int:
    metric = args.metric
    if metric == "electoral_swing":
        result = electoral_swing(_require(args, "value_t"), _require(args, "value_prev"))
    elif metric == "local_swing":
        result = local_swing(_require(args, "value_t"), _require(args, "value_prev"))
    elif metric == "split_ticket_residual":
        result = split_ticket_residual(_require(args, "legislator"), _require(args, "president"))
    elif metric == "candidate_residual":
        baseline_method = args.baseline_method or "unspecified"
        result = candidate_residual(_require(args, "candidate"), _require(args, "baseline"), baseline_method)
    elif metric == "spatial_variance":
        values = [float(item) for item in args.values.split(",")] if args.values else []
        result = spatial_variance(values, method=args.method)
    elif metric == "neighbor_divergence":
        result = neighbor_divergence(_require(args, "area"), _require(args, "neighbor"), region=args.region or "")
    elif metric == "geographic_concentration":
        candidate_votes = json.loads(args.candidate_votes) if args.candidate_votes else {}
        electorate = json.loads(args.electorate) if args.electorate else {}
        result = geographic_concentration(candidate_votes, electorate, top_n=args.top_n)
    else:
        raise SystemExit(f"unsupported metric: {metric}")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    task = _task_from_args(args)
    gate = DataReadinessGate(Path(args.repo_root).resolve() if args.repo_root else None)
    readiness = gate.check(task)
    loader = ElectionLoader(gate.repo_root, mode="offline")
    records = loader.load_election(task.election_type, task.target_year - 4, task.jurisdiction, task.analysis_level)
    context = AnalysisContextBuilder(gate.repo_root).build(
        task=task,
        readiness=readiness,
        records_by_type={task.election_type: records.records},
        current_candidates=readiness.available.get("current_candidates", {}).get("candidates", []),
        warnings=readiness.warnings,
        files_used=[str(loader.repo_root / "data" / "elections")],
    )
    if args.write_manifest:
        path = AnalysisContextBuilder(gate.repo_root).write_manifest(context, Path(args.manifest) if args.manifest else None)
        print(f"manifest_written: {path}")
    print(json.dumps(context.to_dict(), ensure_ascii=False, indent=2))
    return 0



def _cmd_run(args: argparse.Namespace) -> int:
    task = _task_from_args(args)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    retrieval_backend = None
    if getattr(args, "retrieval_inbox", ""):
        retrieval_backend = HostRetrievalBackend(
            inbox_path=Path(args.retrieval_inbox).resolve()
        )
    if retrieval_backend is None and getattr(args, "retrieval_provider", "disabled") != "disabled":
        from .news_retrieval import build_retrieval_backend
        retrieval_backend = build_retrieval_backend(args.retrieval_provider, repo_root or Path(__file__).resolve().parents[1], args.mode)
    pipeline = AnalysisPipeline(
        repo_root=repo_root,
        mode=args.mode,
        retrieval_backend=retrieval_backend,
    )
    context = pipeline.run(
        task,
        allow_online=args.mode != "offline",
        write_manifest=args.write_manifest,
        manifest_path=Path(args.manifest) if args.manifest else None,
        as_of=args.as_of or None,
    )
    print(json.dumps(context.to_dict(), ensure_ascii=False, indent=2))
    status = context.analysis_context.get("readiness", {}).get("status")
    print(f"STATUS: {status}")
    return 0




def _cmd_knowledge_ingest(args: argparse.Namespace) -> int:
    builder = KnowledgePromotionBuilder(
        Path(args.repo_root).resolve() if args.repo_root else None
    )
    result = builder.ingest_retrieval_inbox(
        county=args.county,
        inbox_path=Path(args.retrieval_inbox).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["rejected_count"] == 0 else 2


def _cmd_knowledge_promote(args: argparse.Namespace) -> int:
    builder = KnowledgePromotionBuilder(
        Path(args.repo_root).resolve() if args.repo_root else None
    )
    result = builder.promote_inbox(
        proposal_inbox=Path(args.proposal_inbox).resolve(),
        county_override=args.county or None,
        dry_run=args.dry_run,
        build_package=not args.no_build_package,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["counts"]["requires_review"] or result["counts"]["rejected"]:
        return 2
    return 0


def _cmd_knowledge_build(args: argparse.Namespace) -> int:
    builder = KnowledgePromotionBuilder(
        Path(args.repo_root).resolve() if args.repo_root else None
    )
    result = builder.build_county_package(args.county)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _knowledge_counties(args: argparse.Namespace) -> List[str]:
    if getattr(args, "all_counties", False):
        return list(COUNTIES)
    values = [item.strip() for item in str(getattr(args, "counties", "") or "").split(",") if item.strip()]
    if not values:
        raise SystemExit("knowledge-production requires --counties or --all-counties")
    return values


def _cmd_knowledge_production(args: argparse.Namespace) -> int:
    production = CountyKnowledgeProduction(
        Path(args.repo_root).resolve() if args.repo_root else None
    )
    result = production.build_many(
        _knowledge_counties(args),
        incremental=args.incremental,
        dry_run=args.dry_run,
        run_research=args.run_research,
        resume=not args.no_resume,
        year=args.year,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed_count"] == 0 else 2


def _cmd_knowledge_status(args: argparse.Namespace) -> int:
    production = CountyKnowledgeProduction(
        Path(args.repo_root).resolve() if args.repo_root else None
    )
    counties = list(COUNTIES) if args.all_counties else [
        item.strip() for item in str(args.counties or "").split(",") if item.strip()
    ]
    result = production.status(counties or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_knowledge_coverage(args: argparse.Namespace) -> int:
    counties = list(COUNTIES) if args.all_counties else [
        item.strip() for item in str(args.counties or "").split(",") if item.strip()
    ]
    result = KnowledgeCoverageAudit(
        Path(args.repo_root).resolve() if args.repo_root else None
    ).build(counties or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_knowledge_curated_baseline(args: argparse.Namespace) -> int:
    from .curated_county_baseline import CuratedCountyBaseline

    result = CuratedCountyBaseline(
        Path(args.repo_root).resolve() if args.repo_root else None
    ).run(
        _knowledge_counties(args),
        dry_run=args.dry_run,
        refresh_existing=args.refresh_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not {"rejected", "requires_review"}.intersection(result["decisions"]) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m runtime.cli", description="V1.4 Data, Runtime & Knowledge Layer")
    parser.add_argument("--repo-root", default=None, help="repository root (defaults to runtime parent)")
    sub = parser.add_subparsers(dest="command", required=True)

    readiness = sub.add_parser("readiness", help="run Data Readiness Gate")
    readiness.add_argument("--county", required=True)
    readiness.add_argument("--year", required=True, type=int)
    readiness.add_argument("--type", required=True)
    readiness.add_argument("--level", default="township_district")
    readiness.add_argument("--candidates", default="")
    readiness.set_defaults(func=_cmd_readiness)

    matrix = sub.add_parser("build-matrix", help="build local election matrices")
    matrix.add_argument("--county", required=True)
    matrix.add_argument("--year", required=True, type=int)
    matrix.add_argument("--type", required=True)
    matrix.add_argument("--level", default="township_district")
    matrix.add_argument("--years", default="")
    matrix.set_defaults(func=_cmd_build_matrix)

    metrics = sub.add_parser("metrics", help="compute a V1.1 metric")
    metrics.add_argument("--metric", required=True, choices=[
        "electoral_swing", "local_swing", "split_ticket_residual", "candidate_residual",
        "spatial_variance", "neighbor_divergence", "geographic_concentration",
    ])
    metrics.add_argument("--value-t", type=float)
    metrics.add_argument("--value-prev", type=float)
    metrics.add_argument("--legislator", type=float)
    metrics.add_argument("--president", type=float)
    metrics.add_argument("--candidate", type=float)
    metrics.add_argument("--baseline", type=float)
    metrics.add_argument("--baseline-method", default="")
    metrics.add_argument("--values", default="")
    metrics.add_argument("--method", default="standard_deviation")
    metrics.add_argument("--area", type=float)
    metrics.add_argument("--neighbor", type=float)
    metrics.add_argument("--region", default="")
    metrics.add_argument("--candidate-votes", default="")
    metrics.add_argument("--electorate", default="")
    metrics.add_argument("--top-n", type=int, default=3)
    metrics.set_defaults(func=_cmd_metrics)

    context = sub.add_parser("context", help="build Analysis Context and optional manifest")
    context.add_argument("--county", required=True)
    context.add_argument("--year", required=True, type=int)
    context.add_argument("--type", required=True)
    context.add_argument("--level", default="township_district")
    context.add_argument("--candidates", default="")
    context.add_argument("--write-manifest", action="store_true")
    context.add_argument("--manifest", default="")
    context.set_defaults(func=_cmd_context)

    run = sub.add_parser("run", help="run the end-to-end V1.4 preparation pipeline")
    run.add_argument("--county", required=True)
    run.add_argument("--year", required=True, type=int)
    run.add_argument("--type", required=True)
    run.add_argument("--level", default="township_district")
    run.add_argument("--candidates", default="")
    run.add_argument("--mode", choices=["auto", "online", "offline"], default="auto")
    run.add_argument("--as-of", default="", help="ISO date or timestamp for this campaign snapshot")
    run.add_argument("--write-manifest", action="store_true")
    run.add_argument("--manifest", default="")
    run.add_argument(
        "--retrieval-inbox",
        default="",
        help="JSON/JSONL host-web retrieval inbox for local-knowledge research questions",
    )
    run.add_argument("--retrieval-provider", choices=["local", "gdelt", "disabled"], default="local")
    run.set_defaults(func=_cmd_run)

    ingest = sub.add_parser(
        "knowledge-ingest",
        help="import host Web/Search JSON/JSONL into retrieval staging only",
    )
    ingest.add_argument("--county", required=True)
    ingest.add_argument("--retrieval-inbox", required=True)
    ingest.set_defaults(func=_cmd_knowledge_ingest)

    promote = sub.add_parser(
        "knowledge-promote",
        help="validate and promote structured V1.3 knowledge proposals",
    )
    promote.add_argument("--county", default="")
    promote.add_argument("--proposal-inbox", required=True)
    promote.add_argument("--dry-run", action="store_true")
    promote.add_argument("--no-build-package", action="store_true")
    promote.set_defaults(func=_cmd_knowledge_promote)

    build = sub.add_parser(
        "knowledge-build",
        help="rebuild the generated county knowledge package indexes",
    )
    build.add_argument("--county", required=True)
    build.set_defaults(func=_cmd_knowledge_build)

    production = sub.add_parser(
        "knowledge-production",
        help="prepare or incrementally update one or all 22 county knowledge packages",
    )
    production.add_argument("--counties", default="", help="comma-separated county names")
    production.add_argument("--all-counties", action="store_true")
    production.add_argument("--incremental", action="store_true")
    production.add_argument("--dry-run", action="store_true")
    production.add_argument(
        "--run-research",
        action="store_true",
        help="invoke configured GLM/Tavily research; results remain retrieval leads",
    )
    production.add_argument("--no-resume", action="store_true")
    production.add_argument("--year", type=int, default=2026)
    production.set_defaults(func=_cmd_knowledge_production)

    status = sub.add_parser(
        "knowledge-status",
        help="inspect county knowledge production and package status",
    )
    status.add_argument("--counties", default="")
    status.add_argument("--all-counties", action="store_true")
    status.set_defaults(func=_cmd_knowledge_status)

    coverage = sub.add_parser(
        "knowledge-coverage",
        help="audit actual row-level coverage versus catalogs/leads for 22 counties",
    )
    coverage.add_argument("--counties", default="")
    coverage.add_argument("--all-counties", action="store_true")
    coverage.set_defaults(func=_cmd_knowledge_coverage)

    curated = sub.add_parser(
        "knowledge-curated-baseline",
        help="stage and promote the tracked chat-verified county baseline",
    )
    curated.add_argument("--counties", default="", help="comma-separated county names")
    curated.add_argument("--all-counties", action="store_true")
    curated.add_argument("--dry-run", action="store_true")
    curated.add_argument(
        "--refresh-existing",
        action="store_true",
        help="re-evaluate records whose stable record id already exists",
    )
    curated.set_defaults(func=_cmd_knowledge_curated_baseline)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
