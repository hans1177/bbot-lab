#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: gate.py

"""
GATE - STRATEGY CONFIG DECISION LAYER

역할
- 전략 계산 안 함
- Cycle 사용 안 함
- NORMAL / REVERSAL / EMA_DIRECT 사용 안 함
- EMA touch 사용 안 함

입력
1) state/entry_frame_state.json
2) strategy_config.json

판단
- SINGLE:
    지정 전략 LONG  -> LONG
    지정 전략 SHORT -> SHORT
    그 외 HOLD

- PAIR:
    모든 전략 LONG  -> LONG
    모든 전략 SHORT -> SHORT
    불일치/WAIT      -> HOLD

출력
- frame
- strategy
- decision
- side
- signal_id
- confirmed_open_time
"""

import os
import json
import time
from typing import Any, Dict, List, Optional
import re


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot")

STATE_FILE = os.path.join(
    BASE_DIR,
    "state",
    "entry_frame_state.json",
)

STRATEGY_CONFIG_FILE = os.path.join(
    BASE_DIR,
    "strategy_config.json",
)

GATE_VERSION = "GATE_CONFIG_DRIVEN_V2_20260826"
GATE_POLICY = "TF_STRATEGY_CONSENSUS"

TF_MAP = {
    "30M": "30m",
    "1H": "1h",
    "6H": "6h",
    "12H": "12h",
    "1D": "1d",
    "1W": "1w",
    "1M": "1M",
}



# ============================================================
# JSON CACHE
# ============================================================

_state_cache = {
    "mtime": None,
    "data": None,
}

_config_cache = {
    "mtime": None,
    "data": None,
}


def _load_json_cached(path: str, cache: dict) -> dict:
    previous = (
        cache.get("data")
        if isinstance(
            cache.get("data"),
            dict,
        )
        else None
    )

    try:
        if not os.path.exists(path):
            return (
                previous
                if previous is not None
                else {}
            )

        mtime = os.path.getmtime(path)

        if (
            cache.get("mtime") == mtime
            and previous is not None
        ):
            return previous

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict,
        ):
            return (
                previous
                if previous is not None
                else {}
            )

        cache["mtime"] = mtime
        cache["data"] = data

        return data

    except Exception:
        return (
            previous
            if previous is not None
            else {}
        )


def load_state() -> dict:
    return _load_json_cached(
        STATE_FILE,
        _state_cache,
    )


def load_strategy_config() -> dict:
    return _load_json_cached(
        STRATEGY_CONFIG_FILE,
        _config_cache,
    )


# ============================================================
# NORMALIZE
# ============================================================

def text(value: Any, default: str = "") -> str:
    try:
        value = str(
            value if value is not None else ""
        ).strip()

        return value if value else default

    except Exception:
        return default


def normalize_symbol(symbol: str) -> str:
    return text(symbol).upper()


def normalize_tf(tf: str) -> str:
    raw = text(tf)

    if not raw:
        return ""

    upper = raw.upper()

    if upper in TF_MAP:
        return upper

    for config_tf, state_tf in TF_MAP.items():
        if raw == state_tf:
            return config_tf

        if raw.lower() == state_tf.lower():
            return config_tf

    return ""


def normalize_side(side: str) -> str:
    value = text(side).upper()

    if value in ("BUY", "LONG"):
        return "BUY"

    if value in ("SELL", "SHORT"):
        return "SELL"

    return ""


def normalize_strategy(strategy: str) -> str:
    value = text(strategy).upper()
    if not re.fullmatch(r"[A-Z0-9_]+", value or ""):
        return ""
    return value if value in configured_strategy_names(False) else ""


# ============================================================
# SIGNAL
# ============================================================

