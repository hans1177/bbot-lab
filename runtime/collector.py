#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: collector.py

import os
import time
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from collections import defaultdict

import pandas as pd
from dotenv import load_dotenv
from websocket import WebSocketApp
from binance.client import Client
from threading import Lock

BASE_DIR = os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot")
DATA_DIR = os.getenv(
    "BBOT_CANDLE_DATA_DIR",
    f"{BASE_DIR}/backtest_data",
)  # LOCAL_CANDLE_DATA_DIR_ENV_V1
STATE_DIR = f"{BASE_DIR}/state"
LIVE_DIR = f"{STATE_DIR}/live"
LOG_FILE = f"{BASE_DIR}/logs/runner.log"
CONFIG_FILE = f"{BASE_DIR}/config.json"
EMA_STATE_FILE = f"{STATE_DIR}/ema_state.json"

EMA_STATE_REFRESH_SEC = 10
EMA_STATE_MIN_ROWS = 120
EMA_STATE_LOG_SEC = 60

INTERVALS = ["30m", "1h", "6h", "12h", "1d", "1w", "1M"]
HIGH_TF_INTERVALS = ["1w", "1M"]

# ============================================================
# 수집 시간봉 정책
# - 1h / 6h / 12h / 1d : WebSocket 실시간 수집 + 시작/재연결 시 REST 보충
# - 1w / 1M           : REST 주기 갱신
# - WebSocket 미완성봉은 state/live/<SYMBOL>.json 에만 기록
# - Parquet은 WebSocket 확정봉만 저장해 실시간 쓰기 폭증을 방지
# ============================================================
WS_INTERVALS = ["30m", "1h", "6h", "12h", "1d"]
REST_ONLY_INTERVALS = ["1w", "1M"]
REST_ONLY_REFRESH_SEC = {
    "1w": 600,
    "1M": 1800,
}

INITIAL_SYNC_LIMITS = {
    "30m": 300,
    "1h": 300,
    "6h": 300,
    "12h": 300,
    "1d": 300,
}
last_rest_only_refresh_ts = {}

WS_BASE = "wss://fstream.binance.com/ws"

CONFIG_RELOAD_SEC = 60
FLUSH_INTERVAL_SEC = 1.0
LIVE_FLUSH_INTERVAL_SEC = 1.0
WS_STALE_SEC = 300
WS_MONITOR_SEC = 30

# COLLECTOR_WS_REST_CONSISTENCY_V1
# Periodic REST reconciliation.
# No watchdog decision and no service restart.
WS_REST_CONSISTENCY_SEC = 900
WS_REST_CONSISTENCY_LIMIT = 50

# COLLECTOR_WS_CLIENT_PING_THREAD_OFF_20260607
# websocket-client의 ping_interval은 WS마다 ping thread를 추가 생성한다.
# Binance server ping에는 websocket-client가 pong 응답하므로 client-side ping은 끈다.
WS_CLIENT_PING_INTERVAL = 0
WS_CLIENT_PING_TIMEOUT = None

REST_START_GAP_SEC = 0.2
WS_START_GAP_SEC = 0.05
SYNC_WORKERS = 5

WS_RESTART_BATCH_SIZE = 10
WS_RESTART_BATCH_SLEEP_SEC = 2.0

MEM_LOG_SEC = 60
MEM_WARN_RSS_MB = 800
MEM_WARN_QUEUE_ROWS = 5000

HIGH_TF_CHECK_SEC = 600
HIGH_TF_SYNC_GAP_SEC = 3.0
HIGH_TF_LIMIT = 200
HIGH_TF_MIN_ROWS = {
    "1w": 30,
    "1M": 18,
}



env_path = "/etc/bbot/runner.env"
load_dotenv(env_path if os.path.exists(env_path) else f"{BASE_DIR}/.env")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(LIVE_DIR, exist_ok=True)

client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))

# ================= LOG =================

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

root = logging.getLogger()
root.setLevel(logging.INFO)

for h in list(root.handlers):
    root.removeHandler(h)

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setFormatter(fmt)
root.addHandler(fh)

class OrderTag(logging.Filter):
    def filter(self, record):
        if "[주문]" not in str(record.msg):
            record.msg = "[주문] " + str(record.msg)
        return True

root.addFilter(OrderTag())
logger = logging.getLogger()

# ================= STATE =================

# ================= STATE =================

active_symbols = set()
last_cfg_mtime = 0
last_cfg_check_ts = 0.0
last_high_tf_check_ts = 0.0

write_locks = {}
ws_threads = {}
ws_apps = {}
ws_last_msg_ts = {}
ws_started_ts = {}
ws_stop_flags = {}
ws_run_tokens = {}
ws_lock = Lock()


closed_queue = defaultdict(list)
live_kline_cache = defaultdict(dict)
live_dirty_symbols = set()
last_mem_log_ts = 0.0
last_ema_state_log_ts = 0.0
closed_queue_lock = Lock()
live_kline_lock = Lock()
ema_state_lock = Lock()
ema_state_cache = {}
log_dedupe_cache = {}
log_dedupe_lock = Lock()

# ================= UTIL =================

def now_ts():
    return time.time()

def log_dedupe(level, key, message, ttl_sec=60, exc_info=False):
    now = now_ts()
    dedupe_key = str(key)

    with log_dedupe_lock:
        last_ts = float(log_dedupe_cache.get(dedupe_key, 0.0) or 0.0)
        if now - last_ts < float(ttl_sec):
            return
        log_dedupe_cache[dedupe_key] = now

        if len(log_dedupe_cache) > 5000:
            cutoff = now - 3600.0
            stale_keys = [k for k, ts in log_dedupe_cache.items() if float(ts or 0.0) < cutoff]
            for stale_key in stale_keys:
                log_dedupe_cache.pop(stale_key, None)

    log_fn = getattr(logger, str(level).lower(), logger.info)
    log_fn(message, exc_info=exc_info)

def utc_dt_from_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def next_open_dt(dt, interval):
    if interval == "30m":
        return dt + pd.Timedelta(minutes=30)
    if interval == "1h":
        return dt + pd.Timedelta(hours=1)
    if interval == "6h":
        return dt + pd.Timedelta(hours=6)
    if interval == "12h":
        return dt + pd.Timedelta(hours=12)
    if interval == "1d":
        return dt + pd.Timedelta(days=1)
    if interval == "1w":
        return dt + pd.Timedelta(days=7)
    if interval == "1M":
        y = dt.year
        m = dt.month
        if m == 12:
            return datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        return datetime(y, m + 1, 1, tzinfo=timezone.utc)
    return dt


def is_kline_closed(open_ms, interval, now_ms=None):
    try:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        open_dt = utc_dt_from_ms(open_ms)
        close_dt = next_open_dt(open_dt, interval)
        close_ms = int(close_dt.timestamp() * 1000)
        return now_ms >= close_ms
    except Exception:
        return True

