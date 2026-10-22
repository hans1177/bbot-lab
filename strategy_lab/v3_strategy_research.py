# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_strategy_research.py
"""V3 bounded standalone strategy research. LIVE/strategy payload files are never edited here."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, re, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB=ROOT/"strategy_lab"; RES=LAB/"results"/"v3"; FAMILIES=LAB/"v3_single_families"
GENERATED=RES/"generated_trials"; DEPLOY=LAB/"deploy_ready"; RUNNER=LAB/"run_strategy.py"
CONSTITUTION=ROOT/"ROADMAP_CONSTITUTION_V3.json"
HISTORICAL_STAGE2=RES/"historical_314_pipeline_stage2.json"
TRAILING_STAGE3=RES/"individual_trailing_stage3.json"
REENTRY_STAGE4=RES/"individual_reentry_final_risk_stage4.json"
FULL_VALIDATION_STAGE5=RES/"full_validation_stage5.json"
SEALED_FINAL_BLIND_STAGE5=RES/"sealed_final_blind_stage5.json"
REDEVELOPED=RES/"redeveloped_strategies"
NEW_RESEARCH_EPOCH="HARD_PASS_FIXED_WINDOW_2016_2024_STAGE10"
SYMBOL_UNIVERSE_SNAPSHOT=ROOT/"state"/"symbol_universe.json"
HARD_PASS_DATASET="HARD_PASS_FIXED_WINDOW"
HARD_PASS_LONG4_DATASET="HARD_PASS_LONG4_WINDOW"
ELIGIBLE_PIPELINE_TFS={"30M","1H","6H","12H","1D","1W"}
sys.path.insert(0,str(LAB))
import v3_research_knowledge as knowledge
import v3_policy_research as policy_research

_FILL_CONFIG_PATH=LAB/"lab_config.json"
try:
    _FILL_CONFIG=json.loads(
        _FILL_CONFIG_PATH.read_text(
            encoding="utf-8-sig"
        )
    )
except Exception:
    _FILL_CONFIG={}

CANDIDATE_FILL_TARGET=int(
    _FILL_CONFIG.get(
        "research_candidate_fill_target",
        8,
    )
)

MAIN_FILL_TFS=[str(x).upper() for x in _FILL_CONFIG.get("research_candidate_fill_timeframes",["30M","1H","6H","12H","1D","1W"]) if str(x).upper() in ELIGIBLE_PIPELINE_TFS]
RESEARCH_ONLY_TFS=[]
DETAIL_TFS=[]
TF_WEIGHTS={tf:1.0 for tf in MAIN_FILL_TFS}
TF_ORDER=list(MAIN_FILL_TFS)
MIN_TRADES={"30M":40,"1H":30,"6H":20,"12H":15,"1D":10,"1W":6}


def now(): return datetime.now(timezone.utc).isoformat()
def load(path,default=None):
    try:return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:return {} if default is None else default
def atomic(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
    os.replace(tmp,path)

def pipeline_policy():
    obj=load(CONSTITUTION,{})
    return obj.get("individual_strategy_trailing_optimization") or {}

def hard_pass_contract():
    return pipeline_policy().get("hard_pass_contract") or {}

def gate_contract(name):
    return (pipeline_policy().get("gate_contract") or {}).get(str(name).upper()) or {}

def hard_pass_dates():
    c=hard_pass_contract()
    return (
        str(c.get("research_start_date") or "2016-01-01"),
        str(c.get("research_end_date") or "2024-08-20"),
    )

def family_files(): return sorted(p for p in FAMILIES.glob("*.py") if p.name!="__init__.py")
def load_meta(path):
    spec=importlib.util.spec_from_file_location("v3_meta_"+path.stem,str(path)); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(getattr(mod,"STRATEGY_META",{}) or {})

def wrapper_for(path,tf):
    meta=load_meta(path); base=str(meta.get("name") or path.stem).upper(); name=f"{base}_{tf}_BROAD_V1"
    GENERATED.mkdir(parents=True,exist_ok=True); out=GENERATED/f"{path.stem}_{tf.lower()}_broad_v1.py"
    source=Path(path).read_text(encoding="utf-8-sig")
    code=(
        "# -*- coding: utf-8 -*-\n"
        "# PORTABLE_GENERATED_SINGLE_V2\n"
        "# Exact standalone research source; no local absolute-path dependency.\n"
        + source
        + "\nSTRATEGY_META=dict(STRATEGY_META)\n"
        + f"STRATEGY_META['name']={name!r}\n"
        + f"STRATEGY_META['allowed_timeframes']=[{tf!r}]\n"
    )
    compile(code,str(out),"exec")
    out.write_text(code,encoding="utf-8")
    return out,name,meta

def find_result(strategy_name,after_ts):
    candidates=[]
    for p in (LAB/"results").glob("*.json"):
        try:
            if p.stat().st_mtime+1 < after_ts: continue
            j=load(p,{})
            if str(j.get("strategy") or "").upper()==strategy_name.upper(): candidates.append(p)
        except Exception: pass
    return max(candidates,key=lambda p:p.stat().st_mtime) if candidates else None

def finite(x):
    try:return float(x) if math.isfinite(float(x)) else None
    except Exception:return None

# SOFT_PASS_RESEARCH_SHADOW_V1
def _soft_shadow_qualification(
    reasons,
    *,
    trades,
    min_trades,
    wf_avg,
    wf_stress,
    long_avg,
    long_trades,
):
    """
    SOFT_PASS is research-only.

    HARD invariants:
      - at least one trade
      - positive WF/OOS average
      - missing/invalid/critical/unknown qualification failures stay HARD
      - severe 2x-cost deterioration stays HARD
      - severe long-core deterioration stays HARD

    Relaxable only for LOCAL_TEST / research SHADOW observation:
      - trade count >= 50% of normal threshold and >= 3
      - mildly negative 2x cost stress
      - mildly negative long core
      - non-critical sensitivity/robustness weakness
    """
    raw=[
        str(x or "").upper()
        for x in (reasons or [])
        if str(x or "").strip()
    ]

    hard=[]
    soft=[]

    t=int(trades or 0)

    try:
        mt=int(min_trades)
    except Exception:
        mt=0

    for reason in raw:

        if reason=="CURRENT_TRADES_LOW":
            floor=max(
                3,
                (mt+1)//2
                if mt>0
                else 3,
            )

            if (
                mt>0
                and t>=floor
            ):
                soft.append(reason)
            else:
                hard.append(reason)

            continue

        if (
            reason
            =="WALK_FORWARD_COST_STRESS_2X_NONPOSITIVE"
        ):
            if (
                wf_avg is not None
                and float(wf_avg)>0.0
                and wf_stress is not None
                and float(wf_stress)
                    > -0.25*abs(float(wf_avg))
            ):
                soft.append(reason)
            else:
                hard.append(reason)

            continue

        if reason=="LONG_CORE_NONPOSITIVE":
            if (
                int(long_trades or 0)>0
                and wf_avg is not None
                and float(wf_avg)>0.0
                and long_avg is not None
                and float(long_avg)
                    > -0.25*abs(float(wf_avg))
            ):
                soft.append(reason)
            else:
                hard.append(reason)

            continue

        noncritical_shape=(
            "SENSITIVITY" in reason
            or "ROBUST" in reason
        )

        critical_shape=any(
            word in reason
            for word in (
                "MISSING",
                "INVALID",
                "ERROR",
                "CRITICAL",
                "NO_TRADES",
                "STRUCTURE",
                "CORRUPT",
            )
        )

        if (
            noncritical_shape
            and not critical_shape
        ):
            soft.append(reason)
            continue

        # Anything unknown remains HARD fail-closed.
        hard.append(reason)

    # Absolute research hard gates.
    if t<=0:
        hard.append(
            "SOFT_SHADOW_NO_TRADES"
        )

    if (
        wf_avg is None
        or float(wf_avg)<=0.0
    ):
        hard.append(
            "SOFT_SHADOW_WF_OOS_NONPOSITIVE"
        )

    # de-duplicate preserving order
    hard=list(dict.fromkeys(hard))
    soft=list(dict.fromkeys(soft))

    soft_pass=bool(
        soft
        and not hard
        and t>0
        and wf_avg is not None
        and float(wf_avg)>0.0
    )

    return {
        "soft_pass":soft_pass,
        "hard_reasons":hard,
        "soft_reasons":soft,
        "research_shadow_eligible":
            soft_pass,
        "canary_eligible":False,
    }

def evaluate(report,tf,require_fixed_window=False):
    tf=str(tf or "").upper()
    datasets=report.get("datasets") or {}
    cur_key=HARD_PASS_DATASET if HARD_PASS_DATASET in datasets else "CURRENT_35_FROM_2020"
    cur=((datasets.get(cur_key) or {}).get("timeframes") or {}).get(tf) or {}
    lng_key=HARD_PASS_LONG4_DATASET if HARD_PASS_LONG4_DATASET in datasets else "LONG_4_5Y"
    lng=((datasets.get(lng_key) or {}).get("timeframes") or {}).get(tf) or {}
    sm=cur.get("summary") or {}
    rb=cur.get("v3_robustness") or {}
    lsm=lng.get("summary") or {}
    trades=int(sm.get("trades") or 0)
    hold=finite(sm.get("avg_hold_hours"))
    dev=rb.get("development") or {}
    wf=rb.get("walk_forward") or {}
    blind=rb.get("sealed_blind_single") or {}
    dev_avg=finite(dev.get("avg_net_pct"))
    wf_avg=finite(wf.get("avg_net_pct"))
    wf_stress=finite(wf.get("cost_stress_2x_avg_net_pct"))
    long_avg=finite(lsm.get("avg_net_pct"))
    long_trades=int(lsm.get("trades") or 0)
    reasons=[]
    start_date,end_date=hard_pass_dates()
    rw=report.get("research_window") or {}
    fixed_window_ok=(
        cur_key==HARD_PASS_DATASET
        and str(rw.get("mode") or "").upper()=="HARD_PASS_ONLY"
        and str(rw.get("requested_start") or "")==start_date
        and str(rw.get("requested_end") or "")==end_date
    )
    if require_fixed_window and not fixed_window_ok:
        reasons.append("HARD_PASS_FIXED_WINDOW_MISSING_OR_MISMATCH")
    min_trades=MIN_TRADES.get(tf)
    if min_trades is None:
        reasons.append("QUALIFICATION_MIN_TRADES_POLICY_MISSING")
    elif trades < min_trades:
        reasons.append("CURRENT_TRADES_LOW")
    if dev_avg is None or dev_avg <= 0:
        reasons.append("DEVELOPMENT_EDGE_NONPOSITIVE")
    if wf_avg is None or wf_avg <= 0:
        reasons.append("WALK_FORWARD_EDGE_NONPOSITIVE")
    if wf_stress is None or wf_stress <= 0:
        reasons.append("WALK_FORWARD_COST_STRESS_2X_NONPOSITIVE")
    if long_trades>0 and (long_avg is None or long_avg < 0):
        reasons.append("LONG_CORE_NONPOSITIVE")
    passed=not reasons
    _soft_meta=_soft_shadow_qualification(
        reasons,
        trades=trades,
        min_trades=min_trades,
        wf_avg=wf_avg,
        wf_stress=wf_stress,
        long_avg=long_avg,
        long_trades=long_trades,
    )
    score=(
        wf_stress if wf_stress is not None else -1e9,
        wf_avg if wf_avg is not None else -1e9,
        dev_avg if dev_avg is not None else -1e9,
        -(hold if hold is not None else 1e9),
    )
    out={
        "pass":bool(passed),
        "hard_pass":bool(passed),
        "reasons":reasons,
        "trades":trades,
        "development_avg_net_pct":dev_avg,
        "walk_forward_avg_net_pct":wf_avg,
        "walk_forward_cost_stress_2x_avg_net_pct":wf_stress,
        "sealed_blind_status":"LEGACY_INTERNAL_HOLDOUT_NOT_FINAL_GATE",
        "sealed_blind_trades":int(blind.get("trades") or 0),
        "long4_trades":long_trades,
        "long4_avg_net_pct":long_avg,
        "expected_holding_time_hours":hold,
        "rank_tuple":score,
        "dataset_key":cur_key,
        "hard_pass_fixed_window_ok":fixed_window_ok,
        "hard_pass_research_start":start_date,
        "hard_pass_research_end":end_date,
        "soft_pass":bool(_soft_meta.get("soft_pass")),
        "hard_reasons":list(_soft_meta.get("hard_reasons") or []),
        "soft_reasons":list(_soft_meta.get("soft_reasons") or []),
        "research_shadow_eligible":bool(_soft_meta.get("soft_pass")),
        "canary_eligible":False,
    }
    out["qualification"]=(
        "HARD_PASS" if out["hard_pass"]
        else ("SOFT_PASS" if out["soft_pass"] else "FAIL")
    )
    return out


def evaluate_entry_risk_gate(report,tf,hard_eval):
    if not bool((hard_eval or {}).get("hard_pass")):
        return {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}

    tf=str(tf or "").upper()
    cur=((((report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(tf) or {})
    risk=cur.get("entry_risk") or {}
    if str(risk.get("status") or "").upper()!="MEASURED":
        return {
            "pass":False,
            "decision":"FAIL",
            "pass_mode":"NONE",
            "absolute_fail_reasons":["ENTRY_RISK_METRICS_MISSING"],
            "compensation_flags":[],
            "compensation_strengths":[],
        }

    contract=gate_contract("ENTRY_RISK")
    zone=contract.get("compensation_zone") or {}
    absolute=contract.get("absolute_fail") or {}
    mae_mfe=finite(risk.get("median_mae_to_median_mfe"))
    cvar_ratio=finite(risk.get("cvar95_to_abs_avg_loss"))
    absolute_fail=[]
    boundary=[]

    if mae_mfe is None:
        absolute_fail.append("ENTRY_RISK_MAE_MFE_MISSING")
    elif mae_mfe > float(absolute.get("median_mae_to_median_mfe_max") or 1.50):
        absolute_fail.append("ENTRY_RISK_MAE_MFE_ABSOLUTE_FAIL")
    elif mae_mfe > float(zone.get("median_mae_to_median_mfe_start") or 1.0):
        boundary.append("ENTRY_RISK_MAE_MFE_COMPENSATION_ZONE")

    if cvar_ratio is None:
        absolute_fail.append("ENTRY_RISK_CVAR_RATIO_MISSING")
    elif cvar_ratio > float(absolute.get("cvar95_to_abs_avg_loss_max") or 3.50):
        absolute_fail.append("ENTRY_RISK_CVAR_ABSOLUTE_FAIL")
    elif cvar_ratio > float(zone.get("cvar95_to_abs_avg_loss_start") or 2.5):
        boundary.append("ENTRY_RISK_CVAR_COMPENSATION_ZONE")

    if risk.get("loss_streak_distribution_abnormal_1pct") is True:
        absolute_fail.append("ENTRY_RISK_LOSS_STREAK_DISTRIBUTION_ABNORMAL")

    min_side=max(3,int((MIN_TRADES.get(tf) or 0)//5))
    long_row=risk.get("long") or {}
    short_row=risk.get("short") or {}
    lt=int(long_row.get("trades") or 0)
    st=int(short_row.get("trades") or 0)
    la=finite(long_row.get("avg_net_pct"))
    sa=finite(short_row.get("avg_net_pct"))
    if lt>=min_side and st>=min_side and la is not None and sa is not None:
        if la<=0.0 and sa<=0.0:
            absolute_fail.append("ENTRY_RISK_LONG_SHORT_BOTH_NONPOSITIVE")
        elif la<0.0<sa:
            if abs(la)>=abs(sa):
                absolute_fail.append("ENTRY_RISK_LONG_SEVERE_COLLAPSE")
            else:
                boundary.append("ENTRY_RISK_LONG_MILD_ASYMMETRY")
        elif sa<0.0<la:
            if abs(sa)>=abs(la):
                absolute_fail.append("ENTRY_RISK_SHORT_SEVERE_COLLAPSE")
            else:
                boundary.append("ENTRY_RISK_SHORT_MILD_ASYMMETRY")

    wf=finite((hard_eval or {}).get("walk_forward_avg_net_pct"))
    stress=finite((hard_eval or {}).get("walk_forward_cost_stress_2x_avg_net_pct"))
    payoff=finite(risk.get("payoff_ratio"))
    strengths=[]
    if wf is not None and wf>0.0:
        strengths.append("WALK_FORWARD_OOS")
    if stress is not None and stress>0.0:
        strengths.append("COST_STRESS_2X")
    if payoff is not None and payoff>1.0:
        strengths.append("PAYOFF")

    min_strength=int((pipeline_policy().get("gate_evaluation_model") or {}).get("minimum_compensation_strength_count") or 2)
    if absolute_fail:
        passed=False; decision="FAIL"; pass_mode="NONE"
    elif boundary and len(set(strengths))<min_strength:
        passed=False; decision="FAIL"; pass_mode="INSUFFICIENT_COMPENSATION"
    elif boundary:
        passed=True; decision="COMPENSATION_REVIEW"; pass_mode="COMPENSATED"
    else:
        passed=True; decision="PASS"; pass_mode="DIRECT"

    return {
        "pass":passed,
        "decision":decision,
        "pass_mode":pass_mode,
        "absolute_fail_reasons":list(dict.fromkeys(absolute_fail)),
        "compensation_flags":list(dict.fromkeys(boundary)),
        "compensation_strengths":list(dict.fromkeys(strengths)),
        "minimum_compensation_strength_count":min_strength,
        "metrics":risk,
    }

def _trailing_evidence(report,tf):
    cur=((((report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(str(tf).upper()) or {})
    sm=cur.get("summary") or {}
    rb=cur.get("v3_robustness") or {}
    ex=cur.get("exit_research") or {}
    wf=rb.get("walk_forward") or {}
    stress=rb.get("cost_stress_2x") or {}
    avg_win=finite(sm.get("avg_win_pct")); avg_loss=finite(sm.get("avg_loss_pct"))
    payoff=None
    if avg_win is not None and avg_loss is not None and avg_loss < 0:
        payoff=avg_win/abs(avg_loss)
    return {
        "trades":int(sm.get("trades") or 0),
        "expectancy_pct":finite(sm.get("avg_net_pct")),
        "win_rate_pct":finite(sm.get("win_rate_pct")),
        "payoff_ratio":finite(ex.get("payoff_ratio")) if finite(ex.get("payoff_ratio")) is not None else payoff,
        "walk_forward_oos_expectancy_pct":finite(wf.get("avg_net_pct")),
        "cost_stress_2x_expectancy_pct":finite(stress.get("avg_net_pct")),
        "normalized_mdd_pct_points":finite(ex.get("normalized_mdd_pct_points")),
        "dd_duration_hours":finite(ex.get("dd_duration_hours")),
        "avg_holding_time_hours":finite(ex.get("avg_hold_hours")),
        "avg_mfe_giveback_pct":finite(ex.get("avg_mfe_giveback_pct")),
        "long":ex.get("long") or {},
        "short":ex.get("short") or {},
        "trailing_stop_trades":int(ex.get("trailing_stop_trades") or 0),
    }


def _trailing_pair_key(start,callback):
    return f"{float(start):.4f}|{float(callback):.4f}"


def _ensure_stage3_entry(stage3,target):
    identity=str(target.get("canonical_identity_key") or "")
    entries=stage3.setdefault("entries",{})
    item=entries.get(identity)
    if isinstance(item,dict):
        return item
    plan=policy_research.research_exit({"mode":"INDIVIDUAL_STRATEGY_TRAILING","action":"PLAN"})
    item={
        "strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),
        "timeframe":target.get("timeframe"),
        "canonical_source_file":target.get("canonical_source_file"),
        "baseline":plan.get("baseline"),
        "coarse_candidates":plan.get("coarse_candidates") or [],
        "coarse_results":{},
        "status":"COARSE_PENDING",
        "selected":None,
        "fallback":None,
        "live_effect":False,
    }
    entries[identity]=item
    return item


def _trailing_result_binding_error(report, target, pair, source):
    """Require exact source and selected pair in every completed trailing result.

    A malformed or mismatched result is candidate-local FAIL, never a valid
    measurement and never a crash that stops the whole scheduled research run.
    """
    if not isinstance(report, dict):
        return "TRAILING_RESULT_INVALID"
    original=str(report.get("strategy_file") or "")
    if not original:
        return "TRAILING_RESULT_SOURCE_MISSING"
    path=Path(original)
    if not path.is_absolute():
        path=ROOT/path
    try:
        if path.resolve()!=source.resolve():
            return "TRAILING_RESULT_SOURCE_MISMATCH"
    except (ValueError,OSError,RuntimeError):
        return "TRAILING_RESULT_SOURCE_MISMATCH"
    values=report.get("individual_trailing_research")
    if not isinstance(values, dict):
        return "TRAILING_RESULT_CONFIG_MISSING"
    try:
        measured_start=float(values.get("start_pct"))
        measured_callback=float(values.get("callback_pct"))
        selected_start=float(pair.get("start_pct"))
        selected_callback=float(pair.get("callback_pct"))
    except (ValueError,TypeError,OverflowError):
        return "TRAILING_RESULT_CONFIG_INVALID"
    if not all(math.isfinite(n) for n in (
        measured_start,measured_callback,selected_start,selected_callback,
    )):
        return "TRAILING_RESULT_CONFIG_INVALID"
    if (abs(measured_start-selected_start)>1e-12
        or abs(measured_callback-selected_callback)>1e-12):
        return "TRAILING_RESULT_CONFIG_MISMATCH"
    return None


def _trailing_source_integrity_error(target):
    """Fail closed before measuring or selecting an exact-version trailing policy."""
    rel=str(target.get("canonical_source_file") or "").replace("\\","/")
    expected=str(target.get("source_sha256") or "").lower()
    if not rel or not re.fullmatch(r"[0-9a-f]{64}",expected):
        return "TRAILING_EXACT_SOURCE_BINDING_MISSING"
    original=ROOT/rel
    source=original.resolve()
    if (original.is_symlink() or not source.is_relative_to(ROOT.resolve())
        or not source.is_file()):
        return "TRAILING_CANONICAL_SOURCE_UNAVAILABLE"
    try:
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError:
        return "TRAILING_CANONICAL_SOURCE_UNAVAILABLE"
    if digest!=expected:
        return "TRAILING_EXACT_SOURCE_HASH_MISMATCH"
    return None


def _block_trailing_source_drift(state,target,stage3,item,reason):
    """Quarantine the affected exact version without stalling other candidates."""
    target["pipeline_stage"]="INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE"
    target["last_error"]=reason
    target["direct_canary_allowed"]=False
    target["last_checked_at"]=now()
    item["status"]="INCOMPLETE"
    if reason in {"RUN_STRATEGY_FAIL","RESULT_NOT_FOUND","TRAILING_RUN_TIMEOUT"}:
        item["runtime_error"]=reason
    else:
        item["source_integrity_error"]=reason
    item["last_checked_at"]=now()
    _save_historical_stage2(state)
    atomic(TRAILING_STAGE3,stage3)
    knowledge.record_result(
        f"pipeline_trailing_integrity:{target.get('strategy_id')}:{target.get('timeframe')}:{target.get('exact_strategy_version')}",
        {"strategy_id":target.get("strategy_id"),
         "exact_strategy_version":target.get("exact_strategy_version"),
         "pipeline_stage":target["pipeline_stage"],"reason":reason,
         "automatic_promotion":False,"canary_eligible":False},
        failed=True,
    )
    return {"status":"FAIL","reason":reason,
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":target.get("timeframe"),
            "pipeline_stage":target["pipeline_stage"],"automatic_promotion":False}


def _run_trailing_pair(state,target,stage3,item,pair,phase):
    source_error=_trailing_source_integrity_error(target)
    if source_error:
        return _block_trailing_source_drift(state,target,stage3,item,source_error)
    source=ROOT/str(target.get("canonical_source_file") or "")
    tf=str(target.get("timeframe") or "").upper()
    strategy_id=str(target.get("strategy_id") or "").upper()
    exact_version=str(target.get("exact_strategy_version") or "")
    # TRAILING_PAIR_SPECIFIC_TRIAL_ID_V1
    #
    # One trailing optimization phase evaluates multiple distinct
    # (start_pct, callback_pct) pairs. Trial-ledger dedupe is
    # candidate + timeframe + kind, therefore phase-only kind would
    # incorrectly make every later pair look like a rerun.
    #
    # Keep candidate/timeframe semantics unchanged and make `kind`
    # exact-version + exact-pair specific. Existing completed pairs
    # remain protected by Stage3 result keys and reserve_trial's
    # DO_NOT_RERUN guard remains completely unchanged.
    trial_pair_key=_trailing_pair_key(
        pair.get("start_pct"),
        pair.get("callback_pct"),
    )
    trial_version_key=exact_version or "UNVERSIONED"
    trial_kind=(
        f"INDIVIDUAL_TRAILING_{phase}"
        f"|{trial_version_key}"
        f"|{trial_pair_key}"
    )
    trial=knowledge.reserve_trial(
        (
            f"V3_TRAILING_{phase}_{strategy_id}_{tf}"
            f"|{trial_version_key}"
            f"|{trial_pair_key}"
        ),
        strategy_id,
        tf,
        trial_kind,
    )
    env=os.environ.copy(); env["BBOT_LOCAL_ROOT"]=str(ROOT)
    # Keep bounded trial CPU within the existing shared GitHub runner budget.
    env.update(OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
    research_start,research_end=hard_pass_dates()
    env["BBOT_RESEARCH_DATASET_MODE"]="HARD_PASS_ONLY"
    env["BBOT_RESEARCH_START"]=research_start
    env["BBOT_RESEARCH_END"]=research_end
    env["BBOT_RESEARCH_TRAILING_MODE"]="INDIVIDUAL_TRAILING"
    env["BBOT_RESEARCH_TRAILING_START_PCT"]=str(pair.get("start_pct"))
    env["BBOT_RESEARCH_TRAILING_CALLBACK_PCT"]=str(pair.get("callback_pct"))
    started=time.time()
    try:
        # Bound every subprocess like the existing three-worker compute pool:
        # a stalled exact-version trial cannot block unattended GitHub research.
        p=subprocess.run(
            [sys.executable,str(RUNNER),str(source)],
            cwd=str(ROOT),env=env,text=True,encoding="utf-8",errors="replace",
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=1800,
        )
    except subprocess.TimeoutExpired:
        field="coarse_results" if phase=="COARSE" else "fine_results"
        key=_trailing_pair_key(pair.get("start_pct"),pair.get("callback_pct"))
        item.setdefault(field,{})[key]={
            "status":"RUNTIME_FAIL",
            "reason":"TRAILING_RUN_TIMEOUT",
            "start_pct":pair.get("start_pct"),
            "callback_pct":pair.get("callback_pct"),
            "trial":trial,
        }
        knowledge.complete_trial(trial,"TRAILING_RUN_TIMEOUT","FAIL","UNKNOWN")
        return _block_trailing_source_drift(state,target,stage3,item,"TRAILING_RUN_TIMEOUT")
    field="coarse_results" if phase=="COARSE" else "fine_results"
    done=item.setdefault(field,{})
    key=_trailing_pair_key(pair.get("start_pct"),pair.get("callback_pct"))
    if p.returncode!=0:
        done[key]={
            "status":"RUNTIME_FAIL",
            "start_pct":pair.get("start_pct"),
            "callback_pct":pair.get("callback_pct"),
            "output_tail":p.stdout[-4000:],
            "trial":trial,
        }
        atomic(TRAILING_STAGE3,stage3)
        knowledge.complete_trial(trial,"RUN_STRATEGY_FAIL","FAIL","UNKNOWN")
        # No missing/non-MEASURED grid cell can pass frozen selection.
        # A completed failed trial is protected by the existing rerun guard.
        return _block_trailing_source_drift(state,target,stage3,item,"RUN_STRATEGY_FAIL")
    result=find_result(strategy_id,started)
    if result is None:
        try: source_name=str(load_meta(source).get("name") or "").upper()
        except Exception: source_name=""
        if source_name: result=find_result(source_name,started)
    if result is None:
        done[key]={
            "status":"RESULT_NOT_FOUND",
            "start_pct":pair.get("start_pct"),
            "callback_pct":pair.get("callback_pct"),
            "trial":trial,
        }
        atomic(TRAILING_STAGE3,stage3)
        knowledge.complete_trial(trial,"RESULT_NOT_FOUND","FAIL","UNKNOWN")
        return _block_trailing_source_drift(state,target,stage3,item,"RESULT_NOT_FOUND")
    report=load(result,{})
    binding_error=_trailing_result_binding_error(report,target,pair,source)
    if binding_error is None:
        binding_error=_trailing_source_integrity_error(target)
    if binding_error:
        done[key]={
            "status":"INVALID_RESULT_BINDING",
            "reason":binding_error,
            "start_pct":pair.get("start_pct"),
            "callback_pct":pair.get("callback_pct"),
            "trial":trial,
            "result_json":str(result.relative_to(ROOT)).replace("\\","/"),
        }
        knowledge.complete_trial(trial,binding_error,"FAIL","UNKNOWN")
        return _block_trailing_source_drift(state,target,stage3,item,binding_error)
    evidence=_trailing_evidence(report,tf)
    done[key]={
        "status":"MEASURED",
        "start_pct":float(pair.get("start_pct")),
        "callback_pct":float(pair.get("callback_pct")),
        "evidence":evidence,
        "result_json":str(result.relative_to(ROOT)).replace("\\","/"),
        "trial":trial,
    }
    item["last_checked_at"]=now()
    atomic(TRAILING_STAGE3,stage3)
    knowledge.complete_trial(trial,str(result.relative_to(ROOT)),"MEASURED","LOW")
    return {
        "status":"MEASURED",
        "strategy_id":strategy_id,
        "exact_strategy_version":exact_version,
        "timeframe":tf,
        "phase":phase,
        "pair":pair,
        "evidence":evidence,
        "phase_done":len(done),
        "phase_total":len(item.get("coarse_candidates") or []) if phase=="COARSE" else len(item.get("fine_candidates") or []),
        "automatic_promotion":False,
    }


def _trailing_frozen_results_error(target, item):
    """Authenticate each planned result before exact-version trailing selection.

    Earlier checkpoint results are not exempt from the current exact-source
    and pair binding contract. Validate original reports, not merely the
    persisted Stage3 summary. Never access files outside the research root.
    """
    source=ROOT/str(target.get("canonical_source_file") or "")
    tf=str(target.get("timeframe") or "").upper()
    for plan_key, result_key in (
        ("coarse_candidates","coarse_results"),
        ("fine_candidates","fine_results"),
    ):
        pairs=item.get(plan_key) or []
        results=item.get(result_key) or {}
        if not isinstance(pairs,list) or not isinstance(results,dict):
            return "TRAILING_FROZEN_RESULT_STRUCTURE_INVALID"
        for pair in pairs:
            if not isinstance(pair,dict):
                return "TRAILING_FROZEN_PAIR_INVALID"
            try:
                key=_trailing_pair_key(pair.get("start_pct"),pair.get("callback_pct"))
            except (TypeError,ValueError,OverflowError):
                return "TRAILING_FROZEN_PAIR_INVALID"
            row=results.get(key)
            if not isinstance(row,dict) or row.get("status")!="MEASURED":
                return "TRAILING_FROZEN_MEASUREMENT_MISSING"
            try:
                if (_trailing_pair_key(row.get("start_pct"),row.get("callback_pct"))!=key):
                    return "TRAILING_FROZEN_PAIR_MISMATCH"
            except (TypeError,ValueError,OverflowError):
                return "TRAILING_FROZEN_PAIR_MISMATCH"
            rel=str(row.get("result_json") or "").replace("\\","/")
            # run_strategy.py writes its immutable JSON into LAB/results/,
            # NOT LAB/results/v3/ (which holds Stage3 state). Restrict frozen
            # trial reads to this exact canonical output directory.
            candidate=Path(rel)
            if (candidate.is_absolute() or len(candidate.parts)!=3
                or candidate.parts[:2]!=("strategy_lab","results")
                or not candidate.name.endswith(".json")
                or ".." in candidate.parts or "." in candidate.parts):
                return "TRAILING_FROZEN_RESULT_PATH_INVALID"
            path=ROOT/candidate
            if (path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve())
                or not path.is_file()):
                return "TRAILING_FROZEN_RESULT_MISSING"
            try:
                if path.stat().st_size>32*1024**2:
                    return "TRAILING_FROZEN_RESULT_TOO_LARGE"
                report=load(path,{})
            except (OSError,ValueError):
                return "TRAILING_FROZEN_RESULT_UNREADABLE"
            problem=_trailing_result_binding_error(report,target,pair,source)
            if problem:
                return "TRAILING_FROZEN_"+problem
            if (report.get("research_window") or {}).get("mode")!="HARD_PASS_ONLY":
                return "TRAILING_FROZEN_RESEARCH_WINDOW_INVALID"
            metrics=_trailing_evidence(report,tf)
            if (not isinstance(row.get("evidence"),dict)
                or row["evidence"]!=metrics):
                return "TRAILING_FROZEN_EVIDENCE_MISMATCH"
    return None


def _finalize_trailing_selection(state,target,stage3,item):
    source_error=_trailing_source_integrity_error(target)
    if source_error:
        return _block_trailing_source_drift(state,target,stage3,item,source_error)
    evidence_error=_trailing_frozen_results_error(target,item)
    if evidence_error:
        return _block_trailing_source_drift(state,target,stage3,item,evidence_error)
    tf=str(target.get("timeframe") or "").upper()
    evaluations={}
    evaluations.update(item.get("coarse_results") or {})
    evaluations.update(item.get("fine_results") or {})
    selection=policy_research.research_exit({
        "mode":"INDIVIDUAL_STRATEGY_TRAILING",
        "action":"SELECT",
        "timeframe":tf,
        "minimum_trades":MIN_TRADES.get(tf,1),
        "evaluations":evaluations,
    })
    item["selection"]=selection
    item["status"]="COMPLETE" if selection.get("status") in {"SELECTED","FALLBACK"} else "INCOMPLETE"
    if item["status"]=="COMPLETE":
        target.pop("last_error",None)
        selected=selection.get("selected") or {"start_pct":3.0,"callback_pct":1.5}
        item["selected"]={
            "start_pct":float(selected.get("start_pct",3.0)),
            "callback_pct":float(selected.get("callback_pct",1.5)),
            "fallback":bool(selection.get("fallback")),
        }
        target["individual_trailing"]=dict(item["selected"])
        target["individual_trailing_selection"]=selection
        target["pipeline_stage"]="REENTRY_RESEARCH_PENDING"
    else:
        target["pipeline_stage"]="INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE"
        target["last_error"]=str(selection.get("reason") or "TRAILING_SELECTION_INCOMPLETE")
    target["direct_canary_allowed"]=False
    target["last_checked_at"]=now()
    _save_historical_stage2(state)
    atomic(TRAILING_STAGE3,stage3)
    knowledge.record_result(
        f"pipeline_trailing:{target.get('strategy_id')}:{tf}:{target.get('exact_strategy_version')}",
        {
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":tf,
            "pipeline_stage":target.get("pipeline_stage"),
            "individual_trailing":target.get("individual_trailing"),
            "selection":selection,
            "automatic_promotion":False,
            "canary_eligible":False,
        },
        failed=(item["status"]!="COMPLETE"),
    )
    return {
        "status":item["status"],
        "strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),
        "timeframe":tf,
        "pipeline_stage":target.get("pipeline_stage"),
        "individual_trailing":target.get("individual_trailing"),
        "selection_status":selection.get("status"),
        "selection_reason":selection.get("reason"),
        "automatic_promotion":False,
    }


_FROZEN_TRAILING_REASSESSMENT_CONTRACT="MEASURED_BASELINE_ROBUST_ALTERNATIVE_V1"


def _reassess_invalid_baseline_frozen_one(state):
    """One-time strict reevaluation of existing measured grids, never new trials.

    The legacy selector marked a measured but nonpositive comparison baseline
    INCOMPLETE before inspecting independently eligible robust alternatives.
    Preserve the sealed grid; use the existing exact-source/hash/report guard.
    No fallback to a losing baseline, no new candidate, no release authority.
    """
    stage3=load(TRAILING_STAGE3,{})
    items=stage3.get("entries") if isinstance(stage3,dict) else None
    if not isinstance(items,dict):
        return None
    reason="BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID"
    for target in state.get("entries") or []:
        if (not isinstance(target,dict) or target.get("eligible") is not True
            or target.get("pipeline_stage")!="INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE"
            or target.get("trailing_selection_reassessment_contract")==_FROZEN_TRAILING_REASSESSMENT_CONTRACT
            or target.get("last_error") not in (None,"",reason)):
            continue
        identity=str(target.get("canonical_identity_key") or "")
        item=items.get(identity)
        if not isinstance(item,dict) or item.get("status")!="INCOMPLETE":
            continue
        selection=item.get("selection") or {}
        if not isinstance(selection,dict) or selection.get("reason")!=reason:
            continue
        target["trailing_selection_reassessment_contract"]=_FROZEN_TRAILING_REASSESSMENT_CONTRACT
        target["trailing_selection_previous_reason"]=reason
        target["direct_canary_allowed"]=False
        _save_historical_stage2(state)
        outcome=_finalize_trailing_selection(state,target,stage3,item)
        return {
            "action":"TRAILING_FROZEN_GRID_REASSESSMENT",
            "status":outcome.get("status"),
            "reason":outcome.get("selection_reason") or outcome.get("reason"),
            "pipeline_stage":target.get("pipeline_stage"),
            "automatic_promotion":False,
            "orders_touched":False,
        }
    return None


def run_individual_trailing_one():
    state=load(HISTORICAL_STAGE2,{})
    frozen=_reassess_invalid_baseline_frozen_one(state)
    if frozen is not None:return frozen
    override_target=next((x for x in (state.get("entries") or []) if isinstance(x,dict) and x.get("eligible") is True and x.get("pipeline_stage")=="INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" and isinstance(x.get("redevelopment_trailing_override"),dict)),None)
    if override_target is not None:
        # 재개발 버전의 선택값도 기존 측정 경로를 거친 뒤 다음 단계로 이동한다.
        ov=dict(override_target.get("redevelopment_trailing_override") or {})
        start=finite(ov.get("start_pct")); callback=finite(ov.get("callback_pct"))
        if start is None or callback is None or not (1.0 <= start <= 6.0 and 0.5 <= callback <= 3.0 and callback < start):
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_CONFIG_INVALID","automatic_promotion":False}
        source=ROOT/str(override_target.get("canonical_source_file") or "")
        expected_hash=str(override_target.get("source_sha256") or "").lower()
        if not source.is_file() or not expected_hash or hashlib.sha256(source.read_bytes()).hexdigest()!=expected_hash:
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_SOURCE_HASH_MISMATCH","automatic_promotion":False}
        stage3=load(TRAILING_STAGE3,{"schema":"B_BOT_INDIVIDUAL_TRAILING_STAGE3_V1","entries":{},"automatic_promotion":False})
        item=_ensure_stage3_entry(stage3,override_target)
        if str(item.get("exact_strategy_version") or "")!=str(override_target.get("exact_strategy_version") or ""):
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_VERSION_MISMATCH","automatic_promotion":False}
        pair={"start_pct":start,"callback_pct":callback}
        key=_trailing_pair_key(start,callback)
        row=next((rows.get(key) for rows in (item.get("coarse_results") or {},item.get("fine_results") or {}) if key in rows),None)
        if row is None:
            return _run_trailing_pair(state,override_target,stage3,item,pair,"COARSE")
        if not isinstance(row,dict) or row.get("status")!="MEASURED":
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_MEASUREMENT_FAILED","automatic_promotion":False}
        result_rel=str(row.get("result_json") or "")
        report=load(ROOT/result_rel,{}) if result_rel else {}
        meta=report.get("individual_trailing_research") or {}
        measured_start=finite(meta.get("start_pct")); measured_callback=finite(meta.get("callback_pct"))
        if not report or measured_start is None or measured_callback is None or abs(measured_start-start)>1e-12 or abs(measured_callback-callback)>1e-12:
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_RESULT_MISSING_OR_MISMATCH","automatic_promotion":False}
        report_source=Path(str(report.get("strategy_file") or ""))
        if not report_source.is_absolute():
            report_source=ROOT/report_source
        if not report.get("strategy_file") or report_source.resolve()!=source.resolve():
            return {"status":"FAIL","reason":"REDEVELOPMENT_TRAILING_RESULT_SOURCE_MISMATCH","automatic_promotion":False}
        override_target["individual_trailing"]={**pair,"fallback":False,"redevelopment_override":True}
        override_target["individual_trailing_selection"]={"status":"SELECTED","reason":"TARGETED_FINAL_RISK_REDEVELOPMENT_MEASURED","selected":dict(override_target["individual_trailing"]),"fallback":False,"result_json":result_rel}
        item["selected"]=dict(override_target["individual_trailing"])
        item["status"]="SELECTED"; item["last_checked_at"]=now()
        atomic(TRAILING_STAGE3,stage3)
        override_target["pipeline_stage"]="REENTRY_RESEARCH_PENDING"; override_target["last_checked_at"]=now(); _save_historical_stage2(state)
        return {"status":"COMPLETE","strategy_id":override_target.get("strategy_id"),"exact_strategy_version":override_target.get("exact_strategy_version"),"timeframe":override_target.get("timeframe"),"pipeline_stage":"REENTRY_RESEARCH_PENDING","individual_trailing":override_target.get("individual_trailing"),"selection_status":"SELECTED","selection_reason":"TARGETED_FINAL_RISK_REDEVELOPMENT_MEASURED","automatic_promotion":False}
    targets=[
        x for x in (state.get("entries") or [])
        if isinstance(x,dict)
        and x.get("eligible") is True
        and x.get("pipeline_stage")=="INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING"
    ]
    if not targets:
        return {"status":"NO_TRAILING_BACKLOG","at":now()}
    target=targets[0]
    stage3=load(
        TRAILING_STAGE3,
        {"schema":"B_BOT_INDIVIDUAL_TRAILING_STAGE3_V1","entries":{},"automatic_promotion":False},
    )
    item=_ensure_stage3_entry(stage3,target)

    coarse=item.setdefault("coarse_results",{})
    pair=None
    for candidate in item.get("coarse_candidates") or []:
        key=_trailing_pair_key(candidate.get("start_pct"),candidate.get("callback_pct"))
        if key not in coarse:
            pair=candidate
            break
    if pair is not None:
        item["status"]="COARSE_RUNNING"
        return _run_trailing_pair(state,target,stage3,item,pair,"COARSE")

    if "fine_candidates" not in item:
        fine_plan=policy_research.research_exit({
            "mode":"INDIVIDUAL_STRATEGY_TRAILING",
            "action":"FINE_PLAN",
            "timeframe":target.get("timeframe"),
            "minimum_trades":MIN_TRADES.get(str(target.get("timeframe") or "").upper(),1),
            "evaluations":coarse,
        })
        item["fine_plan"]=fine_plan
        item["fine_candidates"]=fine_plan.get("fine_candidates") or []
        item["fine_results"]={}
        item["status"]="FINE_PENDING"
        atomic(TRAILING_STAGE3,stage3)
        if not item["fine_candidates"]:
            return _finalize_trailing_selection(state,target,stage3,item)
        return {
            "status":"FINE_PLAN_READY",
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":target.get("timeframe"),
            "fine_candidates":len(item["fine_candidates"]),
            "automatic_promotion":False,
        }

    fine=item.setdefault("fine_results",{})
    pair=None
    for candidate in item.get("fine_candidates") or []:
        key=_trailing_pair_key(candidate.get("start_pct"),candidate.get("callback_pct"))
        if key not in fine:
            pair=candidate
            break
    if pair is not None:
        item["status"]="FINE_RUNNING"
        return _run_trailing_pair(state,target,stage3,item,pair,"FINE")

    return _finalize_trailing_selection(state,target,stage3,item)


def _selected_trailing_result(target, stage3=None):
    stage3 = stage3 if isinstance(stage3, dict) else load(TRAILING_STAGE3,{})
    identity = str(target.get("canonical_identity_key") or "")
    item = (stage3.get("entries") or {}).get(identity) or {}
    selected = target.get("individual_trailing") or item.get("selected") or {}
    start = finite(selected.get("start_pct"))
    callback = finite(selected.get("callback_pct"))
    if start is None or callback is None:
        return None
    key = _trailing_pair_key(start, callback)
    for field in ("coarse_results", "fine_results"):
        row = (item.get(field) or {}).get(key)
        if isinstance(row, dict) and str(row.get("status") or "").upper() == "MEASURED":
            return row
    return None


def _reentry_evidence(report,tf):
    out=_trailing_evidence(report,tf)
    cur=((((report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(str(tf).upper()) or {})
    out["entry_risk"]=cur.get("entry_risk") or {}
    return out


def _ensure_stage4_entry(stage4,target):
    identity=str(target.get("canonical_identity_key") or "")
    entries=stage4.setdefault("entries",{})
    item=entries.get(identity)
    if isinstance(item,dict):
        return item
    trailing_row=_selected_trailing_result(target)
    if not isinstance(trailing_row,dict):
        raise RuntimeError("SELECTED_TRAILING_RESULT_NOT_FOUND")
    baseline_path=ROOT/str(trailing_row.get("result_json") or "")
    baseline_report=load(baseline_path,{})
    tf=str(target.get("timeframe") or "").upper()
    plan=policy_research.research_reentry({"mode":"INDIVIDUAL_STRATEGY_REENTRY","action":"PLAN"})
    item={
        "strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),
        "timeframe":tf,
        "canonical_source_file":target.get("canonical_source_file"),
        "selected_trailing":dict(target.get("individual_trailing") or {}),
        "baseline":plan.get("baseline") or {"mode":"CURRENT_STATE_NEXT_1H","enabled":True,"check_tf":"1H"},
        "baseline_research_proxy":plan.get("baseline_research_proxy") or {"mode":"CURRENT_STATE_NEXT_1H_BASELINE_PROXY"},
        "baseline_result_json":str(trailing_row.get("result_json") or ""),
        "baseline_evidence":{},
        "candidates":plan.get("candidates") or [],
        "results":{},
        "status":"REENTRY_PENDING",
        "selected":None,
        "live_effect":False,
    }
    entries[identity]=item
    return item


def _reentry_key(candidate):
    mode=str((candidate or {}).get("mode") or "").upper()
    if mode=="CURRENT_STATE_NEXT_1H_BASELINE_PROXY":
        return "CURRENT_STATE_NEXT_1H_BASELINE_PROXY"
    if mode=="CURRENT_STATE_CONFIRM_STRATEGY_BARS":
        return f"CURRENT_STATE_CONFIRM_STRATEGY_BARS|{int(candidate.get('confirm_bars'))}"
    if mode=="CURRENT_STATE_RESET_RECLAIM":
        return "CURRENT_STATE_RESET_RECLAIM"
    raise RuntimeError("REENTRY_CANDIDATE_MODE_INVALID")


def _block_reentry_integrity(state,target,stage4,item,reason):
    """Fail only the exact reentry candidate; preserve prior measured evidence."""
    target["pipeline_stage"]="REENTRY_RESEARCH_INCOMPLETE"
    target["last_error"]=reason
    target["direct_canary_allowed"]=False
    target["last_checked_at"]=now()
    item["status"]="INCOMPLETE"
    item["reentry_integrity_error"]=reason
    item["last_checked_at"]=now()
    _save_historical_stage2(state)
    atomic(REENTRY_STAGE4,stage4)
    knowledge.record_result(
        f"pipeline_reentry_integrity:{target.get('strategy_id')}:{target.get('timeframe')}:{target.get('exact_strategy_version')}",
        {"strategy_id":target.get("strategy_id"),
         "exact_strategy_version":target.get("exact_strategy_version"),
         "pipeline_stage":target["pipeline_stage"],
         "reason":reason,"automatic_promotion":False,
         "canary_eligible":False},failed=True,
    )
    return {"status":"FAIL","reason":reason,
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "pipeline_stage":target["pipeline_stage"],
            "automatic_promotion":False}


def _reentry_result_binding_error(report,target,source,start,callback,mode,confirm):
    """Bind the executed reentry method, trailing parameters and exact source."""
    problem=_trailing_result_binding_error(
        report,target,{"start_pct":start,"callback_pct":callback},source,
    )
    if problem:return "REENTRY_"+problem
    metadata=report.get("individual_reentry_research")
    if not isinstance(metadata,dict):
        return "REENTRY_RESULT_CONFIG_MISSING"
    if str(metadata.get("mode") or "").upper()!=mode:
        return "REENTRY_RESULT_CONFIG_MISMATCH"
    if mode=="CURRENT_STATE_CONFIRM_STRATEGY_BARS":
        try: bars=int(metadata.get("confirmation_bars"))
        except (ValueError,TypeError,OverflowError):
            return "REENTRY_RESULT_CONFIRMATION_INVALID"
        if bars!=confirm:return "REENTRY_RESULT_CONFIRMATION_MISMATCH"
    if (report.get("research_window") or {}).get("mode")!="HARD_PASS_ONLY":
        return "REENTRY_RESULT_WINDOW_INVALID"
    return None


def _run_reentry_candidate(state,target,stage4,item,candidate):
    source_error=_trailing_source_integrity_error(target)
    if source_error:
        return _block_reentry_integrity(state,target,stage4,item,source_error)
    source=ROOT/str(target.get("canonical_source_file") or "")
    tf=str(target.get("timeframe") or "").upper()
    strategy_id=str(target.get("strategy_id") or "").upper()
    exact_version=str(target.get("exact_strategy_version") or "")
    trailing=target.get("individual_trailing") or {}
    start=finite(trailing.get("start_pct")); callback=finite(trailing.get("callback_pct"))
    if start is None or callback is None:
        return {"status":"FAIL","reason":"SELECTED_TRAILING_MISSING"}
    mode=str(candidate.get("mode") or "").upper()
    confirm=int(candidate.get("confirm_bars") or 0)
    token=("BASE" if mode=="CURRENT_STATE_NEXT_1H_BASELINE_PROXY" else "C"+str(confirm) if mode=="CURRENT_STATE_CONFIRM_STRATEGY_BARS" else "RESET")
    trial=knowledge.reserve_trial(
        f"V3_REENTRY_METHOD_{token}_{strategy_id}_{tf}_STAGE13",
        f"{strategy_id}|{exact_version}|{token}",tf,"INDIVIDUAL_REENTRY_METHOD",
    )
    env=os.environ.copy(); env["BBOT_LOCAL_ROOT"]=str(ROOT)
    research_start,research_end=hard_pass_dates()
    env["BBOT_RESEARCH_DATASET_MODE"]="HARD_PASS_ONLY"
    env["BBOT_RESEARCH_START"]=research_start
    env["BBOT_RESEARCH_END"]=research_end
    env["BBOT_RESEARCH_TRAILING_MODE"]="INDIVIDUAL_TRAILING"
    env["BBOT_RESEARCH_TRAILING_START_PCT"]=str(start)
    env["BBOT_RESEARCH_TRAILING_CALLBACK_PCT"]=str(callback)
    env["BBOT_RESEARCH_REENTRY_MODE"]=mode
    if mode=="CURRENT_STATE_CONFIRM_STRATEGY_BARS":
        env["BBOT_RESEARCH_REENTRY_CONFIRM_BARS"]=str(confirm)
    started=time.time()
    try:
        p=subprocess.run(
            [sys.executable,str(RUNNER),str(source)],
            cwd=str(ROOT),env=env,text=True,encoding="utf-8",errors="replace",
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=1800,
        )
    except subprocess.TimeoutExpired:
        key=_reentry_key(candidate)
        item.setdefault("results",{})[key]={
            "status":"RUNTIME_FAIL","reason":"REENTRY_RUN_TIMEOUT",
            "mode":mode,"confirm_bars":confirm if confirm else None,
            "runtime_equivalent":False,"automatic_deployment_allowed":False,
            "trial":trial,
        }
        knowledge.complete_trial(trial,"REENTRY_RUN_TIMEOUT","FAIL","UNKNOWN")
        return _block_reentry_integrity(state,target,stage4,item,"REENTRY_RUN_TIMEOUT")
    key=_reentry_key(candidate); done=item.setdefault("results",{})
    base_row={
        "mode":mode,
        "confirm_bars":confirm if confirm else None,
        "runtime_equivalent":False,
        "research_resolution":"STRATEGY_TIMEFRAME_PROXY",
        "automatic_deployment_allowed":False,
    }
    if p.returncode!=0:
        done[key]={"status":"RUNTIME_FAIL",**base_row,"output_tail":p.stdout[-4000:],"trial":trial}
        atomic(REENTRY_STAGE4,stage4)
        knowledge.complete_trial(trial,"RUN_STRATEGY_FAIL","FAIL","UNKNOWN")
        return {"status":"FAIL","reason":"RUN_STRATEGY_FAIL","candidate":candidate,"trial":trial}
    result=find_result(strategy_id,started)
    if result is None:
        try: source_name=str(load_meta(source).get("name") or "").upper()
        except Exception: source_name=""
        if source_name: result=find_result(source_name,started)
    if result is None:
        done[key]={"status":"RESULT_NOT_FOUND",**base_row,"trial":trial}
        atomic(REENTRY_STAGE4,stage4)
        knowledge.complete_trial(trial,"RESULT_NOT_FOUND","FAIL","UNKNOWN")
        return {"status":"FAIL","reason":"RESULT_NOT_FOUND","candidate":candidate,"trial":trial}
    report=load(result,{})
    binding_error=_reentry_result_binding_error(
        report,target,source,start,callback,mode,confirm,
    )
    if binding_error is None:
        binding_error=_trailing_source_integrity_error(target)
    if binding_error:
        done[key]={
            "status":"INVALID_RESULT_BINDING",**base_row,
            "reason":binding_error,"trial":trial,
            "result_json":str(result.relative_to(ROOT)).replace("\\","/"),
        }
        knowledge.complete_trial(trial,binding_error,"FAIL","UNKNOWN")
        return _block_reentry_integrity(state,target,stage4,item,binding_error)
    evidence=_reentry_evidence(report,tf)
    done[key]={
        "status":"MEASURED",**base_row,"evidence":evidence,
        "result_json":str(result.relative_to(ROOT)).replace("\\","/"),"trial":trial,
    }
    if mode=="CURRENT_STATE_NEXT_1H_BASELINE_PROXY":
        item["baseline_proxy_result_json"]=done[key]["result_json"]
        item["baseline_evidence"]=evidence
    item["last_checked_at"]=now(); atomic(REENTRY_STAGE4,stage4)
    knowledge.complete_trial(trial,str(result.relative_to(ROOT)),"MEASURED","LOW")
    return {
        "status":"MEASURED","strategy_id":strategy_id,"exact_strategy_version":exact_version,
        "timeframe":tf,"candidate":candidate,"evidence":evidence,
        "runtime_equivalent":False,"automatic_deployment_allowed":False,
        "done":len(done),"total":len(item.get("candidates") or [])+1,"automatic_promotion":False,
    }


def _finalize_reentry_selection(state,target,stage4,item):
    tf=str(target.get("timeframe") or "").upper()
    selection=policy_research.research_reentry({
        "mode":"INDIVIDUAL_STRATEGY_REENTRY",
        "action":"SELECT",
        "timeframe":tf,
        "minimum_trades":MIN_TRADES.get(tf,1),
        "baseline_evidence":item.get("baseline_evidence") or {},
        "evaluations":item.get("results") or {},
    })
    item["selection"]=selection
    item["status"]="COMPLETE" if selection.get("status") in {"SELECTED","FALLBACK"} else "INCOMPLETE"
    if item["status"]=="COMPLETE":
        selected=selection.get("selected") or {"mode":"CURRENT_STATE_NEXT_1H","enabled":True,"check_tf":"1H"}
        if str(selected.get("mode") or "").upper()!="CURRENT_STATE_NEXT_1H":
            raise RuntimeError("NONBASELINE_REENTRY_BINDING_FORBIDDEN_WITHOUT_EXACT_REPLAY")
        final_result=str(item.get("baseline_proxy_result_json") or "")
        if not final_result:
            raise RuntimeError("REENTRY_BASELINE_PROXY_RESULT_MISSING")
        target["individual_reentry"]={
            "mode":"CURRENT_STATE_NEXT_1H",
            "enabled":True,
            "check_tf":"1H",
            "fallback":True,
            "research_proxy_only":False,
            "no_improvement_preserves_existing_policy":True,
        }
        target["reentry_selection"]=selection
        target["final_strategy_result_json"]=final_result
        target["pipeline_stage"]="FINAL_RISK_REVALIDATION_PENDING"
        item["selected"]=dict(target["individual_reentry"])
        item["final_strategy_result_json"]=final_result
    else:
        target["pipeline_stage"]="REENTRY_RESEARCH_INCOMPLETE"
    target["reentry_contract_version"]="CURRENT_STATE_METHOD_RESEARCH_STAGE13_V1"
    target["direct_canary_allowed"]=False; target["last_checked_at"]=now()
    _save_historical_stage2(state); atomic(REENTRY_STAGE4,stage4)
    knowledge.record_result(
        f"pipeline_reentry_stage13:{target.get('strategy_id')}:{tf}:{target.get('exact_strategy_version')}",
        {
            "strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":tf,"pipeline_stage":target.get("pipeline_stage"),
            "individual_trailing":target.get("individual_trailing"),"individual_reentry":target.get("individual_reentry"),
            "selection":selection,"automatic_promotion":False,"canary_eligible":False,
        },
        failed=(item["status"]!="COMPLETE"),
    )
    return {
        "status":item["status"],"strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),"timeframe":tf,
        "pipeline_stage":target.get("pipeline_stage"),"individual_reentry":target.get("individual_reentry"),
        "selection_status":selection.get("status"),"selection_reason":selection.get("reason"),
        "automatic_promotion":False,
    }


def migrate_reentry_contract_stage13():
    """Invalidate only Stage4-era REENTRY research evidence/descendants.

    Existing live Strategy Instances and open Legs are not edited here.
    Blind windows already exposed remain consumed in the blind ledger.
    """
    state=load(HISTORICAL_STAGE2,{})
    stage4=load(REENTRY_STAGE4,{"schema":"B_BOT_REENTRY_FINAL_RISK_STAGE4_V1","entries":{},"automatic_promotion":False})
    active=stage4.setdefault("entries",{})
    legacy=stage4.setdefault("legacy_invalidated_stage13",{})
    reset=0; stage4_invalidated=0; untouched=0
    bad_modes={"BLOCK_UNTIL_SIGNAL_RESET","COOLDOWN_BARS","NEXT_BAR","WAIT_1_BAR","WAIT_2_BARS"}
    downstream_fields=(
        "individual_reentry","reentry_selection","reentry_result_json","final_strategy_result_json",
        "final_risk_gate","full_validation_gate","sealed_final_blind","sealed_final_blind_gate",
        "local_shadow_gate","local_shadow_result","canary_eligible",
    )
    for target in (state.get("entries") or []):
        if not isinstance(target,dict) or target.get("eligible") is not True:
            continue
        identity=str(target.get("canonical_identity_key") or "")
        item=active.get(identity) if identity else None
        item_old=False
        if isinstance(item,dict):
            baseline=str((item.get("baseline") or {}).get("mode") or "").upper()
            candidates=item.get("candidates") or []
            item_old=(baseline=="BLOCK_UNTIL_SIGNAL_RESET" or any(str((c or {}).get("mode") or "").upper()=="COOLDOWN_BARS" for c in candidates if isinstance(c,dict)))
            if item_old or target.get("pipeline_stage")=="REENTRY_RESEARCH_PENDING":
                archived=dict(item)
                archived["stage13_invalidated_at"]=now()
                archived["stage13_invalidation_reason"]="STAGE4_REENTRY_CONTRACT_REPLACED_BY_CURRENT_STATE_METHOD_RESEARCH"
                legacy[identity]=archived
                active.pop(identity,None)
                stage4_invalidated+=1
        selected=target.get("individual_reentry") or {}
        mode=str(selected.get("mode") or "").upper()
        old_contract=str(target.get("reentry_contract_version") or "")
        needs_reset=(mode in bad_modes or (mode and mode!="CURRENT_STATE_NEXT_1H") or (mode and old_contract!="CURRENT_STATE_METHOD_RESEARCH_STAGE13_V1"))
        if needs_reset:
            for key in downstream_fields:
                target.pop(key,None)
            target["pipeline_stage"]="REENTRY_RESEARCH_PENDING"
            target["reentry_contract_version"]="CURRENT_STATE_METHOD_RESEARCH_STAGE13_V1_PENDING"
            target["reentry_stage13_migration"]={
                "at":now(),
                "reason":"INVALIDATE_STAGE4_BLOCK_OR_COOLDOWN_REENTRY_RESEARCH",
                "previous_mode":mode,
                "previous_contract_version":old_contract,
                "existing_live_strategy_instances_untouched":True,
                "open_positions_untouched":True,
                "blind_exposure_ledger_preserved":True,
            }
            target["direct_canary_allowed"]=False
            target["last_checked_at"]=now()
            reset+=1
        else:
            untouched+=1
    stage4["schema"]="B_BOT_REENTRY_METHOD_RESEARCH_STAGE13_V1"
    stage4["stage13_migration"]={"at":now(),"reset_pipeline_targets":reset,"invalidated_stage4_entries":stage4_invalidated}
    _save_historical_stage2(state)
    atomic(REENTRY_STAGE4,stage4)
    return {"status":"PASS","reset_pipeline_targets":reset,"invalidated_stage4_entries":stage4_invalidated,"untouched_targets":untouched}


def run_individual_reentry_one():
    state=load(HISTORICAL_STAGE2,{})
    targets=[
        x for x in (state.get("entries") or [])
        if isinstance(x,dict) and x.get("eligible") is True
        and x.get("pipeline_stage")=="REENTRY_RESEARCH_PENDING"
    ]
    if not targets:
        return {"status":"NO_REENTRY_BACKLOG","at":now()}
    target=targets[0]
    stage4=load(REENTRY_STAGE4,{"schema":"B_BOT_REENTRY_FINAL_RISK_STAGE4_V1","entries":{},"automatic_promotion":False})
    # 이전 코드가 측정 결과 없이 넘긴 재개발 항목은 원래 측정 단계로 복구한다.
    try:
        item=_ensure_stage4_entry(stage4,target)
    except RuntimeError as exc:
        if str(exc)!="SELECTED_TRAILING_RESULT_NOT_FOUND" or not isinstance(target.get("redevelopment_trailing_override"),dict):
            raise
        target["pipeline_stage"]="INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING"
        target["direct_canary_allowed"]=False
        target["last_checked_at"]=now()
        target["trailing_evidence_recovery"]={"reason":"SELECTED_TRAILING_RESULT_NOT_FOUND","at":now(),"automatic_promotion":False}
        _save_historical_stage2(state)
        return {"status":"TRAILING_MEASUREMENT_REQUIRED","strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),"timeframe":target.get("timeframe"),"pipeline_stage":target["pipeline_stage"],"automatic_promotion":False}
    done=item.setdefault("results",{})
    baseline_proxy=item.get("baseline_research_proxy") or {"mode":"CURRENT_STATE_NEXT_1H_BASELINE_PROXY"}
    if _reentry_key(baseline_proxy) not in done:
        item["results"]=done; atomic(REENTRY_STAGE4,stage4)
        return _run_reentry_candidate(state,target,stage4,item,baseline_proxy)
    for candidate in item.get("candidates") or []:
        if _reentry_key(candidate) not in done:
            item["results"]=done; atomic(REENTRY_STAGE4,stage4)
            return _run_reentry_candidate(state,target,stage4,item,candidate)
    return _finalize_reentry_selection(state,target,stage4,item)


def _safe_ratio(final_value, baseline_value):
    f=finite(final_value); b=finite(baseline_value)
    if f is None or b is None:
        return None
    if b <= 1e-12:
        return 1.0 if f <= 1e-12 else float("inf")
    return max(0.0,float(f))/max(1e-12,float(b))


def evaluate_final_risk_gate(target,final_report):
    tf=str(target.get("timeframe") or "").upper()
    base_path=ROOT/str(target.get("new_result_json") or "")
    base_report=load(base_path,{})
    if not base_report:
        return {"pass":False,"decision":"FAIL","pass_mode":"NONE","absolute_fail_reasons":["FINAL_RISK_HARD_PASS_BASELINE_MISSING"]}
    base_ev=_trailing_evidence(base_report,tf)
    final_ev=_trailing_evidence(final_report,tf)
    base_cur=((((base_report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(tf) or {})
    final_cur=((((final_report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(tf) or {})
    base_risk=base_cur.get("entry_risk") or {}
    final_risk=final_cur.get("entry_risk") or {}
    contract=gate_contract("FINAL_RISK_REVALIDATION")
    zone=contract.get("compensation_zone") or {}; absolute=contract.get("absolute_fail") or {}
    absolute_fail=[]; boundary=[]

    wf=finite(final_ev.get("walk_forward_oos_expectancy_pct")); stress=finite(final_ev.get("cost_stress_2x_expectancy_pct"))
    if wf is None or wf<=0.0: absolute_fail.append("FINAL_RISK_WF_OOS_NONPOSITIVE")
    if stress is None or stress<=0.0: absolute_fail.append("FINAL_RISK_COST_STRESS_2X_NONPOSITIVE")

    ratios={
        "mdd":_safe_ratio(final_ev.get("normalized_mdd_pct_points"),base_ev.get("normalized_mdd_pct_points")),
        "dd_duration":_safe_ratio(final_ev.get("dd_duration_hours"),base_ev.get("dd_duration_hours")),
        "cvar95":_safe_ratio(final_risk.get("cvar95_loss_pct_abs"),base_risk.get("cvar95_loss_pct_abs")),
        "holding":_safe_ratio(final_ev.get("avg_holding_time_hours"),base_ev.get("avg_holding_time_hours")),
    }
    streak_add=int(final_risk.get("max_consecutive_losses") or 0)-int(base_risk.get("max_consecutive_losses") or 0)

    checks=[
        ("mdd",ratios["mdd"],float(zone.get("mdd_vs_hard_pass_ratio_start") or 1.25),float(absolute.get("mdd_vs_hard_pass_max_ratio") or 1.75),"FINAL_RISK_MDD"),
        ("dd_duration",ratios["dd_duration"],float(zone.get("dd_duration_vs_hard_pass_ratio_start") or 1.30),float(absolute.get("dd_duration_vs_hard_pass_max_ratio") or 1.75),"FINAL_RISK_DD_DURATION"),
        ("cvar95",ratios["cvar95"],float(zone.get("cvar95_vs_entry_risk_ratio_start") or 1.20),float(absolute.get("cvar95_vs_entry_risk_max_ratio") or 1.60),"FINAL_RISK_CVAR95"),
        ("holding",ratios["holding"],float(zone.get("holding_or_exposure_vs_hard_pass_ratio_start") or 1.30),float(absolute.get("holding_or_exposure_vs_hard_pass_max_ratio") or 1.75),"FINAL_RISK_HOLDING"),
    ]
    for _,value,zone_start,abs_max,label in checks:
        if value is None:
            absolute_fail.append(label+"_MISSING")
        elif value>abs_max:
            absolute_fail.append(label+"_ABSOLUTE_FAIL")
        elif value>zone_start:
            boundary.append(label+"_COMPENSATION_ZONE")

    streak_zone=int(zone.get("max_consecutive_losses_vs_hard_pass_add_start") or 2)
    streak_abs=int(absolute.get("max_consecutive_losses_vs_hard_pass_max_add") or 5)
    if streak_add>streak_abs: absolute_fail.append("FINAL_RISK_LOSS_STREAK_ABSOLUTE_FAIL")
    elif streak_add>streak_zone: boundary.append("FINAL_RISK_LOSS_STREAK_COMPENSATION_ZONE")

    min_side=max(3,int((MIN_TRADES.get(tf) or 0)//5))
    long_row=final_risk.get("long") or {}; short_row=final_risk.get("short") or {}
    lt=int(long_row.get("trades") or 0); st=int(short_row.get("trades") or 0)
    la=finite(long_row.get("avg_net_pct")); sa=finite(short_row.get("avg_net_pct"))
    if lt>=min_side and st>=min_side and la is not None and sa is not None:
        if la<=0.0 and sa<=0.0:
            absolute_fail.append("FINAL_RISK_LONG_SHORT_BOTH_NONPOSITIVE")
        elif la<0.0<sa:
            (absolute_fail if abs(la)>=abs(sa) else boundary).append("FINAL_RISK_LONG_COLLAPSE" if abs(la)>=abs(sa) else "FINAL_RISK_LONG_MILD_ASYMMETRY")
        elif sa<0.0<la:
            (absolute_fail if abs(sa)>=abs(la) else boundary).append("FINAL_RISK_SHORT_COLLAPSE" if abs(sa)>=abs(la) else "FINAL_RISK_SHORT_MILD_ASYMMETRY")

    strengths=[]
    base_expect=finite(base_ev.get("expectancy_pct")); final_expect=finite(final_ev.get("expectancy_pct"))
    if final_expect is not None and base_expect is not None and final_expect>base_expect+1e-12: strengths.append("EXPECTANCY_IMPROVEMENT")
    base_payoff=finite(base_ev.get("payoff_ratio")); final_payoff=finite(final_ev.get("payoff_ratio"))
    if final_payoff is not None and base_payoff is not None and final_payoff>base_payoff+1e-12: strengths.append("PAYOFF_IMPROVEMENT")
    if ratios.get("dd_duration") is not None and ratios["dd_duration"]<1.0-1e-12: strengths.append("DD_RECOVERY_SPEED")
    base_wf=finite(base_ev.get("walk_forward_oos_expectancy_pct"))
    if wf is not None and base_wf is not None and wf>=base_wf-1e-12: strengths.append("OOS_STRENGTH")
    base_stress=finite(base_ev.get("cost_stress_2x_expectancy_pct"))
    if stress is not None and base_stress is not None and stress>=base_stress-1e-12: strengths.append("ROBUSTNESS_STRENGTH")

    min_strength=int((pipeline_policy().get("gate_evaluation_model") or {}).get("minimum_compensation_strength_count") or 2)
    if absolute_fail:
        passed=False; decision="FAIL"; pass_mode="NONE"
    elif boundary and len(set(strengths))<min_strength:
        passed=False; decision="FAIL"; pass_mode="INSUFFICIENT_COMPENSATION"
    elif boundary:
        passed=True; decision="COMPENSATION_REVIEW"; pass_mode="COMPENSATED"
    else:
        passed=True; decision="PASS"; pass_mode="DIRECT"
    return {
        "pass":passed,"decision":decision,"pass_mode":pass_mode,
        "absolute_fail_reasons":list(dict.fromkeys(absolute_fail)),
        "compensation_flags":list(dict.fromkeys(boundary)),
        "compensation_strengths":list(dict.fromkeys(strengths)),
        "minimum_compensation_strength_count":min_strength,
        "ratios":ratios,"max_consecutive_losses_add":streak_add,
        "baseline":{"exit":base_ev,"entry_risk":base_risk},
        "final":{"exit":final_ev,"entry_risk":final_risk},
    }


def run_final_risk_one():
    state=load(HISTORICAL_STAGE2,{})
    targets=[
        x for x in (state.get("entries") or [])
        if isinstance(x,dict) and x.get("eligible") is True
        and x.get("pipeline_stage")=="FINAL_RISK_REVALIDATION_PENDING"
    ]
    if not targets:
        return {"status":"NO_FINAL_RISK_BACKLOG","at":now()}
    target=targets[0]; tf=str(target.get("timeframe") or "").upper()
    final_path=ROOT/str(target.get("final_strategy_result_json") or "")
    final_report=load(final_path,{})
    if not final_report:
        target["pipeline_stage"]="FINAL_RISK_REVALIDATION_RESULT_MISSING"
        target["last_error"]="FINAL_STRATEGY_RESULT_MISSING"; _save_historical_stage2(state)
        return {"status":"FAIL","reason":"FINAL_STRATEGY_RESULT_MISSING","strategy_id":target.get("strategy_id"),"timeframe":tf}
    gate=evaluate_final_risk_gate(target,final_report)
    target["final_risk_gate"]=gate
    target["pipeline_stage"]=("FULL_VALIDATION_PENDING" if bool(gate.get("pass")) else "FINAL_RISK_REDEVELOPMENT_PENDING")
    target["direct_canary_allowed"]=False; target["last_checked_at"]=now(); _save_historical_stage2(state)
    stage4=load(REENTRY_STAGE4,{"schema":"B_BOT_REENTRY_FINAL_RISK_STAGE4_V1","entries":{},"automatic_promotion":False})
    identity=str(target.get("canonical_identity_key") or ""); item=(stage4.setdefault("entries",{})).setdefault(identity,{})
    item["final_risk_gate"]=gate; item["pipeline_stage"]=target.get("pipeline_stage"); item["last_checked_at"]=now(); atomic(REENTRY_STAGE4,stage4)
    knowledge.record_result(
        f"pipeline_final_risk:{target.get('strategy_id')}:{tf}:{target.get('exact_strategy_version')}",
        {
            "strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":tf,"pipeline_stage":target.get("pipeline_stage"),"final_risk_gate":gate,
            "individual_trailing":target.get("individual_trailing"),"individual_reentry":target.get("individual_reentry"),
            "automatic_promotion":False,"canary_eligible":False,
        },failed=(not bool(gate.get("pass"))),
    )
    return {
        "status":"PASS" if bool(gate.get("pass")) else "FAIL",
        "strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),
        "timeframe":tf,"pipeline_stage":target.get("pipeline_stage"),
        "final_risk_decision":gate.get("decision"),"final_risk_pass_mode":gate.get("pass_mode"),
        "automatic_promotion":False,
    }



def _full_validation_symbol_slices(final_report, tf):
    cur=((((final_report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(str(tf).upper()) or {})
    rows=[]
    for symbol,row in sorted((cur.get("symbol_evidence") or {}).items()):
        if not isinstance(row,dict):
            continue
        rb=row.get("v3_robustness") or {}
        wf=rb.get("walk_forward") or {}
        sm=row.get("summary") or {}
        wf_avg=finite(wf.get("avg_net_pct"))
        stress=finite(wf.get("cost_stress_2x_avg_net_pct"))
        trades=int(sm.get("trades") or 0)
        if trades<=0 or wf_avg is None or stress is None:
            continue
        rows.append({
            "symbol":str(symbol).upper(),
            "trades":trades,
            "walk_forward_oos_expectancy_pct":wf_avg,
            "cost_stress_2x_expectancy_pct":stress,
            "positive":bool(wf_avg>0.0 and stress>0.0),
        })
    return rows


def evaluate_full_validation_gate(target, final_report):
    tf=str(target.get("timeframe") or "").upper()
    contract=gate_contract("FULL_VALIDATION")
    absolute=contract.get("absolute_fail") or {}
    zone=contract.get("compensation_zone") or {}
    limits=contract.get("absolute_limits") or {}
    ev=_trailing_evidence(final_report,tf)
    cur=((((final_report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(tf) or {})
    ex=cur.get("exit_research") or {}
    risk=cur.get("entry_risk") or {}
    absolute_fail=[]; boundary=[]; strengths=[]

    wf=finite(ev.get("walk_forward_oos_expectancy_pct"))
    stress=finite(ev.get("cost_stress_2x_expectancy_pct"))
    if wf is None or wf<=0.0:
        absolute_fail.append("FULL_VALIDATION_WF_OOS_NONPOSITIVE")
    if stress is None or stress<=0.0:
        absolute_fail.append("FULL_VALIDATION_COST_STRESS_2X_NONPOSITIVE")

    min_trades=int(MIN_TRADES.get(tf) or 0)
    trades=int(ev.get("trades") or 0)
    if min_trades<=0 or trades<min_trades:
        absolute_fail.append("FULL_VALIDATION_MINIMUM_TRADES_FAIL")

    final_risk=target.get("final_risk_gate") or {}
    if final_risk.get("pass") is not True:
        absolute_fail.append("FULL_VALIDATION_FINAL_RISK_NOT_PASSED")

    slices=_full_validation_symbol_slices(final_report,tf)
    positive_count=sum(1 for x in slices if x.get("positive"))
    positive_ratio=(positive_count/float(len(slices))) if slices else 0.0
    robust_start=float(zone.get("positive_robustness_slice_ratio_start") or 0.70)
    robust_abs=float(limits.get("positive_robustness_slice_ratio_min") or 0.50)
    if positive_ratio < robust_abs-1e-12:
        absolute_fail.append("FULL_VALIDATION_ROBUSTNESS_ABSOLUTE_FAIL")
    elif positive_ratio < robust_start-1e-12:
        boundary.append("FULL_VALIDATION_ROBUSTNESS_COMPENSATION_ZONE")
    else:
        strengths.append("ROBUSTNESS")

    concentration=finite(ex.get("top_5pct_trade_net_profit_share"))
    conc_start=float(zone.get("top_5pct_trade_profit_share_start") or 0.50)
    conc_abs=float(limits.get("top_5pct_trade_profit_share_max") or 0.70)
    if concentration is None:
        absolute_fail.append("FULL_VALIDATION_PROFIT_CONCENTRATION_MISSING")
    elif concentration > conc_abs+1e-12:
        absolute_fail.append("FULL_VALIDATION_PROFIT_CONCENTRATION_ABSOLUTE_FAIL")
    elif concentration > conc_start+1e-12:
        boundary.append("FULL_VALIDATION_PROFIT_CONCENTRATION_COMPENSATION_ZONE")
    else:
        strengths.append("PROFIT_DIVERSIFICATION")

    regimes=ex.get("regimes") or {}
    positive_regimes=[]
    measured_regimes=[]
    for name in ("BULL","BEAR","SIDEWAYS"):
        row=regimes.get(name) or {}
        rt=int(row.get("trades") or 0)
        ravg=finite(row.get("avg_net_pct"))
        if rt>=3 and ravg is not None:
            measured_regimes.append(name)
            if ravg>0.0:
                positive_regimes.append(name)
    required_regimes=int(zone.get("minimum_positive_regime_classes") or 2)
    if len(positive_regimes)==0:
        absolute_fail.append("FULL_VALIDATION_REGIME_TOTAL_COLLAPSE")
    elif len(positive_regimes)<required_regimes:
        boundary.append("FULL_VALIDATION_REGIME_COMPENSATION_ZONE")
    else:
        strengths.append("REGIME_STABILITY")

    trailing_selection=target.get("individual_trailing_selection") or {}
    trailing_fallback=bool((target.get("individual_trailing") or {}).get("fallback"))
    if not trailing_fallback:
        neighbor_ratio=finite(trailing_selection.get("positive_neighbor_ratio"))
        if neighbor_ratio is None:
            absolute_fail.append("FULL_VALIDATION_PARAMETER_STABILITY_MISSING")
        elif neighbor_ratio < 0.50-1e-12:
            absolute_fail.append("FULL_VALIDATION_NEIGHBOR_PARAMETER_COLLAPSE")
        elif neighbor_ratio < 0.70-1e-12:
            boundary.append("FULL_VALIDATION_PARAMETER_STABILITY_COMPENSATION_ZONE")
        else:
            strengths.append("PARAMETER_STABILITY")
    else:
        strengths.append("BASELINE_FALLBACK_STABILITY")

    min_side=max(3,int((MIN_TRADES.get(tf) or 0)//5))
    long_row=risk.get("long") or {}; short_row=risk.get("short") or {}
    lt=int(long_row.get("trades") or 0); st=int(short_row.get("trades") or 0)
    la=finite(long_row.get("avg_net_pct")); sa=finite(short_row.get("avg_net_pct"))
    if lt>=min_side and st>=min_side and la is not None and sa is not None:
        if la<=0.0 and sa<=0.0:
            absolute_fail.append("FULL_VALIDATION_LONG_SHORT_BOTH_NONPOSITIVE")
        elif la<0.0<sa or sa<0.0<la:
            bad=abs(la) if la<0.0 else abs(sa)
            good=sa if la<0.0 else la
            if bad>=abs(good):
                absolute_fail.append("FULL_VALIDATION_LONG_SHORT_COLLAPSE")
            else:
                boundary.append("FULL_VALIDATION_LONG_SHORT_ASYMMETRY")

    base_path=ROOT/str(target.get("new_result_json") or "")
    base_report=load(base_path,{})
    base_ev=_trailing_evidence(base_report,tf) if base_report else {}
    base_expect=finite(base_ev.get("expectancy_pct")); final_expect=finite(ev.get("expectancy_pct"))
    if final_expect is not None and base_expect is not None and final_expect>base_expect+1e-12:
        strengths.append("EXPECTANCY")
    base_payoff=finite(base_ev.get("payoff_ratio")); final_payoff=finite(ev.get("payoff_ratio"))
    if final_payoff is not None and base_payoff is not None and final_payoff>base_payoff+1e-12:
        strengths.append("PAYOFF")
    if wf is not None and wf>0.0:
        strengths.append("OOS_STRENGTH")
    if stress is not None and stress>0.0:
        strengths.append("COST_STRESS_2X")

    min_strength=int((pipeline_policy().get("gate_evaluation_model") or {}).get("minimum_compensation_strength_count") or 2)
    unique_strengths=list(dict.fromkeys(strengths))
    if absolute_fail:
        passed=False; decision="FAIL"; pass_mode="NONE"
    elif boundary and len(unique_strengths)<min_strength:
        passed=False; decision="FAIL"; pass_mode="INSUFFICIENT_COMPENSATION"
    elif boundary:
        passed=True; decision="COMPENSATION_REVIEW"; pass_mode="COMPENSATED"
    else:
        passed=True; decision="PASS"; pass_mode="DIRECT"
    return {
        "pass":passed,"decision":decision,"pass_mode":pass_mode,
        "absolute_fail_reasons":list(dict.fromkeys(absolute_fail)),
        "compensation_flags":list(dict.fromkeys(boundary)),
        "compensation_strengths":unique_strengths,
        "minimum_compensation_strength_count":min_strength,
        "metrics":{
            "trades":trades,"positive_robustness_slice_ratio":positive_ratio,
            "positive_robustness_slices":positive_count,"measured_robustness_slices":len(slices),
            "top_5pct_trade_net_profit_share":concentration,
            "measured_regime_classes":measured_regimes,"positive_regime_classes":positive_regimes,
        },
    }


def run_full_validation_one():
    state=load(HISTORICAL_STAGE2,{})
    targets=[x for x in (state.get("entries") or []) if isinstance(x,dict) and x.get("eligible") is True and x.get("pipeline_stage")=="FULL_VALIDATION_PENDING"]
    if not targets:
        return {"status":"NO_FULL_VALIDATION_BACKLOG","at":now()}
    target=targets[0]; tf=str(target.get("timeframe") or "").upper()
    final_path=ROOT/str(target.get("final_strategy_result_json") or "")
    final_report=load(final_path,{})
    if not final_report:
        target["pipeline_stage"]="FULL_VALIDATION_RESULT_MISSING"; target["last_error"]="FINAL_STRATEGY_RESULT_MISSING"; _save_historical_stage2(state)
        return {"status":"FAIL","reason":"FINAL_STRATEGY_RESULT_MISSING","strategy_id":target.get("strategy_id"),"timeframe":tf}
    gate=evaluate_full_validation_gate(target,final_report)
    target["full_validation_gate"]=gate
    target["pipeline_stage"]=("SEALED_FINAL_BLIND_PENDING" if bool(gate.get("pass")) else "FULL_VALIDATION_REDEVELOPMENT_PENDING")
    target["direct_canary_allowed"]=False; target["last_checked_at"]=now(); _save_historical_stage2(state)
    stage=load(FULL_VALIDATION_STAGE5,{"schema":"B_BOT_FULL_VALIDATION_STAGE5_V1","entries":{},"automatic_promotion":False})
    identity=str(target.get("canonical_identity_key") or "")
    item=(stage.setdefault("entries",{})).setdefault(identity,{})
    item.update({"strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),"timeframe":tf,"full_validation_gate":gate,"pipeline_stage":target.get("pipeline_stage"),"last_checked_at":now()})
    atomic(FULL_VALIDATION_STAGE5,stage)
    knowledge.record_result(f"pipeline_full_validation:{target.get('strategy_id')}:{tf}:{target.get('exact_strategy_version')}",{"strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),"timeframe":tf,"pipeline_stage":target.get("pipeline_stage"),"full_validation_gate":gate,"automatic_promotion":False,"canary_eligible":False},failed=(not bool(gate.get("pass"))))
    return {"status":"PASS" if bool(gate.get("pass")) else "FAIL","strategy_id":target.get("strategy_id"),"exact_strategy_version":target.get("exact_strategy_version"),"timeframe":tf,"pipeline_stage":target.get("pipeline_stage"),"full_validation_decision":gate.get("decision"),"full_validation_pass_mode":gate.get("pass_mode"),"automatic_promotion":False}


def _parse_utc(value):
    if isinstance(value,datetime):
        dt=value
    else:
        text=str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text=text[:-1]+"+00:00"
        try:
            dt=datetime.fromisoformat(text)
        except Exception:
            try:
                dt=datetime.fromisoformat(text[:10]+"T00:00:00+00:00")
            except Exception:
                return None
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _window_id(start_dt,end_dt):
    return f"{start_dt.date().isoformat()}__{end_dt.date().isoformat()}"


def _append_exposure(exposures,symbol,start,end,source):
    sym=str(symbol or "").upper()
    a=_parse_utc(start); b=_parse_utc(end)
    if not sym.endswith("USDT") or a is None or b is None or b<=a:
        return
    exposures.setdefault(sym,[]).append({
        "start":a.isoformat(),
        "end":b.isoformat(),
        "source":str(source or "UNKNOWN"),
    })


def _report_exposure_windows(report,exposures,source):
    for dataset_name,ds in (report.get("datasets") or {}).items():
        if not isinstance(ds,dict):
            continue
        windows=ds.get("symbol_windows") or {}
        if isinstance(windows,dict) and windows:
            for sym,w in windows.items():
                if not isinstance(w,dict):
                    continue
                _append_exposure(
                    exposures,sym,w.get("start"),w.get("end"),
                    f"{source}:{dataset_name}",
                )
            continue
        start=ds.get("start"); end=ds.get("end")
        for sym in (ds.get("symbols") or []):
            _append_exposure(exposures,sym,start,end,f"{source}:{dataset_name}")


def _research_exposure_windows(state,ledger=None):
    """Actual symbol/time exposure, including historical and new research."""
    exposures={}
    seen_paths=set()

    def add_result_path(rel,source):
        rel=str(rel or "").strip()
        if not rel:
            return
        p=ROOT/rel
        key=str(p).lower()
        if key in seen_paths or not p.is_file():
            return
        seen_paths.add(key)
        _report_exposure_windows(load(p,{}),exposures,source)

    for target in (state.get("entries") or []):
        if not isinstance(target,dict):
            continue
        for field in (
            "previous_result_json","new_result_json","final_strategy_result_json",
            "trailing_result_json","reentry_result_json",
        ):
            add_result_path(target.get(field),field)

    # New strategies researched after the historical-314 state was created are
    # exposure too. Never call a symbol/window unseen merely because it is not
    # represented in the historical reentry state file.
    try:
        knowledge_obj,_=knowledge.ensure()
        for record_key,row in (knowledge_obj.get("records") or {}).items():
            if not isinstance(row,dict):
                continue
            for field in (
                "result_json","new_result_json","final_strategy_result_json",
                "trailing_result_json","reentry_result_json",
            ):
                add_result_path(row.get(field),f"KNOWLEDGE:{record_key}:{field}")
    except Exception:
        # Fail-closed exposure selection still has historical state + blind ledger.
        # Missing knowledge expansion must not erase those known exposures.
        pass

    # Stage5/6 blind reservations/results are exposure too. A reserved window is
    # unavailable to a later cohort even before results are opened.
    ledger=ledger if isinstance(ledger,dict) else load(SEALED_FINAL_BLIND_STAGE5,{})
    for row in (ledger.get("used_blind_symbol_windows") or []):
        if not isinstance(row,dict):
            continue
        _append_exposure(
            exposures,row.get("symbol"),row.get("window_start"),row.get("window_end"),
            "SEALED_FINAL_BLIND_LEDGER",
        )
    for cohort in (ledger.get("cohorts") or {}).values():
        if not isinstance(cohort,dict):
            continue
        ws=cohort.get("window_start"); we=cohort.get("window_end")
        for sym in (cohort.get("sealed_symbols") or []):
            _append_exposure(exposures,sym,ws,we,"SEALED_FINAL_BLIND_COHORT")

    for sym,rows in exposures.items():
        rows.sort(key=lambda x:(x.get("start") or "",x.get("end") or "",x.get("source") or ""))
    return exposures


def _symbol_has_any_research_exposure(exposures,symbol):
    return bool(exposures.get(str(symbol or "").upper()) or [])


def _overlaps_exposure(exposures,symbol,start_dt,end_dt):
    sym=str(symbol or "").upper()
    for row in (exposures.get(sym) or []):
        a=_parse_utc(row.get("start")); b=_parse_utc(row.get("end"))
        if a is None or b is None:
            continue
        # [start,end) overlap test
        if start_dt < b and end_dt > a:
            return True
    return False


def _blind_window_candidates(contract):
    _,research_end=hard_pass_dates()
    end_dt=_parse_utc(research_end)
    if end_dt is None:
        return []
    purge_days=max(0,int(contract.get("boundary_purge_days") or 0))
    days=max(30,int(contract.get("blind_window_days") or 90))
    stride=max(days,int(contract.get("blind_window_stride_days") or days))
    cursor=end_dt+timedelta(days=purge_days+1)
    now_dt=datetime.now(timezone.utc)
    out=[]
    for _ in range(64):
        window_end=cursor+timedelta(days=days)
        if window_end>now_dt:
            break
        out.append((cursor,window_end))
        cursor=cursor+timedelta(days=stride)
    return out


def _blind_candle_profile_window(symbol,tf,start_dt,end_dt):
    tf_file={"30M":"30m","1H":"1h","6H":"6h","12H":"12h","1D":"1d","1W":"1w"}.get(str(tf).upper())
    interval_sec={"30M":1800,"1H":3600,"6H":21600,"12H":43200,"1D":86400,"1W":604800}.get(str(tf).upper())
    if not tf_file or not interval_sec:
        return None
    p=ROOT/"backtest_data"/str(symbol).upper()/f"{tf_file}.parquet"
    if not p.is_file():
        return None
    try:
        import pandas as pd
        df=pd.read_parquet(p,columns=["open_time","close"])
        if df.empty:
            return None
        open_dt=pd.to_datetime(df["open_time"],utc=True,errors="coerce")
        valid=open_dt.notna()
        if not valid.any():
            return None
        sec=open_dt.astype("int64")/1_000_000_000.0
        start_sec=start_dt.timestamp(); end_sec=end_dt.timestamp()
        mask=valid & (sec>=start_sec) & (sec<end_sec)
        cut_sec=sec.loc[mask].astype(float)
        close=pd.to_numeric(df.loc[mask,"close"],errors="coerce").dropna().astype(float)
        if cut_sec.empty or close.empty:
            return None
        expected=max(1,int(round((end_sec-start_sec)/float(interval_sec))))
        coverage=min(1.0,float(len(cut_sec))/float(expected))
        diffs=cut_sec.sort_values().diff().dropna().astype(float)
        gaps=int((diffs>float(interval_sec)*1.5).sum()) if len(diffs) else 0
        gap_ratio=gaps/float(max(1,len(diffs)))
        rets=close.pct_change().replace([float("inf"),-float("inf")],float("nan")).dropna()
        vol=float(rets.std(ddof=0)) if len(rets) else 0.0
        return {
            "rows":int(len(cut_sec)),
            "expected_rows":expected,
            "coverage_ratio":coverage,
            "gap_ratio":gap_ratio,
            "return_volatility":vol,
            "window_start":start_dt.isoformat(),
            "window_end":end_dt.isoformat(),
        }
    except Exception:
        return None

def _blind_rows_for_window(eligible,tfs,start_dt,end_dt,exposures,contract,first_cohort):
    min_days=float(contract.get("minimum_history_years") or 2)*365.0
    max_gap=float(contract.get("max_candle_gap_ratio") or 0.01)
    min_cov=float(contract.get("minimum_window_coverage_ratio") or 0.95)
    rows=[]
    for raw in eligible:
        if not isinstance(raw,dict):
            continue
        sym=str(raw.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        if float(raw.get("listing_days") or 0.0)<min_days:
            continue
        if first_cohort and _symbol_has_any_research_exposure(exposures,sym):
            continue
        if _overlaps_exposure(exposures,sym,start_dt,end_dt):
            continue
        profiles={}
        ok=True
        for tf in tfs:
            prof=_blind_candle_profile_window(sym,tf,start_dt,end_dt)
            gap_value=(prof.get("gap_ratio") if prof and prof.get("gap_ratio") is not None else 1.0)
            if (
                not prof
                or float(prof.get("coverage_ratio") or 0.0)<min_cov
                or float(gap_value)>max_gap
            ):
                ok=False
                break
            profiles[tf]=prof
        if not ok:
            continue
        # Rank high-volatility bucket from the highest available timeframe so
        # different pending TF mixes do not silently select on strategy results.
        vol=max(float((p or {}).get("return_volatility") or 0.0) for p in profiles.values()) if profiles else 0.0
        row=dict(raw)
        row["blind_window_profiles"]=profiles
        row["blind_window_volatility_rank_metric"]=vol
        rows.append(row)
    return rows


def _select_blind_mix(rows):
    rows=list(rows)
    rows.sort(key=lambda r:(float(r.get("liquidity_score") or 0.0),float(r.get("quote_volume") or 0.0),str(r.get("symbol") or "")),reverse=True)
    n=len(rows)
    high_pool=rows[:max(4,int(math.ceil(n*0.35)))]
    medium_pool=rows[max(0,int(n*0.25)):max(3,int(math.ceil(n*0.75)))]
    selected=[]; categories={}
    def take(pool,count,category,key=None):
        items=[x for x in pool if str(x.get("symbol") or "").upper() not in set(selected)]
        if key is not None:
            items.sort(key=key,reverse=True)
        for x in items[:count]:
            sym=str(x.get("symbol") or "").upper()
            selected.append(sym); categories[sym]=category
    take(high_pool,4,"HIGH_LIQUIDITY")
    take(medium_pool,3,"MEDIUM_LIQUIDITY")
    remaining=[x for x in rows if str(x.get("symbol") or "").upper() not in set(selected)]
    take(remaining,3,"HIGH_VOLATILITY",key=lambda r:(float(r.get("blind_window_volatility_rank_metric") or 0.0),float(r.get("liquidity_score") or 0.0),str(r.get("symbol") or "")))
    return selected,categories


def _sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def _blind_attempts_entry(ledger,target):
    identity=str(target.get("canonical_identity_key") or "")
    entries=ledger.setdefault("entries",{})
    row=entries.get(identity)
    if not isinstance(row,dict):
        row={
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":str(target.get("timeframe") or "").upper(),
            "attempts":[],
        }
        entries[identity]=row
    elif not isinstance(row.get("attempts"),list):
        # Stage6 V2 compatibility. Preserve a previously sealed single attempt.
        old=dict(row)
        row={
            "strategy_id":old.get("strategy_id") or target.get("strategy_id"),
            "exact_strategy_version":old.get("exact_strategy_version") or target.get("exact_strategy_version"),
            "timeframe":str(old.get("timeframe") or target.get("timeframe") or "").upper(),
            "attempts":[],
        }
        if old.get("cohort_id"):
            row["attempts"].append(old)
        entries[identity]=row
    return row


def _blind_attempt_for_cohort(entry,cohort_id):
    for attempt in reversed(entry.get("attempts") or []):
        if str(attempt.get("cohort_id") or "")==str(cohort_id or ""):
            return attempt
    return None


def _blind_reentry_snapshot(target):
    row=dict(target.get("individual_reentry") or {})
    mode=str(row.get("mode") or "CURRENT_STATE_NEXT_1H").upper()
    if mode!="CURRENT_STATE_NEXT_1H":
        raise RuntimeError("BLIND_NONBASELINE_REENTRY_WITHOUT_EXACT_REPLAY_FORBIDDEN")
    return {
        "mode":"CURRENT_STATE_NEXT_1H",
        "enabled":True,
        "check_tf":"1H",
        "fallback":bool(row.get("fallback",True)),
    }


def _blind_trailing_snapshot(target):
    row=dict(target.get("individual_trailing") or {})
    return {
        "start_pct":float(row.get("start_pct") or 3.0),
        "callback_pct":float(row.get("callback_pct") or 1.5),
        "fallback":bool(row.get("fallback")),
    }


def prepare_sealed_final_blind_one():
    state=load(HISTORICAL_STAGE2,{})
    pending_stages={
        "SEALED_FINAL_BLIND_PENDING",
        "SEALED_FINAL_BLIND_ADDITIONAL_WINDOW_PENDING",
    }
    pending=[
        x for x in (state.get("entries") or [])
        if isinstance(x,dict) and x.get("eligible") is True
        and x.get("pipeline_stage") in pending_stages
    ]
    if not pending:
        return {"status":"NO_SEALED_FINAL_BLIND_BACKLOG","at":now()}

    ledger=load(SEALED_FINAL_BLIND_STAGE5,{
        "schema":"B_BOT_SEALED_FINAL_BLIND_STAGE7_V3",
        "entries":{},"cohorts":{},"used_blind_symbol_windows":[],
        "legacy_used_blind_symbols":[],"automatic_promotion":False,
    })
    if str(ledger.get("schema") or "") not in {
        "B_BOT_SEALED_FINAL_BLIND_STAGE6_V2",
        "B_BOT_SEALED_FINAL_BLIND_STAGE7_V3",
    }:
        ledger["legacy_used_blind_symbols"]=sorted(set(
            str(x).upper() for x in (ledger.get("used_blind_symbols") or [])
            if str(x).upper().endswith("USDT")
        ))
    ledger["schema"]="B_BOT_SEALED_FINAL_BLIND_STAGE7_V3"
    ledger.setdefault("cohorts",{})
    ledger.setdefault("used_blind_symbol_windows",[])
    ledger.setdefault("entries",{})

    # Seal only immutable, existing source artifacts. One broken member must not
    # reopen or invalidate the membership of other valid members.
    sealable=[]
    for target in pending:
        source=ROOT/str(target.get("canonical_source_file") or "")
        if not source.is_file():
            target["pipeline_stage"]="SEALED_FINAL_BLIND_SOURCE_MISSING"
            target["last_error"]="CANONICAL_SOURCE_FILE_MISSING_AT_SEAL"
            target["last_checked_at"]=now()
            continue
        sealable.append((target,source,_sha256_file(source)))
    if len(sealable)!=len(pending):
        _save_historical_stage2(state)
    if not sealable:
        return {"status":"BLOCKED","reason":"NO_SEALABLE_FROZEN_EXACT_VERSION","automatic_promotion":False}

    universe=load(SYMBOL_UNIVERSE_SNAPSHOT,{})
    eligible=universe.get("eligible_candidates") or []
    if not isinstance(eligible,list) or not eligible:
        for target,_,_ in sealable:
            target["pipeline_stage"]="SEALED_FINAL_BLIND_UNIVERSE_REFRESH_REQUIRED"
            target["last_error"]="ELIGIBLE_CANDIDATE_SNAPSHOT_MISSING"
        _save_historical_stage2(state)
        return {
            "status":"BLOCKED",
            "reason":"ELIGIBLE_CANDIDATE_SNAPSHOT_MISSING",
            "action":"RUN_EXISTING_SYMBOL_UNIVERSE_REFRESH_READ_ONLY_BEFORE_BLIND",
            "automatic_promotion":False,
        }

    contract=(pipeline_policy().get("final_blind_contract") or {})
    tfs=sorted(set(
        str(x.get("timeframe") or "").upper()
        for x,_,_ in sealable
        if str(x.get("timeframe") or "").upper() in ELIGIBLE_PIPELINE_TFS
    ))
    exposures=_research_exposure_windows(state,ledger)
    first_cohort=(len(ledger.get("cohorts") or {})==0)
    used_window_ids={
        str((c or {}).get("window_id") or "")
        for c in (ledger.get("cohorts") or {}).values()
        if isinstance(c,dict)
    }

    choice=None
    last_eligible_count=0
    for start_dt,end_dt in _blind_window_candidates(contract):
        wid=_window_id(start_dt,end_dt)
        if wid in used_window_ids:
            continue
        rows=_blind_rows_for_window(
            eligible,tfs,start_dt,end_dt,exposures,contract,first_cohort
        )
        last_eligible_count=len(rows)
        if len(rows)<10:
            continue
        selected,categories=_select_blind_mix(rows)
        if len(selected)==10:
            choice=(start_dt,end_dt,wid,selected,categories,rows)
            break

    if choice is None:
        for target,_,_ in sealable:
            target["pipeline_stage"]="SEALED_FINAL_BLIND_WINDOW_POOL_INSUFFICIENT"
            target["last_error"]="BLIND_UNEXPOSED_SYMBOL_WINDOW_POOL_INSUFFICIENT"
        _save_historical_stage2(state)
        return {
            "status":"BLOCKED",
            "reason":"BLIND_UNEXPOSED_SYMBOL_WINDOW_POOL_INSUFFICIENT",
            "eligible_last_window":last_eligible_count,
            "required":10,
            "first_cohort_requires_completely_unused_symbols":bool(first_cohort),
            "policy":"ROTATE_WINDOW_THEN_FAIL_CLOSED_NO_OVERLAP_REUSE",
            "automatic_promotion":False,
        }

    start_dt,end_dt,wid,selected,categories,rows=choice
    cohort_id=f"BLIND_COHORT_{len(ledger.get('cohorts') or {})+1:04d}_{wid}"
    sealed_at=now()
    member_ids=[]
    for target,source,source_sha in sealable:
        identity=str(target.get("canonical_identity_key") or "")
        member_ids.append(identity)
        trailing_snapshot=_blind_trailing_snapshot(target)
        reentry_snapshot=_blind_reentry_snapshot(target)
        attempt_id=f"{cohort_id}:{identity}"
        attempt={
            "attempt_id":attempt_id,
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":str(target.get("timeframe") or "").upper(),
            "canonical_source_file":str(target.get("canonical_source_file") or "").replace("\\","/"),
            "sealed_source_sha256":source_sha,
            "trailing_snapshot":trailing_snapshot,
            "reentry_snapshot":reentry_snapshot,
            "cohort_id":cohort_id,
            "window_id":wid,
            "window_start":start_dt.isoformat(),
            "window_end":end_dt.isoformat(),
            "sealed_at":sealed_at,
            "sealed_symbols":list(selected),
            "categories":dict(categories),
            "selection_before_result_observation":True,
            "cohort_membership_closed_before_result":True,
            "result_observed":False,
            "tuning_allowed":False,
            "status":"SEALED_PENDING_EVALUATION",
            "automatic_promotion":False,
        }
        entry=_blind_attempts_entry(ledger,target)
        entry.setdefault("attempts",[]).append(attempt)
        entry["current_attempt_id"]=attempt_id
        entry["current_cohort_id"]=cohort_id
        target["sealed_final_blind"]={
            "attempt_id":attempt_id,
            "cohort_id":cohort_id,"window_id":wid,
            "window_start":start_dt.isoformat(),"window_end":end_dt.isoformat(),
            "sealed_symbols":list(selected),"categories":dict(categories),
            "sealed_source_sha256":source_sha,
            "trailing_snapshot":trailing_snapshot,
            "reentry_snapshot":reentry_snapshot,
            "sealed_at":sealed_at,"result_observed":False,
        }
        target["pipeline_stage"]="SEALED_FINAL_BLIND_EVALUATION_PENDING"
        target["direct_canary_allowed"]=False
        target["last_checked_at"]=sealed_at

    ledger.setdefault("cohorts",{})[cohort_id]={
        "cohort_id":cohort_id,
        "window_id":wid,
        "window_start":start_dt.isoformat(),
        "window_end":end_dt.isoformat(),
        "sealed_symbols":list(selected),
        "categories":dict(categories),
        "member_identity_keys":member_ids,
        "member_count":len(member_ids),
        "timeframes":tfs,
        "membership_closed":True,
        "sealed_at":sealed_at,
        "result_observed":False,
        "evaluated_count":0,
        "member_results":{},
        "status":"SEALED_PENDING_EVALUATION",
        "first_cohort_completely_unused_symbol_requirement":bool(first_cohort),
    }
    used_rows=ledger.setdefault("used_blind_symbol_windows",[])
    existing={
        (str(r.get("symbol") or "").upper(),str(r.get("window_id") or ""))
        for r in used_rows if isinstance(r,dict)
    }
    for sym in selected:
        key=(sym,wid)
        if key in existing:
            continue
        used_rows.append({
            "symbol":sym,"window_id":wid,
            "window_start":start_dt.isoformat(),"window_end":end_dt.isoformat(),
            "cohort_id":cohort_id,"reserved_at":sealed_at,
        })
        existing.add(key)
    ledger["updated_at"]=sealed_at
    ledger["automatic_promotion"]=False
    atomic(SEALED_FINAL_BLIND_STAGE5,ledger)
    _save_historical_stage2(state)
    return {
        "status":"SEALED_BATCH",
        "cohort_id":cohort_id,
        "member_count":len(member_ids),
        "timeframes":tfs,
        "window_id":wid,
        "window_start":start_dt.isoformat(),
        "window_end":end_dt.isoformat(),
        "sealed_symbols":selected,
        "categories":categories,
        "source_sha256_sealed":True,
        "trailing_reentry_snapshot_sealed":True,
        "first_cohort_requires_completely_unused_symbols":bool(first_cohort),
        "later_same_symbol_fresh_window_allowed":True,
        "same_symbol_window_reuse_forbidden":True,
        "pipeline_stage":"SEALED_FINAL_BLIND_EVALUATION_PENDING",
        "automatic_promotion":False,
    }


def _blind_tf_file(tf):
    return {"30M":"30m","1H":"1h","6H":"6h","12H":"12h","1D":"1d","1W":"1w"}.get(str(tf).upper())


def _blind_interval_sec(tf):
    return {"30M":1800,"1H":3600,"6H":21600,"12H":43200,"1D":86400,"1W":604800}.get(str(tf).upper())


def _blind_raw_ohlcv(symbol,tf):
    import pandas as pd
    tf_file=_blind_tf_file(tf); interval=_blind_interval_sec(tf)
    if not tf_file or not interval:
        raise RuntimeError("BLIND_TIMEFRAME_UNSUPPORTED")
    path=ROOT/"backtest_data"/str(symbol).upper()/f"{tf_file}.parquet"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    df=pd.read_parquet(path).copy()
    required={"open_time","open","high","low","close","volume"}
    missing=required-set(df.columns)
    if missing:
        raise RuntimeError("BLIND_OHLCV_COLUMNS_MISSING:"+",".join(sorted(missing)))
    open_dt=pd.to_datetime(df["open_time"],utc=True,errors="coerce")
    for col in ("open","high","low","close","volume"):
        df[col]=pd.to_numeric(df[col],errors="coerce")
    if "close_time" in df.columns:
        raw_close=pd.to_numeric(df["close_time"],errors="coerce")
        if raw_close.notna().any():
            scale=1000.0 if float(raw_close.dropna().iloc[-1])>10_000_000_000 else 1.0
            close_sec=raw_close.astype(float)/scale
        else:
            close_sec=None
    else:
        close_sec=None
    open_sec=open_dt.astype("int64")/1_000_000_000.0
    if close_sec is None:
        close_sec=open_sec+float(interval)
    else:
        close_sec=close_sec.where(close_sec.notna(),open_sec+float(interval))
    df["_blind_open_sec"]=open_sec
    df["ts"]=close_sec.round().astype("Int64")
    df=df.dropna(subset=["open_time","ts","open","high","low","close"]).copy()
    df["ts"]=df["ts"].astype("int64")
    df["symbol"]=str(symbol).upper()
    df["datetime"]=pd.to_datetime(df["ts"],unit="s",utc=True)
    return (
        df.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )


def _blind_strategy_module(source,identity):
    name="v3_blind_"+hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    spec=importlib.util.spec_from_file_location(name,str(source))
    if spec is None or spec.loader is None:
        raise RuntimeError("BLIND_STRATEGY_MODULE_SPEC_FAIL")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    meta=dict(getattr(mod,"STRATEGY_META",{}) or {})
    fn=getattr(mod,"generate_signals",None)
    if not callable(fn):
        raise RuntimeError("BLIND_GENERATE_SIGNALS_MISSING")
    if str(meta.get("data_mode") or "OHLCV").upper()!="OHLCV":
        raise RuntimeError("BLIND_UNSUPPORTED_DATA_MODE:"+str(meta.get("data_mode") or ""))
    return meta,fn


def _blind_runner_module():
    import run_strategy as strategy_runner
    return strategy_runner


def _blind_runtime_configs(attempt):
    trailing=dict(attempt.get("trailing_snapshot") or {})
    trailing={
        "start_pct":float(trailing.get("start_pct") or 3.0),
        "callback_pct":float(trailing.get("callback_pct") or 1.5),
    }
    reentry=dict(attempt.get("reentry_snapshot") or {})
    if str(reentry.get("mode") or "").upper()=="CURRENT_STATE_NEXT_1H":
        reentry_cfg={
            "mode":"CURRENT_STATE_NEXT_1H_BASELINE_PROXY",
        }
    else:
        raise RuntimeError("BLIND_REENTRY_SNAPSHOT_INVALID")
    return trailing,reentry_cfg


def _run_sealed_blind_attempt(target,attempt):
    import pandas as pd
    identity=str(target.get("canonical_identity_key") or "")
    source=ROOT/str(attempt.get("canonical_source_file") or target.get("canonical_source_file") or "")
    if not source.is_file():
        raise RuntimeError("BLIND_CANONICAL_SOURCE_MISSING")
    actual_sha=_sha256_file(source)
    expected_sha=str(attempt.get("sealed_source_sha256") or "")
    if not expected_sha or actual_sha!=expected_sha:
        raise RuntimeError("BLIND_EXACT_SOURCE_SHA256_MISMATCH")
    meta,fn=_blind_strategy_module(source,identity)
    runner=_blind_runner_module()
    tf=str(attempt.get("timeframe") or target.get("timeframe") or "").upper()
    ws=_parse_utc(attempt.get("window_start")); we=_parse_utc(attempt.get("window_end"))
    if ws is None or we is None or we<=ws:
        raise RuntimeError("BLIND_WINDOW_INVALID")
    trailing,reentry=_blind_runtime_configs(attempt)
    fee_rate=float((load(LAB/"lab_config.json",{}) or {}).get("fee_rate") or 0.0005)
    all_trades=[]; symbol_evidence={}; source_rows=0; test_rows=0
    for sym in list(attempt.get("sealed_symbols") or []):
        df=_blind_raw_ohlcv(sym,tf)
        source_rows+=len(df)
        raw_signal=fn(df.copy(),tf)
        signal_full=runner.normalize_signal(raw_signal,df.index)
        mask=(
            (df["_blind_open_sec"].to_numpy(dtype=float)>=ws.timestamp())
            & (df["_blind_open_sec"].to_numpy(dtype=float)<we.timestamp())
        )
        if not mask.any():
            symbol_evidence[sym]={"status":"NO_WINDOW_ROWS","trades":0}
            continue
        cut=df.loc[mask].copy().reset_index(drop=True)
        sig=signal_full[mask]
        test_rows+=len(cut)
        trades=runner.simulate(
            cut,sig,fee_rate,trailing=trailing,reentry=reentry
        )
        for tr in trades:
            tr["symbol"]=str(sym).upper()
            tr["timeframe"]=tf
            tr["blind_window_id"]=str(attempt.get("window_id") or "")
            tr["blind_cohort_id"]=str(attempt.get("cohort_id") or "")
        all_trades.extend(trades)
        symbol_evidence[str(sym).upper()]={
            "status":"MEASURED",
            "summary":runner.summarize(trades),
            "v3_robustness":runner.v3_robustness_summary(trades,fee_rate),
            "exit_research":runner.exit_research_summary(trades),
        }
    return {
        "schema":"B_BOT_SEALED_FINAL_BLIND_RUN_STAGE7_V1",
        "evaluated_at":now(),
        "strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),
        "canonical_identity_key":identity,
        "canonical_source_file":str(attempt.get("canonical_source_file") or ""),
        "sealed_source_sha256":expected_sha,
        "verified_source_sha256":actual_sha,
        "timeframe":tf,
        "cohort_id":attempt.get("cohort_id"),
        "window_id":attempt.get("window_id"),
        "window_start":attempt.get("window_start"),
        "window_end":attempt.get("window_end"),
        "sealed_symbols":list(attempt.get("sealed_symbols") or []),
        "trailing_snapshot":dict(attempt.get("trailing_snapshot") or {}),
        "reentry_snapshot":dict(attempt.get("reentry_snapshot") or {}),
        "data_mode":str(meta.get("data_mode") or "OHLCV").upper(),
        "positions_start_flat":True,
        "position_carry_in":False,
        "position_carry_out":False,
        "source_rows":int(source_rows),
        "test_rows":int(test_rows),
        "summary":runner.summarize(all_trades),
        "v3_robustness":runner.v3_robustness_summary(all_trades,fee_rate),
        "exit_research":runner.exit_research_summary(all_trades),
        "symbol_evidence":symbol_evidence,
        "trades":all_trades,
        "automatic_promotion":False,
    }


def _blind_history_reports(target,current_report=None):
    reports=[]; seen=set()
    for row in (target.get("sealed_final_blind_history") or []):
        if not isinstance(row,dict):
            continue
        rel=str(row.get("result_json") or "")
        if not rel or rel in seen:
            continue
        p=ROOT/rel
        rep=load(p,{})
        if rep:
            reports.append(rep); seen.add(rel)
    if isinstance(current_report,dict) and current_report:
        key=str(current_report.get("cohort_id") or "")+"|"+str(current_report.get("window_id") or "")
        if key not in {str(x.get("cohort_id") or "")+"|"+str(x.get("window_id") or "") for x in reports}:
            reports.append(current_report)
    return reports


def evaluate_sealed_final_blind_gate(target,current_report=None):
    runner=_blind_runner_module()
    contract=(pipeline_policy().get("final_blind_contract") or {})
    tf=str(target.get("timeframe") or "").upper()
    reports=_blind_history_reports(target,current_report)
    trades=[]
    windows=[]
    for rep in reports:
        trades.extend([dict(x) for x in (rep.get("trades") or []) if isinstance(x,dict)])
        windows.append({
            "cohort_id":rep.get("cohort_id"),
            "window_id":rep.get("window_id"),
            "window_start":rep.get("window_start"),
            "window_end":rep.get("window_end"),
        })
    fee_rate=float((load(LAB/"lab_config.json",{}) or {}).get("fee_rate") or 0.0005)
    summary=runner.summarize(trades)
    robust=runner.v3_robustness_summary(trades,fee_rate)
    expectancy=finite(summary.get("avg_net_pct"))
    stress=finite((robust.get("cost_stress_2x") or {}).get("avg_net_pct"))
    by_symbol={}
    for tr in trades:
        sym=str(tr.get("symbol") or "").upper()
        if sym:
            by_symbol.setdefault(sym,[]).append(tr)
    active_rows={sym:runner.summarize(rows) for sym,rows in by_symbol.items() if rows}
    active_symbols=len(active_rows)
    positive_symbols=sum(
        1 for row in active_rows.values()
        if finite(row.get("avg_net_pct")) is not None and float(row.get("avg_net_pct"))>0.0
    )
    positive_ratio=(positive_symbols/float(active_symbols)) if active_symbols else 0.0
    min_fraction=float(contract.get("minimum_trade_fraction_of_hard_pass") or 0.25)
    min_trades=max(3,int(math.ceil(float(MIN_TRADES.get(tf) or 0)*min_fraction)))
    min_active=max(1,int(contract.get("minimum_active_symbols") or 3))
    max_windows=max(1,int(contract.get("max_windows_per_exact_version") or 4))
    sample_ok=(int(summary.get("trades") or 0)>=min_trades and active_symbols>=min_active)
    metrics={
        "windows_used":len(windows),
        "trades":int(summary.get("trades") or 0),
        "minimum_trades":min_trades,
        "active_symbols":active_symbols,
        "minimum_active_symbols":min_active,
        "positive_active_symbols":positive_symbols,
        "positive_active_symbol_ratio":positive_ratio,
        "minimum_positive_active_symbol_ratio":float(contract.get("minimum_positive_active_symbol_ratio") or 0.40),
        "aggregate_expectancy_pct":expectancy,
        "cost_stress_2x_expectancy_pct":stress,
    }
    if not sample_ok:
        if len(windows)<max_windows:
            return {
                "pass":False,"decision":"INCONCLUSIVE","pass_mode":"NONE",
                "reason":"BLIND_SAMPLE_INSUFFICIENT_FRESH_WINDOW_REQUIRED",
                "sample_adequate":False,"metrics":metrics,"windows":windows,
                "next_stage":"SEALED_FINAL_BLIND_ADDITIONAL_WINDOW_PENDING",
                "tuning_allowed":False,"automatic_promotion":False,
            }
        return {
            "pass":False,"decision":"HOLD","pass_mode":"NONE",
            "reason":"BLIND_SAMPLE_INSUFFICIENT_MAX_WINDOWS_REACHED",
            "sample_adequate":False,"metrics":metrics,"windows":windows,
            "next_stage":"SEALED_FINAL_BLIND_INSUFFICIENT_SAMPLE_HOLD",
            "tuning_allowed":False,"automatic_promotion":False,
        }
    failures=[]
    if expectancy is None or expectancy<=0.0:
        failures.append("BLIND_AGGREGATE_EXPECTANCY_NONPOSITIVE")
    if stress is None or stress<=0.0:
        failures.append("BLIND_COST_STRESS_2X_EXPECTANCY_NONPOSITIVE")
    min_ratio=float(contract.get("minimum_positive_active_symbol_ratio") or 0.40)
    if positive_ratio<min_ratio-1e-12:
        failures.append("BLIND_POSITIVE_SYMBOL_RATIO_LOW")
    if failures:
        return {
            "pass":False,"decision":"FAIL","pass_mode":"NONE",
            "reason":"SEALED_FINAL_BLIND_ABSOLUTE_FAIL",
            "absolute_fail_reasons":failures,"sample_adequate":True,
            "metrics":metrics,"windows":windows,
            "next_stage":"SEALED_FINAL_BLIND_FAIL_ANALYSIS_ONLY",
            "tuning_allowed":False,"automatic_promotion":False,
        }
    return {
        "pass":True,"decision":"PASS","pass_mode":"DIRECT",
        "reason":"SEALED_FINAL_BLIND_PASS","sample_adequate":True,
        "metrics":metrics,"windows":windows,
        "next_stage":"LOCAL_SHADOW_PENDING",
        "tuning_allowed":False,"automatic_promotion":False,
    }


def run_sealed_final_blind_one():
    state=load(HISTORICAL_STAGE2,{})
    target=next((
        x for x in (state.get("entries") or [])
        if isinstance(x,dict) and x.get("eligible") is True
        and x.get("pipeline_stage")=="SEALED_FINAL_BLIND_EVALUATION_PENDING"
    ),None)
    if target is None:
        return {"status":"NO_SEALED_FINAL_BLIND_EVALUATION_BACKLOG","at":now()}
    ledger=load(SEALED_FINAL_BLIND_STAGE5,{})
    identity=str(target.get("canonical_identity_key") or "")
    entry=(ledger.get("entries") or {}).get(identity) or {}
    current=target.get("sealed_final_blind") or {}
    cohort_id=str(current.get("cohort_id") or entry.get("current_cohort_id") or "")
    attempt=_blind_attempt_for_cohort(entry,cohort_id)
    if not isinstance(attempt,dict):
        target["pipeline_stage"]="SEALED_FINAL_BLIND_LEDGER_MISMATCH"
        target["last_error"]="SEALED_ATTEMPT_NOT_FOUND"
        _save_historical_stage2(state)
        return {"status":"FAIL","reason":"SEALED_ATTEMPT_NOT_FOUND","automatic_promotion":False}
    cohort=(ledger.get("cohorts") or {}).get(cohort_id) or {}
    if cohort.get("membership_closed") is not True:
        target["pipeline_stage"]="SEALED_FINAL_BLIND_MEMBERSHIP_NOT_CLOSED"
        target["last_error"]="BLIND_RESULT_OPEN_BLOCKED_MEMBERSHIP_OPEN"
        _save_historical_stage2(state)
        return {"status":"BLOCKED","reason":"BLIND_COHORT_MEMBERSHIP_NOT_CLOSED","automatic_promotion":False}
    try:
        report=_run_sealed_blind_attempt(target,attempt)
    except Exception as exc:
        attempt["status"]="EVALUATION_RUNTIME_FAIL"
        attempt["runtime_error"]=f"{type(exc).__name__}:{exc}"
        attempt["last_checked_at"]=now()
        target["pipeline_stage"]="SEALED_FINAL_BLIND_RUNTIME_FAIL"
        target["last_error"]=attempt["runtime_error"]
        target["last_checked_at"]=now()
        ledger["updated_at"]=now(); atomic(SEALED_FINAL_BLIND_STAGE5,ledger); _save_historical_stage2(state)
        return {
            "status":"FAIL","reason":"SEALED_FINAL_BLIND_RUNTIME_FAIL",
            "error":attempt["runtime_error"],"strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "automatic_promotion":False,
        }

    out_dir=RES/"sealed_blind_runs"; out_dir.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256((identity+"|"+cohort_id).encode("utf-8")).hexdigest()[:20]
    out_path=out_dir/f"blind_{key}.json"
    atomic(out_path,report)
    rel=str(out_path.relative_to(ROOT)).replace("\\","/")
    gate=evaluate_sealed_final_blind_gate(target,report)
    history=target.setdefault("sealed_final_blind_history",[])
    history.append({
        "cohort_id":cohort_id,"window_id":attempt.get("window_id"),
        "window_start":attempt.get("window_start"),"window_end":attempt.get("window_end"),
        "result_json":rel,"decision":gate.get("decision"),
        "evaluated_at":report.get("evaluated_at"),
    })
    target["sealed_final_blind_gate"]=gate
    target["pipeline_stage"]=str(gate.get("next_stage") or "SEALED_FINAL_BLIND_FAIL_ANALYSIS_ONLY")
    target["direct_canary_allowed"]=False
    target["last_checked_at"]=now()
    if gate.get("decision")=="FAIL":
        target["blind_failure_analysis_only"]=True
        target["blind_failure_automatic_tuning_allowed"]=False
        target["blind_redevelopment_requires_new_exact_version"]=True
        target["blind_redevelopment_restart_from"]="HARD_PASS"
        target["blind_retry_requires_fresh_window_cohort"]=True
    attempt["status"]=(
        "PASS" if gate.get("pass") is True
        else ("INCONCLUSIVE" if gate.get("decision")=="INCONCLUSIVE" else str(gate.get("decision") or "FAIL"))
    )
    attempt["result_json"]=rel
    attempt["gate"]=gate
    attempt["result_observed"]=True
    attempt["tuning_allowed"]=False
    attempt["evaluated_at"]=report.get("evaluated_at")
    cohort.setdefault("member_results",{})[identity]={
        "decision":gate.get("decision"),"pass":bool(gate.get("pass")),
        "result_json":rel,"next_stage":target.get("pipeline_stage"),
        "evaluated_at":report.get("evaluated_at"),
    }
    cohort["result_observed"]=True
    cohort["evaluated_count"]=len(cohort.get("member_results") or {})
    cohort["status"]=(
        "EVALUATION_COMPLETE"
        if int(cohort.get("evaluated_count") or 0)>=int(cohort.get("member_count") or 0)
        else "EVALUATION_IN_PROGRESS"
    )
    ledger["updated_at"]=now(); ledger["automatic_promotion"]=False
    atomic(SEALED_FINAL_BLIND_STAGE5,ledger)
    _save_historical_stage2(state)
    knowledge.record_result(
        f"pipeline_sealed_final_blind:{target.get('strategy_id')}:{target.get('timeframe')}:{target.get('exact_strategy_version')}:{cohort_id}",
        {
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":target.get("timeframe"),
            "cohort_id":cohort_id,"result_json":rel,
            "sealed_final_blind_gate":gate,
            "pipeline_stage":target.get("pipeline_stage"),
            "automatic_promotion":False,"canary_eligible":False,
        },
        failed=(gate.get("decision")=="FAIL"),
    )
    return {
        "status":str(gate.get("decision") or "UNKNOWN"),
        "strategy_id":target.get("strategy_id"),
        "exact_strategy_version":target.get("exact_strategy_version"),
        "timeframe":target.get("timeframe"),
        "cohort_id":cohort_id,
        "result_json":rel,
        "pipeline_stage":target.get("pipeline_stage"),
        "blind_pass":bool(gate.get("pass")),
        "sample_adequate":bool(gate.get("sample_adequate")),
        "metrics":gate.get("metrics") or {},
        "automatic_promotion":False,
    }

def _blind_result_for_row(row):
    p=ROOT/str(row.get("result_json") or "")
    rep=load(p,{})
    tf=str(row.get("timeframe") or "").upper()
    cur=(((rep.get("datasets") or {}).get("CURRENT_35_FROM_2020") or {}).get("timeframes") or {}).get(tf) or {}
    blind=((cur.get("v3_robustness") or {}).get("sealed_blind_single") or {})
    avg=finite(blind.get("avg_net_pct")); stress=finite(blind.get("cost_stress_2x_avg_net_pct")); trades=int(blind.get("trades") or 0)
    passed=(trades>0 and avg is not None and avg>0 and stress is not None and stress>0)
    return {"pass":passed,"trades":trades,"avg_net_pct":avg,"cost_stress_2x_avg_net_pct":stress}

def prepare_historical_314_reentry():
    k,_=knowledge.ensure()
    records=k.get("records") or {}
    raw=[]
    for key,item in records.items():
        if not str(key).startswith("historical_reentry_id:") or not isinstance(item,dict):
            continue
        raw.append((str(key),item))

    rows=[]
    seen=set()
    duplicate_count=0
    start_date,end_date=hard_pass_dates()
    for source_key,item in sorted(raw,key=lambda x:x[0]):
        strategy=str(item.get("strategy_id") or item.get("strategy") or "").strip().upper()
        version=str(item.get("exact_strategy_version") or item.get("strategy_version_id") or "").strip()
        tf=str(item.get("timeframe") or "").upper()
        if not strategy or not version or not tf:
            continue
        identity=f"{strategy}|{version}"
        duplicate=identity in seen
        if duplicate:
            duplicate_count+=1
        else:
            seen.add(identity)
        eligible=(tf in ELIGIBLE_PIPELINE_TFS and not duplicate)
        rows.append({
            "strategy_id":strategy,
            "exact_strategy_version":version,
            "timeframe":tf,
            "canonical_identity_key":identity,
            "canonical_source_file":str(item.get("canonical_source_file") or "").replace("\\","/"),
            "previous_result_json":str(item.get("result_json") or "").replace("\\","/"),
            "source_record_key":source_key,
            "pipeline_stage":(
                "HARD_PASS_RECHECK_PENDING"
                if eligible else ("DUPLICATE_CANONICAL_IDENTITY" if duplicate else "EXCLUDED_TIMEFRAME")
            ),
            "eligible":bool(eligible),
            "research_start":start_date,
            "research_end":end_date,
            "legacy_blind_credit":False,
            "direct_canary_allowed":False,
            "redevelopment_cycles":0,
            "redevelopment_cycles_by_gate":{},
            "pipeline_origin":"HISTORICAL_314",
            "root_pipeline_origin":"HISTORICAL_314",
        })

    state={
        "schema":"B_BOT_HISTORICAL_314_PIPELINE_STAGE2_V1",
        "updated_at":now(),
        "source_contract_name":"HISTORICAL_HARD_PASS_REENTRY_314_V1",
        "source_contract_reference_count":314,
        "canonical_source_records":len(raw),
        "canonical_exact_identities":len(seen),
        "eligible_exact_identities":sum(1 for x in rows if x.get("eligible")),
        "duplicate_exact_identities":duplicate_count,
        "one_week_records":sum(1 for x in rows if x.get("timeframe")=="1W"),
        "eligible_one_week_records":sum(1 for x in rows if x.get("timeframe")=="1W" and x.get("eligible")),
        "research_start":start_date,
        "research_end":end_date,
        "automatic_promotion":False,
        "entries":rows,
    }
    atomic(HISTORICAL_STAGE2,state)
    return {key:value for key,value in state.items() if key!="entries"}


def _save_historical_stage2(state):
    state["updated_at"]=now()
    atomic(HISTORICAL_STAGE2,state)


def run_historical_reentry_one():
    if not HISTORICAL_STAGE2.is_file():
        prepare_historical_314_reentry()
    state=load(HISTORICAL_STAGE2,{})
    entries=state.get("entries") or []
    target=None
    for item in entries:
        if isinstance(item,dict) and item.get("eligible") is True and item.get("pipeline_stage")=="HARD_PASS_RECHECK_PENDING":
            target=item
            break
    if target is None:
        return {"status":"HISTORICAL_REENTRY_BACKLOG_COMPLETE","at":now()}

    source=ROOT/str(target.get("canonical_source_file") or "")
    if not source.is_file():
        target["pipeline_stage"]="SOURCE_MISSING"
        target["last_error"]="CANONICAL_SOURCE_FILE_MISSING"
        _save_historical_stage2(state)
        return {
            "status":"FAIL",
            "reason":"CANONICAL_SOURCE_FILE_MISSING",
            "strategy_id":target.get("strategy_id"),
            "exact_strategy_version":target.get("exact_strategy_version"),
            "timeframe":target.get("timeframe"),
            "source":str(source),
        }

    tf=str(target.get("timeframe") or "").upper()
    strategy_id=str(target.get("strategy_id") or "").upper()
    exact_version=str(target.get("exact_strategy_version") or "")
    # RECOVER_COMPLETED_HARD_PASS_RECHECK
    # A completed ledger trial can outlive an uncommitted queue transition.
    # Reconcile only when the saved result belongs to this exact source.
    _, trial_ledger = knowledge.ensure()
    completed_trials = [
        item for item in trial_ledger.get("entries", [])
        if isinstance(item, dict)
        and item.get("event") == "COMPLETED"
        and item.get("do_not_rerun") is True
        and str(item.get("candidate") or "").strip() == strategy_id
        and str(item.get("timeframe") or "").upper() == tf
        and str(item.get("kind") or "").upper() == "HISTORICAL_HARD_PASS_RECHECK"
    ]
    # 완료된 부모 버전 결과를 자식 버전의 결과로 취급하지 않는다.
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_source_hash = str(target.get("source_sha256") or "").lower()
    if expected_source_hash and source_hash != expected_source_hash:
        raise RuntimeError("HARD_PASS_RECHECK_SOURCE_HASH_MISMATCH:" + exact_version)
    exact_completed = []
    for completed in completed_trials:
        recorded_version = str(completed.get("exact_strategy_version") or "")
        recorded_hash = str(completed.get("source_sha256") or "").lower()
        if recorded_version and recorded_hash:
            if recorded_version != exact_version and recorded_hash != source_hash:
                continue
        saved_rel = str(completed.get("result") or "").replace("\\", "/")
        saved_file = ROOT / saved_rel
        saved_report = load(saved_file, {}) if saved_file.is_file() else {}
        raw_source = str(saved_report.get("strategy_file") or "").replace("\\", "/")
        if not raw_source:
            raise RuntimeError("COMPLETED_TRIAL_RESULT_RECONCILIATION_REQUIRED:" + strategy_id)
        recorded_path = Path(raw_source)
        if not recorded_path.is_absolute():
            recorded_path = ROOT / recorded_path
        if str(recorded_path.resolve()).casefold() != str(source.resolve()).casefold():
            if recorded_version == exact_version or recorded_hash == source_hash:
                raise RuntimeError("COMPLETED_TRIAL_SOURCE_BINDING_MISMATCH:" + exact_version)
            continue
        exact_completed.append(completed)
    if exact_completed:
        previous = exact_completed[-1]
        saved_rel = str(previous.get("result") or "").replace("\\", "/")
        saved_file = ROOT / saved_rel
        report = load(saved_file, {}) if saved_file.is_file() else {}
        recorded_source = Path(str(report.get("strategy_file") or "").replace("\\", "/")).name
        if not report or recorded_source.lower() != source.name.lower():
            raise RuntimeError(
                "COMPLETED_TRIAL_RESULT_RECONCILIATION_REQUIRED:"
                + strategy_id + ":" + tf + ":" + exact_version
            )
        hard_eval = evaluate(report, tf, require_fixed_window=True)
        entry_gate = (
            evaluate_entry_risk_gate(report, tf, hard_eval)
            if bool(hard_eval.get("hard_pass"))
            else {"pass": False, "decision": "NOT_RUN", "reason": "HARD_PASS_REQUIRED"}
        )
        stage = (
            "HARD_PASS_RECHECK_FAIL" if not bool(hard_eval.get("hard_pass"))
            else "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" if bool(entry_gate.get("pass"))
            else "ENTRY_RISK_REDEVELOPMENT_PENDING"
        )
        target.update({
            "pipeline_stage": stage,
            "hard_pass_recheck": hard_eval,
            "entry_risk_gate": entry_gate,
            "new_result_json": saved_rel,
            "last_trial": previous.get("v3_trial"),
            "last_checked_at": now(),
            "direct_canary_allowed": False,
        })
        _save_historical_stage2(state)
        return {
            "status": "RECOVERED_COMPLETED_TRIAL",
            "strategy_id": strategy_id,
            "exact_strategy_version": exact_version,
            "timeframe": tf,
            "pipeline_stage": stage,
            "trial": previous.get("v3_trial"),
            "result_json": saved_rel,
            "automatic_promotion": False,
        }

    trial=knowledge.reserve_trial(
        f"V3_HISTORICAL_REENTRY_{strategy_id}_{tf}",
        strategy_id,
        tf,
        "HISTORICAL_HARD_PASS_RECHECK",
        exact_strategy_version=exact_version,
        source_file=str(source.relative_to(ROOT)).replace("\\", "/"),
        source_sha256=source_hash,
    )
    env=os.environ.copy()
    env["BBOT_LOCAL_ROOT"]=str(ROOT)
    research_start,research_end=hard_pass_dates()
    env["BBOT_RESEARCH_DATASET_MODE"]="HARD_PASS_ONLY"
    env["BBOT_RESEARCH_START"]=research_start
    env["BBOT_RESEARCH_END"]=research_end
    started=time.time()
    p=subprocess.run(
        [sys.executable,str(RUNNER),str(source)],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if p.returncode!=0:
        target["pipeline_stage"]="HARD_PASS_RECHECK_RUNTIME_FAIL"
        target["last_error"]="RUN_STRATEGY_FAIL"
        target["last_output_tail"]=p.stdout[-4000:]
        _save_historical_stage2(state)
        knowledge.complete_trial(trial,"RUN_STRATEGY_FAIL","FAIL","UNKNOWN")
        return {"status":"FAIL","reason":"RUN_STRATEGY_FAIL","trial":trial,"strategy_id":strategy_id,"timeframe":tf}

    result=find_result(strategy_id,started)
    if result is None:
        try:
            source_meta=load_meta(source)
            source_name=str(source_meta.get("name") or "").upper()
        except Exception:
            source_name=""
        if source_name:
            result=find_result(source_name,started)
    if result is None:
        target["pipeline_stage"]="HARD_PASS_RECHECK_RESULT_MISSING"
        target["last_error"]="RESULT_NOT_FOUND"
        _save_historical_stage2(state)
        knowledge.complete_trial(trial,"RESULT_NOT_FOUND","FAIL","UNKNOWN")
        return {"status":"FAIL","reason":"RESULT_NOT_FOUND","trial":trial,"strategy_id":strategy_id,"timeframe":tf}

    report=load(result,{})
    hard_eval=evaluate(report,tf,require_fixed_window=True)
    entry_gate=(
        evaluate_entry_risk_gate(report,tf,hard_eval)
        if bool(hard_eval.get("hard_pass"))
        else {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}
    )

    if not bool(hard_eval.get("hard_pass")):
        stage="HARD_PASS_RECHECK_FAIL"
    elif bool(entry_gate.get("pass")):
        stage="INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING"
    else:
        stage="ENTRY_RISK_REDEVELOPMENT_PENDING"

    target.update({
        "pipeline_stage":stage,
        "hard_pass_recheck":hard_eval,
        "entry_risk_gate":entry_gate,
        "new_result_json":str(result.relative_to(ROOT)).replace("\\","/"),
        "last_trial":trial,
        "last_checked_at":now(),
        "direct_canary_allowed":False,
    })
    _save_historical_stage2(state)

    row={
        "status":"PASS" if bool(hard_eval.get("hard_pass")) else "FAIL",
        "qualification":"HARD_PASS" if bool(hard_eval.get("hard_pass")) else "FAIL",
        "strategy":strategy_id,
        "strategy_id":strategy_id,
        "timeframe":tf,
        "exact_strategy_version":exact_version,
        "strategy_version_id":exact_version,
        "pipeline_stage":stage,
        "hard_pass_recheck":hard_eval,
        "entry_risk_gate":entry_gate,
        "result_json":str(result.relative_to(ROOT)),
        "canary_eligible":False,
        "automatic_promotion":False,
        "research_start":research_start,
        "research_end":research_end,
    }
    knowledge.record_result(
        f"pipeline_recheck:{strategy_id}:{tf}:{exact_version}",
        row,
        failed=(not bool(hard_eval.get("hard_pass"))),
    )
    knowledge.complete_trial(
        trial,
        str(result.relative_to(ROOT)),
        "PASS" if bool(hard_eval.get("hard_pass")) else "FAIL",
        "LOW" if bool(hard_eval.get("hard_pass")) else "UNKNOWN",
    )
    return {
        "status":"PASS" if bool(hard_eval.get("hard_pass")) else "FAIL",
        "trial":trial,
        "strategy_id":strategy_id,
        "exact_strategy_version":exact_version,
        "timeframe":tf,
        "pipeline_stage":stage,
        "hard_pass":bool(hard_eval.get("hard_pass")),
        "entry_risk_pass":bool(entry_gate.get("pass")),
        "entry_risk_decision":entry_gate.get("decision"),
        "result_json":str(result),
    }




def _pipeline_identity(strategy,version,tf):
    return f"{str(strategy or '').upper()}|{str(version or '')}|{str(tf or '').upper()}"


def _new_record_source(key,row):
    rel=str(row.get("family_file") or row.get("source_file") or "").replace("\\","/")
    if rel and (ROOT/rel).is_file(): return rel
    strategy=str(row.get("strategy") or "").lower()
    if str(key).startswith("combo:") and strategy:
        rel=f"strategy_lab/results/v3/generated_combinations/{strategy}.py"
        if (ROOT/rel).is_file(): return rel
    return ""


def sync_new_pipeline_entries(candidate_manifest=None):
    # 파일명: v3_strategy_research.py / 기존 대기열에 고정 후보 원본 연결
    # 기존 평가·실패·봉인 기록은 보존하고 미등록 소스만 검사 대기로 추가한다.
    if candidate_manifest is not None:
        manifest=load(Path(candidate_manifest),None)
        if not isinstance(manifest,list) or len(manifest)!=10:
            raise RuntimeError("FIXED_CANDIDATE_MANIFEST_REQUIRES_TEN")
        if not HISTORICAL_STAGE2.is_file():
            raise RuntimeError("EXISTING_PIPELINE_STATE_REQUIRED")
        state=load(HISTORICAL_STAGE2,{})
        entries=state.get("entries")
        if not isinstance(entries,list):
            raise RuntimeError("EXISTING_PIPELINE_ENTRIES_INVALID")
        registry=load(knowledge.KNOWLEDGE,{})
        prepared=[]; existing=[]; seen=set()
        rs,re=hard_pass_dates()
        for row in manifest:
            if not isinstance(row,dict):
                raise RuntimeError("FIXED_CANDIDATE_ROW_INVALID")
            rel=str(row.get("path") or "").replace("\\","/")
            expected=str(row.get("sha256") or "").lower()
            strategy=str(row.get("strategy") or "").upper()
            source=(ROOT/rel).resolve()
            try:
                source.relative_to((ROOT/"strategy_lab/results/v3").resolve())
            except ValueError:
                raise RuntimeError("FIXED_CANDIDATE_SOURCE_OUTSIDE_RESEARCH")
            if source.suffix!=".py" or not source.is_file() or not expected or _sha256_file(source)!=expected:
                raise RuntimeError("FIXED_CANDIDATE_SOURCE_HASH_MISMATCH:"+rel)
            if expected in seen:
                raise RuntimeError("FIXED_CANDIDATE_DUPLICATE_SOURCE")
            seen.add(expected)
            meta=load_meta(source)
            source_name=str(meta.get("name") or "").upper()
            valid_names={strategy}
            if strategy=="V3_EFFICIENCY_PULLBACK_RESUME":
                valid_names.add(strategy+"_30M_BROAD_V1")
            if source_name not in valid_names or [str(x).upper() for x in meta.get("allowed_timeframes",[])]!=["30M"]:
                raise RuntimeError("FIXED_CANDIDATE_METADATA_MISMATCH:"+rel)
            matches=[x for x in entries if isinstance(x,dict) and str(x.get("timeframe") or "").upper()=="30M" and str(x.get("source_sha256") or "").lower()==expected]
            if matches:
                existing.extend({"strategy_id":x.get("strategy_id"),"exact_strategy_version":x.get("exact_strategy_version"),"pipeline_stage":x.get("pipeline_stage")} for x in matches)
                continue
            for key,record in (registry.get("records") or {}).items():
                if not isinstance(record,dict) or str(record.get("timeframe") or "").upper()!="30M":
                    continue
                record_hash=str(record.get("source_sha256") or "").lower()
                record_rel=str(record.get("source_file") or record.get("family_file") or "").replace("\\","/")
                if record_hash==expected or (record_rel and (ROOT/record_rel).resolve()==source):
                    raise RuntimeError("FIXED_CANDIDATE_EXISTING_EVIDENCE_RECONCILIATION_REQUIRED:"+str(key))
            version="EV_"+expected[:16]+"_30M"
            identity=_pipeline_identity(strategy,version,"30M")
            if any(isinstance(x,dict) and x.get("canonical_identity_key")==identity for x in entries):
                raise RuntimeError("FIXED_CANDIDATE_IDENTITY_SOURCE_CONFLICT:"+identity)
            prepared.append({
                "strategy_id":strategy,"exact_strategy_version":version,"timeframe":"30M",
                "canonical_identity_key":identity,"canonical_source_file":rel,
                "source_sha256":expected,"source_record_key":"operator_fixed_candidate:"+identity,
                "pipeline_stage":"HARD_PASS_RECHECK_PENDING","eligible":True,
                "research_start":rs,"research_end":re,"research_epoch":NEW_RESEARCH_EPOCH,
                "pipeline_origin":"OPERATOR_FIXED_CANDIDATE","root_pipeline_origin":"NEW_AUTO_RESEARCH",
                "legacy_blind_credit":False,"direct_canary_allowed":False,"canary_eligible":False,
                "automatic_promotion":False,"redevelopment_cycles":0,"redevelopment_cycles_by_gate":{},
                "operator_requested_at":now(),"runtime_package_version_reference":str(row.get("version") or ""),
            })
        if prepared:
            entries.extend(prepared)
            state["updated_at"]=now()
            _save_historical_stage2(state)
        return {"status":"FIXED_CANDIDATES_REGISTERED","added":len(prepared),"added_candidates":[{"strategy_id":x["strategy_id"],"exact_strategy_version":x["exact_strategy_version"],"pipeline_stage":x["pipeline_stage"]} for x in prepared],"existing_preserved":existing,"automatic_promotion":False,"orders_touched":False}
    if not HISTORICAL_STAGE2.is_file(): prepare_historical_314_reentry()
    state=load(HISTORICAL_STAGE2,{})
    entries=state.setdefault("entries",[])
    identities={str(x.get("canonical_identity_key") or "") for x in entries if isinstance(x,dict)}
    k,_=knowledge.ensure(); added=0; skipped_old_epoch=0
    rs,re=hard_pass_dates()
    for key,row in sorted((k.get("records") or {}).items()):
        if not isinstance(row,dict): continue
        if not (str(key).startswith("single:") or str(key).startswith("generated:") or str(key).startswith("combo:")): continue
        if str(row.get("status") or "").upper()!="PASS": continue
        tf=str(row.get("timeframe") or "").upper()
        if tf not in ELIGIBLE_PIPELINE_TFS: continue
        if str(row.get("research_start") or "")!=rs or str(row.get("research_end") or "")!=re or row.get("fixed_window_contract") is not True:
            skipped_old_epoch+=1; continue
        source_rel=_new_record_source(key,row)
        if not source_rel: continue
        source=ROOT/source_rel
        source_sha=str(row.get("source_sha256") or _sha256_file(source))
        version=str(row.get("exact_strategy_version") or ("EV_"+source_sha[:16]+"_"+tf))
        strategy=str(row.get("strategy") or "").upper()
        identity=_pipeline_identity(strategy,version,tf)
        if identity in identities: continue
        hard_eval=dict(row.get("evaluation") or {})
        entry_gate=dict(row.get("entry_risk_gate") or {})
        if not entry_gate:
            rep=load(ROOT/str(row.get("result_json") or ""),{})
            entry_gate=evaluate_entry_risk_gate(rep,tf,hard_eval) if bool(hard_eval.get("hard_pass",hard_eval.get("pass"))) else {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}
        stage="INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" if bool(entry_gate.get("pass")) else "ENTRY_RISK_REDEVELOPMENT_PENDING"
        item={
            "strategy_id":strategy,"exact_strategy_version":version,"timeframe":tf,
            "canonical_identity_key":identity,"canonical_source_file":source_rel,
            "previous_result_json":str(row.get("result_json") or "").replace("\\","/"),
            "new_result_json":str(row.get("result_json") or "").replace("\\","/"),
            "source_record_key":str(key),"pipeline_stage":stage,"eligible":True,
            "research_start":rs,"research_end":re,"legacy_blind_credit":False,"direct_canary_allowed":False,
            "redevelopment_cycles":0,"redevelopment_cycles_by_gate":{},"pipeline_origin":"NEW_AUTO_RESEARCH","root_pipeline_origin":"NEW_AUTO_RESEARCH",
            "research_epoch":NEW_RESEARCH_EPOCH,"source_sha256":source_sha,"hard_pass_recheck":hard_eval,"entry_risk_gate":entry_gate,
            "recipe":dict(row.get("recipe") or {}),"components":list(row.get("components") or []),
        }
        entries.append(item); identities.add(identity); added+=1
    state["schema"]="B_BOT_UNIFIED_STRATEGY_PIPELINE_STAGE10_V1"
    state["unified_pipeline_enabled"]=True
    state["updated_at"]=now(); atomic(HISTORICAL_STAGE2,state)
    return {"status":"SYNCED","added":added,"skipped_old_epoch":skipped_old_epoch,"total_entries":len(entries),"automatic_promotion":False}


_RESEARCH_ACTIVE_STAGES={
    "HARD_PASS_RECHECK_PENDING","INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING","REENTRY_RESEARCH_PENDING",
    "FINAL_RISK_REVALIDATION_PENDING","FULL_VALIDATION_PENDING","SEALED_FINAL_BLIND_PENDING",
    "SEALED_FINAL_BLIND_ADDITIONAL_WINDOW_PENDING","SEALED_FINAL_BLIND_EVALUATION_PENDING",
    "ENTRY_RISK_REDEVELOPMENT_PENDING","FINAL_RISK_REDEVELOPMENT_PENDING","FULL_VALIDATION_REDEVELOPMENT_PENDING",
}


def pipeline_work_status():
    state=load(HISTORICAL_STAGE2,{})
    counts={}; historical_active=0; new_active=0
    for x in state.get("entries") or []:
        if not isinstance(x,dict) or x.get("eligible") is not True: continue
        st=str(x.get("pipeline_stage") or ""); counts[st]=counts.get(st,0)+1
        if st in _RESEARCH_ACTIVE_STAGES:
            root_origin=str(x.get("root_pipeline_origin") or x.get("pipeline_origin") or "HISTORICAL_314")
            if root_origin == "NEW_AUTO_RESEARCH": new_active+=1
            else: historical_active+=1
    return {
        "counts":counts,"historical_active":historical_active,"new_active":new_active,
        "historical_backlog_mode":historical_active>0,
        "hard_pass_pending":counts.get("HARD_PASS_RECHECK_PENDING",0),
        "trailing_pending":counts.get("INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",0),
        "reentry_pending":counts.get("REENTRY_RESEARCH_PENDING",0),
        "final_risk_pending":counts.get("FINAL_RISK_REVALIDATION_PENDING",0),
        "full_validation_pending":counts.get("FULL_VALIDATION_PENDING",0),
        "blind_prepare_pending":counts.get("SEALED_FINAL_BLIND_PENDING",0)+counts.get("SEALED_FINAL_BLIND_ADDITIONAL_WINDOW_PENDING",0),
        "blind_evaluation_pending":counts.get("SEALED_FINAL_BLIND_EVALUATION_PENDING",0),
        "redevelopment_pending":counts.get("ENTRY_RISK_REDEVELOPMENT_PENDING",0)+counts.get("FINAL_RISK_REDEVELOPMENT_PENDING",0)+counts.get("FULL_VALIDATION_REDEVELOPMENT_PENDING",0),
        "local_shadow_pending":counts.get("LOCAL_SHADOW_PENDING",0),
    }


def _redevelopment_gate_for_stage(stage):
    return {"ENTRY_RISK_REDEVELOPMENT_PENDING":"ENTRY_RISK","FINAL_RISK_REDEVELOPMENT_PENDING":"FINAL_RISK_REVALIDATION","FULL_VALIDATION_REDEVELOPMENT_PENDING":"FULL_VALIDATION"}.get(str(stage or ""),"")


def _redeveloped_exact_version(source_sha,tf,gate,cycle,extra=""):
    raw=f"{source_sha}|{tf}|{gate}|{cycle}|{extra}".encode("utf-8")
    return "EVR_"+hashlib.sha256(raw).hexdigest()[:20]+"_"+str(tf).upper()


def _make_prior_signal_confirmation_source(target,gate,cycle):
    base_by_gate=dict(target.get("redevelopment_base_source_by_gate") or {})
    source_rel=str(base_by_gate.get(gate) or target.get("canonical_source_file") or "")
    source=ROOT/source_rel
    if not source.is_file(): return None,"SOURCE_MISSING"
    text=source.read_text(encoding="utf-8-sig",errors="strict")
    bars=int(cycle)
    name=f"{str(target.get('strategy_id') or '').upper()}_R{gate}_{cycle}"
    code=f"""# -*- coding: utf-8 -*-\n# PORTABLE_TARGETED_REDEVELOPMENT_V1\n# Failure-related bounded prior-signal confirmation only.\nimport sys,types,numpy as np\n_SRC={text!r}\n_mod=types.ModuleType("_bbot_redev_parent")\n_mod.__file__="<embedded:redev_parent>"\nsys.modules[_mod.__name__]=_mod\nexec(compile(_SRC,_mod.__file__,"exec"),_mod.__dict__)\nSTRATEGY_META=dict(getattr(_mod,"STRATEGY_META",{{}}))\nSTRATEGY_META["name"]={name!r}\nSTRATEGY_META["redevelopment_gate"]={gate!r}\nSTRATEGY_META["redevelopment_cycle"]={cycle!r}\nSTRATEGY_META["prior_signal_confirmation_bars"]={bars!r}\ndef generate_signals(df,tf):\n    raw=np.asarray(_mod.generate_signals(df,tf),dtype=np.int8)\n    out=np.zeros(len(raw),dtype=np.int8)\n    if len(raw)==0:return out\n    ok=(raw!=0)\n    for lag in range(1,{bars!r}+1):\n        shifted=np.zeros(len(raw),dtype=np.int8)\n        shifted[lag:]=raw[:-lag]\n        ok &= (shifted==raw)\n    out[ok]=raw[ok]\n    return out\ndef signal(df,params=None):\n    z=generate_signals(df,"LIVE")\n    v=int(z[-1]) if len(z) else 0\n    return {{"signal":"long" if v>0 else "short" if v<0 else "wait","state":"long" if v>0 else "short" if v<0 else "wait","strength_long":1.0 if v>0 else 0.0,"strength_short":1.0 if v<0 else 0.0,"reason":STRATEGY_META.get("name","")}}\n"""
    REDEVELOPED.mkdir(parents=True,exist_ok=True)
    out=REDEVELOPED/(name.lower()+".py"); compile(code,str(out),"exec"); out.write_text(code,encoding="utf-8")
    return out,""


