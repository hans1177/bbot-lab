#!/usr/bin/env python3
"""Read-only, snapshot-pinned audit of a public B-Bot research checkpoint.

Only two canonical JSON members are fetched, verified against the checkpoint
receipt, and aggregated. Does not run strategy code, alter state, or promote.
"""
import argparse
import ast
import base64
import hashlib
import io
import json
import math
import os
import re
import urllib.request
import zipfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

CHUNK=8*1024*1024
STAGE2="strategy_lab/results/v3/historical_314_pipeline_stage2.json"
STAGE3="strategy_lab/results/v3/individual_trailing_stage3.json"
KNOWN_REASONS={
 "TRAILING_RUN_TIMEOUT","RUN_STRATEGY_FAIL","RESULT_NOT_FOUND",
 "TRAILING_CANONICAL_SOURCE_UNAVAILABLE","TRAILING_EXACT_SOURCE_BINDING_MISSING",
 "TRAILING_EXACT_SOURCE_HASH_MISMATCH",
 "TRAILING_RESULT_INVALID","TRAILING_RESULT_SOURCE_MISSING",
 "TRAILING_RESULT_SOURCE_MISMATCH","TRAILING_RESULT_CONFIG_MISSING",
 "TRAILING_RESULT_CONFIG_INVALID","TRAILING_RESULT_CONFIG_MISMATCH",
 "TRAILING_FROZEN_RESULT_STRUCTURE_INVALID","TRAILING_FROZEN_PAIR_INVALID",
 "TRAILING_FROZEN_MEASUREMENT_MISSING","TRAILING_FROZEN_PAIR_MISMATCH",
 "TRAILING_FROZEN_RESULT_PATH_INVALID","TRAILING_FROZEN_RESULT_MISSING",
 "TRAILING_FROZEN_RESULT_TOO_LARGE","TRAILING_FROZEN_RESULT_UNREADABLE",
 "TRAILING_FROZEN_EVIDENCE_MISMATCH","TRAILING_FROZEN_RESEARCH_WINDOW_INVALID",
 "BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID",
 "TRAILING_SELECTION_INCOMPLETE","NO_ROBUST_MULTI_METRIC_IMPROVEMENT",
 "TRAILING_FROZEN_TRAILING_RESULT_CONFIG_MISSING",
}
KNOWN_ITEM_STATUSES={"COARSE_PENDING","COARSE_RUNNING","FINE_PENDING","FINE_RUNNING",
 "INCOMPLETE","COMPLETE","SELECTED","UNKNOWN"}
KNOWN_PAIR_STATUSES={"MEASURED","RUNTIME_FAIL","RESULT_NOT_FOUND","INVALID_RESULT_BINDING"}
FINAL_RISK_REASON_CODES={
 "FINAL_RISK_HARD_PASS_BASELINE_MISSING",
 "FINAL_RISK_WF_OOS_NONPOSITIVE",
 "FINAL_RISK_COST_STRESS_2X_NONPOSITIVE",
 "FINAL_RISK_MDD_MISSING","FINAL_RISK_MDD_ABSOLUTE_FAIL",
 "FINAL_RISK_DD_DURATION_MISSING","FINAL_RISK_DD_DURATION_ABSOLUTE_FAIL",
 "FINAL_RISK_CVAR95_MISSING","FINAL_RISK_CVAR95_ABSOLUTE_FAIL",
 "FINAL_RISK_HOLDING_MISSING","FINAL_RISK_HOLDING_ABSOLUTE_FAIL",
 "FINAL_RISK_LOSS_STREAK_ABSOLUTE_FAIL",
 "FINAL_RISK_LONG_SHORT_BOTH_NONPOSITIVE",
 "FINAL_RISK_LONG_COLLAPSE","FINAL_RISK_SHORT_COLLAPSE",
}
KNOWN_STAGES={"HARD_PASS_RECHECK_PENDING","HARD_PASS_RECHECK_FAIL",
 "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING","INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE",
 "REENTRY_RESEARCH_PENDING","REENTRY_RESEARCH_INCOMPLETE",
 "FINAL_RISK_REVALIDATION_PENDING","FINAL_RISK_REDEVELOPMENT_PENDING",
 "FULL_VALIDATION_PENDING","FULL_VALIDATION_REDEVELOPMENT_PENDING",
 "SEALED_FINAL_BLIND_PENDING","SEALED_FINAL_BLIND_EVALUATION_PENDING",
 "SEALED_FINAL_BLIND_ADDITIONAL_WINDOW_PENDING","LOCAL_SHADOW_PENDING",
 "REDEVELOPMENT_SUPERSEDED_BY_NEW_VERSION","PERSISTENT_FAIL"}

