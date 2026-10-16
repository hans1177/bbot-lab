# 파일명: tests/test_research_timeframe_scope.py
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
class TimeframeScopeTests(unittest.TestCase):
    def invoke(self,scope):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'strategy_lab').mkdir()
            if scope is not None:(root/'strategy_lab/lab_config.json').write_text(json.dumps({'research_candidate_fill_timeframes':scope}))
            env=dict(os.environ,BBOT_LOCAL_ROOT=str(root))
            source='import sys;sys.path.insert(0,'+repr(str(LAB))+');import v3_hypothesis_factory as h;import v3_memory_hypothesis_wave as w;print(h.TF_ORDER);assert tuple(h.TF_ORDER)==w.TF_ORDER'
            return subprocess.run([sys.executable,'-B','-c',source],env=env,capture_output=True,text=True)
    def test_declared_five_frames_apply_to_both_factories(self):
        r=self.invoke(['1H','6H','12H','1D','1W']);self.assertEqual(r.returncode,0,r.stderr);self.assertNotIn('30M',r.stdout)
    def test_default_six_frames_preserved(self):
        r=self.invoke(None);self.assertEqual(r.returncode,0,r.stderr);self.assertIn('30M',r.stdout)
    def test_unsupported_scope_fails_closed(self):
        r=self.invoke(['2M']);self.assertNotEqual(r.returncode,0);self.assertIn('INVALID_RESEARCH_TIMEFRAME_SCOPE',r.stderr)
