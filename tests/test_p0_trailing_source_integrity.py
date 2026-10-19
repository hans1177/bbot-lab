"""Fail closed exact trailing source identity without touching live runtime."""
import ast
import hashlib
import json
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
            "re":re,"hashlib":hashlib,
            "now":lambda:"2026-10-19T00:00:00Z",
            "atomic":lambda path,row:self.saved.append(("stage3",json.loads(json.dumps(row)))),
            "_save_historical_stage2":lambda row:self.saved.append(("pipeline",json.loads(json.dumps(row)))),
            "knowledge":self.knowledge,
            "policy_research":SimpleNamespace(research_exit=lambda *a,**kw:self.fail("SHOULD_NOT_SELECT")),
            "str":str}
        tree=ast.parse((REPO/"strategy_lab/v3_strategy_research.py").read_text(encoding="utf-8-sig"))
        names={"_trailing_source_integrity_error",
               "_block_trailing_source_drift",
               "_run_trailing_pair",
               "_finalize_trailing_selection"}
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(set(n.name for n in funcs),names)
        exec(compile(ast.Module(body=funcs,type_ignores=[]),"isolated_exact_trailing","exec"),ns)
        self.ns=ns

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
