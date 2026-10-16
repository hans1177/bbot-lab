# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_deployment.py
"""Autonomous V3 deployment state machine. It writes validated sync requests; it does not SSH."""
from __future__ import annotations
import json, os, sqlite3, shutil, time, math, statistics
from datetime import datetime, timezone
from pathlib import Path

import v3_governance as governance
import local_release_approval as release_approval

ROOT=Path(os.getenv("BBOT_LOCAL_ROOT",r"C:\my\bottest")); LAB=ROOT/"strategy_lab"; RES=LAB/"results"/"v3"; DEPLOY=LAB/"deploy_ready"
CONFIG=ROOT/"strategy_config.json"; STATE=RES/"deployment_state.json"; REQUEST=RES/"runtime_sync_request.json"; RUNTIME_DB=Path(os.getenv("BBOT_RUNTIME_DB",str(ROOT/"data"/"server_runtime"/"history.db")))
R4_FEEDBACK_DB=Path(os.getenv("BBOT_R4_FEEDBACK_DB",str(ROOT/"feedback"/"history_sync_snapshot.db")))
PUSH_HOLD=ROOT/"SERVER_PUSH_HOLD.flag"

def now():return datetime.now(timezone.utc).isoformat()
def load(p,d):
    try:return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception:return d
def atomic(p,o):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+".tmp"); t.write_text(json.dumps(o,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8"); os.replace(t,p)
def state():
    s = load(STATE, {})

    if s.get("schema") != "B_BOT_V3_DEPLOYMENT_STATE_V1":
        s = {
            "schema": "B_BOT_V3_DEPLOYMENT_STATE_V1",
            "updated_at": now(),
            "frames": {},
            "policies": {},
            "strategy_instances": {},
            "applied_request_ids": [],
        }

    s.setdefault("frames", {})
    s.setdefault("policies", {})
    s.setdefault("strategy_instances", {})
    s.setdefault("applied_request_ids", [])

    return s


def strategy_registry_row(name,filename): return {"file":filename,"material_key":name.lower(),"engine":"ASSIST_FILE"}
def source_for_single(row):
    p=ROOT/str(row.get("family_file") or "")
    if not p.is_file(): return None
    DEPLOY.mkdir(parents=True,exist_ok=True); dst=DEPLOY/(str(row["strategy"]).lower()+".py"); shutil.copy2(p,dst); return dst

def source_for_combo(row):
    # generated combination itself is live-compatible.
    name=str(row.get("strategy") or "").lower(); p=RES/"generated_combinations"/(name+".py")
    return p if p.is_file() else None

LOCAL_SHADOW_PORTABLE_PREFIXES = (
    "strategy_lab/results/v3/generated_combinations/",
    "strategy_lab/results/v3/generated_safe_grammar/",
    "strategy_lab/deploy_ready/",
)

def _pipeline_local_shadow_row(row):
    return bool(
        isinstance(row,dict)
        and str(row.get("pipeline_contract") or "") == "NEW_MAIN_LOCAL_SHADOW_V1"
        and str(row.get("pipeline_stage") or "") == "LOCAL_SHADOW_PENDING"
    )

def source_for_pipeline(row):
    rel=str((row or {}).get("canonical_source_file") or "").replace("\\","/").lstrip("./")
    if not rel.lower().endswith(".py"):
        return None
    # 같은 소스의 이식 가능 여부는 기존 연구 파이프라인 책임 함수와 일치시킨다.
    import v3_strategy_research as strategy_research
    if not strategy_research._pipeline_source_portable(rel):
        return None
    p=(ROOT/rel).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except Exception:
        return None
    return p if p.is_file() else None

def _pipeline_behavior_policy(cfg,row):
    cfg=cfg if isinstance(cfg,dict) else {}
    if not _pipeline_local_shadow_row(row):
        return {
            "trailing":json.loads(json.dumps(cfg.get("trailing") or {})),
            "state_reentry":json.loads(json.dumps(cfg.get("state_reentry") or {})),
            "opposite_exit":json.loads(json.dumps(cfg.get("opposite_exit") or {})),
            "opposite_reopen":json.loads(json.dumps(cfg.get("opposite_reopen") or {})),
        }

    trailing_selected=row.get("individual_trailing") or {}
    try:
        start=float(trailing_selected.get("start_pct"))
        callback=float(trailing_selected.get("callback_pct"))
    except Exception:
        raise RuntimeError("INDIVIDUAL_TRAILING_BINDING_MISSING")
    if not math.isfinite(start) or not math.isfinite(callback) or start<=0 or callback<=0 or callback>=start:
        raise RuntimeError("INDIVIDUAL_TRAILING_BINDING_INVALID")

    trailing=json.loads(json.dumps(cfg.get("trailing") or {}))
    trailing.update({
        "enabled":True,
        "dynamic_adjustment":False,
        "start_pct":start,
        "callback_pct":callback,
        "individual_strategy_binding":True,
        "binding_exact_strategy_version":str(row.get("exact_strategy_version") or ""),
        "binding_fallback":bool(trailing_selected.get("fallback")),
    })

    selected=row.get("individual_reentry") or {}
    mode=str(selected.get("mode") or "CURRENT_STATE_NEXT_1H").upper()
    state_reentry=json.loads(json.dumps(cfg.get("state_reentry") or {}))
    if mode!="CURRENT_STATE_NEXT_1H":
        raise RuntimeError("NONBASELINE_REENTRY_BINDING_REQUIRES_EXACT_RUNTIME_REPLAY")
    state_reentry.update({
        "enabled":True,
        "mode":"CURRENT_STATE",
        "next_check_tf":"1H",
        "individual_mode":"CURRENT_STATE_NEXT_1H",
        "trigger":"TRAILING_STOP_ONLY",
        "same_direction_required":True,
        "neutral_state_keeps_pending":True,
        "opposite_state_cancels":True,
        "same_bar_immediate_reorder_forbidden":True,
        "individual_strategy_binding":True,
        "binding_exact_strategy_version":str(row.get("exact_strategy_version") or ""),
        "research_fallback":bool(selected.get("fallback",True)),
    })

    return {
        "trailing":trailing,
        "state_reentry":state_reentry,
        "opposite_exit":json.loads(json.dumps(cfg.get("opposite_exit") or {})),
        "opposite_reopen":json.loads(json.dumps(cfg.get("opposite_reopen") or {})),
    }


def research_architecture_v3():
    arch = (
        governance.constitution()
        .get(
            "research_architecture_v3"
        )
        or {}
    )

    if arch.get("policy_id") != (
        "RA_MAIN_MIDDLE_DETAIL_20260830_V1"
    ):
        raise RuntimeError(
            "RESEARCH_ARCHITECTURE_INVALID"
        )

    return arch


def full_validation_pass(row):
    # FULL_VALIDATION boundary.

    if not isinstance(
        row,
        dict,
    ):
        return False

    ev = (
        row.get("evaluation")
        if isinstance(
            row.get("evaluation"),
            dict,
        )
        else {}
    )

    # CANARY_REQUIRES_HARD_PASS_V1
    if ev.get("hard_pass",ev.get("pass")) is not True:
        return False

    if (
        ev.get(
            "walk_forward_avg_net_pct"
        )
        is None
    ):
        return False

    if (
        ev.get(
            "walk_forward_cost_stress_2x_avg_net_pct"
        )
        is None
    ):
        return False

    return True

# RESEARCH_SOFT_SHADOW_ELIGIBILITY_V1
def research_soft_shadow_eligible(row):
    if not isinstance(row,dict):
        return False

    ev=row.get("evaluation") or {}

    if not isinstance(ev,dict):
        return False

    return bool(
        ev.get("soft_pass") is True
        and ev.get(
            "hard_pass",
            ev.get("pass"),
        ) is not True
        and ev.get(
            "research_shadow_eligible",
            True,
        ) is not False
    )



def minimal_shadow_pass(version_id, strategy_instance_id=""):
    return (
        release_approval
        .minimal_shadow_pass(
            version_id,
            strategy_instance_id,
        )
    )




def revalidate_normal_to_canary_retest(tf, evidence):
    """
    Existing NORMAL exact Strategy Version
      -> governed current-data revalidation PASS
      -> CANARY_RETEST at 20%

    This is a capital reduction / requalification path.
    It does not grandfather historical NORMAL status.
    It does not modify strategy logic or parameters.
    """

    tf = str(tf or "").upper()

    if not tf:
        raise RuntimeError(
            "REVALIDATION_TIMEFRAME_MISSING"
        )

    if not isinstance(evidence, dict):
        raise RuntimeError(
            "REVALIDATION_EVIDENCE_INVALID"
        )

    # Existing deployment safety interlocks remain authoritative.
    if PUSH_HOLD.exists() or REQUEST.exists():
        return None

    cv = governance.validate_constitution()

    if not cv.get("ok"):
        raise RuntimeError(
            "ROADMAP_CONSTITUTION_INVALID"
        )

    constitution_obj = governance.constitution()

    contract = (
        (
            constitution_obj.get(
                "research_architecture_v3"
            )
            or {}
        ).get(
            "strategy_research_qualification_operating_constitution"
        )
        or {}
    )

    normal_contract = (
        contract.get(
            "normal_revalidation"
        )
        or {}
    )

    first_revalidation = (
        contract.get(
            "first_formal_revalidation"
        )
        or {}
    )

    if normal_contract.get("required") is not True:
        raise RuntimeError(
            "NORMAL_REVALIDATION_CONTRACT_INVALID"
        )

    if (
        normal_contract.get(
            "past_normal_status_is_not_sufficient_current_normal_evidence"
        )
        is not True
    ):
        raise RuntimeError(
            "REVALIDATION_NORMAL_GRANDFATHERING_FORBIDDEN"
        )

    canary_contract = (
        (
            contract.get(
                "qualification"
            )
            or {}
        ).get(
            "canary"
        )
        or {}
    )

    try:
        canary_weight = float(
            canary_contract.get(
                "capital_weight"
            )
        )
    except Exception:
        raise RuntimeError(
            "REVALIDATION_CANARY_WEIGHT_INVALID"
        )

    if abs(canary_weight - 0.20) > 1e-12:
        raise RuntimeError(
            "REVALIDATION_CANARY_WEIGHT_NOT_TWENTY_PERCENT"
        )

    green_canary_weight = float(
        (
            (
                constitution_obj.get(
                    "strategy_capital_ranking_and_allocation"
                )
                or {}
            ).get(
                "scale_numeric_policy"
            )
            or {}
        ).get(
            "capital_tier_weights",
            {},
        ).get(
            "CANARY",
            -1,
        )
    )

    if abs(
        green_canary_weight
        - canary_weight
    ) > 1e-12:
        raise RuntimeError(
            "REVALIDATION_GREEN7A_CANARY_WEIGHT_MISMATCH"
        )

    cfg = load(CONFIG, {})

    if not isinstance(cfg, dict):
        raise RuntimeError(
            "REVALIDATION_ACTIVE_CONFIG_INVALID"
        )

    frames = cfg.get("frames") or {}

    current = frames.get(tf)

    if not isinstance(current, dict):
        raise RuntimeError(
            "REVALIDATION_ACTIVE_FRAME_MISSING"
        )

    if current.get("enabled") is not True:
        raise RuntimeError(
            "REVALIDATION_REQUIRES_ENABLED_NORMAL"
        )

    if str(
        current.get(
            "deployment_tier"
        )
        or ""
    ).upper() != "NORMAL":
        raise RuntimeError(
            "REVALIDATION_REQUIRES_CURRENT_NORMAL"
        )

    strategy_version = str(
        current.get(
            "strategy_version"
        )
        or ""
    )

    if not strategy_version.startswith("SV_"):
        raise RuntimeError(
            "REVALIDATION_STRATEGY_VERSION_MISSING"
        )

    strategies = [
        str(x).upper()
        for x in (
            current.get(
                "strategies"
            )
            or []
        )
        if str(x).strip()
    ]

    if not strategies:
        raise RuntimeError(
            "REVALIDATION_STRATEGIES_MISSING"
        )

    strategy = "+".join(
        strategies
    )

    # First formal revalidation is constitution-pinned.
    if tf == str(
        first_revalidation.get(
            "timeframe"
        )
        or ""
    ).upper():

        expected_version = str(
            first_revalidation.get(
                "strategy_version"
            )
            or ""
        )

        if strategy_version != expected_version:
            raise RuntimeError(
                "REVALIDATION_STRATEGY_VERSION_MISMATCH"
            )

        if (
            first_revalidation.get(
                "normal_status_grandfathering"
            )
            is not False
        ):
            raise RuntimeError(
                "REVALIDATION_NORMAL_GRANDFATHERING_FORBIDDEN"
            )

        if str(
            first_revalidation.get(
                "method"
            )
            or ""
        ) != (
            "SAME_CONDITIONS_CURRENT_DATA_RETEST_"
            "THEN_REQUALIFY_FROM_LOW_CAPITAL"
        ):
            raise RuntimeError(
                "REVALIDATION_METHOD_CONTRACT_INVALID"
            )

    # Evidence is fixed-condition evidence, not a new optimization.
    required_true = (
        "revalidation_pass",
        "same_conditions",
        "current_data_retest",
        "current_data_extension",
        "historical_cache_identical",
    )

    for key in required_true:
        if evidence.get(key) is not True:
            raise RuntimeError(
                "REVALIDATION_REQUIRED_TRUE_MISSING="
                + key
            )

    required_false = (
        "parameter_reoptimization",
        "strategy_logic_change",
        "strategy_version_change",
    )

    for key in required_false:
        if evidence.get(key) is not False:
            raise RuntimeError(
                "REVALIDATION_REQUIRED_FALSE_MISSING="
                + key
            )

    if str(
        evidence.get(
            "decision"
        )
        or ""
    ).upper() != "CANARY_RETEST":
        raise RuntimeError(
            "REVALIDATION_DECISION_NOT_CANARY_RETEST"
        )

    if str(
        evidence.get(
            "strategy_version"
        )
        or ""
    ) != strategy_version:
        raise RuntimeError(
            "REVALIDATION_STRATEGY_VERSION_MISMATCH"
        )

    result_json = str(
        evidence.get(
            "result_json"
        )
        or ""
    )

    result_sha = str(
        evidence.get(
            "result_sha256"
        )
        or ""
    ).lower()

    if not result_json:
        raise RuntimeError(
            "REVALIDATION_RESULT_JSON_MISSING"
        )

    if (
        len(result_sha) != 64
        or any(
            c not in "0123456789abcdef"
            for c in result_sha
        )
    ):
        raise RuntimeError(
            "REVALIDATION_RESULT_SHA256_INVALID"
        )

    # Preserve exact current NORMAL config as historical evidence.
    previous = json.loads(
        json.dumps(
            current
        )
    )

    new = json.loads(
        json.dumps(
            cfg
        )
    )

    pending = (
        new.setdefault(
            "frames",
            {},
        ).setdefault(
            tf,
            {},
        )
    )

    # Same exact strategy/version. Capital only is reduced.
    pending["enabled"] = True
    pending["status"] = "PROVISIONAL_MAIN"
    pending["strategies"] = list(strategies)
    pending["strategy_version"] = strategy_version
    pending["deployment_tier"] = "CANARY"
    pending["capital_weight"] = canary_weight

    new["version"] = (
        "V3_REVALIDATION_"
        + tf
        + "_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    new[
        "roadmap_constitution"
    ] = cv.get(
        "constitution_version"
    )

    # A failed revalidation Canary must never restore full NORMAL exposure.
    rollback_frame = json.loads(
        json.dumps(
            previous
        )
    )

    rollback_frame["enabled"] = False
    rollback_frame["status"] = "REVALIDATION_FAILED_OFF"
    rollback_frame["deployment_tier"] = "OFF"
    rollback_frame["capital_weight"] = 0.0

    cfgp = (
        RES
        / (
            # REVALIDATION_CANARY_PENDING_CONFIG_CANONICAL_V1
            "strategy_config_pending_"
            + tf.lower()
            + ".json"
        )
    )

    rid = (
        "V3_"
        + tf
        + "_REVALIDATION_CANARY_RETEST_"
        + str(
            int(time.time())
        )
    )

    event_id = (
        "REVALIDATION_CANARY_RETEST:"
        + tf
        + ":"
        + strategy_version
        + ":"
        + result_sha
    )

    req = {
        "schema":
            "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",

        "request_id":
            rid,

        "created_at":
            now(),

        "reason":
            "NORMAL_REVALIDATION_CANARY_RETEST",

        "version_kind":
            "STRATEGY",

        "timeframe":
            tf,

        "strategy":
            strategy,

        "strategies":
            list(strategies),

        "strategy_version":
            strategy_version,

        "policy_versions":
            new.get(
                "policy_versions"
            )
            or {},

        "tier":
            "CANARY",

        "canary_capital_weight":
            canary_weight,

        # Same strategy artifact: no new deployment source is introduced.
        "strategy_file":
            "",

        "strategy_config_file":
            str(
                cfgp.relative_to(
                    ROOT
                )
            ),

        "previous_frame_config":
            previous,

        "real_evidence":
            evidence,

        "constitution_version":
            cv.get(
                "constitution_version"
            ),

        "constitution_hash":
            cv.get(
                "constitution_hash"
            ),

        "autonomous_rule_passed":
            True,

        "core_mutation":
            False,

        "revalidation_canary_retest":
            True,

        "revalidation_event_id":
            event_id,

        "rollback_target":
            "REVALIDATION_FAILED_OFF",
    }

    # Record the revalidation decision append-only first.
    governance.record_strategy_career_event(
        strategy_version,
        "REVALIDATION",
        {
            "timeframe":
                tf,

            "decision":
                "CANARY_RETEST",

            "target_tier":
                "CANARY",

            "capital_weight":
                canary_weight,

            "same_conditions":
                True,

            "parameter_reoptimization":
                False,

            "strategy_logic_change":
                False,

            "strategy_version_change":
                False,

            "result_json":
                result_json,

            "result_sha256":
                result_sha,

            "evidence":
                evidence,
        },
        event_id=event_id,
    )

    # Official staged deployment artifacts.
    atomic(
        cfgp,
        new,
    )

    atomic(
        REQUEST,
        req,
    )

    s = state()

    s.setdefault(
        "frames",
        {},
    )[tf] = {
        "strategy":
            strategy,

        "strategies":
            list(strategies),

        "strategy_version":
            strategy_version,

        "tier":
            "PENDING_CANARY",

        "request_id":
            rid,

        "requested_at":
            now(),

        "previous_frame_config":
            previous,

        "last_evidence":
            evidence,

        "revalidation_canary_retest":
            True,

        "revalidation_event_id":
            event_id,

        "revalidation_rollback_frame":
            rollback_frame,

        "target_capital_weight":
            canary_weight,
    }

    s["updated_at"] = now()

    atomic(
        STATE,
        s,
    )

    governance.update_strategy_passport_summary(
        strategy_version,
        {
            "current_qualification":
                "CANARY_RETEST_PENDING_SYNC",

            "target_qualification":
                "CANARY",

            "deployment_tier":
                "PENDING_CANARY",

            "capital_weight":
                canary_weight,

            "revalidation_required":
                True,

            "revalidation_status":
                "PASS_CANARY_RETEST_PENDING_SYNC",

            "revalidation_result_json":
                result_json,

            "revalidation_result_sha256":
                result_sha,
        },
    )

    return {
        "request_id":
            rid,

        "timeframe":
            tf,

        "strategy":
            strategy,

        "strategies":
            list(strategies),

        "strategy_version":
            strategy_version,

        "tier":
            "PENDING_CANARY",

        "target_tier":
            "CANARY",

        "capital_weight":
            canary_weight,

        "revalidation_canary_retest":
            True,

        "request":
            req,
    }


def _adaptive_growth_account_snapshot():
    """Read-only account mode snapshot for initial Strategy Canary sizing."""
    paths = [
        ROOT / "state" / "account_mode.json",
        ROOT / "data" / "server_runtime" / "state" / "account_mode.json",
    ]
    for path in paths:
        if not path.is_file():
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        mode = str(row.get("mode") or row.get("current_mode") or "").upper()
        if mode not in {"GROWTH", "BALANCED", "DEFENSIVE"}:
            continue
        try:
            equity = float(row.get("avg_equity_usdt_24h"))
        except Exception:
            equity = None
        return {
            "account_mode": mode,
            "account_avg_equity_usdt_24h": equity,
            "source": str(path),
        }
    return {
        "account_mode": "",
        "account_avg_equity_usdt_24h": None,
        "source": "MISSING_FAIL_CLOSED",
    }


def _adaptive_initial_canary_boost():
    snapshot = _adaptive_growth_account_snapshot()
    mode = str(snapshot.get("account_mode") or "").upper()
    try:
        equity = float(snapshot.get("account_avg_equity_usdt_24h"))
    except Exception:
        equity = None
    multiplier = 1.0
    reason = "OFF"
    if mode == "GROWTH" and equity is not None and equity < 70000.0:
        multiplier = 1.80
        reason = "SUB_100M_GROWTH_DEFAULT_CANARY"
    return {
        **snapshot,
        "multiplier": multiplier,
        "hard_max": 2.20,
        "reason": reason,
        "exchange_leverage_changed": False,
    }



def _frame_absolute_leverage_evaluation(evidence, allocation=None):
    """Frame-level absolute leverage evaluation; never compares frames."""
    evidence = evidence if isinstance(evidence, dict) else {}
    allocation = allocation if isinstance(allocation, dict) else {}

    def num(value):
        try:
            x = float(value)
            if x == x and abs(x) != float("inf"):
                return x
        except Exception:
            pass
        return None

    def row(value):
        return value if isinstance(value, dict) else {}

    def first_num(*values):
        for value in values:
            x = num(value)
            if x is not None:
                return x
        return None

    # ---------------------------------------------------------
    # Evidence containers
    # ---------------------------------------------------------

    nested_evidence = row(evidence.get("evidence"))
    metrics = row(evidence.get("metrics"))
    evaluation = row(evidence.get("evaluation"))

    profile = row(evidence.get("live_strategy_profile"))
    if not profile:
        profile = row(evidence.get("strategy_instance_live_profile"))
    if not profile:
        profile = row(nested_evidence.get("live_strategy_profile"))
    if not profile:
        profile = row(metrics.get("live_strategy_profile"))
    if not profile:
        profile = row(evaluation.get("live_strategy_profile"))

    gate = row(evidence.get("final_risk_gate"))
    if not gate:
        gate = row(nested_evidence.get("final_risk_gate"))
    if not gate:
        gate = row(metrics.get("final_risk_gate"))
    if not gate:
        gate = row(evaluation.get("final_risk_gate"))

    ratios = row(gate.get("ratios"))

    final = row(gate.get("final"))
    baseline = row(gate.get("baseline"))

    final_exit = row(final.get("exit"))
    baseline_exit = row(baseline.get("exit"))

    final_entry = row(final.get("entry_risk"))
    baseline_entry = row(baseline.get("entry_risk"))

    # ---------------------------------------------------------
    # Actual live profile
    # ---------------------------------------------------------

    closed_trades = first_num(
        profile.get("closed_complete_live_trades"),
    )

    live_mdd = first_num(profile.get("mdd_roi"))
    live_dd_days = first_num(profile.get("dd_duration_days"))
    live_cvar = first_num(profile.get("cvar95_loss_roi"))
    live_streak = first_num(profile.get("max_consecutive_losses"))
    live_holding_hours = first_num(profile.get("avg_holding_time_hours"))

    has_real_live = bool(
        closed_trades is not None
        and closed_trades > 0
    )

    # ---------------------------------------------------------
    # Existing research ratios are preferred.
    # Missing non-zero thresholds are never fabricated.
    # Zero observed loss values are unit-invariant evidence.
    # ---------------------------------------------------------

    mdd = first_num(ratios.get("mdd"))
    dd = first_num(ratios.get("dd_duration"))
    cvar = first_num(ratios.get("cvar95"))
    holding = first_num(ratios.get("holding"))

    # If research ratio is absent, zero live loss is still
    # unambiguously zero regardless of percentage/ROI units.
    if mdd is None and has_real_live and live_mdd == 0.0:
        mdd = 0.0

    if dd is None and has_real_live and live_dd_days == 0.0:
        dd = 0.0

    if cvar is None and has_real_live and live_cvar == 0.0:
        cvar = 0.0

    # Holding is non-zero in real profiles, so do not invent
    # a ratio unless a same-purpose baseline exists.
    if holding is None and has_real_live and live_holding_hours is not None:
        baseline_holding = first_num(
            baseline_exit.get("avg_holding_time_hours"),
            baseline.get("avg_holding_time_hours"),
        )
        if baseline_holding is not None and baseline_holding > 0:
            holding = live_holding_hours / baseline_holding

    # DD duration: live profile stores days while research may
    # store hours. Convert only when a research baseline exists.
    if dd is None and has_real_live and live_dd_days is not None:
        baseline_dd_hours = first_num(
            baseline_exit.get("dd_duration_hours"),
            baseline.get("dd_duration_hours"),
        )
        if baseline_dd_hours is not None and baseline_dd_hours > 0:
            dd = (live_dd_days * 24.0) / baseline_dd_hours

    # ---------------------------------------------------------
    # Consecutive losses
    # ---------------------------------------------------------

    streak_add = gate.get("max_consecutive_losses_add")

    try:
        streak_add = int(streak_add)
    except Exception:
        streak_add = None

    if streak_add is None:
        final_streak = first_num(
            final.get("max_consecutive_losses"),
            final_entry.get("max_consecutive_losses"),
        )
        baseline_streak = first_num(
            baseline.get("max_consecutive_losses"),
            baseline_entry.get("max_consecutive_losses"),
        )

        if final_streak is not None and baseline_streak is not None:
            streak_add = int(final_streak) - int(baseline_streak)

    # If no research delta exists but actual live evidence exists,
    # use the actual observed streak conservatively as the burden.
    if streak_add is None and has_real_live and live_streak is not None:
        streak_add = int(live_streak)

    # ---------------------------------------------------------
    # Long/short collapse: fail closed when evidence is missing.
    # ---------------------------------------------------------

    long_row = row(final_entry.get("long"))
    short_row = row(final_entry.get("short"))

    long_net = first_num(long_row.get("avg_net_pct"))
    short_net = first_num(short_row.get("avg_net_pct"))

    long_short_evidence = bool(
        long_row
        and short_row
        and long_net is not None
        and short_net is not None
    )

    long_short_ok = bool(
        long_short_evidence
        and not (long_net <= 0.0 and short_net <= 0.0)
        and not (
            long_net < 0.0 < short_net
            and abs(long_net) >= abs(short_net)
        )
        and not (
            short_net < 0.0 < long_net
            and abs(short_net) >= abs(long_net)
        )
    )

    # ---------------------------------------------------------
    # WF / OOS / Cost Stress
    # ---------------------------------------------------------

    wf = first_num(
        evidence.get("walk_forward_oos_expectancy_pct"),
        evidence.get("wf_oos_expectancy"),
        nested_evidence.get("walk_forward_oos_expectancy_pct"),
        nested_evidence.get("wf_oos_expectancy"),
        metrics.get("walk_forward_oos_expectancy_pct"),
        metrics.get("wf_oos_expectancy"),
        evaluation.get("walk_forward_oos_expectancy_pct"),
        evaluation.get("wf_oos_expectancy"),
    )

    stress = first_num(
        evidence.get("cost_stress_2x_expectancy_pct"),
        evidence.get("cost_stress_2x_expectancy"),
        nested_evidence.get("cost_stress_2x_expectancy_pct"),
        nested_evidence.get("cost_stress_2x_expectancy"),
        metrics.get("cost_stress_2x_expectancy_pct"),
        metrics.get("cost_stress_2x_expectancy"),
        evaluation.get("cost_stress_2x_expectancy_pct"),
        evaluation.get("cost_stress_2x_expectancy"),
    )

    walk_forward = row(evidence.get("walk_forward"))
    if not walk_forward:
        walk_forward = row(nested_evidence.get("walk_forward"))
    if not walk_forward:
        walk_forward = row(metrics.get("walk_forward"))
    if not walk_forward:
        walk_forward = row(evaluation.get("walk_forward"))

    cost_stress = row(evidence.get("cost_stress_2x"))
    if not cost_stress:
        cost_stress = row(nested_evidence.get("cost_stress_2x"))
    if not cost_stress:
        cost_stress = row(metrics.get("cost_stress_2x"))
    if not cost_stress:
        cost_stress = row(evaluation.get("cost_stress_2x"))

    if wf is None:
        wf = first_num(walk_forward.get("avg_net_pct"))

    if stress is None:
        stress = first_num(cost_stress.get("avg_net_pct"))

    # ---------------------------------------------------------
    # Existing portfolio/margin/concentration safety.
    # Missing evidence always fails closed.
    # ---------------------------------------------------------

    required_safety = (
        "entry_risk_safe_size_pass",
        "account_risk_budget_pass",
        "portfolio_total_exposure_pass",
        "available_margin_pass",
        "margin_safety_reserve_pass",
        "symbol_concentration_pass",
        "strategy_concentration_pass",
        "correlation_concentration_pass",
        "cost_liquidity_scale_pass",
    )

    failed_constraints = allocation.get("failed_constraints")

    allocation_gate_ok = bool(
        allocation.get("ok") is True
        and isinstance(failed_constraints, list)
        and len(failed_constraints) == 0
    )

    hard_safety_ok = bool(
        allocation_gate_ok
        and all(allocation.get(k) is True for k in required_safety)
        and gate.get("pass") is True
    )

    # ---------------------------------------------------------
    # Five absolute axes.
    # Current R5 semantics: each passed axis = 3 points.
    # ---------------------------------------------------------

    axes = {
        "loss_scale": bool(
            has_real_live
            and mdd is not None
            and mdd <= 1.75
        ),

        "recovery_burden": bool(
            has_real_live
            and dd is not None
            and dd <= 1.75
            and holding is not None
            and holding <= 1.75
        ),

        "loss_quality": bool(
            has_real_live
            and cvar is not None
            and cvar <= 1.60
            and streak_add is not None
            and streak_add <= 5
            and long_short_ok
        ),

        "validation_robustness": bool(
            wf is not None
            and wf > 0.0
            and stress is not None
            and stress > 0.0
            and gate.get("pass") is True
        ),

        "live_safety": bool(
            hard_safety_ok
        ),
    }

    axis_scores = {
        name: (3 if passed else 0)
        for name, passed in axes.items()
    }

    passed_axis_count = sum(
        1 for passed in axes.values() if passed
    )

    total_score = sum(axis_scores.values())
    additional = min(15, max(0, total_score))
    final_leverage = min(25, 10 + additional)

    return {
        "schema": "FRAME_ABSOLUTE_LEVERAGE_EVALUATION_V1",
        "mode": "ABSOLUTE_PER_TIMEFRAME",

        "base_leverage_x": 10,
        "additional_leverage_x": additional,
        "final_leverage_x": final_leverage,

        "max_additional_leverage_x": 15,
        "absolute_max_leverage_x": 25,

        "axis_count": 5,
        "passed_axis_count": passed_axis_count,
        "axis_scores": axis_scores,
        "total_score": total_score,

        "axes": axes,

        "inputs": {
            "closed_complete_live_trades": closed_trades,
            "mdd_roi_live": live_mdd,
            "dd_duration_days_live": live_dd_days,
            "cvar95_loss_roi_live": live_cvar,
            "max_consecutive_losses_live": live_streak,
            "avg_holding_time_hours_live": live_holding_hours,

            "mdd_ratio": mdd,
            "dd_duration_ratio": dd,
            "holding_exposure_ratio": holding,
            "cvar95_ratio": cvar,
            "max_consecutive_losses_add": streak_add,

            "long_net_pct": long_net,
            "short_net_pct": short_net,
            "long_short_evidence": long_short_evidence,

            "wf_oos_expectancy_pct": wf,
            "cost_stress_2x_expectancy_pct": stress,

            "final_risk_gate_pass": gate.get("pass"),
            "allocation_gate_ok": allocation_gate_ok,
            "hard_safety_ok": hard_safety_ok,
        },
    }


def create_provisional_request(tf,row):
    tf = tf.upper()

    cv = (
        governance
        .validate_constitution()
    )

    if not cv.get("ok"):
        raise RuntimeError(
            "ROADMAP_CONSTITUTION_INVALID"
        )

    arch = (
        research_architecture_v3()
    )

    s = state()

    fr = (
        s.setdefault(
            "frames",
            {},
        ).get(tf)
        or {}
    )

    name = str(
        row.get("strategy")
        or ""
    ).upper()

    if not name:
        raise RuntimeError(
            "PROVISIONAL_STRATEGY_MISSING"
        )

    pipeline_local_shadow = _pipeline_local_shadow_row(row)

    # Idempotence only.
    if (
        str(
            fr.get("strategy")
            or ""
        ).upper()
        == name
        and
        str(
            fr.get("tier")
            or ""
        ).upper()
        in {
            "PENDING_SHADOW",
            "LOCAL_PROVISIONAL_READY",
            "PENDING_CANARY_SYNC",
            "CANARY",
            "SMALL",
            "NORMAL",
        }
    ):
        return None

    # FULL_VALIDATION + SEALED FINAL BLIND boundary.
    # NEW_MAIN uses the new exact-version pipeline gates; legacy rows keep
    # their historical compatibility path.
    if pipeline_local_shadow:
        if (row.get("full_validation_gate") or {}).get("pass") is not True:
            raise RuntimeError("PROVISIONAL_REQUIRES_NEW_FULL_VALIDATION_PASS")
        blind_gate=row.get("sealed_final_blind_gate") or {}
        if blind_gate.get("pass") is not True or str(blind_gate.get("next_stage") or "")!="LOCAL_SHADOW_PENDING":
            raise RuntimeError("PROVISIONAL_REQUIRES_SEALED_FINAL_BLIND_PASS")
    else:
        if not full_validation_pass(row):
            raise RuntimeError("PROVISIONAL_REQUIRES_FULL_VALIDATION_PASS")
        if str(row.get("confidence_level") or "").upper() != "SEALED_BLIND_PASS":
            raise RuntimeError("PROVISIONAL_REQUIRES_SEALED_BLIND_PASS")
        blind=(row.get("sealed_blind") or row.get("sealed_blind_single") or row.get("sealed_blind_combo") or {})
        if not isinstance(blind,dict) or blind.get("pass") is not True:
            raise RuntimeError("PROVISIONAL_SEALED_BLIND_EVIDENCE_MISSING")

    srcfile = (
        source_for_pipeline(row)
        if pipeline_local_shadow
        else (source_for_single(row) if row.get("family_file") else source_for_combo(row))
    )

    if srcfile is None:
        raise RuntimeError(
            "DEPLOY_SOURCE_MISSING"
        )

    cfg = load(
        CONFIG,
        {},
    )

    if not isinstance(
        cfg,
        dict,
    ):
        raise RuntimeError(
            "STRATEGY_CONFIG_INVALID"
        )

    # CANONICAL_STRATEGY_VERSION_REUSE_V1
    _canonical_version_id = str(
        row.get("strategy_version_id")
        or row.get("exact_strategy_version")
        or ""
    ).strip()

    _research_strategy_version = (
        row.get("research_strategy_version")
        or row.get("legacy_research_strategy_version")
        or row.get("strategy_version")
    )

    if _canonical_version_id:

        _registry = (
            governance.version_registry()
        )

        version = dict(
            (
                (
                    _registry.get(
                        "strategy_versions"
                    )
                    or {}
                ).get(
                    _canonical_version_id
                )
            )
            or {}
        )

        if pipeline_local_shadow:
            # 등록부 작성은 기존 governance 책임 함수를 통해서만 수행한다.
            version = governance.register_version_candidate(
                "STRATEGY", "STRATEGY",
                {"strategy": name, "timeframe": tf,
                 "source_file": str(srcfile.relative_to(ROOT)).replace(chr(92), "/"),
                 "source_sha256": str(row.get("source_sha256") or "").lower(),
                 "research_strategy_version": _research_strategy_version},
                row, exact_strategy_version=_canonical_version_id,
            )
        if not version:
            raise RuntimeError(
                "CANONICAL_STRATEGY_VERSION_NOT_REGISTERED:"
                + _canonical_version_id
            )

        version["version_id"] = (
            version.get("version_id")
            or _canonical_version_id
        )

        _payload = (
            version.get("payload")
            or {}
        )

        if str(
            _payload.get("strategy")
            or ""
        ).upper() != name:
            raise RuntimeError(
                "CANONICAL_STRATEGY_ID_BINDING_MISMATCH"
            )

        if str(
            _payload.get("timeframe")
            or ""
        ).upper() != str(tf).upper():
            raise RuntimeError(
                "CANONICAL_STRATEGY_TF_BINDING_MISMATCH"
            )

    else:

        version = (
            governance
            .register_version_candidate(
                "STRATEGY",
                "STRATEGY",
                {
                    "timeframe":
                        tf,

                    "strategy":
                        name,

                    "source_file":
                        str(
                            srcfile.relative_to(
                                ROOT
                            )
                        ),

                    "research_strategy_version":
                        _research_strategy_version,
                },
                row,
            )
        )

    governance.set_version_status(
        version["version_id"],
        "VALIDATED",
        {
            "reason":
                "FULL_VALIDATION_AND_SEALED_FINAL_BLIND_PASS",

            "timeframe":
                tf,

            "promotion_next":
                "LOCAL_SHADOW_RUNTIME_GATE",
        },
    )

    # ========================================================
    # R4 Strategy Instance 불변 등록 / LOCAL_TEST -> LOCAL_SHADOW
    # ========================================================
    behavior_policy = _pipeline_behavior_policy(
        cfg,
        row,
    )

    policy_bundle = (
        governance
        .strategy_policy_bundle(
            cfg.get("policy_versions") or {},
            behavior_policy,
        )
    )

    strategy_instance = (
        governance
        .register_strategy_instance(
            name,
            tf,
            version["version_id"],
            policy_bundle["version"],
            policy_bundle["snapshot"],
            source_file=str(
                srcfile.relative_to(ROOT)
            ).replace("\\", "/"),
            evidence={
                "source":
                    "V3_DEPLOYMENT_CREATE_PROVISIONAL",

                "full_validation_pass":
                    True,

                "sealed_blind_pass":
                    True,

                "effective_gate":
                    "LOCAL_SHADOW",

                "individual_trailing":
                    json.loads(json.dumps(row.get("individual_trailing") or {})),

                "individual_reentry":
                    json.loads(json.dumps(row.get("individual_reentry") or {})),

                "at":
                    now(),
            },
            status="LOCAL_TEST",
        )
    )

    strategy_instance_id = str(
        strategy_instance.get(
            "strategy_instance_id"
        )
        or ""
    )

    strategy_instance_status = str(
        strategy_instance.get("status")
        or ""
    ).upper()

    if strategy_instance_status == "LOCAL_TEST":
        strategy_instance = (
            governance
            .set_strategy_instance_status(
                strategy_instance_id,
                "LOCAL_SHADOW",
                {
                    "reason":
                        "LOCAL_SHADOW_COMPATIBILITY_TRANSITION",

                    "timeframe":
                        tf,

                    "strategy":
                        name,

                    "at":
                        now(),
                },
            )
        )

    if str(
        strategy_instance.get("status")
        or ""
    ).upper() != "LOCAL_SHADOW":
        raise RuntimeError(
            "STRATEGY_INSTANCE_LOCAL_SHADOW_REQUIRED"
        )

    new = json.loads(
        json.dumps(cfg)
    )

    new.setdefault(
        "strategy_registry",
        {},
    )[name] = strategy_registry_row(
        name,
        srcfile.name,
    )

    frame = (
        new.setdefault(
            "frames",
            {},
        ).setdefault(
            tf,
            {},
        )
    )

    previous = json.loads(
        json.dumps(frame)
    )

    try:
        canary_weight = float(
            (
                arch.get("canary")
                or {}
            ).get(
                "initial_capital_weight"
            )
        )
    except Exception:
        raise RuntimeError(
            "CANARY_WEIGHT_INVALID"
        )

    if abs(
        canary_weight - 0.20
    ) > 1e-12:
        raise RuntimeError(
            "CANARY_WEIGHT_NOT_TWENTY_PERCENT"
        )

    canary_base_weight = canary_weight
    canary_boost = _adaptive_initial_canary_boost()
    canary_boost_multiplier = float(canary_boost.get("multiplier") or 1.0)
    if canary_boost_multiplier < 1.0 or canary_boost_multiplier > 2.20:
        raise RuntimeError("ADAPTIVE_CANARY_BOOST_OUT_OF_BOUNDS")
    canary_weight = canary_base_weight * canary_boost_multiplier

    frame.update(
        {
            "enabled":
                True,

            "status":
                "PROVISIONAL_MAIN",

            "strategies":
                [name],

            "strategy_version":
                version["version_id"],

            "leverage":
                int(
                    (
                        (
                            new.get(
                                "deployment"
                            )
                            or {}
                        ).get(
                            "provisional_leverage",
                            1,
                        )
                    )
                    or 1
                ),

            "deployment_tier":
                "CANARY",

            "capital_weight":
                canary_weight,

            "capital_base_weight":
                canary_base_weight,

            "adaptive_capital_boost_multiplier":
                canary_boost_multiplier,

            "adaptive_capital_boost_policy":
                "SUB_100M_AGGRESSIVE_GROWTH_ADAPTIVE_CAPITAL_BOOST_V1",

            "legacy_manage_existing":
                bool(
                    frame.get(
                        "legacy_manage_existing",
                        False,
                    )
                ),

            "state_reentry_allowed":
                True,
        }
    )

    new["version"] = (
        "V3_AUTO_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    new[
        "roadmap_constitution"
    ] = cv.get(
        "constitution_version"
    )

    cfgp = (
        RES
        / (
            "strategy_config_pending_"
            f"{tf.lower()}.json"
        )
    )

    atomic(
        cfgp,
        new,
    )

    rid = (
        f"V3_{tf}_"
        f"{name}_"
        f"{int(time.time())}"
    )

    req = {
        "schema":
            "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",

        "request_id":
            rid,

        "created_at":
            now(),

        "reason":
            "PROVISIONAL_MAIN_CANARY_AFTER_LOCAL_SHADOW",

        "version_kind":
            "STRATEGY",

        "timeframe":
            tf,

        "strategy":
            name,

        "strategy_version":
            version["version_id"],

        "strategy_instance_id":
            strategy_instance_id,

        "strategy_policy_bundle_version":
            policy_bundle["version"],

        "strategy_policy_snapshot":
            policy_bundle["snapshot"],

        "policy_versions":
            new.get(
                "policy_versions"
            )
            or {},

        "tier":
            "CANARY",

        "canary_capital_weight":
            canary_weight,

        "canary_base_capital_weight":
            canary_base_weight,

        "adaptive_capital_boost_multiplier":
            canary_boost_multiplier,

        "adaptive_capital_boost":
            canary_boost,

        "strategy_file":
            str(
                srcfile.relative_to(
                    ROOT
                )
            ),

        "strategy_config_file":
            str(
                cfgp.relative_to(
                    ROOT
                )
            ),

        "previous_frame_config":
            previous,

        "research_evidence":
            row,

        "constitution_version":
            cv.get(
                "constitution_version"
            ),

        "constitution_hash":
            cv.get(
                "constitution_hash"
            ),

        "research_architecture_policy_id":
            arch.get(
                "policy_id"
            ),

        "required_pre_runtime_gate":
            "MINIMAL_SHADOW_RUNTIME_SAFETY",

        "effective_pre_runtime_gate":
            "LOCAL_SHADOW_RUNTIME_SAFETY",

        "pipeline_contract":
            str(row.get("pipeline_contract") or "LEGACY_COMPAT"),

        "individual_trailing_binding":
            json.loads(json.dumps(row.get("individual_trailing") or {})),

        "individual_reentry_binding":
            json.loads(json.dumps(row.get("individual_reentry") or {})),

        "autonomous_rule_passed":
            True,

        "approved_for_server_release":
            False,

        "local_qualification_tier":
            "LOCAL_SHADOW",

        "core_mutation":
            False,
    }

    # Runtime sync is NOT emitted here.

    pending_row = {
        "strategy":
            name,

        "strategy_version":
            version["version_id"],

        "strategy_instance_id":
            strategy_instance_id,

        "strategy_policy_bundle_version":
            policy_bundle["version"],

        "tier":
            "PENDING_SHADOW",

        "shadow_status":
            "PENDING",

        "request_id":
            "",

        "requested_at":
            now(),

        "shadow_requested_at":
            now(),

        "previous_frame_config":
            previous,

        "source_row":
            row,

        "pending_request_template":
            req,

        "shadow_evidence_file":
            str(
                (
                    RES
                    / "validation"
                    / version[
                        "version_id"
                    ]
                    / (
                        "shadow__"
                        + strategy_instance_id
                        + ".json"
                    )
                ).relative_to(
                    ROOT
                )
            ),
    }

    active_frame_state = (
        ((s.get("frames") or {}).get(tf))
        or {}
    )

    active_frame_tier = str(
        active_frame_state.get("tier")
        or active_frame_state.get("qualification_status")
        or ""
    ).upper()

    if active_frame_tier not in {
        "CANARY",
        "SMALL",
        "NORMAL",
    }:
        s.setdefault(
            "frames",
            {},
        )[tf] = json.loads(
            json.dumps(
                pending_row
            )
        )

    s.setdefault(
        "strategy_instances",
        {},
    )[strategy_instance_id] = {
        "strategy_instance_id":
            strategy_instance_id,

        "strategy":
            name,

        "timeframe":
            tf,

        "strategy_version":
            version["version_id"],

        "strategy_policy_bundle_version":
            policy_bundle["version"],

        "tier":
            "PENDING_SHADOW",

        "qualification_status":
            "LOCAL_SHADOW",

        "shadow_status":
            "PENDING",

        "requested_at":
            now(),
    }

    s.setdefault(
        "strategy_instances",
        {},
    )[strategy_instance_id].update(
        json.loads(
            json.dumps(
                pending_row
            )
        )
    )

    s["updated_at"] = now()

    atomic(
        STATE,
        s,
    )

    return {
        "local_only":
            True,

        "held":
            PUSH_HOLD.exists(),

        "shadow_required":
            True,

        "tier":
            "PENDING_SHADOW",

        "strategy":
            name,

        "strategy_version":
            version["version_id"],

        "strategy_instance_id":
            strategy_instance_id,

        "strategy_policy_bundle_version":
            policy_bundle["version"],

        "request_template":
            req,
    }



def materialize_local_ready_request():
    """
    Strategy:
      PENDING_SHADOW
      -> Shadow PASS
      -> PENDING_CANARY_SYNC

    Policy section below remains unchanged.
    """

    if (
        PUSH_HOLD.exists()
        or REQUEST.exists()
    ):
        return None

    s = state()

    changed = False

    pending_strategy_rows = []
    seen_strategy_instances = set()

    for tf, fr in (
        s.get("frames")
        or {}
    ).items():
        if not isinstance(fr, dict):
            continue

        sid = str(
            fr.get("strategy_instance_id")
            or ""
        ).strip()

        pending_strategy_rows.append(
            (
                str(tf).upper(),
                fr,
            )
        )

        if sid:
            seen_strategy_instances.add(sid)

    for sid, row in (
        s.get("strategy_instances")
        or {}
    ).items():
        if not isinstance(row, dict):
            continue

        sid = str(
            row.get("strategy_instance_id")
            or sid
            or ""
        ).strip()

        if sid and sid in seen_strategy_instances:
            continue

        tier = str(
            row.get("tier")
            or ""
        ).upper()

        if tier not in {
            "PENDING_SHADOW",
            "LOCAL_PROVISIONAL_READY",
        }:
            continue

        tf = str(
            row.get("timeframe")
            or ""
        ).upper()

        if not tf:
            continue

        pending_strategy_rows.append(
            (
                tf,
                row,
            )
        )

        if sid:
            seen_strategy_instances.add(sid)

    for tf, fr in pending_strategy_rows:

        tier = str(
            fr.get("tier")
            or ""
        ).upper()

        if tier not in {
            "PENDING_SHADOW",
            "LOCAL_PROVISIONAL_READY",
        }:
            continue

        version_id = str(
            fr.get(
                "strategy_version"
            )
            or ""
        )

        if not version_id:

            fr["tier"] = (
                "PENDING_SHADOW"
            )

            fr[
                "shadow_status"
            ] = "BLOCKED"

            fr[
                "shadow_block_reason"
            ] = (
                "PROVISIONAL_REQUIRES_MINIMAL_SHADOW_PASS:"
                "STRATEGY_VERSION_MISSING"
            )

            changed = True
            continue

        strategy_instance_id = str(
            fr.get(
                "strategy_instance_id"
            )
            or ""
        )

        if not strategy_instance_id:
            fr["tier"] = "PENDING_SHADOW"
            fr["shadow_status"] = "BLOCKED"
            fr["shadow_block_reason"] = (
                "PROVISIONAL_REQUIRES_EXACT_STRATEGY_INSTANCE_ID"
            )
            changed = True
            continue

        try:
            strategy_instance = (
                governance
                .strategy_instance(
                    strategy_instance_id
                )
            )
        except Exception:
            fr["tier"] = "PENDING_SHADOW"
            fr["shadow_status"] = "BLOCKED"
            fr["shadow_block_reason"] = (
                "STRATEGY_INSTANCE_REGISTRY_BINDING_MISSING"
            )
            changed = True
            continue

        if str(
            strategy_instance.get(
                "exact_strategy_version"
            )
            or ""
        ) != version_id:
            raise RuntimeError(
                "STRATEGY_INSTANCE_VERSION_BINDING_MISMATCH"
            )

        if str(
            strategy_instance.get("status")
            or ""
        ).upper() != "LOCAL_SHADOW":
            raise RuntimeError(
                "STRATEGY_INSTANCE_NOT_LOCAL_SHADOW"
            )

        if not minimal_shadow_pass(
            version_id,
            strategy_instance_id,
        ):

            fr["tier"] = (
                "PENDING_SHADOW"
            )

            fr[
                "shadow_status"
            ] = "PENDING"

            fr[
                "shadow_block_reason"
            ] = (
                "PROVISIONAL_REQUIRES_MINIMAL_SHADOW_PASS"
            )

            changed = True
            continue

        req = (
            fr.get(
                "pending_request_template"
            )
            or {}
        )

        if (
            not isinstance(
                req,
                dict,
            )
            or not req
        ):

            fr[
                "shadow_status"
            ] = "PASS"

            fr[
                "shadow_block_reason"
            ] = (
                "CANARY_REQUEST_TEMPLATE_MISSING"
            )

            changed = True
            continue

        arch = (
            research_architecture_v3()
        )

        try:
            canary_weight = float(
                (
                    arch.get("canary")
                    or {}
                ).get(
                    "initial_capital_weight"
                )
            )
        except Exception:
            raise RuntimeError(
                "CANARY_WEIGHT_INVALID"
            )

        if abs(
            canary_weight - 0.20
        ) > 1e-12:
            raise RuntimeError(
                "CANARY_WEIGHT_NOT_TWENTY_PERCENT"
            )

        canary_base_weight = canary_weight
        canary_boost = _adaptive_initial_canary_boost()
        canary_boost_multiplier = float(canary_boost.get("multiplier") or 1.0)
        if canary_boost_multiplier < 1.0 or canary_boost_multiplier > 2.20:
            raise RuntimeError("ADAPTIVE_CANARY_BOOST_OUT_OF_BOUNDS")
        canary_weight = canary_base_weight * canary_boost_multiplier

        cfg_rel = str(
            req.get(
                "strategy_config_file"
            )
            or ""
        )

        if not cfg_rel:
            raise RuntimeError(
                "CANARY_CONFIG_FILE_MISSING"
            )

        cfgp = (
            ROOT
            / cfg_rel
        )

        pending_cfg = load(
            cfgp,
            {},
        )

        if not isinstance(
            pending_cfg,
            dict,
        ):
            raise RuntimeError(
                "CANARY_PENDING_CONFIG_INVALID"
            )

        # MULTI_CANARY_FRAME_IS_INDEX_NOT_OWNER_V1
        #
        # A live timeframe frame is only the legacy/representative index.
        # A new peer Strategy Instance must not replace that representative.
        active_state_frame = (
            ((s.get("frames") or {}).get(str(tf).upper()))
            or {}
        )

        active_state_tier = str(
            active_state_frame.get("tier")
            or active_state_frame.get("qualification_status")
            or ""
        ).upper()

        active_state_si = str(
            active_state_frame.get("strategy_instance_id")
            or ""
        ).strip()

        # 서버 설정이 실제 활성 인스턴스의 기준이다. 새 연구소의 배포 이력이
        # 비어 있어도 기존 대표 프레임을 새 카나리로 교체하지 않는다.
        active_cfg = load(CONFIG, {})
        active_cfg_frame = ((active_cfg.get("frames") or {}).get(str(tf).upper()) or {})
        active_live_instances = [
            row for iid, row in ((active_cfg.get("r4_runtime") or {}).get("strategy_instances") or {}).items()
            if isinstance(row, dict) and str(iid) != strategy_instance_id
            and str(row.get("timeframe") or "").upper() == str(tf).upper()
            and str(row.get("tier") or "").upper() in {"CANARY", "SMALL", "NORMAL"}
            and row.get("server_activation_authorized") is True
        ]
        preserve_active_frame = bool(active_cfg_frame) and (
            bool(active_live_instances)
            or (active_state_tier in {"CANARY", "SMALL", "NORMAL"}
                and bool(active_state_si) and active_state_si != strategy_instance_id)
        )

        if preserve_active_frame:
            active_cfg = load(
                CONFIG,
                {},
            )

            active_cfg_frame = (
                (
                    active_cfg.get("frames")
                    or {}
                ).get(
                    str(tf).upper()
                )
                or {}
            )

            if not active_cfg_frame:
                raise RuntimeError(
                    "ACTIVE_PEER_FRAME_CONFIG_MISSING="
                    + str(tf).upper()
                )

            pending_cfg.setdefault(
                "frames",
                {},
            )[str(tf).upper()] = json.loads(
                json.dumps(
                    active_cfg_frame
                )
            )

        pending_frame = (
            pending_cfg
            .setdefault(
                "frames",
                {},
            )
            .setdefault(
                str(tf).upper(),
                {},
            )
        )

        if not preserve_active_frame:
            pending_frame[
                "deployment_tier"
            ] = "CANARY"

            pending_frame[
                "capital_weight"
            ] = canary_weight

            pending_frame[
                "capital_base_weight"
            ] = canary_base_weight

            pending_frame[
                "adaptive_capital_boost_multiplier"
            ] = canary_boost_multiplier

            pending_frame[
                "status"
            ] = "PROVISIONAL_MAIN"

            pending_frame[
                "enabled"
            ] = True

        r4_runtime = pending_cfg.setdefault(
            "r4_runtime",
            {},
        )

        r4_runtime.setdefault(
            "strategy_instances",
            {},
        )[strategy_instance_id] = {
            "strategy_id":
                str(
                    strategy_instance.get(
                        "strategy_id"
                    )
                    or ""
                ),

            "timeframe":
                str(
                    strategy_instance.get(
                        "timeframe"
                    )
                    or ""
                ),

            "exact_strategy_version":
                version_id,

            "strategy_policy_bundle_version":
                str(
                    strategy_instance.get(
                        "strategy_policy_bundle_version"
                    )
                    or ""
                ),

            "policy_snapshot":
                strategy_instance.get(
                    "policy_snapshot"
                )
                or {},

            "tier":
                "CANARY",

            "capital_weight":
                canary_weight,

            "capital_base_weight":
                canary_base_weight,

            "adaptive_capital_boost_multiplier":
                canary_boost_multiplier,

            "adaptive_capital_boost_policy":
                "SUB_100M_AGGRESSIVE_GROWTH_ADAPTIVE_CAPITAL_BOOST_V1",

            "approved_for_server_release":
                True,
        }

        atomic(
            cfgp,
            pending_cfg,
        )

        req = json.loads(
            json.dumps(req)
        )

        req[
            "tier"
        ] = "CANARY"

        req[
            "canary_capital_weight"
        ] = canary_weight

        req[
            "canary_base_capital_weight"
        ] = canary_base_weight

        req[
            "adaptive_capital_boost_multiplier"
        ] = canary_boost_multiplier

        req[
            "adaptive_capital_boost"
        ] = canary_boost

        req[
            "shadow_gate"
        ] = "PASS"

        req[
            "shadow_gate_version_id"
        ] = version_id

        req[
            "shadow_passed_at"
        ] = now()

        strategy_instance = (
            governance
            .set_strategy_instance_status(
                strategy_instance_id,
                "CANARY",
                {
                    "reason":
                        "MINIMAL_SHADOW_RUNTIME_GATE_PASS",

                    "timeframe":
                        str(tf).upper(),

                    "at":
                        now(),
                },
            )
        )

        req[
            "strategy_instance_id"
        ] = strategy_instance_id

        req[
            "strategy_policy_bundle_version"
        ] = str(
            strategy_instance.get(
                "strategy_policy_bundle_version"
            )
            or ""
        )

        req[
            "strategy_policy_snapshot"
        ] = (
            strategy_instance.get(
                "policy_snapshot"
            )
            or {}
        )

        req[
            "approved_for_server_release"
        ] = True

        req[
            "local_qualification_tier"
        ] = "CANARY"

        atomic(
            REQUEST,
            req,
        )

        fr["tier"] = (
            "PENDING_CANARY_SYNC"
        )

        fr[
            "request_id"
        ] = req.get(
            "request_id"
        )

        fr[
            "shadow_status"
        ] = "PASS"

        fr[
            "shadow_passed_at"
        ] = now()

        fr.pop(
            "shadow_block_reason",
            None,
        )

        fr.pop(
            "pending_request_template",
            None,
        )

        fr[
            "qualification_status"
        ] = "CANARY"

        s.setdefault(
            "strategy_instances",
            {},
        )[strategy_instance_id] = {
            "strategy_instance_id":
                strategy_instance_id,

            "strategy":
                str(
                    strategy_instance.get(
                        "strategy_id"
                    )
                    or ""
                ),

            "timeframe":
                str(
                    strategy_instance.get(
                        "timeframe"
                    )
                    or ""
                ),

            "strategy_version":
                version_id,

            "strategy_policy_bundle_version":
                str(
                    strategy_instance.get(
                        "strategy_policy_bundle_version"
                    )
                    or ""
                ),

            "tier":
                "PENDING_CANARY_SYNC",

            "qualification_status":
                "CANARY",

            "approved_for_server_release":
                True,

            "shadow_status":
                "PASS",

            "request_id":
                req.get("request_id"),

            "capital_weight":
                canary_weight,

            "capital_base_weight":
                canary_base_weight,

            "adaptive_capital_boost_multiplier":
                canary_boost_multiplier,

            "updated_at":
                now(),
        }

        s["updated_at"] = now()

        atomic(
            STATE,
            s,
        )

        return req

    if changed:

        s["updated_at"] = now()

        atomic(
            STATE,
            s,
        )

    # R4 Policy Bundle -> exact Strategy Instance Canary
    # --------------------------------------------------------
    # VALIDATED Policy Version은 전역 live policy로 직접 승격하지 않는다.
    # 동일 exact Strategy Version + 새 Policy Bundle로 새 Strategy Instance를
    # 만들고 모든 대상 Instance Shadow가 PASS한 뒤에만 Canary release를 만든다.

    for track in [
        "DIRECTION",
        "ENTRY_RISK",
        "EXIT",
        "REENTRY",
        "SYMBOL",
        "PORTFOLIO",
        "REGIME",
        "EXECUTION",
    ]:
        row = (
            ((s.get("policies") or {}).get(track))
            or {}
        )

        tier = str(
            row.get("tier")
            or ""
        ).upper()
        if tier not in {
            "PENDING_SHADOW",
            "LOCAL_POLICY_READY",
        }:
            continue

        policy_version = str(
            row.get("policy_version")
            or ""
        )
        if not policy_version:
            row["tier"] = "PENDING_SHADOW"
            row["shadow_status"] = "BLOCKED"
            row["shadow_block_reason"] = "POLICY_VERSION_MISSING"
            s["updated_at"] = now()
            atomic(STATE, s)
            continue

        targets = row.get("strategy_instances")
        if not isinstance(targets, list) or not targets:
            row["tier"] = "PENDING_SHADOW"
            row["shadow_status"] = "BLOCKED"
            row["shadow_block_reason"] = (
                "POLICY_REQUIRES_EXACT_STRATEGY_INSTANCE_TARGETS"
            )
            s["updated_at"] = now()
            atomic(STATE, s)
            continue

        all_shadow_pass = True
        bound_instances = []

        for target in targets:
            if not isinstance(target, dict):
                all_shadow_pass = False
                break

            instance_id = str(
                target.get("strategy_instance_id")
                or ""
            )
            exact_version = str(
                target.get("strategy_version")
                or ""
            )

            if not instance_id or not exact_version:
                all_shadow_pass = False
                break

            try:
                instance = governance.strategy_instance(instance_id)
            except Exception:
                all_shadow_pass = False
                break

            if str(
                instance.get("exact_strategy_version")
                or ""
            ) != exact_version:
                raise RuntimeError(
                    "POLICY_STRATEGY_INSTANCE_VERSION_BINDING_MISMATCH="
                    + instance_id
                )

            if str(
                instance.get("strategy_policy_bundle_version")
                or ""
            ) != str(
                row.get("strategy_policy_bundle_version")
                or ""
            ):
                raise RuntimeError(
                    "POLICY_STRATEGY_INSTANCE_BUNDLE_BINDING_MISMATCH="
                    + instance_id
                )

            instance_status = str(
                instance.get("status")
                or ""
            ).upper()

            if instance_status not in {
                "LOCAL_SHADOW",
                "CANARY",
            }:
                raise RuntimeError(
                    "POLICY_STRATEGY_INSTANCE_QUALIFICATION_INVALID="
                    + instance_id
                )

            if not minimal_shadow_pass(
                exact_version,
                instance_id,
            ):
                all_shadow_pass = False

            bound_instances.append(
                (target, instance)
            )

        if not all_shadow_pass:
            row["tier"] = "PENDING_SHADOW"
            row["shadow_status"] = "PENDING"
            row["shadow_block_reason"] = (
                "POLICY_REQUIRES_EXACT_INSTANCE_MINIMAL_SHADOW_PASS"
            )
            s["updated_at"] = now()
            atomic(STATE, s)
            continue

        req = (
            row.get("pending_request_template")
            if isinstance(row.get("pending_request_template"), dict)
            else None
        )
        if not req:
            row["shadow_status"] = "PASS"
            row["shadow_block_reason"] = (
                "POLICY_CANARY_REQUEST_TEMPLATE_MISSING"
            )
            s["updated_at"] = now()
            atomic(STATE, s)
            continue

        if str(
            req.get("version_kind")
            or ""
        ).upper() != "STRATEGY_INSTANCE_POLICY_BUNDLE":
            raise RuntimeError("POLICY_REQUEST_KIND_INVALID")

        if str(
            req.get("policy_track")
            or ""
        ).upper() != track:
            raise RuntimeError("POLICY_REQUEST_TRACK_MISMATCH")

        if str(
            req.get("policy_version")
            or ""
        ) != policy_version:
            raise RuntimeError("POLICY_REQUEST_VERSION_MISMATCH")

        cfg_rel = str(
            req.get("strategy_config_file")
            or ""
        )
        if not cfg_rel:
            raise RuntimeError("POLICY_CANARY_CONFIG_FILE_MISSING")

        cfgp = ROOT / cfg_rel
        pending_cfg = load(cfgp, {})
        if not isinstance(pending_cfg, dict):
            raise RuntimeError("POLICY_CANARY_PENDING_CONFIG_INVALID")

        pending_policy_versions = (
            pending_cfg.get("policy_versions")
            or {}
        )
        if str(
            pending_policy_versions.get(track)
            or ""
        ) != policy_version:
            raise RuntimeError("POLICY_PENDING_CONFIG_VERSION_MISMATCH")

        arch = research_architecture_v3()
        try:
            canary_weight = float(
                (arch.get("canary") or {}).get("initial_capital_weight")
            )
        except Exception:
            raise RuntimeError("CANARY_WEIGHT_INVALID")
        if abs(canary_weight - 0.20) > 1e-12:
            raise RuntimeError("CANARY_WEIGHT_NOT_TWENTY_PERCENT")

        req = json.loads(json.dumps(req))
        approved_targets = []
        pending_instances = (
            pending_cfg
            .setdefault("r4_runtime", {})
            .setdefault("strategy_instances", {})
        )

        for target, instance in bound_instances:
            instance_id = str(target["strategy_instance_id"])
            instance_status = str(
                instance.get("status")
                or ""
            ).upper()

            if instance_status == "LOCAL_SHADOW":
                instance = governance.set_strategy_instance_status(
                    instance_id,
                    "CANARY",
                    {
                        "reason": "POLICY_BUNDLE_MINIMAL_SHADOW_PASS",
                        "policy_track": track,
                        "policy_version": policy_version,
                        "at": now(),
                    },
                )

            approved = dict(target)
            approved["tier"] = "CANARY"
            approved["capital_weight"] = canary_weight
            approved["approved_for_server_release"] = True

            # Frame-level absolute leverage evidence must travel with the
            # exact Strategy Instance into the pending runtime config.
            # It is evaluated at the deployment frame, never per strategy.
            frame_eval = {}
            frame_row = (
                pending_cfg.get("frames", {}).get(
                    str(target.get("timeframe") or "").upper(),
                    {}
                )
                if isinstance(pending_cfg.get("frames"), dict)
                else {}
            )
            if isinstance(frame_row, dict) and isinstance(
                frame_row.get("leverage_absolute_evaluation"), dict
            ):
                frame_eval = json.loads(
                    json.dumps(frame_row["leverage_absolute_evaluation"])
                )

            if not frame_eval:
                state_frame_row = (
                    s.get("frames", {}).get(
                        str(target.get("timeframe") or "").upper(),
                        {}
                    )
                    if isinstance(s.get("frames"), dict)
                    else {}
                )
                if isinstance(state_frame_row, dict) and isinstance(
                    state_frame_row.get("leverage_absolute_evaluation"), dict
                ):
                    frame_eval = json.loads(
                        json.dumps(state_frame_row["leverage_absolute_evaluation"])
                    )

            if frame_eval:
                approved["leverage_absolute_evaluation"] = frame_eval

            approved_targets.append(approved)

            pending_instances[instance_id] = {
                "strategy_id": str(
                    instance.get("strategy_id")
                    or target.get("strategy")
                    or ""
                ),
                "timeframe": str(
                    instance.get("timeframe")
                    or target.get("timeframe")
                    or ""
                ),
                "exact_strategy_version": str(
                    instance.get("exact_strategy_version")
                    or target.get("strategy_version")
                    or ""
                ),
                "strategy_policy_bundle_version": str(
                    instance.get("strategy_policy_bundle_version")
                    or ""
                ),
                "policy_snapshot": (
                    instance.get("policy_snapshot")
                    or target.get("strategy_policy_snapshot")
                    or {}
                ),
                "tier": "CANARY",
                "capital_weight": canary_weight,
                "approved_for_server_release": True,
                "leverage_absolute_evaluation": json.loads(
                    json.dumps(
                        approved.get("leverage_absolute_evaluation") or {}
                    )
                ),
            }

            s.setdefault("strategy_instances", {})[instance_id] = {
                **approved,
                "qualification_status": "CANARY",
                "shadow_status": "PASS",
                "policy_track": track,
                "policy_version": policy_version,
                "updated_at": now(),
            }

        atomic(cfgp, pending_cfg)

        req["tier"] = "CANARY"
        req["shadow_gate"] = "PASS"
        req["shadow_passed_at"] = now()
        req["approved_for_server_release"] = True
        req["local_qualification_tier"] = "CANARY"
        req["strategy_instances"] = approved_targets
        req["shadow_gate_strategy_instances"] = [
            target["strategy_instance_id"]
            for target in approved_targets
        ]

        atomic(REQUEST, req)

        row["tier"] = "PENDING_CANARY_SYNC"
        row["request_id"] = req.get("request_id")
        row["shadow_status"] = "PASS"
        row["shadow_passed_at"] = now()
        row["approved_for_server_release"] = True
        row["strategy_instances"] = approved_targets
        row.pop("shadow_block_reason", None)
        row.pop("pending_request_template", None)

        s["updated_at"] = now()
        atomic(STATE, s)
        return req

    return None






def reconcile_existing_canary(tf):
    """
    Canonical recovery for an already-existing CANARY whose
    active runtime frame drifted from deployment state.

    Recovery only:
      - no new promotion
      - no positive scale
      - preserve original CANARY lifecycle
    """

    tf = str(tf or "").upper()

    if not tf:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_TIMEFRAME_MISSING"
        )

    if PUSH_HOLD.exists() or REQUEST.exists():
        return None

    s = state()

    fr = (
        ((s.get("frames") or {}).get(tf))
        or {}
    )

    if str(
        fr.get("tier") or ""
    ).upper() != "CANARY":
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_REQUIRES_CANARY_STATE"
        )

    strategy = str(
        fr.get("strategy") or ""
    ).upper()

    strategy_version = str(
        fr.get("strategy_version") or ""
    )

    activated_ts = int(
        fr.get("activated_ts") or 0
    )

    if not strategy:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_STRATEGY_MISSING"
        )

    if not strategy_version:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_MISSING"
        )

    if activated_ts <= 0:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_ACTIVATED_TS_MISSING"
        )

    cfg = load(CONFIG, {})

    if not isinstance(cfg, dict):
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_ACTIVE_CONFIG_INVALID"
        )

    active_frame = (
        ((cfg.get("frames") or {}).get(tf))
        or {}
    )

    cfgp = (
        RES
        / (
            "strategy_config_pending_"
            + tf.lower()
            + ".json"
        )
    )

    if not cfgp.exists():
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_PENDING_CONFIG_MISSING"
        )

    pending_cfg = load(cfgp, {})

    if not isinstance(
        pending_cfg,
        dict,
    ):
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_PENDING_CONFIG_INVALID"
        )

    pending_frame = (
        (
            (pending_cfg.get("frames") or {})
            .get(tf)
        )
        or {}
    )

    pending_strategies = [
        str(x).upper()
        for x in (
            pending_frame.get("strategies")
            or []
        )
    ]

    if pending_frame.get("enabled") is not True:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_PENDING_NOT_ENABLED"
        )

    if str(
        pending_frame.get(
            "deployment_tier"
        )
        or ""
    ).upper() != "CANARY":
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_PENDING_NOT_CANARY"
        )

    # MULTI_STRATEGY_RECONCILE_V1
    pending_strategy_identity = (
        "+".join(
            pending_strategies
        )
        if len(
            pending_strategies
        ) > 1
        else (
            pending_strategies[0]
            if pending_strategies
            else ""
        )
    )

    if pending_strategy_identity != strategy:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_STRATEGY_BINDING_FAIL"
        )

    if str(
        pending_frame.get(
            "strategy_version"
        )
        or ""
    ) != strategy_version:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_BINDING_FAIL"
        )

    try:
        weight = float(
            pending_frame.get(
                "capital_weight"
            )
        )
    except Exception:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_WEIGHT_INVALID"
        )

    if abs(weight - 0.20) > 1e-12:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_WEIGHT_NOT_TWENTY_PERCENT"
        )

    # Already repaired.
    if active_frame == pending_frame:
        return None

    # Resolve the exact immutable source from the registered
    # Strategy Version. Never guess a deploy filename.
    version_registry_path = (
        RES
        / "version_registry.json"
    )

    version_registry = load(
        version_registry_path,
        {},
    )

    if not isinstance(
        version_registry,
        dict,
    ):
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_REGISTRY_INVALID"
        )

    version_row = (
        (
            version_registry.get(
                "strategy_versions"
            )
            or {}
        ).get(
            strategy_version
        )
        or {}
    )

    if not isinstance(
        version_row,
        dict,
    ) or not version_row:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_RECORD_MISSING"
        )

    if str(
        version_row.get(
            "version_id"
        )
        or ""
    ) != strategy_version:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_RECORD_ID_MISMATCH"
        )

    if str(
        version_row.get(
            "kind"
        )
        or ""
    ).upper() != "STRATEGY":
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_KIND_MISMATCH"
        )

    if str(
        version_row.get(
            "status"
        )
        or ""
    ).upper() != "CANARY":
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_STATUS_NOT_CANARY"
        )

    payload = (
        version_row.get(
            "payload"
        )
        or {}
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_VERSION_PAYLOAD_INVALID"
        )

    if str(
        payload.get(
            "strategy"
        )
        or ""
    ).upper() != strategy:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_SOURCE_STRATEGY_MISMATCH"
        )

    if str(
        payload.get(
            "timeframe"
        )
        or ""
    ).upper() != tf:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_SOURCE_TIMEFRAME_MISMATCH"
        )

    source_rel = str(
        payload.get(
            "source_file"
        )
        or ""
    )

    if not source_rel:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_SOURCE_FILE_MISSING"
        )

    strategy_path = (
        ROOT
        / source_rel
    ).resolve()

    root_resolved = (
        ROOT.resolve()
    )

    try:
        strategy_path.relative_to(
            root_resolved
        )
    except ValueError:
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_SOURCE_OUTSIDE_ROOT"
        )

    if not strategy_path.exists():
        raise RuntimeError(
            "EXISTING_CANARY_RECONCILE_STRATEGY_SOURCE_MISSING"
        )

    rid = (
        f"V3_{tf}_"
        f"{strategy}_"
        f"CANARY_RECONCILE_"
        f"{int(time.time())}"
    )

    req = {
        "schema":
            "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",

        "request_id":
            rid,

        "created_at":
            now(),

        "reason":
            "EXISTING_CANARY_ACTIVE_CONFIG_RECONCILE",

        "version_kind":
            "STRATEGY",

        "timeframe":
            tf,

        "strategy":
            strategy,

        "strategy_version":
            strategy_version,

        "policy_versions":
            pending_cfg.get(
                "policy_versions"
            )
            or {},

        "tier":
            "CANARY",

        "canary_capital_weight":
            weight,

        "strategy_file":
            str(
                strategy_path.relative_to(
                    ROOT
                )
            ),

        "strategy_config_file":
            str(
                cfgp.relative_to(
                    ROOT
                )
            ),

        "previous_frame_config":
            fr.get(
                "previous_frame_config"
            )
            or {},

        "reconcile_existing_canary":
            True,

        "reconcile_no_promotion":
            True,

        "reconcile_no_scale":
            True,

        "reconcile_original_request_id":
            fr.get("request_id")
            or "",

        "reconcile_preserve_activated_ts":
            activated_ts,

        "autonomous_rule_passed":
            True,

        "core_mutation":
            False,
    }

    atomic(
        REQUEST,
        req,
    )

    return req


