# 파일명: tests/test_telegram_readonly_query.py
"""Pure local tests. No Telegram network, exchange API, production DB, or server changes."""
import importlib.util
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "runtime" / "telegram_readonly_query.py"
spec = importlib.util.spec_from_file_location("telegram_readonly_query", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReadonlyTelegramQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "state").mkdir()
        self.db = self.base / "state" / "history.db"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE r4_active_legs(
                    leg_id TEXT, instance_id TEXT, status TEXT, updated_ts INTEGER
                );
                CREATE TABLE r4_order_intents(id INTEGER);
                CREATE TABLE r4_exit_intents(id INTEGER);
                CREATE TABLE r4_basket_snapshots(mismatch INTEGER);
                CREATE TABLE r4_trade_evidence(evidence_complete INTEGER, hard_fail INTEGER);
                INSERT INTO r4_active_legs VALUES('l1','i1','CLOSED',1792300000);
                INSERT INTO r4_active_legs VALUES('l2','i1','ACTIVE',1792300100);
                INSERT INTO r4_order_intents VALUES(1);
                INSERT INTO r4_order_intents VALUES(2);
                INSERT INTO r4_exit_intents VALUES(3);
                INSERT INTO r4_basket_snapshots VALUES(0);
                INSERT INTO r4_basket_snapshots VALUES(1);
                INSERT INTO r4_trade_evidence VALUES(1,0);
                INSERT INTO r4_trade_evidence VALUES(0,0);
            """)
        self.original = self.db.read_bytes()
        (self.base / "strategy_config.json").write_text(json.dumps({
            "r4_runtime": {"strategy_instances": {
                "SI_test": {"strategy_id": "V3_TEST", "tier": "CANARY",
                            "enabled": True, "entry_enabled": True}}}
        }))

    def test_phrase_router_never_interprets_arbitrary_commands(self):
        self.assertEqual(mod.command_for("오늘 승률 알려줘"), "승률")
        self.assertEqual(mod.command_for("지금 현금 얼마 있어?"), "계좌")
        self.assertEqual(mod.command_for("거래 몇 번 했어?"), "거래")
        self.assertEqual(mod.command_for("/전략"), "전략")
        self.assertEqual(mod.command_for("전략 잠금 상태"), "전략")
        self.assertEqual(mod.command_for("/buy BTC 100"), "도움말")

    def test_trade_count_is_leg_and_order_intents_not_unverified_fills(self):
        text = mod.answer("거래 횟수", self.base, now=1792380000)
        self.assertIn("전체 종료 Leg: 1", text)
        self.assertIn("현재 활성 Leg: 1", text)
        self.assertIn("누적 진입 주문 의도: 2", text)
        self.assertIn("누적 청산 주문 의도: 1", text)
        self.assertIn("승률 분모가 아님", text)
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_winrate_balance_do_not_invent_financial_numbers(self):
        self.assertIn("제공 불가", mod.answer("승률", self.base))
        self.assertIn("조회 경로 미검증", mod.answer("현금 잔고", self.base))
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_risk_and_strategy_are_scoped_and_readonly(self):
        self.assertIn("불일치 바스켓: 1", mod.answer("위험", self.base))
        self.assertIn("경제적 증거 불완전: 1", mod.answer("위험", self.base))
        self.assertIn("V3_TEST / CANARY", mod.answer("전략", self.base))
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_missing_database_is_unavailable_not_healthy(self):
        self.db.unlink()
        self.assertIn("조회 불가", mod.answer("거래", self.base))
        self.assertIn("조회 불가", mod.answer("위험", self.base))

    def test_unauthorized_user_never_receives_data_and_offset_advances(self):
        calls = []
        data = [{"update_id": 4, "message": {
            "chat": {"type": "private", "id": 123},
            "from": {"id": 999, "is_bot": False}, "text": "잔고"} }]
        def fake_api(token, action, payload):
            calls.append((action, payload))
            return data if action == "getUpdates" else {"ok": True}
        mod._offset_save(self.base / "state" / "telegram_query_offset.json", 0)
        n = mod.poll_once("SECRET_NO_LOG", "123", "123", self.base, api=fake_api)
        self.assertEqual(n, 0)
        self.assertEqual([x[0] for x in calls], ["getUpdates"])
        self.assertEqual(mod._offset_load(self.base/"state"/"telegram_query_offset.json"), 5)
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_private_authorized_user_gets_readonly_answer_only(self):
        calls = []
        data = [{"update_id": 9, "message": {
            "chat": {"type": "private", "id": 123},
            "from": {"id": 345, "is_bot": False}, "text": "거래 횟수"} }]
        def fake_api(token, action, payload):
            calls.append((action, payload))
            return data if action == "getUpdates" else {"ok": True}
        mod._offset_save(self.base / "state" / "telegram_query_offset.json", 0)
        n = mod.poll_once("SECRET_NO_LOG", "123", "345", self.base, api=fake_api)
        self.assertEqual(n, 1)
        self.assertEqual([x[0] for x in calls], ["getUpdates", "sendMessage"])
        self.assertIn("전체 종료 Leg", calls[1][1]["text"])
        self.assertNotIn("SECRET_NO_LOG", repr(calls[1]))
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_first_poll_skips_stale_message_backlog(self):
        calls = []
        data = [{"update_id": 49, "message": {
            "chat": {"type": "private", "id": 123},
            "from": {"id": 345, "is_bot": False}, "text": "거래"} }]
        def fake_api(token, action, payload):
            calls.append((action, payload))
            return data
        n = mod.poll_once("SECRET_NO_LOG", "123", "345", self.base, api=fake_api)
        self.assertEqual(n, 0)
        self.assertEqual([x[0] for x in calls], ["getUpdates"])
        self.assertEqual(calls[0][1]["offset"], -1)
        self.assertEqual(mod._offset_load(self.base/"state"/"telegram_query_offset.json"), 50)

    def test_invalid_cursor_symlink_refused(self):
        offset = self.base/"state"/"telegram_query_offset.json"
        offset.symlink_to(self.db)
        with self.assertRaisesRegex(RuntimeError, "SYMLINK"):
            mod._offset_load(offset)
        with self.assertRaisesRegex(RuntimeError, "SYMLINK"):
            mod._offset_save(offset, 2)

    def test_chat_must_be_private(self):
        message = {"chat": {"id": 123, "type": "group"},
                   "from": {"id": 345}, "text": "잔고"}
        self.assertFalse(mod._authorized(message, "123", "345"))
        message["chat"]["type"] = "private"
        self.assertTrue(mod._authorized(message, "123", "345"))
        message["from"]["is_bot"] = True
        self.assertFalse(mod._authorized(message, "123", "345"))


if __name__ == "__main__":
    unittest.main()
