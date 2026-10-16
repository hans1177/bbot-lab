#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: strategy_leg_ledger.py
"""R4 Strategy Instance → Leg → Basket 권위 원장. 거래소 호출은 포함하지 않는다."""

# ================= 임포트 =================
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager, closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ================= 설정 =================
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
DB_FILE = Path(os.getenv("R4_LEDGER_DB", str(BASE / "state" / "history.db")))
BUSY_TIMEOUT_MS = 30000
VALID_TIERS = ("LOCAL_TEST", "LOCAL_SHADOW", "CANARY", "SMALL", "NORMAL")
TERMINAL_STATES = ("FILLED", "CANCELLED", "REJECTED", "FAILED")
TRANSITIONS = {
    "INTENT_SAVED": {"SUBMITTED", "AMBIGUOUS", "CANCELLED", "REJECTED", "FAILED"},
    "SUBMITTED": {"PARTIAL_FILLED", "FILLED", "AMBIGUOUS", "CANCELLED", "REJECTED", "FAILED"},
    "AMBIGUOUS": {"SUBMITTED", "PARTIAL_FILLED", "FILLED", "CANCELLED", "REJECTED", "FAILED"},
    "PARTIAL_FILLED": {"PARTIAL_FILLED", "FILLED", "AMBIGUOUS", "CANCELLED", "FAILED"},
    "FILLED": set(), "CANCELLED": set(), "REJECTED": set(), "FAILED": set(),
}


# ================= 유틸 =================
def now_ts(): return int(time.time())


def canonical_id(prefix, *parts):
    return prefix + hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()[:32]


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


