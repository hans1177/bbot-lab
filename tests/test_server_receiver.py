import ast,hashlib,json,subprocess,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'runtime/strategy_runtime.py').read_text()
FN=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.FunctionDef) and n.name=='receive_github_candidates')
class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        state=self.root/'state';state.mkdir();(state/'github_runtime.git').mkdir()
        self.settings={'enabled':True,'repository':'hans1177/bbot-lab','branch':'runtime-candidates','constitution_hash':'c'*64,'engine_sha256':'e'*64}
        (state/'r4_github_receiver.json').write_text(json.dumps(self.settings))
        self.artifact=b'# PORTABLE_RUNTIME_ARTIFACT_V1\nraise RuntimeError("MUST_NOT_EXECUTE")\n'
        self.name='artifacts/'+'a'*64+'.py';digest=hashlib.sha256(self.artifact).hexdigest()
        row={'strategy_id':'V3_FIXTURE','timeframe':'1H','exact_strategy_version':'EV_FIXTURE_1H','source_sha256':'f'*64,'artifact':self.name,'runtime_sha256':digest,'server_shadow_required':True,'approved_for_server_release':False}
        row['research_evidence']={k:row[k] for k in ('strategy_id','timeframe','exact_strategy_version','source_sha256')}
        row['research_evidence'].update(eligible=True,pipeline_stage='LOCAL_SHADOW_PENDING',full_validation_gate={'pass':True},sealed_final_blind_gate={'pass':True,'next_stage':'LOCAL_SHADOW_PENDING'})
        self.manifest={'schema':'B_BOT_R4_RESEARCH_CANDIDATES_V1','automatic_registration_authorized':False,'constitution_hash':'c'*64,'engine_sha256':'e'*64,'source_commit':'b'*40,'candidates':[row],'files':{self.name:{'bytes':len(self.artifact),'sha256':digest}}}
        self.revision='d'*40;self.calls=[]
        ns={'BASE':self.root,'Path':Path,'json':json,'os':__import__('os')};exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_receiver','exec'),ns);self.receive=ns['receive_github_candidates']
    def run_git(self,args,**kwargs):
        self.calls.append(args);op=args[2];data={'MANIFEST.json':json.dumps(self.manifest).encode(),self.name:self.artifact}
        if op=='rev-parse':out=self.revision.encode()
        elif op=='ls-tree':out='\n'.join(data).encode()
        elif op=='cat-file':out=str(len(data[args[-1].split(':',1)[1]])).encode()
        elif op=='show':out=data[args[-1].split(':',1)[1]]
        else:out=b''
        return types.SimpleNamespace(returncode=0,stdout=out)
    def call(self):
        with patch.object(subprocess,'run',self.run_git):return self.receive()
    def test_receipt_stages_without_executing_or_touching_operating_files(self):
        config=self.root/'strategy_config.json';config.write_text('OPERATING_CONFIG_UNCHANGED')
        result=self.call();self.assertEqual(result['status'],'WAIT_SERVER_VALIDATION');self.assertFalse(result['automatic_registration_enabled'])
        staged=list((self.root/'state/github_candidates').glob('*/runtime.py'));self.assertEqual(len(staged),1);self.assertEqual(staged[0].read_bytes(),self.artifact)
        self.assertEqual(config.read_text(),'OPERATING_CONFIG_UNCHANGED');self.assertFalse((self.root/'state/history.db').exists())
    def test_rewritten_remote_head_updates_only_candidate_tracking_ref(self):
        self.call()
        fetch=next(args for args in self.calls if args[2]=='fetch')
        self.assertEqual(fetch[-1],'+refs/heads/runtime-candidates:refs/remotes/origin/runtime-candidates')
        self.assertNotIn('push',[args[2] for args in self.calls])
        self.assertFalse((self.root/'strategy_config.json').exists())
        self.manifest['constitution_hash']='0'*64
        self.revision='a'*40
        with self.assertRaisesRegex(RuntimeError,'CONTRACT'):self.call()

    def test_same_head_idempotent_and_does_not_repeat_log(self):
        self.call();log=self.root/'logs/runner.log';before=log.read_bytes();self.assertTrue(self.call()['unchanged']);self.assertEqual(log.read_bytes(),before)
    def test_first_gate_only_never_staged(self):
        self.manifest['candidates'][0]['research_evidence']['full_validation_gate']={'pass':False}
        with self.assertRaisesRegex(RuntimeError,'FINAL_GATES_REQUIRED'):self.call()
        self.assertFalse((self.root/'state/github_candidates').exists())
    def test_hash_failure_never_staged(self):
        self.artifact+=b'# changed\n'
        with self.assertRaisesRegex(RuntimeError,'ARTIFACT_HASH'):self.call()
    def test_foreign_engine_rejected(self):
        self.manifest['engine_sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'CONTRACT_MISMATCH'):self.call()
    def test_contract_change_revalidated_on_same_head(self):
        self.call();self.settings['engine_sha256']='0'*64;(self.root/'state/r4_github_receiver.json').write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(RuntimeError,'CONTRACT_MISMATCH'):self.call()
    def test_version_conflict_is_not_overwritten(self):
        self.call();path=next((self.root/'state/github_candidates').glob('*/runtime.py'));path.write_text('EXISTING_DIFFERENT_ARTIFACT');self.revision='e'*40
        with self.assertRaisesRegex(RuntimeError,'IMMUTABLE_VERSION_CONFLICT'):self.call()
        self.assertEqual(path.read_text(),'EXISTING_DIFFERENT_ARTIFACT')
    def test_settings_symlink_rejected(self):
        path=self.root/'state/r4_github_receiver.json';path.rename(path.with_suffix('.real'));path.symlink_to(path.with_suffix('.real'))
        with self.assertRaisesRegex(RuntimeError,'SETTINGS_SYMLINK'):self.call()
    def test_existing_runtime_prefix_is_byte_identical(self):
        prefix=SOURCE.encode()[:24689]
        # The actual AWS byte prefix, including its final newlines, is preserved.
        self.assertEqual(hashlib.sha256(prefix).hexdigest(),'1cb77962db51e26fccc83cdcbb88b5f801fb0c2dd0d5b29aef3a7637bf486cfb')
class InstallerTests(unittest.TestCase):
    def test_research_workspace_is_outside_checked_out_runtime_source(self):
        import yaml
        workflow=yaml.safe_load((ROOT/'.github/workflows/research-run.yml').read_text())
        self.assertEqual(workflow['jobs']['research']['env']['BBOT_LOCAL_ROOT'],'${{ github.workspace }}/cloud-research-workspace')

    def test_exact_deployed_predecessor_hash_is_allowed_without_disabling_version_gate(self):
        source=(ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text()
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_server_receiver')
        remote=next(n.value for n in ast.walk(fn) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'before_pid=service()' in n.value)
        assert "AWS_RUNTIME_SOURCE_CHANGED" in remote
        permitted='bd635b3bc7788ca313e9ace2c05dd1fccb9c310ef485ab919f27f3a06ca4e057'
        self.assertIn("'verified_server_feedback_sha256':"+repr(permitted),source)
        self.assertIn("payload['verified_server_feedback_sha256']",remote)
        self.assertIn("if old_sha not in {",remote)
        self.assertNotIn('force=True',remote)
        self.assertNotIn('skip_hash_check',remote)
        self.assertIn("if service()!=before_pid",remote)

    def test_receiver_refresh_preserves_live_modes_and_rejects_unrelated_settings(self):
        source=(ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text()
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_server_receiver')
        remote=next(n.value for n in ast.walk(fn) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'before_pid=service()' in n.value)
        snippet=remote.split('previous_settings=json.loads(old_settings)',1)[1].split('\nunit=',1)[0]
        snippet='previous_settings=json.loads(old_settings)'+snippet
        def check(previous,enable_registration=False,enable_feedback=False):
            ns={'old_settings':json.dumps(previous).encode() if previous is not None else None,
                'json':json,'payload':{'code_ref':'f'*40,'enable_registration':enable_registration,'enable_feedback':enable_feedback}}
            exec(snippet,ns)
            return ns['config']
        existing={'enabled':True,'repository':'hans1177/bbot-lab','branch':'runtime-candidates',
                  'automatic_registration_enabled':True,'feedback_enabled':True}
        preserved=check(existing)
        self.assertTrue(preserved['automatic_registration_enabled'])
        self.assertTrue(preserved['feedback_enabled'])
        self.assertEqual(preserved['code_ref'],'f'*40)
        initial=check(None)
        self.assertFalse(initial['automatic_registration_enabled'])
        self.assertFalse(initial['feedback_enabled'])
        activated=check(None,enable_registration=True,enable_feedback=True)
        self.assertTrue(activated['automatic_registration_enabled'])
        self.assertTrue(activated['feedback_enabled'])
        foreign={**existing,'repository':'foreign/project'}
        with self.assertRaisesRegex(RuntimeError,'EXISTING_SETTINGS_CONFLICT'):
            check(foreign)

    def test_embedded_remote_script_compiles_and_does_not_restart_r4(self):
        source=(ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text();tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_server_receiver')
        remote=next(n for n in ast.walk(fn) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'before_pid=service()' in n.value).value
        compile(remote,'remote_install','exec');self.assertNotIn("'restart'",remote);self.assertNotIn('sqlite',remote)
        self.assertIn("if service()!=before_pid",remote);self.assertIn('automatic_registration_enabled',remote)
        self.assertIn('parser.add_argument("--install-server-receiver"',source)
