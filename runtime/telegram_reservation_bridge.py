#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only question adapter using the installed reservation.py reporter.

Never starts the existing five-minute reporting loop, places orders, restarts
services, writes to the trade database, or uploads any account information.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
KST = timezone(timedelta(hours=9))
MAX_EXIT_ROWS = 5000
_LOADED = {}


def load_reservation(base=BASE):
    path = base / "reservation.py"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise RuntimeError("RESERVATION_SOURCE_UNAVAILABLE")
    key = str(path.resolve())
    if key not in _LOADED:
        spec = importlib.util.spec_from_file_location("bbot_readonly_reservation", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("RESERVATION_SOURCE_UNAVAILABLE")
        module = importlib.util.module_from_spec(spec)
        # Import initializes reservation logging/env but does not run its main loop.
        spec.loader.exec_module(module)
        _LOADED[key] = module
    return _LOADED[key]


def reservation_credentials(base=BASE):
    # Obtain the exact values the existing five-minute report uses.
    module = load_reservation(base)
    token = str(getattr(module, "TELEGRAM_TOKEN", "") or "")
    chat = str(getattr(module, "TELEGRAM_CHAT_ID", "") or "")
    # Query service must be for one private positive chat only.
    if not token or not re.fullmatch(r"[0-9]{1,20}", chat):
        raise RuntimeError("RESERVATION_PRIVATE_CHAT_NOT_CONFIGURED")
    return token, chat


def _status(run=subprocess.run):
    lines = ["운영 상태 (조회 전용)"]
    for unit in ("r4-runtime.service", "runner.service", "collector.service"):
        try:
            proc = run(["systemctl", "is-active", unit],
                       capture_output=True, text=True, timeout=3)
            value = str(proc.stdout or "").strip()
            state = value if value in ("active", "inactive", "failed") else "미확인"
        except (OSError, subprocess.TimeoutExpired):
            state = "미확인"
        lines.append(unit + ": " + state)
    lines.append("웹소켓 실연결 상태는 아직 직접 계측하지 않음")
    return "\n".join(lines)


def _account(module):
    # Calls the same already-running reporter's read-only Binance account snapshot.
    wallet, available, unrealized = module.account_summary()
    if wallet is None or available is None or unrealized is None:
        return "바이낸스 계좌 조회 실패. 과거 값으로 추정하지 않음."
    return "\n".join((
        "USD-M 계좌 (reservation.py 조회)",
        f"지갑 잔고: {float(wallet):,.2f} USDT",
        f"가용잔고: {float(available):,.2f} USDT",
        f"미실현 손익: {float(unrealized):+,.2f} USDT",
        f"지갑잔고 + 미실현손익: {float(wallet)+float(unrealized):,.2f} USDT",
        "실시간 조회 또는 최대 120초 캐시. 가용잔고는 출금 가능한 현금과 다를 수 있음.",
    ))


def _positions(module):
    rows = module.live_positions()
    if not isinstance(rows, list):
        return "포지션 조회 실패"
    if not rows:
        return "열린 포지션 없음 (거래소 조회)"
    lines = ["보유 포지션 (reservation.py 거래소 조회)"]
    for row in rows[:30]:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{3,24}", symbol):
            continue
        try:
            amount = float(row.get("positionAmt") or 0)
            profit = float(row.get("unRealizedProfit") or 0)
        except (ValueError, TypeError):
            continue
        if amount:
            lines.append(f"{symbol} {'LONG' if amount>0 else 'SHORT'} / 미실현 {profit:+.2f} USDT")
    if len(rows) > 30:
        lines.append(f"추가 {len(rows)-30}건 생략")
    return "\n".join(lines) if len(lines) > 1 else "포지션 값 확인 불가"


def _trade_stats(module, days=0, now=None):
    # Avoid trade_stats(): it updates the legacy lifetime statistics JSON.
    now = time.time() if now is None else float(now)
    rows = module.fetch_rows_direct("exit_history", limit=MAX_EXIT_ROWS)
    if not isinstance(rows, list):
        return "청산 이력 조회 실패"
    if days == 0:
        cutoff = datetime.fromtimestamp(now, KST).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        label = "오늘(KST)"
    else:
        cutoff, label = now - days * 86400, f"최근 {days}일"
    summary = module.empty_trade_stats()
    unresolved = 0
    for row in rows:
        if not isinstance(row, dict) or module.exit_event_ts(row) < cutoff:
            continue
        metrics = module.resolved_exit_metrics(row)
        if metrics.get("resolved"):
            module.accumulate_trade_stats(summary, metrics["pnl"], metrics["roi"])
        else:
            unresolved += 1
    summary = module.finalize_trade_stats(summary)
    win, loss = int(summary["win"]), int(summary["loss"])
    count = int(summary["count"])
    rate = f"{100*win/(win+loss):.1f}%" if win+loss else "집계 대상 없음"
    return "\n".join((
        f"{label} 기존 reservation 청산 통계",
        f"청산 {count}건 / 승 {win} / 패 {loss} / 무 {int(summary['flat'])}",
        f"승률: {rate}",
        f"청산 손익(기존 계산법): {float(summary['pnl']):+,.2f} USDT",
        f"계산 불가 기록: {unresolved}건",
        f"최근 최대 {MAX_EXIT_ROWS}개 행 표본. 이전 행은 포함되지 않을 수 있음.",
        "수수료·펀딩비 및 R4 증거 완결성 검증 전 수치이므로 검증된 순손익이 아님.",
    ))


def _strategies(module):
    data = module.build_strategy_stats()
    if not isinstance(data, dict) or not data:
        return "전략별 청산 통계 없음 또는 조회 불가"
    lines = ["전략별 청산 (reservation.py 통계)"]
    for key, row in sorted(data.items(), key=lambda x: str(x[0]))[:20]:
        if not isinstance(key, tuple) or len(key) != 2 or not isinstance(row, dict):
            continue
        tf, strategy = map(str, key)
        if not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,160}", strategy):
            continue
        win, loss = int(row.get("win") or 0), int(row.get("loss") or 0)
        rate = f"{100*win/(win+loss):.1f}%" if win+loss else "표본 없음"
        lines.append(f"{tf[:8]} {strategy[:30]}: {int(row.get('cnt') or 0)}건 / 승률 {rate} / 손익 {float(row.get('pnl') or 0):+.2f}")
    return "\n".join(lines)