def collector_required_universe_file():

    return os.path.join(
        os.path.dirname(
            os.path.abspath(
                __file__
            )
        ),
        "strategy_lab",
        "results",
        "v3",
        "collector_required_universe.json",
    )


def collector_heartbeat_file():

    return os.path.join(
        os.path.dirname(
            os.path.abspath(
                __file__
            )
        ),
        "strategy_lab",
        "results",
        "v3",
        "collector_heartbeat.json",
    )


def load_required_universe():

    try:

        with open(
            collector_required_universe_file(),
            "r",
            encoding="utf-8-sig",
        ) as handle:

            obj = json.load(
                handle
            )

        values = (
            obj.get(
                "required_universe"
            )
            or obj.get(
                "required_symbols"
            )
            or []
        )

        return {
            str(x).strip().upper()
            for x in values
            if str(x).strip()
        }

    except Exception:

        return set()


def load_symbols():

    with open(
        CONFIG_FILE,
        "r",
        encoding="utf-8",
    ) as handle:

        data = json.load(
            handle
        )

    config_symbols = set()

    for raw_symbol, settings in (
        data.get("SYMBOLS", {})
        or {}
    ).items():

        enabled = True

        if settings is False:
            enabled = False

        if (
            isinstance(settings, dict)
            and settings.get("enabled")
            is False
        ):
            enabled = False

        symbol = str(
            raw_symbol
        ).strip().upper()

        if symbol and enabled:
            config_symbols.add(
                symbol
            )

    required_symbols = (
        load_required_universe()
    )

    return (
        config_symbols
        | required_symbols
    )


def write_collector_heartbeat():

    path = (
        collector_heartbeat_file()
    )

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True,
    )

    obj = {
        "schema":
            "B_BOT_V3_COLLECTOR_HEARTBEAT_V1",

        "updated_at_epoch":
            float(time.time()),

        "pid":
            int(os.getpid()),

        "active_symbols":
            sorted(active_symbols),
    }

    tmp = (
        path
        + "."
        + str(os.getpid())
        + ".tmp"
    )

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            obj,
            handle,
            ensure_ascii=False,
            sort_keys=True,
        )

    os.replace(
        tmp,
        path,
    )


def get_parquet_path(symbol, interval):
    return f"{DATA_DIR}/{symbol}/{interval}.parquet"

def has_parquet(symbol, interval):
    path = get_parquet_path(symbol, interval)
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception:
        return False
    
def has_valid_high_tf_parquet(symbol, interval):
    path = get_parquet_path(symbol, interval)

    try:
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            return False

        df = pd.read_parquet(path)
        if df is None or df.empty:
            return False

        if "open_time" not in df.columns:
            return False

        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        df = df.dropna(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        min_rows = HIGH_TF_MIN_ROWS.get(interval, 1)
        if len(df) < min_rows:
            log_dedupe(
                "info",
                f"HIGH_TF_TOO_SHORT:{symbol}:{interval}:{len(df)}:{min_rows}",
                f"[주문][COLLECTOR][HIGH_TF_TOO_SHORT] {symbol} {interval} rows={len(df)} need={min_rows}",
                ttl_sec=1800,
            )
            return False

        last_ot = df["open_time"].iloc[-1]
        now_utc = pd.Timestamp.utcnow()

        if interval == "1w":
            stale_limit = now_utc - pd.Timedelta(days=14)
        elif interval == "1M":
            stale_limit = now_utc - pd.Timedelta(days=62)
        else:
            stale_limit = now_utc - pd.Timedelta(days=3650)

        if last_ot < stale_limit:
            log_dedupe(
                "info",
                f"HIGH_TF_STALE:{symbol}:{interval}:{last_ot}",
                f"[주문][COLLECTOR][HIGH_TF_STALE] {symbol} {interval} last_ot={last_ot} stale_limit={stale_limit}",
                ttl_sec=1800,
            )
            return False

        return True

    except Exception as e:
        logger.error(f"[주문][COLLECTOR][HIGH_TF_VALIDATE_FAIL] {symbol} {interval} {e}", exc_info=True)
        return False

def safe_json_dump(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def get_live_path(symbol):
    return f"{LIVE_DIR}/{str(symbol or '').upper().strip()}.json"

def update_live_kline(symbol, interval, row):
    sym = str(symbol or "").upper().strip()
    if not sym or interval not in WS_INTERVALS:
        return

    with live_kline_lock:
        live_kline_cache[sym][interval] = dict(row)
        live_dirty_symbols.add(sym)

def flush_live_klines():
    dirty = []
    payloads = {}

    with live_kline_lock:
        if not live_dirty_symbols:
            return

        dirty = list(live_dirty_symbols)
        live_dirty_symbols.clear()

        for sym in dirty:
            payloads[sym] = dict(live_kline_cache.get(sym, {}))

    for sym, payload in payloads.items():
        try:
            safe_json_dump(get_live_path(sym), payload)
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][LIVE_FLUSH_FAIL] {sym} {e}", exc_info=True)

def get_lock(key):
    return write_locks.setdefault(key, Lock())

def save_rows(symbol, interval, rows):
    key = (symbol, interval)
    lock = get_lock(key)

    with lock:
        path = get_parquet_path(symbol, interval)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        df_new = pd.DataFrame(rows)
        df_new["open_time"] = pd.to_datetime(df_new["open_time"], utc=True)

        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
                df = pd.concat([df, df_new], ignore_index=True)
            except Exception:
                logger.error(f"[주문][COLLECTOR][PARQUET_CORRUPT] {symbol} {interval} RESET")
                df = df_new
        else:
            df = df_new

        df = (
            df.drop_duplicates("open_time", keep="last")
            .sort_values("open_time", kind="mergesort")
            .reset_index(drop=True)
        )

        tmp = f"{path}.{os.getpid()}.tmp"
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)

def queue_closed_row(symbol, interval, row):
    with closed_queue_lock:
        closed_queue[(symbol, interval)] = [row]

def flush_closed_queue():
    local = {}
    with closed_queue_lock:
        if not closed_queue:
            return
        for k, v in closed_queue.items():
            if v:
                local[k] = v[:]
        closed_queue.clear()

    for (symbol, interval), rows in local.items():
        try:
            save_rows(symbol, interval, rows)
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][FLUSH_FAIL] {symbol} {interval} {e}", exc_info=True)

