"""Small in-memory audits: immutable version snapshot and allowed output only."""
import importlib.util
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path

MODULE=Path(__file__).resolve().parents[1]/"strategy_lab/research_state_audit.py"
spec=importlib.util.spec_from_file_location("research_state_audit",MODULE)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class ResearchAuditTests(unittest.TestCase):
    def test_lazy_zip_validated_without_fetching_all_parts(self):
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream,"w",zipfile.ZIP_STORED) as z:
                z.writestr("unused.txt",b"x"*10000)
                z.writestr("wanted.json",b'{"v":1}')
            raw=stream.getvalue()
        partsize=mod.CHUNK
        chunks=[raw[i:i+partsize] for i in range(0,len(raw),partsize)]
        rows=[{"name":f"checkpoint.part{i:04d}","bytes":len(chunk),
               "sha256":hashlib.sha256(chunk).hexdigest()} for i,chunk in enumerate(chunks)]
        called=[]
        def fetch(name):
            called.append(name)
            return chunks[int(name[-4:])]
        with zipfile.ZipFile(mod.ChunkZip(rows,len(raw),fetch)) as z:
            self.assertEqual(json.loads(z.read("wanted.json")),{"v":1})
        self.assertEqual(set(called),{"checkpoint.part0000"})

    def test_negative_baseline_counts_positive_alternative_without_approval(self):
        base={"status":"MEASURED","evidence":{
            "trades":45,"walk_forward_oos_expectancy_pct":-0.1,
            "cost_stress_2x_expectancy_pct":-0.2}}
        other={"status":"MEASURED","evidence":{
            "trades":45,"walk_forward_oos_expectancy_pct":0.2,
            "cost_stress_2x_expectancy_pct":0.1}}
        state={"entries":[{"eligible":True,"timeframe":"30M",
             "pipeline_stage":"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE",
             "canonical_identity_key":"x"}]}
        stage3={"entries":{"x":{"coarse_results":{"3.0000|1.5000":base,
                                             "4.0000|1.5000":other}}}}
        result=mod.summarize(state,stage3)
        self.assertEqual(result["baseline_failure_checks"],{
            "BASELINE_WF_NONPOSITIVE":1,"BASELINE_STRESS_NONPOSITIVE":1})
        self.assertEqual(result["eligible_alternative_counts"],{
            "AT_LEAST_ONE_MEASURABLE_ELIGIBLE_ALTERNATIVE":1})
        self.assertFalse(result["automatic_registration_authorized"])

    def test_corrupted_part_rejected(self):
        raw=b"not really a zip"
        part=[{"name":"checkpoint.part0000","bytes":len(raw),
               "sha256":hashlib.sha256(raw).hexdigest()}]
        f=mod.ChunkZip(part,len(raw),lambda name:b"modified content")
        with self.assertRaisesRegex(ValueError,"AUDIT_PART_HASH_MISMATCH"):
            f.read()

    def test_stage_counts_and_private_text_not_leaked(self):
        stage2={"entries":[
            {"eligible":True,"pipeline_stage":"INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE",
             "canonical_identity_key":"a","last_error":"secret account detail"},
            {"eligible":True,"pipeline_stage":"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",
             "canonical_identity_key":"b"},
            {"eligible":True,"pipeline_stage":"FULL_VALIDATION_PENDING","canonical_identity_key":"c"}]}
        stage3={"entries":{"a":{"status":"INCOMPLETE","runtime_error":"RUN_STRATEGY_FAIL"},
                            "b":{"status":"COARSE_RUNNING","coarse_candidates":[{"start_pct":3}],
                                 "coarse_results":{"3.0":{"status":"MEASURED"}}}}}
        result=mod.summarize(stage2,stage3)
        self.assertEqual(result["trailing_incomplete_reasons"],{"OTHER_OR_UNRECORDED":1})
        self.assertEqual(result["baseline_failure_checks"],{"BASELINE_NOT_MEASURED":1})
        self.assertEqual(result["eligible_alternative_counts"],{"NO_ELIGIBLE_ALTERNATIVE":1})
        self.assertEqual(result["stage_counts"]["FULL_VALIDATION_PENDING"],1)
        self.assertEqual(result["trailing_pair_work"]["coarse:MEASURED"],1)
        self.assertNotIn("secret",json.dumps(result))
        self.assertFalse(result["automatic_registration_authorized"])

if __name__=="__main__":unittest.main()
