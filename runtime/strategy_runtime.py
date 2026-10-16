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

# ================= GitHub 직접 수신: 활성화와 분리된 기존 Runtime 인계 경계 =================
def receive_github_candidates():
    """최종 연구 통과 실행물만 대기함에 둔다. config/원장/주문을 변경하지 않는다."""
    import hashlib,re,shutil,subprocess,time
    state=BASE/'state';settings_path=state/'r4_github_receiver.json'
    if settings_path.is_symlink():raise RuntimeError('GITHUB_RECEIVER_SETTINGS_SYMLINK')
    import fcntl
    state.mkdir(parents=True,exist_ok=True)
    receiver_lock=(state/'r4_github_receiver.lock').open('a+b')
    try:fcntl.flock(receiver_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        receiver_lock.close()
        return {'status':'RECEIVER_BUSY','orders_touched':False,'automatic_registration_enabled':False}
    try:
        settings=json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
        if settings.get('enabled') is not True:return {'status':'DISABLED','automatic_registration_enabled':False}
        if settings.get('repository')!='hans1177/bbot-lab' or settings.get('branch')!='runtime-candidates':raise RuntimeError('GITHUB_RECEIVER_SOURCE_INVALID')
        status_path=state/'r4_github_receiver_state.json'
        previous=json.loads(status_path.read_text(encoding='utf-8-sig')) if status_path.exists() else {}
        cache=state/'github_runtime.git';inbox=state/'github_candidates'
        if cache.is_symlink() or inbox.is_symlink() or status_path.is_symlink():raise RuntimeError('GITHUB_RECEIVER_SYMLINK_FORBIDDEN')
        env=os.environ.copy()
        env['GIT_SSH_COMMAND']='ssh -i /home/ec2-user/.ssh/bbot_lab_read -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/ec2-user/.ssh/bbot_lab_hosts'
        def git(*args):
            result=subprocess.run(['git','--git-dir='+str(cache),*args],env=env,capture_output=True,timeout=45)
            if result.returncode:raise RuntimeError('GITHUB_RECEIVER_GIT_FAILED')
            return result.stdout
        if not cache.exists():
            state.mkdir(parents=True,exist_ok=True)
            subprocess.run(['git','init','--bare',str(cache)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
        git('fetch','--depth=1','--no-tags','git@github.com:hans1177/bbot-lab.git','+refs/heads/runtime-candidates:refs/remotes/origin/runtime-candidates')
        revision=git('rev-parse','refs/remotes/origin/runtime-candidates').decode().strip()
        if not re.fullmatch('[0-9a-f]{40}',revision):raise RuntimeError('GITHUB_RECEIVER_REF_INVALID')
        if (not settings.get('automatic_registration_enabled') or previous.get('status') in {'APPLIED','APPLIED_DUPLICATE','WAIT_FINAL_RESEARCH'}) and (not settings.get('feedback_enabled') or 'feedback_candidates' in previous) and previous.get('contract')==[settings.get('constitution_hash'),settings.get('engine_sha256')] and previous.get('received_commit')==revision and previous.get('status') in {'WAIT_FINAL_RESEARCH','WAIT_SERVER_VALIDATION','APPLIED','APPLIED_DUPLICATE'}:
            feedback=publish_github_feedback(previous.get('feedback_candidates') or [],revision) if settings.get('feedback_enabled') is True else {'status':'DISABLED'}
            return {**previous,'unchanged':True,'automatic_registration_enabled':settings.get('automatic_registration_enabled') is True,'operating_config_changed':False,'feedback':feedback}
        names=git('ls-tree','-r','--name-only',revision).decode().splitlines()
        if 'MANIFEST.json' not in names or len(names)>35:raise RuntimeError('GITHUB_RECEIVER_TREE_INVALID')
        def blob(name,limit):
            size=int(git('cat-file','-s',revision+':'+name).decode())
            if size<0 or size>limit:raise RuntimeError('GITHUB_RECEIVER_BLOB_LIMIT')
            raw=git('show',revision+':'+name)
            if len(raw)!=size:raise RuntimeError('GITHUB_RECEIVER_BLOB_SIZE')
            return raw
        manifest=json.loads(blob('MANIFEST.json',1024**2))
        if manifest.get('schema')!='B_BOT_R4_RESEARCH_CANDIDATES_V1' or not isinstance(manifest.get('automatic_registration_authorized'),bool):raise RuntimeError('GITHUB_RECEIVER_MANIFEST_INVALID')
        if manifest.get('constitution_hash')!=settings.get('constitution_hash') or manifest.get('engine_sha256')!=settings.get('engine_sha256'):raise RuntimeError('GITHUB_RECEIVER_CONTRACT_MISMATCH')
        if not re.fullmatch('[0-9a-f]{40}',str(manifest.get('source_commit') or '')):raise RuntimeError('GITHUB_RECEIVER_CODE_REF_INVALID')
        rows=manifest.get('candidates');files=manifest.get('files')
        if not isinstance(rows,list) or len(rows)>32 or not isinstance(files,dict):raise RuntimeError('GITHUB_RECEIVER_CANDIDATES_INVALID')
        if set(names)!={'MANIFEST.json',*files}:raise RuntimeError('GITHUB_RECEIVER_FILE_SET_INVALID')
        pending=[];versions=set();paths=set()
        for row in rows:
            version=str(row.get('exact_strategy_version') or '');strategy=str(row.get('strategy_id') or '');tf=str(row.get('timeframe') or '')
            if not re.fullmatch('[A-Za-z0-9_]{1,160}',version) or not re.fullmatch('[A-Z0-9_]{1,160}',strategy) or tf not in ('30M','1H','6H','12H','1D','1W'):raise RuntimeError('GITHUB_RECEIVER_ID_INVALID')
            if version in versions:raise RuntimeError('GITHUB_RECEIVER_DUPLICATE_VERSION')
            versions.add(version)
            if not re.fullmatch('[0-9a-f]{64}',str(row.get('source_sha256') or '')):raise RuntimeError('GITHUB_RECEIVER_SOURCE_HASH_INVALID')
            evidence=row.get('research_evidence') or {}
            if evidence.get('eligible') is not True or evidence.get('pipeline_stage')!='LOCAL_SHADOW_PENDING' or (evidence.get('full_validation_gate') or {}).get('pass') is not True or (evidence.get('sealed_final_blind_gate') or {}).get('pass') is not True or (evidence.get('sealed_final_blind_gate') or {}).get('next_stage')!='LOCAL_SHADOW_PENDING':raise RuntimeError('GITHUB_RECEIVER_FINAL_GATES_REQUIRED')
            for field in ('strategy_id','timeframe','exact_strategy_version','source_sha256'):
                if evidence.get(field)!=row.get(field):raise RuntimeError('GITHUB_RECEIVER_EVIDENCE_BINDING')
            if row.get('server_shadow_required') is not True or row.get('approved_for_server_release') is not False:raise RuntimeError('GITHUB_RECEIVER_SHADOW_REQUIRED')
            name=str(row.get('artifact') or '')
            if not re.fullmatch(r'artifacts/[0-9a-f]{64}\.py',name) or name not in files or name in paths:raise RuntimeError('GITHUB_RECEIVER_ARTIFACT_PATH')
            paths.add(name);raw=blob(name,128*1024);digest=hashlib.sha256(raw).hexdigest()
            if digest!=row.get('runtime_sha256') or digest!=files[name].get('sha256') or len(raw)!=files[name].get('bytes'):raise RuntimeError('GITHUB_RECEIVER_ARTIFACT_HASH')
            compile(raw.decode('utf-8-sig'),name,'exec')
            if b'PORTABLE_RUNTIME_ARTIFACT_V1' not in raw[:4096]:raise RuntimeError('GITHUB_RECEIVER_ARTIFACT_NOT_PORTABLE')
            candidate_id=hashlib.sha256((strategy+'|'+tf+'|'+version).encode()).hexdigest()
            target=inbox/candidate_id
            if target.is_symlink():raise RuntimeError('GITHUB_RECEIVER_TARGET_SYMLINK')
            if target.exists() and (not (target/'runtime.py').is_file() or hashlib.sha256((target/'runtime.py').read_bytes()).hexdigest()!=digest):raise RuntimeError('GITHUB_RECEIVER_IMMUTABLE_VERSION_CONFLICT')
            pending.append((candidate_id,target,raw,row))
        release=manifest.get('release')
        release_config=None;release_request=None
        if release is not None:
            if not isinstance(release,dict) or manifest.get('automatic_registration_authorized') is not True:raise RuntimeError('GITHUB_RELEASE_AUTHORIZATION_INVALID')
            if release.get('schema')!='B_BOT_R4_VALIDATED_RELEASE_MANIFEST_V1' or release.get('approved_for_server_release') is not True or release.get('local_shadow_pass') is not True or release.get('core_mutation') is not False or release.get('canonical_verifiers')!=6 or release.get('tier')!='CANARY' or release.get('version_kind')!='STRATEGY':raise RuntimeError('GITHUB_RELEASE_CANONICAL_GATES_REQUIRED')
            if release.get('constitution_hash')!=settings.get('constitution_hash'):raise RuntimeError('GITHUB_RELEASE_CONSTITUTION_MISMATCH')
            for name,key in [('release/CONFIG.json','strategy_config_sha256'),('release/REQUEST.json','request_sha256')]:
                raw=blob(name,1024**2)
                if name not in files or hashlib.sha256(raw).hexdigest()!=files[name].get('sha256') or len(raw)!=files[name].get('bytes') or files[name]['sha256']!=release.get(key):raise RuntimeError('GITHUB_RELEASE_PAYLOAD_HASH')
                paths.add(name)
                if name.endswith('CONFIG.json'):release_config=raw
                else:release_request=json.loads(raw)
            if release_request.get('schema')!='B_BOT_V3_RUNTIME_SYNC_REQUEST_V1' or release_request.get('autonomous_rule_passed') is not True or release_request.get('core_mutation') is not False:raise RuntimeError('GITHUB_RELEASE_REQUEST_GATE')
            for key in ('request_id','timeframe','strategy','strategy_version','strategy_policy_bundle_version','tier','version_kind','constitution_hash'):
                if release_request.get(key)!=release.get(key):raise RuntimeError('GITHUB_RELEASE_REQUEST_BINDING:'+key)
            ids=release_request.get('strategy_instance_ids') or [release_request.get('strategy_instance_id')]
            if len(ids)!=1 or not ids[0] or ids!=release.get('strategy_instance_ids'):raise RuntimeError('GITHUB_RELEASE_INSTANCE_BINDING')
            matched=[entry for entry in pending if entry[3]['strategy_id']==release['strategy'] and entry[3]['timeframe']==release['timeframe'] and entry[3]['exact_strategy_version']==release['strategy_version'] and entry[3]['artifact']==release.get('runtime_artifact') and entry[3]['runtime_sha256']==release.get('runtime_sha256')]
            if len(matched)!=1:raise RuntimeError('GITHUB_RELEASE_RESEARCH_BINDING')
        elif manifest.get('automatic_registration_authorized') is not False:raise RuntimeError('GITHUB_RELEASE_APPROVAL_MISSING')
        if paths!=set(files):raise RuntimeError('GITHUB_RECEIVER_UNBOUND_ARTIFACT')
        inbox.mkdir(parents=True,exist_ok=True)
        for candidate_id,target,raw,row in pending:
            if not target.exists():
                import tempfile
                temporary=Path(tempfile.mkdtemp(prefix='.incoming-',dir=str(inbox)))
                try:
                    (temporary/'runtime.py').write_bytes(raw)
                    (temporary/'EVIDENCE.json').write_text(json.dumps({'candidate':row,'source_commit':manifest['source_commit'],'received_commit':revision},ensure_ascii=False,sort_keys=True),encoding='utf-8')
                    os.replace(temporary,target)
                finally:
                    if temporary.exists():shutil.rmtree(temporary)
        keep={row[0] for row in pending}
        stale=sorted((path for path in inbox.iterdir() if path.is_dir() and not path.is_symlink() and path.name not in keep),key=lambda path:path.stat().st_mtime,reverse=True)
        for path in stale[max(0,64-len(keep)):]:shutil.rmtree(path)
        git('reflog','expire','--expire=now','--all');git('gc','--prune=now')
        apply_status='WAIT_SERVER_VALIDATION' if pending else 'WAIT_FINAL_RESEARCH'
        config_changed=False
        if release is not None and settings.get('automatic_registration_enabled') is True:
            if not 0<=time.time()-float(release.get('created_at_epoch') or 0)<=600:
                apply_status='WAIT_RELEASE_REVALIDATION'
            elif (BASE/'SERVER_PUSH_HOLD.flag').exists():
                apply_status='WAIT_SERVER_HOLD'
            else:
                import tempfile
                lock_path=Path('/tmp/bbot_source_sync.lock')
                if lock_path.is_symlink():raise RuntimeError('GITHUB_RELEASE_LOCK_SYMLINK')
                with lock_path.open('a+b') as release_lock:
                    try:fcntl.flock(release_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:raise RuntimeError('GITHUB_RELEASE_MAINTENANCE_BUSY')
                    config_path=BASE/'strategy_config.json';last=state/'r4_release_last_applied.json'
                    if config_path.is_symlink() or last.is_symlink():raise RuntimeError('GITHUB_RELEASE_CONFIG_SYMLINK')
                    current=config_path.read_bytes();config_sha=hashlib.sha256(current).hexdigest()
                    old_last=last.read_bytes() if last.exists() else None
                    previous_release=json.loads(old_last) if old_last else {}
                    if previous_release.get('request_id')==release['request_id']:
                        apply_status='APPLIED_DUPLICATE'
                    elif config_sha!=release.get('base_config_sha256'):
                        apply_status='WAIT_LIVE_CONFIG_CHANGED'
                    else:
                        frozen={'runner.py':'4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160','order.py':'513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c','risk_engine.py':'768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6','candidate.py':'b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac'}
                        if any((BASE/name).is_symlink() or hashlib.sha256((BASE/name).read_bytes()).hexdigest()!=digest for name,digest in frozen.items()):raise RuntimeError('GITHUB_RELEASE_FROZEN_CORE_CHANGED')
                        old=json.loads(current);new=json.loads(release_config);iid=release['strategy_instance_ids'][0];strategy=release['strategy'];tf=release['timeframe']
                        if not isinstance(new,dict) or new.get('frames')!=old.get('frames'):raise RuntimeError('GITHUB_RELEASE_FRAME_OWNER_CHANGE_FORBIDDEN')
                        if {k:v for k,v in new.items() if k not in {'strategy_registry','r4_runtime'}}!={k:v for k,v in old.items() if k not in {'strategy_registry','r4_runtime'}}:raise RuntimeError('GITHUB_RELEASE_UNRELATED_CONFIG_CHANGED')
                        old_runtime=old.get('r4_runtime') or {};new_runtime=new.get('r4_runtime') or {}
                        if {k:v for k,v in new_runtime.items() if k!='strategy_instances'}!={k:v for k,v in old_runtime.items() if k!='strategy_instances'}:raise RuntimeError('GITHUB_RELEASE_RUNTIME_POLICY_CHANGED')
                        old_rows=old_runtime.get('strategy_instances') or {};new_rows=new_runtime.get('strategy_instances') or {}
                        if iid in old_rows or set(new_rows)!=set(old_rows)|{iid}:raise RuntimeError('GITHUB_RELEASE_NEW_INSTANCE_REQUIRED')
                        for key,value in old_rows.items():
                            expected=json.loads(json.dumps(value))
                            if isinstance(expected,dict) and str(expected.get('strategy_id') or '').upper()==strategy and str(expected.get('timeframe') or '').upper()==tf and expected.get('entry_enabled',expected.get('enabled',False)) is True:
                                expected['entry_enabled']=False;expected['superseded_by_strategy_instance_id']=iid
                            if new_rows.get(key)!=expected:raise RuntimeError('GITHUB_RELEASE_EXISTING_INSTANCE_CHANGED')
                        if sum(isinstance(item,dict) and item.get('enabled') is True and item.get('entry_enabled',False) is True and str(item.get('timeframe') or '').upper()==tf and item.get('server_activation_authorized') is True for item in new_rows.values())>2:raise RuntimeError('GITHUB_RELEASE_FRAME_STRATEGY_CAP')
                        row=new_rows[iid]
                        if row.get('strategy_instance_id')!=iid or row.get('strategy_id')!=strategy or row.get('timeframe')!=tf or row.get('exact_strategy_version')!=release['strategy_version'] or row.get('strategy_policy_bundle_version')!=release['strategy_policy_bundle_version']:raise RuntimeError('GITHUB_RELEASE_RUNTIME_BINDING')
                        if row.get('tier')!='CANARY' or row.get('qualification')!='CANARY' or any(row.get(key) is not True for key in ('enabled','entry_enabled','server_activation_authorized','approved_for_server_release')) or not isinstance(row.get('policy_snapshot'),dict) or not row['policy_snapshot'] or float(row.get('capital_weight') or 0)!=0.20:raise RuntimeError('GITHUB_RELEASE_CANARY_CONTRACT')
                        old_registry=old.get('strategy_registry') or {};new_registry=new.get('strategy_registry') or {}
                        if {k:v for k,v in new_registry.items() if k!=strategy}!={k:v for k,v in old_registry.items() if k!=strategy}:raise RuntimeError('GITHUB_RELEASE_UNRELATED_REGISTRY_CHANGED')
                        destination=str(release.get('runtime_strategy_file') or '')
                        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,159}\.py',destination) or '..' in destination or (new_registry.get(strategy) or {}).get('file')!=destination:raise RuntimeError('GITHUB_RELEASE_DESTINATION_INVALID')
                        directory=BASE/'assist/strategies';artifact=directory/destination
                        if directory.is_symlink() or artifact.is_symlink():raise RuntimeError('GITHUB_RELEASE_ARTIFACT_SYMLINK')
                        old_artifact=artifact.read_bytes() if artifact.exists() else None
                        runtime_raw=matched[0][2]
                        if old_artifact is not None and old_artifact!=runtime_raw:raise RuntimeError('GITHUB_RELEASE_EXISTING_ARTIFACT_IMMUTABLE')
                        legacy=subprocess.run(['systemctl','is-active','--quiet','runner.service'],capture_output=True,timeout=10)
                        service=subprocess.run(['systemctl','show','r4-runtime.service','-p','MainPID','--value'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
                        if legacy.returncode==0 or not service or service=='0':raise RuntimeError('GITHUB_RELEASE_SERVICE_NOT_SAFE')
                        backup=state/'github_release_backup'
                        if backup.is_symlink():raise RuntimeError('GITHUB_RELEASE_BACKUP_SYMLINK')
                        backup.mkdir(exist_ok=True)
                        (backup/'strategy_config.json').write_bytes(current)
                        (backup/'manifest.json').write_text(json.dumps({'request_id':release['request_id'],'base_config_sha256':config_sha,'destination':destination,'artifact_existed':old_artifact is not None},sort_keys=True))
                        (backup/'artifact.py').write_bytes(old_artifact or b'')
                        (backup/'last_applied.json').write_bytes(old_last or b'')
                        directory.mkdir(parents=True,exist_ok=True)
                        with tempfile.TemporaryDirectory(prefix='github-release-',dir=str(state)) as folder:
                            stage=Path(folder)
                            if hashlib.sha256(config_path.read_bytes()).hexdigest()!=config_sha:raise RuntimeError('GITHUB_RELEASE_LIVE_CONFIG_CHANGED_DURING_VALIDATION')
                            try:
                                (stage/'artifact.py').write_bytes(runtime_raw);os.replace(stage/'artifact.py',artifact)
                                (stage/'config.json').write_bytes(release_config);os.chmod(stage/'config.json',config_path.stat().st_mode & 0o777);os.replace(stage/'config.json',config_path)
                                time.sleep(3)
                                after=subprocess.run(['systemctl','show','r4-runtime.service','-p','MainPID','--value'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
                                if after!=service or subprocess.run(['systemctl','is-active','--quiet','r4-runtime.service'],capture_output=True,timeout=10).returncode:raise RuntimeError('GITHUB_RELEASE_SERVICE_CHANGED')
                                (stage/'last.json').write_text(json.dumps(release,sort_keys=True));os.replace(stage/'last.json',last)
                                config_changed=True;apply_status='APPLIED'
                            except Exception:
                                (stage/'restore.json').write_bytes(current);os.replace(stage/'restore.json',config_path)
                                if old_artifact is None:artifact.unlink(missing_ok=True)
                                else:(stage/'old.py').write_bytes(old_artifact);os.replace(stage/'old.py',artifact)
                                if old_last is None:last.unlink(missing_ok=True)
                                else:(stage/'last-old.json').write_bytes(old_last);os.replace(stage/'last-old.json',last)
                                raise
        result={'schema':'B_BOT_R4_GITHUB_RECEIVER_STATE_V1','status':apply_status,'contract':[settings.get('constitution_hash'),settings.get('engine_sha256')],'received_commit':revision,'source_commit':manifest['source_commit'],'feedback_candidates':[{'timeframe':row['timeframe'],'required_symbols':row.get('required_symbols') or []} for row in rows],'candidate_ids':sorted(keep),'received_candidates':len(pending),'updated_at':int(time.time()),'automatic_registration_enabled':settings.get('automatic_registration_enabled') is True,'operating_config_changed':config_changed,'orders_touched':False}
        temporary=status_path.with_name(status_path.name+'.'+str(os.getpid())+'.tmp');temporary.write_text(json.dumps(result,sort_keys=True),encoding='utf-8');os.replace(temporary,status_path)
        # 동일 head는 위에서 반환하여 반복 성공 로그를 만들지 않는다.
        log=BASE/'logs/runner.log';log.parent.mkdir(parents=True,exist_ok=True)
        if previous.get('received_commit')!=revision or previous.get('status')!=result['status']:
            with log.open('a',encoding='utf-8') as output:output.write('[주문][R4][GITHUB_RECEIVE] '+json.dumps(result,sort_keys=True)+'\n')
        if settings.get('feedback_enabled') is True:result['feedback']=publish_github_feedback(rows,revision)
        return result
    finally:
        receiver_lock.close()


# ================= 서버 → GitHub: 확정봉과 검증용 운영 설정 =================
def publish_github_feedback(candidates, received_commit):
    """기존 수집기 결과만 전달한다. 거래 API/운영 DB/설정에는 쓰지 않는다."""
    import hashlib,re,subprocess,time
    import pandas as pd
    state=BASE/'state';settings=json.loads((state/'r4_github_receiver.json').read_text(encoding='utf-8-sig'))
    if settings.get('feedback_enabled') is not True:return {'status':'DISABLED'}
    heartbeat_path=BASE/'strategy_lab/results/v3/collector_heartbeat.json'
    heartbeat=json.loads(heartbeat_path.read_text(encoding='utf-8-sig'));stamp=time.time()
    if heartbeat.get('schema')!='B_BOT_V3_COLLECTOR_HEARTBEAT_V1' or not 0<=stamp-float(heartbeat.get('updated_at_epoch') or 0)<=180:raise RuntimeError('FEEDBACK_COLLECTOR_NOT_LIVE')
    config_raw=(BASE/'strategy_config.json').read_bytes();config=json.loads(config_raw.decode('utf-8-sig'))
    if len(config_raw)>1024**2 or any(re.search(pattern,config_raw) for pattern in (rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'(?:AKIA|ASIA)[A-Z0-9]{16}',rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{20,}',rb'\d{6,12}:[A-Za-z0-9_-]{35,}')):raise RuntimeError('FEEDBACK_CONFIG_UNSAFE')
    def credential_fields(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if re.sub('[^a-z]','',str(key).lower()) in {'apikey','apisecret','privatekey','password','accesstoken','telegramtoken','bottoken'} and item:raise RuntimeError('FEEDBACK_CONFIG_CREDENTIAL_FIELD')
                credential_fields(item)
        elif isinstance(value,list):
            for item in value:credential_fields(item)
    credential_fields(config)
    symbols={str(x).upper() for x in config.get('core_symbols') or []};frames={'1H'}
    for candidate in candidates:
        frames.add(str(candidate.get('timeframe') or '').upper())
        symbols.update(str(x).upper() for x in candidate.get('required_symbols') or [])
    intervals={'30M':1800,'1H':3600,'6H':21600,'12H':43200,'1D':86400,'1W':604800}
    if not symbols or len(symbols)>35 or any(not re.fullmatch('[A-Z0-9]{2,30}USDT',symbol) for symbol in symbols) or not frames<=set(intervals):raise RuntimeError('FEEDBACK_UNIVERSE_INVALID')
    if not symbols<=set(heartbeat.get('active_symbols') or []):raise RuntimeError('FEEDBACK_REQUIRED_SYMBOL_NOT_COLLECTED')
    cache_path=state/'r4_github_market_cache.json'
    if cache_path.is_symlink():raise RuntimeError('FEEDBACK_CACHE_SYMLINK')
    cache={}
    if cache_path.exists():
        try:cache=json.loads(cache_path.read_text())
        except (ValueError,OSError):pass
    slot=int(stamp//min(intervals[frame] for frame in frames));identity=[sorted(symbols),sorted(frames),slot]
    if cache.get('identity')==identity:market=cache['market']
    else:
        bars={}
        for symbol in sorted(symbols):
            bars[symbol]={}
            for frame in sorted(frames):
                paths=[BASE/folder/symbol/(case+'.parquet') for folder in ('data/candles','backtest_data') for case in (frame.lower(),frame)]
                path=next((path for path in paths if path.is_file() and not path.is_symlink()),None)
                if path is None or path.stat().st_size>64*1024**2:raise RuntimeError('FEEDBACK_CANDLE_SOURCE_MISSING:'+symbol+':'+frame)
                data=pd.read_parquet(path)
                required={'open_time','open','high','low','close','volume','is_closed'}
                if not required<=set(data.columns):raise RuntimeError('FEEDBACK_CANDLE_COLUMNS:'+symbol+':'+frame)
                data=data[data['is_closed']==True].copy();data['open_time']=pd.to_datetime(data['open_time'],utc=True,errors='coerce');data=data.dropna(subset=['open_time']).sort_values('open_time').drop_duplicates('open_time').tail(160)
                if len(data)<64:raise RuntimeError('FEEDBACK_INSUFFICIENT_CLOSED_BARS:'+symbol+':'+frame)
                rows=[]
                for record in data.to_dict('records'):
                    opening=int(record['open_time'].timestamp()*1000);values=[float(record[field]) for field in ('open','high','low','close','volume')]
                    if not all(__import__('math').isfinite(v) for v in values) or min(values[:4])<=0 or values[4]<0 or values[1]<max(values[0],values[2],values[3]) or values[2]>min(values[0],values[1],values[3]) or opening/1000+intervals[frame]>stamp+1:raise RuntimeError('FEEDBACK_INVALID_CLOSED_BAR')
                    rows.append([opening,*values])
                age=stamp-(rows[-1][0]/1000+intervals[frame])
                if not 0<=age<=max(21600,2.2*intervals[frame]):raise RuntimeError('FEEDBACK_CLOSED_BAR_STALE:'+symbol+':'+frame)
                bars[symbol][frame]=rows
        market={'schema':'B_BOT_PUBLIC_CLOSED_BARS_V1','columns':['open_time_ms','open','high','low','close','volume'],'bars':bars}
        raw=json.dumps({'identity':identity,'market':market},sort_keys=True,allow_nan=False).encode()
        if len(raw)>8*1024**2:raise RuntimeError('FEEDBACK_MARKET_SIZE_LIMIT')
        temporary=cache_path.with_suffix('.tmp');temporary.write_bytes(raw);os.replace(temporary,cache_path)
    for symbol,items in market['bars'].items():
        for frame,rows in items.items():
            if not 0<=stamp-(rows[-1][0]/1000+intervals[frame])<=max(21600,2.2*intervals[frame]):raise RuntimeError('FEEDBACK_CACHED_BAR_STALE')
    payloads={'HEARTBEAT.json':json.dumps(heartbeat,sort_keys=True).encode(),'MARKET.json':json.dumps(market,sort_keys=True,allow_nan=False).encode(),'CONFIG.json':config_raw}
    manifest={'schema':'B_BOT_GITHUB_RUNTIME_FEEDBACK_V1','received_commit':received_commit,'created_at_epoch':stamp,'files':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in payloads.items()},'database_opened':False,'orders_touched':False,'symbols':sorted(symbols),'timeframes':sorted(frames)}
    payloads['MANIFEST.json']=json.dumps(manifest,sort_keys=True).encode()
    repo='git@github.com:hans1177/bbot-lab.git';ref='refs/heads/runtime-feedback';cache_dir=state/'github_runtime.git'
    env=os.environ.copy();env.update(GIT_SSH_COMMAND='ssh -i /home/ec2-user/.ssh/bbot_lab_feedback -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/ec2-user/.ssh/bbot_lab_hosts',GIT_AUTHOR_NAME='B-Bot runtime',GIT_AUTHOR_EMAIL='runtime@localhost',GIT_COMMITTER_NAME='B-Bot runtime',GIT_COMMITTER_EMAIL='runtime@localhost')
    def git(*args,data=None):
        result=subprocess.run(['git','--git-dir='+str(cache_dir),*args],input=data,env=env,capture_output=True,timeout=45)
        if result.returncode:raise RuntimeError('FEEDBACK_GIT_FAILED:'+args[0])
        return result.stdout.decode().strip()
    previous=git('ls-remote',repo,ref);expected=previous.split()[0] if previous else ''
    entries=[]
    for name,raw in payloads.items():
        if len(raw)>8*1024**2:raise RuntimeError('FEEDBACK_BLOB_SIZE_LIMIT')
        digest=git('hash-object','-w','--stdin',data=raw);entries.append('100644 blob '+digest+'\t'+name+'\n')
    tree=git('mktree',data=''.join(entries).encode())
    # 부모 없는 최신 스냅샷으로 원격 branch의 무제한 이력 누적을 피한다.
    commit=git('commit-tree',tree,data=b'Latest read-only runtime market feedback\n')
    git('push','--force-with-lease='+ref+':'+expected,repo,commit+':'+ref)
    git('update-ref',ref,commit)
    feedback_state=state/'r4_github_feedback_state.json'
    if feedback_state.is_symlink():raise RuntimeError('FEEDBACK_STATUS_SYMLINK')
    previous={}
    if feedback_state.exists():
        try:previous=json.loads(feedback_state.read_text())
        except (ValueError,OSError):pass
    scope=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    result={'status':'PUBLISHED','commit':commit,'updated_at_epoch':stamp,'scope':scope,'database_opened':False,'orders_touched':False,'symbols':len(symbols),'timeframes':sorted(frames)}
    temporary=feedback_state.with_suffix('.tmp');temporary.write_text(json.dumps(result,sort_keys=True));os.replace(temporary,feedback_state)
    if previous.get('scope')!=scope:
        log=BASE/'logs/runner.log';log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a',encoding='utf-8') as output:output.write('[주문][R4][GITHUB_FEEDBACK] '+json.dumps(result,sort_keys=True)+'\n')
    return {'status':'PUBLISHED','commit':commit,'symbols':len(symbols),'timeframes':sorted(frames),'orders_touched':False,'database_opened':False}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--receive-github',action='store_true');args=parser.parse_args()
    if not args.receive_github:parser.error('--receive-github required')
    try:
        result=receive_github_candidates()
    except Exception as error:
        import sys,time
        path=BASE/'state/r4_github_receiver_state.json'
        old={}
        if path.is_file() and not path.is_symlink():
            try:old=json.loads(path.read_text(encoding='utf-8-sig'))
            except Exception:pass
        reason=type(error).__name__+':'+str(error)[:160];stamp=int(time.time())
        emit_error=old.get('last_error')!=reason or stamp-int(old.get('last_error_logged_at') or 0)>=300
        if emit_error:
            log=BASE/'logs/runner.log';log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a',encoding='utf-8') as output:output.write('[주문][R4][GITHUB_RECEIVE_FAIL] '+reason+chr(10))
        result={**old,'status':'ERROR','last_error':reason,'last_error_ts':stamp,'last_error_logged_at':stamp if emit_error else old.get('last_error_logged_at',0),'automatic_registration_enabled':False,'orders_touched':False}
        if not path.is_symlink():
            path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.error.tmp');temporary.write_text(json.dumps(result),encoding='utf-8');os.replace(temporary,path)
        print(json.dumps(result,ensure_ascii=True));sys.exit(1)
    print(json.dumps(result,ensure_ascii=True))