def rest_sync(symbol, interval, limit):
    try:
        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            limit=limit + 1
        )
    except Exception as e:
        logger.error(f"[주문][COLLECTOR][REST_SYNC_FAIL] {symbol} {interval} {e}")
        return

    now_ms = int(time.time() * 1000)
    rows = []
    for k in klines:
        rows.append({
            "open_time": utc_dt_from_ms(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
            "quote_volume": float(k[7]),
            "trade_count": int(k[8]),
            "taker_buy_base_volume": float(k[9]),
            "taker_buy_quote_volume": float(k[10]),
            "is_closed": bool(is_kline_closed(k[0], interval, now_ms)),
        })

    try:
        save_rows(symbol, interval, rows)
        logger.info(f"[주문][COLLECTOR][REST_SYNC] {symbol} {interval} rows={len(rows)}")
    except Exception as e:
        logger.error(f"[주문][COLLECTOR][REST_SAVE_FAIL] {symbol} {interval} {e}", exc_info=True)

def rest_backfill(symbol, interval, limit=5):
    try:
        path = get_parquet_path(symbol, interval)
        if not os.path.exists(path):
            return

        df = pd.read_parquet(path)
        if df.empty:
            return

        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        last_ot = df["open_time"].max()

        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        now_ms = int(time.time() * 1000)
        rows = []
        for k in klines:
            ot = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            if ot >= last_ot:
                rows.append({
                    "open_time": ot,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]),
                    "quote_volume": float(k[7]),
                    "trade_count": int(k[8]),
                    "taker_buy_base_volume": float(k[9]),
                    "taker_buy_quote_volume": float(k[10]),
                    "is_closed": bool(is_kline_closed(k[0], interval, now_ms)),
                })

        if rows:
            save_rows(symbol, interval, rows)
            logger.info(f"[주문][COLLECTOR][REST_BACKFILL] {symbol} {interval} +{len(rows)}")

    except Exception as e:
        logger.error(f"[주문][COLLECTOR][REST_BACKFILL_FAIL] {symbol} {interval} {e}", exc_info=True)


def repair_missing_liquidity_batch(symbol, interval, limit=500):
    try:
        path = get_parquet_path(symbol, interval)
        if not os.path.exists(path):
            return 0

        df = pd.read_parquet(path)
        if df is None or df.empty or "open_time" not in df.columns:
            return 0

        required = (
            "close_time",
            "quote_volume",
            "trade_count",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        )

        missing = pd.Series(False, index=df.index)

        for col in required:
            if col not in df.columns:
                missing[:] = True
            else:
                missing |= pd.to_numeric(
                    df[col],
                    errors="coerce",
                ).isna()

        if not bool(missing.any()):
            return 0

        times = pd.to_datetime(
            df.loc[missing, "open_time"],
            utc=True,
            errors="coerce",
        ).dropna().sort_values()

        now_ms = int(time.time() * 1000)

        closed_times = []

        for ot in times:
            open_ms = int(ot.timestamp() * 1000)

            if is_kline_closed(
                open_ms,
                interval,
                now_ms,
            ):
                closed_times.append(open_ms)

        if not closed_times:
            return 0

        batch_limit = max(
            1,
            min(int(limit), 500),
        )

        start_ms = closed_times[0]

        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_ms,
            limit=batch_limit,
        )

        if not klines:
            return 0

        missing_set = set(closed_times)
        rows = []

        for k in klines:
            open_ms = int(k[0])

            if open_ms not in missing_set:
                continue

            if not is_kline_closed(
                open_ms,
                interval,
                now_ms,
            ):
                continue

            rows.append({
                "open_time": utc_dt_from_ms(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trade_count": int(k[8]),
                "taker_buy_base_volume": float(k[9]),
                "taker_buy_quote_volume": float(k[10]),
                "is_closed": True,
            })

        if not rows:
            return 0

        save_rows(
            symbol,
            interval,
            rows,
        )

        logger.info(
            f"[COLLECTOR][PIT_LIQUIDITY_BATCH_REPAIR] "
            f"{symbol} {interval} "
            f"start_ms={start_ms} repaired={len(rows)}"
        )

        return len(rows)

    except Exception as e:
        logger.error(
            f"[COLLECTOR][PIT_LIQUIDITY_BATCH_REPAIR_FAIL] "
            f"{symbol} {interval} {e}",
            exc_info=True,
        )
        return 0


def repair_corrupt_open_time_file(symbol, interval):
    """
    Deterministically restore canonical Binance kline open_time from close_time.

    Contract:
    - WS_INTERVALS only.
    - close_time must be complete integer epoch milliseconds.
    - interval duration comes from existing next_open_dt().
    - conflicting duplicate candle payloads fail closed.
    - compatible duplicates retain the most complete deterministic row.
    - atomic replacement under the existing per-file write lock.
    """
    if interval not in WS_INTERVALS:
        return 0

    key = (symbol, interval)
    lock = get_lock(key)

    with lock:
        path = get_parquet_path(symbol, interval)

        if not os.path.exists(path):
            return 0

        try:
            df = pd.read_parquet(path)
        except Exception as e:
            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_READ_FAIL] "
                f"{symbol} {interval} {e}",
                exc_info=True,
            )
            return 0

        if df is None or df.empty:
            return 0

        if "open_time" not in df.columns or "close_time" not in df.columns:
            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_CONTRACT_FAIL] "
                f"{symbol} {interval} required_column_missing"
            )
            return 0

        close_num = pd.to_numeric(
            df["close_time"],
            errors="coerce",
        )

        if bool(close_num.isna().any()):
            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_CONTRACT_FAIL] "
                f"{symbol} {interval} close_time_missing"
            )
            return 0

        close_round = close_num.round()

        if bool(((close_num - close_round).abs() > 1e-6).any()):
            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_CONTRACT_FAIL] "
                f"{symbol} {interval} close_time_non_integer"
            )
            return 0

        probe = datetime(
            2020,
            1,
            1,
            tzinfo=timezone.utc,
        )
        nxt = next_open_dt(
            probe,
            interval,
        )

        step_ms = int(
            (nxt - probe).total_seconds()
            * 1000
        )

        if step_ms <= 0:
            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_CONTRACT_FAIL] "
                f"{symbol} {interval} invalid_interval_duration"
            )
            return 0

        expected_ms = (
            close_round.astype("int64")
            - (step_ms - 1)
        )

        old_ot = pd.to_datetime(
            df["open_time"],
            utc=True,
            errors="coerce",
        )
        old_ms = (
            old_ot.astype("int64")
            // 1_000_000
        )

        mismatch = (
            old_ot.isna()
            |
            (old_ms != expected_ms)
        )

        repair_count = int(
            mismatch.sum()
        )

        if repair_count <= 0:
            return 0

        work = df.copy()
        work["open_time"] = pd.to_datetime(
            expected_ms,
            unit="ms",
            utc=True,
        )

        dup = work["open_time"].duplicated(
            keep=False,
        )

        check_cols = [
            c for c in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
                "trade_count",
                "taker_buy_base_volume",
                "taker_buy_quote_volume",
            )
            if c in work.columns
        ]

        if bool(dup.any()):
            for _, g in work.loc[dup].groupby(
                "open_time",
                sort=False,
            ):
                for c in check_cols:
                    vals = pd.to_numeric(
                        g[c],
                        errors="coerce",
                    ).dropna()

                    if len(vals) <= 1:
                        continue

                    base = float(
                        vals.iloc[0]
                    )

                    if bool(
                        (
                            (vals.astype(float) - base)
                            .abs()
                            > 1e-12
                        ).any()
                    ):
                        logger.error(
                            f"[COLLECTOR][OPEN_TIME_REPAIR_CONFLICT] "
                            f"{symbol} {interval} "
                            f"open_time={g['open_time'].iloc[0]} "
                            f"column={c}"
                        )
                        return 0

        payload_cols = [
            c for c in work.columns
            if c != "open_time"
        ]

        work["_repair_complete"] = (
            work[payload_cols]
            .notna()
            .sum(axis=1)
        )

        if "is_closed" in work.columns:
            work["_repair_closed"] = (
                work["is_closed"]
                .map(
                    lambda x:
                    1 if x is True else 0
                )
                .fillna(0)
                .astype(int)
            )
        else:
            work["_repair_closed"] = 0

        work["_repair_order"] = range(
            len(work)
        )

        before_rows = len(work)

        work = (
            work.sort_values(
                [
                    "open_time",
                    "_repair_complete",
                    "_repair_closed",
                    "_repair_order",
                ],
                kind="mergesort",
            )
            .drop_duplicates(
                "open_time",
                keep="last",
            )
            .sort_values(
                "open_time",
                kind="mergesort",
            )
            .drop(
                columns=[
                    "_repair_complete",
                    "_repair_closed",
                    "_repair_order",
                ],
            )
            .reset_index(
                drop=True,
            )
        )

        removed = (
            before_rows
            - len(work)
        )

        tmp = f"{path}.{os.getpid()}.tmp"

        try:
            work.to_parquet(
                tmp,
                index=False,
            )
            os.replace(
                tmp,
                path,
            )
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

            logger.error(
                f"[COLLECTOR][OPEN_TIME_REPAIR_WRITE_FAIL] "
                f"{symbol} {interval} {e}",
                exc_info=True,
            )
            return 0

        logger.info(
            f"[COLLECTOR][OPEN_TIME_CANONICAL_REPAIR] "
            f"{symbol} {interval} "
            f"repaired={repair_count} "
            f"dedup_removed={removed} "
            f"rows={len(work)}"
        )

        return repair_count


