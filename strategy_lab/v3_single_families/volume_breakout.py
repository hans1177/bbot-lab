# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_VOLUME_BREAKOUT', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'volume expansion plus short channel breakout', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); h=_num(d["high"]); l=_num(d["low"]); v=_num(d.get("volume",pd.Series(index=d.index,dtype=float))); vm=v.rolling(30,min_periods=20).median(); up=h.rolling(10,min_periods=10).max().shift(1); dn=l.rolling(10,min_periods=10).min().shift(1); hv=v>1.8*vm
    out=np.zeros(len(d),dtype=np.int8); out[(hv&(c>up)).fillna(False).to_numpy()]=1; out[(hv&(c<dn)).fillna(False).to_numpy()]=-1; return out
