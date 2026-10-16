# 파일명: tests/test_cloud_release.py
import ast,hashlib,json,os,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
FN=next(n for n in ast.parse((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='tick')
class CloudReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.results=self.root/'strategy_lab/results/v3';self.results.mkdir(parents=True)
        self.request=self.results/'runtime_sync_request.json';self.req={'request_id':'VALIDATED_1','strategy_version':'EV_EXACT_1H','strategy_instance_ids':['new'],'tier':'CANARY','version_kind':'STRATEGY'};self.request.write_text(json.dumps(self.req))
        self.config=self.root/'strategy_config.json';self.config.write_text(json.dumps({'r4_runtime':{'strategy_instances':{}}}))
        self.receipt={'status':'SERVER_FEEDBACK_IMPORTED','config_sha256':hashlib.sha256(self.config.read_bytes()).hexdigest(),'source_heartbeat_epoch':time.time()};(self.results/'runtime_feedback_receipt.json').write_text(json.dumps(self.receipt))
        self.pull=Mock(return_value=self.receipt);self.verify=Mock();self.apply=Mock();self.state=Mock();self.deploy=types.SimpleNamespace(mark_applied=Mock())
        self.ns={'Path':Path,'ROOT':self.root,'RESULTS':self.results,'REQUEST':self.request,'HOLD':self.root/'SERVER_PUSH_HOLD.flag','STATE':self.results/'state.json','LAST_APPLIED':self.results/'runtime_sync_last_applied.json','roadmap_policy':Mock(),'pull':self.pull,'state':self.state,'log':Mock(),'read_request':lambda:json.loads(self.request.read_bytes()),'validate_request':lambda r:r['request_id'],'verify_local':self.verify,'sha256':lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),'hard_stop':Mock(),'apply_runtime_sync':self.apply,'v3_deployment':self.deploy,'os':os,'json':json,'time':time}
        exec(compile(ast.Module(body=[FN],type_ignores=[]),'actual_tick','exec'),self.ns);self.tick=self.ns['tick']
    def call(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'hans1177/bbot-lab'}):return self.tick(cloud_validation=True)
    def test_six_existing_verifiers_precede_cloud_approval_without_native_sync(self):
        self.assertEqual(self.call(),'VALIDATED_FOR_SERVER');self.verify.assert_called_once_with();self.apply.assert_not_called();self.pull.assert_called_once_with('GITHUB_CLOUD_VALIDATION',runtime_feedback=True);status=self.state.call_args;self.assertEqual(status.args[0],'VALIDATED_FOR_SERVER');self.assertEqual(status.kwargs['canonical_verifiers'],6);self.assertEqual(status.kwargs['request_sha256'],hashlib.sha256(self.request.read_bytes()).hexdigest())
    def test_failed_verification_never_approves_or_applies(self):
        self.verify.side_effect=RuntimeError('CANONICAL_FAIL');self.assertEqual(self.call(),'HARD_STOP');self.apply.assert_not_called();self.ns['hard_stop'].assert_called_once()
    def test_stale_feedback_waits_without_approval(self):
        self.receipt['source_heartbeat_epoch']=time.time()-181;(self.results/'runtime_feedback_receipt.json').write_text(json.dumps(self.receipt));self.assertEqual(self.call(),'WAIT_SERVER_FEEDBACK');self.apply.assert_not_called()
    def test_source_feedback_waits_before_request_processing(self):
        self.pull.return_value={'status':'WAIT_FRESH_SERVER_FEEDBACK'};self.assertEqual(self.call(),'WAIT_SERVER_FEEDBACK');self.verify.assert_not_called();self.apply.assert_not_called()
    def test_server_confirmation_consumes_request_without_resetting_tier(self):
        row={'exact_strategy_version':'EV_EXACT_1H','tier':'NORMAL','server_activation_authorized':True,'approved_for_server_release':True};raw=json.dumps({'r4_runtime':{'strategy_instances':{'new':row}}}).encode();self.config.write_bytes(raw)
        self.assertEqual(self.call(),'SERVER_APPLICATION_CONFIRMED');self.assertFalse(self.request.exists());self.assertEqual(self.config.read_bytes(),raw);self.deploy.mark_applied.assert_called_once_with(self.req);self.apply.assert_not_called();self.verify.assert_not_called()
    def test_cloud_mode_rejected_outside_github(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}):
            with self.assertRaisesRegex(RuntimeError,'ISOLATED_GITHUB'):self.tick(cloud_validation=True)
        self.pull.assert_not_called()
