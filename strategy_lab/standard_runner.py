# 파일명: standard_runner.py
# STRATEGY LAB OFFICIAL STANDARD RUNNER
# Shared engine: leverage_scan.py
# Experiments must not modify this file.
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import strategy_lab.leverage_scan as m

ROOT = Path(r"C:\my\bottest")

# ============================================================
# EXACT TEMPLATE
# ============================================================

def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tmpl = load_mod(
    "exact_template",
    ROOT / "strategy_lab" / "strategies" / "_TEMPLATE.py",
)

ALL_FRAMES = (
    "6H",
    "12H",
    "1D",
    "1W",
)

AUX_BY_TF = {
    "6H": "OBV",
    "12H": "SAR",
    "1D": "SAR",
    "1W": "OBV",
}

FRAMES = (
    "12H",
    "1D",
)

LEVERAGE = {
    "12H": 10,
    "1D": 15,
}


# ============================================================
# EXACT FINAL B SIGNALS
# ============================================================

def agree(a, b):
    return tmpl.agree(a, b)


def raw_ema_state(df, tf):
    prefix = str(tf).lower()

    long_col = f"{prefix}_ema_long"
    short_col = f"{prefix}_ema_short"

    if long_col not in df.columns or short_col not in df.columns:
        raise RuntimeError(
            f"EMA columns missing: {tf}"
        )

    long_v = pd.to_numeric(
        df[long_col],
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=float)

    short_v = pd.to_numeric(
        df[short_col],
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=float)

    state = np.zeros(
        len(df),
        dtype=np.int8,
    )

    state[
        (long_v > short_v)
        &
        (long_v > 0.0)
    ] = 1

    state[
        (short_v > long_v)
        &
        (short_v > 0.0)
    ] = -1

    valid_col = f"{prefix}_ema_valid"

    if valid_col in df.columns:
        valid = pd.to_numeric(
            df[valid_col],
            errors="coerce",
        ).fillna(0).to_numpy(dtype=float)

        state[valid <= 0] = 0

    return state


def final_b_event(df, tf):
    tf = str(tf).upper()

    ema = tmpl.material_signal(
        df,
        tf,
        "EMA_CROSS",
    )

    aux = tmpl.material_signal(
        df,
        tf,
        AUX_BY_TF[tf],
    )

    return agree(
        ema,
        aux,
    )


def final_b_state(df, tf):
    tf = str(tf).upper()

    ema = raw_ema_state(
        df,
        tf,
    )

    aux = tmpl.material_signal(
        df,
        tf,
        AUX_BY_TF[tf],
    )

    return agree(
        ema,
        aux,
    )


# ============================================================
# DATA
# ============================================================

snapshot, active_symbols, active_count = m.load_snapshot(
    m.SNAPSHOT_FILE
)

cache = m.load_current_cache(
    active_symbols
)

symbols = sorted(
    str(x).upper()
    for x in cache["symbol"].dropna().unique()
)

if len(symbols) != 35:
    raise RuntimeError(
        f"expected 35 symbols, got {len(symbols)}"
    )

if int(active_count) != 35:
    raise RuntimeError(
        f"expected active_count=35, got {active_count}"
    )

print(
    "cache symbols =",
    len(symbols),
    "/ active =",
    active_count,
)

print("loading ordinary market base ...")

market = m.load_market_bars(
    cache
)


# ============================================================
# EXACT SIGNAL BOOKS
# ============================================================

print()
print("building EXACT FINAL B EVENT book ...")

event_book, event_meta = (
    m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=final_b_event,
        frames=ALL_FRAMES,
        data_mode="CACHE_MATERIALS",
    )
)

print(
    "building EXACT FINAL B OWNER STATE book ..."
)

state_book, state_meta = (
    m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=final_b_state,
        frames=ALL_FRAMES,
        data_mode="CACHE_MATERIALS",
    )
)

start_ts = max(
    int(
        pd.Timestamp(
            "2020-01-01 00:00:00",
            tz="UTC",
        ).timestamp()
    ),
    int(cache["ts"].min()),
)

end_ts = int(
    cache["ts"].max()
)


# ============================================================
# EXACT 12H + 1D REPRODUCTION
# ============================================================

print()
print("=" * 120)
print("12H+1D EXACT REPRODUCTION")
print("MARK100 | ENTRY GATE ON | CONTROL SL ON")
print("TRAILING 4/2 + STATE")
print("=" * 120)

