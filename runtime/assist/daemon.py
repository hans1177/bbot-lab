#!/usr/bin/env python3
# 파일명: daemon.py
# -*- coding: utf-8 -*-
# 파일명: assist/daemon.py

"""
ASSIST STRATEGY MATERIAL PRODUCER

역할:
- 거래 판단하지 않음
- Cycle 판단하지 않음
- EMA touch 판단하지 않음
- 진입/청산 판단하지 않음
- 확정봉 기준 7개 전략 신호만 생성
- Gate가 사용할 entry_frame_state.json 생성

전략:
MACD / STOCH / SHAPE / DIV / EMA_CROSS / OBV / SAR

프레임:
1H / 6H / 12H / 1D / 1W / 1M
"""

import os
import time
import json
import math
import logging
import tempfile
from datetime import datetime, timezone

import pandas as pd

from assist.utils import (
    load_assist_config,
    load_confirmed_interval_df,
)
import importlib.util
import inspect
import re



# ============================================================
# CONFIG
# ============================================================

BASE_DIR = os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot")
STATE_DIR = os.path.join(BASE_DIR, "state")
LOG_FILE = os.path.join(BASE_DIR, "logs", "runner.log")
ENTRY_FRAME_FILE = os.path.join(STATE_DIR, "entry_frame_state.json")
SIGNAL_ORIGIN_FILE = os.path.join(STATE_DIR, "signal_origin_state.json")

POLICY = "ASSIST_CONFIG_DRIVEN_MATERIAL_V2_20260826"
SCHEMA = "ENTRY_FRAME_STRATEGY_MATERIAL_V1"

LOOP_SEC = max(
    30,
    int(float(os.getenv("ASSIST_6TF_LOOP_SEC", "60") or 60)),
)

LOG_SEC = max(
    60,
    int(float(os.getenv("ASSIST_6TF_LOG_SEC", "300") or 300)),
)

MAX_SYMBOLS = max(
    1,
    int(float(os.getenv("ASSIST_6TF_MAX_SYMBOLS", "80") or 80)),
)

TIMEFRAMES = (
    "1h",
    "6h",
    "12h",
    "1d",
    "1w",
    "1M",
)


# 전략 자체가 필요한 최소 row 검사를 수행하므로
# 여기서는 데이터 존재 여부만 막는다.
MIN_ROWS = {
    "1h": 2,
    "6h": 2,
    "12h": 2,
    "1d": 2,
    "1w": 2,
    "1M": 2,
}


# ============================================================
# LOG
# ============================================================

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("assist")
logger.setLevel(logging.INFO)
logger.handlers.clear()

_file_handler = logging.FileHandler(
    LOG_FILE,
    encoding="utf-8",
)

_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )
)

logger.addHandler(_file_handler)
logger.propagate = False

_last_log = {}


STRATEGY_CONFIG_FILE = os.path.join(BASE_DIR, "strategy_config.json")

STRATEGY_DIR = os.path.join(BASE_DIR, "assist", "strategies")

_TF_CONFIG_KEY = {
    "1h": "1H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
    "1M": "1M",
}

_strategy_config_cache = {"mtime": None, "data": {}}

_strategy_module_cache = {}

def log_once(key, message, sec=LOG_SEC, level=logging.INFO):
    now = time.time()
    last = float(_last_log.get(key, 0.0) or 0.0)

    if now - last < max(1, int(sec)):
        return

    _last_log[key] = now
    logger.log(level, message)

    if len(_last_log) > 3000:
        cutoff = now - max(LOG_SEC * 3, 900)

        for old_key, old_ts in list(_last_log.items()):
            if old_ts < cutoff:
                _last_log.pop(old_key, None)


# ============================================================
# COMMON
# ============================================================

def now_ts():
    return int(time.time())


def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def text(value, default=""):
    try:
        result = str(
            value if value is not None else ""
        ).strip()

        return result if result else str(default)

    except Exception:
        return str(default)


def number(value, default=0.0):
    try:
        result = float(value)

        if math.isnan(result) or math.isinf(result):
            return float(default)

        return result

    except Exception:
        return float(default)


def atomic_write(path, payload):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=directory,
        encoding="utf-8",
        prefix=".tmp_",
    ) as handle:

        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

        handle.flush()
        os.fsync(handle.fileno())

        temp_path = handle.name

    os.replace(temp_path, path)


# ============================================================
# SYMBOLS
# ============================================================

