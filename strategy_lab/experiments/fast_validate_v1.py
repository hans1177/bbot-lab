# file: fast_validate_v1.py
# FAST EXPERIMENT ENGINE V2 validator
# STANDARD source files are not modified.
# 1) short-range V1 vs V2 parity + speed comparison
# 2) only after PASS: full V2 against sealed STANDARD V1 baseline

from __future__ import annotations

import argparse
import cProfile
import importlib.util
import io
import json
import os
import pstats
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

BUILD_ID = "FAST_V2_FIXED_20260826_A"


HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

os.environ["LAB_CROSS_GUARD"] = "1"
os.environ["LAB_CONTROL_SL"] = "1"
os.environ["LAB_MARK_LIQUIDATION"] = "1"
os.environ.pop("LAB_RISK_PROBE", None)

import strategy_lab.leverage_scan as m
from fast_engine_v1 import build_fast_engine


ALL_FRAMES = ("6H", "12H", "1D", "1W")
FRAMES = ("12H", "1D")
AUX_BY_TF = {
    "6H": "OBV",
    "12H": "SAR",
    "1D": "SAR",
    "1W": "OBV",
}
LEVERAGE = {"12H": 10, "1D": 15}

SEALED_BASELINE = {
    "bankrupt": 0,
    "final_equity": 188700719.64527103,
    "compound_pct": 1886907.1964527103,
    "realized_mdd_pct": -83.99704085937681,
    "liquidations": 0,
    "entries": 16379,
    "entry_checks": 128368,
    "entry_blocks": 111989,
    "control_sl_checks": 58118,
    "control_sl_breaches": 2683,
    "control_sl_closes": 2724,
    "trailing_closes": 11812,
    "state_reentries": 13881,
}


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tmpl = load_mod(
    "exact_template_fast_validate_v2",
    LAB / "strategies" / "_TEMPLATE.py",
)


def agree(a, b):
    return tmpl.agree(a, b)


