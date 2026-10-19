#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated R4 critical-failure observer; never starts/stops a trading service.

Opt-in command: --check; one-time Telegram smoke test: --send-test.
The module is intentionally not installed by an R4 receiver or GitHub workflow.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
STATE_NAME = "telegram_critical_watch_state.json"
LOCK_NAME = "telegram_critical_watch.lock"
MIN_SAMPLE_GAP = 45
REPEAT_INTERVAL = 6 * 3600
SAFE_CODES = (
    "R4_RUNTIME_INACTIVE",
    "LEGACY_RUNNER_ACTIVE",
    "COLLECTOR_INACTIVE",
    "COLLECTOR_HEARTBEAT_STALE",
    "COLLECTOR_HEARTBEAT_MISSING",
    "R4_LEDGER_MISSING",
    "R4_LEDGER_UNREADABLE",
    "R4_BASKET_MISMATCH",
)


def _service(unit, run=subprocess.run):
    try:
        result = run(["systemctl", "is-active", unit],
                     capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    value = str(result.stdout or "").strip().lower()
    if value == "active" and result.returncode == 0:
        return "ACTIVE"
    if value in ("inactive", "failed", "deactivating") and result.returncode != 0:
        return "INACTIVE"
    return "UNKNOWN"


def _heartbeat_problem(base, now):
    path = base / "strategy_lab/results/v3/collector_heartbeat.json"
    if path.is_symlink() or not path.is_file():
        return "COLLECTOR_HEARTBEAT_MISSING"
    try:
        if path.stat().st_size > 16 * 1024:
            return "COLLECTOR_HEARTBEAT_MISSING"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("schema") != "B_BOT_V3_COLLECTOR_HEARTBEAT_V1":
            return "COLLECTOR_HEARTBEAT_MISSING"
        age = now - float(data.get("updated_at_epoch") or 0)
        return None if 0 <= age <= 300 else "COLLECTOR_HEARTBEAT_STALE"
    except (OSError, ValueError, TypeError):
        return "COLLECTOR_HEARTBEAT_MISSING"


def _ledger_problem(base):
    path = base / "state/history.db"
    if path.is_symlink() or not path.is_file():
        return "R4_LEDGER_MISSING"
    connection = None
    try:
        uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.3)
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='r4_basket_snapshots'"
        ).fetchone()
        if not table:
            return "R4_LEDGER_UNREADABLE"
        mismatch = connection.execute(
            "SELECT 1 FROM r4_basket_snapshots WHERE mismatch=1 LIMIT 1"
        ).fetchone()
        return "R4_BASKET_MISMATCH" if mismatch else None
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return "R4_LEDGER_UNREADABLE"
    finally:
        if connection is not None:
            connection.close()


def observe(base=BASE, now=None, run=subprocess.run):
    """Return only a bounded allowlisted set of problem codes; no trade data."""
    stamp = time.time() if now is None else now
    result = set()
    r4 = _service("r4-runtime.service", run)
    runner = _service("runner.service", run)
    collector = _service("collector.service", run)
    if r4 == "INACTIVE":
        result.add("R4_RUNTIME_INACTIVE")
    if runner == "ACTIVE":
        result.add("LEGACY_RUNNER_ACTIVE")
    if collector == "INACTIVE":
        result.add("COLLECTOR_INACTIVE")
    heartbeat = _heartbeat_problem(base, stamp)
    if heartbeat:
        result.add(heartbeat)
    ledger = _ledger_problem(base)
    if ledger:
        result.add(ledger)
    return sorted(result)


def _state_load(path):
    if path.is_symlink():
        raise RuntimeError("WATCH_STATE_SYMLINK")
    if not path.exists():
        return {}
    if path.stat().st_size > 16384:
        raise RuntimeError("WATCH_STATE_TOO_LARGE")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError("WATCH_STATE_INVALID")
    return obj


