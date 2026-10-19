# 파일명: tests/test_cloud_checkpoint.py
import contextlib,hashlib,io,json,os,shutil,tempfile,unittest,zipfile,importlib.util,ast,argparse,sys,types
from pathlib import Path
from unittest.mock import patch
import yaml

REPO=Path(__file__).resolve().parents[1]
WORKFLOW=yaml.safe_load((REPO/'.github/workflows/research-run.yml').read_text())
STEPS={s.get('name'):s for s in WORKFLOW['jobs']['research']['steps'] if s.get('name')}

class CloudCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'runtime';self.root.mkdir()
        self.state=self.root/'strategy_lab/results/v3';self.state.mkdir(parents=True)
        (self.root/'ROADMAP_CONSTITUTION_V3.json').write_text('{"policy":"fixture"}')
        (self.root/'strategy_lab/run_strategy.py').write_text('# fixed fixture engine\n')
        shutil.copyfile(REPO/'strategy_lab/v3_research_knowledge.py',self.root/'strategy_lab/v3_research_knowledge.py')
        (self.root/'INPUT_RECEIPT.json').write_text('{"start.zip":"fixed","extra.zip":"fixed"}')
        (self.state/'trial_ledger.json').write_text('{"schema":"fixture","entries":[{"trial":1}]}')
        (self.state/'candidate.py').write_text('# exact candidate fixture\n')
        self.env={'BBOT_LOCAL_ROOT':str(self.root),'RUNNER_TEMP':str(self.base),'GITHUB_SHA':'fixture','GITHUB_RUN_ID':'1'}
    def run_step(self,name):
        with patch.dict(os.environ,self.env),contextlib.redirect_stdout(io.StringIO()):
            exec(compile(STEPS[name]['run'],name,'exec'),{})
    def save(self):
        self.run_step('Preserve research checkpoint')
        shutil.copytree(self.base/'research-checkpoint-out',self.base/'research-checkpoint-in')
    def test_state_roundtrip_and_code_preserved(self):
        before={p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.save();(self.state/'trial_ledger.json').write_text('{}')
        self.run_step('Restore exact research state')
        self.assertEqual(before,{p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assertEqual((self.root/'strategy_lab/run_strategy.py').read_text(),'# fixed fixture engine\n')
    def test_archive_tamper_rejected(self):
        self.save();(self.base/'research-checkpoint-in/checkpoint.zip').write_bytes(b'tamper')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_HASH_MISMATCH'):self.run_step('Restore exact research state')
    def test_repeated_checkpoint_replaces_archive_with_latest_state(self):
        self.run_step('Preserve research checkpoint')
        (self.state/'trial_ledger.json').write_text('{"completed":2}')
        self.run_step('Preserve research checkpoint')
        with zipfile.ZipFile(self.base/'research-checkpoint-out/checkpoint.zip') as archive:
            self.assertEqual(json.loads(archive.read('strategy_lab/results/v3/trial_ledger.json')),{'completed':2})
    def publish(self,head='original',lease='original',failures=None,twice=False):
        self.run_step('Preserve research checkpoint')
        if lease is not None:(self.base/'research-state-head.json').write_text(json.dumps({'head':lease}))
        spec=importlib.util.spec_from_file_location('checkpoint_knowledge',REPO/'strategy_lab/v3_research_knowledge.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.requests=[];remote={'head':head}
        def request(req,timeout):
            body=json.loads(req.data) if req.data else None
            path=req.full_url.split('/repos/owner/repo')[1]
            self.requests.append((path,body))
            if path=='/git/blobs' and failures:
                import urllib.error
                code,message=failures.pop(0)
                raise urllib.error.HTTPError(req.full_url,code,'fixture',{},io.BytesIO(json.dumps({'message':message}).encode()))
            if path=='/git/ref/heads/research-state':result={'object':{'sha':remote['head']}}
            elif path=='/git/commits':
                self.assertEqual(body['parents'],[remote['head']]);result={'sha':'new-commit'}
            elif path=='/git/refs/heads/research-state':
                self.assertIs(body['force'],False);remote['head']=body['sha'];result={}
            else:result={'sha':'object'}
            return io.StringIO(json.dumps(result))
        env=dict(self.env,GH_TOKEN='fixture',GITHUB_REPOSITORY='owner/repo',GITHUB_RUN_ATTEMPT='1')
        with patch.dict(os.environ,env),patch('urllib.request.urlopen',side_effect=request),patch('time.sleep') as sleep:
            try:
                module.publish_cloud_checkpoint()
                if twice:module.publish_cloud_checkpoint()
            finally:self.sleeps=sleep.call_count
    def test_rate_limited_blob_retries_then_updates_lease(self):
        self.publish(failures=[(422,'You have triggered a secondary rate limit')])
        self.assertEqual(self.sleeps,1)
        self.assertEqual(json.loads((self.base/'research-state-head.json').read_text())['head'],'new-commit')
    def test_validation_422_is_not_retried_or_committed(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):self.publish(failures=[(422,'Validation Failed')])
        self.assertEqual(self.sleeps,0)
        self.assertFalse(any(path=='/git/commits' for path,_ in self.requests))
        self.assertEqual(json.loads((self.base/'research-state-head.json').read_text())['head'],'original')
    def test_temporary_server_failure_retry_is_bounded(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):self.publish(failures=[(503,'Unavailable')]*4)
        self.assertEqual(self.sleeps,3)
        self.assertFalse(any(path=='/git/commits' for path,_ in self.requests))
    def test_unchanged_verified_blobs_are_not_uploaded_again(self):
        self.publish(twice=True)
        self.assertEqual(sum(path=='/git/blobs' for path,_ in self.requests),2)
    def test_remote_advance_blocks_stale_checkpoint_before_any_write(self):
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_REMOTE_STATE_CHANGED'):self.publish(head='other-run')
        self.assertTrue(all(body is None for _,body in self.requests))
    def test_missing_restore_lease_cannot_publish(self):
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_RESTORE_LEASE_MISSING'):self.publish(lease=None)
        self.assertTrue(all(body is None for _,body in self.requests))
    def test_intermediate_publish_updates_lease_without_candidate_release(self):
        self.publish()
        self.assertEqual(json.loads((self.base/'research-state-head.json').read_text()),{'head':'new-commit'})
        self.assertFalse(any('runtime-candidates' in path for path,_ in self.requests))
    def test_controller_checkpoints_completed_cycles_under_lock(self):
        node=next(n for n in ast.parse((REPO/'strategy_lab/research_controller.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
        events=[];state={'locked':False,'clock':0}
        @contextlib.contextmanager
        def lock():
            state['locked']=True
            try:yield
            finally:state['locked']=False
        def record(event):
            self.assertTrue(state['locked']);events.append(event)
        def research(held):
            record('research');return {'action':'AUTO_RESEARCH_MANAGER_STAGE10'}
        def sleep(seconds):state['clock']+=60
        ns={'argparse':argparse,'Path':Path,'os':os,'json':json,'lock':lock,
            'PUSH_HOLD':types.SimpleNamespace(exists=lambda:True),
            'managed_research_once':research,
            'time':types.SimpleNamespace(monotonic=lambda:state['clock'],sleep=sleep),
            'knowledge':types.SimpleNamespace(save_cloud_checkpoint=lambda:record('save'),publish_cloud_checkpoint=lambda:record('publish'))}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_controller_main','exec'),ns)
        for bounds in (['--max-cycles','2'],['--max-cycles','0','--time-budget-seconds','120','--finish-margin-seconds','60']):
            with self.subTest(bounds=bounds):
                events.clear();state['clock']=0
                argv=['controller','--mode','loop','--enable-research','--checkpoint-seconds','60']+bounds
                with patch.object(sys,'argv',argv),patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),contextlib.redirect_stdout(io.StringIO()):ns['main']()
                self.assertEqual(events,['research','save','publish','research','save','publish'])
                self.assertFalse(state['locked'])
    def test_changed_engine_rejected(self):
        self.save();(self.root/'strategy_lab/run_strategy.py').write_text('# changed engine')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_ENGINE_MISMATCH'):self.run_step('Restore exact research state')
    def test_changed_inputs_rejected(self):
        self.save();(self.root/'INPUT_RECEIPT.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_INPUTS_CHANGED'):self.run_step('Restore exact research state')
    def test_30m_extension_preserves_other_research(self):
        old={'start.zip':'fixed','research_scope':['1H','6H','12H','1D','1W']}
        p=self.root/'INPUT_RECEIPT.json';p.write_text(json.dumps(old));self.save()
        new=dict(old,research_scope=['30M','1H','6H','12H','1D','1W'],official_30m={'period':['2019-09','2026-09'],'manifest_sha256':'fixture'})
        p.write_text(json.dumps(new));self.run_step('Restore exact research state')
        self.assertEqual(json.loads((self.state/'trial_ledger.json').read_text())['entries'],[{'trial':1}])
    def test_30m_extension_cannot_change_original_archives(self):
        old={'start.zip':'fixed','research_scope':['1H','6H','12H','1D','1W']}
        p=self.root/'INPUT_RECEIPT.json';p.write_text(json.dumps(old));self.save()
        new=dict(old,**{'start.zip':'changed','research_scope':['30M','1H','6H','12H','1D','1W'],'official_30m':{'period':['2019-09','2026-09'],'manifest_sha256':'fixture'}})
        p.write_text(json.dumps(new))
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_INPUTS_CHANGED'):self.run_step('Restore exact research state')
    def test_unlisted_archive_path_rejected(self):
        self.save();folder=self.base/'research-checkpoint-in';p=folder/'checkpoint.zip'
        with zipfile.ZipFile(p,'a') as z:z.writestr('../outside','bad')
        receipt=json.loads((folder/'CHECKPOINT.json').read_text());receipt['archive_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();(folder/'CHECKPOINT.json').write_text(json.dumps(receipt))
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_FILE_SET_MISMATCH'):self.run_step('Restore exact research state')
    def test_workflow_has_one_worker_and_no_checkout_credentials(self):
        self.assertFalse(WORKFLOW['concurrency']['cancel-in-progress'])
        job=WORKFLOW['jobs']['research'];self.assertLessEqual(job['timeout-minutes'],350)
        checkout=next(s for s in job['steps'] if str(s.get('uses','')).startswith('actions/checkout@'))
        self.assertFalse(checkout['with']['persist-credentials'])
        self.assertEqual(job['env']['B_BOT_V3_SERVER_PUSH_HOLD'],'1')
    def test_inline_python_compiles(self):
        for name,step in STEPS.items():
            if step.get('shell')=='python':compile(step['run'],name,'exec')
