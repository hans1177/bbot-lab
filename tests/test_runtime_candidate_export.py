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
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_export','exec'),ns);self.ns=ns;self.export=ns['export_runtime_candidates'];self.out=self.root/'export'
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
    def ready_release(self):
        import time
        from unittest.mock import Mock
        self.config=self.root/'strategy_config.json';self.config.write_text('{"frames":{"1H":{"strategy":"OLD"}},"r4_runtime":{"strategy_instances":{}}}')
        self.request=self.root/'request.json';self.req={'request_id':'RID','version_kind':'STRATEGY','tier':'CANARY','strategy_instance_ids':['INSTANCE'],'strategy':'V3_FIXTURE','timeframe':'1H','strategy_version':'EV_EXACT_1H','strategy_policy_bundle_version':'BUNDLE','strategy_file':'exact.py','strategy_config_file':'strategy_lab/results/v3/strategy_config_pending_fixture.json'};self.request.write_text(json.dumps(self.req))
        pending=self.root/self.req['strategy_config_file'];pending.parent.mkdir(parents=True,exist_ok=True);pending.write_text('{}')
        state=self.root/'approval.json';self.approval={'status':'VALIDATED_FOR_SERVER','validated_at_epoch':time.time(),'canonical_verifiers':6,'request_sha256':hashlib.sha256(self.request.read_bytes()).hexdigest(),'base_config_sha256':hashlib.sha256(self.config.read_bytes()).hexdigest()};state.write_text(json.dumps(self.approval));self.approval_path=state
        self.worker=types.SimpleNamespace(STATE=state,validate_request=Mock());self.merger=types.SimpleNamespace(merge_strategy=Mock(return_value={'strategy_registry':{'V3_FIXTURE':{'file':'exact.py'}}}))
        self.ns.update(REQUEST=self.request,CONFIG=self.config,RES=pending.parent,time=time,minimal_shadow_pass=lambda *a:True,load=lambda p,d:json.loads(Path(p).read_text()) if Path(p).exists() else d)
    def approved_call(self):
        with patch.dict(sys.modules,{'v3_strategy_research':self.owner,'v3_portable_strategy_packager':self.packager,'v3_auto_runtime_release':self.worker,'v3_runtime_config_merge':self.merger}),patch.dict(os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123'}):return self.export(self.out,validated_release=True)
    def test_only_verified_request_can_export_registration_payload(self):
        self.ready_release();self.approved_call();manifest=json.loads((self.out/'MANIFEST.json').read_text());self.assertTrue(manifest['automatic_registration_authorized']);release=manifest['release'];self.assertTrue(release['local_shadow_pass']);self.assertEqual(release['canonical_verifiers'],6);self.assertEqual(release['strategy_instance_ids'],['INSTANCE']);self.assertEqual(release['request_sha256'],hashlib.sha256(self.request.read_bytes()).hexdigest());self.assertEqual(set(manifest['files']),{manifest['candidates'][0]['artifact'],'release/CONFIG.json','release/REQUEST.json'});self.worker.validate_request.assert_called_once();self.merger.merge_strategy.assert_called_once()
    def test_unverified_state_never_authorizes_registration(self):
        self.ready_release();self.approval['status']='WAIT_SHADOW';self.approval_path.write_text(json.dumps(self.approval));self.approved_call();manifest=json.loads((self.out/'MANIFEST.json').read_text());self.assertFalse(manifest['automatic_registration_authorized']);self.assertNotIn('release',manifest)
    def test_changed_request_cannot_reuse_prior_verification(self):
        self.ready_release();self.request.write_text(json.dumps({**self.req,'request_id':'MUTATED'}))
        with self.assertRaisesRegex(RuntimeError,'VERIFICATION_BINDING'):self.approved_call()
    def test_shadow_fail_cannot_export_approved_release(self):
        self.ready_release();self.ns['minimal_shadow_pass']=lambda *a:False
        with self.assertRaisesRegex(RuntimeError,'SHADOW_NOT_PASSED'):self.approved_call()
    def test_stale_verification_cannot_export_release(self):
        self.ready_release();self.approval['validated_at_epoch']-=181;self.approval_path.write_text(json.dumps(self.approval))
        with self.assertRaisesRegex(RuntimeError,'APPROVAL_EXPIRED'):self.approved_call()
    def test_live_config_change_invalidates_approval(self):
        self.ready_release();self.config.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'ACTIVE_CONFIG_CHANGED'):self.approved_call()
    def test_server_registered_blocked_version_is_not_reintroduced(self):
        self.ready_release();self.approval['status']='APPLIED';self.approval_path.write_text(json.dumps(self.approval))
        self.config.write_text(json.dumps({'r4_runtime':{'strategy_instances':{'INSTANCE':{'strategy_id':'V3_FIXTURE','timeframe':'1H','exact_strategy_version':'EV_EXACT_1H','entry_enabled':False,'server_activation_authorized':True}}}}))
        self.approved_call();manifest=json.loads((self.out/'MANIFEST.json').read_text());self.assertEqual(manifest['candidates'],[]);self.assertFalse(manifest['automatic_registration_authorized'])