def raw_ema_state(df, tf):
    prefix = str(tf).lower()
    long_col = f"{prefix}_ema_long"
    short_col = f"{prefix}_ema_short"

    if long_col not in df.columns or short_col not in df.columns:
        raise RuntimeError(f"EMA columns missing: {tf}")

    long_v = pd.to_numeric(df[long_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    short_v = pd.to_numeric(df[short_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)

    state = np.zeros(len(df), dtype=np.int8)
    state[(long_v > short_v) & (long_v > 0.0)] = 1
    state[(short_v > long_v) & (short_v > 0.0)] = -1

    valid_col = f"{prefix}_ema_valid"
    if valid_col in df.columns:
        valid = pd.to_numeric(df[valid_col], errors="coerce").fillna(0).to_numpy(dtype=float)
        state[valid <= 0] = 0
    return state


def final_b_event(df, tf):
    tf = str(tf).upper()
    return agree(
        tmpl.material_signal(df, tf, "EMA_CROSS"),
        tmpl.material_signal(df, tf, AUX_BY_TF[tf]),
    )


def final_b_state(df, tf):
    tf = str(tf).upper()
    return agree(
        raw_ema_state(df, tf),
        tmpl.material_signal(df, tf, AUX_BY_TF[tf]),
    )


class Heartbeat:
    def __init__(self, label, sec=30):
        self.label = str(label)
        self.sec = int(sec)
        self.started = None
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.started = time.perf_counter()

        def loop():
            while not self.stop_event.wait(self.sec):
                elapsed = int(time.perf_counter() - self.started)
                h, rem = divmod(elapsed, 3600)
                mm, ss = divmod(rem, 60)
                print(
                    f"[FASTV2][RUNNING] stage={self.label} "
                    f"elapsed={h:02d}:{mm:02d}:{ss:02d}",
                    flush=True,
                )

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        print(f"[FASTV2][START] stage={self.label}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop_event.set()
        elapsed = int(time.perf_counter() - self.started)
        h, rem = divmod(elapsed, 3600)
        mm, ss = divmod(rem, 60)
        print(
            f"[FASTV2][END] stage={self.label} "
            f"elapsed={h:02d}:{mm:02d}:{ss:02d}",
            flush=True,
        )


def extract_metrics(r):
    stats = r.get("risk_guard_stats", {})
    return {
        "bankrupt": int(r.get("bankrupt", 0)),
        "final_equity": float(r["final_equity"]),
        "compound_pct": float(r["compound_pct"]),
        "realized_mdd_pct": float(r["realized_mdd_pct"]),
        "liquidations": int(r.get("liquidations", 0)),
        "entries": int(r.get("entries", r.get("entry_count", 0))),
        "entry_checks": int(stats.get("entry_checks", 0)),
        "entry_blocks": int(stats.get("entry_blocks", 0)),
        "control_sl_checks": int(stats.get("control_sl_checks", 0)),
        "control_sl_breaches": int(stats.get("control_sl_breaches", 0)),
        "control_sl_closes": int(stats.get("control_sl_closes", 0)),
        "trailing_closes": int(r.get("trailing_closes", r.get("trailing_count", 0))),
        "state_reentries": int(r.get("state_reentries", r.get("reentries", 0))),
        "multi_tf_skips": int(r.get("multi_tf_skips", 0)),
    }


def same_metrics(a, b):
    float_keys = ("final_equity", "compound_pct", "realized_mdd_pct")
    int_keys = (
        "bankrupt",
        "liquidations",
        "entries",
        "entry_checks",
        "entry_blocks",
        "control_sl_checks",
        "control_sl_breaches",
        "control_sl_closes",
        "trailing_closes",
        "state_reentries",
        "multi_tf_skips",
    )
    diffs = {}
    ok = True

    for k in float_keys:
        av = float(a[k])
        bv = float(b[k])
        d = av - bv
        diffs[k] = d
        tol = max(1e-9, abs(av) * 1e-12, abs(bv) * 1e-12)
        if abs(d) > tol:
            ok = False

    for k in int_keys:
        av = int(a[k])
        bv = int(b[k])
        diffs[k] = av - bv
        if av != bv:
            ok = False

    return ok, diffs


def full_baseline_check(actual):
    float_keys = ("final_equity", "compound_pct", "realized_mdd_pct")
    int_keys = (
        "bankrupt",
        "liquidations",
        "entries",
        "entry_checks",
        "entry_blocks",
        "control_sl_checks",
        "control_sl_breaches",
        "control_sl_closes",
        "trailing_closes",
        "state_reentries",
    )
    diffs = {}
    ok = True

    for k in float_keys:
        av = float(actual[k])
        ev = float(SEALED_BASELINE[k])
        d = av - ev
        diffs[k] = d
        tol = max(1e-6, abs(ev) * 1e-11)
        if abs(d) > tol:
            ok = False

    for k in int_keys:
        av = int(actual[k])
        ev = int(SEALED_BASELINE[k])
        diffs[k] = av - ev
        if av != ev:
            ok = False

    return ok, diffs


def run_simulator(fn, kwargs, label, profile_path=None):
    prof = cProfile.Profile() if profile_path is not None else None
    t0 = time.perf_counter()
    with Heartbeat(label):
        if prof is None:
            result = fn(**kwargs)
        else:
            prof.enable()
            try:
                result = fn(**kwargs)
            finally:
                prof.disable()
    elapsed = time.perf_counter() - t0

    if prof is not None:
        stream = io.StringIO()
        stats = pstats.Stats(prof, stream=stream)
        stats.strip_dirs().sort_stats("cumulative").print_stats(60)
        profile_path.write_text(stream.getvalue(), encoding="utf-8")
        print("PROFILE =", profile_path, flush=True)
    return result, elapsed


def print_metrics(title, metrics, elapsed=None):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    print("FINAL EQUITY       =", f"${metrics['final_equity']:,.2f}")
    print("COMPOUND           =", f"{metrics['compound_pct']:+,.2f}%")
    print("MDD                =", f"{metrics['realized_mdd_pct']:+.6f}%")
    print("LIQUIDATIONS       =", metrics["liquidations"])
    print("ENTRIES            =", metrics["entries"])
    print("ENTRY CHECKS       =", metrics["entry_checks"])
    print("ENTRY BLOCKS       =", metrics["entry_blocks"])
    print("CONTROL SL CHECKS  =", metrics["control_sl_checks"])
    print("CONTROL SL BREACH  =", metrics["control_sl_breaches"])
    print("CONTROL SL CLOSES  =", metrics["control_sl_closes"])
    print("TRAILING CLOSES    =", metrics["trailing_closes"])
    print("STATE REENTRIES    =", metrics["state_reentries"])
    print("MULTI TF SKIPS     =", metrics["multi_tf_skips"])
    if elapsed is not None:
        print("ELAPSED            =", f"{elapsed:.2f}s")


def prepare_data():
    snapshot, active_symbols, active_count = m.load_snapshot(m.SNAPSHOT_FILE)
    cache = m.load_current_cache(active_symbols)
    symbols = sorted(str(x).upper() for x in cache["symbol"].dropna().unique())

    if len(symbols) != 35:
        raise RuntimeError(f"expected 35 symbols, got {len(symbols)}")
    if int(active_count) != 35:
        raise RuntimeError(f"expected active_count=35, got {active_count}")

    print("cache symbols =", len(symbols), "/ active =", active_count)
    print("loading ordinary market base ...")
    market = m.load_market_bars(cache)

    print("building EXACT FINAL B EVENT book ...")
    event_book, _ = m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=final_b_event,
        frames=ALL_FRAMES,
        data_mode="CACHE_MATERIALS",
    )

    print("building EXACT FINAL B OWNER STATE book ...")
    state_book, _ = m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=final_b_state,
        frames=ALL_FRAMES,
        data_mode="CACHE_MATERIALS",
    )

    start_ts = max(
        int(pd.Timestamp("2020-01-01 00:00:00", tz="UTC").timestamp()),
        int(cache["ts"].min()),
    )
    end_ts = int(cache["ts"].max())

    print("prewarming Mark 1H liquidation market ...")
    m._lab_mark_liquidation_market(market)
    print("prewarming Cross stress library ...")
    m._lab_cross_stress_cache()

    return {
        "snapshot": snapshot,
        "cache": cache,
        "symbols": symbols,
        "market": market,
        "event_book": event_book,
        "state_book": state_book,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


def make_kwargs(data, start_ts, end_ts):
    return {
        "signal_book": data["event_book"],
        "symbols": data["symbols"],
        "snapshot": data["snapshot"],
        "market": data["market"],
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "leverage_map": LEVERAGE,
        "frames": FRAMES,
        "active_count": 35,
        "start_equity": 10000.0,
        "collect_trade_log": False,
        "trailing_start_pct": 4.0,
        "trailing_callback_pct": 2.0,
        "state_reentry_book": data["state_book"],
        "state_reentry_pullback_pct": 0.0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full", "all"), default="all")
    parser.add_argument("--smoke-days", type=int, default=90)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()

    if args.smoke_days < 7:
        raise SystemExit("--smoke-days must be >= 7")

    RESULTS.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("FAST EXPERIMENT ENGINE V2 VALIDATION")
    print("BUILD ID =", BUILD_ID)
    print("STANDARD SOURCE FILES ARE NOT MODIFIED")
    print("V2 is stored under the existing V1 filenames; smoke compares ORIGINAL EXACT vs V2")
    print("=" * 100)

    with Heartbeat("PREPARE_DATA_AND_BOOKS"):
        data = prepare_data()

    v2 = build_fast_engine(m)
    print("V2 PATCH = PASS", v2.patch_stats)

    payload = {
        "schema": "FAST_ENGINE_V2_VALIDATION",
        "v2_patch_stats": v2.patch_stats,
        "sealed_baseline": SEALED_BASELINE,
        "smoke": None,
        "full": None,
    }

    if args.mode in ("smoke", "all"):
        smoke_start = max(
            int(data["start_ts"]),
            int(data["end_ts"] - args.smoke_days * 86400),
        )
        smoke_kwargs = make_kwargs(data, smoke_start, data["end_ts"])

        p1 = RESULTS / "fast_engine_v2_smoke_exact_profile.txt" if args.profile else None
        p2 = RESULTS / "fast_engine_v2_smoke_v2_profile.txt" if args.profile else None

        r1, sec1 = run_simulator(m.simulate_live_multiframe, smoke_kwargs, "SMOKE_EXACT", p1)
        m1 = extract_metrics(r1)
        print_metrics("SMOKE ORIGINAL EXACT", m1, sec1)

        r2, sec2 = run_simulator(v2.run, smoke_kwargs, "SMOKE_V2", p2)
        m2 = extract_metrics(r2)
        print_metrics("SMOKE FAST V2", m2, sec2)

        ok, diffs = same_metrics(m1, m2)
        speedup = sec1 / sec2 if sec2 > 0 else float("inf")

        print()
        print("EXACT_V2_SMOKE_PARITY =", "PASS" if ok else "FAIL")
        print("V2_SPEEDUP_VS_EXACT   =", f"{speedup:.2f}x")
        print("SMOKE_DIFFS         =", diffs)

        payload["smoke"] = {
            "days": int(args.smoke_days),
            "exact_seconds": float(sec1),
            "v2_seconds": float(sec2),
            "speedup_vs_exact": float(speedup),
            "parity": "PASS" if ok else "FAIL",
            "diffs": diffs,
            "exact": m1,
            "v2": m2,
        }

        if not ok:
            out = RESULTS / "fast_engine_v2_validation.json"
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("RESULT =", out)
            raise SystemExit("FAST_ENGINE_V2_SMOKE_PARITY_FAIL")

    if args.mode in ("full", "all"):
        full_kwargs = make_kwargs(data, data["start_ts"], data["end_ts"])
        r2, sec2 = run_simulator(v2.run, full_kwargs, "FULL_FAST_V2_STANDARD_V1", None)
        m2 = extract_metrics(r2)
        print_metrics("FULL FAST V2 STANDARD V1", m2, sec2)

        ok, diffs = full_baseline_check(m2)
        print()
        print("FULL_V2_BASELINE_PARITY =", "PASS" if ok else "FAIL")
        print("FULL_DIFFS              =", diffs)

        payload["full"] = {
            "seconds": float(sec2),
            "parity": "PASS" if ok else "FAIL",
            "diffs": diffs,
            "v2": m2,
        }

        if not ok:
            out = RESULTS / "fast_engine_v2_validation.json"
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("RESULT =", out)
            raise SystemExit("FAST_ENGINE_V2_FULL_BASELINE_FAIL")

    out = RESULTS / "fast_engine_v2_validation.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("RESULT =", out)
    print("FAST_ENGINE_V2_VALIDATION_COMPLETE")


if __name__ == "__main__":
    main()
