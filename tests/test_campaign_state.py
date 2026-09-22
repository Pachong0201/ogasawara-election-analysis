import datetime as dt
import unittest

from runtime.campaign_state import CampaignStateBuilder, build_same_series_poll_trends
from runtime.models import ElectionTask
from runtime.source_registry import FixtureBackend
from tests.fixtures.helpers import temp_repo


class TestCampaignState(unittest.TestCase):
    def test_windows_and_campaign_change_trigger(self):
        with temp_repo() as root:
            task = ElectionTask("county_mayor", 2026, "高雄市")
            builder = CampaignStateBuilder(root, mode="offline")
            events = [
                {
                    "event_id": "e1",
                    "date": "2026-09-20",
                    "claim_type": "endorsement",
                    "speaker": "人物甲",
                    "source_grade": "C",
                    "verification_status": "verified",
                    "independent_source_count": 2,
                    "summary": "公开支持候选人甲",
                },
                {
                    "event_id": "e2",
                    "date": "2026-09-01",
                    "claim_type": "campaign_event",
                    "source_grade": "C",
                    "verification_status": "verified",
                    "summary": "一般活动",
                },
            ]
            snapshot = builder.build(
                task,
                current_candidates=[
                    {
                        "candidate_id": "c1",
                        "candidate_name": "候选人甲",
                        "candidate_status": "registered",
                    }
                ],
                events=events,
                polls=[],
                allow_online=False,
                as_of=dt.date(2026, 9, 22),
            )
            self.assertEqual(snapshot["campaign_stage"], "registered_general_campaign")
            self.assertEqual(snapshot["window_counts"]["7d"], 1)
            self.assertEqual(snapshot["window_counts"]["30d"], 2)
            self.assertEqual(len(snapshot["campaign_change_triggers"]), 1)
            self.assertTrue(snapshot["campaign_change_triggers"][0]["local_explanation_required"])
            self.assertTrue(snapshot["research_questions"])

    def test_same_series_poll_trend_only_compares_matching_method_and_question(self):
        polls = [
            {
                "poll_id": "p1",
                "pollster": "甲民调",
                "method": "telephone_landline",
                "sample_frame": "20岁以上居民",
                "question_wording": "支持哪位候选人？",
                "field_end": "2026-08-01",
                "candidate_support": [
                    {"candidate": "候选人甲", "support": 0.40},
                    {"candidate": "候选人乙", "support": 0.35},
                ],
            },
            {
                "poll_id": "p2",
                "pollster": "甲民调",
                "method": "telephone_landline",
                "sample_frame": "20岁以上居民",
                "question_wording": "支持哪位候选人？",
                "field_end": "2026-09-01",
                "candidate_support": [
                    {"candidate": "候选人甲", "support": 0.43},
                    {"candidate": "候选人乙", "support": 0.34},
                ],
            },
            {
                "poll_id": "p3",
                "pollster": "甲民调",
                "method": "online_closed",
                "sample_frame": "20岁以上居民",
                "question_wording": "支持哪位候选人？",
                "field_end": "2026-09-10",
                "candidate_support": [
                    {"candidate": "候选人甲", "support": 0.50},
                    {"candidate": "候选人乙", "support": 0.30},
                ],
            },
        ]
        trends = build_same_series_poll_trends(polls)
        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0]["observation_count"], 2)
        delta = trends[0]["deltas"][0]["candidate_change_pp"]
        self.assertEqual(delta["候选人甲"], 3.0)
        self.assertEqual(delta["候选人乙"], -1.0)

    def test_snapshot_delta_detects_new_event_and_candidate(self):
        with temp_repo() as root:
            task = ElectionTask("county_mayor", 2026, "高雄市")
            builder = CampaignStateBuilder(root, mode="offline")
            builder.build(
                task,
                current_candidates=[
                    {
                        "candidate_id": "c1",
                        "candidate_name": "候选人甲",
                        "candidate_status": "nominated",
                    }
                ],
                events=[],
                polls=[],
                allow_online=False,
                as_of=dt.date(2026, 9, 20),
            )
            second = builder.build(
                task,
                current_candidates=[
                    {
                        "candidate_id": "c1",
                        "candidate_name": "候选人甲",
                        "candidate_status": "registered",
                    },
                    {
                        "candidate_id": "c2",
                        "candidate_name": "候选人乙",
                        "candidate_status": "registered",
                    },
                ],
                events=[
                    {
                        "event_id": "e-new",
                        "date": "2026-09-21",
                        "claim_type": "candidate_registration",
                        "source_grade": "A",
                        "verification_status": "verified",
                    }
                ],
                polls=[],
                allow_online=False,
                as_of=dt.date(2026, 9, 22),
            )
            delta = second["delta_from_previous_snapshot"]
            self.assertTrue(delta["has_previous_snapshot"])
            self.assertIn("候选人乙", delta["candidate_added"])
            self.assertEqual(delta["new_events"][0]["event_id"], "e-new")
            self.assertTrue(delta["campaign_stage_changed"])

    def test_online_retrieval_keeps_unverified_result_as_lead(self):
        with temp_repo() as root:
            task = ElectionTask("county_mayor", 2026, "高雄市")
            backend = FixtureBackend(
                responses={
                    "*": [
                        {
                            "title": "近期消息",
                            "summary": "候选人团队调整",
                            "published_at": "2026-09-21",
                            "source_grade": "C",
                            "verification_status": "lead_only",
                        }
                    ]
                }
            )
            builder = CampaignStateBuilder(root, retrieval_backend=backend, mode="online")
            snapshot = builder.build(
                task,
                current_candidates=[],
                events=[],
                polls=[],
                allow_online=True,
                as_of=dt.date(2026, 9, 22),
            )
            self.assertEqual(snapshot["window_counts"]["30d"], 0)
            self.assertTrue(snapshot["retrieval_leads"])


if __name__ == "__main__":
    unittest.main()
