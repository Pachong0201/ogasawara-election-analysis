import unittest

from bot.models import ElectionFocus, ParsedRequest
from bot.report_writer import (
    ChatCompletionsReportWriter,
    DeterministicReportWriter,
    _analysis_payload,
    _reader_payload,
    _apply_validation_issues,
    build_report_writer,
)
from bot.router import FULL_ANALYSIS


class TestDeterministicReportWriter(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_summary_without_openai(self):
        writer = DeterministicReportWriter()
        request = ParsedRequest(
            intent=FULL_ANALYSIS,
            text="分析高雄选情",
            focus=ElectionFocus(jurisdiction="高雄市"),
        )
        context = {
            "analysis_context": {
                "readiness": {"status": "READY"},
                "campaign_state": {
                    "as_of": "2026-09-23T00:00:00+08:00",
                    "windows": {"7d": {"event_count": 3}},
                },
                "current_candidates": [{}, {}],
                "unknowns": [],
            },
            "analysis_manifest": {"skill_version": "1.4.0"},
        }
        text = await writer.write(request, context)
        self.assertIn("高雄市", text)
        self.assertIn("READY", text)
        self.assertIn("7d 3项", text)
        self.assertIn("DEEPSEEK_API_KEY", text)


    async def test_resolved_campaign_event_is_presented_with_evidence_boundary(self):
        writer = DeterministicReportWriter()
        request = ParsedRequest(
            intent=FULL_ANALYSIS,
            text="更新高雄选情",
            focus=ElectionFocus(jurisdiction="高雄市"),
        )
        context = {
            "analysis_context": {
                "readiness": {"status": "READY"},
                "campaign_state": {"as_of": "2026-09-23T00:00:00+08:00"},
                "campaign_event_resolution": {
                    "events": [{
                        "date": "2026-09-22",
                        "event_type": "campaign_org",
                        "verification_status": "corroborated_media",
                        "candidate_entities": ["賴瑞隆"],
                        "evidence_excerpt": "兩家獨立媒體正文都描述同一場後援會成立活動。",
                    }],
                    "stats": {
                        "event_count": 1,
                        "corroborated_event_count": 1,
                        "single_source_event_count": 0,
                    },
                },
                "evidence_summary": {
                    "retrieval": {"backend": "fixture", "available": True}
                },
            },
            "analysis_manifest": {"skill_version": "1.4.0"},
        }
        text = await writer.write(request, context)
        self.assertIn("跨来源相互印证1项", text)
        self.assertIn("仍待高等级来源确认", text)
        self.assertIn("賴瑞隆", text)

    async def test_llm_payload_drops_full_article_content(self):
        context = {
            "analysis_context": {
                "campaign_state": {
                    "retrieval_leads": [{
                        "lead_id": "l1",
                        "title": "标题",
                        "url": "https://example.test/a",
                        "body_status": "read",
                        "content": "X" * 50000,
                        "content_sha256": "abc",
                    }]
                },
                "campaign_event_resolution": {
                    "events": [{
                        "event_id": "e1",
                        "evidence_excerpt": "保留的有限证据摘录",
                    }]
                },
                "assessment": {
                    "version": 1,
                    "current_dynamics": [{"kind": "research_finding", "statement": "结构化分析输入"}],
                    "research_coverage": {"evidence_pack_count": 1, "finding_count": 1},
                    "evidence_pack": [{"evidence_id": "web-1", "excerpt": "正文证据"}],
                },
                "evidence_summary": {
                    "automatic_research": {
                        "status": "completed",
                        "findings": [{"statement": "研究摘要"}],
                        "evidence_pack": [{"url": "https://example.test/a", "content": "不应重复传入"}],
                    }
                },
            },
            "analysis_manifest": {},
        }
        payload = _analysis_payload(context)
        lead = payload["campaign_state"]["retrieval_leads"][0]
        self.assertNotIn("content", lead)
        self.assertEqual(lead["content_sha256"], "abc")
        self.assertEqual(
            payload["campaign_event_resolution"]["events"][0]["evidence_excerpt"],
            "保留的有限证据摘录",
        )
        self.assertEqual(payload["assessment"]["version"], 1)
        self.assertEqual(
            payload["assessment"]["current_dynamics"][0]["statement"],
            "结构化分析输入",
        )
        self.assertNotIn(
            "evidence_pack",
            payload["evidence_summary"]["automatic_research"],
        )
        self.assertEqual(
            payload["assessment"]["evidence_pack"][0]["excerpt"],
            "正文证据",
        )

    async def test_reader_payload_hides_legacy_assessment_from_llm(self):
        context = {
            "analysis_context": {
                "research_brief": {"version": 1, "core_facts": [{"statement": "fact"}]},
                "assessment": {"version": 2, "key_variables": [{"name": "legacy"}]},
                "historical_baseline": {"raw": "legacy"},
                "current_events": [{"event_id": "e1", "evidence_excerpt": "bounded"}],
                "sources": [{"source_id": "s1", "source_grade": "A"}],
                "unknowns": ["gap"],
            },
            "analysis_manifest": {},
        }
        payload = _reader_payload(context)
        self.assertEqual(payload["research_brief"]["version"], 1)
        self.assertNotIn("assessment", payload)
        self.assertNotIn("final_assessment", payload)
        self.assertNotIn("historical_baseline", payload)
        self.assertIn("bounded_evidence_excerpts", payload)


    async def test_validator_only_replaces_flagged_exact_span(self):
        draft = "第一段保持不变。\n\n第二段称该活动已经转化为选票优势。\n\n第三段保持不变。"
        raw = """{"issues":[{"excerpt":"第二段称该活动已经转化为选票优势。","replacement":"第二段只能说明出现了公开活动，尚不能据此判断选票效果。","reason":"组织活动不能直接推断选票效果"}]}"""
        revised = _apply_validation_issues(draft, raw)
        self.assertIn("第一段保持不变。", revised)
        self.assertIn("第三段保持不变。", revised)
        self.assertIn("尚不能据此判断选票效果", revised)
        self.assertNotIn("已经转化为选票优势", revised)

    async def test_validator_ignores_non_exact_or_full_rewrite_attempt(self):
        draft = "原文甲。原文乙。"
        raw = """{"issues":[{"excerpt":"不存在的片段","replacement":"整篇重写内容","reason":"test"}]}"""
        self.assertEqual(_apply_validation_issues(draft, raw), draft)



def test_deepseek_uses_chat_completions_writer():
    writer = build_report_writer(
        api_key="test-key",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
    )
    assert isinstance(writer, ChatCompletionsReportWriter)
    assert writer.base_url == "https://api.deepseek.com"
    assert writer.model == "deepseek-flash"


if __name__ == "__main__":
    unittest.main()
