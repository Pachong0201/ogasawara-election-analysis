import unittest

from bot.models import ElectionFocus, ParsedRequest
from bot.report_writer import DeterministicReportWriter
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


if __name__ == "__main__":
    unittest.main()
