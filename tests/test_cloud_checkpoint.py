# 파일명: tests/test_cloud_checkpoint.py
import base64,contextlib,hashlib,io,json,os,shutil,tempfile,unittest,zipfile,importlib.util,ast,argparse,sys,types
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
    def test_public_checkpoint_rejects_credentials_without_upload(self):
        self.env['BBOT_PUBLIC_RESEARCH']='1'
        (self.state/'trial_ledger.json').write_text(json.dumps({'token':'ghp_'+'A'*36}))
        with self.assertRaisesRegex(RuntimeError,'PUBLIC_RESEARCH_SECRET_REJECTED'):
            self.run_step('Preserve research checkpoint')
        self.assertFalse((self.base/'research-checkpoint-out/CHECKPOINT.json').exists())

    def test_public_checkpoint_marks_scanned_state(self):
        self.env['BBOT_PUBLIC_RESEARCH']='1'
        self.run_step('Preserve research checkpoint')
        receipt=json.loads((self.base/'research-checkpoint-out/CHECKPOINT.json').read_text())
        self.assertEqual(receipt['public_secret_scan'],'PASS')

    def test_checkpoint_diagnoses_trailing_blockers_from_same_archive(self):
        entries=[
            {'eligible':True,'pipeline_stage':'INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE',
             'canonical_identity_key':'version-a','last_error':'TRAILING_RUN_TIMEOUT'},
            {'eligible':True,'pipeline_stage':'INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE',
             'canonical_identity_key':'version-b','last_error':'user-provided sensitive metadata'},
            {'eligible':True,'pipeline_stage':'INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING',
             'canonical_identity_key':'version-c'},
        ]
        (self.state/'historical_314_pipeline_stage2.json').write_text(json.dumps({'entries':entries}))
        (self.state/'individual_trailing_stage3.json').write_text(json.dumps({
            'entries':{'version-a':{'status':'INCOMPLETE'},
                       'version-b':{'status':'INCOMPLETE'}}}))
        self.run_step('Preserve research checkpoint')
        out=self.base/'research-checkpoint-out'
        receipt=json.loads((out/'CHECKPOINT.json').read_text())
        stats=receipt['p0_stage_status']['trailing_blocker_diagnostics']
        self.assertEqual(stats['reasons'],
                         {'OTHER_OR_UNRECORDED':1,'TRAILING_RUN_TIMEOUT':1})
        self.assertNotIn('sensitive metadata',json.dumps(stats))
        with zipfile.ZipFile(out/'checkpoint.zip') as archive:
            for name,sha in [('historical_314_pipeline_stage2.json','source_stage2_sha256'),
                             ('individual_trailing_stage3.json','source_stage3_sha256')]:
                self.assertEqual(
                    hashlib.sha256(archive.read('strategy_lab/results/v3/'+name)).hexdigest(),
                    stats[sha])

    def test_large_checkpoint_transport_has_bounded_reupload_frequency(self):
        # Full checkpoints can exceed 300 MiB and 30 Git blobs.
        # The canonical workflow must not try that upload every minute.
        controller=STEPS['Run existing research controller']['run']
        self.assertIn('--checkpoint-seconds 900',controller)
        self.assertIn('--time-budget-seconds 18000',controller)
        self.assertIn('--finish-margin-seconds 1800',controller)
        final=STEPS['Preserve research checkpoint']
        self.assertIn('save_cloud_checkpoint()',final['run'])
        self.assertIn('always()',final['if'])
        upload=STEPS['Upload verified research checkpoint']
        self.assertIn('publish_cloud_checkpoint(include_candidates=True)',upload['run'])

    def test_p0_stage_snapshot_binds_same_archived_exact_version_evidence(self):
        pipeline_path=self.state/'historical_314_pipeline_stage2.json'
        pipeline={
            'entries':[{
                'strategy_id':'V3_EFFICIENCY_PULLBACK_RESUME',
                'exact_strategy_version':'EV_55a86e97dfadf81c_30M',
                'timeframe':'30M','eligible':True,
                'pipeline_stage':'FULL_VALIDATION_REDEVELOPMENT_PENDING',
                'final_risk_gate':{'pass':True,'decision':'PASS'},
                'full_validation_gate':{
                    'pass':False,'decision':'FAIL',
                    'absolute_fail_reasons':['FULL_VALIDATION_COST_STRESS_2X_NONPOSITIVE'],
                },
                'last_error':'',
                'redevelopment_cycles_by_gate':{'FULL_VALIDATION':1},
            }],
        }
        pipeline_path.write_text(json.dumps(pipeline))
        self.save()
        out=self.base/'research-checkpoint-out'
        receipt=json.loads((out/'CHECKPOINT.json').read_text())
        snap=receipt['p0_stage_status']
        path='strategy_lab/results/v3/historical_314_pipeline_stage2.json'
        self.assertEqual(snap['pipeline_sha256'],receipt['files'][path]['sha256'])
        self.assertEqual(snap['stage_counts'],{'FULL_VALIDATION_REDEVELOPMENT_PENDING':1})
        self.assertEqual(snap['leading']['pipeline_stage'],'FULL_VALIDATION_REDEVELOPMENT_PENDING')
        self.assertTrue(snap['leading']['final_risk_gate']['pass'])
        self.assertFalse(snap['leading']['full_validation_gate']['pass'])
        self.assertEqual(snap['leading']['full_validation_gate']['reasons'],
                         ['FULL_VALIDATION_COST_STRESS_2X_NONPOSITIVE'])
        self.assertEqual(snap['research_full_and_sealed_blind_pass_count'],0)
        self.assertFalse(snap['automatic_registration_authorized'])
        self.assertEqual(snap['aws_final_certification'],'NOT_VERIFIED_BY_GITHUB_RESEARCH')
        with zipfile.ZipFile(out/'checkpoint.zip') as archive:
            self.assertEqual(archive.read(path),pipeline_path.read_bytes())

    def test_p0_snapshot_rejects_false_shadow_pass_and_missing_evidence(self):
        self.run_step('Preserve research checkpoint')
        receipt=json.loads((self.base/'research-checkpoint-out/CHECKPOINT.json').read_text())
        self.assertEqual(receipt['p0_stage_status']['status'],'NO_PIPELINE_EVIDENCE')
        pipeline={
            'entries':[{
                'strategy_id':'V3_EFFICIENCY_PULLBACK_RESUME',
                'exact_strategy_version':'EV_55a86e97dfadf81c_30M',
                'timeframe':'30M','eligible':True,
                'pipeline_stage':'LOCAL_SHADOW_PENDING',
                'full_validation_gate':{'pass':True,'decision':'PASS'},
                'sealed_final_blind_gate':{
                    'pass':True,'decision':'PASS','next_stage':'NOT_LOCAL_SHADOW',
                },
            }],
        }
        (self.state/'historical_314_pipeline_stage2.json').write_text(json.dumps(pipeline))
        self.run_step('Preserve research checkpoint')
        snap=json.loads((self.base/'research-checkpoint-out/CHECKPOINT.json').read_text())['p0_stage_status']
        self.assertEqual(snap['research_full_and_sealed_blind_pass_count'],0)
        self.assertFalse(snap['automatic_registration_authorized'])
        self.assertEqual(snap['aws_final_certification'],'NOT_VERIFIED_BY_GITHUB_RESEARCH')

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
    def publish(self,head='original',lease='original'):
        self.run_step('Preserve research checkpoint')
        if lease is not None:(self.base/'research-state-head.json').write_text(json.dumps({'head':lease}))
        spec=importlib.util.spec_from_file_location('checkpoint_knowledge',REPO/'strategy_lab/v3_research_knowledge.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.requests=[];remote={'head':head}
        def request(req,timeout):
            body=json.loads(req.data) if req.data else None
            path=req.full_url.split('/repos/owner/repo')[1]
            self.requests.append((path,body))
            if path=='/git/ref/heads/research-state':result={'object':{'sha':remote['head']}}
            elif path=='/git/commits':
                self.assertEqual(body['parents'],[remote['head']]);result={'sha':'new-commit'}
            elif path=='/git/refs/heads/research-state':
                self.assertIs(body['force'],False);remote['head']=body['sha'];result={}
            else:result={'sha':'object'}
            return io.StringIO(json.dumps(result))
        env=dict(self.env,GH_TOKEN='fixture',GITHUB_REPOSITORY='owner/repo',GITHUB_RUN_ATTEMPT='1')
        with patch.dict(os.environ,env),patch('urllib.request.urlopen',side_effect=request):module.publish_cloud_checkpoint()
    def test_chunked_upload_preserves_exact_archive_and_manifest(self):
        # Incompressible bytes force more than one Git Blob, without cloud writes.
        (self.state/'research_fixture.parquet').write_bytes(os.urandom(8*1024**2+2048))
        self.publish()
        posted=[body for path,body in self.requests if path=='/git/blobs']
        files=[base64.b64decode(body['content']) for body in posted]
        transports=[json.loads(raw) for raw in files if raw.startswith(b'{"archive_bytes":')]
        self.assertEqual(len(transports),1)
        manifest=transports[0]
        self.assertEqual(manifest['schema'],'B_BOT_RESEARCH_CHECKPOINT_PARTS_V1')
        self.assertGreaterEqual(len(manifest['parts']),2)
        uploaded_chunks=[raw for raw in files if len(raw)>=8*1024**2 or (raw and raw[:2] not in (b'{',b'PK'))]
        self.assertGreaterEqual(len(uploaded_chunks),2)
        self.assertEqual(
            hashlib.sha256((self.base/'research-checkpoint-out/checkpoint.zip').read_bytes()).hexdigest(),
            manifest['archive_sha256']
        )
        tree=next(body for path,body in self.requests if path=='/git/trees')
        self.assertEqual(
            {x['path'] for x in tree['tree']},
            {'CHECKPOINT.json','checkpoint.parts.json'}|{p['name'] for p in manifest['parts']}
        )
        self.assertNotIn('checkpoint.zip',{x['path'] for x in tree['tree']})

    def mocked_download(self, chunked=True, tamper=None):
        self.run_step('Preserve research checkpoint')
        source=self.base/'research-checkpoint-out'
        receipt=(source/'CHECKPOINT.json').read_bytes()
        archive=(source/'checkpoint.zip').read_bytes()
        blobs={'CHECKPOINT.json':receipt}
        if chunked:
            parts=[]
            for index,offset in enumerate(range(0,len(archive),8*1024**2)):
                raw=archive[offset:offset+8*1024**2]
                name=f'checkpoint.part{index:04d}'
                parts.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
                blobs[name]=raw
            transport={'schema':'B_BOT_RESEARCH_CHECKPOINT_PARTS_V1',
                'archive_sha256':hashlib.sha256(archive).hexdigest(),
                'archive_bytes':len(archive),'chunk_bytes':8*1024**2,
                'parts':parts}
            blobs['checkpoint.parts.json']=json.dumps(transport).encode()
        else:
            blobs['checkpoint.zip']=archive
        if tamper:
            blobs[tamper]=b'X'*len(blobs[tamper])
        objects={hashlib.sha256(name.encode()).hexdigest()[:40]:raw for name,raw in blobs.items()}
        tree=[{'path':name,'sha':hashlib.sha256(name.encode()).hexdigest()[:40],
               'type':'blob','size':len(raw),'mode':'100644'}
              for name,raw in blobs.items()]
        def get(request,timeout):
            endpoint=request.full_url.split('/repos/owner/repo',1)[1]
            if endpoint=='/git/ref/heads/research-state':
                payload={'object':{'sha':'h'*40}}
            elif endpoint=='/git/commits/'+'h'*40:
                payload={'tree':{'sha':'t'*40}}
            elif endpoint=='/git/trees/'+'t'*40:
                payload={'tree':tree}
            elif endpoint.startswith('/git/blobs/'):
                raw=objects[endpoint.rsplit('/',1)[-1]]
                payload={'encoding':'base64','size':len(raw),
                    'content':base64.b64encode(raw).decode()}
            else:
                raise AssertionError('unexpected '+endpoint)
            return io.StringIO(json.dumps(payload))
        env=dict(self.env,GH_TOKEN='fixture',GITHUB_REPOSITORY='owner/repo')
        with patch.dict(os.environ,env),patch('urllib.request.urlopen',side_effect=get),contextlib.redirect_stdout(io.StringIO()):
            self.run_step('Download checkpoint')
        return archive

    def test_chunked_restore_recreates_byte_identical_zip_then_exact_state(self):
        expected=self.mocked_download()
        downloaded=(self.base/'research-checkpoint-in/checkpoint.zip').read_bytes()
        self.assertEqual(downloaded,expected)
        self.run_step('Restore exact research state')
        self.assertEqual(json.loads((self.state/'trial_ledger.json').read_text())['entries'],[{'trial':1}])

    def test_legacy_checkpoint_still_restores(self):
        expected=self.mocked_download(chunked=False)
        self.assertEqual((self.base/'research-checkpoint-in/checkpoint.zip').read_bytes(),expected)
        self.run_step('Restore exact research state')

    def test_tampered_chunk_is_rejected_before_research_restore(self):
        with self.assertRaisesRegex(RuntimeError,'STATE_PART_HASH_MISMATCH'):
            self.mocked_download(tamper='checkpoint.part0000')

    def test_chunked_transport_extra_file_rejected(self):
        # Exercise strict published tree contract at extraction point.
        source=STEPS['Download checkpoint']['run']
        self.assertIn("set(entries)!=expected",source)
        self.assertIn("STATE_PART_SET_INVALID",source)
        self.assertIn("STATE_PART_HASH_MISMATCH",source)

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
