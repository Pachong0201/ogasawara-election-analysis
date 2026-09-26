import unittest
from unittest.mock import patch

from runtime.election_loader import write_jsonl
from runtime.pipeline import AnalysisPipeline
from tests.fixtures.helpers import AREA_A, make_task, populate_full_repo, temp_repo


class TestAnalysisPipeline(unittest.TestCase):
    def test_full_local_pipeline_builds_traceable_context(self):
        with temp_repo() as root:
            populate_full_repo(root)
            pipeline = AnalysisPipeline(root, mode="offline")
            context = pipeline.run(make_task(), allow_online=False)

            payload = context.to_dict()
            analysis = payload["analysis_context"]
            manifest = payload["analysis_manifest"]

            self.assertEqual(analysis["readiness"]["status"], "READY")
            self.assertIn(AREA_A, analysis["historical_baseline"]["historical_matrix"]["regions"])
            self.assertGreater(len(analysis["electoral_swing"]), 0)
            self.assertGreater(len(analysis["split_ticket"]), 0)
            self.assertGreater(len(analysis["candidate_residuals"]), 0)
            self.assertIn("triggered_anomaly_count", analysis["evidence_summary"])
            self.assertIn("campaign_state", analysis)
            self.assertIn("as_of", analysis["campaign_state"])
            self.assertIn("knowledge_views", analysis)
            self.assertEqual(
                analysis["knowledge_views"]["stable_local_baseline"]["includes"],
                ["historical_baseline", "local_knowledge.stable_local_baseline"],
            )
            self.assertIn("stable_local_baseline", analysis["local_knowledge"])
            self.assertIn("dynamic_local_state", analysis["local_knowledge"])
            self.assertIn(
                "official_context_files",
                analysis["local_knowledge"]["stable_local_baseline"],
            )
            self.assertGreater(len(manifest["files_used"]), 0)
            self.assertEqual(manifest["skill_version"], "1.4.0")

    def test_pipeline_keeps_insufficient_status_and_does_not_invent_data(self):
        with temp_repo() as root:
            pipeline = AnalysisPipeline(root, mode="offline")
            context = pipeline.run(make_task(), allow_online=False)
            analysis = context.analysis_context

            self.assertEqual(analysis["readiness"]["status"], "INSUFFICIENT")
            self.assertTrue(analysis["readiness"]["missing"])
            self.assertTrue(any("full structural analysis is not allowed" in item for item in analysis["unknowns"]))


    def test_stale_poll_is_labeled_and_cannot_calibrate_current_state(self):
        with temp_repo() as root:
            populate_full_repo(root)
            poll_path = root / "cache" / "polls" / "新竹縣.jsonl"
            write_jsonl(
                poll_path,
                [
                    {
                        "poll_id": "stale-poll",
                        "pollster": "Fixture Pollster",
                        "commissioner": "Fixture",
                        "jurisdiction": "新竹縣",
                        "election_type": "county_mayor",
                        "election_year": 2026,
                        "method": "telephone_landline",
                        "sample_size": 1000,
                        "sample_frame": "20歲以上居民",
                        "sampling": "RDD",
                        "weighting": "性別年齡地區加權",
                        "field_start": "2026-03-01",
                        "field_end": "2026-03-02",
                        "publish_date": "2026-03-03",
                        "moe": 3.1,
                        "moe_applicable": True,
                        "undecided": 0.20,
                        "question_wording": "若明天投票，您支持哪一位候選人？",
                        "cross_tabs_available": False,
                        "source": "fixture",
                        "source_grade": "C",
                        "retrieved_at": "2026-09-22T00:00:00+00:00",
                        "last_verified_at": "2026-09-22T00:00:00+00:00",
                    }
                ],
            )

            pipeline = AnalysisPipeline(root, mode="offline")
            context = pipeline.run(make_task(), allow_online=False)
            analysis = context.analysis_context

            self.assertEqual(analysis["polls"][0]["freshness_status"], "stale")
            self.assertFalse(
                analysis["evidence_summary"]["current_poll_calibration_available"]
            )
            self.assertEqual(analysis["evidence_summary"]["fresh_poll_count"], 0)
            self.assertEqual(analysis["evidence_summary"]["stale_poll_count"], 1)
            self.assertTrue(
                any("must not calibrate the current state" in item for item in analysis["unknowns"])
            )

    def test_campaign_trigger_reaches_local_research_without_structural_anomaly(self):
        with temp_repo() as root:
            populate_full_repo(root)
            write_jsonl(root / "cache" / "events" / "sample.jsonl", [{
                "jurisdiction": "新竹縣", "date": "2026-09-22",
                "claim_type": "endorsement", "speaker": "地方人士", "subject": "候選人",
                "claim_value": "support", "source_grade": "A",
                "source": "fixture", "url": "https://example.test/endorsement",
                "verification_status": "verified", "last_verified_at": "2026-09-22",
            }])
            pipeline = AnalysisPipeline(root, mode="auto")
            with patch.object(pipeline, "_triggered", return_value=[]):
                analysis = pipeline.run(make_task(), allow_online=False,
                                        as_of="2026-09-23").analysis_context
            self.assertEqual(analysis["evidence_summary"]["triggered_anomaly_count"], 0)
            self.assertTrue(analysis["campaign_change_trigger"])
            self.assertTrue(analysis["local_knowledge"]["research_questions"])
            self.assertEqual(analysis["campaign_state_status"], "current")

    def test_offline_structural_analysis_is_separate_from_current_state(self):
        with temp_repo() as root:
            populate_full_repo(root)
            analysis = AnalysisPipeline(root, mode="offline").run(
                make_task(), allow_online=False).analysis_context
            self.assertIn("historical_matrix", analysis["historical_baseline"])
            self.assertEqual(analysis["campaign_state_status"], "insufficient_current_data")
            self.assertFalse(analysis["campaign_change_trigger"])



if __name__ == "__main__":
    unittest.main()