def ensure_high_tf_seed(symbol):
    for interval in HIGH_TF_INTERVALS:
        if has_valid_high_tf_parquet(symbol, interval):
            continue

        logger.info(f"[주문][COLLECTOR][SEED_MISSING] {symbol} {interval}")
        try:
            rest_sync(symbol, interval, HIGH_TF_LIMIT)

            if has_valid_high_tf_parquet(symbol, interval):
                logger.info(f"[주문][COLLECTOR][SEED_DONE] {symbol} {interval}")
            else:
                logger.warning(f"[주문][COLLECTOR][SEED_STILL_MISSING] {symbol} {interval}")

        except Exception as e:
            logger.error(f"[주문][COLLECTOR][SEED_FAIL] {symbol} {interval} {e}", exc_info=True)

        time.sleep(HIGH_TF_SYNC_GAP_SEC)

def ensure_high_tf_seed_all(force=False):
    global last_high_tf_check_ts

    now = now_ts()
    if (not force) and (now - last_high_tf_check_ts < HIGH_TF_CHECK_SEC):
        return

    last_high_tf_check_ts = now

    try:
        for symbol in sorted(active_symbols):
            ensure_high_tf_seed(symbol)
    except Exception as e:
        logger.error(f"[주문][COLLECTOR][SEED_ALL_FAIL] {e}", exc_info=True)

def refresh_rest_only_intervals(force=False):
    # COLLECTOR_HIGH_TF_REST_ONLY_20260523
    # 1w / 1M 데이터는 어시데몬에서 쓰므로 REST로 계속 갱신한다.
    try:
        syms = sorted(active_symbols)
        if not syms:
            return

        now = now_ts()

        for interval in REST_ONLY_INTERVALS:
            refresh_sec = float(REST_ONLY_REFRESH_SEC.get(interval, HIGH_TF_CHECK_SEC) or HIGH_TF_CHECK_SEC)
            last_ts = float(last_rest_only_refresh_ts.get(interval, 0.0) or 0.0)

            if (not force) and (now - last_ts < refresh_sec):
                continue

            last_rest_only_refresh_ts[interval] = now

            logger.info(
                f"[주문][COLLECTOR][REST_ONLY_REFRESH_START] "
                f"interval={interval} symbols={len(syms)} refresh_sec={refresh_sec}"
            )

            for symbol in syms:
                try:
                    if not has_valid_high_tf_parquet(symbol, interval):
                        rest_sync(symbol, interval, HIGH_TF_LIMIT)
                    else:
                        rest_backfill(symbol, interval, limit=5)

                    time.sleep(REST_START_GAP_SEC)

                except Exception as e:
                    logger.error(
                        f"[주문][COLLECTOR][REST_ONLY_REFRESH_FAIL] {symbol} {interval} {e}",
                        exc_info=True
                    )

            logger.info(
                f"[주문][COLLECTOR][REST_ONLY_REFRESH_DONE] "
                f"interval={interval} symbols={len(syms)}"
            )

    except Exception as e:
        logger.error(f"[주문][COLLECTOR][REST_ONLY_REFRESH_ALL_FAIL] {e}", exc_info=True)


def rest_only_refresh_worker():
    while True:
        try:
            refresh_rest_only_intervals(force=False)
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][REST_ONLY_WORKER_FAIL] {e}", exc_info=True)
        time.sleep(30)



def get_rss_mb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return round(int(parts[1]) / 1024.0, 2)
    except Exception:
        pass
    return 0.0



def get_closed_queue_metrics():
    with closed_queue_lock:
        pair_cnt = len(closed_queue)
        row_cnt = 0
        max_pair = "-"
        max_rows = 0

        for (symbol, interval), rows in closed_queue.items():
            cnt = len(rows)
            row_cnt += cnt
            if cnt > max_rows:
                max_rows = cnt
                max_pair = f"{symbol}:{interval}"

    return pair_cnt, row_cnt, max_pair, max_rows


def get_ws_metrics():
    with ws_lock:
        thread_items = list(ws_threads.values())
        app_cnt = len(ws_apps)
        stop_events = list(ws_stop_flags.values())

    thread_cnt = len(thread_items)
    alive_cnt = sum(1 for t in thread_items if t and t.is_alive())
    stop_cnt = sum(1 for ev in stop_events if ev and ev.is_set())

    return thread_cnt, alive_cnt, app_cnt, stop_cnt


