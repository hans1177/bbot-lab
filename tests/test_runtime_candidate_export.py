import ast,hashlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];LAB=ROOT/'strategy_lab'
class CandidateExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.source=self.root/'exact.py';self.source.write_text('# exact portable fixture\n')
        self.lab=self.root/'strategy_lab';self.lab.mkdir();(self.lab/'run_strategy.py').write_text('# engine fixture\n')
        self.row={'strategy_id':'V3_FIXTURE','timeframe':'1H','exact_strategy_version':'EV_EXACT_1H','source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),'eligible':True,'pipeline_stage':'LOCAL_SHADOW_PENDING','full_validation_gate':{'pass':True},'sealed_final_blind_gate':{'pass':True,'next_stage':'LOCAL_SHADOW_PENDING'},'canonical_source_file':'exact.py','individual_trailing':{'start_pct':1,'callback_pct':1},'individual_reentry':{'mode':'CURRENT_STATE_NEXT_1H'}}
        choice=next(n for n in ast.parse((LAB/'v3_strategy_research.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_pipeline_local_shadow_choice')
        ns={'ROOT':self.root,'HISTORICAL_STAGE2':None,'load':lambda *args:{'entries':[self.row]},'_pipeline_source_portable':lambda p:True,'finite':lambda x:x}
        exec(compile(ast.Module(body=[choice],type_ignores=[]),'canonical_choice','exec'),ns)
        self.owner=types.SimpleNamespace(TF_ORDER=['1H'],_pipeline_local_shadow_choice=ns['_pipeline_local_shadow_choice']);self.packages=[]
        def package(source,target):
            self.packages.append(source);target.parent.mkdir(parents=True,exist_ok=True);target.write_text('# PORTABLE_RUNTIME_ARTIFACT_V1\n');return {'runtime_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
        self.packager=types.SimpleNamespace(package_strategy=package)
        fn=next(n for n in ast.parse((LAB/'v3_deployment.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='export_runtime_candidates')
        ns={'Path':Path,'ROOT':self.root,'LAB':self.lab,'json':json,'os':os,'governance':types.SimpleNamespace(validate_constitution=lambda:{'ok':True,'constitution_hash':'c'*64}),'source_for_pipeline':lambda row:self.source}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_export','exec'),ns);self.export=ns['export_runtime_candidates'];self.out=self.root/'export'
    def call(self):
        with patch.dict(sys.modules,{'v3_strategy_research':self.owner,'v3_portable_strategy_packager':self.packager}),patch.dict(os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123'}):return self.export(self.out)
    def test_final_gated_exact_version_exports_shadow_required(self):
        result=self.call();self.assertEqual(result['candidates'],1);manifest=json.loads((self.out/'MANIFEST.json').read_text());self.assertFalse(manifest['automatic_registration_authorized']);self.assertTrue(manifest['candidates'][0]['server_shadow_required']);self.assertFalse(manifest['candidates'][0]['approved_for_server_release'])
    def test_first_gate_pass_not_exported(self):
        self.row['pipeline_stage']='INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING';self.assertEqual(self.call()['candidates'],0);self.assertEqual(self.packages,[])
    def test_blind_fail_not_exported(self):
        self.row['sealed_final_blind_gate']['pass']=False;self.assertEqual(self.call()['candidates'],0)
    def test_source_modified_after_research_rejected(self):
        self.source.write_text('# different code\n')
        with self.assertRaisesRegex(RuntimeError,'SOURCE_HASH'):self.call()
        self.assertEqual(self.packages,[])
