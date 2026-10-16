#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: strategy_runtime.py
"""strategy_config.json single-source runtime loader. 주문/거래소 호출 없음."""
from __future__ import annotations

# ================= 임포트 =================
import json
import os
from pathlib import Path
from typing import Any

# ================= 설정 =================
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
CONFIG_FILE = Path(os.getenv("STRATEGY_CONFIG_FILE", str(BASE / "strategy_config.json")))
_cache_mtime = None
_cache_data = None

R4_LIVE_TIERS = ("CANARY", "SMALL", "NORMAL")
R4_DEFAULT_TIER_WEIGHTS = {
    "CANARY": 0.20,
    "SMALL": 0.50,
    "NORMAL": 1.00,
}
R4_REQUIRED_SCALE_EVIDENCE = (
    "EXACT_STRATEGY_INSTANCE_LIFECYCLE",
    "ENTRY_RISK_SAFE_SIZE_PASS",
    "ACCOUNT_RISK_BUDGET_PASS",
    "PORTFOLIO_TOTAL_EXPOSURE_PASS",
    "AVAILABLE_MARGIN_PASS",
    "MARGIN_SAFETY_RESERVE_PASS",
    "SYMBOL_CONCENTRATION_PASS",
    "STRATEGY_CONCENTRATION_PASS",
    "CORRELATION_CONCENTRATION_PASS",
    "COST_AND_LIQUIDITY_PASS",
    "RELATIVE_TIER_COMPETITION_PASS",
    "STRATEGY_LEG_LEDGER_RECONCILE_PASS",
    "NO_CRITICAL_HARD_FAIL",
)

# ================= 로더 =================
def _load() -> dict:
    global _cache_mtime, _cache_data
    try:
        mt = CONFIG_FILE.stat().st_mtime_ns
    except Exception:
        mt = None
    if isinstance(_cache_data, dict) and mt == _cache_mtime:
        return _cache_data
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("strategy config must be object")
    except Exception:
        return _cache_data if isinstance(_cache_data, dict) else {}
    _cache_mtime = mt
    _cache_data = data
    return data


def reload() -> dict:
    global _cache_mtime
    _cache_mtime = None
    return _load()


def get(path: str, default: Any = None) -> Any:
    cur: Any = _load()
    for part in str(path or "").split("."):
        part = part.strip()
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

# ================= 기존 Frame Runtime =================
def frame(tf: str) -> dict:
    raw = get("frames", {})
    row = raw.get(str(tf or "").upper()) if isinstance(raw, dict) else None
    return dict(row) if isinstance(row, dict) else {}


def enabled_frames() -> list[str]:
    raw = get("frames", {})
    return [
        str(k).upper()
        for k, v in (raw.items() if isinstance(raw, dict) else [])
        if isinstance(v, dict) and bool(v.get("enabled"))
    ]


def frame_strategies(tf: str) -> list[str]:
    raw = frame(tf).get("strategies") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split("+") if x.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for x in raw:
        n = str(x or "").strip().upper()
        if n and n not in out:
            out.append(n)
    return out


def frame_leverage(tf: str, default: int = 0) -> int:
    try:
        raw = frame(tf).get("leverage", default)
        if raw is None or raw == "":
            raw = default
        return int(float(raw))
    except Exception:
        return int(default)


def frame_status(tf: str) -> str:
    return str(frame(tf).get("status") or "").upper()


def frame_deployment_tier(tf: str) -> str:
    return str(frame(tf).get("deployment_tier") or "OFF").upper()


def legacy_manage_existing(tf: str) -> bool:
    return bool(frame(tf).get("legacy_manage_existing", False))


def state_reentry_allowed(tf: str) -> bool:
    return bool(frame(tf).get("state_reentry_allowed", True))


def universe_top_n(default: int = 35) -> int:
    try:
        n = int(float(get("universe_top_n", default) or default))
        return n if n > 0 else int(default)
    except Exception:
        return int(default)


def trailing() -> dict:
    raw = get("trailing", {})
    raw = raw if isinstance(raw, dict) else {}

    def fl(key, default):
        try:
            return float(raw.get(key, default))
        except Exception:
            return float(default)

    try:
        retention = int(float(raw.get("state_retention_sec", 2592000)))
    except Exception:
        retention = 2592000
    return {
        "enabled": bool(raw.get("enabled", True)),
        "mode": str(raw.get("mode") or "FIXED_BASELINE"),
        "dynamic_adjustment": bool(raw.get("dynamic_adjustment", False)),
        "start_pct": fl("start_pct", 3.0),
        "callback_pct": fl("callback_pct", 1.5),
        "state_retention_sec": retention,
    }


