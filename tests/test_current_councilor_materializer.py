from runtime.current_councilor_materializer import CurrentCouncilorMaterializer
from runtime.election_loader import load_jsonl
from runtime.models import SourceFetchResult


class FakeCouncilorAdapter:
    page_url = "https://web.cec.gov.tw/central/article/64709"

    def fetch(self, jurisdiction, election_type, target_year):
        assert jurisdiction == "宜蘭縣"
        assert election_type == "councilor"
        assert target_year == 2026
        rows = [
            {
                "candidate_id": "cec-reg-2026-council-a",
                "name": "甲議員候選人",
                "candidate_name": "甲議員候選人",
                "election_type": "councilor",
                "election_year": 2026,
                "jurisdiction": jurisdiction,
                "official_jurisdiction": "宜蘭縣",
                "electoral_district": "宜蘭縣第1選舉區",
                "candidate_status": "registered",
                "registration_date": "2026-09-01",
                "recommended_by_party": "中國國民黨",
                "party": "中國國民黨",
                "source_id": "cec_current_candidates",
                "source_grade": "A",
                "source_reference": "https://example.test/councilor.pdf",
                "published_at": "2026-09-07",
                "last_verified_at": "2026-09-26T10:00:00+00:00",
            },
            {
                "candidate_id": "cec-reg-2026-council-b",
                "name": "乙議員候選人",
                "candidate_name": "乙議員候選人",
                "election_type": "councilor",
                "election_year": 2026,
                "jurisdiction": jurisdiction,
                "official_jurisdiction": "宜蘭縣",
                "electoral_district": "宜蘭縣第2選舉區",
                "candidate_status": "registered",
                "registration_date": "2026-09-03",
                "recommended_by_party": "",
                "party": "未由政黨推薦",
                "source_id": "cec_current_candidates",
                "source_grade": "A",
                "source_reference": "https://example.test/councilor.pdf",
                "published_at": "2026-09-07",
                "last_verified_at": "2026-09-26T10:00:00+00:00",
            },
        ]
        return SourceFetchResult(
            source_id="cec_current_candidates",
            source_grade="A",
            records=rows,
            warnings=[],
            source_version="fixture",
            raw_reference="https://example.test/councilor.pdf",
        )


def test_materializes_official_councilor_profiles(tmp_path):
    materializer = CurrentCouncilorMaterializer(
        tmp_path,
        adapter=FakeCouncilorAdapter(),
    )
    result = materializer.materialize_county("宜蘭縣", apply=True)

    assert result["status"] == "complete"
    assert result["candidate_count"] == 2
    assert result["promoted_count"] == 2

    path = tmp_path / "knowledge" / "local" / "宜蘭縣" / "candidates.jsonl"
    rows = load_jsonl(path)
    assert {row["name"] for row in rows} == {"甲議員候選人", "乙議員候選人"}
    assert {row["election_type"] for row in rows} == {"councilor"}
    assert {row["electoral_district"] for row in rows} == {
        "宜蘭縣第1選舉區",
        "宜蘭縣第2選舉區",
    }
    assert all(row["source_grade"] == "A" for row in rows)
    assert all(row["candidate_status"] == "registered" for row in rows)
