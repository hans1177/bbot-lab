# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

STRATEGY_META = {'name': 'V3_FRACTAL_BREAKOUT', 'development_mode': 'AUTO_DISCOVERY', 'data_mode': 'OHLCV', 'description': 'confirmed local fractal break without indicator dependency', 'allowed_timeframes': ['1H', '6H', '12H', '1D', '1W']}

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
    d=df.copy(); c=_num(d["close"]); h=_num(d["high"]); l=_num(d["low"]); fh=h.shift(2).where((h.shift(2)>h.shift(3))&(h.shift(2)>h.shift(4))&(h.shift(2)>=h.shift(1))&(h.shift(2)>=h)); fl=l.shift(2).where((l.shift(2)<l.shift(3))&(l.shift(2)<l.shift(4))&(l.shift(2)<=l.shift(1))&(l.shift(2)<=l)); last_h=fh.ffill().shift(1); last_l=fl.ffill().shift(1)
    out=np.zeros(len(d),dtype=np.int8); out[(c>last_h).fillna(False).to_numpy()]=1; out[(c<last_l).fillna(False).to_numpy()]=-1; return out
