"""Trailing baseline stays frozen, but an unprofitable comparator cannot veto a
robust, independently profitable alternative or become an invalid fallback.

Unit tests isolate the production selector. No backtest or live orders are run.
"""
import ast
import unittest
from typing import Any
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "strategy_lab/v3_policy_research.py"
FUNCTIONS = {
    "_measured_trailing_rows",
    "_metric",
    "_trailing_row_eligible",
    "_trailing_quality_tuple",
    "_individual_trailing_select",
}


class TrailingBaselineComparatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(POLICY.read_text(encoding="utf-8-sig"))
        nodes = [n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
        assert {n.name for n in nodes} == FUNCTIONS
        namespace = {
            "Any": Any,
            "_individual_trailing_contract": lambda: {
                "baseline": {"start_pct": 3.0, "callback_pct": 1.5},
                "selection_neighbor_positive_ratio_min": 0.70,
            },
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]),
                     str(POLICY), "exec"), namespace)
        cls.select = staticmethod(namespace["_individual_trailing_select"])

    @staticmethod
    def row(start, callback, wf, stress, *, trades=100,
            payoff=1.5, mdd=10.0, giveback=0.5, holding=10.0):
        return {
            "status": "MEASURED",
            "start_pct": start, "callback_pct": callback,
            "evidence": {
                "trades": trades,
                "walk_forward_oos_expectancy_pct": wf,
                "cost_stress_2x_expectancy_pct": stress,
                "payoff_ratio": payoff,
                "normalized_mdd_pct_points": mdd,
                "avg_mfe_giveback_pct": giveback,
                "avg_holding_time_hours": holding,
            },
        }

    def decide(self, baseline=None, alternatives=()):
        rows = list(alternatives)
        if baseline is not None:
            rows.insert(0, baseline)
        return self.select({
            "mode": "INDIVIDUAL_STRATEGY_TRAILING", "action": "SELECT",
            "minimum_trades": 40,
            "evaluations": {
                f"{x['start_pct']:.4f}|{x['callback_pct']:.4f}": x
                for x in rows
            },
        })

    def positive_neighborhood(self):
        return [
            self.row(4.0, 1.0, 0.10, 0.08, payoff=2.0),
            self.row(4.5, 1.0, 0.09, 0.06, payoff=1.9),
            self.row(4.0, 1.5, 0.08, 0.07, payoff=1.8),
            self.row(4.5, 1.5, 0.09, 0.06, payoff=1.8),
        ]

    def test_negative_baseline_does_not_veto_robust_positive_candidate(self):
        baseline = self.row(3.0, 1.5, -0.04, 0.10, payoff=1.0)
        result = self.decide(baseline, self.positive_neighborhood())
        self.assertEqual(result["status"], "SELECTED")
        self.assertNotEqual(result["selected"], {"start_pct": 3.0, "callback_pct": 1.5})
        self.assertGreater(result["evidence"]["walk_forward_oos_expectancy_pct"], 0)
        self.assertGreater(result["evidence"]["cost_stress_2x_expectancy_pct"], 0)
        self.assertGreaterEqual(result["positive_neighbor_ratio"], 0.70)
        self.assertFalse(result["live_effect"])

    def test_cost_stress_failed_baseline_can_be_compared_not_promoted(self):
        baseline = self.row(3.0, 1.5, 0.01, -0.02, payoff=1.0)
        result = self.decide(baseline, self.positive_neighborhood())
        self.assertEqual(result["status"], "SELECTED")
        self.assertFalse(result["fallback"])

    def test_missing_baseline_stays_incomplete(self):
        result = self.decide(alternatives=self.positive_neighborhood())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["reason"], "BASELINE_3_0_1_5_NOT_MEASURED")
        self.assertIsNone(result["selected"])

    def test_missing_baseline_oos_evidence_stays_incomplete(self):
        baseline = self.row(3.0, 1.5, None, 0.05)
        result = self.decide(baseline, self.positive_neighborhood())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["reason"], "BASELINE_3_0_1_5_WF_EVIDENCE_MISSING")

    def test_unprofitable_baseline_without_valid_alternative_is_not_fallback(self):
        baseline = self.row(3.0, 1.5, -0.04, -0.02)
        result = self.decide(baseline, [self.row(4.0, 1.0, -0.01, -0.01)])
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["reason"], "BASELINE_INELIGIBLE_NO_ROBUST_VALID_ALTERNATIVE")
        self.assertIsNone(result["selected"])
        self.assertFalse(result["live_effect"])

    def test_positive_oos_but_negative_2x_cost_cannot_be_selected(self):
        baseline = self.row(3.0, 1.5, -0.05, 0.10)
        result = self.decide(baseline, [
            self.row(4.0, 1.0, 0.15, -0.02),
            self.row(4.5, 1.0, 0.13, -0.01),
            self.row(4.0, 1.5, 0.12, -0.01),
        ])
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIsNone(result["selected"])

    def test_profitable_baseline_retains_existing_fallback(self):
        baseline = self.row(3.0, 1.5, 0.03, 0.01)
        result = self.decide(baseline)
        self.assertEqual(result["status"], "FALLBACK")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["selected"], {"start_pct": 3.0, "callback_pct": 1.5})
        self.assertFalse(result["live_effect"])

    def test_unstable_alternative_cannot_bypass_neighborhood_check(self):
        baseline = self.row(3.0, 1.5, -0.05, 0.10)
        result = self.decide(baseline, [self.row(4.0, 1.0, 0.15, 0.04)])
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIsNone(result["selected"])


if __name__ == "__main__":
    unittest.main()
