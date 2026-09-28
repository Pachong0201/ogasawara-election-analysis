from pathlib import Path

import yaml

from runtime.county_knowledge import COUNTIES
from runtime.curated_county_baseline import CuratedCountyBaseline


EXISTING_BASELINE = {"新北市", "台南市", "高雄市"}


def test_academic_supplement_covers_remaining_19_counties():
    repo_root = Path(__file__).resolve().parents[1]
    payload = yaml.safe_load(
        (repo_root / "config" / "county_academic_research.yaml").read_text(
            encoding="utf-8"
        )
    )
    rows = payload["academic_records"]
    expected = set(COUNTIES) - EXISTING_BASELINE
    assert len(rows) == 19
    assert {row["county"] for row in rows} == expected
    assert all(row["source_grade"] == "B" for row in rows)
    assert all(row["promote"] is False for row in rows)
    assert all(row["url"].startswith("https://") for row in rows)


def test_default_baseline_stages_supplement_as_leads_without_proposals():
    baseline = CuratedCountyBaseline()
    leads_by_county, proposals = baseline.build_inputs(["基隆市", "嘉義市", "金門縣", "連江縣"])
    academic_ids = {
        row["lead_id"]
        for rows in leads_by_county.values()
        for row in rows
        if str(row.get("lead_id", "")).startswith("academic-")
    }
    assert "academic-keelung-party-faction-elections-2002" in academic_ids
    assert "academic-chiayi-city-resource-support-2023" in academic_ids
    assert "academic-kinmen-political-ecology-2020" in academic_ids
    assert "academic-matsu-political-ecology-2017" in academic_ids

    supplement_ids = {
        row["lead_id"]
        for row in yaml.safe_load(
            (
                Path(__file__).resolve().parents[1]
                / "config"
                / "county_academic_research.yaml"
            ).read_text(encoding="utf-8")
        )["academic_records"]
    }
    proposal_lead_ids = {
        lead_id
        for proposal in proposals
        for lead_id in proposal.get("evidence_lead_ids", [])
    }
    assert supplement_ids.isdisjoint(proposal_lead_ids)
