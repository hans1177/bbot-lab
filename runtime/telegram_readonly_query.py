#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only R4 Telegram query bot (opt-in; never installed or launched automatically).

No order/client imports; only Telegram getUpdates/sendMessage are network operations.
Sensitive account data is unavailable until a separately verified read-only source exists.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
TRADE_DB = BASE / "state" / "history.db"
OFFSET = BASE / "state" / "telegram_query_offset.json"
ALLOW_COMMANDS = ("도움말", "상태", "거래", "전략", "위험", "승률", "계좌")


def command_for(text):
    """Static intent routing only: no natural-language-to-SQL or shell."""
    q = re.sub(r"\s+", "", str(text or "").strip().lower())[:160]
    if any(x in q for x in ("도움", "명령", "help", "뭘물")):
        return "도움말"
    if any(x in q for x in ("승률", "winrate", "얼마나이겼", "이긴비율", "수익률", "손익")):
        return "승률"
    if any(x in q for x in ("현금", "잔고", "자산", "계좌", "증거금", "usdt", "balance")):
        return "계좌"
    if any(x in q for x in ("거래", "체결", "진입", "청산", "횟수", "몇번", "trades")):
        return "거래"
    if any(x in q for x in ("전략", "canary", "strategy", "잠금")):
        return "전략"
    if any(x in q for x in ("위험", "불일치", "증거", "리스크", "risk")):
        return "위험"
    if any(x in q for x in ("상태", "서버", "컬렉터", "웹소켓", "정상", "status")):
        return "상태"
    return "도움말"


def _read_db(base):
    path = base / "state" / "history.db"
    if path.is_symlink() or not path.is_file():
        return None
    uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=0.3)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=300")
    return con


def _count(con, table, condition="", args=()):
    allowed = {"r4_active_legs", "r4_order_intents", "r4_exit_intents",
               "r4_trade_evidence", "r4_basket_snapshots"}
    if table not in allowed:
        raise ValueError("TABLE_NOT_ALLOWED")
    if table not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return None
    return int(con.execute("SELECT COUNT(*) FROM " + table + condition, args).fetchone()[0])


def _number(value):
    return "확인 불가" if value is None else str(value)


def _safe_config(base):
    path = base / "strategy_config.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _status(base):
    def service(unit):
        try:
            result = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                    capture_output=True, timeout=2)
            return "실행" if result.returncode == 0 else "중지/미확인"
        except (OSError, subprocess.TimeoutExpired):
            return "확인 불가"
    heartbeat = base / "strategy_lab/results/v3/collector_heartbeat.json"
    collector = "직접 확인 불가"
    if heartbeat.is_file() and not heartbeat.is_symlink() and heartbeat.stat().st_size <= 16384:
        try:
            d = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            age = time.time() - float(d.get("updated_at_epoch") or 0)
            collector = f"최신 수집 {int(age)}초 전" if 0 <= age <= 180 else "수집 지연 또는 날짜 오류"
        except (ValueError, OSError, TypeError):
            pass
    return "\n".join(("서버 상태 (조회 시각 기준)", f"R4: {service('r4-runtime.service')}",
                      f"구형 runner: {service('runner.service')}",
                      f"collector: {service('collector.service')}",
                      f"컬렉터 하트비트: {collector}",
                      "웹소켓 실연결/전략 전체 평가 상태는 직접 계측 전까지 미확인"))


def _trades(base, now):
    con = _read_db(base)
    if con is None:
        return "거래 원장 조회 불가 (원본 변경 없음)"
    with closing(con):
        today = datetime.fromtimestamp(now, KST).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int(today.timestamp())
        closed = _count(con, "r4_active_legs", " WHERE status='CLOSED'")
        closed_today = _count(con, "r4_active_legs", " WHERE status='CLOSED' AND updated_ts>=?", (start,))
        active = _count(con, "r4_active_legs", " WHERE status='ACTIVE'")
        entry_orders = _count(con, "r4_order_intents")
        exit_orders = _count(con, "r4_exit_intents")
        return "\n".join((
            "R4 거래 원장 (KST, 조회 시각 기준)",
            f"전체 종료 Leg: {_number(closed)}",
            f"오늘 갱신된 종료 상태 Leg: {_number(closed_today)}",
            f"현재 활성 Leg: {_number(active)}",
            f"누적 진입 주문 의도: {_number(entry_orders)}",
            f"누적 청산 주문 의도: {_number(exit_orders)}",
            "주의: Leg/주문 의도 건수는 거래소 체결 건수와 다르며 승률 분모가 아님.",
        ))


