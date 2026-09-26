"""Offline-first importer for official CEC election archives and district CSVs.

The network is deliberately outside this module. Operators can obtain the
official ``votedata.zip`` and/or legislative-district CSV by any trusted means,
then hand the local files to this importer. The importer validates bytes,
records SHA-256 provenance, delegates row parsing to the existing CEC adapter,
and builds versioned geography and historical spatial matrices.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cec_open_data import (
    CECOpenDataAdapter,
    CECOpenDataError,
    JURISDICTION_ALIASES,
    MAX_ARCHIVE_BYTES,
    PATH_FAMILIES,
)
from .county_knowledge import COUNTIES
from .election_loader import ElectionLoader, election_file_path, load_jsonl, safe_component
from .election_normalizer import normalize_records, validate_records
from .matrix_builder import build_cross_level_matrix, build_historical_matrix
from .models import DataQuery, utc_now_iso
from .source_registry import SourceRegistry


DEFAULT_BOUNDARY_SOURCE_URL = (
    "https://data.cec.gov.tw/第11屆立法委員選舉區範圍/立法委員選舉區範圍.csv"
)
DEFAULT_ARCHIVE_SOURCE_URL = "https://data.cec.gov.tw/選舉資料庫/votedata.zip"

# Repository spelling is intentionally stable (台 rather than 臺 where the
# current county package already uses it). The raw official spelling remains
# in every imported row.
_OFFICIAL_TO_CANONICAL: Dict[str, str] = {}
for _alias, _official in JURISDICTION_ALIASES.items():
    if _alias in COUNTIES:
        _OFFICIAL_TO_CANONICAL.setdefault(_official, _alias)
for _county in COUNTIES:
    _OFFICIAL_TO_CANONICAL.setdefault(_county, _county)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized = list(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in materialized)
        + ("\n" if materialized else ""),
        encoding="utf-8",
    )
    tmp.replace(path)


def validate_archive(path: Path) -> Dict[str, Any]:
    """Validate a local CEC ZIP without attempting any network access."""
    path = Path(path)
    if not path.is_file():
        raise CECOpenDataError(f"CEC archive not found: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise CECOpenDataError("CEC archive is empty")
    if size > MAX_ARCHIVE_BYTES:
        raise CECOpenDataError("CEC archive exceeded configured size limit")
    try:
        try:
            zf = zipfile.ZipFile(path, "r", metadata_encoding="cp950")
        except TypeError:
            zf = zipfile.ZipFile(path, "r")
        with zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise CECOpenDataError(f"CEC archive failed ZIP CRC validation: {bad_member}")
            member_count = len(zf.infolist())
    except zipfile.BadZipFile as exc:
        raise CECOpenDataError("CEC source is not a valid ZIP archive") from exc
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size": size,
        "member_count": member_count,
        "source_url": DEFAULT_ARCHIVE_SOURCE_URL,
    }


def preserve_archive_artifact(
    repo_root: Path,
    path: Path,
    *,
    publication_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Keep an immutable local copy and a tracked provenance manifest by SHA256."""
    repo_root = Path(repo_root)
    path = Path(path)
    info = validate_archive(path)
    sha = str(info["sha256"])
    raw_target = repo_root / "cache" / "raw" / "cec" / "imports" / sha / path.name
    raw_target.parent.mkdir(parents=True, exist_ok=True)
    if not raw_target.exists() or _sha256(raw_target) != sha:
        shutil.copy2(path, raw_target)

    manifest = {
        "schema_version": "1.0.0",
        "artifact_type": "cec_votedata_zip",
        "source_id": "cec_open_data",
        "source_grade": "A",
        "source_url": DEFAULT_ARCHIVE_SOURCE_URL,
        "source_filename": path.name,
        "publication_date": publication_date,
        "sha256": sha,
        "size": info["size"],
        "member_count": info["member_count"],
        "raw_archive_path": str(raw_target.relative_to(repo_root)),
        "imported_at": utc_now_iso(),
        "preservation_rule": (
            "raw bytes are copied under cache/raw/cec/imports/<sha256>/; "
            "the tracked manifest preserves filename, URL, hash and publication date"
        ),
    }
    manifest_path = repo_root / "data" / "manifests" / "cec_artifacts" / f"{sha}.json"
    _write_json(manifest_path, manifest)
    return {
        **info,
        "source_filename": path.name,
        "publication_date": publication_date,
        "raw_archive_path": manifest["raw_archive_path"],
        "artifact_manifest": str(manifest_path.relative_to(repo_root)),
    }


