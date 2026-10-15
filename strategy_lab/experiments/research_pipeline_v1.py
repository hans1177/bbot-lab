# file: research_pipeline_v2.py
# RESEARCH PIPELINE V2
#
# Purpose:
# - Preserve STANDARD V1 as immutable Champion.
# - Use validated FAST V2 for research.
# - Eliminate repeated work with shared in-process data/books.
# - Accumulate every completed result.
# - Resume after interruption at candidate granularity.
# - Skip already completed candidates automatically.
# - Use conservative FAST SCREEN first; FULL 35 only for survivors.
#
# Implemented research stages:
#   - REENTRY STRUCTURE LAB V1
#   - REENTRY MATERIAL LAB V1
#   - TRAILING LAB V1
#   - CONTROL SL LAB V1
#   - OPPOSITE SIGNAL REOPEN TIMING LAB V1
#   - OPPOSITE SIGNAL EXIT SOURCE LAB V1
#   - UNIVERSE SIZE LAB V1
#   - ROBUSTNESS LAB V1
#   - WALK-FORWARD LAB V1
#   - WALK-FORWARD RECHECK 3 + U35 ORDER FIX V1
#   - U35 ORDER-REBASE FIX V1
#   - ROBUSTNESS CONFIRM U30 V1
#   - ORIGINAL EXACT U30 FINAL V1
#
# Research invariant:
# Entry Gate, actual liquidation logic, leverage,
# EVENT flat-entry and owner strategy remain STANDARD.
# TRAILING LAB changes only trailing start/callback around STANDARD 4/2.
# CONTROL SL LAB changes only the stress-cushion trigger policy and keeps
# STANDARD trailing 4/2, STANDARD Gate and STANDARD re-entry.
# OPPOSITE SIGNAL REOPEN TIMING LAB changes only what happens AFTER the
# existing owner opposite-signal close. Exit-source semantics remain STANDARD.
# OPPOSITE SIGNAL EXIT SOURCE LAB changes only WHICH owner signal can trigger
# the opposite close. Entry EVENT, Gate, Control SL and risk exits remain fixed.
# UNIVERSE SIZE LAB changes only active symbol count/order. Fixed 10 symbols
# remain fixed and dynamic symbols are taken from the current snapshot ranking.
# ROBUSTNESS LAB stress-tests the selected structure without changing its
# baseline rules: time slices, transaction-cost proxy, +1H/+2H signal delay,
# coarse trailing neighborhoods, and adjacent universe sizes.
# WALK-FORWARD LAB performs frozen-parameter anchored historical OOS-style
# validation against current SEALED STANDARD V1. No fold-by-fold retuning.
# WALK-FORWARD RECHECK 3 retests only the three preselected Universe candidates
# after U20 received REVIEW. No parameter search is performed in this stage.
#
# REENTRY MATERIAL LAB keeps STANDARD owner STATE and adds only
# one owner-specific lower-TF native material confirmation at a time.
# Continuous/filter-like and trigger/event-like materials are screened
# in separate groups so their semantics are not ranked together.

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import importlib.util
import inspect
import json
import math
import os
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd


BUILD_ID = "RESEARCH_PIPELINE_V2_20260826_M"
REQUIRED_FAST_BUILD_ID = "FAST_V2_FIXED_20260826_A"

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------
# Robust project bootstrap.
# Expected local layout:
#   C:\my\bottest\strategy_lab\experiments\research_pipeline_v2.py
# ---------------------------------------------------------------------
ROOT = None
for _p in (HERE, *HERE.parents):
    if (_p / "strategy_lab" / "leverage_scan.py").exists():
        ROOT = _p
        break

if ROOT is None:
    raise RuntimeError(
        "PROJECT ROOT NOT FOUND: expected strategy_lab/leverage_scan.py "
        f"above {HERE}"
    )

LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"
PIPE_RESULTS = RESULTS / "research_pipeline_v2"
CHECKPOINT_ROOT = PIPE_RESULTS / "checkpoints"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# STANDARD research-risk switches.
os.environ["LAB_CROSS_GUARD"] = "1"
os.environ["LAB_CONTROL_SL"] = "1"
os.environ["LAB_MARK_LIQUIDATION"] = "1"
os.environ.pop("LAB_RISK_PROBE", None)

import strategy_lab.leverage_scan as m
from fast_engine_v1 import (
    BUILD_ID as FAST_BUILD_ID,
    FastEngineV2,
    _FastPatchTransformer,
    build_fast_engine,
)

if FAST_BUILD_ID != REQUIRED_FAST_BUILD_ID:
    raise RuntimeError(
        "FAST V2 BUILD MISMATCH: "
        f"required={REQUIRED_FAST_BUILD_ID} actual={FAST_BUILD_ID}"
    )


# =============================================================================
# SEALED STANDARD V1
# =============================================================================

OWNER_FRAMES = ("12H", "1D")
LEVERAGE = {"12H": 10, "1D": 15}
TRAILING_START_PCT = 4.0
TRAILING_CALLBACK_PCT = 2.0

SEALED_STANDARD = {
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
    "multi_tf_skips": 443,
}

AUX_BY_TF = {
    "12H": "SAR",
    "1D": "SAR",
}


# =============================================================================
# SIGNALS
# =============================================================================

def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tmpl = load_mod(
    "exact_template_research_pipeline_v2",
    LAB / "strategies" / "_TEMPLATE.py",
)


def agree(a, b):
    return tmpl.agree(a, b)


def raw_ema_state(df, tf):
    """
    Ongoing EMA direction STATE, not EMA_CROSS EVENT.
    """
    tf = str(tf).upper()
    prefix = tf.lower()
    long_col = f"{prefix}_ema_long"
    short_col = f"{prefix}_ema_short"

    if long_col not in df.columns or short_col not in df.columns:
        raise RuntimeError(f"EMA columns missing: {tf}")

    long_v = (
        pd.to_numeric(df[long_col], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    short_v = (
        pd.to_numeric(df[short_col], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )

    state = np.zeros(len(df), dtype=np.int8)
    state[(long_v > short_v) & (long_v > 0.0)] = 1
    state[(short_v > long_v) & (short_v > 0.0)] = -1

    valid_col = f"{prefix}_ema_valid"
    if valid_col in df.columns:
        valid = (
            pd.to_numeric(df[valid_col], errors="coerce")
            .fillna(0)
            .to_numpy(dtype=float)
        )
        state[valid <= 0] = 0

    return state


def final_b_event(
    df,
    tf,
    cross_entry_gate_mode="STANDARD_CROSS_EVENT",
    ema_sar_entry_mode="EMA_CROSS_PLUS_SAR",
):
    """
    Direct new-entry event composition.

    R7 axis:
      STANDARD_CROSS_EVENT
      CURRENT_EMA_STATE_ENTRY

    R8 axis:
      EMA_CROSS_PLUS_SAR
      EMA_CROSS_ONLY
      SAR_STATE_ONLY

    Non-baseline R8 modes are valid only with the frozen
    R7 STANDARD_CROSS_EVENT mode. This prevents cross-axis
    parameter mixing.
    """
    tf = str(tf).upper()

    cross_mode = str(
        cross_entry_gate_mode
    ).upper()

    composition_mode = str(
        ema_sar_entry_mode
    ).upper()

    if cross_mode not in (
        "STANDARD_CROSS_EVENT",
        "CURRENT_EMA_STATE_ENTRY",
    ):
        raise ValueError(
            "unsupported cross_entry_gate_mode: "
            + repr(cross_entry_gate_mode)
        )

    if composition_mode == "EMA_CROSS_PLUS_SAR":

        if cross_mode == "STANDARD_CROSS_EVENT":
            ema_entry = tmpl.material_signal(
                df,
                tf,
                "EMA_CROSS",
            )

        else:
            ema_entry = raw_ema_state(
                df,
                tf,
            )

        sar_state = tmpl.material_signal(
            df,
            tf,
            AUX_BY_TF[tf],
        )

        return agree(
            ema_entry,
            sar_state,
        )

    if cross_mode != "STANDARD_CROSS_EVENT":
        raise ValueError(
            "R8 non-baseline ema_sar_entry_mode requires "
            "cross_entry_gate_mode='STANDARD_CROSS_EVENT'"
        )

    if composition_mode == "EMA_CROSS_ONLY":
        return tmpl.material_signal(
            df,
            tf,
            "EMA_CROSS",
        )

    if composition_mode == "SAR_STATE_ONLY":
        return tmpl.material_signal(
            df,
            tf,
            AUX_BY_TF[tf],
        )

    raise ValueError(
        "unsupported ema_sar_entry_mode: "
        + repr(ema_sar_entry_mode)
    )


def final_b_owner_state(df, tf):
    """
    STANDARD owner STATE used by current STATE re-entry:
      raw EMA STATE + SAR material agreement.
    """
    tf = str(tf).upper()
    return agree(
        raw_ema_state(df, tf),
        tmpl.material_signal(df, tf, AUX_BY_TF[tf]),
    )


# =============================================================================
# GENERAL UTILITIES
# =============================================================================

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
                hh, rem = divmod(elapsed, 3600)
                mm, ss = divmod(rem, 60)
                print(
                    f"[PIPEV2][RUNNING] stage={self.label} "
                    f"elapsed={hh:02d}:{mm:02d}:{ss:02d}",
                    flush=True,
                )

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        print(f"[PIPEV2][START] stage={self.label}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop_event.set()
        elapsed = int(time.perf_counter() - self.started)
        hh, rem = divmod(elapsed, 3600)
        mm, ss = divmod(rem, 60)
        print(
            f"[PIPEV2][END] stage={self.label} "
            f"elapsed={hh:02d}:{mm:02d}:{ss:02d}",
            flush=True,
        )


def utc_stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def canonical_json(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(text):
    return hashlib.sha256(
        str(text).encode("utf-8")
    ).hexdigest()


def stable_hash(obj):
    return sha256_text(canonical_json(obj))


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def extract_metrics(result):
    stats = result.get("risk_guard_stats", {})
    return {
        "bankrupt": int(result.get("bankrupt", 0)),
        "final_equity": float(result["final_equity"]),
        "compound_pct": float(result["compound_pct"]),
        "realized_mdd_pct": float(result["realized_mdd_pct"]),
        "liquidations": int(result.get("liquidations", 0)),
        "entries": int(result.get("entries", result.get("entry_count", 0))),
        "entry_checks": int(stats.get("entry_checks", 0)),
        "entry_blocks": int(stats.get("entry_blocks", 0)),
        "control_sl_checks": int(stats.get("control_sl_checks", 0)),
        "control_sl_breaches": int(stats.get("control_sl_breaches", 0)),
        "control_sl_closes": int(stats.get("control_sl_closes", 0)),
        "trailing_closes": int(
            result.get("trailing_closes", result.get("trailing_count", 0))
        ),
        "state_reentries": int(
            result.get("state_reentries", result.get("reentries", 0))
        ),
        "state_reentry_cancels": int(
            result.get("state_reentry_cancels", 0)
        ),
        "state_reentry_skips": int(
            result.get("state_reentry_skips", 0)
        ),
        "state_reentry_pullback_waits": int(
            result.get("state_reentry_pullback_waits", 0)
        ),
        "multi_tf_skips": int(result.get("multi_tf_skips", 0)),
    }


PARITY_FLOAT_KEYS = (
    "final_equity",
    "compound_pct",
    "realized_mdd_pct",
)

PARITY_INT_KEYS = (
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
    "state_reentry_cancels",
    "state_reentry_skips",
    "state_reentry_pullback_waits",
    "multi_tf_skips",
)


def compare_metrics(a, b):
    diffs = {}
    ok = True

    for key in PARITY_FLOAT_KEYS:
        av = float(a[key])
        bv = float(b[key])
        diff = av - bv
        diffs[key] = diff
        tol = max(
            1e-9,
            abs(av) * 1e-12,
            abs(bv) * 1e-12,
        )
        if abs(diff) > tol:
            ok = False

    for key in PARITY_INT_KEYS:
        av = int(a[key])
        bv = int(b[key])
        diffs[key] = av - bv
        if av != bv:
            ok = False

    return ok, diffs


def return_over_mdd(metrics):
    mdd = abs(float(metrics["realized_mdd_pct"]))
    if mdd <= 1e-12:
        return float("inf")
    return float(metrics["compound_pct"]) / mdd


def print_metrics(title, metrics, elapsed=None):
    print()
    print("=" * 112)
    print(title)
    print("=" * 112)
    print("FINAL EQUITY       =", f"${metrics['final_equity']:,.2f}")
    print("COMPOUND           =", f"{metrics['compound_pct']:+,.2f}%")
    print("MDD                =", f"{metrics['realized_mdd_pct']:+.6f}%")
    print("RETURN / |MDD|     =", f"{return_over_mdd(metrics):,.2f}")
    print("LIQUIDATIONS       =", metrics["liquidations"])
    print("ENTRIES            =", metrics["entries"])
    print("ENTRY CHECKS       =", metrics["entry_checks"])
    print("ENTRY BLOCKS       =", metrics["entry_blocks"])
    print("CONTROL SL CHECKS  =", metrics["control_sl_checks"])
    print("CONTROL SL BREACH  =", metrics["control_sl_breaches"])
    print("CONTROL SL CLOSES  =", metrics["control_sl_closes"])
    print("TRAILING CLOSES    =", metrics["trailing_closes"])
    print("STATE REENTRIES    =", metrics["state_reentries"])
    print("STATE CANCELS      =", metrics["state_reentry_cancels"])
    print("STATE SKIPS        =", metrics["state_reentry_skips"])
    print("MULTI TF SKIPS     =", metrics["multi_tf_skips"])
    if elapsed is not None:
        print("ELAPSED            =", f"{elapsed:.2f}s")


def timed_run(fn, kwargs, label):
    started = time.perf_counter()
    with Heartbeat(label):
        result = fn(**kwargs)
    elapsed = time.perf_counter() - started
    return result, elapsed


# =============================================================================
# CUMULATIVE RESULTS + RESUME
# =============================================================================

MASTER_JSONL = PIPE_RESULTS / "research_master_results.jsonl"
MASTER_INDEX = PIPE_RESULTS / "research_master_index.json"


def load_jsonl(path):
    path = Path(path)
    rows = []
    if not path.exists():
        return rows

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def refresh_master_index():
    rows = load_jsonl(MASTER_JSONL)
    compact = []

    for row in rows:
        compact.append({
            "record_id": row.get("record_id"),
            "saved_at_utc": row.get("saved_at_utc"),
            "stage": row.get("stage"),
            "candidate": row.get("candidate"),
            "scope": row.get("scope"),
            "verdict": (
                row.get("verdict", {})
                .get("verdict")
                if isinstance(row.get("verdict"), dict)
                else row.get("verdict")
            ),
            "final_equity": (
                row.get("metrics", {})
                .get("final_equity")
                if isinstance(row.get("metrics"), dict)
                else None
            ),
            "mdd_pct": (
                row.get("metrics", {})
                .get("realized_mdd_pct")
                if isinstance(row.get("metrics"), dict)
                else None
            ),
            "checkpoint": row.get("checkpoint"),
        })

    atomic_write_json(
        MASTER_INDEX,
        {
            "schema": "RESEARCH_MASTER_INDEX_V2",
            "updated_at_utc": utc_stamp(),
            "count": len(compact),
            "records": compact,
        },
    )


def append_master_record(record):
    record = dict(record)
    payload_for_id = dict(record)
    payload_for_id.pop("record_id", None)
    payload_for_id.pop("saved_at_utc", None)
    record_id = stable_hash(payload_for_id)

    existing = load_jsonl(MASTER_JSONL)
    existing_ids = {
        str(x.get("record_id", ""))
        for x in existing
    }

    if record_id in existing_ids:
        return record_id, False

    record["record_id"] = record_id
    record["saved_at_utc"] = utc_stamp()

    MASTER_JSONL.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    with MASTER_JSONL.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        f.flush()
        os.fsync(f.fileno())

    refresh_master_index()
    return record_id, True


def register_previous_stress_gate():
    """
    Register prior V1 Stress Gate result in the cumulative index without
    modifying or re-running it.
    """
    legacy = RESULTS / "research_pipeline_v1_stress_gate_latest.json"
    if not legacy.exists():
        return

    raw = legacy.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except Exception:
        return

    append_master_record({
        "schema": "LEGACY_RESULT_REFERENCE_V1",
        "stage": "stress_gate_v1",
        "candidate": "G1_G4_BATCH",
        "scope": "FULL_35",
        "source_path": str(legacy),
        "source_sha256": sha256_text(raw),
        "result_summary": payload.get("ranking", []),
        "note": "Imported reference only; no test re-run.",
    })


def checkpoint_path(stage, candidate, key_hash):
    safe_stage = str(stage).replace("/", "_")
    safe_candidate = str(candidate).replace("/", "_")
    return (
        CHECKPOINT_ROOT
        / safe_stage
        / f"{safe_candidate}_{key_hash[:16]}.json"
    )


def checkpoint_key(
    stage,
    candidate,
    scope,
    candidate_config,
    data_fingerprint,
    start_ts,
    end_ts,
):
    return stable_hash({
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "stage": stage,
        "candidate": candidate,
        "scope": scope,
        "candidate_config": candidate_config,
        "data_fingerprint": data_fingerprint,
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "sealed_standard": SEALED_STANDARD,
    })


def load_checkpoint(
    stage,
    candidate,
    scope,
    candidate_config,
    data_fingerprint,
    start_ts,
    end_ts,
    resume=True,
):
    key = checkpoint_key(
        stage,
        candidate,
        scope,
        candidate_config,
        data_fingerprint,
        start_ts,
        end_ts,
    )
    path = checkpoint_path(stage, candidate, key)

    if not resume or not path.exists():
        return key, path, None

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return key, path, None

    if str(payload.get("checkpoint_key")) != key:
        return key, path, None

    if payload.get("status") != "DONE":
        return key, path, None

    return key, path, payload


def save_checkpoint(
    *,
    key,
    path,
    stage,
    candidate,
    scope,
    candidate_config,
    data_fingerprint,
    start_ts,
    end_ts,
    metrics,
    elapsed_seconds,
    extra=None,
):
    payload = {
        "schema": "RESEARCH_PIPELINE_V2_CHECKPOINT",
        "status": "DONE",
        "checkpoint_key": key,
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "saved_at_utc": utc_stamp(),
        "stage": stage,
        "candidate": candidate,
        "scope": scope,
        "candidate_config": candidate_config,
        "data_fingerprint": data_fingerprint,
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "elapsed_seconds": float(elapsed_seconds),
        "metrics": metrics,
        "extra": extra or {},
    }
    atomic_write_json(path, payload)

    append_master_record({
        "schema": "RESEARCH_RESULT_V2",
        "stage": stage,
        "candidate": candidate,
        "scope": scope,
        "candidate_config": candidate_config,
        "data_fingerprint": data_fingerprint,
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "elapsed_seconds": float(elapsed_seconds),
        "metrics": metrics,
        "extra": extra or {},
        "checkpoint": str(path),
    })

    return payload


# =============================================================================
# COMMON READ-ONLY DATA
# =============================================================================

def prepare_data(cross_entry_gate_mode="STANDARD_CROSS_EVENT", ema_sar_entry_mode="EMA_CROSS_PLUS_SAR"):
    snapshot, active_symbols, active_count = m.load_snapshot(
        m.SNAPSHOT_FILE
    )
    cache = m.load_current_cache(active_symbols)

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
    market = m.load_market_bars(cache)

    print("building STANDARD FINAL B EVENT book ...")
    # R7_PREPARE_DATA_MODE_BINDING_V1
    def _r7_entry_signal(df, tf):
            return final_b_event(
                df,
                tf,
                cross_entry_gate_mode,
                ema_sar_entry_mode,
            )

    event_book, _ = m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=_r7_entry_signal,
        frames=OWNER_FRAMES,
        data_mode="CACHE_MATERIALS",
    )

    print("building STANDARD OWNER STATE book ...")
    owner_state_book, _ = m.build_research_six_frame_signal_book(
        cache=cache,
        signal_fn=final_b_owner_state,
        frames=OWNER_FRAMES,
        data_mode="CACHE_MATERIALS",
    )

    print("building LOWER-TF raw EMA STATE book ...")
    lower_ema_state_book, _ = (
        m.build_research_six_frame_signal_book(
            cache=cache,
            signal_fn=raw_ema_state,
            frames=("1H", "6H", "12H"),
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
    end_ts = int(cache["ts"].max())

    print("prewarming Mark 1H liquidation market ...")
    mark_market = m._lab_mark_liquidation_market(
        market
    )

    print("prewarming STANDARD Cross stress library ...")
    stress_cache = m._lab_cross_stress_cache()

    # Small fingerprint: enough to invalidate stale checkpoints when
    # research input changes.
    fingerprint = {
        "symbol_count": len(symbols),
        "symbols": symbols,
        "cache_rows": int(len(cache)),
        "cache_ts_min": int(cache["ts"].min()),
        "cache_ts_max": int(cache["ts"].max()),
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "mark_rows": int(
            sum(
                len(rows)
                for rows in mark_market.values()
            )
            if isinstance(mark_market, dict)
            else 0
        ),
        "stress_rows": int(len(stress_cache["times"])),
    }
    fingerprint["fingerprint_hash"] = stable_hash(
        fingerprint
    )

    print(
        "DATA_FINGERPRINT =",
        json.dumps(
            fingerprint,
            ensure_ascii=False,
        ),
    )

    return {
        "snapshot": snapshot,
        "cache": cache,
        "symbols": symbols,
        "market": market,
        "event_book": event_book,
        "owner_state_book": owner_state_book,
        "lower_ema_state_book": lower_ema_state_book,
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "fingerprint": fingerprint,
    }


def make_kwargs(
    data,
    start_ts,
    end_ts,
    pullback_pct=0.0,
):
    return {
        "signal_book": data["event_book"],
        "symbols": data["symbols"],
        "snapshot": data["snapshot"],
        "market": data["market"],
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "leverage_map": LEVERAGE,
        "frames": OWNER_FRAMES,
        "active_count": 35,
        "start_equity": 10000.0,
        "collect_trade_log": False,
        "trailing_start_pct": TRAILING_START_PCT,
        "trailing_callback_pct": TRAILING_CALLBACK_PCT,
        "state_reentry_book": data["owner_state_book"],
        "state_reentry_pullback_pct": float(
            pullback_pct
        ),
    }


# =============================================================================
# REENTRY STRUCTURE LAB V1
# =============================================================================
#
# Important semantic split:
#
# OWNER STATE:
#   The existing 12H / 1D strategy state.
#   It still determines trend validity and cancels old waiting state when
#   the owner trend flips opposite.
#
# LOWER CONFIRMATION:
#   Additional raw EMA STATE only in this first structure stage.
#   No EMA_CROSS EVENT is used here.
#
# Later material/event tests are intentionally NOT mixed into this stage.
# That avoids changing TF structure + material type at the same time.
# =============================================================================

REENTRY_STRUCTURE_CANDIDATES = {
    "R1_NEXT_LOWER_EMA_STATE": {
        "description": (
            "Owner STATE + next lower EMA STATE. "
            "12H owner requires 6H EMA STATE; "
            "1D owner requires 12H EMA STATE."
        ),
        "requirements": {
            "12H": ["6H"],
            "1D": ["12H"],
        },
        "pullback_pct": 0.0,
        "complexity": 1,
    },
    "R2_FAST_LOWER_EMA_STATE": {
        "description": (
            "Owner STATE + faster EMA STATE. "
            "12H owner requires 1H EMA STATE; "
            "1D owner requires 6H EMA STATE."
        ),
        "requirements": {
            "12H": ["1H"],
            "1D": ["6H"],
        },
        "pullback_pct": 0.0,
        "complexity": 1,
    },
    "R3_HIERARCHY_EMA_STATE": {
        "description": (
            "Owner STATE + two lower EMA STATE confirmations. "
            "12H owner requires 6H and 1H; "
            "1D owner requires 12H and 6H."
        ),
        "requirements": {
            "12H": ["6H", "1H"],
            "1D": ["12H", "6H"],
        },
        "pullback_pct": 0.0,
        "complexity": 2,
    },
}

STRICT_STANDARD_REENTRY_CONFIG = {
    "description": "Exact STANDARD owner STATE re-entry replica.",
    "requirements": {
        "12H": [],
        "1D": [],
    },
    "pullback_pct": 0.0,
    "complexity": 0,
}


class SignalBookLookup:
    def __init__(self, book):
        self.book = {}
        for key, value in book.items():
            ts_arr, sig_arr = value
            self.book[key] = (
                np.asarray(ts_arr, dtype=np.int64),
                np.asarray(sig_arr, dtype=np.int8),
            )

    def latest(self, symbol, tf, ts):
        key = (
            str(symbol).upper(),
            str(tf).upper(),
        )
        item = self.book.get(key)
        if item is None:
            raise RuntimeError(
                f"signal book key missing: {key}"
            )

        ts_arr, sig_arr = item
        if len(ts_arr) != len(sig_arr):
            raise RuntimeError(
                f"signal book length mismatch: {key}"
            )
        if not len(ts_arr):
            return 0

        i = int(
            np.searchsorted(
                ts_arr,
                int(ts),
                side="right",
            )
            - 1
        )
        if i < 0:
            return 0

        out = int(sig_arr[i])
        if out not in (-1, 0, 1):
            raise RuntimeError(
                f"bad signal value: {key} {out}"
            )
        return out


class ReentryDecisionEngine:
    """
    Decision helper called only inside STATE_WAIT processing.

    Owner trend validity is never delegated to lower TF:
      owner opposite -> cancel old wait exactly as STANDARD.
      owner zero     -> keep waiting.
      owner same     -> check candidate lower STATE conditions.

    Therefore lower TF confirmation can delay re-entry but cannot keep an
    obsolete owner trend alive.
    """

    def __init__(
        self,
        owner_state_book,
        lower_ema_state_book,
        config,
    ):
        self.owner = SignalBookLookup(
            owner_state_book
        )
        self.lower_ema = SignalBookLookup(
            lower_ema_state_book
        )
        self.config = dict(config)

        self.stats = {
            "calls": 0,
            "owner_same": 0,
            "owner_opposite_cancel": 0,
            "owner_zero": 0,
            "lower_blocks": 0,
            "eligible": 0,
            "lower_blocks_by_tf": {},
        }

    def decision(
        self,
        symbol,
        owner_tf,
        wait_side,
        ts,
    ):
        self.stats["calls"] += 1

        symbol = str(symbol).upper()
        owner_tf = str(owner_tf).upper()
        wait_side = int(wait_side)
        ts = int(ts)

        if wait_side not in (-1, 1):
            raise RuntimeError(
                f"bad wait_side: {wait_side}"
            )

        owner_decision = self.owner.latest(
            symbol,
            owner_tf,
            ts,
        )

        if owner_decision == -wait_side:
            self.stats[
                "owner_opposite_cancel"
            ] += 1
            return -wait_side

        if owner_decision == 0:
            self.stats["owner_zero"] += 1
            return 0

        if owner_decision != wait_side:
            # Defensive; only -1/0/1 are possible.
            return 0

        self.stats["owner_same"] += 1

        requirements = (
            self.config
            .get("requirements", {})
            .get(owner_tf, [])
        )

        for lower_tf in requirements:
            lower_tf = str(lower_tf).upper()
            lower_decision = self.lower_ema.latest(
                symbol,
                lower_tf,
                ts,
            )
            if lower_decision != wait_side:
                self.stats["lower_blocks"] += 1
                by_tf = self.stats[
                    "lower_blocks_by_tf"
                ]
                by_tf[lower_tf] = (
                    int(by_tf.get(lower_tf, 0))
                    + 1
                )
                return 0

        self.stats["eligible"] += 1
        return wait_side


# =============================================================================
# FAST V2 REENTRY PATCH
# =============================================================================

class _ReentryCoreTransformer(ast.NodeTransformer):
    """
    Replace only STANDARD's owner-state lookup/calculation inside STATE_WAIT
    with one helper call.

    The surrounding behavior remains unchanged:
      - fresh EVENT may reopen first
      - ts must be after exit
      - pullback code remains STANDARD
      - opposite decision cancels wait
      - enter_position() still passes STANDARD Entry Gate
    """

    def __init__(self):
        super().__init__()
        self.replaced = 0

    @staticmethod
    def _assigns_name(node, name):
        return (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name)
                and t.id == name
                for t in node.targets
            )
        )

    @staticmethod
    def _is_state_decision_validation(node):
        return (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(
                node.test.left,
                ast.Name,
            )
            and node.test.left.id == "state_decision"
            and len(node.test.ops) == 1
            and isinstance(
                node.test.ops[0],
                ast.NotIn,
            )
        )

    def visit_If(self, node):
        node = self.generic_visit(node)

        start_i = None
        end_i = None

        for i, child in enumerate(node.body):
            if (
                start_i is None
                and self._assigns_name(
                    child,
                    "state_key",
                )
            ):
                start_i = i

            if (
                start_i is not None
                and self._is_state_decision_validation(
                    child
                )
            ):
                end_i = i
                break

        if (
            start_i is not None
            and end_i is not None
            and end_i > start_i
        ):
            replacement = ast.Assign(
                targets=[
                    ast.Name(
                        id="state_decision",
                        ctx=ast.Store(),
                    )
                ],
                value=ast.Call(
                    func=ast.Name(
                        id="_pipeline_reentry_decision",
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Name(
                            id="symbol",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="wait_tf",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="wait_side",
                            ctx=ast.Load(),
                        ),
                        ast.Call(
                            func=ast.Name(
                                id="int",
                                ctx=ast.Load(),
                            ),
                            args=[
                                ast.Name(
                                    id="ts",
                                    ctx=ast.Load(),
                                )
                            ],
                            keywords=[],
                        ),
                    ],
                    keywords=[],
                ),
            )
            replacement = ast.copy_location(
                replacement,
                node.body[start_i],
            )

            node.body = (
                node.body[:start_i]
                + [replacement]
                + node.body[end_i:]
            )
            self.replaced += 1

        return node


class ReentryFastEngine(FastEngineV2):
    def __init__(self, module, decision_engine):
        self.decision_engine = decision_engine
        super().__init__(module)

    def _build_simulator(self):
        src = textwrap.dedent(
            inspect.getsource(
                self.m.simulate_live_multiframe
            )
        )
        tree = ast.parse(src)

        # Verified FAST V2 transformations first.
        fast_transformer = _FastPatchTransformer()
        tree = fast_transformer.visit(tree)

        # Then replace only the STATE_WAIT decision source.
        reentry_transformer = (
            _ReentryCoreTransformer()
        )
        tree = reentry_transformer.visit(tree)

        ast.fix_missing_locations(tree)

        fast_actual = {
            "pre_guard_removed":
                fast_transformer.pre_guard_removed,
            "risk_event_appends_removed":
                fast_transformer.risk_event_appends_removed,
            "liq_diagnostics_disabled":
                fast_transformer.liq_diagnostics_disabled,
        }

        if fast_actual != self.EXPECTED_PATCH_COUNTS:
            raise RuntimeError(
                "REENTRY FAST PATCH TARGET ERROR: "
                f"expected={self.EXPECTED_PATCH_COUNTS} "
                f"actual={fast_actual}"
            )

        if reentry_transformer.replaced != 1:
            raise RuntimeError(
                "REENTRY CORE PATCH TARGET ERROR: "
                "expected=1 "
                f"actual={reentry_transformer.replaced}"
            )

        self.patch_stats = dict(fast_actual)
        self.patch_stats[
            "reentry_core_replaced"
        ] = int(
            reentry_transformer.replaced
        )

        code = compile(
            tree,
            "<research_pipeline_v2_reentry_simulator>",
            "exec",
        )

        ns = dict(self.m.__dict__)

        ns["flatten_brackets"] = (
            self._cached_flatten_brackets
        )
        ns["maintenance_margin"] = (
            self._fast_maintenance_margin
        )
        ns["get_mark"] = self._fast_get_mark
        ns["_cross_trigger_price"] = (
            self._fast_cross_trigger_price
        )
        ns["_lab_cross_guard_scenarios"] = (
            self._cached_guard_scenarios
        )
        ns["_pipeline_reentry_decision"] = (
            self.decision_engine.decision
        )

        exec(code, ns, ns)

        fn = ns.get(
            "simulate_live_multiframe"
        )
        if not callable(fn):
            raise RuntimeError(
                "REENTRY simulator clone missing"
            )
        return fn


def build_reentry_engine(
    module,
    data,
    candidate_config,
):
    decision_engine = ReentryDecisionEngine(
        data["owner_state_book"],
        data["lower_ema_state_book"],
        candidate_config,
    )
    engine = ReentryFastEngine(
        module,
        decision_engine,
    )
    return engine, decision_engine


# =============================================================================
# SCREEN / FULL SELECTION
# =============================================================================

def screen_verdict(
    candidate,
    baseline,
):
    """
    Conservative Early Reject only.

    We do NOT early-accept a candidate.
    A candidate is rejected at screen only when the screen result is clearly
    inferior or safety degrades.

    All survivors still require FULL 35.
    """
    if int(candidate["bankrupt"]) != 0:
        return "EARLY_REJECT_BANKRUPT"

    if (
        int(candidate["liquidations"])
        > int(baseline["liquidations"])
    ):
        return "EARLY_REJECT_LIQUIDATION"

    base_eq = max(
        float(baseline["final_equity"]),
        1e-12,
    )
    eq_ratio = (
        float(candidate["final_equity"])
        / base_eq
    )
    mdd_delta = (
        float(candidate["realized_mdd_pct"])
        - float(baseline["realized_mdd_pct"])
    )

    # Very conservative: only remove obvious collapses.
    if eq_ratio < 0.20:
        return "EARLY_REJECT_EQUITY_LT_20PCT"

    if (
        eq_ratio < 0.50
        and mdd_delta <= -5.0
    ):
        return "EARLY_REJECT_EQUITY_MDD"

    return "SURVIVE_SCREEN"


def screen_score(metrics, complexity):
    """
    Used only to choose which survivors get expensive FULL 35.
    No promotion decision is made from this score.
    """
    eq = max(
        float(metrics["final_equity"]),
        1e-12,
    )
    mdd = abs(
        float(metrics["realized_mdd_pct"])
    )
    liq_penalty = (
        int(metrics["liquidations"])
        * 100.0
    )
    complexity_penalty = (
        int(complexity)
        * 0.05
    )

    return (
        math.log(eq)
        - math.log(max(mdd, 1e-9))
        - liq_penalty
        - complexity_penalty
    )


def full_verdict(metrics):
    base = SEALED_STANDARD

    survived = (
        int(metrics["bankrupt"]) == 0
    )
    liquidation_ok = (
        int(metrics["liquidations"])
        <= int(base["liquidations"])
    )
    equity_better = (
        float(metrics["final_equity"])
        > float(base["final_equity"])
    )
    mdd_better = (
        float(metrics["realized_mdd_pct"])
        > float(base["realized_mdd_pct"])
    )
    mdd_not_worse = (
        float(metrics["realized_mdd_pct"])
        >= float(base["realized_mdd_pct"])
    )

    if (
        survived
        and liquidation_ok
        and equity_better
        and mdd_not_worse
    ):
        verdict = "KEEP"

    elif (
        survived
        and liquidation_ok
        and (equity_better or mdd_better)
    ):
        verdict = "REVIEW"

    else:
        verdict = "REJECT"

    return {
        "verdict": verdict,
        "survived": survived,
        "liquidation_ok": liquidation_ok,
        "equity_better": equity_better,
        "mdd_better": mdd_better,
        "mdd_not_worse": mdd_not_worse,
    }


def run_or_resume(
    *,
    data,
    stage,
    candidate,
    scope,
    config,
    start_ts,
    end_ts,
    engine_builder,
    resume,
):
    key, path, checkpoint = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {scope} {candidate}"
        )
        return {
            "metrics": checkpoint["metrics"],
            "elapsed_seconds": float(
                checkpoint.get(
                    "elapsed_seconds",
                    0.0,
                )
            ),
            "extra": checkpoint.get(
                "extra",
                {},
            ),
            "checkpoint": str(path),
            "resumed": True,
        }

    engine, decision_engine = engine_builder()

    kwargs = make_kwargs(
        data,
        start_ts,
        end_ts,
        pullback_pct=float(
            config.get(
                "pullback_pct",
                0.0,
            )
        ),
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        f"{scope}_{candidate}",
    )

    metrics = extract_metrics(result)
    extra = {
        "decision_stats":
            dict(decision_engine.stats),
        "patch_stats":
            dict(engine.patch_stats),
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        metrics=metrics,
        elapsed_seconds=elapsed,
        extra=extra,
    )

    return {
        "metrics": metrics,
        "elapsed_seconds": float(elapsed),
        "extra": extra,
        "checkpoint": str(path),
        "resumed": False,
    }


# =============================================================================
# REENTRY STRUCTURE LAB EXECUTION
# =============================================================================

def run_reentry_structure_lab(data, args):
    stage = "reentry_structure_v1"

    # --------------------------------------------------------------
    # 0. Fail-closed parity:
    # FAST V2 STANDARD vs patched strict STANDARD reentry replica.
    # --------------------------------------------------------------
    parity_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.parity_days)
            * 86400
        ),
    )

    parity_kwargs = make_kwargs(
        data,
        parity_start,
        data["end_ts"],
        pullback_pct=0.0,
    )

    base_engine = build_fast_engine(m)
    strict_engine, strict_decision = (
        build_reentry_engine(
            m,
            data,
            STRICT_STANDARD_REENTRY_CONFIG,
        )
    )

    print(
        "BASE FAST V2 PATCH =",
        base_engine.patch_stats,
    )
    print(
        "STRICT REENTRY PATCH =",
        strict_engine.patch_stats,
    )

    base_result, base_sec = timed_run(
        base_engine.run,
        parity_kwargs,
        "REENTRY_PARITY_BASE_FAST_V2",
    )
    strict_result, strict_sec = timed_run(
        strict_engine.run,
        parity_kwargs,
        "REENTRY_PARITY_STRICT_REPLICA",
    )

    base_metrics = extract_metrics(
        base_result
    )
    strict_metrics = extract_metrics(
        strict_result
    )

    parity_ok, parity_diffs = compare_metrics(
        base_metrics,
        strict_metrics,
    )

    print_metrics(
        "REENTRY PARITY - BASE FAST V2",
        base_metrics,
        base_sec,
    )
    print_metrics(
        "REENTRY PARITY - STRICT REPLICA",
        strict_metrics,
        strict_sec,
    )

    print()
    print(
        "STRICT_REENTRY_PARITY =",
        "PASS" if parity_ok else "FAIL",
    )
    print(
        "STRICT_REENTRY_DIFFS  =",
        parity_diffs,
    )

    parity_payload = {
        "schema": "REENTRY_PARITY_V2",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "status": (
            "PASS" if parity_ok else "FAIL"
        ),
        "days": int(args.parity_days),
        "base_seconds": float(base_sec),
        "replica_seconds": float(strict_sec),
        "base_metrics": base_metrics,
        "replica_metrics": strict_metrics,
        "diffs": parity_diffs,
        "strict_decision_stats":
            strict_decision.stats,
        "data_fingerprint":
            data["fingerprint"],
    }

    atomic_write_json(
        PIPE_RESULTS
        / "reentry_parity_latest.json",
        parity_payload,
    )

    if not parity_ok:
        raise SystemExit(
            "STRICT_REENTRY_PARITY_FAIL"
        )

    # --------------------------------------------------------------
    # 1. SCREEN baseline.
    # Preserve all 35 symbols; only shorten time.
    # This avoids changing Cross portfolio geometry during screening.
    # --------------------------------------------------------------
    screen_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.screen_days)
            * 86400
        ),
    )

    baseline_config = {
        "description":
            "STANDARD V1 screen baseline",
        "requirements": {
            "12H": [],
            "1D": [],
        },
        "pullback_pct": 0.0,
        "complexity": 0,
    }

    baseline_run = run_or_resume(
        data=data,
        stage=stage,
        candidate="STANDARD_SCREEN_BASELINE",
        scope=f"SCREEN_{args.screen_days}D_35",
        config=baseline_config,
        start_ts=screen_start,
        end_ts=data["end_ts"],
        engine_builder=lambda: (
            build_reentry_engine(
                m,
                data,
                STRICT_STANDARD_REENTRY_CONFIG,
            )
        ),
        resume=args.resume,
    )

    screen_baseline = (
        baseline_run["metrics"]
    )

    print_metrics(
        f"SCREEN STANDARD {args.screen_days}D",
        screen_baseline,
        baseline_run["elapsed_seconds"],
    )

    # --------------------------------------------------------------
    # 2. Candidate screen.
    # --------------------------------------------------------------
    candidate_names = [
        x.strip().upper()
        for x in str(
            args.candidates
        ).split(",")
        if x.strip()
    ]

    unknown = [
        x
        for x in candidate_names
        if x
        not in REENTRY_STRUCTURE_CANDIDATES
    ]
    if unknown:
        raise SystemExit(
            "unknown reentry candidates: "
            + ", ".join(unknown)
        )

    screen_rows = []

    for name in candidate_names:
        config = (
            REENTRY_STRUCTURE_CANDIDATES[
                name
            ]
        )

        print()
        print("#" * 112)
        print("SCREEN CANDIDATE =", name)
        print("DESCRIPTION      =", config["description"])
        print("REQUIREMENTS     =", config["requirements"])
        print("PULLBACK         =", config["pullback_pct"])
        print("#" * 112)

        run = run_or_resume(
            data=data,
            stage=stage,
            candidate=name,
            scope=f"SCREEN_{args.screen_days}D_35",
            config=config,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            engine_builder=lambda cfg=config: (
                build_reentry_engine(
                    m,
                    data,
                    cfg,
                )
            ),
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = screen_verdict(
            metrics,
            screen_baseline,
        )
        score = screen_score(
            metrics,
            config["complexity"],
        )

        print_metrics(
            f"REENTRY SCREEN - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "SCREEN VERDICT      =",
            verdict,
        )
        print(
            "SCREEN SCORE        =",
            f"{score:.6f}",
        )

        screen_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "screen_verdict": verdict,
            "screen_score": score,
            "extra": run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
        })

    survivors = [
        row
        for row in screen_rows
        if row["screen_verdict"]
        == "SURVIVE_SCREEN"
    ]

    survivors.sort(
        key=lambda x: (
            -float(x["screen_score"]),
            int(
                x["config"]
                .get("complexity", 0)
            ),
        )
    )

    full_names = [
        row["candidate"]
        for row in survivors[
            : int(args.full_top)
        ]
    ]

    print()
    print("=" * 112)
    print("SCREEN RESULT")
    print("=" * 112)

    for i, row in enumerate(
        sorted(
            screen_rows,
            key=lambda x:
                -float(x["screen_score"]),
        ),
        1,
    ):
        print(
            f"{i:2d}. "
            f"{row['candidate']:30s} "
            f"{row['screen_verdict']:28s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}%"
        )

    print(
        "FULL_35_SELECTED =",
        full_names,
    )

    # --------------------------------------------------------------
    # 3. FULL 35 only for selected survivors.
    # --------------------------------------------------------------
    full_rows = []

    for name in full_names:
        config = (
            REENTRY_STRUCTURE_CANDIDATES[
                name
            ]
        )

        print()
        print("#" * 112)
        print("FULL 35 CANDIDATE =", name)
        print("DESCRIPTION       =", config["description"])
        print("#" * 112)

        run = run_or_resume(
            data=data,
            stage=stage,
            candidate=name,
            scope="FULL_35",
            config=config,
            start_ts=data["start_ts"],
            end_ts=data["end_ts"],
            engine_builder=lambda cfg=config: (
                build_reentry_engine(
                    m,
                    data,
                    cfg,
                )
            ),
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = full_verdict(metrics)

        print_metrics(
            f"REENTRY FULL 35 - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "FULL VERDICT =",
            verdict["verdict"],
        )

        full_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "verdict": verdict,
            "extra": run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
        })

        # Put verdict into cumulative master as a separate compact decision
        # record. It does not re-run or overwrite the raw checkpoint.
        append_master_record({
            "schema":
                "RESEARCH_DECISION_V2",
            "stage": stage,
            "candidate": name,
            "scope": "FULL_35_DECISION",
            "candidate_config": config,
            "metrics": metrics,
            "verdict": verdict,
            "source_checkpoint":
                run["checkpoint"],
            "data_fingerprint":
                data["fingerprint"],
        })

    # Detect exact duplicate FULL metrics after the fact.
    # This lets the next stage avoid carrying two candidates whose entire
    # observable simulation result is identical.
    for i, row in enumerate(full_rows):
        row["duplicate_metrics_of"] = None
        for j in range(i):
            other = full_rows[j]
            same, _ = compare_metrics(
                row["metrics"],
                other["metrics"],
            )
            if same:
                row["duplicate_metrics_of"] = (
                    other["candidate"]
                )
                break

    # --------------------------------------------------------------
    # 4. Timestamped cumulative summary + latest pointer.
    # --------------------------------------------------------------
    stamp = utc_stamp()
    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_REENTRY_STRUCTURE",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "saved_at_utc": stamp,
        "standard_source_modified": False,
        "universe": (
            "35 symbols throughout screen and full"
        ),
        "screen_days":
            int(args.screen_days),
        "full_top":
            int(args.full_top),
        "data_fingerprint":
            data["fingerprint"],
        "parity": parity_payload,
        "screen_baseline":
            screen_baseline,
        "screen_results":
            screen_rows,
        "full_selected":
            full_names,
        "full_results":
            full_rows,
        "next_stage_note": (
            "Use surviving FULL result to choose TF structure. "
            "Only then test material STATE/EVENT variants; "
            "do not mix structure and material tuning."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"reentry_structure_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "reentry_structure_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage": stage,
        "candidate": "BATCH_SUMMARY",
        "scope": "SCREEN_THEN_FULL_35",
        "result_path": str(timestamped),
        "data_fingerprint":
            data["fingerprint"],
        "full_selected": full_names,
        "full_results": [
            {
                "candidate":
                    row["candidate"],
                "metrics":
                    row["metrics"],
                "verdict":
                    row["verdict"],
                "duplicate_metrics_of":
                    row[
                        "duplicate_metrics_of"
                    ],
            }
            for row in full_rows
        ],
    })

    print()
    print("=" * 112)
    print("REENTRY STRUCTURE LAB COMPLETE")
    print("=" * 112)
    print("TIMESTAMPED RESULT =", timestamped)
    print("LATEST RESULT      =", latest)
    print("MASTER JSONL       =", MASTER_JSONL)
    print("MASTER INDEX       =", MASTER_INDEX)
    print(
        "RESUME             =",
        "ON" if args.resume else "OFF",
    )
    print()
    print(
        "NEXT = inspect FULL survivors; then build "
        "REENTRY MATERIAL LAB without re-running completed candidates."
    )

    return summary



