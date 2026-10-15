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
STRATEGY_META={"name":"V3_MEDIAN_DISPLACEMENT","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"robust median displacement with range normalization","allowed_timeframes":["1H","6H","12H","1D","1W"]}

def generate_signals(df,tf):
    c=_num(df["close"]); h=_num(df["high"]); l=_num(df["low"]); med=c.rolling(30,min_periods=30).median(); ar=(h-l).rolling(20,min_periods=20).mean().replace(0,np.nan); z=(c-med)/ar; out=np.zeros(len(df),dtype=np.int8); out[(z>1.5).fillna(False).to_numpy()]=1; out[(z<-1.5).fillna(False).to_numpy()]=-1; return out
