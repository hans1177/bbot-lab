"""A past baseline-ineligible trailing result is recoverable only with frozen
measured evidence and a genuinely qualified alternative (no trial replay).
"""
import ast
import unittest
from typing import Any
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def isolated_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    selected = [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == names
    namespace["Any"] = Any
    exec(compile(ast.Module(body=selected, type_ignores=[]),
                 str(path), "exec"), namespace)
    return namespace


class FrozenTrailingRecoveryTests(unittest.TestCase):
    @staticmethod
    def row(start, callback, wf, stress, *, payoff=1.5):
        return {
            "status": "MEASURED", "start_pct": start, "callback_pct": callback,
            "evidence": {
                "trades": 500,
                "walk_forward_oos_expectancy_pct": wf,
                "cost_stress_2x_expectancy_pct": stress,
                "payoff_ratio": payoff,
                "normalized_mdd_pct_points": 5.0,
                "avg_mfe_giveback_pct": 0.5,
                "avg_holding_time_hours": 10.0,
            },
        }

    def setUp(self):
        policy = isolated_functions(
            ROOT / "strategy_lab/v3_policy_research.py",
            {"_measured_trailing_rows", "_metric", "_trailing_row_eligible",
             "_trailing_quality_tuple", "_individual_trailing_select"},
            {"_individual_trailing_contract": lambda: {
                "baseline": {"start_pct": 3.0, "callback_pct": 1.5},
                "selection_neighbor_positive_ratio_min": 0.70,
            }},
        )
        self.selected = policy["_individual_trailing_select"]
        self.target = {
            "strategy_id": "TEST_ROBUST", "timeframe": "6H",
            "exact_strategy_version": "EV_6H",
            "canonical_identity_key": "TEST_ROBUST|EV_6H|6H",
            "eligible": True,
            "pipeline_stage": "INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE",
        }
        pairs = [
            self.row(3.0, 1.5, -0.04, 0.10, payoff=1.0),
            self.row(4.0, 1.0, 0.10, 0.08, payoff=2.0),
            self.row(4.5, 1.0, 0.09, 0.06, payoff=1.9),
            self.row(4.0, 1.5, 0.08, 0.07, payoff=1.8),
            self.row(4.5, 1.5, 0.09, 0.06, payoff=1.8),
        ]
        results = {f"{x['start_pct']:.4f}|{x['callback_pct']:.4f}": x for x in pairs}
        self.item = {
            "exact_strategy_version": "EV_6H",
            "status": "INCOMPLETE",
            "selection": {"status": "INCOMPLETE",
                          "reason": "BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID"},
            "coarse_results": results,
            "fine_candidates": [],
            "fine_results": {},
        }
        self.state = {"entries": [self.target]}
        self.stage3 = {"entries": {self.target["canonical_identity_key"]: self.item}}
        self.finalized = []
        def finalize(state, target, stage3, item):
            self.finalized.append((state, target, stage3, item))
            return {"status": "COMPLETE", "pipeline_stage": "REENTRY_RESEARCH_PENDING",
                    "automatic_promotion": False}
        helper = isolated_functions(
            ROOT / "strategy_lab/v3_strategy_research.py",
            {"_recover_ineligible_baseline_trailing_one"},
            {"load": lambda *args: self.stage3,
             "TRAILING_STAGE3": "read-only stage3",
             "MIN_TRADES": {"6H": 20},
             "_trailing_pair_key": lambda start, callback:
                 f"{float(start):.4f}|{float(callback):.4f}",
             "policy_research": SimpleNamespace(research_exit=self.selected),
             "_finalize_trailing_selection": finalize},
        )
        self.recover = helper["_recover_ineligible_baseline_trailing_one"]

    def test_old_baseline_incomplete_with_robust_positive_alternative_recovers(self):
        result = self.recover(self.state)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["pipeline_stage"], "REENTRY_RESEARCH_PENDING")
        self.assertFalse(result["automatic_promotion"])
        self.assertEqual(len(self.finalized), 1)
        self.assertIs(self.finalized[0][1], self.target)
        self.assertIs(self.finalized[0][3], self.item)

    def test_unmeasured_baseline_cannot_recover(self):
        self.item["coarse_results"].pop("3.0000|1.5000")
        self.assertIsNone(self.recover(self.state))
        self.assertEqual(len(self.finalized), 0)

    def test_no_robust_profitable_alternative_cannot_recover(self):
        self.item["coarse_results"] = {
            k: row for k, row in self.item["coarse_results"].items()
            if k == "3.0000|1.5000"
        }
        self.assertIsNone(self.recover(self.state))
        self.assertFalse(self.finalized)

    def test_source_integrity_failure_cannot_be_recovered(self):
        self.item["source_integrity_error"] = "TRAILING_FROZEN_EVIDENCE_MISMATCH"
        self.assertIsNone(self.recover(self.state))

    def test_identity_mismatch_cannot_recover(self):
        self.item["exact_strategy_version"] = "EV_DIFFERENT"
        self.assertIsNone(self.recover(self.state))

    def test_unrelated_incomplete_or_active_stage_cannot_recover(self):
        self.item["selection"]["reason"] = "OTHER_INCOMPLETE"
        self.assertIsNone(self.recover(self.state))
        self.item["selection"]["reason"] = "BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID"
        self.target["pipeline_stage"] = "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING"
        self.assertIsNone(self.recover(self.state))

    def test_missing_fine_plan_cannot_recover(self):
        del self.item["fine_candidates"]
        self.assertIsNone(self.recover(self.state))

    def test_noneligible_strategy_does_not_recover(self):
        self.target["eligible"] = False
        self.assertIsNone(self.recover(self.state))


if __name__ == "__main__":
    unittest.main()