def _label(value, known):
    return value if isinstance(value,str) and value in known else "OTHER_OR_UNRECORDED"

MIN_TRADES={"30M":40,"1H":30,"6H":20,"12H":15,"1D":10,"1W":6}

def _metric(evidence,name):
    try:
        n=float(evidence.get(name))
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):
        return None

def _eligible_measurement(row,min_trades):
    if not isinstance(row,dict) or row.get("status")!="MEASURED":
        return False
    ev=row.get("evidence") or {}
    return (int(ev.get("trades") or 0)>=min_trades
            and (_metric(ev,"walk_forward_oos_expectancy_pct") or 0)>0
            and (_metric(ev,"cost_stress_2x_expectancy_pct") or 0)>0)

def _load_pure_trailing_selector(constitution_file=None):
    """Compile only the existing side-effect-free selector functions.

    No imports from the research controller, no order/runtime modules, and
    no strategy scripts are executed.
    """
    root=Path(__file__).resolve().parents[1]
    source=root/"strategy_lab/v3_policy_research.py"
    contract_file=constitution_file or root/"ROADMAP_CONSTITUTION_V3.json"
    contract=(json.loads(Path(contract_file).read_text(encoding="utf-8-sig"))
              .get("individual_strategy_trailing_optimization") or {}
             ).get("trailing_search_contract") or {}
    names={"_metric","_measured_trailing_rows","_trailing_row_eligible",
           "_trailing_quality_tuple","_individual_trailing_select"}
    tree=ast.parse(source.read_text(encoding="utf-8-sig"))
    funcs=[node for node in tree.body if isinstance(node,ast.FunctionDef)
           and node.name in names]
    if {node.name for node in funcs}!=names:
        raise ValueError("STRICT_POLICY_SELECTION_FUNCTIONS_MISSING")
    namespace={"Any":Any,"_individual_trailing_contract":lambda:contract}
    exec(compile(ast.Module(body=funcs,type_ignores=[]),str(source),"exec"),namespace)
    return namespace["_individual_trailing_select"]