def _conservative_trailing_alternatives(target):
    stage3=load(TRAILING_STAGE3,{})
    item=((stage3.get("entries") or {}).get(str(target.get("canonical_identity_key") or "")) or {})
    rows=[]; selected=target.get("individual_trailing") or {}
    selected_key=_trailing_pair_key(selected.get("start_pct",3.0),selected.get("callback_pct",1.5))
    merged={}; merged.update(item.get("coarse_results") or {}); merged.update(item.get("fine_results") or {})
    for key,row in merged.items():
        if key==selected_key or not isinstance(row,dict) or row.get("status")!="MEASURED": continue
        ev=row.get("evidence") or {}; wf=finite(ev.get("walk_forward_oos_expectancy_pct")); stress=finite(ev.get("cost_stress_2x_expectancy_pct")); mdd=finite(ev.get("normalized_mdd_pct_points")); dd=finite(ev.get("dd_duration_hours")); ex=finite(ev.get("expectancy_pct"))
        if wf is None or wf<=0 or stress is None or stress<=0 or mdd is None: continue
        rows.append({"start_pct":float(row.get("start_pct")),"callback_pct":float(row.get("callback_pct")),"mdd":mdd,"dd":dd if dd is not None else 1e18,"expectancy":ex if ex is not None else -1e18})
    rows.sort(key=lambda r:(r["mdd"],r["dd"],-r["expectancy"],r["start_pct"],r["callback_pct"]))
    return rows[:3]