# =============================================================================
# REENTRY MATERIAL LAB V1
# =============================================================================
#
# Structure is fixed back to STANDARD:
#   12H owner -> STANDARD 12H owner STATE remains authoritative
#   1D  owner -> STANDARD 1D owner STATE remains authoritative
#
# Candidate adds exactly ONE lower-TF native material confirmation:
#   12H owner -> 6H material
#   1D  owner -> 12H material
#
# IMPORTANT:
# - Owner opposite STATE still cancels the wait exactly as STANDARD.
# - Lower-TF material may delay re-entry only; it never cancels owner wait.
# - No pullback in this stage.
# - No two-material combinations in this stage.
# - Continuous/filter-like and trigger/event-like materials are separated.
#
# This stage is deliberately coarse. If a material survives FULL 35,
# the next stage may test at most two lower-TF conditions and/or pullback.
# =============================================================================

MATERIAL_GROUPS = {
    "STATE_FILTER": (
        "SAR",
        "MACD",
        "OBV",
    ),
    "EVENT_TRIGGER": (
        "STOCH",
        "DIV",
        "SHAPE",
    ),
}

MATERIAL_CANDIDATES = {}

for _group, _materials in MATERIAL_GROUPS.items():
    for _material in _materials:
        _name = f"M_{_group}_{_material}"
        MATERIAL_CANDIDATES[_name] = {
            "description": (
                f"STANDARD owner STATE + native {_material} lower-TF "
                "confirmation. 12H owner uses 6H; 1D owner uses 12H."
            ),
            "group": _group,
            "requirements": {
                "12H": [
                    {
                        "tf": "6H",
                        "material": _material,
                    }
                ],
                "1D": [
                    {
                        "tf": "12H",
                        "material": _material,
                    }
                ],
            },
            "pullback_pct": 0.0,
            "complexity": 1,
        }


def _native_material_signal_fn(material):
    material = str(material).upper()

    def _fn(df, tf):
        return tmpl.material_signal(
            df,
            str(tf).upper(),
            material,
        )

    return _fn


def build_material_books(data):
    """
    Build each native material signal book once per process.
    Only 6H and 12H are required in this coarse material stage.
    """
    books = {}
    meta = {}

    for group, materials in MATERIAL_GROUPS.items():
        for material in materials:
            print(
                f"building MATERIAL book "
                f"group={group} material={material} "
                "frames=6H,12H ..."
            )

            book, meta_rows = (
                m.build_research_six_frame_signal_book(
                    cache=data["cache"],
                    signal_fn=_native_material_signal_fn(
                        material
                    ),
                    frames=("6H", "12H"),
                    data_mode="CACHE_MATERIALS",
                )
            )

            books[material] = book

            # Observable density is recorded; no assumptions are made
            # from it during candidate verdict selection.
            total_rows = 0
            nonzero_rows = 0

            for _key, (_ts_arr, _sig_arr) in book.items():
                sig = np.asarray(
                    _sig_arr,
                    dtype=np.int8,
                )
                total_rows += int(len(sig))
                nonzero_rows += int(
                    np.count_nonzero(sig)
                )

            meta[material] = {
                "group": group,
                "book_keys": int(len(book)),
                "rows": int(total_rows),
                "nonzero_rows": int(nonzero_rows),
                "nonzero_pct": (
                    float(nonzero_rows)
                    / float(total_rows)
                    * 100.0
                    if total_rows
                    else 0.0
                ),
                "builder_meta_rows": int(
                    len(meta_rows)
                    if meta_rows is not None
                    else 0
                ),
            }

    return books, meta


class MaterialReentryDecisionEngine:
    """
    STANDARD owner STATE remains the source of trend validity.

    If owner trend is same:
      each lower-TF native material requirement must equal wait_side.

    If lower material is 0 or opposite:
      keep waiting; DO NOT cancel the wait.

    Only owner opposite STATE cancels the wait.
    """

    def __init__(
        self,
        owner_state_book,
        material_books,
        config,
    ):
        self.owner = SignalBookLookup(
            owner_state_book
        )
        self.material = {
            str(name).upper(): SignalBookLookup(
                book
            )
            for name, book
            in material_books.items()
        }
        self.config = dict(config)

        self.stats = {
            "calls": 0,
            "owner_same": 0,
            "owner_opposite_cancel": 0,
            "owner_zero": 0,
            "material_blocks": 0,
            "material_zero_blocks": 0,
            "material_opposite_blocks": 0,
            "eligible": 0,
            "blocks_by_requirement": {},
        }

    def decision(
        self,
        symbol,
        owner_tf,
        wait_side,
        ts,
    ):
        self.stats["calls"] += 1

        symbol = str(symbol).upper()
        owner_tf = str(owner_tf).upper()
        wait_side = int(wait_side)
        ts = int(ts)

        if wait_side not in (-1, 1):
            raise RuntimeError(
                f"bad wait_side: {wait_side}"
            )

        owner_decision = self.owner.latest(
            symbol,
            owner_tf,
            ts,
        )

        if owner_decision == -wait_side:
            self.stats[
                "owner_opposite_cancel"
            ] += 1
            return -wait_side

        if owner_decision == 0:
            self.stats["owner_zero"] += 1
            return 0

        if owner_decision != wait_side:
            return 0

        self.stats["owner_same"] += 1

        requirements = (
            self.config
            .get("requirements", {})
            .get(owner_tf, [])
        )

        for req in requirements:
            lower_tf = str(
                req["tf"]
            ).upper()
            material = str(
                req["material"]
            ).upper()

            lookup = self.material.get(
                material
            )
            if lookup is None:
                raise RuntimeError(
                    "material book missing: "
                    f"{material}"
                )

            lower_decision = lookup.latest(
                symbol,
                lower_tf,
                ts,
            )

            if lower_decision != wait_side:
                self.stats[
                    "material_blocks"
                ] += 1

                if lower_decision == 0:
                    self.stats[
                        "material_zero_blocks"
                    ] += 1
                elif lower_decision == -wait_side:
                    self.stats[
                        "material_opposite_blocks"
                    ] += 1

                key = (
                    f"{owner_tf}->{lower_tf}:"
                    f"{material}"
                )
                by_req = self.stats[
                    "blocks_by_requirement"
                ]
                by_req[key] = (
                    int(by_req.get(key, 0))
                    + 1
                )
                return 0

        self.stats["eligible"] += 1
        return wait_side


def build_material_reentry_engine(
    module,
    data,
    material_books,
    candidate_config,
):
    decision_engine = (
        MaterialReentryDecisionEngine(
            data["owner_state_book"],
            material_books,
            candidate_config,
        )
    )
    engine = ReentryFastEngine(
        module,
        decision_engine,
    )
    return engine, decision_engine


def _same_fingerprint(a, b):
    if not isinstance(a, dict):
        return False
    if not isinstance(b, dict):
        return False

    ah = str(
        a.get("fingerprint_hash", "")
    )
    bh = str(
        b.get("fingerprint_hash", "")
    )

    if ah and bh:
        return ah == bh

    # Defensive fallback for older summary format.
    keys = (
        "symbol_count",
        "symbols",
        "cache_rows",
        "cache_ts_min",
        "cache_ts_max",
        "start_ts",
        "end_ts",
        "stress_rows",
    )
    return all(
        a.get(k) == b.get(k)
        for k in keys
    )


def load_prior_screen_baseline(
    data,
    screen_days,
):
    """
    Reuse the completed REENTRY STRUCTURE screen baseline when it is
    exactly compatible. This avoids knowingly re-running STANDARD screen.
    """
    path = (
        PIPE_RESULTS
        / "reentry_structure_latest.json"
    )

    if not path.exists():
        return None

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None

    if int(
        payload.get("screen_days", -1)
    ) != int(screen_days):
        return None

    if not _same_fingerprint(
        payload.get(
            "data_fingerprint",
            {}
        ),
        data["fingerprint"],
    ):
        return None

    baseline = payload.get(
        "screen_baseline"
    )

    if not isinstance(
        baseline,
        dict,
    ):
        return None

    required = {
        "final_equity",
        "compound_pct",
        "realized_mdd_pct",
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
    }

    if not required.issubset(
        baseline.keys()
    ):
        return None

    print(
        "[PIPEV2][REUSE] compatible prior "
        "STANDARD screen baseline =",
        path,
    )
    return baseline


def run_reentry_material_lab(
    data,
    args,
):
    # --------------------------------------------------------------
    # 0. Build shared material books once.
    # --------------------------------------------------------------
    with Heartbeat(
        "BUILD_REENTRY_MATERIAL_BOOKS"
    ):
        material_books, material_meta = (
            build_material_books(
                data
            )
        )

    print()
    print("=" * 112)
    print("REENTRY MATERIAL BOOK META")
    print("=" * 112)

    for material in (
        "SAR",
        "MACD",
        "OBV",
        "STOCH",
        "DIV",
        "SHAPE",
    ):
        meta = material_meta[material]
        print(
            f"{material:8s} "
            f"group={meta['group']:13s} "
            f"nonzero={meta['nonzero_rows']:8d}/"
            f"{meta['rows']:8d} "
            f"({meta['nonzero_pct']:.4f}%)"
        )

    # --------------------------------------------------------------
    # 1. Fail-closed parity of unchanged STANDARD reentry patch.
    # This validates the simulator patch in this file/build before
    # material candidates are allowed to run.
    # --------------------------------------------------------------
    parity_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.parity_days)
            * 86400
        ),
    )

    parity_kwargs = make_kwargs(
        data,
        parity_start,
        data["end_ts"],
        pullback_pct=0.0,
    )

    base_engine = build_fast_engine(m)
    strict_engine, strict_decision = (
        build_reentry_engine(
            m,
            data,
            STRICT_STANDARD_REENTRY_CONFIG,
        )
    )

    print(
        "BASE FAST V2 PATCH =",
        base_engine.patch_stats,
    )
    print(
        "STRICT REENTRY PATCH =",
        strict_engine.patch_stats,
    )

    base_result, base_sec = timed_run(
        base_engine.run,
        parity_kwargs,
        "MATERIAL_PARITY_BASE_FAST_V2",
    )
    strict_result, strict_sec = timed_run(
        strict_engine.run,
        parity_kwargs,
        "MATERIAL_PARITY_STRICT_REPLICA",
    )

    base_metrics = extract_metrics(
        base_result
    )
    strict_metrics = extract_metrics(
        strict_result
    )

    parity_ok, parity_diffs = compare_metrics(
        base_metrics,
        strict_metrics,
    )

    print()
    print(
        "STRICT_REENTRY_PARITY =",
        "PASS" if parity_ok else "FAIL",
    )
    print(
        "STRICT_REENTRY_DIFFS  =",
        parity_diffs,
    )

    parity_payload = {
        "schema":
            "REENTRY_MATERIAL_PARITY_V2",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "status":
            "PASS" if parity_ok else "FAIL",
        "days": int(args.parity_days),
        "base_seconds": float(base_sec),
        "replica_seconds": float(
            strict_sec
        ),
        "base_metrics": base_metrics,
        "replica_metrics":
            strict_metrics,
        "diffs": parity_diffs,
        "strict_decision_stats":
            strict_decision.stats,
        "data_fingerprint":
            data["fingerprint"],
    }

    atomic_write_json(
        PIPE_RESULTS
        / "reentry_material_parity_latest.json",
        parity_payload,
    )

    if not parity_ok:
        raise SystemExit(
            "STRICT_REENTRY_PARITY_FAIL"
        )

    # --------------------------------------------------------------
    # 2. SCREEN baseline.
    # All 35 symbols remain active. Prefer exact reuse of completed
    # structure-stage screen baseline.
    # --------------------------------------------------------------
    screen_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.screen_days)
            * 86400
        ),
    )

    screen_baseline = (
        load_prior_screen_baseline(
            data,
            args.screen_days,
        )
    )

    baseline_source = (
        "REUSED_REENTRY_STRUCTURE"
        if screen_baseline is not None
        else "RUN_NOW"
    )

    if screen_baseline is None:
        baseline_config = {
            "description":
                "STANDARD V1 material-screen baseline",
            "requirements": {
                "12H": [],
                "1D": [],
            },
            "pullback_pct": 0.0,
            "complexity": 0,
        }

        baseline_run = run_or_resume(
            data=data,
            stage="reentry_material_v1",
            candidate=(
                "STANDARD_MATERIAL_SCREEN_BASELINE"
            ),
            scope=(
                f"SCREEN_{args.screen_days}D_35"
            ),
            config=baseline_config,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            engine_builder=lambda: (
                build_reentry_engine(
                    m,
                    data,
                    STRICT_STANDARD_REENTRY_CONFIG,
                )
            ),
            resume=args.resume,
        )

        screen_baseline = (
            baseline_run["metrics"]
        )

    print_metrics(
        f"MATERIAL SCREEN STANDARD "
        f"{args.screen_days}D "
        f"source={baseline_source}",
        screen_baseline,
    )

    # --------------------------------------------------------------
    # 3. Candidate screen, GROUPED by semantics.
    # --------------------------------------------------------------
    candidate_names = [
        x.strip().upper()
        for x in str(
            args.material_candidates
        ).split(",")
        if x.strip()
    ]

    unknown = [
        x
        for x in candidate_names
        if x not in MATERIAL_CANDIDATES
    ]

    if unknown:
        raise SystemExit(
            "unknown material candidates: "
            + ", ".join(unknown)
        )

    screen_rows = []

    for name in candidate_names:
        config = MATERIAL_CANDIDATES[
            name
        ]
        group = str(config["group"])

        print()
        print("#" * 112)
        print("MATERIAL SCREEN CANDIDATE =", name)
        print("GROUP                     =", group)
        print("DESCRIPTION               =", config["description"])
        print("REQUIREMENTS              =", config["requirements"])
        print("#" * 112)

        run = run_or_resume(
            data=data,
            stage=(
                f"reentry_material_"
                f"{group.lower()}_v1"
            ),
            candidate=name,
            scope=(
                f"SCREEN_{args.screen_days}D_35"
            ),
            config=config,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            engine_builder=(
                lambda cfg=config:
                    build_material_reentry_engine(
                        m,
                        data,
                        material_books,
                        cfg,
                    )
            ),
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = screen_verdict(
            metrics,
            screen_baseline,
        )
        score = screen_score(
            metrics,
            config["complexity"],
        )

        print_metrics(
            f"REENTRY MATERIAL SCREEN - "
            f"{name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "SCREEN GROUP        =",
            group,
        )
        print(
            "SCREEN VERDICT      =",
            verdict,
        )
        print(
            "SCREEN SCORE        =",
            f"{score:.6f}",
        )

        screen_rows.append({
            "candidate": name,
            "group": group,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "screen_verdict":
                verdict,
            "screen_score":
                score,
            "extra":
                run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
        })

    # --------------------------------------------------------------
    # 4. Pick FULL candidates separately per semantic group.
    # No STATE_FILTER vs EVENT_TRIGGER cross-ranking here.
    # --------------------------------------------------------------
    selected = []

    for group in MATERIAL_GROUPS:
        rows = [
            row
            for row in screen_rows
            if (
                row["group"] == group
                and
                row["screen_verdict"]
                == "SURVIVE_SCREEN"
            )
        ]

        rows.sort(
            key=lambda x: (
                -float(
                    x["screen_score"]
                ),
                str(
                    x["candidate"]
                ),
            )
        )

        selected.extend(
            row["candidate"]
            for row in rows[
                : int(
                    args.full_top_per_group
                )
            ]
        )

    print()
    print("=" * 112)
    print("REENTRY MATERIAL SCREEN RESULT")
    print("=" * 112)

    for group in MATERIAL_GROUPS:
        print()
        print(
            f"[{group}]"
        )
        rows = [
            row
            for row in screen_rows
            if row["group"] == group
        ]
        rows.sort(
            key=lambda x:
                -float(
                    x["screen_score"]
                )
        )

        for i, row in enumerate(
            rows,
            1,
        ):
            print(
                f"{i:2d}. "
                f"{row['candidate']:34s} "
                f"{row['screen_verdict']:28s} "
                f"equity=${row['metrics']['final_equity']:,.2f} "
                f"MDD={row['metrics']['realized_mdd_pct']:+.4f}%"
            )

    print()
    print(
        "FULL_35_SELECTED =",
        selected,
    )

    # --------------------------------------------------------------
    # 5. FULL 35 only for selected group survivors.
    # --------------------------------------------------------------
    full_rows = []

    for name in selected:
        config = MATERIAL_CANDIDATES[
            name
        ]
        group = str(config["group"])

        print()
        print("#" * 112)
        print("MATERIAL FULL 35 CANDIDATE =", name)
        print("GROUP                      =", group)
        print("DESCRIPTION                =", config["description"])
        print("#" * 112)

        run = run_or_resume(
            data=data,
            stage=(
                f"reentry_material_"
                f"{group.lower()}_v1"
            ),
            candidate=name,
            scope="FULL_35",
            config=config,
            start_ts=data["start_ts"],
            end_ts=data["end_ts"],
            engine_builder=(
                lambda cfg=config:
                    build_material_reentry_engine(
                        m,
                        data,
                        material_books,
                        cfg,
                    )
            ),
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = full_verdict(
            metrics
        )

        print_metrics(
            f"REENTRY MATERIAL FULL 35 "
            f"- {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "FULL VERDICT =",
            verdict["verdict"],
        )

        full_rows.append({
            "candidate": name,
            "group": group,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "verdict": verdict,
            "extra": run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
            "duplicate_metrics_of":
                None,
        })

        append_master_record({
            "schema":
                "RESEARCH_DECISION_V2",
            "stage":
                "reentry_material_v1",
            "candidate": name,
            "scope":
                "FULL_35_DECISION",
            "candidate_config":
                config,
            "metrics":
                metrics,
            "verdict":
                verdict,
            "source_checkpoint":
                run["checkpoint"],
            "data_fingerprint":
                data["fingerprint"],
        })

    # Exact metric duplicate detection after FULL.
    for i, row in enumerate(
        full_rows
    ):
        for j in range(i):
            other = full_rows[j]
            same, _ = compare_metrics(
                row["metrics"],
                other["metrics"],
            )
            if same:
                row[
                    "duplicate_metrics_of"
                ] = other["candidate"]
                break

    # --------------------------------------------------------------
    # 6. Stage decision for next automation step.
    # --------------------------------------------------------------
    keep_review = [
        row
        for row in full_rows
        if (
            row["verdict"]["verdict"]
            in ("KEEP", "REVIEW")
            and
            row[
                "duplicate_metrics_of"
            ] is None
        )
    ]

    if keep_review:
        next_action = (
            "REENTRY_MATERIAL_PAIR_OR_PULLBACK_LAB"
        )
        next_note = (
            "At least one single native material survived FULL 35. "
            "Next: coarse max-two-condition combinations and optional "
            "pullback; keep STANDARD owner STATE authoritative."
        )
    else:
        next_action = (
            "TRAILING_LAB"
        )
        next_note = (
            "No single lower-TF material survived FULL 35. "
            "Do not compound failed reentry filters; keep STANDARD "
            "reentry and move to Trailing Lab."
        )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_REENTRY_MATERIAL",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "universe":
            "35 symbols throughout screen and full",
        "screen_days":
            int(args.screen_days),
        "full_top_per_group":
            int(
                args.full_top_per_group
            ),
        "baseline_source":
            baseline_source,
        "data_fingerprint":
            data["fingerprint"],
        "material_meta":
            material_meta,
        "parity":
            parity_payload,
        "screen_baseline":
            screen_baseline,
        "screen_results":
            screen_rows,
        "full_selected":
            selected,
        "full_results":
            full_rows,
        "next_action":
            next_action,
        "next_note":
            next_note,
    }

    timestamped = (
        PIPE_RESULTS
        / f"reentry_material_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "reentry_material_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            "reentry_material_v1",
        "candidate":
            "BATCH_SUMMARY",
        "scope":
            "GROUPED_SCREEN_THEN_FULL_35",
        "result_path":
            str(timestamped),
        "data_fingerprint":
            data["fingerprint"],
        "full_selected":
            selected,
        "next_action":
            next_action,
        "full_results": [
            {
                "candidate":
                    row["candidate"],
                "group":
                    row["group"],
                "metrics":
                    row["metrics"],
                "verdict":
                    row["verdict"],
                "duplicate_metrics_of":
                    row[
                        "duplicate_metrics_of"
                    ],
            }
            for row in full_rows
        ],
    })

    print()
    print("=" * 112)
    print("REENTRY MATERIAL LAB COMPLETE")
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "MASTER JSONL       =",
        MASTER_JSONL,
    )
    print(
        "MASTER INDEX       =",
        MASTER_INDEX,
    )
    print(
        "NEXT ACTION        =",
        next_action,
    )
    print(
        "NEXT NOTE          =",
        next_note,
    )

    return summary


# =============================================================================
# TRAILING LAB V1
# =============================================================================
#
# STANDARD:
#   start=4.0%, callback=2.0%
#
# Coarse neighborhood only. No fine-grid optimization.
# Screen keeps all 35 symbols and shortens only the time window.
# FULL 35 is run only for the strongest screen survivors.
# =============================================================================

TRAILING_CANDIDATES = {
    "T1_TIGHT_BOTH_3_1P5": {
        "description": "Earlier activation + tighter callback.",
        "trailing_start_pct": 3.0,
        "trailing_callback_pct": 1.5,
        "distance_from_standard": 1.5,
    },
    "T2_EARLY_START_3_2": {
        "description": "Earlier activation; STANDARD callback.",
        "trailing_start_pct": 3.0,
        "trailing_callback_pct": 2.0,
        "distance_from_standard": 1.0,
    },
    "T3_TIGHT_CALLBACK_4_1P5": {
        "description": "STANDARD activation; tighter callback.",
        "trailing_start_pct": 4.0,
        "trailing_callback_pct": 1.5,
        "distance_from_standard": 0.5,
    },
    "T4_WIDE_CALLBACK_4_2P5": {
        "description": "STANDARD activation; wider callback.",
        "trailing_start_pct": 4.0,
        "trailing_callback_pct": 2.5,
        "distance_from_standard": 0.5,
    },
    "T5_LATE_START_5_2": {
        "description": "Later activation; STANDARD callback.",
        "trailing_start_pct": 5.0,
        "trailing_callback_pct": 2.0,
        "distance_from_standard": 1.0,
    },
    "T6_LOOSE_BOTH_5_2P5": {
        "description": "Later activation + wider callback.",
        "trailing_start_pct": 5.0,
        "trailing_callback_pct": 2.5,
        "distance_from_standard": 1.5,
    },
    "T7_WIDE_6_3": {
        "description": "Broad loose trailing neighborhood check.",
        "trailing_start_pct": 6.0,
        "trailing_callback_pct": 3.0,
        "distance_from_standard": 3.0,
    },
}

TRAILING_STANDARD_CONFIG = {
    "description": "SEALED STANDARD trailing baseline.",
    "trailing_start_pct": 4.0,
    "trailing_callback_pct": 2.0,
    "distance_from_standard": 0.0,
}


def make_trailing_kwargs(
    data,
    start_ts,
    end_ts,
    trailing_start_pct,
    trailing_callback_pct,
):
    """
    STANDARD everything except trailing start/callback.
    STATE re-entry remains STANDARD owner STATE with pullback=0.
    """
    return {
        "signal_book": data["event_book"],
        "symbols": data["symbols"],
        "snapshot": data["snapshot"],
        "market": data["market"],
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "leverage_map": LEVERAGE,
        "frames": OWNER_FRAMES,
        "active_count": 35,
        "start_equity": 10000.0,
        "collect_trade_log": False,
        "trailing_start_pct": float(trailing_start_pct),
        "trailing_callback_pct": float(trailing_callback_pct),
        "state_reentry_book": data["owner_state_book"],
        "state_reentry_pullback_pct": 0.0,
    }


def run_or_resume_trailing(
    *,
    data,
    stage,
    candidate,
    scope,
    config,
    start_ts,
    end_ts,
    resume,
):
    key, path, checkpoint = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {scope} {candidate}"
        )
        return {
            "metrics": checkpoint["metrics"],
            "elapsed_seconds": float(
                checkpoint.get("elapsed_seconds", 0.0)
            ),
            "extra": checkpoint.get("extra", {}),
            "checkpoint": str(path),
            "resumed": True,
        }

    engine = build_fast_engine(m)

    kwargs = make_trailing_kwargs(
        data=data,
        start_ts=start_ts,
        end_ts=end_ts,
        trailing_start_pct=config["trailing_start_pct"],
        trailing_callback_pct=config["trailing_callback_pct"],
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        f"{scope}_{candidate}",
    )

    metrics = extract_metrics(result)
    extra = {
        "patch_stats": dict(engine.patch_stats),
        "trailing_start_pct": float(
            config["trailing_start_pct"]
        ),
        "trailing_callback_pct": float(
            config["trailing_callback_pct"]
        ),
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        metrics=metrics,
        elapsed_seconds=elapsed,
        extra=extra,
    )

    return {
        "metrics": metrics,
        "elapsed_seconds": float(elapsed),
        "extra": extra,
        "checkpoint": str(path),
        "resumed": False,
    }


def trailing_screen_score(metrics, baseline, config):
    """
    Ranking only; never used to promote directly.

    Prefer:
      1) no liquidation
      2) higher equity
      3) lower |MDD|
      4) smaller deviation from STANDARD when performance is close.
    """
    eq = max(float(metrics["final_equity"]), 1e-12)
    mdd = max(abs(float(metrics["realized_mdd_pct"])), 1e-9)
    liq = int(metrics["liquidations"])
    distance = float(config.get("distance_from_standard", 0.0))

    # Tiny simplicity penalty; performance dominates.
    return (
        math.log(eq)
        - math.log(mdd)
        - liq * 100.0
        - distance * 0.015
    )


