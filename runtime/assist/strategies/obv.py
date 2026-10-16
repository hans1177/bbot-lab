import pandas as pd
import logging

logger = logging.getLogger("assist")

def calc_obv(df):
    obv = [0]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > df["close"].iloc[i-1]:
            obv.append(obv[-1] + df["volume"].iloc[i])
        elif df["close"].iloc[i] < df["close"].iloc[i-1]:
            obv.append(obv[-1] - df["volume"].iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv)

def signal(df):
    try:
        if df is None or len(df) < 30:
            return {"signal":"wait","strength_long":0.0,"strength_short":0.0,"reason":"LEN"}

        d = df.reset_index(drop=True)

        obv = calc_obv(d)

        obv_prev = float(obv.iloc[-2])
        obv_now  = float(obv.iloc[-1])

        price_prev = float(d["close"].iloc[-2])
        price_now  = float(d["close"].iloc[-1])

        long_s, short_s = 0.0, 0.0
        reasons = []

        # =========================
        # 변화량 필터
        # =========================
        diff = abs(obv_now - obv_prev)
        if diff < (abs(obv_prev) * 0.005):
            return {
                "signal":"wait",
                "strength_long":0.0,
                "strength_short":0.0,
                "reason":"FILTER"
            }

        # =========================
        # 방향
        # =========================
        if obv_now > obv_prev:
            long_s += 1.0
            reasons.append("DIR_LONG")
        elif obv_now < obv_prev:
            short_s += 1.0
            reasons.append("DIR_SHORT")

        # =========================
        # 연속 흐름
        # =========================
        if obv_now > obv_prev and obv_prev > obv.iloc[-3]:
            long_s += 0.5
            reasons.append("FLOW_LONG")

        if obv_now < obv_prev and obv_prev < obv.iloc[-3]:
            short_s += 0.5
            reasons.append("FLOW_SHORT")

        # =========================
        # 다이버전스
        # =========================
        if price_now < price_prev and obv_now > obv_prev:
            long_s += 0.5
            reasons.append("DIV_LONG")

        if price_now > price_prev and obv_now < obv_prev:
            short_s += 0.5
            reasons.append("DIV_SHORT")

        return {
            "signal":"wait",
            "strength_long":round(long_s,2),
            "strength_short":round(short_s,2),
            "reason":"+".join(reasons) if reasons else "NONE"
        }

    except Exception as e:
        return {
            "signal":"wait",
            "strength_long":0.0,
            "strength_short":0.0,
            "reason":f"ERROR:{e}"
        }