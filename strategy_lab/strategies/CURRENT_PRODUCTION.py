# -*- coding: utf-8 -*-

import numpy as np
import pandas as pd


STRATEGY_META = {
    "name": "CURRENT_PRODUCTION_V1",
    "description": "현재 서버 1H/6H/12H 전략",
    "allowed_timeframes": ["1H", "6H", "12H"]
}


def material_signal(df, tf, material):

    p = tf.lower()

    source = (
        "ema"
        if material == "EMA_CROSS"
        else material.lower()
    )

    lc = f"{p}_{source}_long"
    sc = f"{p}_{source}_short"

    lv = pd.to_numeric(
        df[lc],
        errors="coerce"
    ).fillna(0).to_numpy(dtype=float)

    sv = pd.to_numeric(
        df[sc],
        errors="coerce"
    ).fillna(0).to_numpy(dtype=float)

    state = np.zeros(
        len(df),
        dtype=np.int8
    )

    state[
        (lv > sv)
        &
        (lv > 0)
    ] = 1

    state[
        (sv > lv)
        &
        (sv > 0)
    ] = -1

    # EMA_CROSS는 state가 아니라 EVENT
    if material == "EMA_CROSS":

        prev = np.roll(
            state,
            1
        )

        prev[0] = state[0]

        event = np.zeros(
            len(df),
            dtype=np.int8
        )

        event[
            (state == 1)
            &
            (prev != 1)
        ] = 1

        event[
            (state == -1)
            &
            (prev != -1)
        ] = -1

        # run_strategy.py? ?? ?? TF? 30?? ????
        # ???? ?? warm-up validity mask.
        #
        # EMA state ??? ????,
        # production ema_cross.py? len(df) < 30 ??? ??
        # ?? CROSS event? ????.
        valid_col = f"{p}_ema_valid"

        if valid_col in df.columns:
            valid = (
                pd.to_numeric(
                    df[valid_col],
                    errors="coerce",
                )
                .fillna(0)
                .to_numpy(dtype=float)
                > 0
            )

            event[~valid] = 0

        return event

    return state


def agree(a, b):

    n = min(
        len(a),
        len(b)
    )

    aa = np.asarray(
        a[:n],
        dtype=np.int8
    )

    bb = np.asarray(
        b[:n],
        dtype=np.int8
    )

    out = np.zeros(
        n,
        dtype=np.int8
    )

    out[
        (aa == 1)
        &
        (bb == 1)
    ] = 1

    out[
        (aa == -1)
        &
        (bb == -1)
    ] = -1

    return out


def generate_signals(df, tf):

    if tf == "1H":

        return agree(
            material_signal(
                df,
                tf,
                "DIV"
            ),
            material_signal(
                df,
                tf,
                "EMA_CROSS"
            )
        )

    if tf == "6H":

        return agree(
            material_signal(
                df,
                tf,
                "SHAPE"
            ),
            material_signal(
                df,
                tf,
                "EMA_CROSS"
            )
        )

    if tf == "12H":

        return agree(
            material_signal(
                df,
                tf,
                "MACD"
            ),
            material_signal(
                df,
                tf,
                "STOCH"
            )
        )

    return np.zeros(
        len(df),
        dtype=np.int8
    )
