import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE=Path(__file__).resolve().parents[1]/"strategy_lab/v3_strategy_research.py"
TREE=ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
NAMES={"_redevelopment_stability_neighbors","_redevelopment_stability_summary"}
FUNCS=[x for x in TREE.body if isinstance(x,ast.FunctionDef) and x.name in NAMES]
assert {x.name for x in FUNCS}==NAMES

def eligible(row,minimum):
    ev=row.get("evidence") or {}
    return (row.get("status")=="MEASURED"
            and int(ev.get("trades") or 0)>=minimum
            and float(ev.get("walk_forward_oos_expectancy_pct") or 0)>0
            and float(ev.get("cost_stress_2x_expectancy_pct") or 0)>0)

ns={"_trailing_pair_key":lambda s,c:f"{s:.4f}|{c:.4f}",
    "policy_research":SimpleNamespace(_trailing_row_eligible=eligible)}
exec(compile(ast.Module(body=FUNCS,type_ignores=[]),str(SOURCE),"exec"),ns)

def measured(wf=0.2,st=0.1,trades=45):
    return {"status":"MEASURED","evidence":{
        "trades":trades,"walk_forward_oos_expectancy_pct":wf,
        "cost_stress_2x_expectancy_pct":st}}

class StableNeighbors(unittest.TestCase):
    def test_bounded_exact_grid(self):
        for start,callback in [(1.0,0.5),(6.0,3.0),(3.0,1.5)]:
            pairs=ns["_redevelopment_stability_neighbors"](start,callback)
            self.assertGreaterEqual(len(pairs),2)
            self.assertLessEqual(len(pairs),4)
            self.assertEqual(len({(x["start_pct"],x["callback_pct"]) for x in pairs}),len(pairs))
            self.assertTrue(all(1<=x["start_pct"]<=6 and 0.5<=x["callback_pct"]<=3
                                and x["callback_pct"]<x["start_pct"] for x in pairs))

    def test_needs_robust_neighbors_and_positive_center(self):
        summary=ns["_redevelopment_stability_summary"]
        self.assertTrue(summary(measured(),[measured(),measured(),measured()],40)["pass"])
        self.assertFalse(summary(measured(),[measured(wf=-1),measured(wf=-1)],40)["pass"])
        self.assertFalse(summary(measured(wf=-1),[measured(),measured(),measured()],40)["pass"])
        self.assertFalse(summary(measured(),[measured()],40)["pass"])

if __name__=="__main__":unittest.main()