def _strategies(base):
    config = _safe_config(base)
    runtime = config.get("r4_runtime") if isinstance(config.get("r4_runtime"), dict) else {}
    instances = runtime.get("strategy_instances")
    if not isinstance(instances, dict):
        return "전략 설정 조회 불가"
    rows = []
    for instance_id, row in sorted(instances.items()):
        if len(rows) >= 25:
            break
        if not isinstance(row, dict):
            continue
        name = str(row.get("strategy_id") or "UNKNOWN")
        if not re.fullmatch("[A-Za-z0-9_.:-]{1,160}", name):
            continue
        tier = str(row.get("tier") or "UNKNOWN").upper()
        state = "진입 가능 설정" if row.get("enabled") is True and row.get("entry_enabled") is True and row.get("entry_blocked") is not True else "비활성/진입 제한 설정"
        rows.append(f"{name[:45]} / {tier[:8]} / {state}")
    return "전략 설정 (실제 체결 가능 여부와 다름)\n" + ("\n".join(rows) if rows else "설정된 전략 없음")


def _risk(base):
    con = _read_db(base)
    if con is None:
        return "위험 원장 조회 불가"
    with closing(con):
        mismatches = _count(con, "r4_basket_snapshots", " WHERE mismatch=1")
        incomplete = _count(con, "r4_trade_evidence", " WHERE evidence_complete=0")
        failures = _count(con, "r4_trade_evidence", " WHERE hard_fail=1")
        return "\n".join(("R4 위험/체결 증거 (누적 기록)",
                          f"현재 저장된 불일치 바스켓: {_number(mismatches)}",
                          f"경제적 증거 불완전: {_number(incomplete)}",
                          f"증거 hard_fail: {_number(failures)}",
                          "이 기록만으로 거래소 실시간 정합성을 보증하지 않음."))


def answer(text, base=BASE, now=None):
    """Read-only; return a bounded plain-text reply, never secret or account credentials."""
    if os.getenv("TELEGRAM_RESERVATION_QUERY") == "1":
        from telegram_reservation_bridge import reservation_answer
        return reservation_answer(text, base=base, now=now)
    cmd = command_for(text)
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d %H:%M KST")
    if cmd == "도움말":
        body = ("질문: 상태 / 거래 횟수 / 전략 / 위험 / 승률 / 현금 잔고\n"
                "모든 질문은 조회 전용. 매수·매도·서비스 재시작 명령 없음.")
    elif cmd == "승률":
        body = ("검증된 승률/순손익: 아직 제공 불가\n"
                "수수료·펀딩비·전략별 종료 Leg의 전체 체결 증거를 결합하고 완전성 검증을 통과해야 계산 가능.\n"
                "불완전한 증거를 승리/패배로 임의 분류하지 않음.")
    elif cmd == "계좌":
        body = ("실시간 사용 가능 현금/총자산: 아직 조회 경로 미검증\n"
                "거래 API 또는 오래된 운영 DB 값으로 추정하지 않음.\n"
                "별도의 승인된 읽기 전용 잔고 스냅샷 연동이 필요함.")
    else:
        try:
            if cmd == "상태":
                body = _status(base)
            elif cmd == "거래":
                body = _trades(base, now)
            elif cmd == "전략":
                body = _strategies(base)
            elif cmd == "위험":
                body = _risk(base)
            else:
                body = "요청한 조회는 아직 지원하지 않음"
        except (OSError, ValueError, sqlite3.Error):
            body = "조회 실패: 원본 자료를 변경하지 않았으며 상태를 정상으로 가정하지 않음."
    return (body + "\n기준: " + stamp)[:3400]


def _authorized(message, allowed_chat, allowed_user):
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return False
    chat = message.get("chat") or {}
    author = message.get("from") or {}
    return (chat.get("type") == "private"
            and str(chat.get("id")) == allowed_chat
            and str(author.get("id")) == allowed_user
            and not author.get("is_bot"))


