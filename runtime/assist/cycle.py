#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# FINAL CYCLE
# - 6H + 12H + 1D + 1W
# - MACD + EMA
# - TF weight = 1 / 2 / 3 / 4
# - BTC |0.12|
# - Aggregate |0.15|
# - BTC + Aggregate same direction
# - inactive -> keep stable Cycle
# - opposite active -> immediate flip
# - confirmed 1D approval OFF

import json
import os
import time

BASE_DIR = os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot")

STATE_FILE = os.path.join(
    BASE_DIR,
    "assist",
    "cycle.json",
)

META_FILE = os.path.join(
    BASE_DIR,
    "assist",
    "cycle_meta.json",
)

TIMEFRAMES = (
    "6h",
    "12h",
    "1d",
    "1w",
)

MATERIALS = (
    "macd",
    "ema",
)

TF_WEIGHT = {
    "6h": 1.0,
    "12h": 2.0,
    "1d": 3.0,
    "1w": 4.0,
}

BTC_THRESHOLD = 0.12
AGG_THRESHOLD = 0.15

CYCLE_VERSION = (
    "CYCLE_C_V13_STABLE_NO_D1_20260822"
)

MARKET_KEY = "__MARKET__"


def _f(value, default=0.0):
    try:
        value = float(value)

        if value != value:
            return float(default)

        return value

    except Exception:
        return float(default)


def _load(path):
    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        return (
            data
            if isinstance(data, dict)
            else {}
        )

    except Exception:
        return {}


def _save(path, data):
    os.makedirs(
        os.path.dirname(path),
        exist_ok=True,
    )

    tmp = (
        f"{path}."
        f"{os.getpid()}."
        f"{int(time.time() * 1000)}.tmp"
    )

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    os.replace(
        tmp,
        path,
    )


def _material_side_score(material):
    if not isinstance(material, dict):
        return 0.0

    long_score = max(
        0.0,
        _f(
            material.get(
                "strength_long"
            )
        ),
    )

    short_score = max(
        0.0,
        _f(
            material.get(
                "strength_short"
            )
        ),
    )

    score = long_score - short_score

    return max(
        -1.0,
        min(
            1.0,
            score,
        ),
    )


def calculate_score(scores):
    """
    V13 STRENGTH_TF_WEIGHTED.

    6H  MACD weight 1
    6H  EMA  weight 1

    12H MACD weight 2
    12H EMA  weight 2

    1D  MACD weight 3
    1D  EMA  weight 3

    1W  MACD weight 4
    1W  EMA  weight 4

    denominator
    = 1+1+2+2+3+3+4+4
    = 20
    """

    weighted_sum = 0.0
    weight_sum = 0.0
    detail = {}

    for tf in TIMEFRAMES:
        row = (
            scores.get(tf, {})
            if isinstance(scores, dict)
            else {}
        )

        weight = TF_WEIGHT[tf]
        tf_detail = {}

        for material_name in MATERIALS:
            raw = _material_side_score(
                row.get(material_name)
            )

            weighted = raw * weight

            weighted_sum += weighted
            weight_sum += weight

            tf_detail[material_name] = {
                "raw": round(
                    raw,
                    6,
                ),
                "weight": weight,
                "weighted": round(
                    weighted,
                    6,
                ),
            }

        detail[tf] = tf_detail

    score = (
        weighted_sum / weight_sum
        if weight_sum > 0
        else 0.0
    )

    score = max(
        -1.0,
        min(
            1.0,
            score,
        ),
    )

    if score > 0:
        direction = "LONG"

    elif score < 0:
        direction = "SHORT"

    else:
        direction = ""

    return {
        "raw_score": round(
            weighted_sum,
            6,
        ),
        "normalized_score": round(
            score,
            6,
        ),
        "raw_direction": direction,
        "score_detail": detail,
    }


