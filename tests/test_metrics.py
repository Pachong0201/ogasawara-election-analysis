import unittest

from runtime.metrics import (
    candidate_residual,
    electoral_swing,
    geographic_concentration,
    local_swing,
    neighbor_divergence,
    spatial_variance,
    split_ticket_residual,
)


class TestMetrics(unittest.TestCase):
    def test_electoral_swing(self):
        result = electoral_swing(0.55, 0.50, region="甲鄉")
        self.assertAlmostEqual(result.residual, 0.05)
        self.assertEqual(result.metric, "electoral_swing")
        self.assertTrue(result.local_explanation_required is False)  # 5 pp below 8 pp threshold

    def test_local_swing(self):
        result = local_swing(0.05, 0.02, region="甲鄉")
        self.assertAlmostEqual(result.residual, 0.03)
        self.assertEqual(result.baseline_method, "region_mean_swing")

    def test_split_ticket_residual_field_is_residual_not_personal_vote(self):
        result = split_ticket_residual(0.60, 0.40)
        payload = result.to_dict()
        self.assertEqual(payload["residual"], 0.2)
        self.assertNotIn("personal_vote", payload)
        self.assertNotIn("personal_vote_share", payload)

    def test_candidate_residual_requires_baseline_method(self):
        with self.assertRaises(ValueError):
            candidate_residual(0.55, 0.33, "")
        result = candidate_residual(0.55, 0.33, "same_day_party_list")
        self.assertAlmostEqual(result.residual, 0.22)
        self.assertEqual(result.baseline_method, "same_day_party_list")

    def test_spatial_variance_supports_multiple_methods(self):
        sd = spatial_variance([0.30, 0.50, 0.70], method="standard_deviation")
        cv = spatial_variance([0.30, 0.50, 0.70], method="coefficient_of_variation")
        iqr = spatial_variance([0.30, 0.50, 0.70], method="iqr")
        self.assertEqual(sd.metric_method, "standard_deviation")
        self.assertEqual(cv.metric_method, "coefficient_of_variation")
        self.assertEqual(iqr.metric_method, "iqr")

    def test_neighbor_divergence_uses_percentage_points(self):
        result = neighbor_divergence(0.55, 0.35, region="甲鄉", neighbor="乙鄉")
        self.assertAlmostEqual(result.residual, 0.2)
        self.assertEqual(result.threshold_status, "strong_trigger")

    def test_geographic_concentration_reports_excess(self):
        result = geographic_concentration({"甲鄉": 900, "乙鄉": 100}, {"甲鄉": 200, "乙鄉": 1800}, top_n=1)
        self.assertAlmostEqual(result.details["excess_concentration"], 0.8)
        self.assertEqual(result.metric_method, "excess_concentration")


if __name__ == "__main__":
    unittest.main()
