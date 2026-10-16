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


_SRC_A = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\nSTRATEGY_META = {\'name\': \'V3_MOMENTUM_ACCEL\', \'development_mode\': \'AUTO_DISCOVERY\', \'data_mode\': \'OHLCV\', \'description\': \'multi-horizon momentum acceleration\', \'allowed_timeframes\': [\'1H\', \'6H\', \'12H\', \'1D\', \'1W\']}\n\ndef _num(s):\n    return pd.to_numeric(s, errors="coerce")\n\ndef _signal_dict(v, reason):\n    if int(v)>0: return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":reason}\n    if int(v)<0: return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":reason}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":reason}\n\ndef signal(df, params=None):\n    arr=generate_signals(df.copy(), "LIVE")\n    return _signal_dict(arr[-1] if len(arr) else 0, STRATEGY_META["name"])\n\ndef generate_signals(df, tf):\n    d=df.copy(); c=_num(d["close"]); r3=c.pct_change(3); r9=c.pct_change(9); r18=c.pct_change(18); accel=r3-r9/3.0\n    out=np.zeros(len(d),dtype=np.int8); out[((r18>0)&(r9>0)&(r3>0)&(accel>0)).fillna(False).to_numpy()]=1; out[((r18<0)&(r9<0)&(r3<0)&(accel<0)).fillna(False).to_numpy()]=-1; return out\n'

_SRC_B = '# -*- coding: utf-8 -*-\nimport numpy as np\nimport pandas as pd\n\nSTRATEGY_META = {\'name\': \'V3_ADAPTIVE_CHANNEL\', \'development_mode\': \'AUTO_DISCOVERY\', \'data_mode\': \'OHLCV\', \'description\': \'ATR-normalized adaptive trend channel breakout\', \'allowed_timeframes\': [\'1H\', \'6H\', \'12H\', \'1D\', \'1W\']}\n\ndef _num(s):\n    return pd.to_numeric(s, errors="coerce")\n\ndef _signal_dict(v, reason):\n    if int(v)>0: return {"signal":"long","state":"long","strength_long":1.0,"strength_short":0.0,"reason":reason}\n    if int(v)<0: return {"signal":"short","state":"short","strength_long":0.0,"strength_short":1.0,"reason":reason}\n    return {"signal":"wait","state":"wait","strength_long":0.0,"strength_short":0.0,"reason":reason}\n\ndef signal(df, params=None):\n    arr=generate_signals(df.copy(), "LIVE")\n    return _signal_dict(arr[-1] if len(arr) else 0, STRATEGY_META["name"])\n\ndef generate_signals(df, tf):\n    d=df.copy(); c=_num(d["close"]); h=_num(d["high"]); l=_num(d["low"]); pc=c.shift(1); tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(span=20,adjust=False).mean(); ema=c.ewm(span=32,adjust=False).mean(); slope=ema-ema.shift(6); upper=ema+1.2*atr; lower=ema-1.2*atr\n    out=np.zeros(len(d),dtype=np.int8); out[((c>upper)&(slope>0)).fillna(False).to_numpy()]=1; out[((c<lower)&(slope<0)).fillna(False).to_numpy()]=-1; return out\n'


A = _embedded_module(
    _SRC_A,
    '_bbot_v3_combo_momentum_accel_adaptive_channel_1d_v1_a',
)

B = _embedded_module(
    _SRC_B,
    '_bbot_v3_combo_momentum_accel_adaptive_channel_1d_v1_b',
)


STRATEGY_META = {'name': 'V3_COMBO_MOMENTUM_ACCEL_ADAPTIVE_CHANNEL_1D_V1', 'development_mode': 'BOUNDED_COMPLEMENTARY_COMBINATION', 'data_mode': 'OHLCV', 'allowed_timeframes': ['1D'], 'components': ['V3_MOMENTUM_ACCEL', 'V3_ADAPTIVE_CHANNEL']}


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
