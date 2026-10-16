#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: capital_allocator.py
"""V3 자본배분.

order.py의 검증된 주문 계산기는 변경하지 않는다. order.py가 active symbol 수로
기본 분할하는 값을, 이 모듈이 Research Knowledge의 Expected Account Contribution
비율로 다시 가중해 최종 자본 요청을 만든다. 개별 심볼 ranking 근거가 부족하면
reference-only 균등분할 fallback을 허용하되 공통 Portfolio/Risk/Margin hard safety는
별도 계층에서 계속 강제한다.
"""
from __future__ import annotations
import json, math, os
from pathlib import Path
import strategy_runtime

BASE=Path(os.getenv("BOT_BASE_DIR","/home/ec2-user/bot"))


def _finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def deployment_weight(timeframe: str) -> float:
    row=strategy_runtime.frame(timeframe)
    tier=str(row.get("deployment_tier") or "OFF").upper()
    dep=strategy_runtime.get("deployment",{})
    dep=dep if isinstance(dep,dict) else {}
    if tier in {"PROVISIONAL_MAIN","CANARY"}:
        try: return max(0.0,min(1.0,float(dep.get("canary_weight",0.20))))
        except Exception: return 0.0
    if tier=="SMALL":
        try: return max(0.0,min(1.0,float(row.get("capital_weight",dep.get("small_weight",0.0)))))
        except Exception: return 0.0
    if tier in {"VALIDATED_MAIN","NORMAL"}:
        try: return max(0.0,min(1.0,float(dep.get("normal_weight",1.0))))
        except Exception: return 0.0
    return 0.0


def _runtime_knowledge() -> dict:
    for p in (BASE/"state"/"research_knowledge_runtime.json", BASE/"strategy_lab"/"results"/"v3"/"research_knowledge_runtime.json"):
        try:
            x=json.loads(p.read_text(encoding="utf-8-sig"))
            if isinstance(x,dict): return x
        except Exception: pass
    return {}


def _active_symbols() -> set[str]:
    try:
        cfg=json.loads((BASE/"config.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    raw=cfg.get("SYMBOLS") if isinstance(cfg,dict) else None
    if isinstance(raw,dict): return {str(k).upper() for k,v in raw.items() if v}
    if isinstance(raw,list): return {str(x).upper() for x in raw if str(x).strip()}
    return set()


def evidence_priority_weight(candidate: dict, active_symbol_count: int) -> dict:
    """Expected Account Contribution 기반 weight.

    frozen order.compute_entry()는 available/active_count를 먼저 계산하므로,
    여기서 `active_count * contribution_share`를 반환하면 실제 alloc은
    `available * contribution_share`가 된다. MAJOR/correlation은 사용하지 않는다.
    """
    c=candidate if isinstance(candidate,dict) else {}
    edge=c.get("edge") if isinstance(c.get("edge"),dict) else {}
    own=_finite(edge.get("expected_account_contribution"))
    if own is None or own <= 0:
        return {"decision":"BLOCK","reason":"EXPECTED_ACCOUNT_CONTRIBUTION_MISSING_OR_NONPOSITIVE","weight":0.0}
    # VERSIONED_SYMBOL_EXPECTATION_POOL_V1
    symbol=str(c.get("symbol") or "").upper()
    tf=str(c.get("timeframe") or "").upper()
    strategy=str(c.get("strategy_id") or "").upper()
    version=str(c.get("strategy_version") or "").upper()

    if not version:
        return {
            "decision":"BLOCK",
            "reason":"STRATEGY_VERSION_MISSING",
            "weight":0.0,
        }

    k=_runtime_knowledge()
    records=k.get("records") if isinstance(k.get("records"),dict) else {}
    vals={}
    active=_active_symbols()

    for key,row in records.items():
        if not isinstance(row,dict):
            continue

        parts=str(key).upper().split("|")

        # Canonical runtime knowledge key:
        # strategy|timeframe|symbol|strategy_version
        if (
            len(parts)!=4
            or parts[0]!=strategy
            or parts[1]!=tf
            or parts[3]!=version
            or parts[2] in {"","*"}
        ):
            continue

        if active and parts[2] not in active:
            continue

        v=_finite(
            row.get(
                "expected_account_contribution"
            )
        )

        if v is not None and v>0:
            vals[parts[2]]=v
    # SYMBOL_EXPECTATION_VERSION_FALLBACK_V1
    # ??? ??? ??? strategy/version ???
    # positive expected contribution?? equal split ??.
    n=max(1,int(active_symbol_count or 0))

    if symbol not in vals:
        return {
            "decision":"ALLOW",
            "reason":"SYMBOL_EXPECTATION_VERSION_FALLBACK_EQUAL_SPLIT",
            "weight":1.0,
            "share":float(1.0/n),
            "pool_symbols":len(vals),
            "expected_account_contribution":float(own),
            "capital_time_efficiency":
                _finite(edge.get("capital_time_efficiency")),
        }

    total=sum(vals.values())

    if total<=0:
        return {
            "decision":"ALLOW",
            "reason":"VERSION_EXPECTATION_EQUAL_SPLIT_FALLBACK",
            "weight":1.0,
            "share":float(1.0/n),
            "pool_symbols":len(vals),
            "expected_account_contribution":float(own),
            "capital_time_efficiency":
                _finite(edge.get("capital_time_efficiency")),
        }

    share=vals[symbol]/total
    return {
        "decision":"ALLOW","reason":"EXPECTED_ACCOUNT_CONTRIBUTION_SHARE",
        "weight":float(n*share),"share":float(share),"pool_symbols":len(vals),
        "expected_account_contribution":float(own),
        "capital_time_efficiency":_finite(edge.get("capital_time_efficiency")),
    }


def final_weight(timeframe: str, candidate: dict, active_symbol_count: int) -> dict:
    dep=deployment_weight(timeframe)
    if dep<=0: return {"decision":"BLOCK","reason":"DEPLOYMENT_TIER_BLOCK","weight":0.0}
    pr=evidence_priority_weight(candidate,active_symbol_count)
    if pr.get("decision")!="ALLOW": return pr
    out=dict(pr); out["deployment_weight"]=dep; out["weight"]=float(pr["weight"]*dep)
    if out["weight"]<=0: out.update(decision="BLOCK",reason="FINAL_WEIGHT_ZERO")
    return out


def apply_qty(timeframe: str, qty: float) -> dict:
    w=deployment_weight(timeframe); q=max(0.0,float(qty))*w
    return {"weight":w,"requested_qty":float(qty),"approved_qty":q,"decision":"ALLOW" if q>0 else "BLOCK"}


def objective_fields() -> tuple[str,...]:
    return ("expected_return","expected_loss","expected_holding_time_hours","capital_time_efficiency","expected_account_contribution","confidence")
