# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_RANGE_EXPANSION', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'large true-range directional close location expansion', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); h=_num(d["high"]); l=_num(d["low"]); c=_num(d["close"]); pc=c.shift(1); tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); med=tr.rolling(30,min_periods=20).median(); loc=(c-l)/(h-l).replace(0,np.nan)
    out=np.zeros(len(d),dtype=np.int8); out[((tr>1.6*med)&(loc>0.8)&(c>pc)).fillna(False).to_numpy()]=1; out[((tr>1.6*med)&(loc<0.2)&(c<pc)).fillna(False).to_numpy()]=-1; return out
