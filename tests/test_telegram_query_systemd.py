"""Verify opt-in Telegram question poller cannot alter active trading services."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TelegramQuerySystemdTests(unittest.TestCase):
    def test_opt_in_service_imports_only_the_question_poller(self):
        service = (ROOT / "ops" / "telegram-readonly-query.service").read_text()
        timer = (ROOT / "ops" / "telegram-readonly-query.timer").read_text()
        self.assertIn("Type=oneshot", service)
        self.assertIn("User=ec2-user", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("PrivateTmp=true", service)
        self.assertIn(
            "ExecStart=/home/ec2-user/bot/venv/bin/python -B "
            "/home/ec2-user/bot/telegram_readonly_query.py "
            "--poll-once --use-reservation", service)
        self.assertIn(
            "ReadWritePaths=/home/ec2-user/bot/state "
            "/home/ec2-user/bot/logs", service)
        self.assertNotIn("ReadWritePaths=/home/ec2-user/bot\n", service)
        self.assertNotIn("ReadWritePaths=/home/ec2-user/bot\r", service)
        for disallowed in ("systemctl restart", "r4-runtime.service",
                           "runner.service", "reservation.py --", "order.py",
                           "DELETE FROM", "UPDATE r4_"):
            self.assertNotIn(disallowed, service)
            self.assertNotIn(disallowed, timer)

    def test_timer_is_separate_and_idle_between_question_poll_runs(self):
        timer = (ROOT / "ops" / "telegram-readonly-query.timer").read_text()
        self.assertIn("Unit=telegram-readonly-query.service", timer)
        self.assertIn("OnUnitInactiveSec=15s", timer)
        self.assertIn("AccuracySec=5s", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertNotIn("r4-runtime.service", timer)
        self.assertNotIn("telegram-critical-watch.service", timer)

    def test_query_script_remains_opt_in_and_requires_private_chat_auth(self):
        query = (ROOT / "runtime" / "telegram_readonly_query.py").read_text()
        self.assertIn('parser.add_argument("--poll-once"', query)
        self.assertIn('parser.add_argument("--use-reservation"', query)
        self.assertIn('chat.get("type") == "private"', query)
        self.assertIn('and str(author.get("id")) == allowed_user', query)
        self.assertIn('and not author.get("is_bot")', query)
        self.assertIn("TELEGRAM_RESERVATION_QUERY", query)
        self.assertNotIn("systemctl restart", query)
        self.assertNotIn("from order import", query)
        self.assertNotIn("from runner import", query)


if __name__ == "__main__":
    unittest.main()
