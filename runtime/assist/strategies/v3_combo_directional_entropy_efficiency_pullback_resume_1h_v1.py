# PORTABLE_RUNTIME_ARTIFACT_V1
# -*- coding: utf-8 -*-
# PORTABLE_GENERATED_COMBINATION_V1
# AUTO-GENERATED BOUNDED COMBINATION RESEARCH.
# Component implementation is embedded for portable runtime packaging.

import sys
import types
import numpy as np


def _embedded_module(source, name):

    mod = types.ModuleType(name)

    mod.__file__ = (
        "<embedded:"
        + name
        + ">"
    )

    mod.__package__ = ""

    sys.modules[
        name
    ] = mod

    exec(
        compile(
            source,
            mod.__file__,
            "exec",
        ),
        mod.__dict__,
    )

    return mod


_SRC_A = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\ndef _num(s): return pd.to_numeric(s,errors="coerce")\ndef _sig(v,name):\n    if int(v)>0:return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":name}\n    if int(v)<0:return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":name}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":name}\ndef signal(df,params=None):\n    a=generate_signals(df.copy(),"LIVE"); return _sig(a[-1] if len(a) else 0,STRATEGY_META["name"])\nSTRATEGY_META={"name":"V3_DIRECTIONAL_ENTROPY","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"low direction entropy trend persistence","allowed_timeframes":["1H","6H","12H","1D","1W"]}\n\ndef generate_signals(df,tf):\n    c=_num(df["close"]); r=c.diff(); up=(r>0).astype(float); p=up.rolling(20,min_periods=20).mean().clip(1e-6,1-1e-6); ent=-(p*np.log(p)+(1-p)*np.log(1-p)); mom=c.pct_change(20); out=np.zeros(len(df),dtype=np.int8); out[((ent<0.60)&(mom>0)).fillna(False).to_numpy()]=1; out[((ent<0.60)&(mom<0)).fillna(False).to_numpy()]=-1; return out\n'

_SRC_B = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\ndef _num(s): return pd.to_numeric(s,errors="coerce")\ndef _sig(v,name):\n    if int(v)>0:return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":name}\n    if int(v)<0:return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":name}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":name}\ndef signal(df,params=None):\n    a=generate_signals(df.copy(),"LIVE"); return _sig(a[-1] if len(a) else 0,STRATEGY_META["name"])\nSTRATEGY_META={"name":"V3_EFFICIENCY_PULLBACK_RESUME","development_mode":"AUTO_DISCOVERY_WAVE2","data_mode":"OHLCV","description":"efficient directional move, shallow pullback, resume","allowed_timeframes":["1H","6H","12H","1D","1W"]}\n\ndef generate_signals(df,tf):\n    c=_num(df["close"]); change=(c-c.shift(20)).abs(); path=c.diff().abs().rolling(20,min_periods=20).sum().replace(0,np.nan); er=change/path; trend=c-c.shift(20); hi=c.rolling(10,min_periods=10).max(); lo=c.rolling(10,min_periods=10).min(); resume_up=(c>c.shift(1))&(c.shift(1)<hi.shift(1)); resume_dn=(c<c.shift(1))&(c.shift(1)>lo.shift(1)); out=np.zeros(len(df),dtype=np.int8); out[((er>0.45)&(trend>0)&resume_up).fillna(False).to_numpy()]=1; out[((er>0.45)&(trend<0)&resume_dn).fillna(False).to_numpy()]=-1; return out\n'


A = _embedded_module(
    _SRC_A,
    '_bbot_v3_combo_directional_entropy_efficiency_pullback_resume_1h_v1_a',
)

B = _embedded_module(
    _SRC_B,
    '_bbot_v3_combo_directional_entropy_efficiency_pullback_resume_1h_v1_b',
)


STRATEGY_META = {'name': 'V3_COMBO_DIRECTIONAL_ENTROPY_EFFICIENCY_PULLBACK_RESUME_1H_V1', 'development_mode': 'BOUNDED_COMPLEMENTARY_COMBINATION', 'data_mode': 'OHLCV', 'allowed_timeframes': ['1H'], 'components': ['V3_DIRECTIONAL_ENTROPY', 'V3_EFFICIENCY_PULLBACK_RESUME']}


def generate_signals(df, tf):

    x = A.generate_signals(
        df,
        tf,
    )

    y = B.generate_signals(
        df,
        tf,
    )

    out = np.zeros(
        len(df),
        dtype=np.int8,
    )

    out[
        (x > 0)
        & (y > 0)
    ] = 1

    out[
        (x < 0)
        & (y < 0)
    ] = -1

    return out


def signal(df, params=None):

    z = generate_signals(
        df,
        "LIVE",
    )

    v = (
        int(
            z[-1]
        )
        if len(z)
        else 0
    )

    return {
        "signal":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "state":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "strength_long":
            1.0
            if v > 0
            else 0.0,

        "strength_short":
            1.0
            if v < 0
            else 0.0,

        "reason":
            STRATEGY_META[
                "name"
            ],
    }
