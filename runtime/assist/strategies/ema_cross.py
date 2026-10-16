#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: assist/strategies/ema_cross.py

import pandas as pd

def signal(df):
    try:
        if df is None or len(df) < 30:
            return {
                "signal": "wait",
                "state": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "LEN",
            }

        d = df.reset_index(drop=True).copy()
        close = d["close"].astype(float)

        ema13 = close.ewm(
            span=13,
            adjust=False,
        ).mean()

        ema24 = close.ewm(
            span=24,
            adjust=False,
        ).mean()

        e13_prev = float(ema13.iloc[-2])
        e24_prev = float(ema24.iloc[-2])
        e13_now = float(ema13.iloc[-1])
        e24_now = float(ema24.iloc[-1])

        vals = [
            e13_prev,
            e24_prev,
            e13_now,
            e24_now,
        ]

        if any(pd.isna(x) for x in vals):
            return {
                "signal": "wait",
                "state": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "NAN",
            }

        # ----------------------------------------------------
        # EMA STATE
        # ----------------------------------------------------

        if e13_now > e24_now:
            state = "long"

        elif e13_now < e24_now:
            state = "short"

        else:
            state = "wait"

        # ----------------------------------------------------
        # EMA CROSS EVENT
        # ----------------------------------------------------

        if (
            e13_prev <= e24_prev
            and e13_now > e24_now
        ):
            return {
                "signal": "wait",
                "state": state,
                "strength_long": 1.0,
                "strength_short": 0.0,
                "reason": "EMA13_CROSS_UP_EMA24",
            }

        if (
            e13_prev >= e24_prev
            and e13_now < e24_now
        ):
            return {
                "signal": "wait",
                "state": state,
                "strength_long": 0.0,
                "strength_short": 1.0,
                "reason": "EMA13_CROSS_DOWN_EMA24",
            }

        return {
            "signal": "wait",
            "state": state,
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": "NO_CROSS",
        }

    except Exception as e:
        return {
            "signal": "wait",
            "state": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": f"ERROR:{e}",
        }