def run_targeted_redevelopment_one():
    state=load(HISTORICAL_STAGE2,{})
    pending=[]
    for index,x in enumerate(state.get("entries") or []):
        if not isinstance(x,dict) or x.get("eligible") is not True:
            continue
        gate=_redevelopment_gate_for_stage(x.get("pipeline_stage"))
        if not gate:
            continue
        counts=dict(x.get("redevelopment_cycles_by_gate") or {})
        next_cycle=int(counts.get(gate) or 0)+1
        pending.append((0 if next_cycle<=2 else 1,next_cycle,index,x))
    target=min(pending,key=lambda row:(row[0],row[1],row[2]))[3] if pending else None
    if target is None: return {"status":"NO_REDEVELOPMENT_BACKLOG","automatic_promotion":False}
    gate=_redevelopment_gate_for_stage(target.get("pipeline_stage"))
    counts=dict(target.get("redevelopment_cycles_by_gate") or {}); cycle=int(counts.get(gate) or 0)+1
    if cycle>3:
        target["pipeline_stage"]="PERSISTENT_FAIL"; target["persistent_fail_gate"]=gate; target["last_error"]="REDEVELOPMENT_CYCLE_LIMIT"; _save_historical_stage2(state)
        return {"status":"PERSISTENT_FAIL","gate":gate,"reason":"REDEVELOPMENT_CYCLE_LIMIT","automatic_promotion":False}
    source=ROOT/str(target.get("canonical_source_file") or "")
    if not source.is_file():
        target["pipeline_stage"]="PERSISTENT_FAIL"; target["last_error"]="REDEVELOPMENT_SOURCE_MISSING"; _save_historical_stage2(state)
        return {"status":"PERSISTENT_FAIL","gate":gate,"reason":"SOURCE_MISSING","automatic_promotion":False}
    source_sha=_sha256_file(source); new_source=source; override=None; method=""
    if gate in {"ENTRY_RISK","FULL_VALIDATION"}:
        new_source,err=_make_prior_signal_confirmation_source(target,gate,cycle)
        if new_source is None:
            target["pipeline_stage"]="PERSISTENT_FAIL"; target["last_error"]="NO_FAILURE_RELATED_MUTATION_CONTRACT:"+err; _save_historical_stage2(state)
            return {"status":"PERSISTENT_FAIL","gate":gate,"reason":"NO_FAILURE_RELATED_MUTATION_CONTRACT","automatic_promotion":False}
        source_sha=_sha256_file(new_source); method="PRIOR_SIGNAL_CONFIRMATION"; extra=f"confirm={cycle}"
    elif gate=="FINAL_RISK_REVALIDATION":
        alts=list(target.get("redevelopment_trailing_alternatives") or [])
        if not alts: alts=_conservative_trailing_alternatives(target)
        if len(alts)<cycle:
            target["pipeline_stage"]="PERSISTENT_FAIL"; target["last_error"]="NO_CONSERVATIVE_MEASURED_TRAILING_ALTERNATIVE"; _save_historical_stage2(state)
            return {"status":"PERSISTENT_FAIL","gate":gate,"reason":"NO_CONSERVATIVE_MEASURED_TRAILING_ALTERNATIVE","automatic_promotion":False}
        alt=alts[cycle-1]; override={"start_pct":alt["start_pct"],"callback_pct":alt["callback_pct"],"fallback":False,"redevelopment_override":True}; method="CONSERVATIVE_MEASURED_TRAILING"; extra=f"{alt['start_pct']}|{alt['callback_pct']}"
    else:
        target["pipeline_stage"]="PERSISTENT_FAIL"; target["last_error"]="UNSUPPORTED_GATE"; _save_historical_stage2(state)
        return {"status":"PERSISTENT_FAIL","gate":gate,"reason":"UNSUPPORTED_GATE","automatic_promotion":False}
    tf=str(target.get("timeframe") or "").upper(); version=_redeveloped_exact_version(source_sha,tf,gate,cycle,extra); identity=_pipeline_identity(target.get("strategy_id"),version,tf)
    counts[gate]=cycle
    child={
        "strategy_id":target.get("strategy_id"),"exact_strategy_version":version,"timeframe":tf,"canonical_identity_key":identity,
        "canonical_source_file":str(new_source.relative_to(ROOT)).replace("\\","/"),"previous_result_json":target.get("final_strategy_result_json") or target.get("new_result_json") or target.get("previous_result_json"),
        "source_record_key":"targeted_redevelopment:"+identity,"pipeline_stage":"HARD_PASS_RECHECK_PENDING","eligible":True,
        "research_start":hard_pass_dates()[0],"research_end":hard_pass_dates()[1],"legacy_blind_credit":False,"direct_canary_allowed":False,
        "redevelopment_cycles":cycle,"redevelopment_cycles_by_gate":counts,"pipeline_origin":"TARGETED_REDEVELOPMENT",
        "root_pipeline_origin":str(target.get("root_pipeline_origin") or target.get("pipeline_origin") or "HISTORICAL_314"),
        "parent_exact_strategy_version":target.get("exact_strategy_version"),"redevelopment_gate":gate,"redevelopment_method":method,
        "source_sha256":source_sha,"research_epoch":NEW_RESEARCH_EPOCH,
    }
    base_by_gate=dict(target.get("redevelopment_base_source_by_gate") or {})
    if gate not in base_by_gate: base_by_gate[gate]=str(target.get("canonical_source_file") or "")
    child["redevelopment_base_source_by_gate"]=base_by_gate
    if gate=="FINAL_RISK_REVALIDATION": child["redevelopment_trailing_alternatives"]=alts
    if override is not None: child["redevelopment_trailing_override"]=override
    target["pipeline_stage"]="REDEVELOPMENT_SUPERSEDED_BY_NEW_VERSION"; target["redevelopment_child_exact_version"]=version; target["redevelopment_cycles_by_gate"]=counts
    state.setdefault("entries",[]).append(child); _save_historical_stage2(state)
    return {"status":"NEW_EXACT_VERSION_CREATED","gate":gate,"cycle":cycle,"method":method,"parent_exact_strategy_version":target.get("exact_strategy_version"),"exact_strategy_version":version,"pipeline_stage":"HARD_PASS_RECHECK_PENDING","automatic_promotion":False}

