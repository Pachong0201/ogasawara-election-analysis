import io
import json
import unittest
from contextlib import redirect_stdout

from runtime.cli import main
from runtime.election_loader import load_jsonl, write_jsonl
from tests.fixtures.helpers import temp_repo


COUNTY = "新竹縣"
QUESTION = "为什么 甲鄉 出现 candidate_residual 异常？"


def setup_files(root):
    retrieval = root / "cache" / "retrieval" / f"{COUNTY}.jsonl"
    write_jsonl(
        retrieval,
        [
            {
                "lead_id": "b1",
                "county": COUNTY,
                "query": QUESTION,
                "research_questions": [QUESTION],
                "title": "研究資料",
                "summary": "測試來源",
                "url": "https://example.test/b1",
                "source_id": "fixture-academic",
                "source_name": "Fixture Academic",
                "source_grade": "B",
                "verification_status": "verified",
                "independence_key": "fixture-academic",
                "evidence": "fixture evidence",
            }
        ],
    )
    proposal = root / "proposal.jsonl"
    proposal.write_text(
        json.dumps(
            {
                "proposal_id": "cli-h1",
                "county": COUNTY,
                "target_type": "historical_claim",
                "research_questions": [QUESTION],
                "evidence_lead_ids": ["b1"],
                "contradiction_check_completed": True,
                "contradictory_lead_ids": [],
                "scope_boundary": "仅用于历史背景，不外推当前。",
                "target_record": {
                    "claim_id": "cli-h1",
                    "claim": "歷史地方政治結構測試。",
                    "region": "甲鄉",
                    "time_scope": "1990-2000",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return proposal


class TestKnowledgeCLI(unittest.TestCase):
    def run_cli(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(args)
        return code, output.getvalue()

    def test_dry_run_validates_without_writing_knowledge(self):
        with temp_repo() as root:
            proposal = setup_files(root)
            code, output = self.run_cli(
                [
                    "--repo-root",
                    str(root),
                    "knowledge-promote",
                    "--county",
                    COUNTY,
                    "--proposal-inbox",
                    str(proposal),
                    "--dry-run",
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["counts"]["dry_run_pass"], 1)
            self.assertFalse(
                (root / "knowledge" / "historical" / COUNTY / "claims.jsonl").exists()
            )

    def test_promote_then_build_creates_authoritative_record_and_package(self):
        with temp_repo() as root:
            proposal = setup_files(root)
            code, output = self.run_cli(
                [
                    "--repo-root",
                    str(root),
                    "knowledge-promote",
                    "--county",
                    COUNTY,
                    "--proposal-inbox",
                    str(proposal),
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["counts"]["promoted"], 1)

            claims = load_jsonl(
                root / "knowledge" / "historical" / COUNTY / "claims.jsonl"
            )
            self.assertEqual(len(claims), 1)

            package = root / "knowledge" / "counties" / COUNTY
            self.assertTrue((package / "package_manifest.yaml").exists())

            code, output = self.run_cli(
                [
                    "--repo-root",
                    str(root),
                    "knowledge-build",
                    "--county",
                    COUNTY,
                ]
            )
            self.assertEqual(code, 0)
            built = json.loads(output)
            self.assertEqual(built["county"], COUNTY)
            self.assertEqual(
                built["manifest"]["counts"]["historical_claim"], 1
            )

    def test_rejected_proposal_returns_nonzero(self):
        with temp_repo() as root:
            proposal = setup_files(root)
            payload = json.loads(proposal.read_text(encoding="utf-8").strip())
            payload["contradiction_check_completed"] = False
            proposal.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            code, output = self.run_cli(
                [
                    "--repo-root",
                    str(root),
                    "knowledge-promote",
                    "--county",
                    COUNTY,
                    "--proposal-inbox",
                    str(proposal),
                ]
            )
            self.assertEqual(code, 2)
            result = json.loads(output)
            self.assertEqual(result["counts"]["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