def _symbol_enabled(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, dict):
        if "enabled" in value:
            return bool(value.get("enabled"))

        if "active" in value:
            return bool(value.get("active"))

        return True

    if value is None:
        return False

    return bool(value)


def load_symbols():
    config = load_assist_config()

    raw = (
        config.get("SYMBOLS", {})
        if isinstance(config, dict)
        else {}
    )

    symbols = []

    if isinstance(raw, dict):

        for symbol, setting in raw.items():

            if not _symbol_enabled(setting):
                continue

            symbols.append(symbol)

    elif isinstance(raw, (list, tuple)):

        symbols.extend(raw)

    elif isinstance(raw, str):

        symbols.extend(
            x.strip()
            for x in raw.split(",")
            if x.strip()
        )

    normalized = []

    for symbol in symbols:

        value = text(symbol).upper()

        if not value.endswith("USDT"):
            continue

        if value not in normalized:
            normalized.append(value)

    return normalized[:MAX_SYMBOLS]


# ============================================================
# FRAME
# ============================================================

def load_confirmed_frame(symbol, interval):
    try:
        return load_confirmed_interval_df(
            symbol,
            interval,
            min_rows=MIN_ROWS.get(interval, 2),
            log_tag=None,
        )

    except Exception as error:

        log_once(
            f"FRAME_LOAD:{symbol}:{interval}",
            (
                f"[주문][ASSIST][FRAME_LOAD_FAIL] "
                f"{symbol} tf={interval} "
                f"err={type(error).__name__}:{error}"
            ),
            sec=300,
            level=logging.ERROR,
        )

        return None


def frame_open_time(df):
    try:
        if df is None or len(df) == 0:
            return ""

        value = pd.to_datetime(
            df["open_time"].iloc[-1],
            utc=True,
            errors="coerce",
        )

        if pd.isna(value):
            return ""

        return value.isoformat()

    except Exception:
        return ""


# ============================================================
# STRATEGY SIGNAL
# ============================================================

def empty_signal(reason="NOT_READY"):
    return {
        "signal": "wait",
        "strength_long": 0.0,
        "strength_short": 0.0,
        "reason": reason,
    }


def normalize_signal_name(value):
    raw = text(value, "wait").lower()

    if raw in (
        "long",
        "buy",
        "bull",
        "bullish",
    ):
        return "long"

    if raw in (
        "short",
        "sell",
        "bear",
        "bearish",
    ):
        return "short"

    return "wait"


def safe_signal(fn, df, name, interval=None):
    if df is None or len(df) == 0:
        return empty_signal(
            f"{name}_NO_DATA"
        )

    try:
        params = strategy_params(name, interval)

        try:
            signature = inspect.signature(fn)
            positional = [
                p for p in signature.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            has_varargs = any(
                p.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
                for p in signature.parameters.values()
            )
            accepts_params = has_varargs or len(positional) >= 2
        except Exception:
            accepts_params = False

        result = fn(df, params) if accepts_params else fn(df)

        if not isinstance(result, dict):
            return empty_signal(
                f"{name}_BAD_RESULT"
            )

        long_strength = max(
            0.0,
            number(
                result.get("strength_long"),
                0.0,
            ),
        )

        short_strength = max(
            0.0,
            number(
                result.get("strength_short"),
                0.0,
            ),
        )

        signal = normalize_signal_name(
            result.get("signal")
        )

        if signal == "wait":

            if (
                long_strength > 0
                and short_strength <= 0
            ):
                signal = "long"

            elif (
                short_strength > 0
                and long_strength <= 0
            ):
                signal = "short"

        # EMA_CROSS STATE 등 연속 상태 보존
        state = normalize_signal_name(
            result.get("state")
        )

        return {
            "signal": signal,
            "state": state,
            "strength_long": long_strength,
            "strength_short": short_strength,
            "reason": text(
                result.get("reason"),
                "NONE",
            ),
        }

    except Exception as error:

        return {
            "signal": "wait",
            "state": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": (
                f"{name}_ERROR:"
                f"{type(error).__name__}:"
                f"{str(error)[:120]}"
            ),
        }


# ============================================================
# TF MATERIAL
# ============================================================

