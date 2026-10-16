# 파일명: tests/test_github_connection.py
import ast,base64,json,os,subprocess,tempfile,time,types,unittest
from datetime import datetime
from pathlib import Path
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
CONNECT=next(n for n in ast.parse((LAB/'v3_auto_runtime_release.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='connect_github')
class GithubConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.lab=self.root/'strategy_lab';self.v3=self.lab/'results/v3';self.v3.mkdir(parents=True)
        self.config=self.lab/'lab_config.json';self.config.write_text('{"existing":true}')
        self.auto=self.lab/'v3_auto_runtime_release.py';self.auto.write_text('# old release code\n')
        self.dep=self.lab/'v3_deployment.py';self.dep.write_text('def source_for_pipeline(row):\n    return None\n\ndef existing():\n    return 17\n')
        self.controller=self.lab/'research_controller.py';self.controller.write_text('def managed_research_once(held):\n    return {"old":1}\n\ndef run_once(enable_research=True):\n    return {"old":2}\n')
        self.hold=self.root/'SERVER_PUSH_HOLD.flag';self.request=self.v3/'runtime_sync_request.json'
        self.events=[];self.fail=False;self.unknown=False
        self.sources={'v3_auto_runtime_release.py':b'# new release code\n','v3_deployment.py':b'def source_for_pipeline(row):\n    return row\n'}
        self.originals={p:p.read_bytes() for p in [self.config,self.auto,self.dep,self.controller]}
    def call(self):
        def run(cmd,timeout):return 0,base64.b64encode(self.sources[cmd[-3].split('/strategy_lab/')[1].split('?')[0]]).decode()
        def pull(*a,**kw):
            self.events.append('PULL')
            if self.fail:raise RuntimeError('READ_FAILED')
            return {'status':'WAIT_FINAL_RESEARCH','imported':0}
        def atomic(path,value):path.write_text(json.dumps(value))
        process=types.SimpleNamespace(run=lambda *a,**kw:self.events.append(('STOP',a[0][-1])) or types.SimpleNamespace(returncode=0),Popen=lambda *a,**kw:self.events.append('START'),CREATE_NO_WINDOW=0)
        ns={'ROOT':self.root,'REQUEST':self.request,'HOLD':self.hold,'Path':Path,'os':types.SimpleNamespace(name='nt',replace=os.replace),'json':json,'hashlib':__import__('hashlib'),'sha256':lambda p:'unknown' if self.unknown else 'da4e5ae9c5b9491bdfa1b13c65aad96cc6391c35f255d6a0547e50b0e7663fcf','run':run,'self_test':lambda:None,'subprocess':process,'datetime':datetime,'time':time,'atomic_json':atomic,'_github_pull_cache':{},'pull':pull}
        exec(compile(ast.Module(body=[CONNECT],type_ignores=[]),'actual_connect','exec'),ns)
        return ns['connect_github']('a'*40)
    def test_connection_preserves_other_functions_and_disables_only_local_research(self):
        self.assertEqual(self.call(),0)
        cfg=json.loads(self.config.read_text());self.assertTrue(cfg['existing']);self.assertTrue(cfg['github_research']['enabled'])
        self.assertIn('return 17',self.dep.read_text());self.assertEqual(self.controller.read_text().count('GITHUB_RESEARCH_OWNER'),4)
        stop=next(e[1] for e in self.events if isinstance(e,tuple));self.assertNotIn('research_controller',stop);self.assertNotIn('run_strategy',stop)
        self.assertEqual(self.events[-1],'START');self.assertFalse(self.request.exists())
        scripts=[e[1] for e in self.events if isinstance(e,tuple)]
        self.assertEqual(len(scripts),2);self.assertIn('local_continuous_research',scripts[1]);self.assertNotIn('v3_auto_runtime_release',scripts[1])
    def test_connection_failure_restores_all_original_files(self):
        self.fail=True;self.hold.write_text('ROADMAP_RESEARCH_V1|old')
        with self.assertRaisesRegex(RuntimeError,'READ_FAILED'):self.call()
        for p,raw in self.originals.items():self.assertEqual(p.read_bytes(),raw)
        self.assertEqual(self.hold.read_text(),'ROADMAP_RESEARCH_V1|old');self.assertEqual(self.events[-1],'START')
    def test_unknown_local_release_code_requires_review_before_stopping_worker(self):
        self.unknown=True
        with self.assertRaisesRegex(RuntimeError,'CHANGED_REVIEW_REQUIRED'):self.call()
        self.assertEqual(self.events,[])
    def test_manual_hold_is_never_cleared(self):
        self.hold.write_text('manual')
        with self.assertRaisesRegex(RuntimeError,'NONRESEARCH_HOLD_PRESERVED'):self.call()
        self.assertEqual(self.hold.read_text(),'manual');self.assertEqual(self.events,[])
    def test_pending_request_is_never_interrupted(self):
        self.request.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'MUST_FINISH_FIRST'):self.call()
        self.assertEqual(self.events,[])
