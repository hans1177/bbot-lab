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
STRATEGY_META={"name":"V3_VOLUME_PRESSURE","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"signed volume pressure confirmed by price displacement","allowed_timeframes":["1H","6H","12H","1D","1W"]}

def generate_signals(df,tf):
    c=_num(df["close"]); o=_num(df["open"]); v=_num(df["volume"]); direction=np.sign((c-o).fillna(0)); flow=(direction*v).rolling(20,min_periods=20).sum(); base=v.rolling(20,min_periods=20).sum().replace(0,np.nan); p=flow/base; ret=c.pct_change(10); out=np.zeros(len(df),dtype=np.int8); out[((p>0.20)&(ret>0)).fillna(False).to_numpy()]=1; out[((p<-0.20)&(ret<0)).fillna(False).to_numpy()]=-1; return out