def signal_direction(row: dict) -> str:
    if not isinstance(row, dict):
        return "HOLD"

    signal = text(
        row.get("signal"),
        "wait",
    ).lower()

    if signal in (
        "long",
        "buy",
        "bull",
        "bullish",
    ):
        return "LONG"

    if signal in (
        "short",
        "sell",
        "bear",
        "bearish",
    ):
        return "SHORT"

    try:
        long_strength = float(
            row.get("strength_long") or 0.0
        )
    except Exception:
        long_strength = 0.0

    try:
        short_strength = float(
            row.get("strength_short") or 0.0
        )
    except Exception:
        short_strength = 0.0

    if (
        long_strength > 0
        and short_strength <= 0
    ):
        return "LONG"

    if (
        short_strength > 0
        and long_strength <= 0
    ):
        return "SHORT"

    return "HOLD"


# ============================================================
# CONFIG FRAME
# ============================================================

def get_frame_config(tf: str) -> dict:
    tf_name = normalize_tf(tf)

    config = load_strategy_config()
    frames = config.get("frames") or {}

    row = frames.get(tf_name)

    if not isinstance(row, dict):
        return {
            "frame": tf_name,
            "enabled": False,
            "strategies": [],
        }

    raw_strategies = row.get("strategies") or []

    if isinstance(raw_strategies, str):
        raw_strategies = [
            x.strip()
            for x in raw_strategies.split("+")
            if x.strip()
        ]

    strategies = []

    for item in raw_strategies:

        name = normalize_strategy(item)

        if (
            name
            and name not in strategies
        ):
            strategies.append(name)

    try:
        leverage = int(
            float(
                row.get("leverage", 0)
                or 0
            )
        )
    except Exception:
        leverage = 0

    return {
        "frame": tf_name,
        "enabled": bool(
            row.get("enabled", False)
        ),
        "strategies": strategies,
        "leverage": leverage,
    }


# ============================================================
# MATERIAL
# ============================================================

def get_symbol_state(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)

    state = load_state()

    symbols = state.get("symbols") or {}

    row = symbols.get(symbol)

    return row if isinstance(row, dict) else {}


def get_tf_material(
    symbol: str,
    tf: str,
) -> dict:

    symbol_state = get_symbol_state(symbol)

    materials = (
        symbol_state.get("materials")
        or {}
    )

    config_tf = normalize_tf(tf)

    state_tf = TF_MAP.get(
        config_tf,
        "",
    )

    row = materials.get(state_tf)

    return row if isinstance(row, dict) else {}


# ============================================================
# FRAME EVALUATION
# ============================================================