def completed_pairs():
    _,ledger=knowledge.ensure(); out=set()
    for e in ledger.get("entries") or []:
        if e.get("event")!="COMPLETED": continue
        if str(e.get("kind") or "").upper()!="SINGLE_FAMILY_HP2016_2024": continue
        c=str(e.get("candidate") or ""); tf=str(e.get("timeframe") or "").upper()
        if tf in ELIGIBLE_PIPELINE_TFS: out.add((c,tf))
    return out


def _research_tf_distribution():
    row=(pipeline_policy().get("auto_research_allocation_contract") or {}).get("new_strategy_timeframe_distribution_percent") or {}
    out={tf:max(0.0,float(row.get(tf) or 0.0))/100.0 for tf in TF_ORDER}
    total=sum(out.values())
    if total<=0: return {tf:1.0/max(len(TF_ORDER),1) for tf in TF_ORDER}
    return {tf:v/total for tf,v in out.items()}


def _weighted_tf_choice(pending,done_counts=None):
    available=[tf for tf in TF_ORDER if pending.get(tf)]
    if not available: return None
    weights=_research_tf_distribution(); done_counts=done_counts or {}
    total=sum(int(done_counts.get(tf) or 0) for tf in TF_ORDER)
    return max(available,key=lambda tf:(weights.get(tf,0.0)*(total+1)-int(done_counts.get(tf) or 0),weights.get(tf,0.0),-TF_ORDER.index(tf)))

