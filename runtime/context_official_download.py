"""Download and import allow-listed official county social-context resources.

This module is intentionally narrow: resource URLs must be declared in
config/county_context_sources.yaml, HTTPS-only, and each downloaded artifact is
hashed before CountyContextCatalog parses it. Raw bytes live under cache/raw/
and therefore stay out of Git; normalized row-level data and manifests are
tracked under data/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

from .county_context_catalog import CountyContextCatalog
from .models import utc_now_iso


MAX_RESOURCE_BYTES = 80 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_RETRIES = 3


class OfficialContextDownloadError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict_flag is not None:
        context.verify_flags &= ~strict_flag
    partial_flag = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", None)
    if partial_flag is not None:
        context.verify_flags |= partial_flag
    return context


class OfficialContextDownloader:
    def __init__(
        self,
        repo_root: Path,
        *,
        config_path: Optional[Path] = None,
        opener: Any = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.config_path = (
            Path(config_path)
            if config_path
            else self.repo_root / "config" / "county_context_sources.yaml"
        )
        self.config = yaml.safe_load(
            self.config_path.read_text(encoding="utf-8")
        ) or {}
        self.catalog = CountyContextCatalog(
            self.repo_root, config_path=self.config_path
        )
        self.opener = opener

    def _source(self, source_id: str) -> Dict[str, Any]:
        for source in (self.config.get("sources") or {}).values():
            if str(source.get("source_id") or "") == source_id:
                return dict(source)
        raise OfficialContextDownloadError(
            f"unknown official context source_id: {source_id}"
        )

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise OfficialContextDownloadError(
                "official resource_url must be an absolute HTTPS URL"
            )
        return parsed.geturl()

    def _download(
        self,
        source_id: str,
        source: Dict[str, Any],
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        resource_url = self._validate_url(str(source.get("resource_url") or ""))
        resource_format = str(source.get("resource_format") or "").lower().lstrip(".")
        if resource_format not in {"csv", "json", "xml"}:
            raise OfficialContextDownloadError(
                f"{source_id}: unsupported resource_format={resource_format!r}"
            )
        raw_dir = self.repo_root / "cache" / "raw" / "context" / source_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        latest_path = raw_dir / f"latest.{resource_format}"
        # JSON resources themselves use latest.json, so metadata must never share
        # that path or it will overwrite the just-downloaded official payload.
        metadata_path = raw_dir / "latest.meta.json"

        if latest_path.exists() and latest_path.stat().st_size > 0 and not force:
            return {
                "source_id": source_id,
                "resource_url": resource_url,
                "resource_format": resource_format,
                "path": str(latest_path),
                "sha256": _sha256(latest_path),
                "size": latest_path.stat().st_size,
                "downloaded": False,
            }

        request = urllib.request.Request(
            resource_url,
            headers={
                "User-Agent": "ogasawara-election-analysis/1.4",
                "Accept": "*/*",
            },
        )
        last_error: Optional[Exception] = None
        for attempt in range(1, DEFAULT_RETRIES + 1):
            tmp = latest_path.with_suffix(latest_path.suffix + ".tmp")
            if tmp.exists():
                tmp.unlink()
            try:
                if self.opener is not None:
                    response = self.opener(request)
                else:
                    response = urllib.request.urlopen(
                        request,
                        timeout=int(source.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                        context=_ssl_context(),
                    )
                written = 0
                with response:
                    with tmp.open("wb") as out:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_RESOURCE_BYTES:
                                raise OfficialContextDownloadError(
                                    f"{source_id}: resource exceeded {MAX_RESOURCE_BYTES} bytes"
                                )
                            out.write(chunk)
                if written <= 0:
                    raise OfficialContextDownloadError(
                        f"{source_id}: download returned zero bytes"
                    )
                tmp.replace(latest_path)
                metadata = {
                    "source_id": source_id,
                    "dataset_url": source.get("dataset_url"),
                    "resource_url": resource_url,
                    "resource_format": resource_format,
                    "sha256": _sha256(latest_path),
                    "size": latest_path.stat().st_size,
                    "downloaded_at": utc_now_iso(),
                    "attempt": attempt,
                }
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {
                    **metadata,
                    "path": str(latest_path),
                    "downloaded": True,
                }
            except Exception as exc:
                last_error = exc
                if tmp.exists():
                    tmp.unlink()
                if attempt < DEFAULT_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
        raise OfficialContextDownloadError(
            f"{source_id}: official resource download failed after "
            f"{DEFAULT_RETRIES} attempts: {last_error}"
        )

    def materialize_one(
        self,
        source_id: str,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        source = self._source(source_id)
        download = self._download(source_id, source, force=force)
        imported = self.catalog.import_file(source_id, Path(download["path"]))
        if int(imported.get("mapped_row_count") or 0) <= 0:
            diagnostic = imported.get("diagnostic") or {}
            raise OfficialContextDownloadError(
                f"{source_id}: download parsed but produced zero county-mapped rows; "
                f"fields={diagnostic.get('field_names')}; "
                f"samples={diagnostic.get('sample_rows')}"
            )
        manifest_path = self.repo_root / str(imported["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "resource_url": download["resource_url"],
                "resource_format": download["resource_format"],
                "downloaded_sha256": download["sha256"],
                "downloaded_size": download["size"],
                "downloaded_at": download.get("downloaded_at"),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "source_id": source_id,
            "download": download,
            "import": imported,
        }

    def materialize_many(
        self,
        source_ids: Optional[Iterable[str]] = None,
        *,
        force: bool = False,
        fail_fast: bool = False,
    ) -> Dict[str, Any]:
        selected = list(
            source_ids
            or [
                str(source.get("source_id"))
                for source in (self.config.get("sources") or {}).values()
                if source.get("resource_url")
            ]
        )
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for source_id in selected:
            try:
                results.append(self.materialize_one(source_id, force=force))
            except Exception as exc:
                failures.append(
                    {
                        "source_id": source_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                if fail_fast:
                    raise
        output = {
            "source_count": len(selected),
            "success_count": len(results),
            "failure_count": len(failures),
            "results": results,
            "failures": failures,
            "finished_at": utc_now_iso(),
        }
        manifest_path = (
            self.repo_root / "data" / "manifests" / "context_materialization_latest.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output["manifest_path"] = str(manifest_path.relative_to(self.repo_root))
        return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--source-id",
        action="append",
        default=[],
        help="repeatable source_id; default=all configured resource_url entries",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="return success when at least one official source materializes; failures remain in the manifest",
    )
    args = parser.parse_args(argv)
    result = OfficialContextDownloader(args.repo_root).materialize_many(
        args.source_id or None,
        force=args.force,
        fail_fast=args.fail_fast,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failure_count"] == 0:
        return 0
    if args.allow_partial and result["success_count"] > 0:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
