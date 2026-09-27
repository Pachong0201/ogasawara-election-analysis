#!/usr/bin/env python3
"""Summarize official county-context sources: coverage, hashes and gaps.

Distinguishes row-level coverage, real source absence, ambiguous mapping and
external download blockage.  It never promotes rows into knowledge.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, Dict

import yaml

REPO = Path(__file__).resolve().parents[1]


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_inventory() -> Dict[str, Any]:
    config = load_yaml(REPO / "config" / "county_context_sources.yaml")
    counties = sorted(
        p.name for p in (REPO / "data" / "context").iterdir() if p.is_dir() and p.name != "_unmapped"
    )
    materialization = {}
    path = REPO / "data" / "manifests" / "context_materialization_latest.json"
    if path.exists():
        materialization = json.loads(path.read_text(encoding="utf-8"))
    failures = {f["source_id"]: f["error"] for f in materialization.get("failures", [])}

    inventory: Dict[str, Any] = {
        "generated_from": "scripts/context_source_report.py",
        "county_count": len(counties),
        "sources": {},
    }
    for source in (config.get("sources") or {}).values():
        source_id = source["source_id"]
        kind = source["kind"]
        per_county: Dict[str, int] = {}
        unmapped_reasons: Dict[str, int] = {}
        for county in counties:
            rows = [
                row
                for row in load_jsonl(REPO / "data" / "context" / county / f"{kind}.jsonl")
                if row.get("source_id") == source_id
            ]
            if rows:
                per_county[county] = len(rows)
        for pattern in (REPO / "data" / "context" / "_unmapped").glob(f"{kind}-{source_id}.jsonl"):
            for row in load_jsonl(pattern):
                reason = str(row.get("unmapped_reason") or "unknown")
                unmapped_reasons[reason] = unmapped_reasons.get(reason, 0) + 1
        missing = sorted(set(counties) - set(per_county))
        if not per_county and not unmapped_reasons:
            missing_reason = "blocked_source_download_failure"
        elif not missing:
            missing_reason = "full_county_coverage"
        elif unmapped_reasons.get("no_county_name_matched") and len(missing) <= 3:
            missing_reason = "source_contains_no_single_county_row"
        else:
            missing_reason = "source_has_zero_rows_for_missing_counties"

        manifest_path = REPO / "data" / "manifests" / f"context_{source_id}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        inventory["sources"][source_id] = {
            "name": source["source_name"],
            "kind": kind,
            "source_grade": source["source_grade"],
            "dataset_url": source["dataset_url"],
            "resource_url": source["resource_url"],
            "time_scope": source.get("time_scope") or "",
            "update_frequency": source.get("update_frequency") or "",
            "configured_timeout_seconds": source.get("timeout_seconds"),
            "configured_max_retries": source.get("max_retries"),
            "row_data_state": "row_data_available" if per_county else "catalog_or_failure_only",
            "counties_with_rows": len(per_county),
            "missing_counties": missing,
            "missing_reason": missing_reason,
            "mapped_rows": sum(per_county.values()),
            "unmapped_rows": sum(unmapped_reasons.values()),
            "unmapped_reasons": dict(sorted(unmapped_reasons.items())),
            "county_rows": dict(sorted(per_county.items())),
            "manifest_path": str(manifest_path.relative_to(REPO)) if manifest_path.exists() else "",
            "imported_at": manifest.get("imported_at", ""),
            "official_source_sha256": manifest.get("source_sha256", ""),
            "raw_cache_sha256": manifest.get("raw_cache_sha256", ""),
            "raw_cache_path": manifest.get("raw_cache_path", ""),
            "download_fallback": bool(manifest.get("download_fallback")),
            "download_error": manifest.get("download_error", ""),
            "download_failure": failures.get(source_id, ""),
        }
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="data/manifests/context_source_inventory.json"
    )
    args = parser.parse_args()
    inventory = build_inventory()
    out = REPO / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(out.relative_to(REPO)),
                "sources": {
                    sid: {
                        "rows": row["mapped_rows"],
                        "counties": row["counties_with_rows"],
                        "missing": row["missing_counties"],
                        "reason": row["missing_reason"],
                    }
                    for sid, row in inventory["sources"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
