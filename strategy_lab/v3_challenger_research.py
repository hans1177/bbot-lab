# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_challenger_research.py
"""Evidence-bounded challenger selection for an already-active MAIN.

No arbitrary percentage threshold is used. A challenger must pass its one-shot
sealed blind and be non-inferior to the current MAIN on comparable metrics, with
at least one strict improvement. The result only authorizes a CANARY request.
"""
from __future__ import annotations
import json, math, os, sys
from pathlib import Path
ROOT=Path(os.getenv("BBOT_LOCAL_ROOT",r"C:\my\bottest")); LAB=ROOT/"strategy_lab"
sys.path.insert(0,str(LAB))
import v3_research_knowledge as knowledge
import v3_strategy_research as single
import v3_combination_research as combo
import v3_baseline_research as baseline
import v3_hypothesis_factory as hypothesis_factory


def finite(x):
    try:
        y=float(x); return y if math.isfinite(y) else None
    except Exception:return None

def _key(tf): return f"challenger:{tf.upper()}:WAVE1"

def _metric(row,blind_kind):
    ev=row.get("evaluation") or {}
    wf=finite(ev.get("walk_forward_cost_stress_2x_avg_net_pct"))
    long4=finite(ev.get("long4_avg_net_pct"))
    hold=finite(ev.get("expected_holding_time_hours"))
    cte=(wf/hold) if wf is not None and hold not in (None,0) and hold>0 else None
    if blind_kind=="combo": b=row.get("sealed_blind_combo") or {}
    else: b=row.get("sealed_blind") or row.get("sealed_blind_single") or {}
    bs=finite(b.get("cost_stress_2x_avg_net_pct")); ba=finite(b.get("avg_net_pct"))
    return {"wf_stress":wf,"blind_stress":bs,"blind_avg":ba,"long4_avg":long4,"capital_time_efficiency":cte}

def _baseline_metric(row,blind_kind):
    ev=row.get("evaluation") or {}; wf=finite(ev.get("walk_forward_cost_stress_2x_avg_net_pct")); long4=finite(ev.get("long4_avg_net_pct")); hold=finite(ev.get("expected_holding_time_hours")); cte=(wf/hold) if wf is not None and hold not in (None,0) and hold>0 else None
    b=(row.get("sealed_blind_combo") if blind_kind=="combo" else row.get("sealed_blind_single")) or {}
    return {"wf_stress":wf,"blind_stress":finite(b.get("cost_stress_2x_avg_net_pct")),"blind_avg":finite(b.get("avg_net_pct")),"long4_avg":long4,"capital_time_efficiency":cte}

def _dominance(ch,base):
    # Only metrics available on BOTH sides enter dominance. Candidate WF/blind must be positive.
    if ch.get("wf_stress") is None or ch["wf_stress"]<=0:return False,["CHALLENGER_WF_STRESS_NONPOSITIVE"]
    if ch.get("blind_stress") is None or ch["blind_stress"]<=0:return False,["CHALLENGER_BLIND_STRESS_NONPOSITIVE"]
    reasons=[]; strict=False; eps=1e-12
    for name in ("wf_stress","blind_stress","long4_avg","capital_time_efficiency"):
        a=ch.get(name); b=base.get(name)
        if a is None or b is None: continue
        if a+eps < b: reasons.append(f"INFERIOR_{name.upper()}")
        if a > b+eps: strict=True
    if reasons:return False,reasons
    if not strict:return False,["NO_STRICT_IMPROVEMENT"]
    return True,[]

def _candidate_pool(tf):
    rows=[]
    s=single.provisional_choice(tf)
    if s: rows.append((s,"single"))
    # combination choice is valid only after bounded combo trials finish and combo blind passes.
    c=combo.provisional_choice(tf)
    if c: rows.append((c,"combo"))
    return rows

def research_complete(tf):
    tf=tf.upper(); files=single.family_files(); done=single.completed_pairs()
    if any((str(single.load_meta(p).get("name") or p.stem).upper(),tf) not in done for p in files):
        return False
    if not hypothesis_factory.all_complete(tf):
        return False
    if combo.hypotheses(tf):
        return False
    return True

def choice(tf="12H"):
    tf=tf.upper(); k,_=knowledge.ensure(); prior=(k.get("records") or {}).get(_key(tf))
    if isinstance(prior,dict):
        if prior.get("status")=="PASS":return prior.get("candidate")
        return None
    if not research_complete(tf):return None
    b=baseline.get(tf)
    if not b or b.get("status")!="MEASURED":return None
    evaluated=[]
    for row,kind in _candidate_pool(tf):
        cm=_metric(row,kind); bm=_baseline_metric(b,kind); ok,reasons=_dominance(cm,bm)
        evaluated.append({"candidate":row,"kind":kind,"candidate_metrics":cm,"baseline_metrics":bm,"dominates":ok,"reasons":reasons})
    if not evaluated:
        rec={"status":"FAIL","timeframe":tf,"reason":"NO_BLIND_PASS_CHALLENGER","baseline":b.get("strategy"),"evaluated":[]}
        knowledge.record_result(_key(tf),rec,failed=True); return None
    passes=[x for x in evaluated if x["dominates"]]
    if not passes:
        rec={"status":"FAIL","timeframe":tf,"reason":"NO_EVIDENCE_DOMINANT_CHALLENGER","baseline":b.get("strategy"),"evaluated":evaluated}
        knowledge.record_result(_key(tf),rec,failed=True); return None
    def rank(x):
        m=x["candidate_metrics"]; return (m.get("wf_stress") or -1e18,m.get("blind_stress") or -1e18,m.get("capital_time_efficiency") or -1e18)
    best=max(passes,key=rank); rec={"status":"PASS","timeframe":tf,"baseline":b.get("strategy"),"candidate":best["candidate"],"candidate_kind":best["kind"],"candidate_metrics":best["candidate_metrics"],"baseline_metrics":best["baseline_metrics"],"rule":"NONINFERIOR_COMPARABLE_METRICS_PLUS_AT_LEAST_ONE_STRICT_IMPROVEMENT","canary_only":True,"evaluated":evaluated}
    knowledge.record_result(_key(tf),rec,failed=False); return best["candidate"]

if __name__=="__main__":
    tf=(sys.argv[1] if len(sys.argv)>1 else "12H").upper(); print(json.dumps({"timeframe":tf,"choice":choice(tf)},ensure_ascii=False,indent=2,default=str))
