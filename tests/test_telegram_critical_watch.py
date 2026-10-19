# 파일명: tests/test_telegram_critical_watch.py
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "telegram_critical_watch", ROOT / "runtime/telegram_critical_watch.py"
)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class CriticalWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "state").mkdir()
        self.db = self.base / "state/history.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE r4_basket_snapshots(mismatch INTEGER)")
            con.execute("INSERT INTO r4_basket_snapshots VALUES(0)")
        self.original = self.db.read_bytes()
        h = self.base / "strategy_lab/results/v3/collector_heartbeat.json"
        h.parent.mkdir(parents=True)
        h.write_text(json.dumps({"schema":"B_BOT_V3_COLLECTOR_HEARTBEAT_V1",
                                "updated_at_epoch":1000}))
        self.sent = []

    def _run(self, statuses):
        def fake_run(args, **kwargs):
            name = args[-1]
            value = statuses.get(name, "unknown")
            return SimpleNamespace(returncode=0 if value == "active" else 3,
                                   stdout=value+"\n")
        return fake_run

    def test_healthy_probes_cannot_send_alerts(self):
        checks = watch.observe(self.base, 1050, self._run({
            "r4-runtime.service":"active",
            "runner.service":"inactive",
            "collector.service":"active",
        }))
        self.assertEqual(checks, [])
        for when in (1100, 1170, 1240):
            result = watch.evaluate_once(self.base, checks, self.sent.append, when)
            self.assertEqual(result["status"], "OBSERVATION_ONLY")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_two_distinct_samples_and_six_hour_alert_suppression(self):
        for now in (1000, 1020, 1070):
            result = watch.evaluate_once(self.base, ["R4_RUNTIME_INACTIVE"], self.sent.append, now)
        self.assertEqual(result["status"], "ALERT_SENT")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("R4_RUNTIME_INACTIVE", self.sent[0])
        self.assertNotIn("trade_id", self.sent[0])
        watch.evaluate_once(self.base, ["R4_RUNTIME_INACTIVE"], self.sent.append, 1500)
        self.assertEqual(len(self.sent), 1)
        watch.evaluate_once(self.base, ["R4_RUNTIME_INACTIVE"], self.sent.append, 1070+6*3600)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_delivery_failure_not_counted_as_sent_and_retried(self):
        def fail(text):
            raise RuntimeError("https://api.telegram.org/botPRIVATE/sendMessage")
        watch.evaluate_once(self.base, ["COLLECTOR_INACTIVE"], fail, 1000)
        with self.assertRaisesRegex(RuntimeError, "WATCH_TELEGRAM_SEND_FAILED") as exc:
            watch.evaluate_once(self.base, ["COLLECTOR_INACTIVE"], fail, 1080)
        self.assertNotIn("PRIVATE", str(exc.exception))
        state=json.loads((self.base/"state"/watch.STATE_NAME).read_text())
        self.assertEqual(state["active"]["COLLECTOR_INACTIVE"]["last_sent"],0)
        watch.evaluate_once(self.base, ["COLLECTOR_INACTIVE"], self.sent.append, 1150)
        self.assertEqual(len(self.sent),1)

    def test_recovery_clears_incident_only_after_two_clear_samples(self):
        watch.evaluate_once(self.base, ["LEGACY_RUNNER_ACTIVE"], self.sent.append, 1000)
        watch.evaluate_once(self.base, ["LEGACY_RUNNER_ACTIVE"], self.sent.append, 1100)
        self.assertEqual(len(self.sent),1)
        watch.evaluate_once(self.base, [], self.sent.append, 1200)
        watch.evaluate_once(self.base, [], self.sent.append, 1300)
        watch.evaluate_once(self.base, ["LEGACY_RUNNER_ACTIVE"], self.sent.append, 1400)
        watch.evaluate_once(self.base, ["LEGACY_RUNNER_ACTIVE"], self.sent.append, 1500)
        self.assertEqual(len(self.sent),2)

    def test_observes_legacy_runner_and_basket_mismatch_without_modifying_db(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO r4_basket_snapshots VALUES(1)")
        original=self.db.read_bytes()
        result=watch.observe(self.base, 1050, self._run({
            "r4-runtime.service":"active",
            "runner.service":"active",
            "collector.service":"active"
        }))
        self.assertEqual(result,["LEGACY_RUNNER_ACTIVE","R4_BASKET_MISMATCH"])
        self.assertEqual(self.db.read_bytes(),original)

    def test_no_fabricated_websocket_health_or_trading_actions(self):
        result=watch.observe(self.base, 1050, self._run({
            "r4-runtime.service":"unknown",
            "runner.service":"inactive",
            "collector.service":"active"
        }))
        self.assertNotIn("R4_RUNTIME_INACTIVE",result)
        source=(ROOT/"runtime/telegram_critical_watch.py").read_text()
        self.assertNotIn("systemctl\", \"restart",source)
        self.assertNotIn("from order import",source)
        self.assertNotIn("from runner import",source)
        self.assertNotIn("from risk_engine import",source)
        self.assertNotIn("DELETE FROM",source)
        self.assertNotIn("UPDATE r4_",source)

    def test_heartbeat_missing_or_stale_is_not_reported_fresh(self):
        h=self.base/"strategy_lab/results/v3/collector_heartbeat.json"
        self.assertIsNone(watch._heartbeat_problem(self.base,1050))
        self.assertEqual(watch._heartbeat_problem(self.base,2000),"COLLECTOR_HEARTBEAT_STALE")
        h.unlink()
        self.assertEqual(watch._heartbeat_problem(self.base,1050),"COLLECTOR_HEARTBEAT_MISSING")

    def test_unknown_codes_rejected_and_state_unchanged(self):
        with self.assertRaisesRegex(ValueError,"WATCH_UNKNOWN_CODE"):
            watch.evaluate_once(self.base,["PRIVATE_KEY"],self.sent.append,1000)
        self.assertFalse((self.base/"state"/watch.STATE_NAME).exists())

    def test_state_symlink_is_rejected(self):
        f=self.base/"state"/watch.STATE_NAME
        f.symlink_to(self.db)
        with self.assertRaisesRegex(RuntimeError,"WATCH_STATE_SYMLINK"):
            watch.evaluate_once(self.base,[],self.sent.append,1000)

    def test_systemd_template_is_off_by_default_and_never_restarts_trader(self):
        service=(ROOT/"ops/telegram-critical-watch.service").read_text()
        timer=(ROOT/"ops/telegram-critical-watch.timer").read_text()
        self.assertIn("--check", service)
        self.assertIn("User=ec2-user", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertNotIn("systemctl restart", service)
        self.assertNotIn("r4-runtime.service", service)
        self.assertNotIn("runner.service", service)
        self.assertIn("OnUnitInactiveSec=2min", timer)
        self.assertNotIn("OnUnitInactiveSec=30s", timer)

    def test_env_parser_filters_unrelated_secrets(self):
        env=self.base/".env"
        env.write_text("TRADING_SECRET=PRIVATE\nTELEGRAM_TOKEN=abc\nTELEGRAM_CHAT_ID=1234\n")
        self.assertEqual(watch._env_file(env),{
            "TELEGRAM_TOKEN":"abc","TELEGRAM_CHAT_ID":"1234"})
        self.assertNotIn("TRADING_SECRET",watch._env_file(env))


if __name__ == "__main__":
    unittest.main()
