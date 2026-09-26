import io
import json

from runtime.context_official_download import OfficialContextDownloader


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def test_download_and_import_allowlisted_csv(tmp_path):
    config = tmp_path / "config" / "county_context_sources.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """
version: 1
sources:
  fixture:
    kind: civil_associations
    topic: civil_associations
    source_id: fixture_groups
    source_name: Fixture
    source_grade: A
    independence_key: fixture
    dataset_url: https://example.test/dataset
    resource_url: https://example.test/groups.csv
    resource_format: csv
    scope_boundary: fixture only
""",
        encoding="utf-8",
    )
    payload = "名稱,地址\n甲會,新竹縣竹北市\n乙會,台南市中西區\n".encode("utf-8-sig")
    downloader = OfficialContextDownloader(
        tmp_path,
        config_path=config,
        opener=lambda request: _Response(payload),
    )
    result = downloader.materialize_one("fixture_groups", force=True)
    assert result["import"]["mapped_row_count"] == 2
    assert result["import"]["county_file_count"] == 2
    assert (tmp_path / "data" / "context" / "新竹縣" / "civil_associations.jsonl").exists()
    manifest_path = tmp_path / result["import"]["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["resource_url"] == "https://example.test/groups.csv"
    assert len(manifest["downloaded_sha256"]) == 64


def test_rejects_non_https_resource(tmp_path):
    config = tmp_path / "config" / "county_context_sources.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """
version: 1
sources:
  bad:
    kind: civil_associations
    topic: civil_associations
    source_id: bad_source
    source_name: Bad
    source_grade: A
    independence_key: bad
    dataset_url: https://example.test
    resource_url: http://example.test/groups.csv
    resource_format: csv
    scope_boundary: fixture only
""",
        encoding="utf-8",
    )
    downloader = OfficialContextDownloader(tmp_path, config_path=config)
    result = downloader.materialize_many(["bad_source"])
    assert result["success_count"] == 0
    assert result["failure_count"] == 1
    assert "HTTPS" in result["failures"][0]["error"]


def test_json_payload_is_not_overwritten_by_download_metadata(tmp_path):
    config = tmp_path / "config" / "county_context_sources.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """
version: 1
sources:
  fixture:
    kind: farmers_fishermen_associations
    topic: farmers_fishermen_associations
    source_id: fixture_json
    source_name: Fixture JSON
    source_grade: A
    independence_key: fixture_json
    dataset_url: https://example.test/dataset
    resource_url: https://example.test/members.json
    resource_format: json
    scope_boundary: fixture only
""",
        encoding="utf-8",
    )
    payload = json.dumps(
        [{"縣市": "宜蘭縣", "會員數": 123}],
        ensure_ascii=False,
    ).encode("utf-8")
    downloader = OfficialContextDownloader(
        tmp_path,
        config_path=config,
        opener=lambda request: _Response(payload),
    )
    result = downloader.materialize_one("fixture_json", force=True)
    assert result["import"]["mapped_row_count"] == 1
    raw = tmp_path / "cache" / "raw" / "context" / "fixture_json" / "latest.json"
    meta = tmp_path / "cache" / "raw" / "context" / "fixture_json" / "latest.meta.json"
    assert json.loads(raw.read_text(encoding="utf-8"))[0]["縣市"] == "宜蘭縣"
    assert json.loads(meta.read_text(encoding="utf-8"))["source_id"] == "fixture_json"
