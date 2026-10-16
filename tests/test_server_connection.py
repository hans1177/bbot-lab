# 파일명: tests/test_server_connection.py
import ast,base64,hashlib,json,os,subprocess,tempfile,types,unittest
from pathlib import Path
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
FN=next(n for n in ast.parse((LAB/'v3_auto_runtime_release.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_server')
class ServerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.key=self.root/'han.pem';self.key.write_text('fixture private credential never uploaded')
        self.calls=[];self.private=True;self.registered_read_only=True;self.service='active';self.ref=False;self.bad_file=False
    def call(self):
        names=('r4_runtime_service.py','strategy_runtime.py','strategy_leg_ledger.py','multi_strategy_runtime.py','r4_exchange_adapter.py','r4_user_stream.py','assist/cycle.py','assist/strategy_registry.py')
        raw=b'# fixture runtime source\n'
        files={n:{'content':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)} for n in names}
        if self.bad_file:files['config.json']={'content':'','sha256':'','bytes':0}
        def run(command,**kwargs):
            self.calls.append((command,kwargs.get('input')))
            if command[0]=='ssh':
                source=kwargs['input'];compile(source,'server_source','exec')
                if 'ssh-keygen' in source:value={'public_key':'ssh-ed25519 '+base64.b64encode(b'public fixture').decode()+' bbot-lab-read'}
                else:value={'service':self.service,'files':files}
            else:
                path=command[2].removeprefix('repos/hans1177/bbot-lab');data=json.loads(kwargs['input']) if kwargs.get('input') else None
                if path=='':value={'private':self.private}
                elif path=='/keys?per_page=100':value=[]
                elif path=='/keys':value={'id':1,'read_only':self.registered_read_only}
                elif path=='/git/ref/heads/aws-runtime-source':
                    if not self.ref:return types.SimpleNamespace(returncode=1,stdout='',stderr='HTTP 404')
                    value={'object':{'sha':'a'*40}}
                elif path=='/git/blobs':value={'sha':'b'*40}
                elif path=='/git/trees':value={'sha':'c'*40}
                elif path=='/git/commits':value={'sha':'a'*40}
                elif path=='/git/refs':self.ref=True;value={}
                else:raise AssertionError(path)
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        ns={'Path':lambda value:self.key if value==r'C:\my\han.pem' else Path(value),'os':types.SimpleNamespace(name='nt'),'subprocess':types.SimpleNamespace(run=run),'json':json,'hashlib':hashlib,'now':lambda:'NOW'}
        exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_prepare','exec'),ns)
        return ns['prepare_server']()
    def test_server_read_key_and_only_source_snapshot_registered(self):
        self.assertEqual(self.call(),0)
        payloads=[json.loads(value) for command,value in self.calls if command[0]=='gh' and value]
        key=next(p for p in payloads if 'read_only' in p);self.assertIs(key['read_only'],True)
        tree=next(p for p in payloads if 'tree' in p and isinstance(p['tree'],list))
        self.assertEqual(len(tree['tree']),9);self.assertTrue(all(row['path'].startswith('runtime/') or row['path']=='PREFLIGHT.json' for row in tree['tree']))
        self.assertNotIn(self.key.read_text(),json.dumps(payloads));self.assertFalse(any('systemctl restart' in (value or '') for _,value in self.calls))
    def test_public_repository_aborts_before_ssh(self):
        self.private=False
        with self.assertRaisesRegex(RuntimeError,'PRIVATE_RESEARCH'):self.call()
        self.assertFalse(any(c[0][0]=='ssh' for c in self.calls))
    def test_write_enabled_key_not_accepted(self):
        self.registered_read_only=False
        with self.assertRaisesRegex(RuntimeError,'READ_ONLY_NOT_VERIFIED'):self.call()
        self.assertFalse(self.ref)
    def test_inactive_service_not_accepted(self):
        self.service='inactive'
        with self.assertRaisesRegex(RuntimeError,'R4_RUNTIME_NOT_ACTIVE'):self.call()
        self.assertFalse(self.ref)
    def test_operating_config_cannot_be_uploaded(self):
        self.bad_file=True
        with self.assertRaisesRegex(RuntimeError,'FILE_SET_INVALID'):self.call()
        self.assertFalse(self.ref)
    def test_remote_sources_compile_and_pin_official_host_key(self):
        for node in ast.walk(FN):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='remote':
                expression=compile(ast.Expression(node.args[0]),'actual_remote_expression','eval')
                source=eval(expression,{'__builtins__':{},'repr':repr,'list':list,'legacy':{'assist/strategies/div.py':'f'*64}})
                compile(source,'actual_remote','exec')
        public='AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
        self.assertEqual('SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(public)).digest()).decode().rstrip('='),'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU')
