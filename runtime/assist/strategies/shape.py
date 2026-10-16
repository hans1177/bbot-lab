#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일: /home/ec2-user/bot/assist/strategies/shape.py
# 기능:
# - 확정봉 캔들 반전 형태를 사이클 보조점수로 변환
# - 아래꼬리 / 위꼬리 / 상승장악 / 하락장악 판단
# - SHAPE 전체 최대 점수는 롱 0.5 또는 숏 0.5
# - 도지는 점수 없음

# ================= IMPORT =================

import logging

import pandas as pd


# ================= LOG =================

logger = logging.getLogger("assist")


# ================= SIGNAL =================

def signal(df: pd.DataFrame):
    try:
        if df is None or len(df) < 2:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "LEN",
            }

        d = df.reset_index(drop=True)

        cur = d.iloc[-1]
        prev = d.iloc[-2]

        o = float(cur["open"])
        c = float(cur["close"])
        h = float(cur["high"])
        l = float(cur["low"])

        o_prev = float(prev["open"])
        c_prev = float(prev["close"])

        body = abs(c - o)
        total = h - l

        if total <= 0:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "TOTAL_ZERO",
            }

        upper = h - max(o, c)
        lower = min(o, c) - l

        body_ratio = body / total
        is_doji = body_ratio < 0.10

        long_hit = False
        short_hit = False
        reasons = []

        # ================= DOJI =================
        # 도지는 방향성이 약하므로 점수 없음.
        if is_doji:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "DOJI",
            }

        # ================= WICK =================

        if (
            lower > body
            and lower >= upper * 1.2
        ):
            long_hit = True
            reasons.append("LOWER_WICK_LONG")

        if (
            upper > body
            and upper >= lower * 1.2
        ):
            short_hit = True
            reasons.append("UPPER_WICK_SHORT")

        # ================= ENGULF =================

        prev_body_low = min(o_prev, c_prev)
        prev_body_high = max(o_prev, c_prev)

        cur_body_low = min(o, c)
        cur_body_high = max(o, c)

        if (
            c_prev < o_prev
            and c > o
            and cur_body_low <= prev_body_low
            and cur_body_high >= prev_body_high
        ):
            long_hit = True
            reasons.append("ENGULF_LONG")

        if (
            c_prev > o_prev
            and c < o
            and cur_body_low <= prev_body_low
            and cur_body_high >= prev_body_high
        ):
            short_hit = True
            reasons.append("ENGULF_SHORT")

        # ================= FINAL =================
        # 한 봉에서 롱/숏 조건이 동시에 나오면 방향 불명확 → 0점.

        if long_hit and short_hit:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "CONFLICT",
            }

        if long_hit:
            return {
                "signal": "wait",
                "strength_long": 0.5,
                "strength_short": 0.0,
                "reason": "+".join(reasons),
            }

        if short_hit:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.5,
                "reason": "+".join(reasons),
            }

        return {
            "signal": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": "NONE",
        }

    except Exception as e:
        return {
            "signal": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": f"ERROR:{e}",
        }