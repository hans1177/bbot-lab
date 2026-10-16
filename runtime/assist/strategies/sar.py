import pandas as pd
import logging

logger = logging.getLogger("assist")

def signal(df: pd.DataFrame):
    try:
        if len(df) < 5:
            return {
                "signal": "wait",
                "strength_long": 0.0,
                "strength_short": 0.0,
                "reason": "SAR_NO_DATA"
            }

        d = df.reset_index(drop=True)

        AF_STEP = 0.02
        AF_MAX  = 0.2

        trend = None
        af = AF_STEP
        ep = None
        sar = None

        for i in range(1, len(d)):
            high = float(d["high"].iloc[i])
            low  = float(d["low"].iloc[i])
            close_prev = float(d["close"].iloc[i - 1])

            if sar is None:
                if close_prev > float(d["close"].iloc[i - 2]):
                    trend = "long"
                    ep = high
                    sar = float(d["low"].iloc[i - 1])
                else:
                    trend = "short"
                    ep = low
                    sar = float(d["high"].iloc[i - 1])
                continue

            sar = sar + af * (ep - sar)

            if trend == "long":
                sar = min(sar, float(d["low"].iloc[i - 1]), float(d["low"].iloc[i - 2]))

                if low < sar:
                    trend = "short"
                    sar = ep
                    ep = low
                    af = AF_STEP
                else:
                    if high > ep:
                        ep = high
                        af = min(af + AF_STEP, AF_MAX)

            else:
                sar = max(sar, float(d["high"].iloc[i - 1]), float(d["high"].iloc[i - 2]))

                if high > sar:
                    trend = "long"
                    sar = ep
                    ep = high
                    af = AF_STEP
                else:
                    if low < ep:
                        ep = low
                        af = min(af + AF_STEP, AF_MAX)

        close_conf = float(d["close"].iloc[-1])

        long_score = 0.0
        short_score = 0.0
        reasons = []

        if trend == "long" and close_conf > sar:
            long_score = 0.5
            reasons.append("SAR_STATE_LONG")

        if trend == "short" and close_conf < sar:
            short_score = 0.5
            reasons.append("SAR_STATE_SHORT")

        logger.info(
            f"[SAR_TRACE] trend={trend} sar={sar} close_conf={close_conf} "
            f"long={long_score} short={short_score}"
        )

        return {
            "signal": "wait",
            "strength_long": long_score,
            "strength_short": short_score,
            "reason": "+".join(reasons)
        }

    except Exception as e:
        return {
            "signal": "wait",
            "strength_long": 0.0,
            "strength_short": 0.0,
            "reason": f"ERROR:{e}"
        }
