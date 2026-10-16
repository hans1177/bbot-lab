# 파일명: tests/test_runtime_feedback.py
import ast,hashlib,json,subprocess,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
FN=next(n for n in ast.parse((ROOT/'runtime/strategy_runtime.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='publish_github_feedback')
class RuntimeFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir();self.calls=[];self.blobs={}
        (self.state/'r4_github_receiver.json').write_text(json.dumps({'feedback_enabled':True}));(self.state/'github_runtime.git').mkdir()
        self.heartbeat=self.root/'strategy_lab/results/v3/collector_heartbeat.json';self.heartbeat.parent.mkdir(parents=True);self.heartbeat.write_text(json.dumps({'schema':'B_BOT_V3_COLLECTOR_HEARTBEAT_V1','updated_at_epoch':time.time(),'active_symbols':['BTCUSDT']}))
        self.config=self.root/'strategy_config.json';self.config.write_text(json.dumps({'core_symbols':['BTCUSDT'],'leverage_policy':{'min':20,'absolute_max':30}}))
        path=self.root/'backtest_data/BTCUSDT/1h.parquet';path.parent.mkdir(parents=True);self.candle=path
        times=pd.date_range(end=pd.Timestamp.now(tz='UTC').floor('h')-pd.Timedelta(hours=1),periods=160,freq='h')
        self.data=pd.DataFrame({'open_time':times,'open':100.,'high':102.,'low':99.,'close':101.,'volume':10.,'is_closed':True});self.data.to_parquet(path,index=False)
        ns={'BASE':self.root,'Path':Path,'json':json,'os':__import__('os')};exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_publisher','exec'),ns);self.publish=ns['publish_github_feedback']
    def git(self,command,**kwargs):
        self.calls.append((command,kwargs));op=command[2]
        if op=='hash-object':
            raw=kwargs['input'];sha=hashlib.sha1(raw).hexdigest();self.blobs[sha]=raw;out=sha
        elif op=='ls-remote':out='a'*40+'\trefs/heads/runtime-feedback'
        elif op=='mktree':
            self.tree=kwargs['input'].decode();out='b'*40
        elif op=='commit-tree':out='c'*40
        else:out=''
        return types.SimpleNamespace(returncode=0,stdout=out.encode())
    def call(self):
        with patch.object(subprocess,'run',self.git):return self.publish([], 'd'*40)
    def payload(self,name):
        line=next(line for line in self.tree.splitlines() if line.endswith('\t'+name));return json.loads(self.blobs[line.split()[2]])
    def test_feedback_contains_closed_bars_and_changes_no_operating_config_or_db(self):
        history=self.state/'history.db';history.write_bytes(b'OPERATING_DB_NOT_OPENED');before=self.config.read_bytes();result=self.call();self.assertEqual(result['status'],'PUBLISHED');self.assertEqual(self.config.read_bytes(),before);self.assertEqual(history.read_bytes(),b'OPERATING_DB_NOT_OPENED')
        self.assertEqual(len(self.payload('MARKET.json')['bars']['BTCUSDT']['1H']),160)
        push=next(command for command,_ in self.calls if command[2]=='push');self.assertIn('--force-with-lease=refs/heads/runtime-feedback:'+'a'*40,push);self.assertTrue(push[-1].endswith(':refs/heads/runtime-feedback'));self.assertNotIn('refs/heads/main',' '.join(push))
        commit=next(command for command,_ in self.calls if command[2]=='commit-tree');self.assertNotIn('-p',commit)
    def test_stale_collector_cannot_publish(self):
        heartbeat=json.loads(self.heartbeat.read_text());heartbeat['updated_at_epoch']=time.time()-181;self.heartbeat.write_text(json.dumps(heartbeat))
        with self.assertRaisesRegex(RuntimeError,'COLLECTOR_NOT_LIVE'):self.call()
        self.assertEqual(self.calls,[])
    def test_credential_field_never_uploaded(self):
        self.config.write_text(json.dumps({'core_symbols':['BTCUSDT'],'api_key':'must-stay-on-server'}))
        with self.assertRaisesRegex(RuntimeError,'CREDENTIAL_FIELD'):self.call()
        self.assertEqual(self.calls,[])
    def test_invalid_ohlc_never_uploaded(self):
        self.data.loc[159,'high']=90.;self.data.to_parquet(self.candle,index=False)
        with self.assertRaisesRegex(RuntimeError,'INVALID_CLOSED_BAR'):self.call()
        self.assertEqual(self.calls,[])
    def test_in_progress_bar_is_excluded(self):
        future=self.data.iloc[[-1]].copy();future['open_time']=future['open_time']+pd.Timedelta(hours=1);future['is_closed']=False;pd.concat([self.data,future]).to_parquet(self.candle,index=False)
        self.call();self.assertEqual(len(self.payload('MARKET.json')['bars']['BTCUSDT']['1H']),160)
    def test_same_slot_reuses_market_blob_and_deduplicates_log(self):
        self.call();market=self.payload('MANIFEST.json')['files']['MARKET.json']['sha256'];log=self.root/'logs/runner.log';before=log.read_bytes()
        with patch.object(pd,'read_parquet',side_effect=AssertionError('must reuse bounded cache')):self.call()
        self.assertEqual(self.payload('MANIFEST.json')['files']['MARKET.json']['sha256'],market);self.assertEqual(log.read_bytes(),before)
    def test_config_and_market_are_separate_content_addressed_blobs(self):
        self.call();manifest=self.payload('MANIFEST.json');self.assertEqual(set(manifest['files']),{'CONFIG.json','MARKET.json','HEARTBEAT.json'});self.assertFalse(manifest['orders_touched']);self.assertFalse(manifest['database_opened'])
class FeedbackKeyTests(unittest.TestCase):
    def test_only_explicit_feedback_setup_registers_public_write_key(self):
        import base64
        tree=ast.parse((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_server_receiver')
        public='ssh-ed25519 '+base64.b64encode(b'public key fixture').decode();calls=[];created=[]
        def run(command,**kwargs):
            calls.append((command,kwargs))
            if command[0]=='ssh':
                compile(kwargs['input'],'actual_key_setup','exec');value={'public_key':public}
            elif command[2]=='repos/hans1177/bbot-lab':value={'private':True}
            elif command[2].endswith('/keys?per_page=100'):value=[]
            elif command[2].endswith('/keys'):
                created.append(json.loads(kwargs['input']));value={'read_only':False}
            elif '/contents/runtime/' in command[2]:raise RuntimeError('STOP_AFTER_KEY_CHECK')
            else:raise AssertionError(command)
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        ns={'os':types.SimpleNamespace(name='nt'),'subprocess':types.SimpleNamespace(run=run),'json':json}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_install','exec'),ns)
        with self.assertRaisesRegex(RuntimeError,'STOP_AFTER_KEY_CHECK'):ns['install_server_receiver']('a'*40,enable_feedback=True)
        self.assertEqual(created,[{'title':'bbot-runtime-feedback','key':public,'read_only':False}]);self.assertNotIn('PRIVATE KEY',json.dumps(created))
        calls.clear();created.clear()
        with self.assertRaisesRegex(RuntimeError,'STOP_AFTER_KEY_CHECK'):ns['install_server_receiver']('a'*40)
        self.assertEqual(created,[]);self.assertFalse(any(command[0]=='ssh' for command,_ in calls))
