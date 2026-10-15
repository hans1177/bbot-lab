# leverage_scan.py



# -*- coding: utf-8 -*-







from __future__ import annotations
import os







import argparse



import importlib.util



import json



import math



from pathlib import Path







import numpy as np



import pandas as pd











# ============================================================



# PATH / CONFIG



# ============================================================







ROOT = Path(r"C:\my\bottest")



LAB = ROOT / "strategy_lab"



RESULT_DIR = LAB / "results"



CANDLE_ROOT = ROOT / "data" / "candles"







SNAPSHOT_FILE = LAB / "server_exchange_snapshot.json"



CACHE_FILE = ROOT / "gate_lab_exact_long_cache.parquet"



RUNNER_V4_FILE = LAB / "run_strategy.py"



STRATEGY_FILE = LAB / "strategies" / "CURRENT_PRODUCTION.py"







TIMEFRAMES = ("1H", "6H", "12H")



LEVERAGES = tuple(range(10, 51))



FEE_RATE = 0.0005



DEFAULT_START_EQUITY = 10_000.0







MIN_HISTORY_GLOBAL = 30



GLOBAL_WINDOW = 100



SYMBOL_WINDOW = 25



SIDE_WINDOW = 50











# ============================================================



# COMMON



# ============================================================







def safe_float(v, default=np.nan):



    try:



        x = float(v)



        return x if np.isfinite(x) else float(default)



    except Exception:



        return float(default)











def max_drawdown_pct(values):



    a = np.asarray(values, dtype=float)



    if len(a) == 0:



        return np.nan



    peak = np.maximum.accumulate(a)



    dd = a / np.maximum(peak, 1e-12) - 1.0



    return float(np.min(dd) * 100.0)











def profit_factor(values):



    a = np.asarray(values, dtype=float)



    if len(a) == 0:



        return 1.0



    pos = float(a[a > 0].sum())



    neg = float(-a[a < 0].sum())



    if neg <= 1e-12:



        return 3.0 if pos > 0 else 1.0



    return pos / neg











def normalize_signal(v, n):



    a = np.asarray(v)



    out = np.zeros(n, dtype=np.int8)



    m = min(n, len(a))



    z = pd.to_numeric(pd.Series(a[:m]), errors="coerce").fillna(0).to_numpy()



    out[:m][z > 0] = 1



    out[:m][z < 0] = -1



    return out











# ============================================================



# SERVER SNAPSHOT



# ============================================================







def load_snapshot(path: Path):



    if not path.exists():



        raise RuntimeError(f"server exchange snapshot missing: {path}")







    data = json.loads(path.read_text(encoding="utf-8-sig"))



    active_symbols = [str(x).upper() for x in data.get("active_symbols", [])]



    active_count = int(data.get("active_count") or len(active_symbols))







    if active_count <= 0 or not active_symbols:



        raise RuntimeError("snapshot active symbols/count invalid")







    return data, active_symbols, active_count











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











def resolve_actual_leverage(brackets, allocation: float, requested: int):



    lev = max(1, int(requested))



    for _ in range(8):



        notional = max(0.0, allocation) * lev



        b = bracket_for_notional(brackets, notional)



        allowed = max(1, int(safe_float(b.get("initialLeverage"), lev)))



        new_lev = min(int(requested), allowed)



        if new_lev == lev:



            return lev



        lev = new_lev



    return lev











def adjust_qty(snapshot: dict, symbol: str, raw_qty: float, price: float):



    f = snapshot.get("order_filters", {}).get(symbol, {})



    step = safe_float(f.get("step"), 0.0)



    min_qty = safe_float(f.get("min_qty"), 0.0)



    min_notional = safe_float(f.get("min_notional"), 0.0)







    if step <= 0 or raw_qty <= 0:



        return 0.0







    q = math.floor(raw_qty / step) * step



    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0



    q = round(q, decimals)







    if q < min_qty:



        return 0.0



    if price > 0 and q * price < min_notional:



        return 0.0



    return float(q)











# ============================================================



# CURRENT 35-SYMBOL TRADE PATH BUILD



# ============================================================







def load_strategy():



    if not STRATEGY_FILE.exists():



        raise RuntimeError(f"strategy missing: {STRATEGY_FILE}")







    spec = importlib.util.spec_from_file_location("current_production", STRATEGY_FILE)



    if spec is None or spec.loader is None:



        raise RuntimeError(f"cannot import: {STRATEGY_FILE}")







    mod = importlib.util.module_from_spec(spec)



    spec.loader.exec_module(mod)



    fn = getattr(mod, "generate_signals", None)



    if not callable(fn):



        raise RuntimeError("CURRENT_PRODUCTION.generate_signals missing")



    return fn











def load_runner_v4():



    if not RUNNER_V4_FILE.exists():



        raise RuntimeError(



            f"run_strategy V4 missing: {RUNNER_V4_FILE}"



        )







    spec = importlib.util.spec_from_file_location(



        "strategy_runner_v4",



        RUNNER_V4_FILE,



    )







    if spec is None or spec.loader is None:



        raise RuntimeError(



            f"cannot import runner V4: {RUNNER_V4_FILE}"



        )







    mod = importlib.util.module_from_spec(spec)



    spec.loader.exec_module(mod)







    if not hasattr(mod, "add_ema_warmup_valid"):



        raise RuntimeError(



            "runner V4 add_ema_warmup_valid missing"



        )







    return mod











def load_current_cache(active_symbols):



    if not CACHE_FILE.exists():



        raise RuntimeError(f"35-symbol exact cache missing: {CACHE_FILE}")







    df = pd.read_parquet(CACHE_FILE).copy()



    required = {"ts", "symbol", "close"}



    missing = sorted(required - set(df.columns))



    if missing:



        raise RuntimeError(f"cache columns missing: {missing}")







    df["symbol"] = df["symbol"].astype(str).str.upper()



    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")



    df["close"] = pd.to_numeric(df["close"], errors="coerce")



    df = df.dropna(subset=["ts", "symbol", "close"])



    df["ts"] = df["ts"].astype("int64")







    cache_symbols = set(df["symbol"].unique())



    active_set = set(active_symbols)



    missing_active = sorted(active_set - cache_symbols)



    extra_cache = sorted(cache_symbols - active_set)







    if missing_active:



        raise RuntimeError(f"active symbols missing from cache: {missing_active}")







    df = df[df["symbol"].isin(active_set)].copy()



    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)







    print(f"cache symbols = {df['symbol'].nunique()} / active={len(active_symbols)}")



    if extra_cache:



        print(f"cache extra ignored = {len(extra_cache)}")







    return df











def load_hourly(symbol: str):



    candidates = (



        CANDLE_ROOT / symbol / "1h.parquet",



        CANDLE_ROOT / symbol / "1H.parquet",



    )



    path = next((p for p in candidates if p.exists()), None)



    if path is None:



        raise RuntimeError(f"1H candle missing: {symbol}")







    df = pd.read_parquet(path).copy()



    lookup = {str(c).lower(): c for c in df.columns}



    ts_col = next((lookup[k] for k in ("open_time", "timestamp", "ts", "time") if k in lookup), None)



    if ts_col is None:



        raise RuntimeError(f"timestamp missing: {path}")







    ts = pd.to_numeric(df[ts_col], errors="coerce")



    med = ts.dropna().abs().median()



    if pd.notna(med) and med > 10_000_000_000:



        ts = ts / 1000.0







    out = pd.DataFrame({



        "ts": ts,



        "high": pd.to_numeric(df[lookup["high"]], errors="coerce"),



        "low": pd.to_numeric(df[lookup["low"]], errors="coerce"),



    })



    out = out.dropna(subset=["ts", "high", "low"])



    out["ts"] = out["ts"].astype("int64")



    return out.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)











def build_trades(df: pd.DataFrame, signal: np.ndarray, tf: str, symbol: str):



    ts = df["ts"].to_numpy(dtype=np.int64)



    px = df["close"].to_numpy(dtype=float)







    trades = []



    side = 0



    entry_i = None



    entry_price = None







    def close_trade(i, reason):



        nonlocal side, entry_i, entry_price



        trades.append({



            "symbol": symbol,



            "timeframe": tf,



            "side": int(side),



            "entry_ts": int(ts[entry_i]),



            "exit_ts": int(ts[i]),



            "entry_price": float(entry_price),



            "exit_price": float(px[i]),



            "reason": reason,



        })



        side = 0



        entry_i = None



        entry_price = None







    for i in range(len(df)):



        if not np.isfinite(px[i]) or px[i] <= 0:



            continue







        s = int(signal[i])



        if side == 0:



            if s in (1, -1):



                side = s



                entry_i = i



                entry_price = float(px[i])



            continue







        if s == -side:



            new_side = s



            close_trade(i, "OPPOSITE_SIGNAL")



            side = new_side



            entry_i = i



            entry_price = float(px[i])







    if side != 0 and entry_i is not None:



        close_trade(len(df) - 1, "END_OF_TEST")







    return trades











def attach_path_stats_fast(trades, hourly):



    if not trades:



        return







    hts = hourly["ts"].to_numpy(dtype=np.int64)



    highs = hourly["high"].to_numpy(dtype=float)



    lows = hourly["low"].to_numpy(dtype=float)







    for t in trades:



        e0 = int(t["entry_ts"])



        e1 = int(t["exit_ts"])



        entry = float(t["entry_price"])







        i0 = int(np.searchsorted(hts, e0, side="left"))



        i1 = int(np.searchsorted(hts, e1, side="right"))







        if i0 >= i1 or i0 >= len(hts):



            raise RuntimeError(f"hourly path missing {t['symbol']} {e0}->{e1}")







        high = float(np.max(highs[i0:i1]))



        low = float(np.min(lows[i0:i1]))







        if int(t["side"]) == 1:



            mae = low / entry - 1.0



            mfe = high / entry - 1.0



        else:



            mae = -(high / entry - 1.0)



            mfe = entry / low - 1.0







        t["mae_pct"] = float(mae * 100.0)



        t["mfe_pct"] = float(mfe * 100.0)











def trade_unlevered_net_row(row):



    e = float(row.entry_price)



    x = float(row.exit_price)



    gross = (x / e - 1.0) if int(row.side) == 1 else (1.0 - x / e)



    return float(gross - 2.0 * FEE_RATE)











def build_trade_paths(tf: str, cache: pd.DataFrame, signal_fn):



    all_trades = []







    # run_strategy V4? ??? EMA_CROSS warm-up ??? ????.



    runner_v4 = load_runner_v4()



    lab = runner_v4.load_lab_engine()







    for symbol, sdf in cache.groupby("symbol", sort=True):



        d = sdf.reset_index(drop=True)







        d = runner_v4.add_ema_warmup_valid(



            source=d,



            lab=lab,



            symbol=symbol,



            tf=tf,



        )







        signal = normalize_signal(



            signal_fn(d.copy(), tf),



            len(d),



        )







        trades = build_trades(d, signal, tf, symbol)



        hourly = load_hourly(symbol)



        attach_path_stats_fast(trades, hourly)



        all_trades.extend(trades)







    out = pd.DataFrame(all_trades)



    if out.empty:



        raise RuntimeError(f"no trades: {tf}")







    out = out.sort_values(["entry_ts", "exit_ts", "symbol"]).reset_index(drop=True)



    out["trade_id"] = np.arange(len(out), dtype=int)



    out["unlev_net"] = [trade_unlevered_net_row(r) for r in out.itertuples()]







    path = RESULT_DIR / f"trade_paths_v6_35_{tf}.csv"



    out.to_csv(path, index=False)



    return out, path











# ============================================================



# WALK-FORWARD QUALITY



# ============================================================







def history_score(rows):



    if not rows:



        return 0.50







    rets = np.asarray([x[0] for x in rows], dtype=float)



    maes = np.asarray([abs(x[1]) for x in rows if np.isfinite(x[1])], dtype=float)







    win_rate = float(np.mean(rets > 0))



    expectancy = float(np.mean(rets))



    pf = profit_factor(rets)



    mae80 = float(np.quantile(maes, 0.80) / 100.0) if len(maes) else 0.10







    win_score = np.clip((win_rate - 0.40) / 0.30, 0.0, 1.0)



    exp_score = np.clip((expectancy + 0.01) / 0.05, 0.0, 1.0)



    pf_score = np.clip((pf - 0.80) / 1.70, 0.0, 1.0)



    mae_score = np.clip((0.15 - mae80) / 0.13, 0.0, 1.0)







    return float(0.30 * win_score + 0.30 * exp_score + 0.20 * pf_score + 0.20 * mae_score)











def attach_walk_forward_quality(df: pd.DataFrame):



    rows = {int(r.trade_id): r for r in df.itertuples()}



    entries = sorted((int(r.entry_ts), int(r.trade_id)) for r in df.itertuples())



    exits = sorted((int(r.exit_ts), int(r.trade_id)) for r in df.itertuples())







    global_hist = []



    symbol_hist = {}



    side_hist = {1: [], -1: []}



    scores = {}



    ptr = 0







    for entry_ts, trade_id in entries:



        while ptr < len(exits) and exits[ptr][0] < entry_ts:



            _, done_id = exits[ptr]



            row = rows[done_id]



            pair = (float(row.unlev_net), safe_float(row.mae_pct, np.nan))



            global_hist.append(pair)



            symbol_hist.setdefault(str(row.symbol), []).append(pair)



            side_hist.setdefault(int(row.side), []).append(pair)



            ptr += 1







        row = rows[trade_id]



        if len(global_hist) < MIN_HISTORY_GLOBAL:



            scores[trade_id] = 0.50



            continue







        g = history_score(global_hist[-GLOBAL_WINDOW:])



        s_hist = symbol_hist.get(str(row.symbol), [])



        s = history_score(s_hist[-SYMBOL_WINDOW:]) if len(s_hist) >= 8 else g



        d_hist = side_hist.get(int(row.side), [])



        d = history_score(d_hist[-SIDE_WINDOW:]) if len(d_hist) >= 12 else g







        scores[trade_id] = float(np.clip(0.55 * g + 0.30 * s + 0.15 * d, 0.0, 1.0))







    out = df.copy()



    out["quality_score"] = out["trade_id"].map(scores).fillna(0.50).astype(float)



    return out











def leverage_from_quality(score: float, shift: float):



    if score >= 0.80 + shift:



        return 50



    if score >= 0.68 + shift:



        return 40



    if score >= 0.55 + shift:



        return 30



    if score >= 0.42 + shift:



        return 20



    return 10











def dynamic_policies():



    return [(f"DYNAMIC_SHIFT_{x:+.2f}", x) for x in (-0.14, -0.10, -0.06, -0.02, 0.02, 0.06)]











# ============================================================



# CROSS-MARGIN RISK ENGINE



# ============================================================







def maintenance_margin(snapshot: dict, symbol: str, notional: float):



    brackets = flatten_brackets(snapshot, symbol)



    b = bracket_for_notional(brackets, abs(float(notional)))



    mmr = safe_float(b.get("maintMarginRatio"), 0.0)



    cum = safe_float(b.get("cum"), 0.0)



    return max(0.0, abs(float(notional)) * mmr - cum)