def log_mem_status(force=False):
    global last_mem_log_ts

    now = now_ts()
    if (not force) and (now - last_mem_log_ts < MEM_LOG_SEC):
        return

    last_mem_log_ts = now

    rss_mb = get_rss_mb()
    pair_cnt, row_cnt, max_pair, max_rows = get_closed_queue_metrics()
    thread_cnt, alive_cnt, app_cnt, stop_cnt = get_ws_metrics()
    active_cnt = len(active_symbols)
    py_threads = len(threading.enumerate())

    tag = "[주문][COLLECTOR][MEM]"
    if rss_mb >= MEM_WARN_RSS_MB or row_cnt >= MEM_WARN_QUEUE_ROWS:
        tag = "[주문][COLLECTOR][MEM_WARN]"

    logger.info(
        f"{tag} "
        f"rss_mb={rss_mb:.2f} "
        f"active_symbols={active_cnt} "
        f"ws_intervals={','.join(WS_INTERVALS)} "
        f"rest_only_intervals={','.join(REST_ONLY_INTERVALS)} "
        f"ws_threads={thread_cnt} "
        f"ws_alive={alive_cnt} "
        f"ws_apps={app_cnt} "
        f"ws_stop_flags={stop_cnt} "
        f"queue_pairs={pair_cnt} "
        f"queue_rows={row_cnt} "
        f"queue_max={max_pair}:{max_rows} "
        f"py_threads={py_threads}"
    )



# ============================================================
# EMA STATE CACHE
# ============================================================

def _ema_float(v, default=0.0):
    try:
        if v is None or v == "":
            return float(default)
        x = float(v)
        if x != x:
            return float(default)
        return x
    except Exception:
        return float(default)


def _ema_series(close, span):
    return close.ewm(span=int(span), adjust=False).mean()


def _ema_band_pct_from_df(df):
    """
    심볼별 변동성 기반 EMA 터치 허용 밴드.
    고정값 대신 최근 12시간봉 변동성으로 자동 산출.
    """
    try:
        if df is None or len(df) < 30:
            return 0.25

        d = df.tail(24).copy()
        high = d["high"].astype(float)
        low = d["low"].astype(float)
        close = d["close"].astype(float)

        rng_pct = ((high - low).abs() / close.replace(0, 1e-12) * 100.0).dropna()
        if rng_pct.empty:
            return 0.25

        avg = float(rng_pct.mean())

        # 너무 좁거나 너무 넓지 않게 제한
        band = avg * 0.35
        band = max(0.12, min(0.60, band))
        return round(float(band), 4)

    except Exception:
        return 0.25


def _ema_state_label(e13, e26, e52, e104):
    try:
        if e13 > e26 > e52 > e104:
            return "LONG_FULL"
        if e13 < e26 < e52 < e104:
            return "SHORT_FULL"
        return "TRANSITION"
    except Exception:
        return "UNKNOWN"


def _ema_cross_state(prev13, prev26, cur13, cur26):
    try:
        if prev13 <= prev26 and cur13 > cur26:
            return "GOLDEN_CROSS"
        if prev13 >= prev26 and cur13 < cur26:
            return "DEAD_CROSS"
        if cur13 > cur26:
            return "GOLDEN_HOLD"
        if cur13 < cur26:
            return "DEAD_HOLD"
        return "FLAT"
    except Exception:
        return "UNKNOWN"


def _ema_position_lists(price, ema_map):
    above = []
    below = []

    try:
        p = float(price)
        for name, val in ema_map.items():
            ev = _ema_float(val, 0.0)
            if ev <= 0:
                continue
            if p >= ev:
                below.append(name)
            else:
                above.append(name)
    except Exception:
        pass

    return above, below


def _nearest_ema_refs(price, ema_map):
    out = {
        "nearest_ema": "",
        "nearest_ema_value": 0.0,
        "nearest_ema_dist_pct": 999.0,
        "nearest_above_ema": "",
        "nearest_above_value": 0.0,
        "nearest_above_dist_pct": 999.0,
        "nearest_below_ema": "",
        "nearest_below_value": 0.0,
        "nearest_below_dist_pct": 999.0,
    }

    try:
        p = float(price)
        if p <= 0:
            return out

        all_cands = []
        above = []
        below = []

        for name, val in ema_map.items():
            ev = _ema_float(val, 0.0)
            if ev <= 0:
                continue

            dist = abs(p - ev) / ev * 100.0
            row = (float(dist), str(name), float(ev))
            all_cands.append(row)

            if ev >= p:
                above.append(row)
            else:
                below.append(row)

        if all_cands:
            dist, name, ev = sorted(all_cands, key=lambda x: x[0])[0]
            out["nearest_ema"] = name
            out["nearest_ema_value"] = round(ev, 10)
            out["nearest_ema_dist_pct"] = round(dist, 6)

        if above:
            dist, name, ev = sorted(above, key=lambda x: x[0])[0]
            out["nearest_above_ema"] = name
            out["nearest_above_value"] = round(ev, 10)
            out["nearest_above_dist_pct"] = round(dist, 6)

        if below:
            dist, name, ev = sorted(below, key=lambda x: x[0])[0]
            out["nearest_below_ema"] = name
            out["nearest_below_value"] = round(ev, 10)
            out["nearest_below_dist_pct"] = round(dist, 6)

        return out

    except Exception:
        return out


