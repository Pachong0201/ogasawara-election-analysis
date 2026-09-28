from runtime.research_brief import ResearchBriefBuilder


def build_brief():
    assessment = {
        "regional_assessments": [
            {
                "region": "板橋區",
                "historical_structure": [{"metric": "candidate_residuals"}],
                "current_events": [{"event_id": "e1"}],
                "organization_signals": [],
                "issue_signals": [],
                "evidence_gaps": [],
            },
            {
                "region": "印度股市",
                "historical_structure": [],
                "current_events": [{"event_id": "noise"}],
                "organization_signals": [],
                "issue_signals": [],
                "evidence_gaps": [],
            },
        ],
        "organization_networks": [{
            "relation_type": "joint_headquarters",
            "actors": ["候選人甲"],
            "location": "板橋區",
            "confidence": "medium",
            "evidence_count": 2,
            "independent_source_count": 2,
            "evidence_references": ["u1", "u2"],
            "analytical_boundary": "does not establish vote effect",
        }],
        "issue_dynamics": [],
        "third_party_signals": [],
        "follow_up_questions": [{
            "question": "板橋區近30日是否有持續聯合活動？",
            "region": "板橋區",
            "status": "unresolved",
        }],
    }
    return ResearchBriefBuilder().build(
        task={"jurisdiction": "新北市", "election_type": "county_mayor", "target_year": 2026},
        historical_baseline={"historical_matrix": {"rows": []}},
        metrics={
            "candidate_residuals": [{
                "region": "板橋區",
                "value": 0.05,
                "baseline_method": "bracketing_president_average",
                "local_explanation_required": True,
            }],
            "electoral_swing": [],
            "split_ticket": [],
            "spatial_anomalies": [],
        },
        local_knowledge={"county": "新北市", "regions": ["板橋區"]},
        current_candidates=[{"candidate_name": "候選人甲"}],
        current_events=[],
        campaign_event_resolution={"events": [{
            "event_id": "e1",
            "date": "2026-09-20",
            "event_type": "campaign_headquarters",
            "candidate_entities": ["候選人甲"],
            "locations": ["板橋區", "印度股市"],
            "verification_status": "corroborated_media",
            "evidence_excerpt": "候選人在板橋成立聯合競總。",
            "source_urls": ["u1", "u2"],
        }]},
        campaign_state={"as_of": "2026-09-28T00:00:00+08:00", "same_series_poll_changes": []},
        polls=[],
        research_result={"status": "completed", "findings": []},
        assessment=assessment,
        unknowns=["部分地方關係仍缺乏核驗"],
        warnings=[],
        sources=[{"source_id": "fixture", "source_grade": "C", "reference": "u1"}],
    )


def test_research_brief_filters_region_noise():
    brief = build_brief()
    regions = [row["region"] for row in brief["regional_patterns"]]
    assert "板橋區" in regions
    assert "印度股市" not in regions
    assert brief["core_facts"][1]["locations"] == ["板橋區"]


def test_research_brief_is_not_fixed_six_variable_assessment():
    brief = build_brief()
    assert "key_variables" not in brief
    assert brief["key_hypotheses"]
    assert brief["purpose"] == "writer_research_brief"


def test_research_brief_preserves_residual_boundary():
    brief = build_brief()
    row = brief["historical_structure"][0]
    assert "not personal vote" in row["interpretation_boundary"]
    assert "vote transfer" in row["interpretation_boundary"]


def test_research_brief_keeps_writer_freedom():
    brief = build_brief()
    assert "may choose its own structure" in brief["writer_contract"]["rule"]
