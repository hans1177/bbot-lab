# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_MEAN_REVERSION_Z', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'price z-score mean reversion with neutral trend filter', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); mean=c.rolling(30,min_periods=30).mean(); sd=c.rolling(30,min_periods=30).std(); z=(c-mean)/sd.replace(0,np.nan); slow=c.ewm(span=80,adjust=False).mean(); neutral=((slow/slow.shift(10)-1).abs()<0.04)
    out=np.zeros(len(d),dtype=np.int8); out[(neutral&(z<-2.0)).fillna(False).to_numpy()]=1; out[(neutral&(z>2.0)).fillna(False).to_numpy()]=-1; return out
