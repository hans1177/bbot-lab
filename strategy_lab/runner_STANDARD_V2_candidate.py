#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RUNNER - TF STRATEGY OWNER EXECUTOR
STANDARD V2 CANDIDATE - DYNAMIC TOP-N / TRAILING 3-1.5 / NEXT-1H OPPOSITE REOPEN

원칙
------------------------------------------------------------
1. 전략 판단은 gate.py만 담당한다.
2. Runner는 전략 계산을 하지 않는다.
3. 신규 포지션:
      현재 활성 strategy_config 후보 사용
4. 기존 포지션:
      진입 당시 entry_tf + entry_strategy만 사용
5. 동일 방향:
      HOLD
6. 전략 합의 없음:
      HOLD
7. 반대 방향:
      CLOSE
      -> 실제 Binance flat 확인
      -> REVERSE
8. Cycle / NORMAL / REVERSAL / EMA_DIRECT 없음
9. 일반 SL / TP 판단 없음
10. Binance 실제 포지션이 최종 진실값
"""

import os
import time
import json
import uuid
import signal
import sqlite3
import logging
import fcntl
import tempfile

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from order import Order
import gate
import risk_engine


# ============================================================
# CONFIG
# ============================================================

BASE = os.getenv(
    "BOT_BASE_DIR",
    "/home/ec2-user/bot",
)

STATE_DIR = f"{BASE}/state"
CONFIG_FILE = f"{BASE}/config.json"
STRATEGY_CONFIG_FILE = f"{BASE}/strategy_config.json"
DEFAULT_UNIVERSE_TOP_N = 30

POS_CACHE_FILE = (
    f"{STATE_DIR}/position_cache.json"
)

PENDING_FILE = (
    f"{STATE_DIR}/runner_entry_pending_meta.json"
)

SIGNAL_STATE_FILE = (
    f"{STATE_DIR}/runner_signal_dedupe.json"
)

TRAIL_STATE_FILE = (
    f"{STATE_DIR}/runner_trailing_state.json"
)

TRAIL_START_PCT = 3.0
TRAIL_CALLBACK_PCT = 1.5
TRAIL_STATE_RETENTION_SEC = 30 * 24 * 60 * 60

RISK_BRACKET_TTL_SEC = 600.0

CONTROL_SL_STATE_FILE = (
    f"{STATE_DIR}/control_sl_state.json"
)


_RISK_BRACKET_CACHE_TS = 0.0
_RISK_BRACKET_CACHE = {}

HISTORY_DB = f"{STATE_DIR}/history.db"
LOG_FILE = f"{BASE}/logs/runner.log"

LOCK_FILE = (
    "/tmp/bbot_runner_strategy_owner.lock"
)

POLICY = (
    "RUNNER_STANDARD_V2_DYNAMIC_TOPN_20260826"
)

LOOP_SLEEP_SEC = float(
    os.getenv(
        "RUNNER_LOOP_SLEEP_SEC",
        "10",
    )
    or 10
)

RUN_ONCE = (
    str(
        os.getenv(
            "RUNNER_ONCE",
            "0",
        )
    )
    .strip()
    .lower()
    in ("1", "true", "yes", "on")
)

DRY_RUN = (
    str(
        os.getenv(
            "RUNNER_DRY_RUN",
            "0",
        )
    )
    .strip()
    .lower()
    in ("1", "true", "yes", "on")
)



POSITION_VERIFY_RETRY = int(
    float(
        os.getenv(
            "RUNNER_POSITION_VERIFY_RETRY",
            "6",
        )
        or 6
    )
)

POSITION_VERIFY_SLEEP = float(
    os.getenv(
        "RUNNER_POSITION_VERIFY_SLEEP",
        "0.8",
    )
    or 0.8
)

PENDING_MAX_AGE_SEC = int(
    float(
        os.getenv(
            "RUNNER_PENDING_ENTRY_META_MAX_AGE_SEC",
            "3600",
        )
        or 3600
    )
)

LOG_HOLD_SEC = int(
    float(
        os.getenv(
            "RUNNER_LOG_HOLD_SEC",
            "60",
        )
        or 60
    )
)


RUNNING = True
ORDER = None
CONFIG = {}
CONFIG_MTIME = -1.0
POSITION_CACHE = {}
LOG_RL = {}


# ============================================================
# ENV / LOG
# ============================================================

def load_env():
    if not load_dotenv:
        return

    for path in (
        "/etc/bbot/runner.env",
        f"{BASE}/.env",
    ):
        try:
            if os.path.exists(path):
                load_dotenv(
                    path,
                    override=True,
                )
        except Exception:
            pass


load_env()

os.makedirs(
    STATE_DIR,
    exist_ok=True,
)

os.makedirs(
    os.path.dirname(LOG_FILE),
    exist_ok=True,
)

logger = logging.getLogger(
    "runner_strategy_owner"
)

logger.setLevel(
    logging.INFO
)

logger.propagate = False

fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
)

fh = logging.FileHandler(
    LOG_FILE,
    encoding="utf-8",
)

fh.setFormatter(fmt)

logger.handlers = [fh]


# ============================================================
# UTIL
# ============================================================

def now_ts():
    return int(time.time())


def s(value, default=""):
    try:
        value = str(
            value
            if value is not None
            else ""
        ).strip()

        return (
            value
            if value
            else str(default)
        )

    except Exception:
        return str(default)


def u(value, default=""):
    return s(
        value,
        default,
    ).upper()


def f(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def i(value, default=0):
    try:
        return int(
            float(value)
        )
    except Exception:
        return int(default)


def norm_side(value):
    value = u(value)

    if value in (
        "BUY",
        "LONG",
    ):
        return "BUY"

    if value in (
        "SELL",
        "SHORT",
    ):
        return "SELL"

    return ""


def opposite_side(side):
    side = norm_side(side)

    if side == "BUY":
        return "SELL"

    if side == "SELL":
        return "BUY"

    return ""


def position_side(side):
    side = norm_side(side)

    return (
        "LONG"
        if side == "BUY"
        else "SHORT"
    )


def side_from_amt(amount):
    amount = f(
        amount,
        0.0,
    )

    if amount > 0:
        return "BUY"

    if amount < 0:
        return "SELL"

    return ""


def json_load(
    path,
    default=None,
):
    if default is None:
        default = {}

    try:
        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as handle:

            data = json.load(handle)

        return data

    except Exception:
        return default


def atomic_write(
    path,
    payload,
):
    directory = os.path.dirname(path)

    os.makedirs(
        directory,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=directory,
        encoding="utf-8",
        prefix=".runner_tmp_",
    ) as handle:

        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

        handle.flush()
        os.fsync(
            handle.fileno()
        )

        tmp = handle.name

    os.replace(
        tmp,
        path,
    )


def log_rl(
    category,
    key,
    message,
    sec=LOG_HOLD_SEC,
):
    ident = (
        f"{category}|{key}"
    )

    now = time.time()

    last = float(
        LOG_RL.get(
            ident,
            0.0,
        )
        or 0.0
    )

    if (
        now - last
        < max(1, sec)
    ):
        return

    LOG_RL[ident] = now
    logger.info(message)


# ============================================================
# CONFIG
# ============================================================

def reload_config(
    force=False,
):
    global CONFIG
    global CONFIG_MTIME

    try:
        mtime = os.path.getmtime(
            CONFIG_FILE
        )

        if (
            not force
            and CONFIG
            and mtime == CONFIG_MTIME
        ):
            return CONFIG

        with open(
            CONFIG_FILE,
            "r",
            encoding="utf-8",
        ) as handle:

            data = json.load(handle)

        if not isinstance(
            data,
            dict,
        ):
            data = {}

        CONFIG = data
        CONFIG_MTIME = mtime

    except Exception:

        if not isinstance(
            CONFIG,
            dict,
        ):
            CONFIG = {}

    return CONFIG


def cfg_get(
    key,
    default=None,
):
    cfg = reload_config()

    return cfg.get(
        key,
        default,
    )


def _ordered_unique_symbols(values):
    result = []
    seen = set()

    for raw in values or []:
        symbol = u(raw)

        if (
            not symbol
            or symbol in seen
        ):
            continue

        seen.add(symbol)
        result.append(symbol)

    return result


def config_symbols():
    """
    config.json의 심볼 순서를 그대로 유지한다.

    V1은 set + sorted()로 순위를 알파벳순으로 잃었다.
    V2 Dynamic Top-N은 심볼 생성기가 기록한 순서가 곧 우선순위이므로
    중복만 제거하고 원래 순서는 보존한다.
    """
    reload_config()

    for key in (
        "SYMBOLS",
        "symbols",
        "TRADE_SYMBOLS",
        "trade_symbols",
        "TRADING_SYMBOLS",
        "trading_symbols",
        "TARGET_SYMBOLS",
        "target_symbols",
    ):
        value = cfg_get(
            key,
            None,
        )

        if isinstance(
            value,
            list,
        ):
            result = _ordered_unique_symbols(
                value
            )

            if result:
                return result

        if isinstance(
            value,
            str,
        ):
            result = _ordered_unique_symbols(
                value.replace(
                    ";",
                    ",",
                ).split(",")
            )

            if result:
                return result

        if isinstance(
            value,
            dict,
        ):
            result = []

            for symbol, enabled in value.items():
                if enabled is False:
                    continue

                if isinstance(
                    enabled,
                    dict,
                ):
                    if (
                        enabled.get(
                            "enabled",
                            True,
                        )
                        is False
                    ):
                        continue

                    if (
                        enabled.get(
                            "active",
                            True,
                        )
                        is False
                    ):
                        continue

                if isinstance(
                    enabled,
                    str,
                ) and enabled.strip().lower() in (
                    "0",
                    "false",
                    "off",
                    "no",
                ):
                    continue

                result.append(
                    symbol
                )

            result = _ordered_unique_symbols(
                result
            )

            if result:
                return result

    return []


def strategy_universe_top_n():
    """
    활성 전략의 신규진입 Universe 크기.

    우선순위:
      1) strategy_config.json -> universe_top_n
      2) strategy_config.json -> runtime.universe_top_n
      3) config.json -> UNIVERSE_TOP_N / universe_top_n
      4) STANDARD V2 기본값 30

    이후 V3가 25개를 쓰면 config 값만 25로 바꾸면 된다.
    """
    strategy_cfg = json_load(
        STRATEGY_CONFIG_FILE,
        {},
    )

    candidates = []

    if isinstance(
        strategy_cfg,
        dict,
    ):
        candidates.append(
            strategy_cfg.get(
                "universe_top_n"
            )
        )

        runtime_cfg = strategy_cfg.get(
            "runtime"
        )

        if isinstance(
            runtime_cfg,
            dict,
        ):
            candidates.append(
                runtime_cfg.get(
                    "universe_top_n"
                )
            )

    candidates.extend([
        cfg_get(
            "UNIVERSE_TOP_N",
            None,
        ),
        cfg_get(
            "universe_top_n",
            None,
        ),
        DEFAULT_UNIVERSE_TOP_N,
    ])

    for raw in candidates:
        top_n = i(
            raw,
            0,
        )

        if top_n > 0:
            return top_n

    return DEFAULT_UNIVERSE_TOP_N


def active_entry_symbols(
    ranked_symbols=None,
):
    ranked_symbols = (
        list(ranked_symbols)
        if ranked_symbols is not None
        else config_symbols()
    )

    ranked_symbols = (
        _ordered_unique_symbols(
            ranked_symbols
        )
    )

    top_n = strategy_universe_top_n()

    if (
        top_n <= 0
        or not ranked_symbols
    ):
        return []

    return ranked_symbols[
        :min(
            top_n,
            len(ranked_symbols),
        )
    ]


# ============================================================
# ORDER
# ============================================================

def get_order():
    global ORDER

    if ORDER is None:
        ORDER = Order()

    return ORDER


def safe_call(
    func,
    **kwargs,
):
    order = get_order()

    if hasattr(
        order,
        "safe_call",
    ):
        return order.safe_call(
            func,
            **kwargs,
        )

    return func(
        **kwargs
    )


# ============================================================
# BINANCE POSITIONS
# ============================================================

def fetch_positions_fresh():
    order = get_order()

    for name in (
        "get_position_risk",
        "futures_position_information",
        "position_information",
    ):

        fn = getattr(
            order.client,
            name,
            None,
        )

        if not fn:
            continue

        try:
            rows = safe_call(fn)

            if isinstance(
                rows,
                dict,
            ):
                rows = [rows]

            if isinstance(
                rows,
                list,
            ):
                return rows

        except Exception:
            continue

    return []


def fresh_position(
    symbol,
    side=None,
):
    symbol = u(symbol)
    side = norm_side(side)

    for row in fetch_positions_fresh():

        if not isinstance(
            row,
            dict,
        ):
            continue

        if u(
            row.get("symbol")
        ) != symbol:
            continue

        amount = f(
            row.get(
                "positionAmt"
            ),
            0.0,
        )

        if abs(amount) <= 0:
            continue

        live_side = side_from_amt(
            amount
        )

        if (
            side
            and live_side != side
        ):
            continue

        result = dict(row)

        result["symbol"] = symbol
        result["side"] = live_side

        entry_price = f(
            result.get(
                "entryPrice"
            ),
            0.0,
        )

        if entry_price > 0:
            result[
                "entry_price"
            ] = entry_price

        return result

    return None


def same_live_side(
    old_row,
    live_row,
):
    if not isinstance(
        old_row,
        dict,
    ):
        return False

    if not isinstance(
        live_row,
        dict,
    ):
        return False

    return (
        u(
            old_row.get("symbol")
        )
        ==
        u(
            live_row.get("symbol")
        )
        and
        side_from_amt(
            old_row.get(
                "positionAmt"
            )
        )
        ==
        side_from_amt(
            live_row.get(
                "positionAmt"
            )
        )
    )


# ============================================================
# OWNER META
# ============================================================

def owner_meta(src):
    src = (
        src
        if isinstance(
            src,
            dict,
        )
        else {}
    )

    return {
        "entry_id": s(
            src.get("entry_id")
        ),
        "entry_history_id": (
            src.get(
                "entry_history_id"
            )
        ),
        "entry_tf": u(
            src.get("entry_tf")
        ),
        "entry_strategy": u(
            src.get(
                "entry_strategy"
            )
        ),
        "signal_id": s(
            src.get("signal_id")
        ),
        "signal_candle_time": s(
            src.get(
                "signal_candle_time"
            )
            or src.get(
                "confirmed_open_time"
            )
        ),
        "entry_leverage": f(
            src.get(
                "entry_leverage"
            )
            or src.get(
                "leverage"
            ),
            0.0,
        ),
        "leverage": f(
            src.get(
                "leverage"
            )
            or src.get(
                "entry_leverage"
            ),
            0.0,
        ),
        "entry_ts": i(
            src.get(
                "entry_ts"
            )
            or src.get(
                "created_ts"
            ),
            0,
        ),
        "entry_reason": s(
            src.get(
                "entry_reason"
            )
            or src.get(
                "gate_reason"
            )
        ),
    }


def has_owner(row):
    if not isinstance(
        row,
        dict,
    ):
        return False

    return bool(
        u(
            row.get("entry_tf")
        )
        and
        u(
            row.get(
                "entry_strategy"
            )
        )
    )


# ============================================================
# PENDING
# ============================================================

def pending_state():
    data = json_load(
        PENDING_FILE,
        {},
    )

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else {}
    )


def pending_key(
    symbol,
    side,
):
    return (
        f"{u(symbol)}|"
        f"{norm_side(side)}"
    )


def get_pending(
    symbol,
    side,
):
    data = pending_state()

    row = data.get(
        pending_key(
            symbol,
            side,
        )
    )

    if not isinstance(
        row,
        dict,
    ):
        return {}

    created = i(
        row.get(
            "created_ts"
        ),
        0,
    )

    if (
        created <= 0
        or
        now_ts() - created
        > PENDING_MAX_AGE_SEC
    ):
        return {}

    return dict(row)


def save_pending(meta):
    data = pending_state()

    data[
        pending_key(
            meta.get("symbol"),
            meta.get("side"),
        )
    ] = dict(meta)

    atomic_write(
        PENDING_FILE,
        data,
    )


def clear_pending(
    symbol,
    side,
):
    data = pending_state()

    key = pending_key(
        symbol,
        side,
    )

    if key in data:

        data.pop(
            key,
            None,
        )

        atomic_write(
            PENDING_FILE,
            data,
        )

        logger.info(
            f"[주문][RUNNER][PENDING_CLEAR] "
            f"key={key} policy={POLICY}"
        )


def cleanup_expired_pending():
    data = pending_state()
    changed = False
    now = now_ts()

    for key, row in list(
        data.items()
    ):

        if not isinstance(
            row,
            dict,
        ):
            data.pop(
                key,
                None,
            )
            changed = True
            continue

        created = i(
            row.get(
                "created_ts"
            ),
            0,
        )

        if (
            created <= 0
            or now - created
            > PENDING_MAX_AGE_SEC
        ):
            data.pop(
                key,
                None,
            )
            changed = True

    if changed:
        atomic_write(
            PENDING_FILE,
            data,
        )


# ============================================================
# POSITION CACHE
# ============================================================

def refresh_positions(
    write_cache=True,
):
    global POSITION_CACHE

    fresh_rows = (
        fetch_positions_fresh()
    )

    old = json_load(
        POS_CACHE_FILE,
        {},
    )

    if not isinstance(
        old,
        dict,
    ):
        old = {}

    new_cache = {}

    for raw in fresh_rows:

        if not isinstance(
            raw,
            dict,
        ):
            continue

        symbol = u(
            raw.get("symbol")
        )

        amount = f(
            raw.get(
                "positionAmt"
            ),
            0.0,
        )

        side = side_from_amt(
            amount
        )

        if (
            not symbol
            or
            side not in (
                "BUY",
                "SELL",
            )
            or
            abs(amount) <= 0
        ):
            continue

        row = dict(raw)

        row["symbol"] = symbol
        row["side"] = side

        old_row = old.get(
            symbol
        )

        meta = {}

        if same_live_side(
            old_row,
            row,
        ):
            meta = owner_meta(
                old_row
            )

        if not has_owner(meta):

            pending = get_pending(
                symbol,
                side,
            )

            if pending:
                meta = owner_meta(
                    pending
                )

        for key, value in (
            meta.items()
        ):

            if value not in (
                None,
                "",
                0,
                0.0,
            ):
                row[key] = value

        entry_price = f(
            row.get(
                "entryPrice"
            ),
            0.0,
        )

        if entry_price > 0:
            row[
                "entry_price"
            ] = entry_price

        new_cache[
            symbol
        ] = row

    POSITION_CACHE = (
        new_cache
    )

    if write_cache:
        atomic_write(
            POS_CACHE_FILE,
            POSITION_CACHE,
        )

    return POSITION_CACHE


def open_position(symbol):
    symbol = u(symbol)

    row = POSITION_CACHE.get(
        symbol
    )

    if not isinstance(
        row,
        dict,
    ):
        return None

    if abs(
        f(
            row.get(
                "positionAmt"
            ),
            0.0,
        )
    ) <= 0:
        return None

    return row


def update_cache_entry(
    symbol,
    row,
):
    global POSITION_CACHE

    symbol = u(symbol)

    POSITION_CACHE[
        symbol
    ] = dict(row)

    atomic_write(
        POS_CACHE_FILE,
        POSITION_CACHE,
    )


# ============================================================
# PRICE
# ============================================================

def get_price(symbol):
    symbol = u(symbol)
    order = get_order()

    try:
        with order._lock:

            now = time.time()

            block_until = float(
                getattr(
                    order,
                    "_api_block_until",
                    0.0,
                )
                or 0.0
            )

            if block_until > now:
                return 0.0

            order._api_delay()

            result = (
                order.client.mark_price(
                    symbol=symbol
                )
            )

            result = (
                order._unwrap_limit_usage(
                    result,
                    f"mark_price:{symbol}",
                )
            )

        if isinstance(
            result,
            dict,
        ):

            return f(
                result.get(
                    "markPrice"
                ),
                0.0,
            )

    except Exception:
        logger.error(
            f"[주문][RUNNER][PRICE_FAIL] "
            f"{symbol}",
            exc_info=True,
        )

    return 0.0



# ============================================================
# CROSS SURVIVAL RISK
# ============================================================

def risk_wallet_balance():
    """
    Cross Risk 계산용 wallet.

    Binance totalWalletBalance:
    unrealized PnL 제외 wallet balance.
    Risk Engine이 자체적으로 uPnL을 다시 계산한다.
    """
    order = get_order()

    result = safe_call(
        order.client.account
    )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "RISK_ACCOUNT_INVALID"
        )

    wallet = f(
        result.get(
            "totalWalletBalance"
        ),
        -1.0,
    )

    if wallet < 0:
        raise RuntimeError(
            "RISK_WALLET_INVALID"
        )

    return float(
        wallet
    )


def risk_leverage_brackets(
    symbols,
):
    global _RISK_BRACKET_CACHE_TS
    global _RISK_BRACKET_CACHE

    symbols = {
        u(x)
        for x in (
            symbols
            or []
        )
        if u(x)
    }

    now = time.time()

    if (
        isinstance(
            _RISK_BRACKET_CACHE,
            dict,
        )
        and
        symbols.issubset(
            set(
                _RISK_BRACKET_CACHE
            )
        )
        and
        (
            now
            -
            float(
                _RISK_BRACKET_CACHE_TS
                or 0.0
            )
        )
        <= RISK_BRACKET_TTL_SEC
    ):
        return {
            symbol:
                _RISK_BRACKET_CACHE[
                    symbol
                ]
            for symbol in symbols
        }

    order = get_order()

    result = safe_call(
        order.client.leverage_brackets
    )

    rows = []

    if isinstance(
        result,
        list,
    ):
        rows.extend(
            result
        )

    elif isinstance(
        result,
        dict,
    ):
        if result.get(
            "symbol"
        ):
            rows.append(
                result
            )

        elif isinstance(
            result.get(
                "data"
            ),
            list,
        ):
            rows.extend(
                result.get(
                    "data"
                )
            )

    data = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        symbol = u(
            row.get(
                "symbol"
            )
        )

        if not symbol:
            continue

        data[
            symbol
        ] = row

    # 전체 조회가 일부만 반환하면 필요한 심볼만 보충.
    missing = (
        symbols
        -
        set(
            data
        )
    )

    for symbol in sorted(
        missing
    ):

        row = safe_call(
            order.client.leverage_brackets,
            symbol=symbol,
        )

        if isinstance(
            row,
            list,
        ):
            for item in row:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                item_symbol = u(
                    item.get(
                        "symbol"
                    )
                )

                if item_symbol:
                    data[
                        item_symbol
                    ] = item

        elif isinstance(
            row,
            dict,
        ):
            item_symbol = u(
                row.get(
                    "symbol"
                )
            )

            if item_symbol:
                data[
                    item_symbol
                ] = row

    missing = (
        symbols
        -
        set(
            data
        )
    )

    if missing:
        raise RuntimeError(
            "RISK_BRACKETS_MISSING:"
            +
            ",".join(
                sorted(
                    missing
                )
            )
        )

    _RISK_BRACKET_CACHE = dict(
        data
    )

    _RISK_BRACKET_CACHE_TS = (
        now
    )

    return {
        symbol:
            data[
                symbol
            ]
        for symbol in symbols
    }


def risk_live_position_book():
    """
    Binance 실제 포지션을 다시 읽고
    Risk Engine 형식으로 변환한다.
    """
    refresh_positions(
        write_cache=True,
    )

    out = {}

    for symbol, raw in (
        POSITION_CACHE.items()
    ):

        if not isinstance(
            raw,
            dict,
        ):
            continue

        amount = f(
            raw.get(
                "positionAmt"
            ),
            0.0,
        )

        if abs(
            amount
        ) <= 0:
            continue

        side = side_from_amt(
            amount
        )

        if side not in (
            "BUY",
            "SELL",
        ):
            continue

        margin_type = s(
            raw.get(
                "marginType"
            )
        ).lower()

        isolated = s(
            raw.get(
                "isolated"
            )
        ).lower()

        if (
            margin_type == "isolated"
            or
            isolated in (
                "true",
                "1",
                "yes",
            )
        ):
            raise RuntimeError(
                f"RISK_ISOLATED_UNSUPPORTED:"
                f"{symbol}"
            )

        entry_price = f(
            raw.get(
                "entryPrice"
            )
            or
            raw.get(
                "entry_price"
            ),
            0.0,
        )

        if entry_price <= 0:
            raise RuntimeError(
                f"RISK_ENTRY_PRICE_INVALID:"
                f"{symbol}"
            )

        row = dict(
            raw
        )

        row.update({
            "symbol":
                symbol,

            "side":
                (
                    1
                    if side == "BUY"
                    else -1
                ),

            "qty":
                abs(
                    amount
                ),

            "entry_price":
                float(
                    entry_price
                ),

            "entry_ts":
                i(
                    raw.get(
                        "entry_ts"
                    ),
                    0,
                ),

            "owner_tf":
                u(
                    raw.get(
                        "entry_tf"
                    )
                ),

            "owner_strategy":
                u(
                    raw.get(
                        "entry_strategy"
                    )
                ),
        })

        out[
            symbol
        ] = row

    return out


def risk_mark_map(
    positions,
    overrides=None,
):
    overrides = (
        overrides
        if isinstance(
            overrides,
            dict,
        )
        else {}
    )

    marks = {}

    for symbol, row in (
        positions.items()
    ):

        mark = f(
            row.get(
                "markPrice"
            ),
            0.0,
        )

        if mark <= 0:
            mark = get_price(
                symbol
            )

        if mark <= 0:
            raise RuntimeError(
                f"RISK_MARK_INVALID:"
                f"{symbol}"
            )

        marks[
            symbol
        ] = float(
            mark
        )

    for symbol, mark in (
        overrides.items()
    ):

        symbol = u(
            symbol
        )

        mark = f(
            mark,
            0.0,
        )

        if (
            not symbol
            or mark <= 0
        ):
            raise RuntimeError(
                "RISK_MARK_OVERRIDE_INVALID"
            )

        marks[
            symbol
        ] = float(
            mark
        )

    return marks


def risk_entry_guard(
    symbol,
    side,
    qty,
    price,
    frame,
    strategy,
):
    """
    실제 주문 직전 Cross Entry Gate.

    실패/데이터 부족/예외는 모두 fail-closed.
    """
    symbol = u(
        symbol
    )

    side = norm_side(
        side
    )

    try:

        positions = (
            risk_live_position_book()
        )

        # refresh 사이에 같은 심볼 포지션이 생겼으면 차단.
        if symbol in positions:

            return {
                "allow": False,
                "reason":
                    "POSITION_EXISTS_RISK_REFRESH",
                "post": {},
            }

        wallet = (
            risk_wallet_balance()
        )

        marks = risk_mark_map(
            positions,
            {
                symbol:
                    price,
            },
        )

        symbols = (
            set(
                positions
            )
            |
            {
                symbol
            }
        )

        brackets = (
            risk_leverage_brackets(
                symbols
            )
        )

        decision_ts = now_ts()

        candidate = {
            "symbol":
                symbol,

            "side":
                (
                    1
                    if side == "BUY"
                    else -1
                ),

            "entry_price":
                float(
                    price
                ),

            "qty":
                float(
                    qty
                ),

            "entry_ts":
                int(
                    decision_ts
                ),

            "owner_tf":
                u(
                    frame
                ),

            "owner_strategy":
                u(
                    strategy
                ),
        }

        result = (
            risk_engine.entry_gate(
                wallet=wallet,
                positions=positions,
                candidate=candidate,
                marks=marks,
                leverage_brackets=brackets,
                decision_ts=decision_ts,
            )
        )

        if not isinstance(
            result,
            dict,
        ):
            raise RuntimeError(
                "RISK_GATE_RESULT_INVALID"
            )

        result[
            "wallet"
        ] = float(
            wallet
        )

        result[
            "decision_ts"
        ] = int(
            decision_ts
        )

        return result

    except Exception as error:

        logger.error(
            f"[주문][RISK][ENTRY_ERROR] "
            f"{symbol} "
            f"side={side} "
            f"frame={u(frame)} "
            f"strategy={u(strategy)} "
            f"error="
            f"{type(error).__name__}:"
            f"{str(error)[:200]} "
            f"policy={POLICY}",
            exc_info=True,
        )

        return {
            "allow": False,
            "reason": "RISK_EXCEPTION",
            "error": (
                f"{type(error).__name__}:"
                f"{str(error)[:200]}"
            ),
            "post": {},
        }


# ============================================================
# LEVERAGE / SIZE
# ============================================================

def load_entry_leverage(
    symbol,
    side,
    gate_result,
):
    """
    레버리지 출처는 strategy_config -> gate_result 뿐이다.

    0 / 누락:
      주문 금지

    허용:
      1x ~ 50x

    전략 선택과 레버리지 선택은 독립적이다.
    """

    del symbol
    del side

    gate_result = (
        gate_result
        if isinstance(gate_result, dict)
        else {}
    )

    leverage = i(
        gate_result.get("leverage"),
        0,
    )

    if leverage < 1:
        return 0

    if leverage > 50:
        return 0

    return leverage


def exchange_max_leverage(symbol):
    """
    Binance가 해당 심볼에서 허용하는 최대 leverage 조회.

    백테스트와 실거래 조건을 동일하게 유지하기 위해
    요청 leverage보다 거래소 최대값이 낮으면
    자동으로 낮추지 않고 진입을 거부한다.
    """

    symbol = u(symbol)
    order = get_order()

    data = None

    for name in (
        "leverage_brackets",
        "futures_leverage_bracket",
        "leverage_bracket",
        "get_leverage_bracket",
    ):

        fn = getattr(
            order.client,
            name,
            None,
        )

        if not fn:
            continue

        try:
            data = safe_call(
                fn,
                symbol=symbol,
            )
        except TypeError:
            try:
                data = safe_call(fn)
            except Exception:
                data = None
        except Exception:
            data = None

        if data is not None:
            break

    best = 0

    def scan(obj):
        nonlocal best

        if isinstance(
            obj,
            list,
        ):
            for item in obj:
                scan(item)
            return

        if not isinstance(
            obj,
            dict,
        ):
            return

        row_symbol = u(
            obj.get("symbol")
        )

        if (
            row_symbol
            and row_symbol != symbol
        ):
            return

        for key in (
            "initialLeverage",
            "maxLeverage",
            "leverage",
        ):
            try:
                value = int(
                    float(
                        obj.get(key)
                        or 0
                    )
                )
            except Exception:
                value = 0

            if value > best:
                best = value

        for key in (
            "brackets",
            "bracket",
            "data",
            "rows",
            "items",
        ):
            scan(
                obj.get(key)
            )

    scan(data)

    return int(best)


def cap_leverage(
    symbol,
    leverage,
):
    """
    실제 적용 레버리지 결정.

    requested:
      strategy_config 값

    actual:
      min(requested, Binance 심볼 최대)

    즉 거래소 한도가 낮다고 신호를 버리지 않는다.
    """

    requested = i(
        leverage,
        0,
    )

    if requested < 1:
        return 0

    if requested > 50:
        requested = 50

    exchange_max = exchange_max_leverage(
        symbol
    )

    if exchange_max > 0:
        actual = min(
            requested,
            exchange_max,
        )
    else:
        actual = requested

    if actual < 1:
        return 0

    if actual != requested:
        logger.info(
            f"[주문][RUNNER][LEVERAGE_CAP] "
            f"{u(symbol)} "
            f"requested={requested}x "
            f"exchange_max={exchange_max}x "
            f"actual={actual}x "
            f"policy={POLICY}"
        )

    return int(actual)


def load_entry_plan(
    symbol,
    side,
    leverage,
):
    order = get_order()

    try:
        result = order.compute_entry(
            reload_config(),
            u(symbol),
            norm_side(side),
            int(leverage),
            weight=1.0,
        )

        if isinstance(
            result,
            dict,
        ):
            return result

    except Exception:
        logger.error(
            f"[주문][ENTRY][CALC_FAIL] "
            f"{u(symbol)} "
            f"{norm_side(side)}",
            exc_info=True,
        )

    return None


def validate_qty(
    symbol,
    qty,
    price,
):
    order = get_order()

    try:
        adjusted = order._adj_qty(
            u(symbol),
            f(qty),
            f(price),
        )

        if adjusted <= 0:
            return (
                False,
                "QTY_INVALID",
                0.0,
            )

        return (
            True,
            "OK",
            float(adjusted),
        )

    except Exception:
        return (
            False,
            "QTY_VALIDATE_FAIL",
            0.0,
        )


# ============================================================
# SIGNAL DEDUPE
# ============================================================

def signal_state():
    data = json_load(
        SIGNAL_STATE_FILE,
        {},
    )

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else {}
    )


def signal_used(
    symbol,
    signal_id,
):
    signal_id = s(
        signal_id
    )

    if not signal_id:
        return True

    data = signal_state()

    row = data.get(
        u(symbol)
    )

    if not isinstance(
        row,
        dict,
    ):
        return False

    return (
        s(
            row.get(
                "signal_id"
            )
        )
        ==
        signal_id
    )


def mark_signal_used(
    symbol,
    gate_result,
):
    signal_id = s(
        gate_result.get(
            "signal_id"
        )
    )

    if not signal_id:
        return

    data = signal_state()

    data[
        u(symbol)
    ] = {
        "signal_id": signal_id,
        "frame": u(
            gate_result.get(
                "frame"
            )
        ),
        "strategy": u(
            gate_result.get(
                "strategy"
            )
        ),
        "decision": u(
            gate_result.get(
                "decision"
            )
        ),
        "ts": now_ts(),
    }

    atomic_write(
        SIGNAL_STATE_FILE,
        data,
    )


# ============================================================
# HISTORY DB
# ============================================================

def table_meta(
    con,
    table,
):
    output = {}

    for row in con.execute(
        f"PRAGMA table_info({table})"
    ).fetchall():

        output[
            str(row[1])
        ] = {
            "type": u(row[2]),
            "notnull": i(
                row[3],
                0,
            ),
            "default": row[4],
            "pk": i(
                row[5],
                0,
            ),
        }

    return output


def ensure_history_tables():
    os.makedirs(
        os.path.dirname(
            HISTORY_DB
        ),
        exist_ok=True,
    )

    with sqlite3.connect(
        HISTORY_DB,
        timeout=30,
    ) as con:

        con.execute(
            "PRAGMA busy_timeout=30000;"
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS entry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                side TEXT,
                position_side TEXT,
                entry_price REAL,
                qty REAL,
                entry_leverage REAL,
                entry_id TEXT,
                entry_order_id TEXT,
                entry_tf TEXT,
                entry_strategy TEXT,
                signal_id TEXT,
                signal_candle_time TEXT,
                entry_reason TEXT,
                entry_ts INTEGER,
                payload_json TEXT
            )
            """
        )

        existing = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(entry_history)"
            ).fetchall()
        }

        additions = (
            ("entry_tf", "TEXT"),
            ("entry_strategy", "TEXT"),
            ("signal_id", "TEXT"),
            (
                "signal_candle_time",
                "TEXT",
            ),
        )

        for name, typ in additions:

            if name not in existing:
                con.execute(
                    f"ALTER TABLE entry_history "
                    f"ADD COLUMN {name} {typ}"
                )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS exit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                old_side TEXT,
                exit_price REAL,
                qty REAL,
                entry_id TEXT,
                entry_tf TEXT,
                entry_strategy TEXT,
                exit_signal_id TEXT,
                exit_reason TEXT,
                reverse_side TEXT,
                close_order_id TEXT,
                payload_json TEXT
            )
            """
        )

        con.commit()


def generic_insert(
    table,
    row,
):
    ensure_history_tables()

    try:
        with sqlite3.connect(
            HISTORY_DB,
            timeout=30,
        ) as con:

            con.execute(
                "PRAGMA busy_timeout=30000;"
            )

            meta = table_meta(
                con,
                table,
            )

            clean = {}

            for key, value in (
                row or {}
            ).items():

                if key not in meta:
                    continue

                if isinstance(
                    value,
                    (
                        dict,
                        list,
                        tuple,
                    ),
                ):
                    value = json.dumps(
                        value,
                        ensure_ascii=False,
                        default=str,
                    )

                clean[key] = value

            now = now_ts()

            # 기존 DB의 레거시 NOT NULL 컬럼 보호
            for col, info in (
                meta.items()
            ):

                if info.get("pk") == 1:
                    continue

                if (
                    col in clean
                    and
                    clean[col]
                    is not None
                ):
                    continue

                if (
                    info.get(
                        "notnull"
                    )
                    != 1
                ):
                    continue

                typ = info.get(
                    "type"
                ) or ""

                if "INT" in typ:
                    clean[col] = (
                        now
                        if col in (
                            "ts",
                            "entry_ts",
                            "created_at",
                            "first_entry_ts",
                        )
                        else 0
                    )

                elif any(
                    x in typ
                    for x in (
                        "REAL",
                        "FLOA",
                        "DOUB",
                    )
                ):
                    clean[col] = 0.0

                else:
                    clean[col] = ""

            if (
                "ts" in meta
                and
                clean.get("ts")
                in (
                    None,
                    "",
                    0,
                )
            ):
                clean["ts"] = now

            keys = list(
                clean.keys()
            )

            if not keys:
                return None

            cursor = con.execute(
                (
                    f"INSERT INTO {table} "
                    f"({','.join(keys)}) "
                    f"VALUES "
                    f"({','.join(['?'] * len(keys))})"
                ),
                [
                    clean[key]
                    for key in keys
                ],
            )

            con.commit()

            return cursor.lastrowid

    except Exception:
        logger.error(
            f"[주문][RUNNER][DB_FAIL] "
            f"table={table}",
            exc_info=True,
        )

        return None


# ============================================================
# ENTRY META
# ============================================================

def build_pending_meta(
    symbol,
    side,
    gate_result,
    leverage,
    qty,
    price,
):
    now = now_ts()

    entry_id = (
        f"{u(symbol)}_"
        f"{norm_side(side)}_"
        f"{now}_"
        f"{uuid.uuid4().hex[:10]}"
    )

    return {
        "symbol": u(symbol),
        "side": norm_side(side),
        "entry_id": entry_id,
        "entry_tf": u(
            gate_result.get(
                "frame"
            )
        ),
        "entry_strategy": u(
            gate_result.get(
                "strategy"
            )
        ),
        "signal_id": s(
            gate_result.get(
                "signal_id"
            )
        ),
        "signal_candle_time": s(
            gate_result.get(
                "confirmed_open_time"
            )
        ),
        "entry_price": float(
            price
        ),
        "qty": float(qty),
        "leverage": int(
            leverage
        ),
        "entry_leverage": int(
            leverage
        ),
        "created_ts": now,
        "entry_ts": now,
        "entry_reason": s(
            gate_result.get(
                "reason"
            )
        ),
        "gate_reason": s(
            gate_result.get(
                "reason"
            )
        ),
    }


def final_cache_row(
    live,
    pending,
    history_id=None,
):
    row = dict(
        live or {}
    )

    meta = owner_meta(
        pending
    )

    for key, value in (
        meta.items()
    ):

        if value not in (
            None,
            "",
            0,
            0.0,
        ):
            row[key] = value

    row["symbol"] = u(
        pending.get(
            "symbol"
        )
        or row.get(
            "symbol"
        )
    )

    row["side"] = norm_side(
        pending.get(
            "side"
        )
        or side_from_amt(
            row.get(
                "positionAmt"
            )
        )
    )

    if history_id:
        row[
            "entry_history_id"
        ] = int(history_id)

    entry_price = f(
        row.get(
            "entryPrice"
        )
        or pending.get(
            "entry_price"
        ),
        0.0,
    )

    if entry_price > 0:
        row[
            "entry_price"
        ] = entry_price

    return row


# ============================================================
# ORDER ENTRY
# ============================================================

def place_market_entry(
    symbol,
    side,
    qty,
    leverage,
):
    if DRY_RUN:
        return {
            "dry_run": 1,
            "orderId": (
                f"DRY_{u(symbol)}_"
                f"{norm_side(side)}_"
                f"{now_ts()}"
            ),
            "status": "DRY_RUN",
        }

    order = get_order()

    try:
        return order.market_entry(
            u(symbol),
            norm_side(side),
            qty,
            int(leverage),
        )

    except Exception:
        logger.error(
            f"[주문][ENTRY][ORDER_FAIL] "
            f"{u(symbol)} "
            f"{norm_side(side)}",
            exc_info=True,
        )

        return None


def wait_fill(
    symbol,
    order_id,
):
    if DRY_RUN:
        return True

    order = get_order()

    try:
        return bool(
            order.wait_fill(
                u(symbol),
                order_id,
                timeout=5,
            )
        )

    except Exception:
        return False


def verify_position_after_order(
    symbol,
    side,
):
    if DRY_RUN:
        return None

    for attempt in range(
        max(
            1,
            POSITION_VERIFY_RETRY,
        )
    ):

        row = fresh_position(
            symbol,
            side,
        )

        if row:
            return row

        if (
            attempt + 1
            < POSITION_VERIFY_RETRY
        ):
            time.sleep(
                max(
                    0.2,
                    POSITION_VERIFY_SLEEP,
                )
            )

    return None


# ============================================================
# EXECUTE ENTRY
# ============================================================

def execute_entry(
    symbol,
    gate_result,
    owned_reversal=False,
    state_reentry=False,
):
    symbol = u(symbol)

    if owned_reversal and state_reentry:
        logger.error(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"reason=INVALID_ENTRY_MODE "
            f"policy={POLICY}"
        )
        return False

    if not isinstance(
        gate_result,
        dict,
    ):
        return False

    side = norm_side(
        gate_result.get(
            "side"
        )
    )

    frame = u(
        gate_result.get(
            "frame"
        )
    )

    strategy = u(
        gate_result.get(
            "strategy"
        )
    )

    signal_id = s(
        gate_result.get(
            "signal_id"
        )
    )

    if (
        side not in (
            "BUY",
            "SELL",
        )
        or not frame
        or not strategy
        or not signal_id
    ):
        return False

    if fresh_position(
        symbol
    ):
        logger.info(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"reason=POSITION_EXISTS_BEFORE_ORDER "
            f"policy={POLICY}"
        )

        return False

    if signal_used(
        symbol,
        signal_id,
    ):
        log_rl(
            "SIGNAL_USED",
            symbol,
            (
                f"[주문][ENTRY][SKIP] "
                f"{symbol} "
                f"reason=SIGNAL_ALREADY_USED "
                f"signal_id={signal_id} "
                f"policy={POLICY}"
            ),
        )

        return False

    # --------------------------------------------------------
    # 주문 직전 Gate 최종 확인
    # --------------------------------------------------------

    if state_reentry:

        final_gate = (
            gate.evaluate_owned_position(
                symbol,
                frame,
                strategy,
                state_mode=True,
            )
        )

    elif owned_reversal:

        final_gate = (
            gate.evaluate_owned_position(
                symbol,
                frame,
                strategy,
            )
        )

    else:

        final_gate = (
            gate.evaluate_frame(
                symbol,
                frame,
                require_enabled=True,
            )
        )

    if not isinstance(
        final_gate,
        dict,
    ):
        return False

    if (
        i(
            final_gate.get(
                "allow"
            ),
            0,
        )
        != 1
        or
        norm_side(
            final_gate.get(
                "side"
            )
        )
        != side
        or
        u(
            final_gate.get(
                "strategy"
            )
        )
        != strategy
        or
        (
            not state_reentry
            and
            s(
                final_gate.get(
                    "signal_id"
                )
            )
            != signal_id
        )
    ):
        logger.info(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"frame={frame} "
            f"strategy={strategy} "
            f"reason=FINAL_GATE_CHANGED "
            f"policy={POLICY}"
        )

        return False

    price = get_price(
        symbol
    )

    if price <= 0:
        return False

    requested_leverage = (
        load_entry_leverage(
            symbol,
            side,
            gate_result,
        )
    )

    leverage = cap_leverage(
        symbol,
        requested_leverage,
    )

    if leverage <= 0:
        logger.info(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"frame={frame} "
            f"strategy={strategy} "
            f"requested_leverage="
            f"{requested_leverage} "
            f"reason=LEVERAGE_NOT_APPROVED "
            f"policy={POLICY}"
        )

        return False

    plan = load_entry_plan(
        symbol,
        side,
        leverage,
    )

    if not isinstance(
        plan,
        dict,
    ):
        logger.info(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"reason=ENTRY_PLAN_INVALID "
            f"policy={POLICY}"
        )

        return False

    ok_qty, reason, qty = (
        validate_qty(
            symbol,
            plan.get("qty"),
            price,
        )
    )

    if not ok_qty:
        logger.info(
            f"[주문][ENTRY][SKIP] "
            f"{symbol} "
            f"reason={reason} "
            f"policy={POLICY}"
        )

        return False

    # --------------------------------------------------------
    # CROSS ENTRY GATE
    #
    # 모든 신규 노출:
    # - normal EVENT
    # - owned reversal
    # - STATE reentry
    #
    # candidate까지 포함하여 주문 직전 stress 검사.
    # --------------------------------------------------------

    risk_result = risk_entry_guard(
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        frame=frame,
        strategy=strategy,
    )

    if not isinstance(
        risk_result,
        dict,
    ):
        logger.error(
            f"[주문][RISK][ENTRY_BLOCK] "
            f"{symbol} "
            f"reason=RISK_RESULT_INVALID "
            f"policy={POLICY}"
        )

        return False

    if not bool(
        risk_result.get(
            "allow"
        )
    ):
        post = (
            risk_result.get(
                "post"
            )
            if isinstance(
                risk_result.get(
                    "post"
                ),
                dict,
            )
            else {}
        )

        logger.info(
            f"[주문][RISK][ENTRY_BLOCK] "
            f"{symbol} "
            f"side={side} "
            f"frame={frame} "
            f"strategy={strategy} "
            f"reason="
            f"{s(risk_result.get('reason'))} "
            f"baseline_cushion="
            f"{f(post.get('baseline_cushion'), 0.0):.8f} "
            f"min_cushion="
            f"{f(post.get('min_cushion'), 0.0):.8f} "
            f"scenarios="
            f"{i(post.get('scenario_count'), 0)} "
            f"worst_ts="
            f"{s(post.get('worst_scenario_ts'))} "
            f"policy={POLICY}"
        )

        return False

    post = (
        risk_result.get(
            "post"
        )
        if isinstance(
            risk_result.get(
                "post"
            ),
            dict,
        )
        else {}
    )

    logger.info(
        f"[주문][RISK][ENTRY_ALLOW] "
        f"{symbol} "
        f"side={side} "
        f"frame={frame} "
        f"strategy={strategy} "
        f"entry_fee="
        f"{f(risk_result.get('entry_fee'), 0.0):.8f} "
        f"min_cushion="
        f"{f(post.get('min_cushion'), 0.0):.8f} "
        f"scenarios="
        f"{i(post.get('scenario_count'), 0)} "
        f"policy={POLICY}"
    )

    pending = build_pending_meta(
        symbol,
        side,
        gate_result,
        leverage,
        qty,
        price,
    )

    save_pending(
        pending
    )

    logger.info(
        f"[주문][ENTRY][READY] "
        f"{symbol} "
        f"side={side} "
        f"frame={frame} "
        f"strategy={strategy} "
        f"qty={qty} "
        f"requested_lev={requested_leverage} "
        f"actual_lev={leverage} "
        f"signal_id={signal_id} "
        f"owned_reversal="
        f"{int(bool(owned_reversal))} "
        f"state_reentry="
        f"{int(bool(state_reentry))} "
        f"dry_run={int(DRY_RUN)} "
        f"policy={POLICY}"
    )

    response = place_market_entry(
        symbol,
        side,
        qty,
        leverage,
    )

    if not isinstance(
        response,
        dict,
    ) or not (
        response.get(
            "orderId"
        )
        or response.get(
            "clientOrderId"
        )
    ):

        if not fresh_position(
            symbol,
            side,
        ):
            clear_pending(
                symbol,
                side,
            )

        return False

    order_id = s(
        response.get(
            "orderId"
        )
    )

    if DRY_RUN:

        mark_signal_used(
            symbol,
            gate_result,
        )

        clear_pending(
            symbol,
            side,
        )

        logger.info(
            f"[주문][ENTRY][DRY_OK] "
            f"{symbol} "
            f"side={side} "
            f"frame={frame} "
            f"strategy={strategy} "
            f"policy={POLICY}"
        )

        return True

    wait_fill(
        symbol,
        order_id,
    )

    live = (
        verify_position_after_order(
            symbol,
            side,
        )
    )

    if not live:
        logger.error(
            f"[주문][ENTRY][FAIL] "
            f"{symbol} "
            f"reason=POSITION_VERIFY_FAIL "
            f"order_id={order_id} "
            f"policy={POLICY}"
        )

        # 불확실 주문은 pending 유지
        return False

    actual_entry = f(
        live.get(
            "entryPrice"
        ),
        0.0,
    )

    if actual_entry <= 0:
        return False

    pending[
        "entry_price"
    ] = actual_entry

    history_row = {
        "ts": now_ts(),
        "symbol": symbol,
        "side": side,
        "position_side": (
            position_side(side)
        ),
        "entry_price": actual_entry,
        "qty": abs(
            f(
                live.get(
                    "positionAmt"
                ),
                qty,
            )
        ),
        "entry_leverage": leverage,
        "entry_id": pending.get(
            "entry_id"
        ),
        "entry_order_id": order_id,
        "entry_tf": frame,
        "entry_strategy": strategy,
        "signal_id": signal_id,
        "signal_candle_time": (
            pending.get(
                "signal_candle_time"
            )
        ),
        "entry_reason": (
            pending.get(
                "entry_reason"
            )
        ),
        "entry_ts": (
            pending.get(
                "entry_ts"
            )
        ),
        "payload_json": {
            "gate": gate_result,
            "order": response,
            "pending": pending,
        },
    }

    history_id = generic_insert(
        "entry_history",
        history_row,
    )

    cache_row = final_cache_row(
        live,
        pending,
        history_id,
    )

    update_cache_entry(
        symbol,
        cache_row,
    )

    mark_signal_used(
        symbol,
        gate_result,
    )

    clear_pending(
        symbol,
        side,
    )

    logger.info(
        f"[주문][ENTRY][SUCCESS] "
        f"{symbol} "
        f"side={side} "
        f"frame={frame} "
        f"strategy={strategy} "
        f"entry_price={actual_entry} "
        f"history_id={history_id} "
        f"policy={POLICY}"
    )

    return True


# ============================================================
# CLOSE
# ============================================================

def wait_flat(symbol):
    for attempt in range(
        max(
            3,
            POSITION_VERIFY_RETRY,
        )
    ):

        if not fresh_position(
            symbol
        ):
            return True

        if (
            attempt + 1
            < POSITION_VERIFY_RETRY
        ):
            time.sleep(
                max(
                    0.3,
                    POSITION_VERIFY_SLEEP,
                )
            )

    return False


def close_owned_position(
    position,
    gate_result,
):
    if not isinstance(
        position,
        dict,
    ):
        return False

    symbol = u(
        position.get(
            "symbol"
        )
    )

    old_side = side_from_amt(
        position.get(
            "positionAmt"
        )
    )

    amount = abs(
        f(
            position.get(
                "positionAmt"
            ),
            0.0,
        )
    )

    if (
        not symbol
        or
        old_side not in (
            "BUY",
            "SELL",
        )
        or
        amount <= 0
    ):
        return False

    if DRY_RUN:

        logger.info(
            f"[주문][CLOSE][DRY_OK] "
            f"{symbol} "
            f"old_side={old_side} "
            f"frame={u(position.get('entry_tf'))} "
            f"strategy="
            f"{u(position.get('entry_strategy'))} "
            f"policy={POLICY}"
        )

        return True

    order = get_order()

    response = order.close_position(
        symbol,
        amount,
        position_side(
            old_side
        ),
    )

    if not isinstance(
        response,
        dict,
    ):
        logger.error(
            f"[주문][CLOSE][FAIL] "
            f"{symbol} "
            f"policy={POLICY}"
        )

        return False

    if not wait_flat(
        symbol
    ):
        logger.error(
            f"[주문][CLOSE][FAIL] "
            f"{symbol} "
            f"reason=NOT_FLAT_AFTER_CLOSE "
            f"policy={POLICY}"
        )

        return False

    exit_price = get_price(
        symbol
    )

    generic_insert(
        "exit_history",
        {
            "ts": now_ts(),
            "symbol": symbol,
            "old_side": old_side,
            "exit_price": exit_price,
            "qty": amount,
            "entry_id": position.get(
                "entry_id"
            ),
            "entry_tf": u(
                position.get(
                    "entry_tf"
                )
            ),
            "entry_strategy": u(
                position.get(
                    "entry_strategy"
                )
            ),
            "exit_signal_id": s(
                gate_result.get(
                    "signal_id"
                )
            ),
            "exit_reason": s(
                gate_result.get(
                    "reason"
                )
            ),
            "reverse_side": norm_side(
                gate_result.get(
                    "side"
                )
            ),
            "close_order_id": s(
                response.get(
                    "orderId"
                )
            ),
            "payload_json": {
                "position": position,
                "gate": gate_result,
                "close_response": response,
            },
        },
    )

    logger.info(
        f"[주문][CLOSE][SUCCESS] "
        f"{symbol} "
        f"old_side={old_side} "
        f"frame={u(position.get('entry_tf'))} "
        f"strategy="
        f"{u(position.get('entry_strategy'))} "
        f"policy={POLICY}"
    )

    return True


# ============================================================
# PENDING RECOVERY
# ============================================================

def recover_pending_for_active_positions():
    data = pending_state()

    if not data:
        return

    for key, pending in list(
        data.items()
    ):

        if not isinstance(
            pending,
            dict,
        ):
            continue

        symbol = u(
            pending.get(
                "symbol"
            )
        )

        side = norm_side(
            pending.get(
                "side"
            )
        )

        if (
            not symbol
            or
            side not in (
                "BUY",
                "SELL",
            )
        ):
            continue

        live = fresh_position(
            symbol,
            side,
        )

        if not live:
            continue

        cache_row = final_cache_row(
            live,
            pending,
        )

        update_cache_entry(
            symbol,
            cache_row,
        )

        clear_pending(
            symbol,
            side,
        )

        logger.info(
            f"[주문][RUNNER][PENDING_RECOVER_OK] "
            f"{symbol} "
            f"side={side} "
            f"frame={u(pending.get('entry_tf'))} "
            f"strategy="
            f"{u(pending.get('entry_strategy'))} "
            f"policy={POLICY}"
        )


# ============================================================
# NEW ENTRY CANDIDATE
# ============================================================

def new_entry_candidate(symbol):
    result = gate.evaluate_symbol(
        u(symbol)
    )

    candidates = (
        result.get(
            "candidates"
        )
        if isinstance(
            result,
            dict,
        )
        else []
    )

    if not isinstance(
        candidates,
        list,
    ):
        candidates = []

    candidates = [
        row
        for row in candidates
        if isinstance(
            row,
            dict,
        )
        and
        i(
            row.get(
                "allow"
            ),
            0,
        )
        == 1
    ]

    if not candidates:
        return None

    # one-way position에서는 소유 TF가 하나여야 한다.
    # 임의 우선순위를 Runner가 만들지 않는다.
    if len(candidates) != 1:

        logger.info(
            f"[주문][RUNNER][ENTRY_SKIP] "
            f"{u(symbol)} "
            f"reason=MULTI_TF_CANDIDATE "
            f"candidates="
            f"{[(x.get('frame'), x.get('strategy'), x.get('side')) for x in candidates]} "
            f"policy={POLICY}"
        )

        return None

    return candidates[0]


# ============================================================
# POSITION MANAGEMENT
# ============================================================

def manage_owned_position(position):
    """
    기존 포지션 관리.

    우선순위:
    1) Mark Price 기반 Trailing 3/1.5
    2) 기존 owner strategy 유지
    3) owner 반대 합의 시 기존 reverse 흐름

    Trailing은 과거 extreme을 소급하지 않는다.
    state가 처음 만들어지는 순간의 현재 Mark Price부터 추적한다.
    """
    symbol = u(
        position.get(
            "symbol"
        )
    )

    current_side = side_from_amt(
        position.get(
            "positionAmt"
        )
    )

    frame = u(
        position.get(
            "entry_tf"
        )
    )

    strategy = u(
        position.get(
            "entry_strategy"
        )
    )

    # --------------------------------------------------------
    # CURRENT OWNER SYNC
    #
    # 현재 열려 있는 모든 포지션은 legacy/new 구분 없이
    # 현재 strategy_config의 활성 전략으로 관리한다.
    #
    # 과거 exit_history는 건드리지 않는다.
    # --------------------------------------------------------

    strategy_cfg = json_load(
        "/home/ec2-user/bot/strategy_config.json",
        {},
    )

    frames_cfg = (
        strategy_cfg.get(
            "frames",
            {}
        )
        if isinstance(
            strategy_cfg,
            dict,
        )
        else {}
    )

    frame_cfg = (
        frames_cfg.get(
            frame,
            {}
        )
        if (
            isinstance(
                frames_cfg,
                dict,
            )
            and frame
        )
        else {}
    )

    if isinstance(
        frame_cfg,
        dict,
    ):
        enabled = bool(
            frame_cfg.get(
                "enabled",
                False,
            )
        )

        raw_strategies = (
            frame_cfg.get(
                "strategies",
                []
            )
        )

        current_strategies = []

        if isinstance(
            raw_strategies,
            (list, tuple),
        ):
            for item in raw_strategies:
                name = u(item)

                if name:
                    current_strategies.append(
                        name
                    )

        elif raw_strategies:
            name = u(
                raw_strategies
            )

            if name:
                current_strategies.append(
                    name
                )

        current_strategy = "+".join(
            current_strategies
        )

        current_leverage = i(
            frame_cfg.get(
                "leverage"
            ),
            0,
        )

        if (
            enabled
            and current_strategy
            and current_leverage > 0
        ):
            owner_changed = (
                strategy != current_strategy
                or i(
                    position.get(
                        "entry_leverage",
                        position.get(
                            "leverage",
                            0,
                        ),
                    ),
                    0,
                )
                != current_leverage
            )

            strategy = current_strategy

            position = dict(
                position
            )

            position[
                "entry_tf"
            ] = frame

            position[
                "entry_strategy"
            ] = strategy

            position[
                "entry_leverage"
            ] = int(
                current_leverage
            )

            position[
                "leverage"
            ] = int(
                current_leverage
            )

            if owner_changed:
                update_cache_entry(
                    symbol,
                    position,
                )

                logger.info(
                    f"[주문][RUNNER][OWNER_SYNC] "
                    f"{symbol} "
                    f"frame={frame} "
                    f"strategy={strategy} "
                    f"leverage={current_leverage} "
                    f"source=CURRENT_CONFIG "
                    f"policy={POLICY}"
                )

    if (
        not symbol
        or current_side not in (
            "BUY",
            "SELL",
        )
    ):
        return False

    # --------------------------------------------------------
    # TRAILING 3/1.5 - MARK PRICE / NON-RETROACTIVE
    # --------------------------------------------------------

    entry_price = f(
        position.get(
            "entryPrice"
        )
        or position.get(
            "entry_price"
        ),
        0.0,
    )

    mark_price = get_price(
        symbol
    )

    if (
        entry_price > 0
        and mark_price > 0
    ):
        now = now_ts()

        state = json_load(
            TRAIL_STATE_FILE,
            {},
        )

        if not isinstance(
            state,
            dict,
        ):
            state = {}

        dirty = False

        # ----------------------------------------------------
        # 오래된 trailing state 정리
        # ----------------------------------------------------

        for old_symbol, old_row in list(
            state.items()
        ):
            if not isinstance(
                old_row,
                dict,
            ):
                state.pop(
                    old_symbol,
                    None,
                )
                dirty = True
                continue

            updated_ts = i(
                old_row.get(
                    "updated_ts"
                ),
                0,
            )

            if (
                updated_ts > 0
                and now - updated_ts
                > TRAIL_STATE_RETENTION_SEC
            ):
                state.pop(
                    old_symbol,
                    None,
                )
                dirty = True

        row = state.get(
            symbol
        )

        if not isinstance(
            row,
            dict,
        ):
            row = {}

        entry_id = s(
            position.get(
                "entry_id"
            )
        )

        same_position = (
            s(
                row.get(
                    "entry_id"
                )
            )
            == entry_id
            and u(
                row.get(
                    "side"
                )
            )
            == current_side
            and abs(
                f(
                    row.get(
                        "entry_price"
                    ),
                    0.0,
                )
                - entry_price
            )
            <= max(
                1e-12,
                abs(entry_price) * 1e-10,
            )
            and s(
                row.get(
                    "status"
                )
            )
            in (
                "TRACKING",
                "ARMED",
            )
        )

        if not same_position:
            # 기존 보유 포지션도 현재 Mark부터 새로 시작한다.
            row = {
                "symbol": symbol,
                "entry_id": entry_id,
                "side": current_side,
                "entry_tf": frame,
                "entry_strategy": strategy,
                "entry_price": float(
                    entry_price
                ),
                "extreme": float(
                    mark_price
                ),
                "armed": 0,
                "status": "TRACKING",
                "created_ts": now,
                "updated_ts": now,
            }

            state[
                symbol
            ] = row

            dirty = True

            logger.info(
                f"[주문][RUNNER][TRAIL_INIT] "
                f"{symbol} "
                f"side={current_side} "
                f"entry={entry_price:.12g} "
                f"mark={mark_price:.12g} "
                f"frame={frame} "
                f"strategy={strategy} "
                f"start={TRAIL_START_PCT:.2f}% "
                f"callback={TRAIL_CALLBACK_PCT:.2f}% "
                f"basis=MARK_PRICE_NON_RETROACTIVE "
                f"policy={POLICY}"
            )

        extreme = f(
            row.get(
                "extreme"
            ),
            mark_price,
        )

        armed = (
            i(
                row.get(
                    "armed"
                ),
                0,
            )
            == 1
        )

        if current_side == "BUY":
            move_pct = (
                (mark_price - entry_price)
                / entry_price
                * 100.0
            )
        else:
            move_pct = (
                (entry_price - mark_price)
                / entry_price
                * 100.0
            )

        # ----------------------------------------------------
        # +3% 도달 -> Trailing 활성
        # ----------------------------------------------------

        if (
            not armed
            and move_pct >= TRAIL_START_PCT
        ):
            armed = True

            row["armed"] = 1
            row["status"] = "ARMED"
            row["armed_ts"] = now

            # 활성 순간 현재 Mark를 최초 extreme으로 사용.
            extreme = mark_price

            row["extreme"] = float(
                extreme
            )

            row["updated_ts"] = now

            dirty = True

            logger.info(
                f"[주문][RUNNER][TRAIL_ARM] "
                f"{symbol} "
                f"side={current_side} "
                f"move={move_pct:.6f}% "
                f"extreme={extreme:.12g} "
                f"start={TRAIL_START_PCT:.2f}% "
                f"callback={TRAIL_CALLBACK_PCT:.2f}% "
                f"policy={POLICY}"
            )

        # ----------------------------------------------------
        # 활성 후 extreme 갱신 / 1.5% callback
        # ----------------------------------------------------

        if armed:
            old_extreme = extreme

            if current_side == "BUY":
                extreme = max(
                    extreme,
                    mark_price,
                )

                stop_price = (
                    extreme
                    * (
                        1.0
                        - TRAIL_CALLBACK_PCT
                        / 100.0
                    )
                )

                trail_hit = (
                    mark_price <= stop_price
                )

            else:
                extreme = min(
                    extreme,
                    mark_price,
                )

                stop_price = (
                    extreme
                    * (
                        1.0
                        + TRAIL_CALLBACK_PCT
                        / 100.0
                    )
                )

                trail_hit = (
                    mark_price >= stop_price
                )

            if abs(
                extreme - old_extreme
            ) > max(
                1e-12,
                abs(old_extreme) * 1e-12,
            ):
                row["extreme"] = float(
                    extreme
                )

                row["updated_ts"] = now

                dirty = True

            if trail_hit:
                logger.info(
                    f"[주문][RUNNER][TRAIL_EXIT_SIGNAL] "
                    f"{symbol} "
                    f"side={current_side} "
                    f"entry={entry_price:.12g} "
                    f"mark={mark_price:.12g} "
                    f"extreme={extreme:.12g} "
                    f"stop={stop_price:.12g} "
                    f"move={move_pct:.6f}% "
                    f"start={TRAIL_START_PCT:.2f}% "
                    f"callback={TRAIL_CALLBACK_PCT:.2f}% "
                    f"policy={POLICY}"
                )

                trail_result = {
                    "allow": 1,
                    "side": opposite_side(
                        current_side
                    ),
                    "frame": frame,
                    "strategy": strategy,
                    "signal_id": (
                        f"{symbol}|TRAIL_3_1P5|{now}"
                    ),
                    "reason": "TRAILING_3_1P5",
                }

                if close_owned_position(
                    position,
                    trail_result,
                ):
                    row.update({
                        "status": "STATE_WAIT",
                        "armed": 0,
                        "extreme": float(
                            extreme
                        ),
                        "exit_price": float(
                            mark_price
                        ),
                        "exit_ts": now,
                        "updated_ts": now,
                        "entry_tf": frame,
                        "entry_strategy": strategy,
                        "side": current_side,
                        "entry_leverage": f(
                            position.get(
                                "entry_leverage"
                            )
                            or position.get(
                                "leverage"
                            ),
                            0.0,
                        ),
                    })

                    state[
                        symbol
                    ] = row

                    atomic_write(
                        TRAIL_STATE_FILE,
                        state,
                    )

                    logger.info(
                        f"[주문][RUNNER][TRAIL_EXIT_OK] "
                        f"{symbol} "
                        f"next=STATE_REENTRY_WAIT "
                        f"frame={frame} "
                        f"strategy={strategy} "
                        f"policy={POLICY}"
                    )

                    return True

        if dirty:
            state[
                symbol
            ] = row

            atomic_write(
                TRAIL_STATE_FILE,
                state,
            )

        # 반복 로그 dedupe
        if armed:
            log_rl(
                "TRAIL_HOLD",
                symbol,
                (
                    f"[주문][RUNNER][TRAIL_HOLD] "
                    f"{symbol} "
                    f"side={current_side} "
                    f"move={move_pct:.6f}% "
                    f"extreme={extreme:.12g} "
                    f"stop={stop_price:.12g} "
                    f"policy={POLICY}"
                ),
                sec=30,
            )

        else:
            log_rl(
                "TRAIL_WAIT",
                symbol,
                (
                    f"[주문][RUNNER][TRAIL_WAIT] "
                    f"{symbol} "
                    f"side={current_side} "
                    f"move={move_pct:.6f}% "
                    f"start={TRAIL_START_PCT:.2f}% "
                    f"policy={POLICY}"
                ),
                sec=60,
            )

    # --------------------------------------------------------
    # 현재 활성 전략 관리
    # --------------------------------------------------------

    if (
        not frame
        or not strategy
    ):
        log_rl(
            "OWNER_UNRESOLVED",
            symbol,
            (
                f"[주문][RUNNER][HOLD] "
                f"{symbol} "
                f"reason=POSITION_OWNER_UNRESOLVED "
                f"side={current_side} "
                f"policy={POLICY}"
            ),
            sec=300,
        )

        return False

    result = (
        gate.evaluate_owned_position(
            symbol,
            frame,
            strategy,
        )
    )

    if not isinstance(
        result,
        dict,
    ):
        return False

    decision_side = norm_side(
        result.get(
            "side"
        )
    )

    if (
        i(
            result.get(
                "allow"
            ),
            0,
        )
        != 1
        or not decision_side
    ):
        log_rl(
            "OWNER_HOLD",
            symbol,
            (
                f"[주문][RUNNER][HOLD] "
                f"{symbol} "
                f"side={current_side} "
                f"frame={frame} "
                f"strategy={strategy} "
                f"reason={s(result.get('reason'))} "
                f"policy={POLICY}"
            ),
        )

        return False

    if (
        decision_side
        == current_side
    ):
        log_rl(
            "OWNER_SAME",
            symbol,
            (
                f"[주문][RUNNER][HOLD] "
                f"{symbol} "
                f"side={current_side} "
                f"frame={frame} "
                f"strategy={strategy} "
                f"reason=SAME_DIRECTION "
                f"policy={POLICY}"
            ),
        )

        return False

    logger.info(
        f"[주문][RUNNER][REVERSE_SIGNAL] "
        f"{symbol} "
        f"old_side={current_side} "
        f"new_side={decision_side} "
        f"frame={frame} "
        f"strategy={strategy} "
        f"signal_id={s(result.get('signal_id'))} "
        f"policy={POLICY}"
    )

    if not close_owned_position(
        position,
        result,
    ):
        return False

    if (
        not DRY_RUN
        and fresh_position(
            symbol
        )
    ):
        logger.error(
            f"[주문][RUNNER][REVERSE_ABORT] "
            f"{symbol} "
            f"reason=POSITION_NOT_FLAT "
            f"policy={POLICY}"
        )

        return False

    exit_ts = now_ts()
    exit_price = get_price(
        symbol
    )

    return opposite_signal_state_wait(
        position=position,
        new_side=decision_side,
        frame=frame,
        strategy=strategy,
        exit_price=exit_price,
        exit_ts=exit_ts,
    )


# ============================================================
# SYMBOL
# ============================================================


# ============================================================
# CONTROL SL
# ============================================================

def control_sl_confirmed_marks(
    positions,
):
    """
    현재 진행 중인 1H Mark 봉은 제외하고
    직전 확정 1H Mark close만 반환.
    """
    now_ms = int(
        time.time()
        * 1000
    )

    current_hour_ms = (
        now_ms
        // 3600000
    ) * 3600000

    order = get_order()

    marks = {}

    for symbol in sorted(
        positions
    ):

        response = (
            order.client.mark_price_klines(
                symbol=symbol,
                interval="1h",
                limit=3,
            )
        )

        if isinstance(
            response,
            dict,
        ):
            rows = response.get(
                "data"
            )

        else:
            rows = response

        if not isinstance(
            rows,
            list,
        ):
            raise RuntimeError(
                f"{symbol}:BAD_MARK_KLINES"
            )

        chosen = None

        for row in rows:

            if (
                not isinstance(
                    row,
                    list,
                )
                or len(row) < 7
            ):
                continue

            open_ms = int(
                row[
                    0
                ]
            )

            close_ms = int(
                row[
                    6
                ]
            )

            if (
                open_ms
                <
                current_hour_ms
                and
                close_ms
                <
                now_ms
            ):
                chosen = row

        if chosen is None:
            raise RuntimeError(
                f"{symbol}:"
                f"NO_CONFIRMED_1H_MARK"
            )

        marks[
            symbol
        ] = float(
            chosen[
                4
            ]
        )

    control_ts = int(
        current_hour_ms
        // 1000
    )

    return (
        marks,
        control_ts,
    )





def opposite_signal_state_wait(
    position,
    new_side,
    frame,
    strategy,
    exit_price,
    exit_ts,
):
    """
    STANDARD V2:
    owner 반대 EVENT로 청산한 뒤 즉시 반대 진입하지 않는다.

    다음 1H 시점에 CURRENT strategy STATE를 다시 확인하고,
    그 STATE가 new_side와 같을 때만 기존 STATE re-entry 경로로 진입한다.
    """
    symbol = u(
        position.get(
            "symbol"
        )
    )
    new_side = norm_side(
        new_side
    )
    frame = u(
        frame
    )
    strategy = u(
        strategy
    )

    if (
        not symbol
        or new_side not in (
            "BUY",
            "SELL",
        )
        or not frame
        or not strategy
    ):
        return False

    state = json_load(
        TRAIL_STATE_FILE,
        {},
    )

    if not isinstance(
        state,
        dict,
    ):
        state = {}

    next_state_check_ts = (
        ((int(exit_ts) // 3600) + 1)
        * 3600
        if int(exit_ts) > 0
        else ((now_ts() // 3600) + 1) * 3600
    )

    state[
        symbol
    ] = {
        "symbol":
            symbol,
        "status":
            "STATE_WAIT",
        "armed":
            0,
        "side":
            new_side,
        "entry_tf":
            frame,
        "entry_strategy":
            strategy,
        "entry_leverage":
            i(
                position.get(
                    "entry_leverage",
                    position.get(
                        "leverage",
                        0,
                    ),
                ),
                0,
            ),
        "exit_price":
            float(
                exit_price
            ),
        "exit_ts":
            int(
                exit_ts
            ),
        "exit_reason":
            "OPPOSITE_SIGNAL_NEXT_1H_STATE",
        "next_state_check_ts":
            int(
                next_state_check_ts
            ),
        "updated_ts":
            now_ts(),
    }

    atomic_write(
        TRAIL_STATE_FILE,
        state,
    )

    logger.info(
        f"[주문][RUNNER][REVERSE_STATE_WAIT] "
        f"{symbol} "
        f"new_side={new_side} "
        f"frame={frame} "
        f"strategy={strategy} "
        f"exit_ts={int(exit_ts)} "
        f"next_ts={int(next_state_check_ts)} "
        f"policy={POLICY}"
    )

    return True


def control_sl_state_wait(
    position,
    exit_price,
    exit_ts,
):
    """
    Control SL 청산 후 OWNER 보존 STATE_WAIT.
    """
    symbol = u(
        position.get(
            "symbol"
        )
    )

    side = side_from_amt(
        position.get(
            "positionAmt"
        )
    )

    if side not in (
        "BUY",
        "SELL",
    ):
        return False

    state = json_load(
        TRAIL_STATE_FILE,
        {},
    )

    if not isinstance(
        state,
        dict,
    ):
        state = {}

    state[
        symbol
    ] = {
        "status":
            "STATE_WAIT",

        "side":
            side,

        "entry_tf":
            u(
                position.get(
                    "entry_tf"
                )
            ),

        "entry_strategy":
            u(
                position.get(
                    "entry_strategy"
                )
            ),

        "entry_leverage":
            i(
                position.get(
                    "entry_leverage",
                    position.get(
                        "leverage",
                        0,
                    ),
                ),
                0,
            ),

        "exit_price":
            float(
                exit_price
            ),

        "exit_ts":
            int(
                exit_ts
            ),

        "exit_reason":
            "CONTROL_SL",
    }

    atomic_write(
        TRAIL_STATE_FILE,
        state,
    )

    return True


def control_sl_once():
    """
    매 확정 1H마다 정확히 한 번 검사.

    모든 현재 포지션에 동일한 Control SL 정책을 적용한다.
    """
    positions = (
        risk_live_position_book()
    )

    if not positions:
        return False

    (
        confirmed_marks,
        control_ts,
    ) = control_sl_confirmed_marks(
        positions
    )

    control_state = json_load(
        CONTROL_SL_STATE_FILE,
        {},
    )

    if not isinstance(
        control_state,
        dict,
    ):
        control_state = {}

    last_ts = i(
        control_state.get(
            "last_control_ts"
        ),
        0,
    )

    if (
        last_ts
        >=
        control_ts
    ):
        return False

    # legacy/new 구분 없음.
    # 모든 포지션이 Control SL 대상.
    protected = set()

    wallet = (
        risk_wallet_balance()
    )

    brackets = (
        risk_leverage_brackets(
            set(
                positions
            )
        )
    )

    plan = (
        risk_engine.control_plan(
            wallet=wallet,
            positions=positions,
            confirmed_marks=
                confirmed_marks,
            leverage_brackets=
                brackets,
            control_ts=
                control_ts,
            protected_pids=
                protected,
        )
    )

    if not isinstance(
        plan,
        dict,
    ):
        raise RuntimeError(
            "CONTROL_SL_PLAN_INVALID"
        )

    initial = (
        plan.get(
            "initial"
        )
        if isinstance(
            plan.get(
                "initial"
            ),
            dict,
        )
        else {}
    )

    logger.info(
        f"[주문][CONTROL_SL][CHECK] "
        f"control_ts={control_ts} "
        f"positions={len(positions)} "
        f"protected={len(protected)} "
        f"history_ready="
        f"{int(bool(plan.get('history_ready')))} "
        f"breach="
        f"{int(bool(plan.get('breach')))} "
        f"safe="
        f"{int(bool(plan.get('safe')))} "
        f"min_cushion="
        f"{f(initial.get('min_cushion'), 0.0):.8f} "
        f"planned_closes="
        f"{len(plan.get('closes') or [])} "
        f"policy={POLICY}"
    )

    # History 없으면 Control SL 주문 금지.
    if not bool(
        plan.get(
            "history_ready"
        )
    ):
        control_state[
            "last_control_ts"
        ] = control_ts

        control_state[
            "last_result"
        ] = "NO_HISTORY"

        atomic_write(
            CONTROL_SL_STATE_FILE,
            control_state,
        )

        return False

    actions = 0

    for close_plan in (
        plan.get(
            "closes"
        )
        or []
    ):

        symbol = u(
            close_plan.get(
                "symbol"
            )
        )

        # 직전 청산 이후 Binance 실제 포지션 재확인.
        position = fresh_position(
            symbol
        )

        if not isinstance(
            position,
            dict,
        ):
            continue

        # Binance fresh position에는 owner metadata가
        # 없을 수 있으므로 현재 position cache owner를 결합한다.
        cached_position = open_position(
            symbol
        )

        if isinstance(
            cached_position,
            dict,
        ):
            position = dict(
                position
            )

            for key in (
                "entry_id",
                "entry_history_id",
                "entry_tf",
                "entry_strategy",
                "signal_id",
                "signal_candle_time",
                "entry_leverage",
                "leverage",
                "entry_ts",
                "entry_reason",
            ):
                value = cached_position.get(
                    key
                )

                if value not in (
                    None,
                    "",
                ):
                    position[
                        key
                    ] = value

        gate_result = {
            "signal_id":
                (
                    f"CONTROL_SL|"
                    f"{control_ts}|"
                    f"{symbol}"
                ),

            "reason":
                "CONTROL_SL",

            "side":
                "",
        }

        logger.info(
            f"[주문][CONTROL_SL]"
            f"[EXIT_SIGNAL] "
            f"{symbol} "
            f"before="
            f"{f(close_plan.get('before_min_cushion'), 0.0):.8f} "
            f"after="
            f"{f(close_plan.get('after_min_cushion'), 0.0):.8f} "
            f"control_ts={control_ts} "
            f"policy={POLICY}"
        )

        ok = close_owned_position(
            position,
            gate_result,
        )

        if not ok:
            logger.error(
                f"[주문][CONTROL_SL]"
                f"[EXIT_FAIL] "
                f"{symbol} "
                f"control_ts={control_ts} "
                f"policy={POLICY}"
            )

            # 순차 청산 중 실패하면
            # 다음 포지션을 맹목적으로 청산하지 않는다.
            break

        exit_price = get_price(
            symbol
        )

        control_sl_state_wait(
            position,
            exit_price,
            control_ts,
        )

        actions += 1

        logger.info(
            f"[주문][CONTROL_SL]"
            f"[EXIT_OK] "
            f"{symbol} "
            f"owner="
            f"{u(position.get('entry_tf'))}/"
            f"{u(position.get('entry_strategy'))} "
            f"control_ts={control_ts} "
            f"policy={POLICY}"
        )

    # 이번 1H Control event 처리 완료.
    control_state[
        "last_control_ts"
    ] = control_ts

    control_state[
        "last_result"
    ] = {
        "actions":
            actions,

        "protected":
            len(
                protected
            ),

        "breach":
            bool(
                plan.get(
                    "breach"
                )
            ),

        "safe_after_plan":
            bool(
                plan.get(
                    "safe"
                )
            ),
    }

    atomic_write(
        CONTROL_SL_STATE_FILE,
        control_state,
    )

    return actions > 0


def process_symbol(
    symbol,
    allow_new_entry=True,
):
    symbol = u(symbol)

    if not symbol:
        return False

    position = open_position(
        symbol
    )

    if position:

        return manage_owned_position(
            position
        )

    # Dynamic Top-N은 신규 진입에만 적용.
    # Top-N 밖 기존 포지션은 위 manage_owned_position()에서 계속 관리한다.
    # 포지션이 없는 Top-N 밖 심볼은 STATE re-entry와 EVENT entry 모두 금지.
    if not allow_new_entry:
        log_rl(
            "UNIVERSE_TOPN_SKIP",
            symbol,
            (
                f"[주문][RUNNER][ENTRY_SKIP] "
                f"{symbol} "
                f"reason=OUTSIDE_DYNAMIC_TOPN "
                f"top_n={strategy_universe_top_n()} "
                f"policy={POLICY}"
            ),
            sec=300,
        )
        return False

    # --------------------------------------------------------
    # CURRENT STRATEGY STATE RE-ENTRY
    #
    # Trailing / Control SL 청산 뒤 STATE_WAIT은 유지하지만,
    # 재진입 전략/레버리지는 과거 OWNER 값을 재사용하지 않는다.
    #
    # 현재 strategy_config.json의 활성 프레임 설정을 다시 읽어서
    # 현재 전략 STATE가 기존 포지션 방향과 같을 때만 재진입한다.
    #
    # 예:
    #   과거 OWNER : 12H / MACD+STOCH
    #   현재 설정  : 12H / EMA_CROSS+SAR / 10x
    #
    #   trailing exit
    #       -> STATE_WAIT
    #       -> 현재 EMA_CROSS+SAR STATE 검증
    #       -> 같은 방향이면 EMA_CROSS+SAR owner로 재진입
    #
    # 일반 FLAT 신규진입은 기존 EVENT 경로 유지.
    # --------------------------------------------------------

    trail_state = json_load(
        TRAIL_STATE_FILE,
        {},
    )

    if not isinstance(
        trail_state,
        dict,
    ):
        trail_state = {}

    wait = trail_state.get(
        symbol
    )

    if (
        isinstance(
            wait,
            dict,
        )
        and s(
            wait.get(
                "status"
            )
        )
        == "STATE_WAIT"
    ):
        exit_ts = i(
            wait.get(
                "exit_ts"
            ),
            0,
        )

        now = now_ts()

        # 백테스트 hourly timeline과 동일하게
        # 청산된 시간대의 다음 1H부터 STATE 재평가.
        first_state_ts = (
            ((exit_ts // 3600) + 1) * 3600
            if exit_ts > 0
            else now
        )

        retry_state_ts = i(
            wait.get(
                "next_state_check_ts"
            ),
            0,
        )

        next_state_ts = max(
            first_state_ts,
            retry_state_ts,
        )

        if now < next_state_ts:

            log_rl(
                "STATE_REENTRY_HOUR_WAIT",
                symbol,
                (
                    f"[주문][RUNNER][STATE_WAIT] "
                    f"{symbol} "
                    f"reason=NEXT_1H_NOT_READY "
                    f"exit_ts={exit_ts} "
                    f"next_ts={next_state_ts} "
                    f"policy={POLICY}"
                ),
                sec=60,
            )

        else:
            # STATE_WAIT의 과거 frame은
            # 어떤 시간 프레임의 추세를 이어갈지에만 사용.
            frame = u(
                wait.get(
                    "entry_tf"
                )
            )

            wait_side = norm_side(
                wait.get(
                    "side"
                )
            )

            # ------------------------------------------------
            # 현재 strategy_config를 매번 다시 읽는다.
            # 과거 wait.entry_strategy / entry_leverage 사용 금지.
            # ------------------------------------------------

            strategy_cfg = json_load(
                "/home/ec2-user/bot/strategy_config.json",
                {},
            )

            frames_cfg = {}

            if isinstance(
                strategy_cfg,
                dict,
            ):
                candidate_frames = (
                    strategy_cfg.get(
                        "frames"
                    )
                )

                if isinstance(
                    candidate_frames,
                    dict,
                ):
                    frames_cfg = candidate_frames

            frame_cfg = (
                frames_cfg.get(
                    frame,
                    {}
                )
                if frame
                else {}
            )

            if not isinstance(
                frame_cfg,
                dict,
            ):
                frame_cfg = {}

            enabled = bool(
                frame_cfg.get(
                    "enabled",
                    False,
                )
            )

            raw_strategies = (
                frame_cfg.get(
                    "strategies",
                    []
                )
            )

            current_strategies = []

            if isinstance(
                raw_strategies,
                (list, tuple),
            ):
                for item in raw_strategies:
                    name = u(item)

                    if name:
                        current_strategies.append(
                            name
                        )

            elif raw_strategies:
                name = u(
                    raw_strategies
                )

                if name:
                    current_strategies.append(
                        name
                    )

            strategy = "+".join(
                current_strategies
            )

            current_leverage = i(
                frame_cfg.get(
                    "leverage"
                ),
                0,
            )

            # 현재 설정에서 해당 frame이 더 이상 활성 전략이 아니면
            # 과거 STATE_WAIT을 폐기하고 정상 EVENT 경로로 넘긴다.
            if (
                not frame
                or not enabled
                or not strategy
                or current_leverage <= 0
                or wait_side not in (
                    "BUY",
                    "SELL",
                )
            ):
                trail_state.pop(
                    symbol,
                    None,
                )

                atomic_write(
                    TRAIL_STATE_FILE,
                    trail_state,
                )

                logger.info(
                    f"[주문][RUNNER][STATE_WAIT_DROP] "
                    f"{symbol} "
                    f"frame={frame} "
                    f"enabled={int(enabled)} "
                    f"strategy={strategy} "
                    f"leverage={current_leverage} "
                    f"reason=CURRENT_CONFIG_INVALID "
                    f"policy={POLICY}"
                )

            else:
                # ------------------------------------------------
                # 반드시 현재 활성 전략으로 STATE 재평가.
                # ------------------------------------------------

                result = (
                    gate.evaluate_owned_position(
                        symbol,
                        frame,
                        strategy,
                        state_mode=True,
                    )
                )

                if isinstance(
                    result,
                    dict,
                ):
                    state_side = norm_side(
                        result.get(
                            "side"
                        )
                    )

                    allow = (
                        i(
                            result.get(
                                "allow"
                            ),
                            0,
                        )
                        == 1
                    )

                    # 현재 전략 STATE가 반대 방향이면
                    # 이전 추세의 STATE_WAIT 종료.
                    if (
                        allow
                        and state_side
                        and state_side != wait_side
                    ):
                        trail_state.pop(
                            symbol,
                            None,
                        )

                        atomic_write(
                            TRAIL_STATE_FILE,
                            trail_state,
                        )

                        logger.info(
                            f"[주문][RUNNER][STATE_CANCEL] "
                            f"{symbol} "
                            f"old_side={wait_side} "
                            f"new_state={state_side} "
                            f"frame={frame} "
                            f"strategy={strategy} "
                            f"leverage={current_leverage} "
                            f"reason=CURRENT_STRATEGY_OPPOSITE "
                            f"policy={POLICY}"
                        )

                    # 현재 전략 STATE가 기존 방향과 같으면
                    # 현재 전략 OWNER로 재진입.
                    elif (
                        allow
                        and state_side == wait_side
                    ):
                        result = dict(
                            result
                        )

                        # execute_entry가 DB에 현재 전략을
                        # 확실하게 저장하도록 명시적으로 덮어쓴다.
                        result[
                            "frame"
                        ] = frame

                        result[
                            "strategy"
                        ] = strategy

                        result[
                            "entry_tf"
                        ] = frame

                        result[
                            "entry_strategy"
                        ] = strategy

                        result[
                            "leverage"
                        ] = int(
                            current_leverage
                        )

                        base_signal_id = s(
                            result.get(
                                "signal_id"
                            )
                        )

                        # 같은 current-strategy candle에서
                        # 여러 번 trailing/reentry가 가능하도록
                        # exit_ts를 signal id에 포함.
                        result[
                            "signal_id"
                        ] = (
                            f"{base_signal_id}|"
                            f"REENTRY_EXIT_{exit_ts}"
                        )

                        result[
                            "reason"
                        ] = (
                            "CURRENT_STATE_REENTRY"
                        )

                        logger.info(
                            f"[주문][RUNNER][STATE_REENTRY_ALLOW] "
                            f"{symbol} "
                            f"side={wait_side} "
                            f"frame={frame} "
                            f"strategy={strategy} "
                            f"leverage={current_leverage} "
                            f"source=CURRENT_CONFIG "
                            f"policy={POLICY}"
                        )

                        ok = execute_entry(
                            symbol,
                            result,
                            owned_reversal=False,
                            state_reentry=True,
                        )

                        if ok:
                            latest = json_load(
                                TRAIL_STATE_FILE,
                                {},
                            )

                            if isinstance(
                                latest,
                                dict,
                            ):
                                current_wait = (
                                    latest.get(
                                        symbol
                                    )
                                )

                                if (
                                    isinstance(
                                        current_wait,
                                        dict,
                                    )
                                    and s(
                                        current_wait.get(
                                            "status"
                                        )
                                    )
                                    == "STATE_WAIT"
                                    and i(
                                        current_wait.get(
                                            "exit_ts"
                                        ),
                                        0,
                                    )
                                    == exit_ts
                                ):
                                    latest.pop(
                                        symbol,
                                        None,
                                    )

                                    atomic_write(
                                        TRAIL_STATE_FILE,
                                        latest,
                                    )

                            logger.info(
                                f"[주문][RUNNER][STATE_REENTRY_OK] "
                                f"{symbol} "
                                f"side={wait_side} "
                                f"frame={frame} "
                                f"strategy={strategy} "
                                f"leverage={current_leverage} "
                                f"source=CURRENT_CONFIG "
                                f"policy={POLICY}"
                            )

                        else:
                            next_check = (
                                ((now // 3600) + 1)
                                * 3600
                            )

                            latest = json_load(
                                TRAIL_STATE_FILE,
                                {},
                            )

                            if isinstance(
                                latest,
                                dict,
                            ):
                                current_wait = (
                                    latest.get(
                                        symbol
                                    )
                                )

                                if (
                                    isinstance(
                                        current_wait,
                                        dict,
                                    )
                                    and s(
                                        current_wait.get(
                                            "status"
                                        )
                                    )
                                    == "STATE_WAIT"
                                    and i(
                                        current_wait.get(
                                            "exit_ts"
                                        ),
                                        0,
                                    )
                                    == exit_ts
                                ):
                                    current_wait[
                                        "next_state_check_ts"
                                    ] = next_check

                                    current_wait[
                                        "updated_ts"
                                    ] = now

                                    latest[
                                        symbol
                                    ] = current_wait

                                    atomic_write(
                                        TRAIL_STATE_FILE,
                                        latest,
                                    )

                            log_rl(
                                "STATE_REENTRY_SKIP",
                                symbol,
                                (
                                    f"[주문][RUNNER][STATE_REENTRY_SKIP] "
                                    f"{symbol} "
                                    f"side={wait_side} "
                                    f"frame={frame} "
                                    f"strategy={strategy} "
                                    f"leverage={current_leverage} "
                                    f"policy={POLICY}"
                                ),
                                sec=30,
                            )

                        # 같은 loop 안에서 EVENT 주문까지
                        # 중복 실행하지 않는다.
                        return ok

                    else:
                        next_check = (
                            ((now // 3600) + 1)
                            * 3600
                        )

                        latest = json_load(
                            TRAIL_STATE_FILE,
                            {},
                        )

                        if isinstance(
                            latest,
                            dict,
                        ):
                            current_wait = (
                                latest.get(
                                    symbol
                                )
                            )

                            if (
                                isinstance(
                                    current_wait,
                                    dict,
                                )
                                and s(
                                    current_wait.get(
                                        "status"
                                    )
                                )
                                == "STATE_WAIT"
                                and i(
                                    current_wait.get(
                                        "exit_ts"
                                    ),
                                    0,
                                )
                                == exit_ts
                            ):
                                current_wait[
                                    "next_state_check_ts"
                                ] = next_check

                                current_wait[
                                    "updated_ts"
                                ] = now

                                latest[
                                    symbol
                                ] = current_wait

                                atomic_write(
                                    TRAIL_STATE_FILE,
                                    latest,
                                )

                        log_rl(
                            "STATE_REENTRY_NO_CONSENSUS",
                            symbol,
                            (
                                f"[주문][RUNNER][STATE_WAIT] "
                                f"{symbol} "
                                f"side={wait_side} "
                                f"frame={frame} "
                                f"strategy={strategy} "
                                f"leverage={current_leverage} "
                                f"reason=NO_CURRENT_STATE_CONSENSUS "
                                f"policy={POLICY}"
                            ),
                            sec=60,
                        )

    # --------------------------------------------------------
    # NORMAL EVENT ENTRY
    # --------------------------------------------------------

    candidate = (
        new_entry_candidate(
            symbol
        )
    )

    if not candidate:
        return False

    logger.info(
        f"[주문][RUNNER][GATE_ALLOW] "
        f"{symbol} "
        f"side={candidate.get('side')} "
        f"frame={candidate.get('frame')} "
        f"strategy="
        f"{candidate.get('strategy')} "
        f"signal_id="
        f"{candidate.get('signal_id')} "
        f"policy={POLICY}"
    )

    ok = execute_entry(
        symbol,
        candidate,
        owned_reversal=False,
        state_reentry=False,
    )

    # 새 EVENT가 먼저 진입했으면 stale STATE_WAIT 제거.
    if ok:
        latest = json_load(
            TRAIL_STATE_FILE,
            {},
        )

        if isinstance(
            latest,
            dict,
        ):
            stale_wait = latest.get(
                symbol
            )

            if (
                isinstance(
                    stale_wait,
                    dict,
                )
                and s(
                    stale_wait.get(
                        "status"
                    )
                )
                == "STATE_WAIT"
            ):
                latest.pop(
                    symbol,
                    None,
                )

                atomic_write(
                    TRAIL_STATE_FILE,
                    latest,
                )

    return ok


# ============================================================
# MAIN
# ============================================================

def main_loop_once():
    reload_config()
    cleanup_expired_pending()

    # Binance 최종 진실값
    refresh_positions(
        write_cache=True,
    )

    recover_pending_for_active_positions()

    # --------------------------------------------------------
    # CONTROL SL
    #
    # 매 확정 1H마다 1회.
    # Trailing 3/1.5와 독립된 계좌 생존 안전장치.
    # --------------------------------------------------------

    try:

        if control_sl_once():

            # 실제 청산이 있었다면 캐시를 즉시 갱신.
            refresh_positions(
                write_cache=True,
            )

    except Exception:

        logger.error(
            f"[주문][CONTROL_SL][EXCEPTION] "
            f"policy={POLICY}",
            exc_info=True,
        )

    ranked_symbols = config_symbols()
    entry_symbols = active_entry_symbols(
        ranked_symbols
    )
    entry_set = set(
        entry_symbols
    )

    # Top-N 밖이어도 현재 실제 보유 포지션은 계속 trailing / owner / exit 관리.
    managed_only = sorted(
        {
            u(symbol)
            for symbol in POSITION_CACHE
            if (
                u(symbol)
                and u(symbol) not in entry_set
            )
        }
    )

    symbols = (
        list(entry_symbols)
        + managed_only
    )

    if not symbols:

        log_rl(
            "NO_SYMBOLS",
            "CONFIG",
            (
                f"[주문][RUNNER][NO_SYMBOLS] "
                f"policy={POLICY}"
            ),
        )

        return

    actions = 0
    errors = 0

    for symbol in symbols:

        try:

            if process_symbol(
                symbol,
                allow_new_entry=(
                    symbol in entry_set
                ),
            ):
                actions += 1

        except Exception:

            errors += 1

            logger.error(
                f"[주문][RUNNER][SYMBOL_EXCEPTION] "
                f"{u(symbol)} "
                f"policy={POLICY}",
                exc_info=True,
            )

    logger.info(
        f"[주문][RUNNER][LOOP_DONE] "
        f"ranked={len(ranked_symbols)} "
        f"entry_symbols={len(entry_symbols)} "
        f"top_n={strategy_universe_top_n()} "
        f"managed_only={len(managed_only)} "
        f"positions="
        f"{len(POSITION_CACHE)} "
        f"actions={actions} "
        f"errors={errors} "
        f"policy={POLICY}"
    )



def handle_signal(
    signum,
    frame,
):
    global RUNNING

    RUNNING = False

    logger.info(
        f"[주문][RUNNER][STOP_SIGNAL] "
        f"sig={signum} "
        f"policy={POLICY}"
    )


def acquire_lock():
    fd = os.open(
        LOCK_FILE,
        os.O_CREAT
        |
        os.O_RDWR,
        0o644,
    )

    try:

        fcntl.flock(
            fd,
            fcntl.LOCK_EX
            |
            fcntl.LOCK_NB,
        )

    except BlockingIOError:

        raise SystemExit(
            "runner already running"
        )

    return fd


def main():
    lock_fd = acquire_lock()

    signal.signal(
        signal.SIGTERM,
        handle_signal,
    )

    signal.signal(
        signal.SIGINT,
        handle_signal,
    )

    reload_config(
        force=True,
    )

    ensure_history_tables()

    logger.info(
        f"[주문][RUNNER][START] "
        f"policy={POLICY} "
        f"dry_run={int(DRY_RUN)} "
        f"run_once={int(RUN_ONCE)}"
    )

    try:

        while RUNNING:

            main_loop_once()

            if RUN_ONCE:
                break

            time.sleep(
                max(
                    1.0,
                    LOOP_SLEEP_SEC,
                )
            )

    finally:

        try:
            fcntl.flock(
                lock_fd,
                fcntl.LOCK_UN,
            )

            os.close(
                lock_fd
            )

        except Exception:
            pass

        logger.info(
            f"[주문][RUNNER][STOP] "
            f"policy={POLICY}"
        )


if __name__ == "__main__":
    main()
