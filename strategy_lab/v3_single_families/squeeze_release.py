# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_SQUEEZE_RELEASE', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'volatility contraction followed by directional release', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); h=_num(d["high"]); l=_num(d["low"])
    mid=c.rolling(20,min_periods=20).mean(); sd=c.rolling(20,min_periods=20).std(); bw=(4*sd/mid.replace(0,np.nan)).abs(); thresh=bw.rolling(120,min_periods=40).quantile(0.25)
    squeezed=(bw.shift(1)<=thresh.shift(1)); upper=h.rolling(20,min_periods=20).max().shift(1); lower=l.rolling(20,min_periods=20).min().shift(1)
    out=np.zeros(len(d),dtype=np.int8); out[(squeezed&(c>upper)).fillna(False).to_numpy()]=1; out[(squeezed&(c<lower)).fillna(False).to_numpy()]=-1; return out
