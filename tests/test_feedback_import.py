# 파일명: tests/test_feedback_import.py
import ast,base64,hashlib,io,json,os,sys,tempfile,time,types,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
import pandas.core.arrays.arrow.extension_types  # sys.modules mock 복원 전에 Arrow 타입 등록
ROOT=Path(__file__).resolve().parents[1]
FN=next(n for n in ast.parse((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='pull')
class FeedbackImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.results=self.root/'strategy_lab/results/v3';self.results.mkdir(parents=True);self.request=self.results/'runtime_sync_request.json';self.calls=[];self.resolved=[]
        opening=int(time.time()//3600)*3600000-3600000
        self.heartbeat={'schema':'B_BOT_V3_COLLECTOR_HEARTBEAT_V1','updated_at_epoch':time.time()-30,'active_symbols':['BTCUSDT']}
        self.config={'core_symbols':['BTCUSDT']};self.market={'schema':'B_BOT_PUBLIC_CLOSED_BARS_V1','columns':['open_time_ms','open','high','low','close','volume'],'bars':{'BTCUSDT':{'1H':[[opening-(64-i)*3600000,100,102,99,101,10] for i in range(65)]}}}
        self.stamp=time.time();self.corrupt=False;self.missing=False
        ns={'ROOT':self.root,'RESULTS':self.results,'REQUEST':self.request,'Path':Path,'os':os,'json':json,'hashlib':hashlib,'time':time}
        exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_pull','exec'),ns);self.pull=ns['pull']
    def fetch(self,request,**kwargs):
        self.calls.append(request);path=request.full_url.split('repos/hans1177/bbot-lab',1)[1]
        raw={'CONFIG.json':json.dumps(self.config).encode(),'HEARTBEAT.json':json.dumps(self.heartbeat).encode(),'MARKET.json':json.dumps(self.market).encode()}
        manifest={'schema':'B_BOT_GITHUB_RUNTIME_FEEDBACK_V1','created_at_epoch':self.stamp,'received_commit':'d'*40,'files':{n:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in raw.items()}}
        raw['MANIFEST.json']=json.dumps(manifest).encode()
        if self.corrupt:raw['MARKET.json']+=b' '
        names={str(i)*40:n for i,n in enumerate(raw,1)}
        if path=='':value={'private':True}
        elif path=='/git/ref/heads/runtime-feedback':
            if self.missing:raise urllib.error.HTTPError(request.full_url,404,'missing',{},None)
            value={'object':{'sha':'a'*40}}
        elif path=='/git/ref/heads/runtime-candidates':value={'object':{'sha':'d'*40}}
        elif path.startswith('/git/trees/'):
            value={'tree':[{'path':name,'sha':sha,'type':'blob','mode':'100644','size':len(raw[name])} for sha,name in names.items()],'truncated':False}
        elif path.startswith('/git/blobs/'):
            value={'encoding':'base64','content':base64.b64encode(raw[names[path.rsplit('/',1)[1]]]).decode()}
        else:raise AssertionError(path)
        return io.BytesIO(json.dumps(value).encode())
    def call(self):
        env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'hans1177/bbot-lab','GH_TOKEN':'fixture-read-token'}
        with patch.dict(os.environ,env),patch.object(urllib.request,'urlopen',self.fetch),patch.dict(sys.modules,{'v3_collector_required_universe':types.SimpleNamespace(main=lambda:self.resolved.append(True))}):return self.pull('GITHUB_RUNTIME',runtime_feedback=True)
    def test_real_closed_bar_timestamps_and_source_heartbeat_are_preserved(self):
        result=self.call();self.assertEqual(result['status'],'SERVER_FEEDBACK_IMPORTED');self.assertEqual(result['source_heartbeat_epoch'],self.heartbeat['updated_at_epoch']);self.assertEqual(json.loads((self.root/'strategy_config.json').read_text()),self.config);self.assertEqual(self.resolved,[True])
        import pandas as pd
        frame=pd.read_parquet(self.root/'data/candles/BTCUSDT/1h.parquet');self.assertTrue(frame['is_closed'].all());self.assertEqual(int(frame['open_time'].iloc[-1].timestamp()*1000),self.market['bars']['BTCUSDT']['1H'][-1][0]);self.assertFalse((self.root/'config.json').exists());self.assertFalse((self.root/'state/history.db').exists());self.assertTrue(all(request.get_method()=='GET' for request in self.calls))
    def test_hash_mismatch_writes_nothing(self):
        self.corrupt=True
        with self.assertRaisesRegex(RuntimeError,'HASH_MISMATCH'):self.call()
        self.assertFalse((self.root/'strategy_config.json').exists());self.assertEqual(self.resolved,[])
    def test_stale_heartbeat_is_not_refreshed_locally(self):
        self.heartbeat['updated_at_epoch']=time.time()-181;self.assertEqual(self.call()['status'],'WAIT_SERVER_COLLECTOR');self.assertFalse((self.root/'strategy_config.json').exists())
    def test_missing_feedback_waits_without_resetting_research(self):
        self.missing=True;self.assertEqual(self.call()['status'],'WAIT_SERVER_FEEDBACK');self.assertFalse((self.root/'strategy_config.json').exists())
    def test_credentials_in_config_are_rejected_before_write(self):
        self.config['api_key']='never-import-credential'
        with self.assertRaisesRegex(RuntimeError,'CREDENTIAL_FIELD'):self.call()
        self.assertFalse((self.root/'strategy_config.json').exists())
    def test_server_bars_are_imported_only_after_fixed_window_research(self):
        workflow=(ROOT/'.github/workflows/research-run.yml').read_text()
        research=workflow.index('      - name: Run existing research controller')
        feedback=workflow.index('      - name: Read fresh server market feedback')
        handoff=workflow.index('      - name: Prepare runtime candidate handoff')
        self.assertLess(research,feedback)
        self.assertLess(feedback,handoff)

    def test_existing_request_preserved_without_network_calls(self):
        raw=b'{"approved_for_server_release":true}';self.request.write_bytes(raw);self.assertEqual(self.call()['status'],'WAIT_EXISTING_RUNTIME_REQUEST');self.assertEqual(self.request.read_bytes(),raw);self.assertEqual(self.calls,[])
