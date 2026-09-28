"""Golden Case quality gates; fixtures are synthetic, never report templates."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bot.models import ElectionFocus, ParsedRequest
from bot.report_writer import SYSTEM_INSTRUCTIONS, DeterministicReportWriter, _analysis_payload
from bot.router import FULL_ANALYSIS
from runtime.election_assessment import ElectionAssessmentBuilder
from runtime.pipeline import AnalysisPipeline
from tests.fixtures.helpers import make_task, populate_full_repo, temp_repo


FIXTURE = Path(__file__).parent / "fixtures" / "new_taipei_golden_case"


def source():
    return json.loads((FIXTURE / "current_evidence.json").read_text(encoding="utf-8"))


def build(research=None, draft=None):
    data = source()
    metrics = {
        "candidate_residuals": [{"region": "甲區", "value": 0.05, "baseline_method": "bracketing_president_average", "local_explanation_required": True}],
        "split_ticket": [{"region": "乙區", "value": -0.02, "baseline_method": "same_day_president_vote", "local_explanation_required": True}],
        "electoral_swing": [], "spatial_anomalies": [],
    }
    return ElectionAssessmentBuilder().build(
        campaign_state={"as_of": "2026-09-27T00:00:00+08:00", "campaign_state_status": "partial_current_data", "same_series_poll_changes": []},
        current_events=data["events"], campaign_event_resolution={"events": data["events"]},
        metrics=metrics, polls=data["polls"], research_result=research or {},
        unknowns=["部分地区资料不足"], warnings=[], local_knowledge=data["local_knowledge"],
        historical_baseline=json.loads((FIXTURE / "historical_elections.json").read_text(encoding="utf-8")),
        current_candidates=[{"candidate_name": "候選人甲"}, {"candidate_name": "候選人乙"}],
        task={"jurisdiction": "新北市", "election_type": "county_mayor", "target_year": 2026},
        draft_assessment=draft,
    )


def recursive_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from recursive_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_keys(child)


def test_aq01_no_definite_vote_transfer():
    assessment = build()
    assert all(row["actual_vote_transfer_unknown"] for row in assessment["third_party_signals"])


def test_aq02_residual_is_not_personal_vote():
    comparison = assessment_row = build()["cross_level_comparisons"][0]
    assert assessment_row["comparison_type"] == "candidate_residuals"
    assert "not transferred votes, personal votes" in comparison["warning"]


def test_aq03_incomparable_polls_do_not_create_trend():
    assert build()["poll_context"]["comparability_status"] == "no_comparable_trend"


def test_aq04_margin_of_error_has_no_clear_lead():
    assert "not a clear lead" in build()["poll_context"]["margin_rule"]


def test_aq05_single_visit_is_not_grassroots_support():
    visit = next(x for x in build()["organization_networks"] if x["event_id"] == "visit-1")
    assert visit["relation_type"] == "single_visit" and visit["confidence"] == "low"
    assert "does not establish grassroots" in visit["analytical_boundary"]


def test_aq06_joint_headquarters_stronger_but_no_vote_effect():
    hq = next(x for x in build()["organization_networks"] if x["event_id"] == "hq-1")
    assert hq["relation_type"] == "joint_headquarters" and hq["confidence"] == "medium"
    assert "vote effect" in hq["analytical_boundary"]


def test_aq07_third_party_is_independent_dimension():
    row = build()["third_party_signals"][0]
    assert set(row) >= {"party_cooperation", "organization_coordination", "supporter_preference_poll", "actual_vote_transfer_unknown"}


def test_aq08_two_regions_have_distinct_variables():
    regions = {row["region"]: row for row in build()["regional_assessments"]}
    assert {"甲區", "乙區"} <= set(regions)
    assert regions["甲區"]["issue_signals"] != regions["乙區"]["issue_signals"]


def test_aq09_gap_produces_concrete_followup():
    questions = build()["follow_up_questions"]
    assert len(questions) >= 2
    assert all(set(row) >= {"region", "actors", "time_window", "relationship_to_verify", "preferred_evidence_types", "priority"} for row in questions)


def test_aq10_final_is_traceably_updated_after_one_round():
    draft = build()
    question = draft["follow_up_questions"][0]["question"]
    research = dict(source()["research_result"])
    research["findings"][0]["question"] = question
    final = build(research=research, draft=draft)
    assert final["assessment_stage"] == "final"
    assert final["research_history"]["follow_up_round_count"] == 1
    assert final["research_history"]["resolved_question_count"] >= 1


def test_aq11_unresolved_question_remains_visible():
    final = build(research=source()["research_result"], draft=build())
    assert any("實際票流" in item for item in final["uncertainties"])


def test_aq12_writer_receives_final_assessment_first_and_forbids_news_list():
    assessment = build()
    context = {"analysis_context": {"assessment": assessment, "historical_baseline": {}, "local_knowledge": {}, "polls": [], "unknowns": []}}
    payload = _analysis_payload(context)
    assert next(iter(payload)) == "research_brief"
    assert "禁止按日期或新闻逐条机械汇总" in SYSTEM_INSTRUCTIONS
    request = ParsedRequest(intent=FULL_ANALYSIS, text="分析新北", focus=ElectionFocus(jurisdiction="新北市"))
    text = asyncio.run(DeterministicReportWriter().write(request, context))
    assert "结构化分析摘要" in text


def test_aq13_no_prediction_or_ranking_fields():
    forbidden = {"winner", "ranking", "win_probability", "score"}
    assert forbidden.isdisjoint(set(recursive_keys(build())))


def test_aq14_offline_pipeline_never_calls_external_search():
    with temp_repo() as root:
        populate_full_repo(root)
        class ForbiddenBackend:
            research_coordinator = None
            def network_allowed(self):
                return False
            def search(self, *args, **kwargs):
                raise AssertionError("offline external search")
        pipeline = AnalysisPipeline(root, mode="offline", retrieval_backend=ForbiddenBackend())
        assert pipeline.run(make_task(), allow_online=False).analysis_context["assessment"]["version"] == 2


def test_aq15_explicit_historical_as_of_is_preserved():
    with temp_repo() as root:
        populate_full_repo(root)
        as_of = "2024-01-15T08:00:00+08:00"
        analysis = AnalysisPipeline(root, mode="offline").run(make_task(), allow_online=False, as_of=as_of).analysis_context
        assert analysis["assessment"]["as_of"] == as_of
        assert analysis["campaign_state"]["as_of"] == as_of