def runtime_exits(tf,strategy):
    if not RUNTIME_DB.is_file(): return []
    con=sqlite3.connect(str(RUNTIME_DB),timeout=10); con.row_factory=sqlite3.Row
    try:
        cols={r[1] for r in con.execute("PRAGMA table_info(exit_history)")}
        if not {"entry_tf","entry_strategy","roi","ts"}.issubset(cols): return []
        # CANARY_EVIDENCE_EPOCH_V1
        def _col(name, fallback):
            return (
                name
                if name in cols
                else fallback + " AS " + name
            )

        select_cols = [
            "ts",
            "roi",
            "pnl",
            "hold_sec",
            "reason",
            "exit_type",
            "entry_tf",
            "entry_strategy",
            _col("symbol", "''"),
            _col("entry_id", "''"),
            _col("entry_ts", "0"),
            _col("position_cycle_id", "''"),
            _col("payload_json", "'{}'"),
        ]

        sql = (
            "SELECT "
            + ",".join(select_cols)
            + " FROM exit_history "
            + "WHERE UPPER(COALESCE(entry_tf,''))=? "
            + "AND UPPER(COALESCE(entry_strategy,''))=? "
            + "ORDER BY ts"
        )

        rows = con.execute(
            sql,
            (
                tf.upper(),
                strategy.upper(),
            ),
        ).fetchall()

        return [
            dict(r)
            for r in rows
        ]
    finally: con.close()
