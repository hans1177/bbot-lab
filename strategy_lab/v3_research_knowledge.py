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
        rows=k.setdefault("failed_research",[])
        row["record_key"]=key
        if row.get("trial") is not None:
            rows[:]=[old for old in rows if not (old.get("record_key")==key and old.get("trial")==row["trial"])]
        rows.append(row); k["failed_research"]=rows[-1000:]
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



# ============================================================
# 깃허브 연구 체크포인트: 단일 기록 담당이 계산 종료 후 저장
# ============================================================

def _p0_checkpoint_gate_snapshot(gate):
    """Read-only gate diagnostics; neither a failure flag nor a PASS is inferred."""
    if not isinstance(gate,dict):
        return {"evaluated":False,"pass":False,"decision":"NOT_EVALUATED",
                "pass_mode":"NONE","reasons":[],"compensation_flags":[],
                "compensation_strengths":[]}
    reasons=gate.get("absolute_fail_reasons") or gate.get("reasons") or []
    if not isinstance(reasons,list):reasons=[]
    boundary=gate.get("compensation_flags") or []
    strengths=gate.get("compensation_strengths") or []
    if not isinstance(boundary,list):boundary=[]
    if not isinstance(strengths,list):strengths=[]
    return {
        "evaluated":bool(gate),
        "pass":gate.get("pass") is True,
        "decision":str(gate.get("decision") or "NOT_EVALUATED")[:80],
        "pass_mode":str(gate.get("pass_mode") or "NONE")[:80],
        "reasons":[str(reason)[:120] for reason in reasons[:8]],
        "compensation_flags":[str(flag)[:120] for flag in boundary[:8]],
        "compensation_strengths":[str(flag)[:120] for flag in strengths[:8]],
    }


def _p0_checkpoint_stage_snapshot(pipeline, pipeline_sha256):
    """Read-only P0 progress from the exact stage2 bytes archived in this checkpoint.

    Follow only authenticated parent/child exact-version links. A superseded
    root must not conceal the current descendant's failure diagnostics.
    This summary never changes research evidence or authorizes server release.
    """
    from collections import Counter
    baseline={
        "schema":"B_BOT_P0_CHECKPOINT_STAGE_SNAPSHOT_V1",
        "pipeline_sha256":pipeline_sha256,
        "aws_final_certification":"NOT_VERIFIED_BY_GITHUB_RESEARCH",
        "automatic_registration_authorized":False,
        "leading_exact_version":"EV_55a86e97dfadf81c_30M",
        "research_full_and_sealed_blind_pass_count":0,
    }
    entries=pipeline.get("entries") if isinstance(pipeline,dict) else None
    if not isinstance(entries,list):
        return dict(baseline,status="PIPELINE_EVIDENCE_INVALID",stage_counts={},
                    leading=None,leading_lineage=[],leading_current=None)
    valid=[row for row in entries if isinstance(row,dict) and row.get("eligible") is True]
    counts=Counter(str(row.get("pipeline_stage") or "UNKNOWN") for row in valid)
    complete=sum(
        (row.get("full_validation_gate") or {}).get("pass") is True
        and (row.get("sealed_final_blind_gate") or {}).get("pass") is True
        and (row.get("sealed_final_blind_gate") or {}).get("next_stage")=="LOCAL_SHADOW_PENDING"
        and row.get("pipeline_stage")=="LOCAL_SHADOW_PENDING"
        for row in valid if isinstance(row.get("full_validation_gate"),dict)
        and isinstance(row.get("sealed_final_blind_gate"),dict)
    )
    target=next((row for row in valid
                 if str(row.get("exact_strategy_version") or "")==baseline["leading_exact_version"]),None)
    lineage=[]
    current=target
    visited=set()
    while isinstance(current,dict) and len(lineage)<12:
        version=str(current.get("exact_strategy_version") or "")
        if not version or version in visited:
            break
        if (current.get("strategy_id")!=target.get("strategy_id")
            or current.get("timeframe")!=target.get("timeframe")):
            break
        visited.add(version)
        lineage.append({
            "strategy_id":str(current.get("strategy_id") or "")[:120],
            "exact_strategy_version":version[:120],
            "parent_exact_strategy_version":str(current.get("parent_exact_strategy_version") or "")[:120],
            "timeframe":str(current.get("timeframe") or "")[:16],
            "pipeline_stage":str(current.get("pipeline_stage") or "UNKNOWN")[:100],
            "last_error":str(current.get("last_error") or "")[:180],
            "redevelopment_gate":str(current.get("redevelopment_gate") or "")[:80],
            "final_risk_gate":_p0_checkpoint_gate_snapshot(current.get("final_risk_gate")),
            "full_validation_gate":_p0_checkpoint_gate_snapshot(current.get("full_validation_gate")),
            "sealed_final_blind_gate":_p0_checkpoint_gate_snapshot(current.get("sealed_final_blind_gate")),
            "redevelopment_cycles_by_gate":{
                str(k)[:80]:int(v) for k,v in (current.get("redevelopment_cycles_by_gate") or {}).items()
                if isinstance(v,int) and not isinstance(v,bool)
            },
        })
        child_version=str(current.get("redevelopment_child_exact_version") or "")
        if not child_version or child_version in visited:
            break
        child=next((row for row in valid
                    if str(row.get("exact_strategy_version") or "")==child_version
                    and str(row.get("parent_exact_strategy_version") or "")==version
                    and row.get("strategy_id")==target.get("strategy_id")
                    and row.get("timeframe")==target.get("timeframe")),None)
        if child is None:
            break
        current=child
    return dict(baseline,status="RESEARCH_STAGE_ONLY",stage_counts=dict(sorted(counts.items())),
                leading=lineage[0] if lineage else None,
                leading_lineage=lineage,
                leading_current=lineage[-1] if lineage else None,
                research_full_and_sealed_blind_pass_count=complete)


