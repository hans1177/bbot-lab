# 파일명: tests/test_release_verifiers.py
import ast,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ReleaseVerifierTests(unittest.TestCase):
    def setUp(self):
        source=(ROOT/'verify_autonomous_runtime_release.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='worker_base_sync_forbidden')
        ns={'ast':ast};exec(compile(ast.Module(body=[node],type_ignores=[]),'canonical_guard','exec'),ns);self.check=ns['worker_base_sync_forbidden']
    def test_current_preparation_is_read_only(self):
        self.assertTrue(self.check((ROOT/'strategy_lab/v3_auto_runtime_release.py').read_text()))
    def test_base_sync_execution_in_runtime_is_rejected(self):
        self.assertFalse(self.check("def tick():\n    run_powershell('sync_base_to_server.ps1')\n"))
    def test_preparation_cannot_execute_base_sync(self):
        source="def prepare_server():\n    for name in ('sync_base_to_server.ps1',):\n        path.read_bytes()\n        subprocess.run([name])\n"
        self.assertFalse(self.check(source))
    def test_reading_base_sync_outside_snapshot_owner_is_rejected(self):
        self.assertFalse(self.check("def tick():\n    for name in ('sync_base_to_server.ps1',):\n        path.read_bytes()\n"))
    def test_source_text_in_remote_execution_is_rejected(self):
        self.assertFalse(self.check("def prepare_server():\n    remote('powershell sync_base_to_server.ps1')\n"))
    def test_unexpected_callable_in_snapshot_loop_is_rejected(self):
        self.assertFalse(self.check("def prepare_server():\n    for name in ('sync_base_to_server.ps1',):\n        path.read_bytes()\n        unsafe(name)\n"))