def _decode_csv_bytes(content: bytes) -> Tuple[str, str]:
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CEC boundary CSV could not be decoded as UTF-8/CP950/Big5")


def _pick_header(headers: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    normalized = {str(header).strip().replace(" ", ""): header for header in headers}
    for candidate in candidates:
        key = candidate.replace(" ", "")
        if key in normalized:
            return normalized[key]
    for header in headers:
        compact = str(header).strip().replace(" ", "")
        if any(candidate.replace(" ", "") in compact for candidate in candidates):
            return header
    return None


def _county_from_district_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    text = str(name or "").strip()
    match = re.match(
        r"^(.+?[縣市])\s*第?\s*([0-9一二三四五六七八九十]+)\s*(?:選舉區)?",
        text,
    )
    if not match:
        match = re.match(r"^(.+?[縣市])", text)
    if not match:
        return None, None
    official = match.group(1)
    canonical = _OFFICIAL_TO_CANONICAL.get(official)
    if canonical is None and official in COUNTIES:
        canonical = official
    return canonical, official


def parse_legislative_boundary_csv(path: Path) -> Dict[str, Any]:
    """Parse the official district CSV while preserving every source column."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    text, encoding = _decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("CEC boundary CSV has no header row")

    district_header = _pick_header(headers, ("選舉區", "選舉區名稱", "選區"))
    scope_header = _pick_header(headers, ("選舉區範圍", "選區範圍", "範圍"))
    if not district_header or not scope_header:
        raise ValueError(
            "CEC boundary CSV must contain district and scope columns; "
            f"headers={headers}"
        )

    sha = hashlib.sha256(raw).hexdigest()
    version = f"cec-legislator-term11-{sha[:12]}"
    imported_at = utc_now_iso()
    rows: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []
    for line_number, source_row in enumerate(reader, start=2):
        raw_row = {
            str(k or "").strip(): str(v or "").strip()
            for k, v in source_row.items()
        }
        district = raw_row.get(district_header, "").strip()
        scope = raw_row.get(scope_header, "").strip()
        if not district and not scope:
            continue
        county, official_county = _county_from_district_name(district)
        item = {
            "record_id": f"{version}:{line_number}",
            "term": 11,
            "district": district,
            "scope": scope,
            "county": county,
            "official_county": official_county,
            "boundary_version": version,
            "source_id": "cec_legislative_boundaries",
            "source_grade": "A",
            "source_url": DEFAULT_BOUNDARY_SOURCE_URL,
            "source_sha256": sha,
            "source_line": line_number,
            "imported_at": imported_at,
            "raw": raw_row,
        }
        rows.append(item)
        if county not in COUNTIES:
            unmapped.append(item)

    if not rows:
        raise ValueError("CEC boundary CSV contains no data rows")
    return {
        "source_path": str(path.resolve()),
        "source_sha256": sha,
        "source_size": len(raw),
        "encoding": encoding,
        "headers": headers,
        "district_header": district_header,
        "scope_header": scope_header,
        "boundary_version": version,
        "rows": rows,
        "unmapped": unmapped,
    }


def import_legislative_boundaries(repo_root: Path, path: Path) -> Dict[str, Any]:
    parsed = parse_legislative_boundary_csv(path)
    base = (
        Path(repo_root)
        / "data"
        / "geography"
        / "electoral_districts"
        / "cec_legislator_term11"
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {county: [] for county in COUNTIES}
    for row in parsed["rows"]:
        county = row.get("county")
        if county in grouped:
            grouped[county].append(row)

    written: Dict[str, str] = {}
    for county, rows in grouped.items():
        if not rows:
            continue
        target = base / f"{safe_component(county)}.jsonl"
        _write_jsonl(target, rows)
        written[county] = str(target.relative_to(repo_root))
    if parsed["unmapped"]:
        target = base / "_unmapped.jsonl"
        _write_jsonl(target, parsed["unmapped"])
        written["_unmapped"] = str(target.relative_to(repo_root))

    manifest = {
        key: value
        for key, value in parsed.items()
        if key not in {"rows", "unmapped"}
    }
    manifest.update(
        {
            "source_url": DEFAULT_BOUNDARY_SOURCE_URL,
            "row_count": len(parsed["rows"]),
            "unmapped_count": len(parsed["unmapped"]),
            "county_file_count": len([key for key in written if key != "_unmapped"]),
            "files": written,
            "imported_at": utc_now_iso(),
        }
    )
    manifest_path = base / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path.relative_to(repo_root))
    return manifest


def _default_specs() -> List[Tuple[str, int]]:
    return sorted(PATH_FAMILIES.keys(), key=lambda item: (item[1], item[0]))


def import_election_archive(
    repo_root: Path,
    archive_path: Path,
    counties: Sequence[str],
    election_specs: Optional[Sequence[Tuple[str, int]]] = None,
    *,
    publication_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Import supported slices from the supplied ZIP, always reparsing its bytes.

    This deliberately bypasses ElectionLoader's local-first shortcut. Supplying
    a new official ZIP means its bytes must be parsed even when older JSONL
    already exists locally.
    """
    repo_root = Path(repo_root)
    archive_info = preserve_archive_artifact(
        repo_root, archive_path, publication_date=publication_date
    )
    adapter = CECOpenDataAdapter(
        cache_dir=repo_root / "cache" / "raw" / "cec",
        archive_path=Path(archive_path),
    )
    loader = ElectionLoader(
        repo_root=repo_root,
        source_registry=SourceRegistry(adapters=[adapter]),
        mode="online",
    )
    specs = list(election_specs or _default_specs())
    results: List[Dict[str, Any]] = []
    for county in counties:
        if county not in COUNTIES:
            raise ValueError(f"unsupported county: {county}")
        for election_type, year in specs:
            query = DataQuery(
                election_type=election_type,
                year=int(year),
                jurisdiction=county,
                level="township_district",
            )
            try:
                fetched = adapter.fetch(query)
            except Exception as exc:
                results.append(
                    {
                        "county": county,
                        "election_type": election_type,
                        "year": int(year),
                        "status": "invalid",
                        "record_count": 0,
                        "source": "cec_open_data",
                        "persisted": False,
                        "warnings": [],
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    }
                )
                continue

            raw_records = list(fetched.records or [])
            warnings = list(fetched.warnings or [])
            if not raw_records:
                results.append(
                    {
                        "county": county,
                        "election_type": election_type,
                        "year": int(year),
                        "status": "missing",
                        "record_count": 0,
                        "source": fetched.source_id or "cec_open_data",
                        "persisted": False,
                        "warnings": warnings,
                        "errors": [],
                    }
                )
                continue

            defaults = {
                "source_id": adapter.source_id,
                "source": adapter.source_id,
                "source_grade": adapter.source_grade,
                "source_version": fetched.source_version or "",
                "raw_reference": fetched.raw_reference or "",
                "normalization_version": "v1.4.0",
            }
            normalized = normalize_records(raw_records, defaults=defaults)
            normalized = [
                record
                for record in loader._filter_level(normalized, query.level)
                if loader._matches_query(record, query)
            ]
            # These records come from the A-grade official CEC archive itself.
            # Existing local geography may be incomplete, so validate against the
            # union of the registry and the official regions observed in this slice.
            # This preserves unknown-region checking without making an incomplete
            # pre-existing registry block authoritative geography ingestion.
            known_regions = loader._known_regions()
            if known_regions is not None:
                known_regions = set(known_regions)
                for record in normalized:
                    for field in ("jurisdiction", "parent_jurisdiction"):
                        value = str(record.get(field) or "").strip()
                        if value:
                            known_regions.add(value)
            validation = validate_records(
                normalized,
                known_regions=known_regions,
                require_provenance=False,
            )
            errors = [
                issue.message
                for issue in validation.issues
                if issue.severity == "error"
            ]
            if errors or not normalized:
                results.append(
                    {
                        "county": county,
                        "election_type": election_type,
                        "year": int(year),
                        "status": "invalid" if errors else "missing",
                        "record_count": len(normalized),
                        "source": fetched.source_id or "cec_open_data",
                        "persisted": False,
                        "warnings": warnings,
                        "errors": errors,
                    }
                )
                continue

            loader._persist(query, normalized)
            loader._sync_geography_from_records(query, normalized)
            results.append(
                {
                    "county": county,
                    "election_type": election_type,
                    "year": int(year),
                    "status": "filled",
                    "record_count": len(normalized),
                    "source": fetched.source_id or "cec_open_data",
                    "persisted": True,
                    "warnings": warnings,
                    "errors": [],
                }
            )

    return {
        "archive": archive_info,
        "results": results,
        "filled_or_complete": sum(
            1 for row in results if row["status"] in {"filled", "complete"}
        ),
        "missing_or_invalid": sum(
            1 for row in results if row["status"] not in {"filled", "complete"}
        ),
    }


def build_county_matrices(
    repo_root: Path,
    counties: Sequence[str],
    election_specs: Optional[Sequence[Tuple[str, int]]] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    specs = list(election_specs or _default_specs())
    output_dir = repo_root / "data" / "matrices" / "cec"
    summary: Dict[str, Any] = {}
    for county in counties:
        records_by_type: Dict[str, List[Dict[str, Any]]] = {}
        file_refs: List[str] = []
        for election_type, year in specs:
            path = election_file_path(repo_root, election_type, int(year), county)
            if not path.exists():
                continue
            rows = [
                row
                for row in load_jsonl(path)
                if row.get("level") == "township_district"
                and county
                in {row.get("parent_jurisdiction"), row.get("jurisdiction")}
            ]
            if not rows:
                continue
            records_by_type.setdefault(election_type, []).extend(rows)
            file_refs.append(str(path.relative_to(repo_root)))

        target = output_dir / f"{safe_component(county)}.json"
        if not file_refs:
            # An empty JSON shell is not a historical spatial matrix. Remove stale
            # placeholders so coverage auditing reflects actual row-level inputs.
            if target.exists():
                target.unlink()
            summary[county] = {
                "file": None,
                "input_file_count": 0,
                "election_types": [],
                "region_count": 0,
                "state": "no_input_rows",
            }
            continue

        payload = {
            "schema_version": "1.0.0",
            "county": county,
            "generated_at": utc_now_iso(),
            "source_id": "cec_open_data",
            "source_grade": "A",
            "input_files": sorted(file_refs),
            "cross_level": build_cross_level_matrix(
                records_by_type, level="township_district"
            ),
            "historical_by_type": {
                election_type: build_historical_matrix(rows)
                for election_type, rows in sorted(records_by_type.items())
            },
        }
        _write_json(target, payload)
        summary[county] = {
            "file": str(target.relative_to(repo_root)),
            "input_file_count": len(file_refs),
            "election_types": sorted(records_by_type),
            "region_count": len(payload["cross_level"]["regions"]),
            "state": "row_data_available",
        }
    return summary


def run_import(
    repo_root: Path,
    *,
    archive_path: Optional[Path] = None,
    boundary_csv: Optional[Path] = None,
    counties: Optional[Sequence[str]] = None,
    election_specs: Optional[Sequence[Tuple[str, int]]] = None,
    archive_publication_date: Optional[str] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    county_list = list(dict.fromkeys(counties or COUNTIES))
    if not archive_path and not boundary_csv:
        raise ValueError("at least one of archive_path or boundary_csv is required")

    report: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "started_at": utc_now_iso(),
        "network_used": False,
        "counties": county_list,
    }
    if boundary_csv:
        report["boundaries"] = import_legislative_boundaries(
            repo_root, Path(boundary_csv)
        )
    if archive_path:
        report["archive_import"] = import_election_archive(
            repo_root,
            Path(archive_path),
            county_list,
            election_specs=election_specs,
            publication_date=archive_publication_date,
        )
        report["matrices"] = build_county_matrices(
            repo_root,
            county_list,
            election_specs=election_specs,
        )
    report["finished_at"] = utc_now_iso()
    report_path = (
        repo_root / "data" / "manifests" / "cec_offline_import_latest.json"
    )
    _write_json(report_path, report)
    report["report_path"] = str(report_path.relative_to(repo_root))
    return report


def _parse_counties(value: Optional[str]) -> List[str]:
    if not value:
        return list(COUNTIES)
    result = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in result if item not in COUNTIES]
    if unknown:
        raise ValueError(f"unsupported counties: {', '.join(unknown)}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline import of official CEC votedata.zip and "
            "legislative-district CSV"
        )
    )
    parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )
    parser.add_argument("--archive", help="local official votedata.zip")
    parser.add_argument(
        "--archive-publication-date",
        help="optional official publication/update date (YYYY-MM-DD) stored in provenance",
    )
    parser.add_argument(
        "--boundaries", help="local official 第11屆立法委員選舉區範圍 CSV"
    )
    parser.add_argument(
        "--counties",
        help="comma-separated repository county names; default=all 22",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any requested archive slice is missing or invalid",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        counties = _parse_counties(args.counties)
        report = run_import(
            Path(args.repo_root),
            archive_path=Path(args.archive) if args.archive else None,
            boundary_csv=Path(args.boundaries) if args.boundaries else None,
            counties=counties,
            archive_publication_date=args.archive_publication_date,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    missing = int(
        (report.get("archive_import") or {}).get("missing_or_invalid") or 0
    )
    if args.strict and missing:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
