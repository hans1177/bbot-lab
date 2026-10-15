# -*- coding: utf-8 -*-

"""
STRATEGY LAB COMMON TEMPLATE V4

용도
----
MANUAL         : 사람이 직접 개발
AUTO_MUTATION  : 기존 전략 자동 변형
AUTO_DISCOVERY : OHLCV에서 신규 전략 자동 개발

기본 반환값
-----------
 1  = LONG
-1  = SHORT
 0  = HOLD / WAIT

현재 기본 재료
-------------
MACD
STOCH
SHAPE
DIV
EMA_CROSS
OBV
SAR
"""

import numpy as np
import pandas as pd


STRATEGY_META = {
    "name": "MY_STRATEGY_V1",

    # MANUAL / AUTO_MUTATION / AUTO_DISCOVERY
    "development_mode": "MANUAL",

    # CACHE_MATERIALS:
    # 기존 MACD/STOCH/... 신호 컬럼을 사용
    #
    # OHLCV:
    # open/high/low/close/volume 원자료에서 직접 전략 생성
    "data_mode": "CACHE_MATERIALS",

    "description": "직접 만든 연구전략",

    "allowed_timeframes": [
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
        "1M",
    ],
}


def material_signal(
    df: pd.DataFrame,
    tf: str,
    material: str,
) -> np.ndarray:

    material = str(material).upper()
    prefix = str(tf).lower()

    # EMA_CROSS는 cache에서 EMA state를 읽은 뒤
    # 실제 교차 EVENT로 변환한다.
    source = (
        "ema"
        if material == "EMA_CROSS"
        else material.lower()
    )

    lc = f"{prefix}_{source}_long"
    sc = f"{prefix}_{source}_short"

    if lc not in df.columns or sc not in df.columns:
        raise KeyError(
            f"required material columns missing: {lc}, {sc}"
        )

    lv = (
        pd.to_numeric(
            df[lc],
            errors="coerce",
        )
        .fillna(0)
        .to_numpy(dtype=float)
    )

    sv = (
        pd.to_numeric(
            df[sc],
            errors="coerce",
        )
        .fillna(0)
        .to_numpy(dtype=float)
    )

    state = np.zeros(
        len(df),
        dtype=np.int8,
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

    if material != "EMA_CROSS":
        return state

    # EMA state -> EMA CROSS event
    prev = np.roll(
        state,
        1,
    )

    if len(prev):
        prev[0] = state[0]

    event = np.zeros(
        len(df),
        dtype=np.int8,
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

    # production ema_cross.py? ????
    # ?? ?? TF? 30?? ??? ????
    # EMA_CROSS ???? ???? ???.
    valid_col = f"{prefix}_ema_valid"

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


def agree(
    a,
    b,
) -> np.ndarray:

    aa = np.asarray(
        a,
        dtype=np.int8,
    )

    bb = np.asarray(
        b,
        dtype=np.int8,
    )

    n = min(
        len(aa),
        len(bb),
    )

    aa = aa[:n]
    bb = bb[:n]

    out = np.zeros(
        n,
        dtype=np.int8,
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


def generate_signals(
    df: pd.DataFrame,
    tf: str,
) -> np.ndarray:
    """
    예제 전략:
        DIV + EMA_CROSS 같은 방향 합의

    이 함수 내용만 바꾸면
    새로운 수동전략을 만들 수 있다.
    """

    div = material_signal(
        df,
        tf,
        "DIV",
    )

    ema_cross = material_signal(
        df,
        tf,
        "EMA_CROSS",
    )

    return agree(
        div,
        ema_cross,
    )