def summarize(stage2,stage3,selection_preview=None):
    targets=stage2.get("entries") if isinstance(stage2,dict) else None
    items=stage3.get("entries") if isinstance(stage3,dict) else None
    if not isinstance(targets,list) or not isinstance(items,dict):
        raise ValueError("RESEARCH_STAGE_STRUCTURES_INVALID")
    stages=Counter(); reasons=Counter(); item_states=Counter(); pair_states=Counter()
    waiting=Counter(); incomplete=Counter()
    baseline_failures=Counter();alternative_eligibility=Counter()
    strict_outcomes=Counter();reassessed_stages=Counter()
    risk_gate_outcomes=Counter();risk_gate_failures=Counter()
    full_gate_outcomes=Counter();blind_gate_outcomes=Counter()
    for target in targets:
        if not isinstance(target,dict) or target.get("eligible") is not True:
            continue
        stage=_label(target.get("pipeline_stage"),KNOWN_STAGES)
        stages[stage]+=1
        if target.get("trailing_selection_reassessment_contract")=="MEASURED_BASELINE_ROBUST_ALTERNATIVE_V1":
            reassessed_stages[stage]+=1
        for field,counter in (("final_risk_gate",risk_gate_outcomes),
                              ("full_validation_gate",full_gate_outcomes),
                              ("sealed_final_blind_gate",blind_gate_outcomes)):
            gate=target.get(field)
            if isinstance(gate,dict) and gate:
                counter["PASS" if gate.get("pass") is True else "FAIL_OR_INCOMPLETE"]+=1
                if field=="final_risk_gate" and gate.get("pass") is not True:
                    raw=gate.get("absolute_fail_reasons") or []
                    if isinstance(raw,list):
                        for reason in set(v for v in raw if isinstance(v,str)):
                            risk_gate_failures[_label(reason,FINAL_RISK_REASON_CODES)]+=1
        if stage not in ("INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",
                         "INDIVIDUAL_TRAILING_RESEARCH_INCOMPLETE"):
            continue
        item=items.get(str(target.get("canonical_identity_key") or ""))
        item=item if isinstance(item,dict) else {}
        status=_label(item.get("status"),KNOWN_ITEM_STATUSES)
        item_states[f"{stage}:{status}"]+=1
        if not item:
            (incomplete if stage.endswith("INCOMPLETE") else waiting)["NO_STAGE3_ENTRY"]+=1
        selection=item.get("selection"); selection=selection if isinstance(selection,dict) else {}
        cause=(target.get("last_error") or item.get("runtime_error")
               or item.get("source_integrity_error") or selection.get("reason"))
        if stage.endswith("INCOMPLETE"):
            reasons[_label(cause,KNOWN_REASONS)]+=1
            coarse=item.get("coarse_results") or {}
            fine=item.get("fine_results") or {}
            measured={}
            if isinstance(coarse,dict):measured.update(coarse)
            if isinstance(fine,dict):measured.update(fine)
            baseline=measured.get("3.0000|1.5000")
            min_trades=MIN_TRADES.get(str(target.get("timeframe") or "").upper(),10**9)
            if not isinstance(baseline,dict) or baseline.get("status")!="MEASURED":
                baseline_failures["BASELINE_NOT_MEASURED"]+=1
            else:
                ev=baseline.get("evidence") or {}
                try:trades=int(ev.get("trades") or 0)
                except (ValueError,TypeError,OverflowError):trades=0
                if trades<min_trades:baseline_failures["BASELINE_TRADE_COUNT_LOW"]+=1
                wf=_metric(ev,"walk_forward_oos_expectancy_pct")
                stress=_metric(ev,"cost_stress_2x_expectancy_pct")
                if wf is None:baseline_failures["BASELINE_WF_MISSING"]+=1
                elif wf<=0:baseline_failures["BASELINE_WF_NONPOSITIVE"]+=1
                if stress is None:baseline_failures["BASELINE_STRESS_MISSING"]+=1
                elif stress<=0:baseline_failures["BASELINE_STRESS_NONPOSITIVE"]+=1
            eligible_alts=sum(
                _eligible_measurement(row,min_trades)
                for key,row in measured.items() if key!="3.0000|1.5000"
            )
            alternative_eligibility[
                "AT_LEAST_ONE_MEASURABLE_ELIGIBLE_ALTERNATIVE" if eligible_alts else
                "NO_ELIGIBLE_ALTERNATIVE"
            ]+=1
            if selection_preview is not None:
                if not isinstance(coarse,dict) or not isinstance(fine,dict):
                    strict_outcomes["INVALID_FROZEN_GRID"]+=1
                else:
                    exact_rows={**coarse,**fine}
                    measured_count=sum(
                        isinstance(row,dict) and row.get("status")=="MEASURED"
                        for row in exact_rows.values()
                    )
                    if measured_count!=len(exact_rows):
                        strict_outcomes["FROZEN_GRID_NOT_FULLY_MEASURED"]+=1
                    else:
                        decision=selection_preview({
                            "timeframe":str(target.get("timeframe") or ""),
                            "minimum_trades":min_trades,
                            "evaluations":exact_rows,
                        })
                        verdict=str(decision.get("status") or "UNKNOWN")
                        reason=str(decision.get("reason") or "")
                        if verdict=="SELECTED" and reason=="ROBUST_NEIGHBORHOOD_MULTI_METRIC_IMPROVEMENT":
                            strict_outcomes["ROBUST_ALTERNATIVE_PREVIEW_ONLY"]+=1
                        elif verdict=="FALLBACK":
                            strict_outcomes["BASELINE_FALLBACK_PREVIEW_ONLY"]+=1
                        elif verdict=="INCOMPLETE":
                            strict_outcomes["INCOMPLETE_"+(
                                "MISSING_CONTROL" if reason=="BASELINE_3_0_1_5_NOT_MEASURED_OR_INVALID"
                                else "NO_ROBUST_ALTERNATIVE"
                            )]+=1
                        else:
                            strict_outcomes["OTHER_POLICY_DECISION"]+=1
        for phase in ("coarse","fine"):
            planned=item.get(phase+"_candidates") or []
            done=item.get(phase+"_results") or {}
            if not isinstance(planned,list) or not isinstance(done,dict):
                pair_states[phase+":INVALID_STRUCTURE"]+=1
                continue
            pair_states[phase+":PLANNED"]+=len(planned)
            pair_states[phase+":RECORDED"]+=len(done)
            for row in done.values():
                pair_states[phase+":"+_label(row.get("status") if isinstance(row,dict) else None,
                                             KNOWN_PAIR_STATUSES)]+=1
    return {
      "schema":"B_BOT_PUBLIC_CHECKPOINT_READ_ONLY_AUDIT_V1",
      "stage_counts":dict(sorted(stages.items())),
      "trailing_incomplete_reasons":dict(sorted(reasons.items())),
      "baseline_failure_checks":dict(sorted(baseline_failures.items())),
      "eligible_alternative_counts":dict(sorted(alternative_eligibility.items())),
      "strict_selector_preview_not_certification":dict(sorted(strict_outcomes.items())),
      "one_time_reassessed_stages":dict(sorted(reassessed_stages.items())),
      "final_risk_gate_outcomes":dict(sorted(risk_gate_outcomes.items())),
      "final_risk_absolute_failure_counts":dict(sorted(risk_gate_failures.items())),
      "full_validation_gate_outcomes":dict(sorted(full_gate_outcomes.items())),
      "blind_gate_outcomes_without_sealed_details":dict(sorted(blind_gate_outcomes.items())),
      "trailing_item_statuses":dict(sorted(item_states.items())),
      "trailing_pair_work":dict(sorted(pair_states.items())),
      "missing_entries":dict(sorted((waiting+incomplete).items())),
      "automatic_registration_authorized":False,
      "blind_evidence_disclosed":False,
    }