def _section(name: str) -> dict:
    raw = get(name, {})
    return dict(raw) if isinstance(raw, dict) else {}


def control_sl() -> dict:
    return {"enabled": bool(_section("control_sl").get("enabled", True)), **_section("control_sl")}


def entry_gate() -> dict:
    return {"enabled": bool(_section("entry_gate").get("enabled", True)), **_section("entry_gate")}


def state_reentry() -> dict:
    return {"enabled": bool(_section("state_reentry").get("enabled", True)), **_section("state_reentry")}


def opposite_exit() -> dict:
    return _section("opposite_exit")


def opposite_reopen() -> dict:
    return _section("opposite_reopen")


def deployment() -> dict:
    return _section("deployment")


def v3_decision() -> dict:
    return _section("v3_decision")


def account_mode() -> dict:
    return _section("account_mode")


def research_policy() -> dict:
    return _section("research")


def sync_policy() -> dict:
    return _section("sync_policy")


def policy_versions() -> dict:
    raw = get("policy_versions", {})
    return dict(raw) if isinstance(raw, dict) else {}


def strategy_params(name: str) -> dict:
    raw = get("strategy_params", {})
    row = raw.get(str(name or "").upper()) if isinstance(raw, dict) else None
    return dict(row) if isinstance(row, dict) else {}


def registry() -> dict:
    raw = get("strategy_registry", {})
    return dict(raw) if isinstance(raw, dict) else {}

# ================= R4 Strategy Instance Runtime =================
def r4_runtime() -> dict:
    raw = get("r4_runtime", {})
    return dict(raw) if isinstance(raw, dict) else {}


def r4_tier_controller() -> dict:
    raw = r4_runtime().get("tier_controller") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def r4_strategy_instances() -> dict[str, dict]:
    raw = r4_runtime().get("strategy_instances") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(instance_id): dict(row)
        for instance_id, row in raw.items()
        if isinstance(row, dict)
    }


def r4_strategy_instance(instance_id: str) -> dict:
    return dict(r4_strategy_instances().get(str(instance_id)) or {})


def r4_tier_weight(tier: str) -> float:
    tier = str(tier or "").upper()
    controller = r4_tier_controller()
    weights = controller.get("tier_weights") or {}
    if not isinstance(weights, dict):
        weights = {}
    try:
        value = float(weights.get(tier, R4_DEFAULT_TIER_WEIGHTS.get(tier, 0.0)))
    except Exception:
        value = 0.0
    return value


def r4_required_scale_evidence() -> list[str]:
    controller = r4_tier_controller()
    raw = controller.get("required_positive_scale_evidence") or R4_REQUIRED_SCALE_EVIDENCE
    if not isinstance(raw, (list, tuple)):
        return list(R4_REQUIRED_SCALE_EVIDENCE)
    out = []
    for value in raw:
        name = str(value or "").strip().upper()
        if name and name not in out:
            out.append(name)
    return out


def r4_enabled_instances() -> list[dict]:
    out = []
    for instance_id, row in r4_strategy_instances().items():
        tier = str(row.get("tier") or "").upper()
        if tier not in R4_LIVE_TIERS:
            continue
        if row.get("enabled") is not True:
            continue
        if row.get("entry_enabled", True) is not True:
            continue
        item = dict(row)
        item["strategy_instance_id"] = str(row.get("strategy_instance_id") or instance_id)
        item["tier"] = tier
        item["capital_weight"] = r4_tier_weight(tier)
        out.append(item)
    out.sort(
        key=lambda row: (
            str(row.get("timeframe") or ""),
            str(row.get("strategy_id") or ""),
            str(row.get("strategy_instance_id") or ""),
        )
    )
    return out


def r4_instances_for_frame(timeframe: str, exact_version: str = "") -> list[dict]:
    timeframe = str(timeframe or "").upper()
    exact_version = str(exact_version or "").strip()
    out = []
    for row in r4_enabled_instances():
        if str(row.get("timeframe") or "").upper() != timeframe:
            continue
        if exact_version and str(row.get("exact_strategy_version") or "") != exact_version:
            continue
        out.append(row)
    return out

