import unittest

from runtime.matrix_builder import build_cross_level_matrix, build_historical_matrix, build_same_day_matrix
from tests.fixtures.helpers import AREA_A, AREA_B


class TestMatrixBuilder(unittest.TestCase):
    def test_historical_matrix_shape(self):
        records = [
            {"jurisdiction": AREA_A, "election_year": 2022, "candidate_name": "甲", "vote_share": 0.55},
            {"jurisdiction": AREA_A, "election_year": 2022, "candidate_name": "乙", "vote_share": 0.45},
        ]
        matrix = build_historical_matrix(records)
        self.assertEqual(matrix["regions"][AREA_A]["2022"]["甲"], 0.55)
        self.assertEqual(matrix["regions"][AREA_A]["2022"]["乙"], 0.45)

    def test_cross_level_matrix_keeps_election_types_separate(self):
        matrix = build_cross_level_matrix(
            {
                "county_mayor": [{"jurisdiction": AREA_A, "election_year": 2022, "candidate_name": "甲", "vote_share": 0.55, "level": "township_district"}],
                "president": [{"jurisdiction": AREA_A, "election_year": 2024, "candidate_name": "甲", "vote_share": 0.35, "level": "township_district"}],
            }
        )
        self.assertEqual(matrix["regions"][AREA_A]["county_mayor"]["2022"]["甲"], 0.55)
        self.assertEqual(matrix["regions"][AREA_A]["president"]["2024"]["甲"], 0.35)

    def test_same_day_matrix_residual(self):
        rows = build_same_day_matrix(
            [{"jurisdiction": AREA_A, "party": "甲黨", "vote_share": 0.60}],
            [{"jurisdiction": AREA_A, "party": "甲黨", "vote_share": 0.40}],
        )["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["split_ticket_residual"], 0.2)
        self.assertIn("legislator_vote_share", rows[0])


if __name__ == "__main__":
    unittest.main()
