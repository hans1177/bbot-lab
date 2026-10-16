# 파일명: tests/test_server_registration.py
import hashlib,json,subprocess,time,types,unittest
from pathlib import Path
from unittest.mock import patch
import test_server_receiver as receiver_fixture

class ServerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.f=receiver_fixture.ReceiverTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.state=self.root/'state';self.config=self.root/'strategy_config.json';self.calls=[];self.die=False
        self.active={'frames':{'1H':{'strategy':'OLD'}},'leverage_policy':{'min':20,'absolute_max':30},'r4_runtime':{'common_leverage_policy':{'effective_live_exchange_leverage_x':30},'strategy_instances':{'old':{'strategy_id':'V3_FIXTURE','timeframe':'1H','tier':'NORMAL','enabled':True,'entry_enabled':True}}},'strategy_registry':{'OLD':{'file':'old.py'}}}
        self.current=json.dumps(self.active).encode();self.config.write_bytes(self.current)
        self.merged=json.loads(self.current);self.merged['r4_runtime']['strategy_instances']['old'].update(entry_enabled=False,superseded_by_strategy_instance_id='new')
        self.merged['r4_runtime']['strategy_instances']['new']={'strategy_instance_id':'new','strategy_id':'V3_FIXTURE','timeframe':'1H','exact_strategy_version':'EV_EXACT_1H','strategy_policy_bundle_version':'BUNDLE','tier':'CANARY','qualification':'CANARY','capital_weight':0.2,'enabled':True,'entry_enabled':True,'server_activation_authorized':True,'approved_for_server_release':True,'policy_snapshot':{'preserved':'policy'}}
        self.merged['strategy_registry']['V3_FIXTURE']={'file':'exact.py'}
        self.request={'schema':'B_BOT_V3_RUNTIME_SYNC_REQUEST_V1','request_id':'REQUEST_1','strategy':'V3_FIXTURE','timeframe':'1H','strategy_version':'EV_EXACT_1H','strategy_policy_bundle_version':'BUNDLE','tier':'CANARY','version_kind':'STRATEGY','constitution_hash':'c'*64,'strategy_instance_ids':['new'],'autonomous_rule_passed':True,'core_mutation':False}
        candidate=self.f.manifest['candidates'][0];candidate['exact_strategy_version']='EV_EXACT_1H';candidate['research_evidence']['exact_strategy_version']='EV_EXACT_1H'
        self.release={'schema':'B_BOT_R4_VALIDATED_RELEASE_MANIFEST_V1',**{k:self.request[k] for k in ('request_id','strategy','timeframe','strategy_version','strategy_policy_bundle_version','tier','version_kind','constitution_hash','strategy_instance_ids')},'created_at_epoch':time.time(),'base_config_sha256':hashlib.sha256(self.current).hexdigest(),'runtime_strategy_file':'exact.py','runtime_artifact':self.f.name,'runtime_sha256':hashlib.sha256(self.f.artifact).hexdigest(),'approved_for_server_release':True,'local_shadow_pass':True,'canonical_verifiers':6,'core_mutation':False}
        self.f.manifest.update(release=self.release,automatic_registration_authorized=True)
        self.f.settings['automatic_registration_enabled']=True
        (self.state/'r4_github_receiver.json').write_text(json.dumps(self.f.settings))
        self.frozen={'runner.py':'4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160','order.py':'513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c','risk_engine.py':'768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6','candidate.py':'b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac'}
        for name in self.frozen:(self.root/name).write_bytes(('fixture-frozen-'+name).encode())
        self.database=self.state/'history.db';self.database.write_bytes(b'OPERATING_DATABASE_UNCHANGED')
        self.real_hash=hashlib.sha256
    def hash(self,data=b''):
        for name,digest in self.frozen.items():
            if data==('fixture-frozen-'+name).encode():return types.SimpleNamespace(hexdigest=lambda digest=digest:digest)
        return self.real_hash(data)
    def run_command(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='systemctl':
            if args[1]=='show':out='999' if self.die and self.config.read_bytes()!=self.current else '123';return types.SimpleNamespace(returncode=0,stdout=out)
            return types.SimpleNamespace(returncode=3 if args[-1]=='runner.service' else 0,stdout='')
        data={'MANIFEST.json':json.dumps(self.f.manifest).encode(),self.f.name:self.f.artifact,**self.payloads}
        op=args[2]
        if op=='rev-parse':out=self.f.revision.encode()
        elif op=='ls-tree':out='\n'.join(data).encode()
        elif op=='cat-file':out=str(len(data[args[-1].split(':',1)[1]])).encode()
        elif op=='show':out=data[args[-1].split(':',1)[1]]
        else:out=b''
        return types.SimpleNamespace(returncode=0,stdout=out)
    def call(self):
        self.payloads={'release/CONFIG.json':json.dumps(self.merged).encode(),'release/REQUEST.json':json.dumps(self.request).encode()}
        for name,key in [('release/CONFIG.json','strategy_config_sha256'),('release/REQUEST.json','request_sha256')]:
            raw=self.payloads[name];digest=self.real_hash(raw).hexdigest();self.f.manifest['files'][name]={'bytes':len(raw),'sha256':digest};self.release[key]=digest
        with patch.object(subprocess,'run',self.run_command),patch.object(hashlib,'sha256',self.hash),patch.object(time,'sleep',lambda *a:None):return self.f.receive()
    def test_verified_release_adds_only_canary_and_preserves_database_and_leverage(self):
        result=self.call();self.assertEqual(result['status'],'APPLIED');self.assertTrue(result['operating_config_changed']);self.assertEqual(json.loads(self.config.read_bytes()),self.merged);self.assertEqual(self.database.read_bytes(),b'OPERATING_DATABASE_UNCHANGED');self.assertEqual((self.root/'assist/strategies/exact.py').read_bytes(),self.f.artifact);self.assertFalse(any('restart' in cmd for cmd in self.calls))
    def test_disabled_registration_stages_without_config_change(self):
        self.f.settings['automatic_registration_enabled']=False;(self.state/'r4_github_receiver.json').write_text(json.dumps(self.f.settings));self.assertEqual(self.call()['status'],'WAIT_SERVER_VALIDATION');self.assertEqual(self.config.read_bytes(),self.current)
    def test_changed_live_config_waits_without_writing(self):
        self.release['base_config_sha256']='0'*64;self.assertEqual(self.call()['status'],'WAIT_LIVE_CONFIG_CHANGED');self.assertEqual(self.config.read_bytes(),self.current)
    def test_missing_shadow_pass_blocks_release(self):
        self.release['local_shadow_pass']=False
        with self.assertRaisesRegex(RuntimeError,'CANONICAL_GATES'):self.call()
        self.assertEqual(self.config.read_bytes(),self.current)
    def test_missing_canonical_verifier_blocks_release(self):
        self.release['canonical_verifiers']=5
        with self.assertRaisesRegex(RuntimeError,'CANONICAL_GATES'):self.call()
    def test_leverage_mutation_is_rejected(self):
        self.merged['leverage_policy']['absolute_max']=100
        with self.assertRaisesRegex(RuntimeError,'UNRELATED_CONFIG'):self.call()
        self.assertEqual(self.config.read_bytes(),self.current)
    def test_existing_tier_change_is_rejected(self):
        self.merged['r4_runtime']['strategy_instances']['old']['tier']='CANARY'
        with self.assertRaisesRegex(RuntimeError,'EXISTING_INSTANCE'):self.call()
    def test_wrong_request_version_is_rejected(self):
        self.request['strategy_version']='WRONG'
        with self.assertRaisesRegex(RuntimeError,'REQUEST_BINDING'):self.call()
    def test_failed_hot_reload_restores_config_and_artifact(self):
        self.die=True
        with self.assertRaisesRegex(RuntimeError,'SERVICE_CHANGED'):self.call()
        self.assertEqual(self.config.read_bytes(),self.current);self.assertFalse((self.root/'assist/strategies/exact.py').exists());self.assertFalse((self.state/'r4_release_last_applied.json').exists())
    def test_expired_approval_never_applies(self):
        self.release['created_at_epoch']=time.time()-601
        self.assertEqual(self.call()['status'],'WAIT_RELEASE_REVALIDATION')
        self.assertEqual(self.config.read_bytes(),self.current)
    def test_duplicate_request_preserves_subsequent_tier_change(self):
        self.call();new=json.loads(self.config.read_bytes());new['r4_runtime']['strategy_instances']['new']['tier']='NORMAL';raw=json.dumps(new).encode();self.config.write_bytes(raw);self.f.revision='e'*40;self.assertEqual(self.call()['status'],'APPLIED_DUPLICATE');self.assertEqual(self.config.read_bytes(),raw)
    def test_existing_runtime_artifact_cannot_be_overwritten(self):
        directory=self.root/'assist/strategies';directory.mkdir(parents=True);(directory/'exact.py').write_bytes(b'OPEN_POSITION_VERSION')
        with self.assertRaisesRegex(RuntimeError,'ARTIFACT_IMMUTABLE'):self.call()
        self.assertEqual(self.config.read_bytes(),self.current)
    def test_frozen_source_change_blocks_release(self):
        (self.root/'runner.py').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'FROZEN_CORE'):self.call()
    def test_hold_blocks_release(self):
        (self.root/'SERVER_PUSH_HOLD.flag').write_text('maintenance')
        self.assertEqual(self.call()['status'],'WAIT_SERVER_HOLD')
    def test_same_commit_cache_reports_no_new_config_write(self):
        self.call();result=self.call();self.assertTrue(result['unchanged']);self.assertFalse(result['operating_config_changed']);self.assertTrue(result['automatic_registration_enabled'])
    def test_existing_frame_strategy_cap_is_not_bypassed(self):
        for key in ('peer1','peer2'):
            row={'strategy_id':key.upper(),'timeframe':'1H','enabled':True,'entry_enabled':True,'server_activation_authorized':True,'tier':'NORMAL'}
            self.active['r4_runtime']['strategy_instances'][key]=dict(row)
            self.merged['r4_runtime']['strategy_instances'][key]=dict(row)
        self.current=json.dumps(self.active).encode();self.config.write_bytes(self.current);self.release['base_config_sha256']=self.real_hash(self.current).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'FRAME_STRATEGY_CAP'):self.call()
        self.assertEqual(self.config.read_bytes(),self.current)
