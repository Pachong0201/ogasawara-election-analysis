import json
import unittest

from runtime.host_retrieval import HostRetrievalBackend
from runtime.knowledge_loader import KnowledgeLoader
from tests.fixtures.helpers import temp_repo


class TestHostRetrievalBackend(unittest.TestCase):
    def test_missing_grade_downgrades_to_e_and_defaults_to_lead_only(self):
        backend = HostRetrievalBackend(
            records=[
                {
                    "query": "为什么甲乡出现异常？",
                    "title": "地方政治资料",
                    "url": "https://example.test/a",
                }
            ]
        )
        results = backend.search("为什么甲乡出现异常？")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_grade"], "E")
        self.assertEqual(results[0]["verification_status"], "lead_only")

    def test_only_matching_research_question_is_returned(self):
        backend = HostRetrievalBackend(
            records=[
                {
                    "query": "Q1",
                    "summary": "A",
                    "url": "https://example.test/a",
                    "source_grade": "B",
                },
                {
                    "query": "Q2",
                    "summary": "B",
                    "url": "https://example.test/b",
                    "source_grade": "B",
                },
            ]
        )
        results = backend.search("Q2")
        self.assertEqual([item["summary"] for item in results], ["B"])

    def test_jsonl_inbox_integrates_with_knowledge_loader_as_leads_only(self):
        with temp_repo() as root:
            inbox = root / "host-results.jsonl"
            question = "为什么 甲乡 出现 spatial_variance 异常？"
            inbox.write_text(
                json.dumps(
                    {
                        "query": question,
                        "title": "学术研究",
                        "summary": "该研究讨论地方组织与历史选举结构。",
                        "url": "https://example.test/study",
                        "source_id": "host_web",
                        "source_grade": "B",
                        "verification_status": "verified",
                        "evidence": "host verified source metadata",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            backend = HostRetrievalBackend(inbox_path=inbox)
            loader = KnowledgeLoader(
                root,
                retrieval_backend=backend,
                mode="online",
            )
            result = loader.load(
                "新竹縣",
                regions=["甲乡"],
                research_questions=[question],
                allow_online=True,
            )

            self.assertFalse(result["sufficient"])
            self.assertIn("retrieval", result)
            leads = result["retrieval"]["leads"]
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0]["source_grade"], "B")
            self.assertEqual(leads[0]["verification_status"], "verified")
            self.assertTrue(
                any("retrieval leads" in warning for warning in result["warnings"])
            )

            # Re-running the same research question must not duplicate the cache.
            loader.load(
                "新竹縣",
                regions=["甲乡"],
                research_questions=[question],
                allow_online=True,
            )

            cache = root / "cache" / "retrieval" / "新竹縣.jsonl"
            self.assertTrue(cache.exists())
            cached = [
                json.loads(line)
                for line in cache.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(cached), 1)
            self.assertEqual(cached[0]["query"], question)


if __name__ == "__main__":
    unittest.main()