def market_candidate(
    btc_score,
    aggregate_score,
):
    btc = _f(btc_score)
    agg = _f(aggregate_score)

    if (
        btc >= BTC_THRESHOLD
        and
        agg >= AGG_THRESHOLD
    ):
        return "LONG"

    if (
        btc <= -BTC_THRESHOLD
        and
        agg <= -AGG_THRESHOLD
    ):
        return "SHORT"

    return ""


def update_market(
    candidate_direction,
    btc_score,
    aggregate_score,
    aggregate_count=0,
):
    """
    최종 선택된 V16 방식.

    C 비활성:
        기존 Cycle 유지

    C 동일방향:
        기존 Cycle 유지

    C 반대방향 활성:
        즉시 전환

    confirmed 1D:
        사용 안 함
    """

    now = int(
        time.time()
    )

    state = _load(
        STATE_FILE
    )

    meta = _load(
        META_FILE
    )

    old_cycle = state.get(
        MARKET_KEY
    )

    if old_cycle not in (
        "LONG_CYCLE",
        "SHORT_CYCLE",
    ):
        old_cycle = None

    if candidate_direction == "LONG":
        candidate_cycle = "LONG_CYCLE"

    elif candidate_direction == "SHORT":
        candidate_cycle = "SHORT_CYCLE"

    else:
        candidate_cycle = None

    stable_cycle = old_cycle
    changed = False

    if stable_cycle is None:

        if candidate_cycle:
            stable_cycle = candidate_cycle
            changed = True
            reason = "INIT_C_ACTIVE"

        else:
            reason = "INIT_WAIT_C_ACTIVE"

    elif candidate_cycle is None:

        reason = "C_INACTIVE_KEEP"

    elif candidate_cycle == stable_cycle:

        reason = "C_SAME_KEEP"

    else:

        stable_cycle = candidate_cycle
        changed = True
        reason = "FLIP_C_ACTIVE_IMMEDIATE"

    if stable_cycle:

        state[MARKET_KEY] = (
            stable_cycle
        )

        _save(
            STATE_FILE,
            state,
        )

    old_meta = meta.get(
        MARKET_KEY
    )

    old_meta = (
        old_meta
        if isinstance(
            old_meta,
            dict,
        )
        else {}
    )

    if changed:
        stable_since = now

    else:
        stable_since = int(
            old_meta.get(
                "stable_since"
            )
            or now
        )

    row = {
        "stable_cycle": stable_cycle,

        "candidate_direction":
            candidate_direction,

        "btc_score": round(
            _f(btc_score),
            6,
        ),

        "aggregate_score": round(
            _f(aggregate_score),
            6,
        ),

        "aggregate_count": int(
            aggregate_count or 0
        ),

        "btc_threshold":
            BTC_THRESHOLD,

        "aggregate_threshold":
            AGG_THRESHOLD,

        "changed": int(
            changed
        ),

        "reason": reason,

        "stable_since": int(
            stable_since
        ),

        "stable_age_sec": int(
            now - stable_since
        ),

        "updated_ts": now,

        "cycle_version":
            CYCLE_VERSION,

        # gate compatibility
        "normalized_score": round(
            _f(btc_score),
            6,
        ),

        "raw_score": round(
            _f(btc_score),
            6,
        ),

        "raw_direction":
            candidate_direction,

        "d1_confirmed_direction": "",
    }

    meta[MARKET_KEY] = row

    _save(
        META_FILE,
        meta,
    )

    return stable_cycle


def get(symbol):
    symbol = str(
        symbol or ""
    ).upper().strip()

    value = _load(
        STATE_FILE
    ).get(symbol)

    if value in (
        "LONG_CYCLE",
        "SHORT_CYCLE",
    ):
        return value

    return None


def get_meta(symbol=None):
    meta = _load(
        META_FILE
    )

    if symbol is None:
        return meta

    symbol = str(
        symbol or ""
    ).upper().strip()

    return meta.get(
        symbol
    )
