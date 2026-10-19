# 파일명: tests/test_server_candle_watchdog.py
# ================= 임포트 =================
import contextlib
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "server_candle_watchdog", ROOT / "strategy_lab/v3_server_candle_watchdog.py")
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


# ================= 검증 =================
class ServerCandleWatchdogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "state").mkdir()
        (self.root / "logs").mkdir()
        self.symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        values = {
            "DATA": self.root / "backtest_data",
            "STATE_FILE": self.root / "state/server_candle_watchdog_state.json",
            "LOCK_FILE": self.root / "state/server_candle_watchdog.lock",
            "HEARTBEAT_FILE": self.root / "strategy_lab/results/v3/collector_heartbeat.json",
            "LOG_FILE": self.root / "logs/runner.log",
        }
        for name, value in values.items():
            original = getattr(watchdog, name)
            setattr(watchdog, name, value)
            self.addCleanup(setattr, watchdog, name, original)
        watchdog.HEARTBEAT_FILE.parent.mkdir(parents=True)
        watchdog.HEARTBEAT_FILE.write_text(json.dumps({
            "schema": "B_BOT_V3_COLLECTOR_HEARTBEAT_V1",
            "updated_at_epoch": time.time(),
        }))

    def _candle(self, symbol, *, age_hours=1, with_gap=False, invalid=False):
        folder = watchdog.DATA / symbol
        folder.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=age_hours)
        times = pd.date_range(end=stamp, periods=12, freq="h")
        if with_gap:
            times = times[:-2].append(pd.DatetimeIndex([times[-1]]))
        data = pd.DataFrame({
            "open_time": times, "open": 100.0, "high": 105.0,
            "low": 95.0, "close": 101.0, "volume": 4.0, "is_closed": True,
        })
        if invalid:
            data.loc[data.index[-1], "high"] = 90.0
        data.to_parquet(folder / "1h.parquet", index=False)

    def _invoke(self, *, dry_run=True):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            result = watchdog.run_once(dry_run=dry_run, symbols=self.symbols)
        return result, capture.getvalue()

    def test_closed_bar_time_is_not_open_time(self):
        self._candle("BTCUSDT", age_hours=1)
        row = watchdog.inspect_symbol("BTCUSDT")
        self.assertTrue(row["pass"])
        self.assertLess(row["closed_bar_age_seconds"], 3600)
        self.assertFalse(row["rest_repair_verified"])
        self.assertEqual(row["reason"], "FRESH")

    def test_gap_and_ohlcv_corruption_are_reported_not_restarted(self):
        self._candle("BTCUSDT", with_gap=True)
        self._candle("ETHUSDT", invalid=True)
        gap = watchdog.inspect_symbol("BTCUSDT")
        bad = watchdog.inspect_symbol("ETHUSDT")
        self.assertEqual(gap["reason"], "FRESH_WITH_GAPS")
        self.assertEqual(gap["recent_gap_count"], 1)
        self.assertEqual(bad["reason"], "INVALID_OHLCV")
        with patch.object(watchdog, "collector_active", return_value=True), \
             patch.object(watchdog, "restart_collector") as restart:
            code, _ = self._invoke()
            self.assertEqual(code, 0)
            restart.assert_not_called()

    def test_dry_run_wont_restart_or_write_state(self):
        with patch.object(watchdog, "collector_active", return_value=False), \
             patch.object(watchdog, "restart_collector") as restart:
            code, output = self._invoke(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("DRY_RUN_WOULD_RESTART", output)
        self.assertIn('"direct_websocket_heartbeat_verified": false', output)
        self.assertFalse(watchdog.STATE_FILE.exists())
        self.assertFalse(watchdog.LOG_FILE.exists())
        restart.assert_not_called()

    def test_restart_only_collector_after_persistent_majority_stale(self):
        for symbol in self.symbols:
            self._candle(symbol, age_hours=8)
        with patch.object(watchdog, "collector_active", return_value=True), \
             patch.object(watchdog, "restart_collector", return_value=0) as restart, \
             patch.object(watchdog.time, "sleep"):
            code, output = self._invoke(dry_run=False)
        self.assertEqual(code, 0)
        restart.assert_called_once_with()
        self.assertIn("WATCHDOG_RESTART_RC=0", output)
        state = json.loads(watchdog.STATE_FILE.read_text())
        self.assertEqual(len(state["restart_attempt_epochs"]), 1)
        self.assertEqual(state["last_restart_rc"], 0)
        self.assertIn("[주문][R4][COLLECTOR_WATCHDOG]", watchdog.LOG_FILE.read_text())

    def test_recovery_on_second_check_skips_restart(self):
        fresh = {"pass": True, "reason": "FRESH"}
        stale = {"pass": False, "reason": "STALE"}
        reads = iter([stale] * 3 + [fresh] * 3)
        with patch.object(watchdog, "inspect_symbol", side_effect=lambda _: next(reads)), \
             patch.object(watchdog, "collector_active", return_value=True), \
             patch.object(watchdog, "restart_collector") as restart, \
             patch.object(watchdog.time, "sleep"):
            code, output = self._invoke(dry_run=False)
        self.assertEqual(code, 0)
        self.assertIn("RECOVERED_OR_UNVERIFIED_BEFORE_RESTART", output)
        restart.assert_not_called()

    def test_cooldown_and_daily_limit_suppress_restart(self):
        now = time.time()
        watchdog.STATE_FILE.write_text(json.dumps({
            "restart_attempt_epochs": [now - 100],
            "last_restart_epoch": now - 100,
        }))
        with patch.object(watchdog, "collector_active", return_value=False), \
             patch.object(watchdog, "restart_collector") as restart:
            _, output = self._invoke(dry_run=False)
            self.assertIn("RESTART_SUPPRESSED_COOLDOWN", output)
            restart.assert_not_called()
        watchdog.STATE_FILE.write_text(json.dumps({
            "restart_attempt_epochs": [now - 7000, now - 6500, now - 6000],
            "last_restart_epoch": now - 6000,
        }))
        with patch.object(watchdog, "collector_active", return_value=False), \
             patch.object(watchdog, "restart_collector") as restart:
            _, output = self._invoke(dry_run=False)
            self.assertIn("RESTART_SUPPRESSED_DAILY_BUDGET", output)
            restart.assert_not_called()

    def test_corrupt_state_and_unknown_service_fail_closed(self):
        watchdog.STATE_FILE.write_text("{invalid")
        with patch.object(watchdog, "collector_active", return_value=False), \
             patch.object(watchdog, "restart_collector") as restart:
            with self.assertRaisesRegex(RuntimeError, "WATCHDOG_STATE_UNREADABLE"):
                self._invoke(dry_run=False)
            restart.assert_not_called()
        watchdog.STATE_FILE.unlink()
        with patch.object(watchdog, "collector_active", return_value=None), \
             patch.object(watchdog, "restart_collector") as restart:
            _, output = self._invoke(dry_run=False)
            self.assertIn("SERVICE_STATUS_UNKNOWN", output)
            restart.assert_not_called()

    def test_no_r4_service_restart_and_no_order_code(self):
        source = (ROOT / "strategy_lab/v3_server_candle_watchdog.py").read_text()
        self.assertIn('["sudo", "systemctl", "restart", "collector.service"]', source)
        self.assertNotIn('"restart", "r4-runtime.service"', source)
        self.assertNotIn("from order import", source)
        self.assertNotIn("from risk_engine import", source)
        self.assertNotIn("DELETE FROM", source)
        self.assertNotIn("REST_REPAIR_VERIFIED", source)


if __name__ == "__main__":
    unittest.main()
