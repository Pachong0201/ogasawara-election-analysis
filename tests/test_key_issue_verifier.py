from runtime.key_issue_verifier import KeyIssueVerifier
from runtime.county_knowledge import county_research_questions

def _question():
    return next(r["question"] for r in county_research_questions("新竹縣") if r["topic"] == "key_issues")

def _research():
    return {"findings":[{"question":_question(),"statement":"新竹縣某項地方交通建設進入公開討論階段。",
        "citations":[
            {"url":"https://a.invalid/x","title":"A","source_grade":"C","publisher_id":"a","page_date":"2026-09-20",
             "quote":"新竹縣某項地方交通建設進入公開討論，相關單位持續說明方案內容。"},
            {"url":"https://b.invalid/x","title":"B","source_grade":"C","publisher_id":"b","page_date":"2026-09-21",
             "quote":"針對新竹縣某項地方交通建設，相關單位表示目前仍處於公開討論階段。"}
        ]}]}

def test_two_independent_sources_pass_after_counter_check(tmp_path):
    result = KeyIssueVerifier(tmp_path).run(
        "新竹縣", _research(),
        contradiction_checked=True,
        contradiction_note="checked updates and corrections",
        apply=False,
    )
    assert result["proposal_count"] == 1
    receipt = result["decisions"][0]
    assert receipt["decision"] == "dry_run_pass"
    assert receipt["independent_source_count"] == 2
    assert receipt["evidence_grades"] == ["C", "C"]

def test_counter_check_is_required(tmp_path):
    result = KeyIssueVerifier(tmp_path).run("新竹縣", _research(), apply=False)
    assert result["decisions"][0]["decision"] == "rejected"
    assert any("contradiction_check_completed" in r for r in result["decisions"][0]["reasons"])

def test_same_publisher_is_not_two_sources(tmp_path):
    payload = _research()
    payload["findings"][0]["citations"][1]["publisher_id"] = "a"
    result = KeyIssueVerifier(tmp_path).build_proposals(
        "新竹縣", payload, contradiction_checked=True
    )
    assert result["proposal_count"] == 0
    assert result["rejected_count"] == 1
