import json
import zipfile

import pytest

from runtime.cec_offline_import import (
    build_county_matrices,
    import_legislative_boundaries,
    parse_legislative_boundary_csv,
    validate_archive,
)
from runtime.election_loader import write_jsonl
from runtime.cec_open_data import CECOpenDataError
from tests.fixtures.helpers import election_records_for_year


def test_parse_and_import_legislative_boundary_csv(tmp_path):
    source = tmp_path / "boundaries.csv"
    source.write_text(
        "選舉區,選舉區範圍,備註\n"
        "臺北市第1選舉區,北投區及士林區部分里,fixture\n"
        "新北市第1選舉區,石門區、三芝區、淡水區及八里區,fixture\n",
        encoding="utf-8-sig",
    )

    parsed = parse_legislative_boundary_csv(source)
    assert parsed["boundary_version"].startswith("cec-legislator-term11-")
    assert parsed["unmapped"] == []
    assert {row["county"] for row in parsed["rows"]} == {"台北市", "新北市"}
    assert parsed["rows"][0]["raw"]["備註"] == "fixture"

    manifest = import_legislative_boundaries(tmp_path, source)
    assert manifest["row_count"] == 2
    assert manifest["county_file_count"] == 2
    taipei = tmp_path / manifest["files"]["台北市"]
    new_taipei = tmp_path / manifest["files"]["新北市"]
    assert taipei.exists() and new_taipei.exists()
    assert (
        json.loads(taipei.read_text(encoding="utf-8").splitlines()[0])["source_grade"]
        == "A"
    )


def test_boundary_csv_fails_closed_on_unknown_schema(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("foo,bar\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="district and scope"):
        parse_legislative_boundary_csv(source)


def test_validate_archive_checks_zip_and_sha(tmp_path):
    archive = tmp_path / "votedata.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("votedata/readme.txt", "fixture")
    info = validate_archive(archive)
    assert info["size"] > 0
    assert len(info["sha256"]) == 64
    assert info["member_count"] == 1

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not-a-zip")
    with pytest.raises(CECOpenDataError, match="valid ZIP"):
        validate_archive(broken)


def test_build_county_matrices_from_existing_local_records(tmp_path):
    county = "新竹縣"
    specs = [
        ("county_mayor", 2018),
        ("county_mayor", 2022),
        ("president", 2024),
    ]
    for election_type, year in specs:
        path = (
            tmp_path
            / "data"
            / "elections"
            / election_type
            / str(year)
            / f"{county}.jsonl"
        )
        write_jsonl(
            path,
            election_records_for_year(election_type, year, county),
        )

    summary = build_county_matrices(
        tmp_path,
        [county],
        election_specs=specs,
    )
    target = tmp_path / summary[county]["file"]
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert summary[county]["input_file_count"] == 3
    assert summary[county]["region_count"] == 2
    assert set(payload["historical_by_type"]) == {"county_mayor", "president"}
    assert set(payload["cross_level"]["regions"]) == {"甲鄉", "乙鄉"}
