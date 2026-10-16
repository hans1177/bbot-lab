#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, logging, math, threading, os, json, traceback
from binance.um_futures import UMFutures
from config import Config
from binance.client import Client
from dotenv import load_dotenv

logger = logging.getLogger("order")

load_dotenv("/home/ec2-user/bot/.env")

class Order:
    def __init__(self, api_key=None, api_secret=None):
        key = api_key or os.getenv("BINANCE_API_KEY")
        secret = api_secret or os.getenv("BINANCE_API_SECRET")

        if not key or not secret:
            raise RuntimeError("BINANCE API KEY / SECRET missing")

        self.client = UMFutures(
            key=key,
            secret=secret,
            show_limit_usage=True,
        )
        self.v3 = Client(key, secret)
        self.v3.FUTURES_URL = "https://fapi.binance.com/fapi"

        self.config = Config("/home/ec2-user/bot/config.json")

        self._filters = {}
        self._leverage_cache = {}
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._api_block_until = 0.0
        self._api_fail_streak = 0

        self.LEV_LOW = 5
        self.LEV_MID = 5
        self.LEV_HIGH = 5

        self._load_filters()
        logger.info("[주문][INIT] Order 엔진 초기화 완료")

    # ==========================================================
    # 공통 유틸
    # ==========================================================
    def _ts(self):
        return {"timestamp": int(time.time() * 1000), "recvWindow": 5000}

    def _api_delay(self):
        diff = time.time() - self._last_call
        if diff < 0.12:
            time.sleep(0.12 - diff)
        self._last_call = time.time()

    def _norm_usage_headers(self, usage):
        out = {}
        if not isinstance(usage, dict):
            return out

        for k, v in usage.items():
            try:
                out[str(k).lower()] = str(v)
            except Exception:
                continue
        return out

    def _log_limit_usage(self, tag, usage):
        u = self._norm_usage_headers(usage)
        if not u:
            return

        used_1m = u.get("x-mbx-used-weight-1m", "-")
        order_10s = u.get("x-mbx-order-count-10s", "-")
        order_1m = u.get("x-mbx-order-count-1m", "-")

        logger.info(
            f"[주문][API_USAGE] {tag} "
            f"used_weight_1m={used_1m} order_10s={order_10s} order_1m={order_1m}"
        )

    def _unwrap_limit_usage(self, res, tag):
        if isinstance(res, dict) and "limit_usage" in res and "data" in res:
            self._log_limit_usage(tag, res.get("limit_usage", {}))
            return res.get("data")
        return res

    def _log_error_limit_usage(self, tag, err):
        header = getattr(err, "header", None)
        if not isinstance(header, dict):
            return
        self._log_limit_usage(f"{tag}_ERR", header)

    # ==========================================================
    # SAFE API CALL (timestamp 허용 함수만 주입)
    # ==========================================================
    def safe_call(self, func, **kwargs):
        """
        Binance UMFutures SDK는
        - 어떤 API는 timestamp 필요
        - 어떤 API는 timestamp 받으면 바로 예외

        반드시 화이트리스트 방식으로 제어
        """

        TS_REQUIRED = (
            self.client.new_order,
            self.client.change_leverage,
            self.client.get_position_risk,
            self.client.account,
            self.client.get_all_orders,
            self.client.cancel_order,
        )

        with self._lock:
            now = time.time()
            block_until = float(getattr(self, "_api_block_until", 0.0) or 0.0)

            if block_until > now:
                remain = round(block_until - now, 2)
                logger.warning(
                    f"[주문][API_BLOCKED] {func.__name__} remain={remain}s"
                )
                return None

            self._api_delay()

            if func == self.client.mark_price:
                logger.error(
                    "[주문][FATAL] mark_price called via safe_call - STACK TRACE",
                    exc_info=True
                )
                return None

            try:
                if func in TS_REQUIRED and "timestamp" not in kwargs:
                    kwargs.update(self._ts())

                logger.info(
                    f"[주문][API_CALL] {func.__name__} kwargs={kwargs}"
                )

                res = func(**kwargs)
                res = self._unwrap_limit_usage(res, func.__name__)

                logger.info(
                    f"[주문][API_OK] {func.__name__}"
                )

                setattr(self, "_api_fail_streak", 0)
                return res

            except Exception as e:
                code = getattr(e, "code", None)
                msg = str(e)
                fail_streak = int(getattr(self, "_api_fail_streak", 0) or 0) + 1
                setattr(self, "_api_fail_streak", fail_streak)

                self._log_error_limit_usage(func.__name__, e)

                logger.error(
                    f"[주문][API_FAIL] {func.__name__} code={code} err={e}",
                    exc_info=True
                )

                is_rate_limit = (
                    str(code) in ("429", "-1003", "-1008")
                    or "429" in msg
                    or "-1003" in msg
                    or "Too many requests" in msg
                    or "Too much request weight used" in msg
                    or "way too much request weight used" in msg
                )

                if is_rate_limit:
                    cooldown = min(60, 5 * fail_streak)
                    block_until = time.time() + cooldown
                    setattr(self, "_api_block_until", block_until)

                    logger.warning(
                        f"[주문][API_RATE_LIMIT] {func.__name__} "
                        f"code={code} cooldown={cooldown}s fail_streak={fail_streak}"
                    )

                return None
    # ==========================================================
    # 필터 / 수량 보정
    # ==========================================================
    def _load_filters(self):
        info = self.client.exchange_info()
        info = self._unwrap_limit_usage(info, "exchange_info")

        for s in info["symbols"]:
            step = tick = min_qty = min_notional = 0
            prec = s.get("pricePrecision")

            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif f["filterType"] == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f["notional"])

            self._filters[s["symbol"]] = {
                "step": step,
                "tick": tick,
                "min_qty": min_qty,
                "min_notional": min_notional,
                "prec": prec,
            }

        logger.info(f"[주문][FILTER] 로딩 완료 ({len(self._filters)} symbols)")

    def _adj_qty(self, sym, qty, price=None):
        f = self._filters.get(sym)
        if not f:
            return 0

        step = f["step"]
        min_qty = f["min_qty"]

        q = math.floor(qty / step) * step
        q = round(q, int(abs(math.log10(step))))

        if q < min_qty:
            return 0

        if price and q * price < f["min_notional"]:
            return 0

        return q

    def _adj_price(self, sym, price):
        f = self._filters.get(sym)
        if not f:
            return price

        tick = f["tick"]
        if tick == 0:
            return price

        p = math.floor(price / tick) * tick
        prec = int(abs(math.log10(tick))) if tick < 1 else 0
        return round(p, prec)

    # ==========================================================
    # 진입
    # ==========================================================
    def ensure_leverage(self, sym, opinion):
        # ==========================================================
        # ORDER_LEVERAGE_DIRECT_INPUT_20260430
        # 파일: order.py
        # 기능: LOW/MID/HIGH 하드코딩 레버 제거
        # 기준: runner/leverage.py에서 전달받은 최종 leverage 값을 그대로 사용
        # 로그: /home/ec2-user/bot/logs/runner.log
        # ==========================================================
        try:
            lev = int(round(float(opinion or 1)))
        except Exception:
            lev = 1

        lev = max(1, min(125, lev))

        if self._leverage_cache.get(sym) == lev:
            logger.info(f"[주문][LEV_SKIP][CACHE] {sym} lev={lev}")
            return

        res = self.safe_call(self.client.change_leverage, symbol=sym, leverage=lev)
        if res:
            self._leverage_cache[sym] = lev
            logger.info(f"[주문][LEV_OK] {sym} lev={lev}")

    def compute_entry(self, config, sym, side, opinion, weight=1.0):
        # ORDER_ACCOUNT_TTL_CACHE_20260609
        try:
            _now_acc_20260609 = time.time()
            _acc_cache_20260609 = getattr(self, "_account_cache_20260609", None)
            if (
                isinstance(_acc_cache_20260609, tuple)
                and len(_acc_cache_20260609) == 2
                and (_now_acc_20260609 - float(_acc_cache_20260609[0])) <= 30.0
            ):
                bal = _acc_cache_20260609[1]
            else:
                bal = self.safe_call(self.client.account)
                self._account_cache_20260609 = (_now_acc_20260609, bal)
        except Exception:
            bal = self.safe_call(self.client.account)
        if not bal:
            logger.error(f"[주문][CALC_FAIL] account 조회 실패")
            return None

        usdt = float(bal.get("availableBalance", 0))

        try:
            logger.info(f"[주문][MARK_PRICE_REQ] {sym}")

            with self._lock:
                now = time.time()
                block_until = float(getattr(self, "_api_block_until", 0.0) or 0.0)

                if block_until > now:
                    remain = round(block_until - now, 2)
                    logger.warning(f"[주문][MARK_PRICE_BLOCKED] {sym} remain={remain}s")
                    return None

                self._api_delay()
                mp = self.client.mark_price(symbol=sym)
                mp = self._unwrap_limit_usage(mp, f"mark_price:{sym}")

            if not mp or "markPrice" not in mp:
                logger.error(f"[주문][MARK_PRICE_INVALID] {sym} res={mp}")
                return None

            price = float(mp["markPrice"])

            logger.info(f"[주문][MARK_PRICE_OK] {sym} price={price}")

        except Exception as e:
            self._log_error_limit_usage(f"mark_price:{sym}", e)
            logger.error(
                f"[주문][MARK_PRICE_FAIL] {sym} err={e}",
                exc_info=True
            )
            return None

        symbols_cfg = config.get("SYMBOLS", {})
        if isinstance(symbols_cfg, dict):
            active_count = sum(1 for _, v in symbols_cfg.items() if v)
        else:
            active_count = len(symbols_cfg)

        if active_count <= 0:
            logger.error(f"[주문][CALC_FAIL] active symbol count invalid={active_count}")
            return None

        try:
            weight = float(weight or 1.0)
        except Exception:
            weight = 1.0

        if weight <= 0:
            weight = 1.0

        base_alloc_usdt = usdt / active_count
        alloc_usdt = base_alloc_usdt * weight

        # ==========================================================
        # ORDER_ZERO_AVAILABLE_ALLOC_GUARD_20260609
        # available/base_alloc/alloc_usdt 가 0 이하이면 min_notional 보정 금지.
        # 잔고 0 상태에서 XLMUSDT 같은 저가 심볼이 notional=6으로 주문되는 문제 차단.
        # ==========================================================
        if usdt <= 0.0 or base_alloc_usdt <= 0.0 or alloc_usdt <= 0.0:
            logger.info(
                "[주문][CALC_SKIP][ZERO_AVAILABLE_ALLOC] %s | "
                "available=%.6f active_count=%s base_alloc_usdt=%.6f "
                "weight=%.4f alloc_usdt=%.6f",
                sym,
                usdt,
                active_count,
                base_alloc_usdt,
                weight,
                alloc_usdt,
            )
            return None


        # ==========================================================
        # ORDER_LEVERAGE_DIRECT_INPUT_20260430
        # 파일: order.py
        # 기능: 주문금액 계산도 전달받은 최종 leverage 값으로만 계산
        # ==========================================================
        try:
            lev = int(round(float(opinion or 1)))
        except Exception:
            lev = 1

        lev = max(1, min(125, lev))

        notional = alloc_usdt * lev

        f = self._filters.get(sym, {})
        min_notional = f.get("min_notional", 5)

        if notional < min_notional:
            notional = min_notional * 1.2

        raw_qty = notional / price
        qty = self._adj_qty(sym, raw_qty, price)

        if qty <= 0:
            logger.error(
                "[주문][CALC_QTY_ZERO] %s | "
                "available=%.6f active_count=%s base_alloc_usdt=%.6f weight=%.4f "
                "alloc_usdt=%.6f lev=%s notional=%.6f price=%.6f "
                "raw_qty=%.8f adj_qty=%.8f step=%s min_qty=%s min_notional=%s",
                sym,
                usdt,
                active_count,
                base_alloc_usdt,
                weight,
                alloc_usdt,
                lev,
                notional,
                price,
                raw_qty,
                qty,
                f.get("step"),
                f.get("min_qty"),
                f.get("min_notional"),
            )
            return None

        logger.info(
            "[주문][CALC_OK] %s | available=%.6f active_count=%s "
            "base_alloc_usdt=%.6f weight=%.4f alloc_usdt=%.6f "
            "lev=%s notional=%.6f price=%.6f raw_qty=%.8f adj_qty=%.8f opinion=%.2f",
            sym,
            usdt,
            active_count,
            base_alloc_usdt,
            weight,
            alloc_usdt,
            lev,
            notional,
            price,
            raw_qty,
            qty,
            opinion,
        )

        return {
            "qty": qty,
            "weight": weight,
            "lev": lev,
            "notional": notional,
            "price": price,
            "available": usdt,
            "active_count": active_count,
            "base_alloc_usdt": base_alloc_usdt,
            "alloc_usdt": alloc_usdt,
        }

    def market_entry(self, sym, side, qty, opinion):
        logger.info(f"[주문][ENTRY_REQ] {sym} side={side} qty={qty}")

        self.ensure_leverage(sym, opinion)

        res = self.safe_call(
            self.client.new_order,
            symbol=sym,
            side=side,
            type="MARKET",
            quantity=str(qty),
        )

        if not res:
            logger.error(f"[주문][ENTRY_FAIL] {sym}")
            return None

        logger.info(f"[주문][ENTRY_OK] {sym} orderId={res.get('orderId')}")
        return res

    def wait_fill(self, sym, order_id, timeout=5):
        start = time.time()
        while time.time() - start < timeout:
            orders = self.safe_call(self.client.get_all_orders, symbol=sym, limit=20)
            if not orders:
                time.sleep(0.5)
                continue

            for o in orders:
                if str(o.get("orderId")) == str(order_id):
                    if o.get("status") == "FILLED":
                        logger.info(f"[주문][FILL_OK] {sym} orderId={order_id}")
                        return True
            time.sleep(0.5)

        logger.error(f"[주문][FILL_TIMEOUT] {sym} orderId={order_id}")
        return False

    # ==========================================================
    # 청산 (TP 데몬 / SL 데몬 공용)
    # ==========================================================
    def close_position(self, sym, qty, pos_side):
        # ==========================================================
        # ORDER_REDUCEONLY_FRESH_CLOSE_20260528
        # - reduceOnly 청산 전 거래소 positionRisk fresh 조회
        # - 요청 qty가 실제 positionAmt보다 크면 현재 수량으로 축소
        # - 이미 flat이면 CLOSE_SKIP 처리
        # - -2022 ReduceOnly reject 발생 시 fresh 재조회 후 already-flat 성공성 처리/1회 재시도
        # ==========================================================
        pos_side = str(pos_side or "").upper()
        side = "SELL" if pos_side == "LONG" else "BUY"

        def _fresh_close_qty():
            try:
                rows = self.safe_call(self.client.get_position_risk, symbol=sym)
                if not rows:
                    logger.info(f"[주문][CLOSE_SKIP][FRESH_POS_FAIL] {sym} pos={pos_side}")
                    return 0.0

                if isinstance(rows, dict):
                    rows2 = [rows]
                else:
                    rows2 = list(rows or [])

                matched_amt = 0.0
                for r in rows2:
                    if str(r.get("symbol") or "").upper() != str(sym).upper():
                        continue

                    ps = str(r.get("positionSide") or "BOTH").upper()
                    try:
                        amt = float(r.get("positionAmt") or 0.0)
                    except Exception:
                        amt = 0.0

                    if pos_side == "LONG":
                        if ps in ("LONG", "BOTH", "") and amt > 0:
                            matched_amt = abs(amt)
                            break
                    elif pos_side == "SHORT":
                        if ps in ("SHORT", "BOTH", "") and amt < 0:
                            matched_amt = abs(amt)
                            break
                    else:
                        if abs(amt) > 0:
                            matched_amt = abs(amt)
                            break

                try:
                    req_qty = abs(float(qty or 0.0))
                except Exception:
                    req_qty = 0.0

                close_qty = min(req_qty, matched_amt) if matched_amt > 0 else 0.0
                close_qty = self._adj_qty(sym, close_qty)

                logger.info(
                    f"[주문][CLOSE_FRESH_QTY] {sym} pos={pos_side} "
                    f"req_qty={req_qty} pos_qty={matched_amt} close_qty={close_qty}"
                )

                return float(close_qty or 0.0)
            except Exception as e:
                logger.error(f"[주문][CLOSE_FRESH_QTY_FAIL] {sym} err={e}", exc_info=True)
                return 0.0

        def _send_reduce_order(close_qty, retry=0):
            kwargs = {
                "symbol": sym,
                "side": side,
                "type": "MARKET",
                "quantity": str(close_qty),
                "reduceOnly": True,
            }

            with self._lock:
                now = time.time()
                block_until = float(getattr(self, "_api_block_until", 0.0) or 0.0)
                if block_until > now:
                    remain = round(block_until - now, 2)
                    logger.warning(f"[주문][CLOSE_BLOCKED] {sym} remain={remain}s")
                    return None, None

                self._api_delay()

                if "timestamp" not in kwargs:
                    kwargs.update(self._ts())

                logger.info(f"[주문][API_CALL] new_order kwargs={kwargs}")

                try:
                    res = self.client.new_order(**kwargs)
                    res = self._unwrap_limit_usage(res, "new_order")
                    logger.info("[주문][API_OK] new_order")
                    setattr(self, "_api_fail_streak", 0)
                    return res, None
                except Exception as e:
                    code = getattr(e, "code", None)
                    msg = str(e)
                    self._log_error_limit_usage("new_order", e)

                    reduce_reject = (
                        str(code) == "-2022"
                        or "-2022" in msg
                        or "ReduceOnly Order is rejected" in msg
                    )

                    if reduce_reject:
                        logger.warning(
                            f"[주문][CLOSE_REDUCEONLY_REJECT] {sym} "
                            f"pos={pos_side} qty={close_qty} retry={retry} err={e}"
                        )
                    else:
                        logger.error(
                            f"[주문][API_FAIL] new_order code={code} err={e}",
                            exc_info=True,
                        )

                    return None, e

        fresh_qty = _fresh_close_qty()
        if fresh_qty <= 0:
            logger.info(f"[주문][CLOSE_SKIP][ALREADY_FLAT] {sym} pos={pos_side} req_qty={qty}")
            return {"orderId": "ALREADY_FLAT", "symbol": sym, "already_flat": True}

        logger.info(
            f"[주문][CLOSE_REQ] {sym} pos={pos_side} side={side} "
            f"req_qty={qty} fresh_qty={fresh_qty}"
        )

        res, err = _send_reduce_order(fresh_qty, retry=0)

        if res:
            logger.info(f"[주문][CLOSE_OK] {sym} orderId={res.get('orderId')}")
            return res

        msg = str(err or "")
        reduce_reject = (
            "-2022" in msg
            or "ReduceOnly Order is rejected" in msg
        )

        if reduce_reject:
            retry_qty = _fresh_close_qty()
            if retry_qty <= 0:
                logger.info(f"[주문][CLOSE_SKIP][REDUCEONLY_ALREADY_FLAT] {sym} pos={pos_side}")
                return {"orderId": "ALREADY_FLAT_AFTER_REDUCEONLY", "symbol": sym, "already_flat": True}

            logger.info(
                f"[주문][CLOSE_RETRY][REDUCEONLY] {sym} pos={pos_side} "
                f"retry_qty={retry_qty}"
            )
            res2, err2 = _send_reduce_order(retry_qty, retry=1)

            if res2:
                logger.info(f"[주문][CLOSE_OK][RETRY] {sym} orderId={res2.get('orderId')}")
                return res2

        logger.error(f"[주문][CLOSE_FAIL] {sym}")
        return None

    # ==========================================================
    # SL / TP (SL 데몬 전용)
    # ==========================================================
    def register_sl(self, sym, qty, pos_side, sl_price):
        sl_price = self._adj_price(sym, sl_price)
        exit_side = Client.SIDE_SELL if pos_side == "LONG" else Client.SIDE_BUY

        logger.info(
            f"[주문][SL_REQ] {sym} pos={pos_side} stop={sl_price}"
        )

        try:
            self.v3.futures_create_order(
                symbol=sym,
                side=exit_side,
                type="STOP_MARKET",
                stopPrice=str(sl_price),
                closePosition=True,
                workingType="MARK_PRICE",
            )
            logger.info(f"[주문][SL_OK] {sym}")
            return True
        except Exception as e:
            logger.error(f"[주문][SL_FAIL] {sym} err={e}", exc_info=True)
            return False

    def register_tp(self, sym, qty, pos_side, tp_price):
        tp_price = self._adj_price(sym, tp_price)
        exit_side = Client.SIDE_SELL if pos_side == "LONG" else Client.SIDE_BUY

        try:
            tp_price = float(tp_price)

            logger.info(
                f"[주문][TP_REQ] {sym} pos={pos_side} stop={tp_price}"
            )

            res = self.v3.futures_create_order(
                symbol=sym,
                side=exit_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=tp_price,
                closePosition=True,
                workingType="MARK_PRICE",
            )

            logger.info(
                f"[주문][TP_OK] {sym} resp={res}"
            )

            return True

        except Exception as e:
            logger.error(
                f"[주문][TP_FAIL] {sym} pos={pos_side} tp={tp_price} err={str(e)}"
            )

            return False

    # ==========================================================
    # SL 데몬용: reduceOnly 활성 주문 조회
    # ==========================================================
    def raw_open_orders(self, sym):
        with self._lock:
            now = time.time()
            block_until = float(getattr(self, "_api_block_until", 0.0) or 0.0)

            if block_until > now:
                remain = round(block_until - now, 2)
                logger.warning(
                    f"[주문][OPEN_ORDERS_BLOCKED] {sym} remain={remain}s"
                )
                return []

            self._api_delay()

            try:
                res = self.client._request(
                    "GET",
                    "/fapi/v1/openOrders",
                    signed=True,
                    data={
                        "symbol": sym,
                        "timestamp": int(time.time() * 1000),
                        "recvWindow": 5000,
                    },
                )

                if not isinstance(res, list):
                    return []

                out = [
                    o
                    for o in res
                    if o.get("reduceOnly")
                    and o.get("status") in ("NEW", "PARTIALLY_FILLED")
                ]

                setattr(self, "_api_fail_streak", 0)
                logger.info(f"[주문][OPEN_ORDERS] {sym} cnt={len(out)}")
                return out

            except Exception as e:
                code = getattr(e, "code", None)
                msg = str(e)
                fail_streak = int(getattr(self, "_api_fail_streak", 0) or 0) + 1
                setattr(self, "_api_fail_streak", fail_streak)

                logger.error(
                    f"[주문][OPEN_ORDERS_FAIL] {sym} code={code} err={e}",
                    exc_info=True
                )

                is_rate_limit = (
                    str(code) in ("429", "-1003", "-1008")
                    or "429" in msg
                    or "-1003" in msg
                    or "Too many requests" in msg
                    or "Too much request weight used" in msg
                    or "way too much request weight used" in msg
                )

                if is_rate_limit:
                    cooldown = min(60, 5 * fail_streak)
                    block_until = time.time() + cooldown
                    setattr(self, "_api_block_until", block_until)

                    logger.warning(
                        f"[주문][OPEN_ORDERS_RATE_LIMIT] {sym} "
                        f"code={code} cooldown={cooldown}s fail_streak={fail_streak}"
                    )

                return []