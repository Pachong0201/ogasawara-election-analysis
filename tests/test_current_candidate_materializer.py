from runtime.current_candidate_materializer import CurrentCandidateMaterializer
from runtime.election_loader import load_jsonl
from runtime.models import SourceFetchResult


class FakeCandidateAdapter:
    page_url = "https://web.cec.gov.tw/central/article/64709"

    def fetch(self, jurisdiction, election_type, target_year):
        assert jurisdiction == "宜蘭縣"
        assert election_type == "county_mayor"
        assert target_year == 2026
        rows = [
            {
                "candidate_id": "cec-reg-2026-a",
                "name": "甲候選人",
                "candidate_name": "甲候選人",
                "election_type": "county_mayor",
                "election_year": 2026,
                "jurisdiction": jurisdiction,
                "official_jurisdiction": "宜蘭縣",
                "candidate_status": "registered",
                "registration_date": "2026-09-01",
                "recommended_by_party": "中國國民黨",
                "party": "中國國民黨",
                "source": self.page_url,
                "source_id": "cec_current_candidates",
                "source_grade": "A",
                "source_reference": "https://example.test/county.pdf",
                "published_at": "2026-09-07",
                "retrieved_at": "2026-09-26T10:00:00+00:00",
                "last_verified_at": "2026-09-26T10:00:00+00:00",
            },
            {
                "candidate_id": "cec-reg-2026-b",
                "name": "乙候選人",
                "candidate_name": "乙候選人",
                "election_type": "county_mayor",
                "election_year": 2026,
                "jurisdiction": jurisdiction,
                "official_jurisdiction": "宜蘭縣",
                "candidate_status": "registered",
                "registration_date": "2026-09-04",
                "recommended_by_party": "",
                "party": "未由政黨推薦",
                "source": self.page_url,
                "source_id": "cec_current_candidates",
                "source_grade": "A",
                "source_reference": "https://example.test/county.pdf",
                "published_at": "2026-09-07",
                "retrieved_at": "2026-09-26T10:00:00+00:00",
                "last_verified_at": "2026-09-26T10:00:00+00:00",
            },
        ]
        return SourceFetchResult(
            source_id="cec_current_candidates",
            source_grade="A",
            records=rows,
            warnings=[],
            source_version="fixture",
            raw_reference="https://example.test/county.pdf",
        )


def test_materializes_official_candidate_profiles_without_inference(tmp_path):
    materializer = CurrentCandidateMaterializer(
        tmp_path,
        adapter=FakeCandidateAdapter(),
    )
    result = materializer.materialize_county("宜蘭縣", apply=True)

    assert result["status"] == "complete"
    assert result["candidate_count"] == 2
    assert result["promoted_count"] == 2

    path = tmp_path / "knowledge" / "local" / "宜蘭縣" / "candidates.jsonl"
    rows = load_jsonl(path)
    assert {row["name"] for row in rows} == {"甲候選人", "乙候選人"}
    assert {row["candidate_status"] for row in rows} == {"registered"}
    assert all(row["source_grade"] == "A" for row in rows)
    assert all("qualified" not in row for row in rows)
    assert all("electability" not in row for row in rows)
