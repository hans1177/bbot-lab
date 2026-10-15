# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_policy_research.py
"""V3 비-STRATEGY Track bounded 정책 연구.

연구 결과는 Research Candidate Policy Version까지만 생성한다.
Runner/order/Core 수정이나 LIVE config 직접 변경은 금지된다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import v3_entry_risk_calibration as entry_risk_calibration
import v3_evidence_research as evidence
import v3_governance as governance
import v3_task_queue as task_queue

# ============================================================
# 경로 / 상수
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
V3 = ROOT / "strategy_lab" / "results" / "v3"
DB = ROOT / "data" / "server_runtime" / "history.db"
OUT = V3 / "policy_research.json"
MAX_RESULTS = 1000

# ============================================================
# 유틸
# ============================================================


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def atomic(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def table_columns() -> set[str]:
    if not DB.is_file():
        return set()
    con = sqlite3.connect(str(DB), timeout=10)
    try:
        return {str(r[1]) for r in con.execute("PRAGMA table_info(exit_history)").fetchall()}
    except Exception:
        return set()
    finally:
        con.close()


def query(sql: str) -> list[dict[str, Any]]:
    if not DB.is_file():
        return []
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql).fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def register_policy(track: str, payload: dict[str, Any], evidence_row: dict[str, Any]) -> dict[str, Any]:
    return governance.register_version_candidate("POLICY", track, payload, evidence_row)


# ============================================================
# Track 연구 함수
# ============================================================


def research_entry_risk() -> dict[str, Any]:
    """ENTRY_RISK research with explicit evidence-validation promotion gate."""
    import v3_entry_risk_forward_shadow_collector as _entry_risk_forward_shadow_collector
    _entry_risk_forward_shadow_collector.run_once()
    import v3_entry_risk_forward_outcome_collector as _entry_risk_forward_outcome_collector
    _entry_risk_forward_outcome_collector.run_once()

    # ENTRY_RISK_EVIDENCE_VALIDATION_GATE_V1
    from pathlib import Path
    import json

    measured = (
        entry_risk_calibration.run_once()
    )

    root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    validation_path = (
        root
        / "strategy_lab"
        / "results"
        / "v3"
        / "entry_risk_evidence_validation.json"
    )

    paired_samples = int(
        measured.get(
            "paired_samples"
        )
        or 0
    )

    minimum_samples = int(
        measured.get(
            "minimum_paired_samples"
        )
        or 20
    )

    evidence = dict(
        measured
    )

    promotion_gate = {
        "schema":
            "B_BOT_V3_ENTRY_RISK_PROMOTION_GATE_V1",

        "calibration_status":
            measured.get(
                "status"
            ),

        "paired_samples":
            paired_samples,

        "minimum_paired_samples":
            minimum_samples,

        "validation_artifact":
            str(
                validation_path.relative_to(
                    root
                )
            ),

        "validation_status":
            "MISSING",

        "independent_validation":
            False,

        "retrospective_only":
            True,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,

        "reason":
            "EVIDENCE_VALIDATION_REQUIRED",
    }

    evidence[
        "promotion_gate"
    ] = promotion_gate

    # Calibration itself is never sufficient for promotion.
    if (
        measured.get("status")
        != "MEASURED"
        or
        paired_samples
        < minimum_samples
        or
        measured.get(
            "live_effect"
        )
        is not False
    ):
        promotion_gate[
            "reason"
        ] = (
            "CALIBRATION_NOT_READY"
        )

        return {
            "status":
                "MEASURED",

            "candidate":
                None,

            "evidence":
                evidence,
        }

    if not validation_path.is_file():
        return {
            "status":
                "MEASURED",

            "candidate":
                None,

            "evidence":
                evidence,
        }

    try:
        validation = json.loads(
            validation_path.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception as exc:
        promotion_gate[
            "validation_status"
        ] = "INVALID"

        promotion_gate[
            "reason"
        ] = (
            "EVIDENCE_VALIDATION_ARTIFACT_INVALID"
        )

        promotion_gate[
            "validation_error"
        ] = repr(exc)

        return {
            "status":
                "MEASURED",

            "candidate":
                None,

            "evidence":
                evidence,
        }

    promotion_gate[
        "validation_status"
    ] = validation.get(
        "status"
    )

    promotion_gate[
        "independent_validation"
    ] = (
        validation.get(
            "independent_validation"
        )
        is True
    )

    promotion_gate[
        "retrospective_only"
    ] = (
        validation.get(
            "retrospective_only"
        )
        is not False
    )

    promotion_gate[
        "policy_candidate_allowed"
    ] = (
        validation.get(
            "policy_candidate_allowed"
        )
        is True
    )

    validation_ok = (
        validation.get("schema")
        ==
        "B_BOT_V3_ENTRY_RISK_EVIDENCE_VALIDATION_V1"
        and
        validation.get("status")
        == "VALIDATED"
        and
        validation.get(
            "independent_validation"
        )
        is True
        and
        validation.get(
            "retrospective_only"
        )
        is False
        and
        validation.get(
            "policy_candidate_allowed"
        )
        is True
        and
        validation.get(
            "live_effect"
        )
        is False
    )

    if not validation_ok:
        promotion_gate[
            "reason"
        ] = (
            "EVIDENCE_VALIDATION_NOT_PROMOTABLE"
        )

        return {
            "status":
                "MEASURED",

            "candidate":
                None,

            "evidence":
                evidence,
        }

    promotion_gate[
        "reason"
    ] = "PROMOTION_GATE_PASS"

    payload = {
        "model":
            "EMPIRICAL_STOP_MAE_CALIBRATION",

        "adverse_corr":
            measured.get(
                "adverse_corr"
            ),

        "predicted_stop_avg":
            measured.get(
                "predicted_stop_avg"
            ),

        "realized_stop_rate":
            measured.get(
                "realized_stop_rate"
            ),

        "validation_schema":
            validation.get(
                "schema"
            ),

        "validation_status":
            validation.get(
                "status"
            ),

        "live_effect":
            "NONE_UNTIL_VALIDATED",
    }

    return {
        "status":
            "CANDIDATE",

        "candidate":
            register_policy(
                "ENTRY_RISK",
                payload,
                evidence,
            ),

        "evidence":
            evidence,
    }



def _grid_values(start, end, step):
    out = []
    value = float(start)
    while value <= float(end) + 1e-12:
        out.append(round(value, 8))
        value += float(step)
    return out


def _individual_trailing_contract():
    constitution = governance.constitution()
    return (constitution.get("individual_strategy_trailing_optimization") or {}).get("trailing_search_contract") or {}


def _individual_trailing_plan() -> dict[str, Any]:
    c = _individual_trailing_contract()
    allowed_start = c.get("allowed_start_pct") or {}
    allowed_callback = c.get("allowed_callback_pct") or {}
    coarse = c.get("coarse_grid") or {}
    starts = _grid_values(
        allowed_start.get("min", 1.0),
        allowed_start.get("max", 6.0),
        coarse.get("start_step", 1.0),
    )
    callbacks = _grid_values(
        allowed_callback.get("min", 0.5),
        allowed_callback.get("max", 3.0),
        coarse.get("callback_step", 0.5),
    )
    candidates = [
        {"start_pct": s, "callback_pct": cb}
        for s in starts
        for cb in callbacks
        if cb < s
    ]
    baseline = c.get("baseline") or {"start_pct": 3.0, "callback_pct": 1.5}
    if not any(
        abs(float(x["start_pct"]) - float(baseline.get("start_pct", 3.0))) < 1e-12
        and abs(float(x["callback_pct"]) - float(baseline.get("callback_pct", 1.5))) < 1e-12
        for x in candidates
    ):
        candidates.append({"start_pct": float(baseline.get("start_pct", 3.0)), "callback_pct": float(baseline.get("callback_pct", 1.5))})
    candidates.sort(key=lambda x: (float(x["start_pct"]), float(x["callback_pct"])))
    return {
        "status": "PLAN",
        "track": "EXIT",
        "mode": "INDIVIDUAL_STRATEGY_TRAILING",
        "baseline": baseline,
        "coarse_candidates": candidates,
        "contract": c,
        "live_effect": False,
    }


def _measured_trailing_rows(context):
    rows = []
    raw = context.get("evaluations") or {}
    if isinstance(raw, dict):
        raw = list(raw.values())
    for row in raw:
        if not isinstance(row, dict) or str(row.get("status") or "").upper() != "MEASURED":
            continue
        ev = row.get("evidence") or {}
        try:
            start = float(row.get("start_pct"))
            callback = float(row.get("callback_pct"))
        except Exception:
            continue
        rows.append({"start_pct": start, "callback_pct": callback, "evidence": ev})
    return rows


def _metric(ev, name):
    try:
        value = float(ev.get(name))
    except Exception:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _trailing_row_eligible(row, minimum_trades):
    ev = row.get("evidence") or {}
    wf = _metric(ev, "walk_forward_oos_expectancy_pct")
    stress = _metric(ev, "cost_stress_2x_expectancy_pct")
    trades = int(ev.get("trades") or 0)
    return trades >= int(minimum_trades or 1) and wf is not None and wf > 0.0 and stress is not None and stress > 0.0


def _trailing_quality_tuple(row):
    ev = row.get("evidence") or {}
    wf = _metric(ev, "walk_forward_oos_expectancy_pct")
    stress = _metric(ev, "cost_stress_2x_expectancy_pct")
    expectancy = _metric(ev, "expectancy_pct")
    payoff = _metric(ev, "payoff_ratio")
    mdd = _metric(ev, "normalized_mdd_pct_points")
    giveback = _metric(ev, "avg_mfe_giveback_pct")
    return (
        wf if wf is not None else -1e99,
        stress if stress is not None else -1e99,
        expectancy if expectancy is not None else -1e99,
        payoff if payoff is not None else -1e99,
        -(mdd if mdd is not None else 1e99),
        -(giveback if giveback is not None else 1e99),
    )


def _individual_trailing_fine_plan(context) -> dict[str, Any]:
    c = _individual_trailing_contract()
    rows = _measured_trailing_rows(context)
    minimum_trades = int(context.get("minimum_trades") or 1)
    eligible = [x for x in rows if _trailing_row_eligible(x, minimum_trades)]
    eligible.sort(key=_trailing_quality_tuple, reverse=True)
    centers = eligible[:3]
    fine = c.get("fine_grid") or {}
    allowed_start = c.get("allowed_start_pct") or {}
    allowed_callback = c.get("allowed_callback_pct") or {}
    smin = float(allowed_start.get("min", 1.0)); smax = float(allowed_start.get("max", 6.0))
    cbmin = float(allowed_callback.get("min", 0.5)); cbmax = float(allowed_callback.get("max", 3.0))
    srad = float(fine.get("start_radius", 0.5)); cbrad = float(fine.get("callback_radius", 0.5))
    sstep = float(fine.get("start_step", 0.5)); cbstep = float(fine.get("callback_step", 0.25))
    measured_keys = {(round(x["start_pct"],8), round(x["callback_pct"],8)) for x in rows}
    candidates = {}
    for center in centers:
        starts = _grid_values(max(smin, center["start_pct"]-srad), min(smax, center["start_pct"]+srad), sstep)
        callbacks = _grid_values(max(cbmin, center["callback_pct"]-cbrad), min(cbmax, center["callback_pct"]+cbrad), cbstep)
        for start in starts:
            for callback in callbacks:
                if callback >= start:
                    continue
                key = (round(start,8), round(callback,8))
                if key in measured_keys:
                    continue
                candidates[key] = {"start_pct": start, "callback_pct": callback}
    ordered = [candidates[k] for k in sorted(candidates)]
    return {
        "status": "FINE_PLAN",
        "track": "EXIT",
        "mode": "INDIVIDUAL_STRATEGY_TRAILING",
        "centers": [{"start_pct": x["start_pct"], "callback_pct": x["callback_pct"]} for x in centers],
        "fine_candidates": ordered,
        "live_effect": False,
    }


def _individual_trailing_select(context) -> dict[str, Any]:
    c = _individual_trailing_contract()
    rows = _measured_trailing_rows(context)
    minimum_trades = int(context.get("minimum_trades") or 1)
    baseline_cfg = c.get("baseline") or {"start_pct": 3.0, "callback_pct": 1.5}
    baseline = None
    for row in rows:
        if abs(row["start_pct"]-float(baseline_cfg.get("start_pct",3.0))) < 1e-12 and abs(row["callback_pct"]-float(baseline_cfg.get("callback_pct",1.5))) < 1e-12:
            baseline = row
            break
    if baseline is None or not _trailing_row_eligible(baseline, minimum_trades):
        return {
            "status":"INCOMPLETE",
            "selected":None,
            "fallback":baseline_cfg,
            "reason":"BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID",
            "live_effect":False,
        }

    base_ev = baseline.get("evidence") or {}
    base_wf = _metric(base_ev, "walk_forward_oos_expectancy_pct")
    candidates = []
    for row in rows:
        if not _trailing_row_eligible(row, minimum_trades):
            continue
        if row is baseline:
            continue
        ev = row.get("evidence") or {}
        wf = _metric(ev, "walk_forward_oos_expectancy_pct")
        if wf is None or base_wf is None or wf <= base_wf:
            continue
        neighbors = [
            x for x in rows
            if abs(x["start_pct"]-row["start_pct"]) <= 0.5000001
            and abs(x["callback_pct"]-row["callback_pct"]) <= 0.5000001
        ]
        if len(neighbors) < 3:
            continue
        positive = [x for x in neighbors if _trailing_row_eligible(x, minimum_trades)]
        positive_ratio = len(positive) / float(len(neighbors))
        if positive_ratio < float(c.get("selection_neighbor_positive_ratio_min",0.70)):
            continue
        positive_wf = sorted(_metric(x.get("evidence") or {}, "walk_forward_oos_expectancy_pct") for x in positive)
        positive_wf = [x for x in positive_wf if x is not None]
        if not positive_wf:
            continue
        mid = len(positive_wf)//2
        neighborhood_median_wf = positive_wf[mid] if len(positive_wf)%2 else (positive_wf[mid-1]+positive_wf[mid])/2.0
        if neighborhood_median_wf <= base_wf:
            continue

        strengths=[]
        for metric,better_high,label in [
            ("payoff_ratio",True,"PAYOFF"),
            ("normalized_mdd_pct_points",False,"MDD"),
            ("avg_mfe_giveback_pct",False,"GIVEBACK"),
            ("avg_holding_time_hours",False,"HOLDING_TIME"),
        ]:
            cv=_metric(ev,metric); bv=_metric(base_ev,metric)
            if cv is None or bv is None:
                continue
            if (better_high and cv > bv) or ((not better_high) and cv < bv):
                strengths.append(label)
        if not strengths:
            continue

        candidates.append({
            "row":row,
            "positive_neighbor_ratio":positive_ratio,
            "neighborhood_median_wf":neighborhood_median_wf,
            "secondary_strengths":strengths,
        })

    if not candidates:
        return {
            "status":"FALLBACK",
            "selected":dict(baseline_cfg),
            "fallback":True,
            "reason":"NO_ROBUST_MULTI_METRIC_IMPROVEMENT",
            "baseline_evidence":base_ev,
            "live_effect":False,
        }

    candidates.sort(
        key=lambda x:(
            x["neighborhood_median_wf"],
            _trailing_quality_tuple(x["row"]),
            x["positive_neighbor_ratio"],
            len(x["secondary_strengths"]),
        ),
        reverse=True,
    )
    best=candidates[0]
    row=best["row"]
    return {
        "status":"SELECTED",
        "selected":{"start_pct":row["start_pct"],"callback_pct":row["callback_pct"]},
        "fallback":False,
        "reason":"ROBUST_NEIGHBORHOOD_MULTI_METRIC_IMPROVEMENT",
        "evidence":row.get("evidence") or {},
        "positive_neighbor_ratio":best["positive_neighbor_ratio"],
        "neighborhood_median_wf":best["neighborhood_median_wf"],
        "secondary_strengths":best["secondary_strengths"],
        "baseline_evidence":base_ev,
        "live_effect":False,
    }


def research_exit(context: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(context, dict) and str(context.get("mode") or "").upper() == "INDIVIDUAL_STRATEGY_TRAILING":
        action = str(context.get("action") or "PLAN").upper()
        if action == "PLAN":
            return _individual_trailing_plan()
        if action == "FINE_PLAN":
            return _individual_trailing_fine_plan(context)
        if action == "SELECT":
            return _individual_trailing_select(context)
        return {
            "status": "MEASURED",
            "candidate": None,
            "evidence": {
                "reason": "UNKNOWN_INDIVIDUAL_TRAILING_ACTION",
                "action": action,
                "live_effect": False,
            },
        }

    summary = evidence.exit_policy()
    rows = summary.get("rows") or []
    n = sum(int(r.get("n") or 0) for r in rows)
    if n < 20:
        return {"status": "MEASURED", "candidate": None, "evidence": {"closed_trades": n, "rows": rows[:50]}}
    payload = {
        "policy_family": "EXIT_TRAILING_TIMING_RESEARCH",
        "baseline": "CURRENT_FIXED_BASELINE",
        "observed_exit_groups": len(rows),
        "candidate_parameters": None,
        "reason": "PARAMETERS_REQUIRE_SEPARATE_BACKTEST_VALIDATION",
    }
    return {"status": "CANDIDATE", "candidate": register_policy("EXIT", payload, {"closed_trades": n, "rows": rows[:100]}), "evidence": {"closed_trades": n}}


def _individual_reentry_plan() -> dict[str, Any]:
    return {
        "status": "PLAN",
        "mode": "INDIVIDUAL_STRATEGY_REENTRY",
        "baseline": {
            "mode": "CURRENT_STATE_NEXT_1H",
            "enabled": True,
            "check_tf": "1H",
            "trigger": "TRAILING_STOP_ONLY",
        },
        "baseline_research_proxy": {
            "mode": "CURRENT_STATE_NEXT_1H_BASELINE_PROXY",
            "runtime_equivalent": False,
            "research_resolution": "STRATEGY_TIMEFRAME_PROXY",
        },
        "candidates": [
            {"mode": "CURRENT_STATE_CONFIRM_STRATEGY_BARS", "confirm_bars": 2, "runtime_equivalent": False},
            {"mode": "CURRENT_STATE_CONFIRM_STRATEGY_BARS", "confirm_bars": 3, "runtime_equivalent": False},
            {"mode": "CURRENT_STATE_RESET_RECLAIM", "runtime_equivalent": False},
        ],
        "research_objective": "TIMING_CONDITION_CONFIRMATION_WHIPSAW_METHOD",
        "exact_runtime_equivalent_replay_required_before_nonbaseline_adoption": True,
        "same_bar_immediate_reorder_forbidden": True,
        "trigger": "TRAILING_STOP_ONLY",
        "live_effect": False,
    }


def _reentry_finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _individual_reentry_select(context: dict[str, Any]) -> dict[str, Any]:
    baseline = context.get("baseline_evidence") or {}
    rows = context.get("evaluations") or {}
    min_trades = int(context.get("minimum_trades") or 1)

    # Non-baseline method research is allowed to collect evidence, but the
    # current single-timeframe simulator cannot exactly replay the live
    # CURRENT_STATE_NEXT_1H intrabar entry price for every strategy TF.
    # Therefore proxy evidence may never replace the existing live baseline.
    exact_rows = []
    for row in rows.values() if isinstance(rows, dict) else rows:
        if not isinstance(row, dict) or str(row.get("status") or "").upper() != "MEASURED":
            continue
        evidence = row.get("evidence") or {}
        if row.get("runtime_equivalent") is not True:
            continue
        trades = int(evidence.get("trades") or 0)
        wf = _reentry_finite(evidence.get("walk_forward_oos_expectancy_pct"))
        stress = _reentry_finite(evidence.get("cost_stress_2x_expectancy_pct"))
        if trades >= min_trades and wf is not None and wf > 0.0 and stress is not None and stress > 0.0:
            exact_rows.append(row)

    if exact_rows:
        raise RuntimeError("NONBASELINE_REENTRY_EXACT_REPLAY_SELECTION_NOT_IMPLEMENTED")

    return {
        "status": "FALLBACK",
        "selected": {
            "mode": "CURRENT_STATE_NEXT_1H",
            "enabled": True,
            "check_tf": "1H",
        },
        "fallback": True,
        "reason": "NO_EXACT_RUNTIME_EQUIVALENT_NONBASELINE_REENTRY_IMPROVEMENT",
        "baseline_evidence": baseline,
        "proxy_evaluations_measured": len(rows) if isinstance(rows, dict) else len(list(rows or [])),
        "live_effect": False,
    }


def research_reentry(context: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(context, dict) and str(context.get("mode") or "").upper() == "INDIVIDUAL_STRATEGY_REENTRY":
        action = str(context.get("action") or "PLAN").upper()
        if action == "PLAN":
            return _individual_reentry_plan()
        if action == "SELECT":
            return _individual_reentry_select(context)
        return {
            "status": "MEASURED",
            "candidate": None,
            "evidence": {
                "reason": "UNKNOWN_INDIVIDUAL_REENTRY_ACTION",
                "action": action,
                "live_effect": False,
            },
        }

    cols = table_columns()
    reentry_col = next((c for c in ("is_reentry", "reentry", "entry_reason") if c in cols), None)
    if not reentry_col:
        return {"status": "MEASURED", "candidate": None, "evidence": {"reason": "REENTRY_POINT_IN_TIME_FIELD_NOT_AVAILABLE"}}
    rows = query(f"SELECT {reentry_col} reentry_tag, COUNT(*) n, AVG(roi) avg_roi, SUM(COALESCE(pnl,0)) pnl FROM exit_history GROUP BY 1")
    n = sum(int(r.get("n") or 0) for r in rows)
    if n < 20:
        return {"status": "MEASURED", "candidate": None, "evidence": {"rows": rows, "n": n}}
    payload = {"policy_family": "REENTRY_TIMING", "baseline": "CURRENT_STATE_NEXT_1H", "candidate_parameters": None}
    return {"status": "CANDIDATE", "candidate": register_policy("REENTRY", payload, {"rows": rows}), "evidence": {"rows": rows}}


def research_symbol() -> dict[str, Any]:
    knowledge = load(V3 / "research_knowledge_runtime.json", {})
    records = knowledge.get("records") or {}
    symbol_records = [k for k in records if k.count("|") >= 2 and not k.endswith("|*")]
    payload = {
        "policy_family": "EXPECTED_ACCOUNT_CONTRIBUTION_CAPITAL_TIME",
        "symbol_specific_records": len(symbol_records),
        "missing_expectation": "BLOCK",
        "major_weight": 0.0,
    }
    if not symbol_records:
        return {"status": "MEASURED", "candidate": None, "evidence": payload}
    return {"status": "CANDIDATE", "candidate": register_policy("SYMBOL", payload, payload), "evidence": payload}


def research_portfolio() -> dict[str, Any]:
    cap = evidence.capital_time()
    rows = cap.get("rows") or []
    n = sum(int(r.get("n") or 0) for r in rows)
    payload = {
        "policy_family": "PORTFOLIO_CAPITAL_TIME_CONCENTRATION",
        "baseline": "NO_EQUAL_SPLIT_MARGIN_RESERVE",
        "realized_groups": len(rows),
        "candidate_limits": None,
    }
    if n < 20:
        return {"status": "MEASURED", "candidate": None, "evidence": {"n": n, "rows": rows}}
    return {"status": "CANDIDATE", "candidate": register_policy("PORTFOLIO", payload, {"n": n, "rows": rows}), "evidence": {"n": n}}


def research_regime() -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "candidate": None,
        "evidence": {
            "policy": "PIT_REQUIRED",
            "reason": "현재 runtime exit만으로 historical point-in-time regime policy를 만들지 않는다.",
        },
    }


def research_execution() -> dict[str, Any]:
    """
    Autonomous execution-cost/liquidity measurement.

    Measurement is not policy validation.
    Actual execution telemetry remains reference-only.
    No EXECUTION policy candidate is registered here until a
    separately validated numeric cost/liquidity policy exists.
    """
    import v3_execution_cost_liquidity_measurement as measurement

    measured = measurement.run_once()

    payload = {
        "policy_family":
            "VALIDATED_EXECUTION_COST_AND_LIQUIDITY_POLICY",
        "measurement_schema":
            measured.get("schema"),
        "measurement_status":
            measured.get("status"),
        "usable_observations":
            int(measured.get("usable_observation_count") or 0),
        "rejected_observations":
            int(measured.get("rejected_observation_count") or 0),
        "actual_execution_telemetry_role":
            "REFERENCE_ONLY_NON_BLOCKING_FOR_ALL_PROMOTIONS",
        "validated":
            False,
        "policy_candidate_allowed":
            False,
        "numeric_thresholds":
            None,
        "reason":
            "VALIDATED_NUMERIC_COST_LIQUIDITY_POLICY_NOT_AVAILABLE",
    }

    return {
        "status": "MEASURED",
        "candidate": None,
        "evidence": payload,
    }


def research_direction() -> dict[str, Any]:
    cols = table_columns()
    side_col = next((c for c in ("side", "entry_side", "position_side") if c in cols), None)
    if not side_col:
        return {"status": "MEASURED", "candidate": None, "evidence": {"reason": "DIRECTION_FIELD_NOT_AVAILABLE"}}
    rows = query(f"SELECT UPPER(COALESCE({side_col},'')) side, COUNT(*) n, AVG(roi) avg_roi, SUM(COALESCE(pnl,0)) pnl FROM exit_history GROUP BY 1")
    n = sum(int(r.get("n") or 0) for r in rows)
    if n < 20:
        return {"status": "MEASURED", "candidate": None, "evidence": {"rows": rows, "n": n}}
    payload = {"policy_family": "LONG_SHORT_ASYMMETRY", "rows": rows, "direct_bias_change": False}
    return {"status": "CANDIDATE", "candidate": register_policy("DIRECTION", payload, {"rows": rows}), "evidence": {"rows": rows}}


# ============================================================
# Dispatcher
# ============================================================


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    track = str(task.get("track") or "").upper()
    if track == "STRATEGY":
        return {"status": "DEFER_TO_STRATEGY_ENGINE", "candidate": None}

    funcs = {
        "ENTRY_RISK": research_entry_risk,
        "EXIT": research_exit,
        "REENTRY": research_reentry,
        "SYMBOL": research_symbol,
        "PORTFOLIO": research_portfolio,
        "REGIME": research_regime,
        "EXECUTION": research_execution,
        "DIRECTION": research_direction,
    }
    fn = funcs.get(track)
    if fn is None:
        return {"status": "FAIL", "reason": "TRACK_NOT_IMPLEMENTED", "track": track}

    before = governance.capture_kernel_guard()
    result = fn()
    governance.assert_kernel_unchanged(before)
    task_queue.record(task, result)

    state = load(OUT, {"schema": "B_BOT_V3_POLICY_RESEARCH_V1", "results": []})
    state.setdefault("results", []).append({"at": now(), "task": task, "result": result})
    state["results"] = state["results"][-MAX_RESULTS:]
    state["updated_at"] = now()
    atomic(OUT, state)
    return result
