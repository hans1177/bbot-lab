# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_research_knowledge.py
from __future__ import annotations
import json, os, tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
RES=ROOT/"strategy_lab"/"results"/"v3"
KNOWLEDGE=RES/"research_knowledge.json"
TRIALS=RES/"trial_ledger.json"
RUNTIME_SUMMARY=RES/"research_knowledge_runtime.json"

def now(): return datetime.now(timezone.utc).isoformat()
def load(path,default):
    try: return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception: return default

def atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        deadline = time.monotonic() + 5.0
        delay = 0.05
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError as exc:
                if (os.name != "nt"
                    or getattr(exc, "winerror", None) not in (5, 32, 33)
                    or time.monotonic() >= deadline):
                    raise
                time.sleep(delay)
                delay = min(delay * 1.5, 0.25)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def ensure():
    k=load(KNOWLEDGE,{})
    if not isinstance(k,dict) or k.get("schema")!="B_BOT_V3_RESEARCH_KNOWLEDGE_V1":
        k={"schema":"B_BOT_V3_RESEARCH_KNOWLEDGE_V1","updated_at":now(),"records":{},"failed_research":[],"evidence_boundaries":{},"legacy":{"trial73":"COMPLETED_DO_NOT_RERUN"}}
        atomic(KNOWLEDGE,k)
    t=load(TRIALS,{})
    if not isinstance(t,dict) or t.get("schema")!="B_BOT_V3_TRIAL_LEDGER_V1":
        t={"schema":"B_BOT_V3_TRIAL_LEDGER_V1","counter":0,"legacy_counter_frozen_at":73,"entries":[],"updated_at":now()}; atomic(TRIALS,t)
    return k,t

def reserve_trial(
    research_id,
    candidate,
    tf,
    kind="SINGLE_FAMILY",
    rerun_reason=None,
    *,
    exact_strategy_version=None,
    source_file=None,
    source_sha256=None,
):
    # EXACT_COMPLETED_RERUN_GUARD_V1
    #
    # Canonical duplicate key:
    # candidate + timeframe + kind.
    #
    # RESERVED-only rows are retryable because no completed
    # research result was committed.
    #
    # COMPLETED + do_not_rerun=True rows are fail-closed unless
    # an explicitly allowed evidence-contract upgrade is supplied.
    _,t=ensure()

    candidate_key=str(candidate or "").strip()
    tf_key=str(tf or "").upper()
    kind_key=str(kind or "").upper()

    completed=[]

    for e in t.get("entries") or []:
        if not isinstance(e,dict):
            continue

        if e.get("event")!="COMPLETED":
            continue

        if e.get("do_not_rerun") is not True:
            continue

        if str(e.get("candidate") or "").strip()!=candidate_key:
            continue

        if str(e.get("timeframe") or "").upper()!=tf_key:
            continue

        if str(e.get("kind") or "").upper()!=kind_key:
            continue

        # A child is distinct only when BOTH its version and source changed.
        # Missing legacy identity remains fail-closed; renaming alone cannot
        # bypass the completed-trial guard.
        old_version=str(e.get("exact_strategy_version") or "").strip()
        old_hash=str(e.get("source_sha256") or "").lower()
        new_version=str(exact_strategy_version or "").strip()
        new_hash=str(source_sha256 or "").lower()
        if (old_version and old_hash and new_version and new_hash
            and old_version!=new_version and old_hash!=new_hash):
            continue

        completed.append(e)

    allowed_rerun_reasons={
        "EVIDENCE_CONTRACT_UPGRADE_SYMBOL_LEVEL_V1",
    }

    reason=str(rerun_reason or "").strip().upper()

    if completed and reason not in allowed_rerun_reasons:
        raise RuntimeError(
            "DUPLICATE_COMPLETED_TRIAL_DO_NOT_RERUN:"
            +candidate_key
            +":"
            +tf_key
            +":"
            +kind_key
        )

    t["counter"]=int(t.get("counter") or 0)+1
    n=t["counter"]

    row={
        "v3_trial":n,
        "legacy_trial_counter":73,
        "research_id":research_id,
        "candidate":candidate,
        "timeframe":tf,
        "kind":kind,
        "event":"RESERVED",
        "timestamp":now(),
        "do_not_rerun":True,
    }

    if completed:
        row["intentional_revalidation"]=True
        row["rerun_reason"]=reason
        row["rerun_of_trials"]=[
            int(e.get("v3_trial"))
            for e in completed
            if e.get("v3_trial") is not None
        ]

    if exact_strategy_version is not None:
        row["exact_strategy_version"]=str(exact_strategy_version).strip()
    if source_file is not None:
        row["source_file"]=str(source_file).replace("\\", "/")
    if source_sha256 is not None:
        row["source_sha256"]=str(source_sha256).lower()

    t.setdefault("entries",[]).append(row)

    # ?? 100,000 V3 trial event? ??.
    # counter? ?? ???? ???.
    if len(t["entries"])>100000:
        t["entries"]=t["entries"][-100000:]

    t["retention_limit_entries"]=100000
    t["updated_at"]=now()
    atomic(TRIALS,t)

    return n