# ================= 원장 =================
class StrategyLegLedger:
    def __init__(self, db_path=DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as con:
            self._upgrade_existing_schema(con)
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    con.execute(statement)

    def _upgrade_existing_schema(self, con):
        """이전 R4 시험 스키마를 같은 테이블에서 직접 승격한다."""
        def exists(table):
            return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())
        def columns(table):
            return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        def add(table, definition):
            name=definition.split()[0]
            if exists(table) and name not in columns(table): con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        # 파일명: strategy_leg_ledger.py / 이미 고정된 LEG 소유 정보는 재시작 때 보존한다.
        previous_leg_columns=columns("r4_active_legs") if exists("r4_active_legs") else set()
        add("r4_strategy_instances","trailing_policy_version TEXT NOT NULL DEFAULT 'LEGACY_TRAIL_POLICY'")
        for definition in (
            "strategy_id TEXT NOT NULL DEFAULT 'LEGACY_STRATEGY'",
            "strategy_version TEXT NOT NULL DEFAULT 'LEGACY_VERSION'",
            "frame TEXT NOT NULL DEFAULT 'LEGACY_FRAME'",
            "tier TEXT NOT NULL DEFAULT 'LOCAL_TEST'",
            "trailing_policy_version TEXT NOT NULL DEFAULT 'LEGACY_TRAIL_POLICY'",
        ): add("r4_active_legs",definition)
        add("r4_order_intents","filled_qty_units INTEGER NOT NULL DEFAULT 0")
        add("r4_order_intents","updated_ts INTEGER NOT NULL DEFAULT 0")
        add("r4_capacity_reservations","capacity_limit_units INTEGER NOT NULL DEFAULT 0")
        add("r4_fills","fee_asset TEXT NOT NULL DEFAULT ''")
        add("r4_fills","realized_pnl_text TEXT NOT NULL DEFAULT ''")
        for definition in (
            "entry_price_ticks INTEGER NOT NULL DEFAULT 0",
            "last_price_ticks INTEGER NOT NULL DEFAULT 0",
            "activated INTEGER NOT NULL DEFAULT 0",
            "policy_version TEXT NOT NULL DEFAULT ''",
            "mfe_ticks INTEGER NOT NULL DEFAULT 0",
            "mae_ticks INTEGER NOT NULL DEFAULT 0",
        ): add("r4_leg_trailing",definition)
        if exists("r4_order_intents"):
            con.execute("UPDATE r4_order_intents SET status='INTENT_SAVED' WHERE status='RESERVED'")
            con.execute("UPDATE r4_order_intents SET updated_ts=ts WHERE updated_ts=0")
        if exists("r4_capacity_reservations"):
            con.execute("UPDATE r4_capacity_reservations SET capacity_limit_units=qty_units WHERE capacity_limit_units=0")
        if exists("r4_active_legs") and exists("r4_strategy_instances"):
            mapping={"strategy_id":"strategy_id","strategy_version":"exact_version","frame":"timeframe","tier":"tier","trailing_policy_version":"trailing_policy_version"}
            assignments=[
                f"{field}=COALESCE((SELECT {owner_field} FROM r4_strategy_instances s WHERE s.instance_id=r4_active_legs.instance_id),{field})"
                for field,owner_field in mapping.items() if field not in previous_leg_columns
            ]
            if assignments:
                con.execute("UPDATE r4_active_legs SET "+",".join(assignments))

    def connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback(); raise
        finally: con.close()

    # ================= 단일 Writer / Basket Lock =================
    def acquire_writer(self, owner_id, lease_sec=30):
        now=now_ts(); expires=now+int(lease_sec)
        with self.transaction() as con:
            row=con.execute("SELECT owner_id,lease_expires_ts FROM r4_writer_lease WHERE lease_key='ACCOUNT_EXECUTION'").fetchone()
            if row and row["owner_id"]!=owner_id and int(row["lease_expires_ts"])>=now: raise RuntimeError("SINGLE_WRITER_ALREADY_OWNED")
            con.execute("INSERT INTO r4_writer_lease VALUES('ACCOUNT_EXECUTION',?,?,?) ON CONFLICT(lease_key) DO UPDATE SET owner_id=excluded.owner_id,lease_expires_ts=excluded.lease_expires_ts,updated_ts=excluded.updated_ts",(owner_id,expires,now))
        return expires

    def heartbeat_writer(self, owner_id, lease_sec=30):
        with self.transaction() as con:
            if con.execute("UPDATE r4_writer_lease SET lease_expires_ts=?,updated_ts=? WHERE lease_key='ACCOUNT_EXECUTION' AND owner_id=?",(now_ts()+int(lease_sec),now_ts(),owner_id)).rowcount!=1: raise RuntimeError("WRITER_LEASE_LOST")

    def release_writer(self, owner_id):
        with self.transaction() as con: con.execute("DELETE FROM r4_writer_lease WHERE lease_key='ACCOUNT_EXECUTION' AND owner_id=?",(owner_id,))

    def acquire_basket_lock(self, symbol, side, owner_id, lease_sec=15):
        now=now_ts(); key=f"{symbol}:{side}"
        with self.transaction() as con:
            row=con.execute("SELECT owner_id,lease_expires_ts FROM r4_basket_locks WHERE basket_key=?",(key,)).fetchone()
            if row and row["owner_id"]!=owner_id and int(row["lease_expires_ts"])>=now: raise RuntimeError("BASKET_LOCK_BUSY")
            con.execute("INSERT INTO r4_basket_locks VALUES(?,?,?,?) ON CONFLICT(basket_key) DO UPDATE SET owner_id=excluded.owner_id,lease_expires_ts=excluded.lease_expires_ts,updated_ts=excluded.updated_ts",(key,owner_id,now+int(lease_sec),now))

    def release_basket_lock(self, symbol, side, owner_id):
        with self.transaction() as con: con.execute("DELETE FROM r4_basket_locks WHERE basket_key=? AND owner_id=?",(f"{symbol}:{side}",owner_id))

    # ================= 전략 / 진입 상태기계 =================
    def register_instance(self, strategy_id, timeframe, exact_version, policy_bundle, tier="LOCAL_TEST", trailing_policy_version="", exact_instance_id=""):
        """헌법과 같은 canonical Strategy Instance ID로 등록하고 immutable identity를 검증한다."""
        strategy_id=str(strategy_id or "").upper().strip()
        timeframe=str(timeframe or "").upper().strip()
        exact_version=str(exact_version or "").strip()
        policy_bundle=str(policy_bundle or "").strip()
        trailing_policy_version=str(trailing_policy_version or "").strip()
        tier=str(tier or "").upper().strip()
        if tier not in VALID_TIERS or not all((strategy_id,timeframe,exact_version,policy_bundle,trailing_policy_version)):
            raise ValueError("INCOMPLETE_STRATEGY_INSTANCE_IDENTITY")
        instance_id=canonical_id("SI_",strategy_id,timeframe,exact_version,policy_bundle)
        if exact_instance_id and str(exact_instance_id)!=instance_id:
            raise RuntimeError("STRATEGY_INSTANCE_CANONICAL_ID_MISMATCH")
        with self.transaction() as con:
            current=con.execute("SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
            if current:
                immutable=(current["strategy_id"],current["timeframe"],current["exact_version"],current["policy_bundle"],current["trailing_policy_version"])
                expected=(strategy_id,timeframe,exact_version,policy_bundle,trailing_policy_version)
                if immutable!=expected:
                    raise RuntimeError("STRATEGY_INSTANCE_IMMUTABLE_IDENTITY_MISMATCH")
            else:
                con.execute("INSERT INTO r4_strategy_instances(instance_id,strategy_id,timeframe,exact_version,policy_bundle,trailing_policy_version,tier,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?)",(instance_id,strategy_id,timeframe,exact_version,policy_bundle,trailing_policy_version,tier,now_ts(),now_ts()))
        return instance_id

    def reserve_intent(
        self, instance_id, signal_id, symbol, side, qty_units, policy_snapshot,
        capacity_limit_units, tier_leg_limit=None, max_concurrent_legs=None,
    ):
        qty_units=int(qty_units); capacity_limit_units=int(capacity_limit_units)
        if side not in ("LONG","SHORT") or qty_units<=0 or capacity_limit_units<=0: raise ValueError("INVALID_SIDE_QTY_OR_CAPACITY")
        if max_concurrent_legs is not None:
            if tier_leg_limit is not None and int(tier_leg_limit) != int(max_concurrent_legs):
                raise ValueError("CONFLICTING_TIER_LEG_LIMITS")
            tier_leg_limit = max_concurrent_legs
        intent_id=canonical_id("EI_",instance_id,signal_id,symbol,side); leg_id=canonical_id("LEG_",intent_id); client_order_id=canonical_id("R4",intent_id)[:36]
        with self.transaction() as con:
            instance=con.execute("SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
            if not instance: raise KeyError("STRATEGY_INSTANCE_NOT_FOUND")
            existing=con.execute("SELECT * FROM r4_order_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if existing:
                return dict(existing)

            existing=con.execute(
                "SELECT * FROM r4_order_intents "
                "WHERE instance_id=? AND signal_id=? AND symbol=? AND position_side=?",
                (instance_id,signal_id,symbol,side),
            ).fetchone()

            if existing:
                legacy_adoption=con.execute(
                    "SELECT 1 FROM r4_leg_events "
                    "WHERE leg_id=? AND event_type='LEGACY_ADOPTION' LIMIT 1",
                    (existing["leg_id"],),
                ).fetchone()

                if legacy_adoption:
                    return dict(existing)

                raise RuntimeError(
                    "ENTRY_INTENT_IDEMPOTENCY_KEY_COLLISION"
                )
            if tier_leg_limit is not None:
                tier_leg_limit=int(tier_leg_limit)
                if tier_leg_limit<=0:
                    raise ValueError("INVALID_TIER_LEG_LIMIT")
                open_legs=int(con.execute(
                    "SELECT COUNT(*) FROM r4_active_legs l WHERE l.instance_id=? "
                    "AND (l.owned_qty_units>0 OR l.status='PENDING' "
                    "OR EXISTS (SELECT 1 FROM r4_order_intents i WHERE i.leg_id=l.leg_id "
                    "AND i.status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')) "
                    "OR EXISTS (SELECT 1 FROM r4_exit_intents e WHERE e.leg_id=l.leg_id "
                    "AND e.status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')))",
                    (instance_id,),
                ).fetchone()[0] or 0)
                if open_legs>=tier_leg_limit:
                    raise RuntimeError(
                        f"TIER_CONCURRENT_LEG_LIMIT:{open_legs}/{tier_leg_limit}"
                    )
            # 프레임별 독립 전략 최대 2개: 보유·진입 대기·청산 대기를 함께 계산한다.
            occupied = con.execute(
                "SELECT DISTINCT l.instance_id,l.strategy_id FROM r4_active_legs l "
                "WHERE UPPER(l.frame)=UPPER(?) AND (l.owned_qty_units>0 OR l.status='PENDING' "
                "OR EXISTS (SELECT 1 FROM r4_order_intents i WHERE i.leg_id=l.leg_id "
                "AND i.status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')) "
                "OR EXISTS (SELECT 1 FROM r4_exit_intents e WHERE e.leg_id=l.leg_id "
                "AND e.status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')))",
                (instance["timeframe"],),
            ).fetchall()
            occupied_ids = {str(row["instance_id"]) for row in occupied}
            occupied_strategies = {str(row["strategy_id"]) for row in occupied}
            if instance_id not in occupied_ids:
                if str(instance["strategy_id"]) in occupied_strategies:
                    raise RuntimeError("FRAME_STRATEGY_VERSION_OCCUPIED")
                if len(occupied_strategies) >= 2:
                    raise RuntimeError("FRAME_STRATEGY_SLOTS_FULL")
            mismatch=con.execute("SELECT mismatch FROM r4_basket_snapshots WHERE symbol=? AND position_side=?",(symbol,side)).fetchone()
            if mismatch and int(mismatch[0]): raise RuntimeError("BASKET_MISMATCH_BLOCKS_NEW_EXPOSURE")
            other="SHORT" if side=="LONG" else "LONG"
            opposite=int(con.execute("SELECT COALESCE(SUM(owned_qty_units),0) FROM r4_active_legs WHERE symbol=? AND position_side=? AND status='ACTIVE'",(symbol,other)).fetchone()[0])
            if opposite: raise RuntimeError("OPPOSITE_SIDE_MULTI_LEG_DISABLED")
            owned=int(con.execute("SELECT COALESCE(SUM(owned_qty_units),0) FROM r4_active_legs WHERE symbol=? AND position_side=? AND status='ACTIVE'",(symbol,side)).fetchone()[0])
            reserved=int(con.execute("SELECT COALESCE(SUM(qty_units),0) FROM r4_capacity_reservations WHERE symbol=? AND position_side=? AND status='ACTIVE'",(symbol,side)).fetchone()[0])
            if owned+reserved+qty_units>capacity_limit_units: raise RuntimeError("ATOMIC_CAPACITY_EXCEEDED")
            con.execute("INSERT INTO r4_active_legs(leg_id,instance_id,strategy_id,strategy_version,frame,tier,trailing_policy_version,intent_id,signal_id,symbol,position_side,owned_qty_units,entry_qty_units,exit_qty_units,policy_snapshot,status,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(leg_id,instance_id,instance["strategy_id"],instance["exact_version"],instance["timeframe"],instance["tier"],instance["trailing_policy_version"],intent_id,signal_id,symbol,side,0,0,0,canonical_json(policy_snapshot),"PENDING",now_ts(),now_ts()))
            con.execute("INSERT INTO r4_capacity_reservations(intent_id,symbol,position_side,qty_units,capacity_limit_units,status,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?)",(intent_id,symbol,side,qty_units,capacity_limit_units,"ACTIVE",now_ts(),now_ts()))
            con.execute("INSERT INTO r4_order_intents(intent_id,instance_id,leg_id,signal_id,symbol,position_side,requested_qty_units,filled_qty_units,client_order_id,status,ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(intent_id,instance_id,leg_id,signal_id,symbol,side,qty_units,0,client_order_id,"INTENT_SAVED",now_ts(),now_ts()))
            return dict(con.execute("SELECT * FROM r4_order_intents WHERE intent_id=?",(intent_id,)).fetchone())

    def _transition(self, con, intent_id, target, exchange_order_id=None):
        row=con.execute("SELECT status FROM r4_order_intents WHERE intent_id=?",(intent_id,)).fetchone()
        if not row: raise KeyError(intent_id)
        current=row[0]
        if target!=current and target not in TRANSITIONS.get(current,set()): raise RuntimeError(f"INVALID_INTENT_TRANSITION:{current}->{target}")
        con.execute("UPDATE r4_order_intents SET status=?,exchange_order_id=COALESCE(?,exchange_order_id),updated_ts=? WHERE intent_id=?",(target,exchange_order_id,now_ts(),intent_id))

    def mark_submitted(self, intent_id, exchange_order_id=None, ambiguous=False):
        with self.transaction() as con: self._transition(con,intent_id,"AMBIGUOUS" if ambiguous else "SUBMITTED",exchange_order_id)

    def claim_event(self, source, event_key, payload):
        with self.transaction() as con: return bool(con.execute("INSERT OR IGNORE INTO r4_event_dedupe VALUES(?,?,?,?)",(event_key,source,payload_hash(payload),now_ts())).rowcount)

    def confirm_fill(self, intent_id, fill_id, qty_units, price_ticks, fee_text="0", exchange_order_id=None, dedupe_source="", dedupe_event_key="", dedupe_payload=None, fee_asset="", realized_pnl_text="", economic_evidence=None):
        """Entry fill과 WebSocket dedupe를 같은 transaction에 commit해 crash gap을 없앤다."""
        qty_units=int(qty_units)
        if qty_units<=0:
            raise ValueError("FILL_QTY_MUST_BE_POSITIVE")
        with self.transaction() as con:
            intent=con.execute("SELECT * FROM r4_order_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if not intent:
                raise KeyError(intent_id)
            if con.execute("SELECT 1 FROM r4_fills WHERE fill_id=?",(str(fill_id),)).fetchone():
                return False
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(intent["leg_id"],)).fetchone()
            if not leg:
                raise RuntimeError("FILL_LEG_NOT_AVAILABLE")
            if self._duplicate_exchange_trade(
                con,leg,"ENTRY_FILL",fill_id,qty_units,price_ticks,fee_text,
                fee_asset,realized_pnl_text,exchange_order_id,economic_evidence,
            ):
                return False
            total=int(intent["filled_qty_units"])+qty_units
            if total>int(intent["requested_qty_units"]):
                raise RuntimeError("FILL_EXCEEDS_REQUESTED_QTY")
            con.execute(
                "INSERT INTO r4_fills(fill_id,intent_id,leg_id,exchange_order_id,qty_units,price_ticks,fee_text,ts,fee_asset,realized_pnl_text) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (str(fill_id),intent_id,intent["leg_id"],exchange_order_id,qty_units,int(price_ticks),str(fee_text),now_ts(),str(fee_asset or ""),str(realized_pnl_text or "")),
            )
            target="FILLED" if total==int(intent["requested_qty_units"]) else "PARTIAL_FILLED"
            self._transition(con,intent_id,target,exchange_order_id)
            con.execute("UPDATE r4_order_intents SET filled_qty_units=?,updated_ts=? WHERE intent_id=?",(total,now_ts(),intent_id))
            con.execute("UPDATE r4_active_legs SET owned_qty_units=owned_qty_units+?,entry_qty_units=entry_qty_units+?,status='ACTIVE',updated_ts=? WHERE leg_id=?",(qty_units,qty_units,now_ts(),intent["leg_id"]))
            remaining=int(intent["requested_qty_units"])-total
            con.execute("UPDATE r4_capacity_reservations SET qty_units=?,status=?,updated_ts=? WHERE intent_id=?",(remaining,"RELEASED" if remaining==0 else "ACTIVE",now_ts(),intent_id))
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(intent["leg_id"],)).fetchone()
            self._record_fill_evidence(
                con,leg,"ENTRY_FILL",fill_id,qty_units,price_ticks,
                fee_text,fee_asset,realized_pnl_text,exchange_order_id,
                dedupe_source,economic_evidence,
            )
            if dedupe_source and dedupe_event_key:
                con.execute(
                    "INSERT OR IGNORE INTO r4_event_dedupe(event_key,source,payload_hash,ts) VALUES(?,?,?,?)",
                    (str(dedupe_event_key),str(dedupe_source),payload_hash(dedupe_payload if dedupe_payload is not None else {}),now_ts()),
                )
        return True

    def _duplicate_exchange_trade(
        self, con, leg, kind, fill_id, qty_units, price_ticks, fee_text,
        fee_asset, realized_pnl_text, exchange_order_id, economic_evidence,
    ):
        evidence=economic_evidence if isinstance(economic_evidence,dict) else {}
        trade_id=str(evidence.get("trade_id") or "").strip()
        if not trade_id:
            return False
        row=con.execute(
            "SELECT * FROM r4_trade_evidence WHERE symbol=? AND trade_id=?",
            (leg["symbol"],trade_id),
        ).fetchone()
        if not row:
            return False

        def normalized(value):
            text=str(value or "").strip()
            if not text:
                return ""
            try:
                number=Decimal(text)
                return str(number.normalize()) if number.is_finite() else text
            except (ArithmeticError,TypeError,ValueError):
                return text

        candidate={
            "kind":str(kind),
            "leg_id":str(leg["leg_id"]),
            "instance_id":str(leg["instance_id"]),
            "exact_version":str(leg["strategy_version"]),
            "order_id":str(evidence.get("order_id") or exchange_order_id or ""),
            "exchange_ts":int(evidence.get("exchange_ts") or 0),
            "qty_units":int(qty_units),
            "price_ticks":int(price_ticks),
            "qty_text":str(evidence.get("qty_text") or ""),
            "price_text":str(evidence.get("price_text") or ""),
            "fee_text":str(evidence.get("fee_text") if evidence.get("fee_text") is not None else fee_text or ""),
            "fee_asset":str(evidence.get("fee_asset") or fee_asset or "").upper(),
            "realized_pnl_text":str(evidence.get("realized_pnl_text") if evidence.get("realized_pnl_text") is not None else realized_pnl_text or ""),
            "realized_pnl_asset":str(evidence.get("realized_pnl_asset") or "").upper(),
        }
        exact_fields=("kind","leg_id","instance_id","exact_version","order_id","exchange_ts","qty_units","price_ticks","fee_asset","realized_pnl_asset")
        numeric_fields=("qty_text","price_text","fee_text","realized_pnl_text")
        if all(str(row[field])==str(candidate[field]) for field in exact_fields) and all(
            normalized(row[field])==normalized(candidate[field]) for field in numeric_fields
        ):
            return True
        raise RuntimeError("EXCHANGE_TRADE_ID_PAYLOAD_CONFLICT:"+trade_id)

    def _record_fill_evidence(
        self, con, leg, kind, fill_id, qty_units, price_ticks,
        fee_text, fee_asset, realized_pnl_text, exchange_order_id, source, evidence,
    ):
        evidence=evidence if isinstance(evidence,dict) else {}
        try:
            policy=json.loads(leg["policy_snapshot"] or "{}")
        except (TypeError,ValueError):
            policy={}
        if not isinstance(policy,dict):
            policy={}
        trade_id=str(evidence.get("trade_id") or "")
        order_id=str(evidence.get("order_id") or exchange_order_id or "")
        raw_qty=str(evidence.get("qty_text") or "")
        raw_price=str(evidence.get("price_text") or "")
        raw_fee=str(evidence.get("fee_text") if evidence.get("fee_text") is not None else fee_text or "")
        raw_fee_asset=str(evidence.get("fee_asset") or fee_asset or "").upper()
        raw_realized=str(evidence.get("realized_pnl_text") if evidence.get("realized_pnl_text") is not None else realized_pnl_text or "")
        realized_asset=str(evidence.get("realized_pnl_asset") or "").upper()
        exchange_ts=int(evidence.get("exchange_ts") or 0)
        event_source=str(evidence.get("source") or source or "")
        source_event_id=str(evidence.get("source_event_id") or fill_id)
        fee_value=str(evidence.get("fee_value_usdt_text") or "")
        fee_valuation_source=str(evidence.get("fee_valuation_source") or "")
        if raw_fee and raw_fee_asset=="USDT" and not fee_value:
            fee_value=raw_fee
            fee_valuation_source="BINANCE_FEE_ASSET_IS_QUOTE"
        leverage_x=str(policy.get("verified_exchange_leverage_x") or "")
        leverage_source=str(policy.get("leverage_verification_source") or "")
        capital_basis=""
        capital_basis_source=""
        try:
            if kind=="ENTRY_FILL" and leverage_x and leverage_source and raw_qty and raw_price:
                capital_basis=str(Decimal(raw_qty)*Decimal(raw_price)/Decimal(leverage_x))
                capital_basis_source="ACTUAL_ENTRY_NOTIONAL_DIVIDED_BY_VERIFIED_EXCHANGE_LEVERAGE"
        except (ArithmeticError,TypeError,ValueError):
            capital_basis=""
        complete=bool(
            event_source.startswith("BINANCE") and source_event_id and trade_id and order_id
            and exchange_ts>0 and raw_qty and raw_price and raw_fee and raw_fee_asset
            and fee_value and fee_valuation_source and raw_realized.strip() and realized_asset
            and (kind!="ENTRY_FILL" or (capital_basis and leverage_source))
        )
        event_key=canonical_id("R4E_",leg["leg_id"],kind,trade_id or source_event_id)
        payload={
            "kind":kind,"leg_id":leg["leg_id"],"instance_id":leg["instance_id"],
            "exact_version":leg["strategy_version"],"source":event_source,
            "source_event_id":source_event_id,"trade_id":trade_id,"order_id":order_id,
            "symbol":leg["symbol"],"position_side":leg["position_side"],
            "exchange_ts":exchange_ts,"qty_text":raw_qty,"price_text":raw_price,
            "fee_text":raw_fee,"fee_asset":raw_fee_asset,
            "realized_pnl_text":raw_realized,"realized_pnl_asset":realized_asset,
            "capital_basis_usdt":capital_basis,
        }
        con.execute(
            "INSERT INTO r4_trade_evidence(event_key,leg_id,instance_id,strategy_id,exact_version,kind,source,source_event_id,trade_id,order_id,symbol,position_side,qty_units,price_ticks,qty_text,price_text,realized_pnl_text,realized_pnl_asset,fee_text,fee_asset,fee_value_usdt_text,fee_valuation_source,leverage_x,leverage_source,capital_basis_usdt,capital_basis_source,attribution_basis,attribution_evidence_key,funding_text,funding_asset,funding_value_usdt_text,funding_valuation_source,evidence_complete,hard_fail,payload_hash,exchange_ts,ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_key,leg["leg_id"],leg["instance_id"],leg["strategy_id"],leg["strategy_version"],
                kind,event_source,source_event_id,trade_id,order_id,leg["symbol"],leg["position_side"],
                int(qty_units),int(price_ticks),raw_qty,raw_price,raw_realized,realized_asset,
                raw_fee,raw_fee_asset,fee_value,fee_valuation_source,leverage_x,leverage_source,
                capital_basis,capital_basis_source,"","", "", "", "", "",
                int(complete),int(bool(evidence.get("hard_fail"))),payload_hash(payload),exchange_ts,now_ts(),
            ),
        )

    def cancel_intent(self, intent_id, reason, rejected=False):
        target="REJECTED" if rejected else "CANCELLED"
        with self.transaction() as con:
            self._transition(con,intent_id,target)
            con.execute("UPDATE r4_capacity_reservations SET qty_units=0,status='RELEASED',updated_ts=? WHERE intent_id=?",(now_ts(),intent_id))
            con.execute("UPDATE r4_active_legs SET status=CASE WHEN owned_qty_units=0 THEN 'CANCELLED' ELSE 'ACTIVE' END,updated_ts=? WHERE intent_id=?",(now_ts(),intent_id))

    # ================= 청산 / Trailing / Reconcile =================
    def exit_leg(self, leg_id, qty_units, fill_id, price_ticks, reason, fee_text="0"):
        qty_units=int(qty_units)
        with self.transaction() as con:
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not leg or qty_units<=0 or qty_units>int(leg["owned_qty_units"]): raise RuntimeError("EXIT_EXCEEDS_LEG_OWNED_QTY")
            inserted=con.execute("INSERT OR IGNORE INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",(fill_id,leg_id,"EXIT_FILL",qty_units,canonical_json({"price_ticks":int(price_ticks),"reason":reason,"fee_text":str(fee_text)}),now_ts())).rowcount
            if inserted:
                remaining=int(leg["owned_qty_units"])-qty_units
                con.execute("UPDATE r4_active_legs SET owned_qty_units=?,exit_qty_units=exit_qty_units+?,status=?,updated_ts=? WHERE leg_id=?",(remaining,qty_units,"CLOSED" if remaining==0 else "ACTIVE",now_ts(),leg_id))
        return bool(inserted)

    def reserve_exit(self, leg_id, exit_signal_id, qty_units, reason):
        qty_units=int(qty_units); exit_intent_id=canonical_id("XI_",leg_id,exit_signal_id); client_order_id=canonical_id("R4X",exit_intent_id)[:36]
        with self.transaction() as con:
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not leg or qty_units<=0 or qty_units>int(leg["owned_qty_units"]): raise RuntimeError("EXIT_EXCEEDS_LEG_OWNED_QTY")
            pending=int(con.execute("SELECT COALESCE(SUM(requested_qty_units-filled_qty_units),0) FROM r4_exit_intents WHERE leg_id=? AND status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')",(leg_id,)).fetchone()[0])
            if pending+qty_units>int(leg["owned_qty_units"]): raise RuntimeError("EXIT_RESERVATION_EXCEEDS_LEG_OWNED_QTY")
            con.execute("INSERT OR IGNORE INTO r4_exit_intents(exit_intent_id,leg_id,exit_signal_id,requested_qty_units,filled_qty_units,client_order_id,status,reason,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",(exit_intent_id,leg_id,exit_signal_id,qty_units,0,client_order_id,"INTENT_SAVED",reason,now_ts(),now_ts()))
            return dict(con.execute("SELECT * FROM r4_exit_intents WHERE exit_intent_id=?",(exit_intent_id,)).fetchone())

    def mark_exit_submitted(self, exit_intent_id, exchange_order_id="", ambiguous=False):
        with self.transaction() as con:
            con.execute("UPDATE r4_exit_intents SET status=?,exchange_order_id=COALESCE(NULLIF(?,''),exchange_order_id),updated_ts=? WHERE exit_intent_id=?",("AMBIGUOUS" if ambiguous else "SUBMITTED",exchange_order_id,now_ts(),exit_intent_id))

    def confirm_exit_fill(self, exit_intent_id, fill_id, qty_units, price_ticks, fee_text="0", fee_asset="", realized_pnl_text="", exchange_order_id=None, economic_evidence=None):
        """Exit fill event, Leg owned qty, exit intent progress를 한 SQLite transaction으로 commit한다."""
        qty_units=int(qty_units)
        if qty_units<=0:
            raise ValueError("EXIT_FILL_QTY_MUST_BE_POSITIVE")
        with self.transaction() as con:
            row=con.execute("SELECT * FROM r4_exit_intents WHERE exit_intent_id=?",(exit_intent_id,)).fetchone()
            if not row:
                raise KeyError(exit_intent_id)
            existing=con.execute("SELECT event_type FROM r4_leg_events WHERE event_key=?",(str(fill_id),)).fetchone()
            if existing:
                return False
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(row["leg_id"],)).fetchone()
            if not leg or leg["status"] not in {"ACTIVE","CLOSED"}:
                raise RuntimeError("EXIT_FILL_LEG_NOT_AVAILABLE")
            if self._duplicate_exchange_trade(
                con,leg,"EXIT_FILL",fill_id,qty_units,price_ticks,fee_text,
                fee_asset,realized_pnl_text,exchange_order_id,economic_evidence,
            ):
                return False
            total=int(row["filled_qty_units"])+qty_units
            if total>int(row["requested_qty_units"]):
                raise RuntimeError("EXIT_FILL_EXCEEDS_REQUEST")
            if qty_units>int(leg["owned_qty_units"]):
                raise RuntimeError("EXIT_EXCEEDS_LEG_OWNED_QTY")
            remaining=int(leg["owned_qty_units"])-qty_units
            detail={
                "price_ticks":int(price_ticks),
                "reason":str(row["reason"]),
                "fee_text":str(fee_text),
                "fee_asset":str(fee_asset or ""),
                "realized_pnl_text":str(realized_pnl_text or ""),
                "exit_intent_id":str(exit_intent_id),
                "exchange_order_id":str(exchange_order_id or ""),
            }
            con.execute(
                "INSERT INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                (str(fill_id),row["leg_id"],"EXIT_FILL",qty_units,canonical_json(detail),now_ts()),
            )
            con.execute(
                "UPDATE r4_active_legs SET owned_qty_units=?,exit_qty_units=exit_qty_units+?,status=?,updated_ts=? WHERE leg_id=?",
                (remaining,qty_units,"CLOSED" if remaining==0 else "ACTIVE",now_ts(),row["leg_id"]),
            )
            con.execute(
                "UPDATE r4_exit_intents SET filled_qty_units=?,status=?,updated_ts=? WHERE exit_intent_id=?",
                (total,"FILLED" if total==int(row["requested_qty_units"]) else "PARTIAL_FILLED",now_ts(),exit_intent_id),
            )
            self._record_fill_evidence(
                con,leg,"EXIT_FILL",fill_id,qty_units,price_ticks,
                fee_text,fee_asset,realized_pnl_text,exchange_order_id,
                str((economic_evidence or {}).get("source") or ""),economic_evidence,
            )
        return True

    def confirm_leverage_for_leg(self, leg_id, expected_leverage, actual_leverage, source):
        expected=int(expected_leverage)
        actual=int(actual_leverage)
        source=str(source or "").strip()
        if expected<=0 or actual!=expected or not source:
            raise ValueError("VERIFIED_EXCHANGE_LEVERAGE_REQUIRED")
        with self.transaction() as con:
            row=con.execute("SELECT policy_snapshot FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not row:
                raise KeyError(leg_id)
            try:
                policy=json.loads(row["policy_snapshot"] or "{}")
            except (TypeError,ValueError) as exc:
                raise RuntimeError("LEG_POLICY_SNAPSHOT_INVALID") from exc
            if not isinstance(policy,dict):
                raise RuntimeError("LEG_POLICY_SNAPSHOT_INVALID")
            policy["verified_exchange_leverage_x"]=actual
            policy["leverage_verification_source"]=source
            policy["leverage_verified_ts"]=now_ts()
            con.execute(
                "UPDATE r4_active_legs SET policy_snapshot=?,updated_ts=? WHERE leg_id=?",
                (canonical_json(policy),now_ts(),leg_id),
            )
        return actual

    def confirm_external_reduction(self, leg_id, expected_owned_units, exchange_units, fills):
        """거래소가 증명한 외부 청산을 해당 Basket의 모든 Leg에 원자적으로 반영한다.

        부분 청산은 단일 Leg에서만 허용한다. 여러 Leg는 거래소 Basket이 완전히
        비어 있고 모든 진입/청산 intent가 종결되어 있어야만 전량 종료한다.
        한 거래소 체결을 Leg에 나누는 경우 정확한 체결 수량만 FIFO로 귀속한다.
        실현손익/수수료의 전략별 배분은 증거가 없어 수행하지 않는다.
        """
        ids = [str(x) for x in (leg_id if isinstance(leg_id, (list, tuple)) else [leg_id])]
        if not ids or len(set(ids)) != len(ids):
            raise RuntimeError("MANUAL_EXIT_LEG_IDS_INVALID")
        expected_owned_units = int(expected_owned_units)
        exchange_units = int(exchange_units)
        if expected_owned_units <= 0 or exchange_units < 0 or exchange_units >= expected_owned_units or not fills:
            raise RuntimeError("MANUAL_EXIT_INVALID_QTY")
        with self.transaction() as con:
            legs = [con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?", (x,)).fetchone() for x in ids]
            if any(x is None or x["status"] != "ACTIVE" for x in legs):
                raise RuntimeError("MANUAL_EXIT_LEG_CHANGED")
            symbol, side = legs[0]["symbol"], legs[0]["position_side"]
            if any(x["symbol"] != symbol or x["position_side"] != side for x in legs):
                raise RuntimeError("MANUAL_EXIT_BASKET_MIXED")
            active = con.execute(
                "SELECT * FROM r4_active_legs WHERE symbol=? AND position_side=? AND status='ACTIVE' ORDER BY created_ts,leg_id",
                (symbol, side),
            ).fetchall()
            if {x["leg_id"] for x in active} != set(ids):
                raise RuntimeError("MANUAL_EXIT_BASKET_LEG_SET_CHANGED")
            owned = sum(int(x["owned_qty_units"]) for x in active)
            if owned != expected_owned_units:
                raise RuntimeError("MANUAL_EXIT_QTY_CHANGED")
            if len(active) > 1 and exchange_units != 0:
                raise RuntimeError("MANUAL_EXIT_MULTI_LEG_PARTIAL_AMBIGUOUS")
            for leg in active:
                intent = con.execute("SELECT status FROM r4_order_intents WHERE leg_id=?", (leg["leg_id"],)).fetchone()
                if not intent or intent["status"] not in {"FILLED", "CANCELLED"}:
                    raise RuntimeError("MANUAL_EXIT_ENTRY_UNSETTLED")
                pending = con.execute(
                    "SELECT COUNT(*) FROM r4_exit_intents WHERE leg_id=? AND status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')",
                    (leg["leg_id"],),
                ).fetchone()[0]
                if pending:
                    raise RuntimeError("MANUAL_EXIT_PENDING_EXIT")
            fills_sorted = sorted(fills, key=lambda x: (int(x["trade_time_ms"]), str(x["trade_id"])))
            trade_ids = set()
            allocations = []
            remaining = {x["leg_id"]: int(x["owned_qty_units"]) for x in active}
            first_leg = active[0]["leg_id"]
            for fill in fills_sorted:
                trade_id = str(fill.get("trade_id") or "")
                order_id = str(fill.get("order_id") or "")
                qty = int(fill.get("qty_units") or 0)
                ticks = int(fill.get("price_ticks") or 0)
                t_ms = int(fill.get("trade_time_ms") or 0)
                if not trade_id or not order_id or qty <= 0 or ticks <= 0 or t_ms <= 0:
                    raise RuntimeError("MANUAL_EXIT_FILL_EVIDENCE_INVALID")
                if trade_id in trade_ids:
                    raise RuntimeError("MANUAL_EXIT_DUPLICATE_TRADE")
                trade_ids.add(trade_id)
                # 여러 Leg를 닫는 체결은 마지막 진입 이후여야 한다.
                if len(active) > 1 and t_ms < max(int(x["created_ts"]) for x in active)*1000:
                    raise RuntimeError("MANUAL_EXIT_MULTI_LEG_TRADE_BEFORE_LAST_ENTRY")
                existing = con.execute(
                    "SELECT 1 FROM r4_leg_events WHERE event_key=? OR event_key LIKE ? LIMIT 1",
                    (f"EXTERNAL_TRADE:{symbol}:{trade_id}", f"EXTERNAL_TRADE:{symbol}:{trade_id}:%"),
                ).fetchone()
                if existing:
                    # 단일 Leg 부분 청산 후 재조회 시 과거 체결은 멱등 무시한다.
                    if len(active) != 1:
                        raise RuntimeError("MANUAL_EXIT_TRADE_ALREADY_RECORDED")
                    prior = con.execute(
                        "SELECT leg_id,event_type FROM r4_leg_events WHERE event_key=?",
                        (f"EXTERNAL_TRADE:{symbol}:{trade_id}",),
                    ).fetchone()
                    if not prior or prior["leg_id"] != first_leg or prior["event_type"] != "EXIT_FILL":
                        raise RuntimeError("MANUAL_EXIT_TRADE_ALREADY_RECORDED")
                    continue
                left = qty
                for leg in active:
                    lid = leg["leg_id"]
                    part = min(left, remaining[lid])
                    if part:
                        allocations.append((lid, fill, part, ticks, len(active) == 1))
                        remaining[lid] -= part
                        left -= part
                    if left == 0:
                        break
                if left:
                    raise RuntimeError("MANUAL_EXIT_OVER_ALLOCATED")
            diff = expected_owned_units - exchange_units
            if sum(x[2] for x in allocations) != diff:
                raise RuntimeError("MANUAL_EXIT_QTY_NOT_PROVEN")
            if len(active) > 1 and any(remaining.values()):
                raise RuntimeError("MANUAL_EXIT_MULTI_LEG_NOT_FULLY_PROVEN")
            now = now_ts()
            audited_trades = set()
            for lid, fill, qty, ticks, single in allocations:
                trade_id = str(fill["trade_id"])
                key = f"EXTERNAL_TRADE:{symbol}:{trade_id}" if single else f"EXTERNAL_TRADE:{symbol}:{trade_id}:{lid}"
                detail = {
                    "reason": "MANUAL_EXTERNAL_EXIT",
                    "source": "BINANCE_ACCOUNT_TRADES_AND_ORDERS",
                    "exchange_order_id": str(fill["order_id"]),
                    "trade_id": trade_id,
                    "trade_time_ms": int(fill["trade_time_ms"]),
                    "price_ticks": ticks,
                }
                if single:
                    # 기존 단일 Leg 원장 손익/수수료 계약 그대로 유지.
                    detail.update({
                        "fee_text": str(fill.get("fee_text") or "0"),
                        "fee_asset": str(fill.get("fee_asset") or ""),
                        "realized_pnl_text": str(fill.get("realized_pnl_text") or "0"),
                    })
                else:
                    detail.update({
                        "allocation": "FULL_BASKET_FIFO_QUANTITY_ONLY",
                        "trade_total_qty_units": int(fill["qty_units"]),
                        "per_leg_pnl_allocated": False,
                    })
                    # 다중 Leg 거래소 총 실현손익/수수료는 첫 조각에 한 번만 감사 기록.
                    # 임의로 Leg별 수익으로 나누지 않는다.
                    if trade_id not in audited_trades:
                        detail.update({
                            "exchange_trade_fee_unallocated": str(fill.get("fee_text") or "0"),
                            "exchange_trade_realized_pnl_unallocated": str(fill.get("realized_pnl_text") or "0"),
                            "fee_asset": str(fill.get("fee_asset") or ""),
                        })
                        audited_trades.add(trade_id)
                con.execute(
                    "INSERT INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                    (key, lid, "EXIT_FILL", qty, canonical_json(detail), now),
                )
                if single:
                    self._record_fill_evidence(
                        con,
                        next(x for x in active if x["leg_id"] == lid),
                        "EXIT_FILL",
                        trade_id,
                        qty,
                        ticks,
                        fill.get("fee_text"),
                        fill.get("fee_asset"),
                        fill.get("realized_pnl_text"),
                        fill.get("order_id"),
                        "BINANCE_ACCOUNT_TRADES_AND_ORDERS",
                        {
                            "source": "BINANCE_ACCOUNT_TRADES_AND_ORDERS",
                            "source_event_id": f"EXTERNAL_TRADE:{symbol}:{trade_id}",
                            "trade_id": trade_id,
                            "order_id": fill.get("order_id"),
                            "exchange_ts": int(fill["trade_time_ms"]),
                            "qty_text": fill.get("qty_text") or "",
                            "price_text": fill.get("price_text") or "",
                            "fee_text": fill.get("fee_text"),
                            "fee_asset": fill.get("fee_asset"),
                            "realized_pnl_text": fill.get("realized_pnl_text"),
                            "realized_pnl_asset": fill.get("realized_pnl_asset"),
                        },
                    )
            for leg in active:
                lid = leg["leg_id"]
                reduced = int(leg["owned_qty_units"]) - remaining[lid]
                con.execute(
                    "UPDATE r4_active_legs SET owned_qty_units=?,exit_qty_units=exit_qty_units+?,status=?,updated_ts=? WHERE leg_id=?",
                    (remaining[lid], reduced, "CLOSED" if remaining[lid] == 0 else "ACTIVE", now, lid),
                )
                if remaining[lid] == 0:
                    reason = {"reason": "MANUAL_EXTERNAL_EXIT", "source": "BINANCE_ACCOUNT_TRADES_AND_ORDERS"}
                    con.execute(
                        "INSERT OR IGNORE INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                        (canonical_id("REENTRY_CANCEL_", lid, "MANUAL_EXTERNAL_EXIT"), lid, "REENTRY_CANCELLED", 0, canonical_json(reason), now),
                    )
        return {"ok": True, "leg_ids": ids, "recovered_qty_units": diff, "remaining_qty_units": exchange_units}

    def cancel_exit_intent(self, exit_intent_id, reason="EXCHANGE_CANCELLED", rejected=False):
        target="REJECTED" if rejected else "CANCELLED"
        with self.transaction() as con:
            row=con.execute("SELECT status FROM r4_exit_intents WHERE exit_intent_id=?",(exit_intent_id,)).fetchone()
            if not row:
                raise KeyError(exit_intent_id)
            if row["status"] in TERMINAL_STATES:
                return False
            con.execute("UPDATE r4_exit_intents SET status=?,reason=?,updated_ts=? WHERE exit_intent_id=?",(target,str(reason or target),now_ts(),exit_intent_id))
        return True

    def reconcile(self, symbol, side, exchange_qty_units, source_event_id="", dedupe_source="", dedupe_payload=None):
        """Basket reconcile과 WebSocket event dedupe를 같은 transaction에 commit한다."""
        symbol=str(symbol or "").upper()
        side=str(side or "").upper()
        exchange_qty_units=int(exchange_qty_units)
        with self.transaction() as con:
            if dedupe_source and source_event_id:
                prior=con.execute("SELECT 1 FROM r4_event_dedupe WHERE event_key=?",(str(source_event_id),)).fetchone()
                if prior:
                    snapshot=con.execute("SELECT * FROM r4_basket_snapshots WHERE symbol=? AND position_side=?",(symbol,side)).fetchone()
                    if snapshot:
                        return {"ok":int(snapshot["mismatch"])==0,"ledger_qty_units":int(snapshot["ledger_qty_units"]),"exchange_qty_units":int(snapshot["exchange_qty_units"]),"deduped":True}
            owned=int(con.execute("SELECT COALESCE(SUM(owned_qty_units),0) FROM r4_active_legs WHERE symbol=? AND position_side=? AND status='ACTIVE'",(symbol,side)).fetchone()[0])
            mismatch=owned!=exchange_qty_units
            con.execute("INSERT INTO r4_basket_snapshots VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,position_side) DO UPDATE SET ledger_qty_units=excluded.ledger_qty_units,exchange_qty_units=excluded.exchange_qty_units,mismatch=excluded.mismatch,updated_ts=excluded.updated_ts",(symbol,side,owned,exchange_qty_units,int(mismatch),now_ts()))
            key=str(source_event_id or canonical_id("REC_",symbol,side,owned,exchange_qty_units,now_ts()))
            con.execute("INSERT OR IGNORE INTO r4_reconciliation_events VALUES(NULL,?,?,?,?,?,?,?)",(key,symbol,side,owned,exchange_qty_units,"MISMATCH" if mismatch else "PASS",now_ts()))
            if dedupe_source and source_event_id:
                con.execute(
                    "INSERT OR IGNORE INTO r4_event_dedupe(event_key,source,payload_hash,ts) VALUES(?,?,?,?)",
                    (str(source_event_id),str(dedupe_source),payload_hash(dedupe_payload if dedupe_payload is not None else {}),now_ts()),
                )
        return {"ok":not mismatch,"ledger_qty_units":owned,"exchange_qty_units":exchange_qty_units,"deduped":False}

    # ================= Legacy Position Adoption / Cutover =================
    def record_cutover_event(self, phase, result, detail=None, event_key=""):
        """Cutover 증거를 멱등 저장한다. 주문/포지션 변경은 수행하지 않는다."""
        phase=str(phase or "").upper().strip()
        result=str(result or "").upper().strip()
        detail=detail if isinstance(detail,dict) else {}
        if not phase or result not in {"PASS","BLOCK","ABORT","INFO"}:
            raise ValueError("INVALID_CUTOVER_EVENT")
        key=str(event_key or "").strip() or canonical_id("CUT_",phase,result,payload_hash(detail))
        with self.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO r4_cutover_events(event_key,phase,result,detail_json,ts) VALUES(?,?,?,?,?)",
                (key,phase,result,canonical_json(detail),now_ts()),
            )
            row=con.execute("SELECT * FROM r4_cutover_events WHERE event_key=?",(key,)).fetchone()
        out=dict(row) if row else {}
        if out:
            try:
                out["detail"]=json.loads(out.pop("detail_json"))
            except Exception:
                out["detail"]={}
        return out

    def latest_cutover_event(self, phase=""):
        phase=str(phase or "").upper().strip()
        with closing(self.connect()) as con:
            if phase:
                row=con.execute(
                    "SELECT * FROM r4_cutover_events WHERE phase=? ORDER BY ts DESC,id DESC LIMIT 1",
                    (phase,),
                ).fetchone()
            else:
                row=con.execute("SELECT * FROM r4_cutover_events ORDER BY ts DESC,id DESC LIMIT 1").fetchone()
        if not row:
            return {}
        out=dict(row)
        try:
            out["detail"]=json.loads(out.pop("detail_json"))
        except Exception:
            out["detail"]={}
        return out

    def validate_cutover_token(self, token):
        """기존 전환 증거 또는 명시적 운영자 복구 승인만 허용한다."""
        token=str(token or "").strip()
        if not token:
            return False
        for phase in ("LIVE_CUTOVER_AUTHORIZED", "LIVE_RECOVERY_AUTHORIZED"):
            row=self.latest_cutover_event(phase)
            detail=row.get("detail") if isinstance(row.get("detail"),dict) else {}
            if row.get("result")=="PASS" and str(detail.get("cutover_token") or "")==token:
                return True
        return False

    def pending_execution_count(self):
        """R4 원장 자체의 미종결 진입/청산 intent 수."""
        marks=",".join("?" for _ in TERMINAL_STATES)
        with closing(self.connect()) as con:
            a=int(con.execute(f"SELECT COUNT(*) FROM r4_order_intents WHERE status NOT IN ({marks})",TERMINAL_STATES).fetchone()[0] or 0)
            b=int(con.execute(f"SELECT COUNT(*) FROM r4_exit_intents WHERE status NOT IN ({marks})",TERMINAL_STATES).fetchone()[0] or 0)
        return a+b

    def adopt_legacy_positions(self, plans, cutover_token):
        """검증 완료된 legacy 단일-owner 포지션을 한 트랜잭션으로 R4 Leg에 귀속한다."""
        plans=[dict(x) for x in (plans or []) if isinstance(x,dict)]
        token=str(cutover_token or "").strip()
        if not token:
            raise ValueError("CUTOVER_TOKEN_REQUIRED")
        now=now_ts()
        adopted=[]
        with self.transaction() as con:
            lease=con.execute("SELECT owner_id,lease_expires_ts FROM r4_writer_lease WHERE lease_key='ACCOUNT_EXECUTION'").fetchone()
            if lease and int(lease["lease_expires_ts"] or 0)>=now:
                raise RuntimeError("R4_WRITER_ACTIVE_DURING_ADOPTION")

            marks=",".join("?" for _ in TERMINAL_STATES)
            pending=int(con.execute(f"SELECT COUNT(*) FROM r4_order_intents WHERE status NOT IN ({marks})",TERMINAL_STATES).fetchone()[0] or 0)
            pending+=int(con.execute(f"SELECT COUNT(*) FROM r4_exit_intents WHERE status NOT IN ({marks})",TERMINAL_STATES).fetchone()[0] or 0)
            if pending:
                raise RuntimeError("R4_PENDING_EXECUTION_BLOCKS_ADOPTION")

            seen_baskets=set()
            for plan in plans:
                instance_id=str(plan.get("instance_id") or "").strip()
                legacy_entry_id=str(plan.get("legacy_entry_id") or "").strip()
                legacy_history_id=str(plan.get("legacy_entry_history_id") or "").strip()
                legacy_order_id=str(plan.get("legacy_entry_order_id") or "").strip()
                signal_id=str(plan.get("signal_id") or "").strip()
                symbol=str(plan.get("symbol") or "").upper().strip()
                side=str(plan.get("side") or "").upper().strip()
                qty_units=int(plan.get("qty_units") or 0)
                entry_price_ticks=int(plan.get("entry_price_ticks") or 0)
                owner_evidence=plan.get("owner_evidence") if isinstance(plan.get("owner_evidence"),dict) else {}
                evidence_hash=str(plan.get("owner_evidence_sha256") or "").strip()
                policy_snapshot=plan.get("policy_snapshot") if isinstance(plan.get("policy_snapshot"),dict) else {}
                policy_bundle=str(plan.get("policy_bundle_version") or "").strip()

                if not all((instance_id,legacy_entry_id,legacy_history_id,legacy_order_id,signal_id,symbol,evidence_hash,policy_bundle)):
                    raise RuntimeError("LEGACY_ADOPTION_EXACT_OWNER_EVIDENCE_INCOMPLETE")
                if side not in ("LONG","SHORT") or qty_units<=0 or entry_price_ticks<=0 or not policy_snapshot:
                    raise RuntimeError("LEGACY_ADOPTION_POSITION_OR_POLICY_INVALID")
                if payload_hash(owner_evidence)!=evidence_hash:
                    raise RuntimeError("LEGACY_ADOPTION_OWNER_EVIDENCE_HASH_MISMATCH")
                if payload_hash(policy_snapshot)!=str(owner_evidence.get("policy_snapshot_sha256") or ""):
                    raise RuntimeError("LEGACY_ADOPTION_POLICY_SNAPSHOT_HASH_MISMATCH")

                instance=con.execute("SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
                if not instance:
                    raise RuntimeError("LEGACY_ADOPTION_INSTANCE_NOT_REGISTERED:"+instance_id)
                if str(instance["timeframe"]).upper()!=str(owner_evidence.get("owner_timeframe") or "").upper():
                    raise RuntimeError("LEGACY_ADOPTION_TIMEFRAME_OWNER_MISMATCH")
                if str(instance["strategy_id"]).upper()!=str(owner_evidence.get("owner_strategy") or "").upper():
                    raise RuntimeError("LEGACY_ADOPTION_STRATEGY_OWNER_MISMATCH")
                if str(instance["exact_version"])!=str(owner_evidence.get("exact_strategy_version") or ""):
                    raise RuntimeError("LEGACY_ADOPTION_STRATEGY_VERSION_MISMATCH")
                if str(instance["policy_bundle"])!=policy_bundle:
                    raise RuntimeError("LEGACY_ADOPTION_POLICY_BUNDLE_MISMATCH")

                basket=(symbol,side)
                if basket in seen_baskets:
                    raise RuntimeError("LEGACY_ADOPTION_DUPLICATE_BASKET")
                seen_baskets.add(basket)

                intent_id=canonical_id("ADOPT_EI_",instance_id,legacy_entry_id,symbol,side)
                leg_id=canonical_id("ADOPT_LEG_",intent_id)
                client_order_id=canonical_id("ADOPT",intent_id)[:36]
                existing=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
                if existing:
                    if int(existing["owned_qty_units"])!=qty_units or str(existing["instance_id"])!=instance_id:
                        raise RuntimeError("LEGACY_ADOPTION_IDEMPOTENCY_MISMATCH")
                    adopted.append(leg_id)
                    continue

                other=con.execute(
                    "SELECT leg_id FROM r4_active_legs WHERE symbol=? AND position_side=? AND status='ACTIVE' LIMIT 1",
                    (symbol,side),
                ).fetchone()
                if other:
                    raise RuntimeError("LEGACY_ADOPTION_BASKET_ALREADY_OWNED")

                frozen_policy={
                    "origin":"LEGACY_ADOPTION",
                    "strategy_instance_id":instance_id,
                    "exact_strategy_version":str(instance["exact_version"]),
                    "strategy_policy_bundle_version":policy_bundle,
                    "policy_snapshot":policy_snapshot,
                    "owner_evidence_sha256":evidence_hash,
                    "legacy_entry_id":legacy_entry_id,
                    "legacy_entry_history_id":legacy_history_id,
                    "legacy_entry_order_id":legacy_order_id,
                    "entry_price_ticks":entry_price_ticks,
                }
                con.execute(
                    "INSERT INTO r4_active_legs(leg_id,instance_id,strategy_id,strategy_version,frame,tier,trailing_policy_version,intent_id,signal_id,symbol,position_side,owned_qty_units,entry_qty_units,exit_qty_units,policy_snapshot,status,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (leg_id,instance_id,instance["strategy_id"],instance["exact_version"],instance["timeframe"],instance["tier"],instance["trailing_policy_version"],intent_id,signal_id,symbol,side,qty_units,qty_units,0,canonical_json(frozen_policy),"ACTIVE",now,now),
                )
                con.execute(
                    "INSERT INTO r4_order_intents(intent_id,instance_id,leg_id,signal_id,symbol,position_side,requested_qty_units,filled_qty_units,client_order_id,exchange_order_id,status,ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (intent_id,instance_id,leg_id,signal_id,symbol,side,qty_units,qty_units,client_order_id,legacy_order_id,"FILLED",now,now),
                )
                event_key=canonical_id("ADOPT_EVT_",leg_id,evidence_hash)
                con.execute(
                    "INSERT OR IGNORE INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                    (event_key,leg_id,"LEGACY_ADOPTION",qty_units,canonical_json({
                        "legacy_entry_id":legacy_entry_id,
                        "legacy_entry_history_id":legacy_history_id,
                        "legacy_entry_order_id":legacy_order_id,
                        "entry_price_ticks":entry_price_ticks,
                        "owner_evidence_sha256":evidence_hash,
                        "policy_snapshot_sha256":payload_hash(policy_snapshot),
                        "cutover_token":token,
                        "historical_fee_binding_complete":False,
                    }),now),
                )
                con.execute(
                    "INSERT INTO r4_basket_snapshots(symbol,position_side,ledger_qty_units,exchange_qty_units,mismatch,updated_ts) VALUES(?,?,?,?,0,?) ON CONFLICT(symbol,position_side) DO UPDATE SET ledger_qty_units=excluded.ledger_qty_units,exchange_qty_units=excluded.exchange_qty_units,mismatch=0,updated_ts=excluded.updated_ts",
                    (symbol,side,qty_units,qty_units,now),
                )
                reconcile_key=canonical_id("ADOPT_REC_",leg_id,qty_units,evidence_hash)
                con.execute(
                    "INSERT OR IGNORE INTO r4_reconciliation_events(event_key,symbol,position_side,ledger_qty_units,exchange_qty_units,result,ts) VALUES(?,?,?,?,?,?,?)",
                    (reconcile_key,symbol,side,qty_units,qty_units,"PASS",now),
                )
                adopted.append(leg_id)

            detail={"cutover_token":token,"adopted_leg_ids":sorted(adopted),"adopted_count":len(adopted)}
            key=canonical_id("CUT_","ADOPTION_COMMIT","PASS",payload_hash(detail))
            con.execute(
                "INSERT OR IGNORE INTO r4_cutover_events(event_key,phase,result,detail_json,ts) VALUES(?,?,?,?,?)",
                (key,"ADOPTION_COMMIT","PASS",canonical_json(detail),now),
            )
        return {"ok":True,"adopted_leg_ids":adopted,"adopted_count":len(adopted)}

    def leg_management_snapshot(self, leg_id):
        """Leg별 Exit/Trailing/Reentry가 사용할 불변 entry facts와 현재 상태를 한 번에 읽는다."""
        with closing(self.connect()) as con:
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not leg:
                raise KeyError(leg_id)
            intent=con.execute("SELECT status,filled_qty_units,requested_qty_units FROM r4_order_intents WHERE leg_id=?",(leg_id,)).fetchone()
            fills=con.execute("SELECT qty_units,price_ticks FROM r4_fills WHERE leg_id=? ORDER BY id",(leg_id,)).fetchall()
            trailing=con.execute("SELECT * FROM r4_leg_trailing WHERE leg_id=?",(leg_id,)).fetchone()
            pending_exit=int(con.execute(
                "SELECT COALESCE(SUM(requested_qty_units-filled_qty_units),0) FROM r4_exit_intents WHERE leg_id=? AND status NOT IN ('FILLED','CANCELLED','REJECTED','FAILED')",
                (leg_id,),
            ).fetchone()[0] or 0)
            adoption=con.execute(
                "SELECT detail_json FROM r4_leg_events WHERE leg_id=? AND event_type='LEGACY_ADOPTION' ORDER BY id DESC LIMIT 1",
                (leg_id,),
            ).fetchone()
        out=dict(leg)
        try:
            out["policy_snapshot_obj"]=json.loads(out.get("policy_snapshot") or "{}")
        except Exception:
            out["policy_snapshot_obj"]={}
        entry_qty=sum(int(x[0]) for x in fills)
        if entry_qty>0:
            out["entry_vwap_ticks"]=sum(int(x[0])*int(x[1]) for x in fills)//entry_qty
        else:
            fallback=0
            try:
                policy=out["policy_snapshot_obj"]
                fallback=int(policy.get("entry_price_ticks") or 0)
                if fallback<=0 and isinstance(policy.get("policy_snapshot"),dict):
                    fallback=int(policy["policy_snapshot"].get("entry_price_ticks") or 0)
            except Exception:
                fallback=0
            if fallback<=0 and adoption:
                try:
                    fallback=int(json.loads(adoption[0]).get("entry_price_ticks") or 0)
                except Exception:
                    fallback=0
            out["entry_vwap_ticks"]=fallback
        out["entry_intent_status"]=str(intent[0]) if intent else ""
        out["entry_requested_qty_units"]=int(intent[2]) if intent else 0
        out["entry_filled_qty_units"]=int(intent[1]) if intent else 0
        out["pending_exit_qty_units"]=pending_exit
        out["trailing_state"]=dict(trailing) if trailing else {}
        return out

    def update_trailing(self, leg_id, entry_price_ticks, last_price_ticks, extreme_ticks, trigger_ticks, activated, policy_version, mfe_ticks=0, mae_ticks=0):
        """Leg별 trailing과 관측 MFE/MAE extrema를 같은 bounded row에 원자 갱신한다."""
        values=(
            int(entry_price_ticks),int(last_price_ticks),int(extreme_ticks),int(trigger_ticks),
            1 if activated else 0,str(policy_version or ""),now_ts(),str(leg_id),
            max(0,int(mfe_ticks or 0)),max(0,int(mae_ticks or 0)),
        )
        if min(values[0],values[1],values[2])<=0 or values[3]<0 or not values[5]:
            raise ValueError("INVALID_LEG_TRAILING_STATE")
        with self.transaction() as con:
            leg=con.execute("SELECT status,trailing_policy_version FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not leg or leg["status"]!="ACTIVE":
                raise RuntimeError("TRAILING_REQUIRES_ACTIVE_LEG")
            if str(leg["trailing_policy_version"])!=values[5]:
                raise RuntimeError("OPEN_LEG_TRAILING_POLICY_MUTATION_FORBIDDEN")
            current=con.execute("SELECT policy_version,entry_price_ticks,mfe_ticks,mae_ticks FROM r4_leg_trailing WHERE leg_id=?",(leg_id,)).fetchone()
            if current and (str(current["policy_version"] or "") not in ("",values[5]) or int(current["entry_price_ticks"] or 0) not in (0,values[0])):
                raise RuntimeError("OPEN_LEG_TRAILING_SNAPSHOT_MUTATION_FORBIDDEN")
            if current:
                mfe=max(int(current["mfe_ticks"] or 0),values[8])
                mae=max(int(current["mae_ticks"] or 0),values[9])
            else:
                mfe,mae=values[8],values[9]
            con.execute(
                "INSERT INTO r4_leg_trailing(leg_id,extreme_ticks,trigger_ticks,updated_ts,entry_price_ticks,last_price_ticks,activated,policy_version,mfe_ticks,mae_ticks) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(leg_id) DO UPDATE SET "
                "extreme_ticks=excluded.extreme_ticks,trigger_ticks=excluded.trigger_ticks,updated_ts=excluded.updated_ts,"
                "last_price_ticks=excluded.last_price_ticks,activated=excluded.activated,"
                "mfe_ticks=MAX(r4_leg_trailing.mfe_ticks,excluded.mfe_ticks),mae_ticks=MAX(r4_leg_trailing.mae_ticks,excluded.mae_ticks)",
                (leg_id,values[2],values[3],values[6],values[0],values[1],values[4],values[5],mfe,mae),
            )


    def basket_snapshot(self, symbol, side):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_basket_snapshots WHERE symbol=? AND position_side=?",(str(symbol).upper(),str(side).upper())).fetchone()
        return dict(row) if row else {}

    def closed_reentry_candidates(self, limit=256):
        """Trailing/전략 Exit가 완전히 끝난 Leg만 재진입 후보로 반환한다. 귀속은 원 Leg에 남긴다."""
        with closing(self.connect()) as con:
            legs=con.execute(
                "SELECT * FROM r4_active_legs WHERE status='CLOSED' ORDER BY updated_ts DESC,leg_id DESC LIMIT ?",
                (max(1,min(int(limit),2048)),),
            ).fetchall()
            out=[]
            for leg in legs:
                successor=con.execute(
                    "SELECT detail_json FROM r4_leg_events WHERE leg_id=? AND event_type IN ('REENTRY_CREATED','REENTRY_CANCELLED') ORDER BY id DESC LIMIT 1",
                    (leg["leg_id"],),
                ).fetchone()
                if successor:
                    continue
                exit_row=con.execute(
                    "SELECT detail_json,ts FROM r4_leg_events WHERE leg_id=? AND event_type='EXIT_FILL' ORDER BY id DESC LIMIT 1",
                    (leg["leg_id"],),
                ).fetchone()
                if not exit_row:
                    continue
                item=dict(leg)
                try:
                    item["exit_detail"]=json.loads(exit_row["detail_json"] or "{}")
                except Exception:
                    item["exit_detail"]={}
                item["exit_ts"]=int(exit_row["ts"] or 0)
                try:
                    item["policy_snapshot_obj"]=json.loads(item.get("policy_snapshot") or "{}")
                except Exception:
                    item["policy_snapshot_obj"]={}
                out.append(item)
        return out

    def record_reentry_created(self, source_leg_id, successor_leg_id, reentry_signal_id, detail=None):
        detail=dict(detail or {})
        detail.update({"successor_leg_id":str(successor_leg_id),"reentry_signal_id":str(reentry_signal_id)})
        key=canonical_id("REENTRY_EVT_",source_leg_id,successor_leg_id)
        with self.transaction() as con:
            source=con.execute("SELECT status FROM r4_active_legs WHERE leg_id=?",(source_leg_id,)).fetchone()
            successor=con.execute("SELECT leg_id FROM r4_active_legs WHERE leg_id=?",(successor_leg_id,)).fetchone()
            if not source or source["status"]!="CLOSED" or not successor:
                raise RuntimeError("REENTRY_LINEAGE_REQUIRES_CLOSED_SOURCE_AND_SUCCESSOR")
            con.execute(
                "INSERT OR IGNORE INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                (key,source_leg_id,"REENTRY_CREATED",0,canonical_json(detail),now_ts()),
            )
        return key

    def record_reentry_cancelled(self, source_leg_id, reason, detail=None):
        detail=dict(detail or {})
        detail["reason"]=str(reason or "REENTRY_CANCELLED")
        key=canonical_id("REENTRY_CANCEL_",source_leg_id,detail["reason"])
        with self.transaction() as con:
            source=con.execute("SELECT status FROM r4_active_legs WHERE leg_id=?",(source_leg_id,)).fetchone()
            if not source or source["status"]!="CLOSED":
                raise RuntimeError("REENTRY_CANCEL_REQUIRES_CLOSED_SOURCE")
            con.execute(
                "INSERT OR IGNORE INTO r4_leg_events(event_key,leg_id,event_type,qty_units,detail_json,ts) VALUES(?,?,?,?,?,?)",
                (key,source_leg_id,"REENTRY_CANCELLED",0,canonical_json(detail),now_ts()),
            )
        return key

    # ================= Tier / 성과 / 복구 =================
    def set_tier(self, instance_id, tier, evidence_key):
        """CANARY 이후 서버 단일 Writer의 exact-instance Tier 전이만 허용한다."""
        tier=str(tier or "").upper()
        if tier not in VALID_TIERS:
            raise ValueError("INVALID_TIER")
        allowed={
            "CANARY":{"CANARY","SMALL"},
            "SMALL":{"SMALL","NORMAL","CANARY"},
            "NORMAL":{"NORMAL","SMALL"},
        }
        with self.transaction() as con:
            old=con.execute("SELECT tier FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
            if not old:
                raise KeyError(instance_id)
            old_tier=str(old[0]).upper()
            if old_tier not in allowed or tier not in allowed[old_tier]:
                raise RuntimeError(f"INVALID_SERVER_TIER_TRANSITION:{old_tier}->{tier}")
            for basket in con.execute("SELECT DISTINCT symbol,position_side FROM r4_active_legs WHERE instance_id=? AND status='ACTIVE'",(instance_id,)):
                mismatch=con.execute("SELECT mismatch FROM r4_basket_snapshots WHERE symbol=? AND position_side=?",tuple(basket)).fetchone()
                if mismatch and int(mismatch[0]) and VALID_TIERS.index(tier)>VALID_TIERS.index(old_tier):
                    raise RuntimeError("MISMATCH_BLOCKS_POSITIVE_TIER_SCALE")
            if old_tier==tier:
                return {"changed":False,"old_tier":old_tier,"new_tier":tier}
            event_key=canonical_id("TIER_",instance_id,old_tier,tier,evidence_key)
            con.execute("UPDATE r4_strategy_instances SET tier=?,updated_ts=? WHERE instance_id=?",(tier,now_ts(),instance_id))
            con.execute("INSERT OR IGNORE INTO r4_tier_events VALUES(NULL,?,?,?,?,?,?)",(event_key,instance_id,old_tier,tier,str(evidence_key),now_ts()))
        return {"changed":True,"old_tier":old_tier,"new_tier":tier,"event_key":event_key}

    def record_tier_evidence(self, instance_id, evidence_name, passed, detail=None, evidence_key=""):
        """exact Strategy Instance 승격/강등 근거를 bounded DB에 멱등 저장한다."""
        evidence_name=str(evidence_name or "").upper().strip()
        if not evidence_name:
            raise ValueError("TIER_EVIDENCE_NAME_REQUIRED")
        detail=detail if isinstance(detail,dict) else {}
        with self.transaction() as con:
            instance=con.execute("SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
            if not instance:
                raise KeyError(instance_id)
            tier=str(instance["tier"]).upper()
            key=str(evidence_key or "").strip() or canonical_id("TEV_",instance_id,tier,evidence_name,int(bool(passed)),payload_hash(detail))
            con.execute("INSERT OR IGNORE INTO r4_tier_evidence(event_key,instance_id,exact_version,tier,evidence_name,passed,detail_json,ts) VALUES(?,?,?,?,?,?,?,?)",(key,instance_id,instance["exact_version"],tier,evidence_name,int(bool(passed)),canonical_json(detail),now_ts()))
            row=con.execute("SELECT * FROM r4_tier_evidence WHERE event_key=?",(key,)).fetchone()
        return dict(row) if row else {}

    def latest_tier_evidence(self, instance_id, tier=""):
        tier=str(tier or "").upper().strip()
        with closing(self.connect()) as con:
            if tier:
                rows=con.execute("SELECT * FROM r4_tier_evidence WHERE instance_id=? AND tier=? ORDER BY ts DESC,id DESC",(instance_id,tier)).fetchall()
            else:
                rows=con.execute("SELECT * FROM r4_tier_evidence WHERE instance_id=? ORDER BY ts DESC,id DESC",(instance_id,)).fetchall()
        out={}
        for row in rows:
            name=str(row["evidence_name"]).upper()
            if name not in out:
                item=dict(row)
                try:
                    item["detail"]=json.loads(item.pop("detail_json"))
                except Exception:
                    item["detail"]={}
                item["passed"]=bool(item["passed"])
                out[name]=item
        return out

    def instance_state(self, instance_id):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,)).fetchone()
            if not row:
                raise KeyError(instance_id)
            return dict(row)

    def instance_lifecycle_count(self, instance_id, tier=""):
        sql="SELECT COUNT(*) FROM r4_active_legs WHERE instance_id=? AND status='CLOSED'"
        args=[instance_id]
        if tier:
            sql+=" AND tier=?"
            args.append(str(tier).upper())
        with closing(self.connect()) as con:
            return int(con.execute(sql,args).fetchone()[0] or 0)

    def tier_instance_count(self, timeframe, tier):
        with closing(self.connect()) as con:
            return int(con.execute("SELECT COUNT(*) FROM r4_strategy_instances WHERE timeframe=? AND tier=?",(str(timeframe).upper(),str(tier).upper())).fetchone()[0] or 0)

    def record_funding(self, leg_id, funding_id, amount_text):
        with self.transaction() as con: con.execute("INSERT OR IGNORE INTO r4_performance_events VALUES(NULL,?,?,?,?,?)",(funding_id,leg_id,"FUNDING",str(amount_text),now_ts()))

    def record_funding_evidence(
        self, leg_id, funding_id, amount_text, asset, source, funding_ts,
        attribution_evidence, value_usdt_text="", valuation_source="",
    ):
        leg=self.get_leg(leg_id)
        funding_id=str(funding_id or "").strip()
        amount_text=str(amount_text or "").strip()
        asset=str(asset or "").upper().strip()
        source=str(source or "").strip()
        funding_ts=int(funding_ts or 0)
        attribution=attribution_evidence if isinstance(attribution_evidence,dict) else {}
        basis=str(attribution.get("basis") or "").strip()
        attribution_key=str(attribution.get("evidence_key") or "").strip()
        value_usdt_text=str(value_usdt_text or "").strip()
        valuation_source=str(valuation_source or "").strip()
        if asset=="USDT" and amount_text and not value_usdt_text:
            value_usdt_text=amount_text
            valuation_source="BINANCE_INCOME_ASSET_IS_QUOTE"
        try:
            amount=Decimal(amount_text)
            value=Decimal(value_usdt_text) if value_usdt_text else Decimal("NaN")
            valid_amount=amount.is_finite()
            valid_value=value.is_finite()
        except (InvalidOperation,TypeError,ValueError):
            valid_amount=False
            valid_value=False
        if basis=="UNIQUE_LEG_FILL_INTERVAL":
            try:
                entry_ts=int(attribution.get("entry_ts") or 0)
                exit_ts=int(attribution.get("exit_ts") or 0)
            except (TypeError,ValueError):
                entry_ts=0
                exit_ts=0
            attribution_valid=bool(
                attribution.get("leg_id")==leg_id
                and attribution.get("verified") is True
                and attribution.get("candidate_leg_ids")==[leg_id]
                and entry_ts>0 and exit_ts>=entry_ts
                and entry_ts<=funding_ts<=exit_ts and attribution_key
            )
        elif basis=="EXACT_LEG_TRANSACTION_REFERENCE":
            attribution_valid=bool(
                attribution.get("leg_id")==leg_id
                and attribution.get("verified") is True
                and str(attribution.get("funding_id") or "")==funding_id
                and attribution_key
            )
        else:
            attribution_valid=False
        complete=bool(
            funding_id and source.startswith("BINANCE") and asset and funding_ts>0
            and valid_amount and valid_value and valuation_source and attribution_valid
        )
        event_key=canonical_id("R4F_",leg_id,funding_id or source)
        payload={
            "leg_id":leg_id,"instance_id":leg["instance_id"],
            "exact_version":leg["strategy_version"],"funding_id":funding_id,
            "amount_text":amount_text,"asset":asset,"source":source,
            "funding_ts":funding_ts,"attribution_basis":basis,
            "attribution_evidence_key":attribution_key,"value_usdt_text":value_usdt_text,
        }
        with self.transaction() as con:
            existing=con.execute(
                "SELECT payload_hash FROM r4_trade_evidence WHERE event_key=?",
                (event_key,),
            ).fetchone()
            if existing:
                if str(existing["payload_hash"])!=payload_hash(payload):
                    raise RuntimeError("FUNDING_EVENT_ID_PAYLOAD_CONFLICT")
                return {"event_key":event_key,"evidence_complete":complete,"inserted":False}
            con.execute(
                "INSERT INTO r4_trade_evidence(event_key,leg_id,instance_id,strategy_id,exact_version,kind,source,source_event_id,trade_id,order_id,symbol,position_side,qty_units,price_ticks,qty_text,price_text,realized_pnl_text,realized_pnl_asset,fee_text,fee_asset,fee_value_usdt_text,fee_valuation_source,leverage_x,leverage_source,capital_basis_usdt,capital_basis_source,attribution_basis,attribution_evidence_key,funding_text,funding_asset,funding_value_usdt_text,funding_valuation_source,evidence_complete,hard_fail,payload_hash,exchange_ts,ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_key,leg["leg_id"],leg["instance_id"],leg["strategy_id"],leg["strategy_version"],
                    "FUNDING",source,funding_id,"","",leg["symbol"],leg["position_side"],0,0,"","",
                    "","", "","", "","", "","", "","",basis,attribution_key,amount_text,asset,
                    value_usdt_text,valuation_source,int(complete),0,payload_hash(payload),funding_ts,now_ts(),
                ),
            )
        return {"event_key":event_key,"evidence_complete":complete,"inserted":True}

    def confirm_funding_coverage(self, leg_id, source, coverage_start_ts, coverage_end_ts, evidence_key, expected_event_count):
        source=str(source or "").strip()
        evidence_key=str(evidence_key or "").strip()
        start_ts=int(coverage_start_ts)
        end_ts=int(coverage_end_ts)
        expected_event_count=int(expected_event_count)
        if not source.startswith("BINANCE") or not evidence_key or start_ts<0 or end_ts<start_ts:
            raise ValueError("VERIFIED_FUNDING_COVERAGE_REQUIRED")
        with self.transaction() as con:
            leg=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not leg:
                raise KeyError(leg_id)
            if leg["status"]!="CLOSED":
                raise RuntimeError("FUNDING_COVERAGE_REQUIRES_CLOSED_LEG")
            exit_ts=int(con.execute(
                "SELECT COALESCE(MAX(exchange_ts),0) FROM r4_trade_evidence WHERE leg_id=? AND kind='EXIT_FILL'",
                (leg_id,),
            ).fetchone()[0] or 0)
            if start_ts>int(leg["created_ts"]) or end_ts<exit_ts:
                raise RuntimeError("FUNDING_COVERAGE_DOES_NOT_SPAN_LEG")
            recorded_count=int(con.execute(
                "SELECT COUNT(*) FROM r4_trade_evidence WHERE leg_id=? AND kind='FUNDING' "
                "AND exchange_ts>=? AND exchange_ts<=? AND evidence_complete=1",
                (leg_id,start_ts,end_ts),
            ).fetchone()[0] or 0)
            if expected_event_count<0 or recorded_count!=expected_event_count:
                raise RuntimeError("FUNDING_EVENT_COUNT_MISMATCH")
            con.execute(
                "INSERT INTO r4_leg_evidence_coverage(leg_id,evidence_type,source,evidence_key,coverage_start_ts,coverage_end_ts,complete,updated_ts) "
                "VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(leg_id,evidence_type) DO UPDATE SET "
                "source=excluded.source,evidence_key=excluded.evidence_key,coverage_start_ts=excluded.coverage_start_ts,"
                "coverage_end_ts=excluded.coverage_end_ts,complete=1,updated_ts=excluded.updated_ts",
                (leg_id,"FUNDING",source,evidence_key,start_ts,end_ts,now_ts()),
            )
        return True

    def performance_tier_cohort(self, instance_id):
        with closing(self.connect()) as con:
            instance=con.execute(
                "SELECT * FROM r4_strategy_instances WHERE instance_id=?",(instance_id,),
            ).fetchone()
            if not instance:
                raise KeyError(instance_id)
            tier=str(instance["tier"]).upper()
            last_tier_change=int(con.execute(
                "SELECT COALESCE(MAX(ts),0) FROM r4_tier_events WHERE instance_id=? AND new_tier=?",
                (instance_id,tier),
            ).fetchone()[0] or 0)
            epoch_ts=max(int(instance["created_ts"] or 0),last_tier_change)
            legs=con.execute(
                "SELECT * FROM r4_active_legs WHERE instance_id=? AND strategy_version=? AND tier=? "
                "AND status='CLOSED' AND created_ts>=? ORDER BY updated_ts,leg_id",
                (instance_id,instance["exact_version"],tier,epoch_ts),
            ).fetchall()
            cohort=[]
            for leg in legs:
                leg_id=str(leg["leg_id"])
                entries=con.execute(
                    "SELECT fill_id,qty_units,price_ticks,ts FROM r4_fills WHERE leg_id=? ORDER BY id",
                    (leg_id,),
                ).fetchall()
                exits=con.execute(
                    "SELECT event_key,qty_units,detail_json,ts FROM r4_leg_events "
                    "WHERE leg_id=? AND event_type='EXIT_FILL' ORDER BY id",
                    (leg_id,),
                ).fetchall()
                evidence=con.execute(
                    "SELECT * FROM r4_trade_evidence WHERE leg_id=? ORDER BY id",(leg_id,),
                ).fetchall()
                coverage=con.execute(
                    "SELECT * FROM r4_leg_evidence_coverage WHERE leg_id=? AND evidence_type='FUNDING'",
                    (leg_id,),
                ).fetchone()
                cohort.append({
                    "leg":dict(leg),"entries":[dict(row) for row in entries],
                    "exits":[dict(row) for row in exits],
                    "evidence":[dict(row) for row in evidence],
                    "funding_coverage":dict(coverage) if coverage else None,
                })
        return {"instance":dict(instance),"epoch_ts":epoch_ts,"legs":cohort}

    def record_performance_tier_evaluation(self, instance_id, decision):
        decision=decision if isinstance(decision,dict) else {}
        evidence_key=str(decision.get("evidence_key") or "").strip()
        if not evidence_key:
            raise ValueError("PERFORMANCE_EVIDENCE_KEY_REQUIRED")
        instance=self.instance_state(instance_id)
        event_key=canonical_id("R4PE_",instance_id,evidence_key)
        # 상세 체결 근거는 원본 증거 테이블에서 보관하고 평가 행에는 중복 저장하지 않는다.
        detail={key:value for key,value in decision.items() if key!="legs"}
        detail["leg_evidence_count"]=len(decision.get("legs") or [])
        with self.transaction() as con:
            cursor=con.execute(
                "INSERT OR IGNORE INTO r4_tier_performance_evaluations(event_key,instance_id,exact_version,tier,action,target_tier,reason,evidence_key,sample_count,total_net_pnl_usdt,mean_roi_percent,roi_95ci_lower,roi_95ci_upper,detail_json,ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_key,instance_id,instance["exact_version"],
                    str(decision.get("current_tier") or instance["tier"]).upper(),
                    str(decision.get("action") or "HOLD").upper(),
                    str(decision.get("target_tier") or ""),str(decision.get("reason") or ""),
                    evidence_key,int(decision.get("sample_count") or 0),
                    str(decision.get("total_net_pnl_usdt") or ""),
                    str(decision.get("mean_roi_percent") or ""),
                    str(decision.get("roi_95ci_lower") or ""),
                    str(decision.get("roi_95ci_upper") or ""),
                    canonical_json(detail),now_ts(),
                ),
            )
            row=con.execute(
                "SELECT * FROM r4_tier_performance_evaluations WHERE event_key=?",(event_key,),
            ).fetchone()
        return {"inserted":bool(cursor.rowcount),"row":dict(row) if row else {}}

    def has_performance_tier_evaluation(self, instance_id, evidence_key):
        with closing(self.connect()) as con:
            return bool(con.execute(
                "SELECT 1 FROM r4_tier_performance_evaluations WHERE instance_id=? AND evidence_key=? LIMIT 1",
                (str(instance_id),str(evidence_key)),
            ).fetchone())

    def performance(self, leg_id):
        """Leg 성과를 실제 체결/원장 데이터만으로 반환한다. 미관측 값은 조작하지 않는다."""
        with closing(self.connect()) as con:
            leg=con.execute(
                "SELECT * FROM r4_active_legs WHERE leg_id=?",
                (leg_id,),
            ).fetchone()
            if not leg:
                raise KeyError(leg_id)

            entries=con.execute(
                "SELECT qty_units,price_ticks,fee_text,fee_asset,realized_pnl_text "
                "FROM r4_fills WHERE leg_id=? ORDER BY id",
                (leg_id,),
            ).fetchall()

            exits=con.execute(
                "SELECT qty_units,detail_json,ts "
                "FROM r4_leg_events "
                "WHERE leg_id=? AND event_type='EXIT_FILL' "
                "ORDER BY id",
                (leg_id,),
            ).fetchall()

            trail=con.execute(
                "SELECT * FROM r4_leg_trailing WHERE leg_id=?",
                (leg_id,),
            ).fetchone()

            funding=sum(
                (
                    Decimal(r[0])
                    for r in con.execute(
                        "SELECT value_text FROM r4_performance_events "
                        "WHERE leg_id=? AND metric='FUNDING'",
                        (leg_id,),
                    )
                ),
                Decimal("0"),
            )

        eq=sum(int(x[0]) for x in entries)
        ev=(
            sum(
                int(x[0]) * int(x[1])
                for x in entries
            ) // eq
            if eq
            else 0
        )

        xd=[]
        for qty, detail_json, ts in exits:
            try:
                detail=json.loads(detail_json or "{}")
            except Exception:
                detail={}
            xd.append((int(qty), detail, int(ts or 0)))

        xq=sum(q for q,_,_ in xd)
        xv=(
            sum(
                q * int(d.get("price_ticks",0) or 0)
                for q,d,_ in xd
            ) // xq
            if xq
            else 0
        )

        direction=(
            Decimal("1")
            if leg["position_side"]=="LONG"
            else Decimal("-1")
        )
        gross_tick_units=(
            direction
            * Decimal(xq)
            * Decimal(xv-ev)
            if ev and xv and xq
            else Decimal("0")
        )

        realized=[]
        for row in entries:
            text=str(row[4] or "").strip()
            if text:
                try:
                    realized.append(Decimal(text))
                except Exception:
                    pass

        for _,detail,_ in xd:
            text=str(
                detail.get("realized_pnl_text")
                or ""
            ).strip()
            if text:
                try:
                    realized.append(Decimal(text))
                except Exception:
                    pass

        fee_by_asset={}
        for row in entries:
            asset=str(
                row[3]
                or "UNKNOWN"
            ).upper() or "UNKNOWN"
            try:
                fee_by_asset[asset]=(
                    fee_by_asset.get(
                        asset,
                        Decimal("0"),
                    )
                    + Decimal(str(row[2] or "0"))
                )
            except Exception:
                pass

        for _,detail,_ in xd:
            asset=str(
                detail.get("fee_asset")
                or "UNKNOWN"
            ).upper() or "UNKNOWN"
            try:
                fee_by_asset[asset]=(
                    fee_by_asset.get(
                        asset,
                        Decimal("0"),
                    )
                    + Decimal(
                        str(
                            detail.get("fee_text")
                            or "0"
                        )
                    )
                )
            except Exception:
                pass

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
        if ev > 0 and reference_ticks > 0:
            if str(
                leg["position_side"]
            ).upper()=="LONG":
                slippage_bps=(
                    (ev-reference_ticks)
                    / reference_ticks
                    * 10000.0
                )
            else:
                slippage_bps=(
                    (reference_ticks-ev)
                    / reference_ticks
                    * 10000.0
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
        extreme_ticks=(
            int(trail["extreme_ticks"] or 0)
            if trail
            else 0
        )

        max_giveback_ticks=None
        if extreme_ticks > 0 and xv > 0:
            if str(
                leg["position_side"]
            ).upper()=="LONG":
                max_giveback_ticks=max(
                    0,
                    extreme_ticks-xv,
                )
            else:
                max_giveback_ticks=max(
                    0,
                    xv-extreme_ticks,
                )

        exit_ts=max(
            (ts for _,_,ts in xd),
            default=0,
        )
        holding_seconds=(
            max(
                0,
                exit_ts-int(leg["created_ts"] or 0),
            )
            if exit_ts > 0
            else None
        )

        return {
            "leg_id":leg_id,
            "instance_id":leg["instance_id"],
            "entry_vwap_ticks":ev,
            "exit_vwap_ticks":xv,
            "entry_reference_price_ticks":
                reference_ticks or None,
            "entry_slippage_bps":
                slippage_bps,
            "entry_qty_units":eq,
            "exit_qty_units":xq,
            "gross_pnl_tick_units":
                str(gross_tick_units),
            "realized_pnl_text":
                str(
                    sum(
                        realized,
                        Decimal("0"),
                    )
                )
                if realized
                else "",
            "realized_pnl_source":
                (
                    "BINANCE_ORDER_TRADE_UPDATE_RP"
                    if realized
                    else "UNAVAILABLE_NOT_FABRICATED"
                ),
            "fee_by_asset":{
                k:str(v)
                for k,v
                in sorted(
                    fee_by_asset.items()
                )
            },
            "funding_text":str(funding),
            "mfe_ticks":mfe_ticks,
            "mae_ticks":mae_ticks,
            "max_giveback_ticks":
                max_giveback_ticks,
            "holding_seconds":
                holding_seconds,
            "exit_reason":
                (
                    str(
                        xd[-1][1].get("reason")
                        or ""
                    )
                    if xd
                    else ""
                ),
            "status":leg["status"],
        }


    def pending_intents(self):
        marks=",".join("?" for _ in TERMINAL_STATES)
        with closing(self.connect()) as con: return [dict(x) for x in con.execute(f"SELECT * FROM r4_order_intents WHERE status NOT IN ({marks}) ORDER BY ts,intent_id",TERMINAL_STATES)]

    def get_intent(self, intent_id):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_order_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if not row: raise KeyError(intent_id)
            return dict(row)

    def get_intent_by_client_order_id(self, client_order_id):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_order_intents WHERE client_order_id=?",(client_order_id,)).fetchone()
            return dict(row) if row else None

    def pending_exit_intents(self):
        marks=",".join("?" for _ in TERMINAL_STATES)
        with closing(self.connect()) as con:
            return [dict(x) for x in con.execute(
                f"SELECT x.*,l.symbol,l.position_side FROM r4_exit_intents x JOIN r4_active_legs l ON l.leg_id=x.leg_id WHERE x.status NOT IN ({marks}) ORDER BY x.created_ts,x.exit_intent_id",
                TERMINAL_STATES,
            )]

    def get_exit_by_client_order_id(self, client_order_id):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_exit_intents WHERE client_order_id=?",(client_order_id,)).fetchone()
            return dict(row) if row else None

    def get_leg(self, leg_id):
        with closing(self.connect()) as con:
            row=con.execute("SELECT * FROM r4_active_legs WHERE leg_id=?",(leg_id,)).fetchone()
            if not row: raise KeyError(leg_id)
            return dict(row)

    def active_legs(self, symbol=None, side=None):
        sql="SELECT * FROM r4_active_legs WHERE status='ACTIVE'"; args=[]
        if symbol: sql+=" AND symbol=?"; args.append(symbol)
        if side: sql+=" AND position_side=?"; args.append(side)
        with closing(self.connect()) as con: return [dict(x) for x in con.execute(sql+" ORDER BY created_ts,leg_id",args)]


# ================= 스키마 =================
SCHEMA="""
CREATE TABLE IF NOT EXISTS r4_strategy_instances(instance_id TEXT PRIMARY KEY,strategy_id TEXT NOT NULL,timeframe TEXT NOT NULL,exact_version TEXT NOT NULL,policy_bundle TEXT NOT NULL,trailing_policy_version TEXT NOT NULL,tier TEXT NOT NULL,created_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL,UNIQUE(strategy_id,timeframe,exact_version,policy_bundle));
CREATE TABLE IF NOT EXISTS r4_active_legs(leg_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,strategy_id TEXT NOT NULL,strategy_version TEXT NOT NULL,frame TEXT NOT NULL,tier TEXT NOT NULL,trailing_policy_version TEXT NOT NULL,intent_id TEXT NOT NULL UNIQUE,signal_id TEXT NOT NULL,symbol TEXT NOT NULL,position_side TEXT NOT NULL,owned_qty_units INTEGER NOT NULL CHECK(owned_qty_units>=0),entry_qty_units INTEGER NOT NULL,exit_qty_units INTEGER NOT NULL,policy_snapshot TEXT NOT NULL,status TEXT NOT NULL,created_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_order_intents(id INTEGER PRIMARY KEY AUTOINCREMENT,intent_id TEXT NOT NULL UNIQUE,instance_id TEXT NOT NULL,leg_id TEXT NOT NULL UNIQUE,signal_id TEXT NOT NULL,symbol TEXT NOT NULL,position_side TEXT NOT NULL,requested_qty_units INTEGER NOT NULL,filled_qty_units INTEGER NOT NULL,client_order_id TEXT NOT NULL UNIQUE,exchange_order_id TEXT,status TEXT NOT NULL,ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL,UNIQUE(instance_id,signal_id,symbol,position_side));
CREATE TABLE IF NOT EXISTS r4_exit_intents(id INTEGER PRIMARY KEY AUTOINCREMENT,exit_intent_id TEXT NOT NULL UNIQUE,leg_id TEXT NOT NULL,exit_signal_id TEXT NOT NULL,requested_qty_units INTEGER NOT NULL,filled_qty_units INTEGER NOT NULL,client_order_id TEXT NOT NULL UNIQUE,exchange_order_id TEXT,status TEXT NOT NULL,reason TEXT NOT NULL,created_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL,UNIQUE(leg_id,exit_signal_id));
CREATE TABLE IF NOT EXISTS r4_capacity_reservations(intent_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,position_side TEXT NOT NULL,qty_units INTEGER NOT NULL,capacity_limit_units INTEGER NOT NULL,status TEXT NOT NULL,created_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_fills(id INTEGER PRIMARY KEY AUTOINCREMENT,fill_id TEXT NOT NULL UNIQUE,intent_id TEXT NOT NULL,leg_id TEXT NOT NULL,exchange_order_id TEXT,qty_units INTEGER NOT NULL,price_ticks INTEGER NOT NULL,fee_text TEXT NOT NULL,ts INTEGER NOT NULL,fee_asset TEXT NOT NULL DEFAULT '',realized_pnl_text TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS r4_leg_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,leg_id TEXT NOT NULL,event_type TEXT NOT NULL,qty_units INTEGER NOT NULL,detail_json TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_reconciliation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,symbol TEXT NOT NULL,position_side TEXT NOT NULL,ledger_qty_units INTEGER NOT NULL,exchange_qty_units INTEGER NOT NULL,result TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_basket_snapshots(symbol TEXT NOT NULL,position_side TEXT NOT NULL,ledger_qty_units INTEGER NOT NULL,exchange_qty_units INTEGER NOT NULL,mismatch INTEGER NOT NULL,updated_ts INTEGER NOT NULL,PRIMARY KEY(symbol,position_side));
CREATE TABLE IF NOT EXISTS r4_leg_trailing(leg_id TEXT PRIMARY KEY,extreme_ticks INTEGER NOT NULL,trigger_ticks INTEGER NOT NULL,updated_ts INTEGER NOT NULL,entry_price_ticks INTEGER NOT NULL DEFAULT 0,last_price_ticks INTEGER NOT NULL DEFAULT 0,activated INTEGER NOT NULL DEFAULT 0,policy_version TEXT NOT NULL DEFAULT '',mfe_ticks INTEGER NOT NULL DEFAULT 0,mae_ticks INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS r4_tier_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,instance_id TEXT NOT NULL,old_tier TEXT NOT NULL,new_tier TEXT NOT NULL,evidence_key TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_tier_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,instance_id TEXT NOT NULL,exact_version TEXT NOT NULL,tier TEXT NOT NULL,evidence_name TEXT NOT NULL,passed INTEGER NOT NULL CHECK(passed IN (0,1)),detail_json TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_policy_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,instance_id TEXT NOT NULL,policy_bundle TEXT NOT NULL,detail_json TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_hard_safety_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,symbol TEXT NOT NULL,position_side TEXT NOT NULL,action TEXT NOT NULL,detail_json TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_performance_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,leg_id TEXT NOT NULL,metric TEXT NOT NULL,value_text TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_trade_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,leg_id TEXT NOT NULL,instance_id TEXT NOT NULL,strategy_id TEXT NOT NULL,exact_version TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,source_event_id TEXT NOT NULL,trade_id TEXT NOT NULL DEFAULT '',order_id TEXT NOT NULL DEFAULT '',symbol TEXT NOT NULL,position_side TEXT NOT NULL,qty_units INTEGER NOT NULL,price_ticks INTEGER NOT NULL,qty_text TEXT NOT NULL DEFAULT '',price_text TEXT NOT NULL DEFAULT '',realized_pnl_text TEXT NOT NULL DEFAULT '',realized_pnl_asset TEXT NOT NULL DEFAULT '',fee_text TEXT NOT NULL DEFAULT '',fee_asset TEXT NOT NULL DEFAULT '',fee_value_usdt_text TEXT NOT NULL DEFAULT '',fee_valuation_source TEXT NOT NULL DEFAULT '',leverage_x TEXT NOT NULL DEFAULT '',leverage_source TEXT NOT NULL DEFAULT '',capital_basis_usdt TEXT NOT NULL DEFAULT '',capital_basis_source TEXT NOT NULL DEFAULT '',attribution_basis TEXT NOT NULL DEFAULT '',attribution_evidence_key TEXT NOT NULL DEFAULT '',funding_text TEXT NOT NULL DEFAULT '',funding_asset TEXT NOT NULL DEFAULT '',funding_value_usdt_text TEXT NOT NULL DEFAULT '',funding_valuation_source TEXT NOT NULL DEFAULT '',evidence_complete INTEGER NOT NULL CHECK(evidence_complete IN (0,1)),hard_fail INTEGER NOT NULL DEFAULT 0 CHECK(hard_fail IN (0,1)),payload_hash TEXT NOT NULL,exchange_ts INTEGER NOT NULL DEFAULT 0,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_leg_evidence_coverage(leg_id TEXT NOT NULL,evidence_type TEXT NOT NULL,source TEXT NOT NULL,evidence_key TEXT NOT NULL,coverage_start_ts INTEGER NOT NULL,coverage_end_ts INTEGER NOT NULL,complete INTEGER NOT NULL CHECK(complete IN (0,1)),updated_ts INTEGER NOT NULL,PRIMARY KEY(leg_id,evidence_type));
CREATE TABLE IF NOT EXISTS r4_tier_performance_evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,instance_id TEXT NOT NULL,exact_version TEXT NOT NULL,tier TEXT NOT NULL,action TEXT NOT NULL,target_tier TEXT NOT NULL,reason TEXT NOT NULL,evidence_key TEXT NOT NULL,sample_count INTEGER NOT NULL,total_net_pnl_usdt TEXT NOT NULL,mean_roi_percent TEXT NOT NULL,roi_95ci_lower TEXT NOT NULL,roi_95ci_upper TEXT NOT NULL,detail_json TEXT NOT NULL,ts INTEGER NOT NULL,UNIQUE(instance_id,evidence_key));
CREATE TABLE IF NOT EXISTS r4_cutover_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,phase TEXT NOT NULL,result TEXT NOT NULL,detail_json TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_event_dedupe(event_key TEXT PRIMARY KEY,source TEXT NOT NULL,payload_hash TEXT NOT NULL,ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_writer_lease(lease_key TEXT PRIMARY KEY,owner_id TEXT NOT NULL,lease_expires_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS r4_basket_locks(basket_key TEXT PRIMARY KEY,owner_id TEXT NOT NULL,lease_expires_ts INTEGER NOT NULL,updated_ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_r4_legs_basket ON r4_active_legs(symbol,position_side,status);
CREATE INDEX IF NOT EXISTS idx_r4_intents_status ON r4_order_intents(status,updated_ts);
CREATE INDEX IF NOT EXISTS idx_r4_exit_intents_status ON r4_exit_intents(status,updated_ts);
CREATE INDEX IF NOT EXISTS idx_r4_fills_ts ON r4_fills(ts);
CREATE INDEX IF NOT EXISTS idx_r4_leg_events_ts ON r4_leg_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_reconcile_ts ON r4_reconciliation_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_tier_events_ts ON r4_tier_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_tier_evidence_instance ON r4_tier_evidence(instance_id,evidence_name,ts);
CREATE INDEX IF NOT EXISTS idx_r4_tier_evidence_ts ON r4_tier_evidence(ts);
CREATE INDEX IF NOT EXISTS idx_r4_policy_events_ts ON r4_policy_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_hard_safety_ts ON r4_hard_safety_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_performance_ts ON r4_performance_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_trade_evidence_leg ON r4_trade_evidence(leg_id,kind,ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_r4_trade_evidence_exchange_trade ON r4_trade_evidence(symbol,trade_id) WHERE trade_id<>'' AND kind IN ('ENTRY_FILL','EXIT_FILL');
CREATE UNIQUE INDEX IF NOT EXISTS idx_r4_trade_evidence_funding_event ON r4_trade_evidence(source,source_event_id) WHERE source_event_id<>'' AND kind='FUNDING';
CREATE INDEX IF NOT EXISTS idx_r4_tier_performance_instance ON r4_tier_performance_evaluations(instance_id,ts);
CREATE INDEX IF NOT EXISTS idx_r4_cutover_ts ON r4_cutover_events(ts);
CREATE INDEX IF NOT EXISTS idx_r4_dedupe_ts ON r4_event_dedupe(ts);
CREATE INDEX IF NOT EXISTS r4_trade_evidence_leg_kind_ts ON r4_trade_evidence(leg_id,kind,exchange_ts,id);
CREATE INDEX IF NOT EXISTS r4_trade_evidence_retention ON r4_trade_evidence(ts,id);
CREATE INDEX IF NOT EXISTS r4_coverage_retention ON r4_leg_evidence_coverage(updated_ts,leg_id);
CREATE INDEX IF NOT EXISTS r4_tier_evaluation_retention ON r4_tier_performance_evaluations(ts,id);
"""