def build_symbol_ema_state(symbol):
    sym = str(symbol or "").strip().upper()

    out = {
        "symbol": sym,
        "ok": 0,
        "reason": "INIT",
        "updated_ts": int(time.time()),
    }

    try:
        path = get_parquet_path(sym, "12h")
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            out["reason"] = "NO_12H_PARQUET"
            return out

        df = pd.read_parquet(path)
        if df is None or df.empty:
            out["reason"] = "EMPTY_12H_PARQUET"
            return out

        if "close" not in df.columns:
            out["reason"] = "NO_CLOSE_COL"
            return out

        need_cols = ("open_time", "open", "high", "low", "close", "volume")
        for c in need_cols:
            if c not in df.columns:
                out["reason"] = f"NO_{c.upper()}_COL"
                return out

        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        df = df.dropna(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        if len(df) < int(EMA_STATE_MIN_ROWS):
            out["reason"] = f"12H_ROWS_TOO_SHORT:{len(df)}"
            return out

        d = df.tail(180).copy()
        close = d["close"].astype(float).dropna()

        if len(close) < int(EMA_STATE_MIN_ROWS):
            out["reason"] = f"12H_CLOSE_TOO_SHORT:{len(close)}"
            return out

        e13 = _ema_series(close, 13)
        e26 = _ema_series(close, 26)
        e52 = _ema_series(close, 52)
        e104 = _ema_series(close, 104)

        price = _ema_float(close.iloc[-1], 0.0)
        if price <= 0:
            out["reason"] = "BAD_PRICE"
            return out

        cur13 = _ema_float(e13.iloc[-1], 0.0)
        cur26 = _ema_float(e26.iloc[-1], 0.0)
        cur52 = _ema_float(e52.iloc[-1], 0.0)
        cur104 = _ema_float(e104.iloc[-1], 0.0)

        prev13 = _ema_float(e13.iloc[-2], 0.0)
        prev26 = _ema_float(e26.iloc[-2], 0.0)

        if min(cur13, cur26, cur52, cur104, prev13, prev26) <= 0:
            out["reason"] = "BAD_EMA_VALUE"
            return out

        ema_map = {
            "EMA13": cur13,
            "EMA26": cur26,
            "EMA52": cur52,
            "EMA104": cur104,
        }

        above_emas, below_emas = _ema_position_lists(price, ema_map)
        nearest = _nearest_ema_refs(price, ema_map)

        band_pct = _ema_band_pct_from_df(d)
        state = _ema_state_label(cur13, cur26, cur52, cur104)
        cross = _ema_cross_state(prev13, prev26, cur13, cur26)

        last_ot = ""
        try:
            last_ot = d["open_time"].iloc[-1].isoformat()
        except Exception:
            last_ot = ""

        out.update({
            "ok": 1,
            "reason": "OK",
            "updated_ts": int(time.time()),
            "interval": "12h",
            "rows": int(len(df)),
            "used_rows": int(len(d)),
            "last_open_time": last_ot,
            "price": round(float(price), 10),

            "ema13": round(float(cur13), 10),
            "ema26": round(float(cur26), 10),
            "ema52": round(float(cur52), 10),
            "ema104": round(float(cur104), 10),

            "prev_ema13": round(float(prev13), 10),
            "prev_ema26": round(float(prev26), 10),

            "ema_state": state,
            "cross_state": cross,
            "above_emas": above_emas,
            "below_emas": below_emas,
            "ema_band_pct": float(band_pct),

            **nearest,
        })

        return out

    except Exception as e:
        out["reason"] = f"EMA_STATE_ERROR:{str(e)[:120]}"
        return out


def refresh_ema_state_cache(force=False):
    global ema_state_cache, last_ema_state_log_ts

    try:
        syms = sorted(active_symbols)
        if not syms:
            return

        now = now_ts()
        new_symbols = {}
        ok_count = 0
        fail_count = 0

        for sym in syms:
            row = build_symbol_ema_state(sym)
            new_symbols[sym] = row
            if int(row.get("ok", 0) or 0) == 1:
                ok_count += 1
            else:
                fail_count += 1

        payload = {
            "schema": "COLLECTOR_EMA_STATE_V1",
            "version": "COLLECTOR_EMA_STATE_CACHE_20260613",
            "updated_ts": int(now),
            "symbol_count": int(len(syms)),
            "ok_count": int(ok_count),
            "fail_count": int(fail_count),
            "symbols": new_symbols,
        }

        with ema_state_lock:
            ema_state_cache = payload

        safe_json_dump(EMA_STATE_FILE, payload)

        if force or now - int(last_ema_state_log_ts or 0) >= int(EMA_STATE_LOG_SEC):
            last_ema_state_log_ts = now
            logger.info(
                f"[주문][COLLECTOR][EMA_STATE] "
                f"symbols={len(syms)} ok={ok_count} fail={fail_count} "
                f"path={EMA_STATE_FILE}"
            )

    except Exception as e:
        logger.error(f"[주문][COLLECTOR][EMA_STATE_FAIL] {e}", exc_info=True)


def ema_state_worker():
    while True:
        try:
            refresh_ema_state_cache(force=False)
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][EMA_STATE_WORKER_FAIL] {e}", exc_info=True)
        time.sleep(EMA_STATE_REFRESH_SEC)

def mem_monitor_worker():
    while True:
        try:
            log_mem_status()
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][MEM_MONITOR_FAIL] {e}", exc_info=True)
        time.sleep(MEM_LOG_SEC)

# ================= WS =================

def on_message(ws, message):
    try:
        msg = json.loads(message)
        if msg.get("e") != "kline":
            return

        k = msg.get("k")
        if not k:
            return

        symbol = msg.get("s")
        interval = k.get("i")
        if interval not in WS_INTERVALS:
            return

        ws_last_msg_ts[(symbol, interval)] = now_ts()

        live_row = {
            "open_time": utc_dt_from_ms(k["t"]).isoformat(),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "close_time": int(k["T"]),
            "quote_volume": float(k["q"]),
            "trade_count": int(k["n"]),
            "taker_buy_base_volume": float(k["V"]),
            "taker_buy_quote_volume": float(k["Q"]),
            "is_closed": bool(k.get("x")),
        }
        update_live_kline(symbol, interval, live_row)

        # WebSocket Parquet은 확정봉만 큐에 넣는다.
        # 진행봉은 state/live JSON에서 제공해 Parquet read/write 폭증을 막는다.
        if bool(k.get("x")):
            queue_closed_row(symbol, interval, {
                "open_time": utc_dt_from_ms(k["t"]),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "close_time": int(k["T"]),
                "quote_volume": float(k["q"]),
                "trade_count": int(k["n"]),
                "taker_buy_base_volume": float(k["V"]),
                "taker_buy_quote_volume": float(k["Q"]),
                "is_closed": True,
            })

    except Exception as e:
        logger.error(f"[주문][COLLECTOR][WS_ERROR] {e}", exc_info=True)

def on_error(ws, error):
    try:
        symbol = getattr(ws, "_symbol", "?")
        interval = getattr(ws, "_interval", "?")
        log_dedupe(
            "error",
            f"WS_ON_ERROR:{symbol}:{interval}:{str(error)[:120]}",
            f"[주문][COLLECTOR][WS_ON_ERROR] {symbol} {interval} {error}",
            ttl_sec=60,
        )
    except Exception:
        pass

def on_ping(ws, message):
    try:
        symbol = getattr(ws, "_symbol", None)
        interval = getattr(ws, "_interval", None)

        if (
            symbol
            and interval in WS_INTERVALS
        ):
            with ws_lock:
                ws_last_msg_ts[(symbol, interval)] = now_ts()

    except Exception as e:
        logger.error(
            f"[주문][COLLECTOR][WS_PING_FAIL] {e}",
            exc_info=True,
        )


def on_close(ws, code, msg):
    try:
        symbol = getattr(ws, "_symbol", "?")
        interval = getattr(ws, "_interval", "?")
        log_dedupe(
            "info",
            f"WS_CLOSE:{symbol}:{interval}:{code}",
            f"[주문][COLLECTOR][WS_CLOSE] {symbol} {interval} code={code} msg={msg}",
            ttl_sec=60,
        )
    except Exception:
        pass

