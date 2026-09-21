import datetime as dt
import unittest

from runtime.freshness import (
    historical_relationship_is_current,
    is_fresh,
    needs_revalidation,
    poll_moe_applicable,
)
from runtime.knowledge_loader import KnowledgeLoader
from tests.fixtures.helpers import temp_repo, write_jsonl


class TestFreshness(unittest.TestCase):
    def test_permanent_historical_election_is_fresh(self):
        record = {"record_type": "historical_election", "last_verified_at": "1996-01-01"}
        self.assertTrue(is_fresh(record))

    def test_30_day_old_poll_is_stale(self):
        verified = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        record = {"record_type": "poll", "last_verified_at": verified}
        self.assertFalse(is_fresh(record))
        self.assertTrue(needs_revalidation(record))

    def test_closed_online_poll_does_not_apply_traditional_moe(self):
        record = {"method": "online_closed", "moe": None, "moe_applicable": False}
        self.assertFalse(poll_moe_applicable(record))

    def test_1996_relationship_is_not_current(self):
        record = {
            "relationship_id": "r1",
            "record_type": "local_relationship",
            "time_scope": "1996-2000",
            "current_status": "active_verified",
            "source_grade": "B",
        }
        self.assertFalse(historical_relationship_is_current(record))
        self.assertTrue(needs_revalidation(record, current_use=True))

    def test_knowledge_loader_downgrades_stale_active_relationship(self):
        with temp_repo() as root:
            path = root / "knowledge" / "local" / "新竹縣" / "relationships.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "relationship_id": "r1",
                        "record_type": "local_relationship",
                        "region": "甲鄉",
                        "time_scope": "1996-2000",
                        "current_status": "active_verified",
                        "source_grade": "B",
                    }
                ],
            )
            loader = KnowledgeLoader(root, mode="offline")
            result = loader.load_local_knowledge("新竹縣")
            self.assertEqual(result["relationships"][0]["current_status"], "historical_only")


if __name__ == "__main__":
    unittest.main()
