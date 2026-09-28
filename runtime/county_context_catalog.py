"""Official county social-context source catalog and offline raw importer.

The catalog stages A-grade source *catalog* leads only. Raw files are imported
separately and preserved with SHA-256 provenance. No record in this module is
allowed to imply political preference, mobilization effectiveness, or election
outcomes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from .county_knowledge import COUNTIES, county_research_questions
from .election_loader import load_jsonl, safe_component, write_jsonl
from .models import utc_now_iso


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "county_context_sources.yaml"


def _question(county: str, topic: str) -> str:
    for row in county_research_questions(county):
        if row["topic"] == topic:
            return row["question"]
    raise ValueError(f"unknown topic={topic}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode(content: bytes) -> Tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("context source could not be decoded as UTF-8/CP950/Big5")


def _load_csv(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    text, encoding = _decode(path.read_bytes())
    reader = csv.DictReader(io.StringIO(text))
    return [
        {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
        for row in reader
        if isinstance(row, dict)
    ], encoding


def _load_json(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    text, encoding = _decode(path.read_bytes())
    payload = json.loads(text)
    if isinstance(payload, dict):
        preferred = {"records", "data", "result", "results", "rows", "items"}
        selected = None
        for key, value in payload.items():
            if str(key).strip().lower() in preferred and isinstance(value, list):
                selected = value
                break
        if selected is None:
            list_values = [
                value
                for value in payload.values()
                if isinstance(value, list)
                and any(isinstance(item, dict) for item in value)
            ]
            if len(list_values) == 1:
                selected = list_values[0]
        payload = selected if selected is not None else [payload]
    if not isinstance(payload, list):
        raise ValueError("JSON context source must contain an object or list")
    return [dict(row) for row in payload if isinstance(row, dict)], encoding


def _leaf_record(element: ET.Element) -> Optional[Dict[str, Any]]:
    children = list(element)
    if len(children) < 2:
        return None
    if any(list(child) for child in children):
        return None
    row = {
        str(child.tag).split("}")[-1]: str(child.text or "").strip()
        for child in children
    }
    return row if any(row.values()) else None


def _load_xml(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    raw = path.read_bytes()
    _text, encoding = _decode(raw)
    root = ET.fromstring(raw)
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for element in root.iter():
        row = _leaf_record(element)
        if not row:
            continue
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        rows.append(row)
    if not rows:
        raise ValueError("XML context source contained no record-like rows")
    return rows, encoding


def load_rows(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix == ".json":
        return _load_json(path)
    if suffix == ".xml":
        return _load_xml(path)
    raise ValueError(f"unsupported context file format: {suffix}")


COUNTY_VARIANTS: Dict[str, Tuple[str, ...]] = {
    county: tuple(dict.fromkeys((county, county.replace("台", "臺"), county.replace("臺", "台"))))
    for county in COUNTIES
}


def _counties_in_row(row: Dict[str, Any]) -> List[str]:
    haystack = " ".join(str(value or "") for value in row.values()).replace("臺", "台")
    matches: List[str] = []
    for county, variants in COUNTY_VARIANTS.items():
        if any(variant.replace("臺", "台") in haystack for variant in variants):
            matches.append(county)
    return matches


def _to_number(value: Any) -> Optional[float]:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find_field(row: Dict[str, Any], needles: Sequence[str]) -> Optional[str]:
    for key in row:
        compact = str(key).replace(" ", "").replace("－", "-")
        if all(needle in compact for needle in needles):
            return key
    return None


def age_summary(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    total_key = _find_field(row, ("總計", "Grand_total"))
    under_key = _find_field(row, ("未滿15歲", "合計"))
    working_key = _find_field(row, ("15_64", "合計"))
    senior_key = _find_field(row, ("65歲以上", "合計"))
    if not all((total_key, under_key, working_key, senior_key)):
        return None
    total = _to_number(row.get(total_key))
    under = _to_number(row.get(under_key))
    working = _to_number(row.get(working_key))
    senior = _to_number(row.get(senior_key))
    if not total or None in (under, working, senior):
        return None
    return {
        "total": int(total),
        "under_15": int(under),
        "age_15_64": int(working),
        "age_65_plus": int(senior),
        "under_15_share": round(under / total, 6),
        "age_15_64_share": round(working / total, 6),
        "age_65_plus_share": round(senior / total, 6),
    }


class CountyContextCatalog:
    def __init__(self, repo_root: Optional[Path] = None, config_path: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.config_path = Path(config_path) if config_path else self.repo_root / "config" / "county_context_sources.yaml"
        if not self.config_path.exists():
            self.config_path = DEFAULT_CONFIG
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def catalog_leads(self, county: str) -> List[Dict[str, Any]]:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        leads: List[Dict[str, Any]] = []
        for source in (self.config.get("sources") or {}).values():
            topic = str(source["topic"])
            question = _question(county, topic)
            leads.append(
                {
                    "lead_id": f"context-catalog-{safe_component(county)}-{source['source_id']}",
                    "county": county,
                    "query": question,
                    "research_questions": [question],
                    "title": source["source_name"],
                    "summary": f"官方数据源目录：{source['source_name']}；尚未据目录生成{county}行级事实。",
                    "evidence": str(source.get("scope_boundary") or ""),
                    "url": source["dataset_url"],
                    "source_id": source["source_id"],
                    "source_name": source["source_name"],
                    "source_grade": source["source_grade"],
                    "verification_status": "verified_source_catalog",
                    "independence_key": source["independence_key"],
                    "retrieved_at": utc_now_iso(),
                    "time_scope": source.get("time_scope") or "",
                    "scope_boundary": source.get("scope_boundary") or "",
                }
            )
        return leads

    def stage(self, counties: Iterable[str]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for county in counties:
            leads = self.catalog_leads(county)
            path = self.repo_root / "cache" / "retrieval" / f"{safe_component(county)}.jsonl"
            existing = load_jsonl(path) if path.exists() else []
            merged: Dict[str, Dict[str, Any]] = {}
            for row in existing + leads:
                key = str(row.get("lead_id") or row.get("url") or "").strip()
                if key:
                    merged[key] = row
            write_jsonl(path, merged.values())
            counts[county] = len(leads)
        return {"counties": counts, "lead_count": sum(counts.values())}

    @staticmethod
    def _merge_source_records(
        existing: Iterable[Dict[str, Any]],
        incoming: Iterable[Dict[str, Any]],
        source_id: str,
    ) -> List[Dict[str, Any]]:
        """Replace only ``source_id`` records and keep every other source.

        Re-importing the same source is idempotent because records are keyed by
        ``source_id`` and sorted deterministically; records removed from the
        newest official file therefore do not linger.
        """
        kept = [
            row
            for row in existing
            if str(row.get("source_id") or "").strip() != source_id
        ]
        merged = kept + list(incoming)
        merged.sort(
            key=lambda row: (
                str(row.get("source_id") or ""),
                int(row.get("source_row") or 0),
                str(row.get("record_id") or ""),
            )
        )
        return merged

    def _kind_county_files(self, kind: str) -> Dict[str, Path]:
        root = self.repo_root / "data" / "context"
        files: Dict[str, Path] = {}
        if not root.exists():
            return files
        for path in sorted(root.glob(f"*/{kind}.jsonl")):
            if path.parent.name == "_unmapped":
                continue
            files[path.parent.name] = path
        return files

    def import_file(
        self,
        source_id: str,
        path: Path,
        *,
        official_source_sha256: Optional[str] = None,
        diagnostic_note: str = "",
    ) -> Dict[str, Any]:
        source = next(
            (
                item for item in (self.config.get("sources") or {}).values()
                if item.get("source_id") == source_id
            ),
            None,
        )
        if source is None:
            raise ValueError(f"unknown context source_id: {source_id}")
        path = Path(path)
        rows, encoding = load_rows(path)
        raw_cache_sha = _sha256(path)
        source_sha = str(official_source_sha256 or raw_cache_sha).strip().lower()
        if len(source_sha) != 64 or any(ch not in "0123456789abcdef" for ch in source_sha):
            raise ValueError("official_source_sha256 must be a 64-character hex digest")
        try:
            raw_cache_rel = str(path.resolve().relative_to(self.repo_root.resolve()))
        except ValueError:
            raw_cache_rel = str(path.resolve())
        imported_at = utc_now_iso()
        grouped: Dict[str, List[Dict[str, Any]]] = {county: [] for county in COUNTIES}
        unmapped: List[Dict[str, Any]] = []
        for index, raw in enumerate(rows, start=1):
            counties = _counties_in_row(raw)
            normalized = {
                "record_id": f"{source_id}:{source_sha[:12]}:{index}",
                "source_id": source_id,
                "source_grade": source["source_grade"],
                "dataset_url": source["dataset_url"],
                "source_time_scope": source.get("time_scope") or "",
                "source_update_frequency": source.get("update_frequency") or "",
                "source_sha256": source_sha,
                "raw_cache_sha256": raw_cache_sha,
                "raw_cache_path": raw_cache_rel,
                "source_row": index,
                "imported_at": imported_at,
                "scope_boundary": source.get("scope_boundary") or "",
                "raw": raw,
            }
            if source.get("kind") == "age_structure":
                summary = age_summary(raw)
                if summary:
                    normalized["age_summary"] = summary
            if len(counties) == 1:
                normalized["county"] = counties[0]
                grouped[counties[0]].append(normalized)
            else:
                normalized["county_candidates"] = counties
                normalized["unmapped_reason"] = (
                    "no_county_name_matched" if not counties else "multiple_county_names_matched"
                )
                unmapped.append(normalized)

        out_root = self.repo_root / "data" / "context"
        kind = str(source["kind"])
        files: Dict[str, str] = {}
        retained_by_county: Dict[str, int] = {}
        replaced_source_rows = 0
        for county in COUNTIES:
            target = out_root / safe_component(county) / f"{kind}.jsonl"
            existing = load_jsonl(target) if target.exists() else []
            replaced_source_rows += sum(
                1
                for row in existing
                if str(row.get("source_id") or "").strip() == source_id
            )
            merged = self._merge_source_records(existing, grouped.get(county, []), source_id)
            if merged:
                write_jsonl(target, merged)
                files[county] = str(target.relative_to(self.repo_root))
                retained_by_county[county] = len(merged)
            elif target.exists():
                target.unlink()

        unmapped_target = out_root / "_unmapped" / f"{kind}-{source_id}.jsonl"
        existing_unmapped = load_jsonl(unmapped_target) if unmapped_target.exists() else []
        replaced_source_rows += sum(
            1
            for row in existing_unmapped
            if str(row.get("source_id") or "").strip() == source_id
        )
        merged_unmapped = self._merge_source_records(existing_unmapped, unmapped, source_id)
        if merged_unmapped:
            write_jsonl(unmapped_target, merged_unmapped)
            files["_unmapped"] = str(unmapped_target.relative_to(self.repo_root))
        elif unmapped_target.exists():
            unmapped_target.unlink()

        retained_by_source: Dict[str, int] = {}
        retained_total = 0
        for county, count in retained_by_county.items():
            target = out_root / safe_component(county) / f"{kind}.jsonl"
            for row in load_jsonl(target):
                sid = str(row.get("source_id") or "unknown")
                retained_by_source[sid] = retained_by_source.get(sid, 0) + 1
                retained_total += 1
        for row in load_jsonl(unmapped_target):
            sid = str(row.get("source_id") or "unknown")
            retained_by_source[sid] = retained_by_source.get(sid, 0) + 1
            retained_total += 1

        mapped_row_count = sum(len(value) for value in grouped.values())
        reconciliation = {
            "input_row_count": len(rows),
            "mapped_row_count": mapped_row_count,
            "unmapped_row_count": len(unmapped),
            "replaced_source_row_count": replaced_source_rows,
            "retained_row_count": retained_total,
            "retained_unmapped_row_count": len(merged_unmapped),
            "retained_county_file_count": len(
                [key for key in files if key != "_unmapped"]
            ),
            "retained_by_source": dict(sorted(retained_by_source.items())),
            "retained_by_county": retained_by_county,
            "input_accounted": mapped_row_count + len(unmapped) == len(rows),
        }
        manifest = {
            "source_id": source_id,
            "kind": kind,
            "dataset_url": source["dataset_url"],
            "time_scope": source.get("time_scope") or "",
            "update_frequency": source.get("update_frequency") or "",
            "source_path": str(path.resolve()),
            "source_sha256": source_sha,
            "raw_cache_sha256": raw_cache_sha,
            "raw_cache_path": raw_cache_rel,
            "source_size": path.stat().st_size,
            "encoding": encoding,
            "input_row_count": len(rows),
            "mapped_row_count": mapped_row_count,
            "unmapped_row_count": len(unmapped),
            "replaced_source_row_count": replaced_source_rows,
            "retained_row_count": retained_total,
            "retained_by_source": dict(sorted(retained_by_source.items())),
            "retained_by_county": retained_by_county,
            "county_file_count": len([key for key in files if key != "_unmapped"]),
            "files": files,
            "reconciliation": reconciliation,
            "diagnostic": {
                "field_names": sorted(
                    {
                        str(key)
                        for row in rows[:20]
                        for key in row.keys()
                    }
                )[:80],
                "sample_rows": rows[:3],
                "note": diagnostic_note,
            },
            "imported_at": imported_at,
        }
        manifest_path = self.repo_root / "data" / "manifests" / f"context_{source_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path.relative_to(self.repo_root))
        return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--stage-catalog", action="store_true")
    parser.add_argument("--all-counties", action="store_true")
    parser.add_argument("--counties")
    parser.add_argument("--source-id")
    parser.add_argument("--file", type=Path)
    parser.add_argument(
        "--official-sha256",
        default="",
        help="preserve the original official payload hash when importing a recovered raw cache",
    )
    parser.add_argument("--diagnostic-note", default="")
    args = parser.parse_args(argv)
    catalog = CountyContextCatalog(args.repo_root)
    result: Dict[str, Any]
    if args.stage_catalog:
        counties = list(COUNTIES) if args.all_counties or not args.counties else [
            item.strip() for item in args.counties.split(",") if item.strip()
        ]
        result = catalog.stage(counties)
    elif args.source_id and args.file:
        result = catalog.import_file(
            args.source_id,
            args.file,
            official_source_sha256=args.official_sha256 or None,
            diagnostic_note=args.diagnostic_note,
        )
        if result["mapped_row_count"] == 0:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 3
    else:
        parser.error("use --stage-catalog or provide --source-id and --file")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