def save_cloud_checkpoint():
    import sys
    import hashlib,json,os,zipfile
    from pathlib import Path
    root=Path(os.environ['BBOT_LOCAL_ROOT']);out=Path(os.environ['RUNNER_TEMP'])/'research-checkpoint-out';out.mkdir(exist_ok=True)
    state=root/'strategy_lab/results'
    files=[path for path in state.rglob('*') if path.is_file() and path.suffix.lower() in {'.json','.jsonl','.py','.csv','.parquet'} and '__pycache__' not in path.parts]
    if not files:raise RuntimeError('RESEARCH_STATE_NOT_CREATED')
    receipt={'schema':'B_BOT_RESEARCH_CHECKPOINT_V1','release_contract':'FINAL_GATES_THEN_EXISTING_RUNTIME_WORKER_V1','source_run_id':os.environ['GITHUB_RUN_ID'],'source_commit':os.environ['GITHUB_SHA'],'engine_sha256':hashlib.sha256((root/'strategy_lab/run_strategy.py').read_bytes()).hexdigest(),'input_archives':json.loads((root/'INPUT_RECEIPT.json').read_text()),'constitution_sha256':hashlib.sha256((root/'ROADMAP_CONSTITUTION_V3.json').read_bytes()).hexdigest(),'files':{}}
    with zipfile.ZipFile(out/'checkpoint.zip','w',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for path in sorted(files):
            if path.is_symlink() or not path.resolve().is_relative_to(state.resolve()):raise RuntimeError('STATE_PATH_FORBIDDEN')
            if path.stat().st_size>512*1024**2:raise RuntimeError('STATE_FILE_TOO_LARGE')
            raw=path.read_bytes();name=path.relative_to(root).as_posix()
            if path.suffix=='.json':json.loads(raw.decode('utf-8-sig'))
            archive.writestr(name,raw);receipt['files'][name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            if name=='strategy_lab/results/v3/historical_314_pipeline_stage2.json':
                receipt['p0_stage_status']=_p0_checkpoint_stage_snapshot(
                    json.loads(raw.decode('utf-8-sig')),receipt['files'][name]['sha256'])
    receipt.setdefault('p0_stage_status',{
        'schema':'B_BOT_P0_CHECKPOINT_STAGE_SNAPSHOT_V1',
        'status':'NO_PIPELINE_EVIDENCE',
        'pipeline_sha256':None,
        'aws_final_certification':'NOT_VERIFIED_BY_GITHUB_RESEARCH',
        'automatic_registration_authorized':False,
        'research_full_and_sealed_blind_pass_count':0,
        'leading_exact_version':'EV_55a86e97dfadf81c_30M',
        'stage_counts':{},'leading':None,
    })
    receipt['archive_sha256']=hashlib.sha256((out/'checkpoint.zip').read_bytes()).hexdigest()
    (out/'CHECKPOINT.json').write_text(json.dumps(receipt,indent=2))
    print('RESEARCH_CHECKPOINT_SAVED',len(files),file=sys.stderr)
    return receipt


def publish_cloud_checkpoint(include_candidates=False):
    import sys
    import base64,hashlib,json,os,urllib.request,urllib.error
    from pathlib import Path
    api='https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']
    def request(path,data=None,method=None):
        body=None if data is None else json.dumps(data).encode()
        req=urllib.request.Request(api+path,data=body,method=method,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=90) as response:return json.load(response)
    def checkpoint_git_objects(out):
        """Transport unchanged checkpoint bytes in bounded Git blobs.

        A separate transport manifest binds exact ordered chunks to the V1
        archive SHA256; no research result or state path is removed/rewritten.
        """
        archive=out/'checkpoint.zip'
        receipt_path=out/'CHECKPOINT.json'
        if not archive.is_file() or not receipt_path.is_file():
            raise RuntimeError('CHECKPOINT_TRANSPORT_SOURCE_MISSING')
        receipt_raw=receipt_path.read_bytes()
        receipt=json.loads(receipt_raw)
        if receipt.get('schema')!='B_BOT_RESEARCH_CHECKPOINT_V1':
            raise RuntimeError('CHECKPOINT_TRANSPORT_RECEIPT_INVALID')
        chunk_bytes=8*1024**2
        sha=hashlib.sha256()
        parts=[]
        total=0
        with archive.open('rb') as source:
            while True:
                raw=source.read(chunk_bytes)
                if not raw:break
                if len(parts)>=64:raise RuntimeError('CHECKPOINT_TRANSPORT_ARCHIVE_LIMIT')
                index=len(parts)
                name=f'checkpoint.part{index:04d}'
                parts.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
                total+=len(raw)
                sha.update(raw)
        if not parts or sha.hexdigest()!=receipt.get('archive_sha256'):
            raise RuntimeError('CHECKPOINT_TRANSPORT_ARCHIVE_HASH')
        manifest={
            'schema':'B_BOT_RESEARCH_CHECKPOINT_PARTS_V1',
            'archive_sha256':sha.hexdigest(),
            'archive_bytes':total,
            'chunk_bytes':chunk_bytes,
            'parts':parts,
        }
        yield 'CHECKPOINT.json',receipt_raw
        yield 'checkpoint.parts.json',json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        with archive.open('rb') as source:
            for row in parts:
                raw=source.read(chunk_bytes)
                if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                    raise RuntimeError('CHECKPOINT_TRANSPORT_SOURCE_CHANGED')
                yield row['name'],raw
            if source.read(1):raise RuntimeError('CHECKPOINT_TRANSPORT_SOURCE_CHANGED')
    for branch,folder in [('research-state','research-checkpoint-out')]+([('runtime-candidates','runtime-candidates-out')] if include_candidates else []):
        out=Path(os.environ['RUNNER_TEMP'])/folder
        if not out.exists():continue
        if branch=='runtime-candidates' and not (out/'MANIFEST.json').is_file():continue
        try:parent=request('/git/ref/heads/'+branch)['object']['sha']
        except urllib.error.HTTPError as error:
            if error.code!=404:raise
            parent=None
        lease=Path(os.environ['RUNNER_TEMP'])/'research-state-head.json'
        if branch=='research-state':
            if not lease.is_file():raise RuntimeError('CHECKPOINT_RESTORE_LEASE_MISSING')
            expected=json.loads(lease.read_text())['head']
            if parent!=expected:raise RuntimeError('CHECKPOINT_REMOTE_STATE_CHANGED')
        elements=[]
        if branch=='research-state':
            upload=checkpoint_git_objects(out)
        else:
            upload=((path.relative_to(out).as_posix(),path.read_bytes()) for path in sorted(out.rglob('*')) if path.is_file())
        for name,raw in upload:
            if len(raw)>90*1024**2:raise RuntimeError('CHECKPOINT_GIT_BLOB_LIMIT')
            try:
                blob=request('/git/blobs',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})
            except urllib.error.HTTPError as error:
                # Never log raw response bodies, token headers, or checkpoint data.
                raise RuntimeError('CHECKPOINT_GIT_BLOB_REJECTED:'+str(error.code)+':'+name+':bytes='+str(len(raw))) from error
            elements.append({'path':name,'mode':'100644','type':'blob','sha':blob['sha']})
        tree=request('/git/trees',{'tree':elements})
        commit=request('/git/commits',{'message':branch+' run '+os.environ['GITHUB_RUN_ID']+' attempt '+os.environ['GITHUB_RUN_ATTEMPT'],'tree':tree['sha'],'parents':[parent] if parent else []})
        if parent:request('/git/refs/heads/'+branch,{'sha':commit['sha'],'force':False},'PATCH')
        else:request('/git/refs',{'ref':'refs/heads/'+branch,'sha':commit['sha']})
        if request('/git/ref/heads/'+branch)['object']['sha']!=commit['sha']:raise RuntimeError('CHECKPOINT_REF_VERIFY')
        if branch=='research-state':atomic(lease,{'head':commit['sha']})
        print('PRIVATE_GIT_HANDOFF_SAVED_AND_VERIFIED',branch,commit['sha'],file=sys.stderr)
