"""Recover official county-context raw caches from provenance-carrying records.

When ``cache/raw`` is unavailable (for example, a fresh checkout that never ran
the downloader, or a CI workspace whose runner paths are gone) the normalized
records under ``data/context`` still carry the original row payload, the
official payload SHA-256, and the source row index.  This module reconstructs a
row-complete raw cache from those records so the importer can be re-run
reproducibly.

Recovery is explicitly distinct from re-downloading: the reconstructed file is
not byte-identical to the official payload, so the original official hash is
kept as ``source_sha256`` while ``raw_cache_sha256`` records the local cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .county_context_catalog import CountyContextCatalog
from .election_loader import load_jsonl, safe_component
from .models import utc_now_iso


class ContextRecoveryError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_jsonl_text(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _kind_files_from_worktree(repo_root: Path, kind: str, source_id: str) -> List[Path]:
    root = repo_root / "data" / "context"
    files = sorted(root.glob(f"*/{kind}.jsonl"))
    unmapped = root / "_unmapped" / f"{kind}-{source_id}.jsonl"
    if unmapped.exists():
        files.append(unmapped)
    return files


def _kind_files_from_git(repo_root: Path, ref: str, kind: str, source_id: str) -> List[Tuple[str, str]]:
    listing = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", ref, "--", "data/context"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    selected: List[Tuple[str, str]] = []
    for path in listing.splitlines():
        path = path.strip()
        if not path:
            continue
        if f"/{kind}.jsonl" in path and "_unmapped" not in path:
            selected.append((path, "county"))
        elif path.endswith(f"_unmapped/{kind}-{source_id}.jsonl"):
            selected.append((path, "unmapped"))
    content: List[Tuple[str, str]] = []
    for path, scope in selected:
        blob = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        content.append((path, blob))
    return content


def collect_source_records(
    repo_root: Path,
    source_id: str,
    *,
    git_ref: Optional[str] = None,
) -> Dict[str, Any]:
    config = CountyContextCatalog(repo_root).config
    source = next(
        (
            item
            for item in (config.get("sources") or {}).values()
            if item.get("source_id") == source_id
        ),
        None,
    )
    if source is None:
        raise ContextRecoveryError(f"unknown context source_id: {source_id}")
    kind = str(source["kind"])

    if git_ref:
        blobs = _kind_files_from_git(repo_root, git_ref, kind, source_id)
        origin = f"git:{git_ref}"
    else:
        blobs = [
            (str(path), path.read_text(encoding="utf-8"))
            for path in _kind_files_from_worktree(repo_root, kind, source_id)
        ]
        origin = "worktree"

    by_record_id: Dict[str, Dict[str, Any]] = {}
    files_used: List[str] = []
    for path, text in blobs:
        matched = 0
        for record in _parse_jsonl_text(text):
            if str(record.get("source_id") or "") != source_id:
                continue
            record_id = str(record.get("record_id") or "").strip()
            if not record_id:
                raise ContextRecoveryError(f"{path}: record without record_id for {source_id}")
            existing = by_record_id.get(record_id)
            if existing is not None and existing != record:
                raise ContextRecoveryError(
                    f"{path}: conflicting duplicate record_id {record_id}"
                )
            by_record_id[record_id] = record
            matched += 1
        if matched:
            files_used.append(path)

    if not by_record_id:
        raise ContextRecoveryError(f"no records found for {source_id} in {origin}")

    records = sorted(by_record_id.values(), key=lambda row: int(row.get("source_row") or 0))
    expected_rows = max(int(row.get("source_row") or 0) for row in records)
    actual_rows = [int(row.get("source_row") or 0) for row in records]
    if sorted(actual_rows) != list(range(1, expected_rows + 1)):
        missing = sorted(set(range(1, expected_rows + 1)) - set(actual_rows))[:20]
        raise ContextRecoveryError(
            f"{source_id}: recovered rows are not contiguous; missing source_row={missing}"
        )
    sha_values = sorted(
        {str(row.get("source_sha256") or "").strip() for row in records}
    )
    if len(sha_values) != 1 or len(sha_values[0]) != 64:
        raise ContextRecoveryError(
            f"{source_id}: expected exactly one official payload hash, got {sha_values}"
        )
    raw_rows: List[Dict[str, Any]] = []
    for record in records:
        raw = record.get("raw")
        if not isinstance(raw, dict):
            raise ContextRecoveryError(
                f"{source_id}: record {record.get('record_id')} has no raw payload"
            )
        raw_rows.append(dict(raw))
    county_coverage: Dict[str, int] = {}
    for record in records:
        county = str(record.get("county") or "").strip()
        if county:
            county_coverage[county] = county_coverage.get(county, 0) + 1
    return {
        "source_id": source_id,
        "kind": kind,
        "origin": origin,
        "files_used": files_used,
        "file_count": len(files_used),
        "record_count": len(records),
        "expected_row_count": expected_rows,
        "row_order_complete": True,
        "official_source_sha256": sha_values[0],
        "county_coverage": dict(sorted(county_coverage.items())),
        "unmapped_record_count": len(records) - sum(county_coverage.values()),
        "raw_rows": raw_rows,
    }


def write_recovered_raw(
    repo_root: Path,
    source_id: str,
    *,
    git_ref: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    collected = collect_source_records(repo_root, source_id, git_ref=git_ref)
    raw_dir = repo_root / "cache" / "raw" / "context" / source_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload_path = raw_dir / "latest.json"
    payload_path.write_text(
        json.dumps(collected["raw_rows"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    recovered_sha = _sha256(payload_path)
    metadata = {
        "source_id": source_id,
        "kind": collected["kind"],
        "resource_format": "json",
        "sha256": recovered_sha,
        "size": payload_path.stat().st_size,
        "official_source_sha256": collected["official_source_sha256"],
        "origin": collected["origin"],
        "files_used": collected["files_used"],
        "file_count": collected["file_count"],
        "record_count": collected["record_count"],
        "expected_row_count": collected["expected_row_count"],
        "row_order_complete": collected["row_order_complete"],
        "county_coverage": collected["county_coverage"],
        "unmapped_record_count": collected["unmapped_record_count"],
        "recovered_at": utc_now_iso(),
        "recovered": True,
        "note": note
        or "reconstructed from provenance-carrying normalized records; not byte-identical to the official payload",
    }
    (raw_dir / "latest.meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (raw_dir / "latest.recovery.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**metadata, "path": str(payload_path)}


def import_recovered(
    repo_root: Path,
    source_id: str,
    *,
    git_ref: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    recovery = write_recovered_raw(
        repo_root, source_id, git_ref=git_ref, note=note
    )
    catalog = CountyContextCatalog(repo_root)
    imported = catalog.import_file(
        source_id,
        Path(recovery["path"]),
        official_source_sha256=recovery["official_source_sha256"],
        diagnostic_note=note,
    )
    recovery["import"] = imported
    return recovery


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--git-ref",
        default="",
        help="recover from a git revision instead of the current worktree",
    )
    parser.add_argument("--note", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    if args.dry_run:
        collected = collect_source_records(repo_root, args.source_id, git_ref=args.git_ref or None)
        collected.pop("raw_rows", None)
        print(json.dumps(collected, ensure_ascii=False, indent=2))
        return 0
    result = import_recovered(
        repo_root,
        args.source_id,
        git_ref=args.git_ref or None,
        note=args.note,
    )
    result.pop("import", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
