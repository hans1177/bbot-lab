# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_ADAPTIVE_CHANNEL', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'ATR-normalized adaptive trend channel breakout', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); h=_num(d["high"]); l=_num(d["low"]); pc=c.shift(1); tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(span=20,adjust=False).mean(); ema=c.ewm(span=32,adjust=False).mean(); slope=ema-ema.shift(6); upper=ema+1.2*atr; lower=ema-1.2*atr
    out=np.zeros(len(d),dtype=np.int8); out[((c>upper)&(slope>0)).fillna(False).to_numpy()]=1; out[((c<lower)&(slope<0)).fillna(False).to_numpy()]=-1; return out