def evaluate_frame(
    symbol: str,
    tf: str,
    require_enabled: bool = True,
    strategy_instance_id: Optional[str] = None,
) -> dict:

    symbol = normalize_symbol(symbol)
    tf = normalize_tf(tf)

    base = {
        "gate_version": GATE_VERSION,
        "policy": GATE_POLICY,
        "symbol": symbol,
        "frame": tf,
        "enabled": False,
        "strategies": [],
        "strategy": "",
        "leverage": 0,
        "decision": "HOLD",
        "side": "",
        "allow": 0,
        "confirmed_open_time": "",
        "signal_origin_time": "",
        "signal_price": 0.0,
        "signal_id": "",
        "details": {},
        "reason": "",
    }

    if not symbol:
        base["reason"] = "INVALID_SYMBOL"
        return base

    if not tf:
        base["reason"] = "INVALID_FRAME"
        return base

    cfg = get_frame_config(tf)
    instance = None
    if strategy_instance_id:
        config = load_strategy_config()
        instance = (((config.get("r4_runtime") or {}).get("strategy_instances") or {}).get(str(strategy_instance_id)))
        if (not isinstance(instance, dict)
            or instance.get("approved_for_server_release") is not True
            or str(instance.get("timeframe") or "").upper() != tf
            or not str(instance.get("exact_strategy_version") or "")):
            base["reason"] = "EXACT_STRATEGY_INSTANCE_UNAVAILABLE"
            return base
        if (instance.get("enabled") is not True
            or instance.get("entry_enabled", True) is not True
            or instance.get("entry_blocked") is True):
            base["reason"] = "STRATEGY_INSTANCE_ENTRY_DISABLED"
            return base
        cfg = dict(cfg)
        cfg["strategies"] = _normalize_strategy_list(instance.get("strategy_id"))
        base["strategy_instance_id"] = str(strategy_instance_id)
        base["strategy_version"] = str(instance["exact_strategy_version"])

    enabled = bool(
        cfg.get("enabled")
    )

    strategies = (
        cfg.get("strategies")
        or []
    )

    base["enabled"] = enabled
    base["strategies"] = strategies
    base["strategy"] = "+".join(
        strategies
    )
    base["leverage"] = int(
        cfg.get("leverage", 0)
        or 0
    )

    if (
        require_enabled
        and not enabled
    ):
        base["reason"] = "FRAME_DISABLED"
        return base

    if not strategies:
        base["reason"] = "NO_STRATEGY"
        return base

    material = get_tf_material(
        symbol,
        tf,
    )

    if not material:
        base["reason"] = "NO_MATERIAL"
        return base

    if instance is not None:
        version_map = material.get("r4_instance_versions")
        version_map = version_map if isinstance(version_map, dict) else {}
        observed = version_map.get(str(strategy_instance_id))
        frame_row = (load_strategy_config().get("frames") or {}).get(tf) or {}
        representative = (
            str(frame_row.get("strategy_version") or "") == base["strategy_version"]
            and _normalize_strategy_list(frame_row.get("strategies")) == cfg["strategies"]
        )
        if (observed is not None and str(observed) != base["strategy_version"]) or (observed is None and not representative):
            base["reason"] = "EXACT_STRATEGY_MATERIAL_VERSION_UNAVAILABLE"
            return base

    candle_time = text(
        material.get(
            "confirmed_open_time"
        )
    )

    base["confirmed_open_time"] = (
        candle_time
    )

    directions = []

    details = {}

    for strategy in strategies:

        key = strategy_material_key(strategy)

        signal_row = material.get(key)

        direction = signal_direction(
            signal_row
        )

        directions.append(
            direction
        )

        details[strategy] = {
            "direction": direction,
            "signal": (
                signal_row.get("signal")
                if isinstance(signal_row, dict)
                else "wait"
            ),
            "strength_long": (
                signal_row.get(
                    "strength_long",
                    0.0,
                )
                if isinstance(signal_row, dict)
                else 0.0
            ),
            "strength_short": (
                signal_row.get(
                    "strength_short",
                    0.0,
                )
                if isinstance(signal_row, dict)
                else 0.0
            ),
            "reason": (
                signal_row.get(
                    "reason",
                    ""
                )
                if isinstance(signal_row, dict)
                else "NO_SIGNAL"
            ),
            "signal_origin_time": (
                signal_row.get("signal_origin_time", "")
                if isinstance(signal_row, dict) else ""
            ),
            "signal_origin_price": (
                signal_row.get("signal_origin_price", 0.0)
                if isinstance(signal_row, dict) else 0.0
            ),
        }

    base["details"] = details

    # --------------------------------------------------------
    # SINGLE / PAIR / N-strategy consensus
    # --------------------------------------------------------

    if (
        directions
        and all(
            x == "LONG"
            for x in directions
        )
    ):
        decision = "LONG"

    elif (
        directions
        and all(
            x == "SHORT"
            for x in directions
        )
    ):
        decision = "SHORT"

    else:
        decision = "HOLD"

    base["decision"] = decision

    if decision == "LONG":
        base["side"] = "BUY"
        base["allow"] = 1

    elif decision == "SHORT":
        base["side"] = "SELL"
        base["allow"] = 1

    else:
        base["reason"] = (
            "NO_CONSENSUS"
        )
        return base

    # Consensus가 성립한 최초 시점은 구성 전략 중 가장 늦게 같은 방향으로
    # 진입한 strategy origin이다. 이 값이 Late Entry Replay의 원점이다.
    origin_rows = []
    for strategy in strategies:
        detail = details.get(strategy) if isinstance(details.get(strategy), dict) else {}
        ot = text(detail.get("signal_origin_time"))
        try:
            op = float(detail.get("signal_origin_price") or 0.0)
        except Exception:
            op = 0.0
        if ot:
            origin_rows.append((ot, op))
    if origin_rows:
        origin_rows.sort(key=lambda x: x[0])
        base["signal_origin_time"] = origin_rows[-1][0]
        base["signal_price"] = origin_rows[-1][1]
    else:
        base["signal_origin_time"] = candle_time
        try:
            base["signal_price"] = float(material.get("confirmed_close") or 0.0)
        except Exception:
            base["signal_price"] = 0.0

    if not candle_time:
        base["decision"] = "HOLD"
        base["side"] = ""
        base["allow"] = 0
        base["reason"] = (
            "NO_CONFIRMED_CANDLE_TIME"
        )
        return base

    combo = "+".join(
        strategies
    )

    base["signal_id"] = (
        f"{symbol}|"
        f"{tf}|"
        f"{base.get('signal_origin_time') or candle_time}|"
        f"{combo}|"
        f"{decision}"
    )

    if instance is not None and not representative:
        base["signal_id"] += "|" + base["strategy_version"]

    base["reason"] = (
        f"CONSENSUS_{decision}"
    )

    return base


