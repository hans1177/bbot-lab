# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_MOMENTUM_ACCEL', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'multi-horizon momentum acceleration', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); r3=c.pct_change(3); r9=c.pct_change(9); r18=c.pct_change(18); accel=r3-r9/3.0
    out=np.zeros(len(d),dtype=np.int8); out[((r18>0)&(r9>0)&(r3>0)&(accel>0)).fillna(False).to_numpy()]=1; out[((r18<0)&(r9<0)&(r3<0)&(accel<0)).fillna(False).to_numpy()]=-1; return out
