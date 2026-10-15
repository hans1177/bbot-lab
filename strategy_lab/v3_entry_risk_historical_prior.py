from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

ROOT = Path(r"C:\my\bottest")
RESULTS = ROOT / "strategy_lab" / "results"
OUT = RESULTS / "v3" / "entry_risk_historical_prior.json"

SCHEMA = "B_BOT_V3_ENTRY_RISK_HISTORICAL_PRIOR_V1"

PATHS = {
    "trade_mae": RESULTS / "b_exact_owner_mfe_mae_trades.csv",
    "stress_entries": RESULTS / "b_t4_2_state_5_10_15_7_current35_risk_probe_entries.csv",
    "cross_stress_summary": RESULTS / "mark_price_1h_cross_stress_by_symbol.csv",
    "cross_stress_library": ROOT / "risk_data" / "mark_price_1h_cross_stress_library.parquet",
    "r5a_leverage": RESULTS / "r5a_leverage_freeze_latest.json",
    "r5b_sizing": RESULTS / "r5b_freeze_latest.json",
    "r7_cross_entry_gate": RESULTS / "r7_cross_entry_gate_freeze_latest.json",
}

for name, p in PATHS.items():
    if not p.exists():
        raise FileNotFoundError(f"{name}: {p}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def finite_series(s):
    return pd.to_numeric(s, errors="coerce").dropna()


def q(s, value):
    s = finite_series(s)
    if s.empty:
        return None
    return float(s.quantile(value))


def mean(s):
    s = finite_series(s)
    if s.empty:
        return None
    return float(s.mean())


def median(s):
    s = finite_series(s)
    if s.empty:
        return None
    return float(s.median())


def side_name(v):
    try:
        v = int(float(v))
    except Exception:
        return str(v).upper()

    if v > 0:
        return "LONG"
    if v < 0:
        return "SHORT"
    return "UNKNOWN"


def bool_rate(series):
    vals = []

    for x in series:
        if pd.isna(x):
            continue

        if isinstance(x, bool):
            vals.append(1.0 if x else 0.0)
            continue

        s = str(x).strip().lower()

        if s in {"true", "yes", "y"}:
            vals.append(1.0)
            continue

        if s in {"false", "no", "n"}:
            vals.append(0.0)
            continue

        try:
            vals.append(1.0 if float(x) != 0 else 0.0)
        except Exception:
            continue

    return (sum(vals) / len(vals)) if vals else None


# ------------------------------------------------------------------
# 1. Historical realized MAE/MFE prior
# ------------------------------------------------------------------
trades = pd.read_csv(PATHS["trade_mae"])

required = {
    "symbol",
    "side",
    "owner_tf",
    "entry_price",
    "exit_reason",
    "mfe_pct",
    "mae_abs_pct",
    "hold_hours",
}

missing = required - set(trades.columns)
if missing:
    raise RuntimeError(f"TRADE_MAE_COLUMNS_MISSING={sorted(missing)}")

trade_mae_prior = []

for (tf, side), g in trades.groupby(["owner_tf", "side"], dropna=False):
    adverse = finite_series(g["mae_abs_pct"])
    mfe = finite_series(g["mfe_pct"])
    hold = finite_series(g["hold_hours"])

    reasons = g["exit_reason"].fillna("").astype(str).str.upper()
    observed_stop = reasons.str.contains("STOP|CONTROL_SL", regex=True)

    trade_mae_prior.append(
        {
            "timeframe": str(tf).upper(),
            "side": side_name(side),
            "samples": int(len(g)),
            "symbols": int(g["symbol"].astype(str).str.upper().nunique()),
            "mae_abs_pct": {
                "mean": mean(adverse),
                "p50": q(adverse, 0.50),
                "p75": q(adverse, 0.75),
                "p90": q(adverse, 0.90),
                "p95": q(adverse, 0.95),
                "p99": q(adverse, 0.99),
                "max": float(adverse.max()) if not adverse.empty else None,
            },
            "mfe_pct": {
                "p50": q(mfe, 0.50),
                "p90": q(mfe, 0.90),
            },
            "holding_hours": {
                "p50": q(hold, 0.50),
                "p90": q(hold, 0.90),
            },
            # Historical observation only. NOT predictive probability.
            "observed_stop_exit_rate": (
                float(observed_stop.mean())
                if len(observed_stop)
                else None
            ),
        }
    )


# ------------------------------------------------------------------
# 2. Symbol-specific 1H adverse / Cross-Stress prior
# ------------------------------------------------------------------
cross = pd.read_csv(PATHS["cross_stress_summary"])

cross_required = {
    "symbol",
    "max_long_adverse_pct",
    "p999_long_adverse_pct",
    "max_short_adverse_pct",
    "p999_short_adverse_pct",
}

missing = cross_required - set(cross.columns)
if missing:
    raise RuntimeError(f"CROSS_STRESS_COLUMNS_MISSING={sorted(missing)}")

cross_stress_prior = []

for _, r in cross.iterrows():
    cross_stress_prior.append(
        {
            "symbol": str(r["symbol"]).upper(),
            "long": {
                "p999_adverse_pct": float(r["p999_long_adverse_pct"]),
                "historical_max_adverse_pct": float(r["max_long_adverse_pct"]),
            },
            "short": {
                "p999_adverse_pct": float(r["p999_short_adverse_pct"]),
                "historical_max_adverse_pct": float(r["max_short_adverse_pct"]),
            },
        }
    )


# ------------------------------------------------------------------
# 3. Historical candidate/account Cross-Stress entry observations
# ------------------------------------------------------------------
stress = pd.read_csv(PATHS["stress_entries"])

stress_required = {
    "symbol",
    "owner_tf",
    "side",
    "requested_leverage",
    "actual_leverage",
    "post_min_single_stress_cushion_ratio",
    "candidate_created_breach",
    "candidate_worsened_breach",
}

missing = stress_required - set(stress.columns)
if missing:
    raise RuntimeError(f"STRESS_ENTRY_COLUMNS_MISSING={sorted(missing)}")

stress_entry_prior = []

for (tf, side), g in stress.groupby(["owner_tf", "side"], dropna=False):
    stress_entry_prior.append(
        {
            "timeframe": str(tf).upper(),
            "side": side_name(side),
            "samples": int(len(g)),
            "requested_leverage_p50": median(g["requested_leverage"]),
            "actual_leverage_p50": median(g["actual_leverage"]),
            "post_stress_cushion_ratio": {
                "p01": q(g["post_min_single_stress_cushion_ratio"], 0.01),
                "p05": q(g["post_min_single_stress_cushion_ratio"], 0.05),
                "p50": q(g["post_min_single_stress_cushion_ratio"], 0.50),
            },
            "candidate_created_breach_rate": bool_rate(
                g["candidate_created_breach"]
            ),
            "candidate_worsened_breach_rate": bool_rate(
                g["candidate_worsened_breach"]
            ),
        }
    )


# ------------------------------------------------------------------
# 4. Frozen legacy baselines
# ------------------------------------------------------------------
def load_json(p):
    return json.loads(p.read_text(encoding="utf-8-sig"))


r5a = load_json(PATHS["r5a_leverage"])
r5b = load_json(PATHS["r5b_sizing"])
r7 = load_json(PATHS["r7_cross_entry_gate"])

legacy_baselines = {
    "R5A_LEVERAGE": {
        "freeze_status": r5a.get("freeze_status"),
        "winner": r5a.get("winner"),
        "selected_leverage_map": r5a.get("selected_leverage_map"),
        "liquidation_hard_fail_count": r5a.get("liquidation_hard_fail_count"),
    },
    "R5B_SIZING": {
        "freeze_status": r5b.get("freeze_status"),
        "winner": r5b.get("winner"),
        "selected_sizing_multiplier": r5b.get("selected_sizing_multiplier"),
    },
    "R7_CROSS_ENTRY_GATE": {
        "freeze_status": r7.get("freeze_status"),
        "winner": r7.get("winner"),
        "selected_cross_entry_gate_mode": r7.get(
            "selected_cross_entry_gate_mode"
        ),
        "standard_metrics": r7.get("standard_metrics"),
    },
}


# ------------------------------------------------------------------
# 5. Provenance
# ------------------------------------------------------------------
sources = {}

for name, p in PATHS.items():
    sources[name] = {
        "path": str(p.relative_to(ROOT)),
        "size": int(p.stat().st_size),
        "sha256": sha256(p),
    }


# ------------------------------------------------------------------
# 6. Research-only Historical Prior artifact
# ------------------------------------------------------------------
artifact = {
    "schema": SCHEMA,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "status": "HISTORICAL_PRIOR_RESEARCH_ONLY",

    # Critical: historical data is NOT declared to be calibrated prediction.
    "predictive_calibration_status":
        "NOT_PREDICTED_VS_REALIZED_CALIBRATED",

    # Critical: this artifact changes no runtime policy by itself.
    "policy_effect":
        "NONE_UNTIL_SEPARATE_VALIDATION_AND_POLICY_VERSION",

    "interpretation": {
        "trade_mae_prior":
            "REALIZED_V2_DISTRIBUTION_NOT_V3_PREDICTION",
        "cross_stress_prior":
            "HISTORICAL_1H_MARK_PRICE_ADVERSE_REFERENCE",
        "stress_entry_prior":
            "HISTORICAL_ACCOUNT_CROSS_STRESS_ENTRY_OBSERVATION",
        "stop_rate":
            "OBSERVED_EXIT_LABEL_RATE_NOT_STOP_HIT_PROBABILITY",
    },

    "trade_mae_prior": trade_mae_prior,
    "cross_stress_prior": cross_stress_prior,
    "stress_entry_prior": stress_entry_prior,
    "legacy_baselines": legacy_baselines,
    "sources": sources,
}

OUT.parent.mkdir(parents=True, exist_ok=True)

tmp = OUT.with_suffix(".json.tmp")
tmp.write_text(
    json.dumps(
        artifact,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n",
    encoding="utf-8",
)
tmp.replace(OUT)

print("=" * 100)
print("V3 ENTRY-RISK HISTORICAL PRIOR BUILT")
print("=" * 100)
print("OUTPUT =", OUT)
print("TRADE_PRIOR_GROUPS =", len(trade_mae_prior))
print("CROSS_STRESS_SYMBOLS =", len(cross_stress_prior))
print("STRESS_ENTRY_GROUPS =", len(stress_entry_prior))
print("POLICY_EFFECT = NONE")
print("RUNTIME_CHANGE = 0")
print("SERVER_WRITE = 0")
print("ORDER_MUTATION = 0")
