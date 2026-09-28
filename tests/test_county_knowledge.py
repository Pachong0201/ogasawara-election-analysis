import json
from pathlib import Path

import yaml

from runtime.county_knowledge import COUNTIES, TOPICS, CountyKnowledgeProduction, county_research_questions
from runtime.election_loader import load_jsonl, write_jsonl
from runtime.event_importance import event_importance_signals


def test_registry_and_research_plan_cover_all_required_topics():
    assert len(COUNTIES) == 22
    assert len(set(COUNTIES)) == 22
    assert len(TOPICS) == 10
    for county in COUNTIES:
        rows = county_research_questions(county)
        assert len(rows) == 10
        assert len({row["question_id"] for row in rows}) == 10
        assert all(row["county"] == county for row in rows)
        assert all(row["promotion_required"] for row in rows)


def test_dry_run_has_no_filesystem_side_effects(tmp_path):
    result = CountyKnowledgeProduction(tmp_path).build_county("高雄市", dry_run=True)
    assert result["status"] == "dry_run"
    assert result["question_count"] == 10
    assert not (tmp_path / "knowledge").exists()


def test_batch_build_resume_and_required_package_files(tmp_path):
    production = CountyKnowledgeProduction(tmp_path)
    result = production.build_many(["高雄市", "台南市", "新北市"])
    assert result["failed_count"] == 0
    assert result["completed_count"] == 3
    for county in ("高雄市", "台南市", "新北市"):
        root = tmp_path / "knowledge" / "counties" / county
        for name in (
            "package_manifest.yaml", "evidence_index.jsonl", "unresolved_questions.jsonl",
            "political_ecology.md", "entity_relation_index.jsonl", "research_questions.jsonl",
            "county_template.yaml", "production_state.yaml",
        ):
            assert (root / name).exists(), name
        assert len(load_jsonl(root / "unresolved_questions.jsonl")) == 10
        manifest = yaml.safe_load((root / "package_manifest.yaml").read_text(encoding="utf-8"))
        assert manifest["research_question_count"] == 10
        assert manifest["entity_relation_count"] == 0

    second = production.build_county("高雄市")
    assert second["status"] == "unchanged"
    assert second["resumed"] is True


def test_missing_output_forces_recovery_rebuild(tmp_path):
    production = CountyKnowledgeProduction(tmp_path)
    production.build_county("高雄市")
    target = tmp_path / "knowledge" / "counties" / "高雄市" / "political_ecology.md"
    target.unlink()
    recovered = production.build_county("高雄市")
    assert recovered["status"] == "completed"
    assert target.exists()


def test_requesting_research_does_not_resume_non_research_build(tmp_path):
    production = CountyKnowledgeProduction(tmp_path)
    production.build_county("高雄市")
    calls = []

    def fake_research(county, plan, year):
        calls.append((county, len(plan), year))
        return {"status": "completed", "staged_lead_count": 0, "promotion_performed": False}

    production._run_research = fake_research
    result = production.build_county("高雄市", run_research=True, year=2026)
    assert result["status"] == "completed"
    assert calls == [("高雄市", 10, 2026)]
    assert result["state"]["attempt"] == 2


def test_auto_research_result_remains_unverified_staging(tmp_path):
    production = CountyKnowledgeProduction(tmp_path)
    count = production._stage_research_result(
        "高雄市",
        {
            "findings": [
                {
                    "statement": "公开报道中的待核实线索。",
                    "question": "高雄市地方政治网络为何？",
                    "citations": [
                        {
                            "url": "https://example.test/report",
                            "quote": "正文证据摘录",
                            "publisher_id": "fixture",
                            "source_grade": "B",
                        }
                    ],
                }
            ]
        },
    )
    assert count == 1
    lead = load_jsonl(tmp_path / "cache" / "retrieval" / "高雄市.jsonl")[0]
    assert lead["verification_status"] == "body_grounded_unverified"
    assert not (tmp_path / "knowledge" / "historical").exists()


def test_relation_index_and_event_importance_signal_are_evidence_bounded(tmp_path):
    relationship = {
        "relationship_id": "kh-rel-1",
        "subject": "甲议员",
        "subject_type": "person",
        "object": "乙社团",
        "object_type": "organization",
        "relationship_type": "civic_association",
        "region": "凤山区",
        "time_scope": "2024-2026",
        "last_verified_at": "2026-09-01",
        "current_status": "active_verified",
        "source": "fixture",
        "source_grade": "B",
        "independent_source_count": 1,
        "scope_boundary": "仅确认公开关系，不推断支持转移。",
        "uncertainty": {"level": "low", "reason": "官方记录"},
        "research_questions": ["甲议员与乙社团有何公开关系？"],
    }
    path = tmp_path / "knowledge" / "local" / "高雄市" / "relationships.jsonl"
    write_jsonl(path, [relationship])
    production = CountyKnowledgeProduction(tmp_path)
    production.build_county("高雄市")
    index = load_jsonl(tmp_path / "knowledge" / "counties" / "高雄市" / "entity_relation_index.jsonl")
    assert index[0]["subject"] == "甲议员"
    assert index[0]["object"] == "乙社团"

    signals = event_importance_signals(
        [
            {
                "event_id": "event-1",
                "event_type": "endorsement",
                "speaker": "甲议员",
                "locations": ["凤山区"],
                "summary": "甲议员在凤山区公开活动。",
            }
        ],
        {"relationships": [relationship]},
    )
    assert len(signals) == 1
    assert signals[0]["mechanisms"] == ["local_organization_context"]
    assert "不证明因果关系" in signals[0]["why_important"]
    assert "不得用于胜负预测" in signals[0]["interpretation_limit"]


def test_status_reports_not_started_and_completed(tmp_path):
    production = CountyKnowledgeProduction(tmp_path)
    production.build_county("高雄市")
    rows = production.status(["高雄市", "台南市"])["counties"]
    assert rows[0]["status"] == "completed"
    assert rows[1]["status"] == "not_started"