def start_ws(symbol, interval):
    key = (symbol, interval)
    stream = f"{symbol.lower()}@kline_{interval}"
    url = f"{WS_BASE}/{stream}"

    with ws_lock:
        stop_event = ws_stop_flags.get(key)
        run_token = ws_run_tokens.get(key)

    if stop_event is None or run_token is None:
        return

    def _run():
        while True:
            if stop_event.is_set():
                logger.info(
                    f"[주문][COLLECTOR][WS_STOP] "
                    f"{symbol} {interval}"
                )
                break

            with ws_lock:
                if ws_run_tokens.get(key) != run_token:
                    logger.info(
                        f"[주문][COLLECTOR][WS_TOKEN_STOP] "
                        f"{symbol} {interval}"
                    )
                    break

            ws = None

            try:
                ws = WebSocketApp(
                    url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_ping=on_ping,
                )

                ws._symbol = symbol
                ws._interval = interval

                with ws_lock:
                    if (
                        stop_event.is_set()
                        or ws_run_tokens.get(key) != run_token
                    ):
                        try:
                            ws.close()
                        except Exception:
                            pass
                        break

                    now = now_ts()

                    ws_apps[key] = ws
                    ws_started_ts[key] = now
                    ws_last_msg_ts[key] = now

                logger.info(
                    f"[주문][COLLECTOR][WS_START] "
                    f"FUTURES {symbol} {interval}"
                )

                ws.run_forever(
                    ping_interval=WS_CLIENT_PING_INTERVAL,
                    ping_timeout=WS_CLIENT_PING_TIMEOUT,
                )

            except Exception as e:
                logger.error(
                    f"[주문][COLLECTOR][WS_RUN_FAIL] "
                    f"{symbol} {interval} {e}",
                    exc_info=True,
                )

            finally:
                with ws_lock:
                    if ws_apps.get(key) is ws:
                        ws_apps.pop(key, None)

            if stop_event.is_set():
                break

            with ws_lock:
                if ws_run_tokens.get(key) != run_token:
                    break

            for _ in range(30):
                if stop_event.is_set():
                    break

                with ws_lock:
                    if ws_run_tokens.get(key) != run_token:
                        break

                time.sleep(0.1)

        with ws_lock:
            cur_t = ws_threads.get(key)

            if cur_t is threading.current_thread():
                ws_threads.pop(key, None)

    t = threading.Thread(
        target=_run,
        daemon=True,
        name=f"ws_{symbol}_{interval}",
    )

    with ws_lock:
        ws_threads[key] = t

    t.start()

def spawn_ws(symbol, interval):
    key = (symbol, interval)

    with ws_lock:
        t = ws_threads.get(key)
        if t and t.is_alive():
            return

        ws_stop_flags[key] = threading.Event()
        ws_run_tokens[key] = ws_run_tokens.get(key, 0) + 1

    start_ws(symbol, interval)
    time.sleep(WS_START_GAP_SEC)

def stop_ws(symbol, interval):
    key = (symbol, interval)

    with ws_lock:
        stop_event = ws_stop_flags.get(key)
        ws = ws_apps.get(key)
        t = ws_threads.get(key)
        ws_run_tokens[key] = ws_run_tokens.get(key, 0) + 1

        if stop_event:
            stop_event.set()

    try:
        if ws:
            ws.close()
    except Exception:
        pass

    if t and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=5)

    with ws_lock:
        if ws_apps.get(key) is ws:
            ws_apps.pop(key, None)

        cur_t = ws_threads.get(key)
        if cur_t is t:
            ws_threads.pop(key, None)

        cur_ev = ws_stop_flags.get(key)
        if cur_ev is stop_event:
            ws_stop_flags.pop(key, None)

        ws_last_msg_ts.pop(key, None)
        ws_started_ts.pop(key, None)

def restart_all_ws():
    logger.info("[주문][COLLECTOR][WS_RESTART_ALL] hourly reconnect")

    with ws_lock:
        targets = list(ws_threads.keys())

    total = len(targets)
    if total == 0:
        return

    batch_size = max(1, int(WS_RESTART_BATCH_SIZE))

    for start in range(0, total, batch_size):
        batch = targets[start:start + batch_size]
        batch_no = (start // batch_size) + 1
        batch_total = (total + batch_size - 1) // batch_size

        logger.info(
            f"[주문][COLLECTOR][WS_RESTART_BATCH] "
            f"batch={batch_no}/{batch_total} size={len(batch)}"
        )

        for symbol, interval in batch:
            try:
                stop_ws(symbol, interval)
                rest_backfill(symbol, interval)
                logger.info(f"[주문][COLLECTOR][WS_RESTART] {symbol} {interval}")
                spawn_ws(symbol, interval)
            except Exception as e:
                logger.error(
                    f"[주문][COLLECTOR][WS_RESTART_FAIL] {symbol} {interval} {e}",
                    exc_info=True
                )

        if start + batch_size < total:
            time.sleep(WS_RESTART_BATCH_SLEEP_SEC)

def monitor_ws():
    while True:
        try:
            now = now_ts()

            with ws_lock:
                targets = list(ws_threads.keys())

            for symbol, interval in targets:
                key = (symbol, interval)

                with ws_lock:
                    stop_event = ws_stop_flags.get(key)
                    last_ts = ws_last_msg_ts.get(key, 0.0)

                if stop_event and stop_event.is_set():
                    continue

                if last_ts <= 0:
                    continue

                if now - last_ts > WS_STALE_SEC:
                    log_dedupe(
                        "info",
                        f"WS_STALE_RESTART:{symbol}:{interval}",
                        f"[주문][COLLECTOR][WS_STALE_RESTART] {symbol} {interval}",
                        ttl_sec=300,
                    )
                    stop_ws(symbol, interval)
                    rest_backfill(symbol, interval)
                    spawn_ws(symbol, interval)

        except Exception as e:
            logger.error(f"[주문][COLLECTOR][WS_MONITOR_FAIL] {e}", exc_info=True)

        time.sleep(WS_MONITOR_SEC)

# COLLECTOR_WS_REST_CONSISTENCY_V1

def ws_rest_consistency_worker():
    # WebSocket stays primary.
    # REST only repairs possible gaps periodically.
    while True:
        time.sleep(WS_REST_CONSISTENCY_SEC)

        try:
            symbols = sorted(list(active_symbols))
            started = now_ts()
            calls = 0

            logger.info(
                f"[??][COLLECTOR][WS_REST_CONSISTENCY_START] "
                f"symbols={len(symbols)} intervals={len(WS_INTERVALS)}"
            )

            for symbol in symbols:
                for interval in WS_INTERVALS:
                    repair_corrupt_open_time_file(symbol, interval)
                    rest_backfill(
                        symbol,
                        interval,
                        limit=WS_REST_CONSISTENCY_LIMIT,
                    )
                    repair_missing_liquidity_batch(symbol, interval)
                    calls += 1
                    time.sleep(REST_START_GAP_SEC)

            elapsed = round(
                now_ts() - started,
                2,
            )

            logger.info(
                f"[??][COLLECTOR][WS_REST_CONSISTENCY_DONE] "
                f"symbols={len(symbols)} calls={calls} elapsed={elapsed}s"
            )

        except Exception as e:
            logger.error(
                f"[??][COLLECTOR][WS_REST_CONSISTENCY_FAIL] {e}",
                exc_info=True,
            )

# ================= FLUSH =================

def flush_worker():
    while True:
        try:
            flush_closed_queue()
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][FLUSH_WORKER_FAIL] {e}", exc_info=True)
        time.sleep(FLUSH_INTERVAL_SEC)

