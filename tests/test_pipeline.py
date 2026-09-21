import unittest

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
            self.assertEqual(manifest["skill_version"], "1.1.1")

    def test_pipeline_keeps_insufficient_status_and_does_not_invent_data(self):
        with temp_repo() as root:
            pipeline = AnalysisPipeline(root, mode="offline")
            context = pipeline.run(make_task(), allow_online=False)
            analysis = context.analysis_context

            self.assertEqual(analysis["readiness"]["status"], "INSUFFICIENT")
            self.assertTrue(analysis["readiness"]["missing"])
            self.assertTrue(any("full structural analysis is not allowed" in item for item in analysis["unknowns"]))


if __name__ == "__main__":
    unittest.main()
