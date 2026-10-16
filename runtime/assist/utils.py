#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일: /home/ec2-user/bot/assist/utils.py
# 기능:
# - 1H / 6H / 12H / 1D / 1W / 1M 봉 공통 로딩
# - parquet 확정봉과 state/live 진행봉 분리 제공
# - open_time 정렬 / 중복 제거 / 파일 mtime 캐시
# - assist / cycle / daemon 공용 데이터 로더

# ================= IMPORT =================

import os
import json
import logging

import pandas as pd


# ================= LOG =================

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ================= CONFIG =================

BASE_DIR = os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LIVE_DIR = os.path.join(BASE_DIR, "state", "live")

SUPPORTED_INTERVALS = ("1h", "6h", "12h", "1d", "1w", "1M")
LIVE_INTERVALS = ("1h", "6h", "12h", "1d")

EMPTY_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "is_closed",
]


# ================= CACHE =================

_df_cache = {}
_live_cache = {}
_live_mtime = {}
_cfg_cache = {"mtime": None, "data": None}
_assist_load_seen = set()


# ================= UTIL =================

def _norm_symbol(symbol):
    return str(symbol or "").upper().strip()


def _norm_interval(interval):
    value = str(interval or "").strip()
    if value not in SUPPORTED_INTERVALS:
        return ""
    return value


def _parquet_path(symbol, interval):
    symbol = _norm_symbol(symbol)
    interval = _norm_interval(interval)
    if not symbol or not interval:
        return ""
    return f"{BASE_DIR}/backtest_data/{symbol}/{interval}.parquet"


def _live_path(symbol):
    symbol = _norm_symbol(symbol)
    return os.path.join(LIVE_DIR, f"{symbol}.json")


def _empty_df():
    return pd.DataFrame(columns=EMPTY_COLS)