def complete_trial(n,result,status,overfit_risk="LOW"):
    _,t=ensure()
    for row in reversed(t.get("entries",[])):
        if int(row.get("v3_trial") or -1)==int(n):
            row.update({"event":"COMPLETED","status":status,"result":str(result),"overfit_risk":overfit_risk,"completed_at":now(),"do_not_rerun":True}); break
    t["updated_at"]=now(); atomic(TRIALS,t)

def record_result(key,row,failed=False):
    k,_=ensure(); row=dict(row); row["updated_at"]=now()
    if failed:
        k.setdefault("failed_research",[]).append(row); k["failed_research"]=k["failed_research"][-1000:]
    else:
        k.setdefault("records",{})[key]=row
    k["updated_at"]=now(); atomic(KNOWLEDGE,k); write_runtime_summary(k)

def set_boundary(dataset_id,status):
    k,_=ensure(); boundaries=k.setdefault("evidence_boundaries",{}); current=boundaries.get(dataset_id) if isinstance(boundaries.get(dataset_id),dict) else {}
    def rank(value):
        x=str(value or "").upper()
        if x.startswith("REUSED"): return 4
        if x.startswith("SEEN"): return 3
        if x.startswith("SEALED"): return 2
        if x.startswith("UNSEEN"): return 1
        return 0
    if rank(status) < rank(current.get("status")):
        raise RuntimeError("EVIDENCE_STATE_REGRESSION_FORBIDDEN")
    boundaries[dataset_id]={"status":status,"updated_at":now(),"previous_status":current.get("status")}; k["updated_at"]=now(); atomic(KNOWLEDGE,k)

def write_runtime_summary(k=None):
    """작은 runtime summary. Strategy|TF|Symbol|StrategyVersion을 canonical key로 사용한다."""
    k=k if isinstance(k,dict) else load(KNOWLEDGE,{})
    records={}; aliases={}
    rank={"LIVE":5,"NORMAL":5,"SMALL":4,"CANARY":3,"SEALED_BLIND_PASS":2,"BLIND":2,"INITIAL_BACKTEST_ONLY":1,"MEASURED":1}
    for key,row in (k.get("records") or {}).items():
        if not isinstance(row,dict): continue
        strategy=str(row.get("strategy") or "").strip().upper(); tf=str(row.get("timeframe") or "").strip().upper(); symbol=str(row.get("symbol") or "*").strip().upper() or "*"; version=str(row.get("strategy_version") or "").strip().upper()
        if not strategy or not tf or not version: continue
        runtime_key=f"{strategy}|{tf}|{symbol}|{version}"
        current=records.get(runtime_key) or {}
        candidate={x:row.get(x) for x in ("expected_return","expected_loss","expected_holding_time_hours","capital_time_efficiency","expected_account_contribution","confidence","confidence_level","evidence_summary","status","strategy_version","expectation_basis","policy_versions") if x in row}
        old_rank=rank.get(str(current.get("confidence_level") or current.get("status") or "").upper(),0)
        new_rank=rank.get(str(candidate.get("confidence_level") or candidate.get("status") or "").upper(),0)
        if not current or new_rank>=old_rank: records[runtime_key]=candidate
        aliases[f"{strategy}|{tf}|{symbol}"]=runtime_key
    atomic(RUNTIME_SUMMARY,{"schema":"B_BOT_V3_RUNTIME_KNOWLEDGE_V2","updated_at":now(),"key_contract":"STRATEGY|TF|SYMBOL|STRATEGY_VERSION","records":records,"legacy_aliases":aliases})

