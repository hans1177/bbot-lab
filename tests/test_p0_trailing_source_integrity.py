"""Fail closed exact trailing source identity without touching live runtime."""
import ast
import hashlib
import json
import math
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

REPO=Path(__file__).resolve().parents[1]


class ExactTrailingSourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        source=self.root/"strategy_lab/results/v3/generated_trials/immutable.py"
        source.parent.mkdir(parents=True)
        source.write_text("# immutable version\n",encoding="utf-8")
        self.source=source
        self.target={"strategy_id":"V3_EXACT","timeframe":"30M",
                     "exact_strategy_version":"EV_EXACT_30M",
                     "canonical_source_file":source.relative_to(self.root).as_posix(),
                     "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                     "pipeline_stage":"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",
                     "eligible":True}
        self.item={"status":"FINE_RUNNING","fine_candidates":[{"start_pct":3.0,"callback_pct":1.0}]}
        self.state={"entries":[self.target]}
        self.stage3={"entries":{"EXACT":self.item}}
        self.saved=[]
        self.records=[]
        self.knowledge=SimpleNamespace(
            record_result=lambda *a,**kw:self.records.append((a,kw)),
            reserve_trial=lambda *a,**kw:self.fail("MUST_NOT_RESERVE_ON_SOURCE_DRIFT"),
        )
        ns={"ROOT":self.root,"TRAILING_STAGE3":self.root/"stage3.json",
            "re":re,"hashlib":hashlib,"math":math,
            "now":lambda:"2026-10-19T00:00:00Z",
            "atomic":lambda path,row:self.saved.append(("stage3",json.loads(json.dumps(row)))),
            "_save_historical_stage2":lambda row:self.saved.append(("pipeline",json.loads(json.dumps(row)))),
            "knowledge":self.knowledge,
            "policy_research":SimpleNamespace(research_exit=lambda *a,**kw:self.fail("SHOULD_NOT_SELECT")),
            "str":str}
        tree=ast.parse((REPO/"strategy_lab/v3_strategy_research.py").read_text(encoding="utf-8-sig"))
        names={"_trailing_result_binding_error",
               "_trailing_source_integrity_error",
               "_block_trailing_source_drift",
               "_run_trailing_pair",
               "_finalize_trailing_selection"}
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(set(n.name for n in funcs),names)
        exec(compile(ast.Module(body=funcs,type_ignores=[]),"isolated_exact_trailing","exec"),ns)
        self.ns=ns

    def simulated_report_run(self, report):
        """Run only the isolated result-binding segment; no real backtest."""
        completed=[]
        self.knowledge.reserve_trial=lambda *args,**kw:17
        self.knowledge.complete_trial=lambda *args:completed.append(args)
        self.ns.update({
            "os":os,
            "sys":SimpleNamespace(executable="python"),
            "RUNNER":self.root/"strategy_lab/run_strategy.py",
            "hard_pass_dates":lambda:("2016-01-01","2024-12-31"),
            "_trailing_pair_key":lambda *args:"PAIR",
            "time":SimpleNamespace(time=lambda:1.0),
            "subprocess":SimpleNamespace(run=lambda *args,**kw:SimpleNamespace(returncode=0,stdout="")),
            "find_result":lambda *args:self.root/"strategy_lab/results/v3/synthetic.json",
            "load":lambda *args:report,
            "_trailing_evidence":lambda *args:{"trades":500,"expectancy_pct":0.1},
        })
        result=self.ns["_run_trailing_pair"](
            self.state,self.target,self.stage3,self.item,
            {"start_pct":3.0,"callback_pct":1.0},"FINE")
        return result,completed

    def good_report(self):
        return {"strategy_file":str(self.source),
                "individual_trailing_research":{"start_pct":3.0,"callback_pct":1.0}}

    def test_valid_source_and_pair_are_recorded_as_measured(self):
        result,completed=self.simulated_report_run(self.good_report())
        self.assertEqual(result["status"],"MEASURED")
        self.assertEqual(self.item["fine_results"]["PAIR"]["status"],"MEASURED")
        self.assertEqual(self.target["pipeline_stage"],"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING")
        self.assertEqual(completed[0][2],"MEASURED")

    def test_mismatched_report_source_does_not_advance_candidate(self):
        report=self.good_report()
        report["strategy_file"]=str(self.root/"other_source.py")
        result,completed=self.simulated_report_run(report)
        self.assertEqual(result["reason"],"TRAILING_RESULT_SOURCE_MISMATCH")
        self.assertEqual(self.item["fine_results"]["PAIR"]["status"],"INVALID_RESULT_BINDING")
        self.assertEqual(completed[0][2],"FAIL")
        self.assertEqual(self.target["pipeline_stage"],"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE")

    def test_report_missing_trailing_metadata_isolated_not_exception(self):
        report=self.good_report()
        report.pop("individual_trailing_research")
        result,completed=self.simulated_report_run(report)
        self.assertEqual(result["reason"],"TRAILING_RESULT_CONFIG_MISSING")
        self.assertEqual(completed[0][2],"FAIL")

    def test_malformed_and_nonfinite_metadata_isolated(self):
        for wrong in ("bad",float("nan"),None):
            with self.subTest(wrong=repr(wrong)):
                # Reinitialize fixtures to verify no repeated state leakage.
                self.setUp()
                report=self.good_report()
                report["individual_trailing_research"]["callback_pct"]=wrong
                result,completed=self.simulated_report_run(report)
                self.assertEqual(result["reason"],"TRAILING_RESULT_CONFIG_INVALID")
                self.assertEqual(completed[0][2],"FAIL")

    def test_report_pair_mismatch_isolated_not_runtime_crash(self):
        report=self.good_report()
        report["individual_trailing_research"]["start_pct"]=4.0
        result,completed=self.simulated_report_run(report)
        self.assertEqual(result["reason"],"TRAILING_RESULT_CONFIG_MISMATCH")
        self.assertEqual(completed[0][2],"FAIL")

    def test_exact_file_has_no_integrity_error(self):
        self.assertIsNone(self.ns["_trailing_source_integrity_error"](self.target))
        self.assertEqual(self.target["pipeline_stage"],"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING")
        self.assertFalse(self.saved)

    def assert_quarantined(self,expected):
        result=self.ns["_run_trailing_pair"](
            self.state,self.target,self.stage3,self.item,
            {"start_pct":3,"callback_pct":1},"FINE")
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["reason"],expected)
        self.assertFalse(result["automatic_promotion"])
        self.assertEqual(result["pipeline_stage"],"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE")
        self.assertEqual(self.target["pipeline_stage"],"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE")
        self.assertFalse(self.target["direct_canary_allowed"])
        self.assertEqual(self.item["status"],"INCOMPLETE")
        self.assertEqual(self.item["source_integrity_error"],expected)
        self.assertEqual([x[0] for x in self.saved],["pipeline","stage3"])
        self.assertTrue(self.records[0][1]["failed"])
        self.assertEqual(self.records[0][0][1]["reason"],expected)

    def test_mutated_source_is_quarantined_without_new_trial(self):
        self.source.write_text("# changed without version bump\n")
        self.assert_quarantined("TRAILING_EXACT_SOURCE_HASH_MISMATCH")

    def test_source_missing_is_quarantined(self):
        self.source.unlink()
        self.assert_quarantined("TRAILING_CANONICAL_SOURCE_UNAVAILABLE")

    def test_digest_missing_is_quarantined(self):
        self.target["source_sha256"]=""
        self.assert_quarantined("TRAILING_EXACT_SOURCE_BINDING_MISSING")

    def test_symlink_not_allowed(self):
        alternative=self.root/"other.py"
        alternative.write_bytes(self.source.read_bytes())
        self.source.unlink()
        try:
            self.source.symlink_to(alternative)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on platform")
        self.assert_quarantined("TRAILING_CANONICAL_SOURCE_UNAVAILABLE")

    def test_outside_root_cannot_be_read_or_approved(self):
        self.target["canonical_source_file"]="../../../../../external.py"
        self.assert_quarantined("TRAILING_CANONICAL_SOURCE_UNAVAILABLE")

    def test_finalize_cannot_select_if_hash_changed(self):
        self.source.write_text("# modified right before final select\n")
        result=self.ns["_finalize_trailing_selection"](
            self.state,self.target,self.stage3,self.item)
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["reason"],"TRAILING_EXACT_SOURCE_HASH_MISMATCH")
        self.assertTrue(self.records[0][1]["failed"])

    def test_exact_source_integrity_is_independent_of_metrics_or_blind(self):
        self.target["full_validation_gate"]={"pass":True}
        self.target["sealed_final_blind_gate"]={"pass":True}
        self.source.write_text("# modified\n")
        self.assert_quarantined("TRAILING_EXACT_SOURCE_HASH_MISMATCH")


if __name__=="__main__":
    unittest.main()
