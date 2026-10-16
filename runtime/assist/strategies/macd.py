#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일: /home/ec2-user/bot/assist/strategies/macd.py
# 기능:
# - MACD 히스토그램 약화 구간을 이용한 사이클 전환 재료 생성
# - EMA13 아래에서는 롱 신호만 인정
# - EMA13 위에서는 숏 신호만 인정
# - 음수→첫 양수, 양수→첫 음수 막대까지만 신호 인정

# ================= IMPORT =================

import logging

import pandas as pd


# ================= LOG =================

logger = logging.getLogger("assist")


# ================= MACD =================

def calc_macd(df, fast=12, slow=26, signal=9):
    close = df["close"].astype(float)

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()

    return macd - sig


# ================= SIGNAL =================

def signal(df):
    try:
        if df is None or len(df) < 30:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "LEN",
            }

        d = df.reset_index(drop=True).copy()

        close = d["close"].astype(float)
        ema13 = close.ewm(span=13, adjust=False).mean()
        hist = calc_macd(d)

        price = float(close.iloc[-1])
        ema13_now = float(ema13.iloc[-1])

        h_prev = float(hist.iloc[-2])
        h_now = float(hist.iloc[-1])

        long_s = 0.0
        short_s = 0.0
        reason = "NO_SIGNAL"

        # ================= LONG =================
        # 가격이 EMA13 아래에 있을 때만 롱 인정.
        #
        # 1) 음수 히스토그램이 0 방향으로 약화
        #    -0.50 -> -0.30
        #
        # 2) 음수 -> 첫 양수 막대
        #    -0.10 -> +0.03
        #
        # 첫 양수 막대 이후 양수 구간은 점수 없음.
        if price < ema13_now:
            if h_prev < 0.0 and h_now < 0.0 and h_now > h_prev:
                long_s = 1.0
                reason = "LONG_NEGATIVE_WEAKEN"

            elif h_prev < 0.0 and h_now >= 0.0:
                long_s = 1.0
                reason = "LONG_FIRST_POSITIVE"

        # ================= SHORT =================
        # 가격이 EMA13 위에 있을 때만 숏 인정.
        #
        # 1) 양수 히스토그램이 0 방향으로 약화
        #    +0.50 -> +0.30
        #
        # 2) 양수 -> 첫 음수 막대
        #    +0.10 -> -0.03
        #
        # 첫 음수 막대 이후 음수 구간은 점수 없음.
        elif price > ema13_now:
            if h_prev > 0.0 and h_now > 0.0 and h_now < h_prev:
                short_s = 1.0
                reason = "SHORT_POSITIVE_WEAKEN"

            elif h_prev > 0.0 and h_now <= 0.0:
                short_s = 1.0
                reason = "SHORT_FIRST_NEGATIVE"

        return {
            "signal": "wait",
            "strength_long": long_s,
            "strength_short": short_s,
            "reason": reason,
        }

    except Exception as e:
        return {
            "signal": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": f"ERROR:{e}",
        }