def _t_critical_975(df):
    # Two-sided 95% Student-t critical values; asymptotic 1.96.
    table={1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365,8:2.306,9:2.262,10:2.228,11:2.201,12:2.179,13:2.160,14:2.145,15:2.131,16:2.120,17:2.110,18:2.101,19:2.093,20:2.086,21:2.080,22:2.074,23:2.069,24:2.064,25:2.060,26:2.056,27:2.052,28:2.048,29:2.045,30:2.042}
    return table.get(max(1,int(df)),1.96)


def r4_feedback_closed_rows(
    strategy_instance_id,
    tf,
    strategy,
    strategy_version,
    since_ts=0,
):
    """
    R4_FEEDBACK_EXACT_INSTANCE_V2

    Read-only exact-instance production feedback.

    Legacy-adopted positions:
      - exact entry price may be reused from LEGACY_ADOPTION evidence
      - pre-adoption holding time is NOT fabricated
      - pre-adoption MFE/MAE is NOT fabricated
      - historical entry fee completeness is NOT fabricated
    """
    strategy_instance_id=str(
        strategy_instance_id
        or ""
    ).strip()
    tf=str(tf or "").upper()
    strategy=str(strategy or "").upper()
    strategy_version=str(strategy_version or "").strip()
    since_ts=int(since_ts or 0)

    if (
        not strategy_instance_id
        or not tf
        or not strategy
        or not strategy_version
        or not R4_FEEDBACK_DB.is_file()
    ):
        return []

    con=sqlite3.connect(
        f"file:{R4_FEEDBACK_DB}?mode=ro",
        uri=True,
        timeout=10,
    )
    con.row_factory=sqlite3.Row
    out=[]

    try:
        legs=con.execute(
            """
            SELECT *
            FROM r4_active_legs
            WHERE status='CLOSED'
              AND instance_id=?
              AND UPPER(frame)=?
              AND UPPER(strategy_id)=?
              AND strategy_version=?
            ORDER BY updated_ts,leg_id
            """,
            (
                strategy_instance_id,
                tf,
                strategy,
                strategy_version,
            ),
        ).fetchall()

        for leg in legs:
            leg_id=str(leg["leg_id"])

            exit_row=con.execute(
                """
                SELECT detail_json,ts
                FROM r4_leg_events
                WHERE leg_id=?
                  AND event_type='EXIT_FILL'
                ORDER BY id DESC
                LIMIT 1
                """,
                (leg_id,),
            ).fetchone()

            if not exit_row:
                continue

            exit_ts=int(exit_row["ts"] or 0)
            if exit_ts < since_ts:
                continue

            try:
                detail=json.loads(
                    exit_row["detail_json"] or "{}"
                )
            except Exception:
                detail={}

            if not isinstance(detail,dict):
                detail={}

            adoption_row=con.execute(
                """
                SELECT detail_json
                FROM r4_leg_events
                WHERE leg_id=?
                  AND event_type='LEGACY_ADOPTION'
                ORDER BY id
                LIMIT 1
                """,
                (leg_id,),
            ).fetchone()

            adopted=bool(adoption_row)
            adoption_detail={}

            if adoption_row:
                try:
                    adoption_detail=json.loads(
                        adoption_row["detail_json"] or "{}"
                    )
                except Exception:
                    adoption_detail={}

            fills=con.execute(
                """
                SELECT qty_units,price_ticks,
                       fee_text,fee_asset,
                       realized_pnl_text
                FROM r4_fills
                WHERE leg_id=?
                ORDER BY id
                """,
                (leg_id,),
            ).fetchall()

            entry_qty=sum(
                int(x["qty_units"] or 0)
                for x in fills
            )

            entry_ticks=(
                sum(
                    int(x["qty_units"] or 0)
                    * int(x["price_ticks"] or 0)
                    for x in fills
                ) // entry_qty
                if entry_qty
                else 0
            )

            if (
                not entry_ticks
                and adopted
                and isinstance(adoption_detail,dict)
            ):
                entry_ticks=int(
                    adoption_detail.get(
                        "entry_price_ticks"
                    )
                    or 0
                )

            trail=con.execute(
                """
                SELECT *
                FROM r4_leg_trailing
                WHERE leg_id=?
                """,
                (leg_id,),
            ).fetchone()

            if trail and not entry_ticks:
                entry_ticks=int(
                    trail["entry_price_ticks"] or 0
                )

            exit_ticks=int(
                detail.get("price_ticks") or 0
            )

            roi=None

            if entry_ticks > 0 and exit_ticks > 0:
                move=(
                    float(exit_ticks-entry_ticks)
                    / float(entry_ticks)
                )

                if str(
                    leg["position_side"]
                ).upper()=="SHORT":
                    move=-move

                roi=move*100.0

            realized=[]
            fee_by_asset={}

            for fill in fills:
                raw=str(
                    fill["realized_pnl_text"]
                    or ""
                ).strip()

                if raw:
                    try:
                        realized.append(float(raw))
                    except Exception:
                        pass

                asset=str(
                    fill["fee_asset"]
                    or "UNKNOWN"
                ).upper() or "UNKNOWN"

                try:
                    fee=float(
                        str(fill["fee_text"] or "0")
                    )
                    fee_by_asset[asset]=(
                        fee_by_asset.get(asset,0.0)
                        + fee
                    )
                except Exception:
                    pass

            raw_exit_pnl=str(
                detail.get("realized_pnl_text")
                or ""
            ).strip()

            if raw_exit_pnl:
                try:
                    realized.append(
                        float(raw_exit_pnl)
                    )
                except Exception:
                    pass

            exit_asset=str(
                detail.get("fee_asset")
                or "UNKNOWN"
            ).upper() or "UNKNOWN"

            try:
                exit_fee=float(
                    str(detail.get("fee_text") or "0")
                )
                fee_by_asset[exit_asset]=(
                    fee_by_asset.get(exit_asset,0.0)
                    + exit_fee
                )
            except Exception:
                pass

            pnl=(
                sum(realized)
                if realized
                else None
            )

            # Legacy adoption? ?? ????? ???? ???.
            hold_sec=(
                None
                if adopted
                else max(
                    0,
                    exit_ts-int(leg["created_ts"] or 0),
                )
            )

            mfe_ticks=(
                int(trail["mfe_ticks"] or 0)
                if trail
                else None
            )

            mae_ticks=(
                int(trail["mae_ticks"] or 0)
                if trail
                else None
            )

            try:
                policy_snapshot=json.loads(
                    leg["policy_snapshot"]
                    or "{}"
                )
            except Exception:
                policy_snapshot={}

            reference_ticks=int(
                (
                    policy_snapshot
                    if isinstance(
                        policy_snapshot,
                        dict,
                    )
                    else {}
                ).get(
                    "entry_reference_price_ticks"
                )
                or 0
            )

            slippage_bps=None
            if (
                entry_ticks > 0
                and reference_ticks > 0
            ):
                if str(
                    leg["position_side"]
                ).upper()=="LONG":
                    slippage_bps=(
                        (
                            entry_ticks
                            - reference_ticks
                        )
                        / reference_ticks
                        * 10000.0
                    )
                else:
                    slippage_bps=(
                        (
                            reference_ticks
                            - entry_ticks
                        )
                        / reference_ticks
                        * 10000.0
                    )

            extreme_ticks=(
                int(
                    trail["extreme_ticks"]
                    or 0
                )
                if trail
                else 0
            )

            max_giveback_ticks=None
            if (
                extreme_ticks > 0
                and exit_ticks > 0
            ):
                if str(
                    leg["position_side"]
                ).upper()=="LONG":
                    max_giveback_ticks=max(
                        0,
                        extreme_ticks-exit_ticks,
                    )
                else:
                    max_giveback_ticks=max(
                        0,
                        exit_ticks-extreme_ticks,
                    )

            funding_text="0"
            try:
                funding_text=str(
                    sum(
                        (
                            float(
                                r["value_text"]
                            )
                            for r
                            in con.execute(
                                """
                                SELECT value_text
                                FROM r4_performance_events
                                WHERE leg_id=?
                                  AND metric='FUNDING'
                                """,
                                (leg_id,),
                            ).fetchall()
                        ),
                        0.0,
                    )
                )
            except Exception:
                funding_text="0"

            out.append({
                "source":"R4_FEEDBACK",
                "leg_id":leg_id,
                "instance_id":str(leg["instance_id"]),
                "tf":str(leg["frame"]).upper(),
                "strategy":str(leg["strategy_id"]).upper(),
                "strategy_version":str(
                    leg["strategy_version"]
                ),
                "symbol":str(leg["symbol"]).upper(),
                "side":str(
                    leg["position_side"]
                ).upper(),
                "entry_ts":(
                    None
                    if adopted
                    else int(leg["created_ts"] or 0)
                ),
                "exit_ts":exit_ts,
                "ts":exit_ts,
                "roi":roi,
                "pnl":pnl,
                "fee_by_asset":fee_by_asset,
                "hold_sec":hold_sec,
                "holding_seconds":hold_sec,
                "reason":str(
                    detail.get("reason") or ""
                ),
                "exit_reason":str(
                    detail.get("reason") or ""
                ),
                "entry_price_ticks":entry_ticks,
                "exit_price_ticks":exit_ticks,
                "entry_reference_price_ticks":
                    reference_ticks or None,
                "slippage_bps":slippage_bps,
                "funding_text":funding_text,
                "mfe_ticks":mfe_ticks,
                "mae_ticks":mae_ticks,
                "max_giveback_ticks":
                    max_giveback_ticks,
                "legacy_adopted":adopted,
                "holding_time_complete":not adopted,
                "path_extrema_complete":not adopted,
                "fee_history_complete":not adopted,
                "path_extrema_scope":(
                    "POST_ADOPTION_ONLY"
                    if adopted
                    else "FULL_R4_LIFECYCLE"
                ),
            })

    finally:
        con.close()

    return out


