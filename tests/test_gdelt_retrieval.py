import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bot.config import BotConfig
from bot.feishu_app import build_service
from bot.models import ElectionFocus, ParsedRequest
from bot.report_writer import DeterministicReportWriter
from bot.router import CAMPAIGN_UPDATE
from runtime.campaign_state import CampaignStateBuilder
from runtime.gdelt_retrieval import GDELTNewsBackend
from runtime.pipeline import AnalysisPipeline
from runtime.source_registry import OfflineRetrievalError, SourceRegistry
from tests.fixtures.helpers import make_task, populate_full_repo, temp_repo


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class TestGDELTNewsBackend(unittest.TestCase):
    def test_campaign_queries_return_deduplicated_unverified_headlines(self):
        requests = []
        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            return Response(json.dumps({"articles": [
                {"url": "https://news.test/story?id=42&utm_source=tracking", "title": "高雄選舉新聞", "seendate": "20260923T010203Z"},
                {"url": "https://news.test/story?utm_medium=other&id=42", "title": "重複新聞", "seendate": "20260923T010203Z"},
                {"url": "file:///etc/passwd", "title": "bad"},
            ]}).encode())
        backend = GDELTNewsBackend(opener=opener, max_records=5)
        with tempfile.TemporaryDirectory() as tmp:
            builder = CampaignStateBuilder(Path(tmp), retrieval_backend=backend)
            leads, warnings = builder.retrieve_current_leads("高雄市", 2026, allow_online=True)
            snapshot = builder.build("高雄市", 2026, [], [], [], retrieval_leads=leads, persist=False)
        self.assertEqual(warnings, [])
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["source_grade"], "E")
        self.assertEqual(leads[0]["verification_status"], "lead_only")
        self.assertEqual(leads[0]["first_seen_at"], "2026-09-23T01:02:03+00:00")
        self.assertNotIn("published_at", leads[0])
        self.assertEqual(backend.fetch("https://news.test/story?id=42"), backend._seen["https://news.test/story?id=42"])
        self.assertIsNone(backend.fetch("https://other.test/story"))
        self.assertEqual(snapshot["verified_retrieval_lead_count"], 0)
        self.assertNotIn("verified_current_retrieval", snapshot["campaign_change_reasons"])
        self.assertEqual(len(requests), 5)
        params = parse_qs(urlparse(requests[0][0]).query)
        self.assertEqual(params["timespan"], ["30d"])
        self.assertEqual(params["mode"], ["artlist"])
        self.assertIn('"Kaohsiung"', params["query"][0])

    def test_timeout_circuits_request_then_recovers(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise OSError("network unavailable")
        backend = GDELTNewsBackend(opener=opener)
        with self.assertRaises(OfflineRetrievalError):
            backend.search("高雄市 選舉", jurisdiction="高雄市")
        with self.assertRaises(OfflineRetrievalError):
            backend.search("高雄市 選舉", jurisdiction="高雄市")
        self.assertEqual(len(calls), 1)
        self.assertFalse(backend.metadata()["available"])
        backend._cooldown_until = 0
        with self.assertRaises(OfflineRetrievalError):
            backend.search("高雄市 選舉", jurisdiction="高雄市")
        self.assertEqual(len(calls), 2)

    def test_unknown_jurisdiction_does_not_make_request(self):
        backend = GDELTNewsBackend(opener=lambda *_args, **_kwargs: self.fail("network called"))
        self.assertEqual(backend.search("无县市问题"), [])

    def test_pipeline_exposes_live_leads_without_verifying_events(self):
        def opener(request, timeout):
            return Response(json.dumps({"articles": [{
                "url": "https://news.test/election", "title": "Hsinchu election headline",
                "seendate": "20260923T010203Z",
            }]}).encode())
        with temp_repo() as root:
            populate_full_repo(root)
            backend = GDELTNewsBackend(opener=opener)
            registry = SourceRegistry(config_path=root / "config" / "no-adapters.yaml")
            analysis = AnalysisPipeline(root, mode="online", retrieval_backend=backend,
                                        source_registry=registry).run(
                make_task(), allow_online=True,
            ).analysis_context
        self.assertEqual(analysis["campaign_state"]["retrieval_lead_count"], 1)
        self.assertEqual(analysis["campaign_state"]["verified_retrieval_lead_count"], 0)
        self.assertEqual(analysis["evidence_summary"]["retrieval"]["backend"], "gdelt_doc_news")

    def test_service_wiring_respects_disabled_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode, provider, expected in [("online", "gdelt", True), ("offline", "gdelt", False), ("online", "disabled", False)]:
                config = BotConfig(repo_root=Path(tmp), conversation_db=Path(tmp) / "bot.db",
                                   skill_mode=mode, retrieval_provider=provider)
                service = build_service(config)
                self.assertEqual(isinstance(service.skill_service.retrieval_backend, GDELTNewsBackend), expected)


class TestRetrievalPresentation(unittest.IsolatedAsyncioTestCase):
    async def test_headlines_have_traceable_links_and_warning(self):
        writer = DeterministicReportWriter()
        request = ParsedRequest(intent=CAMPAIGN_UPDATE, text="高雄最新情况", focus=ElectionFocus(jurisdiction="高雄市"))
        context = {"analysis_context": {"campaign_state": {
            "as_of": "2026-09-23T02:00:00+00:00", "retrieval_leads": [{
                "title": "高雄選舉新聞", "url": "https://news.test/story", "first_seen_at": "2026-09-23T01:02:03+00:00",
            }]}, "evidence_summary": {"retrieval": {"backend": "gdelt_doc_news", "available": True}}}}
        output = await writer.write(request, context)
        self.assertIn("已读取正文0条", output)
        self.assertIn("https://news.test/story", output)
        self.assertIn("首次发现", output)


if __name__ == "__main__":
    unittest.main()
