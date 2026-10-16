
# ============================================================
# CONFIG_GLOBAL_SPAM_LOG_DEDUPE_20260609
# 전체 서비스 반복 INFO 로그 축소
# 판단/주문/청산/SL 생성 로직 변경 없음
# WARNING/ERROR/체결/실패/청산/CREATE 계열은 보호
# ============================================================
try:
    import logging as _glog_logging_20260609
    import time as _glog_time_20260609
    import re as _glog_re_20260609

    if not getattr(_glog_logging_20260609.Logger, "_bbot_config_global_spam_dedupe_20260609", False):
        _glog_orig_log_20260609 = _glog_logging_20260609.Logger._log
        _glog_last_20260609 = {}

        _GLOG_RULES_20260609 = [
            ("[주문] [ASSIST][LOAD]", 180.0),
            ("[주문][SL][KEEP]", 90.0),
            ("[주문][SL][LOOP]", 90.0),

            ("[주문][TP][META_SYNC_DB]", 180.0),
            ("[주문][TP][ENTRY_TYPE_FROM_ENTRY_DB_SCAN]", 180.0),
            ("[주문][TP][ROLE_TARGET_MULT]", 120.0),
            ("[주문][TP][ADVERSE_UPDATE]", 120.0),
            ("[주문][TP][ALLOW][NEW_NEGATIVE_ROI]", 120.0),

            ("[주문][ENTRY][GATE][EMA_NEAREST_SIMPLE]", 60.0),
            ("[주문][ENTRY][GATE][SIDE_ENTRY_MATRIX]", 60.0),
            ("[주문][ENTRY][GATE][RUNTIME_SIDE_CANDIDATE]", 60.0),
            ("[주문][ENTRY][GATE][EMA_DIRECT_CROSS_TOUCH_GATE]", 60.0),
            ("[주문][ENTRY][GATE][AXIS_ALLOW_DEBUG", 120.0),

            ("[ENTRY][GATE][BODY_SCORE]", 120.0),
            ("[ENTRY][GATE][MOVE_SCORE]", 120.0),
            ("[ENTRY][GATE][BREAK_SCORE]", 120.0),
            ("[ENTRY][GATE][RUNTIME_EVENT_PAIR]", 120.0),
            ("[ENTRY][GATE][RUNTIME_CROSS_PAIR]", 120.0),
            ("[ENTRY][GATE][AUTO_RUNTIME]", 120.0),
            ("[ENTRY][GATE][MACD_EVENT]", 120.0),
            ("[ENTRY][GATE][TRIGGER", 120.0),

            ("[주문][ENTRY][POSITION_CHECK]", 90.0),
            ("[주문][ENTRY][SKIP][POSITION_EXISTS_SAME_SIDE]", 120.0),
            ("[주문][ENTRY][CMP]", 90.0),

            ("[주문][CALC_OK]", 90.0),
            ("[주문][MARK_PRICE_REQ]", 90.0),
            ("[주문][MARK_PRICE_OK]", 90.0),
            ("[주문][API_CALL]", 90.0),
            ("[주문][API_OK]", 90.0),
            ("[주문][API_USAGE]", 90.0),

            ("[SAR_TRACE]", 180.0),
            ("[ENTRY][FAIL_DB_SKIP_DEDUPE_SUMMARY]", 180.0),
            ("[ENTRY][FAIL_DB_COUNT_ONLY]", 120.0),
        ]

        _GLOG_PROTECT_20260609 = (
            "Traceback",
            "CRITICAL",
            "MemoryError",
            "Killed",
            "NameError",
            "SyntaxError",
            "IndentationError",
            "TypeError",
            "AttributeError",

            "ORDER_FAIL",
            "ORDER_EXCEPTION",
            "NO_FILL",
            "ENTRY_DB_OK",
            "ENTRY_DB_EXCEPTION",
            "ORDER_OK",
            "ORDER_FILLED",

            "CREATE_OK",
            "CREATE_FAIL",
            "CREATE_EXCEPTION",
            "CANCEL_EXCEPTION",
            "OPEN_ALGO_FAIL",
            "CROSS_STOP_NOT_FOUND",

            "[TP][EXIT",
            "[EXIT",
            "[주문][SL][CREATE_OK]",
            "[주문][SL][CREATE_FAIL]",
            "[주문][SL][CREATE_EXCEPTION]",
            "[주문][SL][CANCEL_EXCEPTION]",
        )

        def _glog_key_20260609(raw, tag):
            try:
                sym = ""
                side = ""
                reason = ""
                family = ""

                m = _glog_re_20260609.search(r"\b([A-Z0-9]+USDT)\b", raw)
                if m:
                    sym = m.group(1)

                m = _glog_re_20260609.search(r"\bside=([A-Z_]+)", raw)
                if m:
                    side = m.group(1)
                else:
                    m = _glog_re_20260609.search(r"\bside_key=([A-Z_]+)", raw)
                    if m:
                        side = m.group(1)

                m = _glog_re_20260609.search(r"\breason=([A-Z0-9_]+)", raw)
                if m:
                    reason = m.group(1)

                m = _glog_re_20260609.search(r"FINAL family=([A-Z0-9_]+)", raw)
                if m:
                    family = m.group(1)

                m = _glog_re_20260609.search(r"\[ASSIST\]\[LOAD\]\[([A-Z0-9_]+)\]", raw)
                if m:
                    reason = m.group(1)

                return f"{tag}|{sym}|{side}|{reason}|{family}"
            except Exception:
                return str(tag)

        def _glog_dedupe_20260609(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
            try:
                if int(level) >= int(_glog_logging_20260609.WARNING):
                    return _glog_orig_log_20260609(
                        self, level, msg, args,
                        exc_info=exc_info,
                        extra=extra,
                        stack_info=stack_info,
                        stacklevel=stacklevel,
                    )

                raw = str(msg)
                if args:
                    try:
                        raw = raw % args
                    except Exception:
                        raw = str(msg)

                for p in _GLOG_PROTECT_20260609:
                    if p in raw:
                        return _glog_orig_log_20260609(
                            self, level, msg, args,
                            exc_info=exc_info,
                            extra=extra,
                            stack_info=stack_info,
                            stacklevel=stacklevel,
                        )

                now = _glog_time_20260609.time()

                for tag, window in _GLOG_RULES_20260609:
                    if tag in raw:
                        key = _glog_key_20260609(raw, tag)
                        last = float(_glog_last_20260609.get(key, 0.0) or 0.0)

                        if now - last < float(window):
                            return None

                        _glog_last_20260609[key] = now

                        if len(_glog_last_20260609) > 5000:
                            cutoff = now - 900.0
                            for k in list(_glog_last_20260609.keys()):
                                if float(_glog_last_20260609.get(k, 0.0) or 0.0) < cutoff:
                                    _glog_last_20260609.pop(k, None)

                        break

            except Exception:
                pass

            return _glog_orig_log_20260609(
                self, level, msg, args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel,
            )

        _glog_logging_20260609.Logger._log = _glog_dedupe_20260609
        _glog_logging_20260609.Logger._bbot_config_global_spam_dedupe_20260609 = True

except Exception:
    pass


# config.py
# config.json 캐시 + 실시간 리로드

import json
import os
import time
import threading
import logging

logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"


class Config:
    """
    config.json 단일 진입점
    CONFIG.get(...) 으로 사용
    """

    def __init__(self, path=CONFIG_FILE, ttl=10):
        self.path = path
        self.ttl = ttl
        self.cache = {}
        self.last = 0
        self.lock = threading.Lock()
        self.load()

    # -----------------------------------------------------
    def load(self):
        try:
            with open(self.path, "r") as f:
                self.cache = json.load(f)
            self.last = time.time()
            logger.info("[CONFIG] 로드 완료")
        except Exception as e:
            logger.error(f"[CONFIG] 로드 FAIL: {e}")
            self.cache = {}

    # -----------------------------------------------------
    def get(self, key, default=None):
        # TTL 지나면 자동 리로드
        if time.time() - self.last > self.ttl:
            with self.lock:
                if time.time() - self.last > self.ttl:
                    self.load()
        return self.cache.get(key, default)

    def raw(self):
        return self.cache