# ============================================================
# SYMBOL EVALUATION
# ============================================================

def evaluate_symbol(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)

    config = load_strategy_config()
    frames = config.get("frames") or {}

    results = {}
    candidates = []

    for tf in TF_MAP:

        cfg = frames.get(tf)

        if not isinstance(cfg, dict):
            continue

        if not bool(
            cfg.get("enabled", False)
        ):
            continue

        result = evaluate_frame(
            symbol,
            tf,
            require_enabled=True,
        )

        results[tf] = result

        if int(
            result.get("allow", 0)
            or 0
        ) == 1:
            candidates.append(result)

    return {
        "gate_version": GATE_VERSION,
        "policy": GATE_POLICY,
        "symbol": symbol,
        "frames": results,
        "candidates": candidates,
        "candidate_count": len(
            candidates
        ),
    }


# ============================================================
# RUNNER PUBLIC CONTRACT
# ============================================================

def check_result(
    symbol: str,
    side: Optional[str] = None,
    price: Optional[float] = None,
    frame: Optional[str] = None,
) -> dict:
    """
    새 Runner용 public API.

    frame 지정:
      해당 TF 전략만 평가

    frame 미지정:
      활성 TF 전체 후보 반환

    side 지정:
      BUY/SELL 방향 검증까지 수행
    """

    del price

    requested_side = normalize_side(
        side or ""
    )

    if frame:

        result = evaluate_frame(
            symbol,
            frame,
            require_enabled=True,
        )

        if (
            requested_side
            and result.get("side")
            != requested_side
        ):
            result = dict(result)

            result["allow"] = 0
            result["reason"] = (
                "SIDE_MISMATCH"
            )

        return result

    result = evaluate_symbol(
        symbol
    )

    candidates = (
        result.get("candidates")
        or []
    )

    if requested_side:

        matched = [
            x
            for x in candidates
            if x.get("side")
            == requested_side
        ]

        return {
            **result,
            "requested_side": requested_side,
            "matched_candidates": matched,
            "allow": int(
                len(matched) > 0
            ),
        }

    return result


# ============================================================
# FINAL REVALIDATION
# ============================================================

def revalidate_entry(
    symbol: str,
    side: str,
    method: Optional[str] = None,
    price: Optional[float] = None,
    frame: Optional[str] = None,
    strategy: Optional[str] = None,
    signal_id: Optional[str] = None,
) -> dict:
    """
    주문 직전 최종 재검증.

    method는 구 Runner 호환용 인자일 뿐
    판단에는 사용하지 않는다.
    """

    del method
    del price

    if not frame:

        return {
            "allow": 0,
            "symbol": normalize_symbol(
                symbol
            ),
            "reason": (
                "FRAME_REQUIRED"
            ),
        }

    result = evaluate_frame(
        symbol,
        frame,
        require_enabled=True,
    )

    requested_side = normalize_side(
        side
    )

    if (
        result.get("allow") != 1
        or result.get("side")
        != requested_side
    ):
        result = dict(result)

        result["allow"] = 0
        result["reason"] = (
            "FINAL_SIDE_NOT_VALID"
        )

        return result

    if strategy:

        current_strategy = text(
            result.get("strategy")
        ).upper()

        requested_strategy = text(
            strategy
        ).upper()

        if (
            current_strategy
            != requested_strategy
        ):
            result = dict(result)

            result["allow"] = 0
            result["reason"] = (
                "FINAL_STRATEGY_CHANGED"
            )

            return result

    if signal_id:

        if (
            text(result.get("signal_id"))
            != text(signal_id)
        ):
            result = dict(result)

            result["allow"] = 0
            result["reason"] = (
                "FINAL_SIGNAL_CHANGED"
            )

            return result

    return result


