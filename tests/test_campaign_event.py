import tempfile
import unittest
from pathlib import Path

from runtime.campaign_event import CampaignEventResolver
from runtime.campaign_state import CampaignStateBuilder, CampaignStateStore
from runtime.pipeline import AnalysisPipeline
from runtime.source_registry import RetrievalBackend, SourceRegistry
from tests.fixtures.helpers import make_task, populate_full_repo, temp_repo


def lead(domain, slug, title, content, page_date="2026-09-22T09:00:00+08:00"):
    return {
        "lead_id": f"{domain}-{slug}",
        "title": title,
        "url": f"https://{domain}/{slug}",
        "source_name": domain,
        "independence_key": domain,
        "source_grade": "E",
        "verification_status": "body_read_unverified",
        "body_status": "read",
        "content": content,
        "page_date": page_date,
        "first_seen_at": "2026-09-22T02:00:00+00:00",
        "content_sha256": f"sha-{domain}-{slug}",
        "query": "高雄市 2026 選舉 地方人物 支持 組織",
    }


class TestCampaignEventResolver(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"candidate_name": "賴瑞隆"},
            {"candidate_name": "柯志恩"},
        ]

    def test_two_independent_bodies_form_corroborated_research_event(self):
        leads = [
            lead(
                "cna.com.tw",
                "a",
                "賴瑞隆鳳山後援會成立",
                "賴瑞隆在鳳山區成立後援會，地方人士出席活動。後援會表示將持續進行地方組織工作.",
            ),
            lead(
                "udn.com",
                "b",
                "鳳山後援會成立 賴瑞隆出席",
                "賴瑞隆出席鳳山區後援會成立活動，現場多名地方人士參與。團隊表示後續將強化組織。",
            ),
        ]
        result = CampaignEventResolver().extract(
            leads,
            jurisdiction="高雄市",
            current_candidates=self.candidates,
        )
        self.assertEqual(result["stats"]["event_count"], 1)
        self.assertEqual(result["stats"]["corroborated_event_count"], 1)
        event = result["events"][0]
        self.assertEqual(event["verification_status"], "corroborated_media")
        self.assertEqual(event["source_grade"], "C")
        self.assertEqual(event["structural_use"], "research_trigger_only")
        self.assertEqual(event["independent_source_count"], 2)
        self.assertIn("賴瑞隆", event["candidate_entities"])
        self.assertEqual(event["event_type"], "campaign_org")
        self.assertEqual(event["date"], "2026-09-22")
        self.assertEqual(len(event["sources"]), 2)

    def test_single_body_remains_context_only(self):
        result = CampaignEventResolver().extract(
            [
                lead(
                    "cna.com.tw",
                    "a",
                    "柯志恩競選總部動態",
                    "柯志恩競選總部今天召開會議，團隊說明近期競選組織安排與地方活動。",
                )
            ],
            jurisdiction="高雄市",
            current_candidates=self.candidates,
        )
        event = result["events"][0]
        self.assertEqual(event["verification_status"], "single_source_media")
        self.assertEqual(event["structural_use"], "context_only")
        self.assertEqual(event["independent_source_count"], 1)

    def test_different_event_types_are_not_clustered(self):
        result = CampaignEventResolver().extract(
            [
                lead(
                    "cna.com.tw",
                    "org",
                    "賴瑞隆後援會成立",
                    "賴瑞隆在鳳山區成立後援會，地方組織展開動員。",
                ),
                lead(
                    "udn.com",
                    "policy",
                    "賴瑞隆提出交通政策",
                    "賴瑞隆提出新的交通政策，主張改善通勤與公共運輸。",
                ),
            ],
            jurisdiction="高雄市",
            current_candidates=self.candidates,
        )
        self.assertEqual(result["stats"]["event_count"], 2)
        self.assertEqual(
            {event["event_type"] for event in result["events"]},
            {"campaign_org", "policy"},
        )

    def test_unread_headline_does_not_become_event(self):
        result = CampaignEventResolver().extract(
            [
                {
                    "title": "賴瑞隆後援會成立",
                    "url": "https://example.test/a",
                    "body_status": "robots_denied",
                    "first_seen_at": "2026-09-22T00:00:00+00:00",
                }
            ],
            jurisdiction="高雄市",
            current_candidates=self.candidates,
        )
        self.assertEqual(result["events"], [])
        self.assertEqual(result["stats"]["ignored"]["robots_denied"], 1)