def trailing_full_delta(metrics):
    base = SEALED_STANDARD
    return {
        "final_equity_delta": (
            float(metrics["final_equity"])
            - float(base["final_equity"])
        ),
        "mdd_delta_pp": (
            float(metrics["realized_mdd_pct"])
            - float(base["realized_mdd_pct"])
        ),
        "liquidation_delta": (
            int(metrics["liquidations"])
            - int(base["liquidations"])
        ),
        "entries_delta": (
            int(metrics["entries"])
            - int(base["entries"])
        ),
        "control_sl_closes_delta": (
            int(metrics["control_sl_closes"])
            - int(base["control_sl_closes"])
        ),
        "trailing_closes_delta": (
            int(metrics["trailing_closes"])
            - int(base["trailing_closes"])
        ),
        "state_reentries_delta": (
            int(metrics["state_reentries"])
            - int(base["state_reentries"])
        ),
    }


def run_trailing_lab(data, args):
    stage = "trailing_v1"

    # --------------------------------------------------------------
    # 0. Screen baseline.
    # Reuse previously completed compatible STANDARD 730d baseline
    # whenever possible. Otherwise run it once and checkpoint it.
    # --------------------------------------------------------------
    screen_start = max(
        int(data["start_ts"]),
        int(data["end_ts"] - int(args.screen_days) * 86400),
    )

    screen_baseline = load_prior_screen_baseline(
        data,
        args.screen_days,
    )
    baseline_source = "REUSED_REENTRY_STRUCTURE"

    if screen_baseline is None:
        baseline_run = run_or_resume_trailing(
            data=data,
            stage=stage,
            candidate="STANDARD_TRAILING_BASELINE",
            scope=f"SCREEN_{args.screen_days}D_35",
            config=TRAILING_STANDARD_CONFIG,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            resume=args.resume,
        )
        screen_baseline = baseline_run["metrics"]
        baseline_source = "TRAILING_STAGE_CHECKPOINT"

    print_metrics(
        f"TRAILING SCREEN STANDARD {args.screen_days}D "
        f"source={baseline_source}",
        screen_baseline,
    )

    # --------------------------------------------------------------
    # 1. Coarse screen.
    # --------------------------------------------------------------
    candidate_names = [
        x.strip().upper()
        for x in str(args.trailing_candidates).split(",")
        if x.strip()
    ]

    unknown = [
        x for x in candidate_names
        if x not in TRAILING_CANDIDATES
    ]
    if unknown:
        raise SystemExit(
            "unknown trailing candidates: "
            + ", ".join(unknown)
        )

    screen_rows = []

    for name in candidate_names:
        config = TRAILING_CANDIDATES[name]

        start_pct = float(config["trailing_start_pct"])
        callback_pct = float(config["trailing_callback_pct"])

        if callback_pct >= start_pct:
            raise RuntimeError(
                f"invalid trailing candidate {name}: "
                f"callback={callback_pct} >= start={start_pct}"
            )

        print()
        print("#" * 112)
        print("TRAILING SCREEN CANDIDATE =", name)
        print("DESCRIPTION               =", config["description"])
        print("START / CALLBACK          =", f"{start_pct}% / {callback_pct}%")
        print("#" * 112)

        run = run_or_resume_trailing(
            data=data,
            stage=stage,
            candidate=name,
            scope=f"SCREEN_{args.screen_days}D_35",
            config=config,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = screen_verdict(metrics, screen_baseline)
        score = trailing_screen_score(
            metrics,
            screen_baseline,
            config,
        )

        print_metrics(
            f"TRAILING SCREEN - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print("SCREEN VERDICT =", verdict)
        print("SCREEN SCORE   =", f"{score:.6f}")

        screen_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds": run["elapsed_seconds"],
            "screen_verdict": verdict,
            "screen_score": score,
            "checkpoint": run["checkpoint"],
            "resumed": run["resumed"],
        })

    survivors = [
        row for row in screen_rows
        if row["screen_verdict"] == "SURVIVE_SCREEN"
    ]

    survivors.sort(
        key=lambda row: (
            -float(row["screen_score"]),
            float(
                row["config"].get(
                    "distance_from_standard",
                    0.0,
                )
            ),
        )
    )

    full_names = [
        row["candidate"]
        for row in survivors[: int(args.trailing_full_top)]
    ]

    print()
    print("=" * 112)
    print("TRAILING SCREEN RESULT")
    print("=" * 112)

    ranked = sorted(
        screen_rows,
        key=lambda row: -float(row["screen_score"]),
    )
    for i, row in enumerate(ranked, 1):
        cfg = row["config"]
        print(
            f"{i:2d}. {row['candidate']:28s} "
            f"{cfg['trailing_start_pct']:.1f}/"
            f"{cfg['trailing_callback_pct']:.1f} "
            f"{row['screen_verdict']:28s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}%"
        )

    print("FULL_35_SELECTED =", full_names)

    # --------------------------------------------------------------
    # 2. FULL 35 only for selected survivors.
    # --------------------------------------------------------------
    full_rows = []

    for name in full_names:
        config = TRAILING_CANDIDATES[name]

        print()
        print("#" * 112)
        print("TRAILING FULL 35 CANDIDATE =", name)
        print(
            "START / CALLBACK           =",
            f"{config['trailing_start_pct']}% / "
            f"{config['trailing_callback_pct']}%",
        )
        print("#" * 112)

        run = run_or_resume_trailing(
            data=data,
            stage=stage,
            candidate=name,
            scope="FULL_35",
            config=config,
            start_ts=data["start_ts"],
            end_ts=data["end_ts"],
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = full_verdict(metrics)
        delta = trailing_full_delta(metrics)

        print_metrics(
            f"TRAILING FULL 35 - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print("FULL VERDICT =", verdict["verdict"])
        print(
            "VS STANDARD   =",
            f"equity {delta['final_equity_delta']:+,.2f}, "
            f"MDD {delta['mdd_delta_pp']:+.6f}pp, "
            f"liq {delta['liquidation_delta']:+d}",
        )

        full_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds": run["elapsed_seconds"],
            "verdict": verdict,
            "delta_vs_standard": delta,
            "checkpoint": run["checkpoint"],
            "resumed": run["resumed"],
        })

        append_master_record({
            "schema": "RESEARCH_DECISION_V2",
            "stage": stage,
            "candidate": name,
            "scope": "FULL_35_DECISION",
            "candidate_config": config,
            "metrics": metrics,
            "verdict": verdict,
            "delta_vs_standard": delta,
            "source_checkpoint": run["checkpoint"],
            "data_fingerprint": data["fingerprint"],
        })

    # --------------------------------------------------------------
    # 3. Keep only genuine FULL candidates for later integration.
    # REVIEW is retained as a challenger, not promoted.
    # If none survive, STANDARD 4/2 remains the trailing champion.
    # --------------------------------------------------------------
    keepers = [
        row["candidate"]
        for row in full_rows
        if row["verdict"]["verdict"] in ("KEEP", "REVIEW")
    ]

    trailing_champion = (
        keepers[0]
        if keepers
        else "STANDARD_4_2"
    )

    # Exact duplicate result detection.
    for i, row in enumerate(full_rows):
        row["duplicate_metrics_of"] = None
        for j in range(i):
            other = full_rows[j]
            same, _ = compare_metrics(
                row["metrics"],
                other["metrics"],
            )
            if same:
                row["duplicate_metrics_of"] = other["candidate"]
                break

    stamp = utc_stamp()
    summary = {
        "schema": "RESEARCH_PIPELINE_V2_TRAILING_LAB",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "saved_at_utc": stamp,
        "standard_source_modified": False,
        "standard_trailing": {
            "start_pct": 4.0,
            "callback_pct": 2.0,
        },
        "screen_days": int(args.screen_days),
        "trailing_full_top": int(args.trailing_full_top),
        "data_fingerprint": data["fingerprint"],
        "screen_baseline_source": baseline_source,
        "screen_baseline": screen_baseline,
        "screen_results": screen_rows,
        "full_selected": full_names,
        "full_results": full_rows,
        "keepers_for_later_integration": keepers,
        "trailing_champion_for_now": trailing_champion,
        "next_action": "CONTROL_SL_LAB",
        "next_note": (
            "Carry KEEP/REVIEW trailing challengers forward; "
            "otherwise keep STANDARD 4/2. Do not fine-tune trailing "
            "before Control SL and later integration/robustness."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"trailing_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "trailing_latest.json"
    )

    atomic_write_json(timestamped, summary)
    atomic_write_json(latest, summary)

    append_master_record({
        "schema": "RESEARCH_STAGE_SUMMARY_V2",
        "stage": stage,
        "candidate": "BATCH_SUMMARY",
        "scope": "SCREEN_THEN_FULL_35",
        "result_path": str(timestamped),
        "data_fingerprint": data["fingerprint"],
        "full_selected": full_names,
        "keepers_for_later_integration": keepers,
        "trailing_champion_for_now": trailing_champion,
        "next_action": "CONTROL_SL_LAB",
    })

    print()
    print("=" * 112)
    print("TRAILING LAB COMPLETE")
    print("=" * 112)
    print("TIMESTAMPED RESULT =", timestamped)
    print("LATEST RESULT      =", latest)
    print("MASTER JSONL       =", MASTER_JSONL)
    print("MASTER INDEX       =", MASTER_INDEX)
    print("TRAILING KEEPERS   =", keepers)
    print("CHAMPION FOR NOW   =", trailing_champion)
    print("NEXT ACTION        = CONTROL_SL_LAB")

    return summary




# =============================================================================
# CONTROL SL LAB V1
# =============================================================================
#
# STANDARD Control SL:
#   - evaluate at previous confirmed 1H mark
#   - history must be ready
#   - breach when baseline cushion <= 0 OR stress min cushion <= 0
#   - close the position that maximizes remaining min stress cushion
#   - continue until safe
#   - preserve owner TF and enter STATE_WAIT
#
# This lab changes ONLY the Control-SL stress-cushion trigger boundary.
# Everything else in the close-selection loop remains STANDARD.
#
# Dimensionless trigger:
#
#     stress_ratio = min_cushion / baseline_cushion
#
# STANDARD threshold = 0.00
#
# +0.05 / +0.10:
#     proactive; close before the stress cushion reaches zero.
#
# -0.05 / -0.10:
#     tolerant; allow a shallow hypothetical stress deficit before closing.
#
# OFF:
#     ablation only. Actual liquidation simulation remains enabled.
#
# IMPORTANT:
#   Control SL candidates are tested with STANDARD trailing 4/2.
#   The strong Trailing challengers are carried forward separately and are
#   combined only in the later Integration Lab.
# =============================================================================

CONTROL_SL_STANDARD_CONFIG = {
    "description": "Exact STANDARD Control SL trigger.",
    "enabled": True,
    "stress_ratio_threshold": 0.0,
    "complexity": 0,
}

CONTROL_SL_CANDIDATES = {
    "C1_PROACTIVE_10PCT": {
        "description": (
            "Proactive Control SL: require stress cushion to remain "
            "above 10% of baseline cushion."
        ),
        "enabled": True,
        "stress_ratio_threshold": 0.10,
        "complexity": 1,
    },
    "C2_PROACTIVE_5PCT": {
        "description": (
            "Proactive Control SL: require stress cushion to remain "
            "above 5% of baseline cushion."
        ),
        "enabled": True,
        "stress_ratio_threshold": 0.05,
        "complexity": 1,
    },
    "C3_TOLERANT_5PCT": {
        "description": (
            "Tolerant Control SL: allow stress cushion down to "
            "-5% of baseline cushion."
        ),
        "enabled": True,
        "stress_ratio_threshold": -0.05,
        "complexity": 1,
    },
    "C4_TOLERANT_10PCT": {
        "description": (
            "Tolerant Control SL: allow stress cushion down to "
            "-10% of baseline cushion."
        ),
        "enabled": True,
        "stress_ratio_threshold": -0.10,
        "complexity": 1,
    },
    "C5_CONTROL_SL_OFF": {
        "description": (
            "Ablation: Control SL disabled; Entry Gate and actual "
            "liquidation simulation remain enabled."
        ),
        "enabled": False,
        "stress_ratio_threshold": None,
        "complexity": 1,
    },
}


class ControlSLPolicy:
    def __init__(self, config):
        self.config = dict(config)
        self.enabled = bool(
            self.config.get("enabled", True)
        )
        raw = self.config.get(
            "stress_ratio_threshold",
            0.0,
        )
        self.threshold = (
            None if raw is None else float(raw)
        )
        self.stats = {
            "breach_calls": 0,
            "breach_true": 0,
            "breach_false": 0,
            "baseline_nonpositive": 0,
        }

    def breach(self, control_eval):
        """
        Replacement for ONLY:
            not bool(control_eval["safe"])
        inside the Control-SL branch.

        STANDARD parity when threshold == 0:
            not safe
            == baseline_cushion <= 0 OR min_cushion <= 0
        """
        self.stats["breach_calls"] += 1

        baseline = float(
            control_eval["baseline_cushion"]
        )
        minimum = float(
            control_eval["min_cushion"]
        )

        if baseline <= 0.0:
            self.stats["baseline_nonpositive"] += 1
            self.stats["breach_true"] += 1
            return True

        threshold = (
            0.0
            if self.threshold is None
            else float(self.threshold)
        )

        is_breach = bool(
            minimum
            <=
            threshold * baseline
        )

        if is_breach:
            self.stats["breach_true"] += 1
        else:
            self.stats["breach_false"] += 1

        return is_breach


class _ControlSLTriggerTransformer(ast.NodeTransformer):
    """
    Exact narrow patch inside simulate_live_multiframe():

    Replace the two STANDARD expressions:
        not bool(control_eval["safe"])

    with:
        _pipeline_control_sl_breach(control_eval)

    There are exactly two targets in the sealed source:
      1) initial Control-SL breach trigger
      2) while-loop continuation after each position close

    Fail closed if target count changes.
    """

    def __init__(self):
        super().__init__()
        self.replaced = 0

    @staticmethod
    def _is_control_safe_not(node):
        if not (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
        ):
            return False

        call = node.operand
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "bool"
            and len(call.args) == 1
        ):
            return False

        sub = call.args[0]
        if not (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "control_eval"
        ):
            return False

        sl = sub.slice
        return (
            isinstance(sl, ast.Constant)
            and sl.value == "safe"
        )

    def visit_UnaryOp(self, node):
        node = self.generic_visit(node)

        if not self._is_control_safe_not(node):
            return node

        self.replaced += 1

        return ast.copy_location(
            ast.Call(
                func=ast.Name(
                    id="_pipeline_control_sl_breach",
                    ctx=ast.Load(),
                ),
                args=[
                    ast.Name(
                        id="control_eval",
                        ctx=ast.Load(),
                    )
                ],
                keywords=[],
            ),
            node,
        )


class ControlSLFastEngine(FastEngineV2):
    def __init__(self, module, policy):
        self.control_sl_policy = policy
        super().__init__(module)

    def _build_simulator(self):
        src = textwrap.dedent(
            inspect.getsource(
                self.m.simulate_live_multiframe
            )
        )
        tree = ast.parse(src)

        # Verified FAST V2 optimization patch first.
        fast_transformer = _FastPatchTransformer()
        tree = fast_transformer.visit(tree)

        # Then patch ONLY the two Control-SL trigger checks.
        control_transformer = (
            _ControlSLTriggerTransformer()
        )
        tree = control_transformer.visit(tree)

        ast.fix_missing_locations(tree)

        fast_actual = {
            "pre_guard_removed":
                fast_transformer.pre_guard_removed,
            "risk_event_appends_removed":
                fast_transformer.risk_event_appends_removed,
            "liq_diagnostics_disabled":
                fast_transformer.liq_diagnostics_disabled,
        }

        if fast_actual != self.EXPECTED_PATCH_COUNTS:
            raise RuntimeError(
                "CONTROL SL FAST PATCH TARGET ERROR: "
                f"expected={self.EXPECTED_PATCH_COUNTS} "
                f"actual={fast_actual}"
            )

        if control_transformer.replaced != 2:
            raise RuntimeError(
                "CONTROL SL TRIGGER PATCH TARGET ERROR: "
                f"expected=2 actual={control_transformer.replaced}"
            )

        self.patch_stats = dict(fast_actual)
        self.patch_stats[
            "control_sl_trigger_replaced"
        ] = int(control_transformer.replaced)

        code = compile(
            tree,
            "<research_pipeline_v2_control_sl_simulator>",
            "exec",
        )

        ns = dict(self.m.__dict__)

        ns["flatten_brackets"] = (
            self._cached_flatten_brackets
        )
        ns["maintenance_margin"] = (
            self._fast_maintenance_margin
        )
        ns["get_mark"] = self._fast_get_mark
        ns["_cross_trigger_price"] = (
            self._fast_cross_trigger_price
        )
        ns["_lab_cross_guard_scenarios"] = (
            self._cached_guard_scenarios
        )
        ns["_pipeline_control_sl_breach"] = (
            self.control_sl_policy.breach
        )

        exec(code, ns, ns)

        fn = ns.get(
            "simulate_live_multiframe"
        )
        if not callable(fn):
            raise RuntimeError(
                "CONTROL SL simulator clone missing"
            )

        return fn


def build_control_sl_engine(
    module,
    config,
):
    policy = ControlSLPolicy(config)
    engine = ControlSLFastEngine(
        module,
        policy,
    )
    return engine, policy


def make_control_sl_kwargs(
    data,
    start_ts,
    end_ts,
):
    """
    STANDARD everything outside Control SL trigger:
      Gate = STANDARD
      Trailing = 4 / 2
      Reentry = STANDARD owner STATE
      leverage = STANDARD
    """
    return {
        "signal_book": data["event_book"],
        "symbols": data["symbols"],
        "snapshot": data["snapshot"],
        "market": data["market"],
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "leverage_map": LEVERAGE,
        "frames": OWNER_FRAMES,
        "active_count": 35,
        "start_equity": 10000.0,
        "collect_trade_log": False,
        "trailing_start_pct": 4.0,
        "trailing_callback_pct": 2.0,
        "state_reentry_book": data["owner_state_book"],
        "state_reentry_pullback_pct": 0.0,
    }


def run_control_sl_engine_once(
    *,
    engine,
    config,
    kwargs,
    label,
):
    """
    LAB_CONTROL_SL is read inside the simulator at runtime.

    Temporarily switch it for the OFF ablation and restore it afterwards.
    """
    old = os.environ.get("LAB_CONTROL_SL")
    try:
        os.environ["LAB_CONTROL_SL"] = (
            "1"
            if bool(config.get("enabled", True))
            else "0"
        )
        return timed_run(
            engine.run,
            kwargs,
            label,
        )
    finally:
        if old is None:
            os.environ.pop("LAB_CONTROL_SL", None)
        else:
            os.environ["LAB_CONTROL_SL"] = old


def run_or_resume_control_sl(
    *,
    data,
    stage,
    candidate,
    scope,
    config,
    start_ts,
    end_ts,
    resume,
):
    key, path, checkpoint = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {scope} {candidate}"
        )
        return {
            "metrics": checkpoint["metrics"],
            "elapsed_seconds": float(
                checkpoint.get(
                    "elapsed_seconds",
                    0.0,
                )
            ),
            "extra": checkpoint.get(
                "extra",
                {},
            ),
            "checkpoint": str(path),
            "resumed": True,
        }

    engine, policy = build_control_sl_engine(
        m,
        config,
    )

    kwargs = make_control_sl_kwargs(
        data,
        start_ts,
        end_ts,
    )

    result, elapsed = run_control_sl_engine_once(
        engine=engine,
        config=config,
        kwargs=kwargs,
        label=f"{scope}_{candidate}",
    )

    metrics = extract_metrics(result)

    extra = {
        "patch_stats": dict(engine.patch_stats),
        "control_sl_policy_stats": dict(
            policy.stats
        ),
        "control_sl_enabled": bool(
            config.get("enabled", True)
        ),
        "stress_ratio_threshold":
            config.get(
                "stress_ratio_threshold",
                None,
            ),
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        metrics=metrics,
        elapsed_seconds=elapsed,
        extra=extra,
    )

    return {
        "metrics": metrics,
        "elapsed_seconds": float(elapsed),
        "extra": extra,
        "checkpoint": str(path),
        "resumed": False,
    }


def control_sl_screen_score(
    metrics,
    config,
):
    eq = max(
        float(metrics["final_equity"]),
        1e-12,
    )
    mdd = max(
        abs(
            float(metrics["realized_mdd_pct"])
        ),
        1e-9,
    )
    liq = int(metrics["liquidations"])

    # Slight penalty for moving away from STANDARD; performance dominates.
    threshold = config.get(
        "stress_ratio_threshold",
        0.0,
    )
    if threshold is None:
        distance = 0.25
    else:
        distance = abs(float(threshold))

    return (
        math.log(eq)
        - math.log(mdd)
        - liq * 100.0
        - distance * 0.10
    )


def control_sl_full_delta(metrics):
    base = SEALED_STANDARD
    return {
        "final_equity_delta": (
            float(metrics["final_equity"])
            - float(base["final_equity"])
        ),
        "mdd_delta_pp": (
            float(metrics["realized_mdd_pct"])
            - float(base["realized_mdd_pct"])
        ),
        "liquidation_delta": (
            int(metrics["liquidations"])
            - int(base["liquidations"])
        ),
        "entries_delta": (
            int(metrics["entries"])
            - int(base["entries"])
        ),
        "entry_blocks_delta": (
            int(metrics["entry_blocks"])
            - int(base["entry_blocks"])
        ),
        "control_sl_breaches_delta": (
            int(metrics["control_sl_breaches"])
            - int(base["control_sl_breaches"])
        ),
        "control_sl_closes_delta": (
            int(metrics["control_sl_closes"])
            - int(base["control_sl_closes"])
        ),
        "trailing_closes_delta": (
            int(metrics["trailing_closes"])
            - int(base["trailing_closes"])
        ),
        "state_reentries_delta": (
            int(metrics["state_reentries"])
            - int(base["state_reentries"])
        ),
    }


def load_trailing_keepers_for_integration(
    data,
):
    path = (
        PIPE_RESULTS
        / "trailing_latest.json"
    )
    if not path.exists():
        return []

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return []

    prior_fp = payload.get(
        "data_fingerprint",
        {},
    )
    current_fp = data["fingerprint"]

    if (
        prior_fp.get("fingerprint_hash")
        != current_fp.get("fingerprint_hash")
    ):
        return []

    rows = payload.get(
        "keepers_for_later_integration",
        [],
    )
    return [
        str(x)
        for x in rows
    ]


def run_control_sl_lab(data, args):
    stage = "control_sl_v1"

    # --------------------------------------------------------------
    # 0. Fail-closed parity:
    # FAST V2 STANDARD vs patched Control SL threshold=0.
    # --------------------------------------------------------------
    parity_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.parity_days)
            * 86400
        ),
    )

    parity_kwargs = make_control_sl_kwargs(
        data,
        parity_start,
        data["end_ts"],
    )

    base_engine = build_fast_engine(m)
    strict_engine, strict_policy = (
        build_control_sl_engine(
            m,
            CONTROL_SL_STANDARD_CONFIG,
        )
    )

    print(
        "BASE FAST V2 PATCH =",
        base_engine.patch_stats,
    )
    print(
        "STRICT CONTROL SL PATCH =",
        strict_engine.patch_stats,
    )

    # Base runs with STANDARD Control SL enabled.
    old = os.environ.get("LAB_CONTROL_SL")
    try:
        os.environ["LAB_CONTROL_SL"] = "1"
        base_result, base_sec = timed_run(
            base_engine.run,
            parity_kwargs,
            "CONTROL_SL_PARITY_BASE_FAST_V2",
        )
    finally:
        if old is None:
            os.environ.pop("LAB_CONTROL_SL", None)
        else:
            os.environ["LAB_CONTROL_SL"] = old

    strict_result, strict_sec = (
        run_control_sl_engine_once(
            engine=strict_engine,
            config=CONTROL_SL_STANDARD_CONFIG,
            kwargs=parity_kwargs,
            label="CONTROL_SL_PARITY_STRICT_REPLICA",
        )
    )

    base_metrics = extract_metrics(
        base_result
    )
    strict_metrics = extract_metrics(
        strict_result
    )

    parity_ok, parity_diffs = compare_metrics(
        base_metrics,
        strict_metrics,
    )

    print_metrics(
        "CONTROL SL PARITY - BASE FAST V2",
        base_metrics,
        base_sec,
    )
    print_metrics(
        "CONTROL SL PARITY - STRICT REPLICA",
        strict_metrics,
        strict_sec,
    )

    print()
    print(
        "STRICT_CONTROL_SL_PARITY =",
        "PASS" if parity_ok else "FAIL",
    )
    print(
        "STRICT_CONTROL_SL_DIFFS  =",
        parity_diffs,
    )

    parity_payload = {
        "schema": "CONTROL_SL_PARITY_V2",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "status": (
            "PASS" if parity_ok else "FAIL"
        ),
        "days": int(args.parity_days),
        "base_seconds": float(base_sec),
        "replica_seconds": float(strict_sec),
        "base_metrics": base_metrics,
        "replica_metrics": strict_metrics,
        "diffs": parity_diffs,
        "policy_stats": strict_policy.stats,
        "data_fingerprint":
            data["fingerprint"],
    }

    atomic_write_json(
        PIPE_RESULTS
        / "control_sl_parity_latest.json",
        parity_payload,
    )

    if not parity_ok:
        raise SystemExit(
            "STRICT_CONTROL_SL_PARITY_FAIL"
        )

    # --------------------------------------------------------------
    # 1. Reuse compatible STANDARD 730d screen baseline.
    # --------------------------------------------------------------
    screen_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.screen_days)
            * 86400
        ),
    )

    screen_baseline = load_prior_screen_baseline(
        data,
        args.screen_days,
    )
    baseline_source = "REUSED_REENTRY_STRUCTURE"

    if screen_baseline is None:
        baseline_run = run_or_resume_control_sl(
            data=data,
            stage=stage,
            candidate="STANDARD_CONTROL_SL_BASELINE",
            scope=f"SCREEN_{args.screen_days}D_35",
            config=CONTROL_SL_STANDARD_CONFIG,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            resume=args.resume,
        )
        screen_baseline = baseline_run["metrics"]
        baseline_source = "CONTROL_SL_STAGE_CHECKPOINT"

    print_metrics(
        f"CONTROL SL SCREEN STANDARD {args.screen_days}D "
        f"source={baseline_source}",
        screen_baseline,
    )

    # --------------------------------------------------------------
    # 2. Coarse candidate screen.
    # --------------------------------------------------------------
    candidate_names = [
        x.strip().upper()
        for x in str(
            args.control_sl_candidates
        ).split(",")
        if x.strip()
    ]

    unknown = [
        x
        for x in candidate_names
        if x not in CONTROL_SL_CANDIDATES
    ]
    if unknown:
        raise SystemExit(
            "unknown Control SL candidates: "
            + ", ".join(unknown)
        )

    screen_rows = []

    for name in candidate_names:
        config = CONTROL_SL_CANDIDATES[
            name
        ]

        print()
        print("#" * 112)
        print("CONTROL SL SCREEN CANDIDATE =", name)
        print("DESCRIPTION                 =", config["description"])
        print("ENABLED                     =", config["enabled"])
        print(
            "STRESS RATIO THRESHOLD      =",
            config["stress_ratio_threshold"],
        )
        print("#" * 112)

        run = run_or_resume_control_sl(
            data=data,
            stage=stage,
            candidate=name,
            scope=f"SCREEN_{args.screen_days}D_35",
            config=config,
            start_ts=screen_start,
            end_ts=data["end_ts"],
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = screen_verdict(
            metrics,
            screen_baseline,
        )
        score = control_sl_screen_score(
            metrics,
            config,
        )

        print_metrics(
            f"CONTROL SL SCREEN - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "SCREEN VERDICT =",
            verdict,
        )
        print(
            "SCREEN SCORE   =",
            f"{score:.6f}",
        )

        screen_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "screen_verdict": verdict,
            "screen_score": score,
            "extra": run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
        })

    survivors = [
        row
        for row in screen_rows
        if row["screen_verdict"]
        == "SURVIVE_SCREEN"
    ]

    survivors.sort(
        key=lambda row: (
            -float(row["screen_score"]),
            int(
                row["config"]
                .get("complexity", 0)
            ),
        )
    )

    full_names = [
        row["candidate"]
        for row in survivors[
            : int(args.control_sl_full_top)
        ]
    ]

    print()
    print("=" * 112)
    print("CONTROL SL SCREEN RESULT")
    print("=" * 112)

    ranked = sorted(
        screen_rows,
        key=lambda row:
            -float(row["screen_score"]),
    )

    for i, row in enumerate(
        ranked,
        1,
    ):
        cfg = row["config"]
        threshold = cfg[
            "stress_ratio_threshold"
        ]
        threshold_text = (
            "OFF"
            if threshold is None
            else f"{float(threshold):+.2f}"
        )
        print(
            f"{i:2d}. "
            f"{row['candidate']:28s} "
            f"thr={threshold_text:>6s} "
            f"{row['screen_verdict']:28s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}% "
            f"CSL={row['metrics']['control_sl_closes']}"
        )

    print(
        "FULL_35_SELECTED =",
        full_names,
    )

    # --------------------------------------------------------------
    # 3. FULL 35 only for strongest survivors.
    # --------------------------------------------------------------
    full_rows = []

    for name in full_names:
        config = CONTROL_SL_CANDIDATES[
            name
        ]

        print()
        print("#" * 112)
        print(
            "CONTROL SL FULL 35 CANDIDATE =",
            name,
        )
        print(
            "ENABLED / THRESHOLD           =",
            config["enabled"],
            "/",
            config["stress_ratio_threshold"],
        )
        print("#" * 112)

        run = run_or_resume_control_sl(
            data=data,
            stage=stage,
            candidate=name,
            scope="FULL_35",
            config=config,
            start_ts=data["start_ts"],
            end_ts=data["end_ts"],
            resume=args.resume,
        )

        metrics = run["metrics"]
        verdict = full_verdict(metrics)
        delta = control_sl_full_delta(
            metrics
        )

        print_metrics(
            f"CONTROL SL FULL 35 - {name}",
            metrics,
            run["elapsed_seconds"],
        )
        print(
            "FULL VERDICT =",
            verdict["verdict"],
        )
        print(
            "VS STANDARD   =",
            f"equity {delta['final_equity_delta']:+,.2f}, "
            f"MDD {delta['mdd_delta_pp']:+.6f}pp, "
            f"liq {delta['liquidation_delta']:+d}, "
            f"CSL {delta['control_sl_closes_delta']:+d}",
        )

        full_rows.append({
            "candidate": name,
            "config": config,
            "metrics": metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "verdict": verdict,
            "delta_vs_standard": delta,
            "extra": run["extra"],
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
        })

        append_master_record({
            "schema":
                "RESEARCH_DECISION_V2",
            "stage": stage,
            "candidate": name,
            "scope": "FULL_35_DECISION",
            "candidate_config": config,
            "metrics": metrics,
            "verdict": verdict,
            "delta_vs_standard": delta,
            "source_checkpoint":
                run["checkpoint"],
            "data_fingerprint":
                data["fingerprint"],
        })

    keepers = [
        row["candidate"]
        for row in full_rows
        if row["verdict"]["verdict"]
        in ("KEEP", "REVIEW")
    ]

    # Exact duplicate metric detection.
    for i, row in enumerate(full_rows):
        row["duplicate_metrics_of"] = None
        for j in range(i):
            other = full_rows[j]
            same, _ = compare_metrics(
                row["metrics"],
                other["metrics"],
            )
            if same:
                row["duplicate_metrics_of"] = (
                    other["candidate"]
                )
                break

    trailing_keepers = (
        load_trailing_keepers_for_integration(
            data
        )
    )

    control_sl_for_integration = (
        keepers
        if keepers
        else ["STANDARD_CONTROL_SL"]
    )

    stamp = utc_stamp()
    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_CONTROL_SL_LAB",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "saved_at_utc": stamp,
        "standard_source_modified": False,
        "standard_control_sl": {
            "enabled": True,
            "stress_ratio_threshold": 0.0,
            "reference": "previous confirmed 1H mark",
        },
        "test_isolation": {
            "entry_gate": "STANDARD",
            "trailing": "STANDARD_4_2",
            "reentry": "STANDARD",
            "leverage": "STANDARD",
        },
        "screen_days":
            int(args.screen_days),
        "control_sl_full_top":
            int(args.control_sl_full_top),
        "data_fingerprint":
            data["fingerprint"],
        "parity":
            parity_payload,
        "screen_baseline_source":
            baseline_source,
        "screen_baseline":
            screen_baseline,
        "screen_results":
            screen_rows,
        "full_selected":
            full_names,
        "full_results":
            full_rows,
        "control_sl_keepers":
            keepers,
        "trailing_keepers_from_prior_lab":
            trailing_keepers,
        "control_sl_for_integration":
            control_sl_for_integration,
        "next_action":
            "INTEGRATION_LAB",
        "next_note": (
            "Combine surviving Trailing challengers with surviving "
            "Control-SL challengers. If Control SL has no keeper, "
            "use STANDARD Control SL. Gate and Reentry remain STANDARD."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"control_sl_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "control_sl_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage": stage,
        "candidate": "BATCH_SUMMARY",
        "scope": "SCREEN_THEN_FULL_35",
        "result_path": str(timestamped),
        "data_fingerprint":
            data["fingerprint"],
        "control_sl_keepers":
            keepers,
        "trailing_keepers":
            trailing_keepers,
        "next_action":
            "INTEGRATION_LAB",
    })

    print()
    print("=" * 112)
    print("CONTROL SL LAB COMPLETE")
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "MASTER JSONL       =",
        MASTER_JSONL,
    )
    print(
        "MASTER INDEX       =",
        MASTER_INDEX,
    )
    print(
        "CONTROL SL KEEPERS =",
        keepers,
    )
    print(
        "TRAILING KEEPERS   =",
        trailing_keepers,
    )
    print(
        "NEXT ACTION        = INTEGRATION_LAB"
    )

    return summary




# =============================================================================
# OPPOSITE SIGNAL REOPEN TIMING LAB V1
# =============================================================================
#
# CURRENT STANDARD:
#   owner opposite signal
#   -> close current position
#   -> immediately call enter_position() in the opposite direction
#   -> Entry Gate is evaluated again inside enter_position()
#
# This lab DOES NOT change what constitutes an opposite signal.
# It changes only the reopen timing AFTER the STANDARD opposite-signal close.
#
# Modes:
#   IMMEDIATE
#       exact current STANDARD behavior.
#
#   NEXT_1H_STATE
#       close now, clear stale EVENT state, then wait until the next 1H
#       decision time. Reopen only if the original owner TF STATE still
#       confirms the opposite direction. Entry Gate is still re-evaluated.
#
#   NEW_EVENT_ONLY
#       close now, clear stale EVENT state, no immediate reopen. General FLAT
#       entry remains EVENT-only, so the symbol must wait for a genuinely
#       fresh enabled-frame EVENT before another entry can occur.
#
# Important:
#   - Entry Gate remains STANDARD.
#   - Control SL remains STANDARD.
#   - Reentry after Trailing/Control SL remains STANDARD.
#   - Actual liquidation logic remains enabled.
#   - This stage uses the FULL-KEEP Trailing finalists from the prior lab.
#
# Because CONTROL SL LAB produced no keeper, the Integration step is a
# no-compute identity:
#   Trailing finalist + STANDARD Control SL
# which was already FULL-tested in the Trailing Lab.
# We record that resolution and do NOT repeat identical FULL runs.
# =============================================================================

OPPOSITE_TIMING_MODES = {
    "NEXT_1H_STATE": {
        "description": (
            "Opposite close now; reopen from the next 1H decision only if "
            "owner TF STATE still confirms the opposite direction."
        ),
        "mode": "NEXT_1H_STATE",
        "complexity": 1,
    },
    "NEW_EVENT_ONLY": {
        "description": (
            "Opposite close now; clear stale EVENT state and wait for a "
            "genuinely fresh enabled-frame EVENT before any new entry."
        ),
        "mode": "NEW_EVENT_ONLY",
        "complexity": 1,
    },
}

OPPOSITE_STANDARD_MODE = {
    "description": "Exact current STANDARD: immediate opposite reopen attempt.",
    "mode": "IMMEDIATE",
    "complexity": 0,
}


