#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: multi_strategy_runtime.py
"""R4 단독 Runtime. Shadow와 Live는 같은 상태기계를 사용하고 거래소 어댑터만 다르다."""

# ================= 임포트 =================
import logging
import os
import time
import uuid
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from r4_performance_tiering import evaluate_instance as evaluate_performance_instance
from strategy_leg_ledger import StrategyLegLedger

# ================= 로그 =================
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
LOG_FILE = BASE / "logs" / "runner.log"
if not logging.getLogger().handlers:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO, encoding="utf-8", format="%(asctime)s [%(levelname)s] [주문][R4] %(message)s")
logger = logging.getLogger("multi_strategy_runtime")


# ================= 런타임 =================
class MultiStrategyRuntime:
    def __init__(self, ledger=None, exchange=None, owner_id=None):
        self.ledger=ledger or StrategyLegLedger(); self.exchange=exchange; self.owner_id=owner_id or f"R4_{uuid.uuid4().hex}"
        self.mode=os.getenv("R4_RUNTIME_MODE","LOCAL_SHADOW").upper()
        self.cutover_token=str(os.getenv("R4_CUTOVER_TOKEN","") or "").strip()
        self.entry_admission_enabled=self.mode!="EXCLUSIVE_LIVE"
        if self.mode not in ("LOCAL_TEST","LOCAL_SHADOW","EXCLUSIVE_LIVE"): raise RuntimeError("INVALID_R4_RUNTIME_MODE")
        if self.mode=="EXCLUSIVE_LIVE":
            if os.getenv("R4_LIVE_CUTOVER_VERIFIED")!="1" or os.getenv("R4_LEGACY_WRITER_STOPPED")!="1" or exchange is None: raise RuntimeError("LIVE_CUTOVER_GATES_NOT_PROVEN")
            if not self.cutover_token or not self.ledger.validate_cutover_token(self.cutover_token): raise RuntimeError("LIVE_CUTOVER_TOKEN_NOT_PROVEN")
        self.ledger.acquire_writer(self.owner_id)

    def close(self): self.ledger.release_writer(self.owner_id)

    def heartbeat(self): self.ledger.heartbeat_writer(self.owner_id)

    def _query_intent(self, intent):
        try:
            return self.exchange.query_by_client_order_id(intent["client_order_id"],intent["symbol"])
        except TypeError:
            return self.exchange.query_by_client_order_id(intent["client_order_id"])

    def enter(
        self,
        instance_id,
        signal_id,
        symbol,
        side,
        qty_units,
        policy_snapshot,
        capacity_limit_units,
        tier_leg_limit=None,
    ):
        if (
            self.mode == "EXCLUSIVE_LIVE"
            and not self.entry_admission_enabled
        ):
            raise RuntimeError(
                "R4_NEW_ENTRY_ADMISSION_BLOCKED_UNTIL_RECONCILE"
            )

        self.ledger.acquire_basket_lock(
            symbol,
            side,
            self.owner_id,
        )

        try:
            try:
                intent = self.ledger.reserve_intent(
                    instance_id, signal_id, symbol, side, qty_units,
                    policy_snapshot, capacity_limit_units, tier_leg_limit,
                )
            except RuntimeError as exc:
                if str(exc) not in {"FRAME_STRATEGY_SLOTS_FULL", "FRAME_STRATEGY_VERSION_OCCUPIED", "OPPOSITE_SIDE_MULTI_LEG_DISABLED", "TIER_LEG_LIMIT_REACHED"}:
                    raise
                return {"status": "ENTRY_BLOCKED", "reason": str(exc), "intent": {}}

            if intent["status"] in (
                "SUBMITTED",
                "PARTIAL_FILLED",
                "FILLED",
                "AMBIGUOUS",
                "REJECTED",
                "CANCELLED",
            ):
                return {
                    "status":
                        "IDEMPOTENT_EXISTING",
                    "intent":
                        intent,
                }

            if self.exchange is None:
                return {
                    "status":
                        "SHADOW_INTENT_SAVED",
                    "intent":
                        intent,
                }

            try:
                leverage = int(
                    (
                        policy_snapshot
                        or {}
                    ).get(
                        "leverage"
                    )
                    or 0
                )

                actual = (
                    self.exchange
                    .ensure_common_leverage(
                        symbol,
                        leverage,
                    )
                )

                if int(actual) != leverage:
                    raise RuntimeError(
                        "COMMON_LEVERAGE_VERIFY_MISMATCH"
                    )
                self.ledger.confirm_leverage_for_leg(
                    intent["leg_id"],
                    leverage,
                    actual,
                    "BINANCE_ENSURE_COMMON_LEVERAGE",
                )

            except Exception as exc:
                self.ledger.cancel_intent(
                    intent["intent_id"],
                    (
                        "COMMON_LEVERAGE_PREPARE_FAIL:"
                        + type(exc).__name__
                    ),
                    rejected=True,
                )

                return {
                    "status":
                        "LEVERAGE_BLOCKED",

                    "intent":
                        self.ledger.get_intent(
                            intent["intent_id"]
                        ),

                    "reason":
                        type(exc).__name__,
                }

            result = self.exchange.submit(
                intent
            )

            return self._apply_order_result(
                intent,
                result,
                "REST_SUBMIT",
            )

        finally:
            self.ledger.release_basket_lock(
                symbol,
                side,
                self.owner_id,
            )

    def _apply_order_result(self, intent, result, source):
        if result.get("ambiguous"):
            self.ledger.mark_submitted(intent["intent_id"],ambiguous=True)
            resolved=self._query_intent(intent)
            if not resolved or resolved.get("found") is not True: return {"status":"AMBIGUOUS_NO_BLIND_RETRY","intent":intent}
            result=resolved
        if result.get("rejected"):
            self.ledger.cancel_intent(intent["intent_id"],result.get("reason","EXCHANGE_REJECTED"),rejected=True)
            return {"status":"REJECTED","intent":intent}
        self.ledger.mark_submitted(intent["intent_id"],result.get("exchange_order_id"))
        for fill in result.get("fills",[]):
            if "cumulative_qty_units" in fill and not fill.get("economic_evidence"):
                current=self.ledger.get_intent(intent["intent_id"])
                delta=int(fill["cumulative_qty_units"])-int(current["filled_qty_units"])
                if delta<=0: continue
                fill=dict(fill); fill["qty_units"]=delta
            self.process_fill_event(intent["intent_id"],fill,source,result.get("exchange_order_id"))
        if result.get("cancelled"): self.ledger.cancel_intent(intent["intent_id"],result.get("reason","EXCHANGE_CANCELLED"))
        return {"status":"PROCESSED","intent":intent,"fills":result.get("fills",[])}

    def process_fill_event(self, intent_id, fill, source="WEBSOCKET_V4", exchange_order_id=None):
        event_key=f"{source}:{fill['fill_id']}"
        intent=self.ledger.get_intent(intent_id)
        changed=self.ledger.confirm_fill(
            intent_id,fill["fill_id"],fill["qty_units"],fill["price_ticks"],fill.get("fee_text","0"),exchange_order_id,
            dedupe_source=source,dedupe_event_key=event_key,dedupe_payload=fill,
            fee_asset=fill.get("fee_asset",""),realized_pnl_text=fill.get("realized_pnl_text",""),
            economic_evidence=fill.get("economic_evidence"),
        )
        if changed and self.exchange is not None:
            self.reconcile_exchange_basket(intent["symbol"],intent["position_side"],f"POST_ENTRY_FILL:{fill['fill_id']}")
        return changed

    def process_websocket_v4(self, event):
        event_type=str(event.get("event_type") or "").upper(); event_id=str(event.get("event_id") or "")
        if not event_id: raise ValueError("WEBSOCKET_EVENT_ID_REQUIRED")
        if event_type=="FILL": return self.process_fill_event(event["intent_id"],event,"WEBSOCKET_V4",event.get("exchange_order_id"))
        if event_type=="POSITION":
            return self.ledger.reconcile(
                event["symbol"],event["side"],event["exchange_qty_units"],event_id,
                dedupe_source="WEBSOCKET_V4",dedupe_payload=event,
            )
        raise ValueError("UNSUPPORTED_WEBSOCKET_EVENT")

    def process_binance_order_trade_update(self, message):
        """Binance USD-M user-data ORDER_TRADE_UPDATE를 Leg fill로 정규화한다."""
        if str(message.get("e") or "")!="ORDER_TRADE_UPDATE": return False
        order=message.get("o") if isinstance(message.get("o"),dict) else {}
        client_id=str(order.get("c") or "")
        intent=self.ledger.get_intent_by_client_order_id(client_id)
        last_qty=order.get("l") or "0"
        if Decimal(str(last_qty))<=0: return False
        exit_intent=None if intent else self.ledger.get_exit_by_client_order_id(client_id)
        if not intent and not exit_intent: return False
        leg=self.ledger.get_leg(exit_intent["leg_id"]) if exit_intent else None
        symbol=str(order.get("s") or (intent or leg)["symbol"])
        fill={
            "fill_id":f"TRADE:{order.get('t')}:{order.get('i')}",
            "qty_units":self.exchange.qty_to_units(symbol,last_qty),
            "price_ticks":self.exchange.price_to_ticks(symbol,order.get("L") or order.get("ap") or 0),
            "fee_text":str(order.get("n") or "0"),
            "fee_asset":str(order.get("N") or ""),
            "realized_pnl_text":str(order.get("rp") if order.get("rp") is not None else ""),
        }
        trade_id=str(order.get("t") or "")
        order_id=str(order.get("i") or "")
        trade_time_ms=int(order.get("T") or message.get("E") or 0)
        fill["economic_evidence"]={
            "source":"BINANCE_ORDER_TRADE_UPDATE",
            "source_event_id":fill["fill_id"],
            "trade_id":trade_id,
            "order_id":order_id,
            "exchange_ts":trade_time_ms//1000,
            "qty_text":str(last_qty),
            "cumulative_qty_units":self.exchange.qty_to_units(symbol,order.get("z") or last_qty),
            "price_text":str(order.get("L") or order.get("ap") or ""),
            "fee_text":fill["fee_text"],
            "fee_asset":fill["fee_asset"],
            "realized_pnl_text":fill["realized_pnl_text"],
            "realized_pnl_asset":str(order.get("realized_pnl_asset") or order.get("ma") or ""),
        }
        if exit_intent:
            changed=self.ledger.confirm_exit_fill(
                exit_intent["exit_intent_id"],fill["fill_id"],fill["qty_units"],fill["price_ticks"],fill["fee_text"],
                fill.get("fee_asset",""),fill.get("realized_pnl_text",""),order_id,
                fill["economic_evidence"],
            )
            if changed and self.exchange is not None:
                self.reconcile_exchange_basket(leg["symbol"],leg["position_side"],f"POST_EXIT_FILL:{fill['fill_id']}")
            return changed
        return self.process_fill_event(intent["intent_id"],fill,"WEBSOCKET_V4",str(order.get("i") or ""))

    def recover_pending(self, evidence_only=False):
        if self.exchange is None:
            rows=[{"intent_id":x["intent_id"],"action":"SHADOW_NO_EXCHANGE"} for x in self.ledger.pending_intents()]
            rows.extend({"exit_intent_id":x["exit_intent_id"],"action":"SHADOW_NO_EXCHANGE"} for x in self.ledger.pending_exit_intents())
            return rows
        if evidence_only:
            # 파일명: multi_strategy_runtime.py / 기존 복구 경로에서 체결 증거만 보강한다. 주문 제출은 호출하지 않는다.
            now=time.monotonic()
            previous=getattr(self,"_last_execution_evidence_sync",None)
            if previous is not None and now-float(previous)<300:
                return []
            self._last_execution_evidence_sync=now
            actions=[]
            for kind,fetch,key,cursor_key in (
                ("ENTRY_FILL",self.ledger.pending_intents,"intent_id","_entry_evidence_cursor"),
                ("EXIT_FILL",self.ledger.pending_exit_intents,"exit_intent_id","_exit_evidence_cursor"),
            ):
                cursor=str(getattr(self,cursor_key,"") or "")
                intents=fetch(evidence_only=True,after_id=cursor)
                if not intents and cursor:
                    intents=fetch(evidence_only=True)
                for intent in intents:
                    setattr(self,cursor_key,str(intent[key]))
                    try:
                        result=self.exchange.query_by_client_order_id(intent["client_order_id"],intent["symbol"])
                        if not result or result.get("found") is not True:
                            continue
                        if (str(result.get("exchange_order_id") or "")!=str(intent["exchange_order_id"])
                            or int(result.get("cumulative_qty_units") or 0)!=int(intent["filled_qty_units"])):
                            raise RuntimeError("COMPLETED_ORDER_EVIDENCE_IDENTITY_OR_QTY_CONFLICT")
                        for fill in result.get("fills",[]):
                            proof=fill.get("economic_evidence")
                            if not proof:
                                continue
                            if kind=="ENTRY_FILL":
                                self.ledger.confirm_fill(intent[key],fill["fill_id"],fill["qty_units"],fill["price_ticks"],fill.get("fee_text","0"),result["exchange_order_id"],fee_asset=fill.get("fee_asset",""),realized_pnl_text=fill.get("realized_pnl_text",""),economic_evidence=proof)
                            else:
                                self.ledger.confirm_exit_fill(intent[key],fill["fill_id"],fill["qty_units"],fill["price_ticks"],fill.get("fee_text","0"),fill.get("fee_asset",""),fill.get("realized_pnl_text",""),result["exchange_order_id"],proof)
                        actions.append({key:intent[key],"action":"ECONOMIC_EVIDENCE_CHECKED"})
                    except Exception as exc:
                        actions.append({key:intent[key],"action":"ECONOMIC_EVIDENCE_HOLD","error_type":type(exc).__name__})
            return actions
        actions=[]
        for intent in self.ledger.pending_intents():
            result=self._query_intent(intent)
            if not result or "found" not in result:
                actions.append({"intent_id":intent["intent_id"],"action":"UNRESOLVED_NO_RETRY"}); continue
            if result["found"] is False:
                if intent["status"]=="INTENT_SAVED": actions.append({"intent_id":intent["intent_id"],"action":"PROVEN_NOT_SUBMITTED_READY"})
                else: actions.append({"intent_id":intent["intent_id"],"action":"MISSING_AFTER_SUBMIT_RECONCILE_REQUIRED"})
                continue
            self._apply_order_result(intent,result,"RECOVERY_QUERY")
            actions.append({"intent_id":intent["intent_id"],"action":"RECOVERED"})

        for intent in self.ledger.pending_exit_intents():
            leg=self.ledger.get_leg(intent["leg_id"])
            try:
                result=self.exchange.query_by_client_order_id(intent["client_order_id"],leg["symbol"])
            except TypeError:
                result=self.exchange.query_by_client_order_id(intent["client_order_id"])
            if not result or "found" not in result:
                actions.append({"exit_intent_id":intent["exit_intent_id"],"action":"UNRESOLVED_NO_RETRY"}); continue
            if result["found"] is False:
                if intent["status"]=="INTENT_SAVED":
                    snapshot=self.ledger.basket_snapshot(leg["symbol"],leg["position_side"])
                    if snapshot and int(snapshot.get("mismatch") or 0)==0 and int(snapshot.get("ledger_qty_units") or 0)==int(snapshot.get("exchange_qty_units") or 0):
                        resumed=self.exit_leg(
                            intent["leg_id"],
                            int(intent["requested_qty_units"]),
                            intent["exit_signal_id"],
                            intent["reason"],
                        )
                        actions.append({"exit_intent_id":intent["exit_intent_id"],"action":"RESUBMITTED_AFTER_PROVEN_NOT_SUBMITTED","status":resumed.get("status")})
                    else:
                        actions.append({"exit_intent_id":intent["exit_intent_id"],"action":"PROVEN_NOT_SUBMITTED_READY_AFTER_RECONCILE"})
                else:
                    actions.append({"exit_intent_id":intent["exit_intent_id"],"action":"MISSING_AFTER_SUBMIT_RECONCILE_REQUIRED"})
                continue
            self.ledger.mark_exit_submitted(intent["exit_intent_id"],result.get("exchange_order_id",""),ambiguous=False)
            for fill in result.get("fills",[]):
                current=self.ledger.get_exit_by_client_order_id(intent["client_order_id"]) or intent
                amount=(int(fill.get("qty_units") or 0) if fill.get("economic_evidence") else int(fill.get("cumulative_qty_units") or fill.get("qty_units") or 0)-int(current.get("filled_qty_units") or 0))
                if amount>0:
                    self.ledger.confirm_exit_fill(
                        intent["exit_intent_id"],fill["fill_id"],amount,fill["price_ticks"],fill.get("fee_text","0"),
                        fill.get("fee_asset",""),fill.get("realized_pnl_text",""),result.get("exchange_order_id"),fill.get("economic_evidence"),
                    )
            if result.get("rejected"):
                self.ledger.cancel_exit_intent(intent["exit_intent_id"],result.get("reason","EXCHANGE_REJECTED"),rejected=True)
            elif result.get("cancelled"):
                self.ledger.cancel_exit_intent(intent["exit_intent_id"],result.get("reason","EXCHANGE_CANCELLED"),rejected=False)
            self.reconcile_exchange_basket(leg["symbol"],leg["position_side"],f"RECOVER_EXIT:{intent['exit_intent_id']}")
            actions.append({"exit_intent_id":intent["exit_intent_id"],"action":"RECOVERED"})
        return actions

    def reconcile_exchange_basket(self, symbol, side, source_event_id=""):
        """체결 직후 해당 basket만 즉시 거래소와 대조한다."""
        if self.exchange is None:
            return {"ok":True,"ledger_qty_units":0,"exchange_qty_units":0,"shadow":True}
        symbol=str(symbol or "").upper(); side=str(side or "").upper()
        rows=self.exchange.list_position_units() or []
        qty=0
        event_id=str(source_event_id or "")
        for row in rows:
            if str(row.get("symbol") or "").upper()==symbol and str(row.get("side") or "").upper()==side:
                qty=int(row.get("qty_units") or 0)
                if not event_id:
                    event_id=str(row.get("event_id") or "")
                break
        report=self.ledger.reconcile(symbol,side,qty,event_id or f"RECON:{symbol}:{side}:{int(time.time())}")
        if report.get("ok") is not True:
            self.disable_entry_admission("POST_FILL_BASKET_RECONCILE_MISMATCH")
        return report

    def reconcile_all(self):
        """기존 Basket 대조 + 증거가 완전한 수동 청산을 복구한다.

        거래소 수량을 그대로 믿어 원장을 덮어쓰지 않는다. REST 체결과 주문을
        교차 검증할 수 없으면 기존 mismatch/신규 진입 차단을 유지한다.
        """
        if self.exchange is None:
            return []
        exchange_rows = self.exchange.list_position_units()
        if not isinstance(exchange_rows, list):
            raise RuntimeError("EXCHANGE_POSITION_SNAPSHOT_INVALID")
        exchange_map = {}
        for row in exchange_rows:
            key = (str(row["symbol"]).upper(), str(row["side"]).upper())
            if key in exchange_map:
                raise RuntimeError("DUPLICATE_EXCHANGE_POSITION_BASKET")
            exchange_map[key] = {**row, "symbol": key[0], "side": key[1]}
        ledger_keys = {
            (str(row["symbol"]).upper(), str(row["position_side"]).upper())
            for row in self.ledger.active_legs()
        }
        reports = []
        for symbol, side in sorted(set(exchange_map) | ledger_keys):
            row = exchange_map.get((symbol, side)) or {"qty_units": 0, "event_id": ""}
            exchange_units = int(row.get("qty_units") or 0)
            legs = self.ledger.active_legs(symbol, side)
            owned_units = sum(int(leg["owned_qty_units"]) for leg in legs)
            if (
                self.mode == "EXCLUSIVE_LIVE"
                and owned_units > exchange_units >= 0
                and (len(legs) == 1 or exchange_units == 0)
            ):
                try:
                    for leg in legs:
                        snapshot = self.ledger.leg_management_snapshot(leg["leg_id"])
                        if (
                            snapshot.get("entry_intent_status") not in {"FILLED", "CANCELLED"}
                            or int(snapshot.get("pending_exit_qty_units") or 0) != 0
                        ):
                            raise RuntimeError("MANUAL_EXIT_HAS_PENDING_EXECUTION")
                    # 오래된 Leg도 최근 7일 이내 수동 청산은 검증한다.
                    # Binance 7일 조회창을 지키되 창 밖의 미검증 청산은 보류한다.
                    now_ms = int(time.time() * 1000)
                    start_ms = max(min(int(x["created_ts"]) for x in legs) * 1000 - 1000, now_ms - 7 * 86400 * 1000 + 60000)
                    if start_ms > now_ms:
                        raise RuntimeError("MANUAL_EXIT_TRADE_HISTORY_WINDOW_INVALID")
                    # Frozen order.py의 timestamp whitelist를 변경하지 않고
                    # 기존 시각 동기화 함수의 인증값을 명시 전달한다.
                    trades = self.exchange.order.safe_call(
                        self.exchange.order.client.get_account_trades,
                        symbol=symbol, startTime=start_ms, limit=1000,
                        **self.exchange.order._ts(),
                    )
                    orders = self.exchange.order.safe_call(
                        self.exchange.order.client.get_all_orders,
                        symbol=symbol, startTime=start_ms, limit=1000,
                    )
                    if (
                        not isinstance(trades, list) or not isinstance(orders, list)
                        or len(trades) >= 1000 or len(orders) >= 1000
                    ):
                        raise RuntimeError("MANUAL_EXIT_HISTORY_INCOMPLETE")
                    order_map = {}
                    for order in orders:
                        order_id = str(order.get("orderId") or "")
                        if not order_id or order_id in order_map:
                            raise RuntimeError("MANUAL_EXIT_ORDER_HISTORY_INVALID")
                        order_map[order_id] = order
                    with closing(self.ledger.connect()) as con:
                        known_clients = {
                            str(r[0]) for r in con.execute(
                                "SELECT client_order_id FROM r4_order_intents WHERE symbol=? AND position_side=?",
                                (symbol, side),
                            )
                        }
                        known_clients.update(
                            str(r[0]) for r in con.execute(
                                "SELECT x.client_order_id FROM r4_exit_intents x "
                                "JOIN r4_active_legs l ON l.leg_id=x.leg_id "
                                "WHERE l.symbol=? AND l.position_side=?",
                                (symbol, side),
                            )
                        )
                    exit_side = "SELL" if side == "LONG" else "BUY"
                    verified = []
                    trade_by_order = {}
                    for trade in trades:
                        try:
                            trade_id = str(trade["id"])
                            order_id = str(trade["orderId"])
                            t_ms = int(trade["time"])
                            qty = self.exchange.qty_to_units(symbol, trade["qty"])
                        except (KeyError, ValueError, TypeError) as exc:
                            raise RuntimeError("MANUAL_EXIT_TRADE_INVALID") from exc
                        if t_ms < min(int(x["created_ts"]) for x in legs) * 1000:
                            continue
                        if not trade_id or not order_id or qty <= 0:
                            raise RuntimeError("MANUAL_EXIT_TRADE_INVALID")
                        order = order_map.get(order_id)
                        if not order:
                            raise RuntimeError("MANUAL_EXIT_TRADE_ORDER_NOT_FOUND")
                        client_id = str(order.get("clientOrderId") or "")
                        if not client_id:
                            raise RuntimeError("MANUAL_EXIT_CLIENT_ORDER_MISSING")
                        if client_id in known_clients:
                            continue
                        if client_id.upper().startswith("R4"):
                            raise RuntimeError("MANUAL_EXIT_UNTRACKED_BOT_ORDER")
                        trade_side = str(trade.get("side") or "").upper()
                        position_side = str(trade.get("positionSide") or "BOTH").upper()
                        if trade_side != exit_side or position_side not in {"BOTH", side}:
                            raise RuntimeError("MANUAL_EXIT_EXTERNAL_TRADE_AMBIGUOUS")
                        if str(order.get("side") or "").upper() != exit_side:
                            raise RuntimeError("MANUAL_EXIT_ORDER_SIDE_MISMATCH")
                        if str(order.get("positionSide") or "BOTH").upper() not in {"BOTH", side}:
                            raise RuntimeError("MANUAL_EXIT_ORDER_POSITION_SIDE_MISMATCH")
                        if str(order.get("status") or "").upper() != "FILLED":
                            raise RuntimeError("MANUAL_EXIT_ORDER_NOT_SETTLED")
                        price_ticks = self.exchange.price_to_ticks(symbol, trade.get("price") or 0)
                        if price_ticks <= 0:
                            raise RuntimeError("MANUAL_EXIT_PRICE_INVALID")
                        trade_by_order[order_id] = trade_by_order.get(order_id, 0) + qty
                        verified.append({
                            "trade_id": trade_id,
                            "order_id": order_id,
                            "trade_time_ms": t_ms,
                            "qty_units": qty,
                            "price_ticks": price_ticks,
                            "fee_text": str(trade.get("commission") or "0"),
                            "fee_asset": str(trade.get("commissionAsset") or ""),
                            "realized_pnl_text": str(trade.get("realizedPnl") or "0"),
                        })
                    if not verified:
                        raise RuntimeError("MANUAL_EXIT_NO_VERIFIED_EXTERNAL_TRADES")
                    if len({(x["trade_id"], x["order_id"]) for x in verified}) != len(verified):
                        raise RuntimeError("MANUAL_EXIT_DUPLICATE_HISTORY")
                    for order_id, qty in trade_by_order.items():
                        if self.exchange.qty_to_units(symbol, order_map[order_id].get("executedQty") or 0) != qty:
                            raise RuntimeError("MANUAL_EXIT_ORDER_EXECUTION_QTY_MISMATCH")
                    # REST position을 재조회해 검증 중 보유량 변동 여부를 확인한다.
                    after = self.exchange.list_position_units()
                    if not isinstance(after, list):
                        raise RuntimeError("MANUAL_EXIT_POST_SNAPSHOT_FAILED")
                    matching = [x for x in after if str(x.get("symbol") or "").upper() == symbol and str(x.get("side") or "").upper() == side]
                    if len(matching) > 1 or (int(matching[0].get("qty_units") or 0) if matching else 0) != exchange_units:
                        raise RuntimeError("MANUAL_EXIT_EXCHANGE_QTY_MOVED")
                    self.ledger.acquire_basket_lock(symbol, side, self.owner_id)
                    try:
                        result = self.ledger.confirm_external_reduction(
                            [x["leg_id"] for x in legs], owned_units, exchange_units, verified,
                        )
                    finally:
                        self.ledger.release_basket_lock(symbol, side, self.owner_id)
                    logger.warning(
                        "[주문][R4][MANUAL_EXIT_RECONCILED] symbol=%s side=%s leg=%s qty=%s remaining=%s",
                        symbol, side, ",".join(x["leg_id"] for x in legs), result["recovered_qty_units"], exchange_units,
                    )
                except Exception as exc:
                    # 모호하면 수량을 임의 조정하지 않는다. 반복 경고는 상태 지속 시 제한.
                    key = (symbol, side, type(exc).__name__, str(exc))
                    cache = getattr(self, "_manual_reconcile_log_cache", {})
                    now = time.monotonic()
                    if now - cache.get(key, -1000) > 300:
                        logger.warning(
                            "[주문][R4][MANUAL_EXIT_HOLD] symbol=%s side=%s reason=%s",
                            symbol, side, str(exc)[:100],
                        )
                        cache[key] = now
                        if len(cache) > 256:
                            cache = {k: t for k, t in cache.items() if now - t < 600}
                        self._manual_reconcile_log_cache = cache
            report = self.ledger.reconcile(
                symbol, side, exchange_units,
                row.get("event_id", "") or f"RECON:{symbol}:{side}:{int(time.time())}",
            )
            report["symbol"] = symbol
            report["side"] = side
            reports.append(report)
        return reports

    def continuous_reconcile(self, interval_sec=15, cycles=None):
        count=0
        while cycles is None or count<int(cycles):
            self.heartbeat(); self.reconcile_all(); count+=1
            if cycles is None or count<int(cycles): time.sleep(max(1,int(interval_sec)))

    def enable_entry_admission_after_reconcile(self, reports, cutover_token=""):
        """Process restart 포함 매 기동마다 전체 basket reconcile PASS 뒤에만 신규 진입을 연다."""
        if self.mode!="EXCLUSIVE_LIVE":
            self.entry_admission_enabled=True
            return True
        token=str(cutover_token or self.cutover_token or "").strip()
        if not token or not self.ledger.validate_cutover_token(token):
            raise RuntimeError("R4_CUTOVER_TOKEN_INVALID_FOR_ENTRY_ENABLE")
        reports=list(reports or [])
        if any(row.get("ok") is not True for row in reports):
            self.entry_admission_enabled=False
            raise RuntimeError("R4_RECONCILE_MISMATCH_BLOCKS_ENTRY_ENABLE")
        self.entry_admission_enabled=True
        self.ledger.record_cutover_event(
            "NEW_ENTRY_ENABLED_AFTER_RECONCILE",
            "PASS",
            {
                "cutover_token":token,
                "basket_count":len(reports),
                "owner_id":self.owner_id,
            },
        )
        return True

    def disable_entry_admission(self, reason="SAFETY_BLOCK"):
        self.entry_admission_enabled=False
        if self.mode=="EXCLUSIVE_LIVE":
            self.ledger.record_cutover_event(
                "NEW_ENTRY_DISABLED",
                "INFO",
                {"cutover_token":self.cutover_token,"reason":str(reason)},
            )
        return True

    # ================= Tier Evidence / 자동 승격·강등 =================
    def record_scale_evidence(self, instance_id, evidence_name, passed, detail=None, evidence_key=""):
        return self.ledger.record_tier_evidence(instance_id,evidence_name,passed,detail or {},evidence_key)

    def evaluate_tier(self, instance_id, controller):
        """exact Strategy Instance의 서버 Tier 결정을 헌법 evidence만으로 계산한다."""
        controller=controller if isinstance(controller,dict) else {}
        instance=self.ledger.instance_state(instance_id)
        current=str(instance.get("tier") or "").upper()
        if current not in ("CANARY","SMALL","NORMAL"):
            return {"action":"BLOCK","reason":"SERVER_TIER_REQUIRES_CANARY_OR_ABOVE","current_tier":current}

        evidence=self.ledger.latest_tier_evidence(instance_id,current)
        required=[str(x or "").upper() for x in (controller.get("required_positive_scale_evidence") or []) if str(x or "").strip()]
        if not required:
            return {"action":"BLOCK","reason":"POSITIVE_SCALE_EVIDENCE_CONTRACT_MISSING","current_tier":current}

        # Ledger lifecycle 자체가 exact instance/tier에 귀속되므로 별도 추정치로 만들지 않는다.
        canary_lifecycle=self.ledger.instance_lifecycle_count(instance_id,"CANARY")>0
        small_lifecycle=self.ledger.instance_lifecycle_count(instance_id,"SMALL")>0
        evidence_pass={}
        missing=[]
        failed=[]
        for name in required:
            if name=="EXACT_STRATEGY_INSTANCE_LIFECYCLE":
                ok=canary_lifecycle if current=="CANARY" else (small_lifecycle if current=="SMALL" else True)
                evidence_pass[name]=bool(ok)
                if not ok:
                    missing.append(name)
                continue
            row=evidence.get(name)
            if not isinstance(row,dict):
                missing.append(name)
                continue
            ok=row.get("passed") is True
            evidence_pass[name]=ok
            if not ok:
                failed.append(name)

        hard_failed=[x for x in failed if x!="RELATIVE_TIER_COMPETITION_PASS"]
        lifecycle_reference_only = {
            "EXACT_STRATEGY_INSTANCE_LIFECYCLE",
            "ACTUAL_SMALL_LIVE_LIFECYCLE",
        }
        hard_failed = [
            x for x in hard_failed
            if x not in lifecycle_reference_only
        ]

        if hard_failed:
            if current=="CANARY":
                return {
                    "action":"HOLD",
                    "reason":"SAFETY_EVIDENCE_FAIL",
                    "current_tier":current,
                    "failed":hard_failed,
                    "entry_blocked":True,
                }
            return {
                "action":"HOLD",
                "reason":"SAFETY_EVIDENCE_FAIL",
                "current_tier":current,
                "failed":hard_failed,
                "entry_blocked":True,
            }

        failed = [
            x for x in failed
            if x not in lifecycle_reference_only
        ]

        if "RELATIVE_TIER_COMPETITION_PASS" in failed:
            if current=="NORMAL":
                return {"action":"DEMOTE","reason":"RELATIVE_COMPETITION_DEGRADED","current_tier":current,"target_tier":"SMALL"}
            if current=="SMALL":
                return {"action":"DEMOTE","reason":"RELATIVE_COMPETITION_DEGRADED","current_tier":current,"target_tier":"CANARY"}
            return {"action":"HOLD","reason":"RELATIVE_COMPETITION_NOT_PASS","current_tier":current}

        if current=="NORMAL":
            return {"action":"HOLD","reason":"ALREADY_NORMAL","current_tier":current}

        target="SMALL" if current=="CANARY" else "NORMAL"
        if current=="SMALL" and not small_lifecycle:
            if "ACTUAL_SMALL_LIVE_LIFECYCLE" not in missing:
                missing.append("ACTUAL_SMALL_LIVE_LIFECYCLE")

        # LIFECYCLE_REFERENCE_ONLY_NON_BLOCKING_V1
        missing = [
            x for x in missing
            if x not in lifecycle_reference_only
        ]

        if missing:
            return {
                "action":"HOLD",
                "reason":"POSITIVE_SCALE_EVIDENCE_MISSING",
                "current_tier":current,
                "target_tier":target,
                "missing":sorted(set(missing)),
            }

        maxima=controller.get("tier_maximum_eligible_strategies_per_timeframe") or {}
        try:
            maximum=int(maxima.get(target,0) or 0)
        except Exception:
            maximum=0
        if maximum<=0:
            return {"action":"HOLD","reason":"TIER_MAXIMUM_CONTRACT_MISSING","current_tier":current,"target_tier":target}
        occupied=self.ledger.tier_instance_count(instance["timeframe"],target)
        if occupied>=maximum:
            return {
                "action":"HOLD",
                "reason":"TARGET_TIER_MAXIMUM_REACHED",
                "current_tier":current,
                "target_tier":target,
                "occupied":occupied,
                "maximum":maximum,
            }

        return {
            "action":"PROMOTE",
            "reason":"ALL_REQUIRED_EXACT_INSTANCE_EVIDENCE_PASS",
            "current_tier":current,
            "target_tier":target,
            "evidence":evidence_pass,
        }

    def apply_tier(self, instance_id, target_tier, evidence_key):
        return self.ledger.set_tier(instance_id,target_tier,evidence_key)

    def evaluate_performance_tier(self, instance_id):
        return evaluate_performance_instance(self.ledger,instance_id)

    def record_performance_tier_evaluation(self, instance_id, decision):
        return self.ledger.record_performance_tier_evaluation(instance_id,decision)

    def exit_leg(self, leg_id, qty_units, exit_signal_id, reason="STRATEGY_EXIT"):
        leg=self.ledger.get_leg(leg_id)
        qty_units=int(qty_units)
        self.ledger.acquire_basket_lock(leg["symbol"],leg["position_side"],self.owner_id)
        try:
            if self.mode=="EXCLUSIVE_LIVE":
                snapshot=self.ledger.basket_snapshot(leg["symbol"],leg["position_side"])
                if not snapshot or int(snapshot.get("mismatch") or 0)!=0:
                    raise RuntimeError("EXIT_REQUIRES_RECONCILED_BASKET")
                if int(snapshot.get("ledger_qty_units") or 0)!=int(snapshot.get("exchange_qty_units") or 0):
                    raise RuntimeError("EXIT_REQUIRES_EXACT_BASKET_QTY")
                other_owned=max(0,int(snapshot.get("ledger_qty_units") or 0)-int(leg["owned_qty_units"]))
                post_exchange=int(snapshot.get("exchange_qty_units") or 0)-qty_units
                if post_exchange<other_owned:
                    raise RuntimeError("STRATEGY_EXIT_WOULD_TOUCH_OTHER_LEG_QTY")
            intent=self.ledger.reserve_exit(leg_id,exit_signal_id,qty_units,reason)
            if intent["status"] in ("SUBMITTED","PARTIAL_FILLED","FILLED","AMBIGUOUS"): return {"status":"IDEMPOTENT_EXISTING","intent":intent}
            if self.exchange is None: return {"status":"SHADOW_EXIT_SAVED","intent":intent}
            result=self.exchange.submit_reduce(leg,qty_units,intent["client_order_id"])
            if result.get("ambiguous"):
                self.ledger.mark_exit_submitted(intent["exit_intent_id"],ambiguous=True)
                resolved=self.exchange.query_by_client_order_id(intent["client_order_id"],leg["symbol"])
                if not resolved or resolved.get("found") is not True: return {"status":"AMBIGUOUS_NO_BLIND_RETRY","intent":intent}
                result=resolved
            if result.get("rejected"):
                self.ledger.cancel_exit_intent(intent["exit_intent_id"],result.get("reason","EXCHANGE_REJECTED"),rejected=True)
                self.reconcile_exchange_basket(leg["symbol"],leg["position_side"],f"EXIT_REJECT:{intent['exit_intent_id']}")
                return {"status":"REJECTED_RECONCILE_REQUIRED","intent":intent}
            self.ledger.mark_exit_submitted(intent["exit_intent_id"],result.get("exchange_order_id",""))
            for fill in result.get("fills",[]):
                current=self.ledger.get_exit_by_client_order_id(intent["client_order_id"]) or intent
                amount=(int(fill.get("qty_units") or 0) if fill.get("economic_evidence") else int(fill.get("cumulative_qty_units") or fill.get("qty_units") or 0)-int(current.get("filled_qty_units") or 0))
                if amount>0:
                    self.ledger.confirm_exit_fill(
                        intent["exit_intent_id"],fill["fill_id"],amount,fill["price_ticks"],fill.get("fee_text","0"),
                        fill.get("fee_asset",""),fill.get("realized_pnl_text",""),result.get("exchange_order_id"),fill.get("economic_evidence"),
                    )
            if result.get("cancelled"):
                self.ledger.cancel_exit_intent(intent["exit_intent_id"],result.get("reason","EXCHANGE_CANCELLED"),rejected=False)
            self.reconcile_exchange_basket(leg["symbol"],leg["position_side"],f"POST_EXIT_REST:{intent['exit_intent_id']}")
            return {"status":"PROCESSED","intent":intent}
        finally: self.ledger.release_basket_lock(leg["symbol"],leg["position_side"],self.owner_id)

    def reconcile(self, symbol, side, exchange_qty_units): return self.ledger.reconcile(symbol,side,exchange_qty_units)
