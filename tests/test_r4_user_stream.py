# 파일명: tests/test_r4_user_stream.py
"""R4 기존 user-data websocket keepalive 무주문/무네트워크 회귀검증."""
import ast
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "runtime" / "r4_user_stream.py"


def isolated_stream(logs):
    # 운영 모듈 import시 로그/환경을 건드리지 않고 실제 클래스 구현만 추출한다.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "R4UserStream")
    ns = {
        "json": json,
        "threading": threading,
        "_log_error": lambda *args, **kwargs: logs.append((args, kwargs)),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(SOURCE), "exec"), ns)
    return ns["R4UserStream"]


class Ticks:
    def __init__(self, sequence):
        self.sequence = iter(sequence)
        self.seconds = []

    def wait(self, seconds):
        self.seconds.append(seconds)
        return next(self.sequence)


class R4UserStreamTests(unittest.TestCase):
    def fixture(self, responses):
        logs, calls = [], []
        actions = iter(responses)

        def safe_call(method, **kwargs):
            calls.append(kwargs)
            outcome = next(actions)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        order = SimpleNamespace(
            client=SimpleNamespace(renew_listen_key=lambda **kwargs: None),
            safe_call=safe_call,
        )
        stream = isolated_stream(logs)(runtime=None, exchange=SimpleNamespace(order=order))
        stream.listen_key = "SECRET_SHOULD_NOT_APPEAR_IN_LOG"
        stream.stop_event = Ticks([False] * len(responses) + [True])
        return stream, logs, calls

    def test_transient_keepalive_exception_does_not_kill_existing_thread(self):
        stream, logs, calls = self.fixture([RuntimeError("temporary"), {"result": "OK"}])
        stream._keepalive()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(logs), 1)
        self.assertIn("WS_KEEPALIVE_FAIL", logs[0][0][1])
        self.assertNotIn("SECRET_SHOULD_NOT_APPEAR_IN_LOG", repr(logs))
        self.assertEqual(stream.stop_event.seconds, [1500, 1500, 1500])

    def test_none_keepalive_logs_fail_and_next_interval_recovers(self):
        stream, logs, calls = self.fixture([None, {"result": "OK"}])
        stream._keepalive()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(logs), 1)
        self.assertIn("WS_KEEPALIVE_FAIL", logs[0][0][1])

    def test_already_stopped_stream_never_renews(self):
        stream, logs, calls = self.fixture([])
        stream._keepalive()
        self.assertEqual(calls, [])
        self.assertEqual(logs, [])
        self.assertEqual(stream.stop_event.seconds, [1500])

    def test_order_update_is_forwarded_not_resubmitted(self):
        logs, received = [], []
        runtime = SimpleNamespace(process_binance_order_trade_update=lambda msg: received.append(msg))
        stream = isolated_stream(logs)(runtime=runtime, exchange=None)
        msg = {"e": "ORDER_TRADE_UPDATE", "o": {"s": "BTCUSDT", "S": "BUY"}}
        stream._message(None, json.dumps(msg))
        self.assertEqual(received, [msg])
        self.assertEqual(logs, [])

    def test_invalid_message_does_not_crash_user_data_callback(self):
        logs = []
        stream = isolated_stream(logs)(runtime=SimpleNamespace(), exchange=None)
        stream._message(None, "not json")
        self.assertEqual(len(logs), 1)
        self.assertIn("WS_EVENT_FAIL", logs[0][0][1])


if __name__ == "__main__":
    unittest.main()
