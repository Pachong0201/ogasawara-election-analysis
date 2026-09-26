import json
from pathlib import Path

from runtime.county_context_catalog import CountyContextCatalog, age_summary
from runtime.county_knowledge import COUNTIES


def _config(tmp_path):
    source = Path(__file__).resolve().parents[1] / "config" / "county_context_sources.yaml"
    target = tmp_path / "config" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_catalog_stages_five_official_source_leads_per_county(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    result = catalog.stage(COUNTIES)
    assert result["lead_count"] == len(COUNTIES) * 5
    path = tmp_path / "cache" / "retrieval" / "台北市.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    context_rows = [row for row in rows if row["lead_id"].startswith("context-catalog-")]
    assert len(context_rows) == 5
    assert {row["verification_status"] for row in context_rows} == {"verified_source_catalog"}
    assert {row["source_grade"] for row in context_rows} == {"A"}


def test_import_csv_preserves_raw_rows_and_county_mapping(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    path = tmp_path / "fisher.csv"
    path.write_text(
        "縣市別,會員類別,資料年,會員性質,人數\n"
        "臺北市,甲,2025,正會員,100\n"
        "新竹縣,乙,2025,正會員,200\n",
        encoding="utf-8-sig",
    )
    result = catalog.import_file("moa_fishermen_association_members", path)
    assert result["mapped_row_count"] == 2
    assert result["unmapped_row_count"] == 0
    taipei = tmp_path / result["files"]["台北市"]
    record = json.loads(taipei.read_text(encoding="utf-8").splitlines()[0])
    assert record["raw"]["人數"] == "100"
    assert record["source_grade"] == "A"
    assert len(record["source_sha256"]) == 64


def test_age_summary_computes_shares_without_political_inference():
    row = {
        "總計_人_Grand_total_person": "1000",
        "未滿15歲_合計_人_Under_15_years_total_person": "120",
        "年齡15_64歲_合計_人_15-64_years_total_person": "650",
        "年齡65歲以上_合計_人_65_years_and_over_total_person": "230",
    }
    summary = age_summary(row)
    assert summary == {
        "total": 1000,
        "under_15": 120,
        "age_15_64": 650,
        "age_65_plus": 230,
        "under_15_share": 0.12,
        "age_15_64_share": 0.65,
        "age_65_plus_share": 0.23,
    }