def _resolve_r4_feedback_instance_id(
    tf,
    strategy,
    strategy_version,
):
    """
    Resolve feedback to exactly one Strategy Instance.

    Zero matches or multiple matches fail closed.
    """
    tf=str(tf or "").upper()
    strategy=str(strategy or "").upper()
    strategy_version=str(
        strategy_version
        or ""
    ).strip()

    if (
        not tf
        or not strategy
        or not strategy_version
    ):
        return ""

    instances=(
        state().get("strategy_instances")
        or {}
    )

    if not isinstance(instances,dict):
        return ""

    matches=[]

    for instance_id,profile in instances.items():
        if not isinstance(profile,dict):
            continue

        row_tf=str(
            profile.get("timeframe")
            or ""
        ).upper()

        row_strategy=str(
            profile.get("strategy")
            or ""
        ).upper()

        row_version=str(
            profile.get("strategy_version")
            or profile.get("exact_strategy_version")
            or ""
        ).strip()

        if (
            row_tf == tf
            and row_strategy == strategy
            and row_version == strategy_version
        ):
            matches.append(
                str(instance_id)
            )

    if len(matches) != 1:
        return ""

    return matches[0]


def green7a_live_evidence(
    strategy_instance_id,
    tf,
    strategy,
    strategy_version,
    since_ts,
):
    """
    GREEN7A_LIVE_EVIDENCE_ADAPTER_V1

    Build only evidence supported by existing runtime/local state.

    Unsupported or ambiguous facts remain False.
    No synthetic fee/slippage.
    No strategy-version backfill.
    """

    strategy_instance_id = str(
        strategy_instance_id
        or ""
    ).strip()

    tf = str(
        tf
        or ""
    ).upper()

    strategy = str(
        strategy
        or ""
    ).upper()

    strategy_version = str(
        strategy_version
        or ""
    )

    evidence = {
        # ACCOUNT_EQUITY_RANKING_BINDING_V1
        "account_mode": "",
        "account_avg_equity_usdt_24h": None,
        "account_risk_posture": "",
        "account_high_equity_reference_crossed": False,

        "execution_quality_score": 0.0,
        "cost_efficiency_score": 0.0,
        "diversification_value_score": 0.0,

        "entry_risk_safe_size_pass": False,
        "account_risk_budget_pass": False,
        "portfolio_total_exposure_pass": False,
        "available_margin_pass": False,
        "margin_safety_reserve_pass": False,
        "symbol_concentration_pass": False,
        "strategy_concentration_pass": False,
        "correlation_concentration_pass": False,
        "cost_liquidity_scale_pass": False,

        "exact_version_match": False,
        "live_lifecycle_evidence": False,
        "actual_fee_slippage_sane": False,
        "projected_exposure_pass": False,
        "projected_loss_risk_budget_pass": False,

        "runtime_error_count": 0,
        "timing_lookahead_violation_count": 0,
        "signal_parity_violation_count": 0,
        "order_anomaly_count": 0,

        "actual_small_live_lifecycle_evidence": False,

        "green7a_evidence_schema":
            "GREEN7A_LIVE_EVIDENCE_ADAPTER_V1",

        "evidence_fail_closed":
            True,

        "unsupported_evidence": [
            "EXACT_VERSION_LIFECYCLE_BINDING",
            "ACTUAL_FEE_SLIPPAGE",
            "PROJECTED_SCALE_EXPOSURE",
            "PROJECTED_SCALE_LOSS_RISK",
            "LIVE_MARGIN_SCALE_PROJECTION",
            "LIVE_CONCENTRATION_SCALE_PROJECTION",
        ],
    }

    # --------------------------------------------------------
    # Account mode
    #
    # Existing persistent account-mode state may be reused
    # read-only. Missing/stale/invalid remains unknown.
    # --------------------------------------------------------

    account_paths = [
        ROOT
        / "state"
        / "account_mode.json",

        ROOT
        / "data"
        / "server_runtime"
        / "state"
        / "account_mode.json",
    ]

    for path in account_paths:

        if not path.is_file():
            continue

        try:
            row = json.loads(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )
        except Exception:
            continue

        if not isinstance(
            row,
            dict,
        ):
            continue

        mode = str(
            row.get("mode")
            or row.get(
                "current_mode"
            )
            or ""
        ).upper()

        ok = (
            row.get("ok") is True
            if "ok" in row
            else bool(mode)
        )

        if (
            ok
            and mode
            in {
                "GROWTH",
                "BALANCED",
                "DEFENSIVE",
            }
        ):
            evidence[
                "account_mode"
            ] = mode

            try:
                avg_equity = float(
                    row.get(
                        "avg_equity_usdt_24h"
                    )
                )
            except Exception:
                avg_equity = None

            evidence[
                "account_avg_equity_usdt_24h"
            ] = avg_equity

            evidence[
                "account_risk_posture"
            ] = str(
                row.get(
                    "risk_posture"
                )
                or ""
            ).upper()

            evidence[
                "account_high_equity_reference_crossed"
            ] = bool(
                row.get(
                    "high_equity_reference_crossed"
                )
            )

            break

    # --------------------------------------------------------
    # Real lifecycle
    #
    # runtime_exits already scopes by TF + Strategy.
    # This proves real lifecycle exists for the strategy name,
    # but NOT exact Strategy Version because history schema
    # currently lacks strategy_version.
    # --------------------------------------------------------

    try:
        rows = [
            row
            for row in runtime_exits(
                tf,
                strategy,
            )
            if int(
                row.get("ts")
                or row.get("exit_ts")
                or 0
            )
            >= int(
                since_ts
                or 0
            )
        ]
    except Exception:
        rows = []

    real_rows = [
        row
        for row in rows
        if isinstance(
            row,
            dict,
        )
    ]

    # --------------------------------------------------------
    # Exact Strategy Version binding
    #
    # New Frozen runner telemetry already persists version via:
    #   entry payload -> pending.strategy_version
    #   exit payload  -> position.strategy_version
    #
    # Historical rows without version are never backfilled.
    # --------------------------------------------------------

    def _payload_dict(row):

        raw = row.get(
            "payload_json"
        )

        if isinstance(
            raw,
            dict,
        ):
            return raw

        if isinstance(
            raw,
            str,
        ):
            try:
                parsed = json.loads(
                    raw
                )
            except Exception:
                return {}

            return (
                parsed
                if isinstance(
                    parsed,
                    dict,
                )
                else {}
            )

        return {}

    def _payload_version(row):

        payload = _payload_dict(
            row
        )

        candidates = [
            payload.get(
                "strategy_version"
            ),

            (
                payload.get(
                    "pending"
                )
                or {}
            ).get(
                "strategy_version"
            ),

            (
                payload.get(
                    "position"
                )
                or {}
            ).get(
                "strategy_version"
            ),

            (
                payload.get(
                    "gate"
                )
                or {}
            ).get(
                "strategy_version"
            ),

            (
                payload.get(
                    "candidate"
                )
                or {}
            ).get(
                "strategy_version"
            ),

            (
                payload.get(
                    "owner"
                )
                or {}
            ).get(
                "strategy_version"
            ),
        ]

        for value in candidates:

            value = str(
                value
                or ""
            ).strip()

            if value:
                return value

        return ""

    exact_rows = [
        row
        for row in real_rows
        if (
            strategy_version
            and _payload_version(
                row
            )
            == strategy_version
        )
    ]

    # R4_FEEDBACK_EXACT_INSTANCE_V1
    # R4 snapshot? exact Strategy Version?? DB? ?? ??? ??.
    # legacy evidence? ??? ?? leg? ??/???? ???.
    try:
        r4_rows = r4_feedback_closed_rows(
            strategy_instance_id,
            tf,
            strategy,
            strategy_version,
            since_ts,
        )
    except Exception:
        r4_rows = []

    evidence["r4_feedback_closed_count"] = len(
        r4_rows
    )

    evidence["r4_feedback_source"] = (
        "LOCAL_SQLITE_SNAPSHOT"
        if R4_FEEDBACK_DB.is_file()
        else "MISSING"
    )

    evidence["r4_feedback_db"] = str(
        R4_FEEDBACK_DB
    )

    evidence["r4_exact_version_lifecycle_evidence"] = bool(
        r4_rows
    )

    evidence["r4_realized_pnl_sample_count"] = sum(
        1
        for row in r4_rows
        if row.get("pnl") is not None
    )

    evidence["r4_roi_sample_count"] = sum(
        1
        for row in r4_rows
        if row.get("roi") is not None
    )

    evidence["r4_mfe_mae_sample_count"] = sum(
        1
        for row in r4_rows
        if (
            row.get("mfe_ticks") is not None
            or row.get("mae_ticks") is not None
        )
    )

    if r4_rows:
        evidence[
            "live_lifecycle_evidence"
        ] = True

        evidence[
            "exact_version_match"
        ] = True

        evidence[
            "lifecycle_binding"
        ] = (
            "R4_STRATEGY_INSTANCE_EXACT_VERSION"
        )

        evidence[
            "live_lifecycle_count"
        ] = (
            len(exact_rows)
            + len(r4_rows)
        )

        evidence[
            "unsupported_evidence"
        ] = [
            x
            for x in evidence[
                "unsupported_evidence"
            ]
            if x
            != "EXACT_VERSION_LIFECYCLE_BINDING"
        ]

    evidence[
        "live_lifecycle_evidence"
    ] = bool(
        exact_rows
    )

    evidence[
        "live_lifecycle_count"
    ] = len(
        exact_rows
    )

    evidence[
        "unversioned_or_other_version_lifecycle_count"
    ] = (
        len(real_rows)
        - len(exact_rows)
    )

    evidence[
        "lifecycle_binding"
    ] = (
        "TIMEFRAME_STRATEGY_EXACT_VERSION"
        if exact_rows
        else (
            "TIMEFRAME_AND_STRATEGY_ONLY_NO_EXACT_VERSION"
            if real_rows
            else "NONE"
        )
    )

    evidence[
        "exact_version_match"
    ] = bool(
        exact_rows
    )

    # --------------------------------------------------------
    # SMALL lifecycle
    #
    # Do not infer this from current tier alone.
    # Existing rows lack exact Strategy Version and scale tier
    # binding, so fail closed.
    # --------------------------------------------------------

    evidence[
        "actual_small_live_lifecycle_evidence"
    ] = False

    # --------------------------------------------------------
    # Actual execution cost
    #
    # Existing history exposes prices/PNL but does not expose
    # a trustworthy intended-price vs fill-price plus actual
    # commission/slippage binding for this exact version.
    # --------------------------------------------------------

    evidence[
        "actual_fee_slippage_sane"
    ] = False

    # --------------------------------------------------------
    # Shadow evidence
    #
    # Can contribute safety diagnostics only.
    # It MUST NOT satisfy actual live lifecycle or actual
    # fee/slippage requirements.
    # --------------------------------------------------------

    shadow_path = (
        RES
        / "validation"
        / strategy_version
        / "shadow.json"
    )

    if (
        strategy_version
        and shadow_path.is_file()
    ):

        try:
            shadow = load(
                shadow_path,
                {},
            )
        except Exception:
            shadow = {}

        if isinstance(
            shadow,
            dict,
        ):

            gate = (
                shadow.get(
                    "minimal_runtime_gate"
                )
                or {}
            )

            # LEGACY_BUNDLE_SHADOW_PENDING_NOT_VIOLATION_V1
            _legacy_bundle_shadow_pending = (
                str(
                    shadow.get(
                        "evidence_mode"
                    )
                    or ""
                )
                == "LEGACY_CANARY_SHADOW_FAIL_CLOSED"
                and
                "LEGACY_SCALE_SOURCE_MISSING"
                in (
                    shadow.get("errors")
                    or []
                )
            )

            evidence[
                "runtime_error_count"
            ] = (
                0
                if _legacy_bundle_shadow_pending
                else int(
                    gate.get(
                        "runtime_errors"
                    )
                    or 0
                )
            )

            evidence[
                "timing_lookahead_violation_count"
            ] = (
                0
                if (
                    _legacy_bundle_shadow_pending
                    or gate.get(
                        "closed_bar_timing_pass"
                    )
                    is True
                )
                else 1
            )

            evidence[
                "signal_parity_violation_count"
            ] = (
                0
                if (
                    _legacy_bundle_shadow_pending
                    or gate.get(
                        "signal_logic_parity_pass"
                    )
                    is True
                )
                else 1
            )

            evidence[
                "shadow_state_transition_pass"
            ] = (
                gate.get(
                    "state_transition_pass"
                )
                is True
            )

            evidence[
                "shadow_cost_simulation_sanity_pass"
            ] = (
                gate.get(
                    "order_simulation_cost_sanity_pass"
                )
                is True
            )

            evidence[
                "shadow_version_source_binding_pass"
            ] = (
                shadow.get(
                    "version_source_binding_pass"
                )
                is True
            )

    if (
        locals().get(
            "_legacy_bundle_shadow_pending",
            False,
        )
    ):
        if (
            "SHADOW_BUNDLE_RUNTIME_GATE_PENDING"
            not in evidence[
                "unsupported_evidence"
            ]
        ):
            evidence[
                "unsupported_evidence"
            ].append(
                "SHADOW_BUNDLE_RUNTIME_GATE_PENDING"
            )

    # --------------------------------------------------------
    # Explicitly unsupported positive scale projections
    # --------------------------------------------------------

    evidence[
        "entry_risk_safe_size_pass"
    ] = False

    evidence[
        "account_risk_budget_pass"
    ] = False

    evidence[
        "portfolio_total_exposure_pass"
    ] = False

    evidence[
        "available_margin_pass"
    ] = False

    evidence[
        "margin_safety_reserve_pass"
    ] = False

    evidence[
        "symbol_concentration_pass"
    ] = False

    evidence[
        "strategy_concentration_pass"
    ] = False

    evidence[
        "correlation_concentration_pass"
    ] = False

    evidence[
        "cost_liquidity_scale_pass"
    ] = False

    evidence[
        "projected_exposure_pass"
    ] = False

    evidence[
        "projected_loss_risk_budget_pass"
    ] = False

    evidence[
        "execution_quality_score"
    ] = 0.0

    evidence[
        "cost_efficiency_score"
    ] = 0.0

    evidence[
        "diversification_value_score"
    ] = 0.0

    return evidence