def load_market_bars(cache: pd.DataFrame):



    """



    35심볼 1H OHLC를 한 번만 읽는다.







    cache ts는 확정 시각 기준일 수 있으므로 raw 1H timestamp와



    raw+3600 중 cache와 더 많이 겹치는 쪽을 자동 선택한다.



    이후 레버리지 10~50 스캔에서는 이 데이터를 재사용한다.



    """



    market = {}







    for symbol, sdf in cache.groupby("symbol", sort=True):



        c = (



            sdf[["ts", "close"]]



            .drop_duplicates("ts", keep="last")



            .sort_values("ts")



            .reset_index(drop=True)



        )



        cache_ts = set(c["ts"].astype("int64").tolist())







        candidates = (



            CANDLE_ROOT / symbol / "1h.parquet",



            CANDLE_ROOT / symbol / "1H.parquet",



        )



        path = next((x for x in candidates if x.exists()), None)



        if path is None:



            raise RuntimeError(f"1H candle missing: {symbol}")







        raw = pd.read_parquet(path).copy()



        lookup = {str(col).lower(): col for col in raw.columns}



        ts_col = next(



            (lookup[k] for k in ("open_time", "timestamp", "ts", "time") if k in lookup),



            None,



        )



        if ts_col is None:



            raise RuntimeError(f"timestamp missing: {path}")







        ts = pd.to_numeric(raw[ts_col], errors="coerce")



        med = ts.dropna().abs().median()



        if pd.notna(med) and med > 10_000_000_000:



            ts = ts / 1000.0







        h = pd.DataFrame({



            "raw_ts": ts,



            "high": pd.to_numeric(raw[lookup["high"]], errors="coerce"),



            "low": pd.to_numeric(raw[lookup["low"]], errors="coerce"),



            "hclose": pd.to_numeric(raw[lookup["close"]], errors="coerce"),



        }).dropna(subset=["raw_ts", "high", "low", "hclose"])







        h["raw_ts"] = h["raw_ts"].astype("int64")



        raw0 = h["raw_ts"].to_numpy(dtype=np.int64)







        overlap0 = sum(int(x) in cache_ts for x in raw0)



        overlap1 = sum(int(x + 3600) in cache_ts for x in raw0)



        shift = 3600 if overlap1 > overlap0 else 0







        h["ts"] = h["raw_ts"] + shift



        h = (



            h[["ts", "high", "low", "hclose"]]



            .drop_duplicates("ts", keep="last")



            .sort_values("ts")



        )







        m = c.merge(h, on="ts", how="left")



        m["hclose"] = m["hclose"].fillna(m["close"])



        m["high"] = m["high"].fillna(m[["close", "hclose"]].max(axis=1))



        m["low"] = m["low"].fillna(m[["close", "hclose"]].min(axis=1))







        market[str(symbol)] = {



            int(r.ts): (



                float(r.close),



                float(r.low),



                float(r.high),



            )



            for r in m.itertuples()



        }







    return market











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












# ============================================================
# LAB_CROSS_SURVIVAL_GUARD_V1
#
# Local research only.
#
# Principle:
# - fixed strategy leverage remains unchanged
# - entry gate blocks only new exposure
# - control SL reduces existing exposure only when historical
#   Mark Price extreme stress says Cross survival is insufficient
# - historical stress data is limited to data known BEFORE the
#   decision point; a one-hour safety lag is deliberately used
#   to prevent timestamp-alignment lookahead
# ============================================================

import os as _lab_os
from pathlib import Path as _LabPath


_LAB_CROSS_STRESS_CACHE = None


def _lab_cross_stress_cache():

    global _LAB_CROSS_STRESS_CACHE

    if _LAB_CROSS_STRESS_CACHE is not None:
        return _LAB_CROSS_STRESS_CACHE


    path = (
        _LabPath(__file__).resolve().parent
        /
        "results"
        /
        "mark_price_1h_cross_stress_library.parquet"
    )


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
# LAB_CROSS_SURVIVAL_GUARD_V1_HELPERS_END
# ============================================================

def account_state(wallet: float, positions: dict, market: dict, snapshot: dict, ts: int, stress: bool):



    total_upnl = 0.0



    total_maint = 0.0



    total_initial = 0.0



    marks = {}







    for trade_id, p in positions.items():



        mark = get_mark(



            market,



            p["symbol"],



            ts,



            p["side"],



            stress=stress,



        )



        if not np.isfinite(mark) or mark <= 0:



            mark = float(p["entry_price"])







        marks[trade_id] = mark



        total_upnl += position_upnl(p, mark)



        total_maint += maintenance_margin(



            snapshot,



            p["symbol"],



            abs(p["qty"] * mark),



        )



        total_initial += float(p["initial_margin"])







    margin_balance = float(wallet + total_upnl)



    available = float(max(0.0, margin_balance - total_initial))







    return {



        "margin_balance": margin_balance,



        "available": available,



        "maintenance": float(total_maint),



        "upnl": float(total_upnl),



        "marks": marks,



    }











def _single_position_gap(



    wallet: float,



    positions: dict,



    snapshot: dict,



    base_state: dict,



    trade_id: int,



    price: float,



):



    """



    다른 포지션은 같은 시각의 synchronized close에 고정하고,



    한 포지션만 intrabar adverse price로 움직였을 때의



    CROSS margin safety gap = margin_balance - maintenance.



    """



    p = positions[trade_id]



    close_mark = float(base_state["marks"].get(trade_id, p["entry_price"]))







    close_upnl = position_upnl(p, close_mark)



    test_upnl = position_upnl(p, float(price))







    close_mm = maintenance_margin(



        snapshot,



        p["symbol"],



        abs(float(p["qty"]) * close_mark),



    )



    test_mm = maintenance_margin(



        snapshot,



        p["symbol"],



        abs(float(p["qty"]) * float(price)),



    )







    margin_balance = (



        float(base_state["margin_balance"])



        + (test_upnl - close_upnl)



    )



    maintenance = (



        float(base_state["maintenance"])



        + (test_mm - close_mm)



    )



    return float(margin_balance - maintenance)











def _cross_trigger_price(



    wallet: float,



    positions: dict,



    market: dict,



    snapshot: dict,



    ts: int,



    trade_id: int,



    base_state: dict,



):



    """



    1H OHLC만 있는 상태에서 모든 심볼의 worst extreme을 동시에 합치지 않는다.



    다른 포지션은 synchronized close에 두고 해당 포지션 하나만 adverse extreme으로



    이동시킨다. extreme에서 CROSS maintenance breach가 발생할 때만 binary search로



    최초 breach 가격을 근사한다.



    """



    p = positions[trade_id]



    close_mark = float(base_state["marks"].get(trade_id, p["entry_price"]))



    adverse = get_mark(



        market,



        p["symbol"],



        ts,



        p["side"],



        stress=True,



    )







    if not np.isfinite(adverse) or adverse <= 0:



        return None







    if int(p["side"]) == 1:



        if adverse >= close_mark:



            return None



    else:



        if adverse <= close_mark:



            return None







    base_gap = float(base_state["margin_balance"] - base_state["maintenance"])



    if base_gap <= 0:



        return close_mark







    adverse_gap = _single_position_gap(



        wallet,



        positions,



        snapshot,



        base_state,



        trade_id,



        adverse,



    )



    if adverse_gap > 0:



        return None







    safe_p = close_mark



    unsafe_p = float(adverse)







    for _ in range(36):



        mid = (safe_p + unsafe_p) / 2.0



        gap = _single_position_gap(



            wallet,



            positions,



            snapshot,



            base_state,



            trade_id,



            mid,



        )



        if gap > 0:



            safe_p = mid



        else:



            unsafe_p = mid







    return float(unsafe_p)











def liquidate_cross_until_safe(



    wallet: float,



    positions: dict,



    market: dict,



    snapshot: dict,



    ts: int,



    diagnostics=None,
):



    """



    V6.4 CROSS risk model.







    1) 실제 같은 시각 synchronized close 기준으로 계좌 유지증거금 점검.



    2) close 기준 안전하면, 포지션 하나씩만 해당 1H adverse extreme으로 stress.



    3) breach가 생기는 포지션만 최초 breach 가격을 binary-search로 근사 청산.







    V6.2처럼 모든 LONG=low / SHORT=high를 동시에 합산하지 않는다.



    1H OHLC만으로 불가능한 동시 worst-case를 만들지 않기 위한 수정이다.



    """



    liquidated = []







    while positions:



        base = account_state(



            wallet,



            positions,



            market,



            snapshot,



            ts,



            stress=False,



        )







        # A. synchronized close 자체가 이미 unsafe면 close 기준 손실기여 최대 포지션부터 정리.



        if base["margin_balance"] <= base["maintenance"]:



            ranked = []



            for trade_id, p in positions.items():



                mark = float(base["marks"].get(trade_id, p["entry_price"]))



                pnl = position_upnl(p, mark)



                ranked.append((pnl, trade_id, mark))







            ranked.sort(key=lambda x: x[0])



            _, trade_id, mark = ranked[0]



            p = positions.pop(trade_id)







            pnl = position_upnl(p, mark)



            liq_fee = abs(float(p["qty"]) * mark) * FEE_RATE



            wallet += pnl - liq_fee



            liquidated.append((trade_id, p["symbol"], "CLOSE_MARGIN"))







            # Cross 계좌는 wallet balance만 음수라고 파산이 아니다.



            # 남은 포지션의 미실현손익까지 포함한 total equity로 다음 loop에서 재판정한다.



            continue







        # B. 한 포지션씩만 intrabar stress. 가장 가까운 trigger가 먼저 발생한다고 본다.



        candidates = []



        for trade_id, p in positions.items():



            trigger = _cross_trigger_price(



                wallet,



                positions,



                market,



                snapshot,



                ts,



                trade_id,



                base,



            )



            if trigger is None:



                continue







            close_mark = float(base["marks"].get(trade_id, p["entry_price"]))



            distance = abs(trigger / close_mark - 1.0) if close_mark > 0 else np.inf



            candidates.append((distance, trade_id, trigger))







        if not candidates:



            break







        candidates.sort(key=lambda x: (x[0], x[1]))



        _, trade_id, trigger = candidates[0]



        # LAB_LIQUIDATION_MARK_DIAG_V1
        if diagnostics is not None:
            diag_p = positions[trade_id]
            diag_state = account_state(
                wallet,
                positions,
                market,
                snapshot,
                ts,
                stress=False,
            )
            diag_close = float(
                diag_state['marks'].get(
                    trade_id,
                    diag_p['entry_price'],
                )
            )
            diag_adverse = float(
                get_mark(
                    market,
                    diag_p['symbol'],
                    ts,
                    diag_p['side'],
                    stress=True,
                )
            )
            diagnostics.append({
                'event': 'LIQUIDATION_DIAG',
                'reason': 'INTRABAR_SINGLE',
                'ts': int(ts),
                'trade_id': int(trade_id),
                'symbol': str(diag_p['symbol']).upper(),
                'owner_tf': str(
                    diag_p.get(
                        'owner_tf',
                        'UNKNOWN',
                    )
                ).upper(),
                'side': int(diag_p['side']),
                'entry_ts': int(diag_p['entry_ts']),
                'entry_price': float(diag_p['entry_price']),
                'qty': float(diag_p['qty']),
                'requested_leverage': int(
                    diag_p['requested_leverage']
                ),
                'actual_leverage': int(
                    diag_p['actual_leverage']
                ),
                'ordinary_close': float(diag_close),
                'ordinary_adverse_extreme': float(diag_adverse),
                'liquidation_trigger': float(trigger),
                'wallet_before_liq': float(wallet),
                'margin_balance_before_liq': float(
                    diag_state['margin_balance']
                ),
                'maintenance_before_liq': float(
                    diag_state['maintenance']
                ),
                'close_cushion_before_liq': float(
                    diag_state['margin_balance']
                    - diag_state['maintenance']
                ),
            })
        p = positions.pop(trade_id)







        pnl = position_upnl(p, trigger)



        liq_fee = abs(float(p["qty"]) * trigger) * FEE_RATE



        wallet += pnl - liq_fee



        liquidated.append((trade_id, p["symbol"], "INTRABAR_SINGLE"))







        # Cross 계좌 파산 여부는 wallet 단독이 아니라



        # wallet + remaining uPnL = margin_balance 로 판단한다.



        # 다음 loop에서 account_state()가 계좌 전체를 다시 평가한다.







    return float(wallet), liquidated











# ============================================================



# PORTFOLIO SIMULATION - CROSS MARGIN



# ============================================================







def simulate_portfolio(



    df: pd.DataFrame,



    snapshot: dict,



    active_count: int,



    start_equity: float,



    leverage_selector,



    market: dict,



):



    wallet = float(start_equity)



    positions = {}



    curve = [wallet]







    entries_count = 0



    liquidations = 0



    close_margin_liquidations = 0



    intrabar_liquidations = 0




    skipped = 0



    traded_symbols = set()



    liquidated_symbols = set()



    actual_leverages = []







    rows = {int(r.trade_id): r for r in df.itertuples()}



    entry_events = {}



    exit_events = {}







    for r in df.itertuples():



        entry_events.setdefault(int(r.entry_ts), []).append(int(r.trade_id))



        exit_events.setdefault(int(r.exit_ts), []).append(int(r.trade_id))







    timeline = sorted(



        set(entry_events)



        | set(exit_events)



        | {int(x) for x in df["entry_ts"].tolist()}



        | {int(x) for x in df["exit_ts"].tolist()}



    )







    # trade 이벤트 사이에도 liquidation은 생길 수 있으므로



    # 테스트 구간의 실제 1H cache timeline 전체를 사용한다.



    if market:



        # ?? 35??? ???? ?? ????



        # ? ??? timeline? ?? ?? ????? ???? ? ??.



        # ?? market timestamp? union? ?? timeline?? ????.



        market_ts = set()







        for symbol_rows in market.values():



            market_ts.update(



                int(x)



                for x in symbol_rows.keys()



            )







        all_market_ts = sorted(



            market_ts



        )







        min_ts = min(timeline)



        max_ts = max(timeline)







        timeline = [



            x



            for x in all_market_ts



            if min_ts <= x <= max_ts



        ]







    liquidated_ids = set()







    for ts in timeline:



        # 1) 이전 시간부터 보유 중인 포지션의 CROSS 유지증거금 점검.



        if positions:



            wallet, liqs = liquidate_cross_until_safe(



                wallet,



                positions,



                market,



                snapshot,



                ts,



            )



            for trade_id, symbol, reason in liqs:



                liquidated_ids.add(int(trade_id))



                liquidations += 1



                liquidated_symbols.add(str(symbol))



                if reason == "CLOSE_MARGIN":



                    close_margin_liquidations += 1



                elif reason == "INTRABAR_SINGLE":



                    intrabar_liquidations += 1







        # 2) 정상 전략 청산. liquidation된 trade는 이미 제거됨.



        for trade_id in exit_events.get(ts, []):



            if trade_id in liquidated_ids:



                continue







            p = positions.pop(trade_id, None)



            if p is None:



                continue







            mark = get_mark(market, p["symbol"], ts, stress=False)



            if not np.isfinite(mark) or mark <= 0:



                mark = float(p["exit_price"])







            pnl = position_upnl(p, mark)



            exit_fee = abs(p["qty"] * mark) * FEE_RATE



            wallet += pnl - exit_fee



            # Cross에서는 wallet balance가 일시적으로 음수여도



            # 다른 포지션의 양(+) uPnL로 account equity가 살아 있을 수 있다.







        # 3) 신규 진입. 서버와 같이 현재 availableBalance를 /35.



        for trade_id in entry_events.get(ts, []):



            row = rows[trade_id]







            close_state = account_state(



                wallet,



                positions,



                market,



                snapshot,



                ts,



                stress=False,



            )



            available = close_state["available"]



            if available <= 0:



                skipped += 1



                continue







            requested = max(10, min(50, int(leverage_selector(row))))



            symbol = str(row.symbol).upper()



            brackets = flatten_brackets(snapshot, symbol)







            allocation = available / float(active_count)



            actual = resolve_actual_leverage(brackets, allocation, requested)







            entry_price = float(row.entry_price)



            mark = get_mark(market, symbol, ts, stress=False)



            if np.isfinite(mark) and mark > 0:



                entry_price = float(mark)







            notional = allocation * actual







            f = snapshot.get("order_filters", {}).get(symbol, {})



            min_notional = safe_float(f.get("min_notional"), 0.0)



            if min_notional > 0 and notional < min_notional:



                notional = min_notional * 1.20



                allocation = notional / actual







            if allocation <= 0 or allocation > available:



                skipped += 1



                continue







            raw_qty = notional / entry_price



            qty = adjust_qty(snapshot, symbol, raw_qty, entry_price)



            if qty <= 0:



                skipped += 1



                continue







            actual_notional = qty * entry_price



            initial_margin = actual_notional / actual



            entry_fee = actual_notional * FEE_RATE







            # 서버 availableBalance 기준으로 신규 증거금 슬롯을 제한한다.



            # wallet 단독 값으로 진입을 막지 않는다. Cross는 total equity가 기준이다.



            if initial_margin > available:



                skipped += 1



                continue







            if entry_fee >= close_state["margin_balance"]:



                skipped += 1



                continue







            wallet -= entry_fee







            positions[trade_id] = {



                "symbol": symbol,



                "side": int(row.side),



                "entry_price": entry_price,



                "exit_price": float(row.exit_price),



                "qty": float(qty),



                "initial_margin": float(initial_margin),



                "requested_leverage": int(requested),



                "actual_leverage": int(actual),



            }







            entries_count += 1



            traded_symbols.add(symbol)



            actual_leverages.append(actual)







        # 4) 계좌 equity curve는 close mark-to-market 기준.



        st = account_state(wallet, positions, market, snapshot, ts, stress=False)



        curve.append(max(0.0, st["margin_balance"]))







    # 마지막 close 기준 equity.



    if timeline:



        st = account_state(wallet, positions, market, snapshot, timeline[-1], stress=False)



        final_equity = float(max(0.0, st["margin_balance"]))



    else:



        final_equity = float(max(0.0, wallet))







    compound_pct = (final_equity / start_equity - 1.0) * 100.0



    mdd_pct = max_drawdown_pct(curve)



    liq_trade_rate = liquidations / entries_count * 100.0 if entries_count else np.nan



    liq_symbol_rate = (



        len(liquidated_symbols) / len(traded_symbols) * 100.0



        if traded_symbols else np.nan



    )







    growth_log = (



        math.log(max(final_equity, 1e-12) / start_equity)



        if final_equity > 0 else -100.0



    )







    aggressive_score = (



        growth_log



        - 0.10 * abs(min(mdd_pct, 0.0)) / 100.0



        - 0.25 * (liq_trade_rate / 100.0 if np.isfinite(liq_trade_rate) else 1.0)



    )







    return {



        "start_equity": start_equity,



        "final_equity": final_equity,



        "compound_pct": compound_pct,



        "realized_mdd_pct": mdd_pct,



        "entries": entries_count,



        "liquidations": liquidations,



        "close_margin_liquidations": close_margin_liquidations,



        "intrabar_single_liquidations": intrabar_liquidations,



        "liquidation_trade_rate_pct": liq_trade_rate,



        "liquidated_symbols": len(liquidated_symbols),



        "traded_symbols": len(traded_symbols),



        "liquidated_symbol_rate_pct": liq_symbol_rate,



        "avg_actual_leverage": float(np.mean(actual_leverages)) if actual_leverages else np.nan,



        "max_actual_leverage": int(max(actual_leverages)) if actual_leverages else 0,



        "skipped_entries": skipped,



        "bankrupt": int(final_equity <= 0.0),



        "aggressive_score": float(aggressive_score),



    }