# ================= 기존 Owner 호환 =================
def resolve_owner(previous_frame: str = "", previous_strategy: str = "") -> dict:
    """Legacy single-owner 호환. R4 독점 Runtime은 Strategy Instance API를 사용한다."""
    previous_frame = str(previous_frame or "").strip().upper()
    previous_strategy = str(previous_strategy or "").strip().upper()

    if previous_frame:
        row = frame(previous_frame)
        tier = str(row.get("deployment_tier") or "OFF").upper()
        status = str(row.get("status") or "").upper()
        strategies = frame_strategies(previous_frame)
        leverage = frame_leverage(previous_frame, 0)
        if bool(row.get("enabled")) and strategies and leverage > 0 and tier in R4_LIVE_TIERS:
            return {
                "frame": previous_frame,
                "strategies": strategies,
                "strategy": "+".join(strategies),
                "leverage": leverage,
                "strategy_version": str(row.get("strategy_version") or ""),
                "deployment_tier": tier,
                "status": status,
                "source": "ACTIVE_RUNTIME_OWNER" if tier == "NORMAL" else "SCOPED_RUNTIME_OWNER",
            }

    raw_frames = get("frames", {})
    raw_frames = raw_frames if isinstance(raw_frames, dict) else {}
    priority = get("position_management.owner_fallback_priority", ["12H", "1D", "6H", "1H", "30M", "1W"])
    priority = [str(x or "").upper() for x in priority]
    normal = []
    for tf, row in raw_frames.items():
        if not isinstance(row, dict):
            continue
        tf = str(tf or "").upper()
        if not bool(row.get("enabled")):
            continue
        if str(row.get("deployment_tier") or "").upper() != "NORMAL":
            continue
        if str(row.get("status") or "").upper() != "VALIDATED_MAIN":
            continue
        strategies = frame_strategies(tf)
        leverage = frame_leverage(tf, 0)
        if not strategies or leverage <= 0:
            continue
        rank = priority.index(tf) if tf in priority else len(priority) + 100
        normal.append((rank, tf, strategies, leverage, row))

    if normal:
        normal.sort(key=lambda x: x[0])
        _, tf, strategies, leverage, row = normal[0]
        return {
            "frame": tf,
            "strategies": strategies,
            "strategy": "+".join(strategies),
            "leverage": leverage,
            "strategy_version": str(row.get("strategy_version") or ""),
            "deployment_tier": "NORMAL",
            "status": str(row.get("status") or "").upper(),
            "source": "ACTIVE_RUNTIME_OWNER",
        }

    if previous_frame:
        row = frame(previous_frame)
        strategies = [x for x in previous_strategy.split("+") if x] if previous_strategy else frame_strategies(previous_frame)
        leverage = frame_leverage(previous_frame, 0)
        if strategies:
            return {
                "frame": previous_frame,
                "strategies": strategies,
                "strategy": "+".join(strategies),
                "leverage": leverage,
                "strategy_version": str(row.get("strategy_version") or ""),
                "deployment_tier": str(row.get("deployment_tier") or "LEGACY").upper(),
                "status": str(row.get("status") or "").upper(),
                "source": "LEGACY_FAILSAFE_OWNER",
            }

    return {
        "frame": "",
        "strategies": [],
        "strategy": "",
        "leverage": 0,
        "strategy_version": "",
        "deployment_tier": "OFF",
        "status": "",
        "source": "NO_ACTIVE_OWNER",
    }