def canary_evidence(tf,strategy,since_ts=0):
    # MULTI_STRATEGY_CANARY_EVIDENCE_V1
    strategy_components = [
        str(x).upper()
        for x in str(
            strategy
            or ""
        ).split("+")
        if str(x).strip()
    ]

    rows = []

    # LEGACY_OPEN_POSITION_EVIDENCE_EXCLUSION_V1
    evidence_epoch_ts = int(
        since_ts
        or 0
    )

    def _epoch_row_eligible(row):
        exit_ts = int(
            row.get("ts")
            or 0
        )

        if exit_ts < evidence_epoch_ts:
            return False

        # Preserve generic verifier behavior when no epoch exists.
        if evidence_epoch_ts <= 0:
            return True

        entry_ts = int(
            row.get("entry_ts")
            or 0
        )

        # Fail closed:
        # unknown/pre-epoch entries are legacy and never contribute.
        return (
            entry_ts
            >= evidence_epoch_ts
        )

    for component in strategy_components:
        rows.extend(
            [
                r
                for r in runtime_exits(
                    tf,
                    component,
                )
                if _epoch_row_eligible(
                    r
                )
            ]
        )
    vals=[]; hard=[]
    for r in rows:
        try: vals.append(float(r.get("roi")))
        except Exception: pass
        txt=(str(r.get("reason") or "")+" "+str(r.get("exit_type") or "")).upper()
        if "LIQUID" in txt or "FORCED_LIQ" in txt: hard.append(r)
    n=len(vals); mean=(sum(vals)/n) if n else None; lo=hi=None
    if n>=2:
        sd=statistics.stdev(vals)
        se=sd/math.sqrt(n) if n>0 else math.inf
        tcrit=_t_critical_975(n-1)
        lo=mean-tcrit*se; hi=mean+tcrit*se
    total_pnl=sum(float(r.get("pnl") or 0) for r in rows)
    if hard:
        decision="ROLLBACK"
    elif n>=2 and hi is not None and hi<0:
        decision="ROLLBACK"
    elif n>=2 and lo is not None and lo>0 and total_pnl>0:
        decision="PROMOTE"
    else:
        decision="WAIT_MORE_REAL_EVIDENCE"
    out = {
        "closed_trades":len(rows),"roi_samples":n,"avg_roi":mean,"roi_95ci_lower":lo,"roi_95ci_upper":hi,
        "total_pnl":total_pnl,"avg_hold_hours":(sum(float(r.get("hold_sec") or 0) for r in rows)/len(rows)/3600.0) if rows else None,
        "hard_fail_count":len(hard),"last_exit_ts":max([int(r.get("ts") or 0) for r in rows],default=0),
        "evidence_decision":decision,"sufficiency_rule":"95CI_NOT_FIXED_TRADE_COUNT_OR_DAYS"
    }

    s = state()

    fr = (
        (
            s.get("frames")
            or {}
        ).get(
            str(tf or "").upper()
        )
        or {}
    )

    strategy_version = str(
        fr.get(
            "strategy_version"
        )
        or ""
    )

    strategy_instance_id = (
        _resolve_r4_feedback_instance_id(
            tf,
            strategy,
            strategy_version,
        )
    )

    live = green7a_live_evidence(
        strategy_instance_id,
        tf,
        strategy,
        strategy_version,
        since_ts,
    )

    out.update(
        live
    )

    return out


# ============================================================
# TIER_PORTFOLIO_DIVERSITY_SELECTION_V1
#
# CANARY : exploration pool, no new diversity restriction.
# SMALL  : quality 70% + diversity 30%.
# NORMAL : quality 60% + diversity 40%.
#
# Measured pair metrics are enforced when available.
# Missing pair metrics are never fabricated.
# Family-cap is always enforceable.
# Tier capacity is a maximum, never a quota.
# ============================================================

def _v3_div_num(value):
    try:
        if isinstance(value,bool):
            return 1.0 if value else 0.0
        x=float(value)
        if x == x:
            return x
    except Exception:
        pass
    return None


def _v3_find_metric(obj,names,depth=0):
    if depth>4:
        return None

    wanted={
        str(x).strip().lower()
        for x in names
    }

    if isinstance(obj,dict):
        for key,value in obj.items():
            if str(key).strip().lower() in wanted:
                n=_v3_div_num(value)
                if n is not None:
                    return n

        for value in obj.values():
            if isinstance(value,(dict,list,tuple)):
                found=_v3_find_metric(
                    value,
                    wanted,
                    depth+1,
                )
                if found is not None:
                    return found

    elif isinstance(obj,(list,tuple)):
        for value in obj:
            found=_v3_find_metric(
                value,
                wanted,
                depth+1,
            )
            if found is not None:
                return found

    return None


def _v3_row_version(row):
    if not isinstance(row,dict):
        return ""

    return str(
        row.get("strategy_version")
        or row.get("exact_strategy_version")
        or (
            (row.get("evidence") or {}).get(
                "strategy_version"
            )
            if isinstance(
                row.get("evidence"),
                dict,
            )
            else ""
        )
        or ""
    ).strip()


def _v3_strategy_family(row):
    if not isinstance(row,dict):
        return ""

    evidence=(
        row.get("evidence")
        if isinstance(
            row.get("evidence"),
            dict,
        )
        else {}
    )

    components=(
        row.get("components")
        or evidence.get("components")
    )

    if isinstance(components,(list,tuple)):
        parts=sorted(
            str(x).strip().upper()
            for x in components
            if str(x).strip()
        )

        if parts:
            return "COMBO:"+"+".join(parts)

    declared=(
        row.get("strategy_family")
        or row.get("family")
        or evidence.get("strategy_family")
        or evidence.get("family")
        or evidence.get("family_file")
    )

    if declared:
        return str(declared).strip().upper()

    strategy=str(
        row.get("strategy")
        or evidence.get("strategy")
        or ""
    ).strip().upper()

    return strategy


def _v3_pair_metric(a,b,names):
    if not isinstance(a,dict) or not isinstance(b,dict):
        return None

    peer_ids={
        _v3_row_version(b).upper(),
        str(b.get("strategy") or "").strip().upper(),
    }

    peer_ids={
        x for x in peer_ids
        if x
    }

    wanted={
        str(x).strip().lower()
        for x in names
    }

    sources=[
        a,
        (
            a.get("evidence")
            if isinstance(a.get("evidence"),dict)
            else {}
        ),
    ]

    containers=(
        "pairwise_metrics",
        "diversity_pairs",
        "portfolio_diversity",
        "correlations",
        "correlation_by_strategy",
        "correlation_by_version",
        "entry_overlap_by_strategy",
        "entry_overlap_by_version",
        "dd_overlap_by_strategy",
        "dd_overlap_by_version",
    )

    for source in sources:
        if not isinstance(source,dict):
            continue

        for cname in containers:
            container=source.get(cname)

            if not isinstance(container,dict):
                continue

            # Peer keyed row.
            for key,value in container.items():
                if str(key).strip().upper() not in peer_ids:
                    continue

                if isinstance(value,dict):
                    n=_v3_find_metric(
                        value,
                        wanted,
                    )
                    if n is not None:
                        return n

                else:
                    n=_v3_div_num(value)

                    if n is not None and (
                        cname.lower() in wanted
                        or any(
                            x in cname.lower()
                            for x in wanted
                        )
                    ):
                        return n

            # Metric keyed -> peer map.
            for metric_key,value in container.items():
                if str(metric_key).strip().lower() not in wanted:
                    continue

                if not isinstance(value,dict):
                    continue

                for peer_key,peer_value in value.items():
                    if str(peer_key).strip().upper() in peer_ids:
                        n=_v3_div_num(peer_value)
                        if n is not None:
                            return n

    return None


def _v3_entry_priority_metrics(evidence):
    evidence=(
        evidence
        if isinstance(evidence,dict)
        else {}
    )

    return {
        "walk_forward_oos":
            _v3_find_metric(
                evidence,
                (
                    "walk_forward_avg_net_pct",
                    "wf_oos_expectancy",
                    "walk_forward_expectancy",
                    "expected_return",
                ),
            ),

        "cost_stress_2x":
            _v3_find_metric(
                evidence,
                (
                    "cost_stress_2x_net_pct",
                    "cost_stress_2x_avg_net_pct",
                    "stress_2x_avg_net_pct",
                    "cost_2x_expectancy",
                ),
            ),

        "mdd":
            _v3_find_metric(
                evidence,
                (
                    "mdd_pct",
                    "max_drawdown_pct",
                    "maximum_drawdown_pct",
                    "mdd",
                ),
            ),

        "robustness":
            _v3_find_metric(
                evidence,
                (
                    "robustness_score",
                    "robust_score",
                    "robustness",
                ),
            ),

        "dd_duration":
            _v3_find_metric(
                evidence,
                (
                    "dd_duration",
                    "drawdown_duration",
                    "max_drawdown_duration",
                    "drawdown_duration_hours",
                ),
            ),
    }


def _v3_tier_diversity_gate(
    candidates,
    ranking,
    strategy_version,
    target,
    evidence,
):
    target=str(target or "").upper()

    if target not in {"SMALL","NORMAL"}:
        return {
            "ok":True,
            "reason":"DIVERSITY_GATE_NOT_REQUIRED",
            "target":target,
        }

    policy=(
        {
            "quality_weight":0.70,
            "diversity_weight":0.30,
            "same_family_max":2,
            "correlation_limit":0.80,
            "entry_overlap_limit":0.75,
            "dd_overlap_limit":0.70,
            "capacity_max":5,
        }
        if target=="SMALL"
        else
        {
            "quality_weight":0.60,
            "diversity_weight":0.40,
            "same_family_max":1,
            "correlation_limit":0.70,
            "entry_overlap_limit":0.65,
            "dd_overlap_limit":0.60,
            "capacity_max":3,
        }
    )

    rows=[
        x for x in (candidates or [])
        if isinstance(x,dict)
    ]

    current=None

    for row in rows:
        if (
            _v3_row_version(row)
            == str(strategy_version or "")
        ):
            current=row
            break

    if current is None:
        current={
            "strategy_version":
                str(strategy_version or ""),
            "evidence":
                evidence
                if isinstance(evidence,dict)
                else {},
        }

    incumbents=[
        row
        for row in rows
        if str(
            row.get("status")
            or row.get("tier")
            or ""
        ).upper()==target
        and _v3_row_version(row)
            != str(strategy_version or "")
    ]

    current_family=_v3_strategy_family(
        current
    )

    same_family=sum(
        1
        for peer in incumbents
        if current_family
        and _v3_strategy_family(peer)
            ==current_family
    )

    reasons=[]

    if (
        current_family
        and same_family
            >= int(policy["same_family_max"])
    ):
        reasons.append(
            "SAME_FAMILY_LIMIT_EXCEEDED"
        )

    pair_rows=[]

    for peer in incumbents:
        corr=_v3_pair_metric(
            current,
            peer,
            (
                "pnl_correlation",
                "return_correlation",
                "correlation",
            ),
        )

        entry_overlap=_v3_pair_metric(
            current,
            peer,
            (
                "entry_overlap",
                "entry_overlap_ratio",
                "signal_overlap",
            ),
        )

        dd_overlap=_v3_pair_metric(
            current,
            peer,
            (
                "dd_overlap",
                "drawdown_overlap",
                "simultaneous_dd_overlap",
            ),
        )

        if (
            corr is not None
            and abs(float(corr))
                > float(
                    policy["correlation_limit"]
                )
        ):
            reasons.append(
                "PAIRWISE_CORRELATION_LIMIT_EXCEEDED"
            )

        if (
            entry_overlap is not None
            and float(entry_overlap)
                > float(
                    policy["entry_overlap_limit"]
                )
        ):
            reasons.append(
                "ENTRY_OVERLAP_LIMIT_EXCEEDED"
            )

        if (
            dd_overlap is not None
            and float(dd_overlap)
                > float(
                    policy["dd_overlap_limit"]
                )
        ):
            reasons.append(
                "DD_OVERLAP_LIMIT_EXCEEDED"
            )

        components=[]

        if corr is not None:
            components.append(
                max(
                    0.0,
                    1.0-min(
                        1.0,
                        abs(float(corr)),
                    ),
                )
            )

        if entry_overlap is not None:
            components.append(
                max(
                    0.0,
                    1.0-min(
                        1.0,
                        float(entry_overlap),
                    ),
                )
            )

        if dd_overlap is not None:
            components.append(
                max(
                    0.0,
                    1.0-min(
                        1.0,
                        float(dd_overlap),
                    ),
                )
            )

        family_diverse=(
            not current_family
            or _v3_strategy_family(peer)
                !=current_family
        )

        components.append(
            1.0 if family_diverse else 0.0
        )

        pair_rows.append({
            "peer_strategy":
                str(peer.get("strategy") or ""),
            "peer_strategy_version":
                _v3_row_version(peer),
            "peer_family":
                _v3_strategy_family(peer),
            "pnl_correlation":
                corr,
            "entry_overlap":
                entry_overlap,
            "dd_overlap":
                dd_overlap,
            "diversity_score":
                (
                    sum(components)
                    /len(components)
                    if components
                    else 0.5
                ),
        })

    # Existing capital ranking defines quality;
    # do not invent a second profitability model.
    quality_score=0.5

    ranked=[
        x for x in (ranking or [])
        if isinstance(x,dict)
    ]

    if ranked:
        for idx,row in enumerate(ranked):
            if (
                _v3_row_version(row)
                ==str(strategy_version or "")
            ):
                quality_score=(
                    1.0
                    if len(ranked)==1
                    else
                    1.0
                    -(
                        float(idx)
                        /float(len(ranked)-1)
                    )
                )
                break

    diversity_score=(
        sum(
            float(x["diversity_score"])
            for x in pair_rows
        )/len(pair_rows)
        if pair_rows
        else 1.0
    )

    portfolio_score=(
        float(policy["quality_weight"])
        *quality_score
        +
        float(policy["diversity_weight"])
        *diversity_score
    )

    return {
        "ok":
            not reasons,

        "reason":
            (
                "PASS"
                if not reasons
                else reasons[0]
            ),

        "reasons":
            list(dict.fromkeys(reasons)),

        "target":
            target,

        "quality_weight":
            policy["quality_weight"],

        "diversity_weight":
            policy["diversity_weight"],

        "quality_score":
            quality_score,

        "diversity_score":
            diversity_score,

        "portfolio_score":
            portfolio_score,

        "candidate_family":
            current_family,

        "same_family_existing_count":
            same_family,

        "same_family_max":
            policy["same_family_max"],

        "correlation_limit":
            policy["correlation_limit"],

        "entry_overlap_limit":
            policy["entry_overlap_limit"],

        "dd_overlap_limit":
            policy["dd_overlap_limit"],

        "capacity_max":
            policy["capacity_max"],

        "force_fill_capacity":
            False,

        "pairwise":
            pair_rows,

        "missing_pair_metrics_are_fabricated":
            False,
    }


def next_tier_request(tf):
    """
    Existing REAL Canary evidence remains authoritative for
    CANARY -> SMALL -> NORMAL.

    GREEN7A_CAPITAL_PIPELINE_V1

    Positive scaling order:
      REAL evidence
      -> minimal Shadow safety
      -> Strategy Capital Ranking
      -> Portfolio Allocation Gate
      -> Capital Scale Gate
      -> runtime-sync request

    Rollback reduces exposure and bypasses every positive-scale
    ranking/allocation/scale requirement.
    """

    from v3_strategy_capital_ranking import (
        rank_strategies,
    )

    from v3_portfolio_allocation_gate import (
        evaluate_portfolio_allocation,
    )

    from v3_capital_scale_gate import (
        evaluate_capital_scale_gate,
    )

    tf = str(
        tf
        or ""
    ).upper()

    try:
        profile_state=state()
        last_profile_ts=_parse_iso_epoch(profile_state.get("live_strategy_profile_last_update_at"))
        current_profile_ts=int(time.time())
        if (not last_profile_ts) or (current_profile_ts-last_profile_ts >= 86400):
            update_r4_strategy_profiles()
    except Exception:
        # Profile enrichment must never override or break the existing safety/tier path.
        pass

    s = state()

    fr = (
        (
            s.get(
                "frames"
            )
            or {}
        ).get(
            tf
        )
        or {}
    )

    tier = str(
        fr.get(
            "tier"
        )
        or ""
    ).upper()

    strategy = str(
        fr.get(
            "strategy"
        )
        or ""
    ).upper()

    strategy_version = str(
        fr.get(
            "strategy_version"
        )
        or ""
    )

    if (
        tier
        not in {
            "CANARY",
            "SMALL",
        }
        or not strategy
    ):
        return None

    cfg = load(
        CONFIG,
        {},
    )

    dep = (
        cfg.get(
            "deployment"
        )
        or {}
    )

    since = int(
        fr.get(
            "activated_ts"
        )
        or 0
    )

    ev = canary_evidence(
        tf,
        strategy,
        since,
    )

    verdict = str(
        ev.get(
            "evidence_decision"
        )
        or "WAIT_MORE_REAL_EVIDENCE"
    ).upper()

    if verdict == "WAIT_MORE_REAL_EVIDENCE":
        return None

    target = (
        "ROLLBACK"
        if verdict == "ROLLBACK"
        else (
            "SMALL"
            if tier == "CANARY"
            else "NORMAL"
        )
    )

    # --------------------------------------------------------
    # Safety-reducing rollback bypass
    # --------------------------------------------------------

    if target != "ROLLBACK":

        # ----------------------------------------------------
        # Existing mandatory Shadow boundary
        # ----------------------------------------------------

        if not strategy_version:

            fr[
                "shadow_status"
            ] = "BLOCKED"

            fr[
                "shadow_block_reason"
            ] = (
                "SHADOW_REQUIRED_BEFORE_SCALE:"
                "STRATEGY_VERSION_MISSING"
            )

            s["updated_at"] = now()

            atomic(
                STATE,
                s,
            )

            return None

        if os.getenv(
            "BBOT_SERVER_TIER_CONTROLLER",
            "0",
        ).strip().lower() in {"1", "true", "yes", "on"}:
            # SERVER_EXACT_VERSION_TIER_SCALE_AUTOMATION_V1
            shadow_pass = (
                fr.get("shadow_status") == "PASS"
                and str(fr.get("strategy_version") or "") == strategy_version
            )
        else:
            shadow_pass = minimal_shadow_pass(
                strategy_version
            )

        if not shadow_pass:
            fr["capital_shadow_reference_status"] = (
                "REFERENCE_ONLY_NON_BLOCKING"
            )
            fr["capital_shadow_reference_reason"] = (
                "SHADOW_NOT_PASS"
            )

        # ----------------------------------------------------
        # GREEN-7A-1 Strategy Capital Ranking
        # ----------------------------------------------------

        account_mode = str(
            ev.get(
                "account_mode"
            )
            or fr.get(
                "account_mode"
            )
            or ""
        ).upper()

        candidates = []

        for frame_name, row in (
            s.get(
                "frames"
            )
            or {}
        ).items():

            if not isinstance(
                row,
                dict,
            ):
                continue

            row_tier = str(
                row.get("tier")
                or ""
            ).upper()

            row_strategy = str(
                row.get("strategy")
                or ""
            ).upper()

            row_version = str(
                row.get(
                    "strategy_version"
                )
                or ""
            )

            if (
                row_tier
                not in {
                    "CANARY",
                    "SMALL",
                    "NORMAL",
                }
                or not row_strategy
                or not row_version
            ):
                continue

            row_evidence = (
                ev
                if (
                    str(frame_name).upper() == tf
                    and row_version
                    == strategy_version
                )
                else (
                    row.get(
                        "last_evidence"
                    )
                    or {}
                )
            )
            row_evidence = dict(row_evidence) if isinstance(row_evidence,dict) else {}
            row_evidence.update(
                _strategy_instance_live_profile_evidence(
                    s,
                    str(frame_name).upper(),
                    row_strategy,
                    row_version,
                )
            )
            row_evidence["current_tier"] = row_tier

            candidates.append(
                {
                    "timeframe":
                        str(frame_name).upper(),

                    "strategy":
                        row_strategy,

                    "strategy_version":
                        row_version,

                    "status":
                        row_tier,

                    "target_tier":
                        target,

                    "evidence":
                        row_evidence,
                }
            )

        ranking = rank_strategies(
            candidates,
            account_mode,
            ev.get(
                "account_avg_equity_usdt_24h"
            ),
        )

        fr[
            "capital_ranking"
        ] = ranking

        if not ranking.get("ok"):
            fr["capital_ranking_reference_status"] = (
                "REFERENCE_ONLY_NON_BLOCKING"
            )
            fr["capital_ranking_reference_reason"] = str(
                ranking.get("reason")
                or "NOT_PASS"
            )

        # ----------------------------------------------------
        # GREEN-7A-2 Portfolio Allocation Gate
        # ----------------------------------------------------

        allocation = evaluate_portfolio_allocation(
            ranking,
            strategy_version,
            target,
            ev,
        )

        # TIER_PORTFOLIO_DIVERSITY_SELECTION_V1
        diversity_gate = _v3_tier_diversity_gate(
            candidates,
            ranking,
            strategy_version,
            target,
            ev,
        )

        fr[
            "tier_diversity_gate"
        ] = diversity_gate

        if (
            target in {"SMALL","NORMAL"}
            and not diversity_gate.get("ok")
        ):
            allocation = dict(
                allocation
                if isinstance(allocation,dict)
                else {}
            )

            allocation["ok"] = False
            allocation[
                "reason"
            ] = "TIER_DIVERSITY_GATE_BLOCK"

            allocation[
                "tier_diversity_gate"
            ] = diversity_gate

        fr[
            "portfolio_allocation_gate"
        ] = allocation

        # ----------------------------------------------------
        # R5 FRAME LEVERAGE EVIDENCE BINDING
        # ----------------------------------------------------
        # 기존 research/canary evidence에 exact Strategy Instance
        # live profile과 result_json의 검증 증거를 합친다.
        # 없는 증거는 생성하지 않는다.
        leverage_evidence = dict(
            ev
            if isinstance(ev, dict)
            else {}
        )

        # 1. exact Strategy Instance live profile
        live_profile_binding = (
            _strategy_instance_live_profile_evidence(
                s,
                tf,
                strategy,
                strategy_version,
            )
        )

        if isinstance(
            live_profile_binding,
            dict,
        ):
            leverage_evidence.update(
                live_profile_binding
            )

            profile = (
                live_profile_binding.get(
                    "strategy_instance_live_profile"
                )
            )

            if isinstance(
                profile,
                dict,
            ):
                leverage_evidence[
                    "live_strategy_profile"
                ] = json.loads(
                    json.dumps(profile)
                )

        # R5 CURRENT PIPELINE RESEARCH BINDING V1
        # Exact timeframe + strategy + Strategy Version only.
        # Ambiguous/missing pipeline evidence remains unbound.
        pipeline_row = None
        pipeline_path = (
            Path(__file__).resolve().parent
            / "results"
            / "v3"
            / "historical_314_pipeline_stage2.json"
        )

        try:
            if pipeline_path.is_file():
                pipeline_state = json.loads(
                    pipeline_path.read_text(
                        encoding="utf-8-sig"
                    )
                )
                pipeline_matches = [
                    row
                    for row in (
                        pipeline_state.get("entries") or []
                    )
                    if isinstance(row, dict)
                    and str(
                        row.get("timeframe") or ""
                    ).upper() == str(tf or "").upper()
                    and str(
                        row.get("strategy_id") or ""
                    ).upper() == str(
                        strategy or ""
                    ).upper()
                    and str(
                        row.get("exact_strategy_version")
                        or row.get("strategy_version")
                        or ""
                    ) == str(strategy_version or "")
                ]

                if len(pipeline_matches) == 1:
                    pipeline_row = pipeline_matches[0]
        except Exception:
            pipeline_row = None

        if isinstance(pipeline_row, dict):
            leverage_evidence[
                "research_pipeline_stage"
            ] = pipeline_row.get("pipeline_stage")

            leverage_evidence[
                "research_pipeline_exact_strategy_version"
            ] = pipeline_row.get(
                "exact_strategy_version"
            )

            for key in (
                "hard_pass_recheck",
                "entry_risk_gate",
                "final_risk_gate",
            ):
                value = pipeline_row.get(key)
                if isinstance(value, dict):
                    leverage_evidence[key] = json.loads(
                        json.dumps(value)
                    )

            pipeline_result_json = str(
                pipeline_row.get(
                    "final_strategy_result_json"
                )
                or pipeline_row.get("new_result_json")
                or ""
            ).strip()

            if pipeline_result_json:
                leverage_evidence[
                    "result_json"
                ] = pipeline_result_json

            leverage_evidence[
                "leverage_research_pipeline_bound"
            ] = True

        # 2. existing research result_json
        result_json = str(
            leverage_evidence.get(
                "result_json"
            )
            or ev.get(
                "result_json"
            )
            or ""
        ).strip()

        if result_json:

            result_path = Path(
                result_json
            )

            if not result_path.is_absolute():
                result_path = ROOT / result_path

            if result_path.is_file():

                try:
                    research_result = json.loads(
                        result_path.read_text(
                            encoding="utf-8-sig"
                        )
                    )
                except Exception:
                    research_result = {}

                if isinstance(
                    research_result,
                    dict,
                ):

                    # 최상위 evidence/metrics/evaluation을
                    # 기존 구조 그대로 재사용한다.
                    for key in (
                        "evidence",
                        "metrics",
                        "evaluation",
                        "final_risk_gate",
                        "walk_forward",
                        "cost_stress_2x",
                        "final_risk",
                        "baseline_risk",
                    ):

                        value = research_result.get(
                            key
                        )

                        if isinstance(
                            value,
                            dict,
                        ):
                            leverage_evidence[
                                key
                            ] = json.loads(
                                json.dumps(value)
                            )

                    # result 자체에 직접 존재하는 검증 필드도 보존
                    for key in (
                        "walk_forward_oos_expectancy_pct",
                        "wf_oos_expectancy",
                        "cost_stress_2x_expectancy_pct",
                        "cost_stress_2x_expectancy",
                        "strategy_version",
                        "timeframe",
                    ):

                        if key in research_result:
                            leverage_evidence[
                                key
                            ] = research_result[
                                key
                            ]

                    leverage_evidence[
                        "leverage_research_result_bound"
                    ] = True

                    leverage_evidence[
                        "leverage_research_result_path"
                    ] = str(
                        result_path
                    )

        # R5 PIPELINE HARD-PASS CANONICAL ROBUSTNESS V1
        if isinstance(pipeline_row, dict):
            hard_pass_recheck = pipeline_row.get(
                "hard_pass_recheck"
            )

            if isinstance(hard_pass_recheck, dict):
                pipeline_wf = hard_pass_recheck.get(
                    "walk_forward_avg_net_pct"
                )
                pipeline_stress = hard_pass_recheck.get(
                    "walk_forward_cost_stress_2x_avg_net_pct"
                )

                if pipeline_wf is not None:
                    leverage_evidence[
                        "walk_forward_oos_expectancy_pct"
                    ] = pipeline_wf

                if pipeline_stress is not None:
                    leverage_evidence[
                        "cost_stress_2x_expectancy_pct"
                    ] = pipeline_stress

        fr[
            "leverage_evidence_binding"
        ] = {
            "strategy_instance_id":
                live_profile_binding.get(
                    "strategy_instance_id"
                )
                if isinstance(
                    live_profile_binding,
                    dict,
                )
                else None,
            "strategy_version":
                strategy_version,
            "timeframe":
                tf,
            "research_result_bound":
                bool(
                    leverage_evidence.get(
                        "leverage_research_result_bound"
                    )
                ),
            "live_profile_bound":
                bool(
                    leverage_evidence.get(
                        "strategy_instance_live_profile"
                    )
                ),
        }

        fr[
            "leverage_absolute_evaluation"
        ] = _frame_absolute_leverage_evaluation(
            leverage_evidence,
            allocation,
        )

        if not allocation.get("ok"):

            fr[
                "capital_scale_status"
            ] = "BLOCKED"

            fr[
                "capital_scale_block_reason"
            ] = (
                "PORTFOLIO_ALLOCATION_GATE:"
                + str(
                    allocation.get(
                        "reason"
                    )
                    or "BLOCK"
                )
            )

            s["updated_at"] = now()

            atomic(
                STATE,
                s,
            )

            return None

        # ----------------------------------------------------
        # GREEN-7A-3 Capital Scale Gate
        # ----------------------------------------------------

        scale_gate = evaluate_capital_scale_gate(
            tier,
            target,
            strategy_version,
            ev,
            ranking,
            allocation,
            True,
            PUSH_HOLD.exists(),
        )

        fr[
            "capital_scale_gate"
        ] = scale_gate

        if not scale_gate.get("ok"):

            fr[
                "capital_scale_status"
            ] = "BLOCKED"

            fr[
                "capital_scale_block_reason"
            ] = str(
                scale_gate.get(
                    "reason"
                )
                or "CAPITAL_SCALE_GATE_BLOCK"
            )

            s["updated_at"] = now()

            atomic(
                STATE,
                s,
            )

            return None

        fr[
            "capital_scale_status"
        ] = "PASS"

        fr.pop(
            "capital_scale_block_reason",
            None,
        )

    new = json.loads(
        json.dumps(
            cfg
        )
    )

    f = new[
        "frames"
    ][tf]

    if target == "ROLLBACK":

        if fr.get(
            "revalidation_canary_retest"
        ) is True:

            prev = (
                fr.get(
                    "revalidation_rollback_frame"
                )
                or {}
            )

            if not isinstance(
                prev,
                dict,
            ) or not prev:

                raise RuntimeError(
                    "REVALIDATION_ROLLBACK_FRAME_MISSING"
                )

            if prev.get(
                "enabled"
            ) is not False:

                raise RuntimeError(
                    "REVALIDATION_ROLLBACK_MUST_REDUCE_EXPOSURE"
                )

            try:
                rollback_weight = float(
                    prev.get(
                        "capital_weight",
                        0.0,
                    )
                    or 0.0
                )
            except Exception:
                raise RuntimeError(
                    "REVALIDATION_ROLLBACK_WEIGHT_INVALID"
                )

            if rollback_weight > 0.0:
                raise RuntimeError(
                    "REVALIDATION_ROLLBACK_POSITIVE_WEIGHT_FORBIDDEN"
                )

            reason = (
                "REVALIDATION_CANARY_RETEST_FAILED_OFF"
            )

        else:

            prev = (
                fr.get(
                    "previous_frame_config"
                )
                or {}
            )

            reason = (
                "REAL_EVIDENCE_ROLLBACK"
            )

        new[
            "frames"
        ][tf] = prev

    else:

        f[
            "deployment_tier"
        ] = target

        # RUNNER_PRIORITY_EVIDENCE_V1
        f[
            "entry_priority_metrics"
        ] = _v3_entry_priority_metrics(
            ev
        )

        f[
            "strategy_family"
        ] = _v3_strategy_family({
            "strategy":strategy,
            "evidence":ev,
        })

        f[
            "tier_diversity_policy_version"
        ] = "TIER_PORTFOLIO_DIVERSITY_SELECTION_V1"

        f[
            "status"
        ] = (
            "VALIDATED_MAIN"
            if target == "NORMAL"
            else "PROVISIONAL_MAIN"
        )

        base_weight = float(
            dep.get(
                "small_weight",
                0.10,
            )
            if target == "SMALL"
            else dep.get(
                "normal_weight",
                1.0,
            )
        )

        allocation_row = (
            fr.get(
                "portfolio_allocation_gate"
            )
            or {}
        )

        allocation_multiplier = float(
            allocation_row.get(
                "weight_multiplier",
                0.0,
            )
            or 0.0
        )

        boost_multiplier = float(
            allocation_row.get(
                "capital_boost_multiplier",
                1.0,
            )
            or 1.0
        )

        hard_max_multiplier = {
            "CANARY": 2.20,
            "SMALL": 3.00,
            "NORMAL": 3.50,
        }.get(target, 1.0)

        if (
            boost_multiplier < 1.0
            or boost_multiplier > hard_max_multiplier
        ):
            raise RuntimeError(
                "ADAPTIVE_CAPITAL_BOOST_OUT_OF_BOUNDS"
            )

        f[
            "capital_base_weight"
        ] = base_weight

        f[
            "adaptive_capital_boost_multiplier"
        ] = boost_multiplier

        f[
            "adaptive_capital_boost_policy"
        ] = (
            "SUB_100M_AGGRESSIVE_GROWTH_"
            "ADAPTIVE_CAPITAL_BOOST_V1"
        )

        leverage_eval = (
            fr.get("leverage_absolute_evaluation")
            if isinstance(fr.get("leverage_absolute_evaluation"), dict)
            else {}
        )
        f[
            "leverage_absolute_evaluation"
        ] = json.loads(json.dumps(leverage_eval))
        f[
            "evaluated_leverage_x"
        ] = int(leverage_eval.get("final_leverage_x") or 10)

        f[
            "capital_weight"
        ] = max(
            0.0,
            min(
                base_weight * hard_max_multiplier,
                base_weight
                * allocation_multiplier
                * boost_multiplier,
            ),
        )

        if f[
            "capital_weight"
        ] <= 0.0:

            fr[
                "capital_scale_status"
            ] = "BLOCKED"

            fr[
                "capital_scale_block_reason"
            ] = (
                "PORTFOLIO_ALLOCATION_ZERO_WEIGHT"
            )

            s["updated_at"] = now()

            atomic(
                STATE,
                s,
            )

            return None

        reason = (
            f"AUTO_GREEN7A_SCALE_"
            f"{tier}_TO_{target}"
        )

    cfgp = (
        RES
        / (
            f"strategy_config_pending_"
            f"{tf.lower()}_"
            f"{target.lower()}.json"
        )
    )

    atomic(
        cfgp,
        new,
    )

    rid = (
        f"V3_{tf}_"
        f"{strategy}_"
        f"{target}_"
        f"{int(time.time())}"
    )

    strategy_path = (
        DEPLOY
        / (
            strategy.lower()
            + ".py"
        )
    )

    req = {
        "schema":
            "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",

        "request_id":
            rid,

        "created_at":
            now(),

        "reason":
            reason,

        "version_kind":
            "STRATEGY",

        "timeframe":
            tf,

        "strategy":
            strategy,

        "strategy_version":
            strategy_version,

        "policy_versions":
            new.get(
                "policy_versions"
            )
            or {},

        "tier":
            target,

        "strategy_file":
            (
                str(
                    strategy_path.relative_to(
                        ROOT
                    )
                )
                if (
                    target != "ROLLBACK"
                    and strategy_path.exists()
                )
                else ""
            ),

        "strategy_config_file":
            str(
                cfgp.relative_to(
                    ROOT
                )
            ),

        "autonomous_rule_passed":
            True,

        "core_mutation":
            False,

        "real_evidence":
            ev,

        "rollback_target":
            "PREVIOUS_VALIDATED_VERSION",
    }

    if target != "ROLLBACK":

        req[
            "shadow_gate"
        ] = "PASS"

        req[
            "shadow_gate_version_id"
        ] = strategy_version

        req[
            "shadow_passed_at"
        ] = now()

        req[
            "strategy_capital_ranking"
        ] = fr.get(
            "capital_ranking"
        )

        req[
            "portfolio_allocation_gate"
        ] = fr.get(
            "portfolio_allocation_gate"
        )

        req[
            "capital_scale_gate"
        ] = fr.get(
            "capital_scale_gate"
        )

        req[
            "green7a_pipeline"
        ] = (
            "GREEN7A_CAPITAL_PIPELINE_V1"
        )

    atomic(
        REQUEST,
        req,
    )

    fr[
        "tier"
    ] = (
        "PENDING_"
        + target
    )

    fr[
        "request_id"
    ] = rid

    fr[
        "last_evidence"
    ] = ev

    if target != "ROLLBACK":

        fr[
            "shadow_status"
        ] = "PASS"

        fr[
            "shadow_passed_at"
        ] = now()

        fr.pop(
            "shadow_block_reason",
            None,
        )

    s["updated_at"] = now()

    atomic(
        STATE,
        s,
    )

    return req


