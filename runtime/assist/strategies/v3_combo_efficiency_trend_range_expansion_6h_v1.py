# -*- coding: utf-8 -*-
# PORTABLE_RUNTIME_ARTIFACT_V1
# Compiled from a validated generated-combination source.
# Component implementation is embedded; no research path is required at runtime.

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


_SRC_A = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\nSTRATEGY_META = {\'name\': \'V3_EFFICIENCY_TREND\', \'development_mode\': \'AUTO_DISCOVERY\', \'data_mode\': \'OHLCV\', \'description\': \'Kaufman-style directional efficiency with trend slope\', \'allowed_timeframes\': [\'1H\', \'6H\', \'12H\', \'1D\', \'1W\']}\n\ndef _num(s):\n    return pd.to_numeric(s, errors="coerce")\n\ndef _signal_dict(v, reason):\n    if int(v)>0: return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":reason}\n    if int(v)<0: return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":reason}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":reason}\n\ndef signal(df, params=None):\n    arr=generate_signals(df.copy(), "LIVE")\n    return _signal_dict(arr[-1] if len(arr) else 0, STRATEGY_META["name"])\n\ndef generate_signals(df, tf):\n    d=df.copy(); c=_num(d["close"]); change=(c-c.shift(20)).abs(); noise=c.diff().abs().rolling(20,min_periods=20).sum(); er=change/noise.replace(0,np.nan); ema=c.ewm(span=30,adjust=False).mean(); slope=ema-ema.shift(5)\n    out=np.zeros(len(d),dtype=np.int8); out[((er>0.35)&(slope>0)&(c>ema)).fillna(False).to_numpy()]=1; out[((er>0.35)&(slope<0)&(c<ema)).fillna(False).to_numpy()]=-1; return out\n'

_SRC_B = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\nSTRATEGY_META = {\'name\': \'V3_RANGE_EXPANSION\', \'development_mode\': \'AUTO_DISCOVERY\', \'data_mode\': \'OHLCV\', \'description\': \'large true-range directional close location expansion\', \'allowed_timeframes\': [\'1H\', \'6H\', \'12H\', \'1D\', \'1W\']}\n\ndef _num(s):\n    return pd.to_numeric(s, errors="coerce")\n\ndef _signal_dict(v, reason):\n    if int(v)>0: return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":reason}\n    if int(v)<0: return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":reason}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":reason}\n\ndef signal(df, params=None):\n    arr=generate_signals(df.copy(), "LIVE")\n    return _signal_dict(arr[-1] if len(arr) else 0, STRATEGY_META["name"])\n\ndef generate_signals(df, tf):\n    d=df.copy(); h=_num(d["high"]); l=_num(d["low"]); c=_num(d["close"]); pc=c.shift(1); tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); med=tr.rolling(30,min_periods=20).median(); loc=(c-l)/(h-l).replace(0,np.nan)\n    out=np.zeros(len(d),dtype=np.int8); out[((tr>1.6*med)&(loc>0.8)&(c>pc)).fillna(False).to_numpy()]=1; out[((tr>1.6*med)&(loc<0.2)&(c<pc)).fillna(False).to_numpy()]=-1; return out\n'


A = _embedded_module(
    _SRC_A,
    '_bbot_v3_combo_efficiency_trend_range_expansion_6h_v1_a',
)

B = _embedded_module(
    _SRC_B,
    '_bbot_v3_combo_efficiency_trend_range_expansion_6h_v1_b',
)


STRATEGY_META = {'name': 'V3_COMBO_EFFICIENCY_TREND_RANGE_EXPANSION_6H_V1', 'development_mode': 'BOUNDED_COMPLEMENTARY_COMBINATION', 'data_mode': 'OHLCV', 'allowed_timeframes': ['6H'], 'components': ['V3_EFFICIENCY_TREND', 'V3_RANGE_EXPANSION']}


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