def _normalize_df(df):
    if df is None or df.empty:
        return _empty_df()

    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    if "open_time" not in df.columns:
        return _empty_df()

    try:
        df["open_time"] = pd.to_datetime(
            df["open_time"],
            utc=True,
            errors="coerce",
        )
    except Exception:
        return _empty_df()

    df = df.dropna(subset=["open_time"])

    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = 0.0

    if "is_closed" not in df.columns:
        df["is_closed"] = True

    df["is_closed"] = df["is_closed"].fillna(False).astype(bool)

    df = (
        df.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
    return df


def _load_parquet_cached(symbol, interval):
    symbol = _norm_symbol(symbol)
    interval = _norm_interval(interval)
    path = _parquet_path(symbol, interval)

    if not path or not os.path.exists(path):
        return _empty_df()

    try:
        mtime = os.path.getmtime(path)
        key = (symbol, interval)

        hit = _df_cache.get(key)
        if hit and hit["mtime"] == mtime:
            return hit["df"].copy()

        df = pd.read_parquet(path)
        df = _normalize_df(df)

        _df_cache[key] = {
            "mtime": mtime,
            "df": df,
        }
        return df.copy()

    except Exception as e:
        logger.error(
            f"[주문][ASSIST][UTILS][PARQUET_LOAD_FAIL] "
            f"{symbol} {interval} {e}"
        )
        return _empty_df()


def _load_live_symbol(symbol):
    symbol = _norm_symbol(symbol)
    path = _live_path(symbol)

    if not os.path.exists(path):
        return {}

    try:
        mtime = os.path.getmtime(path)

        hit = _live_cache.get(symbol)
        if hit is not None and _live_mtime.get(symbol) == mtime:
            return hit

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            data = {}

        _live_cache[symbol] = data
        _live_mtime[symbol] = mtime
        return data

    except Exception as e:
        logger.error(
            f"[주문][ASSIST][UTILS][LIVE_LOAD_FAIL] "
            f"{symbol} {e}"
        )
        return {}


def _overlay_live_row(df, symbol, interval):
    symbol = _norm_symbol(symbol)
    interval = _norm_interval(interval)

    if interval not in LIVE_INTERVALS:
        return df if df is not None else _empty_df()

    live = _load_live_symbol(symbol)
    row = live.get(interval)

    if not isinstance(row, dict) or not row:
        return df if df is not None else _empty_df()

    try:
        live_row = pd.DataFrame([{
            "open_time": pd.to_datetime(
                row["open_time"],
                utc=True,
                errors="raise",
            ),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "is_closed": bool(row.get("is_closed", False)),
        }])
    except Exception:
        return df if df is not None else _empty_df()

    if df is None or df.empty:
        return _normalize_df(live_row)

    merged = pd.concat([df, live_row], ignore_index=True)
    return _normalize_df(merged)


def _confirmed_only(df):
    if df is None or df.empty:
        return _empty_df()

    d = _normalize_df(df)
    if d.empty:
        return d

    if "is_closed" not in d.columns:
        return d

    return d[d["is_closed"] == True].reset_index(drop=True)


def _log_last_row(df, symbol, tag):
    global _assist_load_seen

    try:
        if df is None or df.empty:
            return

        row = df.iloc[-1]
        ot = pd.to_datetime(
            row["open_time"],
            utc=True,
        ).tz_convert("Asia/Seoul")

        key = (
            str(symbol),
            str(tag),
            str(ot),
            bool(row.get("is_closed", False)),
        )

        if key in _assist_load_seen:
            return

        logger.info(
            f"[주문][ASSIST][LOAD][{tag}] "
            f"{symbol} OT_KST={ot} "
            f"CLOSE={row['close']} "
            f"CLOSED={bool(row.get('is_closed', False))}"
        )

        _assist_load_seen.add(key)

        if len(_assist_load_seen) > 5000:
            _assist_load_seen = set(list(_assist_load_seen)[-2000:])

    except Exception:
        pass


# ================= COMMON LOAD =================

def load_interval_df(
    symbol,
    interval,
    min_rows=2,
    include_live=True,
    confirmed_only=False,
    log_tag=None,
):
    symbol = _norm_symbol(symbol)
    interval = _norm_interval(interval)

    if not symbol or not interval:
        return None

    df = _load_parquet_cached(symbol, interval)

    if include_live:
        df = _overlay_live_row(df, symbol, interval)

    if confirmed_only:
        df = _confirmed_only(df)

    if df is None or df.empty or len(df) < int(min_rows):
        return None

    if log_tag:
        _log_last_row(df, symbol, log_tag)

    return df


def load_confirmed_interval_df(symbol, interval, min_rows=2, log_tag=None):
    return load_interval_df(
        symbol=symbol,
        interval=interval,
        min_rows=min_rows,
        include_live=False,
        confirmed_only=True,
        log_tag=log_tag,
    )


def load_live_interval_df(symbol, interval, min_rows=2, log_tag=None):
    return load_interval_df(
        symbol=symbol,
        interval=interval,
        min_rows=min_rows,
        include_live=True,
        confirmed_only=False,
        log_tag=log_tag,
    )


def get_last_confirmed_row(symbol, interval):
    df = load_confirmed_interval_df(
        symbol,
        interval,
        min_rows=1,
    )
    if df is None or df.empty:
        return None
    return df.iloc[-1].copy()


def get_current_row(symbol, interval):
    df = load_live_interval_df(
        symbol,
        interval,
        min_rows=1,
    )
    if df is None or df.empty:
        return None
    return df.iloc[-1].copy()


# ================= TIMEFRAME LOADERS =================

def load_hourly_df(symbol="BTCUSDT", min_rows=2, confirmed_only=False):
    if confirmed_only:
        return load_confirmed_interval_df(
            symbol,
            "1h",
            min_rows=min_rows,
            log_tag="1H_CONFIRMED",
        )

    return load_live_interval_df(
        symbol,
        "1h",
        min_rows=min_rows,
        log_tag="1H_LIVE",
    )


def load_6h_df(symbol="BTCUSDT", min_rows=2, confirmed_only=False):
    if confirmed_only:
        return load_confirmed_interval_df(
            symbol,
            "6h",
            min_rows=min_rows,
            log_tag="6H_CONFIRMED",
        )

    return load_live_interval_df(
        symbol,
        "6h",
        min_rows=min_rows,
        log_tag="6H_LIVE",
    )


def load_12h_df(symbol="BTCUSDT", min_rows=2, confirmed_only=False):
    if confirmed_only:
        return load_confirmed_interval_df(
            symbol,
            "12h",
            min_rows=min_rows,
            log_tag="12H_CONFIRMED",
        )

    return load_live_interval_df(
        symbol,
        "12h",
        min_rows=min_rows,
        log_tag="12H_LIVE",
    )


def load_daily_df(symbol="BTCUSDT", min_rows=2, confirmed_only=False):
    if confirmed_only:
        return load_confirmed_interval_df(
            symbol,
            "1d",
            min_rows=min_rows,
            log_tag="1D_CONFIRMED",
        )

    return load_live_interval_df(
        symbol,
        "1d",
        min_rows=min_rows,
        log_tag="1D_LIVE",
    )


def load_weekly_df(symbol="BTCUSDT", min_rows=2):
    return load_confirmed_interval_df(
        symbol,
        "1w",
        min_rows=min_rows,
        log_tag="1W_CONFIRMED",
    )


def load_monthly_df(symbol="BTCUSDT", min_rows=2):
    return load_confirmed_interval_df(
        symbol,
        "1M",
        min_rows=min_rows,
        log_tag="1M_CONFIRMED",
    )


# ================= COMPATIBILITY =================

def load_hourly_df_symbol(symbol, min_rows=2):
    return load_hourly_df(
        symbol=symbol,
        min_rows=min_rows,
        confirmed_only=False,
    )


# ================= CONFIG LOAD =================

def load_assist_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            return {}

        mtime = os.path.getmtime(CONFIG_FILE)

        if (
            _cfg_cache["mtime"] == mtime
            and _cfg_cache["data"] is not None
        ):
            return dict(_cfg_cache["data"])

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            data = {}

        _cfg_cache["mtime"] = mtime
        _cfg_cache["data"] = data
        return dict(data)

    except Exception as e:
        logger.error(
            f"[주문][ASSIST][UTILS][CONFIG_LOAD_FAIL] {e}"
        )
        return {}


# ================= VALIDATION =================

def validate_last_candles(df, tf_sec, tag="TF"):
    try:
        if df is None or len(df) < 2:
            return False

        d = _normalize_df(df)
        if len(d) < 2:
            return False

        t1 = pd.to_datetime(
            d["open_time"].iloc[-1],
            utc=True,
        )
        t2 = pd.to_datetime(
            d["open_time"].iloc[-2],
            utc=True,
        )

        if t1 <= t2:
            logger.error(
                f"[주문][ASSIST][{tag}][INVALID][TIME] "
                f"-1={t1} -2={t2}"
            )
            return False

        diff = (t1 - t2).total_seconds()
        tol = 5 if tf_sec < 3600 else 120

        if abs(diff - tf_sec) > tol:
            logger.error(
                f"[주문][ASSIST][{tag}][INVALID][GAP] "
                f"diff={diff}s tf={tf_sec}s tol={tol}s"
            )
            return False

        return True

    except Exception as e:
        logger.error(
            f"[주문][ASSIST][{tag}][INVALID][EXC] {e}",
            exc_info=True,
        )
        return False