# ============================================================



# TEST FRAME



# ============================================================







# ============================================================


# CURRENT LIVE MULTI-FRAME PORTFOLIO


#


# ?? LIVE ?? ??:


#   1H  DIV+EMA_CROSS   10x


#   6H  SHAPE+EMA_CROSS 20x


#   12H MACD+STOCH      10x


#


# ?? LIVE ??:


# - symbol? one-way ??? 1?


# - FLAT?? allow ??? ??? 1?? ?? ??


# - 2? ???? MULTI_TF_CANDIDATE -> SKIP


# - ?? TF/strategy? owner


# - ?? ??? owner TF? ??


# - owner ?? consensus??? close + reverse


#


# ??:


# ? signal-book builder? ?? LIVE 1H/6H/12H ??.


# 1W/1M ??? confirmation schedule?? ??? ???? ???.


# ============================================================








def build_current_live_multiframe_signal_book(


    cache,


    signal_fn,


    frames=("1H", "6H", "12H"),


):


    frame_seconds = {


        "1H": 3600,


        "6H": 6 * 3600,


        "12H": 12 * 3600,


    }





    frames = tuple(


        str(x).upper()


        for x in frames


    )





    bad = [


        tf


        for tf in frames


        if tf not in frame_seconds


    ]





    if bad:


        raise ValueError(


            f"unsupported current-live frame: {bad}"


        )





    runner_v4 = load_runner_v4()


    lab = runner_v4.load_lab_engine()





    signal_book = {}





    grouped = list(


        cache.groupby(


            "symbol",


            sort=False,


        )


    )





    for tf in frames:





        sec = frame_seconds[tf]





        for symbol, sdf in grouped:





            symbol = str(


                symbol


            ).upper()





            d = (


                sdf


                .sort_values("ts")


                .reset_index(drop=True)


            )





            d = runner_v4.add_ema_warmup_valid(


                source=d,


                lab=lab,


                symbol=symbol,


                tf=tf,


            )





            sig = normalize_signal(


                signal_fn(


                    d.copy(),


                    tf,


                ),


                len(d),


            )





            sig = np.asarray(


                sig,


                dtype=np.int8,


            )





            ts_arr = d[


                "ts"


            ].to_numpy(


                dtype=np.int64


            )





            confirmed = (


                ts_arr % sec


                == 0


            )





            signal_book[


                (


                    symbol,


                    tf,


                )


            ] = (


                ts_arr[


                    confirmed


                ],


                sig[


                    confirmed


                ],


            )





    return signal_book








# ============================================================

# RESEARCH SIX-FRAME SIGNAL BOOK

#

# ??:

#   1H / 6H / 12H / 1D / 1W / 1M

#

# CACHE_MATERIALS:

# - 1H~1W:

#   long hourly cache?? ??? ????,

#   ?? raw TF? confirmed_ts_for_tf() ?????

#   Gate decision? ????.

#

# - 1M:

#   build_monthly_material_frame() ?? source? ??.

#   ts ??? ?? ? ?? = ?? ????.

#

# OHLCV:

# - source_frame_for_symbol()? ??? raw confirmed frame? ??.

#

# ??? ??:

# - ?? epoch modulo? 1D/1W confirmation? ???? ???.

# - HOLD(0)? confirmation update? ????.

# - simulator? ?? confirmation?? ??? decision? ????.

# ============================================================





def build_research_six_frame_signal_book(

    cache,

    signal_fn,

    frames=(

        "1H",

        "6H",

        "12H",

        "1D",

        "1W",

        "1M",

    ),

    data_mode="CACHE_MATERIALS",

):

    runner_v4 = load_runner_v4()



    lab = runner_v4.load_lab_engine()



    material_modules = (

        runner_v4.load_material_modules()

    )



    frames = tuple(

        str(x).upper()

        for x in frames

    )



    allowed = {

        "1H",

        "6H",

        "12H",

        "1D",

        "1W",

        "1M",

    }



    bad = [

        tf

        for tf in frames

        if tf not in allowed

    ]



    if bad:

        raise ValueError(

            f"unsupported research frame: {bad}"

        )



    mode = str(

        data_mode

    ).upper()



    if mode not in (

        "CACHE_MATERIALS",

        "OHLCV",

    ):

        raise ValueError(

            f"unsupported data_mode: {data_mode}"

        )



    symbols = sorted(

        str(x).upper()

        for x in cache[

            "symbol"

        ].dropna().unique()

    )



    monthly_cache = {}



    signal_book = {}



    meta_rows = []



    for tf in frames:



        for symbol in symbols:



            source = (

                runner_v4.source_frame_for_symbol(

                    cache=cache,

                    lab=lab,

                    material_modules=

                        material_modules,

                    symbol=symbol,

                    tf=tf,

                    data_mode=mode,

                    monthly_cache=

                        monthly_cache,

                )

            )



            if source is None or source.empty:



                signal_book[

                    (

                        symbol,

                        tf,

                    )

                ] = (

                    np.array(

                        [],

                        dtype=np.int64,

                    ),

                    np.array(

                        [],

                        dtype=np.int8,

                    ),

                )



                meta_rows.append({

                    "symbol": symbol,

                    "timeframe": tf,

                    "source_rows": 0,

                    "confirmed_rows": 0,

                    "nonzero_rows": 0,

                })



                continue



            d = (

                source

                .sort_values("ts")

                .drop_duplicates("ts")

                .reset_index(drop=True)

            )



            # EMA_CROSS production warm-up semantics.

            d = runner_v4.add_ema_warmup_valid(

                source=d,

                lab=lab,

                symbol=symbol,

                tf=tf,

            )



            signal = normalize_signal(

                signal_fn(

                    d.copy(),

                    tf,

                ),

                len(d),

            )



            signal = np.asarray(

                signal,

                dtype=np.int8,

            )



            ts_arr = d[

                "ts"

            ].to_numpy(

                dtype=np.int64

            )



            # ------------------------------------------------

            # CACHE_MATERIALS 1H~1W

            #

            # d? hourly cache??? actual raw TF?? ??

            # ??? confirmation timestamp? ??.

            # ------------------------------------------------



            if (

                mode

                == "CACHE_MATERIALS"

                and

                tf != "1M"

            ):



                prefix = tf.lower()



                raw_path = (

                    lab.find_tf_file(

                        runner_v4.DATA_DIR,

                        symbol,

                        prefix,

                    )

                )



                raw_tf = (

                    lab.read_ohlc(

                        raw_path

                    )

                    .copy()

                )



                confirmed_ts = (

                    lab.confirmed_ts_for_tf(

                        raw_tf,

                        prefix,

                    )

                )



                confirmed_ts = (

                    pd.to_numeric(

                        confirmed_ts,

                        errors="coerce",

                    )

                    .dropna()

                    .astype("int64")

                    .to_numpy()

                )



                confirmed_ts = np.unique(

                    confirmed_ts

                )



                mask = np.isin(

                    ts_arr,

                    confirmed_ts,

                    assume_unique=False,

                )



                out_ts = ts_arr[

                    mask

                ]



                out_signal = signal[

                    mask

                ]



            else:



                # 1M CACHE_MATERIALS:

                # build_monthly_material_frame()? ts?

                # ?? ?? ? ?? confirmation.

                #

                # OHLCV:

                # source_frame_for_symbol()? raw TF?

                # confirmed timestamp ???? ??.

                out_ts = ts_arr

                out_signal = signal



            out_ts = np.asarray(

                out_ts,

                dtype=np.int64,

            )



            out_signal = np.asarray(

                out_signal,

                dtype=np.int8,

            )



            if len(

                out_ts

            ) != len(

                out_signal

            ):

                raise RuntimeError(

                    "research signal book "

                    f"length mismatch: "

                    f"{symbol} {tf}"

                )



            if (

                len(out_ts) > 1

                and

                np.any(

                    np.diff(

                        out_ts

                    )

                    <= 0

                )

            ):

                raise RuntimeError(

                    "research signal timestamps "

                    f"not strictly increasing: "

                    f"{symbol} {tf}"

                )



            signal_book[

                (

                    symbol,

                    tf,

                )

            ] = (

                out_ts,

                out_signal,

            )



            meta_rows.append({

                "symbol":

                    symbol,

                "timeframe":

                    tf,

                "source_rows":

                    int(

                        len(d)

                    ),

                "confirmed_rows":

                    int(

                        len(out_ts)

                    ),

                "nonzero_rows":

                    int(

                        np.count_nonzero(

                            out_signal

                        )

                    ),

            })



    meta = pd.DataFrame(

        meta_rows

    )



    return (

        signal_book,

        meta,

    )



# ============================================================
# LAB_MARK_LIQUIDATION_V1
#
# Optional exchange-liquidation price source:
#
#   LAB_MARK_LIQUIDATION=0
#       existing ordinary OHLC liquidation model
#
#   LAB_MARK_LIQUIDATION=1
#       Binance USD-M Mark Price 1H OHLC
#
# Important timestamp alignment:
#
# Mark parquet ts is candle OPEN time.
# simulate_live_multiframe ts is effective confirmed/close time.
#
# Therefore Mark raw ts is shifted +3600 seconds.
#
# Guard / Control SL / strategy logic is NOT changed here.
# ============================================================

_LAB_MARK_LIQUIDATION_MARKET_CACHE = {
    "signature": None,
    "market": None,
}


def _lab_mark_liquidation_market(
    base_market,
):

    global _LAB_MARK_LIQUIDATION_MARKET_CACHE

    mark_dir = (
        LAB
        / "data"
        / "mark_price_1h"
    )


    if not mark_dir.exists():

        raise RuntimeError(
            "LAB_MARK_LIQUIDATION=1 but "
            f"Mark directory missing: {mark_dir}"
        )


    symbols = tuple(
        sorted(
            str(x).upper()
            for x in base_market.keys()
        )
    )


    signature_parts = []


    for symbol in symbols:

        rows = base_market.get(
            symbol,
            {}
        )

        keys = list(
            rows.keys()
        )


        if keys:

            signature_parts.append(
                (
                    symbol,
                    int(
                        min(
                            keys
                        )
                    ),
                    int(
                        max(
                            keys
                        )
                    ),
                    int(
                        len(
                            keys
                        )
                    ),
                )
            )

        else:

            signature_parts.append(
                (
                    symbol,
                    None,
                    None,
                    0,
                )
            )


    signature = tuple(
        signature_parts
    )


    if (
        _LAB_MARK_LIQUIDATION_MARKET_CACHE[
            "signature"
        ]
        ==
        signature
        and
        _LAB_MARK_LIQUIDATION_MARKET_CACHE[
            "market"
        ]
        is not None
    ):

        return (
            _LAB_MARK_LIQUIDATION_MARKET_CACHE[
                "market"
            ]
        )


    out = {}

    total_rows = 0
    mark_rows = 0
    fallback_rows = 0

    missing_files = []


    for symbol in symbols:

        ordinary = base_market.get(
            symbol,
            {}
        )


        mark_path = (
            mark_dir
            /
            f"{symbol}.parquet"
        )


        mark_lookup = {}


        if mark_path.exists():

            d = pd.read_parquet(
                mark_path
            ).copy()


            lookup = {
                str(c).lower():
                    c
                for c in d.columns
            }


            ts_col = next(
                (
                    lookup[name]
                    for name in (
                        "ts",
                        "open_time",
                        "open_ts",
                        "timestamp",
                        "time",
                    )
                    if name in lookup
                ),
                None,
            )


            if ts_col is None:

                raise RuntimeError(
                    f"{symbol}: Mark timestamp column missing | "
                    f"columns={list(d.columns)}"
                )


            for name in (
                "high",
                "low",
                "close",
            ):

                if name not in lookup:

                    raise RuntimeError(
                        f"{symbol}: Mark {name} column missing | "
                        f"columns={list(d.columns)}"
                    )


            mts = pd.to_numeric(
                d[
                    ts_col
                ],
                errors="coerce",
            )


            med = (
                mts.dropna()
                .abs()
                .median()
            )


            if (
                pd.notna(
                    med
                )
                and
                med
                >
                10_000_000_000
            ):

                mts = (
                    mts
                    /
                    1000.0
                )


            md = pd.DataFrame({
                "raw_ts":
                    mts,

                "high":
                    pd.to_numeric(
                        d[
                            lookup[
                                "high"
                            ]
                        ],
                        errors="coerce",
                    ),

                "low":
                    pd.to_numeric(
                        d[
                            lookup[
                                "low"
                            ]
                        ],
                        errors="coerce",
                    ),

                "close":
                    pd.to_numeric(
                        d[
                            lookup[
                                "close"
                            ]
                        ],
                        errors="coerce",
                    ),
            })


            md = (
                md
                .dropna(
                    subset=[
                        "raw_ts",
                        "high",
                        "low",
                        "close",
                    ]
                )
                .copy()
            )


            md[
                "raw_ts"
            ] = (
                md[
                    "raw_ts"
                ]
                .astype(
                    "int64"
                )
            )


            # ------------------------------------------------
            # Mark raw timestamp = candle OPEN.
            #
            # Simulator timestamp = effective close/confirmed
            # timestamp.
            #
            # Example:
            #
            # Mark 02:00 candle
            #     represents 02:00 -> 03:00
            #
            # Simulator effective ts
            #     = 03:00
            # ------------------------------------------------

            md[
                "effective_ts"
            ] = (
                md[
                    "raw_ts"
                ]
                +
                3600
            )


            md = (
                md
                .sort_values(
                    "effective_ts"
                )
                .drop_duplicates(
                    "effective_ts",
                    keep="last",
                )
            )


            for r in md.itertuples(
                index=False
            ):

                effective_ts = int(
                    r.effective_ts
                )

                close = float(
                    r.close
                )

                low = float(
                    r.low
                )

                high = float(
                    r.high
                )


                if (
                    np.isfinite(
                        close
                    )
                    and
                    np.isfinite(
                        low
                    )
                    and
                    np.isfinite(
                        high
                    )
                    and
                    close > 0.0
                    and
                    low > 0.0
                    and
                    high > 0.0
                ):

                    mark_lookup[
                        effective_ts
                    ] = (
                        close,
                        low,
                        high,
                    )


        else:

            missing_files.append(
                symbol
            )


        merged = {}


        for ts, ordinary_row in ordinary.items():

            ts = int(
                ts
            )

            total_rows += 1


            if ts in mark_lookup:

                merged[
                    ts
                ] = (
                    mark_lookup[
                        ts
                    ]
                )

                mark_rows += 1

            else:

                # --------------------------------------------
                # Conservative continuity fallback.
                #
                # Do NOT silently remove the liquidation test
                # merely because one Mark 1H row is absent.
                #
                # Any liquidation occurring on a fallback row
                # must later be diagnosed separately.
                # --------------------------------------------

                merged[
                    ts
                ] = (
                    ordinary_row
                )

                fallback_rows += 1


        out[
            symbol
        ] = merged


    coverage_pct = (
        (
            mark_rows
            /
            total_rows
        )
        *
        100.0
        if total_rows
        else
        0.0
    )


    print()
    print(
        "=" * 120
    )

    print(
        "LAB MARK LIQUIDATION MARKET"
    )

    print(
        "=" * 120
    )

    print(
        "MODE          = BINANCE USD-M MARK PRICE 1H"
    )

    print(
        "ALIGNMENT     = MARK raw ts + 3600"
    )

    print(
        "TOTAL ROWS    =",
        total_rows,
    )

    print(
        "MARK ROWS     =",
        mark_rows,
    )

    print(
        "FALLBACK ROWS =",
        fallback_rows,
    )

    print(
        "MARK COVERAGE =",
        f"{coverage_pct:.4f}%",
    )

    print(
        "MISSING FILES =",
        (
            ",".join(
                missing_files
            )
            if missing_files
            else
            "NONE"
        ),
    )

    print(
        "=" * 120
    )

    print()


    _LAB_MARK_LIQUIDATION_MARKET_CACHE = {
        "signature":
            signature,

        "market":
            out,
    }


    return out


