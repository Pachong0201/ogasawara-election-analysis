"""Regression gates for the v1.4 live campaign contract."""

import tempfile
import unittest
from pathlib import Path

from runtime.campaign_events import CampaignEventLoader
from runtime.campaign_state import CampaignStateBuilder, CampaignStateStore
from runtime.source_registry import FixtureBackend


AS_OF = "2026-09-23T12:00:00+08:00"
REGION = "測試縣"


def event(event_id="e1", date="2026-09-21", grade="A", verification="verified", **kwargs):
    row = dict(event_id=event_id, jurisdiction=REGION, date=date, claim_type="endorsement",
               speaker="地方人士", subject="候選人甲", claim_value="support",
               source_grade=grade, verification_status=verification,
               source="fixture", last_verified_at="2026-09-22", url=f"https://example.test/{event_id}")
    row.update(kwargs)
    return row


def poll(poll_id, date, pollster="P", method="CATI", wording="choice", **kwargs):
    return dict(poll_id=poll_id, field_end=date, publish_date=date, pollster=pollster,
                commissioner="C", method=method, sample_frame="adults", question_wording=wording,
                weighting="age", sampling="RDD", source_grade="A", verification_status="verified",
                candidate_support={"甲": 0.4 if poll_id == "p1" else 0.44}, **kwargs)


class LiveCampaignContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.loader = CampaignEventLoader(self.root)
        self.builder = CampaignStateBuilder(self.root, config={"campaign_state": {"persist_snapshots": False}})

    def build(self, events=None, candidates=None, polls=None, **kwargs):
        return self.builder.build(REGION, 2026, candidates or [], events or [], polls or [],
                                  as_of=AS_OF, persist=False, **kwargs)

    def test_twenty_day_event_only_in_thirty_day_window(self):
        row = event(date="2026-09-03", last_verified_at="2026-09-22")
        snapshot = self.build([row])
        self.assertEqual([snapshot["windows"][f"{n}d"]["event_count"] for n in (7, 14, 30)], [0, 0, 1])
        self.assertFalse(snapshot["campaign_change_trigger"])

    def test_window_boundary_and_future_verification(self):
        rows = [event(event_id="edge", date="2026-09-17"),
                event(event_id="outside", date="2026-09-16"),
                event(event_id="future-proof", last_verified_at="2026-09-24")]
        snapshot = self.build(rows)
        self.assertEqual(snapshot["windows"]["7d"]["event_count"], 2)
        self.assertEqual(snapshot["windows"]["7d"]["verified_trigger_event_count"], 1)

    def test_multi_source_dedup_retains_evidence_and_prefers_verified(self):
        a = event(grade="D", verification="campaign_claim", source="first")
        b = event(event_id="other-id", grade="A", source="second")
        result = self.loader.audit([a, b], REGION, AS_OF)
        self.assertEqual(result["deduplicated_count"], 1)
        self.assertEqual(result["events"][0]["source_grade"], "A")
        self.assertEqual(result["events"][0]["evidence_count"], 2)
        self.assertEqual({ref["reference"] for ref in result["events"][0]["evidence_refs"]},
                         {"https://example.test/e1", "https://example.test/other-id"})

    def test_opposing_evidence_is_quarantined(self):
        a, b = event(), event(event_id="e2")
        b["claim_value"] = "not_support"
        rows = self.loader.audit([a, b], REGION, AS_OF)
        self.assertEqual(len(rows["conflicts"]), 1)
        self.assertTrue(all(not row["usable_for_trigger"] for row in rows["events"]))
        self.assertFalse(self.build(rows["events"], event_conflicts=rows["conflicts"])["campaign_change_trigger"])

    def test_grade_gate_and_c_verification_and_de_not_independent(self):
        for grade, verification, expected in (
            ("A", "verified", True), ("B", "verified", True), ("C", "verified", True),
            ("C", "lead_only", False), ("D", "verified", False), ("E", "verified", False),
        ):
            with self.subTest(grade=grade, verification=verification):
                row = self.loader.audit([event(grade=grade, verification=verification)], REGION, AS_OF)["events"][0]
                self.assertEqual(self.build([row])["campaign_change_trigger"], expected)

    def test_snapshot_sequence_and_unchanged_run(self):
        store = CampaignStateStore(self.root)
        canonical = self.loader.audit([event()], REGION, AS_OF)["events"]
        previous = self.build(canonical, [{"name": "甲"}], [poll("p1", "2026-09-20")])
        first_path = store.save(REGION, previous)
        second = self.build(self.loader.audit([event(), event(event_id="e2")], REGION, AS_OF)["events"],
                            [{"name": "甲"}, {"name": "乙"}],
                            [poll("p1", "2026-09-20"), poll("p2", "2026-09-22")])
        # Supplied source IDs alone are not distinct underlying events.
        self.assertEqual(second["snapshot_delta"]["new_event_ids"], [])
        self.assertIn({"change": "candidate_added", "candidate_key": "乙"},
                      second["snapshot_delta"]["candidate_changes"])
        self.assertEqual(second["snapshot_delta"]["new_poll_ids"], ["p2"])
        second_path = store.save(REGION, second)
        self.assertNotEqual(first_path, second_path)
        self.assertTrue(first_path.exists())
        third = self.build(canonical, [{"name": "甲"}, {"name": "乙"}],
                           [poll("p1", "2026-09-20"), poll("p2", "2026-09-22")])
        self.assertEqual(third["snapshot_delta"]["change_status"], "unchanged")

    def test_missing_candidate_cache_is_uncertain_not_withdrawal(self):
        store = CampaignStateStore(self.root)
        store.save(REGION, self.build(candidates=[{"candidate_id": "甲"}]))
        result = self.build()
        self.assertEqual(result["snapshot_delta"]["candidate_changes"], [])
        self.assertEqual(result["snapshot_delta"]["change_status"], "uncertain")

    def test_new_event_stable_id_and_future_exclusion(self):
        old = self.loader.audit([event()], REGION, AS_OF)["events"]
        self.builder.store.save(REGION, self.build(old))
        new = event(event_id="new", date="2026-09-22", subject="候選人乙")
        rows = self.loader.audit([event(), new, event(event_id="future", date="2026-09-24")],
                                 REGION, AS_OF)
        self.assertEqual(rows["excluded_future"], 1)
        delta = self.build(rows["events"])["snapshot_delta"]
        self.assertEqual(len(delta["new_event_ids"]), 1)
        self.assertEqual(delta["dimension_changes"]["organization"], delta["new_event_ids"])

    def test_same_series_signature_gates(self):
        baseline = poll("p1", "2026-09-10")
        for changes, expected in (
            ({}, 1), ({"pollster": "Q"}, 0), ({"method": "online"}, 0),
            ({"question_wording": "different"}, 0), ({"weighting": "none"}, 0),
        ):
            with self.subTest(changes=changes):
                later = {**poll("p2", "2026-09-22"), **changes}
                result = self.builder.same_series_poll_changes([baseline, later])
                self.assertEqual(len(result), expected)
                if result:
                    self.assertEqual(result[0]["candidate_deltas"], {"甲": 4.0})
                    self.assertIn("series_id", result[0])

    def test_stale_poll_does_not_calibrate_or_trigger(self):
        older = poll("p1", "2026-05-01")
        later = poll("p2", "2026-06-01")
        snapshot = self.build(polls=[older, later])
        self.assertEqual(snapshot["same_series_poll_changes"], [])
        self.assertEqual(snapshot["campaign_state_status"], "insufficient_current_data")

    def test_offline_and_lead_only_are_insufficient(self):
        self.builder.mode = "offline"
        self.assertEqual(self.build([event()])["campaign_state_status"], "insufficient_current_data")
        self.builder.mode = "auto"
        result = self.build(retrieval_leads=[{"url": "https://example.test", "verification_status": "lead_only"}],
                            online_expected=True)
        self.assertEqual(result["campaign_state_status"], "insufficient_current_data")
        self.assertFalse(result["campaign_change_trigger"])

    def test_search_covers_current_topics_and_stays_lead_only(self):
        self.builder.retrieval_backend = FixtureBackend(default=[{
            "url": "https://example.test", "source_grade": "A", "verification_status": "verified"
        }])
        queries = self.builder.build_retrieval_queries(REGION, 2026)
        self.assertGreaterEqual(len(queries), 5)
        leads, warnings = self.builder.retrieve_current_leads(REGION, 2026, True)
        self.assertFalse(warnings)
        self.assertTrue(all(row["verification_status"] == "lead_only" for row in leads))
