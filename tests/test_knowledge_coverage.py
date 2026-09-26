import json

from runtime.election_loader import write_jsonl
from runtime.knowledge_coverage import KnowledgeCoverageAudit


def test_coverage_distinguishes_catalogs_from_row_data(tmp_path):
    audit = KnowledgeCoverageAudit(tmp_path)
    before = audit.county("新竹縣")
    context_sources = before["stable_local_baseline"]["official_social_context"]["sources"]
    assert context_sources
    assert all(row["state"] == "source_catalog_only" for row in context_sources)
    assert before["stable_local_baseline"]["academic_research"]["state"] == "research_leads_only"
    assert before["stable_local_baseline"]["academic_research"]["lead_count"] == 1

    context_path = tmp_path / "data" / "context" / "新竹縣" / "age_structure.jsonl"
    write_jsonl(
        context_path,
        [{"record_id": "age-1", "source_id": "dgbas_census_age_structure_2020"}],
    )
    election_path = (
        tmp_path / "data" / "elections" / "president" / "2024" / "新竹縣.jsonl"
    )
    write_jsonl(election_path, [{"record_id": "e-1"}])

    boundary = (
        tmp_path
        / "data"
        / "geography"
        / "electoral_districts"
        / "cec_legislator_term11"
        / "新竹縣.jsonl"
    )
    write_jsonl(boundary, [{"record_id": "b-1"}])
    matrix = tmp_path / "data" / "matrices" / "cec" / "新竹縣.json"
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text(json.dumps({"county": "新竹縣"}), encoding="utf-8")

    relationships = (
        tmp_path / "knowledge" / "local" / "新竹縣" / "relationships.jsonl"
    )
    write_jsonl(
        relationships,
        [
            {
                "relationship_id": "r-1",
                "current_status": "active_verified",
                "verification_evidence": [
                    {"independence_key": "cec"},
                    {"independence_key": "media-a"},
                ],
            }
        ],
    )
    issues = tmp_path / "knowledge" / "local" / "新竹縣" / "issues.jsonl"
    write_jsonl(issues, [{"claim_id": "i-1"}])

    after = audit.county("新竹縣")
    assert after["stable_local_baseline"]["cec_elections"]["state"] == "row_data_available"
    assert after["stable_local_baseline"]["term11_legislative_boundaries"]["state"] == "row_data_available"
    assert after["stable_local_baseline"]["cec_spatial_matrix"]["state"] == "row_data_available"
    sources = after["stable_local_baseline"]["official_social_context"]["sources"]
    age = next(row for row in sources if row["source_id"] == "dgbas_census_age_structure_2020")
    assert age["state"] == "row_data_available"
    assert after["dynamic_local_state"]["active_verified_relationship_count"] == 1
    assert after["dynamic_local_state"]["dual_source_relationship_count"] == 1
    assert after["dynamic_local_state"]["current_issue_count"] == 1


def test_all_county_audit_keeps_19_county_academic_supplement_as_leads(tmp_path):
    report = KnowledgeCoverageAudit(tmp_path).build()
    assert report["county_count"] == 22
    academic = [
        row
        for row in report["counties"]
        if row["stable_local_baseline"]["academic_research"]["state"]
        == "research_leads_only"
    ]
    assert len(academic) == 19
    assert "source_catalog_only" in report["interpretation_boundary"]
