# 파일명: strategy_lab/v3_server_candle_watchdog.py
"""Isolated collector watchdog. Never controls R4, orders, or trading state."""
from __future__ import annotations

# ================= 임포트 =================
import argparse
import fcntl
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ================= 설정 =================
ROOT = Path(os.environ.get("BOT_BASE_DIR") or "/home/ec2-user/bot")
DATA = ROOT / "backtest_data"
SCHEMA = "B_BOT_V3_SERVER_CANDLE_WATCHDOG_V1"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "TRUMPUSDT", "JTOUSDT")
MAX_1H_AGE_SECONDS = 3 * 3600
RESTART_COOLDOWN_SECONDS = 30 * 60
MAX_RESTARTS_PER_DAY = 3
HEARTBEAT_MAX_AGE_SECONDS = 300
STATE_FILE = ROOT / "state/server_candle_watchdog_state.json"
LOCK_FILE = ROOT / "state/server_candle_watchdog.lock"
HEARTBEAT_FILE = ROOT / "strategy_lab/results/v3/collector_heartbeat.json"
LOG_FILE = ROOT / "logs/runner.log"
RESTARTABLE_CANDLE_REASONS = frozenset({"MISSING", "EMPTY", "NO_CLOSED_BAR", "STALE"})


# ================= 유틸 =================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            x = float(value)
            if not math.isfinite(x):
                return None
            if x > 1e12:
                return pd.to_datetime(x, unit="ms", utc=True)
            if x > 1e9:
                return pd.to_datetime(x, unit="s", utc=True)
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts
    except (TypeError, ValueError, OverflowError):
        return None


def read_state() -> dict:
    if STATE_FILE.is_symlink():
        raise RuntimeError("WATCHDOG_STATE_SYMLINK")
    if not STATE_FILE.exists():
        return {}
    if not STATE_FILE.is_file() or STATE_FILE.stat().st_size > 16 * 1024:
        raise RuntimeError("WATCHDOG_STATE_UNSAFE")
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("WATCHDOG_STATE_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise RuntimeError("WATCHDOG_STATE_UNREADABLE")
    return value


def atomic_write_state(value: dict) -> None:
    if STATE_FILE.is_symlink() or STATE_FILE.parent.is_symlink():
        raise RuntimeError("WATCHDOG_STATE_SYMLINK")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(STATE_FILE.name + "." + str(os.getpid()) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, STATE_FILE)
    finally:
        if temp.exists():
            temp.unlink()


def heartbeat_status(now: float) -> str:
    if HEARTBEAT_FILE.is_symlink() or not HEARTBEAT_FILE.is_file():
        return "MISSING"
    try:
        if HEARTBEAT_FILE.stat().st_size > 16 * 1024:
            return "INVALID"
        row = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8-sig"))
        if row.get("schema") != "B_BOT_V3_COLLECTOR_HEARTBEAT_V1":
            return "INVALID"
        age = now - float(row.get("updated_at_epoch") or 0)
        return "FRESH" if 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS else "STALE"
    except (OSError, ValueError, TypeError, AttributeError):
        return "INVALID"


def _log_transition(state: dict, status: str) -> None:
    # Only state transitions are logged; never export paths, exception text or keys.
    if state.get("last_logged_status") == status:
        return
    if LOG_FILE.is_symlink():
        raise RuntimeError("WATCHDOG_LOG_SYMLINK")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write("[주문][R4][COLLECTOR_WATCHDOG] " + status + "\n")
    state["last_logged_status"] = status


# ================= 캔들 검사 =================
def inspect_symbol(symbol: str) -> dict:
    path = DATA / symbol / "1h.parquet"
    result = {"symbol": symbol, "pass": False, "reason": None,
              "open_time": None, "closed_bar_age_seconds": None,
              "recent_gap_count": None, "rest_repair_verified": False}
    if path.is_symlink():
        result["reason"] = "UNSAFE_FILE"
        return result
    if not path.is_file():
        result["reason"] = "MISSING"
        return result
    try:
        frame = pd.read_parquet(path)
    except Exception:
        result["reason"] = "READ_FAIL"
        return result
    if frame.empty:
        result["reason"] = "EMPTY"
        return result
    required = {"open_time", "open", "high", "low", "close", "volume", "is_closed"}
    if not required.issubset(frame.columns):
        result["reason"] = "REQUIRED_COLUMNS_MISSING"
        return result
    try:
        closed = frame.loc[frame["is_closed"].eq(True)].copy()
        if closed.empty:
            result["reason"] = "NO_CLOSED_BAR"
            return result
        closed["_time"] = closed["open_time"].map(parse_timestamp)
        if closed["_time"].isna().any() or closed["_time"].duplicated().any():
            result["reason"] = "INVALID_TIMESTAMPS"
            return result
        closed.sort_values("_time", inplace=True)
        row = closed.iloc[-1]
        prices = [float(row[name]) for name in ("open", "high", "low", "close", "volume")]
        op, hi, lo, cl, vol = prices
        if (not all(math.isfinite(x) for x in prices) or min(op, hi, lo, cl) <= 0
                or hi < max(op, cl, lo) or lo > min(op, cl) or vol < 0):
            result["reason"] = "INVALID_OHLCV"
            return result
        stamp = row["_time"]
        now = pd.Timestamp.now(tz="UTC")
        age = (now - (stamp + pd.Timedelta(hours=1))).total_seconds()
        result["open_time"] = stamp.isoformat()
        result["closed_bar_age_seconds"] = float(age)
        tail = closed["_time"].tail(24)
        gaps = tail.diff().dropna().dt.total_seconds()
        result["recent_gap_count"] = int((gaps > 3601).sum())
        if age < -90:
            result["reason"] = "FUTURE_BAR"
        elif age > MAX_1H_AGE_SECONDS:
            result["reason"] = "STALE"
        else:
            result["pass"] = True
            result["reason"] = "FRESH_WITH_GAPS" if result["recent_gap_count"] else "FRESH"
    except (TypeError, ValueError, OverflowError, KeyError):
        result["reason"] = "INVALID_CANDLE_DATA"
    return result


# ================= 서비스 =================
def collector_active():
    # None means an unknown systemd result; never equate uncertainty with "down".
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "collector.service"],
            capture_output=True, text=True, check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or "").strip()
    if result.returncode == 0 and value == "active":
        return True
    if value in {"inactive", "failed", "deactivating"}:
        return False
    return None


