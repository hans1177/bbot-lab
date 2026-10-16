# 파일명: tests/test_context_verification.py
import ast,os,sys,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ContextVerificationTests(unittest.TestCase):
    def test_runtime_verifier_uses_host_interpreter_on_linux(self):
        tree=ast.parse((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_python')
        calls=[];ns={'ROOT':Path('/isolated'),'Path':Path,'sys':sys,'os':types.SimpleNamespace(name='posix'),'run':lambda cmd,timeout:calls.append((cmd,timeout))}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_interpreter','exec'),ns);ns['run_python'](Path('/isolated/verify_v3.py'))
        self.assertEqual(calls[0][0],[sys.executable,'/isolated/verify_v3.py'])
    def test_windows_existing_hidden_worker_interpreter_preserved(self):
        tree=ast.parse((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_python')
        calls=[];ns={'ROOT':Path('/isolated'),'Path':Path,'sys':sys,'os':types.SimpleNamespace(name='nt'),'run':lambda cmd,timeout:calls.append(cmd)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_interpreter','exec'),ns);ns['run_python'](Path('/isolated/verify_v3.py'))
        self.assertEqual(calls[0][0],'/isolated/.venv/Scripts/pythonw.exe')
    def test_full_ci_context_does_not_use_overlay_inheritance_or_live_credentials(self):
        import yaml
        workflow=yaml.safe_load((ROOT/'.github/workflows/research-check.yml').read_text())
        step=next(x for x in workflow['jobs']['check']['steps'] if x.get('name')=='Run all six canonical release verifiers in isolation')
        compile(step['run'],'full_context_ci','exec');self.assertNotIn('--overlay-package',step['run']);self.assertIn("RUNNER_DRY_RUN='1'",step['run']);self.assertIn('--verify-context',step['run']);self.assertNotIn('config.json',step['run'])
