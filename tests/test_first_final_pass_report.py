"""Cloud research completion must not claim AWS certification."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "p0_report", ROOT / "strategy_lab/first_final_pass_report.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FirstFinalPassReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.v3 = self.root / "strategy_lab/results/v3"
        self.v3.mkdir(parents=True)
        self.manifest = self.root / "out/MANIFEST.json"
        self.manifest.parent.mkdir(parents=True)
        self.trial = {
            "strategy_id": "TEST_CANDIDATE",
            "exact_strategy_version": "EV_EXACT_30M",
            "canonical_identity_key": "TEST|EV_EXACT_30M|30M",
            "timeframe": "30M",
            "eligible": True,
            "pipeline_stage": "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",
        }
        self.fine = {"strategy_id": "TEST_CANDIDATE",
            "fine_candidates": [{"start_pct": 3, "callback_pct": 1},
                               {"start_pct": 3, "callback_pct": 1.5}],
            "fine_results": {"3.0000|1.0000": {"status": "MEASURED"}},
            "status": "FINE_RUNNING"}
        self.pipeline([self.trial])
        self.put(self.v3 / "individual_trailing_stage3.json",
                 {"entries": {self.trial["canonical_identity_key"]: self.fine}})
        self.put(self.manifest, {"candidates": [],
                    "automatic_registration_authorized": False})

    @staticmethod
    def put(path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def pipeline(self, rows):
        self.put(self.v3 / "historical_314_pipeline_stage2.json",
                 {"entries": rows})

    def report(self):
        return mod.certification_progress(self.root, self.manifest)

    def test_frozen_exact_version_research_progress(self):
        result = self.report()
        self.assertEqual(result["stage_counts"],
                         {"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING": 1})
        self.assertEqual(result["closest_trailing"][0]["fine_measured"], 1)
        self.assertEqual(result["closest_trailing"][0]["fine_planned"], 2)
        self.assertEqual(result["first_final_pass_status"],
                         "NO_FINAL_VALIDATED_STRATEGY_YET")
        self.assertEqual(result["aws_final_certification"],
                         "NOT_VERIFIED_BY_GITHUB_RESEARCH")

    def test_blind_pass_requires_full_validation_and_exact_next_stage(self):
        row = dict(self.trial, pipeline_stage="LOCAL_SHADOW_PENDING",
                   full_validation_gate={"pass": True},
                   sealed_final_blind_gate={"pass": True,
                                            "next_stage": "LOCAL_SHADOW_PENDING"})
        self.pipeline([row])
        self.assertEqual(self.report()["research_full_and_sealed_blind_pass_count"], 1)
        self.assertEqual(self.report()["aws_final_certification"],
                         "NOT_VERIFIED_BY_GITHUB_RESEARCH")
        row["sealed_final_blind_gate"]["next_stage"] = "OTHER"
        self.pipeline([row])
        self.assertEqual(self.report()["research_full_and_sealed_blind_pass_count"], 0)
        self.assertEqual(self.report()["unverified_shadow_stage_count"], 1)
        row["sealed_final_blind_gate"]["next_stage"] = "LOCAL_SHADOW_PENDING"
        row["full_validation_gate"]["pass"] = False
        self.pipeline([row])
        self.assertEqual(self.report()["research_full_and_sealed_blind_pass_count"], 0)

    def test_report_exposes_full_validation_failure_without_changing_gates(self):
        row = dict(self.trial, pipeline_stage="FULL_VALIDATION_REDEVELOPMENT_PENDING",
                   full_validation_gate={
                       "pass": False, "decision": "FAIL", "pass_mode": "NONE",
                       "absolute_fail_reasons": [
                           "FULL_VALIDATION_PROFIT_CONCENTRATION_ABSOLUTE_FAIL"
                       ],
                       "compensation_flags": []
                   }, last_checked_at="2026-10-20T12:00:00Z")
        self.pipeline([row])
        before = (self.v3 / "historical_314_pipeline_stage2.json").read_bytes()
        progress = self.report()
        self.assertEqual(progress["full_validation_failed_count"], 1)
        self.assertEqual(progress["research_full_and_sealed_blind_pass_count"], 0)
        failed = progress["full_validation_failures"][0]
        self.assertEqual(failed["strategy"], "TEST_CANDIDATE")
        self.assertEqual(failed["absolute_fail_reasons"],
                         ["FULL_VALIDATION_PROFIT_CONCENTRATION_ABSOLUTE_FAIL"])
        self.assertEqual((self.v3 / "historical_314_pipeline_stage2.json").read_bytes(),
                         before)
        self.assertEqual(progress["aws_final_certification"],
                         "NOT_VERIFIED_BY_GITHUB_RESEARCH")

    def test_validated_stage_never_counted_as_full_validation_failure(self):
        row = dict(self.trial, pipeline_stage="SEALED_FINAL_BLIND_PENDING",
                   full_validation_gate={"pass": True, "decision": "PASS"})
        self.pipeline([row])
        progress = self.report()
        self.assertEqual(progress["full_validation_failed_count"], 0)
        self.assertEqual(progress["full_validation_failures"], [])

    def test_handoff_still_requires_real_server_shadow(self):
        self.put(self.manifest, {"candidates": [{
            "strategy_id": "TEST_CANDIDATE", "timeframe": "30M",
            "exact_strategy_version": "EV_EXACT_30M",
            "server_shadow_required": True,
            "approved_for_server_release": False,
        }], "automatic_registration_authorized": False})
        result = self.report()
        self.assertEqual(result["handoff_awaiting_server_validation_count"], 1)
        self.assertEqual(result["first_final_pass_status"],
                         "SHADOW_AND_CANONICAL_RELEASE_PENDING")
        self.assertEqual(result["aws_final_certification"],
                         "NOT_VERIFIED_BY_GITHUB_RESEARCH")

    def test_ineligible_rows_not_counted(self):
        row = dict(self.trial, eligible=False)
        self.pipeline([row])
        self.assertEqual(self.report()["stage_counts"], {})
        self.assertEqual(self.report()["pipeline_entries_seen"], 1)

    def test_nonexistent_and_corrupt_manifests_do_not_forge_pass(self):
        self.manifest.unlink()
        self.assertEqual(self.report()["handoff_awaiting_server_validation_count"], 0)
        self.manifest.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.report()["aws_final_certification"],
                         "NOT_VERIFIED_BY_GITHUB_RESEARCH")

    def test_read_only_inputs_unchanged(self):
        before={p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.report()
        self.assertEqual(before,{p: p.read_bytes() for p in self.root.rglob("*")
                                 if p.is_file()})

    def test_cloud_research_resumes_on_p0_source_merge_without_cancel(self):
        import yaml
        flow=yaml.safe_load((ROOT / ".github/workflows/research-run.yml").read_text())
        trigger=flow.get("on",flow.get(True))
        self.assertIn("schedule",trigger)
        self.assertIn("push",trigger)
        self.assertEqual(trigger["push"]["branches"],["main"])
        changed=set(trigger["push"]["paths"])
        self.assertIn("strategy_lab/v3_strategy_research.py",changed)
        self.assertIn("strategy_lab/research_controller.py",changed)
        self.assertIn("strategy_lab/v3_auto_runtime_release.py",changed)
        self.assertEqual(flow["concurrency"]["group"],"research-runtime-single-worker")
        self.assertIs(flow["concurrency"]["cancel-in-progress"],False)
        self.assertEqual(flow["jobs"]["research"]["env"]["B_BOT_V3_SERVER_PUSH_HOLD"],"1")
        steps=flow["jobs"]["research"]["steps"]
        research=next(step for step in steps if step.get("name")=="Run existing research controller")
        self.assertIn("--time-budget-seconds 18000",research["run"])
        self.assertIn("--max-cycles",research["run"])

    def test_actual_workflow_scheduled_with_report_after_export(self):
        import yaml
        flow=yaml.safe_load((ROOT / ".github/workflows/research-run.yml").read_text())
        steps=[step.get("name") for step in flow["jobs"]["research"]["steps"]]
        self.assertIn("P0 final-certification progress (read only)",steps)
        self.assertLess(steps.index("Prepare runtime candidate handoff"),
                        steps.index("P0 final-certification progress (read only)"))
        self.assertLess(steps.index("P0 final-certification progress (read only)"),
                        steps.index("Preserve research checkpoint"))
        self.assertTrue(flow.get("on") is None or isinstance(flow,dict))

if __name__ == "__main__":
    unittest.main()
