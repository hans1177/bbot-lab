"""Frozen trailing reassessment must be one-shot, read-only to trial evidence."""
import ast
import hashlib
import runpy
import tempfile
import unittest
import numpy as np
import pandas as pd
from pathlib import Path
from unittest.mock import Mock

REPO=Path(__file__).resolve().parents[1]
SOURCE=REPO/"strategy_lab/v3_strategy_research.py"

class FrozenTrailingReassessmentTests(unittest.TestCase):
    def setUp(self):
        self.source=SOURCE.read_text(encoding="utf-8-sig")
        tree=ast.parse(self.source)
        funcs=[node for node in tree.body if isinstance(node,ast.FunctionDef)
               and node.name=="_reassess_invalid_baseline_frozen_one"]
        self.assertEqual(len(funcs),1)
        self.tree=funcs[0]
        self.reason="BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID"
        self.target={
            "eligible":True,
            "pipeline_stage":"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE",
            "canonical_identity_key":"exact-frozen-id",
            "last_error":self.reason,
        }
        self.state={"entries":[self.target]}
        self.item={"status":"INCOMPLETE","selection":{"reason":self.reason}}
        self.stage3={"entries":{"exact-frozen-id":self.item}}
        self.saves=[]
        self.finalizations=[]
        def fake_finalize(state,target,stage3,item):
            self.finalizations.append((state,target,stage3,item))
            target["pipeline_stage"]="REENTRY_RESEARCH_PENDING"
            return {"status":"COMPLETE","selection_reason":"ROBUST_NEIGHBORHOOD_MULTI_METRIC_IMPROVEMENT"}
        ns={
            "TRAILING_STAGE3":"stage3",
            "load":lambda path,default:self.stage3,
            "_save_historical_stage2":lambda state:self.saves.append(dict(self.target)),
            "_finalize_trailing_selection":fake_finalize,
            "_FROZEN_TRAILING_REASSESSMENT_CONTRACT":"MEASURED_BASELINE_ROBUST_ALTERNATIVE_V1",
        }
        exec(compile(ast.Module(body=funcs,type_ignores=[]),str(SOURCE),"exec"),ns)
        self.call=ns["_reassess_invalid_baseline_frozen_one"]

    def test_frozen_incomplete_can_advance_only_through_existing_finalizer(self):
        result=self.call(self.state)
        self.assertEqual(result["action"],"TRAILING_FROZEN_GRID_REASSESSMENT")
        self.assertEqual(result["pipeline_stage"],"REENTRY_RESEARCH_PENDING")
        self.assertEqual(result["status"],"COMPLETE")
        self.assertFalse(result["automatic_promotion"])
        self.assertFalse(result["orders_touched"])
        self.assertEqual(len(self.finalizations),1)
        self.assertEqual(len(self.saves),1)
        self.assertEqual(self.saves[0]["trailing_selection_reassessment_contract"],
                         "MEASURED_BASELINE_ROBUST_ALTERNATIVE_V1")
        self.assertFalse(self.target["direct_canary_allowed"])

    def test_legacy_reason_and_exact_item_required(self):
        for bad in (
            {"last_error":"TRAILING_RUN_TIMEOUT"},
            {"eligible":False},
            {"pipeline_stage":"HARD_PASS_RECHECK_FAIL"},
            {"canonical_identity_key":"wrong"},
        ):
            with self.subTest(bad=bad):
                self.setUp()
                self.target.update(bad)
                self.assertIsNone(self.call(self.state))
                self.assertEqual(len(self.finalizations),0)

    def test_one_time_marker_skips_rerun(self):
        self.target["trailing_selection_reassessment_contract"] = (
            "MEASURED_BASELINE_ROBUST_ALTERNATIVE_V1")
        self.assertIsNone(self.call(self.state))
        self.assertFalse(self.finalizations)

    def test_only_freeze_finalizer_called_no_trial_execution(self):
        calls=set()
        for node in ast.walk(self.tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                calls.add(node.func.id)
        self.assertIn("_finalize_trailing_selection",calls)
        self.assertNotIn("reserve_trial",calls)
        self.assertNotIn("run_strategy",calls)
        self.assertNotIn("publish_cloud_checkpoint",calls)

class CausalRegimeRedevelopmentTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        (self.root/"parent.py").write_text(
            "import numpy as np\n"
            "STRATEGY_META={'name':'TEST_PARENT'}\n"
            "def generate_signals(df,tf):\n"
            "    return np.ones(len(df),dtype=np.int8)\n",encoding="utf-8")
        nodes=[node for node in ast.parse(SOURCE.read_text()).body
               if isinstance(node,ast.FunctionDef)
               and node.name=="_make_prior_signal_confirmation_source"]
        self.assertEqual(len(nodes),1)
        ns={"ROOT":self.root,"REDEVELOPED":self.root/"redeveloped",
            "hashlib":hashlib}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),
                     str(SOURCE),"exec"),ns)
        self.generate=ns["_make_prior_signal_confirmation_source"]
        self.target={"canonical_source_file":"parent.py",
                     "strategy_id":"TEST_PARENT"}

    def test_new_final_validation_filter_uses_only_closed_bar_history(self):
        output,error=self.generate(self.target,"FULL_VALIDATION",1)
        self.assertEqual(error,"")
        self.assertIn("_regime_",output.name)
        module=runpy.run_path(str(output))
        close=np.concatenate([np.linspace(120,80,60),np.linspace(80,140,60)])
        frame=pd.DataFrame({"close":close})
        signals=module["generate_signals"](frame,"1H")
        self.assertEqual(int(signals[45]),0)
        self.assertEqual(int(signals[110]),1)
        for length in (30,65,88,115):
            np.testing.assert_array_equal(
                signals[:length],
                module["generate_signals"](frame.iloc[:length],"1H"))
        # Entry-risk redevelopment remains the original confirmation-only path.
        original,error=self.generate(self.target,"ENTRY_RISK",1)
        self.assertEqual(error,"")
        self.assertNotIn("_regime_",original.name)
        legacy=runpy.run_path(str(original))["generate_signals"](frame,"1H")
        self.assertEqual(int(legacy[45]),1)
        self.assertEqual(int(legacy[110]),1)

    def test_frozen_redevelopment_source_never_overwritten(self):
        original,_=self.generate(self.target,"FULL_VALIDATION",1)
        before=original.read_bytes()
        same,_=self.generate(self.target,"FULL_VALIDATION",1)
        self.assertEqual(original,same)
        self.assertEqual(original.read_bytes(),before)
        original.write_text("# frozen content differs",encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError,"FROZEN_STRATEGY_SOURCE_CONFLICT"):
            self.generate(self.target,"FULL_VALIDATION",1)
        self.assertEqual(original.read_text(encoding="utf-8"),"# frozen content differs")


if __name__=="__main__":
    unittest.main()