class OppositeTimingPolicy:
    def __init__(self, config):
        self.config = dict(config)
        self.mode = str(
            self.config.get("mode", "IMMEDIATE")
        ).upper()
        if self.mode not in (
            "IMMEDIATE",
            "NEXT_1H_STATE",
            "NEW_EVENT_ONLY",
        ):
            raise ValueError(
                f"bad opposite timing mode: {self.mode}"
            )

        self.stats = {
            "helper_calls": 0,
            "immediate_calls": 0,
            "deferred_next_1h_state": 0,
            "deferred_new_event_only": 0,
            "frame_state_resets": 0,
        }

    def reopen(
        self,
        enter_position_fn,
        wallet,
        symbol,
        decision,
        owner_tf,
        ts,
        next_pid,
        frame_state,
        state_reentry_wait,
        frames,
        mark,
    ):
        """
        Drop-in replacement for ONLY the immediate enter_position() call in
        the owner opposite-signal branch.

        Return shape stays identical:
            wallet, next_pid, ok, actual_leverage
        """
        self.stats["helper_calls"] += 1

        if self.mode == "IMMEDIATE":
            self.stats["immediate_calls"] += 1
            return enter_position_fn(
                wallet,
                symbol,
                decision,
                owner_tf,
                ts,
                next_pid,
            )

        symbol_u = str(symbol).upper()
        owner_tf_u = str(owner_tf).upper()

        # Do not allow the just-consumed opposite EVENT state to behave as
        # a stale FLAT entry signal on the next timestamp.
        for tf in frames:
            frame_state[
                (symbol_u, str(tf).upper())
            ] = 0

        self.stats["frame_state_resets"] += 1

        if self.mode == "NEXT_1H_STATE":
            # Reuse the already-validated STANDARD owner STATE wait engine.
            # The wait code checks only when ts > exit_ts, so this enforces
            # at least the next 1H decision boundary.
            state_reentry_wait[symbol_u] = {
                "side": int(decision),
                "owner_tf": owner_tf_u,
                "exit_ts": int(ts),
                "exit_price": float(mark),
            }
            self.stats[
                "deferred_next_1h_state"
            ] += 1

        elif self.mode == "NEW_EVENT_ONLY":
            # No STATE wait. With frame_state cleared, only a genuinely
            # fresh future EVENT can repopulate FLAT candidates.
            state_reentry_wait.pop(
                symbol_u,
                None,
            )
            self.stats[
                "deferred_new_event_only"
            ] += 1

        return (
            float(wallet),
            int(next_pid),
            False,
            0,
        )


class _OppositeReopenTransformer(ast.NodeTransformer):
    """
    Patch exactly ONE enter_position() call:
    the call inside:
        if decision == -old_side:
            ...
            enter_position(wallet, symbol, decision, owner_tf, ts, next_pid)

    Fail closed if the sealed source changes.
    """

    def __init__(self):
        super().__init__()
        self.in_owner_opposite = 0
        self.replaced = 0

    @staticmethod
    def _is_owner_opposite_test(test):
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
        ):
            return False

        left = test.left
        right = test.comparators[0]

        return (
            isinstance(left, ast.Name)
            and left.id == "decision"
            and isinstance(right, ast.UnaryOp)
            and isinstance(right.op, ast.USub)
            and isinstance(right.operand, ast.Name)
            and right.operand.id == "old_side"
        )

    @staticmethod
    def _is_target_enter_call(node):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "enter_position"
            and len(node.args) == 6
        ):
            return False

        a = node.args
        return (
            isinstance(a[0], ast.Name)
            and a[0].id == "wallet"
            and isinstance(a[1], ast.Name)
            and a[1].id == "symbol"
            and isinstance(a[2], ast.Name)
            and a[2].id == "decision"
            and isinstance(a[3], ast.Name)
            and a[3].id == "owner_tf"
            and isinstance(a[4], ast.Name)
            and a[4].id == "ts"
            and isinstance(a[5], ast.Name)
            and a[5].id == "next_pid"
        )

    def visit_If(self, node):
        is_target = self._is_owner_opposite_test(
            node.test
        )
        if is_target:
            self.in_owner_opposite += 1

        node = self.generic_visit(node)

        if is_target:
            self.in_owner_opposite -= 1

        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)

        if (
            self.in_owner_opposite > 0
            and self._is_target_enter_call(node)
        ):
            self.replaced += 1

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(
                        id="_pipeline_opposite_reopen",
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Name(
                            id="enter_position",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="wallet",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="symbol",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="decision",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="owner_tf",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="ts",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="next_pid",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="frame_state",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="state_reentry_wait",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="frames",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="mark",
                            ctx=ast.Load(),
                        ),
                    ],
                    keywords=[],
                ),
                node,
            )

        return node


class OppositeTimingFastEngine(FastEngineV2):
    def __init__(self, module, policy):
        self.opposite_timing_policy = policy
        super().__init__(module)

    def _build_simulator(self):
        src = textwrap.dedent(
            inspect.getsource(
                self.m.simulate_live_multiframe
            )
        )
        tree = ast.parse(src)

        # Verified FAST V2 optimizations first.
        fast_transformer = _FastPatchTransformer()
        tree = fast_transformer.visit(tree)

        # Then patch only the owner-opposite immediate reopen call.
        opposite_transformer = (
            _OppositeReopenTransformer()
        )
        tree = opposite_transformer.visit(tree)

        ast.fix_missing_locations(tree)

        fast_actual = {
            "pre_guard_removed":
                fast_transformer.pre_guard_removed,
            "risk_event_appends_removed":
                fast_transformer.risk_event_appends_removed,
            "liq_diagnostics_disabled":
                fast_transformer.liq_diagnostics_disabled,
        }

        if fast_actual != self.EXPECTED_PATCH_COUNTS:
            raise RuntimeError(
                "OPPOSITE FAST PATCH TARGET ERROR: "
                f"expected={self.EXPECTED_PATCH_COUNTS} "
                f"actual={fast_actual}"
            )

        if opposite_transformer.replaced != 1:
            raise RuntimeError(
                "OPPOSITE REOPEN PATCH TARGET ERROR: "
                f"expected=1 actual={opposite_transformer.replaced}"
            )

        self.patch_stats = dict(fast_actual)
        self.patch_stats[
            "opposite_reopen_replaced"
        ] = int(
            opposite_transformer.replaced
        )

        code = compile(
            tree,
            "<research_pipeline_v2_opposite_timing_simulator>",
            "exec",
        )

        ns = dict(self.m.__dict__)

        ns["flatten_brackets"] = (
            self._cached_flatten_brackets
        )
        ns["maintenance_margin"] = (
            self._fast_maintenance_margin
        )
        ns["get_mark"] = self._fast_get_mark
        ns["_cross_trigger_price"] = (
            self._fast_cross_trigger_price
        )
        ns["_lab_cross_guard_scenarios"] = (
            self._cached_guard_scenarios
        )
        ns["_pipeline_opposite_reopen"] = (
            self.opposite_timing_policy.reopen
        )

        exec(code, ns, ns)

        fn = ns.get(
            "simulate_live_multiframe"
        )
        if not callable(fn):
            raise RuntimeError(
                "OPPOSITE timing simulator clone missing"
            )

        return fn


def build_opposite_timing_engine(
    module,
    config,
):
    policy = OppositeTimingPolicy(config)
    engine = OppositeTimingFastEngine(
        module,
        policy,
    )
    return engine, policy


def _load_json_file(path):
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"cannot read required result JSON: {path}: {exc}"
        ) from exc


def resolve_integration_without_rerun(
    data,
):
    """
    Current branch-specific Integration resolution.

    CONTROL SL LAB has no keeper, so:
        Trailing keeper + STANDARD Control SL
    is exactly the configuration already FULL-tested by Trailing Lab.

    Re-running those FULL simulations would be duplicate compute.
    """
    trailing_path = (
        PIPE_RESULTS
        / "trailing_latest.json"
    )
    control_path = (
        PIPE_RESULTS
        / "control_sl_latest.json"
    )

    if not trailing_path.exists():
        raise RuntimeError(
            "trailing_latest.json missing; cannot resolve integration"
        )
    if not control_path.exists():
        raise RuntimeError(
            "control_sl_latest.json missing; cannot resolve integration"
        )

    trailing = _load_json_file(
        trailing_path
    )
    control = _load_json_file(
        control_path
    )

    current_hash = data[
        "fingerprint"
    ].get("fingerprint_hash")

    for label, payload in (
        ("TRAILING", trailing),
        ("CONTROL_SL", control),
    ):
        prior_hash = (
            payload.get(
                "data_fingerprint",
                {},
            ).get("fingerprint_hash")
        )
        if prior_hash != current_hash:
            raise RuntimeError(
                f"{label} RESULT FINGERPRINT MISMATCH: "
                f"{prior_hash} != {current_hash}"
            )

    control_keepers = list(
        control.get(
            "control_sl_keepers",
            [],
        )
    )

    if control_keepers:
        raise RuntimeError(
            "CONTROL SL KEEPERS EXIST. Real Integration Lab is required "
            "before Opposite Timing Lab. keepers="
            + repr(control_keepers)
        )

    full_rows = list(
        trailing.get(
            "full_results",
            [],
        )
    )

    keep_rows = []
    review_rows = []

    for row in full_rows:
        verdict_obj = row.get(
            "verdict",
            {},
        )
        verdict = str(
            verdict_obj.get(
                "verdict",
                "",
            )
        ).upper()

        if verdict == "KEEP":
            keep_rows.append(row)
        elif verdict == "REVIEW":
            review_rows.append(row)

    if not keep_rows:
        raise RuntimeError(
            "No FULL KEEP trailing finalist found"
        )

    # Primary opposite timing lab uses KEEP only.
    # REVIEW remains archived but does not multiply the timing search.
    finalists = keep_rows

    integration = {
        "schema":
            "RESEARCH_PIPELINE_V2_INTEGRATION_RESOLUTION",
        "pipeline_build_id": BUILD_ID,
        "fast_build_id": FAST_BUILD_ID,
        "saved_at_utc": utc_stamp(),
        "data_fingerprint":
            data["fingerprint"],
        "rerun_skipped": True,
        "reason": (
            "CONTROL SL LAB produced no keeper. "
            "Trailing KEEP + STANDARD Control SL is identical to the "
            "already FULL-tested Trailing Lab configurations."
        ),
        "control_sl_selected":
            "STANDARD_CONTROL_SL",
        "trailing_keep_finalists": [
            row["candidate"]
            for row in finalists
        ],
        "trailing_review_archived": [
            row["candidate"]
            for row in review_rows
        ],
        "next_action":
            "OPPOSITE_SIGNAL_REOPEN_TIMING_LAB",
    }

    integration_path = (
        PIPE_RESULTS
        / "integration_latest.json"
    )
    atomic_write_json(
        integration_path,
        integration,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            "integration_resolution",
        "candidate":
            "NO_CONTROL_SL_KEEPER_IDENTITY",
        "scope":
            "NO_COMPUTE_DUPLICATE_SKIP",
        "result_path":
            str(integration_path),
        "data_fingerprint":
            data["fingerprint"],
        "trailing_keep_finalists":
            integration[
                "trailing_keep_finalists"
            ],
        "control_sl_selected":
            "STANDARD_CONTROL_SL",
        "next_action":
            "OPPOSITE_SIGNAL_REOPEN_TIMING_LAB",
    })

    print()
    print("=" * 112)
    print("INTEGRATION RESOLUTION")
    print("=" * 112)
    print(
        "CONTROL SL = STANDARD "
        "(no Control SL keeper)"
    )
    print(
        "TRAILING KEEP FINALISTS =",
        integration[
            "trailing_keep_finalists"
        ],
    )
    print(
        "TRAILING REVIEW ARCHIVED =",
        integration[
            "trailing_review_archived"
        ],
    )
    print(
        "DUPLICATE FULL RERUN = SKIPPED"
    )

    return trailing, finalists, integration


def make_opposite_timing_kwargs(
    data,
    start_ts,
    end_ts,
    trailing_start_pct,
    trailing_callback_pct,
):
    return {
        "signal_book": data["event_book"],
        "symbols": data["symbols"],
        "snapshot": data["snapshot"],
        "market": data["market"],
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "leverage_map": LEVERAGE,
        "frames": OWNER_FRAMES,
        "active_count": 35,
        "start_equity": 10000.0,
        "collect_trade_log": False,
        "trailing_start_pct":
            float(trailing_start_pct),
        "trailing_callback_pct":
            float(trailing_callback_pct),
        "state_reentry_book":
            data["owner_state_book"],
        "state_reentry_pullback_pct": 0.0,
    }


def run_or_resume_opposite_timing(
    *,
    data,
    stage,
    candidate,
    scope,
    config,
    trailing_config,
    start_ts,
    end_ts,
    resume,
):
    combined_config = {
        "opposite_timing": config,
        "trailing":
            trailing_config,
        "control_sl":
            "STANDARD",
        "entry_gate":
            "STANDARD",
        "reentry":
            "STANDARD",
    }

    key, path, checkpoint = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=combined_config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {scope} {candidate}"
        )
        return {
            "metrics":
                checkpoint["metrics"],
            "elapsed_seconds":
                float(
                    checkpoint.get(
                        "elapsed_seconds",
                        0.0,
                    )
                ),
            "extra":
                checkpoint.get(
                    "extra",
                    {},
                ),
            "checkpoint":
                str(path),
            "resumed":
                True,
        }

    engine, policy = (
        build_opposite_timing_engine(
            m,
            config,
        )
    )

    kwargs = make_opposite_timing_kwargs(
        data,
        start_ts,
        end_ts,
        trailing_config[
            "trailing_start_pct"
        ],
        trailing_config[
            "trailing_callback_pct"
        ],
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        f"{scope}_{candidate}",
    )

    metrics = extract_metrics(
        result
    )

    extra = {
        "patch_stats":
            dict(engine.patch_stats),
        "opposite_policy_stats":
            dict(policy.stats),
        "mode":
            config["mode"],
        "trailing_start_pct":
            trailing_config[
                "trailing_start_pct"
            ],
        "trailing_callback_pct":
            trailing_config[
                "trailing_callback_pct"
            ],
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=combined_config,
        data_fingerprint=data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        metrics=metrics,
        elapsed_seconds=elapsed,
        extra=extra,
    )

    return {
        "metrics": metrics,
        "elapsed_seconds":
            float(elapsed),
        "extra": extra,
        "checkpoint": str(path),
        "resumed": False,
    }


def opposite_screen_score(
    metrics,
    parent_screen_metrics,
):
    eq = max(
        float(metrics["final_equity"]),
        1e-12,
    )
    mdd = max(
        abs(
            float(metrics["realized_mdd_pct"])
        ),
        1e-9,
    )
    liq = int(
        metrics["liquidations"]
    )

    parent_eq = max(
        float(
            parent_screen_metrics[
                "final_equity"
            ]
        ),
        1e-12,
    )

    relative = math.log(
        eq / parent_eq
    )

    return (
        math.log(eq)
        - math.log(mdd)
        + 0.20 * relative
        - liq * 100.0
    )


def opposite_parent_full_verdict(
    metrics,
    parent_metrics,
):
    """
    Conservative comparison against the already FULL-tested immediate parent.
    """
    eq = float(
        metrics["final_equity"]
    )
    parent_eq = float(
        parent_metrics["final_equity"]
    )

    mdd = float(
        metrics["realized_mdd_pct"]
    )
    parent_mdd = float(
        parent_metrics[
            "realized_mdd_pct"
        ]
    )

    liq = int(
        metrics["liquidations"]
    )
    parent_liq = int(
        parent_metrics["liquidations"]
    )

    if liq > parent_liq:
        return {
            "verdict": "REJECT",
            "reason":
                "MORE_LIQUIDATIONS_THAN_PARENT",
        }

    # Dominates or matches parent on both core axes.
    if (
        eq >= parent_eq
        and mdd >= parent_mdd
        and (
            eq > parent_eq
            or mdd > parent_mdd
        )
    ):
        return {
            "verdict": "KEEP",
            "reason":
                "DOMINATES_IMMEDIATE_PARENT",
        }

    # Review only for a meaningful risk/return trade-off.
    mdd_improvement_pp = (
        mdd - parent_mdd
    )
    eq_ratio = (
        eq / parent_eq
        if parent_eq > 0
        else 0.0
    )

    if (
        mdd_improvement_pp >= 2.0
        and eq_ratio >= 0.80
    ):
        return {
            "verdict": "REVIEW",
            "reason":
                "MDD_IMPROVEMENT_WITH_LIMITED_EQUITY_COST",
        }

    if (
        eq_ratio >= 1.20
        and mdd_improvement_pp >= -2.0
    ):
        return {
            "verdict": "REVIEW",
            "reason":
                "EQUITY_IMPROVEMENT_WITH_LIMITED_MDD_COST",
        }

    return {
        "verdict": "REJECT",
        "reason":
            "WORSE_THAN_IMMEDIATE_PARENT",
    }


def _find_trailing_screen_row(
    trailing_payload,
    candidate,
):
    for row in trailing_payload.get(
        "screen_results",
        [],
    ):
        if str(
            row.get("candidate", "")
        ) == str(candidate):
            return row
    raise RuntimeError(
        f"missing trailing screen row: {candidate}"
    )


def run_opposite_timing_lab(
    data,
    args,
):
    stage = "opposite_timing_v1"

    trailing_payload, finalists, integration = (
        resolve_integration_without_rerun(
            data
        )
    )

    # --------------------------------------------------------------
    # 0. Fail-closed IMMEDIATE parity against BASE FAST V2.
    # Use STANDARD trailing 4/2 so only the patch itself is validated.
    # --------------------------------------------------------------
    parity_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.parity_days)
            * 86400
        ),
    )

    parity_kwargs = (
        make_opposite_timing_kwargs(
            data,
            parity_start,
            data["end_ts"],
            4.0,
            2.0,
        )
    )

    base_engine = build_fast_engine(
        m
    )
    strict_engine, strict_policy = (
        build_opposite_timing_engine(
            m,
            OPPOSITE_STANDARD_MODE,
        )
    )

    print(
        "BASE FAST V2 PATCH =",
        base_engine.patch_stats,
    )
    print(
        "STRICT OPPOSITE TIMING PATCH =",
        strict_engine.patch_stats,
    )

    base_result, base_sec = timed_run(
        base_engine.run,
        parity_kwargs,
        "OPPOSITE_TIMING_PARITY_BASE_FAST_V2",
    )
    strict_result, strict_sec = timed_run(
        strict_engine.run,
        parity_kwargs,
        "OPPOSITE_TIMING_PARITY_STRICT_IMMEDIATE",
    )

    base_metrics = extract_metrics(
        base_result
    )
    strict_metrics = extract_metrics(
        strict_result
    )

    parity_ok, parity_diffs = (
        compare_metrics(
            base_metrics,
            strict_metrics,
        )
    )

    print()
    print(
        "STRICT_OPPOSITE_TIMING_PARITY =",
        "PASS" if parity_ok else "FAIL",
    )
    print(
        "STRICT_OPPOSITE_TIMING_DIFFS  =",
        parity_diffs,
    )

    parity_payload = {
        "schema":
            "OPPOSITE_TIMING_PARITY_V2",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "status":
            "PASS" if parity_ok else "FAIL",
        "days":
            int(args.parity_days),
        "base_seconds":
            float(base_sec),
        "replica_seconds":
            float(strict_sec),
        "base_metrics":
            base_metrics,
        "replica_metrics":
            strict_metrics,
        "diffs":
            parity_diffs,
        "policy_stats":
            strict_policy.stats,
        "data_fingerprint":
            data["fingerprint"],
    }

    atomic_write_json(
        PIPE_RESULTS
        / "opposite_timing_parity_latest.json",
        parity_payload,
    )

    if not parity_ok:
        raise SystemExit(
            "STRICT_OPPOSITE_TIMING_PARITY_FAIL"
        )

    # --------------------------------------------------------------
    # 1. Build candidate grid:
    #    each FULL-KEEP trailing finalist x two defer modes.
    #    IMMEDIATE parent rows are reused, never rerun.
    # --------------------------------------------------------------
    screen_start = max(
        int(data["start_ts"]),
        int(
            data["end_ts"]
            - int(args.screen_days)
            * 86400
        ),
    )

    screen_rows = []

    for parent in finalists:
        parent_name = str(
            parent["candidate"]
        )
        trailing_config = dict(
            parent["config"]
        )
        parent_full_metrics = dict(
            parent["metrics"]
        )
        parent_screen_row = (
            _find_trailing_screen_row(
                trailing_payload,
                parent_name,
            )
        )
        parent_screen_metrics = dict(
            parent_screen_row["metrics"]
        )

        print()
        print("=" * 112)
        print(
            "OPPOSITE TIMING PARENT =",
            parent_name,
        )
        print(
            "TRAILING START/CALLBACK =",
            trailing_config[
                "trailing_start_pct"
            ],
            "/",
            trailing_config[
                "trailing_callback_pct"
            ],
        )
        print(
            "IMMEDIATE SCREEN EQUITY =",
            f"${parent_screen_metrics['final_equity']:,.2f}",
        )
        print(
            "IMMEDIATE FULL EQUITY   =",
            f"${parent_full_metrics['final_equity']:,.2f}",
        )
        print("=" * 112)

        for mode_name, mode_config in (
            OPPOSITE_TIMING_MODES.items()
        ):
            candidate = (
                f"{parent_name}__{mode_name}"
            )

            print()
            print("#" * 112)
            print(
                "OPPOSITE TIMING SCREEN CANDIDATE =",
                candidate,
            )
            print(
                "MODE                             =",
                mode_name,
            )
            print(
                "DESCRIPTION                      =",
                mode_config[
                    "description"
                ],
            )
            print("#" * 112)

            run = (
                run_or_resume_opposite_timing(
                    data=data,
                    stage=stage,
                    candidate=candidate,
                    scope=(
                        f"SCREEN_"
                        f"{args.screen_days}D_35"
                    ),
                    config=mode_config,
                    trailing_config=
                        trailing_config,
                    start_ts=
                        screen_start,
                    end_ts=
                        data["end_ts"],
                    resume=
                        args.resume,
                )
            )

            metrics = run["metrics"]
            verdict = screen_verdict(
                metrics,
                parent_screen_metrics,
            )
            score = opposite_screen_score(
                metrics,
                parent_screen_metrics,
            )

            print_metrics(
                f"OPPOSITE TIMING SCREEN - {candidate}",
                metrics,
                run["elapsed_seconds"],
            )
            print(
                "SCREEN VERDICT =",
                verdict,
            )
            print(
                "SCREEN SCORE   =",
                f"{score:.6f}",
            )

            screen_rows.append({
                "candidate":
                    candidate,
                "parent_trailing":
                    parent_name,
                "trailing_config":
                    trailing_config,
                "mode":
                    mode_name,
                "mode_config":
                    mode_config,
                "metrics":
                    metrics,
                "parent_screen_metrics":
                    parent_screen_metrics,
                "parent_full_metrics":
                    parent_full_metrics,
                "elapsed_seconds":
                    run["elapsed_seconds"],
                "screen_verdict":
                    verdict,
                "screen_score":
                    score,
                "checkpoint":
                    run["checkpoint"],
                "resumed":
                    run["resumed"],
                "extra":
                    run["extra"],
            })

    # Max one deferred timing finalist per trailing parent.
    selected = []

    for parent in finalists:
        parent_name = str(
            parent["candidate"]
        )
        rows = [
            row
            for row in screen_rows
            if (
                row["parent_trailing"]
                == parent_name
                and row["screen_verdict"]
                == "SURVIVE_SCREEN"
            )
        ]
        rows.sort(
            key=lambda row:
                -float(
                    row["screen_score"]
                )
        )

        if rows:
            selected.append(
                rows[0]
            )

    selected = selected[
        : int(
            args.opposite_full_top
        )
    ]

    print()
    print("=" * 112)
    print(
        "OPPOSITE TIMING SCREEN RESULT"
    )
    print("=" * 112)

    ranked = sorted(
        screen_rows,
        key=lambda row:
            -float(
                row["screen_score"]
            ),
    )

    for i, row in enumerate(
        ranked,
        1,
    ):
        print(
            f"{i:2d}. "
            f"{row['candidate']:52s} "
            f"{row['screen_verdict']:28s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}%"
        )

    print(
        "FULL_35_SELECTED =",
        [
            row["candidate"]
            for row in selected
        ],
    )

    # --------------------------------------------------------------
    # 2. FULL 35 and compare against each candidate's IMMEDIATE parent.
    # --------------------------------------------------------------
    full_rows = []

    for row in selected:
        candidate = row[
            "candidate"
        ]

        print()
        print("#" * 112)
        print(
            "OPPOSITE TIMING FULL 35 CANDIDATE =",
            candidate,
        )
        print(
            "PARENT IMMEDIATE                  =",
            row[
                "parent_trailing"
            ],
        )
        print(
            "MODE                              =",
            row["mode"],
        )
        print("#" * 112)

        run = (
            run_or_resume_opposite_timing(
                data=data,
                stage=stage,
                candidate=candidate,
                scope="FULL_35",
                config=row[
                    "mode_config"
                ],
                trailing_config=row[
                    "trailing_config"
                ],
                start_ts=
                    data["start_ts"],
                end_ts=
                    data["end_ts"],
                resume=
                    args.resume,
            )
        )

        metrics = run["metrics"]
        parent_metrics = row[
            "parent_full_metrics"
        ]
        verdict = (
            opposite_parent_full_verdict(
                metrics,
                parent_metrics,
            )
        )

        eq_delta = (
            float(
                metrics[
                    "final_equity"
                ]
            )
            - float(
                parent_metrics[
                    "final_equity"
                ]
            )
        )
        mdd_delta = (
            float(
                metrics[
                    "realized_mdd_pct"
                ]
            )
            - float(
                parent_metrics[
                    "realized_mdd_pct"
                ]
            )
        )

        print_metrics(
            f"OPPOSITE TIMING FULL 35 - {candidate}",
            metrics,
            run[
                "elapsed_seconds"
            ],
        )
        print(
            "FULL VERDICT VS IMMEDIATE PARENT =",
            verdict["verdict"],
        )
        print(
            "FULL REASON  =",
            verdict["reason"],
        )
        print(
            "VS PARENT    =",
            f"equity {eq_delta:+,.2f}, "
            f"MDD {mdd_delta:+.6f}pp, "
            f"liq "
            f"{int(metrics['liquidations']) - int(parent_metrics['liquidations']):+d}",
        )

        full_row = {
            **row,
            "metrics":
                metrics,
            "elapsed_seconds":
                run["elapsed_seconds"],
            "verdict":
                verdict,
            "delta_vs_immediate_parent": {
                "final_equity_delta":
                    eq_delta,
                "mdd_delta_pp":
                    mdd_delta,
                "liquidation_delta":
                    int(
                        metrics[
                            "liquidations"
                        ]
                    )
                    - int(
                        parent_metrics[
                            "liquidations"
                        ]
                    ),
            },
            "checkpoint":
                run["checkpoint"],
            "resumed":
                run["resumed"],
            "extra":
                run["extra"],
        }

        full_rows.append(
            full_row
        )

        append_master_record({
            "schema":
                "RESEARCH_DECISION_V2",
            "stage":
                stage,
            "candidate":
                candidate,
            "scope":
                "FULL_35_DECISION",
            "parent_trailing":
                row[
                    "parent_trailing"
                ],
            "mode":
                row["mode"],
            "metrics":
                metrics,
            "verdict":
                verdict,
            "delta_vs_immediate_parent":
                full_row[
                    "delta_vs_immediate_parent"
                ],
            "source_checkpoint":
                run["checkpoint"],
            "data_fingerprint":
                data["fingerprint"],
        })

    keepers = [
        row["candidate"]
        for row in full_rows
        if row["verdict"][
            "verdict"
        ] in (
            "KEEP",
            "REVIEW",
        )
    ]

    # If no deferred timing survives, current immediate behavior remains the
    # timing champion for each trailing finalist.
    timing_champions = []

    for parent in finalists:
        parent_name = str(
            parent["candidate"]
        )
        parent_candidates = [
            row
            for row in full_rows
            if (
                row["parent_trailing"]
                == parent_name
                and row["verdict"][
                    "verdict"
                ] in (
                    "KEEP",
                    "REVIEW",
                )
            )
        ]

        if parent_candidates:
            best = sorted(
                parent_candidates,
                key=lambda row: (
                    0
                    if row["verdict"][
                        "verdict"
                    ] == "KEEP"
                    else 1,
                    -float(
                        row["metrics"][
                            "final_equity"
                        ]
                    ),
                ),
            )[0]

            timing_champions.append({
                "parent_trailing":
                    parent_name,
                "timing":
                    best["mode"],
                "candidate":
                    best["candidate"],
                "source":
                    "OPPOSITE_TIMING_LAB",
            })

        else:
            timing_champions.append({
                "parent_trailing":
                    parent_name,
                "timing":
                    "IMMEDIATE",
                "candidate":
                    parent_name,
                "source":
                    "PRIOR_TRAILING_FULL",
            })

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_OPPOSITE_TIMING_LAB",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "data_fingerprint":
            data["fingerprint"],
        "integration_resolution":
            integration,
        "parity":
            parity_payload,
        "screen_days":
            int(
                args.screen_days
            ),
        "screen_results":
            screen_rows,
        "full_selected": [
            row["candidate"]
            for row in selected
        ],
        "full_results":
            full_rows,
        "deferred_timing_keepers":
            keepers,
        "timing_champions":
            timing_champions,
        "next_action":
            "OPPOSITE_EXIT_SOURCE_LAB",
        "next_note": (
            "Now test WHICH component opposite signal should trigger the "
            "close (combined consensus vs EMA-only vs SAR-only vs either), "
            "while keeping the winning reopen timing fixed."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"opposite_timing_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "opposite_timing_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            "BATCH_SUMMARY",
        "scope":
            "SCREEN_THEN_FULL_35",
        "result_path":
            str(timestamped),
        "data_fingerprint":
            data["fingerprint"],
        "timing_champions":
            timing_champions,
        "next_action":
            "OPPOSITE_EXIT_SOURCE_LAB",
    })

    print()
    print("=" * 112)
    print(
        "OPPOSITE SIGNAL REOPEN TIMING LAB COMPLETE"
    )
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "TIMING CHAMPIONS   =",
        timing_champions,
    )
    print(
        "NEXT ACTION        = OPPOSITE_EXIT_SOURCE_LAB"
    )

    return summary




# =============================================================================
# OPPOSITE SIGNAL EXIT SOURCE LAB V1
# =============================================================================
#
# Current STANDARD opposite-close source:
#   FINAL B owner EVENT = EMA_CROSS EVENT + SAR agreement
#
# New coarse alternatives:
#
#   EMA_ONLY
#       Close only when the owner-TF EMA_CROSS EVENT is opposite.
#
#   SAR_ONLY
#       Close when the owner-TF native SAR signal is opposite.
#
#   EITHER_OPPOSITE
#       Close when EITHER EMA_CROSS or SAR is opposite.
#       If the two disagree, opposite-to-current-position wins because this
#       candidate explicitly means "one signal is enough to close".
#
# The already validated parent behavior remains the baseline:
#   T1 branch -> Trailing 3/1.5 + NEXT_1H_STATE reopen
#   T3 branch -> Trailing 4/1.5 + IMMEDIATE reopen
#
# Entry stays FINAL B EVENT. Flat-entry logic is NOT changed.
# Control SL stays STANDARD.
# Trailing/Control-SL STATE re-entry stays STANDARD.
# Actual liquidation simulation remains enabled.
# =============================================================================

EXIT_SOURCE_STANDARD = {
    "description": (
        "Current STANDARD opposite-close source: FINAL B owner EVENT "
        "(EMA_CROSS + SAR agreement)."
    ),
    "mode": "STANDARD_COMBINED",
    "complexity": 0,
}

EXIT_SOURCE_CANDIDATES = {
    "EMA_ONLY": {
        "description": (
            "Opposite close uses owner-TF EMA_CROSS EVENT only."
        ),
        "mode": "EMA_ONLY",
        "complexity": 1,
    },
    "SAR_ONLY": {
        "description": (
            "Opposite close uses owner-TF native SAR signal only."
        ),
        "mode": "SAR_ONLY",
        "complexity": 1,
    },
    "EITHER_OPPOSITE": {
        "description": (
            "Opposite close triggers when either owner-TF EMA_CROSS "
            "or SAR is opposite to the held position."
        ),
        "mode": "EITHER_OPPOSITE",
        "complexity": 1,
    },
}


def _exit_source_signal_fn(source):
    source = str(source).upper()

    if source == "EMA_CROSS":
        return _native_material_signal_fn(
            "EMA_CROSS"
        )
    if source == "SAR":
        return _native_material_signal_fn(
            "SAR"
        )

    raise ValueError(
        f"bad exit source: {source}"
    )


def build_exit_source_books(data):
    """
    Build owner-TF EMA_CROSS and SAR books once.

    These are read-only research books. Entry signal_book remains
    data['event_book'] and is never replaced.
    """
    books = {}

    for source in (
        "EMA_CROSS",
        "SAR",
    ):
        print(
            f"building OPPOSITE EXIT SOURCE book "
            f"source={source} frames=12H,1D ..."
        )

        book, _meta = (
            m.build_research_six_frame_signal_book(
                cache=data["cache"],
                signal_fn=_exit_source_signal_fn(
                    source
                ),
                frames=OWNER_FRAMES,
                data_mode="CACHE_MATERIALS",
            )
        )

        books[source] = book

    return books


class ExitSourcePolicy:
    def __init__(
        self,
        config,
        books,
    ):
        self.config = dict(config)
        self.mode = str(
            self.config.get(
                "mode",
                "STANDARD_COMBINED",
            )
        ).upper()

        if self.mode not in (
            "STANDARD_COMBINED",
            "EMA_ONLY",
            "SAR_ONLY",
            "EITHER_OPPOSITE",
        ):
            raise ValueError(
                f"bad exit source mode: {self.mode}"
            )

        self.books = books or {}
        self.stats = {
            "decision_calls": 0,
            "standard_combined_calls": 0,
            "ema_only_calls": 0,
            "sar_only_calls": 0,
            "either_calls": 0,
            "either_opposite_wins": 0,
        }

    @staticmethod
    def _signal_at(
        book,
        symbol,
        tf,
        ts,
    ):
        key = (
            str(symbol).upper(),
            str(tf).upper(),
        )

        pair = book.get(key)
        if pair is None:
            return 0

        ts_arr, sig_arr = pair
        ts_arr = np.asarray(
            ts_arr,
            dtype=np.int64,
        )
        sig_arr = np.asarray(
            sig_arr,
            dtype=np.int8,
        )

        i = int(
            np.searchsorted(
                ts_arr,
                int(ts),
                side="right",
            )
            - 1
        )

        if i < 0:
            return 0

        return int(
            sig_arr[i]
        )

    def decision(
        self,
        symbol,
        owner_tf,
        ts,
        old_side,
        frame_state,
    ):
        self.stats[
            "decision_calls"
        ] += 1

        symbol_u = str(
            symbol
        ).upper()
        owner_tf_u = str(
            owner_tf
        ).upper()
        old_side_i = int(
            old_side
        )

        if self.mode == "STANDARD_COMBINED":
            self.stats[
                "standard_combined_calls"
            ] += 1
            return int(
                frame_state.get(
                    (
                        symbol_u,
                        owner_tf_u,
                    ),
                    0,
                )
            )

        ema = self._signal_at(
            self.books[
                "EMA_CROSS"
            ],
            symbol_u,
            owner_tf_u,
            ts,
        )
        sar = self._signal_at(
            self.books[
                "SAR"
            ],
            symbol_u,
            owner_tf_u,
            ts,
        )

        if self.mode == "EMA_ONLY":
            self.stats[
                "ema_only_calls"
            ] += 1
            return int(ema)

        if self.mode == "SAR_ONLY":
            self.stats[
                "sar_only_calls"
            ] += 1
            return int(sar)

        self.stats[
            "either_calls"
        ] += 1

        opposite = -old_side_i

        # "Either" means any opposite component is enough to close.
        if (
            int(ema) == opposite
            or int(sar) == opposite
        ):
            self.stats[
                "either_opposite_wins"
            ] += 1
            return int(opposite)

        # No opposite component. Same-side confirmation is harmless.
        if (
            int(ema) == old_side_i
            or int(sar) == old_side_i
        ):
            return int(
                old_side_i
            )

        return 0