def mark_applied(req):
    s = state()

    tf = str(
        req.get("timeframe")
        or ""
    ).upper()

    rid = str(
        req.get("request_id")
        or ""
    )

    tier = str(
        req.get("tier")
        or ""
    ).upper()

    kind = str(
        req.get("version_kind")
        or "STRATEGY"
    ).upper()

    fr = (
        s.setdefault(
            "frames",
            {},
        ).setdefault(
            tf,
            {},
        )
        if tf
        else {}
    )

    # --------------------------------------------------------
    # Strategy Version
    # --------------------------------------------------------

    if kind == "STRATEGY":
        version_id = str(
            req.get(
                "strategy_version"
            )
            or ""
        )

        request_instance_id = str(
            req.get("strategy_instance_id")
            or ""
        ).strip()

        current_frame_tier = str(
            fr.get("tier")
            or fr.get("qualification_status")
            or ""
        ).upper()

        current_frame_instance_id = str(
            fr.get("strategy_instance_id")
            or ""
        ).strip()

        peer_strategy_apply = (
            tier != "ROLLBACK"
            and bool(request_instance_id)
            and current_frame_tier in {
                "CANARY",
                "SMALL",
                "NORMAL",
            }
            and bool(current_frame_instance_id)
            and current_frame_instance_id
                != request_instance_id
        )

        if tier == "ROLLBACK":
            fr.update(
                {
                    "tier": "ROLLED_BACK",
                    "request_id": rid,
                    "applied_at": now(),
                    "activated_ts": int(
                        time.time()
                    ),
                }
            )

            if version_id:
                try:
                    governance.set_version_status(
                        version_id,
                        "ROLLED_BACK",
                        req.get(
                            "real_evidence"
                        )
                        or {},
                    )
                except Exception:
                    pass

        else:
            reconcile_existing = (
                req.get(
                    "reconcile_existing_canary"
                )
                is True
            )

            if reconcile_existing:
                preserve_ts = int(
                    req.get(
                        "reconcile_preserve_activated_ts"
                    )
                    or 0
                )

                current_tier = str(
                    fr.get("tier")
                    or ""
                ).upper()

                current_strategy = str(
                    fr.get("strategy")
                    or ""
                ).upper()

                current_version = str(
                    fr.get(
                        "strategy_version"
                    )
                    or ""
                )

                current_ts = int(
                    fr.get(
                        "activated_ts"
                    )
                    or 0
                )

                request_strategy = str(
                    req.get("strategy")
                    or ""
                ).upper()

                if (
                    tier != "CANARY"
                    or preserve_ts <= 0
                    or current_tier != "CANARY"
                    or current_strategy != request_strategy
                    or current_version != version_id
                    or current_ts != preserve_ts
                ):
                    raise RuntimeError(
                        "EXISTING_CANARY_RECONCILE_STATE_BINDING_FAIL"
                    )

                # Preserve original request_id,
                # applied_at and activated_ts.
                fr[
                    "last_reconcile_request_id"
                ] = rid

                fr[
                    "last_reconciled_at"
                ] = now()

            else:
                applied_strategies = (
                    req.get(
                        "strategies"
                    )
                    if isinstance(
                        req.get(
                            "strategies"
                        ),
                        list,
                    )
                    else (
                        fr.get(
                            "strategies"
                        )
                        or []
                    )
                )

                if not applied_strategies:
                    request_strategy = str(
                        req.get(
                            "strategy"
                        )
                        or ""
                    ).upper()

                    if request_strategy:
                        applied_strategies = [
                            request_strategy
                        ]

                applied_strategies = [
                    str(x).upper()
                    for x in applied_strategies
                    if str(x).strip()
                ]

                if not peer_strategy_apply:
                    fr.update(
                        {
                            "strategy": str(
                                req.get(
                                    "strategy"
                                )
                                or ""
                            ).upper(),
                            "strategies":
                                applied_strategies,
                            "strategy_version": version_id,
                            "strategy_instance_id":
                                request_instance_id,
                            "qualification_status":
                                tier,
                            "tier": tier,
                            "request_id": rid,
                            "applied_at": now(),
                            "activated_ts": int(
                                time.time()
                            ),
                        }
                    )

            status = {
                "CANARY": "CANARY",
                "SMALL": "SMALL",
                "NORMAL": "NORMAL",
            }.get(tier)

            if (
                version_id
                and status
                and not reconcile_existing
            ):
                governance.set_version_status(
                    version_id,
                    status,
                    req.get(
                        "real_evidence"
                    )
                    or {},
                )

                if (
                    req.get(
                        "revalidation_canary_retest"
                    )
                    is True
                    and tier == "CANARY"
                ):
                    governance.record_strategy_career_event(
                        version_id,
                        "CANARY",
                        {
                            "timeframe":
                                tf,

                            "capital_weight":
                                req.get(
                                    "canary_capital_weight",
                                    0.02,
                                ),

                            "source":
                                "NORMAL_REVALIDATION_CANARY_RETEST",

                            "request_id":
                                rid,

                            "revalidation_event_id":
                                req.get(
                                    "revalidation_event_id"
                                ),
                        },
                        event_id=(
                            "REVALIDATION_CANARY_APPLIED:"
                            + rid
                        ),
                    )

                    governance.update_strategy_passport_summary(
                        version_id,
                        {
                            "current_qualification":
                                "CANARY",

                            "target_qualification":
                                "CANARY",

                            "deployment_tier":
                                "CANARY",

                            "capital_weight":
                                req.get(
                                    "canary_capital_weight",
                                    0.02,
                                ),

                            "revalidation_required":
                                True,

                            "revalidation_status":
                                "CANARY_RETEST_ACTIVE",

                            "last_revalidation_request_id":
                                rid,
                        },
                    )

                    fr[
                        "last_evidence"
                    ] = (
                        req.get(
                            "real_evidence"
                        )
                        or {}
                    )

        # MULTI_CANARY_EXACT_STRATEGY_INSTANCE_APPLIED_V1
        if (
            tier != "ROLLBACK"
            and request_instance_id
        ):
            applied_ts = int(
                time.time()
            )

            instance_row = (
                s.setdefault(
                    "strategy_instances",
                    {},
                ).setdefault(
                    request_instance_id,
                    {},
                )
            )

            instance_row.update(
                {
                    "strategy_instance_id":
                        request_instance_id,

                    "strategy":
                        str(
                            req.get("strategy")
                            or instance_row.get("strategy")
                            or ""
                        ).upper(),

                    "timeframe":
                        tf,

                    "strategy_version":
                        version_id,

                    "tier":
                        tier,

                    "qualification_status":
                        tier,

                    "request_id":
                        rid,

                    "applied_at":
                        now(),

                    "activated_ts":
                        applied_ts,

                    "shadow_status":
                        str(
                            req.get("shadow_gate")
                            or instance_row.get("shadow_status")
                            or ""
                        ).upper(),

                    "approved_for_server_release":
                        bool(
                            req.get(
                                "approved_for_server_release",
                                True,
                            )
                        ),
                }
            )

    # --------------------------------------------------------
    # Policy Version
    # --------------------------------------------------------

    elif kind == "POLICY":
        track = str(
            req.get(
                "policy_track"
            )
            or ""
        ).upper()

        version_id = str(
            req.get(
                "policy_version"
            )
            or ""
        )

        policy_state = (
            s.setdefault(
                "policies",
                {},
            ).setdefault(
                track,
                {},
            )
        )

        status = {
            "CANARY": "CANARY",
            "SMALL": "SMALL",
            "NORMAL": "NORMAL",
            "ROLLBACK": "ROLLED_BACK",
        }.get(tier)

        if tier == "ROLLBACK":
            policy_state.update(
                {
                    "tier": "ROLLED_BACK",
                    "rolled_back_version": version_id,
                    "policy_version": req.get(
                        "previous_policy_version"
                    )
                    or "",
                    "request_id": rid,
                    "applied_at": now(),
                    "activated_ts": int(
                        time.time()
                    ),
                    "last_evidence": req.get(
                        "real_evidence"
                    )
                    or {},
                }
            )

        else:
            policy_state.update(
                {
                    "policy_track": track,
                    "policy_version": version_id,
                    "tier": tier,
                    "request_id": rid,
                    "applied_at": now(),
                    "activated_ts": int(
                        time.time()
                    ),
                    "previous_policy_version": (
                        req.get(
                            "previous_policy_version"
                        )
                        if req.get(
                            "previous_policy_version"
                        )
                        is not None
                        else policy_state.get(
                            "previous_policy_version"
                        )
                    ),
                    "previous_policy_config": (
                        req.get(
                            "previous_policy_config"
                        )
                        if isinstance(
                            req.get(
                                "previous_policy_config"
                            ),
                            dict,
                        )
                        else policy_state.get(
                            "previous_policy_config"
                        )
                        or {}
                    ),
                    "last_evidence": req.get(
                        "real_evidence"
                    )
                    or {},
                }
            )

        if version_id and status:
            governance.set_version_status(
                version_id,
                status,
                req.get(
                    "real_evidence"
                )
                or {},
            )

    s.setdefault(
        "applied_request_ids",
        [],
    ).append(rid)

    s["applied_request_ids"] = (
        s["applied_request_ids"][-200:]
    )

    s["updated_at"] = now()

    atomic(
        STATE,
        s,
    )

def policy_canary_evidence(version_id, since_ts=0):
    """
    exit_history? ?? ??? owner metadata? ??
    ?? Policy Version? ??? ?? ??? ????.
    """
    reg = governance.version_registry()

    version = (
        (reg.get("policy_versions") or {})
        .get(str(version_id))
    )

    if not isinstance(version, dict):
        raise RuntimeError("POLICY_VERSION_NOT_FOUND")

    track = str(
        version.get("track") or ""
    ).upper()

    result = {
        "policy_version": str(version_id),
        "policy_track": track,
        "closed_trades": 0,
        "roi_samples": 0,
        "avg_roi": None,
        "roi_95ci_lower": None,
        "roi_95ci_upper": None,
        "hard_fail_count": 0,
        "last_exit_ts": 0,
        "avg_hold_hours": None,
        "evidence_decision": "WAIT_MORE_REAL_EVIDENCE",
        "attribution_rule": "POSITION_OWNER_POLICY_VERSION",
        "sufficiency_rule": "95CI_NOT_FIXED_TRADE_COUNT_OR_DAYS",
    }

    if not RUNTIME_DB.is_file():
        return result

    con = sqlite3.connect(
        str(RUNTIME_DB),
        timeout=10,
    )
    con.row_factory = sqlite3.Row

    try:
        cols = {
            str(r[1])
            for r in con.execute(
                "PRAGMA table_info(exit_history)"
            ).fetchall()
        }

        if "payload_json" not in cols:
            return result

        wanted = [
            name
            for name in (
                "ts",
                "exit_ts",
                "roi",
                "pnl",
                "hold_sec",
                "holding_seconds",
                "reason",
                "exit_reason",
                "exit_type",
                "exit_price",
                "qty",
                "old_side",
                "payload_json",
            )
            if name in cols
        ]

        if not wanted:
            return result

        rows = [
            dict(r)
            for r in con.execute(
                "SELECT "
                + ",".join(wanted)
                + " FROM exit_history ORDER BY "
                + (
                    "exit_ts"
                    if "exit_ts" in cols
                    else "ts"
                )
            ).fetchall()
        ]

    finally:
        con.close()

    vals = []
    hold_seconds = []
    hard = []
    last_exit_ts = 0

    for row in rows:
        ts = int(
            row.get("exit_ts")
            or row.get("ts")
            or 0
        )

        if ts < int(since_ts or 0):
            continue

        try:
            payload = json.loads(
                row.get("payload_json")
                or "{}"
            )
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue

        position = payload.get("position")

        if not isinstance(position, dict):
            continue

        # PURE_CANARY_EXCLUDES_MIXED
        if bool(
            position.get(
                "mixed_version_lifecycle",
                False,
            )
        ):
            continue

        policy_versions = position.get(
            "policy_versions"
        )

        if not isinstance(policy_versions, dict):
            snapshot = position.get(
                "policy_snapshot"
            )

            if isinstance(snapshot, dict):
                policy_versions = snapshot.get(
                    "policy_versions"
                )

        if not isinstance(policy_versions, dict):
            continue

        if str(
            policy_versions.get(track)
            or ""
        ) != str(version_id):
            continue

        roi = row.get("roi")

        if roi is None:
            lifecycle = payload.get(
                "position_lifecycle"
            )

            if isinstance(lifecycle, dict):
                roi = lifecycle.get(
                    "current_roi"
                )

        if roi is None:
            entry_price = (
                position.get("entryPrice")
                or position.get("entry_price")
            )
            exit_price = row.get(
                "exit_price"
            )
            side = str(
                row.get("old_side")
                or position.get("side")
                or ""
            ).upper()

            try:
                entry_price = float(entry_price)
                exit_price = float(exit_price)

                if entry_price > 0:
                    move = (
                        exit_price - entry_price
                    ) / entry_price

                    if side in {
                        "SELL",
                        "SHORT",
                    }:
                        move = -move

                    roi = move * 100.0

            except Exception:
                roi = None

        try:
            if roi is not None:
                vals.append(float(roi))
        except Exception:
            pass

        hold = (
            row.get("holding_seconds")
            if row.get("holding_seconds") is not None
            else row.get("hold_sec")
        )

        if hold is None:
            lifecycle = payload.get(
                "position_lifecycle"
            )

            if isinstance(lifecycle, dict):
                hold = lifecycle.get(
                    "holding_seconds"
                )

        try:
            if hold is not None:
                hold_seconds.append(
                    max(
                        0.0,
                        float(hold),
                    )
                )
        except Exception:
            pass

        txt = " ".join(
            [
                str(row.get("reason") or ""),
                str(row.get("exit_reason") or ""),
                str(row.get("exit_type") or ""),
            ]
        ).upper()

        if (
            "LIQUID" in txt
            or "FORCED_LIQ" in txt
        ):
            hard.append(row)

        last_exit_ts = max(
            last_exit_ts,
            ts,
        )

    n = len(vals)

    mean = (
        sum(vals) / n
        if n
        else None
    )

    lo = None
    hi = None

    if n >= 2:
        sd = statistics.stdev(vals)
        se = sd / math.sqrt(n)
        tcrit = _t_critical_975(
            n - 1
        )

        lo = mean - tcrit * se
        hi = mean + tcrit * se

    if hard:
        decision = "ROLLBACK"

    elif (
        n >= 2
        and hi is not None
        and hi < 0
    ):
        decision = "ROLLBACK"

    elif (
        n >= 2
        and lo is not None
        and lo > 0
    ):
        decision = "PROMOTE"

    else:
        decision = "WAIT_MORE_REAL_EVIDENCE"

    result.update(
        {
            "closed_trades": n,
            "roi_samples": n,
            "avg_roi": mean,
            "roi_95ci_lower": lo,
            "roi_95ci_upper": hi,
            "hard_fail_count": len(hard),
            "last_exit_ts": last_exit_ts,
            "avg_hold_hours": (
                sum(hold_seconds)
                / len(hold_seconds)
                / 3600.0
                if hold_seconds
                else None
            ),
            "evidence_decision": decision,
        }
    )

    return result



LIVE_PROFILE_INITIAL_REQUIREMENTS = {
    "30M": {"closed_trades": 50, "days": 60},
    "1H": {"closed_trades": 40, "days": 90},
    "6H": {"closed_trades": 30, "days": 120},
    "12H": {"closed_trades": 25, "days": 180},
    "1D": {"closed_trades": 20, "days": 365},
    "1W": {"closed_trades": 10, "days": 730},
}
LIVE_PROFILE_MIN_PEERS = 3
LIVE_PROFILE_CLASS_MARGIN = 0.15
LIVE_PROFILE_REVIEW_MIN_DAYS = 7
LIVE_PROFILE_RECLASS_TRADE_TRIGGER = 20
LIVE_PROFILE_RECLASS_DAY_TRIGGER = 30


