# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

def _num(s): return pd.to_numeric(s,errors="coerce")
def _sig(v,name):
    if int(v)>0:return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":name}
    if int(v)<0:return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":name}
    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":name}
def signal(df,params=None):
    a=generate_signals(df.copy(),"LIVE"); return _sig(a[-1] if len(a) else 0,STRATEGY_META["name"])
STRATEGY_META={"name":"V3_EFFICIENCY_PULLBACK_RESUME","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"efficient directional move, shallow pullback, resume","allowed_timeframes":["1H","6H","12H","1D","1W"]}

def generate_signals(df,tf):
    c=_num(df["close"]); change=(c-c.shift(20)).abs(); path=c.diff().abs().rolling(20,min_periods=20).sum().replace(0,np.nan); er=change/path; trend=c-c.shift(20); hi=c.rolling(10,min_periods=10).max(); lo=c.rolling(10,min_periods=10).min(); resume_up=(c>c.shift(1))&(c.shift(1)<hi.shift(1)); resume_dn=(c<c.shift(1))&(c.shift(1)>lo.shift(1)); out=np.zeros(len(df),dtype=np.int8); out[((er>0.45)&(trend>0)&resume_up).fillna(False).to_numpy()]=1; out[((er>0.45)&(trend<0)&resume_dn).fillna(False).to_numpy()]=-1; return out
