# 파일명: tests/test_frame_compute.py
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


class FrameComputeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.lab=self.root/'strategy_lab'
        (self.lab/'results/v3').mkdir(parents=True)
        for name in ('ROADMAP_CONSTITUTION_V3.json','strategy_lab/run_strategy.py','INPUT_RECEIPT.json'):
            (self.root/name).write_text('{}')
        self.frames=['30M','1H','6H'];self.records={};self.finalized=[]
        self.owner=threading.get_ident();self.barrier=threading.Barrier(3)
        def choose(**kwargs):
            return next(((tf,None) for tf in self.frames if tf not in kwargs['excluded_timeframes']),None)
        def run_one(timeframe=None,prepare_only=False,task=None,process=None,**kwargs):
            self.assertEqual(threading.get_ident(),self.owner)
            if prepare_only:
                source=self.lab/'results/v3'/f'{timeframe}.py';source.write_text('# candidate')
                return {'kind':'SINGLE','timeframe':timeframe,'family':timeframe,'trial':self.frames.index(timeframe)+1,
                    'source':str(source.relative_to(self.root)),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                    'strategy_name':timeframe,'started':time.time(),'env':{}}
            self.finalized.append(task['timeframe'])
            return {'status':'PASS','trial':task['trial']}
        def record(key,row):
            self.assertEqual(threading.get_ident(),self.owner)
            self.records[key]=json.loads(json.dumps(row))
        def compute(args,**kwargs):
            self.assertNotEqual(threading.get_ident(),self.owner)
            self.assertNotIn('GH_TOKEN',kwargs['env'])
            self.barrier.wait(timeout=5)
            name=Path(args[-1]).stem
            (self.lab/'results'/f'{name}.json').write_text(json.dumps({'strategy':name}))
            return subprocess.CompletedProcess(args,0,'finished')
        self.compute=compute
        self.strategy=types.SimpleNamespace(choose_next=choose,run_one=run_one,
            find_result=lambda name,started:self.lab/'results'/f'{name}.json')
        ns=dict(ROOT=self.root,LAB=self.lab,hashlib=hashlib,json=json,time=time,os=os,sys=sys,
            subprocess=subprocess,ThreadPoolExecutor=ThreadPoolExecutor,strategy_research=self.strategy,
            hypothesis_factory=types.SimpleNamespace(choose_next=lambda **kw:None),
            knowledge=types.SimpleNamespace(record_result=record),_record_strategy_track=lambda *args:None)
        path=Path(__file__).resolve().parents[1]/'strategy_lab/research_controller.py'
        node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_run_frame_batch')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_frame_batch','exec'),ns)
        self.run=ns['_run_frame_batch']

    def test_three_computations_overlap_but_state_writes_are_serial(self):
        with patch('subprocess.run',side_effect=self.compute),patch.dict(os.environ,{'GH_TOKEN':'private'}):row=self.run()
        self.assertEqual(row['workers'],3)
        self.assertEqual(row['timeframes'],self.frames)
        self.assertEqual(self.finalized,self.frames)
        self.assertEqual(self.records['controller_compute_batch']['status'],'FINALIZED')

    def test_resume_computed_batch_does_not_recompute_or_refinalize_done_task(self):
        original=self.strategy.run_one
        def fail_second(**kwargs):
            if kwargs.get('task',{}).get('timeframe')=='1H':raise RuntimeError('interrupted')
            return original(**kwargs)
        self.strategy.run_one=fail_second
        with patch('subprocess.run',side_effect=self.compute):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):self.run()
        saved=self.records['controller_compute_batch']
        self.assertEqual(saved['status'],'COMPUTED')
        self.strategy.run_one=original
        with patch('subprocess.run',side_effect=AssertionError('must not recompute')):self.run(resume=saved)
        self.assertEqual(self.finalized,self.frames)

    def test_changed_source_cannot_reuse_computed_result(self):
        with patch('subprocess.run',side_effect=self.compute):self.run()
        saved=self.records['controller_compute_batch'];saved['status']='COMPUTED'
        (self.root/saved['tasks'][0]['source']).write_text('# changed')
        with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):self.run(resume=saved)

    def test_changed_result_cannot_be_finalized(self):
        with patch('subprocess.run',side_effect=self.compute):self.run()
        saved=self.records['controller_compute_batch'];saved['status']='COMPUTED'
        task=saved['tasks'][0];task.pop('finalized')
        (self.root/task['result_file']).write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'RESULT_CHANGED'):self.run(resume=saved)

    def test_changed_dataset_binding_blocks_resume(self):
        with patch('subprocess.run',side_effect=self.compute):self.run()
        (self.root/'INPUT_RECEIPT.json').write_text('{"changed":true}')
        with self.assertRaisesRegex(RuntimeError,'BINDING_CHANGED'):self.run(resume=self.records['controller_compute_batch'])

    def test_grammar_finalization_restores_training_dates_from_saved_task(self):
        path=Path(__file__).resolve().parents[1]/'strategy_lab/v3_hypothesis_factory.py'
        node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='run_one')
        source=self.lab/'results/v3/grammar.py';source.write_text('# source')
        report=self.lab/'results/grammar.json';report.write_text('{}')
        rows=[]
        ns={'ROOT':self.root,'hashlib':hashlib,'json':json,'Any':object,
            'governance':types.SimpleNamespace(assert_kernel_unchanged=lambda before:None),
            'finite':lambda value:None,
            'knowledge':types.SimpleNamespace(complete_trial=lambda *args:None,record_result=lambda key,row,failed:rows.append(row))}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_grammar_finalization','exec'),ns)
        sr=types.SimpleNamespace(evaluate=lambda *args,**kw:{'pass':False,'hard_pass':False})
        task={'timeframe':'1H','recipe':{},'name':'GRAMMAR','trial':1,'source':str(source.relative_to(self.root)),
            'strategy_name':'GRAMMAR','started':0,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'before':{},'result_file':str(report.relative_to(self.root)),
            'env':{'BBOT_RESEARCH_START':'2016-01-01','BBOT_RESEARCH_END':'2024-08-20'}}
        with patch.dict(sys.modules,{'v3_strategy_research':sr}):ns['run_one'](task=task,process=subprocess.CompletedProcess([],0,''))
        self.assertEqual(rows[0]['research_start'],'2016-01-01')
        self.assertEqual(rows[0]['research_end'],'2024-08-20')


if __name__=='__main__':unittest.main()
