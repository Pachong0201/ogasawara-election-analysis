import json
import unittest

from runtime.analysis_context import AnalysisContextBuilder
from runtime.data_readiness import DataReadinessGate
from tests.fixtures.helpers import DEFAULT_JURISDICTION, REAL_RUNTIME_CONFIG, make_task, populate_full_repo, temp_repo


class TestAnalysisContext(unittest.TestCase):
    def test_context_and_manifest(self):
        with temp_repo() as root:
            populate_full_repo(root)
            task = make_task()
            gate = DataReadinessGate(root, runtime_config_path=REAL_RUNTIME_CONFIG)
            readiness = gate.check(task)
            builder = AnalysisContextBuilder(root)
            context = builder.build(
                task=task,
                readiness=readiness,
                evidence_summary={"note": "fixture"},
                unknowns=["local knowledge still insufficient"],
                warnings=readiness.warnings,
            )
            payload = context.to_dict()
            for key in [
                "task",
                "readiness",
                "historical_baseline",
                "electoral_swing",
                "split_ticket",
                "candidate_residuals",
                "spatial_anomalies",
                "local_knowledge",
                "current_candidates",
                "current_events",
                "polls",
                "evidence_summary",
                "unknowns",
                "warnings",
                "sources",
            ]:
                self.assertIn(key, payload["analysis_context"])
            self.assertEqual(payload["analysis_manifest"]["task_id"], task.task_id)
            manifest_path = root / "analysis_manifest.json"
            written = builder.write_manifest(context, manifest_path)
            self.assertTrue(written.exists())
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("analysis_manifest", parsed)
            self.assertEqual(parsed["analysis_manifest"]["missing_data"], readiness.missing)

    def test_llm_input_only_uses_context_and_manifest_summary(self):
        with temp_repo() as root:
            populate_full_repo(root)
            task = make_task()
            gate = DataReadinessGate(root, runtime_config_path=REAL_RUNTIME_CONFIG)
            context = AnalysisContextBuilder(root).build(task=task, readiness=gate.check(task))
            llm_input = AnalysisContextBuilder.ensure_llm_input(context)
            self.assertIn("analysis_context", llm_input)
            self.assertIn("analysis_manifest_summary", llm_input)


if __name__ == "__main__":
    unittest.main()