# LAB_MARK_LIQUIDATION_V1_HELPERS_END


def simulate_live_multiframe(


    signal_book,


    symbols,


    snapshot,


    market,


    start_ts,


    end_ts,


    leverage_map,


    frames=("1H", "6H", "12H"),


    active_count=35,


    start_equity=10000.0,


    collect_trade_log: bool = False,

    trailing_start_pct=None,
    trailing_callback_pct=None,

    state_reentry_book=None,
    state_reentry_pullback_pct=0.0,
    sizing_multiplier=1.0,
    portfolio_initial_margin_cap_ratio=None,
    entry_priority_mode=None,
    entry_priority_telemetry=False,

):



    # LAB_MARK_LIQUIDATION_V1_INIT

    # R6A_COMPETITION_TELEMETRY_V1 BEGIN
    _r6a_comp_enabled = bool(entry_priority_telemetry) or (
        __import__("os").environ.get(
            "BBOT_R6A_ENTRY_PRIORITY_TELEMETRY",
            ""
        ) == "1"
    )
    if _r6a_comp_enabled:
        globals()["_r6a_entry_priority_telemetry_runtime"] = {
            "schema": "B_BOT_R6A_COMPETITION_RUNTIME_V1",
            "timestamps": {},
            "dispatch_attempt_total": 0,
            "dispatch_success_total": 0,
            "dispatch_fail_total": 0,
        }
        _r6a_comp_path = __import__("os").environ.get(
            "BBOT_R6A_ENTRY_PRIORITY_TELEMETRY_PATH",
            ""
        )
        if _r6a_comp_path:
            def _r6a_comp_flush():
                _r6a_comp_payload = globals().get(
                    "_r6a_entry_priority_telemetry_runtime"
                )
                if _r6a_comp_payload is None:
                    return
                with open(
                    _r6a_comp_path,
                    "w",
                    encoding="utf-8",
                ) as _r6a_comp_f:
                    __import__("json").dump(
                        _r6a_comp_payload,
                        _r6a_comp_f,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
            __import__("atexit").register(
                _r6a_comp_flush
            )
    # R6A_COMPETITION_TELEMETRY_V1 END

    lab_mark_liquidation_enabled = (
        os.getenv(
            "LAB_MARK_LIQUIDATION",
            "0",
        )
        ==
        "1"
    )


    if lab_mark_liquidation_enabled:

        liquidation_market = (
            _lab_mark_liquidation_market(
                market
            )
        )

    else:

        liquidation_market = market

    # LAB_MARK_LIQUIDATION_V1_INIT_END

    trailing_enabled = (
        trailing_start_pct is not None
        or trailing_callback_pct is not None
    )

    if trailing_enabled:

        if (
            trailing_start_pct is None
            or trailing_callback_pct is None
        ):
            raise ValueError(
                "trailing requires both start and callback"
            )

        trailing_start_pct = float(
            trailing_start_pct
        )

        trailing_callback_pct = float(
            trailing_callback_pct
        )

        if not (
            0.0 < trailing_start_pct < 100.0
        ):
            raise ValueError(
                "bad trailing_start_pct"
            )

        if not (
            0.0 < trailing_callback_pct < 100.0
        ):
            raise ValueError(
                "bad trailing_callback_pct"
            )


    state_reentry_enabled = (
        state_reentry_book is not None
    )

    if (
        state_reentry_enabled
        and not trailing_enabled
    ):
        raise ValueError(
            "STATE reentry requires trailing enabled"
        )

    state_reentry_pullback_pct = float(
        state_reentry_pullback_pct
    )

    if not (
        0.0
        <= state_reentry_pullback_pct
        < 100.0
    ):
        raise ValueError(
            "bad state_reentry_pullback_pct"
        )

    if (
        state_reentry_pullback_pct > 0.0
        and not state_reentry_enabled
    ):
        raise ValueError(
            "pullback reentry requires STATE reentry enabled"
        )

    frames = tuple(


        str(x).upper()


        for x in frames


    )





    symbols = [


        str(x).upper()


        for x in symbols


    ]





    start_ts = int(


        start_ts


    )





    end_ts = int(


        end_ts


    )





    active_count = int(


        active_count


    )





    start_equity = float(


        start_equity


    )





    if active_count <= 0:


        raise ValueError(


            "active_count <= 0"


        )





    if end_ts <= start_ts:


        raise ValueError(


            "invalid simulation window"


        )





    missing_lev = [


        tf


        for tf in frames


        if tf not in leverage_map


    ]





    if missing_lev:


        raise ValueError(


            f"missing leverage: {missing_lev}"


        )





    # --------------------------------------------------------


    # Fold ?? ?? ??? ?? state +


    # Fold ?? confirmation ???? ??


    # --------------------------------------------------------






    if state_reentry_enabled:

        if not isinstance(
            state_reentry_book,
            dict,
        ):
            raise TypeError(
                "state_reentry_book must be dict"
            )

        missing_state_keys = [
            (symbol, tf)
            for symbol in symbols
            for tf in frames
            if (symbol, tf)
            not in state_reentry_book
        ]

        if missing_state_keys:
            raise ValueError(
                "missing STATE reentry keys: "
                + str(
                    missing_state_keys[:10]
                )
            )

    frame_state = {}


    updates = {}





    for symbol in symbols:





        for tf in frames:





            key = (


                symbol,


                tf,


            )





            if key not in signal_book:





                frame_state[


                    key


                ] = 0





                continue





            ts_arr, sig_arr = (


                signal_book[key]


            )





            ts_arr = np.asarray(


                ts_arr,


                dtype=np.int64,


            )





            sig_arr = np.asarray(


                sig_arr,


                dtype=np.int8,


            )





            if len(ts_arr) != len(sig_arr):


                raise RuntimeError(


                    f"signal book length mismatch: "


                    f"{symbol} {tf}"


                )





            prior_i = (


                np.searchsorted(


                    ts_arr,


                    start_ts,


                    side="right",


                )


                - 1


            )





            frame_state[


                key


            ] = (


                int(


                    sig_arr[


                        prior_i


                    ]


                )


                if prior_i >= 0


                else 0


            )





            lo = np.searchsorted(


                ts_arr,


                start_ts,


                side="left",


            )





            hi = np.searchsorted(


                ts_arr,


                end_ts,


                side="left",


            )





            for i in range(


                int(lo),


                int(hi),


            ):





                ts = int(


                    ts_arr[i]


                )





                updates.setdefault(


                    ts,


                    [],


                ).append(


                    (


                        symbol,


                        tf,


                        int(


                            sig_arr[i]


                        ),


                    )


                )





    # --------------------------------------------------------


    # ?? 1H market union timeline


    # --------------------------------------------------------





    market_ts = set()





    for symbol_rows in market.values():





        market_ts.update(


            int(x)


            for x in symbol_rows.keys()


            if (


                start_ts


                <= int(x)


                < end_ts


            )


        )





    timeline = sorted(


        market_ts


    )





    if not timeline:


        raise RuntimeError(


            "multiframe market timeline empty"


        )





    # --------------------------------------------------------


    # Account state


    # --------------------------------------------------------





    wallet = float(


        start_equity


    )





    positions = {}

    trade_log = []

    trailing_closes = 0

    state_reentry_wait = {}
    state_reentries = 0
    state_reentry_cancels = 0
    state_reentry_skips = 0
    state_reentry_pullback_waits = 0



    symbol_to_pid = {}





    next_pid = 1





    curve = [


        wallet


    ]





    entries = 0


    reversals = 0





    multi_tf_skips = 0



    # ========================================================
    # LAB_RISK_PROBE_V1_BEGIN
    #
    # Diagnostic only.
    # This block MUST NOT change entry / liquidation decisions.
    #
    # Enable:
    #   $env:LAB_RISK_PROBE="1"
    #
    # Disable:
    #   Remove-Item Env:LAB_RISK_PROBE
    # ========================================================

    collect_risk_probe = (
        str(
            __import__("os").environ.get(
                "LAB_RISK_PROBE",
                "0",
            )
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


    risk_probe_rows = []

    # ========================================================
    # LAB_CROSS_SURVIVAL_GUARD_V1_CONFIG
    #
    # Defaults OFF.
    #
    # LAB_CROSS_GUARD=1
    #     New exposure Entry Gate.
    #
    # LAB_CONTROL_SL=1
    #     Existing-position account-survival Control SL.
    # ========================================================

    lab_cross_guard_enabled = (
        str(
            _lab_os.getenv(
                "LAB_CROSS_GUARD",
                "0",
            )
        )
        ==
        "1"
    )

    lab_control_sl_enabled = (
        str(
            _lab_os.getenv(
                "LAB_CONTROL_SL",
                "0",
            )
        )
        ==
        "1"
    )


    risk_guard_stats = {
        "entry_checks":
            0,

        "entry_blocks":
            0,

        "entry_no_history_blocks":
            0,

        "entry_blocks_by_tf":
            {},

        "control_sl_checks":
            0,

        "control_sl_breaches":
            0,

        "control_sl_closes":
            0,

        "control_sl_by_tf":
            {},
    }


    risk_guard_events = []

    # LAB_CROSS_SURVIVAL_GUARD_V1_CONFIG_END



    def _lab_risk_probe_metrics(
        wallet_value,
        positions_value,
        ts_value,
    ):

        st_probe = account_state(
            float(wallet_value),
            positions_value,
            market,
            snapshot,
            int(ts_value),
            stress=False,
        )

        margin_balance = float(
            st_probe[
                "margin_balance"
            ]
        )

        maintenance = float(
            st_probe[
                "maintenance"
            ]
        )

        available_probe = float(
            st_probe[
                "available"
            ]
        )

        upnl_probe = float(
            st_probe[
                "upnl"
            ]
        )

        total_initial = float(
            sum(
                float(
                    x.get(
                        "initial_margin",
                        0.0,
                    )
                )
                for x
                in positions_value.values()
            )
        )

        gross_notional = 0.0
        long_notional = 0.0
        short_notional = 0.0

        for probe_trade_id, probe_p in (
            positions_value.items()
        ):

            probe_mark = float(
                st_probe[
                    "marks"
                ].get(
                    probe_trade_id,
                    probe_p[
                        "entry_price"
                    ],
                )
            )

            probe_notional = abs(
                float(
                    probe_p[
                        "qty"
                    ]
                )
                *
                probe_mark
            )

            gross_notional += (
                probe_notional
            )

            if int(
                probe_p[
                    "side"
                ]
            ) > 0:
                long_notional += (
                    probe_notional
                )

            else:
                short_notional += (
                    probe_notional
                )


        if margin_balance > 0.0:

            effective_leverage = (
                gross_notional
                /
                margin_balance
            )

            maintenance_ratio = (
                maintenance
                /
                margin_balance
            )

            cushion_ratio = (
                (
                    margin_balance
                    -
                    maintenance
                )
                /
                margin_balance
            )

            initial_margin_ratio = (
                total_initial
                /
                margin_balance
            )

        else:

            effective_leverage = (
                float("inf")
            )

            maintenance_ratio = (
                float("inf")
            )

            cushion_ratio = (
                float("-inf")
            )

            initial_margin_ratio = (
                float("inf")
            )


        cushion_abs = float(
            margin_balance
            -
            maintenance
        )


        # ----------------------------------------------------
        # V6.4-compatible one-position-at-a-time
        # 1H adverse-extreme stress diagnostic.
        #
        # DO NOT simultaneously stress every position.
        # ----------------------------------------------------

        min_single_stress_cushion_abs = (
            cushion_abs
        )

        min_single_stress_cushion_ratio = (
            cushion_ratio
        )

        min_single_stress_margin_balance = (
            margin_balance
        )

        single_stress_breach_count = 0


        for probe_trade_id, probe_p in (
            positions_value.items()
        ):

            close_mark = float(
                st_probe[
                    "marks"
                ].get(
                    probe_trade_id,
                    probe_p[
                        "entry_price"
                    ],
                )
            )

            if (
                not np.isfinite(
                    close_mark
                )
                or
                close_mark <= 0.0
            ):
                close_mark = float(
                    probe_p[
                        "entry_price"
                    ]
                )


            adverse_mark = get_mark(
                market,
                probe_p[
                    "symbol"
                ],
                int(
                    ts_value
                ),
                probe_p[
                    "side"
                ],
                stress=True,
            )

            if (
                not np.isfinite(
                    adverse_mark
                )
                or
                adverse_mark <= 0.0
            ):
                adverse_mark = (
                    close_mark
                )


            close_upnl = (
                position_upnl(
                    probe_p,
                    close_mark,
                )
            )

            adverse_upnl = (
                position_upnl(
                    probe_p,
                    adverse_mark,
                )
            )


            close_maint = (
                maintenance_margin(
                    snapshot,
                    probe_p[
                        "symbol"
                    ],
                    abs(
                        float(
                            probe_p[
                                "qty"
                            ]
                        )
                        *
                        close_mark
                    ),
                )
            )

            adverse_maint = (
                maintenance_margin(
                    snapshot,
                    probe_p[
                        "symbol"
                    ],
                    abs(
                        float(
                            probe_p[
                                "qty"
                            ]
                        )
                        *
                        adverse_mark
                    ),
                )
            )


            stress_margin_balance = float(
                margin_balance
                -
                close_upnl
                +
                adverse_upnl
            )

            stress_maintenance = float(
                maintenance
                -
                close_maint
                +
                adverse_maint
            )

            stress_cushion_abs = float(
                stress_margin_balance
                -
                stress_maintenance
            )


            if margin_balance > 0.0:

                stress_cushion_ratio = (
                    stress_cushion_abs
                    /
                    margin_balance
                )

            else:

                stress_cushion_ratio = (
                    float("-inf")
                )


            if (
                stress_margin_balance
                <=
                stress_maintenance
            ):
                single_stress_breach_count += 1


            if (
                stress_cushion_abs
                <
                min_single_stress_cushion_abs
            ):

                min_single_stress_cushion_abs = (
                    stress_cushion_abs
                )

                min_single_stress_cushion_ratio = (
                    stress_cushion_ratio
                )

                min_single_stress_margin_balance = (
                    stress_margin_balance
                )


        return {
            "margin_balance":
                margin_balance,

            "maintenance":
                maintenance,

            "maintenance_cushion_abs":
                cushion_abs,

            "maintenance_cushion_ratio":
                cushion_ratio,

            "available":
                available_probe,

            "upnl":
                upnl_probe,

            "total_initial_margin":
                total_initial,

            "initial_margin_ratio":
                initial_margin_ratio,

            "gross_notional":
                gross_notional,

            "long_notional":
                long_notional,

            "short_notional":
                short_notional,

            "account_effective_leverage":
                effective_leverage,

            "maintenance_ratio":
                maintenance_ratio,

            "position_count":
                int(
                    len(
                        positions_value
                    )
                ),

            "min_single_stress_cushion_abs":
                float(
                    min_single_stress_cushion_abs
                ),

            "min_single_stress_cushion_ratio":
                float(
                    min_single_stress_cushion_ratio
                ),

            "min_single_stress_margin_balance":
                float(
                    min_single_stress_margin_balance
                ),

            "single_stress_breach_count":
                int(
                    single_stress_breach_count
                ),
        }

    # LAB_RISK_PROBE_V1_METRICS_END


    entry_skips = 0





    liquidations = 0


    close_margin_liquidations = 0


    intrabar_liquidations = 0

    # Diagnostic only:
    # liquidation result by original owner TF.
    # This must never affect liquidation decisions.
    liquidations_by_owner_tf = {
        str(tf).upper(): {
            "total": 0,
            "close_margin": 0,
            "intrabar_single": 0,
        }
        for tf in frames
    }
    liquidations_by_owner_tf["UNKNOWN"] = {
        "total": 0,
        "close_margin": 0,
        "intrabar_single": 0,
    }





    traded_symbols = set()


    liquidated_symbols = set()





    owner_entries = {


        tf: 0


        for tf in frames


    }





    actual_leverages = []





    bankrupt = 0


    bankrupt_ts = None





    last_processed_ts = int(


        timeline[0]


    )





    # --------------------------------------------------------


    # Entry helper


    # --------------------------------------------------------





    def enter_position(


        wallet,


        symbol,


        side,


        tf,


        ts,


        next_pid,


    ):


        # R6A_PRECISE_RESOURCE_REJECTION_V1 RESET
        if _r6a_comp_enabled:
            globals()["_r6a_last_entry_reject_reason"] = None

        st = account_state(


            wallet,


            positions,


            market,


            snapshot,


            ts,


            stress=False,


        )





        available = float(


            st[


                "available"


            ]


        )





        if available <= 0:


            return (


                wallet,


                next_pid,


                False,


                0,


            )





        requested = int(


            leverage_map[


                tf


            ]


        )





        if requested <= 0:


            return (


                wallet,


                next_pid,


                False,


                0,


            )





        brackets = flatten_brackets(


            snapshot,


            symbol,


        )





        allocation = (


            available


            / float(


                active_count


            )


        )
        allocation = (allocation * float(sizing_multiplier))





        actual = int(


            resolve_actual_leverage(


                brackets,


                allocation,


                requested,


            )


        )





        if actual <= 0:


            return (


                wallet,


                next_pid,


                False,


                0,


            )





        price = get_mark(


            market,


            symbol,


            ts,


            stress=False,


        )





        if (


            not np.isfinite(


                price


            )


            or


            price <= 0


        ):


            return (


                wallet,


                next_pid,


                False,


                actual,


            )





        price = float(


            price


        )





        notional = (


            allocation


            * actual


        )





        filters = (


            snapshot


            .get(


                "order_filters",


                {},


            )


            .get(


                symbol,


                {},


            )


        )





        min_notional = safe_float(


            filters.get(


                "min_notional"


            ),


            0.0,


        )





        if (


            min_notional > 0


            and


            notional < min_notional


        ):





            notional = (


                min_notional


                * 1.20


            )





            allocation = (


                notional


                / actual


            )





        if (


            allocation <= 0


            or


            allocation > available


        ):


            # R6A_PRECISE_RESOURCE_REJECTION_V1
            if _r6a_comp_enabled:
                globals()["_r6a_last_entry_reject_reason"] = 'AVAILABLE_RESOURCE_GATE'
            return (


                wallet,


                next_pid,


                False,


                actual,


            )





        raw_qty = (


            notional


            / price


        )





        qty = float(


            adjust_qty(


                snapshot,


                symbol,


                raw_qty,


                price,


            )


        )





        if qty <= 0:


            return (


                wallet,


                next_pid,


                False,


                actual,


            )





        actual_notional = (


            qty


            * price


        )





        initial_margin = (


            actual_notional


            / actual


        )





        entry_fee = (


            actual_notional


            * FEE_RATE


        )





        if (


            initial_margin


            > available


        ):


            # R6A_PRECISE_RESOURCE_REJECTION_V1
            if _r6a_comp_enabled:
                globals()["_r6a_last_entry_reject_reason"] = 'INITIAL_MARGIN_AVAILABLE'
            return (


                wallet,


                next_pid,


                False,


                actual,


            )

        # R6_PORTFOLIO_EXPOSURE_V3 BEGIN
        if portfolio_initial_margin_cap_ratio is not None:
            r6_cap = float(
                portfolio_initial_margin_cap_ratio
            )

            if not (0.0 < r6_cap <= 1.0):
                raise ValueError(
                    "portfolio_initial_margin_cap_ratio must be in (0, 1]"
                )

            r6_margin_balance = float(
                st["margin_balance"]
            )

            if r6_margin_balance <= 0.0:
                # R6A_PRECISE_RESOURCE_REJECTION_V1
                if _r6a_comp_enabled:
                    globals()["_r6a_last_entry_reject_reason"] = 'MARGIN_BALANCE_NONPOSITIVE'
                return (
                    wallet,
                    next_pid,
                    False,
                    actual,
                )

            r6_existing_initial_margin = float(
                sum(
                    float(
                        r6_p.get(
                            "initial_margin",
                            0.0,
                        )
                    )
                    for r6_p in positions.values()
                )
            )

            r6_projected_initial_margin = (
                r6_existing_initial_margin
                + float(initial_margin)
            )

            r6_projected_initial_margin_ratio = (
                r6_projected_initial_margin
                / r6_margin_balance
            )

            if (
                r6_projected_initial_margin_ratio
                > r6_cap
            ):
                # R6A_PRECISE_RESOURCE_REJECTION_V1
                if _r6a_comp_enabled:
                    globals()["_r6a_last_entry_reject_reason"] = 'INITIAL_MARGIN_RESOURCE_GATE'
                return (
                    wallet,
                    next_pid,
                    False,
                    actual,
                )
        # R6_PORTFOLIO_EXPOSURE_V3 END






        if (


            entry_fee


            >= st[


                "margin_balance"


            ]


        ):


            # R6A_PRECISE_RESOURCE_REJECTION_V1
            if _r6a_comp_enabled:
                globals()["_r6a_last_entry_reject_reason"] = 'ENTRY_FEE_MARGIN_BALANCE'
            return (


                wallet,


                next_pid,


                False,


                actual,


            )







        # ====================================================
        # LAB_CROSS_SURVIVAL_GUARD_V1_ENTRY_GATE
        # ====================================================

        if lab_cross_guard_enabled:

            risk_guard_stats[
                "entry_checks"
            ] += 1


            guard_pid = -1

            while guard_pid in positions:

                guard_pid -= 1


            guard_positions = dict(
                positions
            )


            guard_positions[
                guard_pid
            ] = {
                "symbol":
                    str(
                        symbol
                    ).upper(),

                "side":
                    int(
                        side
                    ),

                "entry_price":
                    float(
                        price
                    ),

                "exit_price":
                    float(
                        price
                    ),

                "qty":
                    float(
                        qty
                    ),

                "initial_margin":
                    float(
                        initial_margin
                    ),

                "requested_leverage":
                    int(
                        requested
                    ),

                "actual_leverage":
                    int(
                        actual
                    ),

                "owner_tf":
                    str(
                        tf
                    ).upper(),

                "entry_ts":
                    int(
                        ts
                    ),

                "entry_fee":
                    float(
                        entry_fee
                    ),
            }


            guard_wallet = float(
                wallet
                -
                entry_fee
            )


            # LAB_ENTRY_BLOCK_PRE_DIAG_V1
            # Diagnostic only: evaluate account BEFORE candidate.
            pre_guard_eval = (
                _lab_cross_guard_eval(
                    float(wallet),
                    positions,
                    market,
                    snapshot,
                    int(ts),
                )
            )

            guard_eval = (
                _lab_cross_guard_eval(
                    guard_wallet,
                    guard_positions,
                    market,
                    snapshot,
                    int(
                        ts
                    ),
                )
            )


            guard_block = (
                not bool(
                    guard_eval[
                        "history_ready"
                    ]
                )
                or
                not bool(
                    guard_eval[
                        "safe"
                    ]
                )
            )


            if guard_block:

                risk_guard_stats[
                    "entry_blocks"
                ] += 1


                tf_key = str(
                    tf
                ).upper()


                risk_guard_stats[
                    "entry_blocks_by_tf"
                ][
                    tf_key
                ] = (
                    int(
                        risk_guard_stats[
                            "entry_blocks_by_tf"
                        ].get(
                            tf_key,
                            0,
                        )
                    )
                    +
                    1
                )


                if not bool(
                    guard_eval[
                        "history_ready"
                    ]
                ):

                    risk_guard_stats[
                        "entry_no_history_blocks"
                    ] += 1

                    block_reason = (
                        "NO_HISTORY"
                    )

                else:

                    block_reason = (
                        "STRESS_BREACH"
                    )


                risk_guard_events.append(
                    {
                        "event":
                            "ENTRY_BLOCK",

                        "reason":
                            block_reason,

                        "ts":
                            int(
                                ts
                            ),

                        "symbol":
                            str(
                                symbol
                            ).upper(),

                        "owner_tf":
                            tf_key,

                        "side":
                            int(
                                side
                            ),

                        "requested_leverage":
                            int(
                                requested
                            ),

                        "actual_leverage":
                            int(
                                actual
                            ),

                        "candidate_notional":
                            float(
                                actual_notional
                            ),

                        "baseline_cushion":
                            float(
                                guard_eval[
                                    "baseline_cushion"
                                ]
                            ),

                        "min_stress_cushion":
                            float(
                                guard_eval[
                                    "min_cushion"
                                ]
                            ),

                        "worst_scenario_ts":
                            guard_eval[
                                "worst_scenario_ts"
                            ],

                        "scenario_count":
                            int(
                                guard_eval[
                                    "scenario_count"
                                ]
                            ),
                        "pre_history_ready": bool(pre_guard_eval["history_ready"]),
                        "pre_safe": bool(pre_guard_eval["safe"]),
                        "pre_baseline_cushion": float(pre_guard_eval["baseline_cushion"]),
                        "pre_min_stress_cushion": float(pre_guard_eval["min_cushion"]),
                        "pre_worst_scenario_ts": pre_guard_eval["worst_scenario_ts"],
                        "pre_scenario_count": int(pre_guard_eval["scenario_count"]),
                        "candidate_baseline_cushion_delta": float(guard_eval["baseline_cushion"] - pre_guard_eval["baseline_cushion"]),
                        "candidate_min_stress_cushion_delta": float(guard_eval["min_cushion"] - pre_guard_eval["min_cushion"]),
                    }
                )


                return (
                    wallet,
                    next_pid,
                    False,
                    actual,
                )

        # LAB_CROSS_SURVIVAL_GUARD_V1_ENTRY_GATE_END

        # ====================================================
        # LAB_RISK_PROBE_V1_ENTRY
        #
        # Diagnostic only.
        # Create a COPY with the proposed position.
        # Real wallet / positions remain untouched.
        # ====================================================

        if collect_risk_probe:

            pre_probe = (
                _lab_risk_probe_metrics(
                    wallet,
                    positions,
                    ts,
                )
            )


            probe_pid = -1

            while (
                probe_pid
                in positions
            ):
                probe_pid -= 1


            probe_positions = dict(
                positions
            )

            probe_positions[
                probe_pid
            ] = {
                "symbol":
                    symbol,

                "side":
                    int(
                        side
                    ),

                "entry_price":
                    float(
                        price
                    ),

                "qty":
                    float(
                        qty
                    ),

                "initial_margin":
                    float(
                        initial_margin
                    ),

                "requested_leverage":
                    int(
                        requested
                    ),

                "actual_leverage":
                    int(
                        actual
                    ),

                "owner_tf":
                    str(
                        tf
                    ).upper(),

                "entry_ts":
                    int(
                        ts
                    ),

                "entry_fee":
                    float(
                        entry_fee
                    ),
            }


            post_wallet_probe = float(
                wallet
                -
                entry_fee
            )


            post_probe = (
                _lab_risk_probe_metrics(
                    post_wallet_probe,
                    probe_positions,
                    ts,
                )
            )


            probe_row = {
                "ts":
                    int(
                        ts
                    ),

                "symbol":
                    str(
                        symbol
                    ).upper(),

                "owner_tf":
                    str(
                        tf
                    ).upper(),

                "side":
                    int(
                        side
                    ),

                "requested_leverage":
                    int(
                        requested
                    ),

                "actual_leverage":
                    int(
                        actual
                    ),

                "entry_price":
                    float(
                        price
                    ),

                "qty":
                    float(
                        qty
                    ),

                "candidate_notional":
                    float(
                        actual_notional
                    ),

                "candidate_initial_margin":
                    float(
                        initial_margin
                    ),

                "candidate_entry_fee":
                    float(
                        entry_fee
                    ),
            }


            for probe_key, probe_value in (
                pre_probe.items()
            ):

                probe_row[
                    "pre_"
                    +
                    str(
                        probe_key
                    )
                ] = probe_value


            for probe_key, probe_value in (
                post_probe.items()
            ):

                probe_row[
                    "post_"
                    +
                    str(
                        probe_key
                    )
                ] = probe_value


            risk_probe_rows.append(
                probe_row
            )

        # LAB_RISK_PROBE_V1_ENTRY_END


        wallet -= entry_fee





        pid = int(


            next_pid


        )





        positions[


            pid


        ] = {


            "symbol": symbol,


            "side": int(


                side


            ),


            "entry_price":


                price,


            "exit_price":


                price,


            "qty":


                qty,


            "initial_margin":


                float(


                    initial_margin


                ),


            "requested_leverage":


                int(


                    requested


                ),


            "actual_leverage":


                int(


                    actual


                ),


            "owner_tf":


                tf,


            "entry_ts": int(ts),
            "entry_fee": float(entry_fee),
        }





        symbol_to_pid[


            symbol


        ] = pid





        return (


            wallet,


            pid + 1,


            True,


            actual,


        )





    # --------------------------------------------------------


    # Timeline


    # --------------------------------------------------------





    for ts in timeline:





        ts = int(


            ts


        )





        last_processed_ts = ts

        # ====================================================
        # LAB_CROSS_SURVIVAL_GUARD_V1_CONTROL_SL
        #
        # Decision/execution reference:
        # previous confirmed 1H close.
        #
        # This avoids using the current hour's high/low before
        # the existing liquidation simulation.
        # ====================================================

        if (
            lab_control_sl_enabled
            and
            positions
        ):

            control_ts = (
                int(
                    ts
                )
                -
                3600
            )


            if control_ts > 0:

                risk_guard_stats[
                    "control_sl_checks"
                ] += 1


                control_eval = (
                    _lab_cross_guard_eval(
                        wallet,
                        positions,
                        market,
                        snapshot,
                        control_ts,
                    )
                )


                if (
                    bool(
                        control_eval[
                            "history_ready"
                        ]
                    )
                    and
                    not bool(
                        control_eval[
                            "safe"
                        ]
                    )
                ):

                    risk_guard_stats[
                        "control_sl_breaches"
                    ] += 1


                    # ----------------------------------------
                    # Keep the original stress scenario set
                    # fixed during this one Control-SL event.
                    #
                    # Otherwise removing one position could
                    # also remove the scenario that exposed
                    # the original account weakness.
                    # ----------------------------------------

                    fixed_scenarios = list(
                        control_eval[
                            "scenario_indices"
                        ]
                    )


                    while (
                        positions
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
                            positions.items()
                        ):

                            if (
                                int(
                                    candidate_p.get(
                                        "entry_ts",
                                        control_ts,
                                    )
                                )
                                >
                                control_ts
                            ):

                                continue


                            candidate_symbol = str(
                                candidate_p[
                                    "symbol"
                                ]
                            ).upper()


                            candidate_mark = get_mark(
                                market,
                                candidate_symbol,
                                control_ts,
                                stress=False,
                            )


                            if (
                                not np.isfinite(
                                    candidate_mark
                                )
                                or
                                candidate_mark <= 0
                            ):

                                continue


                            candidate_mark = float(
                                candidate_mark
                            )


                            candidate_pnl = position_upnl(
                                candidate_p,
                                candidate_mark,
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
                                FEE_RATE
                            )


                            trial_wallet = (
                                float(
                                    wallet
                                )
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
                                positions
                            )


                            trial_positions.pop(
                                candidate_pid,
                                None,
                            )


                            trial_eval = (
                                _lab_cross_guard_eval(
                                    trial_wallet,
                                    trial_positions,
                                    market,
                                    snapshot,
                                    control_ts,
                                    scenario_indices=
                                        fixed_scenarios,
                                )
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
                                        candidate_symbol,

                                    "mark":
                                        candidate_mark,

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


                        if best is None:

                            break


                        close_pid = best[
                            "pid"
                        ]


                        close_p = positions.pop(
                            close_pid
                        )


                        close_symbol = str(
                            close_p[
                                "symbol"
                            ]
                        ).upper()


                        close_price = float(
                            best[
                                "mark"
                            ]
                        )


                        close_pnl = float(
                            best[
                                "pnl"
                            ]
                        )


                        close_exit_fee = float(
                            best[
                                "exit_fee"
                            ]
                        )


                        wallet += (
                            close_pnl
                            -
                            close_exit_fee
                        )


                        if (
                            symbol_to_pid.get(
                                close_symbol
                            )
                            ==
                            close_pid
                        ):

                            symbol_to_pid.pop(
                                close_symbol,
                                None,
                            )


                        owner_tf_key = str(
                            close_p.get(
                                "owner_tf",
                                "UNKNOWN",
                            )
                        ).upper()


                        risk_guard_stats[
                            "control_sl_closes"
                        ] += 1


                        risk_guard_stats[
                            "control_sl_by_tf"
                        ][
                            owner_tf_key
                        ] = (
                            int(
                                risk_guard_stats[
                                    "control_sl_by_tf"
                                ].get(
                                    owner_tf_key,
                                    0,
                                )
                            )
                            +
                            1
                        )


                        risk_guard_events.append(
                            {
                                "event":
                                    "CONTROL_SL",

                                "ts":
                                    int(
                                        control_ts
                                    ),

                                "symbol":
                                    close_symbol,

                                "owner_tf":
                                    owner_tf_key,

                                "side":
                                    int(
                                        close_p[
                                            "side"
                                        ]
                                    ),

                                "exit_price":
                                    float(
                                        close_price
                                    ),

                                "gross_pnl":
                                    float(
                                        close_pnl
                                    ),

                                "exit_fee":
                                    float(
                                        close_exit_fee
                                    ),

                                "before_min_stress_cushion":
                                    float(
                                        control_eval[
                                            "min_cushion"
                                        ]
                                    ),

                                "after_min_stress_cushion":
                                    float(
                                        best[
                                            "trial_eval"
                                        ][
                                            "min_cushion"
                                        ]
                                    ),

                                "worst_scenario_ts":
                                    control_eval[
                                        "worst_scenario_ts"
                                    ],

                                "scenario_count":
                                    int(
                                        control_eval[
                                            "scenario_count"
                                        ]
                                    ),
                            }
                        )


                        # ------------------------------------
                        # Preserve strategy ownership.
                        #
                        # A Control-SL exit may later re-enter
                        # only through the existing STATE
                        # reentry mechanism, and that reentry
                        # must pass Entry Gate again.
                        # ------------------------------------

                        if state_reentry_enabled:

                            state_reentry_wait[
                                close_symbol
                            ] = {
                                "side":
                                    int(
                                        close_p[
                                            "side"
                                        ]
                                    ),

                                "owner_tf":
                                    owner_tf_key,

                                "exit_ts":
                                    int(
                                        control_ts
                                    ),

                                "exit_price":
                                    float(
                                        close_price
                                    ),
                            }


                        if collect_trade_log:

                            trade_log.append(
                                {
                                    "symbol":
                                        close_symbol,

                                    "side":
                                        int(
                                            close_p[
                                                "side"
                                            ]
                                        ),

                                    "owner_tf":
                                        owner_tf_key,

                                    "entry_ts":
                                        int(
                                            close_p[
                                                "entry_ts"
                                            ]
                                        ),

                                    "exit_ts":
                                        int(
                                            control_ts
                                        ),

                                    "entry_price":
                                        float(
                                            close_p[
                                                "entry_price"
                                            ]
                                        ),

                                    "exit_price":
                                        float(
                                            close_price
                                        ),

                                    "qty":
                                        float(
                                            close_p[
                                                "qty"
                                            ]
                                        ),

                                    "initial_margin":
                                        float(
                                            close_p[
                                                "initial_margin"
                                            ]
                                        ),

                                    "requested_leverage":
                                        int(
                                            close_p[
                                                "requested_leverage"
                                            ]
                                        ),

                                    "actual_leverage":
                                        int(
                                            close_p[
                                                "actual_leverage"
                                            ]
                                        ),

                                    "entry_fee":
                                        float(
                                            close_p[
                                                "entry_fee"
                                            ]
                                        ),

                                    "exit_fee":
                                        float(
                                            close_exit_fee
                                        ),

                                    "gross_pnl":
                                        float(
                                            close_pnl
                                        ),

                                    "net_pnl":
                                        float(
                                            close_pnl
                                            -
                                            close_exit_fee
                                            -
                                            float(
                                                close_p[
                                                    "entry_fee"
                                                ]
                                            )
                                        ),

                                    "exit_reason":
                                        "CONTROL_SL",
                                }
                            )


                        control_eval = (
                            _lab_cross_guard_eval(
                                wallet,
                                positions,
                                market,
                                snapshot,
                                control_ts,
                                scenario_indices=
                                    fixed_scenarios,
                            )
                        )

        # LAB_CROSS_SURVIVAL_GUARD_V1_CONTROL_SL_END






        # 0) ?? ??? ?? ??? TF state ??


        for (


            symbol,


            tf,


            side,


        ) in updates.get(


            ts,


            [],


        ):





            frame_state[


                (


                    symbol,


                    tf,


                )


            ] = int(


                side


            )





        # 1) CROSS liquidation


        if positions:





            # Preserve owner metadata before liquidation removes
            # positions. Diagnostic only; no execution effect.
            liquidation_owner_tf = {
                int(pid): str(
                    pos.get("owner_tf", "UNKNOWN")
                ).upper()
                for pid, pos in positions.items()
            }

            liq_diag_rows = []
            pre_liq_wallet = float(wallet)
            pre_liq_positions = dict(positions)
            wallet, liqs = (


                liquidate_cross_until_safe(


                    wallet,


                    positions,


                    liquidation_market,


                    snapshot,


                    ts,


                    diagnostics=liq_diag_rows,
                )


            )
            if liq_diag_rows:
                prior_guard = None
                prior_guard_error = None
                decision_ts = int(ts) - 3600
                if decision_ts > 0:
                    try:
                        prior_guard = _lab_cross_guard_eval(
                            pre_liq_wallet,
                            pre_liq_positions,
                            market,
                            snapshot,
                            decision_ts,
                        )
                    except Exception as exc:
                        prior_guard_error = repr(exc)
                for diag_row in liq_diag_rows:
                    diag_row['prior_guard_decision_ts'] = int(
                        decision_ts
                    )
                    if prior_guard is not None:
                        diag_row['prior_guard_history_ready'] = bool(
                            prior_guard.get('history_ready', False)
                        )
                        diag_row['prior_guard_safe'] = bool(
                            prior_guard.get('safe', False)
                        )
                        diag_row['prior_guard_baseline_cushion'] = float(
                            prior_guard.get('baseline_cushion', float('nan'))
                        )
                        diag_row['prior_guard_min_stress_cushion'] = float(
                            prior_guard.get('min_cushion', float('nan'))
                        )
                        diag_row['prior_guard_worst_scenario_ts'] = prior_guard.get(
                            'worst_scenario_ts'
                        )
                    if prior_guard_error is not None:
                        diag_row['prior_guard_error'] = prior_guard_error
                    risk_guard_events.append(
                        diag_row
                    )





            for (


                pid,


                symbol,


                reason,


            ) in liqs:





                symbol = str(


                    symbol


                ).upper()





                liquidations += 1

                liq_owner_tf = liquidation_owner_tf.get(
                    int(pid),
                    "UNKNOWN",
                )

                if liq_owner_tf not in liquidations_by_owner_tf:
                    liq_owner_tf = "UNKNOWN"

                liquidations_by_owner_tf[
                    liq_owner_tf
                ]["total"] += 1





                liquidated_symbols.add(


                    symbol


                )





                if (


                    symbol_to_pid.get(


                        symbol


                    )


                    == pid


                ):


                    symbol_to_pid.pop(


                        symbol,


                        None,


                    )





                if (


                    reason


                    == "CLOSE_MARGIN"


                ):


                    close_margin_liquidations += 1

                    liquidations_by_owner_tf[
                        liq_owner_tf
                    ]["close_margin"] += 1





                elif (


                    reason


                    == "INTRABAR_SINGLE"


                ):


                    intrabar_liquidations += 1

                    liquidations_by_owner_tf[
                        liq_owner_tf
                    ]["intrabar_single"] += 1





        # liquidation ?? ?? ?? ??


        st = account_state(


            wallet,


            positions,


            market,


            snapshot,


            ts,


            stress=False,


        )





        if (


            st[


                "margin_balance"


            ]


            <= 0.0


        ):





            bankrupt = 1


            bankrupt_ts = ts





            curve.append(


                0.0


            )





            break





        # 2) Runner?? symbol ?? ??



        # ----------------------------------------------------
        # Optional 1H trailing
        # Previous confirmed extreme -> current bar stop check
        # ----------------------------------------------------

        if trailing_enabled and positions:

            for trail_pid in list(positions.keys()):

                if trail_pid not in positions:
                    continue

                trail_p = positions[trail_pid]

                trail_symbol = str(
                    trail_p["symbol"]
                ).upper()

                # Entry occurs at this candle close.
                # Earliest trailing evaluation is next 1H bar.
                if int(ts) <= int(
                    trail_p["entry_ts"]
                ):
                    continue

                trail_row = (
                    market
                    .get(trail_symbol, {})
                    .get(int(ts))
                )

                if trail_row is None:
                    continue

                trail_close = float(
                    trail_row[0]
                )

                trail_low = float(
                    trail_row[1]
                )

                trail_high = float(
                    trail_row[2]
                )

                if not (
                    np.isfinite(trail_close)
                    and np.isfinite(trail_low)
                    and np.isfinite(trail_high)
                    and trail_close > 0
                    and trail_low > 0
                    and trail_high > 0
                ):
                    continue

                trail_side = int(
                    trail_p["side"]
                )

                trail_entry = float(
                    trail_p["entry_price"]
                )

                trail_extreme = float(
                    trail_p.get(
                        "_trail_extreme",
                        trail_entry,
                    )
                )

                start_frac = (
                    trailing_start_pct
                    / 100.0
                )

                callback_frac = (
                    trailing_callback_pct
                    / 100.0
                )

                trail_armed = False
                trail_hit = False
                trail_stop = None

                if trail_side == 1:

                    trail_armed = (
                        trail_extreme
                        >= trail_entry
                        * (1.0 + start_frac)
                    )

                    if trail_armed:

                        trail_stop = (
                            trail_extreme
                            * (1.0 - callback_frac)
                        )

                        trail_hit = (
                            trail_low
                            <= trail_stop
                        )

                elif trail_side == -1:

                    trail_armed = (
                        trail_extreme
                        <= trail_entry
                        * (1.0 - start_frac)
                    )

                    if trail_armed:

                        trail_stop = (
                            trail_extreme
                            * (1.0 + callback_frac)
                        )

                        trail_hit = (
                            trail_high
                            >= trail_stop
                        )

                else:
                    raise RuntimeError(
                        f"bad trailing side: {trail_side}"
                    )

                if trail_hit:

                    trail_stop = float(
                        trail_stop
                    )

                    trail_p = positions.pop(
                        trail_pid
                    )

                    if (
                        symbol_to_pid.get(
                            trail_symbol
                        )
                        == trail_pid
                    ):
                        symbol_to_pid.pop(
                            trail_symbol,
                            None,
                        )

                    trail_pnl = position_upnl(
                        trail_p,
                        trail_stop,
                    )

                    trail_exit_fee = (
                        abs(
                            float(trail_p["qty"])
                            * trail_stop
                        )
                        * FEE_RATE
                    )

                    wallet += (
                        trail_pnl
                        - trail_exit_fee
                    )

                    trailing_closes += 1

                    if state_reentry_enabled:

                        state_reentry_wait[
                            trail_symbol
                        ] = {
                            "side": int(
                                trail_p["side"]
                            ),
                            "owner_tf": str(
                                trail_p["owner_tf"]
                            ).upper(),
                            "exit_ts": int(ts),
                            "exit_price": float(trail_stop),
                        }


                    if collect_trade_log:

                        trade_log.append({
                            "symbol": trail_symbol,
                            "side": int(trail_p["side"]),
                            "owner_tf": str(
                                trail_p["owner_tf"]
                            ).upper(),
                            "entry_ts": int(
                                trail_p["entry_ts"]
                            ),
                            "exit_ts": int(ts),
                            "entry_price": float(
                                trail_p["entry_price"]
                            ),
                            "exit_price": float(
                                trail_stop
                            ),
                            "qty": float(
                                trail_p["qty"]
                            ),
                            "initial_margin": float(
                                trail_p["initial_margin"]
                            ),
                            "requested_leverage": int(
                                trail_p["requested_leverage"]
                            ),
                            "actual_leverage": int(
                                trail_p["actual_leverage"]
                            ),
                            "entry_fee": float(
                                trail_p["entry_fee"]
                            ),
                            "exit_fee": float(
                                trail_exit_fee
                            ),
                            "gross_pnl": float(
                                trail_pnl
                            ),
                            "net_pnl": float(
                                trail_pnl
                                - trail_exit_fee
                                - float(
                                    trail_p["entry_fee"]
                                )
                            ),
                            "exit_reason": "TRAILING",
                            "trail_start_pct": float(
                                trailing_start_pct
                            ),
                            "trail_callback_pct": float(
                                trailing_callback_pct
                            ),
                            "trail_extreme": float(
                                trail_extreme
                            ),
                            "trail_stop": float(
                                trail_stop
                            ),
                        })

                    # Do not reuse stale EVENT states after
                    # a trailing exit.
                    for trail_tf in frames:
                        frame_state[(
                            trail_symbol,
                            trail_tf,
                        )] = 0

                    # Re-apply ONLY fresh confirmations
                    # that actually occurred at this ts.
                    # This permits a genuine new EVENT but
                    # prevents STATE/stale-event re-entry.
                    for (
                        fresh_symbol,
                        fresh_tf,
                        fresh_side,
                    ) in updates.get(ts, []):

                        if (
                            str(fresh_symbol).upper()
                            == trail_symbol
                        ):
                            frame_state[(
                                trail_symbol,
                                fresh_tf,
                            )] = int(
                                fresh_side
                            )

                    continue

                # Survived current candle.
                # Only now may this candle improve the peak.
                if trail_side == 1:
                    trail_p[
                        "_trail_extreme"
                    ] = max(
                        trail_extreme,
                        trail_high,
                    )

                else:
                    trail_p[
                        "_trail_extreme"
                    ] = min(
                        trail_extreme,
                        trail_low,
                    )

        for symbol in symbols:

            # ------------------------------------------------
            # OWNER STATE re-entry
            # Only after a previous TRAILING exit.
            # General FLAT entry remains EVENT-only.
            # ------------------------------------------------

            if state_reentry_enabled:

                wait = state_reentry_wait.get(
                    symbol
                )

                if wait is not None:

                    existing_pid = symbol_to_pid.get(
                        symbol
                    )

                    # A fresh normal EVENT may already have
                    # reopened this symbol.
                    if (
                        existing_pid is not None
                        and existing_pid in positions
                    ):
                        state_reentry_wait.pop(
                            symbol,
                            None,
                        )

                    elif int(ts) > int(
                        wait["exit_ts"]
                    ):

                        wait_side = int(
                            wait["side"]
                        )

                        wait_tf = str(
                            wait["owner_tf"]
                        ).upper()

                        state_key = (
                            symbol,
                            wait_tf,
                        )

                        state_ts_arr, state_sig_arr = (
                            state_reentry_book[
                                state_key
                            ]
                        )

                        state_ts_arr = np.asarray(
                            state_ts_arr,
                            dtype=np.int64,
                        )

                        state_sig_arr = np.asarray(
                            state_sig_arr,
                            dtype=np.int8,
                        )

                        if (
                            len(state_ts_arr)
                            != len(state_sig_arr)
                        ):
                            raise RuntimeError(
                                "STATE book length mismatch: "
                                f'{symbol} {wait_tf}'
                            )

                        state_decision = 0

                        if len(state_ts_arr):

                            state_i = int(
                                np.searchsorted(
                                    state_ts_arr,
                                    int(ts),
                                    side="right",
                                )
                                - 1
                            )

                            if state_i >= 0:
                                state_decision = int(
                                    state_sig_arr[
                                        state_i
                                    ]
                                )

                        if state_decision not in (
                            -1,
                            0,
                            1,
                        ):
                            raise RuntimeError(
                                "bad STATE decision: "
                                f'{state_decision}'
                            )

                        # ------------------------------------------
                        # Optional additional pullback before
                        # OWNER STATE re-entry.
                        #
                        # 0% preserves the exact old behavior.
                        # Use current 1H CLOSE only.
                        # ------------------------------------------
                        pullback_ok = False

                        if state_decision == wait_side:

                            if (
                                state_reentry_pullback_pct
                                <= 0.0
                            ):
                                pullback_ok = True

                            else:
                                wait_exit_price = float(
                                    wait.get(
                                        "exit_price",
                                        np.nan,
                                    )
                                )

                                reentry_row = (
                                    market
                                    .get(symbol, {})
                                    .get(int(ts))
                                )

                                if (
                                    np.isfinite(
                                        wait_exit_price
                                    )
                                    and wait_exit_price > 0
                                    and reentry_row
                                    is not None
                                ):
                                    reentry_price = float(
                                        reentry_row[0]
                                    )

                                    if (
                                        np.isfinite(
                                            reentry_price
                                        )
                                        and reentry_price > 0
                                    ):
                                        pullback_frac = (
                                            state_reentry_pullback_pct
                                            / 100.0
                                        )

                                        if wait_side == 1:
                                            pullback_ok = (
                                                reentry_price
                                                <= wait_exit_price
                                                * (
                                                    1.0
                                                    - pullback_frac
                                                )
                                            )

                                        elif wait_side == -1:
                                            pullback_ok = (
                                                reentry_price
                                                >= wait_exit_price
                                                * (
                                                    1.0
                                                    + pullback_frac
                                                )
                                            )

                                        else:
                                            raise RuntimeError(
                                                "bad STATE "
                                                "reentry side"
                                            )

                                if not pullback_ok:
                                    state_reentry_pullback_waits += 1

                        # Opposite STATE = old trend ended.
                        if state_decision == -wait_side:

                            state_reentry_wait.pop(
                                symbol,
                                None,
                            )

                            state_reentry_cancels += 1

                        # 0 = no full consensus; keep waiting.
                        elif state_decision == wait_side and pullback_ok:

                            (
                                wallet,
                                next_pid,
                                state_ok,
                                state_actual,
                            ) = enter_position(
                                wallet,
                                symbol,
                                wait_side,
                                wait_tf,
                                ts,
                                next_pid,
                            )

                            if state_ok:

                                entries += 1
                                state_reentries += 1

                                owner_entries[
                                    wait_tf
                                ] += 1

                                traded_symbols.add(
                                    symbol
                                )

                                actual_leverages.append(
                                    int(
                                        state_actual
                                    )
                                )

                                state_reentry_wait.pop(
                                    symbol,
                                    None,
                                )

                            else:
                                state_reentry_skips += 1






            pid = symbol_to_pid.get(


                symbol


            )





            # ------------------------------------------------


            # ???:


            # owner frame? ??


            # ------------------------------------------------





            if (


                pid is not None


                and


                pid in positions


            ):





                p = positions[


                    pid


                ]





                old_side = int(


                    p[


                        "side"


                    ]


                )





                owner_tf = str(


                    p[


                        "owner_tf"


                    ]


                )





                decision = int(


                    frame_state.get(


                        (


                            symbol,


                            owner_tf,


                        ),


                        0,


                    )


                )





                # WAIT/no consensus ?? same direction


                if (


                    decision == 0


                    or


                    decision == old_side


                ):


                    continue





                # owner ?? consensus


                if (


                    decision


                    == -old_side


                ):





                    mark = get_mark(


                        market,


                        symbol,


                        ts,


                        stress=False,


                    )





                    if (


                        not np.isfinite(


                            mark


                        )


                        or


                        mark <= 0


                    ):


                        continue





                    mark = float(


                        mark


                    )





                    p = positions.pop(


                        pid


                    )





                    symbol_to_pid.pop(


                        symbol,


                        None,


                    )





                    pnl = position_upnl(


                        p,


                        mark,


                    )





                    exit_fee = (


                        abs(


                            p[


                                "qty"


                            ]


                            * mark


                        )


                        * FEE_RATE


                    )





                    wallet += (


                        pnl


                        - exit_fee


                    )

                    if collect_trade_log:
                        trade_log.append({
                            "symbol": str(p["symbol"]).upper(),
                            "side": int(p["side"]),
                            "owner_tf": str(p["owner_tf"]).upper(),
                            "entry_ts": int(p["entry_ts"]),
                            "exit_ts": int(ts),
                            "entry_price": float(p["entry_price"]),
                            "exit_price": float(mark),
                            "qty": float(p["qty"]),
                            "initial_margin": float(p["initial_margin"]),
                            "requested_leverage": int(p["requested_leverage"]),
                            "actual_leverage": int(p["actual_leverage"]),
                            "entry_fee": float(p["entry_fee"]),
                            "exit_fee": float(exit_fee),
                            "gross_pnl": float(pnl),
                            "net_pnl": float(
                                pnl
                                - exit_fee
                                - float(p["entry_fee"])
                            ),
                            "exit_reason": "OPPOSITE_SIGNAL",
                        })





                    (


                        wallet,


                        next_pid,


                        ok,


                        actual,


                    ) = enter_position(


                        wallet,


                        symbol,


                        decision,


                        owner_tf,


                        ts,


                        next_pid,


                    )





                    if ok:





                        entries += 1


                        reversals += 1





                        owner_entries[


                            owner_tf


                        ] += 1





                        traded_symbols.add(


                            symbol


                        )





                        actual_leverages.append(


                            int(


                                actual


                            )


                        )





                    else:





                        entry_skips += 1





                continue





            # ------------------------------------------------


            # FLAT:


            # ?? enabled frame ??


            # ------------------------------------------------





            candidates = []





            for tf in frames:





                side = int(


                    frame_state.get(


                        (


                            symbol,


                            tf,


                        ),


                        0,


                    )


                )





                if side in (


                    1,


                    -1,


                ):





                    candidates.append(


                        (


                            tf,


                            side,


                        )


                    )





            if not candidates:


                continue





            # one-way owner? ??? ???? ??.


            if len(


                candidates


            ) != 1:





                multi_tf_skips += 1


                continue





            tf, side = (


                candidates[


                    0


                ]


            )





            # R6A_ENTRY_PRIORITY_BASELINE_HOOK_V1 BEGIN
            if entry_priority_mode is not None:
                raise ValueError(
                    "R6A entry_priority_mode non-None modes are not sealed yet"
                )
            # R6A_ENTRY_PRIORITY_BASELINE_HOOK_V1 END

            # R6A_DIRECT_COMPETITION_CAPTURE_V1 BEGIN
            if _r6a_comp_enabled:
                _r6a_comp_tel = globals().get(
                    "_r6a_entry_priority_telemetry_runtime"
                )
                if _r6a_comp_tel is None:
                    raise RuntimeError(
                        "R6A competition telemetry runtime missing"
                    )
                _r6a_comp_ts = int(ts)
                _r6a_comp_bucket = _r6a_comp_tel[
                    "timestamps"
                ].setdefault(
                    _r6a_comp_ts,
                    [],
                )
                _r6a_comp_bucket.append({
                    "symbol": str(symbol),
                    "side": int(side),
                    "tf": str(tf),
                })
                _r6a_comp_tel[
                    "dispatch_attempt_total"
                ] += 1
            # R6A_DIRECT_COMPETITION_CAPTURE_V1 END

            (


                wallet,


                next_pid,


                ok,


                actual,


            ) = enter_position(


                wallet,


                symbol,


                side,


                tf,


                ts,


                next_pid,


            )

            # R6A_DIRECT_COMPETITION_RESULT_V1 BEGIN
            if _r6a_comp_enabled:
                _r6a_comp_tel = globals()[
                    "_r6a_entry_priority_telemetry_runtime"
                ]
                _r6a_comp_rec = _r6a_comp_tel[
                    "timestamps"
                ][int(ts)][-1]
                _r6a_comp_rec["ok"] = bool(ok)
                if bool(ok):
                    _r6a_comp_tel[
                        "dispatch_success_total"
                    ] += 1
                else:
                    _r6a_comp_tel[
                        "dispatch_fail_total"
                    ] += 1
                _r6a_comp_reason = globals().get(
                    "_r6a_last_entry_reject_reason"
                )
                _r6a_comp_rec["reject_reason"] = (
                    None if bool(ok) else _r6a_comp_reason
                )
                _r6a_comp_rec["resource_block"] = bool(
                    (not bool(ok))
                    and
                    (_r6a_comp_reason is not None)
                )
            # R6A_DIRECT_COMPETITION_RESULT_V1 END





            if ok:





                entries += 1





                owner_entries[


                    tf


                ] += 1





                traded_symbols.add(


                    symbol


                )





                actual_leverages.append(


                    int(


                        actual


                    )


                )





            else:





                entry_skips += 1





        # 3) close mark-to-market equity


        st = account_state(


            wallet,


            positions,


            market,


            snapshot,


            ts,


            stress=False,


        )





        equity = float(


            st[


                "margin_balance"


            ]


        )





        curve.append(


            max(


                0.0,


                equity,


            )


        )





        if equity <= 0.0:





            bankrupt = 1


            bankrupt_ts = ts





            break





    # --------------------------------------------------------


    # OOS ?? ????


    # --------------------------------------------------------





    oos_end_closes = 0





    if not bankrupt:





        for pid in list(


            positions.keys()


        ):





            p = positions.pop(


                pid


            )





            symbol = str(


                p[


                    "symbol"


                ]


            ).upper()





            mark = get_mark(


                market,


                symbol,


                last_processed_ts,


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





            pnl = position_upnl(


                p,


                mark,


            )





            exit_fee = (


                abs(


                    p[


                        "qty"


                    ]


                    * mark


                )


                * FEE_RATE


            )





            wallet += (


                pnl


                - exit_fee


            )

            if collect_trade_log:
                trade_log.append({
                    "symbol": str(p["symbol"]).upper(),
                    "side": int(p["side"]),
                    "owner_tf": str(p["owner_tf"]).upper(),
                    "entry_ts": int(p["entry_ts"]),
                    "exit_ts": int(last_processed_ts),
                    "entry_price": float(p["entry_price"]),
                    "exit_price": float(mark),
                    "qty": float(p["qty"]),
                    "initial_margin": float(p["initial_margin"]),
                    "requested_leverage": int(p["requested_leverage"]),
                    "actual_leverage": int(p["actual_leverage"]),
                    "entry_fee": float(p["entry_fee"]),
                    "exit_fee": float(exit_fee),
                    "gross_pnl": float(pnl),
                    "net_pnl": float(
                        pnl
                        - exit_fee
                        - float(p["entry_fee"])
                    ),
                    "exit_reason": "OOS_END",
                })





            symbol_to_pid.pop(


                symbol,


                None,


            )





            oos_end_closes += 1





    final_equity = (


        0.0


        if bankrupt


        else max(


            0.0,


            float(


                wallet


            ),


        )


    )





    curve.append(


        final_equity


    )





    compound_pct = (


        final_equity


        / start_equity


        - 1.0


    ) * 100.0





    mdd_pct = max_drawdown_pct(


        curve


    )





    if collect_trade_log and int(liquidations) != 0:
        raise RuntimeError(
            "trade_log requested but liquidation occurred; fail closed"
        )

    return {

        # LAB_RISK_PROBE_V1_RETURN
        "risk_probe_enabled":
            int(
                collect_risk_probe
            ),


        "risk_guard_stats":
            dict(
                risk_guard_stats
            ),

        "risk_guard_events":
            risk_guard_events,

        "risk_probe_rows":
            risk_probe_rows,

        "trailing_closes": int(trailing_closes),
        "state_reentries": int(state_reentries),
        "state_reentry_cancels": int(state_reentry_cancels),
        "state_reentry_skips": int(state_reentry_skips),
        "state_reentry_pullback_waits": int(
            state_reentry_pullback_waits
        ),
        "state_reentry_pullback_pct": float(
            state_reentry_pullback_pct
        ),

        "trade_log": (trade_log if collect_trade_log else None),



        "start_equity":


            float(


                start_equity


            ),


        "final_equity":


            float(


                final_equity


            ),


        "compound_pct":


            float(


                compound_pct


            ),


        "realized_mdd_pct":


            float(


                mdd_pct


            ),


        "entries":


            int(


                entries


            ),


        "reversals":


            int(


                reversals


            ),


        "multi_tf_skips":


            int(


                multi_tf_skips


            ),


        "skipped_entries":


            int(


                entry_skips


            ),


        "liquidations_by_owner_tf": {
            str(tf): {
                "total": int(v.get("total", 0)),
                "close_margin": int(
                    v.get("close_margin", 0)
                ),
                "intrabar_single": int(
                    v.get("intrabar_single", 0)
                ),
            }
            for tf, v in liquidations_by_owner_tf.items()
        },

        "liquidations":


            int(


                liquidations


            ),


        "close_margin_liquidations":


            int(


                close_margin_liquidations


            ),


        "intrabar_single_liquidations":


            int(


                intrabar_liquidations


            ),


        "liquidated_symbols":


            int(


                len(


                    liquidated_symbols


                )


            ),


        "traded_symbols":


            int(


                len(


                    traded_symbols


                )


            ),


        "oos_end_closes":


            int(


                oos_end_closes


            ),


        "bankrupt":


            int(


                bankrupt


            ),


        "bankrupt_ts":


            (


                int(


                    bankrupt_ts


                )


                if bankrupt_ts


                is not None


                else None


            ),


        "owner_entries":


            {


                tf: int(


                    owner_entries[


                        tf


                    ]


                )


                for tf in frames


            },


        "avg_actual_leverage":


            (


                float(


                    np.mean(


                        actual_leverages


                    )


                )


                if actual_leverages


                else np.nan


            ),


        "max_actual_leverage":


            (


                int(


                    max(


                        actual_leverages


                    )


                )


                if actual_leverages


                else 0


            ),


    }





# ============================================================
# ANCHORED EXPANDING WALK-FORWARD / OOS
#
# APPROVED SCHEDULE
# -----------------
# TRAIN minimum : 24 months
# OOS           : 6 months
# STEP          : 6 months
# TRAIN         : anchored expanding
# final partial OOS excluded
#
# CURRENT_35_FROM_2020
#   anchor = 2020-01-01 00:00 UTC
#
# LONG_4_5Y
#   anchor = actual LONG4 common start
#
# This layer does NOT invent leverage.
# Portfolio execution requires leverage_map for every frame.
# ============================================================


def build_anchored_expanding_wf_folds(
    cache,
    dataset_name,
    train_months=24,
    oos_months=6,
    step_months=6,
):
    runner_v4 = load_runner_v4()

    datasets = (
        runner_v4.make_dataset_windows(
            cache
        )
    )

    dataset_name = str(
        dataset_name
    ).upper()

    if dataset_name not in datasets:
        raise ValueError(
            f"unknown dataset: {dataset_name}"
        )

    if int(train_months) <= 0:
        raise ValueError(
            "train_months <= 0"
        )

    if int(oos_months) <= 0:
        raise ValueError(
            "oos_months <= 0"
        )

    if int(step_months) <= 0:
        raise ValueError(
            "step_months <= 0"
        )

    dataset = datasets[
        dataset_name
    ]

    windows = dataset[
        "windows"
    ]

    if not windows:
        raise RuntimeError(
            f"dataset windows empty: {dataset_name}"
        )

    starts = [
        int(
            w["start"]
        )
        for w in windows.values()
    ]

    ends = [
        int(
            w["end"]
        )
        for w in windows.values()
    ]

    # --------------------------------------------------------
    # Dataset anchor/end
    # --------------------------------------------------------

    if (
        dataset_name
        == "CURRENT_35_FROM_2020"
    ):
        # Approved research calendar anchor.
        dataset_start = pd.Timestamp(
            "2020-01-01 00:00:00",
            tz="UTC",
        )

        # Current35 is per-symbol history.
        # Later listings enter when their own data begins.
        dataset_end = pd.to_datetime(
            max(ends),
            unit="s",
            utc=True,
        )

    elif (
        dataset_name
        == "LONG_4_5Y"
    ):
        # LONG4 windows were already common-trimmed
        # by make_dataset_windows().
        dataset_start = pd.to_datetime(
            max(starts),
            unit="s",
            utc=True,
        )

        dataset_end = pd.to_datetime(
            min(ends),
            unit="s",
            utc=True,
        )

    else:
        raise ValueError(
            f"unsupported WF dataset: {dataset_name}"
        )

    first_oos_start = (
        dataset_start
        + pd.DateOffset(
            months=int(
                train_months
            )
        )
    )

    rows = []

    fold_no = 1
    oos_start = first_oos_start

    while True:

        oos_end = (
            oos_start
            + pd.DateOffset(
                months=int(
                    oos_months
                )
            )
        )

        # Incomplete final OOS is excluded.
        if oos_end > dataset_end:
            break

        train_start = (
            dataset_start
        )

        train_end = (
            oos_start
        )

        rows.append({
            "fold":
                int(
                    fold_no
                ),

            "dataset":
                dataset_name,

            "train_start_ts":
                int(
                    train_start.timestamp()
                ),

            "train_end_ts":
                int(
                    train_end.timestamp()
                ),

            "oos_start_ts":
                int(
                    oos_start.timestamp()
                ),

            "oos_end_ts":
                int(
                    oos_end.timestamp()
                ),

            "train_start":
                str(
                    train_start
                ),

            "train_end":
                str(
                    train_end
                ),

            "oos_start":
                str(
                    oos_start
                ),

            "oos_end":
                str(
                    oos_end
                ),
        })

        fold_no += 1

        oos_start = (
            oos_start
            + pd.DateOffset(
                months=int(
                    step_months
                )
            )
        )

    out = pd.DataFrame(
        rows
    )

    if out.empty:
        raise RuntimeError(
            f"no complete WF folds: "
            f"{dataset_name}"
        )

    return out


def run_research_multiframe_wf_oos(
    cache,
    signal_book,
    symbols,
    snapshot,
    market,
    dataset_name,
    leverage_map,
    frames=(
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
        "1M",
    ),
    active_count=35,
    start_equity=10000.0,
):
    frames = tuple(
        str(x).upper()
        for x in frames
    )

    leverage_map = {
        str(k).upper():
            int(v)
        for k, v in dict(
            leverage_map
        ).items()
    }

    # --------------------------------------------------------
    # Fail closed:
    # ???? ?? leverage? ?? ???? ???.
    # --------------------------------------------------------

    missing = [
        tf
        for tf in frames
        if tf not in leverage_map
    ]

    if missing:
        raise RuntimeError(
            "MISSING_RESEARCH_LEVERAGE_APPROVAL: "
            + ",".join(
                missing
            )
        )

    invalid = [
        tf
        for tf in frames
        if int(
            leverage_map[tf]
        ) <= 0
    ]

    if invalid:
        raise RuntimeError(
            "INVALID_RESEARCH_LEVERAGE: "
            + ",".join(
                invalid
            )
        )

    folds = (
        build_anchored_expanding_wf_folds(
            cache=cache,
            dataset_name=
                dataset_name,
            train_months=24,
            oos_months=6,
            step_months=6,
        )
    )

    rows = []

    for fold in folds.itertuples(
        index=False
    ):

        result = (
            simulate_live_multiframe(
                signal_book=
                    signal_book,
                symbols=
                    symbols,
                snapshot=
                    snapshot,
                market=
                    market,
                start_ts=
                    int(
                        fold.oos_start_ts
                    ),
                end_ts=
                    int(
                        fold.oos_end_ts
                    ),
                leverage_map=
                    leverage_map,
                frames=
                    frames,
                active_count=
                    int(
                        active_count
                    ),
                start_equity=
                    float(
                        start_equity
                    ),
            )
        )

        owner = result[
            "owner_entries"
        ]

        row = {
            "fold":
                int(
                    fold.fold
                ),

            "dataset":
                str(
                    fold.dataset
                ),

            "train_start":
                str(
                    fold.train_start
                ),

            "train_end":
                str(
                    fold.train_end
                ),

            "oos_start":
                str(
                    fold.oos_start
                ),

            "oos_end":
                str(
                    fold.oos_end
                ),

            "final_equity":
                float(
                    result[
                        "final_equity"
                    ]
                ),

            "compound_pct":
                float(
                    result[
                        "compound_pct"
                    ]
                ),

            "mdd_pct":
                float(
                    result[
                        "realized_mdd_pct"
                    ]
                ),

            "entries":
                int(
                    result[
                        "entries"
                    ]
                ),

            "reversals":
                int(
                    result[
                        "reversals"
                    ]
                ),

            "liquidations":
                int(
                    result[
                        "liquidations"
                    ]
                ),

            "bankrupt":
                int(
                    result[
                        "bankrupt"
                    ]
                ),

            "bankrupt_ts":
                result[
                    "bankrupt_ts"
                ],

            "multi_tf_skips":
                int(
                    result[
                        "multi_tf_skips"
                    ]
                ),
        }

        for tf in frames:

            row[
                f"owner_{tf}"
            ] = int(
                owner.get(
                    tf,
                    0,
                )
            )

        rows.append(
            row
        )

    return pd.DataFrame(
        rows
    )

def run_tf(tf, cache, signal_fn, snapshot, active_count, start_equity, market):



    trades, trade_path = build_trade_paths(tf, cache, signal_fn)



    df = attach_walk_forward_quality(trades)







    quality_path = RESULT_DIR / f"leverage_v6_quality_35_{tf}.csv"



    df.to_csv(quality_path, index=False)







    rows = []







    for lev in LEVERAGES:



        metrics = simulate_portfolio(



            df, snapshot, active_count, start_equity,



            leverage_selector=lambda row, lev=lev: lev,



            market=market,



        )



        rows.append({



            "timeframe": tf,



            "policy": f"FIXED_{lev}X",



            "policy_type": "FIXED",



            "requested_leverage": lev,



            **metrics,



        })







    for name, shift in dynamic_policies():



        metrics = simulate_portfolio(



            df, snapshot, active_count, start_equity,



            leverage_selector=lambda row, shift=shift: leverage_from_quality(



                float(row.quality_score), shift



            ),



            market=market,



        )



        rows.append({



            "timeframe": tf,



            "policy": name,



            "policy_type": "DYNAMIC",



            "requested_leverage": np.nan,



            "dynamic_shift": shift,



            **metrics,



        })







    out = pd.DataFrame(rows)



    out = out.sort_values(



        ["aggressive_score", "final_equity"],



        ascending=[False, False],



    ).reset_index(drop=True)







    scan_path = RESULT_DIR / f"leverage_v6_35_cross_{tf}.csv"



    out.to_csv(scan_path, index=False)







    valid = out[



        (pd.to_numeric(out["bankrupt"], errors="coerce").fillna(1) == 0)



        & (pd.to_numeric(out["final_equity"], errors="coerce").fillna(0) > 0)



    ].copy()







    if valid.empty:



        best_profit = None



        best_aggressive = None



    else:



        best_profit = valid.sort_values(



            ["final_equity", "aggressive_score"],



            ascending=[False, False],



        ).iloc[0].to_dict()



        best_aggressive = valid.sort_values(



            ["aggressive_score", "final_equity"],



            ascending=[False, False],



        ).iloc[0].to_dict()







    fixed10_rows = out[out["policy"] == "FIXED_10X"]



    fixed10 = fixed10_rows.iloc[0].to_dict() if len(fixed10_rows) else None







    return {



        "timeframe": tf,



        "trades": int(len(df)),



        "trade_path": str(trade_path),



        "scan_path": str(scan_path),



        "quality_path": str(quality_path),



        "fixed_10x": fixed10,



        "best_profit": best_profit,



        "best_aggressive": best_aggressive,



    }











# ============================================================



# MAIN



# ============================================================







def main():



    parser = argparse.ArgumentParser()



    parser.add_argument("--start-equity", type=float, default=DEFAULT_START_EQUITY)



    args = parser.parse_args()







    if args.start_equity <= 0:



        raise RuntimeError("--start-equity must be > 0")







    RESULT_DIR.mkdir(parents=True, exist_ok=True)







    snapshot, active_symbols, active_count = load_snapshot(SNAPSHOT_FILE)



    cache = load_current_cache(active_symbols)



    signal_fn = load_strategy()



    print("loading 35-symbol hourly risk bars ...")



    market = load_market_bars(cache)



    print("hourly risk bars = ready")







    print("=" * 100)



    print(" STRATEGY LAB V6.4 - CROSS-EQUITY 35 SYMBOL LEVERAGE OPTIMIZER")



    print("=" * 100)



    print(f"frames       = {','.join(TIMEFRAMES)}")



    print("1D           = PENDING / NOT INCLUDED")



    print(f"active_count = {active_count} (35 total; members rotate)")



    print(f"cache        = {CACHE_FILE}")



    print(f"symbols      = {cache['symbol'].nunique()}")



    print("leverage     = 10x ~ 50x")



    print("allocation   = availableBalance / active_count")



    print("strategy     = CURRENT_PRODUCTION; cached material signals reused")



    print("liquidation  = CROSS total equity + maintenance; wallet-only bankruptcy disabled")



    print("qty          = server step/minQty/minNotional rules")



    print("cross fix    = negative wallet != bankruptcy; total equity decides")



    print("universe     = current 35-symbol pool used as historical stress set")



    print("server auto-apply = OFF")



    print("=" * 100)







    report = {



        "schema": "STRATEGY_LAB_LEVERAGE_V6_4_CROSS_EQUITY_CURRENT35",



        "active_count": active_count,



        "active_symbols": active_symbols,



        "frames": {},



    }







    for tf in TIMEFRAMES:



        result = run_tf(



            tf, cache, signal_fn, snapshot, active_count, float(args.start_equity), market



        )



        report["frames"][tf] = result







        bp = result["best_profit"]



        ba = result["best_aggressive"]



        f10 = result["fixed_10x"]







        print()



        print(f"[{tf}] trades={result['trades']} symbols={cache['symbol'].nunique()}")







        if f10 is not None:



            print(



                " FIXED 10X      :",



                f"compound={f10['compound_pct']:+.2f}%",



                f"MDD={f10['realized_mdd_pct']:.2f}%",



                f"liq={int(f10['liquidations'])}/{int(f10['entries'])} "



                f"({f10['liquidation_trade_rate_pct']:.2f}%)",



                f"close/intra={int(f10['close_margin_liquidations'])}/"



                f"{int(f10['intrabar_single_liquidations'])}",



            )







        if bp is None or ba is None:



            print(" NO VALID 10~50X CANDIDATE: all policies bankrupt/invalid")



            continue







        print(



            " BEST PROFIT     :",



            bp["policy"],



            f"compound={bp['compound_pct']:+.2f}%",



            f"MDD={bp['realized_mdd_pct']:.2f}%",



            f"liq={int(bp['liquidations'])}/{int(bp['entries'])} "



            f"({bp['liquidation_trade_rate_pct']:.2f}%)",



            f"liq_symbols={int(bp['liquidated_symbols'])}/{int(bp['traded_symbols'])}",



        )



        print(



            " BEST AGGRESSIVE :",



            ba["policy"],



            f"compound={ba['compound_pct']:+.2f}%",



            f"MDD={ba['realized_mdd_pct']:.2f}%",



            f"liq={int(ba['liquidations'])}/{int(ba['entries'])} "



            f"({ba['liquidation_trade_rate_pct']:.2f}%)",



            f"liq_symbols={int(ba['liquidated_symbols'])}/{int(ba['traded_symbols'])}",



        )







    candidate_path = RESULT_DIR / "leverage_v6_35_cross_candidates.json"



    candidate_path.write_text(



        json.dumps(report, ensure_ascii=False, indent=2, default=str),



        encoding="utf-8",



    )







    print()



    print("=" * 100)



    print(" V6.4 COMPLETE")



    print("=" * 100)



    print("candidate =", candidate_path)



    print("server auto-apply = OFF")



    print("1D auto-promote   = OFF")











if __name__ == "__main__":



    main()