def qualified_candidate_rows(tf):
    tf=str(tf or "").upper()
    k,_=knowledge.ensure()
    rows=[]
    seen=set()

    for key,row in (k.get("records") or {}).items():
        if not isinstance(row,dict):
            continue

        if str(row.get("timeframe") or "").upper()!=tf:
            continue

        if str(row.get("status") or "").upper()!="PASS":
            continue

        kl=str(key or "").lower()

        if (
            kl.startswith("runtime:")
            or "boundary" in kl
            or "shadow" in kl
            or "validation" in kl
            or "deployment" in kl
        ):
            continue

        identity=str(
            row.get("strategy")
            or row.get("selected_strategy")
            or row.get("candidate")
            or ""
        ).strip().upper()

        if not identity or identity in seen:
            continue

        seen.add(identity)
        rows.append(row)

    return rows


def qualified_candidate_count(tf):
    return len(qualified_candidate_rows(tf))


def candidate_fill_priority():
    underfilled=[
        tf
        for tf in MAIN_FILL_TFS
        if qualified_candidate_count(tf)
        < CANDIDATE_FILL_TARGET
    ]

    return sorted(
        underfilled,
        key=lambda tf:(
            qualified_candidate_count(tf),
            MAIN_FILL_TFS.index(tf),
        ),
    )