class _ExitSourceDecisionTransformer(
    ast.NodeTransformer
):
    """
    Replace exactly one owner-position decision assignment:

        decision = int(frame_state.get((symbol, owner_tf), 0))

    with:

        decision = int(
            _pipeline_exit_source_decision(
                symbol, owner_tf, ts, old_side, frame_state
            )
        )

    Fail closed if the sealed source changes.
    """

    def __init__(self):
        super().__init__()
        self.replaced = 0

    @staticmethod
    def _is_target_assign(node):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(
                node.targets[0],
                ast.Name,
            )
            and node.targets[0].id
            == "decision"
        ):
            return False

        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(
                value.func,
                ast.Name,
            )
            and value.func.id == "int"
            and len(value.args) == 1
        ):
            return False

        inner = value.args[0]
        if not (
            isinstance(inner, ast.Call)
            and isinstance(
                inner.func,
                ast.Attribute,
            )
            and inner.func.attr == "get"
            and isinstance(
                inner.func.value,
                ast.Name,
            )
            and inner.func.value.id
            == "frame_state"
        ):
            return False

        return True

    def visit_Assign(self, node):
        node = self.generic_visit(
            node
        )

        if not self._is_target_assign(
            node
        ):
            return node

        self.replaced += 1

        node.value = ast.Call(
            func=ast.Name(
                id="int",
                ctx=ast.Load(),
            ),
            args=[
                ast.Call(
                    func=ast.Name(
                        id=(
                            "_pipeline_exit_source_decision"
                        ),
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Name(
                            id="symbol",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="owner_tf",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="ts",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="old_side",
                            ctx=ast.Load(),
                        ),
                        ast.Name(
                            id="frame_state",
                            ctx=ast.Load(),
                        ),
                    ],
                    keywords=[],
                )
            ],
            keywords=[],
        )

        return node


class ExitSourceTimingFastEngine(
    FastEngineV2
):
    def __init__(
        self,
        module,
        exit_source_policy,
        timing_policy,
    ):
        self.exit_source_policy = (
            exit_source_policy
        )
        self.opposite_timing_policy = (
            timing_policy
        )
        super().__init__(
            module
        )

    def _build_simulator(self):
        src = textwrap.dedent(
            inspect.getsource(
                self.m.simulate_live_multiframe
            )
        )
        tree = ast.parse(
            src
        )

        # 1) Verified FAST V2 patch.
        fast_transformer = (
            _FastPatchTransformer()
        )
        tree = fast_transformer.visit(
            tree
        )

        # 2) Replace owner opposite-close source.
        exit_transformer = (
            _ExitSourceDecisionTransformer()
        )
        tree = exit_transformer.visit(
            tree
        )

        # 3) Preserve the already-selected reopen timing.
        timing_transformer = (
            _OppositeReopenTransformer()
        )
        tree = timing_transformer.visit(
            tree
        )

        ast.fix_missing_locations(
            tree
        )

        fast_actual = {
            "pre_guard_removed":
                fast_transformer.pre_guard_removed,
            "risk_event_appends_removed":
                fast_transformer.risk_event_appends_removed,
            "liq_diagnostics_disabled":
                fast_transformer.liq_diagnostics_disabled,
        }

        if fast_actual != (
            self.EXPECTED_PATCH_COUNTS
        ):
            raise RuntimeError(
                "EXIT SOURCE FAST PATCH TARGET ERROR: "
                f"expected={self.EXPECTED_PATCH_COUNTS} "
                f"actual={fast_actual}"
            )

        if (
            exit_transformer.replaced
            != 1
        ):
            raise RuntimeError(
                "EXIT SOURCE DECISION PATCH TARGET ERROR: "
                f"expected=1 "
                f"actual={exit_transformer.replaced}"
            )

        if (
            timing_transformer.replaced
            != 1
        ):
            raise RuntimeError(
                "EXIT SOURCE TIMING PATCH TARGET ERROR: "
                f"expected=1 "
                f"actual={timing_transformer.replaced}"
            )

        self.patch_stats = dict(
            fast_actual
        )
        self.patch_stats[
            "exit_source_decision_replaced"
        ] = int(
            exit_transformer.replaced
        )
        self.patch_stats[
            "opposite_reopen_replaced"
        ] = int(
            timing_transformer.replaced
        )

        code = compile(
            tree,
            (
                "<research_pipeline_v2_"
                "opposite_exit_source_simulator>"
            ),
            "exec",
        )

        ns = dict(
            self.m.__dict__
        )

        ns["flatten_brackets"] = (
            self._cached_flatten_brackets
        )
        ns["maintenance_margin"] = (
            self._fast_maintenance_margin
        )
        ns["get_mark"] = (
            self._fast_get_mark
        )
        ns["_cross_trigger_price"] = (
            self._fast_cross_trigger_price
        )
        ns[
            "_lab_cross_guard_scenarios"
        ] = (
            self._cached_guard_scenarios
        )

        ns[
            "_pipeline_exit_source_decision"
        ] = (
            self.exit_source_policy.decision
        )
        ns[
            "_pipeline_opposite_reopen"
        ] = (
            self.opposite_timing_policy.reopen
        )

        exec(
            code,
            ns,
            ns,
        )

        fn = ns.get(
            "simulate_live_multiframe"
        )
        if not callable(fn):
            raise RuntimeError(
                "EXIT SOURCE simulator clone missing"
            )

        return fn


def build_exit_source_timing_engine(
    module,
    exit_config,
    timing_config,
    books,
):
    exit_policy = ExitSourcePolicy(
        exit_config,
        books,
    )
    timing_policy = OppositeTimingPolicy(
        timing_config
    )
    engine = ExitSourceTimingFastEngine(
        module,
        exit_policy,
        timing_policy,
    )
    return (
        engine,
        exit_policy,
        timing_policy,
    )


def resolve_exit_source_parents(
    data,
):
    """
    Resolve the two timing champions produced by the prior stage.

    Parent FULL and SCREEN metrics are reused. No duplicate parent runs.
    """
    trailing_path = (
        PIPE_RESULTS
        / "trailing_latest.json"
    )
    timing_path = (
        PIPE_RESULTS
        / "opposite_timing_latest.json"
    )

    if not trailing_path.exists():
        raise RuntimeError(
            "trailing_latest.json missing"
        )
    if not timing_path.exists():
        raise RuntimeError(
            "opposite_timing_latest.json missing"
        )

    trailing = _load_json_file(
        trailing_path
    )
    timing = _load_json_file(
        timing_path
    )

    current_hash = (
        data["fingerprint"].get(
            "fingerprint_hash"
        )
    )

    for label, payload in (
        ("TRAILING", trailing),
        ("OPPOSITE_TIMING", timing),
    ):
        prior_hash = (
            payload.get(
                "data_fingerprint",
                {},
            ).get(
                "fingerprint_hash"
            )
        )
        if prior_hash != current_hash:
            raise RuntimeError(
                f"{label} RESULT FINGERPRINT MISMATCH: "
                f"{prior_hash} != {current_hash}"
            )

    parents = []

    timing_full_map = {
        str(row["candidate"]):
            row
        for row in timing.get(
            "full_results",
            [],
        )
    }
    timing_screen_map = {
        str(row["candidate"]):
            row
        for row in timing.get(
            "screen_results",
            [],
        )
    }
    trailing_full_map = {
        str(row["candidate"]):
            row
        for row in trailing.get(
            "full_results",
            [],
        )
    }
    trailing_screen_map = {
        str(row["candidate"]):
            row
        for row in trailing.get(
            "screen_results",
            [],
        )
    }

    for champion in timing.get(
        "timing_champions",
        [],
    ):
        parent_trailing = str(
            champion[
                "parent_trailing"
            ]
        )
        timing_mode = str(
            champion[
                "timing"
            ]
        ).upper()
        candidate = str(
            champion[
                "candidate"
            ]
        )
        source = str(
            champion[
                "source"
            ]
        )

        trailing_row = (
            trailing_full_map.get(
                parent_trailing
            )
        )
        if trailing_row is None:
            raise RuntimeError(
                "missing trailing FULL parent: "
                + parent_trailing
            )

        trailing_config = dict(
            trailing_row[
                "config"
            ]
        )

        if (
            source
            == "OPPOSITE_TIMING_LAB"
        ):
            full_row = (
                timing_full_map.get(
                    candidate
                )
            )
            screen_row = (
                timing_screen_map.get(
                    candidate
                )
            )
        else:
            full_row = (
                trailing_full_map.get(
                    candidate
                )
            )
            screen_row = (
                trailing_screen_map.get(
                    candidate
                )
            )

        if (
            full_row is None
            or screen_row is None
        ):
            raise RuntimeError(
                "missing timing parent metrics: "
                + candidate
            )

        parents.append({
            "parent_trailing":
                parent_trailing,
            "parent_candidate":
                candidate,
            "timing_mode":
                timing_mode,
            "timing_source":
                source,
            "timing_config": {
                "description":
                    "Resolved prior timing champion",
                "mode":
                    timing_mode,
                "complexity":
                    0
                    if timing_mode
                    == "IMMEDIATE"
                    else 1,
            },
            "trailing_config":
                trailing_config,
            "screen_metrics":
                dict(
                    screen_row[
                        "metrics"
                    ]
                ),
            "full_metrics":
                dict(
                    full_row[
                        "metrics"
                    ]
                ),
        })

    if not parents:
        raise RuntimeError(
            "no opposite timing champions resolved"
        )

    print()
    print("=" * 112)
    print(
        "OPPOSITE EXIT SOURCE PARENTS"
    )
    print("=" * 112)

    for parent in parents:
        print(
            parent[
                "parent_candidate"
            ],
            "| timing=",
            parent[
                "timing_mode"
            ],
            "| trailing=",
            (
                parent[
                    "trailing_config"
                ][
                    "trailing_start_pct"
                ]
            ),
            "/",
            (
                parent[
                    "trailing_config"
                ][
                    "trailing_callback_pct"
                ]
            ),
            "| FULL equity=",
            (
                f"${parent['full_metrics']['final_equity']:,.2f}"
            ),
            "| MDD=",
            (
                f"{parent['full_metrics']['realized_mdd_pct']:+.4f}%"
            ),
        )

    return (
        trailing,
        timing,
        parents,
    )


def make_exit_source_kwargs(
    data,
    parent,
    start_ts,
    end_ts,
):
    cfg = parent[
        "trailing_config"
    ]

    return {
        "signal_book":
            data["event_book"],
        "symbols":
            data["symbols"],
        "snapshot":
            data["snapshot"],
        "market":
            data["market"],
        "start_ts":
            int(start_ts),
        "end_ts":
            int(end_ts),
        "leverage_map":
            LEVERAGE,
        "frames":
            OWNER_FRAMES,
        "active_count":
            35,
        "start_equity":
            10000.0,
        "collect_trade_log":
            False,
        "trailing_start_pct":
            float(
                cfg[
                    "trailing_start_pct"
                ]
            ),
        "trailing_callback_pct":
            float(
                cfg[
                    "trailing_callback_pct"
                ]
            ),
        "state_reentry_book":
            data[
                "owner_state_book"
            ],
        "state_reentry_pullback_pct":
            0.0,
    }


def run_or_resume_exit_source(
    *,
    data,
    books,
    stage,
    candidate,
    scope,
    parent,
    exit_config,
    start_ts,
    end_ts,
    resume,
):
    combined_config = {
        "exit_source":
            exit_config,
        "timing":
            parent[
                "timing_config"
            ],
        "trailing":
            parent[
                "trailing_config"
            ],
        "control_sl":
            "STANDARD",
        "entry_gate":
            "STANDARD",
        "entry_event":
            "FINAL_B_STANDARD",
        "reentry":
            "STANDARD",
    }

    (
        key,
        path,
        checkpoint,
    ) = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=
            combined_config,
        data_fingerprint=
            data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {scope} {candidate}"
        )
        return {
            "metrics":
                checkpoint[
                    "metrics"
                ],
            "elapsed_seconds":
                float(
                    checkpoint.get(
                        "elapsed_seconds",
                        0.0,
                    )
                ),
            "extra":
                checkpoint.get(
                    "extra",
                    {},
                ),
            "checkpoint":
                str(path),
            "resumed":
                True,
        }

    (
        engine,
        exit_policy,
        timing_policy,
    ) = build_exit_source_timing_engine(
        m,
        exit_config,
        parent[
            "timing_config"
        ],
        books,
    )

    kwargs = (
        make_exit_source_kwargs(
            data,
            parent,
            start_ts,
            end_ts,
        )
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        f"{scope}_{candidate}",
    )

    metrics = extract_metrics(
        result
    )

    extra = {
        "patch_stats":
            dict(
                engine.patch_stats
            ),
        "exit_source_policy_stats":
            dict(
                exit_policy.stats
            ),
        "timing_policy_stats":
            dict(
                timing_policy.stats
            ),
        "exit_source_mode":
            exit_config[
                "mode"
            ],
        "timing_mode":
            parent[
                "timing_mode"
            ],
        "parent_candidate":
            parent[
                "parent_candidate"
            ],
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=scope,
        candidate_config=
            combined_config,
        data_fingerprint=
            data["fingerprint"],
        start_ts=start_ts,
        end_ts=end_ts,
        metrics=metrics,
        elapsed_seconds=elapsed,
        extra=extra,
    )

    return {
        "metrics":
            metrics,
        "elapsed_seconds":
            float(elapsed),
        "extra":
            extra,
        "checkpoint":
            str(path),
        "resumed":
            False,
    }


def run_exit_source_parity(
    data,
    books,
    parents,
    args,
):
    """
    Fail closed for BOTH resolved parent timing modes.

    Baseline:
      OppositeTimingFastEngine with the already-selected timing.

    Strict replica:
      ExitSourceTimingFastEngine with STANDARD_COMBINED source
      and the same already-selected timing.
    """
    parity_start = max(
        int(
            data["start_ts"]
        ),
        int(
            data["end_ts"]
            - int(
                args.parity_days
            )
            * 86400
        ),
    )

    rows = []
    all_ok = True

    for parent in parents:
        timing_config = (
            parent[
                "timing_config"
            ]
        )

        base_policy = (
            OppositeTimingPolicy(
                timing_config
            )
        )
        base_engine = (
            OppositeTimingFastEngine(
                m,
                base_policy,
            )
        )

        (
            strict_engine,
            exit_policy,
            strict_timing_policy,
        ) = (
            build_exit_source_timing_engine(
                m,
                EXIT_SOURCE_STANDARD,
                timing_config,
                books,
            )
        )

        kwargs = (
            make_exit_source_kwargs(
                data,
                parent,
                parity_start,
                data["end_ts"],
            )
        )

        label = (
            parent[
                "parent_candidate"
            ]
        )

        base_result, base_sec = timed_run(
            base_engine.run,
            kwargs,
            (
                "EXIT_SOURCE_PARITY_BASE_"
                + label
            ),
        )
        strict_result, strict_sec = (
            timed_run(
                strict_engine.run,
                kwargs,
                (
                    "EXIT_SOURCE_PARITY_STRICT_"
                    + label
                ),
            )
        )

        base_metrics = (
            extract_metrics(
                base_result
            )
        )
        strict_metrics = (
            extract_metrics(
                strict_result
            )
        )

        ok, diffs = (
            compare_metrics(
                base_metrics,
                strict_metrics,
            )
        )

        all_ok = (
            all_ok
            and bool(ok)
        )

        print()
        print(
            "STRICT_EXIT_SOURCE_PARITY",
            label,
            "=",
            "PASS" if ok else "FAIL",
        )
        print(
            "STRICT_EXIT_SOURCE_DIFFS",
            label,
            "=",
            diffs,
        )

        rows.append({
            "parent":
                label,
            "timing_mode":
                parent[
                    "timing_mode"
                ],
            "status":
                "PASS"
                if ok
                else "FAIL",
            "diffs":
                diffs,
            "base_seconds":
                float(
                    base_sec
                ),
            "strict_seconds":
                float(
                    strict_sec
                ),
            "base_metrics":
                base_metrics,
            "strict_metrics":
                strict_metrics,
            "exit_policy_stats":
                exit_policy.stats,
            "timing_policy_stats":
                strict_timing_policy.stats,
        })

    payload = {
        "schema":
            "OPPOSITE_EXIT_SOURCE_PARITY_V2",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "status":
            "PASS"
            if all_ok
            else "FAIL",
        "days":
            int(
                args.parity_days
            ),
        "rows":
            rows,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
    }

    atomic_write_json(
        PIPE_RESULTS
        / "opposite_exit_source_parity_latest.json",
        payload,
    )

    print()
    print(
        "STRICT_OPPOSITE_EXIT_SOURCE_PARITY =",
        "PASS"
        if all_ok
        else "FAIL",
    )

    if not all_ok:
        raise SystemExit(
            "STRICT_OPPOSITE_EXIT_SOURCE_PARITY_FAIL"
        )

    return payload


def exit_source_screen_score(
    metrics,
    parent_screen_metrics,
):
    eq = max(
        float(
            metrics[
                "final_equity"
            ]
        ),
        1e-12,
    )
    mdd = max(
        abs(
            float(
                metrics[
                    "realized_mdd_pct"
                ]
            )
        ),
        1e-9,
    )
    liq = int(
        metrics[
            "liquidations"
        ]
    )

    parent_eq = max(
        float(
            parent_screen_metrics[
                "final_equity"
            ]
        ),
        1e-12,
    )

    relative = math.log(
        eq / parent_eq
    )

    return (
        math.log(eq)
        - math.log(mdd)
        + 0.20 * relative
        - liq * 100.0
    )


def run_opposite_exit_source_lab(
    data,
    args,
):
    stage = (
        "opposite_exit_source_v1"
    )

    (
        trailing_payload,
        timing_payload,
        parents,
    ) = resolve_exit_source_parents(
        data
    )

    # Build two owner-TF books once.
    with Heartbeat(
        "BUILD_OPPOSITE_EXIT_SOURCE_BOOKS"
    ):
        books = (
            build_exit_source_books(
                data
            )
        )

    # Fail-closed parity for both parent timing branches.
    parity_payload = (
        run_exit_source_parity(
            data,
            books,
            parents,
            args,
        )
    )

    screen_start = max(
        int(
            data["start_ts"]
        ),
        int(
            data["end_ts"]
            - int(
                args.screen_days
            )
            * 86400
        ),
    )

    screen_rows = []

    for parent in parents:
        print()
        print("=" * 112)
        print(
            "EXIT SOURCE PARENT =",
            parent[
                "parent_candidate"
            ],
        )
        print(
            "TIMING             =",
            parent[
                "timing_mode"
            ],
        )
        print(
            "PARENT SCREEN      =",
            (
                f"${parent['screen_metrics']['final_equity']:,.2f} "
                f"/ MDD "
                f"{parent['screen_metrics']['realized_mdd_pct']:+.4f}%"
            ),
        )
        print(
            "PARENT FULL        =",
            (
                f"${parent['full_metrics']['final_equity']:,.2f} "
                f"/ MDD "
                f"{parent['full_metrics']['realized_mdd_pct']:+.4f}%"
            ),
        )
        print("=" * 112)

        for source_name, source_config in (
            EXIT_SOURCE_CANDIDATES.items()
        ):
            candidate = (
                f"{parent['parent_candidate']}"
                f"__EXIT_{source_name}"
            )

            print()
            print("#" * 112)
            print(
                "OPPOSITE EXIT SOURCE SCREEN CANDIDATE =",
                candidate,
            )
            print(
                "SOURCE                                =",
                source_name,
            )
            print(
                "DESCRIPTION                           =",
                source_config[
                    "description"
                ],
            )
            print("#" * 112)

            run = (
                run_or_resume_exit_source(
                    data=data,
                    books=books,
                    stage=stage,
                    candidate=
                        candidate,
                    scope=(
                        f"SCREEN_"
                        f"{args.screen_days}D_35"
                    ),
                    parent=
                        parent,
                    exit_config=
                        source_config,
                    start_ts=
                        screen_start,
                    end_ts=
                        data["end_ts"],
                    resume=
                        args.resume,
                )
            )

            metrics = (
                run[
                    "metrics"
                ]
            )

            verdict = (
                screen_verdict(
                    metrics,
                    parent[
                        "screen_metrics"
                    ],
                )
            )

            score = (
                exit_source_screen_score(
                    metrics,
                    parent[
                        "screen_metrics"
                    ],
                )
            )

            print_metrics(
                (
                    "OPPOSITE EXIT SOURCE SCREEN - "
                    + candidate
                ),
                metrics,
                run[
                    "elapsed_seconds"
                ],
            )
            print(
                "SCREEN VERDICT =",
                verdict,
            )
            print(
                "SCREEN SCORE   =",
                f"{score:.6f}",
            )

            screen_rows.append({
                "candidate":
                    candidate,
                "parent_candidate":
                    parent[
                        "parent_candidate"
                    ],
                "parent":
                    parent,
                "exit_source":
                    source_name,
                "exit_config":
                    source_config,
                "metrics":
                    metrics,
                "elapsed_seconds":
                    run[
                        "elapsed_seconds"
                    ],
                "screen_verdict":
                    verdict,
                "screen_score":
                    score,
                "checkpoint":
                    run[
                        "checkpoint"
                    ],
                "resumed":
                    run[
                        "resumed"
                    ],
                "extra":
                    run[
                        "extra"
                    ],
            })

    # At most one new exit-source finalist per timing parent.
    selected = []

    for parent in parents:
        rows = [
            row
            for row in screen_rows
            if (
                row[
                    "parent_candidate"
                ]
                == parent[
                    "parent_candidate"
                ]
                and row[
                    "screen_verdict"
                ]
                == "SURVIVE_SCREEN"
            )
        ]

        rows.sort(
            key=lambda row:
                -float(
                    row[
                        "screen_score"
                    ]
                )
        )

        if rows:
            selected.append(
                rows[0]
            )

    selected = selected[
        : int(
            args.exit_source_full_top
        )
    ]

    print()
    print("=" * 112)
    print(
        "OPPOSITE EXIT SOURCE SCREEN RESULT"
    )
    print("=" * 112)

    ranked = sorted(
        screen_rows,
        key=lambda row:
            -float(
                row[
                    "screen_score"
                ]
            ),
    )

    for i, row in enumerate(
        ranked,
        1,
    ):
        print(
            f"{i:2d}. "
            f"{row['candidate']:70s} "
            f"{row['screen_verdict']:28s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}%"
        )

    print(
        "FULL_35_SELECTED =",
        [
            row[
                "candidate"
            ]
            for row in selected
        ],
    )

    full_rows = []

    for row in selected:
        candidate = (
            row[
                "candidate"
            ]
        )
        parent = (
            row[
                "parent"
            ]
        )

        print()
        print("#" * 112)
        print(
            "OPPOSITE EXIT SOURCE FULL 35 CANDIDATE =",
            candidate,
        )
        print(
            "PARENT                                =",
            parent[
                "parent_candidate"
            ],
        )
        print(
            "SOURCE                                =",
            row[
                "exit_source"
            ],
        )
        print("#" * 112)

        run = (
            run_or_resume_exit_source(
                data=data,
                books=books,
                stage=stage,
                candidate=
                    candidate,
                scope=
                    "FULL_35",
                parent=
                    parent,
                exit_config=
                    row[
                        "exit_config"
                    ],
                start_ts=
                    data[
                        "start_ts"
                    ],
                end_ts=
                    data[
                        "end_ts"
                    ],
                resume=
                    args.resume,
            )
        )

        metrics = (
            run[
                "metrics"
            ]
        )
        parent_metrics = (
            parent[
                "full_metrics"
            ]
        )

        verdict = (
            opposite_parent_full_verdict(
                metrics,
                parent_metrics,
            )
        )

        eq_delta = (
            float(
                metrics[
                    "final_equity"
                ]
            )
            - float(
                parent_metrics[
                    "final_equity"
                ]
            )
        )
        mdd_delta = (
            float(
                metrics[
                    "realized_mdd_pct"
                ]
            )
            - float(
                parent_metrics[
                    "realized_mdd_pct"
                ]
            )
        )

        print_metrics(
            (
                "OPPOSITE EXIT SOURCE FULL 35 - "
                + candidate
            ),
            metrics,
            run[
                "elapsed_seconds"
            ],
        )
        print(
            "FULL VERDICT VS PARENT =",
            verdict[
                "verdict"
            ],
        )
        print(
            "FULL REASON            =",
            verdict[
                "reason"
            ],
        )
        print(
            "VS PARENT              =",
            (
                f"equity {eq_delta:+,.2f}, "
                f"MDD {mdd_delta:+.6f}pp, "
                f"liq "
                f"{int(metrics['liquidations']) - int(parent_metrics['liquidations']):+d}"
            ),
        )

        full_row = {
            **row,
            "metrics":
                metrics,
            "elapsed_seconds":
                run[
                    "elapsed_seconds"
                ],
            "verdict":
                verdict,
            "delta_vs_parent": {
                "final_equity_delta":
                    eq_delta,
                "mdd_delta_pp":
                    mdd_delta,
                "liquidation_delta":
                    int(
                        metrics[
                            "liquidations"
                        ]
                    )
                    - int(
                        parent_metrics[
                            "liquidations"
                        ]
                    ),
            },
            "checkpoint":
                run[
                    "checkpoint"
                ],
            "resumed":
                run[
                    "resumed"
                ],
            "extra":
                run[
                    "extra"
                ],
        }

        full_rows.append(
            full_row
        )

        append_master_record({
            "schema":
                "RESEARCH_DECISION_V2",
            "stage":
                stage,
            "candidate":
                candidate,
            "scope":
                "FULL_35_DECISION",
            "parent":
                parent[
                    "parent_candidate"
                ],
            "exit_source":
                row[
                    "exit_source"
                ],
            "metrics":
                metrics,
            "verdict":
                verdict,
            "delta_vs_parent":
                full_row[
                    "delta_vs_parent"
                ],
            "source_checkpoint":
                run[
                    "checkpoint"
                ],
            "data_fingerprint":
                data[
                    "fingerprint"
                ],
        })

    # Resolve one structure champion per parent.
    structure_champions = []

    for parent in parents:
        parent_name = (
            parent[
                "parent_candidate"
            ]
        )

        contenders = [
            row
            for row in full_rows
            if (
                row[
                    "parent_candidate"
                ]
                == parent_name
                and row[
                    "verdict"
                ][
                    "verdict"
                ]
                in (
                    "KEEP",
                    "REVIEW",
                )
            )
        ]

        if contenders:
            contenders.sort(
                key=lambda row: (
                    0
                    if row[
                        "verdict"
                    ][
                        "verdict"
                    ]
                    == "KEEP"
                    else 1,
                    -float(
                        row[
                            "metrics"
                        ][
                            "final_equity"
                        ]
                    ),
                )
            )

            best = contenders[0]

            structure_champions.append({
                "candidate":
                    best[
                        "candidate"
                    ],
                "parent":
                    parent_name,
                "exit_source":
                    best[
                        "exit_source"
                    ],
                "timing":
                    parent[
                        "timing_mode"
                    ],
                "trailing_config":
                    parent[
                        "trailing_config"
                    ],
                "metrics":
                    best[
                        "metrics"
                    ],
                "source":
                    "OPPOSITE_EXIT_SOURCE_LAB",
            })
        else:
            structure_champions.append({
                "candidate":
                    parent_name,
                "parent":
                    parent_name,
                "exit_source":
                    "STANDARD_COMBINED",
                "timing":
                    parent[
                        "timing_mode"
                    ],
                "trailing_config":
                    parent[
                        "trailing_config"
                    ],
                "metrics":
                    parent[
                        "full_metrics"
                    ],
                "source":
                    "PRIOR_PARENT_FULL",
            })

    # Rank only for display. Carry both structure branches into Universe Lab.
    structure_champions.sort(
        key=lambda row: (
            -float(
                row[
                    "metrics"
                ][
                    "final_equity"
                ]
            ),
            abs(
                float(
                    row[
                        "metrics"
                    ][
                        "realized_mdd_pct"
                    ]
                )
            ),
        )
    )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_OPPOSITE_EXIT_SOURCE_LAB",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "parity":
            parity_payload,
        "screen_days":
            int(
                args.screen_days
            ),
        "parents":
            parents,
        "screen_results":
            screen_rows,
        "full_selected": [
            row[
                "candidate"
            ]
            for row in selected
        ],
        "full_results":
            full_rows,
        "structure_champions":
            structure_champions,
        "next_action":
            "UNIVERSE_SIZE_LAB",
        "next_note": (
            "Carry both resolved structure branches into coarse Universe "
            "15/20/25/30/35. Fixed 10 symbols stay fixed."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"opposite_exit_source_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "opposite_exit_source_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            "BATCH_SUMMARY",
        "scope":
            "SCREEN_THEN_FULL_35",
        "result_path":
            str(
                timestamped
            ),
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "structure_champions":
            structure_champions,
        "next_action":
            "UNIVERSE_SIZE_LAB",
    })

    print()
    print("=" * 112)
    print(
        "OPPOSITE SIGNAL EXIT SOURCE LAB COMPLETE"
    )
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "STRUCTURE CHAMPIONS =",
        [
            {
                "candidate":
                    row[
                        "candidate"
                    ],
                "exit_source":
                    row[
                        "exit_source"
                    ],
                "timing":
                    row[
                        "timing"
                    ],
                "equity":
                    row[
                        "metrics"
                    ][
                        "final_equity"
                    ],
                "mdd":
                    row[
                        "metrics"
                    ][
                        "realized_mdd_pct"
                    ],
            }
            for row in structure_champions
        ],
    )
    print(
        "NEXT ACTION        = UNIVERSE_SIZE_LAB"
    )

    return summary




# =============================================================================
# UNIVERSE SIZE LAB V1
# =============================================================================
#
# Coarse universe sizes only:
#   15 / 20 / 25 / 30 / 35
#
# Composition:
#   fixed 10 symbols always remain first
#   + current snapshot-ranked dynamic symbols
#
# Example:
#   15 = fixed10 + dynamic5
#   20 = fixed10 + dynamic10
#   ...
#   35 = fixed10 + dynamic25
#
# No fine tuning such as 27/28/29.
#
# Both surviving structure branches from Opposite Exit Source Lab are tested.
# 35-symbol parent results are REUSED rather than recomputed because they are
# identical already-FULL-tested configurations.
#
# Every smaller universe uses:
#   symbols      = first N current active_symbols
#   active_count = N
#
# This intentionally preserves the simulator's portfolio allocation geometry,
# where available wallet is divided by active_count.
# =============================================================================

UNIVERSE_SIZES = (
    15,
    20,
    25,
    30,
    35,
)

FIXED_10_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LTCUSDT",
    "LINKUSDT",
)


def resolve_universe_structures(
    data,
):
    path = (
        PIPE_RESULTS
        / "opposite_exit_source_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "opposite_exit_source_latest.json missing"
        )

    payload = _load_json_file(
        path
    )

    current_hash = (
        data["fingerprint"].get(
            "fingerprint_hash"
        )
    )
    prior_hash = (
        payload.get(
            "data_fingerprint",
            {},
        ).get(
            "fingerprint_hash"
        )
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "OPPOSITE EXIT SOURCE RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    structures = list(
        payload.get(
            "structure_champions",
            [],
        )
    )

    if not structures:
        raise RuntimeError(
            "no structure_champions in opposite_exit_source_latest.json"
        )

    print()
    print("=" * 112)
    print("UNIVERSE STRUCTURE PARENTS")
    print("=" * 112)

    for row in structures:
        print(
            row["candidate"],
            "| exit=",
            row["exit_source"],
            "| timing=",
            row["timing"],
            "| trailing=",
            row["trailing_config"][
                "trailing_start_pct"
            ],
            "/",
            row["trailing_config"][
                "trailing_callback_pct"
            ],
            "| FULL35 equity=",
            f"${row['metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['metrics']['realized_mdd_pct']:+.4f}%",
        )

    return (
        payload,
        structures,
    )


def resolve_snapshot_universe_order(
    data,
):
    raw = list(
        data["snapshot"].get(
            "active_symbols",
            [],
        )
    )

    ordered = [
        str(x).upper()
        for x in raw
    ]

    if len(ordered) != 35:
        raise RuntimeError(
            f"expected snapshot active_symbols=35, got {len(ordered)}"
        )

    if len(set(ordered)) != 35:
        raise RuntimeError(
            "snapshot active_symbols contains duplicates"
        )

    cache_set = set(
        str(x).upper()
        for x in data["symbols"]
    )

    if set(ordered) != cache_set:
        missing = sorted(
            cache_set
            - set(ordered)
        )
        extra = sorted(
            set(ordered)
            - cache_set
        )
        raise RuntimeError(
            "snapshot/cache symbol set mismatch "
            f"missing={missing} extra={extra}"
        )

    fixed_prefix = tuple(
        ordered[:10]
    )

    if fixed_prefix != FIXED_10_SYMBOLS:
        raise RuntimeError(
            "FIXED10 PREFIX PARITY FAIL: "
            f"expected={FIXED_10_SYMBOLS} "
            f"actual={fixed_prefix}"
        )

    print()
    print("=" * 112)
    print("UNIVERSE ORDER")
    print("=" * 112)
    print(
        "FIXED10 =",
        list(
            FIXED_10_SYMBOLS
        ),
    )
    print(
        "DYNAMIC25 =",
        ordered[10:],
    )

    return ordered


def _timing_config_from_mode(
    mode,
):
    mode_u = str(
        mode
    ).upper()

    if mode_u == "IMMEDIATE":
        return dict(
            OPPOSITE_STANDARD_MODE
        )

    if mode_u in OPPOSITE_TIMING_MODES:
        return dict(
            OPPOSITE_TIMING_MODES[
                mode_u
            ]
        )

    raise RuntimeError(
        f"unknown timing mode: {mode_u}"
    )


def _exit_config_from_source(
    source,
):
    source_u = str(
        source
    ).upper()

    if source_u == "STANDARD_COMBINED":
        return dict(
            EXIT_SOURCE_STANDARD
        )

    if source_u in EXIT_SOURCE_CANDIDATES:
        return dict(
            EXIT_SOURCE_CANDIDATES[
                source_u
            ]
        )

    raise RuntimeError(
        f"unknown exit source: {source_u}"
    )


def make_universe_parent(
    structure,
):
    return {
        "parent_candidate":
            str(
                structure[
                    "candidate"
                ]
            ),
        "timing_mode":
            str(
                structure[
                    "timing"
                ]
            ).upper(),
        "timing_config":
            _timing_config_from_mode(
                structure[
                    "timing"
                ]
            ),
        "trailing_config":
            dict(
                structure[
                    "trailing_config"
                ]
            ),
        "exit_source":
            str(
                structure[
                    "exit_source"
                ]
            ).upper(),
        "exit_config":
            _exit_config_from_source(
                structure[
                    "exit_source"
                ]
            ),
        "full35_metrics":
            dict(
                structure[
                    "metrics"
                ]
            ),
        "source":
            str(
                structure.get(
                    "source",
                    "",
                )
            ),
    }


def make_universe_kwargs(
    data,
    parent,
    universe_symbols,
    universe_size,
):
    cfg = parent[
        "trailing_config"
    ]

    return {
        "signal_book":
            data["event_book"],
        "symbols":
            list(
                universe_symbols
            ),
        "snapshot":
            data["snapshot"],
        "market":
            data["market"],
        "start_ts":
            int(
                data["start_ts"]
            ),
        "end_ts":
            int(
                data["end_ts"]
            ),
        "leverage_map":
            LEVERAGE,
        "frames":
            OWNER_FRAMES,
        "active_count":
            int(
                universe_size
            ),
        "start_equity":
            10000.0,
        "collect_trade_log":
            False,
        "trailing_start_pct":
            float(
                cfg[
                    "trailing_start_pct"
                ]
            ),
        "trailing_callback_pct":
            float(
                cfg[
                    "trailing_callback_pct"
                ]
            ),
        "state_reentry_book":
            data[
                "owner_state_book"
            ],
        "state_reentry_pullback_pct":
            0.0,
    }


