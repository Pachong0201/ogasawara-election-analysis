import unittest

from runtime.election_loader import ElectionLoader, election_file_path, load_jsonl
from runtime.source_registry import FixtureBackend, OfflineBackend, OfflineRetrievalError, SourceRegistry
from tests.fixtures.helpers import (
    DEFAULT_JURISDICTION,
    FixtureElectionDataSource,
    election_records_for_year,
    temp_repo,
    write_election_records,
)


class TestElectionLoader(unittest.TestCase):
    def _fixture_source(self, year=2022):
        records = election_records_for_year("county_mayor", year, DEFAULT_JURISDICTION)
        key = FixtureElectionDataSource.key("county_mayor", year, DEFAULT_JURISDICTION, "township_district")
        return FixtureElectionDataSource({key: records})

    def test_online_fills_missing_period_and_persists(self):
        with temp_repo() as root:
            adapter = self._fixture_source(2022)
            registry = SourceRegistry(adapters=[adapter])
            loader = ElectionLoader(root, source_registry=registry, mode="online")
            result = loader.load_election("county_mayor", 2022, DEFAULT_JURISDICTION, "township_district")
            self.assertEqual(result.status, "filled")
            self.assertTrue(result.persisted)
            self.assertEqual(len(result.records), 4)
            self.assertTrue(election_file_path(root, "county_mayor", 2022, DEFAULT_JURISDICTION).exists())
            self.assertEqual(len(adapter.calls), 1)

            # The second call reads the local file and must not trigger the network adapter.
            second = loader.load_election("county_mayor", 2022, DEFAULT_JURISDICTION, "township_district")
            self.assertEqual(second.status, "complete")
            self.assertEqual(len(adapter.calls), 1)

    def test_offline_does_not_fetch_and_returns_missing(self):
        with temp_repo() as root:
            adapter = self._fixture_source(2022)
            registry = SourceRegistry(adapters=[adapter])
            loader = ElectionLoader(root, source_registry=registry, mode="offline")
            result = loader.load_election("county_mayor", 2022, DEFAULT_JURISDICTION, "township_district")
            self.assertEqual(result.status, "missing")
            self.assertEqual(len(adapter.calls), 0)
            self.assertFalse(election_file_path(root, "county_mayor", 2022, DEFAULT_JURISDICTION).exists())

    def test_load_polls_validates_schema_and_moe_applicability(self):
        with temp_repo() as root:
            from runtime.election_loader import write_jsonl

            path = root / "cache" / "polls" / "poll.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "pollster": "fixture",
                        "commissioner": "fixture",
                        "method": "online_closed",
                        "sample_size": 1000,
                        "field_start": "2026-01-01",
                        "field_end": "2026-01-02",
                        "publish_date": "2026-01-03",
                        "moe": None,
                        "moe_applicable": False,
                        "undecided": 0.20,
                        "source": "fixture",
                        "source_grade": "C",
                    },
                    {"pollster": "bad"},
                ],
            )
            loader = ElectionLoader(root, mode="offline")
            result = loader.load_polls()
            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(len(result["invalid"]), 1)
            self.assertFalse(result["invalid"][0]["validation_status"] == "passed")

    def test_retrieval_backends_are_injectable_and_offline_fails_closed(self):
        backend = FixtureBackend({"query": [{"title": "fixture lead"}]})
        self.assertEqual(backend.search("query")[0]["title"], "fixture lead")
        with self.assertRaises(OfflineRetrievalError):
            OfflineBackend().search("query")

    def test_local_validated_data_is_not_refetched(self):
        with temp_repo() as root:
            write_election_records(root, "county_mayor", 2022, DEFAULT_JURISDICTION, election_records_for_year("county_mayor", 2022))
            adapter = self._fixture_source(2022)
            loader = ElectionLoader(root, source_registry=SourceRegistry(adapters=[adapter]), mode="online")
            result = loader.load_election("county_mayor", 2022, DEFAULT_JURISDICTION, "township_district")
            self.assertEqual(result.status, "complete")
            self.assertEqual(len(adapter.calls), 0)


    def test_generated_record_ids_preserve_multiple_townships(self):
        with temp_repo() as root:
            records = election_records_for_year("county_mayor", 2022, DEFAULT_JURISDICTION)
            for record in records:
                record.pop("record_id", None)

            key = FixtureElectionDataSource.key(
                "county_mayor", 2022, DEFAULT_JURISDICTION, "township_district"
            )
            adapter = FixtureElectionDataSource({key: records})
            loader = ElectionLoader(
                root,
                source_registry=SourceRegistry(adapters=[adapter]),
                mode="online",
            )
            result = loader.load_election(
                "county_mayor", 2022, DEFAULT_JURISDICTION, "township_district"
            )
            self.assertEqual(result.status, "filled")

            path = election_file_path(root, "county_mayor", 2022, DEFAULT_JURISDICTION)
            persisted = load_jsonl(path)
            self.assertEqual(len(persisted), 4)
            self.assertEqual(len({record["record_id"] for record in persisted}), 4)
            self.assertEqual(
                {record["jurisdiction"] for record in persisted},
                {"甲鄉", "乙鄉"},
            )



if __name__ == "__main__":
    unittest.main()