def _parse_iso_epoch(value):
    try:
        text=str(value or "").strip()
        if not text:
            return 0
        if text.endswith("Z"):
            text=text[:-1]+"+00:00"
        dt=datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def _live_profile_drawdown(rows, evaluation_ts=0):
    ordered=sorted(
        [r for r in rows if isinstance(r,dict) and r.get("roi") is not None],
        key=lambda r:int(r.get("exit_ts") or 0),
    )
    equity=0.0
    peak=0.0
    peak_ts=0
    drawdown_start_ts=0
    max_dd=0.0
    max_dd_duration=0
    for r in ordered:
        ts=int(r.get("exit_ts") or 0)
        equity += float(r.get("roi") or 0.0)
        if equity >= peak:
            if drawdown_start_ts and ts:
                max_dd_duration=max(max_dd_duration,ts-drawdown_start_ts)
            peak=equity
            peak_ts=ts
            drawdown_start_ts=0
        else:
            dd=peak-equity
            max_dd=max(max_dd,dd)
            if not drawdown_start_ts:
                drawdown_start_ts=peak_ts or ts
            if drawdown_start_ts and ts:
                max_dd_duration=max(max_dd_duration,ts-drawdown_start_ts)
    if drawdown_start_ts and int(evaluation_ts or 0) > drawdown_start_ts:
        max_dd_duration=max(max_dd_duration,int(evaluation_ts)-drawdown_start_ts)
    return max_dd, (max_dd_duration/86400.0 if max_dd_duration else 0.0)


def _live_profile_cvar95(rois):
    vals=sorted(float(x) for x in rois)
    if not vals:
        return None
    count=max(1,int(math.ceil(len(vals)*0.05)))
    tail=vals[:count]
    mean=sum(tail)/len(tail)
    return max(0.0,-mean)


def _live_profile_loss_streak(rois):
    best=0
    cur=0
    for value in rois:
        if float(value) < 0.0:
            cur+=1
            best=max(best,cur)
        else:
            cur=0
    return best


def _live_profile_payoff(rois):
    wins=[float(x) for x in rois if float(x)>0.0]
    losses=[float(x) for x in rois if float(x)<0.0]
    if not wins or not losses:
        return None
    return (sum(wins)/len(wins))/abs(sum(losses)/len(losses))


def _live_profile_percentile(value, population, higher_is_better=True):
    try:
        value=float(value)
        vals=sorted(float(x) for x in population if x is not None and math.isfinite(float(x)))
    except Exception:
        return 0.5
    if not vals:
        return 0.5
    lower=sum(1 for x in vals if x<value)
    equal=sum(1 for x in vals if x==value)
    pct=(lower+0.5*equal)/len(vals)
    return pct if higher_is_better else 1.0-pct


def _live_profile_proposals(metric_rows):
    eligible={
        k:v for k,v in metric_rows.items()
        if isinstance(v,dict) and v.get("classification_eligible") is True
    }
    if len(eligible) < LIVE_PROFILE_MIN_PEERS:
        return {k:"BALANCED" for k in eligible}

    def population(key):
        return [row.get(key) for row in eligible.values() if row.get(key) is not None]

    pops={key:population(key) for key in (
        "expectancy_roi","payoff_ratio","weekly_performance_roi","cumulative_live_performance_roi",
        "mdd_roi","dd_duration_days","cvar95_loss_roi","max_consecutive_losses",
    )}
    out={}
    for instance_id,row in eligible.items():
        growth=[
            _live_profile_percentile(row.get("expectancy_roi"),pops["expectancy_roi"],True),
            _live_profile_percentile(row.get("payoff_ratio"),pops["payoff_ratio"],True),
            _live_profile_percentile(row.get("weekly_performance_roi"),pops["weekly_performance_roi"],True),
            _live_profile_percentile(row.get("cumulative_live_performance_roi"),pops["cumulative_live_performance_roi"],True),
        ]
        defensive=[
            _live_profile_percentile(row.get("mdd_roi"),pops["mdd_roi"],False),
            _live_profile_percentile(row.get("dd_duration_days"),pops["dd_duration_days"],False),
            _live_profile_percentile(row.get("cvar95_loss_roi"),pops["cvar95_loss_roi"],False),
            _live_profile_percentile(row.get("max_consecutive_losses"),pops["max_consecutive_losses"],False),
        ]
        g=sum(growth)/len(growth)
        d=sum(defensive)/len(defensive)
        row["growth_characteristic_score"]=g
        row["defensive_characteristic_score"]=d
        if g-d >= LIVE_PROFILE_CLASS_MARGIN:
            out[instance_id]="GROWTH"
        elif d-g >= LIVE_PROFILE_CLASS_MARGIN:
            out[instance_id]="DEFENSIVE"
        else:
            out[instance_id]="BALANCED"
    return out


def _strategy_instance_live_profile_evidence(state_obj, tf, strategy, strategy_version):
    instances=(state_obj.get("strategy_instances") or {}) if isinstance(state_obj,dict) else {}
    matches=[]
    for instance_id,row in instances.items():
        if not isinstance(row,dict):
            continue
        if str(row.get("timeframe") or "").upper()!=str(tf or "").upper():
            continue
        if str(row.get("strategy") or row.get("strategy_id") or "").upper()!=str(strategy or "").upper():
            continue
        if str(row.get("strategy_version") or row.get("exact_strategy_version") or "")!=str(strategy_version or ""):
            continue
        live=row.get("live_strategy_profile")
        if isinstance(live,dict):
            matches.append((str(instance_id),live))
    if len(matches)!=1:
        return {}
    return {
        "strategy_instance_id":matches[0][0],
        "strategy_instance_live_profile":json.loads(json.dumps(matches[0][1])),
    }


def update_r4_strategy_profiles():
    """Daily exact-Strategy-Instance live metrics and weekly profile classification.

    Profile data never mutates strategy logic, trailing, reentry, leverage, tier,
    or any hard safety gate. Legacy-adopted/incomplete lifecycle rows are excluded
    from personality metrics instead of being fabricated.
    """
    s=state()
    instances=s.get("strategy_instances") or {}
    active_counts={}
    active_qty={}

    if R4_FEEDBACK_DB.is_file():
        con=sqlite3.connect(f"file:{R4_FEEDBACK_DB}?mode=ro",uri=True,timeout=10)
        try:
            for row in con.execute(
                """SELECT instance_id,COUNT(*),COALESCE(SUM(owned_qty_units),0)
                   FROM r4_active_legs WHERE status='ACTIVE' GROUP BY instance_id"""
            ):
                active_counts[str(row[0])]=int(row[1] or 0)
                active_qty[str(row[0])]=int(row[2] or 0)
        finally:
            con.close()

    updated_at=now()
    now_ts=_parse_iso_epoch(updated_at)
    metrics_by_id={}
    raw_rows_by_id={}
    base_live_by_id={}

    for instance_id,profile in instances.items():
        if not isinstance(profile,dict):
            continue
        tf=str(profile.get("timeframe") or "").upper()
        strategy=str(profile.get("strategy") or profile.get("strategy_id") or "")
        version=str(profile.get("strategy_version") or profile.get("exact_strategy_version") or "")
        rows=r4_feedback_closed_rows(instance_id,tf,strategy,version,0)
        raw_rows_by_id[str(instance_id)]=rows

        rois_all=[float(r["roi"]) for r in rows if r.get("roi") is not None]
        pnls=[float(r["pnl"]) for r in rows if r.get("pnl") is not None]
        hold=[float(r["holding_seconds"]) for r in rows if r.get("holding_seconds") is not None]
        complete_rows=[
            r for r in rows
            if not r.get("legacy_adopted")
            and r.get("roi") is not None
            and r.get("entry_ts") is not None
            and r.get("exit_ts") is not None
        ]
        complete_rows.sort(key=lambda r:int(r.get("exit_ts") or 0))
        profile_rois=[float(r["roi"]) for r in complete_rows]
        profile_holds=[float(r["holding_seconds"]) for r in complete_rows if r.get("holding_seconds") is not None]
        wins=[x for x in profile_rois if x>0]
        losses=[x for x in profile_rois if x<0]
        first_entry=min([int(r.get("entry_ts") or 0) for r in complete_rows if int(r.get("entry_ts") or 0)>0],default=0)
        previous=profile.get("live_strategy_profile") if isinstance(profile.get("live_strategy_profile"),dict) else {}
        previous_start=_parse_iso_epoch(previous.get("observation_started_at"))
        start_candidates=[x for x in (first_entry,previous_start) if x>0]
        observation_start=min(start_candidates) if start_candidates else now_ts
        observation_days=max(0.0,(now_ts-observation_start)/86400.0) if now_ts and observation_start else 0.0
        mdd,dd_days=_live_profile_drawdown(complete_rows,now_ts)
        cvar=_live_profile_cvar95(profile_rois)
        weekly=sum(
            float(r.get("roi") or 0.0)
            for r in complete_rows
            if int(r.get("exit_ts") or 0) >= max(0,now_ts-7*86400)
        )
        req=LIVE_PROFILE_INITIAL_REQUIREMENTS.get(tf)
        eligible=bool(
            req
            and len(complete_rows)>=int(req["closed_trades"])
            and observation_days>=float(req["days"])
        )
        metrics={
            "schema":"B_BOT_STRATEGY_INSTANCE_LIVE_PROFILE_V1",
            "identity_unit":"STRATEGY_INSTANCE",
            "strategy_instance_id":str(instance_id),
            "strategy":strategy,
            "exact_strategy_version":version,
            "timeframe":tf,
            "current_tier":str(profile.get("tier") or "").upper(),
            "metrics_updated_at":updated_at,
            "observation_started_at":datetime.fromtimestamp(observation_start,timezone.utc).isoformat() if observation_start else updated_at,
            "observation_days":observation_days,
            "closed_complete_live_trades":len(complete_rows),
            "legacy_or_incomplete_rows_excluded":len(rows)-len(complete_rows),
            "win_rate":(len(wins)/len(profile_rois) if profile_rois else None),
            "expectancy_roi":(sum(profile_rois)/len(profile_rois) if profile_rois else None),
            "payoff_ratio":_live_profile_payoff(profile_rois),
            "mdd_roi":mdd if profile_rois else None,
            "dd_duration_days":dd_days if profile_rois else None,
            "cvar95_loss_roi":cvar,
            "max_consecutive_losses":_live_profile_loss_streak(profile_rois),
            "avg_holding_time_hours":(sum(profile_holds)/len(profile_holds)/3600.0 if profile_holds else None),
            "fill_quality":(
                "ACTUAL_ENTRY_REFERENCE_V1"
                if any(
                    r.get("slippage_bps")
                    is not None
                    for r in complete_rows
                )
                else None
            ),
            "slippage_bps":(
                sum(
                    float(r["slippage_bps"])
                    for r in complete_rows
                    if r.get("slippage_bps")
                    is not None
                )
                / max(
                    1,
                    sum(
                        1
                        for r in complete_rows
                        if r.get("slippage_bps")
                        is not None
                    ),
                )
                if any(
                    r.get("slippage_bps")
                    is not None
                    for r in complete_rows
                )
                else None
            ),
            "execution_metric_status":(
                "ACTUAL_ENTRY_REFERENCE_AVAILABLE"
                if any(
                    r.get("slippage_bps")
                    is not None
                    for r in complete_rows
                )
                else "NOT_AVAILABLE_NO_FABRICATION"
            ),
            "weekly_performance_roi":weekly if profile_rois else None,
            "cumulative_live_performance_roi":sum(profile_rois) if profile_rois else None,
            "classification_eligible":eligible,
            "classification_requirement":req or {},
            "classification_method":"RELATIVE_LIVE_PROFILE_WITH_HYSTERESIS_V1",
        }
        metrics_by_id[str(instance_id)]=metrics

        complete_mfe=[float(r["mfe_ticks"]) for r in rows if r.get("mfe_ticks") is not None and r.get("path_extrema_complete")]
        complete_mae=[float(r["mae_ticks"]) for r in rows if r.get("mae_ticks") is not None and r.get("path_extrema_complete")]
        post_adoption_extrema=sum(1 for r in rows if r.get("legacy_adopted") and (r.get("mfe_ticks") is not None or r.get("mae_ticks") is not None))
        slippage_samples=[
            float(r["slippage_bps"])
            for r in rows
            if r.get("slippage_bps")
            is not None
        ]
        giveback_samples=[
            float(r["max_giveback_ticks"])
            for r in rows
            if r.get("max_giveback_ticks")
            is not None
        ]
        funding_samples=[]
        for r in rows:
            try:
                funding_samples.append(
                    float(
                        r.get("funding_text")
                        or 0.0
                    )
                )
            except Exception:
                pass

        n=len(rois_all)
        mean=sum(rois_all)/n if n else None
        lo=hi=None
        if n>=2:
            sd=statistics.stdev(rois_all); se=sd/math.sqrt(n); tcrit=_t_critical_975(n-1)
            lo=mean-tcrit*se; hi=mean+tcrit*se
        total_pnl=sum(pnls) if pnls else None
        reasons={}; fees={}; hard=0; wins_all=losses_all=breakeven=0
        for r in rows:
            reason=str(r.get("exit_reason") or "UNKNOWN"); reasons[reason]=reasons.get(reason,0)+1
            txt=reason.upper(); hard += 1 if ("LIQUID" in txt or "FORCED_LIQ" in txt) else 0
            for asset,value in (r.get("fee_by_asset") or {}).items():
                try: fees[str(asset)]=fees.get(str(asset),0.0)+float(value)
                except Exception: pass
            v=r.get("pnl") if r.get("pnl") is not None else r.get("roi")
            if v is not None:
                v=float(v)
                if v>0:wins_all+=1
                elif v<0:losses_all+=1
                else:breakeven+=1
        if hard: decision="ROLLBACK"
        elif n>=2 and hi is not None and hi<0: decision="ROLLBACK"
        elif n>=2 and lo is not None and lo>0 and total_pnl is not None and total_pnl>0: decision="PROMOTE"
        else: decision="WAIT_MORE_REAL_EVIDENCE"
        base_live_by_id[str(instance_id)]={
            "schema":"B_BOT_R4_LIVE_FEEDBACK_V1","source":"R4_LOCAL_SQLITE_SNAPSHOT","source_db":str(R4_FEEDBACK_DB),
            "strategy_instance_id":str(instance_id),"strategy":strategy,"strategy_version":version,"timeframe":tf,
            "current_tier":str(profile.get("tier") or ""),"active_legs":active_counts.get(str(instance_id),0),
            "active_owned_qty_units":active_qty.get(str(instance_id),0),"closed_trades":len(rows),
            "win_rate_samples":wins_all+losses_all+breakeven,"wins":wins_all,"losses":losses_all,"breakeven":breakeven,
            "win_rate_pct":(wins_all/(wins_all+losses_all+breakeven)*100.0 if (wins_all+losses_all+breakeven) else None),
            "realized_pnl":total_pnl,"realized_pnl_samples":len(pnls),"fee_by_asset":{k:fees[k] for k in sorted(fees)},
            "fee_incomplete_trade_count":sum(1 for r in rows if not r.get("fee_history_complete")),
            "roi_samples":n,"avg_roi":mean,"roi_95ci_lower":lo,"roi_95ci_upper":hi,
            "holding_time_samples":len(hold),"avg_hold_hours":(sum(hold)/len(hold)/3600.0 if hold else None),
            "mfe_mae_complete_samples":min(len(complete_mfe),len(complete_mae)),
            "avg_mfe_ticks":(sum(complete_mfe)/len(complete_mfe) if complete_mfe else None),
            "avg_mae_ticks":(sum(complete_mae)/len(complete_mae) if complete_mae else None),
            "entry_slippage_samples":len(slippage_samples),
            "avg_entry_slippage_bps":(
                sum(slippage_samples)
                / len(slippage_samples)
                if slippage_samples
                else None
            ),
            "max_giveback_samples":len(giveback_samples),
            "avg_max_giveback_ticks":(
                sum(giveback_samples)
                / len(giveback_samples)
                if giveback_samples
                else None
            ),
            "funding_samples":len(funding_samples),
            "funding_total":(
                sum(funding_samples)
                if funding_samples
                else None
            ),
            "post_adoption_mfe_mae_samples":post_adoption_extrema,
            "legacy_adoption_closed_trades":sum(1 for r in rows if r.get("legacy_adopted")),
            "exit_reason_counts":{k:reasons[k] for k in sorted(reasons)},
            "hard_fail_count":hard,"last_exit_ts":max([int(r.get("exit_ts") or 0) for r in rows],default=0),
            "evidence_decision":decision,"sufficiency_rule":"95CI_NOT_FIXED_TRADE_COUNT_OR_DAYS","feedback_updated_at":updated_at,
        }

    proposals=_live_profile_proposals(metrics_by_id)
    summary={}
    for instance_id,profile in instances.items():
        if not isinstance(profile,dict) or str(instance_id) not in metrics_by_id:
            continue
        metrics=metrics_by_id[str(instance_id)]
        previous=profile.get("live_strategy_profile") if isinstance(profile.get("live_strategy_profile"),dict) else {}
        official=str(previous.get("official_class") or "").upper()
        classification_status="CLASSIFIED" if official in {"GROWTH","BALANCED","DEFENSIVE"} else "UNCLASSIFIED"
        proposal=proposals.get(str(instance_id),"") if metrics.get("classification_eligible") else ""
        last_review_ts=_parse_iso_epoch(previous.get("last_official_review_at"))
        review_interval_due=(not last_review_ts) or (now_ts-last_review_ts >= LIVE_PROFILE_REVIEW_MIN_DAYS*86400)
        if metrics.get("classification_eligible") and not official:
            official=proposal or "BALANCED"
            classification_status="CLASSIFIED"
            metrics["classified_at"]=updated_at
            metrics["last_official_review_at"]=updated_at
            metrics["last_class_review_closed_trades"]=metrics["closed_complete_live_trades"]
            metrics["last_class_review_observation_days"]=metrics["observation_days"]
            metrics["pending_reclassification_class"]=""
            metrics["pending_reclassification_confirmations"]=0
        else:
            metrics["classified_at"]=previous.get("classified_at")
            metrics["last_official_review_at"]=previous.get("last_official_review_at")
            metrics["last_class_review_closed_trades"]=int(previous.get("last_class_review_closed_trades") or 0)
            metrics["last_class_review_observation_days"]=float(previous.get("last_class_review_observation_days") or 0.0)
            metrics["pending_reclassification_class"]=str(previous.get("pending_reclassification_class") or "")
            metrics["pending_reclassification_confirmations"]=int(previous.get("pending_reclassification_confirmations") or 0)
            trade_due=(metrics["closed_complete_live_trades"]-metrics["last_class_review_closed_trades"] >= LIVE_PROFILE_RECLASS_TRADE_TRIGGER)
            day_due=(metrics["observation_days"]-metrics["last_class_review_observation_days"] >= LIVE_PROFILE_RECLASS_DAY_TRIGGER)
            if official and proposal and review_interval_due and (trade_due or day_due):
                if proposal==official:
                    metrics["pending_reclassification_class"]=""
                    metrics["pending_reclassification_confirmations"]=0
                else:
                    if metrics["pending_reclassification_class"]==proposal:
                        metrics["pending_reclassification_confirmations"]+=1
                    else:
                        metrics["pending_reclassification_class"]=proposal
                        metrics["pending_reclassification_confirmations"]=1
                    if metrics["pending_reclassification_confirmations"]>=2:
                        official=proposal
                        metrics["pending_reclassification_class"]=""
                        metrics["pending_reclassification_confirmations"]=0
                        metrics["last_class_changed_at"]=updated_at
                metrics["last_official_review_at"]=updated_at
                metrics["last_class_review_closed_trades"]=metrics["closed_complete_live_trades"]
                metrics["last_class_review_observation_days"]=metrics["observation_days"]
        metrics["official_class"]=official
        metrics["classification_status"]="CLASSIFIED" if official else "UNCLASSIFIED"
        metrics["proposed_class"]=proposal
        metrics["profile_effect_scope"]="PORTFOLIO_SELECTION_PRIORITY_ONLY"
        metrics["strategy_logic_mutation"]=False
        metrics["trailing_reentry_mutation"]=False
        metrics["exchange_leverage_mutation"]=False
        profile["live_strategy_profile"]=metrics
        profile["live_feedback"]=base_live_by_id[str(instance_id)]
        summary[str(instance_id)]={
            "closed_complete":metrics["closed_complete_live_trades"],
            "observation_days":metrics["observation_days"],
            "eligible":metrics["classification_eligible"],
            "profile_class":metrics["official_class"],
            "classification_status":metrics["classification_status"],
            "active":base_live_by_id[str(instance_id)]["active_legs"],
        }

    # =========================================================
    # FRAME ABSOLUTE LEVERAGE EVALUATION PERSISTENCE
    # =========================================================
    # 프레임별 절대평가 결과를 deployment_state에 기록한다.
    # 전략 간 상대평가가 아니며, 실제 레버리지 변경은 기존
    # runtime sync / deployment 경계를 통해서만 반영한다.
    frame_rows = s.get("frames") or {}

    for frame_name, frame_row in frame_rows.items():

        if not isinstance(frame_row, dict):
            continue

        tf_name = str(frame_name or "").upper()

        if tf_name not in {
            "30M",
            "1H",
            "6H",
            "12H",
            "1D",
            "1W",
        }:
            continue

        frame_evidence = frame_row.get("last_evidence")

        if not isinstance(frame_evidence, dict):
            frame_evidence = frame_row.get("evidence")

        if not isinstance(frame_evidence, dict):
            frame_evidence = {}

        frame_evidence = dict(frame_evidence)

        # R5 profile refresh must preserve the same exact-instance evidence
        # binding used by the deployment path. Otherwise this refresh pass
        # would overwrite a valid frame evaluation with a fail-closed 10x.
        frame_strategy = str(
            frame_row.get("strategy")
            or frame_row.get("strategy_id")
            or ""
        )
        frame_version = str(
            frame_row.get("strategy_version")
            or frame_row.get("exact_strategy_version")
            or ""
        )

        # R5 PROFILE REFRESH PIPELINE RESEARCH BINDING V1
        # Same exact-current provenance rule as runtime.
        pipeline_row = None
        pipeline_path = (
            Path(__file__).resolve().parent
            / "results"
            / "v3"
            / "historical_314_pipeline_stage2.json"
        )

        try:
            if pipeline_path.is_file():
                pipeline_state = json.loads(
                    pipeline_path.read_text(
                        encoding="utf-8-sig"
                    )
                )
                pipeline_matches = [
                    row
                    for row in (
                        pipeline_state.get("entries") or []
                    )
                    if isinstance(row, dict)
                    and str(
                        row.get("timeframe") or ""
                    ).upper() == str(
                        tf_name or ""
                    ).upper()
                    and str(
                        row.get("strategy_id") or ""
                    ).upper() == str(
                        frame_strategy or ""
                    ).upper()
                    and str(
                        row.get("exact_strategy_version")
                        or row.get("strategy_version")
                        or ""
                    ) == str(frame_version or "")
                ]

                if len(pipeline_matches) == 1:
                    pipeline_row = pipeline_matches[0]
        except Exception:
            pipeline_row = None

        if isinstance(pipeline_row, dict):
            frame_evidence[
                "research_pipeline_stage"
            ] = pipeline_row.get("pipeline_stage")

            frame_evidence[
                "research_pipeline_exact_strategy_version"
            ] = pipeline_row.get(
                "exact_strategy_version"
            )

            for key in (
                "hard_pass_recheck",
                "entry_risk_gate",
                "final_risk_gate",
            ):
                value = pipeline_row.get(key)
                if isinstance(value, dict):
                    frame_evidence[key] = json.loads(
                        json.dumps(value)
                    )

            pipeline_result_json = str(
                pipeline_row.get(
                    "final_strategy_result_json"
                )
                or pipeline_row.get("new_result_json")
                or ""
            ).strip()

            if pipeline_result_json:
                frame_evidence[
                    "result_json"
                ] = pipeline_result_json

            frame_evidence[
                "leverage_research_pipeline_bound"
            ] = True

        if frame_strategy and frame_version:
            live_profile_binding = (
                _strategy_instance_live_profile_evidence(
                    s,
                    tf_name,
                    frame_strategy,
                    frame_version,
                )
            )

            if isinstance(live_profile_binding, dict):
                frame_evidence.update(live_profile_binding)

                profile = live_profile_binding.get(
                    "strategy_instance_live_profile"
                )

                if isinstance(profile, dict):
                    frame_evidence[
                        "live_strategy_profile"
                    ] = json.loads(json.dumps(profile))

        source_row = frame_row.get("source_row")
        if not isinstance(source_row, dict):
            source_row = {}

        source_row_result_json = str(
            source_row.get("result_json") or ""
        ).strip()

        result_json = str(
            frame_evidence.get("result_json")
            or source_row_result_json
            or ""
        ).strip()

        if result_json:
            result_path = Path(result_json)

            if not result_path.is_absolute():
                result_path = ROOT / result_path

            if result_path.is_file():
                try:
                    research_result = json.loads(
                        result_path.read_text(
                            encoding="utf-8-sig"
                        )
                    )
                except Exception:
                    research_result = {}

                if isinstance(research_result, dict):
                    # R5 legacy research-result evidence binding.
                    # Older V3 result files keep frame robustness under
                    # $.timeframes.<TF>.v3_robustness rather than top-level.
                    # Bind only existing evidence; never fabricate hard gates.
                    research_timeframes = (
                        research_result.get("timeframes")
                        if isinstance(research_result.get("timeframes"), dict)
                        else {}
                    )

                    research_tf = (
                        research_timeframes.get(tf_name)
                        if isinstance(research_timeframes.get(tf_name), dict)
                        else {}
                    )

                    robustness = (
                        research_tf.get("v3_robustness")
                        if isinstance(research_tf.get("v3_robustness"), dict)
                        else {}
                    )

                    if robustness:
                        legacy_wf = robustness.get("walk_forward")
                        legacy_stress = robustness.get("cost_stress_2x")

                        if isinstance(legacy_wf, dict):
                            frame_evidence["walk_forward"] = json.loads(
                                json.dumps(legacy_wf)
                            )

                        if isinstance(legacy_stress, dict):
                            frame_evidence["cost_stress_2x"] = json.loads(
                                json.dumps(legacy_stress)
                            )

                    for key in (
                        "evidence",
                        "metrics",
                        "evaluation",
                        "final_risk_gate",
                        "walk_forward",
                        "cost_stress_2x",
                        "final_risk",
                        "baseline_risk",
                    ):
                        value = research_result.get(key)
                        if isinstance(value, dict):
                            frame_evidence[key] = json.loads(
                                json.dumps(value)
                            )

                    for key in (
                        "walk_forward_oos_expectancy_pct",
                        "wf_oos_expectancy",
                        "cost_stress_2x_expectancy_pct",
                        "cost_stress_2x_expectancy",
                        "strategy_version",
                        "timeframe",
                    ):
                        if key in research_result:
                            frame_evidence[key] = research_result[key]

                    frame_evidence[
                        "leverage_research_result_bound"
                    ] = True

        # R5 REFRESH HARD-PASS CANONICAL ROBUSTNESS V1
        if isinstance(pipeline_row, dict):
            hard_pass_recheck = pipeline_row.get(
                "hard_pass_recheck"
            )

            if isinstance(hard_pass_recheck, dict):
                pipeline_wf = hard_pass_recheck.get(
                    "walk_forward_avg_net_pct"
                )
                pipeline_stress = hard_pass_recheck.get(
                    "walk_forward_cost_stress_2x_avg_net_pct"
                )

                if pipeline_wf is not None:
                    frame_evidence[
                        "walk_forward_oos_expectancy_pct"
                    ] = pipeline_wf

                if pipeline_stress is not None:
                    frame_evidence[
                        "cost_stress_2x_expectancy_pct"
                    ] = pipeline_stress

        frame_allocation = (
            frame_row.get("portfolio_allocation_gate")
        )

        if not isinstance(frame_allocation, dict):
            frame_allocation = {}

        frame_row[
            "leverage_absolute_evaluation"
        ] = _frame_absolute_leverage_evaluation(
            frame_evidence,
            frame_allocation,
        )

    s["live_strategy_profile_last_update_at"] = updated_at
    s["updated_at"] = updated_at
    atomic(STATE, s)
    return summary


