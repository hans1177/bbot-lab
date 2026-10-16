#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: r4_exchange_adapter.py
"""Frozen order.py를 수정하지 않고 사용하는 Binance USD-M R4 어댑터."""
from __future__ import annotations

# ================= 임포트 =================
import time
from decimal import Decimal, ROUND_DOWN

from order import Order


# ================= 거래소 어댑터 =================
class BinanceR4Exchange:
    def __init__(self, order=None):
        self.order = order or Order()

    def available_margin_usdt(self):
        """R4_LIVE_AVAILABLE_MARGIN_READ_V1: live exchange available resource."""
        now = time.time()
        cached = getattr(
            self.order,
            "_account_cache_20260609",
            None,
        )

        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and (now - float(cached[0])) <= 30.0
        ):
            account = cached[1]
        else:
            account = self.order.safe_call(
                self.order.client.account
            )

            if isinstance(account, dict):
                self.order._account_cache_20260609 = (
                    now,
                    account,
                )

        if not isinstance(account, dict):
            raise RuntimeError(
                "EXCHANGE_ACCOUNT_SNAPSHOT_FAILED_OR_INVALID"
            )

        if "availableBalance" not in account:
            raise RuntimeError(
                "EXCHANGE_AVAILABLE_BALANCE_MISSING"
            )

        try:
            available = Decimal(
                str(account.get("availableBalance"))
            )
        except Exception as exc:
            raise RuntimeError(
                "EXCHANGE_AVAILABLE_BALANCE_INVALID"
            ) from exc

        if not available.is_finite():
            raise RuntimeError(
                "EXCHANGE_AVAILABLE_BALANCE_NONFINITE"
            )

        return float(available)

    def _filter(self, symbol):
        row = self.order._filters.get(str(symbol).upper()) or {}
        step = Decimal(str(row.get("step") or "0"))
        tick = Decimal(str(row.get("tick") or "0"))
        if step <= 0 or tick <= 0:
            raise RuntimeError("EXCHANGE_FILTER_NOT_FOUND:" + str(symbol))
        return step, tick

    def qty_to_units(self, symbol, qty):
        step, _ = self._filter(symbol)
        return int((Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN))

    def units_to_qty(self, symbol, units):
        step, _ = self._filter(symbol)
        return format(step * int(units), "f")

    def price_to_ticks(self, symbol, price):
        _, tick = self._filter(symbol)
        return int((Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN))

    def current_symbol_leverage(self, symbol):
        """Read symbol leverage and whether an exchange basket is open."""
        symbol = str(symbol or "").upper()

        config_rows = self.order.safe_call(
            self.order.client.symbol_configuration,
            symbol=symbol,
        )

        if isinstance(config_rows, dict):
            config_rows = [config_rows]

        if not isinstance(config_rows, list):
            raise RuntimeError(
                "EXCHANGE_LEVERAGE_SNAPSHOT_FAILED_OR_INVALID"
            )

        current = 0

        for row in config_rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "EXCHANGE_LEVERAGE_ROW_INVALID"
                )

            if str(row.get("symbol") or "").upper() != symbol:
                continue

            try:
                current = int(float(row.get("leverage") or 0))
            except Exception:
                current = 0

            break

        if current < 1:
            raise RuntimeError(
                "EXCHANGE_CURRENT_LEVERAGE_UNKNOWN:"
                + symbol
            )

        position_rows = self.order.safe_call(
            self.order.client.get_position_risk,
            symbol=symbol,
        )

        if isinstance(position_rows, dict):
            position_rows = [position_rows]

        if not isinstance(position_rows, list):
            raise RuntimeError(
                "EXCHANGE_POSITION_SNAPSHOT_FAILED_OR_INVALID"
            )

        opened = False

        for row in position_rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "EXCHANGE_POSITION_ROW_INVALID"
                )

            if str(row.get("symbol") or "").upper() != symbol:
                continue

            try:
                amount = Decimal(
                    str(row.get("positionAmt") or 0)
                )
            except Exception:
                amount = Decimal("0")

            if amount != 0:
                opened = True
                break

        return {
            "leverage": current,
            "open": opened,
        }


    def ensure_common_leverage(
        self,
        symbol,
        leverage,
    ):
        """
        ?? exchange leverage? symbol ??.
        open basket?? ?? ???? ??,
        flat? ?? common target?? ???.
        """
        symbol = str(
            symbol or ""
        ).upper()

        target = int(leverage)

        if target < 1 or target > 125:
            raise RuntimeError(
                "COMMON_LEVERAGE_INVALID:"
                + symbol
            )

        state = self.current_symbol_leverage(
            symbol
        )

        current = int(
            state["leverage"]
        )

        if state["open"]:
            if current != target:
                raise RuntimeError(
                    "OPEN_BASKET_LEVERAGE_CHANGE_DEFERRED:"
                    + symbol
                )

            return current

        if current == target:
            return current

        result = self.order.safe_call(
            self.order.client.change_leverage,
            symbol=symbol,
            leverage=target,
        )

        if not isinstance(result, dict):
            raise RuntimeError(
                "COMMON_LEVERAGE_CHANGE_FAILED:"
                + symbol
            )

        verify = self.current_symbol_leverage(
            symbol
        )

        if (
            verify["open"]
            or int(
                verify["leverage"]
            )
            != target
        ):
            raise RuntimeError(
                "COMMON_LEVERAGE_CHANGE_VERIFY_FAILED:"
                + symbol
            )

        return target

    def submit(self, intent):
        symbol = intent["symbol"]
        side = "BUY" if intent["position_side"] == "LONG" else "SELL"
        kwargs = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self.units_to_qty(symbol, intent["requested_qty_units"]),
            "newClientOrderId": intent["client_order_id"],
        }
        result = self.order.safe_call(self.order.client.new_order, **kwargs)
        if not isinstance(result, dict):
            return {"ambiguous": True}
        return self._normalize_order(symbol, result)

    def submit_reduce(self, leg, qty_units, client_order_id):
        symbol = leg["symbol"]
        side = "SELL" if leg["position_side"] == "LONG" else "BUY"
        result = self.order.safe_call(
            self.order.client.new_order,
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=self.units_to_qty(symbol, qty_units),
            reduceOnly=True,
            newClientOrderId=client_order_id,
        )
        if not isinstance(result, dict):
            return {"ambiguous": True}
        return self._normalize_order(symbol, result)

    def query_by_client_order_id(self, client_order_id, symbol=None):
        # 파일명: r4_exchange_adapter.py / 오래된 주문도 정확한 고객 주문 식별자로 조회한다.
        if not symbol:
            return {"found":None}
        query=getattr(self.order.client,"query_order",None)
        if callable(query):
            row=self.order.safe_call(query,symbol=symbol,origClientOrderId=client_order_id)
            if not isinstance(row,dict):
                return {"found":None}
            if (str(row.get("clientOrderId") or row.get("origClientOrderId") or "")!=str(client_order_id)
                or str(row.get("symbol") or "").upper()!=str(symbol).upper()):
                return {"found":None}
            result=self._normalize_order(symbol,row)
            result["found"]=True
            return result
        rows=self.order.safe_call(self.order.client.get_all_orders,symbol=symbol,limit=100)
        if not isinstance(rows,list):
            return {"found":None}
        for row in rows:
            if str(row.get("clientOrderId") or row.get("origClientOrderId") or "")==str(client_order_id):
                result=self._normalize_order(symbol,row)
                result["found"]=True
                return result
        # 최근 100건에 없다는 이유만으로 미제출을 확정하지 않는다.
        return {"found":None}

    def _normalize_order(self, symbol, row):
        # 파일명: r4_exchange_adapter.py / 주문별 실제 체결을 확인한 경우에만 경제 증거를 붙인다.
        status=str(row.get('status') or 'NEW').upper()
        order_id=str(row.get('orderId') or '')
        cumulative=self.qty_to_units(symbol,row.get('executedQty') or 0)
        avg_price=row.get('avgPrice') or row.get('price') or 0
        out={
            'exchange_order_id':order_id,'status':status,
            'cumulative_qty_units':cumulative,
            'price_ticks':self.price_to_ticks(symbol,avg_price) if Decimal(str(avg_price or 0))>0 else 0,
            'fills':[],'cancelled':status in ('CANCELED','EXPIRED'),
            'rejected':status=='REJECTED',
        }
        if cumulative<=0:
            return out
        # 체결 조회 지연·실패 시 기존 수량 복구는 유지하고, 비용을 검증했다고 표시하지 않는다.
        out['fills']=[{'fill_id':f'ORDER:{order_id}:CUM:{cumulative}',
            'cumulative_qty_units':cumulative,'price_ticks':out['price_ticks'],'fee_text':'0'}]
        try:
            client=getattr(self.order,'client',None)
            query=getattr(client,'get_account_trades',None)
            if not callable(query) or not order_id:
                return out
            rows=self.order.safe_call(query,symbol=symbol,orderId=int(order_id),limit=1000)
            if not isinstance(rows,list) or not rows or len(rows)>=1000:
                return out
            seen=set();fills=[];units=0;raw_total=Decimal('0')
            for trade in sorted(rows,key=lambda t:(int(t['time']),int(t['id']))):
                tid=str(trade['id'])
                if (str(trade.get('symbol') or '').upper()!=str(symbol).upper()
                    or str(trade.get('orderId') or '')!=order_id or tid in seen
                    or (row.get('side') and trade.get('side')!=row['side'])
                    or int(trade['time'])<=0):
                    return out
                seen.add(tid)
                qty=Decimal(str(trade['qty']));price=Decimal(str(trade['price']))
                fee=Decimal(str(trade['commission']));realized=Decimal(str(trade['realizedPnl']))
                if not all(v.is_finite() for v in (qty,price,fee,realized)) or qty<=0 or price<=0 or fee<0:
                    return out
                qty_units=self.qty_to_units(symbol,qty)
                ticks=self.price_to_ticks(symbol,price)
                if qty_units<=0 or ticks<=0 or Decimal(self.units_to_qty(symbol,qty_units))!=qty:
                    return out
                units+=qty_units;raw_total+=qty
                fill_id=f'TRADE:{tid}:{order_id}'
                evidence={
                    'source':'BINANCE_ACCOUNT_TRADES','source_event_id':fill_id,
                    'trade_id':tid,'order_id':order_id,'exchange_ts':int(trade['time'])//1000,
                    'qty_text':str(trade['qty']),'price_text':str(trade['price']),
                    'fee_text':str(trade['commission']),'fee_asset':str(trade.get('commissionAsset') or ''),
                    'realized_pnl_text':str(trade['realizedPnl']),
                    'realized_pnl_asset':str(trade.get('marginAsset') or ''),
                    'cumulative_qty_units':units,
                }
                fills.append({'fill_id':fill_id,'qty_units':qty_units,'price_ticks':ticks,
                    'fee_text':evidence['fee_text'],'fee_asset':evidence['fee_asset'],
                    'realized_pnl_text':evidence['realized_pnl_text'],'economic_evidence':evidence})
            if units==cumulative and raw_total==Decimal(str(row.get('executedQty') or 0)):
                out['fills']=fills
        except (KeyError,TypeError,ValueError,ArithmeticError,AttributeError):
            pass
        return out

    # ================= Cutover / Reconcile 읽기 전용 Snapshot =================
    def list_position_units(self):
        """Live reconcile용 포지션 snapshot. API 실패를 flat으로 오인하지 않고 fail-closed 한다."""
        rows = self.order.safe_call(self.order.client.get_position_risk)
        if not isinstance(rows, list):
            raise RuntimeError("EXCHANGE_POSITION_SNAPSHOT_FAILED_OR_INVALID")

        out = []
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("EXCHANGE_POSITION_ROW_INVALID")
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            amount = Decimal(str(row.get("positionAmt") or 0))
            if amount == 0:
                continue
            raw_position_side = str(row.get("positionSide") or "BOTH").upper()
            side = raw_position_side if raw_position_side in ("LONG", "SHORT") else ("LONG" if amount > 0 else "SHORT")
            entry_price = Decimal(str(row.get("entryPrice") or 0))
            out.append({
                "symbol": symbol,
                "side": side,
                "position_side": raw_position_side,
                "qty_units": self.qty_to_units(symbol, abs(amount)),
                "qty_text": format(abs(amount), "f"),
                "entry_price_ticks": self.price_to_ticks(symbol, entry_price) if entry_price > 0 else 0,
                "entry_price_text": format(entry_price, "f"),
                "update_time": int(row.get("updateTime") or 0),
                "event_id": f"REST_POSITION:{symbol}:{row.get('updateTime') or ''}:{amount}:{raw_position_side}",
            })
        return out

    def list_open_orders(self):
        """?? ?? ??? ??? SDK ?? API? ?? ?? ????. ??/?? ??? ??? ????."""
        rows = self.order.safe_call(self.order.client.get_orders)
        if not isinstance(rows, list):
            raise RuntimeError("EXCHANGE_OPEN_ORDER_SNAPSHOT_FAILED_OR_INVALID")

        out = []
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("EXCHANGE_OPEN_ORDER_ROW_INVALID")
            status = str(row.get("status") or "").upper()
            if status not in ("NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"):
                continue
            symbol = str(row.get("symbol") or "").upper()
            out.append({
                "symbol": symbol,
                "side": str(row.get("side") or "").upper(),
                "position_side": str(row.get("positionSide") or "BOTH").upper(),
                "status": status,
                "reduce_only": bool(row.get("reduceOnly")),
                "client_order_id": str(row.get("clientOrderId") or row.get("origClientOrderId") or ""),
                "exchange_order_id": str(row.get("orderId") or ""),
                "orig_qty": str(row.get("origQty") or "0"),
                "executed_qty": str(row.get("executedQty") or "0"),
                "update_time": int(row.get("updateTime") or row.get("time") or 0),
            })
        return out

