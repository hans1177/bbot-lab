#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일: /home/ec2-user/bot/assist/strategies/stoch.py
# 기능:
# - Stoch RSI 과매도/과매수 반전 모멘텀 점수 생성
# - 20 이하 상향크로스 후 롱 상태 유지
# - 80 이상 하향크로스 후 숏 상태 유지
# - 자체 다이버전스 계산 없음

# ================= IMPORT =================

import logging

import pandas as pd


# ================= LOG =================

logger = logging.getLogger("assist")


# ================= STOCH RSI =================

def calc_stoch_rsi(
    df,
    rsi_len=14,
    stoch_len=14,
    smooth_k=3,
    smooth_d=3,
):
    close = df["close"].astype(float)

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / rsi_len,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / rsi_len,
        adjust=False,
    ).mean().replace(0, 1e-9)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    rsi_min = rsi.rolling(stoch_len).min()
    rsi_max = rsi.rolling(stoch_len).max()

    denom = (rsi_max - rsi_min).replace(0, 1e-9)

    stoch = (rsi - rsi_min) / denom * 100

    k = stoch.rolling(smooth_k).mean()
    dline = k.rolling(smooth_d).mean()

    return k, dline


# ================= CROSS STATE =================

def _find_last_extreme_cross(k, dline):
    last_side = ""

    start = max(1, len(k) - 30)

    for i in range(start, len(k)):
        try:
            k_prev = float(k.iloc[i - 1])
            d_prev = float(dline.iloc[i - 1])

            k_now = float(k.iloc[i])
            d_now = float(dline.iloc[i])

            if any(
                pd.isna(v)
                for v in (k_prev, d_prev, k_now, d_now)
            ):
                continue

            # 과매도 구간에서 시작된 상향크로스.
            # 이번 봉 마감 K가 20 위로 올라가도 인정한다.
            if (
                k_prev <= 20.0
                and d_prev <= 20.0
                and k_prev <= d_prev
                and k_now > d_now
            ):
                last_side = "LONG"
                continue

            # 과매수 구간에서 시작된 하향크로스.
            # 이번 봉 마감 K가 80 아래로 내려가도 인정한다.
            if (
                k_prev >= 80.0
                and d_prev >= 80.0
                and k_prev >= d_prev
                and k_now < d_now
            ):
                last_side = "SHORT"

        except Exception:
            continue

    return last_side


# ================= SIGNAL =================

def signal(df):
    try:
        if df is None or len(df) < 40:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "LEN",
            }

        d = df.reset_index(drop=True).copy()

        k, dline = calc_stoch_rsi(d)

        k_prev = float(k.iloc[-2])
        k_now = float(k.iloc[-1])
        d_now = float(dline.iloc[-1])

        if any(
            pd.isna(v)
            for v in (k_prev, k_now, d_now)
        ):
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "NAN",
            }

        last_cross = _find_last_extreme_cross(k, dline)

        long_s = 0.0
        short_s = 0.0
        reason = "NO_SIGNAL"

        # ================= LONG =================
        # 20 이하에서 시작된 상향크로스가 가장 최근 크로스이고
        # K > D + K 상승 상태가 유지되는 동안 +1.
        if (
            last_cross == "LONG"
            and k_now > d_now
            and k_now > k_prev
        ):
            long_s = 1.0
            reason = "LONG_OVERSOLD_CROSS_HOLD"

        # ================= SHORT =================
        # 80 이상에서 시작된 하향크로스가 가장 최근 크로스이고
        # K < D + K 하락 상태가 유지되는 동안 -1 재료.
        elif (
            last_cross == "SHORT"
            and k_now < d_now
            and k_now < k_prev
        ):
            short_s = 1.0
            reason = "SHORT_OVERBOUGHT_CROSS_HOLD"

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