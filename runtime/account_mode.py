#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: account_mode.py
"""24h 평균 Binance Futures Equity(KRW 환산) 기반 계좌 모드.

거래/시장 데이터는 Binance Futures 전용이다. KRW 환산값은 외부 거래소를 호출하지 않고
명시적 로컬/환경 설정만 사용한다. 값이 없거나 유효하지 않으면 신규 진입을 fail-closed 한다.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
STATE = BASE / "state" / "account_mode.json"
STRATEGY_CONFIG = Path(os.getenv("STRATEGY_CONFIG_FILE", str(BASE / "strategy_config.json")))
WINDOW_SEC = 24 * 3600
SAMPLE_BUCKET_SEC = 300

# FLEXIBLE_ACCOUNT_RISK_PROFILE_V1
GROWTH_BOUNDARY_USDT = 70_000.0
DEFENSIVE_BOUNDARY_USDT = 350_000.0
HIGH_EQUITY_REFERENCE_USDT = None


def risk_posture(avg_equity_usdt: float | None, mode: str) -> dict:
    """Native-USDT account-size posture. Reference only; hard safety unchanged."""
    m = str(mode or "DEFENSIVE").upper()

    try:
        e = float(avg_equity_usdt)
        if not (e >= 0.0):
            raise ValueError
    except Exception:
        e = None

    if m == "GROWTH":
        posture = "GROWTH_MAX_RELAXED"
    elif m == "BALANCED":
        posture = "BALANCED"
    else:
        posture = "DEFENSIVE_CONSERVATIVE"

    if e is None:
        progress = None
    elif e < GROWTH_BOUNDARY_USDT:
        progress = 0.0
    elif e < DEFENSIVE_BOUNDARY_USDT:
        progress = (
            (e - GROWTH_BOUNDARY_USDT)
            / (
                DEFENSIVE_BOUNDARY_USDT
                - GROWTH_BOUNDARY_USDT
            )
        )
    else:
        progress = 1.0 - (
            DEFENSIVE_BOUNDARY_USDT
            / max(
                e,
                DEFENSIVE_BOUNDARY_USDT,
            )
        )

    return {
        "risk_posture": posture,
        "continuous_conservative_progress": progress,
        "high_equity_reference_usdt": None,
        "high_equity_reference_crossed": False,
        "growth_no_extra_account_size_haircut": m == "GROWTH",
        "liquidation_safety_unchanged": True,
        "hard_numeric_risk_budget_invented": False,
    }


def _atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict:
    try:
        row = json.loads(path.read_text(encoding="utf-8-sig"))
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}


def load_state() -> dict:
    return _load_json(STATE)


def resolve_krw_per_usdt() -> tuple[float | None, str]:
    """외부 거래소/API 호출 없이 명시적 값만 사용한다."""
    raw = os.getenv("BBOT_KRW_PER_USDT", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value, "ENV_BBOT_KRW_PER_USDT"
        except Exception:
            pass

    cfg = _load_json(STRATEGY_CONFIG)
    section = cfg.get("account_mode") if isinstance(cfg.get("account_mode"), dict) else {}
    try:
        value = float(section.get("krw_per_usdt") or 0)
    except Exception:
        value = 0.0
    if value > 0:
        return value, "STRATEGY_CONFIG_LOCAL_VALUE"
    return None, "LOCAL_FX_NOT_CONFIGURED"


def resolve_mode(
    avg_equity_usdt: float,
    previous: str = "GROWTH",
) -> str:
    """Constitutional native-USDT amount-mode bands."""
    e = float(avg_equity_usdt)

    if e < 0.0:
        raise ValueError("EQUITY_INVALID")

    if e < GROWTH_BOUNDARY_USDT:
        return "GROWTH"

    if e < DEFENSIVE_BOUNDARY_USDT:
        return "BALANCED"

    return "DEFENSIVE"


def update(
    wallet_balance_usdt: float,
    unrealized_pnl_usdt: float,
    *,
    now: int | None = None,
) -> dict:
    """24h average Binance Futures equity using native USDT."""
    import json
    import math
    import os
    import time

    now = int(
        time.time()
        if now is None
        else now
    )

    try:
        equity = (
            float(wallet_balance_usdt)
            + float(unrealized_pnl_usdt)
        )
    except Exception:
        equity = float("nan")

    if (
        not math.isfinite(equity)
        or equity < 0.0
    ):
        return {
            "ok": False,
            "reason": "EQUITY_INVALID",
            "mode": None,
            "block_new_entry": True,
        }

    try:
        st = json.loads(
            STATE.read_text(
                encoding="utf-8-sig"
            )
        ) if STATE.is_file() else {}
    except Exception:
        st = {}

    old_samples = (
        st.get("samples")
        if isinstance(
            st.get("samples"),
            list,
        )
        else []
    )

    cutoff = now - 86400
    samples = []

    for row in old_samples:
        if not isinstance(row,dict):
            continue

        try:
            ts = int(row.get("ts"))
            value = float(
                row.get("equity_usdt")
            )
        except Exception:
            continue

        if (
            ts >= cutoff
            and math.isfinite(value)
            and value >= 0.0
        ):
            samples.append({
                "ts": ts,
                "equity_usdt": value,
            })

    samples.append({
        "ts": now,
        "equity_usdt": equity,
    })

    samples = samples[-1440:]

    avg = (
        sum(
            float(x["equity_usdt"])
            for x in samples
        )
        / len(samples)
    )

    mode = resolve_mode(
        avg,
        str(
            st.get("mode")
            or "GROWTH"
        ),
    )

    profile = risk_posture(
        avg,
        mode,
    )

    out = {
        "schema": "B_BOT_ACCOUNT_MODE_V2",
        "ok": True,
        "reason": "OK_USDT_NATIVE",
        "mode": mode,
        **profile,

        "equity_basis":
            "BINANCE_FUTURES_ACCOUNT_EQUITY_USDT",

        "equity_formula":
            "TOTAL_WALLET_BALANCE_PLUS_TOTAL_UNREALIZED_PROFIT",

        "avg_equity_usdt_24h": avg,
        "current_equity_usdt": equity,

        # Legacy KRW fields remain non-authoritative.
        "avg_equity_krw_24h": None,
        "current_equity_krw": None,
        "krw_per_usdt": None,
        "fx_source": "NOT_REQUIRED_USDT_NATIVE",
        "fx_available": False,

        "updated_ts": now,
        "samples": samples,
        "block_new_entry": False,
    }

    STATE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp=STATE.with_suffix(
        STATE.suffix+".tmp"
    )

    tmp.write_text(
        json.dumps(
            out,
            ensure_ascii=False,
            indent=2,
        )+"\n",
        encoding="utf-8",
    )

    os.replace(
        tmp,
        STATE,
    )

    return out
