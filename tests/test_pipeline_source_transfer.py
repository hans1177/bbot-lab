# 파일명: tests/test_pipeline_source_transfer.py
import ast,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
class PipelineSourceTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        ns={'ROOT':self.root,'_LOCAL_SHADOW_PORTABLE_SOURCE_PREFIXES':('strategy_lab/results/v3/redeveloped_strategies/',)}
        f=next(n for n in ast.parse((LAB/'v3_strategy_research.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_pipeline_source_portable')
        exec(compile(ast.Module(body=[f],type_ignores=[]),'actual_portability','exec'),ns)
        self.owner=types.SimpleNamespace(_pipeline_source_portable=ns['_pipeline_source_portable'])
        f=next(n for n in ast.parse((LAB/'v3_deployment.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='source_for_pipeline')
        self.ns={'ROOT':self.root};exec(compile(ast.Module(body=[f],type_ignores=[]),'actual_source_transfer','exec'),self.ns)
    def call(self,rel):
        with patch.dict(sys.modules,{'v3_strategy_research':self.owner}):return self.ns['source_for_pipeline']({'canonical_source_file':rel})
    def test_portable_single_source_is_transferable(self):
        rel='strategy_lab/results/v3/generated_trials/single.py';p=self.root/rel;p.parent.mkdir(parents=True);p.write_text('# PORTABLE_GENERATED_SINGLE_V2\n')
        self.assertEqual(self.call(rel),p)
    def test_legacy_wrapper_is_not_transferable(self):
        rel='strategy_lab/results/v3/generated_trials/single.py';p=self.root/rel;p.parent.mkdir(parents=True);p.write_text('# legacy absolute dependency\n')
        self.assertIsNone(self.call(rel))
    def test_redeveloped_source_remains_transferable(self):
        rel='strategy_lab/results/v3/redeveloped_strategies/exact.py';p=self.root/rel;p.parent.mkdir(parents=True);p.write_text('# exact standalone source\n')
        self.assertEqual(self.call(rel),p)
