import tempfile
import unittest
from pathlib import Path

from runtime.campaign_events import CampaignEventLoader
from runtime.election_loader import write_jsonl


class CampaignEventLoaderTests(unittest.TestCase):
    def test_normalize_dedupe_and_conflict_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "cache" / "events" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(
                path,
                [
                    {
                        "event_id": "e1",
                        "jurisdiction": "新竹縣",
                        "date": "2026-09-20",
                        "claim_type": "endorsement",
                        "speaker": "甲",
                        "subject": "乙",
                        "claim_value": "support",
                        "source_grade": "C",
                        "verification_status": "verified",
                        "url": "https://example.test/1",
                    },
                    {
                        "event_id": "e1-duplicate",
                        "jurisdiction": "新竹縣",
                        "date": "2026-09-20",
                        "claim_type": "endorsement",
                        "speaker": "甲",
                        "subject": "乙",
                        "claim_value": "support",
                        "source_grade": "D",
                        "verification_status": "campaign_claim",
                        "url": "https://example.test/1",
                    },
                    {
                        "event_id": "e2",
                        "jurisdiction": "新竹縣",
                        "date": "2026-09-20",
                        "claim_type": "endorsement",
                        "speaker": "甲",
                        "subject": "乙",
                        "claim_value": "not_support",
                        "source_grade": "C",
                        "verification_status": "verified",
                        "url": "https://example.test/2",
                    },
                ],
            )
            result = CampaignEventLoader(root).load_cache("新竹縣")
            self.assertEqual(result["raw_count"], 3)
            self.assertEqual(result["deduplicated_count"], 2)
            self.assertEqual(len(result["conflicts"]), 1)
            self.assertTrue(all(row["layer_id"] == "L4" for row in result["events"]))
            self.assertTrue(all(not row["usable_for_trigger"] for row in result["events"]))
            self.assertTrue(all(row["evidence_status"] == "requires_review" for row in result["events"]))

    def test_foreign_jurisdiction_and_missing_date_are_dropped(self):
        loader = CampaignEventLoader(Path("."))
        rows = [
            {"jurisdiction": "臺北市", "date": "2026-09-20", "claim_type": "policy"},
            {"jurisdiction": "新竹縣", "claim_type": "policy"},
        ]
        normalized = [loader.normalize(row, "新竹縣") for row in rows]
        self.assertEqual(normalized, [None, None])


if __name__ == "__main__":
    unittest.main()
