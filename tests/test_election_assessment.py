from runtime.election_assessment import ElectionAssessmentBuilder


def test_assessment_separates_events_research_and_uncertainty():
    builder = ElectionAssessmentBuilder()
    assessment = builder.build(
        campaign_state={
            "as_of": "2026-09-28T10:00:00+08:00",
            "campaign_state_status": "partial_current_data",
            "same_series_poll_changes": [],
        },
        current_events=[],
        campaign_event_resolution={
            "events": [
                {
                    "event_id": "event-1",
                    "date": "2026-09-27",
                    "event_type": "campaign_org",
                    "verification_status": "corroborated_media",
                    "candidate_entities": ["候選人甲"],
                    "organizations": ["地方後援會"],
                    "locations": ["行政區A"],
                    "evidence_excerpt": "兩家獨立媒體正文均描述同一場後援會成立活動。",
                },
                {
                    "event_id": "event-2",
                    "date": "2026-09-27",
                    "event_type": "controversy",
                    "verification_status": "requires_review",
                    "candidate_entities": ["候選人乙"],
                    "locations": ["行政區B"],
                    "evidence_excerpt": "報導內容存在互相矛盾的說法。",
                },
            ]
        },
        metrics={
            "electoral_swing": [
                {
                    "region": "行政區A",
                    "party": "測試政黨",
                    "local_explanation_required": True,
                    "signal": "observe",
                    "value": 0.1,
                }
            ],
            "split_ticket": [],
            "candidate_residuals": [],
            "spatial_anomalies": [],
        },
        polls=[],
        research_result={
            "status": "completed",
            "search_count": 5,
            "body_count": 8,
            "evidence_pack": [
                {
                    "url": "https://example.test/a",
                    "title": "地方選舉新聞",
                    "source_name": "example.test",
                    "source_kind": "media",
                    "source_grade": "C",
                    "publisher_id": "example",
                    "independence_key": "example",
                    "page_date": "2026-09-27",
                    "content": "候選人近期多次針對地方交通議題提出說明。" * 20,
                }
            ],
            "findings": [
                {
                    "statement": "該媒體報導候選人近期持續談及地方交通議題。",
                    "verification_status": "body_grounded_unverified",
                    "citations": [
                        {
                            "url": "https://example.test/a",
                            "source_name": "example.test",
                            "source_grade": "C",
                            "publisher_id": "example",
                            "independence_key": "example",
                        }
                    ],
                }
            ],
            "unresolved": ["仍缺第二個獨立來源"],
        },
        unknowns=["沒有可比較的新鮮民調"],
        warnings=["1 stale poll record(s) are stale"],
    )

    assert assessment["research_coverage"]["evidence_pack_count"] == 1
    assert assessment["research_coverage"]["finding_count"] == 1
    assert len(assessment["current_dynamics"]) == 3
    assert assessment["organization_signals"][0]["event_id"] == "event-1"
    assert assessment["issue_signals"][0]["event_id"] == "event-2"
    assert assessment["counter_evidence"][0]["event_id"] == "event-2"
    assert assessment["geographic_signals"] == [
        {"location": "行政區A", "observed_event_count": 1},
        {"location": "行政區B", "observed_event_count": 1},
    ]
    assert assessment["historical_signals"][0]["metric"] == "electoral_swing"
    assert any("第二個獨立來源" in item for item in assessment["uncertainties"])


def test_assessment_never_creates_candidate_ranking_fields():
    assessment = ElectionAssessmentBuilder().build(
        campaign_state={},
        current_events=[],
        campaign_event_resolution={},
        metrics={},
        polls=[],
        research_result={},
        unknowns=[],
        warnings=[],
    )
    forbidden = {"winner", "ranking", "win_probability", "score"}
    assert forbidden.isdisjoint(assessment.keys())
