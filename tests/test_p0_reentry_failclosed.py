"""Exact-version reentry cannot stall or certify malformed training evidence."""
import ast
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO=Path(__file__).resolve().parents[1]


class ReentryIntegrityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.source=self.root/"strategy_lab/results/v3/generated_trials/frozen.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# frozen exact strategy\n")
        self.other={"strategy_id":"INDEPENDENT","pipeline_stage":"REENTRY_RESEARCH_PENDING"}
        self.target={
            "strategy_id":"TEST_REENTRY","timeframe":"30M",
            "exact_strategy_version":"EV_FROZEN_30M",
            "canonical_source_file":self.source.relative_to(self.root).as_posix(),
            "source_sha256":hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "individual_trailing":{"start_pct":3,"callback_pct":1},
            "pipeline_stage":"REENTRY_RESEARCH_PENDING","eligible":True,
        }
        self.stage={"entries":{}}
        self.item={"results":{},"candidates":[]}
        self.state={"entries":[self.target,self.other]}
        self.completed=[]
        self.records=[]
        self.writes=[]
        self.calls=[]
        self.report={
            "strategy_file":str(self.source),
            "individual_trailing_research":{"start_pct":3.0,"callback_pct":1.0},
            "individual_reentry_research":{"mode":"CURRENT_STATE_NEXT_1H_BASELINE_PROXY","confirmation_bars":1},
            "research_window":{"mode":"HARD_PASS_ONLY"},
        }
        self.candidate={"mode":"CURRENT_STATE_NEXT_1H_BASELINE_PROXY"}
        self.knowledge=SimpleNamespace(
            reserve_trial=lambda *a,**kw:17,
            complete_trial=lambda *a:self.completed.append(a),
            record_result=lambda *a,**kw:self.records.append((a,kw)),
        )
        def run(*args,**kw):
            self.calls.append(kw)
            return SimpleNamespace(returncode=0,stdout="")
        self.runner=run
        ns={"ROOT":self.root,"Path":Path,"math":math,"hashlib":hashlib,
            "re":re,"os":os,"sys":sys,
            "RUNNER":self.root/"strategy_lab/run_strategy.py",
            "REENTRY_STAGE4":self.root/"reentry.json",
            "now":lambda:"2026-10-20T00:00:00Z",
            "time":SimpleNamespace(time=lambda:1.0),
            "finite":lambda x:float(x) if x is not None else None,
            "hard_pass_dates":lambda:("2016-01-01","2024-08-20"),
            "knowledge":self.knowledge,
            "atomic":lambda *a:self.writes.append("stage"),
            "_save_historical_stage2":lambda *a:self.writes.append("pipeline"),
            "subprocess":SimpleNamespace(
                PIPE=subprocess.PIPE,STDOUT=subprocess.STDOUT,
                TimeoutExpired=subprocess.TimeoutExpired,run=lambda *a,**kw:self.runner(*a,**kw)),
            "find_result":lambda *a:self.root/"strategy_lab/results/synthetic.json",
            "load":lambda *a:self.report,
            "_reentry_evidence":lambda *a:{"trades":60},
        }
        tree=ast.parse((REPO/"strategy_lab/v3_strategy_research.py").read_text(encoding="utf-8-sig"))
        names={"_trailing_source_integrity_error","_trailing_result_binding_error",
               "_block_reentry_integrity","_reentry_result_binding_error",
               "_reentry_key","_run_reentry_candidate"}
        extracted=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(set(n.name for n in extracted),names)
        exec(compile(ast.Module(body=extracted,type_ignores=[]),"isolated_reentry","exec"),ns)
        self.ns=ns

    def run_trial(self):
        return self.ns["_run_reentry_candidate"](
            self.state,self.target,self.stage,self.item,self.candidate,
        )

    def assert_invalid(self,reason):
        result=self.run_trial()
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["reason"],reason)
        self.assertFalse(result["automatic_promotion"])
        self.assertEqual(self.target["pipeline_stage"],"REENTRY_RESEARCH_INCOMPLETE")
        self.assertFalse(self.target["direct_canary_allowed"])
        self.assertEqual(self.item["status"],"INCOMPLETE")
        self.assertEqual(self.other["pipeline_stage"],"REENTRY_RESEARCH_PENDING")
        self.assertTrue(self.records[0][1]["failed"])

    def test_valid_exact_reentry_report_is_measured_with_30_minute_bound(self):
        result=self.run_trial()
        self.assertEqual(result["status"],"MEASURED")
        self.assertEqual(self.calls[0]["timeout"],1800)
        self.assertEqual(self.item["results"]["CURRENT_STATE_NEXT_1H_BASELINE_PROXY"]["status"],"MEASURED")
        self.assertEqual(self.completed[0][2],"MEASURED")
        self.assertEqual(self.target["pipeline_stage"],"REENTRY_RESEARCH_PENDING")

    def test_timeout_quarantines_exact_candidate_not_other_research(self):
        def raise_timeout(*args,**kw):
            self.assertEqual(kw["timeout"],1800)
            raise subprocess.TimeoutExpired(cmd=args[0],timeout=kw["timeout"])
        self.runner=raise_timeout
        self.assert_invalid("REENTRY_RUN_TIMEOUT")
        self.assertEqual(self.completed[0][2],"FAIL")
        self.assertEqual(self.item["results"]["CURRENT_STATE_NEXT_1H_BASELINE_PROXY"]["status"],"RUNTIME_FAIL")

    def test_missing_method_metadata_rejected_without_crash(self):
        self.report.pop("individual_reentry_research")
        self.assert_invalid("REENTRY_RESULT_CONFIG_MISSING")
        self.assertEqual(self.completed[0][2],"FAIL")

    def test_wrong_reentry_mode_rejected(self):
        self.report["individual_reentry_research"]["mode"]="CURRENT_STATE_RESET_RECLAIM"
        self.assert_invalid("REENTRY_RESULT_CONFIG_MISMATCH")

    def test_drifted_strategy_source_path_rejected(self):
        self.report["strategy_file"]=str(self.root/"different.py")
        self.assert_invalid("REENTRY_TRAILING_RESULT_SOURCE_MISMATCH")

    def test_malformed_trailing_metadata_rejected_without_typeerror(self):
        self.report["individual_trailing_research"]["callback_pct"]="not a number"
        self.assert_invalid("REENTRY_TRAILING_RESULT_CONFIG_INVALID")

    def test_wrong_training_window_rejected(self):
        self.report["research_window"]["mode"]="LEGACY_DEFAULT"
        self.assert_invalid("REENTRY_RESULT_WINDOW_INVALID")

    def test_source_mutation_quarantined_before_trial_reservation(self):
        self.source.write_text("# mutated\n")
        self.assert_invalid("TRAILING_EXACT_SOURCE_HASH_MISMATCH")
        self.assertFalse(self.completed)
        self.assertFalse(self.calls)

    def test_confirmation_bars_exactly_bound(self):
        self.candidate={"mode":"CURRENT_STATE_CONFIRM_STRATEGY_BARS","confirm_bars":2}
        self.report["individual_reentry_research"]={
            "mode":"CURRENT_STATE_CONFIRM_STRATEGY_BARS","confirmation_bars":3}
        self.assert_invalid("REENTRY_RESULT_CONFIRMATION_MISMATCH")

    def test_non_numeric_confirmation_bars_rejected(self):
        self.candidate={"mode":"CURRENT_STATE_CONFIRM_STRATEGY_BARS","confirm_bars":2}
        self.report["individual_reentry_research"]={
            "mode":"CURRENT_STATE_CONFIRM_STRATEGY_BARS","confirmation_bars":"bad"}
        self.assert_invalid("REENTRY_RESULT_CONFIRMATION_INVALID")


if __name__=="__main__":
    unittest.main()