def run_or_resume_universe(
    *,
    data,
    books,
    stage,
    candidate,
    parent,
    universe_size,
    universe_symbols,
    resume,
):
    candidate_config = {
        "structure":
            parent[
                "parent_candidate"
            ],
        "timing":
            parent[
                "timing_config"
            ],
        "trailing":
            parent[
                "trailing_config"
            ],
        "exit_source":
            parent[
                "exit_config"
            ],
        "control_sl":
            "STANDARD",
        "entry_gate":
            "STANDARD",
        "entry_event":
            "FINAL_B_STANDARD",
        "reentry":
            "STANDARD",
        "universe_size":
            int(
                universe_size
            ),
        "universe_symbols":
            list(
                universe_symbols
            ),
    }

    (
        key,
        path,
        checkpoint,
    ) = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope=(
            f"FULL_UNIVERSE_{universe_size}"
        ),
        candidate_config=
            candidate_config,
        data_fingerprint=
            data["fingerprint"],
        start_ts=
            data["start_ts"],
        end_ts=
            data["end_ts"],
        resume=resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} U{universe_size} {candidate}"
        )
        return {
            "metrics":
                checkpoint[
                    "metrics"
                ],
            "elapsed_seconds":
                float(
                    checkpoint.get(
                        "elapsed_seconds",
                        0.0,
                    )
                ),
            "extra":
                checkpoint.get(
                    "extra",
                    {},
                ),
            "checkpoint":
                str(path),
            "resumed":
                True,
        }

    (
        engine,
        exit_policy,
        timing_policy,
    ) = build_exit_source_timing_engine(
        m,
        parent[
            "exit_config"
        ],
        parent[
            "timing_config"
        ],
        books,
    )

    kwargs = (
        make_universe_kwargs(
            data,
            parent,
            universe_symbols,
            universe_size,
        )
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        (
            f"FULL_UNIVERSE_{universe_size}_"
            f"{candidate}"
        ),
    )

    metrics = extract_metrics(
        result
    )

    extra = {
        "patch_stats":
            dict(
                engine.patch_stats
            ),
        "exit_source_policy_stats":
            dict(
                exit_policy.stats
            ),
        "timing_policy_stats":
            dict(
                timing_policy.stats
            ),
        "universe_size":
            int(
                universe_size
            ),
        "universe_symbols":
            list(
                universe_symbols
            ),
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope=(
            f"FULL_UNIVERSE_{universe_size}"
        ),
        candidate_config=
            candidate_config,
        data_fingerprint=
            data["fingerprint"],
        start_ts=
            data["start_ts"],
        end_ts=
            data["end_ts"],
        metrics=metrics,
        elapsed_seconds=
            elapsed,
        extra=extra,
    )

    return {
        "metrics":
            metrics,
        "elapsed_seconds":
            float(
                elapsed
            ),
        "extra":
            extra,
        "checkpoint":
            str(path),
        "resumed":
            False,
    }


def _rank_universe_rows(
    rows,
):
    """
    Transparent shortlist selection without fine-tuned scalar weights.

    Zero-liquidation candidates only.
    Keep up to 3 unique representatives:
      1) highest equity
      2) best MDD
      3) best combined ordinal rank on equity + MDD

    Robustness Lab makes the later decision.
    """
    safe = [
        row
        for row in rows
        if int(
            row[
                "metrics"
            ][
                "liquidations"
            ]
        ) == 0
        and float(
            row[
                "metrics"
            ][
                "final_equity"
            ]
        ) > 0.0
    ]

    if not safe:
        return []

    by_equity = sorted(
        safe,
        key=lambda row:
            -float(
                row[
                    "metrics"
                ][
                    "final_equity"
                ]
            ),
    )

    by_mdd = sorted(
        safe,
        key=lambda row:
            -float(
                row[
                    "metrics"
                ][
                    "realized_mdd_pct"
                ]
            ),
    )

    eq_rank = {
        row["candidate"]:
            i
        for i, row in enumerate(
            by_equity,
            1,
        )
    }
    mdd_rank = {
        row["candidate"]:
            i
        for i, row in enumerate(
            by_mdd,
            1,
        )
    }

    balanced = sorted(
        safe,
        key=lambda row: (
            eq_rank[
                row["candidate"]
            ]
            + mdd_rank[
                row["candidate"]
            ],
            eq_rank[
                row["candidate"]
            ],
            mdd_rank[
                row["candidate"]
            ],
        ),
    )[0]

    picks = []

    for row, reason in (
        (
            by_equity[0],
            "BEST_EQUITY",
        ),
        (
            by_mdd[0],
            "BEST_MDD",
        ),
        (
            balanced,
            "BEST_BALANCED_RANK",
        ),
    ):
        if not any(
            x[
                "candidate"
            ]
            == row[
                "candidate"
            ]
            for x in picks
        ):
            picked = dict(
                row
            )
            picked[
                "shortlist_reason"
            ] = reason
            picked[
                "equity_rank"
            ] = eq_rank[
                row[
                    "candidate"
                ]
            ]
            picked[
                "mdd_rank"
            ] = mdd_rank[
                row[
                    "candidate"
                ]
            ]
            picks.append(
                picked
            )

    return picks[:3]


def run_universe_size_lab(
    data,
    args,
):
    stage = (
        "universe_size_v1"
    )

    (
        prior_payload,
        structures,
    ) = resolve_universe_structures(
        data
    )

    ordered_symbols = (
        resolve_snapshot_universe_order(
            data
        )
    )

    parents = [
        make_universe_parent(
            row
        )
        for row in structures
    ]

    # Only EITHER/SAR/EMA branches need component books.
    # Build once because one current champion uses EITHER_OPPOSITE.
    with Heartbeat(
        "BUILD_UNIVERSE_EXIT_SOURCE_BOOKS"
    ):
        books = (
            build_exit_source_books(
                data
            )
        )

    rows = []

    for parent in parents:
        print()
        print("=" * 112)
        print(
            "UNIVERSE STRUCTURE =",
            parent[
                "parent_candidate"
            ],
        )
        print(
            "EXIT / TIMING      =",
            parent[
                "exit_source"
            ],
            "/",
            parent[
                "timing_mode"
            ],
        )
        print(
            "TRAILING           =",
            parent[
                "trailing_config"
            ][
                "trailing_start_pct"
            ],
            "/",
            parent[
                "trailing_config"
            ][
                "trailing_callback_pct"
            ],
        )
        print("=" * 112)

        for universe_size in UNIVERSE_SIZES:
            universe_symbols = (
                ordered_symbols[
                    : int(
                        universe_size
                    )
                ]
            )

            candidate = (
                f"{parent['parent_candidate']}"
                f"__U{universe_size}"
            )

            print()
            print("#" * 112)
            print(
                "UNIVERSE CANDIDATE =",
                candidate,
            )
            print(
                "ACTIVE COUNT       =",
                universe_size,
            )
            print(
                "SYMBOLS            =",
                universe_symbols,
            )
            print("#" * 112)

            if int(
                universe_size
            ) == 35:
                metrics = dict(
                    parent[
                        "full35_metrics"
                    ]
                )

                print_metrics(
                    (
                        "UNIVERSE FULL - "
                        + candidate
                        + " [REUSED FULL35]"
                    ),
                    metrics,
                    0.0,
                )

                row = {
                    "candidate":
                        candidate,
                    "structure":
                        parent[
                            "parent_candidate"
                        ],
                    "universe_size":
                        35,
                    "symbols":
                        list(
                            universe_symbols
                        ),
                    "parent":
                        parent,
                    "metrics":
                        metrics,
                    "elapsed_seconds":
                        0.0,
                    "checkpoint":
                        None,
                    "resumed":
                        True,
                    "reused_full35":
                        True,
                }

            else:
                run = (
                    run_or_resume_universe(
                        data=data,
                        books=books,
                        stage=stage,
                        candidate=
                            candidate,
                        parent=
                            parent,
                        universe_size=
                            universe_size,
                        universe_symbols=
                            universe_symbols,
                        resume=
                            args.resume,
                    )
                )

                metrics = (
                    run[
                        "metrics"
                    ]
                )

                print_metrics(
                    (
                        "UNIVERSE FULL - "
                        + candidate
                    ),
                    metrics,
                    run[
                        "elapsed_seconds"
                    ],
                )

                row = {
                    "candidate":
                        candidate,
                    "structure":
                        parent[
                            "parent_candidate"
                        ],
                    "universe_size":
                        int(
                            universe_size
                        ),
                    "symbols":
                        list(
                            universe_symbols
                        ),
                    "parent":
                        parent,
                    "metrics":
                        metrics,
                    "elapsed_seconds":
                        run[
                            "elapsed_seconds"
                        ],
                    "checkpoint":
                        run[
                            "checkpoint"
                        ],
                    "resumed":
                        run[
                            "resumed"
                        ],
                    "reused_full35":
                        False,
                    "extra":
                        run[
                            "extra"
                        ],
                }

            rows.append(
                row
            )

    print()
    print("=" * 112)
    print(
        "UNIVERSE SIZE RESULT"
    )
    print("=" * 112)

    ranked_equity = sorted(
        rows,
        key=lambda row: (
            int(
                row[
                    "metrics"
                ][
                    "liquidations"
                ]
            ),
            -float(
                row[
                    "metrics"
                ][
                    "final_equity"
                ]
            ),
        ),
    )

    for i, row in enumerate(
        ranked_equity,
        1,
    ):
        print(
            f"{i:2d}. "
            f"{row['candidate']:72s} "
            f"equity=${row['metrics']['final_equity']:,.2f} "
            f"MDD={row['metrics']['realized_mdd_pct']:+.4f}% "
            f"LIQ={int(row['metrics']['liquidations'])}"
        )

    shortlist = (
        _rank_universe_rows(
            rows
        )
    )

    if not shortlist:
        raise RuntimeError(
            "UNIVERSE LAB: no zero-liquidation candidate survived"
        )

    print()
    print(
        "ROBUSTNESS SHORTLIST ="
    )
    for row in shortlist:
        print(
            " -",
            row[
                "candidate"
            ],
            "| reason=",
            row[
                "shortlist_reason"
            ],
            "| equity=",
            f"${row['metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['metrics']['realized_mdd_pct']:+.4f}%",
        )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_UNIVERSE_SIZE_LAB",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "fixed10":
            list(
                FIXED_10_SYMBOLS
            ),
        "snapshot_order":
            list(
                ordered_symbols
            ),
        "universe_sizes":
            list(
                UNIVERSE_SIZES
            ),
        "structure_parents":
            parents,
        "results":
            rows,
        "robustness_shortlist":
            shortlist,
        "next_action":
            "ROBUSTNESS_LAB",
        "next_note": (
            "Run robustness only on the compact shortlist: time slices, "
            "fee/slippage stress, +1H/+2H delay and coarse parameter "
            "neighborhoods. No fine tuning."
        ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"universe_size_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "universe_size_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            "BATCH_SUMMARY",
        "scope":
            "FULL_HISTORY_COARSE_UNIVERSE",
        "result_path":
            str(
                timestamped
            ),
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "robustness_shortlist": [
            {
                "candidate":
                    row[
                        "candidate"
                    ],
                "reason":
                    row[
                        "shortlist_reason"
                    ],
                "equity":
                    row[
                        "metrics"
                    ][
                        "final_equity"
                    ],
                "mdd":
                    row[
                        "metrics"
                    ][
                        "realized_mdd_pct"
                    ],
                "liquidations":
                    row[
                        "metrics"
                    ][
                        "liquidations"
                    ],
            }
            for row in shortlist
        ],
        "next_action":
            "ROBUSTNESS_LAB",
    })

    print()
    print("=" * 112)
    print(
        "UNIVERSE SIZE LAB COMPLETE"
    )
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "NEXT ACTION        = ROBUSTNESS_LAB"
    )

    return summary




# =============================================================================
# ROBUSTNESS LAB V1
# =============================================================================
#
# Candidate source:
#   universe_size_latest.json -> robustness_shortlist
#
# Current shortlist can contain 1..3 representatives. No new optimizer is run.
#
# Stress families:
#
# 1) TIME SLICES
#    Split the available history into three contiguous thirds.
#
# 2) TRANSACTION COST
#    Baseline simulator fee is 5 bp per side (FEE_RATE=0.0005).
#    Run effective 7.5 bp and 10 bp per side.
#    This is a conservative fee+slippage COST PROXY, not an exact fill-price
#    slippage model. It stresses realized transaction cost without changing
#    market highs/lows or liquidation trigger mechanics.
#
# 3) SIGNAL DELAY
#    Shift ALL decision books forward by +1H and +2H:
#      entry EVENT
#      owner STATE
#      opposite-exit source books
#    Market data is not shifted.
#
# 4) TRAILING NEIGHBORHOOD
#    Coarse, not fine-tuned:
#      start:    3 / 4 / 5
#      callback: 1 / 1.5 / 2
#    Only four one-axis neighbors around the selected baseline are tested.
#
# 5) UNIVERSE NEIGHBORS
#    Reuse already-computed adjacent coarse universe sizes where available.
#
# Hard rule:
#    Any actual liquidation or bankruptcy in a robustness run = HARD FAIL.
#
# Soft rules are deliberately coarse and only determine PASS vs REVIEW.
# REVIEW is still carried into Walk-Forward; HARD FAIL is not.
# =============================================================================


def _shift_signal_book(
    book,
    delay_hours,
):
    delay_seconds = int(
        delay_hours
    ) * 3600

    if delay_seconds == 0:
        return book

    shifted = {}

    for key, pair in book.items():
        ts_arr, sig_arr = pair
        shifted[key] = (
            np.asarray(
                ts_arr,
                dtype=np.int64,
            )
            + int(
                delay_seconds
            ),
            np.asarray(
                sig_arr,
                dtype=np.int8,
            ).copy(),
        )

    return shifted


def _robust_time_slices(
    start_ts,
    end_ts,
):
    start_ts = int(
        start_ts
    )
    end_ts = int(
        end_ts
    )

    total_hours = max(
        1,
        (
            end_ts
            - start_ts
        )
        // 3600,
    )

    third = max(
        1,
        total_hours // 3,
    )

    cut1 = (
        start_ts
        + third * 3600
    )
    cut2 = (
        start_ts
        + third * 2 * 3600
    )

    cut1 = min(
        cut1,
        end_ts - 2 * 3600,
    )
    cut2 = min(
        max(
            cut2,
            cut1 + 3600,
        ),
        end_ts - 3600,
    )

    return (
        (
            "TIME_EARLY",
            start_ts,
            cut1,
        ),
        (
            "TIME_MIDDLE",
            cut1,
            cut2,
        ),
        (
            "TIME_RECENT",
            cut2,
            end_ts,
        ),
    )


def _resolve_robustness_candidates(
    data,
):
    path = (
        PIPE_RESULTS
        / "universe_size_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "universe_size_latest.json missing"
        )

    payload = _load_json_file(
        path
    )

    current_hash = (
        data[
            "fingerprint"
        ].get(
            "fingerprint_hash"
        )
    )
    prior_hash = (
        payload.get(
            "data_fingerprint",
            {},
        ).get(
            "fingerprint_hash"
        )
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "UNIVERSE RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    shortlist = list(
        payload.get(
            "robustness_shortlist",
            [],
        )
    )

    if not shortlist:
        raise RuntimeError(
            "universe robustness_shortlist empty"
        )

    print()
    print("=" * 112)
    print(
        "ROBUSTNESS CANDIDATES"
    )
    print("=" * 112)

    for row in shortlist:
        print(
            row[
                "candidate"
            ],
            "| reason=",
            row.get(
                "shortlist_reason",
                "",
            ),
            "| equity=",
            f"${row['metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['metrics']['realized_mdd_pct']:+.4f}%",
            "| U=",
            row[
                "universe_size"
            ],
        )

    return (
        payload,
        shortlist,
    )


def _robust_parent_from_row(
    row,
):
    parent = dict(
        row[
            "parent"
        ]
    )

    parent[
        "universe_size"
    ] = int(
        row[
            "universe_size"
        ]
    )
    parent[
        "universe_symbols"
    ] = list(
        row[
            "symbols"
        ]
    )
    parent[
        "baseline_metrics"
    ] = dict(
        row[
            "metrics"
        ]
    )
    parent[
        "baseline_candidate"
    ] = str(
        row[
            "candidate"
        ]
    )

    return parent


def _build_robust_engine(
    parent,
    books,
    effective_fee_rate,
):
    (
        engine,
        exit_policy,
        timing_policy,
    ) = (
        build_exit_source_timing_engine(
            m,
            parent[
                "exit_config"
            ],
            parent[
                "timing_config"
            ],
            books,
        )
    )

    # The cloned simulator owns a private globals dict. Override transaction
    # cost there only; STANDARD source/module is not modified.
    engine.simulator.__globals__[
        "FEE_RATE"
    ] = float(
        effective_fee_rate
    )

    return (
        engine,
        exit_policy,
        timing_policy,
    )


def _make_robust_kwargs(
    data,
    parent,
    *,
    start_ts,
    end_ts,
    event_book=None,
    state_book=None,
    trailing_start_pct=None,
    trailing_callback_pct=None,
):
    if event_book is None:
        event_book = data[
            "event_book"
        ]

    if state_book is None:
        state_book = data[
            "owner_state_book"
        ]

    if trailing_start_pct is None:
        trailing_start_pct = (
            parent[
                "trailing_config"
            ][
                "trailing_start_pct"
            ]
        )

    if trailing_callback_pct is None:
        trailing_callback_pct = (
            parent[
                "trailing_config"
            ][
                "trailing_callback_pct"
            ]
        )

    return {
        "signal_book":
            event_book,
        "symbols":
            list(
                parent[
                    "universe_symbols"
                ]
            ),
        "snapshot":
            data[
                "snapshot"
            ],
        "market":
            data[
                "market"
            ],
        "start_ts":
            int(
                start_ts
            ),
        "end_ts":
            int(
                end_ts
            ),
        "leverage_map":
            LEVERAGE,
        "frames":
            OWNER_FRAMES,
        "active_count":
            int(
                parent[
                    "universe_size"
                ]
            ),
        "start_equity":
            10000.0,
        "collect_trade_log":
            False,
        "trailing_start_pct":
            float(
                trailing_start_pct
            ),
        "trailing_callback_pct":
            float(
                trailing_callback_pct
            ),
        "state_reentry_book":
            state_book,
        "state_reentry_pullback_pct":
            0.0,
    }


def _run_or_resume_robust_scenario(
    *,
    data,
    base_books,
    stage,
    parent,
    scenario_name,
    scenario_config,
    resume,
):
    candidate = (
        parent[
            "baseline_candidate"
        ]
        + "__"
        + scenario_name
    )

    start_ts = int(
        scenario_config.get(
            "start_ts",
            data[
                "start_ts"
            ],
        )
    )
    end_ts = int(
        scenario_config.get(
            "end_ts",
            data[
                "end_ts"
            ],
        )
    )

    delay_hours = int(
        scenario_config.get(
            "delay_hours",
            0,
        )
    )

    fee_rate = float(
        scenario_config.get(
            "effective_fee_rate",
            float(
                m.FEE_RATE
            ),
        )
    )

    trailing_start = float(
        scenario_config.get(
            "trailing_start_pct",
            parent[
                "trailing_config"
            ][
                "trailing_start_pct"
            ],
        )
    )
    trailing_callback = float(
        scenario_config.get(
            "trailing_callback_pct",
            parent[
                "trailing_config"
            ][
                "trailing_callback_pct"
            ],
        )
    )

    candidate_config = {
        "baseline_candidate":
            parent[
                "baseline_candidate"
            ],
        "scenario":
            scenario_name,
        "scenario_config":
            scenario_config,
        "universe_size":
            parent[
                "universe_size"
            ],
        "universe_symbols":
            parent[
                "universe_symbols"
            ],
        "exit_source":
            parent[
                "exit_config"
            ],
        "timing":
            parent[
                "timing_config"
            ],
        "control_sl":
            "STANDARD",
        "entry_gate":
            "STANDARD",
        "reentry":
            "STANDARD",
    }

    (
        key,
        path,
        checkpoint,
    ) = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope="ROBUSTNESS",
        candidate_config=
            candidate_config,
        data_fingerprint=
            data[
                "fingerprint"
            ],
        start_ts=
            start_ts,
        end_ts=
            end_ts,
        resume=
            resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {candidate}"
        )
        return {
            "candidate":
                candidate,
            "scenario":
                scenario_name,
            "scenario_config":
                scenario_config,
            "metrics":
                checkpoint[
                    "metrics"
                ],
            "elapsed_seconds":
                float(
                    checkpoint.get(
                        "elapsed_seconds",
                        0.0,
                    )
                ),
            "extra":
                checkpoint.get(
                    "extra",
                    {},
                ),
            "checkpoint":
                str(
                    path
                ),
            "resumed":
                True,
        }

    if delay_hours:
        event_book = (
            _shift_signal_book(
                data[
                    "event_book"
                ],
                delay_hours,
            )
        )
        state_book = (
            _shift_signal_book(
                data[
                    "owner_state_book"
                ],
                delay_hours,
            )
        )
        books = {
            name:
                _shift_signal_book(
                    book,
                    delay_hours,
                )
            for name, book
            in base_books.items()
        }
    else:
        event_book = data[
            "event_book"
        ]
        state_book = data[
            "owner_state_book"
        ]
        books = base_books

    (
        engine,
        exit_policy,
        timing_policy,
    ) = _build_robust_engine(
        parent,
        books,
        fee_rate,
    )

    kwargs = _make_robust_kwargs(
        data,
        parent,
        start_ts=
            start_ts,
        end_ts=
            end_ts,
        event_book=
            event_book,
        state_book=
            state_book,
        trailing_start_pct=
            trailing_start,
        trailing_callback_pct=
            trailing_callback,
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        (
            "ROBUST_"
            + candidate
        ),
    )

    metrics = extract_metrics(
        result
    )

    extra = {
        "patch_stats":
            dict(
                engine.patch_stats
            ),
        "exit_source_policy_stats":
            dict(
                exit_policy.stats
            ),
        "timing_policy_stats":
            dict(
                timing_policy.stats
            ),
        "effective_fee_rate":
            fee_rate,
        "delay_hours":
            delay_hours,
        "trailing_start_pct":
            trailing_start,
        "trailing_callback_pct":
            trailing_callback,
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope="ROBUSTNESS",
        candidate_config=
            candidate_config,
        data_fingerprint=
            data[
                "fingerprint"
            ],
        start_ts=
            start_ts,
        end_ts=
            end_ts,
        metrics=
            metrics,
        elapsed_seconds=
            elapsed,
        extra=
            extra,
    )

    return {
        "candidate":
            candidate,
        "scenario":
            scenario_name,
        "scenario_config":
            scenario_config,
        "metrics":
            metrics,
        "elapsed_seconds":
            float(
                elapsed
            ),
        "extra":
            extra,
        "checkpoint":
            str(
                path
            ),
        "resumed":
            False,
    }


def _find_adjacent_universe_evidence(
    universe_payload,
    baseline_row,
):
    size = int(
        baseline_row[
            "universe_size"
        ]
    )
    structure = str(
        baseline_row[
            "structure"
        ]
    )

    rows = [
        row
        for row in universe_payload.get(
            "results",
            [],
        )
        if str(
            row[
                "structure"
            ]
        )
        == structure
    ]

    wanted = {
        size - 5,
        size + 5,
    }

    return [
        row
        for row in rows
        if int(
            row[
                "universe_size"
            ]
        )
        in wanted
        and int(
            row[
                "universe_size"
            ]
        )
        in UNIVERSE_SIZES
    ]


def _robustness_verdict(
    baseline_metrics,
    scenario_rows,
    adjacent_rows,
):
    hard_fail_reasons = []

    for row in scenario_rows:
        mtr = row[
            "metrics"
        ]

        if (
            int(
                mtr.get(
                    "liquidations",
                    0,
                )
            )
            > 0
        ):
            hard_fail_reasons.append(
                row[
                    "scenario"
                ]
                + ":LIQUIDATION"
            )

        if bool(
            mtr.get(
                "bankrupt",
                False,
            )
        ):
            hard_fail_reasons.append(
                row[
                    "scenario"
                ]
                + ":BANKRUPT"
            )

        if float(
            mtr[
                "final_equity"
            ]
        ) <= 0.0:
            hard_fail_reasons.append(
                row[
                    "scenario"
                ]
                + ":ZERO_EQUITY"
            )

    for row in adjacent_rows:
        mtr = row[
            "metrics"
        ]
        if (
            int(
                mtr.get(
                    "liquidations",
                    0,
                )
            )
            > 0
        ):
            hard_fail_reasons.append(
                row[
                    "candidate"
                ]
                + ":ADJ_UNIVERSE_LIQ"
            )

    if hard_fail_reasons:
        return {
            "verdict":
                "HARD_FAIL",
            "hard_fail_reasons":
                hard_fail_reasons,
            "soft_checks":
                {},
        }

    families = {}

    time_rows = [
        row
        for row in scenario_rows
        if row[
            "scenario"
        ].startswith(
            "TIME_"
        )
    ]

    stress_rows = [
        row
        for row in scenario_rows
        if (
            row[
                "scenario"
            ].startswith(
                "COST_"
            )
            or row[
                "scenario"
            ].startswith(
                "DELAY_"
            )
        )
    ]

    trail_rows = [
        row
        for row in scenario_rows
        if row[
            "scenario"
        ].startswith(
            "TRAIL_"
        )
    ]

    profitable_time = sum(
        1
        for row in time_rows
        if float(
            row[
                "metrics"
            ][
                "final_equity"
            ]
        )
        >= 10000.0
    )

    time_floor_ok = all(
        float(
            row[
                "metrics"
            ][
                "final_equity"
            ]
        )
        >= 5000.0
        and float(
            row[
                "metrics"
            ][
                "realized_mdd_pct"
            ]
        )
        >= -95.0
        for row in time_rows
    )

    families[
        "time_slices"
    ] = bool(
        profitable_time >= 2
        and time_floor_ok
    )

    families[
        "cost_delay"
    ] = all(
        float(
            row[
                "metrics"
            ][
                "final_equity"
            ]
        )
        >= 10000.0
        and float(
            row[
                "metrics"
            ][
                "realized_mdd_pct"
            ]
        )
        >= -90.0
        for row in stress_rows
    )

    baseline_eq = float(
        baseline_metrics[
            "final_equity"
        ]
    )

    trail_equities = [
        float(
            row[
                "metrics"
            ][
                "final_equity"
            ]
        )
        for row in trail_rows
    ]

    trail_equities_sorted = sorted(
        trail_equities
    )

    if trail_equities_sorted:
        mid = len(
            trail_equities_sorted
        ) // 2

        if (
            len(
                trail_equities_sorted
            )
            % 2
        ):
            median_trail_eq = (
                trail_equities_sorted[
                    mid
                ]
            )
        else:
            median_trail_eq = (
                trail_equities_sorted[
                    mid - 1
                ]
                + trail_equities_sorted[
                    mid
                ]
            ) / 2.0
    else:
        median_trail_eq = 0.0

    profitable_trail = sum(
        1
        for eq in trail_equities
        if eq >= 10000.0
    )

    families[
        "trailing_neighborhood"
    ] = bool(
        profitable_trail
        >= max(
            1,
            len(
                trail_rows
            )
            - 1,
        )
        and median_trail_eq
        >= 0.20
        * baseline_eq
        and all(
            float(
                row[
                    "metrics"
                ][
                    "realized_mdd_pct"
                ]
            )
            >= -95.0
            for row in trail_rows
        )
    )

    families[
        "adjacent_universe"
    ] = bool(
        adjacent_rows
        and all(
            float(
                row[
                    "metrics"
                ][
                    "final_equity"
                ]
            )
            >= 0.25
            * baseline_eq
            and float(
                row[
                    "metrics"
                ][
                    "realized_mdd_pct"
                ]
            )
            >= -85.0
            for row in adjacent_rows
        )
    )

    all_soft = all(
        families.values()
    )

    return {
        "verdict":
            "PASS"
            if all_soft
            else "REVIEW",
        "hard_fail_reasons":
            [],
        "soft_checks":
            families,
    }


def run_robustness_lab(
    data,
    args,
):
    stage = (
        "robustness_v1"
    )

    (
        universe_payload,
        shortlist,
    ) = (
        _resolve_robustness_candidates(
            data
        )
    )

    with Heartbeat(
        "BUILD_ROBUSTNESS_EXIT_SOURCE_BOOKS"
    ):
        base_books = (
            build_exit_source_books(
                data
            )
        )

    time_slices = (
        _robust_time_slices(
            data[
                "start_ts"
            ],
            data[
                "end_ts"
            ],
        )
    )

    base_fee = float(
        m.FEE_RATE
    )

    all_candidate_results = []

    for baseline_row in shortlist:
        parent = (
            _robust_parent_from_row(
                baseline_row
            )
        )

        print()
        print("=" * 112)
        print(
            "ROBUSTNESS BASELINE =",
            parent[
                "baseline_candidate"
            ],
        )
        print(
            "BASELINE EQUITY     =",
            f"${parent['baseline_metrics']['final_equity']:,.2f}",
        )
        print(
            "BASELINE MDD        =",
            f"{parent['baseline_metrics']['realized_mdd_pct']:+.4f}%",
        )
        print(
            "UNIVERSE            =",
            parent[
                "universe_size"
            ],
        )
        print("=" * 112)

        scenarios = []

        for (
            label,
            start_ts,
            end_ts,
        ) in time_slices:
            scenarios.append(
                (
                    label,
                    {
                        "family":
                            "TIME_SLICE",
                        "start_ts":
                            int(
                                start_ts
                            ),
                        "end_ts":
                            int(
                                end_ts
                            ),
                    },
                )
            )

        scenarios.extend([
            (
                "COST_7P5BP_PER_SIDE",
                {
                    "family":
                        "TRANSACTION_COST_PROXY",
                    "effective_fee_rate":
                        0.00075,
                    "note":
                        "5bp baseline fee + 2.5bp extra cost proxy",
                },
            ),
            (
                "COST_10BP_PER_SIDE",
                {
                    "family":
                        "TRANSACTION_COST_PROXY",
                    "effective_fee_rate":
                        0.00100,
                    "note":
                        "5bp baseline fee + 5bp extra cost proxy",
                },
            ),
            (
                "DELAY_1H",
                {
                    "family":
                        "SIGNAL_DELAY",
                    "delay_hours":
                        1,
                },
            ),
            (
                "DELAY_2H",
                {
                    "family":
                        "SIGNAL_DELAY",
                    "delay_hours":
                        2,
                },
            ),
        ])

        base_start = float(
            parent[
                "trailing_config"
            ][
                "trailing_start_pct"
            ]
        )
        base_callback = float(
            parent[
                "trailing_config"
            ][
                "trailing_callback_pct"
            ]
        )

        trail_neighbors = [
            (
                "TRAIL_START_3",
                3.0,
                base_callback,
            ),
            (
                "TRAIL_START_5",
                5.0,
                base_callback,
            ),
            (
                "TRAIL_CALLBACK_1",
                base_start,
                1.0,
            ),
            (
                "TRAIL_CALLBACK_2",
                base_start,
                2.0,
            ),
        ]

        # If one neighbor equals baseline, skip it and substitute the next
        # coarse point so we never waste an identical run.
        normalized = []
        seen_pairs = {
            (
                base_start,
                base_callback,
            )
        }

        fallback_points = [
            (
                "TRAIL_START_6",
                6.0,
                base_callback,
            ),
            (
                "TRAIL_CALLBACK_2P5",
                base_start,
                2.5,
            ),
        ]

        for item in (
            trail_neighbors
            + fallback_points
        ):
            (
                label,
                start_v,
                callback_v,
            ) = item
            pair = (
                float(
                    start_v
                ),
                float(
                    callback_v
                ),
            )

            if pair in seen_pairs:
                continue

            seen_pairs.add(
                pair
            )

            normalized.append(
                (
                    label,
                    start_v,
                    callback_v,
                )
            )

            if len(
                normalized
            ) >= 4:
                break

        for (
            label,
            start_v,
            callback_v,
        ) in normalized:
            scenarios.append(
                (
                    label,
                    {
                        "family":
                            "TRAILING_NEIGHBORHOOD",
                        "trailing_start_pct":
                            float(
                                start_v
                            ),
                        "trailing_callback_pct":
                            float(
                                callback_v
                            ),
                    },
                )
            )

        scenario_rows = []

        for (
            scenario_name,
            scenario_config,
        ) in scenarios:
            print()
            print("#" * 112)
            print(
                "ROBUSTNESS SCENARIO =",
                scenario_name,
            )
            print(
                "CONFIG              =",
                scenario_config,
            )
            print("#" * 112)

            row = (
                _run_or_resume_robust_scenario(
                    data=data,
                    base_books=
                        base_books,
                    stage=stage,
                    parent=
                        parent,
                    scenario_name=
                        scenario_name,
                    scenario_config=
                        scenario_config,
                    resume=
                        args.resume,
                )
            )

            print_metrics(
                (
                    "ROBUSTNESS - "
                    + row[
                        "candidate"
                    ]
                ),
                row[
                    "metrics"
                ],
                row[
                    "elapsed_seconds"
                ],
            )

            scenario_rows.append(
                row
            )

        adjacent_rows = (
            _find_adjacent_universe_evidence(
                universe_payload,
                baseline_row,
            )
        )

        print()
        print(
            "ADJACENT UNIVERSE EVIDENCE ="
        )
        for row in adjacent_rows:
            print(
                " -",
                row[
                    "candidate"
                ],
                "| equity=",
                f"${row['metrics']['final_equity']:,.2f}",
                "| MDD=",
                f"{row['metrics']['realized_mdd_pct']:+.4f}%",
                "| LIQ=",
                row[
                    "metrics"
                ][
                    "liquidations"
                ],
            )

        verdict = (
            _robustness_verdict(
                parent[
                    "baseline_metrics"
                ],
                scenario_rows,
                adjacent_rows,
            )
        )

        print()
        print(
            "ROBUSTNESS VERDICT =",
            verdict[
                "verdict"
            ],
        )
        print(
            "SOFT CHECKS        =",
            verdict[
                "soft_checks"
            ],
        )
        print(
            "HARD FAIL REASONS  =",
            verdict[
                "hard_fail_reasons"
            ],
        )

        all_candidate_results.append({
            "baseline_candidate":
                parent[
                    "baseline_candidate"
                ],
            "parent":
                parent,
            "baseline_metrics":
                parent[
                    "baseline_metrics"
                ],
            "scenario_results":
                scenario_rows,
            "adjacent_universe_evidence":
                adjacent_rows,
            "verdict":
                verdict,
        })

    walk_forward_candidates = [
        row
        for row in all_candidate_results
        if row[
            "verdict"
        ][
            "verdict"
        ]
        in (
            "PASS",
            "REVIEW",
        )
    ]

    if not walk_forward_candidates:
        raise RuntimeError(
            "ROBUSTNESS LAB: all candidates HARD_FAIL"
        )

    # PASS before REVIEW, then higher baseline equity.
    walk_forward_candidates.sort(
        key=lambda row: (
            0
            if row[
                "verdict"
            ][
                "verdict"
            ]
            == "PASS"
            else 1,
            -float(
                row[
                    "baseline_metrics"
                ][
                    "final_equity"
                ]
            ),
        )
    )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_ROBUSTNESS_LAB",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "base_fee_rate":
            base_fee,
        "transaction_cost_note": (
            "Cost scenarios are fee+slippage transaction-cost proxies. "
            "They do not alter fill price or liquidation trigger price."
        ),
        "candidate_results":
            all_candidate_results,
        "walk_forward_candidates":
            walk_forward_candidates,
        "next_action":
            "WALK_FORWARD_LAB",
    }

    timestamped = (
        PIPE_RESULTS
        / f"robustness_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "robustness_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            "BATCH_SUMMARY",
        "scope":
            "ROBUSTNESS",
        "result_path":
            str(
                timestamped
            ),
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "walk_forward_candidates": [
            {
                "candidate":
                    row[
                        "baseline_candidate"
                    ],
                "verdict":
                    row[
                        "verdict"
                    ][
                        "verdict"
                    ],
                "baseline_equity":
                    row[
                        "baseline_metrics"
                    ][
                        "final_equity"
                    ],
                "baseline_mdd":
                    row[
                        "baseline_metrics"
                    ][
                        "realized_mdd_pct"
                    ],
            }
            for row in walk_forward_candidates
        ],
        "next_action":
            "WALK_FORWARD_LAB",
    })

    print()
    print("=" * 112)
    print(
        "ROBUSTNESS LAB COMPLETE"
    )
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "WALK-FORWARD CANDIDATES =",
        [
            (
                row[
                    "baseline_candidate"
                ],
                row[
                    "verdict"
                ][
                    "verdict"
                ],
            )
            for row in walk_forward_candidates
        ],
    )
    print(
        "NEXT ACTION        = WALK_FORWARD_LAB"
    )

    return summary




