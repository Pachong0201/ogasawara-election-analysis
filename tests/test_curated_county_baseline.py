from runtime.curated_county_baseline import CuratedCountyBaseline
from runtime.county_knowledge import COUNTIES
from runtime.election_loader import load_jsonl


def _copy_config(tmp_path):
    source = CuratedCountyBaseline().config_path
    target = tmp_path / "config" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_baseline_has_complete_official_population_and_industry_coverage():
    baseline = CuratedCountyBaseline()
    assert set(baseline.config["population_2025"]) == set(COUNTIES)
    assert set(baseline.config["primary_employment_industry_2021"]) == set(COUNTIES)


def test_dry_run_passes_all_structured_proposals_without_writes(tmp_path):
    baseline = CuratedCountyBaseline(tmp_path, _copy_config(tmp_path))
    leads_by_county, proposals = baseline.build_inputs(COUNTIES)
    for county, leads in leads_by_county.items():
        baseline._stage_leads(county, leads)
    result = baseline.run(COUNTIES, dry_run=True)
    assert result["proposal_count"] == 49
    assert result["decisions"] == {"dry_run_pass": 49}
    assert not (tmp_path / "knowledge").exists()


def test_apply_promotes_facts_keeps_cec_catalog_unresolved_and_is_idempotent(tmp_path):
    baseline = CuratedCountyBaseline(tmp_path, _copy_config(tmp_path))
    first = baseline.run(["高雄市", "台南市", "新北市"])
    assert first["decisions"] == {"promoted": 11}

    high_claims = load_jsonl(tmp_path / "knowledge" / "historical" / "高雄市" / "claims.jsonl")
    assert len(high_claims) == 3
    relations = load_jsonl(tmp_path / "knowledge" / "local" / "高雄市" / "relationships.jsonl")
    assert len(relations) == 1
    assert relations[0]["independent_source_count"] == 2
    assert relations[0]["promotion_provenance"]["contradiction_check_completed"] is True
    assert relations[0]["promotion_provenance"]["contradiction_check_note"]

    unresolved = load_jsonl(tmp_path / "knowledge" / "counties" / "高雄市" / "unresolved_questions.jsonl")
    electoral = [row for row in unresolved if row.get("topic") == "electoral_geography" or "边界" in row.get("query", "")]
    assert electoral

    package_files = sorted((tmp_path / "knowledge" / "counties" / "高雄市").glob("*"))
    before = {path.name: path.read_bytes() for path in package_files if path.is_file()}
    second = baseline.run(["高雄市", "台南市", "新北市"])
    assert second["evaluated_count"] == 0
    assert second["skipped_existing_count"] == 11
    assert len(load_jsonl(tmp_path / "knowledge" / "historical" / "高雄市" / "claims.jsonl")) == 3
    after = {path.name: path.read_bytes() for path in package_files if path.is_file()}
    assert after == before
