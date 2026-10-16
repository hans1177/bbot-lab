#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: late_entry.py
"""
V3 Late Entry Replay.
- 원신호부터 현재까지 1H 가격경로를 사용한다.
- 기존 고정 Trailing 3/1.5, owner 전략 반대합의, next confirmed 1H state 재진입을 재현한다.
- intrabar 순서를 OHLC만으로 확정할 수 없는 경우 fail-closed.
- Control SL은 포트폴리오/계좌 상태 의존이므로 과거 단일 Candidate에서 추정하지 않고
  현재 Entry Risk가 최종 차단한다.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

import strategy_runtime
from assist import strategy_registry

BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
DATA = BASE / "backtest_data"
_CACHE: dict[tuple, tuple[float, dict]] = {}

TF_FILE = {"30M":"30m","1H":"1h","6H":"6h","12H":"12h","1D":"1d","1W":"1w"}


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _ts(v: Any):
    try:
        x=pd.to_datetime(v,utc=True,errors="coerce")
        return None if pd.isna(x) else x
    except Exception:
        return None


def _load(symbol: str, interval: str) -> pd.DataFrame:
    p=DATA / symbol / f"{interval}.parquet"
    if not p.is_file():
        return pd.DataFrame()
    try:
        d=pd.read_parquet(p)
        d=d.copy(); d.columns=[str(c).lower() for c in d.columns]
        need={"open_time","open","high","low","close"}
        if not need.issubset(d.columns): return pd.DataFrame()
        d["open_time"]=pd.to_datetime(d["open_time"],utc=True,errors="coerce")
        for c in ("open","high","low","close"):
            d[c]=pd.to_numeric(d[c],errors="coerce")
        return d.dropna(subset=list(need)).sort_values("open_time").drop_duplicates("open_time",keep="last").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _signal_direction(result: dict) -> str:
    if not isinstance(result,dict): return "WAIT"
    sig=_text(result.get("signal")).lower()
    state=_text(result.get("state")).lower()
    if sig in {"long","buy"} or state in {"long","buy"}: return "LONG"
    if sig in {"short","sell"} or state in {"short","sell"}: return "SHORT"
    try:
        lo=float(result.get("strength_long") or 0); sh=float(result.get("strength_short") or 0)
    except Exception:
        lo=sh=0
    if lo>0 and sh<=0: return "LONG"
    if sh>0 and lo<=0: return "SHORT"
    return "WAIT"


def _consensus(df: pd.DataFrame, strategies: list[str]) -> str:
    dirs=[]
    for name in strategies:
        try: dirs.append(_signal_direction(strategy_registry.strategy_signal(name,df)))
        except Exception: return "WAIT"
    if dirs and all(x=="LONG" for x in dirs): return "LONG"
    if dirs and all(x=="SHORT" for x in dirs): return "SHORT"
    return "WAIT"


def replay(candidate: dict, current_price: float | None = None, *, max_1h_bars: int = 5000) -> dict:
    # RESEARCH_SHADOW_LATE_ENTRY_RELAX_V1
    import os as _shadow_os
    _shadow_runtime_mode=str(
        _shadow_os.getenv(
            "R4_RUNTIME_MODE",
            "LOCAL_SHADOW",
        )
        or "LOCAL_SHADOW"
    ).upper()
    _research_shadow_observe=(
        _shadow_runtime_mode
        in {
            "LOCAL_SHADOW",
            "LOCAL_TEST",
            "SHADOW",
            "TEST",
        }
    )
    signal=candidate.get("signal") if isinstance(candidate,dict) else {}
    symbol=_text(candidate.get("symbol")).upper()
    tf=_text(candidate.get("timeframe")).upper()
    side=_text(candidate.get("side")).upper()
    origin=_ts(signal.get("signal_time"))
    signal_price=signal.get("signal_price")
    strategies=[x for x in _text(candidate.get("strategy_id")).upper().split("+") if x]
    if side not in {"LONG","SHORT"}:
        return {"decision":"BLOCK","reason":"NO_TRADE","replay_status":"INVALID"}
    if not symbol or tf not in TF_FILE or origin is None or not strategies:
        return {"decision":"BLOCK","reason":"REPLAY_INPUT_INVALID","replay_status":"INVALID"}
    try: signal_price=float(signal_price)
    except Exception: signal_price=0.0
    if signal_price<=0:
        return {"decision":"BLOCK","reason":"SIGNAL_PRICE_MISSING","replay_status":"INVALID"}

    one=_load(symbol,"1h")
    owner=_load(symbol,TF_FILE[tf])
    if one.empty or owner.empty:
        return {"decision":"BLOCK","reason":"REPLAY_DATA_MISSING","replay_status":"INVALID"}
    one=one[one["open_time"]>=origin].copy()
    if one.empty:
        return {"decision":"BLOCK","reason":"REPLAY_PATH_EMPTY","replay_status":"INVALID"}
    if len(one)>max_1h_bars:
        return {"decision":"BLOCK","reason":"REPLAY_PATH_TOO_LONG","replay_status":"INVALID","bars":int(len(one))}

    trail=strategy_runtime.trailing()
    start=float(trail.get("start_pct",3.0))/100.0
    callback=float(trail.get("callback_pct",1.5))/100.0
    in_pos=True; armed=False; extreme=signal_price; entry=signal_price
    exits=[]; reentries=[]; ambiguous=False; ambiguity_recoveries=[]
    current_consensus=side
    owner_idx=0
    owner_times=list(owner["open_time"])
    last_owner_cut=-1

    for _,bar in one.iterrows():
        t=bar["open_time"]
        while owner_idx < len(owner_times) and owner_times[owner_idx] <= t:
            owner_idx += 1
        cut=owner_idx
        if cut>0 and cut!=last_owner_cut:
            current_consensus=_consensus(owner.iloc[:cut],strategies)
            last_owner_cut=cut
            if in_pos and current_consensus in {"LONG","SHORT"} and current_consensus != side:
                exits.append({"ts":str(t),"reason":"OPPOSITE_EXIT","price":float(bar["close"])})
                in_pos=False
                # 원 Candidate 방향이 반전됐으므로 과거 신호 Late Entry는 더 이상 유효하지 않다.
                return {"decision":"BLOCK","reason":"OPPOSITE_EXIT_OCCURRED","replay_status":"EXITED","exits":exits,"reentries":reentries}

        if not in_pos:
            if current_consensus == side:
                # 기존 State Reentry: trailing exit 후 next confirmed 1H check에서 같은 owner state면 재진입.
                in_pos=True; armed=False; entry=float(bar["close"]); extreme=entry
                reentries.append({"ts":str(t),"reason":"STATE_REENTRY","price":entry})
            else:
                continue

        high=float(bar["high"]); low=float(bar["low"]); close=float(bar["close"])
        if side=="LONG":
            arm_hit=(high-entry)/entry >= start
            if not armed and arm_hit:
                armed=True; extreme=max(entry,high)
                stop=extreme*(1-callback)
                if low <= stop:
                    # HISTORICAL_INTRABAR_AMBIGUITY_CONSERVATIVE_REPLAY_V1
                    if t < one.iloc[-1]["open_time"]:
                        exits.append({
                            "ts":str(t),
                            "reason":
                                "TRAILING_AMBIGUOUS_CONSERVATIVE_EXIT",
                            "price":float(stop),
                        })
                        ambiguity_recoveries.append({
                            "ts":str(t),
                            "side":side,
                            "resolution":
                                "EXIT_THEN_EXISTING_STATE_REENTRY",
                        })
                        in_pos=False
                        armed=False
                        continue

                    ambiguous=True
                    break
            elif armed:
                extreme=max(extreme,high); stop=extreme*(1-callback)
                if low <= stop:
                    exits.append({"ts":str(t),"reason":"TRAILING","price":stop})
                    in_pos=False; armed=False
        else:
            arm_hit=(entry-low)/entry >= start
            if not armed and arm_hit:
                armed=True; extreme=min(entry,low)
                stop=extreme*(1+callback)
                if high >= stop:
                    # HISTORICAL_INTRABAR_AMBIGUITY_CONSERVATIVE_REPLAY_V1
                    if t < one.iloc[-1]["open_time"]:
                        exits.append({
                            "ts":str(t),
                            "reason":
                                "TRAILING_AMBIGUOUS_CONSERVATIVE_EXIT",
                            "price":float(stop),
                        })
                        ambiguity_recoveries.append({
                            "ts":str(t),
                            "side":side,
                            "resolution":
                                "EXIT_THEN_EXISTING_STATE_REENTRY",
                        })
                        in_pos=False
                        armed=False
                        continue

                    ambiguous=True
                    break
            elif armed:
                extreme=min(extreme,low); stop=extreme*(1+callback)
                if high >= stop:
                    exits.append({"ts":str(t),"reason":"TRAILING","price":stop})
                    in_pos=False; armed=False

    if ambiguous:
        if _research_shadow_observe:
            return {
                "decision":"ALLOW",
                "reason":"RESEARCH_SHADOW_COUNTERFACTUAL_ALLOW",
                "replay_status":"SHADOW_WOULD_BLOCK",
                "would_block_reason":"INTRABAR_ORDER_AMBIGUOUS",
                "shadow_only":True,
                "live_eligible":False,
                "exits":exits,
                "reentries":reentries,
                "ambiguity_recoveries":ambiguity_recoveries,
            }
        return {"decision":"BLOCK","reason":"INTRABAR_ORDER_AMBIGUOUS","replay_status":"AMBIGUOUS","exits":exits,"reentries":reentries,"ambiguity_recoveries":ambiguity_recoveries}
    if not in_pos:
        if _research_shadow_observe:
            return {
                "decision":"ALLOW",
                "reason":"RESEARCH_SHADOW_COUNTERFACTUAL_ALLOW",
                "replay_status":"SHADOW_WOULD_BLOCK",
                "would_block_reason":"POSITION_NOT_ALIVE_AT_NOW",
                "shadow_only":True,
                "live_eligible":False,
                "exits":exits,
                "reentries":reentries,
            }
        return {"decision":"BLOCK","reason":"POSITION_NOT_ALIVE_AT_NOW","replay_status":"EXITED","exits":exits,"reentries":reentries}
    cp=float(current_price or one.iloc[-1]["close"])
    return {
        "decision":"ALLOW",
        "reason":"BASELINE_REPLAY_ALIVE",
        "replay_status":"ALIVE" if not reentries else "REENTERED",
        "original_signal_price":signal_price,
        "current_price":cp,
        "price_distance_pct":((cp-signal_price)/signal_price*100.0) if side=="LONG" else ((signal_price-cp)/signal_price*100.0),
        "bars":int(len(one)),
        "exits":exits,
        "reentries":reentries,
        "ambiguity_recoveries":ambiguity_recoveries,
        "control_sl_replay":"DEFER_TO_CURRENT_ACCOUNT_ENTRY_RISK",
        "remaining_expected_edge":None,
        "remaining_expected_edge_status":"RESEARCH_KNOWLEDGE_NOT_YET_CALIBRATED",
    }
