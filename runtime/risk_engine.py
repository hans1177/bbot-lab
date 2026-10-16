#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Live Cross Survival Risk Engine.

원본:
risk_engine_source.zip / leverage_scan.py

실거래 적용:
- Cross Entry Gate
- Control SL
- Binance Mark Price
- Binance leverage bracket 유지증거금
- historical 1H Cross stress
- one-hour lookahead safety lag

일반 SL/TP 기능은 이 파일에 넣지 않는다.
"""

from pathlib import Path as _LabPath

import numpy as np
import pandas as pd


BASE_DIR = _LabPath(
    __file__
).resolve().parent

STRESS_LIBRARY = (
    BASE_DIR
    / "risk_data"
    / "mark_price_1h_cross_stress_library.parquet"
)

FEE_RATE = 0.0005

_LAB_CROSS_STRESS_CACHE = None


def safe_float(v, default=np.nan):



    try:



        x = float(v)



        return x if np.isfinite(x) else float(default)



    except Exception:



        return float(default)


def flatten_brackets(snapshot: dict, symbol: str):



    raw = snapshot.get("leverage_brackets", {}).get(symbol)



    if not raw:



        raise RuntimeError(f"leverage brackets missing: {symbol}")







    if isinstance(raw, dict):



        raw = [raw]







    out = []



    for item in raw:



        if not isinstance(item, dict):



            continue



        rows = item.get("brackets")



        if isinstance(rows, list):



            out.extend(rows)



        elif "maintMarginRatio" in item:



            out.append(item)







    out = [x for x in out if isinstance(x, dict)]



    out.sort(key=lambda x: safe_float(x.get("notionalFloor"), 0.0))







    if not out:



        raise RuntimeError(f"empty leverage brackets: {symbol}")



    return out


def bracket_for_notional(brackets, notional: float):



    n = abs(float(notional))



    for b in brackets:



        floor = safe_float(b.get("notionalFloor"), 0.0)



        cap = safe_float(b.get("notionalCap"), np.inf)



        if n >= floor and n < cap:



            return b



    return brackets[-1]


def maintenance_margin(snapshot: dict, symbol: str, notional: float):



    brackets = flatten_brackets(snapshot, symbol)



    b = bracket_for_notional(brackets, abs(float(notional)))



    mmr = safe_float(b.get("maintMarginRatio"), 0.0)



    cum = safe_float(b.get("cum"), 0.0)



    return max(0.0, abs(float(notional)) * mmr - cum)


def get_mark(market, symbol: str, ts: int, side: int = 0, stress: bool = False):



    row = market.get(str(symbol), {}).get(int(ts))



    if row is None:



        return np.nan







    close, low, high = row



    if not stress or side == 0:



        return float(close)







    return float(low if int(side) == 1 else high)


def position_upnl(position: dict, mark: float):



    if not np.isfinite(mark) or mark <= 0:



        return 0.0







    if int(position["side"]) == 1:



        return float(position["qty"] * (mark - position["entry_price"]))







    return float(position["qty"] * (position["entry_price"] - mark))


def _lab_cross_stress_cache():

    global _LAB_CROSS_STRESS_CACHE

    if _LAB_CROSS_STRESS_CACHE is not None:
        return _LAB_CROSS_STRESS_CACHE


    path = STRESS_LIBRARY


    if not path.exists():

        raise RuntimeError(
            "LAB Cross stress library missing: "
            +
            str(path)
        )


    d = pd.read_parquet(
        path,
        columns=[
            "ts",
            "symbol",
            "long_price_mult",
            "short_price_mult",
        ],
    ).copy()


    d["ts"] = pd.to_numeric(
        d["ts"],
        errors="coerce",
    )

    d["long_price_mult"] = pd.to_numeric(
        d["long_price_mult"],
        errors="coerce",
    )

    d["short_price_mult"] = pd.to_numeric(
        d["short_price_mult"],
        errors="coerce",
    )


    d["symbol"] = (
        d["symbol"]
        .astype(str)
        .str.upper()
    )


    d = (
        d.replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
        .dropna(
            subset=[
                "ts",
                "symbol",
                "long_price_mult",
                "short_price_mult",
            ]
        )
        .sort_values(
            [
                "ts",
                "symbol",
            ]
        )
        .drop_duplicates(
            subset=[
                "ts",
                "symbol",
            ],
            keep="last",
        )
        .reset_index(
            drop=True
        )
    )


    d["ts"] = d[
        "ts"
    ].astype(
        np.int64
    )


    times = np.array(
        sorted(
            d[
                "ts"
            ].unique()
        ),
        dtype=np.int64,
    )


    symbols = sorted(
        d[
            "symbol"
        ].unique()
    )


    symbol_to_col = {
        str(symbol):
            int(i)
        for i, symbol in enumerate(
            symbols
        )
    }


    time_index = pd.Index(
        times
    )


    ti = time_index.get_indexer(
        d[
            "ts"
        ].to_numpy(
            dtype=np.int64
        )
    )


    si = (
        d[
            "symbol"
        ]
        .map(
            symbol_to_col
        )
        .to_numpy(
            dtype=np.int32
        )
    )


    rows = len(
        times
    )

    cols = len(
        symbols
    )


    long_mult = np.full(
        (
            rows,
            cols,
        ),
        np.nan,
        dtype=np.float32,
    )


    short_mult = np.full(
        (
            rows,
            cols,
        ),
        np.nan,
        dtype=np.float32,
    )


    long_mult[
        ti,
        si,
    ] = d[
        "long_price_mult"
    ].to_numpy(
        dtype=np.float32
    )


    short_mult[
        ti,
        si,
    ] = d[
        "short_price_mult"
    ].to_numpy(
        dtype=np.float32
    )


    # --------------------------------------------------------
    # If a symbol did not exist in a historical shock hour,
    # use the actual worst same-hour shock of that SIDE as a
    # conservative proxy.
    #
    # This does NOT invent simultaneous all-time maxima.
    # It remains one real historical UTC hour.
    # --------------------------------------------------------

    fallback_long = np.nanmin(
        long_mult,
        axis=1,
    ).astype(
        np.float32
    )


    fallback_short = np.nanmax(
        short_mult,
        axis=1,
    ).astype(
        np.float32
    )


    long_adverse = (
        1.0
        -
        long_mult
    )


    short_adverse = (
        short_mult
        -
        1.0
    )


    worst_adverse = np.fmax(
        long_adverse,
        short_adverse,
    )


    max_long = np.nanmax(
        long_adverse,
        axis=1,
    )


    max_short = np.nanmax(
        short_adverse,
        axis=1,
    )


    count20 = np.sum(
        worst_adverse
        >=
        0.20,
        axis=1,
    ).astype(
        np.int32
    )


    count40 = np.sum(
        worst_adverse
        >=
        0.40,
        axis=1,
    ).astype(
        np.int32
    )


    count60 = np.sum(
        worst_adverse
        >=
        0.60,
        axis=1,
    ).astype(
        np.int32
    )


    def cumulative_argmax(arr):

        arr = np.asarray(
            arr,
            dtype=float,
        )

        out = np.full(
            len(arr),
            -1,
            dtype=np.int32,
        )

        best_i = -1
        best_v = -np.inf


        for i, value in enumerate(
            arr
        ):

            if (
                np.isfinite(
                    value
                )
                and
                (
                    best_i < 0
                    or
                    value > best_v
                )
            ):

                best_i = int(
                    i
                )

                best_v = float(
                    value
                )


            out[
                i
            ] = int(
                best_i
            )


        return out


    # --------------------------------------------------------
    # Per-symbol running historical MAX event.
    # At decision time we can retrieve the strongest adverse
    # event that had actually been observed by then.
    # --------------------------------------------------------

    cumulative_long_idx = np.full(
        (
            rows,
            cols,
        ),
        -1,
        dtype=np.int32,
    )


    cumulative_short_idx = np.full(
        (
            rows,
            cols,
        ),
        -1,
        dtype=np.int32,
    )


    for col in range(
        cols
    ):

        best_long_i = -1
        best_long_v = np.inf

        best_short_i = -1
        best_short_v = -np.inf


        for i in range(
            rows
        ):

            lm = float(
                long_mult[
                    i,
                    col,
                ]
            )


            if (
                np.isfinite(
                    lm
                )
                and
                (
                    best_long_i < 0
                    or
                    lm < best_long_v
                )
            ):

                best_long_i = int(
                    i
                )

                best_long_v = float(
                    lm
                )


            cumulative_long_idx[
                i,
                col,
            ] = int(
                best_long_i
            )


            sm = float(
                short_mult[
                    i,
                    col,
                ]
            )


            if (
                np.isfinite(
                    sm
                )
                and
                (
                    best_short_i < 0
                    or
                    sm > best_short_v
                )
            ):

                best_short_i = int(
                    i
                )

                best_short_v = float(
                    sm
                )


            cumulative_short_idx[
                i,
                col,
            ] = int(
                best_short_i
            )


    market_record_indices = {
        "max_long":
            cumulative_argmax(
                max_long
            ),

        "max_short":
            cumulative_argmax(
                max_short
            ),

        "count20":
            cumulative_argmax(
                count20
            ),

        "count40":
            cumulative_argmax(
                count40
            ),

        "count60":
            cumulative_argmax(
                count60
            ),
    }


    _LAB_CROSS_STRESS_CACHE = {
        "times":
            times,

        "symbols":
            symbols,

        "symbol_to_col":
            symbol_to_col,

        "long_mult":
            long_mult,

        "short_mult":
            short_mult,

        "fallback_long":
            fallback_long,

        "fallback_short":
            fallback_short,

        "cumulative_long_idx":
            cumulative_long_idx,

        "cumulative_short_idx":
            cumulative_short_idx,

        "market_record_indices":
            market_record_indices,
    }


    return _LAB_CROSS_STRESS_CACHE


def _lab_cross_guard_scenarios(
    positions,
    decision_ts,
):

    cache = _lab_cross_stress_cache()

    times = cache[
        "times"
    ]


    # --------------------------------------------------------
    # One-hour safety lag:
    #
    # Stress hour must have completed before the decision.
    # This avoids accidental future use if the strategy cache
    # timestamps represent candle-confirmation time while the
    # Binance Mark cache uses candle-open timestamps.
    # --------------------------------------------------------

    history_cutoff = (
        int(
            decision_ts
        )
        -
        3600
    )


    end_i = (
        int(
            np.searchsorted(
                times,
                history_cutoff,
                side="right",
            )
        )
        -
        1
    )


    if end_i < 0:

        return []


    scenarios = set()


    symbol_to_col = cache[
        "symbol_to_col"
    ]


    for p in positions.values():

        symbol = str(
            p[
                "symbol"
            ]
        ).upper()

        side = int(
            p[
                "side"
            ]
        )


        col = symbol_to_col.get(
            symbol
        )


        if col is None:
            continue


        if side == 1:

            idx = int(
                cache[
                    "cumulative_long_idx"
                ][
                    end_i,
                    col,
                ]
            )


        elif side == -1:

            idx = int(
                cache[
                    "cumulative_short_idx"
                ][
                    end_i,
                    col,
                ]
            )


        else:

            continue


        if idx >= 0:

            scenarios.add(
                idx
            )


    # --------------------------------------------------------
    # Add actual historical market-wide record shock hours.
    # No average MAE is used.
    # --------------------------------------------------------

    for arr in cache[
        "market_record_indices"
    ].values():

        idx = int(
            arr[
                end_i
            ]
        )


        if idx >= 0:

            scenarios.add(
                idx
            )


    return sorted(
        scenarios
    )


def _lab_cross_guard_eval(
    wallet,
    positions,
    market,
    snapshot,
    decision_ts,
    scenario_indices=None,
):

    wallet = float(
        wallet
    )

    decision_ts = int(
        decision_ts
    )


    if not positions:

        return {
            "safe":
                True,

            "history_ready":
                True,

            "baseline_cushion":
                float(
                    wallet
                ),

            "min_cushion":
                float(
                    wallet
                ),

            "worst_scenario_ts":
                None,

            "scenario_count":
                0,

            "scenario_indices":
                [],
        }


    cache = _lab_cross_stress_cache()


    current_marks = {}


    baseline_upnl = 0.0
    baseline_maint = 0.0


    for pid, p in positions.items():

        mark = get_mark(
            market,
            p[
                "symbol"
            ],
            decision_ts,
            stress=False,
        )


        if (
            not np.isfinite(
                mark
            )
            or
            mark <= 0
        ):

            mark = float(
                p[
                    "entry_price"
                ]
            )


        mark = float(
            mark
        )


        current_marks[
            pid
        ] = mark


        baseline_upnl += position_upnl(
            p,
            mark,
        )


        baseline_maint += maintenance_margin(
            snapshot,
            p[
                "symbol"
            ],
            abs(
                float(
                    p[
                        "qty"
                    ]
                )
                *
                mark
            ),
        )


    baseline_margin_balance = (
        wallet
        +
        baseline_upnl
    )


    baseline_cushion = (
        baseline_margin_balance
        -
        baseline_maint
    )


    if scenario_indices is None:

        scenario_indices = (
            _lab_cross_guard_scenarios(
                positions,
                decision_ts,
            )
        )


    else:

        scenario_indices = [
            int(
                x
            )
            for x in scenario_indices
            if int(
                x
            )
            >=
            0
        ]


    if not scenario_indices:

        return {
            "safe":
                bool(
                    baseline_cushion
                    >
                    0.0
                ),

            "history_ready":
                False,

            "baseline_cushion":
                float(
                    baseline_cushion
                ),

            "min_cushion":
                float(
                    baseline_cushion
                ),

            "worst_scenario_ts":
                None,

            "scenario_count":
                0,

            "scenario_indices":
                [],
        }


    symbol_to_col = cache[
        "symbol_to_col"
    ]


    min_cushion = float(
        baseline_cushion
    )

    worst_scenario_ts = None


    for scenario_i in scenario_indices:

        scenario_i = int(
            scenario_i
        )


        total_upnl = 0.0
        total_maint = 0.0


        for pid, p in positions.items():

            symbol = str(
                p[
                    "symbol"
                ]
            ).upper()

            side = int(
                p[
                    "side"
                ]
            )


            base_mark = float(
                current_marks[
                    pid
                ]
            )


            col = symbol_to_col.get(
                symbol
            )


            if side == 1:

                if col is None:

                    mult = float(
                        cache[
                            "fallback_long"
                        ][
                            scenario_i
                        ]
                    )

                else:

                    mult = float(
                        cache[
                            "long_mult"
                        ][
                            scenario_i,
                            col,
                        ]
                    )


                    if not np.isfinite(
                        mult
                    ):

                        mult = float(
                            cache[
                                "fallback_long"
                            ][
                                scenario_i
                            ]
                        )


            elif side == -1:

                if col is None:

                    mult = float(
                        cache[
                            "fallback_short"
                        ][
                            scenario_i
                        ]
                    )

                else:

                    mult = float(
                        cache[
                            "short_mult"
                        ][
                            scenario_i,
                            col,
                        ]
                    )


                    if not np.isfinite(
                        mult
                    ):

                        mult = float(
                            cache[
                                "fallback_short"
                            ][
                                scenario_i
                            ]
                        )


            else:

                continue


            if (
                not np.isfinite(
                    mult
                )
                or
                mult <= 0.0
            ):

                mult = 1.0


            shock_mark = max(
                1e-12,
                base_mark
                *
                mult,
            )


            total_upnl += position_upnl(
                p,
                shock_mark,
            )


            total_maint += maintenance_margin(
                snapshot,
                symbol,
                abs(
                    float(
                        p[
                            "qty"
                        ]
                    )
                    *
                    shock_mark
                ),
            )


        margin_balance = (
            wallet
            +
            total_upnl
        )


        cushion = (
            margin_balance
            -
            total_maint
        )


        if cushion < min_cushion:

            min_cushion = float(
                cushion
            )

            worst_scenario_ts = int(
                cache[
                    "times"
                ][
                    scenario_i
                ]
            )


    return {
        "safe":
            bool(
                baseline_cushion
                >
                0.0
                and
                min_cushion
                >
                0.0
            ),

        "history_ready":
            True,

        "baseline_cushion":
            float(
                baseline_cushion
            ),

        "min_cushion":
            float(
                min_cushion
            ),

        "worst_scenario_ts":
            worst_scenario_ts,

        "scenario_count":
            int(
                len(
                    scenario_indices
                )
            ),

        "scenario_indices":
            list(
                scenario_indices
            ),
    }



# ============================================================
# LIVE ADAPTER
# ============================================================

def normalize_side(value):

    if isinstance(
        value,
        str,
    ):

        value = (
            value
            .strip()
            .upper()
        )

        if value in (
            "BUY",
            "LONG",
            "1",
            "+1",
        ):
            return 1

        if value in (
            "SELL",
            "SHORT",
            "-1",
        ):
            return -1

        return 0

    try:
        value = float(
            value
        )

    except Exception:
        return 0

    if value > 0:
        return 1

    if value < 0:
        return -1

    return 0


def normalize_position(row):

    if not isinstance(
        row,
        dict,
    ):
        raise TypeError(
            "position must be dict"
        )

    out = dict(
        row
    )

    symbol = str(
        row.get(
            "symbol",
            "",
        )
    ).strip().upper()

    side = normalize_side(
        row.get(
            "side",
            row.get(
                "positionAmt",
                0,
            ),
        )
    )

    entry_price = safe_float(
        row.get(
            "entry_price",
            row.get(
                "entryPrice",
                0,
            ),
        ),
        0.0,
    )

    qty = abs(
        safe_float(
            row.get(
                "qty",
                row.get(
                    "positionAmt",
                    0,
                ),
            ),
            0.0,
        )
    )

    if not symbol:
        raise ValueError(
            "position symbol missing"
        )

    if side not in (
        -1,
        1,
    ):
        raise ValueError(
            f"bad position side: {symbol}"
        )

    if entry_price <= 0:
        raise ValueError(
            f"bad entry price: {symbol}"
        )

    if qty <= 0:
        raise ValueError(
            f"bad qty: {symbol}"
        )

    out.update({
        "symbol":
            symbol,

        "side":
            int(
                side
            ),

        "entry_price":
            float(
                entry_price
            ),

        "qty":
            float(
                qty
            ),

        "entry_ts":
            int(
                row.get(
                    "entry_ts",
                    0,
                )
                or 0
            ),
    })

    return out


def normalize_positions(
    positions,
):

    out = {}

    if isinstance(
        positions,
        dict,
    ):

        iterator = (
            positions.items()
        )

    else:

        iterator = enumerate(
            positions
            or []
        )

    for key, row in iterator:

        out[
            str(
                key
            )
        ] = normalize_position(
            row
        )

    return out


def live_market(
    marks,
    decision_ts,
):

    decision_ts = int(
        decision_ts
    )

    out = {}

    for symbol, mark in (
        marks
        or {}
    ).items():

        symbol = str(
            symbol
        ).upper()

        mark = safe_float(
            mark,
            0.0,
        )

        if mark <= 0:
            continue

        out[
            symbol
        ] = {
            decision_ts:
                (
                    float(mark),
                    float(mark),
                    float(mark),
                )
        }

    return out


def live_snapshot(
    leverage_brackets,
):

    if (
        isinstance(
            leverage_brackets,
            dict,
        )
        and
        "leverage_brackets"
        in leverage_brackets
    ):

        return (
            leverage_brackets
        )

    return {
        "leverage_brackets":
            leverage_brackets
    }


def guard_eval(
    wallet,
    positions,
    marks,
    leverage_brackets,
    decision_ts,
    scenario_indices=None,
):

    positions = (
        normalize_positions(
            positions
        )
    )

    market = live_market(
        marks,
        decision_ts,
    )

    snapshot = live_snapshot(
        leverage_brackets
    )

    return (
        _lab_cross_guard_eval(
            float(
                wallet
            ),
            positions,
            market,
            snapshot,
            int(
                decision_ts
            ),
            scenario_indices=
                scenario_indices,
        )
    )


# ============================================================
# CROSS ENTRY GATE
# ============================================================

def entry_gate(
    wallet,
    positions,
    candidate,
    marks,
    leverage_brackets,
    decision_ts,
    fee_rate=FEE_RATE,
):

    positions = (
        normalize_positions(
            positions
        )
    )

    candidate = (
        normalize_position(
            candidate
        )
    )

    pre_eval = guard_eval(
        wallet,
        positions,
        marks,
        leverage_brackets,
        decision_ts,
    )

    candidate_notional = abs(
        float(
            candidate[
                "qty"
            ]
        )
        *
        float(
            candidate[
                "entry_price"
            ]
        )
    )

    entry_fee = (
        candidate_notional
        *
        float(
            fee_rate
        )
    )

    candidate_pid = (
        "__candidate__"
    )

    while (
        candidate_pid
        in positions
    ):

        candidate_pid += "_"

    guard_positions = dict(
        positions
    )

    guard_positions[
        candidate_pid
    ] = candidate

    guard_marks = {
        str(k).upper():
            float(v)

        for k, v in (
            marks
            or {}
        ).items()
    }

    guard_marks[
        candidate[
            "symbol"
        ]
    ] = float(
        candidate[
            "entry_price"
        ]
    )

    guard_wallet = (
        float(
            wallet
        )
        -
        float(
            entry_fee
        )
    )

    post_eval = guard_eval(
        guard_wallet,
        guard_positions,
        guard_marks,
        leverage_brackets,
        decision_ts,
    )

    block = (
        not bool(
            post_eval[
                "history_ready"
            ]
        )
        or
        not bool(
            post_eval[
                "safe"
            ]
        )
    )

    if not bool(
        post_eval[
            "history_ready"
        ]
    ):

        reason = (
            "NO_HISTORY"
        )

    elif not bool(
        post_eval[
            "safe"
        ]
    ):

        reason = (
            "STRESS_BREACH"
        )

    else:

        reason = (
            "ALLOW"
        )

    out = {
        "allow": not block,
        "reason": reason,
        "entry_fee": float(entry_fee),
        "candidate_notional": float(candidate_notional),
        "pre": pre_eval,
        "post": post_eval,
    }
    out["v3_entry_risk"] = v3_entry_risk_contract(out, candidate=candidate)
    return out


# ============================================================
# V3 ENTRY RISK CONTRACT
# ============================================================

def v3_entry_risk_contract(entry_gate_result, candidate=None):
    """검증된 Cross Stress Gate를 V3 Entry Risk 공통 출력으로 변환한다.

    STOP 확률/예상 MAE는 calibration 전까지 숫자를 만들지 않는다. Safe size는
    실제 Cross Stress 검증을 통과한 수량/명목만 기록한다.
    """
    result=entry_gate_result if isinstance(entry_gate_result,dict) else {}
    allow=bool(result.get("allow")); post=result.get("post") if isinstance(result.get("post"),dict) else {}
    try:min_cushion=float(post.get("min_cushion"))
    except Exception:min_cushion=None
    try:notional=float(result.get("candidate_notional") or 0.0)
    except Exception:notional=0.0
    c=candidate if isinstance(candidate,dict) else {}
    try:safe_qty=float(c.get("qty") or 0.0) if allow else 0.0
    except Exception:safe_qty=0.0
    return {"schema":"B_BOT_V3_ENTRY_RISK_V1","decision":"ALLOW" if allow else "BLOCK","entry_risk_score":None,"stop_hit_probability":None,"expected_adverse_move":None,"safe_size_notional":notional if allow else 0.0,"safe_size_qty":safe_qty,"safe_leverage":None,"min_cross_stress_cushion":min_cushion,"calibration_status":"ACCUMULATE_BACK_CANARY_LIVE_FIRST","source":"EXISTING_CROSS_STRESS_GATE","reason":str(result.get("reason") or "")}

def control_plan(
    wallet,
    positions,
    confirmed_marks,
    leverage_brackets,
    control_ts,
    fee_rate=FEE_RATE,
    protected_pids=None,
):

    wallet = float(
        wallet
    )

    control_ts = int(
        control_ts
    )

    positions = (
        normalize_positions(
            positions
        )
    )

    protected_pids = {
        str(x)
        for x in (
            protected_pids
            or []
        )
    }

    control_eval = guard_eval(
        wallet,
        positions,
        confirmed_marks,
        leverage_brackets,
        control_ts,
    )

    initial_eval = dict(
        control_eval
    )

    if not bool(
        control_eval[
            "history_ready"
        ]
    ):

        return {
            "breach":
                False,

            "history_ready":
                False,

            "safe":
                bool(
                    control_eval[
                        "safe"
                    ]
                ),

            "initial":
                initial_eval,

            "final":
                control_eval,

            "fixed_scenarios":
                [],

            "closes":
                [],

            "wallet_after":
                wallet,

            "protected_pids":
                sorted(
                    protected_pids
                ),
        }

    if bool(
        control_eval[
            "safe"
        ]
    ):

        return {
            "breach":
                False,

            "history_ready":
                True,

            "safe":
                True,

            "initial":
                initial_eval,

            "final":
                control_eval,

            "fixed_scenarios":
                list(
                    control_eval[
                        "scenario_indices"
                    ]
                ),

            "closes":
                [],

            "wallet_after":
                wallet,

            "protected_pids":
                sorted(
                    protected_pids
                ),
        }

    # 최초 breach에서 stress scenario를 고정.
    fixed_scenarios = list(
        control_eval[
            "scenario_indices"
        ]
    )

    work_positions = dict(
        positions
    )

    work_wallet = float(
        wallet
    )

    closes = []

    while (
        work_positions
        and
        not bool(
            control_eval[
                "safe"
            ]
        )
    ):

        best = None

        for (
            candidate_pid,
            candidate_p,
        ) in list(
            work_positions.items()
        ):

            # 기존 포지션 보호.
            # 위험 계산에는 계속 포함하지만
            # 강제청산 후보에서는 제외.
            if (
                str(
                    candidate_pid
                )
                in protected_pids
            ):
                continue

            if (
                int(
                    candidate_p.get(
                        "entry_ts",
                        control_ts,
                    )
                    or control_ts
                )
                >
                control_ts
            ):
                continue

            symbol = str(
                candidate_p[
                    "symbol"
                ]
            ).upper()

            candidate_mark = safe_float(
                (
                    confirmed_marks
                    or {}
                ).get(
                    symbol
                ),
                0.0,
            )

            if candidate_mark <= 0:
                continue

            candidate_pnl = (
                position_upnl(
                    candidate_p,
                    candidate_mark,
                )
            )

            candidate_exit_fee = (
                abs(
                    float(
                        candidate_p[
                            "qty"
                        ]
                    )
                    *
                    candidate_mark
                )
                *
                float(
                    fee_rate
                )
            )

            trial_wallet = (
                work_wallet
                +
                float(
                    candidate_pnl
                )
                -
                float(
                    candidate_exit_fee
                )
            )

            trial_positions = dict(
                work_positions
            )

            trial_positions.pop(
                candidate_pid,
                None,
            )

            trial_eval = guard_eval(
                trial_wallet,
                trial_positions,
                confirmed_marks,
                leverage_brackets,
                control_ts,
                scenario_indices=
                    fixed_scenarios,
            )

            trial_score = float(
                trial_eval[
                    "min_cushion"
                ]
            )

            if (
                best is None
                or
                trial_score
                >
                best[
                    "score"
                ]
            ):

                best = {
                    "pid":
                        candidate_pid,

                    "symbol":
                        symbol,

                    "mark":
                        float(
                            candidate_mark
                        ),

                    "pnl":
                        float(
                            candidate_pnl
                        ),

                    "exit_fee":
                        float(
                            candidate_exit_fee
                        ),

                    "score":
                        trial_score,

                    "trial_eval":
                        trial_eval,
                }

        # 위험하지만 제거 가능한 신규 포지션이 없음.
        if best is None:
            break

        before_eval = dict(
            control_eval
        )

        close_pid = (
            best[
                "pid"
            ]
        )

        close_p = (
            work_positions.pop(
                close_pid
            )
        )

        work_wallet += (
            best[
                "pnl"
            ]
            -
            best[
                "exit_fee"
            ]
        )

        closes.append({
            "pid":
                close_pid,

            "symbol":
                best[
                    "symbol"
                ],

            "side":
                int(
                    close_p[
                        "side"
                    ]
                ),

            "owner_tf":
                str(
                    close_p.get(
                        "owner_tf",
                        close_p.get(
                            "entry_tf",
                            "",
                        ),
                    )
                ).upper(),

            "owner_strategy":
                str(
                    close_p.get(
                        "owner_strategy",
                        close_p.get(
                            "entry_strategy",
                            "",
                        ),
                    )
                ).upper(),

            "entry_price":
                float(
                    close_p[
                        "entry_price"
                    ]
                ),

            "qty":
                float(
                    close_p[
                        "qty"
                    ]
                ),

            "entry_ts":
                int(
                    close_p.get(
                        "entry_ts",
                        0,
                    )
                    or 0
                ),

            "exit_price":
                float(
                    best[
                        "mark"
                    ]
                ),

            "gross_pnl":
                float(
                    best[
                        "pnl"
                    ]
                ),

            "exit_fee":
                float(
                    best[
                        "exit_fee"
                    ]
                ),

            "before_min_cushion":
                float(
                    before_eval[
                        "min_cushion"
                    ]
                ),

            "after_min_cushion":
                float(
                    best[
                        "trial_eval"
                    ][
                        "min_cushion"
                    ]
                ),

            "worst_scenario_ts":
                before_eval[
                    "worst_scenario_ts"
                ],

            "scenario_count":
                int(
                    before_eval[
                        "scenario_count"
                    ]
                ),
        })

        control_eval = guard_eval(
            work_wallet,
            work_positions,
            confirmed_marks,
            leverage_brackets,
            control_ts,
            scenario_indices=
                fixed_scenarios,
        )

    return {
        "breach":
            True,

        "history_ready":
            True,

        "safe":
            bool(
                control_eval[
                    "safe"
                ]
            ),

        "initial":
            initial_eval,

        "final":
            control_eval,

        "fixed_scenarios":
            fixed_scenarios,

        "closes":
            closes,

        "wallet_after":
            float(
                work_wallet
            ),

        "protected_pids":
            sorted(
                protected_pids
            ),
    }



def self_check():

    cache = (
        _lab_cross_stress_cache()
    )

    times = cache[
        "times"
    ]

    return {
        "ok":
            True,

        "path":
            str(
                STRESS_LIBRARY
            ),

        "hours":
            int(
                len(
                    times
                )
            ),

        "symbols":
            int(
                len(
                    cache[
                        "symbols"
                    ]
                )
            ),

        "min_ts":
            int(
                times[
                    0
                ]
            ),

        "max_ts":
            int(
                times[
                    -1
                ]
            ),
    }
