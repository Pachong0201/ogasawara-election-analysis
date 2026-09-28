from runtime.materialize_official_body_review import build_artifacts


def test_build_artifacts_keeps_scope_dates_sources_and_counter_gate():
    review = {
        "reviewed_at": "2026-09-27",
        "sources": [{
            "county": "宜蘭縣",
            "date": "2026-09-05",
            "source": "宜蘭縣政府環境保護局",
            "url": "https://www.ilepb.gov.tw/PubNews/PubNewsContent.aspx?id=3646",
            "title": "親子攜手淨溪、企業接力護水",
            "quote": "新群水環境巡守隊首度攜手臺灣湯淺電池股份有限公司及宜蘭縣政府",
            "counter_query": "活動標題 澄清 更正 撤回",
            "note": "已核對正文及更新索引，未發現推翻事件的資料。",
            "relations": [{
                "subject": "新群水環境巡守隊",
                "subject_type": "organization",
                "object": "臺灣湯淺電池股份有限公司",
                "object_type": "organization",
                "relationship_type": "other",
                "description": "只確認當日共同淨溪與捐贈互動。",
            }],
            "issues": [{
                "title": "水環境巡守",
                "statement": "官方正文記錄當日公私協力淨溪；後續成效未核實。",
                "claim_type": "policy",
            }],
        }],
    }
    leads, proposals = build_artifacts(review, "20260927")
    assert len(leads) == 1
    assert len(proposals) == 2
    assert leads[0]["source_grade"] == "A"
    assert leads[0]["published_at"] == "2026-09-05"
    relation = proposals[0]
    assert relation["contradiction_check_completed"] is True
    assert relation["target_record"]["time_scope"] == "2026-09-05"
    assert relation["target_record"]["last_verified_at"] == "2026-09-27"
    assert "不得推定持續合作" in relation["scope_boundary"]
    issue = proposals[1]
    assert issue["target_record"]["verification_status"] == "official_record"
    assert issue["evidence_lead_ids"] == ["official-body-20260927-0"]