def build_tf_material(symbol, interval):
    config_before = json.dumps(load_strategy_config(), sort_keys=True)
    source_before = {}
    for name in configured_strategy_names(interval):
        path = _strategy_path(name)
        if path:
            try:
                stat = os.stat(path)
                source_before[name] = (path, stat.st_mtime_ns, stat.st_size)
            except OSError:
                source_before[name] = None
    df = load_confirmed_frame(
        symbol,
        interval,
    )

    confirmed_time = frame_open_time(df)

    confirmed_close = 0.0
    try:
        if df is not None and not df.empty:
            confirmed_close = number(df.iloc[-1].get("close"), 0.0)
    except Exception:
        confirmed_close = 0.0

    row = {
        "interval": interval,
        "rows": (
            0
            if df is None
            else int(len(df))
        ),
        "confirmed_open_time": confirmed_time,
        "confirmed_close": confirmed_close,
        "candle_id": (
            f"{symbol}|{interval}|{confirmed_time}"
            if confirmed_time
            else ""
        ),
    }

    for strategy_name, strategy_fn in load_strategies(interval).items():

        row[strategy_name] = safe_signal(
            strategy_fn,
            df,
            strategy_name.upper(),
            interval=interval,
        )

    # 현재 계산 결과의 인스턴스 버전을 함께 기록해 오래된 자료의 혼용을 차단한다.
    config = load_strategy_config()
    changed = config_before != json.dumps(config, sort_keys=True)
    for name, previous in source_before.items():
        path = _strategy_path(name)
        try:
            stat = os.stat(path)
            current = (path, stat.st_mtime_ns, stat.st_size)
        except (OSError, TypeError):
            current = None
        if previous != current:
            changed = True
    if changed:
        log_once(
            f"STRATEGY_MATERIAL_CHANGED:{interval}",
            f"[주문][ASSIST][MATERIAL_RETRY] {interval} 설정·소스 변경으로 신호 재계산 대기",
            sec=300,
            level=logging.WARNING,
        )
        # 전환 중 계산한 신호는 대표 전략에도 사용하지 않는다.
        return {key: value for key, value in row.items() if key not in {name.lower() for name in source_before}}
    tf_name = _TF_CONFIG_KEY.get(interval)
    instance_versions = {}
    for instance_id, instance in (((config.get("r4_runtime") or {}).get("strategy_instances") or {}).items()):
        if not isinstance(instance, dict) or instance.get("approved_for_server_release") is not True:
            continue
        if str(instance.get("timeframe") or "").upper() != str(tf_name or "").upper():
            continue
        components = [part.strip().lower() for part in str(instance.get("strategy_id") or "").split("+") if part.strip()]
        if components and all(isinstance(row.get(part), dict) for part in components):
            instance_versions[str(instance_id)] = str(instance.get("exact_strategy_version") or "")
    row["r4_instance_versions"] = instance_versions
    return row


def build_all_materials(symbol):
    return {
        interval: build_tf_material(
            symbol,
            interval,
        )
        for interval in TIMEFRAMES
    }


# ============================================================
# SYMBOL STATE
# ============================================================

def build_symbol_material(symbol):
    materials = build_all_materials(symbol)

    return {
        "symbol": symbol,
        "schema": SCHEMA,
        "policy": POLICY,
        "updated_ts": now_ts(),
        "updated_at": utc_iso(),
        "materials": materials,
    }


# ============================================================
# SIGNAL ORIGIN STATE
# ============================================================

