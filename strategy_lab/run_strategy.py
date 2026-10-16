#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# STRATEGY_LAB_RUNNER_V4_20260823
#
# 목적
# ----
# 1. CURRENT_35_FROM_2020
# 2. LONG_4_5Y
# 3. 1H/6H/12H/1D/1W = long exact cache
# 4. 1M = raw monthly parquet + 다음 달 시작시각 확정
# 5. USD-M SHORT 수익률 공식 수정
# 6. 기존 잘못된 전액 순차 compound/MDD 제거
#
# 아직 하지 않는 것
# ----------------
# - 35슬롯 실제 계좌 compound
# - portfolio MDD
# - WF/OOS
# - regime
# - leverage
#
# 위 항목은 다음 단계의 portfolio simulator에서 계산한다.

import sys
import os
import json
import math
import importlib.util
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd


ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"

CONFIG = LAB / "lab_config.json"
RESULTS = LAB / "results"

DATA_DIR = ROOT / "data" / "candles"
LAB_ENGINE = ROOT / "gate_lab_fast.py"

LONG4_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
]

MATERIALS = [
    "macd",
    "stoch",
    "shape",
    "div",
    "obv",
    "sar",
]

HARD_PASS_DATASET = "HARD_PASS_FIXED_WINDOW"
HARD_PASS_LONG4_DATASET = "HARD_PASS_LONG4_WINDOW"
HARD_PASS_MODE_ENV = "BBOT_RESEARCH_DATASET_MODE"
HARD_PASS_START_ENV = "BBOT_RESEARCH_START"
HARD_PASS_END_ENV = "BBOT_RESEARCH_END"
TRAILING_MODE_ENV = "BBOT_RESEARCH_TRAILING_MODE"
TRAILING_START_ENV = "BBOT_RESEARCH_TRAILING_START_PCT"
TRAILING_CALLBACK_ENV = "BBOT_RESEARCH_TRAILING_CALLBACK_PCT"
REENTRY_MODE_ENV = "BBOT_RESEARCH_REENTRY_MODE"
REENTRY_CONFIRM_BARS_ENV = "BBOT_RESEARCH_REENTRY_CONFIRM_BARS"


def individual_trailing_from_env():
    mode = str(os.getenv(TRAILING_MODE_ENV) or "").strip().upper()
    if mode != "INDIVIDUAL_TRAILING":
        return None
    try:
        start = float(os.getenv(TRAILING_START_ENV))
        callback = float(os.getenv(TRAILING_CALLBACK_ENV))
    except Exception as exc:
        raise RuntimeError("INDIVIDUAL_TRAILING_ENV_INVALID") from exc
    if not (1.0 <= start <= 6.0):
        raise RuntimeError("INDIVIDUAL_TRAILING_START_OUT_OF_RANGE")
    if not (0.5 <= callback <= 3.0):
        raise RuntimeError("INDIVIDUAL_TRAILING_CALLBACK_OUT_OF_RANGE")
    if callback >= start:
        raise RuntimeError("INDIVIDUAL_TRAILING_CALLBACK_NOT_LT_START")
    return {
        "mode": mode,
        "start_pct": float(start),
        "callback_pct": float(callback),
        "block_reentry_until_signal_reset": True,
        "same_bar_new_peak_stop": False,
        "fill_model": "PRIOR_PEAK_STOP_WITH_GAP_AWARE_OPEN",
    }


def individual_reentry_from_env():
    mode = str(os.getenv(REENTRY_MODE_ENV) or "").strip().upper()
    if not mode:
        return None
    if mode == "CURRENT_STATE_NEXT_1H_BASELINE_PROXY":
        return {
            "mode": mode,
            "method": "CURRENT_STATE_NEXT_1H",
            "confirmation_bars": 1,
            "trigger": "TRAILING_STOP_ONLY",
            "same_direction_only": True,
            "neutral_state_keeps_pending": True,
            "opposite_state_cancels": True,
            "same_bar_immediate_reorder": False,
            "runtime_equivalent": False,
            "research_resolution": "STRATEGY_TIMEFRAME_PROXY",
        }
    if mode == "CURRENT_STATE_CONFIRM_STRATEGY_BARS":
        try:
            confirm = int(os.getenv(REENTRY_CONFIRM_BARS_ENV))
        except Exception as exc:
            raise RuntimeError("INDIVIDUAL_REENTRY_CONFIRM_ENV_INVALID") from exc
        if confirm not in {2, 3}:
            raise RuntimeError("INDIVIDUAL_REENTRY_CONFIRM_BARS_OUT_OF_RANGE")
        return {
            "mode": mode,
            "confirmation_bars": confirm,
            "trigger": "TRAILING_STOP_ONLY",
            "same_direction_only": True,
            "neutral_state_keeps_pending": True,
            "opposite_state_cancels": True,
            "same_bar_immediate_reorder": False,
            "runtime_equivalent": False,
            "research_resolution": "STRATEGY_TIMEFRAME_PROXY",
        }
    if mode == "CURRENT_STATE_RESET_RECLAIM":
        return {
            "mode": mode,
            "confirmation_bars": 1,
            "require_neutral_reset": True,
            "trigger": "TRAILING_STOP_ONLY",
            "same_direction_only": True,
            "neutral_state_keeps_pending": True,
            "opposite_state_cancels": True,
            "same_bar_immediate_reorder": False,
            "runtime_equivalent": False,
            "research_resolution": "STRATEGY_TIMEFRAME_PROXY",
        }
    raise RuntimeError("INDIVIDUAL_REENTRY_MODE_INVALID")


def _parse_utc_date_bound(raw, *, end=False):
    text = str(raw or "").strip()
    if not text:
        return None
    ts = pd.Timestamp(text)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    if end and len(text) == 10:
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return int(ts.timestamp())


def hard_pass_window_from_env():
    mode = str(os.getenv(HARD_PASS_MODE_ENV) or "").strip().upper()
    if mode != "HARD_PASS_ONLY":
        return None
    start = _parse_utc_date_bound(os.getenv(HARD_PASS_START_ENV), end=False)
    end = _parse_utc_date_bound(os.getenv(HARD_PASS_END_ENV), end=True)
    if start is None or end is None or end <= start:
        raise RuntimeError("HARD_PASS_RESEARCH_WINDOW_INVALID")
    return {
        "mode": mode,
        "start_ts": start,
        "end_ts": end,
        "requested_start": str(os.getenv(HARD_PASS_START_ENV) or ""),
        "requested_end": str(os.getenv(HARD_PASS_END_ENV) or ""),
    }


# ============================================================
# CONFIG
# ============================================================

def load_config():

    with CONFIG.open(
        "r",
        encoding="utf-8-sig",
    ) as f:
        cfg = json.load(f)

    raw = str(cfg.get("data_file") or "")
    if os.getenv("BBOT_LOCAL_ROOT") and raw.lower().endswith("gate_lab_exact_long_cache.parquet"):
        cfg["data_file"] = str(ROOT / "gate_lab_exact_long_cache.parquet")

    return cfg


