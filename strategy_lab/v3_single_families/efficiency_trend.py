# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_EFFICIENCY_TREND', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'Kaufman-style directional efficiency with trend slope', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

def _num(s):
    return pd.to_numeric(s, errors="coerce")

def _signal_dict(v, reason):
    if int(v)>0: return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":reason}
    if int(v)<0: return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":reason}
    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":reason}

def signal(df, params=None):
    arr=generate_signals(df.copy(), "LIVE")
    return _signal_dict(arr[-1] if len(arr) else 0, STRATEGY_META["name"])

def generate_signals(df, tf):
    d=df.copy(); c=_num(d["close"]); change=(c-c.shift(20)).abs(); noise=c.diff().abs().rolling(20,min_periods=20).sum(); er=change/noise.replace(0,np.nan); ema=c.ewm(span=30,adjust=False).mean(); slope=ema-ema.shift(5)
    out=np.zeros(len(d),dtype=np.int8); out[((er>0.35)&(slope>0)&(c>ema)).fillna(False).to_numpy()]=1; out[((er>0.35)&(slope<0)&(c<ema)).fillna(False).to_numpy()]=-1; return out
