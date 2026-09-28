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


def _fixture_config(tmp_path, extra=""):
    config = tmp_path / "config" / "county_context_sources.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"""
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
    timeout_seconds: 1
    max_retries: 1
    scope_boundary: fixture only
{extra}
""",
        encoding="utf-8",
    )
    return config


def test_failed_download_reuses_verified_cache_with_diagnostics(tmp_path):
    config = _fixture_config(tmp_path)
    payload = "名稱,地址\n甲會,新竹縣竹北市\n".encode("utf-8-sig")
    first = OfficialContextDownloader(
        tmp_path, config_path=config, opener=lambda request: _Response(payload)
    ).materialize_one("fixture_groups", force=True)
    assert first["download"]["downloaded"] is True

    def failing_opener(request):
        raise TimeoutError("temple endpoint timed out")

    second = OfficialContextDownloader(
        tmp_path, config_path=config, opener=failing_opener
    ).materialize_one("fixture_groups", force=True)
    assert second["download"]["fallback"] is True
    manifest = json.loads(
        (tmp_path / second["import"]["manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["download_fallback"] is True
    assert "TimeoutError" in manifest["download_error"]
    assert manifest["downloaded_sha256"] == first["download"]["sha256"]


def test_ssl_context_keeps_verification_with_pinned_intermediate():
    import ssl

    pytest = __import__("pytest")
    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import padding

    from runtime.context_official_download import _ssl_context

    context = _ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True

    cert_path = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "config"
        / "certs"
        / "twca_secure_ssl_ca.pem"
    )
    intermediate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert intermediate.subject.rfc4514_string() == (
        "CN=TWCA Secure SSL Certification Authority,O=TAIWAN-CA,C=TW"
    )
    assert intermediate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value.ca

    import certifi

    roots = []
    blob = certifi.where()
    data = __import__("pathlib").Path(blob).read_bytes()
    for chunk in data.split(b"-----END CERTIFICATE-----"):
        if b"-----BEGIN CERTIFICATE-----" in chunk:
            payload = chunk.split(b"-----BEGIN CERTIFICATE-----", 1)[1]
            try:
                roots.append(
                    x509.load_pem_x509_certificate(
                        b"-----BEGIN CERTIFICATE-----"
                        + payload
                        + b"-----END CERTIFICATE-----"
                    )
                )
            except Exception:
                continue
    trusted = [root for root in roots if root.subject == intermediate.issuer]
    assert trusted, "pinned intermediate must chain to a certifi root"
    trusted[0].public_key().verify(
        intermediate.signature,
        intermediate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        intermediate.signature_hash_algorithm,
    )