# ============================================================
# MODULE LOAD
# ============================================================

def load_python_module(
    name,
    path,
):

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(path)

    spec = (
        importlib.util
        .spec_from_file_location(
            name,
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise ImportError(
            f"cannot load module: {path}"
        )

    module = (
        importlib.util
        .module_from_spec(spec)
    )

    spec.loader.exec_module(
        module
    )

    return module


def load_lab_engine():

    return load_python_module(
        "gate_lab_fast_runtime",
        LAB_ENGINE,
    )


def load_strategy(path):

    path = Path(path)

    module = load_python_module(
        "lab_strategy",
        path,
    )

    if not hasattr(
        module,
        "STRATEGY_META",
    ):
        raise RuntimeError(
            "STRATEGY_META missing"
        )

    if not hasattr(
        module,
        "generate_signals",
    ):
        raise RuntimeError(
            "generate_signals missing"
        )

    return (
        module.STRATEGY_META,
        module.generate_signals,
    )


def load_material_modules():

    out = {}

    for name in MATERIALS:

        p = (
            ROOT
            / "assist"
            / "strategies"
            / f"{name}.py"
        )

        out[name] = load_python_module(
            f"live_material_{name}",
            p,
        )

    return out


# ============================================================
# CACHE
# ============================================================

def load_cache(path):

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(path)

    df = pd.read_parquet(
        path
    ).copy()

    required = {
        "ts",
        "symbol",
        "close",
    }

    missing = (
        required
        - set(df.columns)
    )

    if missing:
        raise RuntimeError(
            f"cache required columns missing: "
            f"{sorted(missing)}"
        )

    df["ts"] = pd.to_numeric(
        df["ts"],
        errors="coerce",
    )

    df["close"] = pd.to_numeric(
        df["close"],
        errors="coerce",
    )

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.upper()
    )

    df = df.dropna(
        subset=[
            "ts",
            "symbol",
            "close",
        ]
    )

    df["ts"] = (
        df["ts"]
        .astype("int64")
    )

    df["datetime"] = pd.to_datetime(
        df["ts"],
        unit="s",
        utc=True,
    )

    df = (
        df.sort_values(
            [
                "symbol",
                "ts",
            ]
        )
        .drop_duplicates(
            [
                "symbol",
                "ts",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    return df


# ============================================================
# SIGNAL
# ============================================================

def normalize_signal(
    v,
    index,
):

    if not isinstance(
        v,
        pd.Series,
    ):

        v = pd.Series(
            np.asarray(v),
            index=index[:len(v)],
        )

    v = (
        pd.to_numeric(
            v,
            errors="coerce",
        )
        .fillna(0)
    )

    out = np.zeros(
        len(index),
        dtype=np.int8,
    )

    n = min(
        len(out),
        len(v),
    )

    arr = (
        v.iloc[:n]
        .to_numpy(
            dtype=float
        )
    )

    tmp = np.zeros(
        n,
        dtype=np.int8,
    )

    tmp[
        arr > 0
    ] = 1

    tmp[
        arr < 0
    ] = -1

    out[:n] = tmp

    return out


# ============================================================
# RETURN
# ============================================================

def trade_net_return(
    side,
    entry,
    exit_price,
    fee_rate,
):

    entry = float(entry)
    exit_price = float(exit_price)

    if (
        not np.isfinite(entry)
        or not np.isfinite(exit_price)
        or entry <= 0
        or exit_price <= 0
    ):
        return np.nan

    # Linear USD-M futures price return.
    #
    # LONG:
    #   (exit - entry) / entry
    #
    # SHORT:
    #   (entry - exit) / entry
    #
    # leverage는 여기서 적용하지 않는다.
    # 실제 계좌 simulator에서 별도로 적용한다.

    if side == 1:

        gross = (
            exit_price
            / entry
            - 1.0
        )

    elif side == -1:

        gross = (
            1.0
            - exit_price
            / entry
        )

    else:
        return np.nan

    # 진입 + 청산 수수료.
    # 현재 값은 notional return 기준.
    net = (
        gross
        - 2.0 * float(fee_rate)
    )

    return float(net)


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate(
    df,
    signal,
    fee_rate,
    trailing=None,
    reentry=None,
):

    if len(df) == 0:
        return []

    ts = (
        df["ts"]
        .to_numpy(
            dtype=np.int64
        )
    )

    px = (
        pd.to_numeric(
            df["close"],
            errors="coerce",
        )
        .to_numpy(
            dtype=float
        )
    )

    high_px = pd.to_numeric(
        df["high"] if "high" in df.columns else df["close"],
        errors="coerce",
    ).to_numpy(dtype=float)

    low_px = pd.to_numeric(
        df["low"] if "low" in df.columns else df["close"],
        errors="coerce",
    ).to_numpy(dtype=float)

    open_px = pd.to_numeric(
        df["open"] if "open" in df.columns else df["close"],
        errors="coerce",
    ).to_numpy(dtype=float)

    trailing_cfg = trailing if isinstance(trailing, dict) else None
    if trailing_cfg is not None:
        trailing_start = float(trailing_cfg.get("start_pct")) / 100.0
        trailing_callback = float(trailing_cfg.get("callback_pct")) / 100.0
        if trailing_start <= 0.0 or trailing_callback <= 0.0 or trailing_callback >= trailing_start:
            raise RuntimeError("INDIVIDUAL_TRAILING_CONFIG_INVALID")
    else:
        trailing_start = None
        trailing_callback = None

    reentry_cfg = reentry if isinstance(reentry, dict) else None
    if reentry_cfg is not None:
        if trailing_cfg is None:
            raise RuntimeError("INDIVIDUAL_REENTRY_REQUIRES_TRAILING")
        reentry_mode = str(reentry_cfg.get("mode") or "").upper()
        if reentry_mode not in {
            "CURRENT_STATE_NEXT_1H_BASELINE_PROXY",
            "CURRENT_STATE_CONFIRM_STRATEGY_BARS",
            "CURRENT_STATE_RESET_RECLAIM",
        }:
            raise RuntimeError("INDIVIDUAL_REENTRY_CONFIG_INVALID")
        reentry_confirmation_bars = int(reentry_cfg.get("confirmation_bars") or 1)
        if reentry_confirmation_bars not in {1, 2, 3}:
            raise RuntimeError("INDIVIDUAL_REENTRY_CONFIRMATION_INVALID")
        reentry_require_neutral_reset = bool(reentry_cfg.get("require_neutral_reset"))
    else:
        reentry_mode = None
        reentry_confirmation_bars = None
        reentry_require_neutral_reset = False

    signal = np.asarray(
        signal,
        dtype=np.int8,
    )

    if len(signal) != len(df):
        raise RuntimeError(
            "signal length mismatch"
        )

    trades = []

    position = 0
    entry_i = None
    entry_price = None
    trailing_active = False
    trailing_peak = None
    trailing_reentry_block_side = 0
    trailing_reentry_confirm_count = 0
    trailing_reentry_neutral_seen = False
    entry_reason = None

    def entry_regime_at(index):
        lookback = 20
        if index is None or int(index) < lookback:
            return "UNKNOWN"
        start = int(index) - lookback
        hist = np.asarray(px[start:int(index) + 1], dtype=float)
        if len(hist) < lookback + 1 or np.any(~np.isfinite(hist)) or np.any(hist <= 0.0):
            return "UNKNOWN"
        log_returns = np.diff(np.log(hist))
        vol = float(np.std(log_returns)) if len(log_returns) else 0.0
        trend = float(np.log(hist[-1] / hist[0]))
        if vol <= 1e-12:
            z = 0.0 if abs(trend) <= 1e-12 else (999.0 if trend > 0.0 else -999.0)
        else:
            z = trend / (vol * math.sqrt(float(lookback)))
        if z >= 0.5:
            return "BULL"
        if z <= -0.5:
            return "BEAR"
        return "SIDEWAYS"

    def close_trade(
        i,
        reason,
        exit_price_override=None,
    ):

        nonlocal position
        nonlocal entry_i
        nonlocal entry_price
        nonlocal trailing_active
        nonlocal trailing_peak
        nonlocal trailing_reentry_block_side
        nonlocal trailing_reentry_confirm_count
        nonlocal trailing_reentry_neutral_seen
        nonlocal entry_reason

        exit_price = (
            float(exit_price_override)
            if exit_price_override is not None
            else float(px[i])
        )

        closing_side = int(position)
        ret = trade_net_return(
            position,
            float(entry_price),
            exit_price,
            fee_rate,
        )

        # Entry occurs at the entry bar close. Do not use the entry bar
        # high/low for post-entry MAE/MFE because that movement happened
        # before the modeled fill.
        if i > entry_i:
            path_high = high_px[entry_i + 1:i + 1]
            path_low = low_px[entry_i + 1:i + 1]
        else:
            path_high = np.asarray([px[i]], dtype=float)
            path_low = np.asarray([px[i]], dtype=float)

        finite_high = path_high[np.isfinite(path_high)]
        finite_low = path_low[np.isfinite(path_low)]
        mfe_pct = None
        mae_pct_abs = None
        if len(finite_high) and len(finite_low):
            highest = float(np.max(finite_high))
            lowest = float(np.min(finite_low))
            if position == 1:
                favorable = highest / float(entry_price) - 1.0
                adverse = lowest / float(entry_price) - 1.0
            else:
                favorable = 1.0 - lowest / float(entry_price)
                adverse = 1.0 - highest / float(entry_price)
            mfe_pct = max(0.0, float(favorable) * 100.0)
            mae_pct_abs = max(0.0, -float(adverse) * 100.0)

        trades.append({
            "side": (
                "LONG"
                if position == 1
                else "SHORT"
            ),
            "entry_ts": int(
                ts[entry_i]
            ),
            "exit_ts": int(
                ts[i]
            ),
            "entry_price": float(
                entry_price
            ),
            "exit_price": float(
                exit_price
            ),
            "net_return": float(
                ret
            ),
            "net_pct": float(
                ret * 100.0
            ),
            "hold_hours": float(
                (
                    ts[i]
                    - ts[entry_i]
                )
                / 3600.0
            ),
            "exit_reason": reason,
            "entry_reason": str(entry_reason or "SIGNAL_ENTRY"),
            "entry_regime": entry_regime_at(entry_i),
            "mfe_pct": mfe_pct,
            "mae_pct_abs": mae_pct_abs,
        })

        if reason == "TRAILING_STOP":
            trailing_reentry_block_side = closing_side
            trailing_reentry_confirm_count = 0
            trailing_reentry_neutral_seen = False

        position = 0
        entry_i = None
        entry_price = None
        trailing_active = False
        trailing_peak = None
        entry_reason = None

    for i in range(
        len(df)
    ):

        if (
            not np.isfinite(
                px[i]
            )
            or px[i] <= 0
        ):
            continue

        s = int(
            signal[i]
        )

        # FLAT. Trailing-only optimization intentionally isolates EXIT and
        # therefore blocks same-side reentry until a true neutral reset.
        # REENTRY research is a separate method study.  The current single-TF
        # backtest can measure closed-bar method proxies, but these proxies are
        # never treated as exact CURRENT_STATE_NEXT_1H runtime replay.
        if position == 0:

            reentry_after_trailing = False

            if trailing_reentry_block_side:
                if reentry_cfg is None:
                    if s == 0:
                        trailing_reentry_block_side = 0
                        trailing_reentry_confirm_count = 0
                        trailing_reentry_neutral_seen = False
                    continue

                if s == 0:
                    trailing_reentry_confirm_count = 0
                    trailing_reentry_neutral_seen = True
                    continue

                if s != trailing_reentry_block_side:
                    trailing_reentry_block_side = 0
                    trailing_reentry_confirm_count = 0
                    trailing_reentry_neutral_seen = False
                    continue

                if reentry_require_neutral_reset and not trailing_reentry_neutral_seen:
                    continue

                trailing_reentry_confirm_count += 1
                if trailing_reentry_confirm_count < int(reentry_confirmation_bars or 1):
                    continue

                trailing_reentry_block_side = 0
                trailing_reentry_confirm_count = 0
                trailing_reentry_neutral_seen = False
                reentry_after_trailing = True

            if s in (
                1,
                -1,
            ):

                position = s
                entry_i = i
                entry_price = float(
                    px[i]
                )
                entry_reason = (
                    "REENTRY_AFTER_TRAILING"
                    if reentry_after_trailing
                    else "SIGNAL_ENTRY"
                )
                trailing_active = False
                trailing_peak = float(entry_price)

            continue

        # Optional research-only trailing. We use the PRIOR confirmed peak
        # for stop detection, then update the peak only if the prior stop was
        # not touched. This avoids assuming an optimistic intrabar path.
        if trailing_cfg is not None and i > entry_i:
            if trailing_active and trailing_peak is not None:
                if position == 1:
                    stop_price = float(trailing_peak) * (1.0 - trailing_callback)
                    if np.isfinite(low_px[i]) and float(low_px[i]) <= stop_price:
                        fill = stop_price
                        if np.isfinite(open_px[i]) and float(open_px[i]) < stop_price:
                            fill = float(open_px[i])
                        close_trade(i, "TRAILING_STOP", fill)
                        continue
                else:
                    stop_price = float(trailing_peak) * (1.0 + trailing_callback)
                    if np.isfinite(high_px[i]) and float(high_px[i]) >= stop_price:
                        fill = stop_price
                        if np.isfinite(open_px[i]) and float(open_px[i]) > stop_price:
                            fill = float(open_px[i])
                        close_trade(i, "TRAILING_STOP", fill)
                        continue

                if position == 1 and np.isfinite(high_px[i]):
                    trailing_peak = max(float(trailing_peak), float(high_px[i]))
                elif position == -1 and np.isfinite(low_px[i]):
                    trailing_peak = min(float(trailing_peak), float(low_px[i]))

            elif not trailing_active:
                if position == 1 and np.isfinite(high_px[i]):
                    favorable = float(high_px[i]) / float(entry_price) - 1.0
                    if favorable >= trailing_start:
                        trailing_active = True
                        trailing_peak = float(high_px[i])
                elif position == -1 and np.isfinite(low_px[i]):
                    favorable = 1.0 - float(low_px[i]) / float(entry_price)
                    if favorable >= trailing_start:
                        trailing_active = True
                        trailing_peak = float(low_px[i])

        # Existing opposite-signal exit remains unchanged.
        if s == -position:

            new_side = s

            close_trade(
                i,
                "OPPOSITE_SIGNAL",
            )

            position = new_side
            entry_i = i
            entry_price = float(
                px[i]
            )
            entry_reason = "OPPOSITE_SIGNAL_REVERSAL"
            trailing_active = False
            trailing_peak = float(entry_price)

    # 마지막 미청산 포지션은
    # 연구 종료 시점 mark-to-market.
    if (
        position != 0
        and entry_i is not None
    ):

        i = len(df) - 1

        if (
            np.isfinite(
                px[i]
            )
            and px[i] > 0
        ):

            close_trade(
                i,
                "END_OF_TEST",
            )

    return trades


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    trades,
):

    # compound_pct / mdd_pct는
    # V3처럼 모든 거래를 순차 전액복리하지 않는다.
    #
    # 호환 key만 유지하고 NaN.
    # 실제 값은 이후 35-slot portfolio simulator가 계산.

    if not trades:

        return {
            "trades": 0,
            "long_trades": 0,
            "short_trades": 0,
            "win_rate_pct": np.nan,
            "avg_net_pct": np.nan,
            "median_net_pct": np.nan,
            "best_net_pct": np.nan,
            "worst_net_pct": np.nan,
            "avg_hold_hours": np.nan,
            "avg_win_pct": np.nan,
            "avg_loss_pct": np.nan,

            "compound_pct": np.nan,
            "mdd_pct": np.nan,

            "portfolio_metric_status":
                "DEFERRED_35_SLOT_SIMULATOR",
        }

    net = np.asarray(
        [
            x["net_pct"]
            for x in trades
        ],
        dtype=float,
    )

    hold = np.asarray(
        [
            x["hold_hours"]
            for x in trades
        ],
        dtype=float,
    )

    return {
        "trades": int(
            len(trades)
        ),

        "long_trades": int(
            sum(
                x["side"] == "LONG"
                for x in trades
            )
        ),

        "short_trades": int(
            sum(
                x["side"] == "SHORT"
                for x in trades
            )
        ),

        "win_rate_pct": float(
            (
                net > 0
            ).mean()
            * 100.0
        ),

        "avg_net_pct": float(
            np.mean(net)
        ),

        "median_net_pct": float(
            np.median(net)
        ),

        "best_net_pct": float(
            np.max(net)
        ),

        "worst_net_pct": float(
            np.min(net)
        ),

        "avg_hold_hours": float(
            np.mean(hold)
        ),

        "avg_win_pct": float(np.mean(net[net > 0])) if np.any(net > 0) else np.nan,
        "avg_loss_pct": float(np.mean(net[net < 0])) if np.any(net < 0) else np.nan,

        # V4에서는 의도적으로 계산하지 않음.
        "compound_pct": np.nan,
        "mdd_pct": np.nan,

        "portfolio_metric_status":
            "DEFERRED_35_SLOT_SIMULATOR",
    }


def _probability_at_least_one_loss_run(n, loss_probability, run_length):
    n = int(n or 0)
    k = int(run_length or 0)
    p = float(loss_probability or 0.0)
    if n <= 0 or k <= 0 or p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    if k > n:
        return 0.0
    states = [0.0] * k
    states[0] = 1.0
    for _ in range(n):
        nxt = [0.0] * k
        survived = sum(states)
        nxt[0] += survived * (1.0 - p)
        for streak in range(k - 1):
            nxt[streak + 1] += states[streak] * p
        states = nxt
    return max(0.0, min(1.0, 1.0 - sum(states)))


def entry_risk_summary(trades):
    if not trades:
        return {"status": "NO_TRADES", "trades": 0}

    ordered = sorted(trades, key=lambda x: int(x.get("exit_ts") or 0))
    net = np.asarray([float(x.get("net_pct")) for x in ordered], dtype=float)
    mae = np.asarray([
        float(x.get("mae_pct_abs")) for x in ordered
        if x.get("mae_pct_abs") is not None
    ], dtype=float)
    mfe = np.asarray([
        float(x.get("mfe_pct")) for x in ordered
        if x.get("mfe_pct") is not None
    ], dtype=float)

    losses = net[net < 0.0]
    wins = net[net > 0.0]
    avg_loss_abs = float(abs(np.mean(losses))) if len(losses) else 0.0
    tail_n = max(1, int(math.ceil(len(net) * 0.05)))
    tail = np.sort(net)[:tail_n]
    cvar95_abs = max(0.0, -float(np.mean(tail)))
    cvar_ratio = cvar95_abs / avg_loss_abs if avg_loss_abs > 0.0 else 0.0

    median_mae = float(np.median(mae)) if len(mae) else None
    median_mfe = float(np.median(mfe)) if len(mfe) else None
    mae_mfe_ratio = None
    if median_mae is not None and median_mfe is not None and median_mfe > 0.0:
        mae_mfe_ratio = median_mae / median_mfe

    grouped = {}
    for trade in ordered:
        symbol = str(trade.get("symbol") or "__ALL__").upper()
        grouped.setdefault(symbol, []).append(trade)

    max_streak = 0
    minimum_tail_probability = 1.0
    abnormal_streak = False
    abnormal_symbol = None
    for symbol, rows in grouped.items():
        values = np.asarray([
            float(x.get("net_pct"))
            for x in sorted(rows, key=lambda x: int(x.get("exit_ts") or 0))
        ], dtype=float)
        local_max = 0
        local_streak = 0
        for value in values:
            if value < 0.0:
                local_streak += 1
                local_max = max(local_max, local_streak)
            else:
                local_streak = 0
        loss_probability = float((values < 0.0).mean()) if len(values) else 0.0
        tail_probability = (
            _probability_at_least_one_loss_run(len(values), loss_probability, local_max)
            if local_max > 0 else 1.0
        )
        if local_max > max_streak:
            max_streak = local_max
        if tail_probability < minimum_tail_probability:
            minimum_tail_probability = tail_probability
            abnormal_symbol = symbol
        if local_max > 0 and tail_probability < 0.01:
            abnormal_streak = True
            abnormal_symbol = symbol

    def side_summary(side):
        values = np.asarray([
            float(x.get("net_pct")) for x in ordered
            if str(x.get("side") or "").upper() == side
        ], dtype=float)
        if not len(values):
            return {"trades": 0, "avg_net_pct": None}
        return {"trades": int(len(values)), "avg_net_pct": float(np.mean(values))}

    payoff_ratio = None
    if len(wins) and len(losses) and avg_loss_abs > 0.0:
        payoff_ratio = float(np.mean(wins)) / avg_loss_abs

    return {
        "status": "MEASURED",
        "trades": int(len(net)),
        "win_rate_pct": float((net > 0.0).mean() * 100.0),
        "median_mae_pct_abs": median_mae,
        "median_mfe_pct": median_mfe,
        "median_mae_to_median_mfe": mae_mfe_ratio,
        "cvar95_loss_pct_abs": cvar95_abs,
        "avg_loss_pct_abs": avg_loss_abs,
        "cvar95_to_abs_avg_loss": cvar_ratio,
        "max_consecutive_losses": int(max_streak),
        "loss_streak_tail_probability": minimum_tail_probability,
        "loss_streak_distribution_abnormal_1pct": bool(abnormal_streak),
        "loss_streak_symbol": abnormal_symbol,
        "payoff_ratio": payoff_ratio,
        "long": side_summary("LONG"),
        "short": side_summary("SHORT"),
    }


def exit_research_summary(trades):
    if not trades:
        return {"status": "NO_TRADES", "trades": 0}

    ordered = sorted(trades, key=lambda x: (int(x.get("exit_ts") or 0), str(x.get("symbol") or "")))
    net = np.asarray([float(x.get("net_pct") or 0.0) for x in ordered], dtype=float)

    # Normalized equal-notional research drawdown only. Trades closing on the
    # same timestamp are aggregated first so symbol iteration order cannot
    # manufacture or hide drawdown.
    buckets = {}
    for tr in ordered:
        ts_value = int(tr.get("exit_ts") or 0)
        buckets[ts_value] = buckets.get(ts_value, 0.0) + float(tr.get("net_pct") or 0.0)
    bucket_rows = sorted(buckets.items())
    equity = 0.0
    peak = 0.0
    peak_ts = bucket_rows[0][0] if bucket_rows else 0
    mdd = 0.0
    max_dd_duration_hours = 0.0
    for ts_value, pnl_pct in bucket_rows:
        equity += float(pnl_pct)
        if equity >= peak - 1e-12:
            peak = max(peak, equity)
            peak_ts = ts_value
        else:
            dd = peak - equity
            mdd = max(mdd, dd)
            max_dd_duration_hours = max(
                max_dd_duration_hours,
                max(0.0, float(ts_value - peak_ts) / 3600.0),
            )

    wins = net[net > 0.0]
    losses = net[net < 0.0]
    payoff = None
    if len(wins) and len(losses):
        denom = abs(float(np.mean(losses)))
        if denom > 0.0:
            payoff = float(np.mean(wins)) / denom

    givebacks = []
    for tr in ordered:
        mfe = tr.get("mfe_pct")
        if mfe is None:
            continue
        realized = float(tr.get("net_pct") or 0.0)
        givebacks.append(max(0.0, float(mfe) - max(0.0, realized)))

    def side_avg(name):
        vals = [float(x.get("net_pct") or 0.0) for x in ordered if str(x.get("side") or "").upper() == name]
        return {"trades": len(vals), "avg_net_pct": float(np.mean(vals)) if vals else None}

    total_net = float(np.sum(net)) if len(net) else 0.0
    top_n = max(1, int(math.ceil(len(net) * 0.05))) if len(net) else 0
    top_share = None
    if top_n > 0 and total_net > 1e-12:
        top_values = sorted((float(x) for x in net), reverse=True)[:top_n]
        top_share = float(sum(top_values) / total_net)

    regimes = {}
    for regime_name in ("BULL", "BEAR", "SIDEWAYS", "UNKNOWN"):
        values = [float(x.get("net_pct") or 0.0) for x in ordered if str(x.get("entry_regime") or "UNKNOWN").upper() == regime_name]
        regimes[regime_name] = {
            "trades": int(len(values)),
            "avg_net_pct": float(np.mean(values)) if values else None,
        }

    return {
        "status": "MEASURED",
        "trades": int(len(ordered)),
        "normalized_mdd_pct_points": float(mdd),
        "mdd_model": "NORMALIZED_EQUAL_NOTIONAL_EXIT_TIME_BUCKETS",
        "dd_duration_hours": float(max_dd_duration_hours),
        "payoff_ratio": payoff,
        "avg_hold_hours": float(np.mean([float(x.get("hold_hours") or 0.0) for x in ordered])),
        "avg_mfe_giveback_pct": float(np.mean(givebacks)) if givebacks else None,
        "median_mfe_giveback_pct": float(np.median(givebacks)) if givebacks else None,
        "long": side_avg("LONG"),
        "short": side_avg("SHORT"),
        "trailing_stop_trades": int(sum(1 for x in ordered if x.get("exit_reason") == "TRAILING_STOP")),
        "reentry_after_trailing_trades": int(sum(1 for x in ordered if x.get("entry_reason") == "REENTRY_AFTER_TRAILING")),
        "top_5pct_trade_count": int(top_n),
        "top_5pct_trade_net_profit_share": top_share,
        "regime_classifier": "PAST_20_BAR_LOG_RETURN_Z_VS_REALIZED_VOL_0P5",
        "regimes": regimes,
    }


# ============================================================
# V3 ROBUSTNESS SUMMARY
# ============================================================

def v3_robustness_summary(trades, fee_rate):
    if not trades:
        return {
            "status":"NO_TRADES","time_segments":[],"development":{},"walk_forward":{},
            "sealed_blind_single":{},"sealed_blind_combo":{},
            "cost_stress_2x":{"trades":0,"avg_net_pct":np.nan},
        }
    ordered=sorted(trades,key=lambda x:int(x.get("exit_ts") or 0)); n=len(ordered)
    # 40/20/20/20 chronological boundaries. Broad ranking only sees first 60%.
    cuts=[0,(n*4)//10,(n*6)//10,(n*8)//10,n]
    names=["development","walk_forward","sealed_blind_single","sealed_blind_combo"]
    penalty_pct=2.0*float(fee_rate)*100.0; segments=[]; named={}
    for i,name in enumerate(names):
        part=ordered[cuts[i]:cuts[i+1]]; sm=summarize(part); stressed=[]
        for tr in part:
            x=dict(tr)
            try:
                x["net_pct"]=float(x.get("net_pct"))-penalty_pct; x["net_return"]=x["net_pct"]/100.0
            except Exception: continue
            stressed.append(x)
        ss=summarize(stressed)
        row={"segment":i+1,"name":name,"trades":sm.get("trades"),"avg_net_pct":sm.get("avg_net_pct"),"win_rate_pct":sm.get("win_rate_pct"),"avg_hold_hours":sm.get("avg_hold_hours"),"cost_stress_2x_avg_net_pct":ss.get("avg_net_pct")}
        segments.append(row); named[name]=row
    stressed=[]
    for tr in ordered:
        x=dict(tr)
        try: x["net_pct"]=float(x.get("net_pct"))-penalty_pct; x["net_return"]=x["net_pct"]/100.0
        except Exception: continue
        stressed.append(x)
    stress=summarize(stressed)
    return {
        "status":"PASS","time_segments":segments,
        "development":named["development"],"walk_forward":named["walk_forward"],
        "sealed_blind_single":named["sealed_blind_single"],"sealed_blind_combo":named["sealed_blind_combo"],
        "blind_policy":"SINGLE_AND_COMBINATION_HAVE_SEPARATE_ONE_SHOT_HOLDOUTS",
        "cost_stress_2x":{"trades":stress.get("trades"),"avg_net_pct":stress.get("avg_net_pct"),"win_rate_pct":stress.get("win_rate_pct")},
    }


# ============================================================
# MONTHLY MATERIAL FRAME
# ============================================================

def build_monthly_material_frame(
    lab,
    material_modules,
    symbol,
    max_history=240,
):

    p = lab.find_monthly_file(
        DATA_DIR,
        symbol,
    )

    raw = lab.read_ohlc(
        p
    ).copy()

    if "open_ts" not in raw.columns:
        raise RuntimeError(
            f"monthly open_ts missing: {symbol}"
        )

    raw["ts"] = (
        lab.infer_month_close_ts(
            raw["open_ts"]
        )
    )

    raw = (
        raw.sort_values(
            "ts"
        )
        .drop_duplicates(
            "ts"
        )
        .reset_index(
            drop=True
        )
    )

    for c in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        if c in raw.columns:
            raw[c] = pd.to_numeric(
                raw[c],
                errors="coerce",
            )

    raw["symbol"] = symbol

    raw["datetime"] = pd.to_datetime(
        raw["ts"],
        unit="s",
        utc=True,
    )

    # --------------------------------------------------------
    # EMA cache semantics
    #
    # 연구전략은:
    # 1m_ema_long / short = EMA STATE
    # 이후 material_signal()에서
    # EMA_CROSS EVENT로 변환한다.
    #
    # 따라서 여기서는 EVENT를 만들면 안 된다.
    # --------------------------------------------------------

    close = pd.to_numeric(
        raw["close"],
        errors="coerce",
    )

    ema13 = close.ewm(
        span=13,
        adjust=False,
    ).mean()

    ema24 = close.ewm(
        span=24,
        adjust=False,
    ).mean()

    ema_state = np.zeros(
        len(raw),
        dtype=np.int8,
    )

    ema_state[
        (
            ema13 > ema24
        ).to_numpy()
    ] = 1

    ema_state[
        (
            ema13 < ema24
        ).to_numpy()
    ] = -1

    raw["1m_ema_long"] = (
        ema_state == 1
    ).astype(
        np.int8
    )

    raw["1m_ema_short"] = (
        ema_state == -1
    ).astype(
        np.int8
    )

    # --------------------------------------------------------
    # 실제 서버 전략 module.signal()로
    # 월봉 material exact 계산.
    # --------------------------------------------------------

    for material in MATERIALS:

        min_history = (
            5
            if material == "sar"
            else 30
        )

        side = lab.exact_signal_series(
            raw,
            material_modules[
                material
            ],
            min_history=min_history,
            max_history=max_history,
        )

        raw[
            f"1m_{material}_long"
        ] = (
            side == 1
        ).astype(
            np.int8
        )

        raw[
            f"1m_{material}_short"
        ] = (
            side == -1
        ).astype(
            np.int8
        )

    return raw


# ============================================================
# RAW OHLCV FRAME
# ============================================================

def load_raw_frame(
    lab,
    symbol,
    tf,
):

    tfu = str(
        tf
    ).upper()

    if tfu == "1M":

        p = lab.find_monthly_file(
            DATA_DIR,
            symbol,
        )

        raw = lab.read_ohlc(
            p
        ).copy()

        raw["ts"] = (
            lab.infer_month_close_ts(
                raw["open_ts"]
            )
        )

    else:

        p = lab.find_tf_file(
            DATA_DIR,
            symbol,
            tfu.lower(),
        )

        raw = lab.read_ohlc(
            p
        ).copy()

        raw["ts"] = (
            lab.confirmed_ts_for_tf(
                raw,
                tfu.lower(),
            )
        )

    raw = (
        raw.sort_values(
            "ts"
        )
        .drop_duplicates(
            "ts"
        )
        .reset_index(
            drop=True
        )
    )

    raw["symbol"] = symbol

    raw["datetime"] = pd.to_datetime(
        raw["ts"],
        unit="s",
        utc=True,
    )

    return raw


# ============================================================
# DATASET WINDOWS
# ============================================================

def make_dataset_windows(
    cache,
):

    symbol_windows = {}

    for symbol, sdf in cache.groupby(
        "symbol",
        sort=True,
    ):

        symbol_windows[
            symbol
        ] = {
            "start": int(
                sdf["ts"].min()
            ),
            "end": int(
                sdf["ts"].max()
            ),
        }

    current_symbols = sorted(
        symbol_windows
    )

    if len(
        current_symbols
    ) != 35:
        raise RuntimeError(
            f"CURRENT symbol count != 35: "
            f"{len(current_symbols)}"
        )

    missing_long4 = [
        s
        for s in LONG4_SYMBOLS
        if s not in symbol_windows
    ]

    if missing_long4:
        raise RuntimeError(
            f"LONG4 symbols missing: "
            f"{missing_long4}"
        )

    common_start = max(
        symbol_windows[s][
            "start"
        ]
        for s in LONG4_SYMBOLS
    )

    common_end = min(
        symbol_windows[s][
            "end"
        ]
        for s in LONG4_SYMBOLS
    )

    if (
        common_end
        <= common_start
    ):
        raise RuntimeError(
            "LONG4 common window invalid"
        )

    long4_windows = {
        s: {
            "start": int(
                common_start
            ),
            "end": int(
                common_end
            ),
        }
        for s in LONG4_SYMBOLS
    }

    return {
        "CURRENT_35_FROM_2020": {
            "symbols":
                current_symbols,
            "windows":
                symbol_windows,
        },

        "LONG_4_5Y": {
            "symbols":
                LONG4_SYMBOLS.copy(),
            "windows":
                long4_windows,
        },
    }


def make_hard_pass_dataset_windows(cache, start_ts, end_ts):
    symbol_windows = {}
    for symbol, sdf in cache.groupby("symbol", sort=True):
        raw_start = int(sdf["ts"].min())
        raw_end = int(sdf["ts"].max())
        start = max(raw_start, int(start_ts))
        end = min(raw_end, int(end_ts))
        if end < start:
            continue
        symbol_windows[str(symbol).upper()] = {
            "start": start,
            "end": end,
        }

    if not symbol_windows:
        raise RuntimeError("HARD_PASS_RESEARCH_WINDOW_HAS_NO_SYMBOL_DATA")

    out = {
        HARD_PASS_DATASET: {
            "symbols": sorted(symbol_windows),
            "windows": symbol_windows,
        }
    }

    if all(sym in symbol_windows for sym in LONG4_SYMBOLS):
        common_start = max(symbol_windows[sym]["start"] for sym in LONG4_SYMBOLS)
        common_end = min(symbol_windows[sym]["end"] for sym in LONG4_SYMBOLS)
        if common_end >= common_start:
            out[HARD_PASS_LONG4_DATASET] = {
                "symbols": LONG4_SYMBOLS.copy(),
                "windows": {
                    sym: {"start": common_start, "end": common_end}
                    for sym in LONG4_SYMBOLS
                },
            }
    return out


# ============================================================
# EMA CROSS WARM-UP
# ============================================================

def add_ema_warmup_valid(
    source,
    lab,
    symbol,
    tf,
):

    d = source.copy()

    tfu = str(tf).upper()
    prefix = tfu.lower()
    valid_col = f"{prefix}_ema_valid"

    # 1M:
    # source 1? = ?? ?? 1?.
    if tfu == "1M":

        valid = np.zeros(
            len(d),
            dtype=np.int8,
        )

        if len(valid) >= 30:
            valid[29:] = 1

        d[valid_col] = valid

        return d

    # 1H ~ 1W:
    # ?? ?? TF ????
    # 30?? ??? timestamp? ???.
    p = lab.find_tf_file(
        DATA_DIR,
        symbol,
        prefix,
    )

    raw = lab.read_ohlc(
        p
    ).copy()

    raw["ts"] = lab.confirmed_ts_for_tf(
        raw,
        prefix,
    )

    raw = (
        raw.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )

    valid = np.zeros(
        len(d),
        dtype=np.int8,
    )

    if len(raw) >= 30:

        first_valid_ts = int(
            raw.iloc[29]["ts"]
        )

        valid[
            d["ts"].to_numpy(dtype=np.int64)
            >= first_valid_ts
        ] = 1

    d[valid_col] = valid

    return d


# ============================================================
# FRAME PREPARATION
# ============================================================

def source_frame_for_symbol(
    cache,
    lab,
    material_modules,
    symbol,
    tf,
    data_mode,
    monthly_cache,
):

    tfu = str(
        tf
    ).upper()

    mode = str(
        data_mode
    ).upper()

    if mode == "OHLCV":

        return load_raw_frame(
            lab,
            symbol,
            tfu,
        )

    if mode != "CACHE_MATERIALS":
        raise RuntimeError(
            f"unsupported data_mode: "
            f"{data_mode}"
        )

    if tfu != "1M":

        d = (
            cache[
                cache["symbol"]
                == symbol
            ]
            .copy()
            .reset_index(
                drop=True
            )
        )

        return d

    # Monthly cache-like frame는
    # 한 번 만든 뒤 dataset 간 재사용.
    if symbol not in monthly_cache:
        if not material_modules:
            material_modules.update(load_material_modules())

        monthly_cache[
            symbol
        ] = (
            build_monthly_material_frame(
                lab,
                material_modules,
                symbol,
            )
        )

    return monthly_cache[
        symbol
    ].copy()


# ============================================================
# DATASET / TF RUN
# ============================================================

def run_dataset_tf(
    cache,
    lab,
    material_modules,
    dataset,
    tf,
    fn,
    fee_rate,
    data_mode,
    monthly_cache,
    trailing=None,
    reentry=None,
):

    all_trades = []
    symbol_evidence = {}

    symbols = dataset[
        "symbols"
    ]

    windows = dataset[
        "windows"
    ]

    tested_symbols = 0
    source_rows = 0
    test_rows = 0

    for symbol in symbols:

        source = source_frame_for_symbol(
            cache=cache,
            lab=lab,
            material_modules=material_modules,
            symbol=symbol,
            tf=tf,
            data_mode=data_mode,
            monthly_cache=monthly_cache,
        )

        if len(source) == 0:
            continue

        # 중요:
        # signal 계산은 window 자르기 전에 수행.
        #
        # → 2020 이전 raw history가 있는 경우
        #   indicator warm-up 유지.
        #
        # → 이후 실제 테스트 기간만 자른다.

        source = add_ema_warmup_valid(
            source=source,
            lab=lab,
            symbol=symbol,
            tf=tf,
        )

        raw_signal = fn(
            source.copy(),
            str(tf).upper(),
        )

        signal_full = normalize_signal(
            raw_signal,
            source.index,
        )

        w = windows[
            symbol
        ]

        mask = (
            (
                source["ts"]
                >= int(w["start"])
            )
            &
            (
                source["ts"]
                <= int(w["end"])
            )
        ).to_numpy(
            dtype=bool
        )

        if not mask.any():
            continue

        d = (
            source.loc[
                mask
            ]
            .copy()
            .reset_index(
                drop=True
            )
        )

        signal = (
            signal_full[
                mask
            ]
        )

        trades = simulate(
            d,
            signal,
            fee_rate,
            trailing=trailing,
            reentry=reentry,
        )

        for x in trades:

            x["symbol"] = symbol
            x["timeframe"] = str(
                tf
            ).upper()

        all_trades.extend(
            trades
        )
        symbol_evidence[str(symbol).upper()] = {
            "summary": summarize(trades),
            "v3_robustness": v3_robustness_summary(trades, fee_rate),
            "entry_risk": entry_risk_summary(trades),
            "exit_research": exit_research_summary(trades),
        }

        tested_symbols += 1
        source_rows += int(
            len(source)
        )
        test_rows += int(
            len(d)
        )

    summary = summarize(
        all_trades
    )

    meta = {
        "tested_symbols":
            int(tested_symbols),
        "source_rows":
            int(source_rows),
        "test_rows":
            int(test_rows),
        "trailing_research": trailing if isinstance(trailing, dict) else None,
        "reentry_research": reentry if isinstance(reentry, dict) else None,
    }

    return (
        summary,
        all_trades,
        meta,
        symbol_evidence,
    )


# ============================================================
# PRINT
# ============================================================

def fmt_num(
    v,
    fmt,
):

    try:

        if v is None:
            return "N/A"

        x = float(v)

        if not np.isfinite(
            x
        ):
            return "N/A"

        return format(
            x,
            fmt,
        )

    except Exception:
        return "N/A"


def iso_ts(
    ts,
):

    return (
        pd.to_datetime(
            int(ts),
            unit="s",
            utc=True,
        )
        .isoformat()
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if len(
        sys.argv
    ) < 2:
        raise SystemExit(
            "strategy file required"
        )

    strategy_file = Path(
        sys.argv[1]
    )

    cfg = load_config()

    cache = load_cache(
        cfg["data_file"]
    )

    meta, fn = load_strategy(
        strategy_file
    )

    lab = load_lab_engine()

    # 재료 모듈은 월봉 CACHE_MATERIALS 경로에서만 필요하다.
    material_modules = {}

    hard_pass_window = hard_pass_window_from_env()
    individual_trailing = individual_trailing_from_env()
    individual_reentry = individual_reentry_from_env()

    if hard_pass_window is not None:
        datasets = make_hard_pass_dataset_windows(
            cache,
            hard_pass_window["start_ts"],
            hard_pass_window["end_ts"],
        )
    else:
        datasets = (
            make_dataset_windows(
                cache
            )
        )

    fee_rate = float(
        cfg.get(
            "fee_rate",
            0.0005,
        )
    )

    allowed = [
        str(x).upper()
        for x in meta.get(
            "allowed_timeframes",
            cfg["timeframes"],
        )
    ]

    configured_tfs = [
        str(x).upper()
        for x in cfg[
            "timeframes"
        ]
    ]

    data_mode = str(
        meta.get(
            "data_mode",
            "CACHE_MATERIALS",
        )
    ).upper()

    monthly_cache = {}

    print()
    print("=" * 96)
    print(
        " STRATEGY LAB V4 "
        "- LONG DATA / MONTHLY EXACT"
    )
    print("=" * 96)

    print(
        "strategy   =",
        meta.get(
            "name",
            strategy_file.stem,
        )
    )

    print(
        "data_mode  =",
        data_mode,
    )

    print(
        "cache      =",
        cfg["data_file"],
    )

    print(
        "rows       =",
        len(cache),
    )

    print(
        "symbols    =",
        cache["symbol"].nunique(),
    )

    print(
        "start      =",
        cache["datetime"].min(),
    )

    print(
        "end        =",
        cache["datetime"].max(),
    )

    print(
        "fee_rate   =",
        fee_rate,
    )

    print(
        "return     =",
        "LINEAR_USDM_UNLEVERAGED",
    )

    print(
        "portfolio  =",
        "DEFERRED_35_SLOT_SIMULATOR",
    )

    print("=" * 96)

    report = {
        "schema":
            "STRATEGY_LAB_RESULT_V4",

        "strategy":
            meta.get(
                "name",
                strategy_file.stem,
            ),

        "strategy_file":
            str(
                strategy_file
            ),

        "development_mode":
            meta.get(
                "development_mode",
                "UNSPECIFIED",
            ),

        "data_mode":
            data_mode,

        "fee_rate":
            fee_rate,

        "return_model":
            "LINEAR_USDM_UNLEVERAGED",

        "portfolio_metrics":
            "DEFERRED_35_SLOT_SIMULATOR",

        "tested_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "research_window": (
            {
                "mode": hard_pass_window["mode"],
                "requested_start": hard_pass_window["requested_start"],
                "requested_end": hard_pass_window["requested_end"],
            }
            if hard_pass_window is not None
            else {"mode": "LEGACY_DEFAULT"}
        ),

        "individual_trailing_research": individual_trailing,
        "individual_reentry_research": individual_reentry,

        "datasets": {},
    }

    for dataset_name, dataset in (
        datasets.items()
    ):

        windows = dataset[
            "windows"
        ]

        starts = [
            x["start"]
            for x in windows.values()
        ]

        ends = [
            x["end"]
            for x in windows.values()
        ]

        ds_report = {
            "symbol_count":
                int(
                    len(
                        dataset[
                            "symbols"
                        ]
                    )
                ),

            "symbols":
                list(
                    dataset[
                        "symbols"
                    ]
                ),

            "start":
                iso_ts(
                    min(starts)
                ),

            "end":
                iso_ts(
                    max(ends)
                ),

            "symbol_windows": {
                s: {
                    "start":
                        iso_ts(
                            windows[s][
                                "start"
                            ]
                        ),
                    "end":
                        iso_ts(
                            windows[s][
                                "end"
                            ]
                        ),
                }
                for s in dataset[
                    "symbols"
                ]
            },

            "timeframes": {},
        }

        if (
            dataset_name
            in {"LONG_4_5Y", HARD_PASS_LONG4_DATASET}
        ):

            common_start = min(
                x["start"]
                for x in windows.values()
            )

            common_end = max(
                x["end"]
                for x in windows.values()
            )

            ds_report[
                "common_years"
            ] = float(
                (
                    common_end
                    - common_start
                )
                / (
                    365.2425
                    * 86400.0
                )
            )

        print()
        print(
            "-" * 96
        )

        print(
            dataset_name,
            f"symbols={len(dataset['symbols'])}",
        )

        print(
            "-" * 96
        )

        for tf in configured_tfs:

            if tf not in allowed:
                continue

            summary, trades, run_meta, symbol_evidence = (
                run_dataset_tf(
                    cache=cache,
                    lab=lab,
                    material_modules=material_modules,
                    dataset=dataset,
                    tf=tf,
                    fn=fn,
                    fee_rate=fee_rate,
                    data_mode=data_mode,
                    monthly_cache=monthly_cache,
                    trailing=individual_trailing,
                    reentry=individual_reentry,
                )
            )

            ds_report[
                "timeframes"
            ][tf] = {
                "summary": summary,
                "run_meta": run_meta,
                "v3_robustness": v3_robustness_summary(trades, fee_rate),
                "entry_risk": entry_risk_summary(trades),
                "exit_research": exit_research_summary(trades),
                "symbol_evidence": symbol_evidence,
            }

            print(
                f"{tf:<4} "
                f"symbols={run_meta['tested_symbols']:<2} "
                f"rows={run_meta['test_rows']:<8} "
                f"trades={summary['trades']:<6} "
                f"L/S="
                f"{summary['long_trades']}/"
                f"{summary['short_trades']} "
                f"win="
                f"{fmt_num(summary['win_rate_pct'], '6.2f')}% "
                f"avg="
                f"{fmt_num(summary['avg_net_pct'], '+9.4f')}% "
                f"median="
                f"{fmt_num(summary['median_net_pct'], '+9.4f')}% "
                f"hold="
                f"{fmt_num(summary['avg_hold_hours'], '8.1f')}h "
                f"portfolio=DEFERRED"
            )

        report[
            "datasets"
        ][
            dataset_name
        ] = ds_report

    # --------------------------------------------------------
    # V3 호환 alias
    #
    # 기존 코드가 top-level ["timeframes"]를 읽더라도
    # CURRENT_35_FROM_2020 결과를 볼 수 있게 유지.
    # --------------------------------------------------------

    alias_dataset = (
        HARD_PASS_DATASET
        if hard_pass_window is not None
        else "CURRENT_35_FROM_2020"
    )

    report["timeframes"] = report["datasets"][alias_dataset]["timeframes"]

    RESULTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    name = str(
        report[
            "strategy"
        ]
    ).replace(
        " ",
        "_",
    )

    jf = (
        RESULTS
        / f"{stamp}_{name}.json"
    )

    jf.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 96)

    print(
        "RESULT_JSON =",
        jf,
    )

    print(
        "SCHEMA      =",
        report[
            "schema"
        ],
    )

    print(
        "V3_ALIAS    =",
        f"timeframes -> {alias_dataset}",
    )

    print("=" * 96)


if __name__ == "__main__":
    main()
