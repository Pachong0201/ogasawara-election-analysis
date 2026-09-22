import json
import tempfile
import unittest
from pathlib import Path

from runtime.campaign_state import CampaignStateBuilder, CampaignStateStore
from runtime.source_registry import FixtureBackend


class CampaignStateTests(unittest.TestCase):
    def test_windows_and_verified_event_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            builder = CampaignStateBuilder(
                Path(tmp),
                config={"campaign_state": {"persist_snapshots": False, "windows_days": [7, 14, 30]}},
            )
            events = [
                {
                    "event_id": "e1",
                    "date": "2026-09-20",
                    "claim_type": "endorsement",
                    "source_grade": "C",
                    "verification_status": "verified",
                },
                {
                    "event_id": "e2",
                    "date": "2026-08-01",
                    "claim_type": "policy",
                    "source_grade": "C",
                    "verification_status": "verified",
                },
                {
                    "event_id": "e3",
                    "date": "2026-09-21",
                    "claim_type": "controversy",
                    "source_grade": "D",
                    "verification_status": "campaign_claim",
                },
            ]
            snapshot = builder.build(
                "高雄市",
                2026,
                current_candidates=[],
                current_events=events,
                polls=[],
                as_of="2026-09-23T00:00:00+08:00",
                persist=False,
            )
            self.assertEqual(snapshot["windows"]["7d"]["event_count"], 2)
            self.assertEqual(snapshot["windows"]["7d"]["verified_trigger_event_count"], 1)
            self.assertTrue(snapshot["campaign_change_trigger"])
            self.assertIn("verified_recent_campaign_event", snapshot["campaign_change_reasons"])

    def test_same_series_poll_delta_uses_percentage_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            builder = CampaignStateBuilder(
                Path(tmp),
                config={
                    "campaign_state": {
                        "persist_snapshots": False,
                        "same_series_poll_change_observe_pp": 3.0,
                    }
                },
            )
            base = {
                "pollster": "Example Poll",
                "commissioner": "Example Commissioner",
                "method": "CATI",
                "sample_frame": "landline",
                "question_wording": "candidate choice",
            }
            polls = [
                {
                    **base,
                    "poll_id": "p1",
                    "field_end": "2026-08-20",
                    "candidate_support": [
                        {"candidate": "甲", "support": 0.40},
                        {"candidate": "乙", "support": 0.35},
                    ],
                },
                {
                    **base,
                    "poll_id": "p2",
                    "field_end": "2026-09-20",
                    "candidate_support": [
                        {"candidate": "甲", "support": 0.44},
                        {"candidate": "乙", "support": 0.33},
                    ],
                },
            ]
            changes = builder.same_series_poll_changes(polls)
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["candidate_deltas"]["甲"], 4.0)
            self.assertEqual(changes[0]["candidate_deltas"]["乙"], -2.0)
            self.assertTrue(changes[0]["change_observed"])

    def test_different_poll_series_are_not_combined(self):
        with tempfile.TemporaryDirectory() as tmp:
            builder = CampaignStateBuilder(Path(tmp), config={"campaign_state": {"persist_snapshots": False}})
            polls = [
                {
                    "poll_id": "p1",
                    "pollster": "A",
                    "commissioner": "X",
                    "method": "CATI",
                    "sample_frame": "landline",
                    "question_wording": "choice",
                    "field_end": "2026-08-20",
                    "candidate_support": [{"candidate": "甲", "support": 0.40}],
                },
                {
                    "poll_id": "p2",
                    "pollster": "B",
                    "commissioner": "X",
                    "method": "online",
                    "sample_frame": "panel",
                    "question_wording": "choice",
                    "field_end": "2026-09-20",
                    "candidate_support": [{"candidate": "甲", "support": 0.50}],
                },
            ]
            self.assertEqual(builder.same_series_poll_changes(polls), [])

    def test_previous_snapshot_produces_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CampaignStateStore(root)
            store.save(
                "高雄市",
                {
                    "as_of": "2026-09-20T00:00:00+08:00",
                    "candidate_keys": ["甲"],
                    "event_ids": ["old-event"],
                    "poll_ids": ["old-poll"],
                },
            )
            builder = CampaignStateBuilder(root, config={"campaign_state": {"persist_snapshots": False}})
            snapshot = builder.build(
                "高雄市",
                2026,
                current_candidates=[{"candidate_name": "甲"}, {"candidate_name": "乙"}],
                current_events=[{"event_id": "new-event", "date": "2026-09-22"}],
                polls=[{"poll_id": "new-poll", "field_end": "2026-09-22"}],
                as_of="2026-09-23T00:00:00+08:00",
                persist=False,
            )
            delta = snapshot["snapshot_delta"]
            self.assertTrue(delta["previous_snapshot_available"])
            self.assertIn({"change": "candidate_added", "candidate_key": "乙"}, delta["candidate_changes"])
            self.assertEqual(delta["new_event_ids"], ["new-event"])
            self.assertEqual(delta["new_poll_ids"], ["new-poll"])

    def test_host_retrieval_stays_lead_only_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FixtureBackend(
                default=[
                    {
                        "title": "latest campaign report",
                        "url": "https://example.test/report",
                        "source_grade": "C",
                    }
                ]
            )
            builder = CampaignStateBuilder(
                Path(tmp),
                retrieval_backend=backend,
                config={"campaign_state": {"persist_snapshots": False}},
            )
            leads, warnings = builder.retrieve_current_leads("高雄市", 2026, allow_online=True)
            self.assertFalse(warnings)
            self.assertGreaterEqual(len(leads), 1)
            self.assertTrue(all(item["verification_status"] == "lead_only" for item in leads))
            self.assertTrue(all(item["layer_id"] == "L4" for item in leads))


if __name__ == "__main__":
    unittest.main()