def next_policy_tier_request(track=None):
    """
    Policy Version? ?? ?? ??? ??
    CANARY -> SMALL -> NORMAL ?? ROLLBACK??.
    """
    if PUSH_HOLD.exists() or REQUEST.exists():
        return None

    s = state()

    tracks = [
        str(track).upper()
    ] if track else [
        "DIRECTION",
        "ENTRY_RISK",
        "EXIT",
        "REENTRY",
        "SYMBOL",
        "PORTFOLIO",
        "REGIME",
        "EXECUTION",
    ]

    for policy_track in tracks:
        row = (
            (s.get("policies") or {})
            .get(policy_track)
            or {}
        )

        tier = str(
            row.get("tier") or ""
        ).upper()

        if tier not in {
            "CANARY",
            "SMALL",
        }:
            continue

        version_id = str(
            row.get("policy_version")
            or ""
        )

        if not version_id:
            continue

        evidence = policy_canary_evidence(
            version_id,
            int(
                row.get("activated_ts")
                or 0
            ),
        )

        verdict = str(
            evidence.get(
                "evidence_decision"
            )
            or "WAIT_MORE_REAL_EVIDENCE"
        ).upper()

        if verdict == "WAIT_MORE_REAL_EVIDENCE":
            continue

        target = (
            "ROLLBACK"
            if verdict == "ROLLBACK"
            else (
                "SMALL"
                if tier == "CANARY"
                else "NORMAL"
            )
        )

        cfg = load(
            CONFIG,
            {},
        )

        if not isinstance(cfg, dict):
            raise RuntimeError(
                "STRATEGY_CONFIG_INVALID"
            )

        new = json.loads(
            json.dumps(cfg)
        )

        if target == "ROLLBACK":
            previous_config = row.get(
                "previous_policy_config"
            )

            if not isinstance(
                previous_config,
                dict,
            ):
                previous_config = {}

            for key, value in previous_config.items():
                new[key] = json.loads(
                    json.dumps(value)
                )

            previous_version = str(
                row.get(
                    "previous_policy_version"
                )
                or ""
            )

            if previous_version:
                new.setdefault(
                    "policy_versions",
                    {},
                )[policy_track] = previous_version
            else:
                (
                    new.setdefault(
                        "policy_versions",
                        {},
                    )
                    .pop(
                        policy_track,
                        None,
                    )
                )

            reason = (
                "REAL_POLICY_EVIDENCE_ROLLBACK"
            )

        else:
            reason = (
                f"AUTO_POLICY_SCALE_"
                f"{tier}_TO_{target}_95CI"
            )

        cfgp = (
            RES
            / (
                "strategy_config_pending_policy_"
                f"{policy_track.lower()}_"
                f"{target.lower()}.json"
            )
        )

        atomic(
            cfgp,
            new,
        )

        rid = (
            f"V3_POLICY_"
            f"{policy_track}_"
            f"{target}_"
            f"{int(time.time())}"
        )

        req = {
            "schema": "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",
            "request_id": rid,
            "created_at": now(),
            "reason": reason,
            "version_kind": "POLICY",
            "policy_track": policy_track,
            "policy_version": version_id,
            "previous_policy_version": row.get(
                "previous_policy_version"
            ),
            "previous_policy_config": row.get(
                "previous_policy_config"
            )
            or {},
            "timeframe": "",
            "strategy": "",
            "tier": target,
            "strategy_file": "",
            "strategy_config_file": str(
                cfgp.relative_to(ROOT)
            ),
            "autonomous_rule_passed": True,
            "core_mutation": False,
            "real_evidence": evidence,
            "rollback_target": (
                row.get(
                    "previous_policy_version"
                )
                or "PREVIOUS_VALIDATED_VERSION"
            ),
        }

        atomic(
            REQUEST,
            req,
        )

        row["tier"] = (
            "PENDING_" + target
        )
        row["request_id"] = rid
        row["last_evidence"] = evidence

        s["updated_at"] = now()
        atomic(
            STATE,
            s,
        )

        return req

    return None


def _apply_config_patch(target, patch):
    if not isinstance(target,dict) or not isinstance(patch,dict): raise RuntimeError("POLICY_CONFIG_PATCH_INVALID")
    for key,value in patch.items():
        if isinstance(value,dict) and isinstance(target.get(key),dict):
            _apply_config_patch(target[key],value)
        else:
            target[key]=value



def create_policy_canary_request(version_id):
    """
    VALIDATED Policy Version
      -> exact Strategy Instance 새 Policy Bundle 등록
      -> LOCAL_TEST
      -> LOCAL_SHADOW

    서버 runtime-sync request는 Shadow PASS 전에는 만들지 않는다.
    """
    cv = governance.validate_constitution()
    reg = governance.version_registry()

    version_row = (
        (
            reg.get("policy_versions")
            or {}
        ).get(str(version_id))
    )

    if not isinstance(version_row, dict):
        raise RuntimeError("POLICY_VERSION_NOT_FOUND")

    if str(
        version_row.get("status")
        or ""
    ).upper() != "VALIDATED":
        raise RuntimeError("POLICY_VERSION_NOT_VALIDATED")

    track = str(
        version_row.get("track")
        or ""
    ).upper()

    payload = (
        version_row.get("payload")
        if isinstance(version_row.get("payload"), dict)
        else {}
    )

    patch = (
        payload.get("config_patch")
        if isinstance(payload.get("config_patch"), dict)
        else None
    )

    if not patch:
        raise RuntimeError("POLICY_CONFIG_PATCH_MISSING")

    allowed = {
        "ENTRY_RISK": {"v3_decision"},
        "EXIT": {"trailing", "opposite_exit"},
        "REENTRY": {"state_reentry", "opposite_reopen"},
        "SYMBOL": {"v3_decision"},
        "PORTFOLIO": {"v3_decision"},
        "REGIME": {"v3_decision"},
        "EXECUTION": {"v3_decision"},
        "DIRECTION": {"v3_decision"},
    }

    top = set(patch)
    if not top.issubset(allowed.get(track, set())):
        raise RuntimeError("POLICY_PATCH_OUTSIDE_ALLOWED_SCOPE")

    cfg = load(CONFIG, {})
    if not isinstance(cfg, dict):
        raise RuntimeError("STRATEGY_CONFIG_INVALID")

    previous_version = str(
        ((cfg.get("policy_versions") or {}).get(track))
        or ""
    )

    previous_config = {
        key: json.loads(json.dumps(cfg.get(key)))
        for key in top
    }

    new = json.loads(json.dumps(cfg))
    _apply_config_patch(new, patch)
    new.setdefault("policy_versions", {})[track] = str(version_id)

    if bool((new.get("trailing") or {}).get("dynamic_adjustment")):
        raise RuntimeError(
            "DYNAMIC_TRAILING_REQUIRES_SEPARATE_APPROVED_CONTRACT"
        )

    if bool((new.get("v3_decision") or {}).get("additional_entry_same_symbol")):
        raise RuntimeError("ADDITIONAL_ENTRY_SAME_SYMBOL_FORBIDDEN")

    behavior_policy = {
        "trailing": new.get("trailing") or {},
        "state_reentry": new.get("state_reentry") or {},
        "opposite_exit": new.get("opposite_exit") or {},
        "opposite_reopen": new.get("opposite_reopen") or {},
    }
    policy_bundle = governance.strategy_policy_bundle(
        new.get("policy_versions") or {},
        behavior_policy,
    )

    targets = []
    frames = cfg.get("frames") or {}
    for tf in sorted(frames):
        frame = frames.get(tf)
        if not isinstance(frame, dict):
            continue
        if frame.get("enabled") is not True:
            continue

        deployment_tier = str(
            frame.get("deployment_tier")
            or ""
        ).upper()
        if deployment_tier not in {"CANARY", "SMALL", "NORMAL"}:
            continue

        exact_version = str(
            frame.get("strategy_version")
            or ""
        )
        strategy_version_row = (
            (reg.get("strategy_versions") or {}).get(exact_version)
            or {}
        )
        if not isinstance(strategy_version_row, dict) or not strategy_version_row:
            raise RuntimeError(
                "POLICY_TARGET_STRATEGY_VERSION_MISSING="
                + str(tf).upper()
            )

        if str(
            strategy_version_row.get("status")
            or ""
        ).upper() not in {
            "VALIDATED",
            "PROVISIONAL",
            "CANARY",
            "SMALL",
            "NORMAL",
        }:
            raise RuntimeError(
                "POLICY_TARGET_STRATEGY_VERSION_NOT_VALIDATED="
                + str(tf).upper()
            )

        strategy_payload = (
            strategy_version_row.get("payload")
            if isinstance(strategy_version_row.get("payload"), dict)
            else {}
        )
        strategy_id = str(
            strategy_payload.get("strategy")
            or "+".join(
                str(x)
                for x in (frame.get("strategies") or [])
                if str(x).strip()
            )
            or ""
        ).upper()
        if not strategy_id:
            raise RuntimeError(
                "POLICY_TARGET_STRATEGY_ID_MISSING="
                + str(tf).upper()
            )

        source_file = str(
            strategy_payload.get("source_file")
            or ""
        ).replace("\\", "/")

        instance = governance.register_strategy_instance(
            strategy_id,
            str(tf).upper(),
            exact_version,
            policy_bundle["version"],
            policy_bundle["snapshot"],
            source_file=source_file,
            evidence={
                "source": "POLICY_VERSION_REQUALIFICATION",
                "policy_track": track,
                "policy_version": str(version_id),
                "previous_policy_version": previous_version,
                "at": now(),
            },
            status="LOCAL_TEST",
        )

        instance_id = str(
            instance.get("strategy_instance_id")
            or ""
        )
        instance_status = str(
            instance.get("status")
            or ""
        ).upper()

        if instance_status == "LOCAL_TEST":
            instance = governance.set_strategy_instance_status(
                instance_id,
                "LOCAL_SHADOW",
                {
                    "reason": "POLICY_BUNDLE_LOCAL_TEST_PASS",
                    "policy_track": track,
                    "policy_version": str(version_id),
                    "at": now(),
                },
            )
            instance_status = str(
                instance.get("status")
                or ""
            ).upper()

        if instance_status in {"CANARY", "SMALL", "NORMAL"}:
            continue

        if instance_status != "LOCAL_SHADOW":
            raise RuntimeError(
                "POLICY_TARGET_STRATEGY_INSTANCE_NOT_LOCAL_SHADOW="
                + instance_id
            )

        targets.append(
            {
                "strategy_instance_id": instance_id,
                "strategy": strategy_id,
                "timeframe": str(tf).upper(),
                "strategy_version": exact_version,
                "strategy_policy_bundle_version": policy_bundle["version"],
                "strategy_policy_snapshot": policy_bundle["snapshot"],
                "source_file": source_file,
                "previous_deployment_tier": deployment_tier,
            }
        )

    if not targets:
        return None

    cfgp = (
        RES
        / (
            "strategy_config_pending_policy_"
            f"{track.lower()}_"
            f"{str(version_id).lower()}.json"
        )
    )

    atomic(cfgp, new)

    rid = (
        f"R4_POLICY_{track}_"
        f"{int(time.time())}"
    )

    req = {
        "schema": "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",
        "request_id": rid,
        "created_at": now(),
        "reason": "VALIDATED_POLICY_BUNDLE_CANARY_AFTER_EXACT_INSTANCE_SHADOW",
        "version_kind": "STRATEGY_INSTANCE_POLICY_BUNDLE",
        "constitution_version": cv.get("constitution_version"),
        "constitution_hash": cv.get("constitution_hash"),
        "policy_track": track,
        "policy_version": str(version_id),
        "previous_policy_version": previous_version,
        "previous_policy_config": previous_config,
        "timeframe": "",
        "strategy": "",
        "tier": "CANARY",
        "strategy_file": "",
        "strategy_config_file": str(cfgp.relative_to(ROOT)),
        "strategy_policy_bundle_version": policy_bundle["version"],
        "strategy_policy_snapshot": policy_bundle["snapshot"],
        "strategy_instances": targets,
        "required_pre_runtime_gate": "EXACT_STRATEGY_INSTANCE_MINIMAL_SHADOW_RUNTIME_SAFETY",
        "autonomous_rule_passed": True,
        "approved_for_server_release": False,
        "core_mutation": False,
        "rollback_target": (
            previous_version
            or "PREVIOUS_VALIDATED_VERSION"
        ),
    }

    s = state()
    policy_state = s.setdefault("policies", {}).setdefault(track, {})
    policy_state.clear()
    policy_state.update(
        {
            "policy_track": track,
            "policy_version": str(version_id),
            "previous_policy_version": previous_version,
            "previous_policy_config": previous_config,
            "strategy_policy_bundle_version": policy_bundle["version"],
            "tier": "PENDING_SHADOW",
            "shadow_status": "PENDING",
            "request_id": "",
            "requested_at": now(),
            "shadow_requested_at": now(),
            "strategy_instances": targets,
            "pending_request_template": req,
            "shadow_evidence_files": {
                target["strategy_instance_id"]: str(
                    (
                        RES
                        / "validation"
                        / target["strategy_version"]
                        / (
                            "shadow__"
                            + target["strategy_instance_id"]
                            + ".json"
                        )
                    ).relative_to(ROOT)
                )
                for target in targets
            },
        }
    )

    for target in targets:
        s.setdefault("strategy_instances", {})[
            target["strategy_instance_id"]
        ] = {
            **target,
            "tier": "PENDING_SHADOW",
            "qualification_status": "LOCAL_SHADOW",
            "shadow_status": "PENDING",
            "policy_track": track,
            "policy_version": str(version_id),
            "updated_at": now(),
        }

    s["updated_at"] = now()
    atomic(STATE, s)

    return {
        "local_only": True,
        "held": PUSH_HOLD.exists(),
        "validated": True,
        "shadow_required": True,
        "tier": "PENDING_SHADOW",
        "policy_track": track,
        "policy_version": str(version_id),
        "strategy_policy_bundle_version": policy_bundle["version"],
        "strategy_instance_ids": [
            target["strategy_instance_id"]
            for target in targets
        ],
        "request_template": req,
    }






# ================= GitHub → 실행 서버 후보 인계 =================
def export_runtime_candidates(output, validated_release=False):
    """기존 최종 gate/packager를 사용한다. 서버 등록 승인이나 운영 설정은 만들지 않는다."""
    import hashlib,re
    import v3_strategy_research as research
    import v3_portable_strategy_packager as packager
    out=Path(output).resolve()
    if out.exists():raise RuntimeError('RUNTIME_HANDOFF_OUTPUT_EXISTS')
    cv=governance.validate_constitution()
    if cv.get('ok') is not True:raise RuntimeError('RUNTIME_HANDOFF_CONSTITUTION_INVALID')
    rows=[];files={}
    live_instances=((load(CONFIG,{}) if validated_release else {}).get('r4_runtime') or {}).get('strategy_instances') or {}
    for tf in research.TF_ORDER:
        excluded=set()
        while len(rows)<32:
            row=research._pipeline_local_shadow_choice(tf,excluded)
            if not row:break
            excluded.add(row['strategy_id'])
            if any(isinstance(item,dict) and item.get('exact_strategy_version')==row.get('exact_strategy_version') and item.get('strategy_id')==row.get('strategy_id') and item.get('timeframe')==tf and item.get('server_activation_authorized') is True for item in live_instances.values()):continue
            source=source_for_pipeline(row)
            if source is None or hashlib.sha256(source.read_bytes()).hexdigest()!=row.get('source_sha256'):raise RuntimeError('RUNTIME_HANDOFF_SOURCE_HASH')
            version=str(row.get('exact_strategy_version') or '')
            strategy=str(row.get('strategy_id') or '')
            if not re.fullmatch('[A-Z0-9_]{1,160}',strategy) or not re.fullmatch('[A-Za-z0-9_]{1,160}',version):raise RuntimeError('RUNTIME_HANDOFF_ID_INVALID')
            name='artifacts/'+hashlib.sha256((strategy+'|'+tf+'|'+version).encode()).hexdigest()+'.py'
            # Packaging happens only after exact source + final gates were checked.
            out.mkdir(parents=True,exist_ok=True)
            packaged=packager.package_strategy(source,out/name)
            raw=(out/name).read_bytes()
            if len(raw)>128*1024:raise RuntimeError('RUNTIME_HANDOFF_ARTIFACT_LIMIT')
            files[name]={'bytes':len(raw),'sha256':packaged['runtime_sha256']}
            rows.append({'strategy_id':strategy,'timeframe':tf,'exact_strategy_version':version,'source_sha256':row['source_sha256'],'artifact':name,'runtime_sha256':packaged['runtime_sha256'],'research_evidence':row,'server_shadow_required':True,'approved_for_server_release':False})
    manifest={'schema':'B_BOT_R4_RESEARCH_CANDIDATES_V1','source_commit':os.environ.get('GITHUB_SHA'),'source_run_id':os.environ.get('GITHUB_RUN_ID'),'constitution_hash':cv['constitution_hash'],'engine_sha256':hashlib.sha256(RUNNER.read_bytes()).hexdigest() if 'RUNNER' in globals() else hashlib.sha256((LAB/'run_strategy.py').read_bytes()).hexdigest(),'candidates':rows,'files':files,'automatic_registration_authorized':False}
    if validated_release:
        import v3_auto_runtime_release as worker
        import v3_runtime_config_merge as merger
        required=load(RES/'collector_required_universe.json',{}).get('required_symbols') or []
        for candidate in rows:candidate['required_symbols']=required
        approval=load(worker.STATE,{})
        if approval.get('status')=='VALIDATED_FOR_SERVER':
            if not REQUEST.is_file() or not 0<=time.time()-float(approval.get('validated_at_epoch') or 0)<=180:raise RuntimeError('RUNTIME_RELEASE_APPROVAL_EXPIRED')
            request_raw=REQUEST.read_bytes();req=json.loads(request_raw)
            worker.validate_request(req)
            if hashlib.sha256(request_raw).hexdigest()!=approval.get('request_sha256') or approval.get('canonical_verifiers')!=6:raise RuntimeError('RUNTIME_RELEASE_VERIFICATION_BINDING')
            ids=req.get('strategy_instance_ids') or [req.get('strategy_instance_id')]
            if req.get('version_kind')!='STRATEGY' or req.get('tier')!='CANARY' or len(ids)!=1 or not ids[0]:raise RuntimeError('RUNTIME_RELEASE_SINGLE_CANARY_REQUIRED')
            matches=[row for row in rows if row['strategy_id']==req.get('strategy') and row['timeframe']==req.get('timeframe') and row['exact_strategy_version']==req.get('strategy_version')]
            if len(matches)!=1:raise RuntimeError('RUNTIME_RELEASE_RESEARCH_BINDING')
            candidate=matches[0]
            if not minimal_shadow_pass(req['strategy_version'],ids[0]):raise RuntimeError('RUNTIME_RELEASE_SHADOW_NOT_PASSED')
            active_raw=CONFIG.read_bytes()
            if hashlib.sha256(active_raw).hexdigest()!=approval.get('base_config_sha256'):raise RuntimeError('RUNTIME_RELEASE_ACTIVE_CONFIG_CHANGED')
            rel=str(req.get('strategy_config_file') or '').replace(chr(92),'/')
            target=(ROOT/rel).resolve()
            if not rel.startswith('strategy_lab/results/v3/strategy_config_pending_') or not target.is_relative_to(ROOT.resolve()) or not target.is_file():raise RuntimeError('RUNTIME_RELEASE_PENDING_PATH')
            merged=merger.merge_strategy(json.loads(active_raw),load(target,{}),req['timeframe'],req['strategy'],req['strategy_version'],'CANARY',ids[0])
            destination=str((merged.get('strategy_registry') or {}).get(req['strategy'],{}).get('file') or '')
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,159}\.py',destination) or '..' in destination:raise RuntimeError('RUNTIME_RELEASE_DESTINATION')
            # 동일 canonical packager 산출물만 설치한다. 연구 소스는 서버 payload에 포함하지 않는다.
            runtime_path=ROOT/str(req.get('strategy_file') or '').replace(chr(92),'/')
            if not runtime_path.resolve().is_relative_to(ROOT.resolve()) or not runtime_path.is_file():raise RuntimeError('RUNTIME_RELEASE_SOURCE_MISSING')
            check=out/'release-source-check.py';packager.package_strategy(runtime_path,check)
            if check.read_bytes()!=(out/candidate['artifact']).read_bytes():raise RuntimeError('RUNTIME_RELEASE_PACKAGED_SOURCE_MISMATCH')
            check.unlink()
            payloads={'release/CONFIG.json':json.dumps(merged,ensure_ascii=False,sort_keys=True,indent=2).encode(),'release/REQUEST.json':request_raw}
            for name,data in payloads.items():
                (out/name).parent.mkdir(parents=True,exist_ok=True);(out/name).write_bytes(data);files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            manifest['release']={'schema':'B_BOT_R4_VALIDATED_RELEASE_MANIFEST_V1','request_id':req['request_id'],'created_at_epoch':time.time(),'version_kind':'STRATEGY','tier':'CANARY','strategy_instance_ids':ids,'strategy':req['strategy'],'timeframe':req['timeframe'],'strategy_version':req['strategy_version'],'strategy_policy_bundle_version':req['strategy_policy_bundle_version'],'constitution_hash':cv['constitution_hash'],'base_config_sha256':approval['base_config_sha256'],'strategy_config_sha256':files['release/CONFIG.json']['sha256'],'request_sha256':files['release/REQUEST.json']['sha256'],'runtime_strategy_file':destination,'runtime_artifact':candidate['artifact'],'runtime_sha256':candidate['runtime_sha256'],'approved_for_server_release':True,'local_shadow_pass':True,'canonical_verifiers':6,'core_mutation':False}
            manifest['automatic_registration_authorized']=True
    raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2).encode()
    if len(raw)>1024**2 or sum(item['bytes'] for item in files.values())>8*1024**2:raise RuntimeError('RUNTIME_HANDOFF_TOTAL_LIMIT')
    out.mkdir(parents=True,exist_ok=True);(out/'MANIFEST.json').write_bytes(raw)
    return {'status':'RUNTIME_CANDIDATES_EXPORTED','candidates':len(rows),'server_shadow_required':True,'orders_touched':False}