# =============================================================================
# WALK-FORWARD LAB V1
# =============================================================================
#
# PURPOSE
# -------
# Validate the already-frozen research structure across sequential future
# windows without re-optimizing inside each fold.
#
# IMPORTANT LIMITATION
# --------------------
# This is historical pseudo-OOS / anchored walk-forward validation because the
# candidate itself was discovered using the same overall historical dataset.
# It is NOT true future unseen data. The user explicitly removed Shadow/Forward
# Test from the current promotion flow, so this is the final historical
# robustness layer before ORIGINAL EXACT.
#
# DESIGN
# ------
# - Candidate source: robustness_latest.json PASS/REVIEW list
# - Minimum anchored history before first test: 730 days
# - Test window: 365 days
# - Step: 365 days
# - Include final partial test window if >= 180 days
# - Candidate parameters remain FIXED in every fold
# - Compare against current SEALED STANDARD V1 on the same fold
# - Each fold starts from $10,000 for both systems
# - Actual liquidation simulation remains ON
#
# CURRENT STANDARD comparator:
#   Universe 35
#   Trailing 4 / 2
#   Opposite exit source = STANDARD_COMBINED (EMA+SAR agreement)
#   Opposite reopen timing = IMMEDIATE
#   Gate / Control SL / STATE reentry = STANDARD
#
# PASS GATE (coarse, no fine tuning):
# - zero actual liquidations / no bankruptcy in every candidate fold
# - candidate profitable in >= 60% of folds
# - candidate beats STANDARD final equity in >= 60% of folds
# - candidate MDD is >= STANDARD MDD in >= 50% of folds
# - geometric mean(candidate equity / STANDARD equity) >= 1.0
# - most recent fold is profitable
# - no candidate fold MDD <= -99%
#
# PASS -> write frozen V2 candidate record for ORIGINAL EXACT.
# REVIEW -> do not auto-freeze as promotion candidate.
# HARD_FAIL -> stop promotion.
# =============================================================================

WALK_FORWARD_MIN_TRAIN_DAYS = 730
WALK_FORWARD_TEST_DAYS = 365
WALK_FORWARD_STEP_DAYS = 365
WALK_FORWARD_MIN_FINAL_DAYS = 180


def _load_walk_forward_candidates(data):
    path = (
        PIPE_RESULTS
        / "robustness_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "robustness_latest.json missing"
        )

    payload = _load_json_file(
        path
    )

    current_hash = (
        data[
            "fingerprint"
        ].get(
            "fingerprint_hash"
        )
    )
    prior_hash = (
        payload.get(
            "data_fingerprint",
            {},
        ).get(
            "fingerprint_hash"
        )
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "ROBUSTNESS RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    candidates = list(
        payload.get(
            "walk_forward_candidates",
            [],
        )
    )

    if not candidates:
        raise RuntimeError(
            "robustness walk_forward_candidates empty"
        )

    # PASS before REVIEW, then higher baseline equity.
    candidates.sort(
        key=lambda row: (
            0
            if row[
                "verdict"
            ][
                "verdict"
            ]
            == "PASS"
            else 1,
            -float(
                row[
                    "baseline_metrics"
                ][
                    "final_equity"
                ]
            ),
        )
    )

    print()
    print("=" * 112)
    print("WALK-FORWARD CANDIDATES")
    print("=" * 112)

    for row in candidates:
        print(
            row[
                "baseline_candidate"
            ],
            "| robustness=",
            row[
                "verdict"
            ][
                "verdict"
            ],
            "| equity=",
            f"${row['baseline_metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['baseline_metrics']['realized_mdd_pct']:+.4f}%",
        )

    return (
        payload,
        candidates,
    )


def _walk_forward_parent_from_robust_row(
    row,
):
    parent = dict(
        row[
            "parent"
        ]
    )

    # robustness parent already contains universe_size/symbols/config.
    parent[
        "baseline_candidate"
    ] = str(
        row[
            "baseline_candidate"
        ]
    )
    parent[
        "baseline_metrics"
    ] = dict(
        row[
            "baseline_metrics"
        ]
    )

    required = (
        "universe_size",
        "universe_symbols",
        "trailing_config",
        "timing_config",
        "exit_config",
    )

    missing = [
        key
        for key in required
        if key not in parent
    ]

    if missing:
        raise RuntimeError(
            "walk-forward parent missing keys: "
            + repr(
                missing
            )
        )

    return parent


def _standard_walk_forward_parent(
    data,
):
    ordered = [
        str(x).upper()
        for x in data[
            "snapshot"
        ].get(
            "active_symbols",
            [],
        )
    ]

    if len(ordered) != 35:
        raise RuntimeError(
            "STANDARD comparator expected 35 active symbols"
        )

    return {
        "baseline_candidate":
            "SEALED_STANDARD_V1_U35",
        "universe_size":
            35,
        "universe_symbols":
            ordered,
        "trailing_config": {
            "trailing_start_pct":
                4.0,
            "trailing_callback_pct":
                2.0,
        },
        "timing_mode":
            "IMMEDIATE",
        "timing_config":
            dict(
                OPPOSITE_STANDARD_MODE
            ),
        "exit_source":
            "STANDARD_COMBINED",
        "exit_config":
            dict(
                EXIT_SOURCE_STANDARD
            ),
    }


def _build_walk_forward_folds(
    start_ts,
    end_ts,
):
    start_ts = int(
        start_ts
    )
    end_ts = int(
        end_ts
    )

    day = 86400

    first_test_start = (
        start_ts
        + WALK_FORWARD_MIN_TRAIN_DAYS
        * day
    )

    if (
        first_test_start
        >= end_ts
    ):
        raise RuntimeError(
            "not enough history for walk-forward"
        )

    folds = []
    test_start = int(
        first_test_start
    )
    fold_no = 1

    full_test_seconds = (
        WALK_FORWARD_TEST_DAYS
        * day
    )
    step_seconds = (
        WALK_FORWARD_STEP_DAYS
        * day
    )
    min_final_seconds = (
        WALK_FORWARD_MIN_FINAL_DAYS
        * day
    )

    while (
        test_start
        + full_test_seconds
        <= end_ts
    ):
        test_end = (
            test_start
            + full_test_seconds
        )

        folds.append({
            "fold":
                fold_no,
            "train_start_ts":
                start_ts,
            "train_end_ts":
                test_start,
            "test_start_ts":
                test_start,
            "test_end_ts":
                test_end,
            "test_days":
                WALK_FORWARD_TEST_DAYS,
            "partial":
                False,
        })

        fold_no += 1
        test_start += (
            step_seconds
        )

    remaining = (
        end_ts
        - test_start
    )

    if (
        remaining
        >= min_final_seconds
    ):
        folds.append({
            "fold":
                fold_no,
            "train_start_ts":
                start_ts,
            "train_end_ts":
                test_start,
            "test_start_ts":
                test_start,
            "test_end_ts":
                end_ts,
            "test_days":
                int(
                    remaining
                    // day
                ),
            "partial":
                True,
        })

    if len(
        folds
    ) < 3:
        raise RuntimeError(
            f"walk-forward requires >=3 folds, got {len(folds)}"
        )

    return folds


def _make_wf_kwargs(
    data,
    parent,
    fold,
):
    return {
        "signal_book":
            data[
                "event_book"
            ],
        "symbols":
            list(
                parent[
                    "universe_symbols"
                ]
            ),
        "snapshot":
            data[
                "snapshot"
            ],
        "market":
            data[
                "market"
            ],
        "start_ts":
            int(
                fold[
                    "test_start_ts"
                ]
            ),
        "end_ts":
            int(
                fold[
                    "test_end_ts"
                ]
            ),
        "leverage_map":
            LEVERAGE,
        "frames":
            OWNER_FRAMES,
        "active_count":
            int(
                parent[
                    "universe_size"
                ]
            ),
        "start_equity":
            10000.0,
        "collect_trade_log":
            False,
        "trailing_start_pct":
            float(
                parent[
                    "trailing_config"
                ][
                    "trailing_start_pct"
                ]
            ),
        "trailing_callback_pct":
            float(
                parent[
                    "trailing_config"
                ][
                    "trailing_callback_pct"
                ]
            ),
        "state_reentry_book":
            data[
                "owner_state_book"
            ],
        "state_reentry_pullback_pct":
            0.0,
    }


def _run_or_resume_wf_fold(
    *,
    data,
    books,
    stage,
    role,
    parent,
    fold,
    resume,
):
    fold_no = int(
        fold[
            "fold"
        ]
    )

    candidate = (
        str(
            parent[
                "baseline_candidate"
            ]
        )
        + f"__WF_FOLD_{fold_no}"
        + "__"
        + str(
            role
        ).upper()
    )

    candidate_config = {
        "role":
            str(
                role
            ).upper(),
        "parent":
            parent[
                "baseline_candidate"
            ],
        "fold":
            fold,
        "universe_size":
            int(
                parent[
                    "universe_size"
                ]
            ),
        "universe_symbols":
            list(
                parent[
                    "universe_symbols"
                ]
            ),
        "trailing":
            parent[
                "trailing_config"
            ],
        "timing":
            parent[
                "timing_config"
            ],
        "exit_source":
            parent[
                "exit_config"
            ],
        "gate":
            "STANDARD",
        "control_sl":
            "STANDARD",
        "reentry":
            "STANDARD",
    }

    (
        key,
        path,
        checkpoint,
    ) = load_checkpoint(
        stage=stage,
        candidate=candidate,
        scope="WALK_FORWARD_FOLD",
        candidate_config=
            candidate_config,
        data_fingerprint=
            data[
                "fingerprint"
            ],
        start_ts=
            fold[
                "test_start_ts"
            ],
        end_ts=
            fold[
                "test_end_ts"
            ],
        resume=
            resume,
    )

    if checkpoint is not None:
        print(
            f"[PIPEV2][RESUME][SKIP] "
            f"{stage} {candidate}"
        )
        return {
            "candidate":
                candidate,
            "metrics":
                checkpoint[
                    "metrics"
                ],
            "elapsed_seconds":
                float(
                    checkpoint.get(
                        "elapsed_seconds",
                        0.0,
                    )
                ),
            "checkpoint":
                str(
                    path
                ),
            "resumed":
                True,
            "extra":
                checkpoint.get(
                    "extra",
                    {},
                ),
        }

    (
        engine,
        exit_policy,
        timing_policy,
    ) = (
        build_exit_source_timing_engine(
            m,
            parent[
                "exit_config"
            ],
            parent[
                "timing_config"
            ],
            books,
        )
    )

    kwargs = (
        _make_wf_kwargs(
            data,
            parent,
            fold,
        )
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        (
            "WF_"
            + str(
                role
            ).upper()
            + "_F"
            + str(
                fold_no
            )
        ),
    )

    metrics = extract_metrics(
        result
    )

    extra = {
        "role":
            str(
                role
            ).upper(),
        "fold":
            fold,
        "patch_stats":
            dict(
                engine.patch_stats
            ),
        "exit_source_policy_stats":
            dict(
                exit_policy.stats
            ),
        "timing_policy_stats":
            dict(
                timing_policy.stats
            ),
    }

    save_checkpoint(
        key=key,
        path=path,
        stage=stage,
        candidate=candidate,
        scope="WALK_FORWARD_FOLD",
        candidate_config=
            candidate_config,
        data_fingerprint=
            data[
                "fingerprint"
            ],
        start_ts=
            fold[
                "test_start_ts"
            ],
        end_ts=
            fold[
                "test_end_ts"
            ],
        metrics=
            metrics,
        elapsed_seconds=
            elapsed,
        extra=
            extra,
    )

    return {
        "candidate":
            candidate,
        "metrics":
            metrics,
        "elapsed_seconds":
            float(
                elapsed
            ),
        "checkpoint":
            str(
                path
            ),
        "resumed":
            False,
        "extra":
            extra,
    }


def _run_wf_candidate_replay_parity(
    data,
    books,
    parent,
):
    """
    Re-run the selected candidate once on the full research history and require
    exact metric equality with the Universe/Robustness baseline. This prevents
    a config-resolution mistake from contaminating Walk-Forward.
    """
    (
        engine,
        _exit_policy,
        _timing_policy,
    ) = (
        build_exit_source_timing_engine(
            m,
            parent[
                "exit_config"
            ],
            parent[
                "timing_config"
            ],
            books,
        )
    )

    fold = {
        "test_start_ts":
            int(
                data[
                    "start_ts"
                ]
            ),
        "test_end_ts":
            int(
                data[
                    "end_ts"
                ]
            ),
    }

    kwargs = (
        _make_wf_kwargs(
            data,
            parent,
            fold,
        )
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        "WALK_FORWARD_CANDIDATE_REPLAY_PARITY",
    )

    metrics = extract_metrics(
        result
    )

    expected = dict(
        parent[
            "baseline_metrics"
        ]
    )

    ok, diffs = (
        compare_metrics(
            metrics,
            expected,
        )
    )

    print()
    print(
        "STRICT_WALK_FORWARD_REPLAY_PARITY =",
        "PASS"
        if ok
        else "FAIL",
    )
    print(
        "STRICT_WALK_FORWARD_REPLAY_DIFFS  =",
        diffs,
    )
    print(
        "REPLAY ELAPSED                    =",
        f"{elapsed:.2f}s",
    )

    if not ok:
        raise SystemExit(
            "STRICT_WALK_FORWARD_REPLAY_PARITY_FAIL"
        )

    return {
        "status":
            "PASS",
        "metrics":
            metrics,
        "expected":
            expected,
        "diffs":
            diffs,
        "elapsed_seconds":
            float(
                elapsed
            ),
    }


def _safe_geo_mean(
    values,
):
    values = [
        float(v)
        for v in values
        if float(v) > 0.0
    ]

    if not values:
        return 0.0

    return float(
        math.exp(
            sum(
                math.log(v)
                for v in values
            )
            / len(
                values
            )
        )
    )


def _walk_forward_verdict(
    fold_rows,
):
    n = len(
        fold_rows
    )

    hard_fail_reasons = []

    for row in fold_rows:
        cm = row[
            "candidate_metrics"
        ]

        if int(
            cm.get(
                "liquidations",
                0,
            )
        ) > 0:
            hard_fail_reasons.append(
                f"F{row['fold']}:LIQUIDATION"
            )

        if bool(
            cm.get(
                "bankrupt",
                False,
            )
        ):
            hard_fail_reasons.append(
                f"F{row['fold']}:BANKRUPT"
            )

        if float(
            cm[
                "final_equity"
            ]
        ) <= 0.0:
            hard_fail_reasons.append(
                f"F{row['fold']}:ZERO_EQUITY"
            )

        if float(
            cm[
                "realized_mdd_pct"
            ]
        ) <= -99.0:
            hard_fail_reasons.append(
                f"F{row['fold']}:MDD_LE_-99"
            )

    if hard_fail_reasons:
        return {
            "verdict":
                "HARD_FAIL",
            "hard_fail_reasons":
                hard_fail_reasons,
            "checks":
                {},
        }

    profitable = sum(
        1
        for row in fold_rows
        if float(
            row[
                "candidate_metrics"
            ][
                "final_equity"
            ]
        )
        >= 10000.0
    )

    equity_wins = sum(
        1
        for row in fold_rows
        if float(
            row[
                "candidate_metrics"
            ][
                "final_equity"
            ]
        )
        >= float(
            row[
                "standard_metrics"
            ][
                "final_equity"
            ]
        )
    )

    mdd_wins = sum(
        1
        for row in fold_rows
        if float(
            row[
                "candidate_metrics"
            ][
                "realized_mdd_pct"
            ]
        )
        >= float(
            row[
                "standard_metrics"
            ][
                "realized_mdd_pct"
            ]
        )
    )

    ratios = [
        max(
            1e-12,
            float(
                row[
                    "candidate_metrics"
                ][
                    "final_equity"
                ]
            ),
        )
        / max(
            1e-12,
            float(
                row[
                    "standard_metrics"
                ][
                    "final_equity"
                ]
            ),
        )
        for row in fold_rows
    ]

    geo_ratio = (
        _safe_geo_mean(
            ratios
        )
    )

    most_recent_profitable = (
        float(
            fold_rows[
                -1
            ][
                "candidate_metrics"
            ][
                "final_equity"
            ]
        )
        >= 10000.0
    )

    min_60 = int(
        math.ceil(
            n * 0.60
        )
    )
    min_50 = int(
        math.ceil(
            n * 0.50
        )
    )

    checks = {
        "fold_count_ge_3":
            n >= 3,
        "profitable_folds":
            profitable
            >= min_60,
        "equity_vs_standard":
            equity_wins
            >= min_60,
        "mdd_vs_standard":
            mdd_wins
            >= min_50,
        "geo_equity_ratio_ge_1":
            geo_ratio
            >= 1.0,
        "recent_fold_profitable":
            bool(
                most_recent_profitable
            ),
    }

    return {
        "verdict":
            "PASS"
            if all(
                checks.values()
            )
            else "REVIEW",
        "hard_fail_reasons":
            [],
        "checks":
            checks,
        "fold_count":
            n,
        "profitable_folds":
            profitable,
        "equity_wins_vs_standard":
            equity_wins,
        "mdd_wins_vs_standard":
            mdd_wins,
        "geo_equity_ratio_vs_standard":
            geo_ratio,
    }


def _freeze_walk_forward_candidate(
    data,
    robust_row,
    parent,
    wf_summary,
):
    stamp = utc_stamp()

    freeze_payload = {
        "schema":
            "STANDARD_V2_CANDIDATE_FREEZE",
        "freeze_id":
            (
                "STANDARD_V2_CANDIDATE_"
                + stamp
            ),
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "frozen_at_utc":
            stamp,
        "status":
            "FROZEN_FOR_ORIGINAL_EXACT",
        "source_candidate":
            parent[
                "baseline_candidate"
            ],
        "robustness_verdict":
            robust_row[
                "verdict"
            ],
        "walk_forward_verdict":
            wf_summary[
                "verdict"
            ],
        "strategy": {
            "owner_frames":
                list(
                    OWNER_FRAMES
                ),
            "leverage_map":
                dict(
                    LEVERAGE
                ),
            "entry_signal":
                "FINAL_B_EMA_CROSS_PLUS_SAR",
            "entry_gate":
                "STANDARD_CROSS_ENTRY_GATE",
            "control_sl":
                "STANDARD_CONTROL_SL",
            "reentry":
                "STANDARD_STATE_REENTRY",
            "trailing":
                dict(
                    parent[
                        "trailing_config"
                    ]
                ),
            "opposite_exit_source":
                parent[
                    "exit_source"
                ],
            "opposite_reopen_timing":
                parent[
                    "timing_mode"
                ],
            "universe_size":
                int(
                    parent[
                        "universe_size"
                    ]
                ),
            "symbols":
                list(
                    parent[
                        "universe_symbols"
                    ]
                ),
        },
        "baseline_full_metrics":
            dict(
                parent[
                    "baseline_metrics"
                ]
            ),
        "walk_forward":
            wf_summary,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
    }

    freeze_payload[
        "freeze_hash"
    ] = stable_hash(
        freeze_payload
    )

    timestamped = (
        PIPE_RESULTS
        / (
            "standard_v2_candidate_freeze_"
            + stamp
            + ".json"
        )
    )
    latest = (
        PIPE_RESULTS
        / "standard_v2_candidate_freeze_latest.json"
    )

    atomic_write_json(
        timestamped,
        freeze_payload,
    )
    atomic_write_json(
        latest,
        freeze_payload,
    )

    return (
        freeze_payload,
        timestamped,
        latest,
    )


