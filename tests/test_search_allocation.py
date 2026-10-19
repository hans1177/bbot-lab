import ast,hashlib,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

REPO=Path(__file__).resolve().parents[1]

def functions(path,names,ns):
    nodes=[n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
    return ns

class SearchAllocationTests(unittest.TestCase):
    def test_fixed_rotation_preserves_basic_turns(self):
        records={};calls=[]
        def record(k,v):records[k]=v
        ns={'knowledge':types.SimpleNamespace(ensure=lambda:({'records':records},{}),record_result=record),
            '_search_candidate_available':lambda:True,
            '_run_search_candidate_one':lambda:(calls.append('search') or {'status':'DONE'}),
            '_run_basic_research_one':lambda held:(calls.append('basic') or {'status':'DONE'})}
        functions(REPO/'strategy_lab/research_controller.py',{'_run_new_research_one'},ns)
        for _ in range(9):ns['_run_new_research_one'](True)
        self.assertEqual(calls,['search','basic','basic','basic','search','basic','basic','basic','search'])
        self.assertEqual(records['new_research_rotation']['cursor'],9)

    def test_failed_dispatch_does_not_consume_turn(self):
        records={}
        def fail():raise RuntimeError('interrupted')
        ns={'knowledge':types.SimpleNamespace(ensure=lambda:({'records':records},{}),record_result=lambda k,v:records.update({k:v})),
            '_search_candidate_available':lambda:True,'_run_search_candidate_one':fail}
        functions(REPO/'strategy_lab/research_controller.py',{'_run_new_research_one'},ns)
        with self.assertRaisesRegex(RuntimeError,'interrupted'):ns['_run_new_research_one'](True)
        self.assertEqual(records,{})

    def test_search_frames_rotate_and_completed_candidates_are_skipped(self):
        records={};done=set();seen=[]
        contracts=[{'timeframe':tf} for tf in ('1H','6H','12H')]
        def run(candidate):
            key=(candidate['name'],candidate['timeframe']);seen.append(key);done.add(key);return {'status':'FAIL'}
        wave=types.SimpleNamespace(wave=lambda tf,search_contract:{'wave_id':tf,'candidates':[{'name':'A','timeframe':tf}]})
        ns={'knowledge':types.SimpleNamespace(ensure=lambda:({'records':records},{}),record_result=lambda k,v:records.update({k:v})),
            '_search_contracts':lambda:contracts,'hypothesis_factory':types.SimpleNamespace(completed_pairs=lambda:done,run_one=run),
            '_record_strategy_track':lambda *args:None}
        functions(REPO/'strategy_lab/research_controller.py',{'_run_search_candidate_one'},ns)
        with patch.dict('sys.modules',{'v3_memory_hypothesis_wave':wave}):
            for _ in range(3):self.assertEqual(ns['_run_search_candidate_one']()['status'],'DONE')
            self.assertIsNone(ns['_run_search_candidate_one']())
        self.assertEqual(seen,[('A','1H'),('A','6H'),('A','12H')])

    def test_training_preparation_excludes_future_and_preserves_server_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'strategy_lab').mkdir()
            cfg=root/'strategy_lab/lab_config.json';cfg.write_text('{"fee_rate":0.0005}')
            server=root/'strategy_config.json';server.write_text('{"sentinel":true}')
            ts=pd.date_range('2019-01-01','2025-01-01',freq='D',tz='UTC').astype('int64')//10**9
            frame=pd.DataFrame({'ts':ts,'open':100.,'high':102.,'low':99.,'close':101.,'volume':10.})
            runner=types.SimpleNamespace(load_lab_engine=lambda:None,load_raw_frame=lambda *args:frame)
            def atomic(p,d):p.write_text(json.dumps(d))
            ns={'hf':types.SimpleNamespace(ROOT=root,TF_ORDER=('1H','6H','12H'),knowledge=types.SimpleNamespace(atomic=atomic)),
                'json':json,'hashlib':hashlib,'WAVE_SIZE_PER_TF':12}
            functions(REPO/'strategy_lab/v3_memory_hypothesis_wave.py',{'prepare_cloud_training_pilot'},ns)
            with patch.dict('sys.modules',{'run_strategy':runner}):
                ns['prepare_cloud_training_pilot']();first=json.loads(cfg.read_text())['candidate_search']
                ns['prepare_cloud_training_pilot']();self.assertEqual(first,json.loads(cfg.read_text())['candidate_search'])
            self.assertFalse(first['automatic_wave_refresh'])
            for contract in first['contracts']:
                self.assertEqual(contract['max_evaluations'],32)
                for row in contract['training_files']:
                    path=root/row['path'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['sha256'])
                    data=pd.read_csv(path);self.assertGreaterEqual(data.ts.min(),pd.Timestamp('2020-01-01',tz='UTC').timestamp())
                    self.assertLess(data.ts.max(),pd.Timestamp('2022-01-01',tz='UTC').timestamp())
            self.assertEqual(server.read_text(),'{"sentinel":true}')

if __name__=='__main__':unittest.main()
