import ast
import contextlib
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ResearchLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(__file__).resolve().parents[1] / 'strategy_lab/research_controller.py'
        tree = ast.parse(path.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'lock')
        body = ast.unparse(fn)
        lock_path = str(Path(self.temp.name) / 'research_controller.lock')
        self.ns = dict(contextmanager=contextlib.contextmanager, LOCK=Path(lock_path),
                       os=os, fcntl=fcntl, msvcrt=None)
        exec(body, self.ns)
        self.prefix = ('import os,fcntl\nfrom pathlib import Path\n'
                       'from contextlib import contextmanager\nmsvcrt=None\nLOCK=Path('
                       + repr(lock_path) + ')\n' + body)
        self.child = self.prefix + '\ntry:\n with lock():print("ACQUIRED")\nexcept RuntimeError as e:print(str(e))\n'

    def tearDown(self):
        self.temp.cleanup()

    def child_result(self):
        result = subprocess.run([sys.executable, '-B', '-c', self.child],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_concurrent_worker_rejected(self):
        with self.ns['lock']():
            self.assertEqual(self.child_result(), 'RESEARCH_ALREADY_RUNNING')

    def test_release_after_normal_return(self):
        with self.ns['lock']():
            pass
        self.assertEqual(self.child_result(), 'ACQUIRED')

    def test_os_releases_after_crash(self):
        result = subprocess.run([sys.executable, '-B', '-c', self.prefix + '\nwith lock():os._exit(17)\n'], timeout=5)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.child_result(), 'ACQUIRED')

    def test_missing_lock_backend_blocks(self):
        self.ns['fcntl'] = None
        with self.assertRaisesRegex(RuntimeError, 'RESEARCH_LOCK_BACKEND_UNAVAILABLE'):
            with self.ns['lock']():
                self.fail('lock bypass')