class TestCampaignEventStateIntegration(unittest.TestCase):
    def test_corroborated_event_enters_windows_and_triggers_research_only(self):
        resolver = CampaignEventResolver()
        resolved = resolver.extract(
            [
                lead(
                    "cna.com.tw",
                    "a",
                    "賴瑞隆鳳山後援會成立",
                    "賴瑞隆在鳳山區成立後援會，地方人士參與，團隊展開組織動員。",
                ),
                lead(
                    "udn.com",
                    "b",
                    "鳳山後援會成立",
                    "賴瑞隆出席鳳山區後援會成立活動，地方組織開始後續動員。",
                ),
            ],
            jurisdiction="高雄市",
            current_candidates=[{"candidate_name": "賴瑞隆"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            builder = CampaignStateBuilder(
                Path(tmp),
                config={"campaign_state": {"persist_snapshots": False, "windows_days": [7, 14, 30]}},
            )
            snapshot = builder.build(
                "高雄市",
                2026,
                current_candidates=[{"candidate_name": "賴瑞隆"}],
                current_events=resolved["events"],
                polls=[],
                retrieval_leads=[],
                as_of="2026-09-23T12:00:00+08:00",
                persist=False,
            )
        self.assertEqual(snapshot["windows"]["7d"]["event_count"], 1)
        self.assertEqual(snapshot["windows"]["7d"]["verified_trigger_event_count"], 0)
        self.assertEqual(snapshot["windows"]["7d"]["corroborated_media_event_count"], 1)
        self.assertTrue(snapshot["campaign_change_trigger"])
        self.assertIn(
            "corroborated_recent_campaign_event",
            snapshot["campaign_change_reasons"],
        )
        self.assertNotIn(
            "verified_recent_campaign_event",
            snapshot["campaign_change_reasons"],
        )

    def test_snapshot_delta_tracks_new_retrieval_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            CampaignStateStore(root).save(
                "高雄市",
                {
                    "as_of": "2026-09-20T00:00:00+08:00",
                    "target_year": 2026,
                    "election": {"election_type": "unspecified", "target_year": 2026},
                    "candidate_keys": ["賴瑞隆"],
                    "event_ids": [],
                    "poll_ids": [],
                    "retrieval_lead_ids": ["old-lead"],
                },
            )
            builder = CampaignStateBuilder(
                root,
                config={"campaign_state": {"persist_snapshots": False}},
            )
            snapshot = builder.build(
                "高雄市",
                2026,
                current_candidates=[{"candidate_name": "賴瑞隆"}],
                current_events=[],
                polls=[],
                retrieval_leads=[
                    {"lead_id": "old-lead"},
                    {"lead_id": "new-lead"},
                ],
                as_of="2026-09-23T00:00:00+08:00",
                persist=False,
            )
        self.assertEqual(
            snapshot["snapshot_delta"]["new_retrieval_lead_ids"],
            ["new-lead"],
        )


class BodyBackend(RetrievalBackend):
    def search(self, query, **kwargs):
        return [
            lead(
                "cna.com.tw",
                "pipeline-a",
                "甲候選人甲鄉後援會成立",
                "甲候選人在甲鄉成立後援會，地方人士參與，團隊展開組織動員。",
            ),
            lead(
                "udn.com",
                "pipeline-b",
                "甲鄉後援會成立 甲候選人出席",
                "甲候選人出席甲鄉後援會成立活動，地方組織開始後續動員。",
            ),
        ]

    def fetch(self, url):
        return None

    def metadata(self):
        return {"backend": "fixture_body_backend", "lead_only": True, "available": True}


class TestPipelineCampaignEventIntegration(unittest.TestCase):
    def test_pipeline_resolves_bodies_before_campaign_state_and_context(self):
        with temp_repo() as root:
            populate_full_repo(root, jurisdiction="高雄市")
            backend = BodyBackend()
            registry = SourceRegistry(config_path=root / "config" / "no-adapters.yaml")
            context = AnalysisPipeline(
                root,
                mode="online",
                retrieval_backend=backend,
                source_registry=registry,
            ).run(
                make_task(jurisdiction="高雄市"),
                allow_online=True,
            ).to_dict()

        analysis = context["analysis_context"]
        resolution = analysis["campaign_event_resolution"]
        self.assertEqual(resolution["stats"]["corroborated_event_count"], 1)
        self.assertEqual(len(resolution["events"]), 1)
        self.assertTrue(
            any(
                event.get("verification_status") == "corroborated_media"
                for event in analysis["current_events"]
            )
        )
        self.assertIn(
            "corroborated_recent_campaign_event",
            analysis["campaign_state"]["campaign_change_reasons"],
        )
        self.assertEqual(
            analysis["evidence_summary"]["campaign_event_resolution"]["corroborated_event_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