# ============================================================
# DEBUG
# ============================================================

def debug_summary(
    symbol: str = "BTCUSDT",
) -> dict:

    out = {}

    for tf in TF_MAP:

        out[tf] = evaluate_frame(
            symbol,
            tf,
            require_enabled=False,
        )

    return out


def configured_strategy_names(enabled_only=False):
    config = load_strategy_config()
    frames = config.get("frames") or {}
    if not isinstance(frames, dict):
        return []

    out = []
    for row in frames.values():
        if not isinstance(row, dict):
            continue
        if enabled_only and not bool(row.get("enabled", False)):
            continue
        raw = row.get("strategies") or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split("+") if x.strip()]
        if not isinstance(raw, (list, tuple)):
            continue
        for item in raw:
            value = text(item).upper()
            if re.fullmatch(r"[A-Z0-9_]+", value or "") and value not in out:
                out.append(value)
    for instance in (((config.get("r4_runtime") or {}).get("strategy_instances") or {}).values()):
        if not isinstance(instance, dict) or instance.get("approved_for_server_release") is not True:
            continue
        frame_row = frames.get(str(instance.get("timeframe") or "").upper()) or {}
        if enabled_only and (not frame_row.get("enabled") or instance.get("enabled") is not True):
            continue
        for part in str(instance.get("strategy_id") or "").split("+"):
            value = text(part).upper()
            if re.fullmatch(r"[A-Z0-9_]+", value or "") and value not in out:
                out.append(value)
    return out

def strategy_material_key(strategy: str) -> str:
    value = normalize_strategy(strategy)
    return value.lower() if value else ""

def resolve_management_owner(previous_frame: str = "") -> dict:
    """
    기존 포지션 owner 보존.
    previous_frame이 있으면 신규진입 enabled 여부와 무관하게 그 frame의
    configured strategy/leverage를 우선 사용한다. 신규 진입 활성화와 기존
    포지션 관리는 분리한다.
    """
    config = load_strategy_config()
    frames = config.get("frames") or {}
    if not isinstance(frames, dict):
        frames = {}

    previous = normalize_tf(previous_frame)
    if previous:
        row = get_frame_config(previous)
        strategies = list(row.get("strategies") or [])
        try:
            leverage = int(row.get("leverage") or 0)
        except Exception:
            leverage = 0
        if strategies and leverage > 0:
            return {
                "frame": previous,
                "strategies": strategies,
                "strategy": "+".join(strategies),
                "leverage": leverage,
                "source": "PRESERVED_ENTRY_FRAME_CONFIG",
            }

    management = config.get("position_management") or {}
    if not isinstance(management, dict):
        management = {}
    priority = management.get("owner_fallback_priority") or ["12H", "1D", "6H", "1H", "30M", "1W"]
    if not isinstance(priority, (list, tuple)):
        priority = ["12H", "1D", "6H", "1H", "30M", "1W"]

    for item in priority:
        tf = normalize_tf(item)
        row = get_frame_config(tf)
        if not row.get("enabled"):
            continue
        strategies = list(row.get("strategies") or [])
        try:
            leverage = int(row.get("leverage") or 0)
        except Exception:
            leverage = 0
        if strategies and leverage > 0:
            return {
                "frame": tf,
                "strategies": strategies,
                "strategy": "+".join(strategies),
                "leverage": leverage,
                "source": "ACTIVE_FALLBACK_ONLY",
            }

    return {"frame": "", "strategies": [], "strategy": "", "leverage": 0, "source": "UNRESOLVED"}

