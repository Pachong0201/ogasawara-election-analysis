import datetime as dt
import unittest

import yaml

from runtime.election_loader import load_jsonl, write_jsonl
from runtime.knowledge_builder import KnowledgePromotionBuilder
from runtime.knowledge_loader import KnowledgeLoader
from tests.fixtures.helpers import temp_repo


COUNTY = "新竹縣"
QUESTION = "为什么 甲鄉 出现 candidate_residual 异常？"


def retrieval_path(root):
    return root / "cache" / "retrieval" / f"{COUNTY}.jsonl"


def lead(
    lead_id,
    grade="B",
    verification_status="verified",
    independence_key="",
    query=QUESTION,
):
    return {
        "lead_id": lead_id,
        "county": COUNTY,
        "query": query,
        "research_questions": [query],
        "title": f"來源 {lead_id}",
        "summary": "結構化測試來源。",
        "url": f"https://example.test/{lead_id}",
        "source_id": f"source-{lead_id}",
        "source_name": f"Source {lead_id}",
        "source_grade": grade,
        "verification_status": verification_status,
        "independence_key": independence_key,
        "published_at": dt.date.today().isoformat(),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "evidence": "fixture evidence",
    }


def historical_proposal(evidence_ids, claim_id="h1", text="歷史地方政治結構存在。"):
    return {
        "proposal_id": f"proposal-{claim_id}",
        "county": COUNTY,
        "target_type": "historical_claim",
        "research_questions": [QUESTION],
        "evidence_lead_ids": evidence_ids,
        "contradiction_check_completed": True,
        "scope_boundary": "仅用于解释1990-2000年甲鄉历史结构，不外推当前。",
        "target_record": {
            "claim_id": claim_id,
            "claim": text,
            "region": "甲鄉",
            "time_scope": "1990-2000",
        },
    }