def _cycle(module):
    market_direction, market_scores, market_total, count = module.load_cycle_report()
    btc_direction, btc_scores, btc_total = module.load_btc_cycle_report()
    lines = [f"CYCLE 비교 ({count}종합 ↔ BTCUSDT)"]
    for tf in ("1h", "6h", "12h", "1d", "1w", "1m"):
        lines.append(f"{tf.upper()}: 종합 {float(market_scores.get(tf) or 0):+.2f} / BTC {float(btc_scores.get(tf) or 0):+.2f}")
    lines.append(f"전체: 종합 {float(market_total):+.2f} / BTC {float(btc_total):+.2f}")
    lines.append("시장 참고 지표이며 실거래 진입 가능 여부를 확정하지 않음.")
    return "\n".join(lines)


def reservation_answer(text, base=BASE, now=None):
    """Static read-only query routing; never accepts SQL or remote commands."""
    q = re.sub(r"\s+", "", str(text or "").lower())[:160]
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now, KST).strftime("%m-%d %H:%M KST")
    if any(s in q for s in ("도움", "help", "명령")):
        body = "질문: 현금, 포지션, 오늘 승률, 최근 7일 거래, 전략별 성과, CYCLE, 서버 상태"
    elif any(s in q for s in ("상태", "서버", "웹소켓", "컬렉터", "r4")):
        body = _status()
    else:
        try:
            reservation = load_reservation(base)
            if any(s in q for s in ("현금", "잔고", "자산", "계좌", "증거금", "usdt")):
                body = _account(reservation)
            elif any(s in q for s in ("포지션", "보유", "long", "short")):
                body = _positions(reservation)
            elif any(s in q for s in ("전략별", "전략", "strategy")):
                body = _strategies(reservation)
            elif any(s in q for s in ("cycle", "사이클", "시장점수", "btc비교")):
                body = _cycle(reservation)
            elif any(s in q for s in ("7일", "일주일", "일주")):
                body = _trade_stats(reservation, 7, now)
            elif any(s in q for s in ("승률", "거래", "청산", "수익", "손익", "몇번", "횟수")):
                body = _trade_stats(reservation, 0, now)
            else:
                body = "지원 질문: 현금, 포지션, 승률, 7일 거래, 전략, CYCLE, 서버 상태"
        except Exception:
            # Do not expose raw exception text containing private exchange URLs or keys.
            body = "reservation.py 조회 실패: 값을 추정하지 않음."
    return (body + "\n기준: " + stamp)[:3450]