if __name__ == "__main__":

    print(
        json.dumps(
            debug_summary(),
            ensure_ascii=False,
            indent=2,
        )
    )


# ============================================================
# OWNED POSITION EVALUATION
# ============================================================

def _normalize_strategy_list(strategies):
    """
    기존 포지션이 진입 당시 소유한 전략 조합을 정규화한다.

    지원:
      "SHAPE+EMA_CROSS"
      ["SHAPE", "EMA_CROSS"]
    """

    if isinstance(strategies, str):
        raw = [
            x.strip()
            for x in strategies.split("+")
            if x.strip()
        ]

    elif isinstance(strategies, (list, tuple)):
        raw = list(strategies)

    else:
        raw = []

    out = []

    for item in raw:
        name = normalize_strategy(item)

        if name and name not in out:
            out.append(name)

    return out


def evaluate_owned_position(
    symbol,
    frame,
    strategies,
    state_mode=False,
    strategy_instance_id=None,
    exact_strategy_version=None,
):
    """
    기존 OWNER 포지션 및 Trailing 후 STATE 재진입 평가.

    state_mode=False
    - 기존 EVENT 전략 평가.

    state_mode=True
    - Trailing 청산 후 재진입 전용.
    - EMA_CROSS는 현재 EMA13/EMA24 STATE 사용.
    - 일반 FLAT 신규진입에서는 사용하지 않는다.
    """

    symbol = normalize_symbol(symbol)
    requested_tf = normalize_tf(frame)
    requested_strategies = _normalize_strategy_list(strategies)

    if requested_tf and requested_strategies:
        runtime_owner = {
            "frame": requested_tf,
            "strategies": requested_strategies,
            "strategy": "+".join(requested_strategies),
            "source": "ENTRY_FACT_OWNER",
        }
    else:
        runtime_owner = resolve_management_owner(requested_tf)

    tf = normalize_tf(runtime_owner.get("frame"))
    strategy_list = _normalize_strategy_list(runtime_owner.get("strategies"))
    state_mode = bool(state_mode)

    base = {
        "gate_version": GATE_VERSION,
        "policy": GATE_POLICY,
        "symbol": symbol,
        "frame": tf,
        "enabled": True,
        "owned_position": 1,
        "state_mode": int(state_mode),
        "owner_source": runtime_owner.get("source"),
        "strategies": strategy_list,
        "strategy": "+".join(strategy_list),
        "decision": "HOLD",
        "side": "",
        "allow": 0,
        "confirmed_open_time": "",
        "signal_id": "",
        "details": {},
        "reason": "",
    }

    if not symbol:
        base["reason"] = "INVALID_SYMBOL"
        return base

    if not tf:
        base["reason"] = "INVALID_FRAME"
        return base

    if not strategy_list:
        base["reason"] = "NO_OWNED_STRATEGY"
        return base

    instance = None
    if strategy_instance_id:
        config = load_strategy_config()
        instance = (((config.get("r4_runtime") or {}).get("strategy_instances") or {}).get(str(strategy_instance_id)))
        if (not isinstance(instance, dict)
            or instance.get("approved_for_server_release") is not True
            or str(instance.get("timeframe") or "").upper() != tf
            or _normalize_strategy_list(instance.get("strategy_id")) != strategy_list
            or str(instance.get("exact_strategy_version") or "") != str(exact_strategy_version or "")):
            base["reason"] = "EXACT_OWNED_STRATEGY_INSTANCE_UNAVAILABLE"
            return base
        base["strategy_instance_id"] = str(strategy_instance_id)
        base["strategy_version"] = str(exact_strategy_version)

    material = get_tf_material(
        symbol,
        tf,
    )

    if not material:
        base["reason"] = "NO_MATERIAL"
        return base

    if instance is not None:
        version_map = material.get("r4_instance_versions")
        version_map = version_map if isinstance(version_map, dict) else {}
        observed = version_map.get(str(strategy_instance_id))
        frame_row = (load_strategy_config().get("frames") or {}).get(tf) or {}
        representative = (
            str(frame_row.get("strategy_version") or "") == str(exact_strategy_version)
            and _normalize_strategy_list(frame_row.get("strategies")) == strategy_list
        )
        if (observed is not None and str(observed) != str(exact_strategy_version)) or (observed is None and not representative):
            base["reason"] = "EXACT_OWNED_MATERIAL_VERSION_UNAVAILABLE"
            return base

    candle_time = text(
        material.get(
            "confirmed_open_time"
        )
    )

    base[
        "confirmed_open_time"
    ] = candle_time

    directions = []
    details = {}

    for strategy in strategy_list:

        key = strategy_material_key(strategy)

        signal_row = material.get(
            key
        )

        # ----------------------------------------------------
        # STATE 재진입
        # EMA_CROSS만 교차 EVENT 대신 EMA 현재 STATE 사용
        # ----------------------------------------------------

        if (
            state_mode
            and isinstance(
                signal_row,
                dict,
            )
            and "state" in signal_row
        ):
            raw_state = text(
                signal_row.get(
                    "state"
                ),
                "wait",
            ).lower()

            if raw_state in (
                "long",
                "buy",
                "bull",
                "bullish",
            ):
                direction = "LONG"

            elif raw_state in (
                "short",
                "sell",
                "bear",
                "bearish",
            ):
                direction = "SHORT"

            else:
                direction = "HOLD"

        else:
            direction = signal_direction(
                signal_row
            )

        directions.append(
            direction
        )

        details[strategy] = {
            "direction": direction,
            "state": (
                signal_row.get(
                    "state",
                    "wait",
                )
                if isinstance(
                    signal_row,
                    dict,
                )
                else "wait"
            ),
            "signal": (
                signal_row.get(
                    "signal"
                )
                if isinstance(
                    signal_row,
                    dict,
                )
                else "wait"
            ),
            "strength_long": (
                signal_row.get(
                    "strength_long",
                    0.0,
                )
                if isinstance(
                    signal_row,
                    dict,
                )
                else 0.0
            ),
            "strength_short": (
                signal_row.get(
                    "strength_short",
                    0.0,
                )
                if isinstance(
                    signal_row,
                    dict,
                )
                else 0.0
            ),
            "reason": (
                signal_row.get(
                    "reason",
                    "",
                )
                if isinstance(
                    signal_row,
                    dict,
                )
                else "NO_SIGNAL"
            ),
        }

    base["details"] = details

    if (
        directions
        and all(
            x == "LONG"
            for x in directions
        )
    ):
        decision = "LONG"

    elif (
        directions
        and all(
            x == "SHORT"
            for x in directions
        )
    ):
        decision = "SHORT"

    else:
        decision = "HOLD"

    base["decision"] = decision

    if decision == "LONG":
        base["side"] = "BUY"
        base["allow"] = 1

    elif decision == "SHORT":
        base["side"] = "SELL"
        base["allow"] = 1

    else:
        base["reason"] = (
            "OWNED_STATE_NO_CONSENSUS"
            if state_mode
            else "OWNED_NO_CONSENSUS"
        )

        return base

    if not candle_time:
        base["decision"] = "HOLD"
        base["side"] = ""
        base["allow"] = 0
        base["reason"] = (
            "NO_CONFIRMED_CANDLE_TIME"
        )

        return base

    combo = "+".join(
        strategy_list
    )

    mode_name = (
        "STATE"
        if state_mode
        else "EVENT"
    )

    base["signal_id"] = (
        f"{symbol}|"
        f"{tf}|"
        f"{candle_time}|"
        f"{combo}|"
        f"{mode_name}|"
        f"{decision}"
    )

    base["reason"] = (
        f"OWNED_{mode_name}_CONSENSUS_{decision}"
    )

    return base

