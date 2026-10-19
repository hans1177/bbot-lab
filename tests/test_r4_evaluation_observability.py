"""Isolated tests for best-effort R4 signal/exit observations and GitHub feedback."""
import ast
import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))
from r4_evaluation_observer import R4EvaluationObserver, SCHEMA


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "evaluations.json"
        self.observer = R4EvaluationObserver(self.path)

    def test_latest_signal_and_exit_observations_are_bounded_and_no_trade_fields(self):
        self.assertFalse(self.observer.flush())
        self.assertTrue(self.observer.note("ENTRY", "SI_test", "12H", "AXSUSDT", "ENTRY_GATE_NO_ALLOW"))
        self.assertTrue(self.observer.note("EXIT", "SI_test", "12H", "AXSUSDT",
                                          "NO_OPPOSITE", "LEG_test", "SHORT"))
        self.assertTrue(self.observer.flush())
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["schema"], SCHEMA)
        self.assertEqual(payload["entry_evaluations"][0]["status"], "ENTRY_GATE_NO_ALLOW")
        self.assertEqual(payload["exit_evaluations"][0]["side"], "SHORT")
        self.assertNotIn("signal_id", self.path.read_text())
        self.assertNotIn("price", self.path.read_text())
        self.assertNotIn("quantity", self.path.read_text())
        self.assertFalse(self.observer.flush())
        self.observer.note("EXIT", "SI_test", "12H", "AXSUSDT",
                           "OPPOSITE_EXIT_ATTEMPTED", "LEG_test", "SHORT")
        self.observer.flush()
        self.assertEqual(len(json.loads(self.path.read_text())["exit_evaluations"]), 1)

    def test_unsafe_payload_is_rejected_and_snapshot_unchanged(self):
        self.assertFalse(self.observer.note("ENTRY", "SI_test", "1H", "BTCUSDT",
                                            "BUY_NOW;SECRET_KEY=abcd"))
        self.assertFalse(self.observer.note("EXIT", "SI_test", "1H", "BTCUSDT",
                                            "NO_OPPOSITE", "LEG_test", "OTHER"))
        self.assertFalse(self.observer.flush())
        self.assertFalse(self.path.exists())

    def test_symlink_guard_prevents_writes_outside_state(self):
        target = Path(self.temp.name) / "do_not_change"
        target.write_text("SAFE")
        self.path.symlink_to(target)
        self.observer.note("ENTRY", "SI_test", "1H", "BTCUSDT", "ENTRY_GATE_NO_ALLOW")
        with self.assertRaisesRegex(RuntimeError, "SYMLINK"):
            self.observer.flush()
        self.assertEqual(target.read_text(), "SAFE")

    def test_real_service_observes_both_real_gate_paths_and_flushes_in_finally(self):
        tree = ast.parse((RUNTIME / "r4_runtime_service.py").read_text())
        service = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                       and n.name == "R4RuntimeService")
        methods = {n.name: n for n in service.body if isinstance(n, ast.FunctionDef)}
        calls = {n.attr for n in ast.walk(methods["cycle"])
                 if isinstance(n, ast.Attribute)}
        self.assertIn("evaluate_frame", calls)
        self.assertIn("_eval_note", calls)
        managed = ast.unparse(methods["_manage_active_legs"])
        self.assertIn("evaluate_owned_position", managed)
        self.assertIn("NO_OPPOSITE", managed)
        self.assertIn("OPPOSITE_EXIT_ATTEMPTED", managed)
        self.assertIn("SIGNAL_EVALUATION_ERROR", managed)
        self.assertIn("exit_leg(", managed)
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "main")
        main_text = ast.unparse(main)
        self.assertIn("service.evaluation_observer.flush()", main_text)
        self.assertIn("finally:", main_text)
        self.assertIn("except Exception:", ast.unparse(methods["_eval_note"]))


if __name__ == "__main__":
    unittest.main()
