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
STRATEGY_META={"name":"V3_LINEAR_SLOPE_TREND","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"rolling linear-regression slope normalized by price","allowed_timeframes":["1H","6H","12H","1D","1W"]}

def generate_signals(df,tf):
    c=_num(df["close"]); n=24; idx=np.arange(n,dtype=float); den=((idx-idx.mean())**2).sum()
    slope=c.rolling(n,min_periods=n).apply(lambda x: float(((idx-idx.mean())*(x-x.mean())).sum()/den),raw=True); norm=slope/c.rolling(n,min_periods=n).mean(); out=np.zeros(len(df),dtype=np.int8); out[(norm>0.001).fillna(False).to_numpy()]=1; out[(norm<-0.001).fillna(False).to_numpy()]=-1; return out
