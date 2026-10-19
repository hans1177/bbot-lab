# 파일명: tests/test_runtime_feedback.py
import ast,hashlib,json,sqlite3,subprocess,tempfile,time,types,unittest
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
    def test_existing_systemd_restart_policy_is_observed_read_only(self):
        # 파일명: test_runtime_feedback.py / R4 복구 설정을 추정하지 않고 실제 systemd 출력만 기록한다.
        original=self.config.read_bytes()
        def systemd_and_git(command, **kwargs):
            if command[:3]==['systemctl','show','r4-runtime.service']:
                return types.SimpleNamespace(returncode=0,stdout='on-failure')
            return self.git(command,**kwargs)
        with patch.object(subprocess,'run',systemd_and_git):
            self.publish([], 'd'*40)
        runtime=self.payload('DIAGNOSTICS.json')['modules']['runtime']
        self.assertEqual(runtime['systemd_restart_policy'],'on-failure')
        self.assertEqual(self.config.read_bytes(),original)
        self.assertFalse((self.state/'history.db').exists())

    def test_systemd_restart_policy_unavailable_is_unknown(self):
        self.call()
        self.assertEqual(
            self.payload('DIAGNOSTICS.json')['modules']['runtime']['systemd_restart_policy'],
            'UNKNOWN',
        )

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
        self.call();manifest=self.payload('MANIFEST.json');self.assertEqual(set(manifest['files']),{'CONFIG.json','MARKET.json','HEARTBEAT.json','DIAGNOSTICS.json'});self.assertFalse(manifest['orders_touched']);self.assertFalse(manifest['database_opened']);self.assertTrue(manifest['database_read_only']);self.assertEqual(self.payload('DIAGNOSTICS.json')['modules']['collector']['state'],'HEARTBEAT_FRESH')
    def test_diagnostics_summarizes_order_exit_fill_recovery_and_storage_read_only(self):
        db=self.state/'history.db'
        with sqlite3.connect(db) as con:
            con.executescript("""
                CREATE TABLE r4_order_intents(id INTEGER PRIMARY KEY,status TEXT,updated_ts INTEGER);
                CREATE TABLE r4_exit_intents(id INTEGER PRIMARY KEY,status TEXT,updated_ts INTEGER);
                CREATE TABLE r4_fills(id INTEGER PRIMARY KEY,ts INTEGER);
                CREATE TABLE r4_leg_events(id INTEGER PRIMARY KEY,event_type TEXT,ts INTEGER);
                CREATE TABLE r4_trade_evidence(id INTEGER PRIMARY KEY,kind TEXT,evidence_complete INTEGER,hard_fail INTEGER,ts INTEGER);
                CREATE TABLE r4_reconciliation_events(id INTEGER PRIMARY KEY,result TEXT,ts INTEGER);
                CREATE TABLE r4_hard_safety_events(id INTEGER PRIMARY KEY,action TEXT,ts INTEGER);
                CREATE TABLE r4_basket_snapshots(mismatch INTEGER);
                INSERT INTO r4_order_intents VALUES(1,'AMBIGUOUS',100);
                INSERT INTO r4_exit_intents VALUES(1,'PARTIAL_FILLED',101);
                INSERT INTO r4_fills VALUES(1,102);
                INSERT INTO r4_leg_events VALUES(1,'EXIT_FILL',103);
                INSERT INTO r4_trade_evidence VALUES(1,'EXIT_FILL',0,1,104);
                INSERT INTO r4_reconciliation_events VALUES(1,'MISMATCH',105);
                INSERT INTO r4_hard_safety_events VALUES(1,'HARD_BLOCK',106);
                INSERT INTO r4_basket_snapshots VALUES(1);
            """)
        original=db.read_bytes()
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        self.assertEqual(diag['ledger']['state'],'READ_ONLY_OK')
        self.assertEqual(diag['modules']['order']['state'],'EVIDENCE_PRESENT')
        self.assertEqual(diag['ledger']['recent']['order']['by_type']['AMBIGUOUS'],1)
        self.assertEqual(diag['ledger']['recent']['exit']['by_type']['PARTIAL_FILLED'],1)
        self.assertEqual(diag['ledger']['recent']['entry_fill']['by_type']['RECORDED'],1)
        self.assertEqual(diag['ledger']['recent']['exit_fill']['by_type']['EXIT_FILL'],1)
        self.assertEqual(diag['ledger']['mismatched_baskets']['mismatch'],1)
        self.assertEqual(diag['ledger']['proofs']['incomplete'],1)
        self.assertEqual(diag['ledger']['proofs']['hard_fail'],1)
        self.assertEqual(diag['modules']['storage']['state'],'HARD_FAIL_EVIDENCE')
        self.assertEqual(db.read_bytes(),original)
        raw=json.dumps(diag)
        self.assertNotIn('client_order_id',raw)
        self.assertNotIn('API_KEY',raw)
    def test_missing_or_invalid_database_is_not_misreported_healthy(self):
        db=self.state/'history.db'
        self.call()
        self.assertEqual(self.payload('DIAGNOSTICS.json')['ledger']['state'],'MISSING')
        db.write_bytes(b'INVALID_SQLITE_BYTES')
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        self.assertEqual(diag['ledger']['state'],'UNAVAILABLE')
        self.assertEqual(diag['modules']['storage']['state'],'DATABASE_READ_FAILED')
    def test_log_diagnostics_uploads_allowlisted_tags_only(self):
        logfile=self.root/'logs/runner.log';logfile.parent.mkdir(exist_ok=True)
        logfile.write_text('[ERROR] [주문][R4][WS_KEEPALIVE_FAIL] sensitive=DoNotExport\n[주문][R4][ORDER_FAIL] key=private\n')
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        self.assertEqual(diag['errors']['tags']['WS_KEEPALIVE_FAIL'],1)
        self.assertEqual(diag['errors']['tags']['ORDER_FAIL'],1)
        self.assertNotIn('DoNotExport',json.dumps(diag))
        self.assertNotIn('private',json.dumps(diag))
    def test_bounded_ws_signal_and_telegram_diagnostics_do_not_export_log_contents(self):
        now=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime())
        iid='SI_test_fixture'
        config=json.loads(self.config.read_text())
        config['r4_runtime']={'strategy_instances':{iid:{
            'strategy_id':'V3_TEST','timeframe':'1H','tier':'CANARY',
            'enabled':True,'entry_enabled':True}}}
        self.config.write_text(json.dumps(config))
        (self.root/'server_status_telegram.py').write_text('# safe test stub\n')
        (self.root/'monitor.py').write_text('# safe test stub\n')
        log=self.root/'logs/runner.log';log.parent.mkdir(exist_ok=True)
        log.write_text('\n'.join((
            f'{now} [INFO] [주문][R4][WS_CONNECTED] token=SECRET_SHOULD_NOT_LEAK',
            f'{now} [INFO] [주문][R4][ENTRY_RESULT] instance={iid} symbol=BTCUSDT status=PROCESSED extra=SECRET_SHOULD_NOT_LEAK',
            f'{now} [INFO] [주문][R4][ENTRY_BLOCK] instance={iid} reason=ACCOUNT_MODE_GUARD',
            f'{now} [INFO] [주문][SERVER_STATUS_TELEGRAM] sent ok chars=150 elapsed=1.3s token=SECRET_SHOULD_NOT_LEAK',
            f'{now} [ERROR] [주문][SERVER_STATUS_TELEGRAM] fail err=https://api.telegram.org/botTOKEN_PRIVATE/sendMessage',
            ''
        )))
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        ws=diag['modules']['websocket']
        self.assertEqual(ws['state'],'LOG_EVENTS_OBSERVED')
        self.assertEqual(ws['log_event_counts']['WS_CONNECTED'],1)
        self.assertFalse(ws['direct_connection_heartbeat'])
        signal=diag['modules']['signal']
        self.assertEqual(signal['state'],'PARTIAL_LOG_EVIDENCE')
        self.assertEqual(signal['log_event_counts']['ENTRY_RESULT'],1)
        self.assertFalse(signal['direct_evaluation_heartbeat'])
        instance=diag['ledger']['instances'][0]
        self.assertEqual(instance['diagnostic_entry_results'],1)
        self.assertEqual(instance['diagnostic_entry_blocks'],1)
        self.assertFalse(instance['evaluation_heartbeat_observed'])
        tg=diag['modules']['telegram']
        self.assertEqual(tg['state'],'SEND_FAILURE_LOGGED')
        self.assertEqual(tg['send_ok_count'],1)
        self.assertEqual(tg['send_failure_count'],1)
        self.assertFalse(tg['message_delivery_verified'])
        self.assertIn('server_status_telegram.py',tg['source_files'])
        self.assertNotIn('SECRET_SHOULD_NOT_LEAK',json.dumps(diag))
        self.assertNotIn('TOKEN_PRIVATE',json.dumps(diag))
        self.assertNotIn('https://',json.dumps(diag))
    def test_no_log_does_not_invent_live_ws_telegram_or_signal_health(self):
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        self.assertEqual(diag['modules']['websocket']['state'],'NO_DIRECT_HEARTBEAT')
        self.assertEqual(diag['modules']['signal']['state'],'NO_DIRECT_EVIDENCE')
        self.assertEqual(diag['modules']['telegram']['state'],'SOURCE_NOT_FOUND')
        self.assertEqual(diag['modules']['telegram']['send_ok_count'],0)
    def test_ws_failure_only_is_not_reported_as_connected(self):
        log=self.root/'logs/runner.log';log.parent.mkdir(exist_ok=True)
        log.write_text('[ERROR] [주문][R4][WS_KEEPALIVE_FAIL] token=SECRET_SHOULD_NOT_LEAK\n')
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        ws=diag['modules']['websocket']
        self.assertEqual(ws['state'],'FAILURE_EVENTS_OBSERVED')
        self.assertFalse(ws['direct_connection_heartbeat'])
        self.assertEqual(ws['last_log_event_epoch'],0)
        self.assertNotIn('SECRET_SHOULD_NOT_LEAK',json.dumps(diag))
    def test_telegram_log_success_is_observation_not_delivery_confirmation(self):
        log=self.root/'logs/runner.log';log.parent.mkdir(exist_ok=True)
        log.write_text('[INFO] [주문][SERVER_STATUS_TELEGRAM] sent ok chars=100 elapsed=0.2s\n')
        self.call()
        diag=self.payload('DIAGNOSTICS.json')
        self.assertEqual(diag['modules']['telegram']['state'],'SEND_OK_LOGGED')
        self.assertFalse(diag['modules']['telegram']['message_delivery_verified'])
        self.assertEqual(diag['modules']['telegram']['last_send_ok_epoch'],0)
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