class TestKnowledgePromotionBuilder(unittest.TestCase):
    def test_verified_b_source_promotes_historical_claim_and_builds_package(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B", independence_key="academic-1")])
            builder = KnowledgePromotionBuilder(root)
            result = builder.promote(historical_proposal(["b1"]))

            self.assertEqual(result["receipt"]["decision"], "promoted")
            self.assertEqual(len(result["receipt"]["record_sha256"]), 64)
            self.assertEqual(len(result["receipt"]["evidence_snapshot_sha256"]), 64)
            claim_path = root / "knowledge" / "historical" / COUNTY / "claims.jsonl"
            claims = load_jsonl(claim_path)
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]["layer_id"], "L2")
            self.assertEqual(claims[0]["source_grade"], "B")
            self.assertEqual(
                claims[0]["promotion_provenance"]["evidence_lead_ids"], ["b1"]
            )

            package = root / "knowledge" / "counties" / COUNTY
            self.assertTrue((package / "package_manifest.yaml").exists())
            self.assertTrue((package / "evidence_index.jsonl").exists())
            self.assertTrue((package / "political_ecology.md").exists())
            manifest = yaml.safe_load(
                (package / "package_manifest.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["counts"]["historical_claim"], 1)
            self.assertTrue(
                manifest["rules"]["generated_files_are_indexes_not_new_facts"]
            )

    def test_single_c_source_is_rejected(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("c1", grade="C", independence_key="media-1")])
            result = KnowledgePromotionBuilder(root).promote(
                historical_proposal(["c1"])
            )
            self.assertEqual(result["receipt"]["decision"], "rejected")
            self.assertTrue(
                any("requires at least two" in reason for reason in result["receipt"]["reasons"])
            )
            self.assertFalse(
                (root / "knowledge" / "historical" / COUNTY / "claims.jsonl").exists()
            )

    def test_two_independent_verified_c_sources_can_promote(self):
        with temp_repo() as root:
            write_jsonl(
                retrieval_path(root),
                [
                    lead("c1", grade="C", independence_key="media-1"),
                    lead("c2", grade="C", independence_key="media-2"),
                ],
            )
            proposal = {
                "proposal_id": "relationship-1",
                "county": COUNTY,
                "target_type": "local_relationship",
                "research_questions": [QUESTION],
                "evidence_lead_ids": ["c1", "c2"],
                "contradiction_check_completed": True,
                "target_record": {
                    "relationship_id": "r1",
                    "subject": "甲人物",
                    "object": "甲組織",
                    "relationship_type": "organization_membership",
                    "region": "甲鄉",
                    "time_scope": "2018-2022",
                    "current_status": "historical_only",
                },
            }
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "promoted")
            self.assertEqual(result["receipt"]["independent_source_count"], 2)
            records = load_jsonl(
                root / "knowledge" / "local" / COUNTY / "relationships.jsonl"
            )
            self.assertEqual(records[0]["source_grade"], "C")
            self.assertEqual(records[0]["independent_source_count"], 2)
            self.assertEqual(
                records[0]["structural_use"], "historical_background_only"
            )

    def test_two_c_sources_without_explicit_independence_keys_are_rejected(self):
        with temp_repo() as root:
            write_jsonl(
                retrieval_path(root),
                [lead("c1", grade="C"), lead("c2", grade="C")],
            )
            result = KnowledgePromotionBuilder(root).promote(
                historical_proposal(["c1", "c2"])
            )
            self.assertEqual(result["receipt"]["decision"], "rejected")
            self.assertTrue(
                any("independence_key" in reason for reason in result["receipt"]["reasons"])
            )

    def test_d_or_e_only_evidence_is_rejected(self):
        with temp_repo() as root:
            write_jsonl(
                retrieval_path(root),
                [
                    lead("d1", grade="D", independence_key="campaign"),
                    lead("e1", grade="E", independence_key="social"),
                ],
            )
            result = KnowledgePromotionBuilder(root).promote(
                historical_proposal(["d1", "e1"])
            )
            self.assertEqual(result["receipt"]["decision"], "rejected")

    def test_verified_contradiction_requires_review(self):
        with temp_repo() as root:
            write_jsonl(
                retrieval_path(root),
                [
                    lead("b1", grade="B", independence_key="academic-1"),
                    lead("counter", grade="C", independence_key="media-1"),
                ],
            )
            proposal = historical_proposal(["b1"])
            proposal["contradictory_lead_ids"] = ["counter"]
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "requires_review")
            self.assertTrue(
                any("contradictory evidence" in reason for reason in result["receipt"]["reasons"])
            )

    def test_active_relationship_requires_recent_verification(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B")])
            proposal = {
                "proposal_id": "active-r1",
                "county": COUNTY,
                "target_type": "local_relationship",
                "research_questions": [QUESTION],
                "evidence_lead_ids": ["b1"],
                "contradiction_check_completed": True,
                "target_record": {
                    "relationship_id": "active-r1",
                    "subject": "甲人物",
                    "object": "乙人物",
                    "relationship_type": "public_endorsement",
                    "region": "甲鄉",
                    "time_scope": "2010-2011",
                    "current_status": "active_verified",
                    "last_verified_at": "2011-01-01",
                },
            }
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "rejected")
            self.assertTrue(
                any("within five years" in reason for reason in result["receipt"]["reasons"])
            )

    def test_recent_active_relationship_is_promoted_with_verification_evidence(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("a1", grade="A")])
            today = dt.date.today().isoformat()
            proposal = {
                "proposal_id": "active-r2",
                "county": COUNTY,
                "target_type": "local_relationship",
                "research_questions": [QUESTION],
                "evidence_lead_ids": ["a1"],
                "contradiction_check_completed": True,
                "target_record": {
                    "relationship_id": "active-r2",
                    "subject": "甲人物",
                    "object": "乙人物",
                    "relationship_type": "public_endorsement",
                    "region": "甲鄉",
                    "time_scope": f"{dt.date.today().year}",
                    "current_status": "active_verified",
                    "last_verified_at": today,
                },
            }
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "promoted")
            record = load_jsonl(
                root / "knowledge" / "local" / COUNTY / "relationships.jsonl"
            )[0]
            self.assertEqual(
                record["structural_use"], "can_support_current_interpretation"
            )
            self.assertEqual(len(record["verification_evidence"]), 1)

            local = KnowledgeLoader(root, mode="offline").load_local_knowledge(
                COUNTY,
                regions=["甲鄉"],
                research_questions=[QUESTION],
            )
            self.assertTrue(local["sufficient"])
            self.assertEqual(local["current_evidence_count"], 1)
            self.assertTrue(local["question_covered"])

    def test_missing_structured_fields_is_rejected_without_inference(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B")])
            proposal = {
                "proposal_id": "bad-r",
                "county": COUNTY,
                "target_type": "local_relationship",
                "research_questions": [QUESTION],
                "evidence_lead_ids": ["b1"],
                "contradiction_check_completed": True,
                "target_record": {
                    "relationship_id": "bad-r",
                    "time_scope": "2026",
                    "current_status": "active_verified",
                    "last_verified_at": dt.date.today().isoformat(),
                },
            }
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "rejected")
            self.assertTrue(
                any("subject" in reason and "object" in reason for reason in result["receipt"]["reasons"])
            )

    def test_idempotent_repromotion_does_not_duplicate_record(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B")])
            builder = KnowledgePromotionBuilder(root)
            proposal = historical_proposal(["b1"])
            first = builder.promote(proposal)
            second = builder.promote(proposal)
            self.assertEqual(first["receipt"]["decision"], "promoted")
            self.assertEqual(second["receipt"]["decision"], "promoted")
            records = load_jsonl(
                root / "knowledge" / "historical" / COUNTY / "claims.jsonl"
            )
            self.assertEqual(len(records), 1)
            self.assertTrue(
                any("idempotent" in warning for warning in second["receipt"]["warnings"])
            )

    def test_conflicting_same_id_without_newer_verification_requires_review(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B")])
            builder = KnowledgePromotionBuilder(root)
            builder.promote(historical_proposal(["b1"], text="原始歷史敘述。"))
            result = builder.promote(
                historical_proposal(["b1"], text="互相衝突的新敘述。")
            )
            self.assertEqual(result["receipt"]["decision"], "requires_review")
            records = load_jsonl(
                root / "knowledge" / "historical" / COUNTY / "claims.jsonl"
            )
            self.assertEqual(records[0]["claim"], "原始歷史敘述。")

    def test_unpromoted_leads_appear_as_unresolved_questions(self):
        with temp_repo() as root:
            unused_question = "为什么 乙鄉 出现 spatial_variance 异常？"
            write_jsonl(
                retrieval_path(root),
                [
                    lead("b1", grade="B"),
                    lead(
                        "unused",
                        grade="C",
                        independence_key="media-2",
                        query=unused_question,
                    ),
                ],
            )
            builder = KnowledgePromotionBuilder(root)
            builder.promote(historical_proposal(["b1"]))
            builder.build_county_package(COUNTY)

            unresolved = load_jsonl(
                root
                / "knowledge"
                / "counties"
                / COUNTY
                / "unresolved_questions.jsonl"
            )
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(unresolved[0]["query"], unused_question)
            self.assertEqual(unresolved[0]["lead_ids"], ["unused"])


    def test_missing_contradiction_check_is_rejected(self):
        with temp_repo() as root:
            write_jsonl(retrieval_path(root), [lead("b1", grade="B")])
            proposal = historical_proposal(["b1"])
            proposal.pop("contradiction_check_completed", None)
            result = KnowledgePromotionBuilder(root).promote(proposal)
            self.assertEqual(result["receipt"]["decision"], "rejected")
            self.assertTrue(
                any(
                    "contradiction_check_completed" in reason
                    for reason in result["receipt"]["reasons"]
                )
            )



if __name__ == "__main__":
    unittest.main()