def _state_save(path, row):
    if path.is_symlink():
        raise RuntimeError("WATCH_STATE_SYMLINK")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temp.write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def evaluate_once(base, problems, send, now=None):
    """Alert after two spaced samples, dedupe for six hours, no phantom success.

    send(message) is deliberately injected for deterministic test coverage.
    """
    stamp = time.time() if now is None else float(now)
    selected = set(problems)
    if not selected.issubset(SAFE_CODES):
        raise ValueError("WATCH_UNKNOWN_CODE")
    path = base / "state" / STATE_NAME
    old = _state_load(path)
    counts = old.get("counts") if isinstance(old.get("counts"), dict) else {}
    next_counts = {}
    active = old.get("active") if isinstance(old.get("active"), dict) else {}
    new_active = {}
    to_send = []
    for code in SAFE_CODES:
        previous = counts.get(code) if isinstance(counts.get(code), dict) else {}
        last_sample = float(previous.get("last_sample") or 0)
        streak = int(previous.get("streak") or 0)
        clear = int(previous.get("clear") or 0)
        was_active = active.get(code) if isinstance(active.get(code), dict) else {}
        last_sent = float(was_active.get("last_sent") or 0)
        sampled = not last_sample or stamp - last_sample >= MIN_SAMPLE_GAP
        if code in selected:
            if sampled:
                streak += 1
                clear = 0
        elif sampled:
            streak = 0
            clear += 1
            if clear >= 2:
                last_sent = 0
        next_counts[code] = {
            "streak":min(streak, 3), "clear":min(clear, 3),
            "last_sample":stamp if sampled else last_sample,
        }
        new_active[code] = {"last_sent":last_sent}
        if code in selected and streak >= 2 and (not last_sent or stamp - last_sent >= REPEAT_INTERVAL):
            to_send.append(code)
    sent = False
    if to_send:
        # Only controlled identifiers; never include logs, balances, positions or secrets.
        message = "[R4 중대 장애 감지]\n" + "\n".join("- " + x for x in to_send)
        message += "\n직접 확인 필요. 자동 재시작/거래 조작 없음."
        try:
            send(message)
        except Exception:
            # Preserve observed streak without assuming delivery. Next check retries.
            _state_save(path, {"counts":next_counts, "active":new_active})
            raise RuntimeError("WATCH_TELEGRAM_SEND_FAILED") from None
        for code in to_send:
            new_active[code]["last_sent"] = stamp
        sent = True
    _state_save(path, {"counts":next_counts, "active":new_active})
    return {"status":"ALERT_SENT" if sent else "OBSERVATION_ONLY",
            "problem_count":len(selected), "alert_code_count":len(to_send)}


def _env_file(path):
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise RuntimeError("WATCH_ENV_TOO_LARGE")
        lines = path.read_text(encoding="utf-8").splitlines()
    except PermissionError:
        return {}
    result = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "BOT_TOKEN",
                           "TELEGRAM_CHAT_ID", "CHAT_ID"):
            result[key.strip()] = value.strip().strip("\"'")
    return result


def _telegram_credentials(base):
    # Prefer precisely the destination used by the existing 5-minute report.
    # Do not fall back to another bot silently if this reporter is installed.
    if (base / "reservation.py").is_file() or (base / "reservation.py").is_symlink():
        from telegram_reservation_bridge import reservation_credentials
        return reservation_credentials(base)
    settings = {}
    for path in (Path("/etc/bbot/runner.env"), base / ".env"):
        settings.update(_env_file(path))
    settings.update({k:v for k,v in os.environ.items() if k in (
        "TELEGRAM_BOT_TOKEN","TELEGRAM_TOKEN","BOT_TOKEN","TELEGRAM_CHAT_ID","CHAT_ID")})
    token = next((settings[k] for k in ("TELEGRAM_BOT_TOKEN","TELEGRAM_TOKEN","BOT_TOKEN")
                  if settings.get(k)), "")
    chat = next((settings[k] for k in ("TELEGRAM_CHAT_ID","CHAT_ID")
                 if settings.get(k)), "")
    # Not logged or uploaded.
    if not token or not chat or not chat.isdecimal():
        raise RuntimeError("WATCH_TELEGRAM_CONFIG_MISSING")
    return token, chat


def _telegram_send(token, chat_id, text):
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode({"chat_id":chat_id,"text":text,
                                         "disable_web_page_preview":"true"}).encode("utf-8"),
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read(1024*64))
    except Exception:
        # Exceptions may contain token-bearing URLs: never propagate them.
        raise RuntimeError("WATCH_TELEGRAM_TRANSPORT_FAILED") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("WATCH_TELEGRAM_REJECTED")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--send-test", action="store_true")
    args = parser.parse_args()
    try:
        token, chat_id = _telegram_credentials(BASE)
        lock = BASE / "state" / LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.is_symlink():
            raise RuntimeError("WATCH_LOCK_SYMLINK")
        with lock.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.send_test:
                _telegram_send(token, chat_id,
                    "[R4 장애 알림 테스트]\n독립 알림 채널 전송 확인용. 거래/서버 조작 없음.")
                print(json.dumps({"status":"TEST_SENT"}, ensure_ascii=True))
                return
            result = evaluate_once(
                BASE, observe(BASE),
                lambda msg: _telegram_send(token, chat_id, msg))
            print(json.dumps(result, ensure_ascii=True))
    except Exception:
        # RuntimeError and urllib exceptions could carry sensitive env/token URLs.
        raise SystemExit("WATCH_FAILED")


if __name__ == "__main__":
    main()
