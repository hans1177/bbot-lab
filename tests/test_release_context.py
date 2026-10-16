# 파일명: tests/test_release_context.py
import ast,base64,hashlib,json,os,tempfile,types,unittest
from pathlib import Path
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
SOURCE=(LAB/'v3_auto_runtime_release.py').read_text()
FN=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_server')
class ReleaseContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.key=self.root/'han.pem';self.key.write_text('fixture never uploaded');self.calls=[]
        self.report={'service':'active','files':{},'missing':[],'blocked':[]};self.private=True
        (self.root/'verify_v3.py').write_text('expected='+repr({'assist/strategies/'+name+'.py':'f'*64 for name in ('div','ema_cross','macd','obv','sar','shape','stoch')}))
    def call(self):
        def run(command,**kwargs):
            self.calls.append(command)
            if command[0]=='ssh':
                compile(kwargs['input'],'actual_remote_audit','exec');value=self.report
            elif len(command)==3:value={'private':self.private}
            else:raise AssertionError('Audit mutated GitHub before validation completed')
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        ns={'Path':lambda p:self.key if p==r'C:\my\han.pem' else Path(p),'ROOT':self.root,'os':types.SimpleNamespace(name='nt'),'subprocess':types.SimpleNamespace(run=run),'json':json,'hashlib':hashlib,'now':lambda:'NOW'}
        exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_preparation','exec'),ns);return ns['prepare_server'](release_context=True)
    def add(self,name,raw):self.report['files'][name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'content':base64.b64encode(raw).decode()}
    def test_missing_frozen_sources_abort_before_upload(self):
        with self.assertRaisesRegex(RuntimeError,'FROZEN_SOURCE_MISSING'):self.call()
    def test_changed_frozen_source_aborts_before_upload(self):
        self.add('runner.py',b'# changed kernel\n')
        with self.assertRaisesRegex(RuntimeError,'FROZEN_CORE_CHANGED'):self.call()
    def test_unlisted_path_aborts_before_upload(self):
        self.add('../secret.py',b'# outside\n')
        with self.assertRaisesRegex(RuntimeError,'PATH_INVALID'):self.call()
    def test_public_repository_aborts_before_ssh(self):
        self.private=False
        with self.assertRaisesRegex(RuntimeError,'PRIVATE_RESEARCH'):self.call()
        self.assertFalse(any(c[0]=='ssh' for c in self.calls))
    def test_stopped_r4_aborts(self):
        self.report['service']='inactive'
        with self.assertRaisesRegex(RuntimeError,'R4_RUNTIME_NOT_ACTIVE'):self.call()
    def test_remote_only_reads_source_and_queries_service(self):
        script=next(n.value for n in ast.walk(FN) if isinstance(n,ast.Constant) and isinstance(n.value,str) and "queue=['runner.py'" in n.value)
        tree=ast.parse(script)
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('write_text','write_bytes','unlink','connect') for n in ast.walk(tree)))
        self.assertNotIn('sqlite',script);self.assertNotIn('restart',script);self.assertIn("'secrets.py','credentials.py'",script)
    def run_remote(self,unsafe=False):
        import contextlib,io,subprocess
        from unittest.mock import patch
        (self.root/'assist').mkdir();(self.root/'assist/__init__.py').write_text('')
        (self.root/'runner.py').write_text('import assist.daemon\nimport config\nimport credentials\n')
        (self.root/'assist/daemon.py').write_text('from . import cycle\n')
        (self.root/'assist/cycle.py').write_text('# dependency\n')
        (self.root/'config.py').write_text('class Config: pass\n')
        (self.root/'credentials.py').write_text('api_key="NEVER_EXPORT_THIS_CREDENTIAL_123"\n')
        (self.root/'strategy_config.json').write_text(json.dumps({'research':{'trial_budget_required':True},**({'api_key':'THIS_IS_A_SECRET_VALUE_123456789'} if unsafe else {})}))
        script=next(n.value for n in ast.walk(FN) if isinstance(n,ast.Constant) and isinstance(n.value,str) and "queue=['runner.py'" in n.value)
        tree=ast.parse(script)
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and node.value=='/home/ec2-user/bot':node.value=str(self.root)
        output=io.StringIO()
        with patch.object(subprocess,'run',lambda *a,**k:types.SimpleNamespace(stdout='active')),contextlib.redirect_stdout(output):exec(compile(tree,'actual_remote','exec'),{'legacy_names':[]})
        return json.loads(output.getvalue())
    def test_remote_follows_relative_dependencies_and_never_exports_credentials_module(self):
        report=self.run_remote();self.assertIn('assist/cycle.py',report['files']);self.assertIn('credentials.py',report['blocked']);self.assertNotIn('credentials.py',report['files']);self.assertIn('config.py',report['files']);self.assertNotIn('config.json',report['files']);self.assertFalse(report['database_opened'])
    def test_remote_rejects_credentials_in_strategy_config(self):
        with self.assertRaisesRegex(RuntimeError,'CONFIG_SNAPSHOT_UNSAFE'):self.run_remote(unsafe=True)
