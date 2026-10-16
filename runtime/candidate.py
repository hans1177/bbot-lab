#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: candidate.py
"""V3 공통 Candidate Contract. 주문/거래소 호출 없음."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

SCHEMA = "B_BOT_V3_CANDIDATE_V1"


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _float(v: Any, default=None):
    try:
        return float(v)
    except Exception:
        return default


def _side(v: Any) -> str:
    s = _text(v).upper()
    if s in {"BUY", "LONG"}:
        return "LONG"
    if s in {"SELL", "SHORT"}:
        return "SHORT"
    return "NO_TRADE"



def _runtime_config_version() -> str:
    try:
        import strategy_runtime
        return _text(strategy_runtime.get("version", "UNKNOWN")) or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _runtime_versions(timeframe: str) -> tuple[str, dict]:
    try:
        import strategy_runtime
        frame = strategy_runtime.frame(timeframe)
        strategy_version = _text(frame.get("strategy_version") or "CONFIG_CURRENT") or "CONFIG_CURRENT"
        policy_versions = strategy_runtime.get("policy_versions", {})
        return strategy_version, dict(policy_versions) if isinstance(policy_versions, dict) else {}
    except Exception:
        return "CONFIG_CURRENT", {}


def candidate_id(symbol: str, timeframe: str, strategy_id: str, strategy_version: str, side: str, signal_time: str) -> str:
    raw = "|".join([
        _text(symbol).upper(),
        _text(timeframe).upper(),
        _text(strategy_id).upper(),
        _text(strategy_version).upper(),
        _text(side).upper(),
        _text(signal_time),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build(gate_result: dict, *, signal_price=None, current_price=None, evidence: dict | None = None) -> dict:
    row = gate_result if isinstance(gate_result, dict) else {}
    symbol = _text(row.get("symbol")).upper()
    timeframe = _text(row.get("frame")).upper()
    strategy = _text(row.get("strategy")).upper()
    signal_time = _text(row.get("signal_origin_time") or row.get("confirmed_open_time"))
    side = _side(row.get("side") or row.get("decision"))
    runtime_strategy_version, runtime_policy_versions = _runtime_versions(timeframe)
    strategy_version = _text(row.get("strategy_version") or runtime_strategy_version or "CONFIG_CURRENT")
    policy_versions = row.get("policy_versions") if isinstance(row.get("policy_versions"), dict) else runtime_policy_versions
    cid = candidate_id(symbol, timeframe, strategy, strategy_version, side, signal_time)
    return {
        "schema": SCHEMA,
        "candidate_id": cid,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": strategy,
        "strategy_version": strategy_version,
        "policy_versions": dict(policy_versions),
        "research_version": _text(row.get("research_version") or ""),
        "config_version": _text(row.get("config_version") or _runtime_config_version()),
        "side": side,
        "signal": {
            "signal_time": signal_time,
            "signal_price": _float(signal_price if signal_price is not None else row.get("signal_price")),
            "current_price": _float(current_price),
            "source_signal_id": _text(row.get("signal_id")),
            "still_active": bool(row.get("allow") == 1 and side != "NO_TRADE"),
        },
        "edge": {
            "expected_return": None,
            "expected_loss": None,
            "expected_holding_time_hours": None,
            "capital_time_efficiency": None,
            "expected_account_contribution": None,
            "confidence": None,
            "status": "RESEARCH_KNOWLEDGE_NOT_YET_BOUND",
        },
        "evidence": evidence if isinstance(evidence, dict) else {},
        "entry_risk": {},
        "late_entry": {},
        "allocation": {},
        "validity": {
            "valid_from": signal_time,
            "valid_until": None,
            "strategy_state_driven": True,
            "signal_still_active": bool(row.get("allow") == 1 and side != "NO_TRADE"),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def canonical_json(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def load_runtime_knowledge(base_dir: str = "/home/ec2-user/bot") -> dict:
    from pathlib import Path
    try:
        p=Path(base_dir)/"state"/"research_knowledge_runtime.json"
        x=json.loads(p.read_text(encoding="utf-8"))
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}


def bind_knowledge(row: dict, knowledge: dict | None = None) -> dict:
    out=dict(row or {})
    knowledge=knowledge if isinstance(knowledge,dict) else load_runtime_knowledge()
    strategy=_text(out.get("strategy_id")).upper(); tf=_text(out.get("timeframe")).upper(); symbol=_text(out.get("symbol")).upper(); version=_text(out.get("strategy_version")).upper()
    key="|".join([strategy,tf,symbol,version])
    family="|".join([strategy,tf,"*",version])
    rec=None
    records=knowledge.get("records") if isinstance(knowledge.get("records"),dict) else {}
    for k in (key,family):
        if isinstance(records.get(k),dict): rec=records[k]; break
    if rec is None:
        aliases=knowledge.get("legacy_aliases") if isinstance(knowledge.get("legacy_aliases"),dict) else {}
        for old_key in ("|".join([strategy,tf,symbol]),"|".join([strategy,tf,"*"])):
            target=aliases.get(old_key)
            if target and isinstance(records.get(target),dict): rec=records[target]; break
    if rec:
        edge=dict(out.get("edge") or {})
        for f in ("expected_return","expected_loss","expected_holding_time_hours","capital_time_efficiency","expected_account_contribution","confidence"):
            if f in rec: edge[f]=rec.get(f)
        edge["status"]="BOUND_FROM_VERSIONED_RESEARCH_KNOWLEDGE"
        out["edge"]=edge
        out["evidence"]=rec.get("evidence_summary") if isinstance(rec.get("evidence_summary"),dict) else out.get("evidence",{})
    return out


def select_best(rows: list[dict]) -> tuple[dict | None,str]:
    """Select one executable candidate from same-symbol multi-TF competition.

    Constitutional objective:
      1. Expected Account Contribution
      2. Capital-Time Efficiency
      3. Confidence as tertiary evidence

    Multi-candidate comparison fails closed when required objective evidence
    is incomplete. Exact objective/evidence ties also fail closed instead of
    selecting by list order or timeframe.
    """
    rows = [x for x in rows if isinstance(x, dict)]

    if not rows:
        return None, "NO_CANDIDATE"

    if len(rows) == 1:
        return rows[0], "SINGLE_CANDIDATE"

    scored = []

    for row in rows:
        edge = (
            row.get("edge")
            if isinstance(row.get("edge"), dict)
            else {}
        )

        try:
            expected_account_contribution = float(
                edge.get("expected_account_contribution")
            )
        except Exception:
            expected_account_contribution = None

        try:
            capital_time_efficiency = float(
                edge.get("capital_time_efficiency")
            )
        except Exception:
            capital_time_efficiency = None

        try:
            confidence = float(
                edge.get("confidence")
            )
        except Exception:
            confidence = 0.0

        if (
            expected_account_contribution is None
            or capital_time_efficiency is None
        ):
            continue

        scored.append(
            (
                expected_account_contribution,
                capital_time_efficiency,
                confidence,
                row,
            )
        )

    if not scored:
        return None, "MULTI_CANDIDATE_NO_EXPECTATION_EVIDENCE"

    if len(scored) != len(rows):
        return None, "MULTI_CANDIDATE_INCOMPLETE_EXPECTATION_EVIDENCE"

    scored.sort(
        key=lambda x: (
            x[0],
            x[1],
            x[2],
        ),
        reverse=True,
    )

    if (
        len(scored) > 1
        and scored[0][:3] == scored[1][:3]
    ):
        return None, "MULTI_CANDIDATE_TIE"

    return (
        scored[0][3],
        "EXPECTED_ACCOUNT_CONTRIBUTION_PLUS_CAPITAL_TIME_EFFICIENCY",
    )