# ================= 검증 =================
def validate() -> dict:
    d = _load()
    errors = []
    frames = d.get("frames")
    if not isinstance(frames, dict):
        errors.append("frames missing")
    if universe_top_n(0) <= 0 or universe_top_n(0) > 35:
        errors.append("universe_top_n invalid")
    t = trailing()
    if t["start_pct"] <= 0 or t["callback_pct"] <= 0:
        errors.append("trailing invalid")
    if t.get("dynamic_adjustment"):
        errors.append("initial dynamic trailing forbidden")
    for tf in enabled_frames():
        if not frame_strategies(tf):
            errors.append(f"{tf}: no strategies")
        if frame_leverage(tf, 0) <= 0:
            errors.append(f"{tf}: leverage invalid")
        if not str(frame(tf).get("strategy_version") or "").strip():
            errors.append(f"{tf}: strategy_version missing")

    trade_tfs = {
        "30M",
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
    }
    active_tfs = set(enabled_frames())

    if not active_tfs.issubset(trade_tfs):
        errors.append(
            "enabled frame outside trading timeframe universe"
        )

    for tf in trade_tfs:
        row = frame(tf)
        if not row:
            errors.append(
                f"{tf}: trading frame config missing"
            )
            continue
        if frame_leverage(tf, 0) != 10:
            errors.append(
                f"{tf}: common leverage must be 10x"
            )

    ref = frame("1M")
    if (
        ref.get("enabled") is not False
        or ref.get("direct_entry") is not False
        or frame_leverage("1M", -1) != 0
        or frame_status("1M") != "REFERENCE_ONLY"
    ):
        errors.append(
            "1M must remain reference-only and non-trading"
        )

    owner_priority = [
        str(x or "").upper()
        for x in get(
            "position_management.owner_fallback_priority",
            [],
        )
    ]
    if (
        "30M" not in owner_priority
        or "1W" not in owner_priority
        or "1M" in owner_priority
    ):
        errors.append(
            "owner fallback priority violates trading/reference timeframe policy"
        )
    if bool(get("v3_decision.additional_entry_same_symbol", True)):
        errors.append("additional entry must be disabled initially")
    required_policy = {"DIRECTION", "ENTRY_RISK", "EXIT", "REENTRY", "SYMBOL", "PORTFOLIO", "REGIME", "EXECUTION"}
    pv = policy_versions()
    if not required_policy.issubset({str(k).upper() for k in pv}):
        errors.append("policy_versions incomplete")
    if str(get("stable_kernel.mode", "")).upper() != "FROZEN_CORE_RESEARCH_CANNOT_MUTATE":
        errors.append("stable kernel mode invalid")
    if bool(get("stable_kernel.research_core_mutation", True)):
        errors.append("research core mutation forbidden")
    if not bool(get("research.roadmap_constitution_required", False)):
        errors.append("roadmap constitution gate missing")
    if get("research.change_control_order", []) != ["ROADMAP_CONSTITUTION", "VERIFIER", "IMPLEMENTATION", "SELF_VERIFICATION"]:
        errors.append("change control order invalid")

    runtime = r4_runtime()
    if runtime:
        controller = r4_tier_controller()
        cutover = runtime.get("cutover") if isinstance(runtime.get("cutover"), dict) else {}
        if runtime.get("exclusive_single_writer") is not True:
            errors.append("r4 exclusive single writer missing")
        if runtime.get("legacy_runner_must_be_stopped") is not True:
            errors.append("r4 legacy runner stop gate missing")
        if runtime.get("reconcile_before_entry") is not True:
            errors.append("r4 reconcile before entry missing")
        if controller.get("enabled") is not True:
            errors.append("r4 tier controller disabled")
        if controller.get("new_strategy_version_promotion") is not False:
            errors.append("server strategy version promotion forbidden")
        for tier, expected in R4_DEFAULT_TIER_WEIGHTS.items():
            actual = r4_tier_weight(tier)
            if abs(actual - expected) > 1e-12:
                errors.append(f"r4 tier weight invalid:{tier}")
        configured_required = set(r4_required_scale_evidence())
        if not set(R4_REQUIRED_SCALE_EVIDENCE).issubset(configured_required):
            errors.append("r4 positive scale evidence incomplete")

        capacity_policy = (
            runtime.get("capacity_policy")
            if isinstance(
                runtime.get("capacity_policy"),
                dict,
            )
            else {}
        )

        if (
            str(
                capacity_policy.get("mode")
                or ""
            ).upper()
            != "LIVE_RISK_APPROVED_INCREMENT"
        ):
            errors.append(
                "r4 dynamic capacity policy missing"
            )

        if (
            capacity_policy.get(
                "static_symbol_map"
            )
            is not False
        ):
            errors.append(
                "r4 static symbol capacity forbidden"
            )

        if (
            str(
                capacity_policy.get("unit")
                or ""
            ).upper()
            != "EXCHANGE_LOT_SIZE_STEP_INTEGER_UNITS"
        ):
            errors.append(
                "r4 capacity unit invalid"
            )

        if (
            str(
                capacity_policy.get(
                    "universe_source"
                )
                or ""
            ).upper()
            != "CURRENT_ACTIVE_CONFIG"
        ):
            errors.append(
                "r4 capacity universe source invalid"
            )

        common_leverage = (
            runtime.get(
                "common_leverage_policy"
            )
            if isinstance(
                runtime.get(
                    "common_leverage_policy"
                ),
                dict,
            )
            else {}
        )

        approved_sources = (
            common_leverage.get(
                "approved_sources"
            )
            if isinstance(
                common_leverage.get(
                    "approved_sources"
                ),
                dict,
            )
            else {}
        )

        try:
            normalized_sources = {
                str(k).upper():
                    int(float(v))
                for k, v
                in approved_sources.items()
            }

            common_target = int(
                float(
                    common_leverage.get(
                        "target"
                    )
                    or 0
                )
            )

        except Exception:
            normalized_sources = {}
            common_target = 0

        if (
            str(
                common_leverage.get("mode")
                or ""
            ).upper()
            != "MIN_RESEARCH_APPROVED_BASELINE"
        ):
            errors.append(
                "r4 common leverage mode invalid"
            )

        if (
            str(
                common_leverage.get("source")
                or ""
            )
            != "R5A_LEVERAGE_FROZEN_STANDARD_V2"
        ):
            errors.append(
                "r4 common leverage source invalid"
            )

        if (
            str(
                common_leverage.get(
                    "symbol_scope"
                )
                or ""
            ).upper()
            != "DYNAMIC_UNIVERSE"
        ):
            errors.append(
                "r4 common leverage symbol scope invalid"
            )

        if (
            str(
                common_leverage.get(
                    "open_basket_change"
                )
                or ""
            ).upper()
            != "DEFER_UNTIL_FLAT"
        ):
            errors.append(
                "r4 common leverage open basket policy invalid"
            )

        if normalized_sources != {
            "12H": 10,
            "1D": 15,
        }:
            errors.append(
                "r4 common leverage frozen sources invalid"
            )

        if (
            common_target != 10
            or common_target
            != min(
                normalized_sources.values(),
                default=0,
            )
        ):
            errors.append(
                "r4 common leverage target not conservative approved minimum"
            )

        increment = common_leverage.get("owner_leverage_increment_x", 0)
        if type(increment) is not int or increment not in (0, 5, 10, 15, 20):
            errors.append("r4 owner leverage increment invalid")
        else:
            effective = common_leverage.get("effective_live_exchange_leverage_x", common_target)
            if type(effective) is not int or effective != common_target + increment:
                errors.append("r4 effective leverage does not match baseline plus owner increment")

        # Cutover는 실거래 전환의 최종 경계다. 준비 플래그는 bool만 허용하고,
        # adoption/재기동 핵심 안전 조건은 설정으로 약화할 수 없게 고정한다.
        if not cutover:
            errors.append("r4 cutover config missing")
        else:
            if not isinstance(cutover.get("full_pipeline_ready"), bool):
                errors.append("r4 full_pipeline_ready must be bool")
            legacy_service = str(cutover.get("legacy_service_name") or "").strip()
            r4_service = str(cutover.get("r4_service_name") or "").strip()
            if not legacy_service or not r4_service or legacy_service == r4_service:
                errors.append("r4 cutover service ownership invalid")
            approvals = cutover.get("legacy_adoption_approvals")
            if not isinstance(approvals, dict):
                errors.append("r4 legacy adoption approvals invalid")
            required_true = (
                "forced_exit_forbidden",
                "require_exact_owner_evidence",
                "require_exact_qty_reconciliation",
                "require_exact_side",
                "require_no_pending_ambiguous_order",
                "require_policy_snapshot",
                "legacy_restart_after_adoption_forbidden",
                "new_entry_after_reconcile_only",
            )
            for key in required_true:
                if cutover.get(key) is not True:
                    errors.append(f"r4 cutover invariant missing:{key}")
            for key in ("legacy_barrier_timeout_sec", "startup_reconcile_timeout_sec"):
                try:
                    if int(cutover.get(key) or 0) <= 0:
                        errors.append(f"r4 cutover timeout invalid:{key}")
                except Exception:
                    errors.append(f"r4 cutover timeout invalid:{key}")

        for instance_id, row in r4_strategy_instances().items():
            declared_id = str(row.get("strategy_instance_id") or instance_id)
            if declared_id != instance_id:
                errors.append(f"r4 instance key mismatch:{instance_id}")
            for key in ("strategy_id", "timeframe", "exact_strategy_version", "strategy_policy_bundle_version"):
                if not str(row.get(key) or "").strip():
                    errors.append(f"r4 instance missing {key}:{instance_id}")
            tier = str(row.get("tier") or "").upper()
            if tier not in R4_LIVE_TIERS:
                errors.append(f"r4 instance tier invalid:{instance_id}")
            if row.get("approved_for_server_release") is not True:
                errors.append(f"r4 instance not approved:{instance_id}")

    return {
        "ok": not errors,
        "errors": errors,
        "schema": d.get("schema"),
        "version": d.get("version"),
        "enabled_frames": enabled_frames(),
        "trade_timeframes": ["30M","1H","6H","12H","1D","1W"],
        "reference_only_timeframes": ["1M"],
        "r4_enabled_instances": [x.get("strategy_instance_id") for x in r4_enabled_instances()],
        "universe_top_n": universe_top_n(0),
        "policy_versions": pv,
    }
