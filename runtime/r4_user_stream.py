#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: r4_user_stream.py
"""Binance Futures user-data WebSocket 수명주기와 R4 fill 전달."""
from __future__ import annotations

# ================= 임포트 =================
import json
import logging
import os
import threading
import time
from pathlib import Path

# ================= 로그 =================
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
LOG_FILE = BASE / "logs" / "runner.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
if not logging.getLogger().handlers:
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
logger = logging.getLogger("r4_user_stream")
_log_dedupe = {}


def _log_error(key, message, exc_info=False, sec=60):
    """WebSocket 반복 실패 로그를 bounded dedupe한다."""
    now = time.monotonic()
    token = str(key)
    last = _log_dedupe.get(token)
    if last is not None and now - last < float(sec):
        return
    _log_dedupe[token] = now
    if len(_log_dedupe) > 1024:
        cutoff = now - 600.0
        for old_key in [k for k, stamp in _log_dedupe.items() if stamp < cutoff]:
            _log_dedupe.pop(old_key, None)
    logger.error(message, exc_info=exc_info)


# ================= WebSocket =================
class R4UserStream:
    def __init__(self, runtime, exchange):
        self.runtime = runtime
        self.exchange = exchange
        self.ws = None
        self.listen_key = ""
        self.stop_event = threading.Event()
        self.keeper = None

    # ================= 웹소켓 장애 감지 =================
    # 연결 감지는 기록 전용이다. 신규/기존 주문, 포지션, 서비스에는 손대지 않는다.
    def _on_open(self, *_):
        logger.info("[주문][R4][WS_CONNECTED]")

    def _on_close(self, *_):
        if not self.stop_event.is_set():
            _log_error("WS_CLOSED", "[주문][R4][WS_CLOSED]", sec=60)

    def _on_error(self, _, error):
        _log_error(
            "WS_ERROR:" + type(error).__name__,
            "[주문][R4][WS_ERROR] err_type=" + type(error).__name__,
            sec=60,
        )

    def _message(self, _, raw):
        try:
            msg = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(msg, dict):
                self.runtime.process_binance_order_trade_update(msg)
        except Exception as exc:
            _log_error(
                "WS_EVENT_FAIL:" + type(exc).__name__,
                f"[주문][R4][WS_EVENT_FAIL] err={exc}",
                exc_info=True,
                sec=60,
            )

    def start(self):
        from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient

        row = self.exchange.order.safe_call(self.exchange.order.client.new_listen_key)
        self.listen_key = str((row or {}).get("listenKey") or "")
        if not self.listen_key:
            raise RuntimeError("USER_STREAM_LISTEN_KEY_FAILED")
        self.ws = UMFuturesWebsocketClient(
            on_message=self._message,
            on_open=self._on_open,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        self.ws.user_data(listen_key=self.listen_key, id=1)
        self.keeper = threading.Thread(
            target=self._keepalive,
            name="r4-user-stream-keepalive",
            daemon=True,
        )
        self.keeper.start()

    def _keepalive(self):
        while not self.stop_event.wait(25 * 60):
            try:
                result = self.exchange.order.safe_call(
                    self.exchange.order.client.renew_listen_key,
                    listenKey=self.listen_key,
                )
                if result is None:
                    _log_error(
                        "WS_KEEPALIVE_FAIL",
                        "[주문][R4][WS_KEEPALIVE_FAIL]",
                        sec=300,
                    )
            except Exception as exc:
                # 기존 25분 갱신 루프를 종료시키지 않고 기록만 한다.
                _log_error(
                    "WS_KEEPALIVE_EXCEPTION:" + type(exc).__name__,
                    "[주문][R4][WS_KEEPALIVE_EXCEPTION] err_type=" + type(exc).__name__,
                    sec=300,
                )

    def close(self):
        self.stop_event.set()
        if self.ws is not None:
            try:
                self.ws.stop()
            except Exception as exc:
                _log_error(
                    "WS_STOP_FAIL:" + type(exc).__name__,
                    f"[주문][R4][WS_STOP_FAIL] err={exc}",
                    exc_info=True,
                    sec=300,
                )