def choose_next(timeframe=None, excluded_families=(), excluded_timeframes=()):
    done=completed_pairs(); files=family_files(); pending={}; done_counts={}
    for tf in TF_ORDER:
        pending[tf]=[]; done_counts[tf]=0
        for p in files:
            name=str(load_meta(p).get("name") or p.stem).upper()
            if (name,tf) in done: done_counts[tf]+=1
            elif (timeframe is None or tf == timeframe) and tf not in excluded_timeframes and name not in excluded_families: pending[tf].append(p)
    # CANDIDATE_FILL_PRIORITY_BINDING_V1
    fill_priority=candidate_fill_priority()
    tf=next((x for x in fill_priority if pending.get(x)),None)
    if tf is None:
        tf=_weighted_tf_choice(pending,done_counts)
    if tf is None: return None
    return tf,pending[tf][0]

def run_one(timeframe=None, excluded_families=(), prepare_only=False, task=None, process=None):
    # 예약과 결과 반영은 컨트롤러 한 곳에서만 수행한다. 계산만 병렬 실행한다.
    if task is None:
        nxt=choose_next(timeframe, excluded_families)
        if not nxt:return {"status":"BROAD_STANDALONE_COMPLETE","at":now()}
        tf,path=nxt; wrapper,strategy_name,meta=wrapper_for(path,tf)
        base=str(meta.get("name") or path.stem).upper()
        trial=knowledge.reserve_trial(f"V3_SINGLE_HP2016_2024_{base}_{tf}",base,tf,"SINGLE_FAMILY_HP2016_2024")
        research_start,research_end=hard_pass_dates()
        task={"kind":"SINGLE", "timeframe":tf, "family":base, "trial":trial,
              "source":str(wrapper.relative_to(ROOT)), "parent":str(path.relative_to(ROOT)),
              "strategy_name":strategy_name, "source_sha256":_sha256_file(wrapper),
              "started":time.time(), "env":{"BBOT_RESEARCH_DATASET_MODE":"HARD_PASS_ONLY",
              "BBOT_RESEARCH_START":research_start,"BBOT_RESEARCH_END":research_end}}
        if prepare_only:return task
    tf=task["timeframe"]; base=task["family"]; trial=task["trial"]
    wrapper=ROOT/task["source"]; path=ROOT/task["parent"]
    strategy_name=task["strategy_name"]; start=task["started"]
    if _sha256_file(wrapper)!=task["source_sha256"]:raise RuntimeError("PARALLEL_SOURCE_CHANGED")
    if process is None:
        env=os.environ.copy();env.update(task["env"],BBOT_LOCAL_ROOT=str(ROOT))
        process=subprocess.run([sys.executable,str(RUNNER),str(wrapper)],cwd=str(ROOT),env=env,text=True,encoding="utf-8",errors="replace",stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=1800)
    p=process
    if p.returncode!=0:
        knowledge.complete_trial(trial,"RUN_STRATEGY_FAIL","FAIL","UNKNOWN")
        knowledge.record_result(f"single:{base}:{tf}",{"strategy":base,"timeframe":tf,"trial":trial,"status":"FAIL","reason":"RUN_STRATEGY_FAIL","output_tail":p.stdout[-4000:]},failed=True)
        return {"status":"FAIL","trial":trial,"strategy":base,"timeframe":tf,"reason":"RUN_STRATEGY_FAIL"}
    result=ROOT/task["result_file"] if task.get("result_file") else find_result(strategy_name,start)
    if result is None:
        # RESULT_NOT_FOUND_REJECT_MEMORY_V1
        _result_not_found_source = locals().get('h')
        if not isinstance(_result_not_found_source,dict):
            _result_not_found_source = {}
        _result_not_found_strategy = str(
            locals().get('strategy_name')
            or locals().get('name')
            or locals().get('strategy')
            or _result_not_found_source.get('strategy')
            or _result_not_found_source.get('name')
            or ''
        ).upper()
        _result_not_found_origin = str(
            locals().get('origin')
            or (
                'SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE'
                if bool(locals().get('recovery'))
                else 'RESULT_NOT_FOUND'
            )
        ).upper()
        _result_not_found_row = {
            'status': 'FAIL',
            'reason': 'RESULT_NOT_FOUND',
            'trial': trial,
            'strategy': _result_not_found_strategy,
            'timeframe': str(tf).upper(),
            'research_origin': _result_not_found_origin,
        }
        knowledge.record_result(
            f'result_missing:{tf}:{trial}',
            _result_not_found_row,
            failed=True,
        )
        knowledge.complete_trial(trial,"RESULT_NOT_FOUND","FAIL","UNKNOWN"); return {"status":"FAIL","reason":"RESULT_NOT_FOUND","trial":trial}
    report=load(result,{})
    ev=evaluate(report,tf,require_fixed_window=True)
    entry_gate=(
        evaluate_entry_risk_gate(report,tf,ev)
        if bool(ev.get("hard_pass"))
        else {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}
    )
    # SOFT_PASS_STATUS_SINGLE_V1
    status=("PASS" if bool(ev.get("hard_pass",ev.get("pass"))) else ("SOFT_PASS" if bool(ev.get("soft_pass")) else "FAIL"))
    hold=ev.get("expected_holding_time_hours")
    edge_pct=ev.get("walk_forward_avg_net_pct"); edge=(float(edge_pct)/100.0) if edge_pct is not None else None
    cur=(((report.get("datasets") or {}).get(HARD_PASS_DATASET) or {}).get("timeframes") or {}).get(tf) or {}
    sm=cur.get("summary") if isinstance(cur.get("summary"),dict) else {}; loss_pct=finite(sm.get("avg_loss_pct")); loss=abs(loss_pct/100.0) if loss_pct is not None else None
    cte=(float(edge)/float(hold)) if edge is not None and hold not in (None,0) and float(hold)>0 else None
    row={"strategy":base,"family_file":str(wrapper.relative_to(ROOT)).replace("\\","/"),"parent_family_file":str(path.relative_to(ROOT)).replace("\\","/"),"timeframe":tf,"trial":trial,"status":status,"evaluation":ev,"result_json":str(result.relative_to(ROOT)),"evidence_summary":{"backtest":"EXACT_LONG_CACHE_HARD_PASS_FIXED_WINDOW","development":"CHRONOLOGICAL_FIRST_40PCT","walk_forward":"CHRONOLOGICAL_NEXT_20PCT","legacy_internal_holdout":"HISTORICAL_EVIDENCE_ONLY","cost_stress":"2X","long_core":"BTC_ETH_SOL_XRP"},"expected_return":edge,"expected_loss":loss,"expected_holding_time_hours":hold,"capital_time_efficiency":cte,"expected_account_contribution":edge,"confidence":None,"confidence_level":"INITIAL_BACKTEST_ONLY","strategy_version":"BROAD_V1","expectation_basis":"NET_EXPECTED_RETURN_PER_UNIT_CAPITAL_SAME_ENGINE"}
    row["entry_risk_gate"]=entry_gate
    row["pipeline_stage"]=(
        "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING"
        if status=="PASS" and bool(entry_gate.get("pass"))
        else ("ENTRY_RISK_REDEVELOPMENT_PENDING" if status=="PASS" else "HARD_PASS_FAIL")
    )
    row["research_start"],row["research_end"]=hard_pass_dates()
    row["fixed_window_contract"]=True
    row["research_epoch"]=NEW_RESEARCH_EPOCH
    row["source_sha256"]=_sha256_file(wrapper)
    row["exact_strategy_version"]="EV_"+row["source_sha256"][:16]+"_"+tf
    row["automatic_promotion"]=False
    # SOFT_PASS_ROW_FLAGS_SINGLE_V1
    row["qualification"]=(
        "HARD_PASS"
        if status=="PASS"
        else status
    )
    row["research_shadow_eligible"]=(status=="SOFT_PASS")
    row["canary_eligible"]=False
    knowledge.complete_trial(trial,str(result.relative_to(ROOT)),status,"LOW" if status in {"PASS","SOFT_PASS"} else "UNKNOWN")
    knowledge.record_result(f"single:{base}:{tf}",row,failed=(status=="FAIL"))
    if ev["pass"]:
        for sym,se in (cur.get("symbol_evidence") or {}).items():
            if not isinstance(se,dict): continue
            ssm=se.get("summary") if isinstance(se.get("summary"),dict) else {}; srb=se.get("v3_robustness") if isinstance(se.get("v3_robustness"),dict) else {}; swf=srb.get("walk_forward") if isinstance(srb.get("walk_forward"),dict) else {}
            sedge_pct=finite(swf.get("avg_net_pct")); shold=finite(ssm.get("avg_hold_hours")); sloss_pct=finite(ssm.get("avg_loss_pct")); strades=int(ssm.get("trades") or 0)
            if sedge_pct is None or shold is None or shold<=0 or strades<=0: continue
            sedge=sedge_pct/100.0; rec=dict(row); rec.update({"symbol":str(sym).upper(),"expected_return":sedge,"expected_loss":abs(sloss_pct/100.0) if sloss_pct is not None else None,"expected_holding_time_hours":shold,"capital_time_efficiency":sedge/shold,"expected_account_contribution":sedge,"sample_trades":strades})
            knowledge.record_result(f"runtime:{base}:{tf}:{str(sym).upper()}",rec,failed=False)
    return {"status":status,"trial":trial,"strategy":base,"timeframe":tf,"evaluation":ev,"result_json":str(result)}