class ChunkZip(io.RawIOBase):
    """Lazy random-access reader; every requested blob is SHA256 verified."""
    def __init__(self, parts, total, fetch):
        self.parts=parts;self.total=total;self.fetch=fetch;self.pos=0
        self.cache=OrderedDict()
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        if whence==0:p=offset
        elif whence==1:p=self.pos+offset
        elif whence==2:p=self.total+offset
        else:raise ValueError("BAD_SEEK_MODE")
        if p<0:raise ValueError("NEGATIVE_SEEK")
        self.pos=p
        return p
    def _chunk(self,index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        row=self.parts[index]
        raw=self.fetch(row["name"])
        if len(raw)!=row["bytes"] or hashlib.sha256(raw).hexdigest()!=row["sha256"]:
            raise ValueError("AUDIT_PART_HASH_MISMATCH")
        self.cache[index]=raw
        if len(self.cache)>8:self.cache.popitem(last=False)
        return raw
    def read(self,size=-1):
        if self.pos>=self.total:return b""
        if size is None or size<0:size=self.total-self.pos
        remaining=min(size,self.total-self.pos)
        out=[]
        while remaining:
            index=self.pos//CHUNK;offset=self.pos%CHUNK
            part=self._chunk(index)
            take=min(remaining,len(part)-offset)
            if take<=0:raise ValueError("INVALID_CHUNK_OFFSET")
            out.append(part[offset:offset+take])
            self.pos+=take;remaining-=take
        return b"".join(out)

def audit(api):
    head=api("/git/ref/heads/research-state")["object"]["sha"]
    commit=api("/git/commits/"+head)
    rows=api("/git/trees/"+commit["tree"]["sha"])["tree"]
    entries={row["path"]:row for row in rows}
    def blob(name):
        row=entries.get(name)
        if not isinstance(row,dict) or row.get("type")!="blob":
            raise ValueError("AUDIT_BLOB_MISSING")
        body=api("/git/blobs/"+row["sha"])
        if body.get("encoding")!="base64":raise ValueError("AUDIT_BLOB_ENCODING")
        raw=base64.b64decode(body["content"].replace("\n",""),validate=True)
        if len(raw)!=row["size"]:raise ValueError("AUDIT_BLOB_SIZE")
        return raw
    receipt=json.loads(blob("CHECKPOINT.json"))
    manifest=json.loads(blob("checkpoint.parts.json"))
    parts=manifest.get("parts")
    if (receipt.get("schema")!="B_BOT_RESEARCH_CHECKPOINT_V1"
        or manifest.get("schema")!="B_BOT_RESEARCH_CHECKPOINT_PARTS_V1"
        or manifest.get("chunk_bytes")!=CHUNK
        or receipt.get("archive_sha256")!=manifest.get("archive_sha256")
        or not isinstance(parts,list) or not 1<=len(parts)<=64):
        raise ValueError("AUDIT_MANIFEST_INVALID")
    total=0;expected={"CHECKPOINT.json","checkpoint.parts.json"}
    for i,row in enumerate(parts):
        name=f"checkpoint.part{i:04d}"
        if (not isinstance(row,dict) or set(row)!={"name","bytes","sha256"}
            or row.get("name")!=name or not isinstance(row.get("bytes"),int)
            or not 0<row["bytes"]<=CHUNK
            or (i<len(parts)-1 and row["bytes"]!=CHUNK)
            or not isinstance(row.get("sha256"),str)
            or re.fullmatch(r"[0-9a-f]{64}",row["sha256"]) is None):
            raise ValueError("AUDIT_PART_DESCRIPTOR_INVALID")
        total+=row["bytes"];expected.add(name)
    if (set(entries)!=expected or total!=manifest.get("archive_bytes")
        or not 0<total<=512*1024*1024):
        raise ValueError("AUDIT_PART_SET_INVALID")
    archive=ChunkZip(parts,total,blob)
    with zipfile.ZipFile(archive) as z:
        decoded=[]
        for name in (STAGE2,STAGE3):
            entry=(receipt.get("files") or {}).get(name)
            if not isinstance(entry,dict) or z.getinfo(name).file_size>32*1024*1024:
                raise ValueError("AUDIT_RESEARCH_FILE_INVALID")
            raw=z.read(name)
            if len(raw)!=entry.get("bytes") or hashlib.sha256(raw).hexdigest()!=entry.get("sha256"):
                raise ValueError("AUDIT_STAGE_MEMBER_HASH")
            decoded.append(json.loads(raw.decode("utf-8-sig")))
    contract_path=Path(__file__).resolve().parents[1]/"ROADMAP_CONSTITUTION_V3.json"
    expected_constitution=receipt.get("constitution_sha256")
    if (not isinstance(expected_constitution,str)
        or hashlib.sha256(contract_path.read_bytes()).hexdigest()!=expected_constitution):
        raise ValueError("AUDIT_EXACT_CONSTITUTION_MISMATCH")
    result=summarize(*decoded,selection_preview=_load_pure_trailing_selector(contract_path))
    result["verified_archive_members"]=[STAGE2,STAGE3]
    result["checkpoint_commit"]=head
    result["checkpoint_run_id"]=str(receipt.get("source_run_id") or "")
    result["source_commit"]=str(receipt.get("source_commit") or "")
    result["read_only"]=True
    result["whole_archive_sha256_verified"]=False
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo",default=os.environ.get("GITHUB_REPOSITORY",""))
    args=parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",args.repo) is None:
        raise SystemExit("INVALID_REPOSITORY")
    token=os.environ.get("GH_TOKEN")
    if not token:raise SystemExit("GITHUB_TOKEN_REQUIRED")
    prefix="https://api.github.com/repos/"+args.repo
    def get(path):
        req=urllib.request.Request(prefix+path,headers={
            "Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2022-11-28"})
        with urllib.request.urlopen(req,timeout=90) as response:
            return json.load(response)
    print(json.dumps(audit(get),sort_keys=True),flush=True)

if __name__=="__main__":main()
