# 파일명: tests/test_peer_frame_preservation.py
import ast,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TREE=ast.parse((ROOT/'strategy_lab/v3_deployment.py').read_text())
FN=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='materialize_local_ready_request')
NAMES={'active_state_frame','active_state_tier','active_state_si','active_cfg','active_cfg_frame','active_live_instances','preserve_active_frame'}
class PeerFramePreservationTests(unittest.TestCase):
    def check(self,config,fr=None):
        assignments={n.targets[0].id:n for n in ast.walk(FN) if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in NAMES}
        ns={'s':{'frames':{'1H':fr or {}}},'tf':'1H','strategy_instance_id':'new','CONFIG':None,'load':lambda *args:config}
        for name in ('active_state_frame','active_state_tier','active_state_si','active_cfg','active_cfg_frame','active_live_instances','preserve_active_frame'):
            node=assignments[name]
            # 중첩된 같은 이름의 대입 대신 실제 보존 조건 앞의 책임 블록을 선택한다.
            if name in {'active_cfg','active_cfg_frame'}:
                node=min((n for n in ast.walk(FN) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets) and n.lineno>assignments['active_state_si'].lineno),key=lambda n:n.lineno)
            exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_peer_boundary','exec'),ns)
        return ns['preserve_active_frame']
    def test_empty_local_history_preserves_actual_live_frame(self):
        config={'frames':{'1H':{'strategy':'LIVE'}},'r4_runtime':{'strategy_instances':{'old':{'timeframe':'1H','tier':'NORMAL','server_activation_authorized':True}}}}
        self.assertTrue(self.check(config))
    def test_no_live_peer_keeps_existing_new_frame_path(self):
        self.assertFalse(self.check({'frames':{'1H':{'enabled':False}}}))
    def test_other_timeframe_does_not_claim_frame(self):
        config={'frames':{'1H':{'strategy':'LIVE'}},'r4_runtime':{'strategy_instances':{'old':{'timeframe':'6H','tier':'NORMAL','server_activation_authorized':True}}}}
        self.assertFalse(self.check(config))
