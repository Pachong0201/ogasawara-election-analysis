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


def _write_json(path: Path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _source_ids(path: Path):
    return sorted(
        json.loads(line)["source_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def test_agri_and_fisher_sources_coexist_in_county_file(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    farmers = tmp_path / "farmers.json"
    fishers = tmp_path / "fishers.json"
    _write_json(
        farmers,
        [
            {"縣市別": "臺北市", "會員類別": "正會員", "資料年": "2024", "人數": "100"},
            {"縣市別": "新竹縣", "會員類別": "正會員", "資料年": "2024", "人數": "200"},
        ],
    )
    _write_json(
        fishers,
        [
            {"縣市別": "臺北市", "會員性質": "甲類", "資料年": "2025", "人數": "10"},
            {"縣市別": "新竹縣", "會員性質": "甲類", "資料年": "2025", "人數": "20"},
        ],
    )
    first = catalog.import_file("moa_farmers_association_members", farmers)
    second = catalog.import_file("moa_fishermen_association_members", fishers)

    taipei = tmp_path / first["files"]["台北市"]
    rows = [json.loads(line) for line in taipei.read_text(encoding="utf-8").splitlines()]
    assert sorted(row["source_id"] for row in rows) == [
        "moa_farmers_association_members",
        "moa_fishermen_association_members",
    ]
    assert second["retained_row_count"] == 4
    assert second["retained_by_source"] == {
        "moa_farmers_association_members": 2,
        "moa_fishermen_association_members": 2,
    }
    # no cross-county leakage
    hsinchu = tmp_path / first["files"]["新竹縣"]
    assert len(hsinchu.read_text(encoding="utf-8").splitlines()) == 2


def test_fisher_then_farmer_order_is_symmetric(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    farmers = tmp_path / "farmers.json"
    fishers = tmp_path / "fishers.json"
    _write_json(farmers, [{"縣市別": "嘉義市", "會員類別": "正會員", "人數": "7"}])
    _write_json(fishers, [{"縣市別": "嘉義市", "會員性質": "乙類", "人數": "3"}])
    catalog.import_file("moa_fishermen_association_members", fishers)
    result = catalog.import_file("moa_farmers_association_members", farmers)
    target = tmp_path / result["files"]["嘉義市"]
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert {row["source_id"] for row in rows} == {
        "moa_farmers_association_members",
        "moa_fishermen_association_members",
    }
    assert result["reconciliation"]["retained_row_count"] == 2
    assert result["reconciliation"]["input_accounted"] is True


def test_same_source_reimport_is_idempotent(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    farmers = tmp_path / "farmers.json"
    _write_json(farmers, [{"縣市別": "臺中市", "會員類別": "正會員", "人數": "55"}])
    first = catalog.import_file("moa_farmers_association_members", farmers)
    target = tmp_path / first["files"]["台中市"]
    before = target.read_text(encoding="utf-8")
    second = catalog.import_file("moa_farmers_association_members", farmers)
    after = target.read_text(encoding="utf-8")
    assert before == after
    assert second["retained_row_count"] == 1
    assert second["replaced_source_row_count"] == 1
    assert second["reconciliation"]["retained_by_source"] == {
        "moa_farmers_association_members": 1
    }


def test_source_update_removes_stale_rows_but_keeps_other_source(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    farmers_v1 = tmp_path / "farmers-v1.json"
    farmers_v2 = tmp_path / "farmers-v2.json"
    fishers = tmp_path / "fishers.json"
    _write_json(
        farmers_v1,
        [
            {"縣市別": "高雄市", "會員類別": "正會員", "人數": "100"},
            {"縣市別": "高雄市", "會員類別": "贊助會員", "人數": "50"},
        ],
    )
    _write_json(
        farmers_v2,
        [{"縣市別": "高雄市", "會員類別": "正會員", "人數": "120"}],
    )
    _write_json(fishers, [{"縣市別": "高雄市", "會員性質": "甲類", "人數": "9"}])
    catalog.import_file("moa_farmers_association_members", farmers_v1)
    catalog.import_file("moa_fishermen_association_members", fishers)
    result = catalog.import_file("moa_farmers_association_members", farmers_v2)
    target = tmp_path / result["files"]["高雄市"]
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    farmer_rows = [row for row in rows if row["source_id"] == "moa_farmers_association_members"]
    assert len(farmer_rows) == 1
    assert farmer_rows[0]["raw"]["人數"] == "120"
    assert result["replaced_source_row_count"] == 2  # both v1 farmer rows replaced
    assert result["retained_by_source"]["moa_fishermen_association_members"] == 1


def test_unmapped_rows_are_reconciled_and_replaced_per_source(tmp_path):
    catalog = CountyContextCatalog(tmp_path, _config(tmp_path))
    farmers = tmp_path / "farmers.json"
    _write_json(
        farmers,
        [
            {"縣市別": "臺北市", "會員類別": "正會員", "人數": "100"},
            {"縣市別": "不存在的地方", "會員類別": "正會員", "人數": "1"},
        ],
    )
    result = catalog.import_file("moa_farmers_association_members", farmers)
    assert result["input_row_count"] == 2
    assert result["mapped_row_count"] == 1
    assert result["unmapped_row_count"] == 1
    assert result["reconciliation"]["input_accounted"] is True
    unmapped = tmp_path / result["files"]["_unmapped"]
    assert len(unmapped.read_text(encoding="utf-8").splitlines()) == 1

    # Re-import without the invalid row: the unmapped file must not retain stale rows.
    _write_json(farmers, [{"縣市別": "臺北市", "會員類別": "正會員", "人數": "100"}])
    second = catalog.import_file("moa_farmers_association_members", farmers)
    assert second["unmapped_row_count"] == 0
    assert "_unmapped" not in second["files"]
    assert not unmapped.exists()
