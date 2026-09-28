from runtime.l3_candidate_party_research import L3CandidatePartyResearchBatch


def test_retry_pending_is_a_batch_failure(tmp_path, monkeypatch):
    batch = L3CandidatePartyResearchBatch(tmp_path)
    monkeypatch.setattr(batch, "run_county", lambda *args, **kwargs: {
        "county": "新竹縣",
        "research_status": "retry_pending",
        "last_error": "provider_http_429",
        "promoted_count": 0,
        "unresolved_count": 1,
    })
    result = batch.run_many(["新竹縣"])
    assert result["failure_count"] == 1
    assert result["failures"] == [{
        "county": "新竹縣", "error": "provider_http_429",
    }]


def test_insufficient_evidence_is_a_completed_research_attempt(tmp_path, monkeypatch):
    batch = L3CandidatePartyResearchBatch(tmp_path)
    monkeypatch.setattr(batch, "run_county", lambda *args, **kwargs: {
        "county": "新竹縣",
        "research_status": "insufficient_evidence",
        "last_error": "",
        "promoted_count": 0,
        "unresolved_count": 1,
    })
    result = batch.run_many(["新竹縣"])
    assert result["failure_count"] == 0
