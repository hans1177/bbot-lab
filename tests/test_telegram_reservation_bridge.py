# 파일명: tests/test_telegram_reservation_bridge.py
"""The real reservation.py is only mounted on AWS. Tests emulate its public interface."""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "telegram_reservation_bridge", ROOT / "runtime/telegram_reservation_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ReservationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.base = Path(self.dir.name)
        self.counts = {"account":0,"positions":0,"trades":0}
        self.module = types.SimpleNamespace(
            TELEGRAM_TOKEN="BOT_SECRET_TEST_NOT_EXPORT",
            TELEGRAM_CHAT_ID="12345",
        )
        def account():
            self.counts["account"] += 1
            return 272.36,176.94,-50.0
        def positions():
            self.counts["positions"] += 1
            return [{"symbol":"BTCUSDT","positionAmt":"-0.12","unRealizedProfit":"-2.5"}]
        def exits(table, limit=None):
            self.counts["trades"] += 1
            self.assertEqual(table, "exit_history")
            self.assertEqual(limit, bridge.MAX_EXIT_ROWS)
            return [{"ts":100,"pnl":2.0,"roi":3.0},
                    {"ts":101,"pnl":-1.0,"roi":-4.0},
                    {"ts":102,"pnl":None,"roi":None}]
        def accumulate(box,pnl,roi):
            box["count"] += 1
            box["win"] += (pnl>0)
            box["loss"] += (pnl<0)
            box["pnl"] += pnl
        self.module.account_summary=account
        self.module.live_positions=positions
        self.module.fetch_rows_direct=exits
        self.module.exit_event_ts=lambda x: x["ts"]
        self.module.resolved_exit_metrics=lambda x: (
            {"resolved": False} if x["pnl"] is None else
            {"resolved":True,"pnl":x["pnl"],"roi":x["roi"]})
        self.module.empty_trade_stats=lambda :{
            "count":0,"win":0,"loss":0,"flat":0,"pnl":0.0}
        self.module.accumulate_trade_stats=accumulate
        self.module.finalize_trade_stats=lambda x:x
        self.module.build_strategy_stats=lambda :{
            ("1H","V3_TEST"):{"win":1,"loss":1,"cnt":2,"pnl":1.0}}
        self.module.load_cycle_report=lambda :(
            "LONG",{tf:1 for tf in ("1h","6h","12h","1d","1w","1m")},0.5,35)
        self.module.load_btc_cycle_report=lambda :(
            "LONG",{tf:2 for tf in ("1h","6h","12h","1d","1w","1m")},1.2)

    def test_reservation_credentials_use_reporter_exact_destination(self):
        with patch.object(bridge,"load_reservation",return_value=self.module):
            token, chat=bridge.reservation_credentials(self.base)
        self.assertEqual(token,"BOT_SECRET_TEST_NOT_EXPORT")
        self.assertEqual(chat,"12345")

    def test_explicit_account_only_invokes_readonly_reservation_account(self):
        with patch.object(bridge,"load_reservation",return_value=self.module):
            answer=bridge.reservation_answer("현금 보유 얼마야",self.base,now=200)
        self.assertIn("272.36 USDT",answer)
        self.assertIn("176.94 USDT",answer)
        self.assertIn("-50.00 USDT",answer)
        self.assertNotIn("BOT_SECRET",answer)
        self.assertEqual(self.counts,{"account":1,"positions":0,"trades":0})

    def test_positions_and_strategy_separated_from_account(self):
        with patch.object(bridge,"load_reservation",return_value=self.module):
            pos=bridge.reservation_answer("포지션",self.base,now=200)
            stat=bridge.reservation_answer("전략별 성과",self.base,now=200)
        self.assertIn("BTCUSDT SHORT",pos)
        self.assertIn("V3_TEST",stat)
        self.assertEqual(self.counts["account"],0)

    def test_winrate_and_loss_show_existing_report_provenance(self):
        with patch.object(bridge,"load_reservation",return_value=self.module):
            answer=bridge.reservation_answer("오늘 승률",self.base,now=200)
        # All three synthetic exits fall after the KST day start for epoch 200.
        self.assertIn("승률: 50.0%",answer)
        self.assertIn("계산 불가 기록: 1",answer)
        self.assertIn("검증된 순손익이 아님",answer)
        self.assertEqual(self.counts["trades"],1)

    def test_no_trades_does_not_claim_zero_percent_winrate(self):
        self.module.fetch_rows_direct=lambda *a,**kw: []
        with patch.object(bridge,"load_reservation",return_value=self.module):
            answer=bridge.reservation_answer("승률",self.base,now=200)
        self.assertIn("승률: 집계 대상 없음",answer)
        self.assertNotIn("승률: 0.0%",answer)

    def test_no_reservation_never_invents_account_balance(self):
        with self.assertRaisesRegex(RuntimeError,"SOURCE_UNAVAILABLE"):
            bridge.load_reservation(self.base)
        answer=bridge.reservation_answer("계좌",self.base,now=200)
        self.assertIn("조회 실패",answer)
        self.assertNotIn("272.36",answer)

    def test_only_known_code_paths_invoked_and_import_never_runs_sender(self):
        fixture=self.base/"reservation.py"
        fixture.write_text(
            'TELEGRAM_TOKEN="secret-fixture-token"\n'
            'TELEGRAM_CHAT_ID="1234"\n'
            'def main():\n'
            '    raise AssertionError("sender loop executed")\n'
            'if __name__ == "__main__":\n'
            '    main()\n'
        )
        bridge._LOADED.clear()
        token,chat=bridge.reservation_credentials(self.base)
        self.assertEqual((token,chat),("secret-fixture-token","1234"))
        fixture.unlink()

    def test_legacy_report_source_cannot_be_symlink(self):
        source=self.base/"reservation.py"
        source.symlink_to(ROOT/"runtime/telegram_reservation_bridge.py")
        with self.assertRaisesRegex(RuntimeError,"SOURCE_UNAVAILABLE"):
            bridge.load_reservation(self.base)

    def test_query_bot_can_route_into_reservation_on_explicit_mode(self):
        path=ROOT/"runtime/telegram_readonly_query.py"
        spec=importlib.util.spec_from_file_location("telegram_readonly_query",path)
        query=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(query)
        with (patch.dict(os.environ,{"TELEGRAM_RESERVATION_QUERY":"1"}),
              patch.dict(sys.modules,{"telegram_reservation_bridge":bridge}),
              patch.object(bridge,"load_reservation",return_value=self.module)):
            answer=query.answer("현금",base=self.base,now=200)
        self.assertIn("지갑 잔고",answer)
        self.assertIn("272.36",answer)

    def test_watchdog_uses_same_reservation_credentials_if_present(self):
        fixture=self.base/"reservation.py"
        fixture.write_text("TELEGRAM_TOKEN='x'\nTELEGRAM_CHAT_ID='1'\n")
        path=ROOT/"runtime/telegram_critical_watch.py"
        spec=importlib.util.spec_from_file_location("telegram_critical_watch",path)
        watch=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        with (patch.dict(sys.modules,{"telegram_reservation_bridge":bridge}),
              patch.object(bridge,"load_reservation",return_value=self.module)):
            token,chat=watch._telegram_credentials(self.base)
        self.assertEqual((token,chat),("BOT_SECRET_TEST_NOT_EXPORT","12345"))
        fixture.unlink()


if __name__ == "__main__":
    unittest.main()