def all_results_for_tf(tf):
    k,_=knowledge.ensure(); rows=[]
    for key,row in (k.get("records") or {}).items():
        if not (key.startswith("single:") or key.startswith("generated:")):
            continue
        if str(row.get("timeframe") or "").upper()==tf and str(row.get("status") or "").upper()=="PASS":
            rows.append(row)
    return rows

_LOCAL_SHADOW_PORTABLE_SOURCE_PREFIXES=(
    "strategy_lab/results/v3/generated_combinations/",
    "strategy_lab/results/v3/generated_safe_grammar/",
    "strategy_lab/results/v3/redeveloped_strategies/",
    "strategy_lab/deploy_ready/",
)

def _pipeline_source_portable(source_rel):
    source_rel=str(source_rel or "").replace("\\","/").lstrip("./")
    if not source_rel.lower().endswith(".py"): return False
    if source_rel.startswith("strategy_lab/results/v3/generated_trials/"):
        p=ROOT/source_rel
        try: return "PORTABLE_GENERATED_SINGLE_V2" in p.read_text(encoding="utf-8-sig",errors="strict")[:4096]
        except Exception: return False
    return bool(
        any(source_rel.startswith(prefix) for prefix in _LOCAL_SHADOW_PORTABLE_SOURCE_PREFIXES)
        and "/" not in next((source_rel[len(prefix):] for prefix in _LOCAL_SHADOW_PORTABLE_SOURCE_PREFIXES if source_rel.startswith(prefix)), "")
    )

