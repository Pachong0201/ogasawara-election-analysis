import json
from pathlib import Path

from runtime.current_relationship_verifier import CurrentRelationshipVerifier
from runtime.election_loader import current_candidates_path, write_jsonl


def _candidate():
    return {
        "candidate_id": "cec-reg-fixture",
        "name": "甲候選人",
        "candidate_name": "甲候選人",
        "election_type": "county_mayor",
        "election_year": 2026,
        "jurisdiction": "新竹縣",
        "candidate_status": "registered",
        "registration_date": "2026-09-02",
        "recommended_by_party": "民主進步黨",
        "party": "民主進步黨",
        "source": "https://web.cec.gov.tw/central/article/64733",
        "source_reference": "https://web.cec.gov.tw/api/file/fixture.pdf",
        "source_grade": "A",
        "published_at": "2026-09-07",
        "last_verified_at": "2026-09-07T00:00:00+08:00",
    }


def _research(quote):
    return {
        "findings": [
            {
                "question": "fixture",
                "statement": "fixture media statement",
                "citations": [
                    {
                        "url": "https://example-media.invalid/story",
                        "title": "fixture",
                        "source_grade": "C",
                        "source_kind": "media",
                        "publisher_id": "fixture_media",
                        "page_date": "2026-09-10",
                        "quote": quote,
                    }
                ],
            }
        ]
    }


def test_dual_source_party_relationship_dry_run_passes(tmp_path):
    path = current_candidates_path(tmp_path, "新竹縣")
    write_jsonl(path, [_candidate()])
    verifier = CurrentRelationshipVerifier(tmp_path)
    result = verifier.run(
        "新竹縣",
        _research("甲候選人完成登記，並由民主進步黨推薦參選縣長。"),
        apply=False,
    )
    assert result["proposal_count"] == 1
    assert result["unresolved_count"] == 0
    assert result["decisions"][0]["decision"] == "dry_run_pass"
    assert result["decisions"][0]["independent_source_count"] == 2
    assert result["decisions"][0]["evidence_grades"] == ["A", "C"]


def test_promoted_candidate_profiles_are_used_when_runtime_cache_is_absent(tmp_path):
    path = tmp_path / "knowledge" / "local" / "新竹縣" / "candidates.jsonl"
    write_jsonl(path, [_candidate()])
    verifier = CurrentRelationshipVerifier(tmp_path)
    result = verifier.run(
        "新竹縣",
        _research("甲候選人完成登記，並由民主進步黨推薦參選縣長。"),
        apply=False,
    )
    assert result["candidate_count"] == 1
    assert result["proposal_count"] == 1
    assert result["unresolved_count"] == 0


def test_media_quote_must_name_candidate_and_party(tmp_path):
    path = current_candidates_path(tmp_path, "新竹縣")
    write_jsonl(path, [_candidate()])
    verifier = CurrentRelationshipVerifier(tmp_path)
    result = verifier.build_proposals(
        "新竹縣",
        _research("甲候選人今天完成登記，但本文沒有寫出推薦政黨。"),
    )
    assert result["proposal_count"] == 0
    assert result["unresolved_count"] == 1


def test_unrecommended_candidates_are_not_turned_into_party_relations(tmp_path):
    row = _candidate()
    row["recommended_by_party"] = ""
    row["party"] = "未由政黨推薦"
    path = current_candidates_path(tmp_path, "新竹縣")
    write_jsonl(path, [row])
    result = CurrentRelationshipVerifier(tmp_path).build_proposals(
        "新竹縣", _research("甲候選人完成登記。")
    )
    assert result["proposal_count"] == 0
    assert result["unresolved_count"] == 0
