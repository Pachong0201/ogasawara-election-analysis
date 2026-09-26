import unittest

from bot.models import ElectionFocus, ParsedRequest
from bot.report_writer import DeterministicReportWriter, _analysis_payload
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
        self.assertIn("OPENAI_API_KEY", text)


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


if __name__ == "__main__":
    unittest.main()
