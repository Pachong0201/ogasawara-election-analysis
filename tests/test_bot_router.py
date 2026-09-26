import unittest

from bot.models import ElectionFocus
from bot.router import (
    CAMPAIGN_UPDATE,
    CONTEXT_QA,
    FULL_ANALYSIS,
    POLL_ANALYSIS,
    SOURCES,
    IntentRouter,
)


class TestBotRouter(unittest.TestCase):
    def setUp(self):
        self.router = IntentRouter(default_target_year=2026)

    def test_full_analysis_extracts_jurisdiction(self):
        request = self.router.parse("分析高雄选情")
        self.assertEqual(request.intent, FULL_ANALYSIS)
        self.assertEqual(request.focus.jurisdiction, "高雄市")
        self.assertEqual(request.focus.target_year, 2026)

    def test_update_extracts_window(self):
        request = self.router.parse("高雄最近7天有什么变化")
        self.assertEqual(request.intent, CAMPAIGN_UPDATE)
        self.assertEqual(request.window_days, 7)
        self.assertTrue(request.refresh)

    def test_followup_reuses_focus(self):
        previous = ElectionFocus(jurisdiction="高雄市", target_year=2026)
        request = self.router.parse("为什么凤山重要？", previous)
        self.assertEqual(request.intent, CONTEXT_QA)
        self.assertEqual(request.focus.jurisdiction, "高雄市")

    def test_poll_and_sources(self):
        previous = ElectionFocus(jurisdiction="台南市", target_year=2026)
        self.assertEqual(self.router.parse("最新民调怎么看", previous).intent, POLL_ANALYSIS)
        self.assertEqual(self.router.parse("给我看依据", previous).intent, SOURCES)


if __name__ == "__main__":
    unittest.main()