def run_walk_forward_lab(
    data,
    args,
):
    stage = (
        "walk_forward_v1"
    )

    (
        robustness_payload,
        robust_candidates,
    ) = (
        _load_walk_forward_candidates(
            data
        )
    )

    # Current pipeline carries only the top robust structure into exact.
    robust_row = robust_candidates[
        0
    ]
    parent = (
        _walk_forward_parent_from_robust_row(
            robust_row
        )
    )
    standard_parent = (
        _standard_walk_forward_parent(
            data
        )
    )

    with Heartbeat(
        "BUILD_WALK_FORWARD_EXIT_SOURCE_BOOKS"
    ):
        books = (
            build_exit_source_books(
                data
            )
        )

    replay_parity = (
        _run_wf_candidate_replay_parity(
            data,
            books,
            parent,
        )
    )

    folds = (
        _build_walk_forward_folds(
            data[
                "start_ts"
            ],
            data[
                "end_ts"
            ],
        )
    )

    print()
    print("=" * 112)
    print("WALK-FORWARD FOLDS")
    print("=" * 112)

    for fold in folds:
        print(
            "FOLD",
            fold[
                "fold"
            ],
            "| train=",
            pd.to_datetime(
                fold[
                    "train_start_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "->",
            pd.to_datetime(
                fold[
                    "train_end_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "| test=",
            pd.to_datetime(
                fold[
                    "test_start_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "->",
            pd.to_datetime(
                fold[
                    "test_end_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "| days=",
            fold[
                "test_days"
            ],
            "| partial=",
            fold[
                "partial"
            ],
        )

    fold_rows = []

    for fold in folds:
        print()
        print("#" * 112)
        print(
            "WALK-FORWARD FOLD =",
            fold[
                "fold"
            ],
        )
        print(
            "TEST DAYS         =",
            fold[
                "test_days"
            ],
        )
        print("#" * 112)

        cand_run = (
            _run_or_resume_wf_fold(
                data=data,
                books=books,
                stage=stage,
                role="CANDIDATE",
                parent=parent,
                fold=fold,
                resume=args.resume,
            )
        )

        std_run = (
            _run_or_resume_wf_fold(
                data=data,
                books=books,
                stage=stage,
                role="STANDARD_V1",
                parent=standard_parent,
                fold=fold,
                resume=args.resume,
            )
        )

        cm = cand_run[
            "metrics"
        ]
        sm = std_run[
            "metrics"
        ]

        print_metrics(
            (
                f"WF FOLD {fold['fold']} "
                "CANDIDATE"
            ),
            cm,
            cand_run[
                "elapsed_seconds"
            ],
        )
        print_metrics(
            (
                f"WF FOLD {fold['fold']} "
                "STANDARD V1"
            ),
            sm,
            std_run[
                "elapsed_seconds"
            ],
        )

        eq_ratio = (
            float(
                cm[
                    "final_equity"
                ]
            )
            / max(
                1e-12,
                float(
                    sm[
                        "final_equity"
                    ]
                ),
            )
        )

        mdd_delta = (
            float(
                cm[
                    "realized_mdd_pct"
                ]
            )
            - float(
                sm[
                    "realized_mdd_pct"
                ]
            )
        )

        print(
            "FOLD COMPARE =",
            f"equity_ratio={eq_ratio:.6f}",
            f"mdd_delta={mdd_delta:+.6f}pp",
            "candidate_liq=",
            cm[
                "liquidations"
            ],
        )

        fold_rows.append({
            **fold,
            "candidate_metrics":
                cm,
            "standard_metrics":
                sm,
            "equity_ratio_vs_standard":
                eq_ratio,
            "mdd_delta_pp_vs_standard":
                mdd_delta,
            "candidate_checkpoint":
                cand_run[
                    "checkpoint"
                ],
            "standard_checkpoint":
                std_run[
                    "checkpoint"
                ],
        })

    verdict = (
        _walk_forward_verdict(
            fold_rows
        )
    )

    print()
    print("=" * 112)
    print("WALK-FORWARD VERDICT")
    print("=" * 112)
    print(
        "VERDICT =",
        verdict[
            "verdict"
        ],
    )
    print(
        "CHECKS  =",
        verdict.get(
            "checks",
            {},
        ),
    )
    print(
        "HARD FAIL REASONS =",
        verdict.get(
            "hard_fail_reasons",
            [],
        ),
    )
    print(
        "PROFITABLE FOLDS =",
        verdict.get(
            "profitable_folds",
            0,
        ),
        "/",
        len(
            fold_rows
        ),
    )
    print(
        "EQUITY WINS VS STANDARD =",
        verdict.get(
            "equity_wins_vs_standard",
            0,
        ),
        "/",
        len(
            fold_rows
        ),
    )
    print(
        "MDD WINS VS STANDARD =",
        verdict.get(
            "mdd_wins_vs_standard",
            0,
        ),
        "/",
        len(
            fold_rows
        ),
    )
    print(
        "GEO EQUITY RATIO VS STANDARD =",
        f"{verdict.get('geo_equity_ratio_vs_standard', 0.0):.6f}",
    )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_WALK_FORWARD_LAB",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "historical_oos_note": (
            "Frozen-parameter anchored historical pseudo-OOS. "
            "Not true future unseen data."
        ),
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "source_robustness_candidate":
            robust_row,
        "candidate_parent":
            parent,
        "standard_comparator":
            standard_parent,
        "replay_parity":
            replay_parity,
        "fold_config": {
            "min_train_days":
                WALK_FORWARD_MIN_TRAIN_DAYS,
            "test_days":
                WALK_FORWARD_TEST_DAYS,
            "step_days":
                WALK_FORWARD_STEP_DAYS,
            "min_final_days":
                WALK_FORWARD_MIN_FINAL_DAYS,
        },
        "folds":
            fold_rows,
        "verdict":
            verdict,
        "next_action":
            (
                "ORIGINAL_EXACT"
                if verdict[
                    "verdict"
                ]
                == "PASS"
                else "REVIEW_BEFORE_ORIGINAL_EXACT"
            ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"walk_forward_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "walk_forward_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    freeze_payload = None
    freeze_timestamped = None
    freeze_latest = None

    if (
        verdict[
            "verdict"
        ]
        == "PASS"
    ):
        (
            freeze_payload,
            freeze_timestamped,
            freeze_latest,
        ) = (
            _freeze_walk_forward_candidate(
                data,
                robust_row,
                parent,
                summary,
            )
        )

        append_master_record({
            "schema":
                "STANDARD_V2_CANDIDATE_FREEZE",
            "stage":
                stage,
            "candidate":
                parent[
                    "baseline_candidate"
                ],
            "scope":
                "FROZEN_FOR_ORIGINAL_EXACT",
            "freeze_path":
                str(
                    freeze_timestamped
                ),
            "freeze_hash":
                freeze_payload[
                    "freeze_hash"
                ],
            "data_fingerprint":
                data[
                    "fingerprint"
                ],
            "next_action":
                "ORIGINAL_EXACT",
        })

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            parent[
                "baseline_candidate"
            ],
        "scope":
            "ANCHORED_WALK_FORWARD",
        "result_path":
            str(
                timestamped
            ),
        "verdict":
            verdict,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "next_action":
            summary[
                "next_action"
            ],
    })

    print()
    print("=" * 112)
    print("WALK-FORWARD LAB COMPLETE")
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )

    if freeze_latest is not None:
        print(
            "CANDIDATE FREEZE   =",
            freeze_latest,
        )
        print(
            "NEXT ACTION        = ORIGINAL_EXACT"
        )
    else:
        print(
            "CANDIDATE FREEZE   = NONE"
        )
        print(
            "NEXT ACTION        = REVIEW_BEFORE_ORIGINAL_EXACT"
        )

    return summary




# =============================================================================
# WALK-FORWARD RECHECK 3 V1
# =============================================================================
#
# After U20 received REVIEW, retest ONLY:
#
#   1) T3 + U25
#   2) T1 + U30
#   3) T1 + U35
#
# These are fixed preselected alternatives from Universe Size Lab.
# No new search / optimization is performed here.
#
# The fold design and PASS gate are IDENTICAL to WALK_FORWARD_LAB V1.
#
# Speed:
#   - candidate FULL replay parity is run once per candidate
#   - STANDARD V1 fold checkpoints from the prior Walk-Forward are reused when
#     present because stage/candidate/config/fold boundaries are identical
#   - only the new candidate folds need fresh simulation
#
# PASS candidates are ranked only after all three are evaluated.
# This stage does NOT run ORIGINAL EXACT.
# =============================================================================

WALK_FORWARD_RECHECK_TARGETS = (
    "T3_TIGHT_CALLBACK_4_1P5__EXIT_EITHER_OPPOSITE__U25",
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30",
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U35",
)


def _load_recheck_universe_rows(
    data,
):
    path = (
        PIPE_RESULTS
        / "universe_size_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "universe_size_latest.json missing"
        )

    payload = _load_json_file(
        path
    )

    current_hash = (
        data[
            "fingerprint"
        ].get(
            "fingerprint_hash"
        )
    )
    prior_hash = (
        payload.get(
            "data_fingerprint",
            {},
        ).get(
            "fingerprint_hash"
        )
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "UNIVERSE RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    by_candidate = {
        str(
            row[
                "candidate"
            ]
        ):
            row
        for row in payload.get(
            "results",
            [],
        )
    }

    missing = [
        name
        for name
        in WALK_FORWARD_RECHECK_TARGETS
        if name not in by_candidate
    ]

    if missing:
        raise RuntimeError(
            "recheck target missing from universe result: "
            + repr(
                missing
            )
        )

    rows = [
        by_candidate[
            name
        ]
        for name
        in WALK_FORWARD_RECHECK_TARGETS
    ]

    print()
    print("=" * 112)
    print("WALK-FORWARD RECHECK 3 TARGETS")
    print("=" * 112)

    for row in rows:
        print(
            row[
                "candidate"
            ],
            "| equity=",
            f"${row['metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['metrics']['realized_mdd_pct']:+.4f}%",
            "| LIQ=",
            row[
                "metrics"
            ][
                "liquidations"
            ],
        )

    return (
        payload,
        rows,
    )


def _wf_parent_from_universe_row(
    row,
):
    parent = dict(
        row[
            "parent"
        ]
    )

    parent[
        "baseline_candidate"
    ] = str(
        row[
            "candidate"
        ]
    )
    parent[
        "baseline_metrics"
    ] = dict(
        row[
            "metrics"
        ]
    )
    parent[
        "universe_size"
    ] = int(
        row[
            "universe_size"
        ]
    )
    parent[
        "universe_symbols"
    ] = list(
        row[
            "symbols"
        ]
    )
    parent[
        "reused_full35"
    ] = bool(
        row.get(
            "reused_full35",
            False,
        )
    )

    return parent



def _run_recheck_replay_parity(
    data,
    books,
    parent,
):
    """
    Strict parity for normal Universe rows.

    Special U35 repair:
    Universe Lab reused an older FULL35 result produced with data["symbols"]
    ordering, while Universe Lab itself defines U35 as fixed10 + dynamic25
    snapshot order. The set is identical but the simulator is order-sensitive
    when simultaneous candidates compete for portfolio capacity.

    For a row explicitly marked reused_full35=True:
      - require same 35-symbol set,
      - require legacy order != Universe order,
      - recompute one FULL35 run in the Universe order,
      - use that recomputed run as the corrected U35 baseline,
      - never overwrite the old immutable Universe result.
    """
    if not bool(
        parent.get(
            "reused_full35",
            False,
        )
    ):
        return (
            _run_wf_candidate_replay_parity(
                data,
                books,
                parent,
            )
        )

    legacy_order = [
        str(x).upper()
        for x in data[
            "symbols"
        ]
    ]
    universe_order = [
        str(x).upper()
        for x in parent[
            "universe_symbols"
        ]
    ]

    if len(
        universe_order
    ) != 35:
        raise RuntimeError(
            "U35 rebase expected 35 symbols"
        )

    if set(
        legacy_order
    ) != set(
        universe_order
    ):
        raise RuntimeError(
            "U35 rebase symbol set mismatch"
        )

    if legacy_order == universe_order:
        raise RuntimeError(
            "U35 rebase guard failed: orders are identical"
        )

    (
        engine,
        _exit_policy,
        _timing_policy,
    ) = (
        build_exit_source_timing_engine(
            m,
            parent[
                "exit_config"
            ],
            parent[
                "timing_config"
            ],
            books,
        )
    )

    fold = {
        "test_start_ts":
            int(
                data[
                    "start_ts"
                ]
            ),
        "test_end_ts":
            int(
                data[
                    "end_ts"
                ]
            ),
    }

    kwargs = (
        _make_wf_kwargs(
            data,
            parent,
            fold,
        )
    )

    result, elapsed = timed_run(
        engine.run,
        kwargs,
        "U35_ORDERED_FULL_REBASE",
    )

    metrics = extract_metrics(
        result
    )

    old_expected = dict(
        parent[
            "baseline_metrics"
        ]
    )

    ok_old, diffs_old = (
        compare_metrics(
            metrics,
            old_expected,
        )
    )

    print()
    print(
        "U35 LEGACY FULL35 REUSED =",
        True,
    )
    print(
        "U35 LEGACY ORDER         =",
        legacy_order,
    )
    print(
        "U35 UNIVERSE ORDER       =",
        universe_order,
    )
    print(
        "U35 OLD BASELINE MATCH   =",
        "PASS"
        if ok_old
        else "EXPECTED_MISMATCH",
    )
    print(
        "U35 OLD BASELINE DIFFS   =",
        diffs_old,
    )
    print(
        "U35 ORDERED FULL EQUITY  =",
        f"${metrics['final_equity']:,.2f}",
    )
    print(
        "U35 ORDERED FULL MDD     =",
        f"{metrics['realized_mdd_pct']:+.6f}%",
    )
    print(
        "U35 ORDERED FULL LIQ     =",
        metrics[
            "liquidations"
        ],
    )
    print(
        "U35 ORDER REBASE         = PASS"
    )

    if (
        int(
            metrics.get(
                "liquidations",
                0,
            )
        )
        > 0
        or bool(
            metrics.get(
                "bankrupt",
                False,
            )
        )
    ):
        raise SystemExit(
            "U35_ORDER_REBASE_HARD_FAIL"
        )

    # Use the corrected current Universe ordering for this recheck only.
    # Do NOT mutate universe_size_latest.json.
    parent[
        "baseline_metrics"
    ] = dict(
        metrics
    )
    parent[
        "u35_order_rebased"
    ] = True
    parent[
        "u35_legacy_baseline_metrics"
    ] = old_expected

    return {
        "status":
            "U35_ORDER_REBASE_PASS",
        "metrics":
            metrics,
        "legacy_expected":
            old_expected,
        "legacy_diffs":
            diffs_old,
        "legacy_order":
            legacy_order,
        "universe_order":
            universe_order,
        "elapsed_seconds":
            float(
                elapsed
            ),
    }


def _rank_recheck_passes(
    candidate_summaries,
):
    passed = [
        row
        for row in candidate_summaries
        if row[
            "verdict"
        ][
            "verdict"
        ]
        == "PASS"
    ]

    passed.sort(
        key=lambda row: (
            -float(
                row[
                    "verdict"
                ].get(
                    "geo_equity_ratio_vs_standard",
                    0.0,
                )
            ),
            -int(
                row[
                    "verdict"
                ].get(
                    "equity_wins_vs_standard",
                    0,
                )
            ),
            -int(
                row[
                    "verdict"
                ].get(
                    "mdd_wins_vs_standard",
                    0,
                )
            ),
            -float(
                row[
                    "parent"
                ][
                    "baseline_metrics"
                ][
                    "final_equity"
                ]
            ),
        )
    )

    return passed


def run_walk_forward_recheck_3(
    data,
    args,
):
    summary_stage = (
        "walk_forward_recheck_3_v1"
    )

    # IMPORTANT:
    # Use the exact same checkpoint stage as the first Walk-Forward so the
    # already-computed STANDARD V1 folds are reused by RESUME.
    fold_checkpoint_stage = (
        "walk_forward_v1"
    )

    (
        universe_payload,
        target_rows,
    ) = (
        _load_recheck_universe_rows(
            data
        )
    )

    standard_parent = (
        _standard_walk_forward_parent(
            data
        )
    )

    folds = (
        _build_walk_forward_folds(
            data[
                "start_ts"
            ],
            data[
                "end_ts"
            ],
        )
    )

    with Heartbeat(
        "BUILD_WF_RECHECK_EXIT_SOURCE_BOOKS"
    ):
        books = (
            build_exit_source_books(
                data
            )
        )

    print()
    print("=" * 112)
    print("WALK-FORWARD RECHECK FOLDS")
    print("=" * 112)

    for fold in folds:
        print(
            "FOLD",
            fold[
                "fold"
            ],
            "| test=",
            pd.to_datetime(
                fold[
                    "test_start_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "->",
            pd.to_datetime(
                fold[
                    "test_end_ts"
                ],
                unit="s",
                utc=True,
            ).strftime(
                "%Y-%m-%d"
            ),
            "| days=",
            fold[
                "test_days"
            ],
        )

    candidate_summaries = []

    for target_row in target_rows:
        parent = (
            _wf_parent_from_universe_row(
                target_row
            )
        )

        print()
        print("=" * 112)
        print(
            "RECHECK CANDIDATE =",
            parent[
                "baseline_candidate"
            ],
        )
        print(
            "FULL EQUITY       =",
            f"${parent['baseline_metrics']['final_equity']:,.2f}",
        )
        print(
            "FULL MDD          =",
            f"{parent['baseline_metrics']['realized_mdd_pct']:+.4f}%",
        )
        print(
            "UNIVERSE          =",
            parent[
                "universe_size"
            ],
        )
        print("=" * 112)

        replay_parity = (
            _run_recheck_replay_parity(
                data,
                books,
                parent,
            )
        )

        fold_rows = []

        for fold in folds:
            print()
            print("#" * 112)
            print(
                "CANDIDATE =",
                parent[
                    "baseline_candidate"
                ],
            )
            print(
                "FOLD      =",
                fold[
                    "fold"
                ],
            )
            print("#" * 112)

            cand_run = (
                _run_or_resume_wf_fold(
                    data=data,
                    books=books,
                    stage=
                        fold_checkpoint_stage,
                    role="CANDIDATE",
                    parent=parent,
                    fold=fold,
                    resume=
                        args.resume,
                )
            )

            std_run = (
                _run_or_resume_wf_fold(
                    data=data,
                    books=books,
                    stage=
                        fold_checkpoint_stage,
                    role="STANDARD_V1",
                    parent=
                        standard_parent,
                    fold=fold,
                    resume=
                        args.resume,
                )
            )

            cm = cand_run[
                "metrics"
            ]
            sm = std_run[
                "metrics"
            ]

            eq_ratio = (
                float(
                    cm[
                        "final_equity"
                    ]
                )
                / max(
                    1e-12,
                    float(
                        sm[
                            "final_equity"
                        ]
                    ),
                )
            )

            mdd_delta = (
                float(
                    cm[
                        "realized_mdd_pct"
                    ]
                )
                - float(
                    sm[
                        "realized_mdd_pct"
                    ]
                )
            )

            print_metrics(
                (
                    f"RECHECK FOLD {fold['fold']} "
                    "CANDIDATE"
                ),
                cm,
                cand_run[
                    "elapsed_seconds"
                ],
            )

            print(
                "STANDARD V1 =",
                f"${sm['final_equity']:,.2f}",
                "| MDD=",
                f"{sm['realized_mdd_pct']:+.4f}%",
                "| LIQ=",
                sm[
                    "liquidations"
                ],
                "| resumed=",
                std_run[
                    "resumed"
                ],
            )

            print(
                "FOLD COMPARE =",
                f"equity_ratio={eq_ratio:.6f}",
                f"mdd_delta={mdd_delta:+.6f}pp",
                "candidate_liq=",
                cm[
                    "liquidations"
                ],
            )

            fold_rows.append({
                **fold,
                "candidate_metrics":
                    cm,
                "standard_metrics":
                    sm,
                "equity_ratio_vs_standard":
                    eq_ratio,
                "mdd_delta_pp_vs_standard":
                    mdd_delta,
                "candidate_checkpoint":
                    cand_run[
                        "checkpoint"
                    ],
                "standard_checkpoint":
                    std_run[
                        "checkpoint"
                    ],
                "standard_resumed":
                    std_run[
                        "resumed"
                    ],
            })

        verdict = (
            _walk_forward_verdict(
                fold_rows
            )
        )

        print()
        print(
            "CANDIDATE VERDICT =",
            verdict[
                "verdict"
            ],
        )
        print(
            "CHECKS            =",
            verdict.get(
                "checks",
                {},
            ),
        )
        print(
            "PROFITABLE FOLDS  =",
            verdict.get(
                "profitable_folds",
                0,
            ),
            "/",
            len(
                fold_rows
            ),
        )
        print(
            "EQUITY WINS       =",
            verdict.get(
                "equity_wins_vs_standard",
                0,
            ),
            "/",
            len(
                fold_rows
            ),
        )
        print(
            "MDD WINS          =",
            verdict.get(
                "mdd_wins_vs_standard",
                0,
            ),
            "/",
            len(
                fold_rows
            ),
        )
        print(
            "GEO EQUITY RATIO  =",
            f"{verdict.get('geo_equity_ratio_vs_standard', 0.0):.6f}",
        )

        candidate_summaries.append({
            "candidate":
                parent[
                    "baseline_candidate"
                ],
            "parent":
                parent,
            "replay_parity":
                replay_parity,
            "folds":
                fold_rows,
            "verdict":
                verdict,
        })

    passed = (
        _rank_recheck_passes(
            candidate_summaries
        )
    )

    print()
    print("=" * 112)
    print("WALK-FORWARD RECHECK 3 RESULT")
    print("=" * 112)

    for i, row in enumerate(
        candidate_summaries,
        1,
    ):
        print(
            f"{i}.",
            row[
                "candidate"
            ],
            "| verdict=",
            row[
                "verdict"
            ][
                "verdict"
            ],
            "| profitable=",
            row[
                "verdict"
            ].get(
                "profitable_folds",
                0,
            ),
            "/",
            len(
                row[
                    "folds"
                ]
            ),
            "| equity_wins=",
            row[
                "verdict"
            ].get(
                "equity_wins_vs_standard",
                0,
            ),
            "| mdd_wins=",
            row[
                "verdict"
            ].get(
                "mdd_wins_vs_standard",
                0,
            ),
            "| geo_ratio=",
            f"{row['verdict'].get('geo_equity_ratio_vs_standard', 0.0):.6f}",
        )

    if passed:
        top = passed[
            0
        ]
        next_action = (
            "ROBUSTNESS_CONFIRM_THEN_ORIGINAL_EXACT"
        )
        print()
        print(
            "TOP PASS =",
            top[
                "candidate"
            ],
        )
        print(
            "NEXT ACTION = ROBUSTNESS_CONFIRM_THEN_ORIGINAL_EXACT"
        )
    else:
        top = None
        next_action = (
            "NO_PASS_REVIEW"
        )
        print()
        print(
            "TOP PASS = NONE"
        )
        print(
            "NEXT ACTION = NO_PASS_REVIEW"
        )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_WALK_FORWARD_RECHECK_3",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "targets":
            list(
                WALK_FORWARD_RECHECK_TARGETS
            ),
        "fold_config": {
            "min_train_days":
                WALK_FORWARD_MIN_TRAIN_DAYS,
            "test_days":
                WALK_FORWARD_TEST_DAYS,
            "step_days":
                WALK_FORWARD_STEP_DAYS,
            "min_final_days":
                WALK_FORWARD_MIN_FINAL_DAYS,
        },
        "candidate_results":
            candidate_summaries,
        "pass_candidates":
            passed,
        "top_pass_candidate":
            (
                top
                if top is not None
                else None
            ),
        "next_action":
            next_action,
    }

    timestamped = (
        PIPE_RESULTS
        / (
            "walk_forward_recheck_3_"
            + stamp
            + ".json"
        )
    )
    latest = (
        PIPE_RESULTS
        / "walk_forward_recheck_3_latest.json"
    )

    atomic_write_json(
        timestamped,
        summary,
    )
    atomic_write_json(
        latest,
        summary,
    )

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            summary_stage,
        "candidate":
            "RECHECK_3",
        "scope":
            "ANCHORED_WALK_FORWARD",
        "result_path":
            str(
                timestamped
            ),
        "pass_candidates": [
            {
                "candidate":
                    row[
                        "candidate"
                    ],
                "geo_equity_ratio_vs_standard":
                    row[
                        "verdict"
                    ].get(
                        "geo_equity_ratio_vs_standard",
                        0.0,
                    ),
                "equity_wins_vs_standard":
                    row[
                        "verdict"
                    ].get(
                        "equity_wins_vs_standard",
                        0,
                    ),
                "mdd_wins_vs_standard":
                    row[
                        "verdict"
                    ].get(
                        "mdd_wins_vs_standard",
                        0,
                    ),
            }
            for row in passed
        ],
        "next_action":
            next_action,
    })

    print()
    print("=" * 112)
    print("WALK-FORWARD RECHECK 3 COMPLETE")
    print("=" * 112)
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )
    print(
        "PASS CANDIDATES    =",
        [
            row[
                "candidate"
            ]
            for row in passed
        ],
    )
    print(
        "NEXT ACTION        =",
        next_action,
    )

    return summary




# =============================================================================
# ROBUSTNESS CONFIRM U30 V1
# =============================================================================
#
# Purpose:
#   Final robustness confirmation for the Walk-Forward top PASS only.
#
# Candidate:
#   T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30
#
# It reuses the exact same robustness families and coarse PASS/REVIEW logic
# already used in ROBUSTNESS LAB V1:
#   - time thirds
#   - transaction-cost proxy 7.5bp / 10bp per side
#   - +1H / +2H signal delay
#   - trailing neighborhood
#   - adjacent universe evidence
#
# No new parameter search is performed.
# PASS -> freeze STANDARD V2 candidate for ORIGINAL EXACT.
# =============================================================================

ROBUSTNESS_CONFIRM_TARGET = (
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30"
)


def _load_recheck_top_pass(data):
    path = (
        PIPE_RESULTS
        / "walk_forward_recheck_3_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "walk_forward_recheck_3_latest.json missing"
        )

    payload = _load_json_file(path)

    current_hash = data["fingerprint"].get(
        "fingerprint_hash"
    )
    prior_hash = payload.get(
        "data_fingerprint",
        {},
    ).get(
        "fingerprint_hash"
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "RECHECK RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    top = payload.get(
        "top_pass_candidate"
    )

    if not top:
        raise RuntimeError(
            "top_pass_candidate missing"
        )

    if str(top.get("candidate")) != ROBUSTNESS_CONFIRM_TARGET:
        raise RuntimeError(
            "unexpected top pass candidate: "
            f"{top.get('candidate')}"
        )

    return payload, top


def _load_universe_row_for_candidate(
    data,
    candidate_name,
):
    path = (
        PIPE_RESULTS
        / "universe_size_latest.json"
    )

    if not path.exists():
        raise RuntimeError(
            "universe_size_latest.json missing"
        )

    payload = _load_json_file(path)

    current_hash = data["fingerprint"].get(
        "fingerprint_hash"
    )
    prior_hash = payload.get(
        "data_fingerprint",
        {},
    ).get(
        "fingerprint_hash"
    )

    if prior_hash != current_hash:
        raise RuntimeError(
            "UNIVERSE RESULT FINGERPRINT MISMATCH: "
            f"{prior_hash} != {current_hash}"
        )

    for row in payload.get("results", []):
        if str(row.get("candidate")) == str(candidate_name):
            return payload, row

    raise RuntimeError(
        f"candidate missing from universe results: {candidate_name}"
    )


def _freeze_u30_for_original_exact(
    data,
    parent,
    walk_forward_recheck,
    robustness_confirm,
):
    stamp = utc_stamp()

    payload = {
        "schema":
            "STANDARD_V2_CANDIDATE_FREEZE",
        "freeze_id":
            "STANDARD_V2_CANDIDATE_" + stamp,
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "frozen_at_utc":
            stamp,
        "status":
            "FROZEN_FOR_ORIGINAL_EXACT",
        "source_candidate":
            parent["baseline_candidate"],
        "strategy": {
            "owner_frames":
                list(OWNER_FRAMES),
            "leverage_map":
                dict(LEVERAGE),
            "entry_signal":
                "FINAL_B_EMA_CROSS_PLUS_SAR",
            "entry_gate":
                "STANDARD_CROSS_ENTRY_GATE",
            "control_sl":
                "STANDARD_CONTROL_SL",
            "reentry":
                "STANDARD_STATE_REENTRY",
            "trailing":
                dict(parent["trailing_config"]),
            "opposite_exit_source":
                parent["exit_source"],
            "opposite_reopen_timing":
                parent["timing_mode"],
            "universe_size":
                int(parent["universe_size"]),
            "symbols":
                list(parent["universe_symbols"]),
        },
        "baseline_full_metrics":
            dict(parent["baseline_metrics"]),
        "walk_forward_recheck":
            walk_forward_recheck,
        "robustness_confirm":
            robustness_confirm,
        "data_fingerprint":
            data["fingerprint"],
        "next_action":
            "ORIGINAL_EXACT",
    }

    payload["freeze_hash"] = stable_hash(payload)

    timestamped = (
        PIPE_RESULTS
        / f"standard_v2_candidate_freeze_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "standard_v2_candidate_freeze_latest.json"
    )

    atomic_write_json(timestamped, payload)
    atomic_write_json(latest, payload)

    append_master_record({
        "schema":
            "STANDARD_V2_CANDIDATE_FREEZE",
        "stage":
            "robustness_confirm_u30_v1",
        "candidate":
            parent["baseline_candidate"],
        "scope":
            "FROZEN_FOR_ORIGINAL_EXACT",
        "freeze_path":
            str(timestamped),
        "freeze_hash":
            payload["freeze_hash"],
        "data_fingerprint":
            data["fingerprint"],
        "next_action":
            "ORIGINAL_EXACT",
    })

    return payload, timestamped, latest


def run_robustness_confirm_u30(
    data,
    args,
):
    stage = "robustness_confirm_u30_v1"

    recheck_payload, top = _load_recheck_top_pass(
        data
    )
    universe_payload, universe_row = (
        _load_universe_row_for_candidate(
            data,
            ROBUSTNESS_CONFIRM_TARGET,
        )
    )

    parent = _wf_parent_from_universe_row(
        universe_row
    )

    # Ensure the exact candidate selected by Walk-Forward is what is tested.
    if parent["baseline_candidate"] != ROBUSTNESS_CONFIRM_TARGET:
        raise RuntimeError(
            "robustness confirm candidate mismatch"
        )

    with Heartbeat(
        "BUILD_ROBUSTNESS_CONFIRM_EXIT_SOURCE_BOOKS"
    ):
        base_books = build_exit_source_books(
            data
        )

    print()
    print("=" * 112)
    print("ROBUSTNESS CONFIRM TARGET")
    print("=" * 112)
    print("CANDIDATE =", parent["baseline_candidate"])
    print(
        "FULL EQUITY =",
        f"${parent['baseline_metrics']['final_equity']:,.2f}",
    )
    print(
        "FULL MDD    =",
        f"{parent['baseline_metrics']['realized_mdd_pct']:+.6f}%",
    )
    print("UNIVERSE    =", parent["universe_size"])
    print(
        "WF GEO RATIO=",
        f"{top['verdict'].get('geo_equity_ratio_vs_standard', 0.0):.6f}",
    )
    print("=" * 112)

    time_slices = _robust_time_slices(
        data["start_ts"],
        data["end_ts"],
    )

    scenarios = []

    for label, start_ts, end_ts in time_slices:
        scenarios.append(
            (
                label,
                {
                    "family":
                        "TIME_SLICE",
                    "start_ts":
                        int(start_ts),
                    "end_ts":
                        int(end_ts),
                },
            )
        )

    scenarios.extend([
        (
            "COST_7P5BP_PER_SIDE",
            {
                "family":
                    "TRANSACTION_COST_PROXY",
                "effective_fee_rate":
                    0.00075,
            },
        ),
        (
            "COST_10BP_PER_SIDE",
            {
                "family":
                    "TRANSACTION_COST_PROXY",
                "effective_fee_rate":
                    0.00100,
            },
        ),
        (
            "DELAY_1H",
            {
                "family":
                    "SIGNAL_DELAY",
                "delay_hours":
                    1,
            },
        ),
        (
            "DELAY_2H",
            {
                "family":
                    "SIGNAL_DELAY",
                "delay_hours":
                    2,
            },
        ),
    ])

    base_start = float(
        parent["trailing_config"][
            "trailing_start_pct"
        ]
    )
    base_callback = float(
        parent["trailing_config"][
            "trailing_callback_pct"
        ]
    )

    raw_trail = [
        ("TRAIL_START_2", 2.0, base_callback),
        ("TRAIL_START_4", 4.0, base_callback),
        ("TRAIL_CALLBACK_1", base_start, 1.0),
        ("TRAIL_CALLBACK_2", base_start, 2.0),
    ]

    seen = {
        (base_start, base_callback)
    }
    trail_scenarios = []

    for label, start_v, callback_v in raw_trail:
        pair = (
            float(start_v),
            float(callback_v),
        )
        if pair in seen:
            continue
        seen.add(pair)
        trail_scenarios.append(
            (
                label,
                {
                    "family":
                        "TRAILING_NEIGHBORHOOD",
                    "trailing_start_pct":
                        float(start_v),
                    "trailing_callback_pct":
                        float(callback_v),
                },
            )
        )

    scenarios.extend(trail_scenarios)

    scenario_rows = []

    for scenario_name, scenario_config in scenarios:
        print()
        print("#" * 112)
        print("ROBUSTNESS CONFIRM =", scenario_name)
        print("CONFIG             =", scenario_config)
        print("#" * 112)

        row = _run_or_resume_robust_scenario(
            data=data,
            base_books=base_books,
            stage=stage,
            parent=parent,
            scenario_name=scenario_name,
            scenario_config=scenario_config,
            resume=args.resume,
        )

        print_metrics(
            "ROBUSTNESS CONFIRM - "
            + row["candidate"],
            row["metrics"],
            row["elapsed_seconds"],
        )

        scenario_rows.append(row)

    adjacent_rows = (
        _find_adjacent_universe_evidence(
            universe_payload,
            universe_row,
        )
    )

    print()
    print("ADJACENT UNIVERSE EVIDENCE =")
    for row in adjacent_rows:
        print(
            " -",
            row["candidate"],
            "| equity=",
            f"${row['metrics']['final_equity']:,.2f}",
            "| MDD=",
            f"{row['metrics']['realized_mdd_pct']:+.4f}%",
            "| LIQ=",
            row["metrics"]["liquidations"],
        )

    verdict = _robustness_verdict(
        parent["baseline_metrics"],
        scenario_rows,
        adjacent_rows,
    )

    print()
    print("=" * 112)
    print("ROBUSTNESS CONFIRM VERDICT")
    print("=" * 112)
    print("VERDICT =", verdict["verdict"])
    print(
        "SOFT CHECKS =",
        verdict.get("soft_checks", {}),
    )
    print(
        "HARD FAIL REASONS =",
        verdict.get("hard_fail_reasons", []),
    )

    stamp = utc_stamp()

    summary = {
        "schema":
            "RESEARCH_PIPELINE_V2_ROBUSTNESS_CONFIRM_U30",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "candidate":
            parent["baseline_candidate"],
        "parent":
            parent,
        "walk_forward_top_pass":
            top,
        "scenario_results":
            scenario_rows,
        "adjacent_universe_evidence":
            adjacent_rows,
        "verdict":
            verdict,
        "data_fingerprint":
            data["fingerprint"],
        "next_action":
            (
                "ORIGINAL_EXACT"
                if verdict["verdict"] == "PASS"
                else "REVIEW"
            ),
    }

    timestamped = (
        PIPE_RESULTS
        / f"robustness_confirm_u30_{stamp}.json"
    )
    latest = (
        PIPE_RESULTS
        / "robustness_confirm_u30_latest.json"
    )

    atomic_write_json(timestamped, summary)
    atomic_write_json(latest, summary)

    append_master_record({
        "schema":
            "RESEARCH_STAGE_SUMMARY_V2",
        "stage":
            stage,
        "candidate":
            parent["baseline_candidate"],
        "scope":
            "ROBUSTNESS_CONFIRM",
        "result_path":
            str(timestamped),
        "verdict":
            verdict,
        "data_fingerprint":
            data["fingerprint"],
        "next_action":
            summary["next_action"],
    })

    if verdict["verdict"] == "PASS":
        (
            freeze_payload,
            freeze_timestamped,
            freeze_latest,
        ) = _freeze_u30_for_original_exact(
            data,
            parent,
            top,
            summary,
        )

        print()
        print(
            "CANDIDATE FREEZE =",
            freeze_latest,
        )
        print(
            "FREEZE HASH      =",
            freeze_payload["freeze_hash"],
        )
        print(
            "NEXT ACTION      = ORIGINAL_EXACT"
        )
    else:
        print()
        print("CANDIDATE FREEZE = NONE")
        print("NEXT ACTION      = REVIEW")

    print()
    print("=" * 112)
    print("ROBUSTNESS CONFIRM U30 COMPLETE")
    print("=" * 112)
    print("TIMESTAMPED RESULT =", timestamped)
    print("LATEST RESULT      =", latest)

    return summary




# =============================================================================
# ORIGINAL EXACT U30 FINAL V1
# =============================================================================
#
# Final historical execution/parity test for the frozen STANDARD V2 candidate.
#
# IMPORTANT:
#   - Uses the ORIGINAL leverage_scan.simulate_live_multiframe source.
#   - DOES NOT apply _FastPatchTransformer or any FAST replacements.
#   - Applies only the one already-selected strategy behavior change:
#       opposite reopen timing = NEXT_1H_STATE
#   - Exit source must remain STANDARD_COMBINED for this frozen T1/U30 candidate.
#   - Entry Gate / Control SL / Mark liquidation / STATE reentry remain exact
#     original leverage_scan behavior.
#   - STANDARD V1 source files are never modified.
#
# PASS requires exact metric parity with the frozen FAST FULL candidate
# within the existing strict compare_metrics() tolerance.
# =============================================================================

ORIGINAL_EXACT_TARGET = (
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30"
)


def _load_original_exact_freeze(data):
    freeze_path = (
        PIPE_RESULTS
        / "standard_v2_candidate_freeze_latest.json"
    )

    if not freeze_path.exists():
        raise RuntimeError(
            "standard_v2_candidate_freeze_latest.json missing"
        )

    freeze = _load_json_file(
        freeze_path
    )

    if freeze.get("schema") != "STANDARD_V2_CANDIDATE_FREEZE":
        raise RuntimeError(
            "bad candidate freeze schema"
        )

    if freeze.get("status") != "FROZEN_FOR_ORIGINAL_EXACT":
        raise RuntimeError(
            "candidate is not frozen for ORIGINAL EXACT"
        )

    if str(freeze.get("source_candidate")) != ORIGINAL_EXACT_TARGET:
        raise RuntimeError(
            "unexpected frozen candidate: "
            + repr(
                freeze.get("source_candidate")
            )
        )

    current_hash = data["fingerprint"].get(
        "fingerprint_hash"
    )
    frozen_hash = freeze.get(
        "data_fingerprint",
        {},
    ).get(
        "fingerprint_hash"
    )

    if current_hash != frozen_hash:
        raise RuntimeError(
            "ORIGINAL EXACT DATA FINGERPRINT MISMATCH: "
            f"{frozen_hash} != {current_hash}"
        )

    strategy = dict(
        freeze.get(
            "strategy",
            {},
        )
    )

    required = {
        "universe_size": 30,
        "opposite_exit_source": "STANDARD_COMBINED",
        "opposite_reopen_timing": "NEXT_1H_STATE",
    }

    for key, expected in required.items():
        actual = strategy.get(key)
        if str(actual) != str(expected):
            raise RuntimeError(
                "frozen strategy mismatch: "
                f"{key} expected={expected!r} actual={actual!r}"
            )

    symbols = [
        str(x).upper()
        for x in strategy.get(
            "symbols",
            [],
        )
    ]

    if len(symbols) != 30:
        raise RuntimeError(
            f"frozen U30 expected 30 symbols, got {len(symbols)}"
        )

    trailing = dict(
        strategy.get(
            "trailing",
            {},
        )
    )

    if (
        float(
            trailing.get(
                "trailing_start_pct",
                -1,
            )
        )
        != 3.0
        or float(
            trailing.get(
                "trailing_callback_pct",
                -1,
            )
        )
        != 1.5
    ):
        raise RuntimeError(
            "frozen trailing mismatch; expected 3.0 / 1.5"
        )

    expected_metrics = dict(
        freeze.get(
            "baseline_full_metrics",
            {},
        )
    )

    missing_metrics = [
        key
        for key in (
            *PARITY_FLOAT_KEYS,
            *PARITY_INT_KEYS,
        )
        if key not in expected_metrics
    ]

    if missing_metrics:
        raise RuntimeError(
            "frozen baseline metrics missing: "
            + repr(
                missing_metrics
            )
        )

    return (
        freeze_path,
        freeze,
        strategy,
        symbols,
        expected_metrics,
    )


class OriginalExactTimingEngine:
    """
    ORIGINAL exact simulator clone.

    No FAST AST optimization is applied here.
    Only the selected NEXT_1H_STATE opposite-reopen behavior is patched.
    """

    def __init__(
        self,
        module,
        timing_config,
    ):
        self.m = module
        self.policy = OppositeTimingPolicy(
            timing_config
        )
        self.patch_stats = {}
        self.simulator = self._build_simulator()

    def _build_simulator(self):
        src = textwrap.dedent(
            inspect.getsource(
                self.m.simulate_live_multiframe
            )
        )
        tree = ast.parse(
            src
        )

        transformer = (
            _OppositeReopenTransformer()
        )
        tree = transformer.visit(
            tree
        )
        ast.fix_missing_locations(
            tree
        )

        if transformer.replaced != 1:
            raise RuntimeError(
                "ORIGINAL EXACT OPPOSITE REOPEN PATCH TARGET ERROR: "
                f"expected=1 actual={transformer.replaced}"
            )

        self.patch_stats = {
            "fast_patch_applied": False,
            "opposite_reopen_replaced":
                int(
                    transformer.replaced
                ),
        }

        code = compile(
            tree,
            "<standard_v2_original_exact_simulator>",
            "exec",
        )

        # Critical: original leverage_scan namespace only.
        # No FastEngine cached/replacement helpers are injected.
        ns = dict(
            self.m.__dict__
        )
        ns[
            "_pipeline_opposite_reopen"
        ] = (
            self.policy.reopen
        )

        exec(
            code,
            ns,
            ns,
        )

        fn = ns.get(
            "simulate_live_multiframe"
        )

        if not callable(fn):
            raise RuntimeError(
                "ORIGINAL EXACT simulator clone missing"
            )

        return fn

    def run(
        self,
        kwargs,
    ):
        return self.simulator(
            **kwargs
        )


def _original_exact_kwargs(
    data,
    strategy,
    symbols,
):
    trailing = dict(
        strategy[
            "trailing"
        ]
    )

    leverage_map = {
        str(k).upper():
            int(v)
        for k, v in dict(
            strategy.get(
                "leverage_map",
                LEVERAGE,
            )
        ).items()
    }

    frames = tuple(
        str(x).upper()
        for x in strategy.get(
            "owner_frames",
            OWNER_FRAMES,
        )
    )

    if frames != OWNER_FRAMES:
        raise RuntimeError(
            f"unexpected owner frames: {frames}"
        )

    if leverage_map != LEVERAGE:
        raise RuntimeError(
            f"unexpected leverage map: {leverage_map}"
        )

    return {
        "signal_book":
            data[
                "event_book"
            ],
        "symbols":
            list(
                symbols
            ),
        "snapshot":
            data[
                "snapshot"
            ],
        "market":
            data[
                "market"
            ],
        "start_ts":
            int(
                data[
                    "start_ts"
                ]
            ),
        "end_ts":
            int(
                data[
                    "end_ts"
                ]
            ),
        "leverage_map":
            leverage_map,
        "frames":
            frames,
        "active_count":
            int(
                strategy[
                    "universe_size"
                ]
            ),
        "start_equity":
            10000.0,
        "collect_trade_log":
            False,
        "trailing_start_pct":
            float(
                trailing[
                    "trailing_start_pct"
                ]
            ),
        "trailing_callback_pct":
            float(
                trailing[
                    "trailing_callback_pct"
                ]
            ),
        "state_reentry_book":
            data[
                "owner_state_book"
            ],
        "state_reentry_pullback_pct":
            0.0,
    }


def run_original_exact_u30(
    data,
    args,
):
    (
        freeze_path,
        freeze,
        strategy,
        symbols,
        expected_metrics,
    ) = _load_original_exact_freeze(
        data
    )

    print()
    print("=" * 112)
    print("ORIGINAL EXACT FROZEN CANDIDATE")
    print("=" * 112)
    print(
        "CANDIDATE      =",
        freeze[
            "source_candidate"
        ],
    )
    print(
        "FREEZE HASH    =",
        freeze.get(
            "freeze_hash"
        ),
    )
    print(
        "UNIVERSE       =",
        strategy[
            "universe_size"
        ],
    )
    print(
        "TRAILING       =",
        strategy[
            "trailing"
        ][
            "trailing_start_pct"
        ],
        "/",
        strategy[
            "trailing"
        ][
            "trailing_callback_pct"
        ],
    )
    print(
        "EXIT SOURCE    =",
        strategy[
            "opposite_exit_source"
        ],
    )
    print(
        "REOPEN TIMING  =",
        strategy[
            "opposite_reopen_timing"
        ],
    )
    print(
        "EXPECTED EQUITY=",
        f"${expected_metrics['final_equity']:,.2f}",
    )
    print(
        "EXPECTED MDD   =",
        f"{expected_metrics['realized_mdd_pct']:+.6f}%",
    )
    print("=" * 112)

    engine = OriginalExactTimingEngine(
        m,
        {
            "mode":
                strategy[
                    "opposite_reopen_timing"
                ],
            "description":
                "Frozen STANDARD V2 ORIGINAL EXACT",
            "complexity":
                1,
        },
    )

    print(
        "ORIGINAL EXACT PATCH STATS =",
        engine.patch_stats,
    )

    kwargs = _original_exact_kwargs(
        data,
        strategy,
        symbols,
    )

    started = time.perf_counter()

    with Heartbeat(
        "ORIGINAL_EXACT_U30",
        sec=60,
    ):
        result = engine.run(
            kwargs
        )

    elapsed = (
        time.perf_counter()
        - started
    )

    metrics = extract_metrics(
        result
    )

    print_metrics(
        "STANDARD V2 ORIGINAL EXACT U30",
        metrics,
        elapsed,
    )

    parity_ok, diffs = compare_metrics(
        metrics,
        expected_metrics,
    )

    hard_ok = (
        int(
            metrics.get(
                "liquidations",
                0,
            )
        )
        == 0
        and not bool(
            metrics.get(
                "bankrupt",
                False,
            )
        )
    )

    verdict = (
        "PASS"
        if (
            parity_ok
            and hard_ok
        )
        else "FAIL"
    )

    print()
    print("=" * 112)
    print("ORIGINAL EXACT FINAL VERDICT")
    print("=" * 112)
    print(
        "FAST/FROZEN PARITY =",
        "PASS"
        if parity_ok
        else "FAIL",
    )
    print(
        "PARITY DIFFS       =",
        diffs,
    )
    print(
        "ZERO LIQ / SURVIVE =",
        "PASS"
        if hard_ok
        else "FAIL",
    )
    print(
        "VERDICT            =",
        verdict,
    )

    stamp = utc_stamp()

    payload = {
        "schema":
            "STANDARD_V2_ORIGINAL_EXACT_RESULT",
        "pipeline_build_id":
            BUILD_ID,
        "fast_build_id":
            FAST_BUILD_ID,
        "saved_at_utc":
            stamp,
        "standard_source_modified":
            False,
        "source_freeze_path":
            str(
                freeze_path
            ),
        "source_freeze_hash":
            freeze.get(
                "freeze_hash"
            ),
        "candidate":
            freeze[
                "source_candidate"
            ],
        "strategy":
            strategy,
        "expected_fast_frozen_metrics":
            expected_metrics,
        "original_exact_metrics":
            metrics,
        "parity_ok":
            bool(
                parity_ok
            ),
        "parity_diffs":
            diffs,
        "hard_safety_ok":
            bool(
                hard_ok
            ),
        "verdict":
            verdict,
        "elapsed_seconds":
            float(
                elapsed
            ),
        "exact_patch_stats":
            engine.patch_stats,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "next_action":
            (
                "STANDARD_V2_VERSION_RECORD_AND_SERVER_PARITY_PREP"
                if verdict == "PASS"
                else "STOP_REVIEW_ORIGINAL_EXACT_MISMATCH"
            ),
    }

    timestamped = (
        PIPE_RESULTS
        / (
            "original_exact_u30_"
            + stamp
            + ".json"
        )
    )
    latest = (
        PIPE_RESULTS
        / "original_exact_u30_latest.json"
    )

    atomic_write_json(
        timestamped,
        payload,
    )
    atomic_write_json(
        latest,
        payload,
    )

    append_master_record({
        "schema":
            "STANDARD_V2_ORIGINAL_EXACT_RESULT",
        "stage":
            "original_exact_u30_v1",
        "candidate":
            freeze[
                "source_candidate"
            ],
        "scope":
            "FINAL_ORIGINAL_EXACT",
        "result_path":
            str(
                timestamped
            ),
        "freeze_hash":
            freeze.get(
                "freeze_hash"
            ),
        "metrics":
            metrics,
        "parity_ok":
            bool(
                parity_ok
            ),
        "verdict":
            verdict,
        "data_fingerprint":
            data[
                "fingerprint"
            ],
        "next_action":
            payload[
                "next_action"
            ],
    })

    if verdict == "PASS":
        validation = {
            "schema":
                "STANDARD_V2_HISTORICAL_VALIDATION",
            "version":
                "STANDARD_V2",
            "status":
                "HISTORICAL_VALIDATION_PASS",
            "candidate":
                freeze[
                    "source_candidate"
                ],
            "freeze_hash":
                freeze.get(
                    "freeze_hash"
                ),
            "original_exact_result":
                str(
                    timestamped
                ),
            "original_exact_metrics":
                metrics,
            "data_fingerprint":
                data[
                    "fingerprint"
                ],
            "note":
                (
                    "Historical validation used the frozen U30 symbol set. "
                    "Any live Dynamic Top-N universe policy must be recorded "
                    "separately as an operational universe policy."
                ),
            "next_action":
                (
                    "STANDARD_V2_VERSION_RECORD_AND_SERVER_PARITY_PREP"
                ),
        }

        validation_path = (
            PIPE_RESULTS
            / "standard_v2_validation_latest.json"
        )
        atomic_write_json(
            validation_path,
            validation,
        )

        print(
            "STANDARD V2 HISTORICAL VALIDATION = PASS"
        )
        print(
            "VALIDATION RECORD =",
            validation_path,
        )
        print(
            "NEXT ACTION = STANDARD_V2_VERSION_RECORD_AND_SERVER_PARITY_PREP"
        )
    else:
        print(
            "STANDARD V2 HISTORICAL VALIDATION = FAIL"
        )
        print(
            "NEXT ACTION = STOP_REVIEW_ORIGINAL_EXACT_MISMATCH"
        )

    print()
    print(
        "TIMESTAMPED RESULT =",
        timestamped,
    )
    print(
        "LATEST RESULT      =",
        latest,
    )

    return payload



# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Research Pipeline V2. "
            "Final ORIGINAL EXACT U30."
        )
    )
    parser.add_argument(
        "--stage",
        choices=(
            "original_exact_u30",
        ),
        default="original_exact_u30",
    )
    args = parser.parse_args()

    RESULTS.mkdir(
        parents=True,
        exist_ok=True,
    )
    PIPE_RESULTS.mkdir(
        parents=True,
        exist_ok=True,
    )
    CHECKPOINT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 112)
    print("RESEARCH PIPELINE V2")
    print("BUILD ID      =", BUILD_ID)
    print("FAST BUILD ID =", FAST_BUILD_ID)
    print("STANDARD SOURCE FILES ARE NOT MODIFIED")
    print("CHAMPION      = SEALED STANDARD V1")
    print("STAGE         = original_exact_u30")
    print("TARGET        = FROZEN T1 U30")
    print("ENGINE        = ORIGINAL EXACT")
    print("FAST PATCH    = OFF")
    print("=" * 112)

    register_previous_stress_gate()

    with Heartbeat(
        "PREPARE_SHARED_READ_ONLY_DATA"
    ):
        data = prepare_data()

    run_original_exact_u30(
        data,
        args,
    )


if __name__ == "__main__":
    main()
