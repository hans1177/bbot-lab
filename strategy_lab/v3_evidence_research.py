# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_evidence_research.py
"""서버 READ-ONLY snapshot을 로컬 연구 evidence로 환류한다.

원칙:
- 서버/포지션/실주문/Runtime 설정을 절대 수정하지 않는다.
- exact Strategy Instance + exact Strategy Version 귀속이 확인된 R4 원장만 사용한다.
- 없는 실현값은 추정/백필하지 않고 UNAVAILABLE로 남긴다.
- LIVE 성과는 연구 우선순위와 사후분석에만 사용하고 직접 승격 근거로 바꾸지 않는다.
"""
from __future__ import annotations

# ================= 임포트 =================
import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ================= 경로 / 상수 =================
ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
V3 = ROOT / "strategy_lab" / "results" / "v3"
DB = ROOT / "data" / "server_runtime" / "history.db"
RSTATE = ROOT / "data" / "server_runtime" / "state"
OUT = V3 / "evidence_research.json"
RUNTIME_FEEDBACK = V3 / "runtime_feedback.json"

TASKS = [
    "LIVE_STRATEGY_SUMMARY",
    "EXIT_POLICY_SUMMARY",
    "MAJOR_OTHER_EXPLORATORY",
    "TF_CONFLICT_OBSERVATION",
    "CAPITAL_TIME_REALIZED",
]

R4_REQUIRED_EVIDENCE = [
    "ENTRY_ID",
    "ORDER_ID",
    "FILLS",
    "FEE",
    "SLIPPAGE",
    "REALIZED_PNL",
    "MAE",
    "MFE",
    "HOLDING_TIME",
    "STOP_EXIT_REENTRY_RESULT",
    "EXECUTION_FAILURE",
]

MAX_FEEDBACK_LEGS = 2000
MAX_FEEDBACK_INSTANCES = 256
MAX_IDS_PER_LEG = 16

EVIDENCE_TRACK_MAP = {
    "ENTRY_RISK_SAFE_SIZE_PASS": "ENTRY_RISK",
    "ACCOUNT_RISK_BUDGET_PASS": "PORTFOLIO",
    "PORTFOLIO_TOTAL_EXPOSURE_PASS": "PORTFOLIO",
    "AVAILABLE_MARGIN_PASS": "PORTFOLIO",
    "MARGIN_SAFETY_RESERVE_PASS": "PORTFOLIO",
    "SYMBOL_CONCENTRATION_PASS": "SYMBOL",
    "STRATEGY_CONCENTRATION_PASS": "PORTFOLIO",
    "CORRELATION_CONCENTRATION_PASS": "PORTFOLIO",
    "COST_AND_LIQUIDITY_PASS": "EXECUTION",
    "RELATIVE_TIER_COMPETITION_PASS": "STRATEGY",
    "STRATEGY_LEG_LEDGER_RECONCILE_PASS": "EXECUTION",
    "NO_CRITICAL_HARD_FAIL": "EXECUTION",
}

