# 파일명: tests/test_github_release_input.py
import ast,base64,contextlib,copy,hashlib,io,json,tempfile,types,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import sys
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
PULL=next(n for n in ast.parse((LAB/'v3_auto_runtime_release.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='pull')
class GithubReleaseInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.v3=self.root/'strategy_lab/results/v3';self.v3.mkdir(parents=True)
        (self.root/'ROADMAP_CONSTITUTION_V3.json').write_text('{}')
        (self.root/'strategy_lab/lab_config.json').write_text(json.dumps({'github_research':{'enabled':True,'repository':'hans1177/bbot-lab','engine_sha256':'engine'}}))
        self.code=b'# PORTABLE_GENERATED_SINGLE_V2\nSTRATEGY_META={}\n'
        self.source='strategy_lab/results/v3/generated_trials/fixed.py'
        self.row={'eligible':True,'strategy_id':'FIXED','timeframe':'1H','exact_strategy_version':'EV_FIXED_1H','pipeline_stage':'LOCAL_SHADOW_PENDING','canonical_source_file':self.source,'source_sha256':hashlib.sha256(self.code).hexdigest(),'full_validation_gate':{'pass':True},'sealed_final_blind_gate':{'pass':True,'next_stage':'LOCAL_SHADOW_PENDING'},'individual_trailing':{'start_pct':3,'callback_pct':1},'individual_reentry':{'mode':'CURRENT_STATE_NEXT_1H'}}
        self.record={'strategy':'FIXED','timeframe':'1H','exact_strategy_version':'EV_FIXED_1H','source_sha256':self.row['source_sha256'],'status':'PASS','evaluation':{'pass':True},'family_file':self.source}
        self.original={'entries':[{'exact_strategy_version':'OLD','pipeline_stage':'PERSISTENT_FAIL'}]}
        (self.v3/'historical_314_pipeline_stage2.json').write_text(json.dumps(self.original))
        self.events=[];self.hold=self.root/'SERVER_PUSH_HOLD.flag';self.request=self.v3/'runtime_sync_request.json';self.private=True;self.corrupt=None;self.engine='engine';self.extra={}
        @contextlib.contextmanager
        def lock():yield
        self.modules={'research_controller':types.SimpleNamespace(lock=lock),'v3_research_knowledge':types.SimpleNamespace(record_result=lambda *a,**k:self.events.append(a))}
    def run_pull(self):
        files={'strategy_lab/results/v3/historical_314_pipeline_stage2.json':json.dumps({'entries':[self.row]}).encode(),'strategy_lab/results/v3/research_knowledge.json':json.dumps({'records':{'single:fixed':self.record} if self.record is not None else {}}).encode(),self.source:self.code,**self.extra}
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as archive:
            for name,raw in files.items():archive.writestr(name,raw)
        archive=buf.getvalue()
        receipt={'schema':'B_BOT_RESEARCH_CHECKPOINT_V1','archive_sha256':hashlib.sha256(archive).hexdigest(),'constitution_sha256':hashlib.sha256(b'{}').hexdigest(),'engine_sha256':self.engine,'source_commit':'a'*40,'files':{n:{'bytes':len(r),'sha256':hashlib.sha256(r).hexdigest()} for n,r in files.items()}}
        if self.corrupt:receipt['files'][self.source]['sha256']='bad'
        blobs={'receipt':json.dumps(receipt).encode(),'archive':archive}
        def run(cmd,timeout):
            path=cmd[-1].removeprefix('repos/hans1177/bbot-lab')
            if path=='':value={'private':self.private}
            elif path=='/git/ref/heads/research-state':value={'object':{'sha':'b'*40}}
            elif path=='/git/commits/'+'b'*40:value={'tree':{'sha':'tree'}}
            elif path=='/git/trees/tree':value={'tree':[{'path':'CHECKPOINT.json','type':'blob','sha':'receipt'},{'path':'checkpoint.zip','type':'blob','sha':'archive'}]}
            else:
                raw=blobs[path.split('/')[-1]];value={'content':base64.b64encode(raw).decode(),'size':len(raw)}
            return 0,json.dumps(value)
        def atomic(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        self.ns={'ROOT':self.root,'REQUEST':self.request,'HOLD':self.hold,'json':json,'hashlib':hashlib,'Path':Path,'time':types.SimpleNamespace(monotonic=lambda:100),'_github_pull_cache':{},'run':run,'sha256':lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),'atomic_json':atomic,'now':lambda:'NOW','log':lambda *a:None}
        exec(compile(ast.Module(body=[PULL],type_ignores=[]),'actual_pull','exec'),self.ns)
        with patch.dict(sys.modules,self.modules):return self.ns['pull']('test',research_only=True)
    def assert_unchanged(self):
        self.assertEqual(json.loads((self.v3/'historical_314_pipeline_stage2.json').read_text()),self.original)
        self.assertFalse((self.root/self.source).exists());self.assertEqual(self.events,[])
    def test_final_qualified_source_imported_preserving_old_state_and_no_request(self):
        r=self.run_pull();self.assertEqual(r['imported'],1);self.assertFalse(self.request.exists())
        rows=json.loads((self.v3/'historical_314_pipeline_stage2.json').read_text())['entries'];self.assertEqual(rows[0],self.original['entries'][0]);self.assertEqual(rows[1]['source_sha256'],self.row['source_sha256']);self.assertEqual((self.root/self.source).read_bytes(),self.code)
    def test_only_first_gate_pass_not_imported(self):
        self.row['pipeline_stage']='INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING';self.assertEqual(self.run_pull()['imported'],0);self.assert_unchanged()
    def test_blind_failure_is_blocked(self):
        self.row['sealed_final_blind_gate']['pass']=False
        with self.assertRaisesRegex(RuntimeError,'FINAL_GATES_REQUIRED'):self.run_pull()
        self.assert_unchanged()
    def test_changed_source_or_manifest_is_blocked(self):
        self.corrupt=True
        with self.assertRaisesRegex(RuntimeError,'FILE_HASH'):self.run_pull()
        self.assert_unchanged()
    def test_unknown_engine_is_blocked(self):
        self.engine='unknown'
        with self.assertRaisesRegex(RuntimeError,'ENGINE_MISMATCH'):self.run_pull()
        self.assert_unchanged()
    def test_missing_exact_backtest_is_blocked(self):
        self.record=None
        with self.assertRaisesRegex(RuntimeError,'BOUND_BACKTEST_REQUIRED'):self.run_pull()
        self.assert_unchanged()
    def test_foreign_source_path_is_blocked(self):
        self.row['canonical_source_file']='strategy_lab/results/v3/../../order.py'
        with self.assertRaisesRegex(RuntimeError,'SOURCE_MISSING'):self.run_pull()
        self.assert_unchanged()
    def test_public_repository_is_blocked(self):
        self.private=False
        with self.assertRaisesRegex(RuntimeError,'PRIVATE_REQUIRED'):self.run_pull()
        self.assert_unchanged()
    def test_manual_hold_preserved(self):
        self.hold.write_text('manual')
        with self.assertRaisesRegex(RuntimeError,'LOCAL_RELEASE_BUSY'):self.run_pull()
        self.assert_unchanged();self.assertEqual(self.hold.read_text(),'manual')
    def test_existing_version_never_overwritten(self):
        self.original['entries'].append({'exact_strategy_version':'EV_FIXED_1H','pipeline_stage':'PERSISTENT_FAIL'})
        (self.v3/'historical_314_pipeline_stage2.json').write_text(json.dumps(self.original))
        with self.assertRaisesRegex(RuntimeError,'EXISTING_VERSION_CONFLICT'):self.run_pull()
        self.assert_unchanged()