def _offset_load(path):
    if path.is_symlink():
        raise RuntimeError("OFFSET_SYMLINK_FORBIDDEN")
    if not path.exists():
        return 0
    if path.stat().st_size > 1024:
        raise RuntimeError("OFFSET_FILE_TOO_LARGE")
    obj = json.loads(path.read_text(encoding="utf-8"))
    offset = obj.get("next_update_id")
    if not isinstance(offset, int) or offset < 0:
        raise RuntimeError("OFFSET_FILE_INVALID")
    return offset


def _offset_save(path, number):
    if path.is_symlink():
        raise RuntimeError("OFFSET_SYMLINK_FORBIDDEN")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temp.write_text(json.dumps({"next_update_id":int(number)}), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _api(token, action, payload):
    # No raw urllib exception reaches caller logs; token appears only in HTTPS URL.
    url = "https://api.telegram.org/bot" + token + "/" + action
    encoded = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=32) as response:
            result = json.loads(response.read(1024 * 1024))
    except Exception as exc:
        raise RuntimeError("TELEGRAM_REQUEST_FAILED") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("TELEGRAM_RESPONSE_INVALID")
    return result.get("result")


def poll_once(token, chat_id, user_id, base=BASE, api=_api):
    """Authenticated polling. Writes only isolated polling cursor, never the trading DB."""
    cursor = base / "state" / "telegram_query_offset.json"
    offset = _offset_load(cursor)
    if not cursor.exists():
        # 첫 설치에서 과거 채팅을 오래된 질문으로 재처리하거나 전송하지 않는다.
        latest = api(token, "getUpdates", {"offset":-1, "limit":1,
                    "timeout":0, "allowed_updates":'["message"]'})
        if not isinstance(latest, list):
            raise RuntimeError("TELEGRAM_BOOTSTRAP_INVALID")
        ids = [x.get("update_id") for x in latest if isinstance(x,dict) and isinstance(x.get("update_id"),int)]
        _offset_save(cursor, max(ids)+1 if ids else 0)
        return 0
    updates = api(token, "getUpdates", {"offset":offset, "limit":10,
                "timeout":20, "allowed_updates":'["message"]'})
    if not isinstance(updates, list):
        raise RuntimeError("TELEGRAM_UPDATES_INVALID")
    processed = 0
    for item in updates:
        if not isinstance(item, dict) or not isinstance(item.get("update_id"), int):
            continue
        next_offset = item["update_id"] + 1
        if next_offset < offset:
            continue
        message = item.get("message")
        if _authorized(message, chat_id, user_id):
            reply = answer(message["text"], base=base)
            api(token, "sendMessage", {"chat_id":chat_id, "text":reply,
                                       "disable_web_page_preview":"true"})
            processed += 1
        _offset_save(cursor, next_offset)
        offset = next_offset
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-once", action="store_true")
    parser.add_argument("--use-reservation", action="store_true",
                        help="use the deployed 5-minute reservation.py reporter account and chat")
    args = parser.parse_args()
    if not args.poll_once:
        parser.error("Use --poll-once only after Telegram owner preflight")
    if args.use_reservation:
        # The private conversation chat ID is also the user's Telegram ID.
        # The imported reporter is never executed as a script.
        from telegram_reservation_bridge import reservation_credentials
        try:
            token, chat_id = reservation_credentials(BASE)
        except Exception:
            raise SystemExit("RESERVATION_CREDENTIALS_UNAVAILABLE") from None
        user_id = chat_id
        os.environ["TELEGRAM_RESERVATION_QUERY"] = "1"
    else:
        token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
        chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID") or ""
        user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID") or ""
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{25,}", token):
        raise SystemExit("TELEGRAM_TOKEN_NOT_SET")
    if not chat_id.isdecimal() or not user_id.isdecimal():
        raise SystemExit("TELEGRAM_ALLOWLIST_NOT_SET")
    state = BASE / "state"
    state.mkdir(parents=True, exist_ok=True)
    lockpath = state / "telegram_query_lock"
    if lockpath.is_symlink():
        raise SystemExit("TELEGRAM_LOCK_SYMLINK")
    with lockpath.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("TELEGRAM_QUERY_ALREADY_ACTIVE")
        # No secrets or raw exception strings are printed.
        try:
            poll_once(token, chat_id, user_id)
        except Exception:
            raise SystemExit("TELEGRAM_POLL_FAILED")


if __name__ == "__main__":
    main()