# ================= 유틸 =================
def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def atomic(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _decimal(value):
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _json(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        obj = json.loads(str(value or "{}"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _mean(values):
    rows = [float(x) for x in values if x is not None]
    return (sum(rows) / len(rows)) if rows else None


def _digest_payload(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _table_exists(name: str) -> bool:
    if not DB.exists():
        return False
    con = sqlite3.connect(str(DB), timeout=10)
    try:
        return bool(
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (str(name),),
            ).fetchone()
        )
    finally:
        con.close()


def table_columns(name):
    if not DB.exists():
        return set()
    con = sqlite3.connect(str(DB), timeout=10)
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({name})").fetchall()}
    except Exception:
        return set()
    finally:
        con.close()


def q(sql, args=()):
    if not DB.exists():
        return []
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(x) for x in con.execute(sql, args).fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def signature():
    h = hashlib.sha256()
    for path in [
        DB,
        RSTATE / "major_symbols.json",
        RSTATE / "v3_blocked_additional_candidates.json",
        RUNTIME_FEEDBACK,
    ]:
        if path.exists():
            st = path.stat()
            h.update(str(path).encode())
            h.update(str(st.st_size).encode())
            h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()


# ================= 기존 descriptive evidence =================
def hold_expr():
    cols = table_columns("exit_history")
    if "hold_sec" in cols:
        return "COALESCE(hold_sec,0)"
    if "holding_seconds" in cols:
        return "COALESCE(holding_seconds,0)"
    return "0"


def live_summary():
    # LIVE_POSTMORTEM_DESCRIPTIVE_V2
    #
    # Legacy execution history is descriptive evidence only.
    # It is never silently treated as exact R4 instance evidence.
    h = hold_expr()
    cols = table_columns("exit_history")
    giveback = (
        "AVG(COALESCE(max_giveback_roi,0))"
        if "max_giveback_roi" in cols
        else "NULL"
    )

    rows = q(
        f"SELECT UPPER(COALESCE(entry_tf,'')) tf, "
        f"UPPER(COALESCE(entry_strategy,'')) strategy, "
        f"COUNT(*) n, "
        f"SUM(CASE WHEN roi>0 THEN 1 ELSE 0 END) wins, "
        f"SUM(CASE WHEN roi<0 THEN 1 ELSE 0 END) losses, "
        f"SUM(CASE WHEN roi=0 THEN 1 ELSE 0 END) breakeven, "
        f"AVG(roi) avg_roi, "
        f"AVG(CASE WHEN roi>0 THEN roi END) avg_win_roi, "
        f"AVG(CASE WHEN roi<0 THEN roi END) avg_loss_roi, "
        f"SUM(COALESCE(pnl,0)) pnl, "
        f"AVG({h})/3600.0 hold_h, "
        f"AVG(COALESCE(max_favorable_roi,0)) mfe, "
        f"AVG(COALESCE(max_adverse_roi,0)) mae, "
        f"{giveback} max_giveback "
        "FROM exit_history "
        "WHERE COALESCE(entry_tf,'')<>'' "
        "GROUP BY 1,2"
    )

    trade_rows = q(
        "SELECT id,ts,"
        "UPPER(COALESCE(entry_tf,'')) tf,"
        "UPPER(COALESCE(entry_strategy,'')) strategy,"
        "COALESCE(pnl,0) pnl "
        "FROM exit_history "
        "WHERE COALESCE(entry_tf,'')<>'' "
        "ORDER BY tf,strategy,ts,id"
    )

    grouped = {}

    for item in trade_rows:
        key=(
            str(item.get("tf") or "").upper(),
            str(item.get("strategy") or "").upper(),
        )
        grouped.setdefault(key,[]).append(item)

    for row in rows:
        n=int(row.get("n") or 0)
        wins=int(row.get("wins") or 0)
        losses=int(row.get("losses") or 0)

        row["win_rate"]=(wins/n) if n>0 else None

        aw=row.get("avg_win_roi")
        al=row.get("avg_loss_roi")

        try:
            row["payoff_ratio"]=(
                float(aw)/abs(float(al))
                if aw is not None
                and al is not None
                and float(al)!=0.0
                else None
            )
        except Exception:
            row["payoff_ratio"]=None

        if n>=30:
            grade="MATURE_30_PLUS"
        elif n>=10:
            grade="DEVELOPING_10_29"
        elif n>=5:
            grade="THIN_5_9"
        else:
            grade="VERY_THIN_LT5"

        row["sample_size_grade"]=grade
        row["realized_expectancy_roi"]=row.get("avg_roi")

        key=(
            str(row.get("tf") or "").upper(),
            str(row.get("strategy") or "").upper(),
        )

        equity=0.0
        peak=0.0
        max_dd=0.0
        dd_len=0
        max_dd_len=0

        for item in grouped.get(key,[]):
            try:
                pnl=float(item.get("pnl") or 0.0)
            except Exception:
                pnl=0.0

            equity+=pnl

            if equity>=peak:
                peak=equity
                dd_len=0
            else:
                dd_len+=1
                max_dd_len=max(max_dd_len,dd_len)
                max_dd=max(max_dd,peak-equity)

        row["realized_pnl_mdd"]=max_dd
        row["realized_pnl_dd_duration_trades"]=max_dd_len

    return {
        "rows": rows,
        "interpretation": (
            "REAL_EXECUTION_DESCRIPTIVE; "
            "expectancy/win-rate/payoff/MFE/MAE/hold/"
            "realized-PnL-drawdown are descriptive only; "
            "legacy history is never silently equated to "
            "R4 exact-instance metrics."
        ),
    }


def exit_policy():
    h = hold_expr()
    rows = q(
        f"SELECT UPPER(COALESCE(entry_tf,'')) tf, UPPER(COALESCE(entry_strategy,'')) strategy, "
        f"COALESCE(exit_type,'') exit_type, COALESCE(reason,'') reason, COUNT(*) n, AVG(roi) avg_roi, AVG({h})/3600.0 hold_h "
        "FROM exit_history WHERE COALESCE(entry_tf,'')<>'' GROUP BY 1,2,3,4 ORDER BY n DESC LIMIT 300"
    )
    return {"rows": rows, "policy": "DESCRIPTIVE_ONLY; policy mutation requires separate validation."}


def major_other():
    major = load(RSTATE / "major_symbols.json", {})
    profiles = major.get("profiles") if isinstance(major, dict) else {}
    labels = {
        str(k).upper(): str((v or {}).get("classification") or (v or {}).get("class") or (v or {}).get("label") or "OTHER").upper()
        for k, v in (profiles.items() if isinstance(profiles, dict) else [])
    }
    h = hold_expr()
    rows = q(
        f"SELECT symbol, UPPER(COALESCE(entry_tf,'')) tf, UPPER(COALESCE(entry_strategy,'')) strategy, roi, pnl, {h} hold_sec "
        "FROM exit_history WHERE COALESCE(entry_tf,'')<>''"
    )
    agg = {}
    for row in rows:
        label = labels.get(str(row.get("symbol") or "").upper(), "UNKNOWN_CURRENT_PROFILE")
        key = (row.get("tf"), row.get("strategy"), label)
        item = agg.setdefault(key, {"n": 0, "roi": 0.0, "pnl": 0.0, "hold": 0.0})
        item["n"] += 1
        item["roi"] += float(row.get("roi") or 0)
        item["pnl"] += float(row.get("pnl") or 0)
        item["hold"] += float(row.get("hold_sec") or 0)
    out = []
    for (tf, strategy, label), item in agg.items():
        out.append(
            {
                "tf": tf,
                "strategy": strategy,
                "class": label,
                "n": item["n"],
                "avg_roi": item["roi"] / item["n"],
                "total_pnl": item["pnl"],
                "avg_hold_h": item["hold"] / item["n"] / 3600,
            }
        )
    return {
        "rows": out,
        "limitation": "Current MAJOR/OTHER label is not historical point-in-time classification. Exploratory only.",
    }


def tf_conflict():
    state = load(RSTATE / "v3_blocked_additional_candidates.json", {})
    rows = []
    for _, per in (state.items() if isinstance(state, dict) else []):
        if not isinstance(per, dict):
            continue
        for _, row in per.items():
            if isinstance(row, dict):
                rows.append(row)
    same = sum(str(row.get("relation")) == "SAME_DIRECTION" for row in rows)
    opposite = sum(str(row.get("relation")) == "OPPOSITE_DIRECTION" for row in rows)
    return {
        "observations": len(rows),
        "same_direction": same,
        "opposite_direction": opposite,
        "rows": rows[-200:],
        "live_action": "RECORD_ONLY",
    }


def capital_time():
    h = hold_expr()
    rows = q(
        f"SELECT UPPER(COALESCE(entry_tf,'')) tf, UPPER(COALESCE(entry_strategy,'')) strategy, COUNT(*) n, "
        f"SUM(COALESCE(pnl,0)) pnl, SUM({h})/3600.0 capital_hours "
        "FROM exit_history WHERE COALESCE(entry_tf,'')<>'' GROUP BY 1,2"
    )
    for row in rows:
        hours = float(row.get("capital_hours") or 0)
        row["realized_pnl_per_hold_hour"] = float(row.get("pnl") or 0) / hours if hours > 0 else None
    return {"rows": rows, "use": "Research evidence only; not a standalone allocation rule."}


# ================= R4 exact-instance Runtime 환류 =================
def _r4_ready() -> bool:
    required = {
        "r4_strategy_instances",
        "r4_active_legs",
        "r4_order_intents",
        "r4_fills",
        "r4_leg_events",
        "r4_tier_evidence",
    }
    return DB.exists() and all(_table_exists(name) for name in required)


def _fee_totals(entry_fills, exit_details):
    totals = defaultdict(Decimal)
    for row in entry_fills:
        asset = str(row.get("fee_asset") or "UNKNOWN").upper() or "UNKNOWN"
        value = _decimal(row.get("fee_text"))
        if value is not None:
            totals[asset] += value
    for detail in exit_details:
        asset = str(detail.get("fee_asset") or "UNKNOWN").upper() or "UNKNOWN"
        value = _decimal(detail.get("fee_text"))
        if value is not None:
            totals[asset] += value
    return {key: str(value) for key, value in sorted(totals.items())}


def _realized_pnl(entry_fills, exit_details):
    values = []
    for row in entry_fills:
        value = _decimal(row.get("realized_pnl_text"))
        if value is not None:
            values.append(value)
    for detail in exit_details:
        value = _decimal(detail.get("realized_pnl_text"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return str(sum(values, Decimal("0")))


def _entry_reference_ticks(policy):
    value = policy.get("entry_reference_price_ticks")
    try:
        value = int(value or 0)
    except Exception:
        value = 0
    return value if value > 0 else None


def _legacy_entry_id(policy, events):
    value = str(policy.get("legacy_entry_id") or "").strip()
    if value:
        return value
    for event in events:
        if str(event.get("event_type") or "") == "LEGACY_ADOPTION":
            detail = _json(event.get("detail_json"))
            value = str(detail.get("legacy_entry_id") or "").strip()
            if value:
                return value
    return ""


def _r4_leg_records():
    leg_rows = q(
        """
        SELECT l.*, s.exact_version AS instance_exact_version,
               s.policy_bundle AS instance_policy_bundle,
               s.strategy_id AS instance_strategy_id,
               s.timeframe AS instance_timeframe,
               s.tier AS instance_current_tier
        FROM r4_active_legs l
        JOIN r4_strategy_instances s ON s.instance_id=l.instance_id
        ORDER BY l.updated_ts DESC, l.leg_id DESC
        LIMIT ?
        """,
        (MAX_FEEDBACK_LEGS,),
    )
    if not leg_rows:
        return []

    fill_cols = table_columns("r4_fills")
    has_fee_asset = "fee_asset" in fill_cols
    has_realized = "realized_pnl_text" in fill_cols
    trailing_cols = table_columns("r4_leg_trailing")
    has_mfe = "mfe_ticks" in trailing_cols
    has_mae = "mae_ticks" in trailing_cols

    records = []
    for leg in leg_rows:
        instance_id = str(leg.get("instance_id") or "")
        exact_version = str(leg.get("instance_exact_version") or "")
        if not instance_id or str(leg.get("strategy_version") or "") != exact_version:
            raise RuntimeError("R4_RUNTIME_FEEDBACK_EXACT_INSTANCE_VERSION_MISMATCH")
        if str(leg.get("strategy_id") or "").upper() != str(leg.get("instance_strategy_id") or "").upper():
            raise RuntimeError("R4_RUNTIME_FEEDBACK_STRATEGY_ID_MISMATCH")
        if str(leg.get("frame") or "").upper() != str(leg.get("instance_timeframe") or "").upper():
            raise RuntimeError("R4_RUNTIME_FEEDBACK_TIMEFRAME_MISMATCH")

        order_rows = q("SELECT * FROM r4_order_intents WHERE leg_id=? LIMIT 2", (leg["leg_id"],))
        order = order_rows[0] if order_rows else {}
        if len(order_rows) > 1:
            raise RuntimeError("R4_RUNTIME_FEEDBACK_MULTIPLE_ENTRY_INTENTS")

        fill_select = "id,fill_id,intent_id,leg_id,exchange_order_id,qty_units,price_ticks,fee_text,ts"
        if has_fee_asset:
            fill_select += ",fee_asset"
        if has_realized:
            fill_select += ",realized_pnl_text"
        entry_fills = q(f"SELECT {fill_select} FROM r4_fills WHERE leg_id=? ORDER BY ts,id", (leg["leg_id"],))
        for row in entry_fills:
            row.setdefault("fee_asset", "")
            row.setdefault("realized_pnl_text", "")

        events = q("SELECT * FROM r4_leg_events WHERE leg_id=? ORDER BY ts,id", (leg["leg_id"],))
        exit_events = [row for row in events if str(row.get("event_type") or "") == "EXIT_FILL"]
        exit_details = [_json(row.get("detail_json")) for row in exit_events]
        reentry_events = [row for row in events if str(row.get("event_type") or "") in {"REENTRY_CREATED", "REENTRY_CANCELLED"}]

        exit_intents = q("SELECT * FROM r4_exit_intents WHERE leg_id=? ORDER BY created_ts,id", (leg["leg_id"],)) if _table_exists("r4_exit_intents") else []
        performance_events = q("SELECT * FROM r4_performance_events WHERE leg_id=? ORDER BY ts,id", (leg["leg_id"],)) if _table_exists("r4_performance_events") else []

        trailing = {}
        if _table_exists("r4_leg_trailing"):
            rows = q("SELECT * FROM r4_leg_trailing WHERE leg_id=?", (leg["leg_id"],))
            trailing = rows[0] if rows else {}

        policy = _json(leg.get("policy_snapshot"))
        adopted = str(policy.get("origin") or "").upper() == "LEGACY_ADOPTION"
        entry_id = _legacy_entry_id(policy, events) if adopted else str(leg.get("intent_id") or "")
        entry_id_source = "LEGACY_ENTRY_ID_EXACT_ADOPTION" if adopted else "R4_ENTRY_INTENT_ID"

        entry_qty = sum(int(row.get("qty_units") or 0) for row in entry_fills)
        entry_vwap = (
            sum(int(row.get("qty_units") or 0) * int(row.get("price_ticks") or 0) for row in entry_fills) // entry_qty
            if entry_qty > 0 else 0
        )
        if entry_vwap <= 0 and adopted:
            try:
                entry_vwap = int(policy.get("entry_price_ticks") or 0)
            except Exception:
                entry_vwap = 0

        exit_qty = sum(int(row.get("qty_units") or 0) for row in exit_events)
        exit_vwap = (
            sum(int(row.get("qty_units") or 0) * int(detail.get("price_ticks") or 0) for row, detail in zip(exit_events, exit_details)) // exit_qty
            if exit_qty > 0 else 0
        )

        reference_ticks = _entry_reference_ticks(policy)
        slippage_bps = None
        if reference_ticks and entry_vwap > 0:
            if str(leg.get("position_side") or "").upper() == "LONG":
                slippage_bps = (entry_vwap - reference_ticks) * 10000.0 / reference_ticks
            else:
                slippage_bps = (reference_ticks - entry_vwap) * 10000.0 / reference_ticks

        mfe_ticks = int(trailing.get("mfe_ticks") or 0) if has_mfe else 0
        mae_ticks = int(trailing.get("mae_ticks") or 0) if has_mae else 0
        mfe_bps = (mfe_ticks * 10000.0 / entry_vwap) if entry_vwap > 0 and (has_mfe and trailing) else None
        mae_bps = (mae_ticks * 10000.0 / entry_vwap) if entry_vwap > 0 and (has_mae and trailing) else None

        entry_ts = min((int(row.get("ts") or 0) for row in entry_fills if int(row.get("ts") or 0) > 0), default=0)
        exit_ts = max((int(row.get("ts") or 0) for row in exit_events if int(row.get("ts") or 0) > 0), default=0)
        holding_hours = ((exit_ts - entry_ts) / 3600.0) if entry_ts > 0 and exit_ts >= entry_ts else None
        if adopted and not entry_fills:
            holding_hours = None

        failure_rows = []
        if str(order.get("status") or "").upper() in {"REJECTED", "FAILED"}:
            failure_rows.append("ENTRY_" + str(order.get("status") or "").upper())
        for item in exit_intents:
            if str(item.get("status") or "").upper() in {"REJECTED", "FAILED"}:
                failure_rows.append("EXIT_" + str(item.get("status") or "").upper())

        funding = []
        for item in performance_events:
            if str(item.get("metric") or "").upper() == "FUNDING":
                funding.append(str(item.get("value_text") or "0"))

        exit_reasons = [str(detail.get("reason") or "") for detail in exit_details if str(detail.get("reason") or "")]
        reentry_result = []
        for item in reentry_events:
            reentry_result.append(
                {
                    "event_type": str(item.get("event_type") or ""),
                    "detail": _json(item.get("detail_json")),
                    "ts": int(item.get("ts") or 0),
                }
            )

        record = {
            "strategy_instance_id": instance_id,
            "strategy_id": str(leg.get("strategy_id") or ""),
            "timeframe": str(leg.get("frame") or "").upper(),
            "exact_strategy_version": exact_version,
            "strategy_policy_bundle_version": str(leg.get("instance_policy_bundle") or ""),
            "leg_id": str(leg.get("leg_id") or ""),
            "tier_at_entry": str(leg.get("tier") or "").upper(),
            "current_instance_tier": str(leg.get("instance_current_tier") or "").upper(),
            "symbol": str(leg.get("symbol") or "").upper(),
            "position_side": str(leg.get("position_side") or "").upper(),
            "status": str(leg.get("status") or "").upper(),
            "entry_id": entry_id or None,
            "entry_id_source": entry_id_source if entry_id else "UNAVAILABLE_NOT_FABRICATED",
            "order_id": str(order.get("exchange_order_id") or "") or None,
            "entry_fill_ids": [str(row.get("fill_id") or "") for row in entry_fills[-MAX_IDS_PER_LEG:]],
            "exit_fill_ids": [str(row.get("event_key") or "") for row in exit_events[-MAX_IDS_PER_LEG:]],
            "entry_fill_count": len(entry_fills),
            "exit_fill_count": len(exit_events),
            "entry_vwap_ticks": entry_vwap or None,
            "exit_vwap_ticks": exit_vwap or None,
            "fee_by_asset": _fee_totals(entry_fills, exit_details),
            "funding_values": funding[-MAX_IDS_PER_LEG:],
            "entry_reference_price_ticks": reference_ticks,
            "entry_slippage_bps": slippage_bps,
            "realized_pnl_text": _realized_pnl(entry_fills, exit_details),
            "realized_pnl_source": "BINANCE_ORDER_TRADE_UPDATE_RP" if _realized_pnl(entry_fills, exit_details) is not None else "UNAVAILABLE_NOT_FABRICATED",
            "mfe_ticks": mfe_ticks if (has_mfe and trailing) else None,
            "mae_ticks": mae_ticks if (has_mae and trailing) else None,
            "mfe_bps": mfe_bps,
            "mae_bps": mae_bps,
            "mfe_mae_scope": "POST_ADOPTION_RUNTIME_ONLY" if adopted else "R4_RUNTIME_LIFECYCLE",
            "holding_time_hours": holding_hours,
            "exit_reasons": exit_reasons[-MAX_IDS_PER_LEG:],
            "reentry_result": reentry_result[-MAX_IDS_PER_LEG:],
            "execution_failures": failure_rows[-MAX_IDS_PER_LEG:],
            "evidence_availability": {},
        }
        record["evidence_availability"] = {
            "ENTRY_ID": record["entry_id"] is not None,
            "ORDER_ID": record["order_id"] is not None,
            "FILLS": (record["entry_fill_count"] + record["exit_fill_count"]) > 0,
            "FEE": bool(record["fee_by_asset"]),
            "SLIPPAGE": record["entry_slippage_bps"] is not None,
            "REALIZED_PNL": record["realized_pnl_text"] is not None,
            "MAE": record["mae_bps"] is not None,
            "MFE": record["mfe_bps"] is not None,
            "HOLDING_TIME": record["holding_time_hours"] is not None,
            "STOP_EXIT_REENTRY_RESULT": bool(record["exit_reasons"] or record["reentry_result"]),
            "EXECUTION_FAILURE": True,
        }
        records.append(record)

    return records


def _latest_failed_tier_evidence(instance_id: str):
    rows = q(
        """
        SELECT evidence_name, passed, detail_json, tier, exact_version, ts
        FROM r4_tier_evidence
        WHERE instance_id=?
        ORDER BY ts DESC, id DESC
        LIMIT 128
        """,
        (instance_id,),
    )
    latest = {}
    for row in rows:
        name = str(row.get("evidence_name") or "").upper()
        if name and name not in latest:
            latest[name] = row
    return [row for row in latest.values() if int(row.get("passed") or 0) != 1]


def _tier_demotions(instance_id: str):
    if not _table_exists("r4_tier_events"):
        return []
    rank = {"CANARY": 1, "SMALL": 2, "NORMAL": 3}
    rows = q(
        "SELECT old_tier,new_tier,evidence_key,ts FROM r4_tier_events WHERE instance_id=? ORDER BY ts DESC,id DESC LIMIT 64",
        (instance_id,),
    )
    return [
        row for row in rows
        if rank.get(str(row.get("new_tier") or "").upper(), 0) < rank.get(str(row.get("old_tier") or "").upper(), 0)
    ]


def _instance_summaries(records):
    # R4_EXACT_INSTANCE_POSTMORTEM_V2
    grouped = defaultdict(list)

    for row in records:
        grouped[
            str(row.get("strategy_instance_id") or "")
        ].append(row)

    summaries = {}
    priority_reasons = defaultdict(list)

    for instance_id, rows in list(grouped.items())[:MAX_FEEDBACK_INSTANCES]:
        closed=[
            row
            for row in rows
            if row.get("status")=="CLOSED"
        ]

        realized=[
            _decimal(row.get("realized_pnl_text"))
            for row in closed
        ]

        realized=[
            value
            for value in realized
            if value is not None
        ]

        wins=sum(
            1 for value in realized
            if value>0
        )

        losses=sum(
            1 for value in realized
            if value<0
        )

        breakeven=sum(
            1 for value in realized
            if value==0
        )

        avg_realized=(
            sum(realized,Decimal("0"))
            / Decimal(len(realized))
            if realized
            else None
        )

        win_values=[
            value
            for value in realized
            if value>0
        ]

        loss_values=[
            value
            for value in realized
            if value<0
        ]

        avg_win=(
            sum(win_values,Decimal("0"))
            / Decimal(len(win_values))
            if win_values
            else None
        )

        avg_loss=(
            sum(loss_values,Decimal("0"))
            / Decimal(len(loss_values))
            if loss_values
            else None
        )

        payoff=None

        if (
            avg_win is not None
            and avg_loss is not None
            and avg_loss!=0
        ):
            payoff=float(
                avg_win/abs(avg_loss)
            )

        sample_n=len(realized)

        if sample_n>=30:
            sample_grade="MATURE_30_PLUS"
        elif sample_n>=10:
            sample_grade="DEVELOPING_10_29"
        elif sample_n>=5:
            sample_grade="THIN_5_9"
        else:
            sample_grade="VERY_THIN_LT5"

        failures=sum(
            len(row.get("execution_failures") or [])
            for row in rows
        )

        funding_event_count=sum(
            len(row.get("funding_values") or [])
            for row in rows
        )

        reentry_event_count=sum(
            len(row.get("reentry_result") or [])
            for row in rows
        )

        exit_reason_counts={}

        for row in rows:
            for reason in row.get("exit_reasons") or []:
                key=str(reason or "").strip() or "UNKNOWN"
                exit_reason_counts[key]=(
                    exit_reason_counts.get(key,0)+1
                )

        fee_by_asset_totals={}

        for row in rows:
            fees=row.get("fee_by_asset") or {}

            if not isinstance(fees,dict):
                continue

            for asset,value in fees.items():
                dec=_decimal(value)

                if dec is None:
                    continue

                key=str(asset or "").upper() or "UNKNOWN"

                fee_by_asset_totals[key]=(
                    fee_by_asset_totals.get(
                        key,
                        Decimal("0"),
                    )
                    +dec
                )

        failed_evidence = _latest_failed_tier_evidence(instance_id)
        demotions = _tier_demotions(instance_id)

        for evidence in failed_evidence:
            name=str(
                evidence.get("evidence_name")
                or ""
            ).upper()

            track=EVIDENCE_TRACK_MAP.get(name)

            if track:
                priority_reasons[track].append(
                    f"{instance_id}:{name}"
                )

        # ?? ??/?? ??? expensive research trigger? ???.
        if failures>=2 and len(rows)>=2:
            priority_reasons["EXECUTION"].append(
                f"{instance_id}:"
                f"REPEATED_EXECUTION_FAILURES={failures}"
            )

        if demotions:
            priority_reasons["STRATEGY"].append(
                f"{instance_id}:SERVER_TIER_DEMOTION"
            )

        summaries[instance_id] = {
            "strategy_instance_id": instance_id,
            "strategy_id": rows[0].get("strategy_id"),
            "timeframe": rows[0].get("timeframe"),
            "exact_strategy_version":
                rows[0].get("exact_strategy_version"),
            "strategy_policy_bundle_version":
                rows[0].get("strategy_policy_bundle_version"),
            "current_instance_tier":
                rows[0].get("current_instance_tier"),

            "leg_count": len(rows),
            "closed_leg_count": len(closed),

            "realized_pnl_sample_count": sample_n,
            "realized_pnl_total_text":
                str(sum(realized,Decimal("0")))
                if realized else None,
            "realized_expectancy_pnl_text":
                str(avg_realized)
                if avg_realized is not None else None,

            "win_count": wins,
            "loss_count": losses,
            "breakeven_count": breakeven,
            "win_rate":
                (wins/sample_n)
                if sample_n>0 else None,

            "avg_win_pnl_text":
                str(avg_win)
                if avg_win is not None else None,
            "avg_loss_pnl_text":
                str(avg_loss)
                if avg_loss is not None else None,
            "payoff_ratio": payoff,
            "sample_size_grade": sample_grade,

            "isolated_loss_priority_suppressed":
                bool(sample_n==1 and losses==1),

            "avg_entry_slippage_bps":
                _mean(
                    row.get("entry_slippage_bps")
                    for row in rows
                ),

            "avg_mfe_bps":
                _mean(
                    row.get("mfe_bps")
                    for row in rows
                ),

            "avg_mae_bps":
                _mean(
                    row.get("mae_bps")
                    for row in rows
                ),

            "avg_holding_time_hours":
                _mean(
                    row.get("holding_time_hours")
                    for row in closed
                ),

            "fee_by_asset_totals":{
                key:str(value)
                for key,value
                in sorted(fee_by_asset_totals.items())
            },

            "funding_event_count":
                funding_event_count,

            "reentry_event_count":
                reentry_event_count,

            "exit_reason_counts":
                dict(
                    sorted(
                        exit_reason_counts.items()
                    )
                ),

            "execution_failure_count":
                failures,

            "failed_tier_evidence":[
                {
                    "evidence_name":
                        str(
                            item.get("evidence_name")
                            or ""
                        ).upper(),
                    "tier":
                        str(
                            item.get("tier")
                            or ""
                        ).upper(),
                    "exact_version":
                        str(
                            item.get("exact_version")
                            or ""
                        ),
                    "detail":
                        _json(
                            item.get("detail_json")
                        ),
                    "ts":
                        int(item.get("ts") or 0),
                }
                for item in failed_evidence
            ],

            "tier_demotion_count":
                len(demotions),

            "material_live_drift":
                bool(
                    failed_evidence
                    or demotions
                    or (
                        failures>=2
                        and len(rows)>=2
                    )
                ),
        }

    hints={}

    for track,reasons in sorted(
        priority_reasons.items()
    ):
        unique=sorted(set(reasons))[-64:]

        hints[track]={
            "raise_priority":bool(unique),
            "reason_count":len(unique),
            "reasons":unique,
            "max_scheduler_multiplier":2.0,
            "direct_live_policy_effect":False,
        }

    return summaries,hints


def refresh_runtime_feedback():
    """현재 server_runtime snapshot을 exact-instance 연구 feedback으로 원자 갱신한다."""
    if not _r4_ready():
        payload = {
            "schema": "B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1",
            "status": "R4_RUNTIME_EVIDENCE_UNAVAILABLE",
            "updated_at": now(),
            "source_read_only": True,
            "server_write": False,
            "service_restart": False,
            "position_mutation": False,
            "required_evidence": R4_REQUIRED_EVIDENCE,
            "records": [],
            "instances": {},
            "priority_hints": {},
        }
        digest_base = {key: value for key, value in payload.items() if key != "updated_at"}
        payload["evidence_digest"] = _digest_payload(digest_base)
        atomic(RUNTIME_FEEDBACK, payload)
        return payload

    records = _r4_leg_records()
    summaries, hints = _instance_summaries(records)
    availability = {name: 0 for name in R4_REQUIRED_EVIDENCE}
    for row in records:
        for name, present in (row.get("evidence_availability") or {}).items():
            if present and name in availability:
                availability[name] += 1

    payload = {
        "schema": "B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1",
        "status": "PASS",
        "updated_at": now(),
        "source": str(DB),
        "source_read_only": True,
        "server_write": False,
        "service_restart": False,
        "position_mutation": False,
        "direct_live_policy_effect": False,
        "exact_strategy_instance_binding_required": True,
        "exact_strategy_version_binding_required": True,
        "required_evidence": R4_REQUIRED_EVIDENCE,
        "availability_counts": availability,
        "record_retention_limit": MAX_FEEDBACK_LEGS,
        "instance_retention_limit": MAX_FEEDBACK_INSTANCES,
        "records": records,
        "instances": summaries,
        "priority_hints": hints,
        "single_isolated_live_loss_expensive_research_trigger": False,
    }
    digest_base = {key: value for key, value in payload.items() if key not in {"updated_at", "evidence_digest"}}
    payload["evidence_digest"] = _digest_payload(digest_base)

    current = load(RUNTIME_FEEDBACK, {})
    if current.get("evidence_digest") == payload["evidence_digest"]:
        # 같은 snapshot은 파일을 다시 쓰지 않아 연구 input signature를 불필요하게 흔들지 않는다.
        return current

    atomic(RUNTIME_FEEDBACK, payload)
    return payload


# ================= Bounded evidence cursor =================
def run_next():
    feedback = refresh_runtime_feedback()
    state = load(OUT, {"schema": "B_BOT_V3_EVIDENCE_RESEARCH_V1", "tasks": {}, "cursor": 0})
    sig = signature()
    cursor = int(state.get("cursor") or 0) % len(TASKS)
    task = TASKS[cursor]
    previous = ((state.get("tasks") or {}).get(task) or {}).get("input_signature")
    if previous == sig:
        state["cursor"] = (cursor + 1) % len(TASKS)
        state["updated_at"] = now()
        atomic(OUT, state)
        return {
            "status": "NO_NEW_INPUT",
            "task": task,
            "runtime_feedback_digest": feedback.get("evidence_digest"),
        }

    fn = {
        "LIVE_STRATEGY_SUMMARY": live_summary,
        "EXIT_POLICY_SUMMARY": exit_policy,
        "MAJOR_OTHER_EXPLORATORY": major_other,
        "TF_CONFLICT_OBSERVATION": tf_conflict,
        "CAPITAL_TIME_REALIZED": capital_time,
    }[task]
    result = fn()
    state.setdefault("tasks", {})[task] = {
        "updated_at": now(),
        "input_signature": sig,
        "result": result,
        "policy_effect": "NONE_UNTIL_SEPARATE_VALIDATION",
        "runtime_feedback_digest": feedback.get("evidence_digest"),
    }
    state["cursor"] = (cursor + 1) % len(TASKS)
    state["updated_at"] = now()
    atomic(OUT, state)
    return {
        "status": "UPDATED",
        "task": task,
        "runtime_feedback_digest": feedback.get("evidence_digest"),
        "result_summary": {key: value for key, value in result.items() if key != "rows"},
    }


# ================= 메인 =================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-feedback", action="store_true")
    args = parser.parse_args()
    result = refresh_runtime_feedback() if args.runtime_feedback else run_next()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
