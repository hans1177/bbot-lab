"""Frozen trailing policy: invalid comparison baseline cannot block robust alternatives."""
import ast
import unittest
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/"strategy_lab/v3_policy_research.py"
FUNCTIONS={"_metric","_measured_trailing_rows","_trailing_row_eligible",
           "_trailing_quality_tuple","_individual_trailing_select"}
POLICY={"baseline":{"start_pct":3.0,"callback_pct":1.5},
        "selection_neighbor_positive_ratio_min":0.70}
tree=ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in FUNCTIONS]
assert {n.name for n in nodes}==FUNCTIONS
ns={"_individual_trailing_contract":lambda:POLICY}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),"exec"),ns)

def measured(start,callback,wf,stress,payoff=2.0,trades=45):
    return {"status":"MEASURED","start_pct":start,"callback_pct":callback,
            "evidence":{"walk_forward_oos_expectancy_pct":wf,
                        "cost_stress_2x_expectancy_pct":stress,
                        "payoff_ratio":payoff,"trades":trades,
                        "normalized_mdd_pct_points":10.0,
                        "avg_mfe_giveback_pct":0.3,
                        "avg_holding_time_hours":4.0}}

class StrictInvalidBaselineAlternativeTests(unittest.TestCase):
    def select(self,rows):
        return ns["_individual_trailing_select"]({
            "minimum_trades":40,"evaluations":{
                str(index):row for index,row in enumerate(rows)
            }})

    def test_negative_baseline_can_select_proven_robust_neighbor(self):
        rows=[measured(3,1.5,-0.1,-0.1,payoff=1.0),
              measured(4,1.5,0.2,0.1,payoff=2.0),
              measured(4,1.25,0.25,0.1,payoff=2.0),
              measured(4.5,1.5,0.3,0.2,payoff=2.0)]
        result=self.select(rows)
        self.assertEqual(result["status"],"SELECTED")
        self.assertFalse(result["fallback"])
        self.assertEqual(result["reason"],"ROBUST_NEIGHBORHOOD_MULTI_METRIC_IMPROVEMENT")
        self.assertFalse(result["live_effect"])
        self.assertNotEqual(result["selected"],{"start_pct":3.0,"callback_pct":1.5})

    def test_unstable_alternative_cannot_override_invalid_baseline(self):
        rows=[measured(3,1.5,-0.1,-0.1,payoff=1.0),
              measured(4,1.5,0.2,0.1,payoff=2.0)]
        result=self.select(rows)
        self.assertEqual(result["status"],"INCOMPLETE")
        self.assertIsNone(result["selected"])
        self.assertEqual(result["reason"],"INVALID_BASELINE_NO_ROBUST_VALID_ALTERNATIVE")

    def test_all_negative_alternatives_stay_incomplete(self):
        rows=[measured(3,1.5,-0.2,-0.1,payoff=1.0),
              measured(4,1.5,-0.1,-0.2,payoff=2.0),
              measured(4,1.25,-0.1,0.1,payoff=2.0),
              measured(4.5,1.5,0.2,-0.1,payoff=2.0)]
        self.assertEqual(self.select(rows)["status"],"INCOMPLETE")

    def test_no_baseline_cannot_select_alternative(self):
        rows=[measured(4,1.5,0.2,0.1),
              measured(4,1.25,0.25,0.1),
              measured(4.5,1.5,0.3,0.2)]
        result=self.select(rows)
        self.assertEqual(result["status"],"INCOMPLETE")
        self.assertIsNone(result["selected"])

    def test_baseline_without_minimum_sample_cannot_be_control(self):
        rows=[measured(3,1.5,-0.1,-0.1,trades=10),
              measured(4,1.5,0.2,0.1),
              measured(4,1.25,0.25,0.1),
              measured(4.5,1.5,0.3,0.2)]
        self.assertEqual(self.select(rows)["status"],"INCOMPLETE")

    def test_baseline_stays_valid_fallback_when_no_robust_improvement(self):
        rows=[measured(3,1.5,0.1,0.1,payoff=1.0),
              measured(4,1.5,0.08,0.1,payoff=1.1)]
        result=self.select(rows)
        self.assertEqual(result["status"],"FALLBACK")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["selected"],{"start_pct":3.0,"callback_pct":1.5})
        self.assertFalse(result["live_effect"])

if __name__=="__main__":unittest.main()