def live_flush_worker():
    while True:
        try:
            flush_live_klines()
        except Exception as e:
            logger.error(f"[주문][COLLECTOR][LIVE_FLUSH_WORKER_FAIL] {e}", exc_info=True)
        time.sleep(LIVE_FLUSH_INTERVAL_SEC)


# ================= CONFIG RELOAD =================

def sync_symbol(symbol):
    # 사이클/진입 공통 원본 데이터: 1h, 6h, 12h, 1d + 상위봉 1w, 1M.
    # WebSocket 시작 전에 REST로 최근 확정/진행봉을 먼저 맞춘다.
    for interval in WS_INTERVALS:
        limit = int(INITIAL_SYNC_LIMITS.get(interval, 300))
        rest_sync(symbol, interval, limit)
        time.sleep(REST_START_GAP_SEC)

    ensure_high_tf_seed(symbol)

    for interval in WS_INTERVALS:
        spawn_ws(symbol, interval)

def sync_symbol_safe(symbol):
    try:
        started = now_ts()
        sync_symbol(symbol)
        elapsed = round(now_ts() - started, 2)
        logger.info(f"[주문][COLLECTOR][SYNC_DONE] {symbol} elapsed={elapsed}s")
        return symbol, True, ""
    except Exception as e:
        logger.error(f"[주문][COLLECTOR][SYNC_FAIL] {symbol} {e}", exc_info=True)
        return symbol, False, str(e)

last_required_universe_mtime = 0.0


def reload_symbols(force=False):

    global active_symbols
    global last_cfg_mtime
    global last_cfg_check_ts
    global last_required_universe_mtime

    now = now_ts()

    if (
        not force
        and
        now - last_cfg_check_ts
        < CONFIG_RELOAD_SEC
    ):
        return

    last_cfg_check_ts = now

    try:

        cfg_mtime = os.path.getmtime(
            CONFIG_FILE
        )

        required_file = (
            collector_required_universe_file()
        )

        required_mtime = (
            os.path.getmtime(
                required_file
            )
            if os.path.exists(
                required_file
            )
            else 0.0
        )

        if (
            not force
            and cfg_mtime
            == last_cfg_mtime
            and required_mtime
            == last_required_universe_mtime
        ):
            return

        new_syms = load_symbols()

        add = (
            new_syms
            - active_symbols
        )

        remove = (
            active_symbols
            - new_syms
        )

        add_list = sorted(add)
        remove_list = sorted(remove)

        if add_list:

            logger.info(
                f"[주문][COLLECTOR][SYNC_BATCH_START] "
                f"count={len(add_list)} "
                f"workers={SYNC_WORKERS} "
                f"symbols={add_list}"
            )

            max_workers = min(
                SYNC_WORKERS,
                max(
                    1,
                    len(add_list),
                ),
            )

            with ThreadPoolExecutor(
                max_workers=max_workers
            ) as ex:

                fut_map = {
                    ex.submit(
                        sync_symbol_safe,
                        symbol,
                    ):
                    symbol
                    for symbol
                    in add_list
                }

                for fut in as_completed(
                    fut_map
                ):

                    symbol = fut_map[fut]

                    try:

                        _, ok, error = (
                            fut.result()
                        )

                        if not ok:

                            logger.error(
                                f"[주문][COLLECTOR][SYNC_RESULT_FAIL] "
                                f"{symbol} {error}"
                            )

                    except Exception as error:

                        logger.error(
                            f"[주문][COLLECTOR][SYNC_RESULT_ERROR] "
                            f"{symbol} {error}",
                            exc_info=True,
                        )

        for symbol in remove_list:

            for interval in WS_INTERVALS:

                stop_ws(
                    symbol,
                    interval,
                )

        active_symbols = new_syms

        last_cfg_mtime = (
            cfg_mtime
        )

        last_required_universe_mtime = (
            required_mtime
        )

        logger.info(
            f"[주문][COLLECTOR][SYMBOL_RELOAD] "
            f"add={add_list} "
            f"remove={remove_list}"
        )

    except Exception as error:

        logger.error(
            f"[주문][COLLECTOR][SYMBOL_RELOAD_FAIL] "
            f"{error}",
            exc_info=True,
        )

# ================= MAIN =================

if __name__ == "__main__":
    logger.info("[주문][COLLECTOR] START")

    active_symbols = load_symbols()
    last_cfg_mtime = os.path.getmtime(CONFIG_FILE)
    last_cfg_check_ts = 0.0
    last_high_tf_check_ts = 0.0

    sync_list = sorted(active_symbols)
    if sync_list:
        logger.info(
            f"[주문][COLLECTOR][BOOT_SYNC_BATCH_START] "
            f"count={len(sync_list)} workers={SYNC_WORKERS} symbols={sync_list}"
        )

        batch_started = now_ts()
        max_workers = min(SYNC_WORKERS, max(1, len(sync_list)))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {ex.submit(sync_symbol_safe, s): s for s in sync_list}

            for fut in as_completed(fut_map):
                symbol = fut_map[fut]
                try:
                    _, ok, err = fut.result()
                    if not ok:
                        logger.error(f"[주문][COLLECTOR][BOOT_SYNC_RESULT_FAIL] {symbol} {err}")
                except Exception as e:
                    logger.error(f"[주문][COLLECTOR][BOOT_SYNC_RESULT_ERROR] {symbol} {e}", exc_info=True)

        batch_elapsed = round(now_ts() - batch_started, 2)
        logger.info(
            f"[주문][COLLECTOR][BOOT_SYNC_BATCH_DONE] "
            f"count={len(sync_list)} workers={max_workers} elapsed={batch_elapsed}s"
        )

    ensure_high_tf_seed_all(force=True)
    refresh_rest_only_intervals(force=True)

    threading.Thread(target=flush_worker, daemon=True).start()
    threading.Thread(target=live_flush_worker, daemon=True).start()
    threading.Thread(target=monitor_ws, daemon=True).start()
    threading.Thread(target=mem_monitor_worker, daemon=True).start()
    threading.Thread(target=rest_only_refresh_worker, daemon=True).start()
    threading.Thread(
        target=ws_rest_consistency_worker,
        daemon=True,
    ).start()

    refresh_ema_state_cache(force=True)

    threading.Thread(target=ema_state_worker, daemon=True).start()

    log_mem_status(force=True)

    while True:
        reload_symbols()
        ensure_high_tf_seed_all()
        write_collector_heartbeat()
        time.sleep(1)