def _pipeline_local_shadow_choice(tf, exclude_strategy_ids=()):
    tf=str(tf or "").upper()
    state=load(HISTORICAL_STAGE2,{})
    rows=[]
    for target in (state.get("entries") or []):
        if not isinstance(target,dict) or target.get("eligible") is not True:
            continue
        if str(target.get("timeframe") or "").upper()!=tf:
            continue
        if str(target.get("strategy_id") or "").upper() in exclude_strategy_ids:
            continue
        if str(target.get("pipeline_stage") or "")!="LOCAL_SHADOW_PENDING":
            continue
        if (target.get("full_validation_gate") or {}).get("pass") is not True:
            continue
        if (target.get("sealed_final_blind_gate") or {}).get("pass") is not True:
            continue
        source_rel=str(target.get("canonical_source_file") or "").replace("\\","/")
        if not _pipeline_source_portable(source_rel):
            continue
        source=ROOT/source_rel
        if not source.is_file():
            continue
        trailing=dict(target.get("individual_trailing") or {})
        if finite(trailing.get("start_pct")) is None or finite(trailing.get("callback_pct")) is None:
            continue
        reentry=dict(target.get("individual_reentry") or {})
        if str(reentry.get("mode") or "").upper() != "CURRENT_STATE_NEXT_1H":
            continue
        row=dict(target)
        row.update({
            "strategy":str(target.get("strategy_id") or "").upper(),
            "strategy_id":str(target.get("strategy_id") or "").upper(),
            "strategy_version_id":str(target.get("exact_strategy_version") or ""),
            "exact_strategy_version":str(target.get("exact_strategy_version") or ""),
            "timeframe":tf,
            "evaluation":dict(target.get("hard_pass_recheck") or {}),
            "canonical_source_file":source_rel,
            "pipeline_contract":"NEW_MAIN_LOCAL_SHADOW_V1",
            "confidence_level":"SEALED_BLIND_PASS",
            "sealed_blind":{
                "pass":True,
                "validation_type":"UNEXPOSED_SYMBOL_TIME_WINDOW_FINAL_BLIND",
                "gate":dict(target.get("sealed_final_blind_gate") or {}),
            },
            "canary_eligible":False,
            "automatic_promotion":False,
        })
        rows.append(row)
    rows.sort(key=lambda row:(str(row.get("last_checked_at") or ""),str(row.get("strategy") or ""),str(row.get("exact_strategy_version") or "")))
    return rows[0] if rows else None

def local_shadow_pipeline_status(tf=""):
    state=load(HISTORICAL_STAGE2,{})
    tf=str(tf or "").upper()
    pending=[]; portable=[]; nonportable=[]
    for target in (state.get("entries") or []):
        if not isinstance(target,dict) or target.get("eligible") is not True:
            continue
        if str(target.get("pipeline_stage") or "")!="LOCAL_SHADOW_PENDING":
            continue
        if tf and str(target.get("timeframe") or "").upper()!=tf:
            continue
        pending.append(target)
        if _pipeline_source_portable(target.get("canonical_source_file")):
            portable.append(target)
        else:
            nonportable.append(target)
    return {
        "pending":len(pending),
        "portable":len(portable),
        "portable_source_required":len(nonportable),
        "nonportable_exact_versions":[str(x.get("exact_strategy_version") or "") for x in nonportable[:50]],
        "automatic_promotion":False,
    }

def provisional_choice(tf):
    if (pipeline_policy().get("final_blind_contract") or {}).get("official_gate_name")=="SEALED_FINAL_BLIND":
        return _pipeline_local_shadow_choice(tf)
    # Standalone 18 families + bounded BASE safe-grammar + structural mutation
    # research wave? ?? ?? ??? ?? TF? one-shot sealed blind? ??.
    done=completed_pairs(); files=family_files()
    if any((str(load_meta(p).get("name") or p.stem).upper(),tf) not in done for p in files):
        return None
    import v3_hypothesis_factory as hypothesis_factory
    # MAIN_FIRST_AVAILABLE_BASE_POOL
    # INITIAL_MAIN_FILL_POOL_18_PLUS_48
    # Full BASE-48 / Mutation-24 completion does not block initial MAIN.

    rows=all_results_for_tf(tf)

    # MUTATION_RESEARCH_CONTINUES_AFTER_INITIAL_MAIN_FILL
    # MUTATION_WAVE_RESEARCH_ONLY_UNTIL_PORTABLE_VALIDATION
    # Mutation evidence is accumulated automatically but cannot become a
    # promotable Strategy Version until a separate portable-artifact
    # validation proves exact behavior parity.
    rows=[
        r for r in rows
        if "_M_" not in str(
            r.get("strategy") or ""
        ).upper()
    ]

    if not rows:return None
    def key(r):
        e=r.get("evaluation") or {}
        return (finite(e.get("walk_forward_cost_stress_2x_avg_net_pct")) or -1e9,finite(e.get("walk_forward_avg_net_pct")) or -1e9,finite(e.get("development_avg_net_pct")) or -1e9,-(finite(e.get("expected_holding_time_hours")) or 1e9))
    best=max(rows,key=key)
    k,_=knowledge.ensure()
    bkey=f"blind:{tf}:WAVE1"
    boundary_key=f"V3_WAVE1_{tf}_SEALED_BLIND"
    prior=(k.get("records") or {}).get(bkey)
    boundary=(k.get("evidence_boundaries") or {}).get(boundary_key) or {}
    boundary_status=str(boundary.get("status") or "").upper()
    if isinstance(prior,dict):
        if not (
            prior.get("pass")
            and str(
                prior.get("selected_strategy")
                or ""
            ).upper()
            ==
            str(
                best.get("strategy")
                or ""
            ).upper()
        ):
            return None

        out=dict(best)
        out["sealed_blind"]=(
            prior.get("sealed_blind")
            or {}
        )
        out["confidence_level"]="SEALED_BLIND_PASS"
        out["evidence_summary"]=dict(
            out.get("evidence_summary")
            or {},
            sealed_blind="PASS_ONE_SHOT_REHYDRATED",
        )
        return out
    # SEALED_BOUNDARY_RECORD_MISSING_BLOCK
    # A consumed one-shot sealed segment is irreversible. Missing/corrupt
    # result metadata must never cause the sealed segment to be reopened.
    if boundary_status in {"SEEN_ONE_SHOT","REUSED_FOR_RESEARCH"}:
        return None
    blind=_blind_result_for_row(best)
    record={"timeframe":tf,"selected_strategy":best.get("strategy"),"pass":bool(blind.get("pass")),"sealed_blind":blind,"status":"PASS" if blind.get("pass") else "FAIL","evidence_boundary":"SEALED_OPENED_ONCE_AFTER_TF_BROAD_SELECTION","strategy_version":best.get("strategy_version"),"candidate_pool":"18_SINGLE_PLUS_48_SAFE_GRAMMAR"}
    knowledge.set_boundary(f"V3_WAVE1_{tf}_SEALED_BLIND","SEEN_ONE_SHOT")
    # SEALED_BLIND_OFFICIAL_RECORD_V1
    knowledge.record_result(bkey, record, failed=False)
    if not bool(blind.get("pass")):
        _sealed_fail_memory = dict(record)
        _sealed_fail_memory["official_record_key"] = bkey
        knowledge.record_result(
            bkey,
            _sealed_fail_memory,
            failed=True,
        )
    if not blind.get("pass"):
        return None
    best=dict(best); best["sealed_blind"]=blind; best["confidence_level"]="SEALED_BLIND_PASS"; best["evidence_summary"]=dict(best.get("evidence_summary") or {},sealed_blind="PASS_ONE_SHOT")
    return best


def deploy_copy_for(row):
    src=ROOT/str(row["family_file"]); name=str(row["strategy"]).upper(); dst=DEPLOY/(name.lower()+".py"); DEPLOY.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst); return dst

def _peek_provisional(tf):
    k,_=knowledge.ensure(); rec=(k.get("records") or {}).get(f"blind:{tf}:WAVE1")
    if not isinstance(rec,dict) or not rec.get("pass"):
        return None
    selected=str(rec.get("selected_strategy") or "").upper()
    for row in all_results_for_tf(tf):
        if str(row.get("strategy") or "").upper()==selected:
            out=dict(row); out["sealed_blind"]=rec.get("sealed_blind") or {}; out["confidence_level"]="SEALED_BLIND_PASS"; return out
    return None

def status():
    # Pure status: never opens a sealed blind or mutates research evidence.
    files=family_files(); done=completed_pairs(); out={"schema":"B_BOT_V3_STRATEGY_RESEARCH_STATUS_V1","updated_at":now(),"family_count":len(files),"timeframes":{}}
    for tf in TF_ORDER:
        cnt=sum((str(load_meta(p).get("name") or p.stem).upper(),tf) in done for p in files)
        phase="MAIN_FILL" if tf in MAIN_FILL_TFS else ("RESEARCH_ONLY_UNTIL_QUALIFIED" if tf in RESEARCH_ONLY_TFS else "ACTIVE_MAIN_DETAIL_CHALLENGER")
        out["timeframes"][tf]={"weight":TF_WEIGHTS.get(tf),"phase":phase,"completed_broad_trials":cnt,"budget":len(files),"broad_complete":cnt==len(files),"provisional_choice":_peek_provisional(tf) if tf not in RESEARCH_ONLY_TFS else None}
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["once","status","historical-reentry","historical-once","trailing-once","reentry-once","reentry-stage13-migrate","final-risk-once","full-validation-once","blind-prepare","blind-once","redevelopment-once","pipeline-sync","pipeline-status"],default="once"); args=ap.parse_args()
    if args.mode=="status": print(json.dumps(status(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="historical-reentry": print(json.dumps(prepare_historical_314_reentry(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="pipeline-sync": print(json.dumps(sync_new_pipeline_entries(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="pipeline-status": print(json.dumps(pipeline_work_status(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="redevelopment-once": print(json.dumps(run_targeted_redevelopment_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="historical-once": print(json.dumps(run_historical_reentry_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="trailing-once": print(json.dumps(run_individual_trailing_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="reentry-once": print(json.dumps(run_individual_reentry_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="reentry-stage13-migrate": print(json.dumps(migrate_reentry_contract_stage13(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="final-risk-once": print(json.dumps(run_final_risk_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="full-validation-once": print(json.dumps(run_full_validation_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="blind-prepare": print(json.dumps(prepare_sealed_final_blind_one(),ensure_ascii=False,indent=2,default=str)); return
    if args.mode=="blind-once": print(json.dumps(run_sealed_final_blind_one(),ensure_ascii=False,indent=2,default=str)); return
    print(json.dumps(run_one(),ensure_ascii=False,indent=2,default=str))

if __name__=="__main__": main()
