"""P0 scheduling chooses near-complete frozen trailing work, not winning metrics."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TrailingWorkPriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/"strategy_lab/v3_strategy_research.py"
        parsed=ast.parse(path.read_text(encoding="utf-8-sig"))
        nodes=[x for x in parsed.body if isinstance(x,ast.FunctionDef)
               and x.name=="_trailing_remaining_work_key"]
        assert len(nodes)==1
        ns={"_trailing_pair_key":lambda a,b:f"{float(a):.4f}|{float(b):.4f}"}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),"exec"),ns)
        cls.priority=staticmethod(ns["_trailing_remaining_work_key"])

    def setUp(self):
        self.target={"canonical_identity_key":"TEST|EV_1","exact_strategy_version":"EV_1"}
        self.coarse=[{"start_pct":3.0,"callback_pct":1.5},
                     {"start_pct":4.0,"callback_pct":1.0}]
        self.fine=[{"start_pct":4.5,"callback_pct":1.0},
                   {"start_pct":4.0,"callback_pct":1.5}]

    def test_new_candidate_uses_existing_order(self):
        self.assertEqual(self.priority(self.target,{}),(3,0))

    def test_coarse_partial_remaining_trials(self):
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{"status":"MEASURED"}}}
        self.assertEqual(self.priority(self.target,{"TEST|EV_1":row}),(2,1))

    def test_fine_planned_before_coarse_and_new(self):
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{},
                               "4.0000|1.0000":{}},
             "fine_candidates":self.fine,
             "fine_results":{"4.5000|1.0000":{}}}
        self.assertEqual(self.priority(self.target,{"TEST|EV_1":row}),(0,1))

    def test_ready_to_finalize_gets_highest_priority(self):
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{},
                               "4.0000|1.0000":{}},
             "fine_candidates":self.fine,
             "fine_results":{"4.5000|1.0000":{},
                             "4.0000|1.5000":{}}}
        self.assertEqual(self.priority(self.target,{"TEST|EV_1":row}),(0,0))

    def test_fine_plan_boundary_before_coarse(self):
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{},
                               "4.0000|1.0000":{}}}
        self.assertEqual(self.priority(self.target,{"TEST|EV_1":row}),(1,0))

    def test_exact_version_mismatch_never_uses_other_version_progress(self):
        row={"exact_strategy_version":"EV_2",
             "fine_candidates":self.fine,
             "fine_results":{}}
        self.assertEqual(self.priority(self.target,{"TEST|EV_1":row}),(3,0))

    def test_profitability_does_not_affect_priority(self):
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{"evidence":{"expectancy_pct":-99}}}}
        one=self.priority(self.target,{"TEST|EV_1":row})
        row["coarse_results"]["3.0000|1.5000"]["evidence"]["expectancy_pct"]=99
        self.assertEqual(one,self.priority(self.target,{"TEST|EV_1":row}))

    def test_does_not_change_any_state(self):
        import copy
        row={"exact_strategy_version":"EV_1",
             "coarse_candidates":self.coarse,
             "coarse_results":{"3.0000|1.5000":{}}}
        entries={"TEST|EV_1":row}
        original=copy.deepcopy(entries)
        self.priority(self.target,entries)
        self.assertEqual(entries,original)


if __name__=="__main__":
    unittest.main()
