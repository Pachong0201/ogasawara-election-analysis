import unittest

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
            self.assertGreater(len(manifest["files_used"]), 0)
            self.assertEqual(manifest["skill_version"], "1.3.0")

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



if __name__ == "__main__":
    unittest.main()
