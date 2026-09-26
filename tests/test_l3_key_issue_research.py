from runtime.county_knowledge import county_research_questions
from runtime.l3_key_issue_research import L3KeyIssueResearchBatch


def _question():
    return next(
        row["question"]
        for row in county_research_questions("新竹縣")
        if row["topic"] == "key_issues"
    )


def _primary():
    return {
        "status": "completed",
        "search_count": 3,
        "body_count": 2,
        "findings": [
            {
                "question": _question(),
                "statement": "新竹縣某項地方交通建設仍處於公開討論階段。",
                "citations": [
                    {
                        "url": "https://a.invalid/x",
                        "title": "A",
                        "source_grade": "C",
                        "publisher_id": "a",
                        "page_date": "2026-09-20",
                        "quote": "新竹縣某項地方交通建設仍處於公開討論，相關單位持續說明方案內容。",
                    },
                    {
                        "url": "https://b.invalid/x",
                        "title": "B",
                        "source_grade": "C",
                        "publisher_id": "b",
                        "page_date": "2026-09-21",
                        "quote": "針對新竹縣某項地方交通建設，相關單位表示目前仍處於公開討論階段。",
                    },
                ],
            }
        ],
    }


def test_batch_promotes_only_after_completed_counter_search(tmp_path, monkeypatch):
    batch = L3KeyIssueResearchBatch(tmp_path)
    calls = []

    def fake_research(county, questions):
        calls.append(questions)
        if len(calls) == 1:
            return _primary()
        return {
            "status": "insufficient_evidence",
            "search_count": 2,
            "body_count": 1,
            "findings": [],
        }

    monkeypatch.setattr(batch, "_research_sync", fake_research)
    result = batch.run_county("新竹縣", apply=False)

    assert len(calls) == 2
    assert calls[1][0].startswith("反证核验：")
    assert result["counter_check_completed"] is True
    assert result["proposal_count"] == 1
    assert result["promoted_count"] == 1


def test_failed_counter_search_cannot_pass_promotion_gate(tmp_path, monkeypatch):
    batch = L3KeyIssueResearchBatch(tmp_path)
    calls = 0

    def fake_research(county, questions):
        nonlocal calls
        calls += 1
        return _primary() if calls == 1 else {"status": "failed", "last_error": "fixture"}

    monkeypatch.setattr(batch, "_research_sync", fake_research)
    result = batch.run_county("新竹縣", apply=False)

    assert result["counter_check_completed"] is False
    assert result["proposal_count"] == 1
    assert result["promoted_count"] == 0


def test_batch_caps_findings_per_county(tmp_path, monkeypatch):
    batch = L3KeyIssueResearchBatch(tmp_path)
    payload = _primary()
    payload["findings"] = payload["findings"] * 5
    calls = 0

    def fake_research(county, questions):
        nonlocal calls
        calls += 1
        if calls == 1:
            return payload
        return {"status": "completed", "search_count": 1, "body_count": 1, "findings": []}

    monkeypatch.setattr(batch, "_research_sync", fake_research)
    result = batch.run_county("新竹縣", apply=False, max_findings=2)
    assert result["finding_count"] == 2


def test_grounded_counter_update_blocks_automatic_promotion(tmp_path, monkeypatch):
    batch = L3KeyIssueResearchBatch(tmp_path)
    calls = 0

    def fake_research(county, questions):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _primary()
        return {
            "status": "completed",
            "search_count": 2,
            "body_count": 2,
            "findings": [
                {
                    "question": questions[0],
                    "statement": "该交通建设的执行状态已有更新。",
                    "citations": [],
                }
            ],
        }

    monkeypatch.setattr(batch, "_research_sync", fake_research)
    result = batch.run_county("新竹縣", apply=False)
    assert result["counter_update_finding_count"] == 1
    assert result["counter_check_completed"] is False
    assert result["promoted_count"] == 0
