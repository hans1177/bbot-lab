#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일: /home/ec2-user/bot/assist/strategies/div.py
# 기능:
# - 확정봉 전용 강한 다이버전스 탐지
# - 가격 LL/HH와 Stoch RSI + OBV 다이버전스가 동시에 성립할 때만 인정
# - 사이클 점수용 공통 반환 형식 사용

# ================= IMPORT =================

import pandas as pd


# ================= INDICATOR =================

def calc_stochrsi(df, length=20):
    close = df["close"].astype(float)

    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = diff.clip(upper=0).abs()

    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()

    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    min_rsi = rsi.rolling(length).min()
    max_rsi = rsi.rolling(length).max()

    return (
        (rsi - min_rsi)
        / (max_rsi - min_rsi + 1e-9)
        * 100
    ).fillna(50.0)


def calc_obv(df):
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    obv = [0.0]

    for i in range(1, len(df)):
        if close.iloc[i] > close.iloc[i - 1]:
            obv.append(obv[-1] + volume.iloc[i])

        elif close.iloc[i] < close.iloc[i - 1]:
            obv.append(obv[-1] - volume.iloc[i])

        else:
            obv.append(obv[-1])

    return pd.Series(obv, index=df.index)


# ================= SIGNAL =================

def signal(df):
    try:
        if df is None or len(df) < 50:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "LEN",
            }

        d = df.reset_index(drop=True).copy()

        # 확정봉 전용 입력을 기준으로 마지막 2개 확정봉 비교.
        prev = -2
        cur = -1

        low_prev = float(d["low"].iloc[prev])
        low_cur = float(d["low"].iloc[cur])

        high_prev = float(d["high"].iloc[prev])
        high_cur = float(d["high"].iloc[cur])

        stoch = calc_stochrsi(d)
        obv = calc_obv(d)

        st_prev = float(stoch.iloc[prev])
        st_cur = float(stoch.iloc[cur])

        obv_prev = float(obv.iloc[prev])
        obv_cur = float(obv.iloc[cur])

        long_stoch = False
        long_obv = False

        short_stoch = False
        short_obv = False

        # ================= LONG =================
        # 가격은 더 낮은 저점 LL
        # Stoch는 더 높은 저점 HL
        # OBV도 더 높은 저점 HL
        if low_cur < low_prev:
            long_stoch = st_cur > st_prev
            long_obv = obv_cur > obv_prev

        # ================= SHORT =================
        # 가격은 더 높은 고점 HH
        # Stoch는 더 낮은 고점 LH
        # OBV도 더 낮은 고점 LH
        if high_cur > high_prev:
            short_stoch = st_cur < st_prev
            short_obv = obv_cur < obv_prev

        long_hit = long_stoch and long_obv
        short_hit = short_stoch and short_obv

        # 충돌 시 무효.
        if long_hit and short_hit:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "CONFLICT",
            }

        if long_hit:
            return {
                "signal": "BUY",
                "strength_long": 1.0,
                "strength_short": 0.0,
                "reason": "DIV_LONG_STOCH_OBV",
            }

        if short_hit:
            return {
                "signal": "SELL",
                "strength_long": 0.0,
                "strength_short": 1.0,
                "reason": "DIV_SHORT_STOCH_OBV",
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