def _load_signal_origin_state():
    try:
        with open(SIGNAL_ORIGIN_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def attach_signal_origins(output):
    """
    각 Strategy의 동일 방향 상태가 계속되는 동안 최초 확정봉 시간/가격을 보존한다.
    이 값은 Late Entry Replay의 원점이며 주문 판단 자체를 변경하지 않는다.
    """
    prior = _load_signal_origin_state()
    next_state = {}

    symbols = output.get("symbols") if isinstance(output, dict) else {}
    if not isinstance(symbols, dict):
        return output

    for symbol, symbol_row in symbols.items():
        materials = symbol_row.get("materials") if isinstance(symbol_row, dict) else {}
        if not isinstance(materials, dict):
            continue
        for interval, material in materials.items():
            if not isinstance(material, dict):
                continue
            confirmed_time = text(material.get("confirmed_open_time"))
            confirmed_close = number(material.get("confirmed_close"), 0.0)
            for strategy_name in configured_strategy_names(interval):
                key = str(strategy_name).strip().lower()
                sig = material.get(key)
                if not isinstance(sig, dict):
                    continue
                direction = normalize_signal_name(sig.get("signal") or sig.get("state"))
                if direction not in ("long", "short"):
                    sig["signal_origin_time"] = ""
                    sig["signal_origin_price"] = 0.0
                    continue
                state_key = f"{symbol}|{interval}|{key}"
                prev = prior.get(state_key) if isinstance(prior.get(state_key), dict) else {}
                instances = (((load_strategy_config().get("r4_runtime") or {}).get("strategy_instances") or {}))
                version_map = material.get("r4_instance_versions") or {}
                binding = sorted(
                    str(instance_id) + ":" + str(version)
                    for instance_id, version in version_map.items()
                    if key.upper() in str((instances.get(instance_id) or {}).get("strategy_id") or "").upper().split("+")
                )
                if (text(prev.get("direction")).lower() == direction
                    and ("version_binding" not in prev or prev.get("version_binding") == binding)):
                    origin_time = text(prev.get("signal_origin_time"), confirmed_time)
                    origin_price = number(prev.get("signal_origin_price"), confirmed_close)
                else:
                    origin_time = confirmed_time
                    origin_price = confirmed_close
                sig["signal_origin_time"] = origin_time
                sig["signal_origin_price"] = origin_price
                next_state[state_key] = {
                    "version_binding": binding,
                    "direction": direction,
                    "signal_origin_time": origin_time,
                    "signal_origin_price": origin_price,
                    "updated_ts": now_ts(),
                }

    atomic_write(SIGNAL_ORIGIN_FILE, next_state)
    return output


# ============================================================
# STATE
# ============================================================

def build_entry_frame_state():
    symbols = load_symbols()

    output = {
        "schema": SCHEMA,
        "policy": POLICY,
        "updated_ts": now_ts(),
        "updated_at": utc_iso(),
        "source": "assist-daemon",
        "role": "STRATEGY_MATERIAL_ONLY",
        "timeframes": list(TIMEFRAMES),
        "strategies": [name.lower() for name in configured_strategy_names()],
        "symbols": {},
        "symbol_count": len(symbols),
        "ok_count": 0,
        "fail_count": 0,
    }

    for symbol in symbols:

        try:

            output["symbols"][symbol] = (
                build_symbol_material(symbol)
            )

            output["ok_count"] += 1

        except Exception as error:

            output["symbols"][symbol] = {
                "symbol": symbol,
                "schema": SCHEMA,
                "policy": POLICY,
                "updated_ts": now_ts(),
                "error": (
                    f"{type(error).__name__}:"
                    f"{str(error)[:200]}"
                ),
            }

            output["fail_count"] += 1

            logger.error(
                (
                    f"[주문][ASSIST][SYMBOL_FAIL] "
                    f"{symbol} "
                    f"err={type(error).__name__}:{error}"
                ),
                exc_info=True,
            )

    return attach_signal_origins(output)


# ============================================================
# LOOP
# ============================================================

def daemon_loop():

    logger.info(
        (
            f"[주문][ASSIST][START] "
            f"role=STRATEGY_MATERIAL_ONLY "
            f"tf=1H,6H,12H,1D,1W,1M "
            f"strategies={','.join(configured_strategy_names()) or 'NONE'} "
            f"loop_sec={LOOP_SEC} "
            f"policy={POLICY}"
        )
    )

    while True:

        started = time.time()

        try:

            state = build_entry_frame_state()

            atomic_write(
                ENTRY_FRAME_FILE,
                state,
            )

            log_once(
                "STATE_WRITE",
                (
                    f"[주문][ASSIST][STATE] "
                    f"symbols={state.get('symbol_count')} "
                    f"ok={state.get('ok_count')} "
                    f"fail={state.get('fail_count')} "
                    f"elapsed="
                    f"{time.time() - started:.2f}s "
                    f"policy={POLICY}"
                ),
            )

        except Exception as error:

            log_once(
                f"LOOP_FAIL:{type(error).__name__}",
                (
                    f"[주문][ASSIST][LOOP_FAIL] "
                    f"err={type(error).__name__}:{error} "
                    f"policy={POLICY}"
                ),
                sec=60,
                level=logging.ERROR,
            )

        elapsed = time.time() - started

        time.sleep(
            max(
                1.0,
                LOOP_SEC - elapsed,
            )
        )


def main():
    daemon_loop()


def load_strategy_config():
    previous = _strategy_config_cache.get("data")
    if not isinstance(previous, dict):
        previous = {}
    try:
        mtime = os.path.getmtime(STRATEGY_CONFIG_FILE)
        if _strategy_config_cache.get("mtime") == mtime:
            return previous
        with open(STRATEGY_CONFIG_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return previous
        _strategy_config_cache["mtime"] = mtime
        _strategy_config_cache["data"] = data
        return data
    except Exception:
        return previous

def _normalize_strategy_name(value):
    name = text(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]+", name or ""):
        return ""
    return name

def configured_strategy_names(interval=None):
    config = load_strategy_config()
    frames = config.get("frames") or {}
    if not isinstance(frames, dict):
        return []

    wanted_tf = _TF_CONFIG_KEY.get(interval) if interval else None
    out = []

    for tf_name, row in frames.items():
        if wanted_tf and str(tf_name).upper() != wanted_tf.upper():
            continue
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        raw = row.get("strategies") or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split("+") if x.strip()]
        if not isinstance(raw, (list, tuple)):
            continue
        for item in raw:
            name = _normalize_strategy_name(item)
            if name and name not in out:
                out.append(name)
    instances = ((config.get("r4_runtime") or {}).get("strategy_instances") or {})
    for instance in instances.values():
        if not isinstance(instance, dict) or instance.get("approved_for_server_release") is not True:
            continue
        tf_name = str(instance.get("timeframe") or "").upper()
        if wanted_tf and tf_name != wanted_tf.upper():
            continue
        frame_row = frames.get(tf_name) or {}
        if not frame_row.get("enabled"):
            continue
        # 신규 진입이 꺼져도 기존 보유의 반대 신호·재진입 상태 계산은 보존한다.
        for part in str(instance.get("strategy_id") or "").split("+"):
            name = _normalize_strategy_name(part)
            if name and name not in out:
                out.append(name)
    return out

def strategy_params(name, interval=None):
    config = load_strategy_config()
    normalized = _normalize_strategy_name(name)
    merged = {}

    all_params = config.get("strategy_params") or {}
    if isinstance(all_params, dict):
        row = all_params.get(normalized)
        if row is None:
            row = all_params.get(str(name).lower())
        if isinstance(row, dict):
            merged.update(row)

    tf_name = _TF_CONFIG_KEY.get(interval) if interval else None
    frames = config.get("frames") or {}
    if tf_name and isinstance(frames, dict):
        frame_row = frames.get(tf_name) or {}
        if isinstance(frame_row, dict):
            frame_params = frame_row.get("strategy_params") or {}
            if isinstance(frame_params, dict):
                row = frame_params.get(normalized)
                if row is None:
                    row = frame_params.get(str(name).lower())
                if isinstance(row, dict):
                    merged.update(row)

    return merged

def _strategy_path(name):
    """Resolve configured strategy payload without guessing its filename.

    V2 legacy strategies still fall back to <name>.lower()+.py, while promoted
    V3 strategies use strategy_config.strategy_registry[name].file.
    """
    normalized = _normalize_strategy_name(name)
    if not normalized:
        return None

    filename = ""
    config = load_strategy_config()
    registry = config.get("strategy_registry") if isinstance(config, dict) else None
    row = registry.get(normalized) if isinstance(registry, dict) else None
    if isinstance(row, dict):
        raw = os.path.basename(str(row.get("file") or "").strip())
        if raw and raw.lower().endswith(".py"):
            filename = raw

    if not filename:
        filename = normalized.lower() + ".py"

    path = os.path.join(STRATEGY_DIR, filename)
    return path if os.path.isfile(path) else None

def _load_strategy_callable(name):
    normalized = _normalize_strategy_name(name)
    path = _strategy_path(normalized)
    if not normalized or not path:
        return None

    try:
        stat = os.stat(path)
        mtime = (path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        return None

    cached = _strategy_module_cache.get(normalized)
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        fn = cached.get("signal")
        return fn if callable(fn) else None

    try:
        module_name = f"assist_dynamic_strategy_{normalized.lower()}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "signal", None)
        if not callable(fn):
            return None
        loaded_stat = os.stat(path)
        if (path, loaded_stat.st_mtime_ns, loaded_stat.st_size) != mtime:
            return None
        _strategy_module_cache[normalized] = {
            "mtime": mtime,
            "signal": fn,
        }
        return fn
    except Exception as error:
        log_once(
            f"STRATEGY_LOAD:{normalized}",
            f"[주문][ASSIST][STRATEGY_LOAD_FAIL] {normalized} err={type(error).__name__}:{error}",
            sec=300,
            level=logging.ERROR,
        )
        return None

def load_strategies(interval=None):
    out = {}
    for name in configured_strategy_names(interval):
        fn = _load_strategy_callable(name)
        if callable(fn):
            out[name.lower()] = fn
    return out

if __name__ == "__main__":
    main()