r = m.simulate_live_multiframe(
    signal_book=event_book,
    symbols=symbols,
    snapshot=snapshot,
    market=market,
    start_ts=start_ts,
    end_ts=end_ts,
    leverage_map=LEVERAGE,
    frames=FRAMES,
    active_count=35,
    start_equity=10000.0,
    collect_trade_log=False,
    trailing_start_pct=4.0,
    trailing_callback_pct=2.0,
    state_reentry_book=state_book,
    state_reentry_pullback_pct=0.0,
)

stats = r.get(
    "risk_guard_stats",
    {},
)

final_equity = float(
    r["final_equity"]
)

compound = float(
    r["compound_pct"]
)

mdd = float(
    r["realized_mdd_pct"]
)

ratio = (
    compound / abs(mdd)
    if abs(mdd) > 0
    else float("nan")
)

print()
print(
    "STATUS             =",
    "BANKRUPT"
    if int(r.get("bankrupt", 0))
    else "SURVIVED",
)
print(
    "FINAL EQUITY       =",
    f"${final_equity:,.2f}",
)
print(
    "COMPOUND           =",
    f"{compound:+,.2f}%",
)
print(
    "MDD                =",
    f"{mdd:+,.2f}%",
)
print(
    "RETURN / |MDD|     =",
    f"{ratio:,.2f}",
)
print(
    "LIQUIDATIONS       =",
    int(r.get("liquidations", 0)),
)
print(
    "ENTRIES            =",
    int(r.get("entries", r.get("entry_count", 0))),
)
print(
    "ENTRY CHECKS       =",
    int(stats.get("entry_checks", 0)),
)
print(
    "ENTRY BLOCKS       =",
    int(stats.get("entry_blocks", 0)),
)
print(
    "CONTROL SL CHECKS  =",
    int(stats.get("control_sl_checks", 0)),
)
print(
    "CONTROL SL BREACH  =",
    int(stats.get("control_sl_breaches", 0)),
)
print(
    "CONTROL SL CLOSES  =",
    int(stats.get("control_sl_closes", 0)),
)
print(
    "TRAILING CLOSES    =",
    int(r.get("trailing_closes", r.get("trailing_count", 0))),
)
print(
    "STATE REENTRIES    =",
    int(r.get("state_reentries", r.get("reentries", 0))),
)


# ============================================================
# HARD REGRESSION CHECK
# ============================================================

EXPECTED = {
    "final_equity": 188700719.65,
    "compound": 1886907.20,
    "mdd": -84.00,
    "entry_blocks": 111989,
    "control_sl_closes": 2724,
}

print()
print("=" * 120)
print("REGRESSION CHECK")
print("=" * 120)

print(
    "EXPECTED FINAL EQUITY =",
    EXPECTED["final_equity"],
)
print(
    "ACTUAL FINAL EQUITY   =",
    final_equity,
)

print(
    "EXPECTED COMPOUND     =",
    EXPECTED["compound"],
)
print(
    "ACTUAL COMPOUND       =",
    compound,
)

print(
    "EXPECTED MDD          =",
    EXPECTED["mdd"],
)
print(
    "ACTUAL MDD            =",
    mdd,
)

print(
    "EXPECTED ENTRY BLOCKS =",
    EXPECTED["entry_blocks"],
)
print(
    "ACTUAL ENTRY BLOCKS   =",
    int(stats.get("entry_blocks", 0)),
)

print(
    "EXPECTED CONTROL SL   =",
    EXPECTED["control_sl_closes"],
)
print(
    "ACTUAL CONTROL SL     =",
    int(stats.get("control_sl_closes", 0)),
)

equity_ok = abs(
    final_equity - EXPECTED["final_equity"]
) <= 1.0

compound_ok = abs(
    compound - EXPECTED["compound"]
) <= 1.0

mdd_ok = abs(
    mdd - EXPECTED["mdd"]
) <= 0.05

blocks_ok = (
    int(stats.get("entry_blocks", 0))
    ==
    EXPECTED["entry_blocks"]
)

control_ok = (
    int(stats.get("control_sl_closes", 0))
    ==
    EXPECTED["control_sl_closes"]
)

repro_ok = (
    equity_ok
    and compound_ok
    and mdd_ok
    and blocks_ok
    and control_ok
)

print()
print(
    "REPRO_STATUS =",
    "PASS"
    if repro_ok
    else "FAIL",
)

if not repro_ok:
    raise SystemExit(
        "FINAL_B_12H1D_REGRESSION_FAIL"
    )

print(
    "FINAL_B_12H1D_REGRESSION_OK"
)


