import unittest

from runtime.data_readiness import DataReadinessGate
from runtime.election_loader import ElectionLoader, election_file_path
from runtime.source_registry import SourceRegistry
from tests.fixtures.helpers import (
    AREA_A,
    AREA_B,
    DEFAULT_JURISDICTION,
    REAL_RUNTIME_CONFIG,
    election_records_for_year,
    make_task,
    populate_full_repo,
    temp_repo,
    write_election_records,
    write_geography,
    FixtureElectionDataSource,
)


class TestDataReadiness(unittest.TestCase):
    def _gate(self, root):
        return DataReadinessGate(root, runtime_config_path=REAL_RUNTIME_CONFIG)

    def test_insufficient_when_only_one_period(self):
        with temp_repo() as root:
            write_geography(root)
            write_election_records(root, "county_mayor", 2022, DEFAULT_JURISDICTION, election_records_for_year("county_mayor", 2022))
            report = self._gate(root).check(make_task())
            self.assertEqual(report.status, "INSUFFICIENT")
            self.assertTrue(any("historical_same_type" in item for item in report.missing))

    def test_ready_when_hard_and_preferred_data_present(self):
        with temp_repo() as root:
            populate_full_repo(root)
            report = self._gate(root).check(make_task())
            self.assertEqual(report.status, "READY")
            self.assertEqual(report.required["historical_same_type"]["found_count"], 3)
            self.assertEqual(report.required["presidential"]["found_count"], 3)
            self.assertTrue(report.required["township_level"]["satisfied"])
            self.assertTrue(report.required["current_candidate_list"]["satisfied"])

    def test_partial_when_legislator_missing(self):
        with temp_repo() as root:
            populate_full_repo(root, include_legislator=False)
            report = self._gate(root).check(make_task())
            self.assertEqual(report.status, "PARTIAL")
            self.assertTrue(any("regional_legislator" in warning for warning in report.warnings))

    def test_boundary_mismatch_blocks_swing(self):
        with temp_repo() as root:
            populate_full_repo(root, boundary_version="v1")
            # Rewrite one historical year with a different, unmapped boundary version.
            records = election_records_for_year("county_mayor", 2014, boundary_version="v2")
            write_election_records(root, "county_mayor", 2014, DEFAULT_JURISDICTION, records)
            report = self._gate(root).check(make_task())
            self.assertEqual(report.status, "INSUFFICIENT")
            self.assertFalse(report.required["boundary_compatibility"]["satisfied"])
            self.assertTrue(any("boundary_compatibility" in item for item in report.missing))

    def test_prepare_online_fills_missing_period_and_rechecks(self):
        with temp_repo() as root:
            populate_full_repo(root, include_legislator=True)
            # Remove 2022 county mayor local data to simulate a missing period.
            target = election_file_path(root, "county_mayor", 2022, DEFAULT_JURISDICTION)
            target.unlink()
            # Fixture adapter supplies the missing 2022 period.
            key = FixtureElectionDataSource.key("county_mayor", 2022, DEFAULT_JURISDICTION, "township_district")
            adapter = FixtureElectionDataSource({key: election_records_for_year("county_mayor", 2022, DEFAULT_JURISDICTION)})
            loader = ElectionLoader(root, source_registry=SourceRegistry(adapters=[adapter]), mode="online")
            gate = self._gate(root)
            initial = gate.check(make_task())
            self.assertEqual(initial.status, "INSUFFICIENT")
            prepared = gate.prepare(make_task(), loader=loader, allow_online=True)
            self.assertEqual(prepared.status, "READY")
            self.assertTrue(target.exists())
            self.assertEqual(len(adapter.calls), 1)

    def test_missing_current_candidates_is_insufficient(self):
        with temp_repo() as root:
            populate_full_repo(root, include_current_candidates=False)
            # No task candidates and no cache candidates.
            report = self._gate(root).check(make_task(candidates=[]))
            self.assertEqual(report.status, "INSUFFICIENT")
            self.assertTrue(any("current_candidate_list" in item for item in report.missing))


    def test_task_candidate_names_do_not_bypass_candidate_verification(self):
        with temp_repo() as root:
            populate_full_repo(root, include_current_candidates=False)
            report = self._gate(root).check(
                make_task(candidates=["甲候選人", "乙候選人"])
            )
            self.assertEqual(report.status, "INSUFFICIENT")
            self.assertFalse(report.required["current_candidate_list"]["satisfied"])
            self.assertTrue(
                any("unverified seeds" in warning for warning in report.warnings)
            )



if __name__ == "__main__":
    unittest.main()