def restart_collector() -> int:
    # No R4 restart, order operation, exit, or position mutation.
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "collector.service"],
            capture_output=True, text=True, check=False, timeout=35)
        return int(result.returncode)
    except (OSError, subprocess.TimeoutExpired):
        return 1


def _summary(symbols: tuple[str, ...]) -> tuple[list[dict], int, bool]:
    details = [inspect_symbol(symbol) for symbol in symbols]
    fresh = sum(bool(row["pass"]) for row in details)
    restartable_failed = sum(row["reason"] in RESTARTABLE_CANDLE_REASONS for row in details)
    # Corrupt files must never cause a blind collector restart.
    majority = restartable_failed >= len(symbols) // 2 + 1
    return details, fresh, majority


# ================= 메인 =================
def run_once(*, dry_run: bool, symbols: tuple[str, ...]) -> int:
    now = time.time()
    details, fresh, stale_majority = _summary(symbols)
    active = collector_active()
    heartbeat = heartbeat_status(now)
    state = read_state()
    attempts = sorted(float(x) for x in state.get("restart_attempt_epochs", [])
                      if isinstance(x, (int, float)) and 0 <= now - x < 86400)
    last_restart = max(attempts, default=float(state.get("last_restart_epoch") or 0))
    cooldown_ok = now - last_restart >= RESTART_COOLDOWN_SECONDS
    budget_ok = len(attempts) < MAX_RESTARTS_PER_DAY

    # A heartbeat proves collector activity, not a direct WebSocket frame/pong.
    reason = "HEALTHY"
    if active is None:
        reason = "SERVICE_STATUS_UNKNOWN"
    elif active is False:
        reason = "COLLECTOR_NOT_ACTIVE"
    elif stale_majority:
        reason = "MAJORITY_1H_STALE"
    elif heartbeat != "FRESH":
        reason = "COLLECTOR_HEARTBEAT_" + heartbeat
    restart_needed = reason in {"COLLECTOR_NOT_ACTIVE", "MAJORITY_1H_STALE",
                                "COLLECTOR_HEARTBEAT_STALE"}

    report = {
        "schema": SCHEMA, "checked_at": utc_now().isoformat(),
        "collector_active": active, "heartbeat_state": heartbeat,
        "direct_websocket_heartbeat_verified": False,
        "fresh_count": fresh, "symbol_count": len(details),
        "stale_majority": stale_majority, "restart_needed": restart_needed,
        "cooldown_ok": cooldown_ok, "daily_restart_budget_ok": budget_ok,
        "dry_run": dry_run, "reason": reason, "details": details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if dry_run:
        print("WATCHDOG_ACTION=DRY_RUN_WOULD_RESTART" if restart_needed
              else "WATCHDOG_ACTION=DRY_RUN_NO_ACTION")
        return 0

    if not restart_needed:
        status = "HEALTHY" if reason == "HEALTHY" else reason
        _log_transition(state, status)
        state.update(schema=SCHEMA, checked_at_epoch=now, last_reason=reason,
                     restart_attempt_epochs=attempts)
        atomic_write_state(state)
        return 0

    if not cooldown_ok or not budget_ok:
        action = "RESTART_SUPPRESSED_COOLDOWN" if not cooldown_ok else "RESTART_SUPPRESSED_DAILY_BUDGET"
        print("WATCHDOG_ACTION=" + action)
        _log_transition(state, action)
        state.update(schema=SCHEMA, checked_at_epoch=now, last_reason=reason,
                     restart_attempt_epochs=attempts)
        atomic_write_state(state)
        return 0

    time.sleep(20)
    confirmed_details, _, confirmed_majority = _summary(symbols)
    confirmed_service = collector_active()
    confirmed_heartbeat = heartbeat_status(time.time())
    if (confirmed_service is None
            or (confirmed_service is True and not confirmed_majority
                and confirmed_heartbeat == "FRESH")):
        print("WATCHDOG_ACTION=RECOVERED_OR_UNVERIFIED_BEFORE_RESTART")
        _log_transition(state, "RECOVERED_OR_UNVERIFIED_BEFORE_RESTART")
        state.update(schema=SCHEMA, checked_at_epoch=time.time(), last_reason=reason,
                     restart_attempt_epochs=attempts)
        atomic_write_state(state)
        return 0
    # If heartbeat alone was stale, require the second heartbeat to remain stale.
    if (reason == "COLLECTOR_HEARTBEAT_STALE"
            and confirmed_heartbeat != "STALE"):
        print("WATCHDOG_ACTION=HEARTBEAT_RECOVERED")
        return 0

    # Persist attempt before invoking systemd, so a crash cannot erase rate limits.
    attempt = time.time()
    attempts.append(attempt)
    state.update(schema=SCHEMA, checked_at_epoch=attempt, last_reason=reason,
                 last_restart_epoch=attempt, restart_attempt_epochs=attempts,
                 last_restart_rc=None)
    _log_transition(state, "COLLECTOR_RESTART_ATTEMPT")
    atomic_write_state(state)
    rc = restart_collector()
    state["last_restart_rc"] = rc
    _log_transition(state, "COLLECTOR_RESTART_OK" if rc == 0 else "COLLECTOR_RESTART_FAILED")
    atomic_write_state(state)
    print("WATCHDOG_ACTION=COLLECTOR_RESTART")
    print("WATCHDOG_RESTART_RC=" + str(rc))
    return rc


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true",
                      help="Explicitly permit collector-only restart after two checks")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(x.strip().upper() for x in args.symbols.split(",")
                                  if x.strip()))
    if len(symbols) < 3 or len(symbols) > 50 or any(
            not x.endswith("USDT") or not x.isalnum() or len(x) > 30 for x in symbols):
        raise SystemExit("INVALID_WATCHDOG_SYMBOLS")
    if not args.apply:
        return run_once(dry_run=True, symbols=symbols)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.is_symlink():
        raise RuntimeError("WATCHDOG_LOCK_SYMLINK")
    with LOCK_FILE.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("WATCHDOG_ACTION=ALREADY_RUNNING")
            return 0
        return run_once(dry_run=False, symbols=symbols)


if __name__ == "__main__":
    raise SystemExit(main())
