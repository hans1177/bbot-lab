# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_governance.py
"""B-Bot V3 Roadmap Constitution 강제 계층.

Research는 결과/후보 버전만 만들 수 있고 stable trading kernel 소스는 수정할 수 없다.
실제 활성 전환은 검증된 Strategy/Policy Version + Strategy Runtime Sync가 담당한다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 경로 / 상수
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
V3 = LAB / "results" / "v3"
CONSTITUTION = ROOT / "ROADMAP_CONSTITUTION_V3.json"
VERSION_REGISTRY = V3 / "version_registry.json"
CHANGE_CONTROL = V3 / "change_control_state.json"

EXPECTED_SCHEMA = "B_BOT_V3_ROADMAP_CONSTITUTION_V2"
EXPECTED_TRACKS = [
    "DIRECTION",
    "STRATEGY",
    "ENTRY_RISK",
    "EXIT",
    "REENTRY",
    "SYMBOL",
    "PORTFOLIO",
    "REGIME",
    "EXECUTION",
]
EXPECTED_CHANGE_ORDER = [
    "UNDERSTAND_EXISTING_STRUCTURE",
    "ROADMAP_CONSTITUTION",
    "VERIFIER",
    "IMPLEMENTATION",
    "SELF_VERIFICATION",
    "REGRESSION",
    "UPDATE_MASTER_HANDOFF_JSON",
]
EXPECTED_RELEASE_VALIDATION_GATES = {
    "OVERFIT": [
        "BOUNDED_SEARCH_ONLY",
        "SEARCH_UNTIL_WINNER_FORBIDDEN",
        "WALK_FORWARD_REQUIRED",
        "COST_STRESS_REQUIRED",
        "ONE_SHOT_SEALED_BLIND_REQUIRED",
        "SEALED_BLIND_REUSE_FOR_RESEARCH_FORBIDDEN",
        "CAUSAL_PREFIX_INVARIANCE_REQUIRED",
        "PROMOTION_REQUIRES_VALIDATION_AND_CANARY",
    ],
    "LOGIC": [
        "TRACK_SCOPE_SEPARATION",
        "STRATEGY_POLICY_VERSION_SEPARATION",
        "VERSION_STATE_TRANSITIONS_ENFORCED",
        "STABLE_KERNEL_IMMUTABLE_DURING_RESEARCH",
        "ROLLBACK_TARGET_PREVIOUS_VALIDATED_VERSION",
        "TRIAL73_FROZEN",
        "SERVER_PUSH_HOLD_UNTIL_LOCAL_PASS",
        "REAL_ORDER_OFF_DURING_LOCAL_TEST",
    ],
}

VERSION_TRANSITIONS = {
    "RESEARCH_CANDIDATE": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"PROVISIONAL", "CANARY", "REJECTED"},
    "PROVISIONAL": {"CANARY", "REJECTED"},
    "CANARY": {"SMALL", "ROLLED_BACK", "REJECTED"},
    "SMALL": {"NORMAL", "ROLLED_BACK"},
    "NORMAL": {"ROLLED_BACK"},
    "ROLLED_BACK": set(),
    "REJECTED": set(),
}

# ============================================================
# R4 Strategy Instance 상태
# ============================================================

R4_INSTANCE_TIERS = ("LOCAL_TEST", "LOCAL_SHADOW", "CANARY", "SMALL", "NORMAL")
R4_INSTANCE_TRANSITIONS = {
    "LOCAL_TEST": {"LOCAL_SHADOW", "REJECTED"},
    "LOCAL_SHADOW": {"CANARY", "REJECTED"},
    "CANARY": {"SMALL", "ROLLED_BACK", "QUARANTINED"},
    "SMALL": {"NORMAL", "CANARY", "ROLLED_BACK", "QUARANTINED"},
    "NORMAL": {"SMALL", "CANARY", "ROLLED_BACK", "QUARANTINED"},
    "ROLLED_BACK": set(),
    "QUARANTINED": {"LOCAL_TEST"},
    "REJECTED": set(),
}

# ============================================================
# 유틸
# ============================================================


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def atomic(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def stable_hash(obj: Any) -> str:
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# Roadmap Constitution
# ============================================================


def constitution() -> dict[str, Any]:
    obj = load(CONSTITUTION, {})
    if not isinstance(obj, dict):
        raise RuntimeError("ROADMAP_CONSTITUTION_INVALID")
    return obj


def validate_constitution() -> dict[str, Any]:
    obj = constitution()
    errors: list[str] = []

    if obj.get("schema") != EXPECTED_SCHEMA:
        errors.append("SCHEMA")

    tracks = list((obj.get("tracks") or {}).keys())
    if (
        len(tracks) != len(EXPECTED_TRACKS)
        or set(tracks) != set(EXPECTED_TRACKS)
    ):
        errors.append("TRACKS")

    # ========================================================
    # Research Architecture V3
    # ========================================================

    research_architecture_v3 = (
        obj.get("research_architecture_v3")
        or {}
    )

    if research_architecture_v3.get("policy_id") != (
        "RA_MAIN_MIDDLE_DETAIL_20260830_V1"
    ):
        errors.append(
            "RESEARCH_ARCHITECTURE_POLICY_ID"
        )

    if research_architecture_v3.get("status") != (
        "FINAL_CONFIRMED_CONSTITUTION"
    ):
        errors.append(
            "RESEARCH_ARCHITECTURE_STATUS"
        )

    trailing_contract = (
        obj.get(
            "individual_strategy_trailing_optimization"
        )
        or {}
    )

    allocation_contract = (
        trailing_contract.get(
            "auto_research_allocation_contract"
        )
        or {}
    )

    if allocation_contract.get(
        "allocation_model"
    ) != "PRIORITY_QUEUE_WITH_RESOURCE_CAPS":
        errors.append(
            "AUTO_RESEARCH_ALLOCATION_MODEL"
        )

    backlog_allocation = (
        allocation_contract.get(
            "historical_314_backlog_mode"
        )
        or {}
    ).get(
        "allocation_percent"
    ) or {}

    if sum(
        int(x)
        for x in backlog_allocation.values()
    ) != 100:
        errors.append(
            "AUTO_RESEARCH_BACKLOG_ALLOCATION"
        )

    normal_allocation = (
        allocation_contract.get(
            "normal_mode"
        )
        or {}
    ).get(
        "allocation_percent"
    ) or {}

    if sum(
        int(x)
        for x in normal_allocation.values()
    ) != 100:
        errors.append(
            "AUTO_RESEARCH_NORMAL_ALLOCATION"
        )

    timeframe_allocation = (
        allocation_contract.get(
            "new_strategy_timeframe_distribution_percent"
        )
        or {}
    )

    if sum(
        int(x)
        for x in timeframe_allocation.values()
    ) != 100:
        errors.append(
            "AUTO_RESEARCH_TIMEFRAME_ALLOCATION"
        )

    if timeframe_allocation != {
        "30M": 20,
        "1H": 23,
        "6H": 23,
        "12H": 18,
        "1D": 10,
        "1W": 6,
    }:
        errors.append(
            "AUTO_RESEARCH_TIMEFRAME_ALLOCATION_EXACT"
        )

    if list(
        trailing_contract.get(
            "eligible_timeframes"
        )
        or []
    ) != [
        "30M",
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
    ]:
        errors.append(
            "TRADING_TIMEFRAME_UNIVERSE"
        )

    if list(
        trailing_contract.get(
            "reference_only_timeframes"
        )
        or []
    ) != ["1M"]:
        errors.append(
            "REFERENCE_ONLY_TIMEFRAME_UNIVERSE"
        )

    if (
        trailing_contract.get(
            "hard_pass_minimum_trades"
        )
        or {}
    ) != {
        "30M": 40,
        "1H": 30,
        "6H": 20,
        "12H": 15,
        "1D": 10,
        "1W": 6,
    }:
        errors.append(
            "HARD_PASS_MINIMUM_TRADES_BY_TF"
        )

    if int(
        allocation_contract.get(
            "max_workers_per_strategy_id_exact_version"
        )
        or 0
    ) != 2:
        errors.append(
            "AUTO_RESEARCH_EXACT_VERSION_WORKER_CAP"
        )

    if float(
        allocation_contract.get(
            "max_worker_share_per_strategy_family"
        )
        or 0.0
    ) > 0.30:
        errors.append(
            "AUTO_RESEARCH_FAMILY_WORKER_CAP"
        )

    trailing_allocation = (
        allocation_contract.get(
            "trailing"
        )
        or {}
    )

    if float(
        trailing_allocation.get(
            "cpu_share_max"
        )
        or 0.0
    ) > 0.30:
        errors.append(
            "AUTO_RESEARCH_TRAILING_CPU_CAP"
        )

    redevelopment_allocation = (
        allocation_contract.get(
            "redevelopment"
        )
        or {}
    )

    if float(
        redevelopment_allocation.get(
            "resource_share_max"
        )
        or 0.0
    ) > 0.10:
        errors.append(
            "AUTO_RESEARCH_REDEVELOPMENT_CAP"
        )

    trailing_contract = (
        obj.get(
            "individual_strategy_trailing_optimization"
        )
        or {}
    )

    gate_model = (
        trailing_contract.get(
            "gate_evaluation_model"
        )
        or {}
    )

    if gate_model.get(
        "model"
    ) != "ABSOLUTE_FAIL_PLUS_COMPENSATION_ZONE":
        errors.append(
            "GATE_EVALUATION_MODEL"
        )

    if gate_model.get(
        "absolute_fail_cannot_be_compensated"
    ) is not True:
        errors.append(
            "ABSOLUTE_FAIL_COMPENSATION_RULE"
        )

    redevelopment_contract = (
        trailing_contract.get(
            "redevelopment_contract"
        )
        or {}
    )

    if int(
        redevelopment_contract.get(
            "max_targeted_cycles_per_failed_gate"
        )
        or 0
    ) != 3:
        errors.append(
            "REDEVELOPMENT_MAX_CYCLES"
        )

    final_blind_contract = (
        trailing_contract.get(
            "final_blind_contract"
        )
        or {}
    )

    if final_blind_contract.get(
        "official_gate_name"
    ) != "SEALED_FINAL_BLIND":
        errors.append(
            "SEALED_FINAL_BLIND_GATE"
        )

    trailing_search_contract = (
        trailing_contract.get(
            "trailing_search_contract"
        )
        or {}
    )

    baseline = trailing_search_contract.get("baseline") or {}
    if float(baseline.get("start_pct") or 0.0) != 3.0 or float(baseline.get("callback_pct") or 0.0) != 1.5:
        errors.append("INDIVIDUAL_TRAILING_BASELINE")

    if trailing_search_contract.get("logical_constraint") != "CALLBACK_LT_START":
        errors.append("INDIVIDUAL_TRAILING_LOGICAL_CONSTRAINT")

    if trailing_search_contract.get("trailing_induced_reentry_blocked_during_exit_research") is not True:
        errors.append("INDIVIDUAL_TRAILING_REENTRY_ISOLATION")

    reentry_search_contract = (
        trailing_contract.get(
            "reentry_search_contract"
        )
        or {}
    )

    if reentry_search_contract.get("scope") != "TRAILING_STOP_SAME_DIRECTION_REENTRY_METHOD_RESEARCH":
        errors.append("INDIVIDUAL_REENTRY_SCOPE")

    reentry_candidates = reentry_search_contract.get("candidates") or []
    if len(reentry_candidates) != 3:
        errors.append("INDIVIDUAL_REENTRY_CANDIDATE_COUNT")
    else:
        methods = [str((x or {}).get("mode") or "").upper() for x in reentry_candidates if isinstance(x,dict)]
        if methods != [
            "CURRENT_STATE_CONFIRM_STRATEGY_BARS",
            "CURRENT_STATE_CONFIRM_STRATEGY_BARS",
            "CURRENT_STATE_RESET_RECLAIM",
        ]:
            errors.append("INDIVIDUAL_REENTRY_CANDIDATE_SET")
        confirms = [int((x or {}).get("confirm_bars") or 0) for x in reentry_candidates[:2]]
        if confirms != [2,3]:
            errors.append("INDIVIDUAL_REENTRY_CONFIRM_SET")

    if reentry_search_contract.get("same_bar_immediate_reorder_forbidden") is not True:
        errors.append("INDIVIDUAL_REENTRY_SAME_BAR")

    if reentry_search_contract.get("trailing_parameters_frozen_during_reentry_research") is not True:
        errors.append("INDIVIDUAL_REENTRY_TRAILING_FREEZE")

    if reentry_search_contract.get("fallback") != "CURRENT_STATE_NEXT_1H":
        errors.append("INDIVIDUAL_REENTRY_FALLBACK")

    expected_arch_stages = {
        "MAIN": [
            "SETUP",
            "TRIGGER",
            "FILTER",
        ],
        "MIDDLE": [
            "HOLD_RISK",
            "EXIT_TRAILING",
            "REENTRY",
        ],
        "DETAIL": [
            "REAL_COST",
            "SYMBOL_PORTFOLIO",
            "EXECUTION_REAL_GAP",
        ],
    }

    for axis, expected_stages in (
        expected_arch_stages.items()
    ):
        actual = (
            (
                research_architecture_v3
                .get(axis)
                or {}
            )
            .get("stages")
        )

        if actual != expected_stages:
            errors.append(
                f"RESEARCH_ARCHITECTURE_{axis}_STAGES"
            )

    allocation = (
        research_architecture_v3
        .get("resource_allocation")
        or {}
    )

    tiers = allocation.get("tiers") or []

    expected_allocations = [
        (0.50, 0.30, 0.20),
        (0.45, 0.30, 0.25),
        (0.40, 0.30, 0.30),
        (0.35, 0.30, 0.35),
        (0.30, 0.30, 0.40),
        (0.25, 0.30, 0.45),
    ]

    if len(tiers) < len(expected_allocations):
        errors.append(
            "RESEARCH_RESOURCE_TIERS"
        )
    else:
        for idx, expected_weights in enumerate(
            expected_allocations
        ):
            row = tiers[idx] or {}

            try:
                actual_weights = (
                    float(row.get("MAIN")),
                    float(row.get("MIDDLE")),
                    float(row.get("DETAIL")),
                )
            except Exception:
                errors.append(
                    f"RESEARCH_RESOURCE_TIER_{idx}"
                )
                continue

            if any(
                abs(a - b) > 1e-12
                for a, b in zip(
                    actual_weights,
                    expected_weights,
                )
            ):
                errors.append(
                    f"RESEARCH_RESOURCE_TIER_{idx}"
                )

    sealed = (
        (
            research_architecture_v3
            .get("evaluation")
            or {}
        )
        .get("sealed_blind")
        or {}
    )

    if sealed.get("new_main_only") is not True:
        errors.append(
            "SEALED_BLIND_NEW_MAIN_ONLY"
        )

    if sealed.get("reuse_for_middle") is not False:
        errors.append(
            "SEALED_BLIND_MIDDLE_REUSE"
        )

    if sealed.get("reuse_for_detail") is not False:
        errors.append(
            "SEALED_BLIND_DETAIL_REUSE"
        )

    shadow = (
        research_architecture_v3
        .get("shadow")
        or {}
    )

    if shadow.get(
        "mandatory_minimal_runtime_gate"
    ) is not True:
        errors.append(
            "SHADOW_MINIMAL_RUNTIME_GATE"
        )

    if shadow.get(
        "profitability_gate"
    ) is not False:
        errors.append(
            "SHADOW_PROFITABILITY_GATE"
        )

    canary = (
        research_architecture_v3
        .get("canary")
        or {}
    )

    try:
        canary_weight = float(
            canary.get("initial_capital_weight")
        )
    except Exception:
        canary_weight = -1.0

    if abs(canary_weight - 0.20) > 1e-12:
        errors.append(
            "CANARY_INITIAL_WEIGHT"
        )

    research_scheduler = (
        research_architecture_v3
        .get("research_scheduler")
        or {}
    )

    if research_scheduler.get("status") != (
        "FINAL_CONFIRMED"
    ):
        errors.append(
            "RESEARCH_SCHEDULER_STATUS"
        )

    if (
        (
            research_scheduler
            .get("cheap_first_policy")
            or {}
        )
        .get("enabled")
        is not True
    ):
        errors.append(
            "RESEARCH_SCHEDULER_CHEAP_FIRST"
        )

    if (
        (
            research_scheduler
            .get("early_stop")
            or {}
        )
        .get("enabled")
        is not True
    ):
        errors.append(
            "RESEARCH_SCHEDULER_EARLY_STOP"
        )

    if (
        (
            research_scheduler
            .get("bounded_compute")
            or {}
        )
        .get("required")
        is not True
    ):
        errors.append(
            "RESEARCH_SCHEDULER_BOUNDED_COMPUTE"
        )

    chapters = obj.get("chapters") or {}
    clause_ids: list[str] = []
    for chapter in [str(x) for x in range(1, 13)]:
        row = chapters.get(chapter) or {}
        clauses = row.get("clauses") or []
        for item in clauses:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                clause_ids.append(str(item[0]))
            else:
                errors.append(f"CHAPTER_{chapter}_CLAUSE_FORMAT")

    if len(clause_ids) != 93 or len(set(clause_ids)) != 93:
        errors.append("CHAPTER_CLAUSE_93")

    gov = obj.get("governance_clauses") or []
    gov_ids = [str(x.get("id")) for x in gov if isinstance(x, dict)]
    if len(gov_ids) != 12 or len(set(gov_ids)) != 12:
        errors.append("GOVERNANCE_CLAUSE_12")

    if obj.get("change_control_order") != EXPECTED_CHANGE_ORDER:
        errors.append("CHANGE_CONTROL_ORDER")

    kernel = obj.get("stable_trading_kernel") or {}
    if kernel.get("policy") != "FROZEN_CORE_RESEARCH_CANNOT_MUTATE":
        errors.append("STABLE_KERNEL_POLICY")
    protected = kernel.get("protected_files") or []
    for required in ("runner.py", "order.py", "gate.py"):
        if required not in protected:
            errors.append(f"PROTECTED_{required}")

    open_position_policy = (
        obj.get("open_position_version_policy")
        or {}
    )

    expected_open_position_policy = {
        "policy_id":
            "ACTIVE_RUNTIME_VERSION_FOLLOWS_PROMOTED_NORMAL_V1",
        "entry_facts_immutable": True,
        "normal_promotion_updates_open_positions": True,
        "normal_rollback_updates_open_positions": True,
        "canary_small_scope_only": True,
        "global_apply_tier": "NORMAL",
        "preserve_exchange_position": True,
        "close_reopen_for_version_update": False,
        "change_position_qty_for_version_update": False,
        "change_exchange_leverage_for_version_update": False,
        "version_history_required": True,
        "version_change_timestamp_required": True,
        "mixed_version_lifecycle_mark_required": True,
        "mixed_version_lifecycle_excluded_from_pure_canary": True,
    }

    for key, expected in expected_open_position_policy.items():
        if open_position_policy.get(key) != expected:
            errors.append(
                f"OPEN_POSITION_VERSION_POLICY_{key}"
            )

    immutable_fields = (
        open_position_policy.get("immutable_fields")
        or []
    )

    for required in (
        "entry_id",
        "entry_price",
        "entry_ts",
        "entry_tf",
        "entry_strategy",
        "entry_leverage",
    ):
        if required not in immutable_fields:
            errors.append(
                f"OPEN_POSITION_IMMUTABLE_{required}"
            )

    mutable_runtime_fields = (
        open_position_policy.get("mutable_runtime_fields")
        or []
    )

    for required in (
        "active_owner_tf",
        "active_owner_strategy",
        "active_owner_leverage",
        "strategy_version",
        "policy_versions",
        "policy_snapshot",
    ):
        if required not in mutable_runtime_fields:
            errors.append(
                f"OPEN_POSITION_RUNTIME_{required}"
            )

    local_server = (
        obj.get("local_server_separation_policy")
        or {}
    )

    if local_server.get("policy_id") != (
        "LOCAL_RESEARCH_SERVER_EXECUTION_SEPARATION_V1"
    ):
        errors.append("LOCAL_SERVER_POLICY_ID")

    if local_server.get("local_role") != "RESEARCH_LAB":
        errors.append("LOCAL_ROLE_RESEARCH_LAB")

    if local_server.get("server_role") != (
        "VALIDATED_RUNTIME_EXECUTOR"
    ):
        errors.append("SERVER_ROLE_RUNTIME_EXECUTOR")

    if local_server.get(
        "server_never_promotes_versions"
    ) is not True:
        errors.append("SERVER_PROMOTION_FORBIDDEN")

    if local_server.get(
        "server_never_researches_strategies"
    ) is not True:
        errors.append("SERVER_RESEARCH_FORBIDDEN")

    pull = (
        local_server.get("server_to_local_sync")
        or {}
    )

    if pull.get("name") != (
        "SERVER_TO_LOCAL_READ_ONLY_PULL"
    ):
        errors.append("SERVER_LOCAL_PULL_NAME")

    if pull.get("read_only") is not True:
        errors.append("SERVER_LOCAL_PULL_READ_ONLY")

    if pull.get("server_write") is not False:
        errors.append("SERVER_LOCAL_PULL_WRITE")

    release = (
        local_server.get("local_to_server_sync")
        or {}
    )

    if release.get("name") != (
        "LOCAL_TO_SERVER_VALIDATED_RELEASE"
    ):
        errors.append("LOCAL_SERVER_RELEASE_NAME")

    if release.get(
        "explicit_release_required"
    ) is not True:
        errors.append("LOCAL_SERVER_RELEASE_EXPLICIT")

    if release.get("validated_only") is not True:
        errors.append("LOCAL_SERVER_VALIDATED_ONLY")

    if release.get("minimum_tier") != "CANARY":
        errors.append("LOCAL_SERVER_CANARY_MINIMUM_REQUIRED")

    for key in (
        "research_files_forbidden",
        "strategy_lab_forbidden",
        "verification_files_forbidden",
        "result_files_forbidden",
    ):
        if release.get(key) is not True:
            errors.append(
                f"LOCAL_SERVER_{key.upper()}"
            )

    continuous = (
        obj.get("continuous_operation_pipeline")
        or {}
    )

    if continuous.get("policy_id") != (
        "LOCAL_CONTINUOUS_RESEARCH_SERVER_"
        "VALIDATED_RELEASE_V1"
    ):
        errors.append(
            "CONTINUOUS_OPERATION_POLICY_ID"
        )

    server_local = (
        continuous.get("server_to_local")
        or {}
    )

    if server_local.get("mode") != "READ_ONLY_SYNC":
        errors.append(
            "SERVER_LOCAL_SYNC_NOT_READ_ONLY"
        )

    if server_local.get("continuous") is not False:
        errors.append(
            "SERVER_LOCAL_SYNC_MUST_BE_SCHEDULED"
        )

    if server_local.get("schedule_type") != "FIXED_DAILY":
        errors.append(
            "SERVER_LOCAL_SYNC_SCHEDULE_TYPE"
        )

    if server_local.get("timezone") != "Asia/Seoul":
        errors.append(
            "SERVER_LOCAL_SYNC_TIMEZONE"
        )

    if server_local.get("times") != ["09:00", "21:00"]:
        errors.append(
            "SERVER_LOCAL_SYNC_TIMES"
        )

    if server_local.get("missed_schedule_catchup") is not True:
        errors.append(
            "SERVER_LOCAL_SYNC_CATCHUP"
        )

    if server_local.get("server_write") is not False:
        errors.append(
            "SERVER_LOCAL_SYNC_WRITE_FORBIDDEN"
        )

    local_research = (
        continuous.get("local_research")
        or {}
    )

    if local_research.get("mode") != (
        "CONTINUOUS_RESEARCH"
    ):
        errors.append(
            "LOCAL_RESEARCH_NOT_CONTINUOUS"
        )

    if local_research.get(
        "runs_while_pc_online"
    ) is not True:
        errors.append(
            "LOCAL_RESEARCH_PC_ONLINE_RULE"
        )

    if local_research.get("real_order") is not False:
        errors.append(
            "LOCAL_RESEARCH_REAL_ORDER_FORBIDDEN"
        )

    approval = (
        continuous.get("approval_gate")
        or {}
    )

    if approval.get("validated_only") is not True:
        errors.append(
            "APPROVAL_VALIDATED_ONLY"
        )

    if approval.get("normal_only") is not False:
        errors.append(
            "APPROVAL_MUST_ALLOW_INITIAL_CANARY"
        )

    if approval.get("initial_strategy_instance_canary_allowed") is not True:
        errors.append("APPROVAL_INITIAL_CANARY_NOT_EXPLICIT")

    if approval.get("release_flag") != (
        "approved_for_server_release"
    ):
        errors.append(
            "APPROVAL_RELEASE_FLAG"
        )

    local_server = (
        continuous.get("local_to_server")
        or {}
    )

    if local_server.get("mode") != (
        "VALIDATED_STRATEGY_OVERWRITE"
    ):
        errors.append(
            "LOCAL_SERVER_RELEASE_MODE"
        )

    if local_server.get("approved_only") is not True:
        errors.append(
            "LOCAL_SERVER_APPROVED_ONLY"
        )

    if local_server.get("minimum_tier") != "CANARY":
        errors.append(
            "LOCAL_SERVER_CANARY_MINIMUM_REQUIRED"
        )

    if local_server.get(
        "explicit_release_required"
    ) is not True:
        errors.append(
            "LOCAL_SERVER_EXPLICIT_RELEASE"
        )

    forbidden = set(
        local_server.get("forbidden_payload")
        or []
    )

    for required in (
        "strategy_lab",
        "verify_files",
        "research_results",
        "shadow_research_code",
    ):
        if required not in forbidden:
            errors.append(
                f"SERVER_FORBIDDEN_PAYLOAD_{required}"
            )

    server_runtime = (
        continuous.get("server_runtime")
        or {}
    )

    if server_runtime.get("role") != "EXECUTION_ONLY":
        errors.append(
            "SERVER_NOT_EXECUTION_ONLY"
        )

    for key in (
        "research",
        "backtest",
        "shadow_research",
        "promotion_decision",
    ):
        if server_runtime.get(key) is not False:
            errors.append(
                f"SERVER_FORBIDDEN_{key.upper()}"
            )

    if server_runtime.get(
        "open_positions_follow_latest_normal"
    ) is not True:
        errors.append(
            "OPEN_POSITION_NORMAL_FOLLOW"
        )

    if server_runtime.get(
        "version_update_close_reopen"
    ) is not False:
        errors.append(
            "VERSION_UPDATE_CLOSE_REOPEN_FORBIDDEN"
        )

    versioning = obj.get("versioning") or {}
    if versioning.get("strategy") != "STRATEGY_VERSION_REQUIRED":
        errors.append("STRATEGY_VERSION_REQUIRED")
    if versioning.get("direct_core_patch_for_policy") != "FORBIDDEN":
        errors.append("DIRECT_CORE_POLICY_PATCH")

    lifecycle = obj.get("lifecycle_research") or []
    for required in (
        "ENTRY_SIGNAL",
        "ENTRY_RISK",
        "HOLD_PATH_MFE_MAE_GIVEBACK",
        "EXIT_TRAILING_TIMING",
        "REENTRY",
        "COST_SLIPPAGE_FUNDING",
        "REAL_BACKTEST_GAP",
    ):
        if required not in lifecycle:
            errors.append(f"LIFECYCLE_{required}")

    release_gates = obj.get("release_validation_gates") or {}
    for gate_name, expected_rules in EXPECTED_RELEASE_VALIDATION_GATES.items():
        gate = release_gates.get(gate_name) if isinstance(release_gates, dict) else None
        if not isinstance(gate, dict):
            errors.append(f"RELEASE_GATE_{gate_name}_MISSING")
            continue
        if gate.get("required") is not True:
            errors.append(f"RELEASE_GATE_{gate_name}_NOT_REQUIRED")
        if gate.get("must_pass_before_package") is not True:
            errors.append(f"RELEASE_GATE_{gate_name}_PACKAGE")
        if gate.get("must_pass_before_server_release") is not True:
            errors.append(f"RELEASE_GATE_{gate_name}_SERVER")
        rules = gate.get("rules") or []
        if rules != expected_rules:
            errors.append(f"RELEASE_GATE_{gate_name}_RULES")

    legacy = obj.get("legacy") or {}
    if legacy.get("trial73") != "COMPLETED_DO_NOT_RERUN":
        errors.append("TRIAL73")
    if int(legacy.get("search_counter") or -1) != 73:
        errors.append("LEGACY_SEARCH_73")
    if int(legacy.get("parameter_counter") or -1) != 73:
        errors.append("LEGACY_PARAMETER_73")

    full_impl = (trailing_contract.get("full_validation_implementation") or {})
    if full_impl.get("regime_uses_future_information") is not False:
        errors.append("FULL_VALIDATION_REGIME_FUTURE_INFO")
    if full_impl.get("profit_concentration_metric") != "TOP_5PCT_TRADES_NET_PROFIT_SHARE":
        errors.append("FULL_VALIDATION_PROFIT_CONCENTRATION")
    final_blind_contract = trailing_contract.get("final_blind_contract") or {}
    if final_blind_contract.get("candidate_universe_source") != "EXISTING_SYMBOL_V3_ELIGIBLE_CANDIDATES_SNAPSHOT":
        errors.append("SEALED_FINAL_BLIND_UNIVERSE_SOURCE")
    if final_blind_contract.get("validation_type") != "UNEXPOSED_SYMBOL_TIME_WINDOW_FINAL_BLIND":
        errors.append("SEALED_FINAL_BLIND_WINDOW_TYPE")
    if final_blind_contract.get("blind_unit") != "SYMBOL_TIME_WINDOW":
        errors.append("SEALED_FINAL_BLIND_UNIT")
    if final_blind_contract.get("pool_insufficient_policy") != "ROTATE_WINDOW_THEN_FAIL_CLOSED_NO_OVERLAP_REUSE":
        errors.append("SEALED_FINAL_BLIND_POOL_POLICY")
    if final_blind_contract.get("used_blind_symbol_window_reuse_forbidden") is not True:
        errors.append("SEALED_FINAL_BLIND_WINDOW_REUSE")
    if final_blind_contract.get("same_symbol_fresh_nonoverlap_window_allowed") is not True:
        errors.append("SEALED_FINAL_BLIND_FRESH_WINDOW_REUSE")
    if int(final_blind_contract.get("blind_window_days") or 0) != 90:
        errors.append("SEALED_FINAL_BLIND_WINDOW_DAYS")
    if int(final_blind_contract.get("boundary_purge_days") or -1) != 7:
        errors.append("SEALED_FINAL_BLIND_PURGE_DAYS")
    if final_blind_contract.get("cohort_batching") != "ALL_CURRENT_FROZEN_PENDING_BEFORE_RESULT_OPEN":
        errors.append("SEALED_FINAL_BLIND_COHORT_BATCHING")
    if final_blind_contract.get("same_cohort_shared_across_frozen_exact_versions") is not True:
        errors.append("SEALED_FINAL_BLIND_COHORT_SHARE")
    if final_blind_contract.get("evaluation_model") != "ABSOLUTE_ONLY_NO_COMPENSATION":
        errors.append("SEALED_FINAL_BLIND_EVALUATION_MODEL")
    if abs(float(final_blind_contract.get("minimum_trade_fraction_of_hard_pass") or 0.0)-0.25)>1e-12:
        errors.append("SEALED_FINAL_BLIND_MIN_TRADE_FRACTION")
    if int(final_blind_contract.get("minimum_active_symbols") or 0) != 3:
        errors.append("SEALED_FINAL_BLIND_MIN_ACTIVE_SYMBOLS")
    if abs(float(final_blind_contract.get("minimum_positive_active_symbol_ratio") or 0.0)-0.40)>1e-12:
        errors.append("SEALED_FINAL_BLIND_MIN_POSITIVE_SYMBOL_RATIO")
    if int(final_blind_contract.get("max_windows_per_exact_version") or 0) != 4:
        errors.append("SEALED_FINAL_BLIND_MAX_WINDOWS")
    if final_blind_contract.get("insufficient_sample_policy") != "FRESH_UNEXPOSED_WINDOW_SAME_EXACT_VERSION_NO_TUNING":
        errors.append("SEALED_FINAL_BLIND_INSUFFICIENT_SAMPLE_POLICY")
    if final_blind_contract.get("pass_next_stage") != "LOCAL_SHADOW_PENDING":
        errors.append("SEALED_FINAL_BLIND_PASS_NEXT")
    if final_blind_contract.get("exact_source_sha256_sealed_before_evaluation") is not True:
        errors.append("SEALED_FINAL_BLIND_SOURCE_HASH_SEAL")
    if final_blind_contract.get("trailing_reentry_snapshot_sealed_before_evaluation") is not True:
        errors.append("SEALED_FINAL_BLIND_POLICY_SNAPSHOT_SEAL")
    if final_blind_contract.get("blind_failure_automatic_tuning_forbidden") is not True:
        errors.append("SEALED_FINAL_BLIND_FAIL_TUNING")

    runtime_binding = trailing_contract.get("runtime_binding_contract") or {}
    if runtime_binding.get("strategy_instance_behavior_source") != "SEALED_EXACT_VERSION_RESEARCH_SNAPSHOT":
        errors.append("STRATEGY_INSTANCE_RESEARCH_SNAPSHOT_BINDING")
    if runtime_binding.get("global_trailing_config_mutation_for_individual_strategy_forbidden") is not True:
        errors.append("INDIVIDUAL_TRAILING_GLOBAL_MUTATION_FORBIDDEN")
    if runtime_binding.get("individual_trailing_binding_required_before_local_shadow") is not True:
        errors.append("INDIVIDUAL_TRAILING_BINDING_REQUIRED")
    if abs(float(runtime_binding.get("individual_trailing_fallback_start_pct") or 0.0)-3.0)>1e-12:
        errors.append("INDIVIDUAL_TRAILING_FALLBACK_START")
    if abs(float(runtime_binding.get("individual_trailing_fallback_callback_pct") or 0.0)-1.5)>1e-12:
        errors.append("INDIVIDUAL_TRAILING_FALLBACK_CALLBACK")
    if runtime_binding.get("trailing_dynamic_adjustment_forbidden") is not True:
        errors.append("INDIVIDUAL_TRAILING_DYNAMIC_FORBIDDEN")
    if runtime_binding.get("individual_reentry_binding_required_before_local_shadow") is not True:
        errors.append("INDIVIDUAL_REENTRY_BINDING_REQUIRED")
    reentry_mapping = runtime_binding.get("reentry_runtime_mapping") or {}
    baseline_mapping = reentry_mapping.get("CURRENT_STATE_NEXT_1H") or {}
    if baseline_mapping.get("runtime_mode") != "CURRENT_STATE":
        errors.append("INDIVIDUAL_REENTRY_RUNTIME_MODE")
    if baseline_mapping.get("next_check_tf") != "1H":
        errors.append("INDIVIDUAL_REENTRY_NEXT_CHECK_TF")
    if baseline_mapping.get("state_reentry_enabled") is not True:
        errors.append("INDIVIDUAL_REENTRY_BASELINE_ENABLED")
    legacy_mapping = reentry_mapping.get("COOLDOWN_BARS_LEGACY_COMPATIBILITY_ONLY") or {}
    if legacy_mapping.get("new_research_adoption_forbidden") is not True:
        errors.append("INDIVIDUAL_REENTRY_LEGACY_COOLDOWN_ADOPTION")
    if legacy_mapping.get("existing_strategy_instance_compatibility_preserved") is not True:
        errors.append("INDIVIDUAL_REENTRY_LEGACY_COMPATIBILITY")
    if runtime_binding.get("open_leg_policy_snapshot_immutable") is not True:
        errors.append("OPEN_LEG_POLICY_SNAPSHOT_IMMUTABLE")
    if runtime_binding.get("runner_py_modification_required") is not False:
        errors.append("RUNNER_MODIFICATION_NOT_REQUIRED")
    if runtime_binding.get("nonportable_research_wrapper_deployment_forbidden") is not True:
        errors.append("NONPORTABLE_RESEARCH_WRAPPER_DEPLOYMENT")

    local_shadow_contract = trailing_contract.get("shadow_contract") or {}
    if local_shadow_contract.get("effective_gate") != "LOCAL_SHADOW":
        errors.append("LOCAL_SHADOW_EFFECTIVE_GATE")
    if local_shadow_contract.get("local_test_compatibility_state_only") is not True:
        errors.append("LOCAL_TEST_COMPATIBILITY_ONLY")
    if abs(float(local_shadow_contract.get("canary_capital_weight") or 0.0)-0.20)>1e-12:
        errors.append("LOCAL_SHADOW_CANARY_WEIGHT")
    required_shadow_checks = set(local_shadow_contract.get("required_checks") or [])
    for required_shadow_check in (
        "EXACT_STRATEGY_INSTANCE_BINDING",
        "INDIVIDUAL_TRAILING_SNAPSHOT_BINDING",
        "INDIVIDUAL_REENTRY_SNAPSHOT_BINDING",
        "ORDER_QUANTITY_CALCULATION",
        "DUPLICATE_OR_MISSING_ACTION_INTEGRITY",
        "PORTFOLIO_ALLOCATION_GATE_INTEGRATION",
    ):
        if required_shadow_check not in required_shadow_checks:
            errors.append("LOCAL_SHADOW_REQUIRED_CHECK_" + required_shadow_check)

    live_profile = (obj.get("strategy_capital_ranking_and_allocation") or {}).get("live_strategy_profile") or {}
    if live_profile.get("identity_unit") != "STRATEGY_INSTANCE":
        errors.append("LIVE_PROFILE_IDENTITY_UNIT")
    if live_profile.get("classification_method") != "RELATIVE_LIVE_PROFILE_WITH_HYSTERESIS_V1":
        errors.append("LIVE_PROFILE_CLASSIFICATION_METHOD")
    if live_profile.get("complete_r4_lifecycle_only_for_profile_metrics") is not True:
        errors.append("LIVE_PROFILE_COMPLETE_LIFECYCLE_ONLY")
    if live_profile.get("legacy_adoption_excluded_from_profile_metrics") is not True:
        errors.append("LIVE_PROFILE_LEGACY_ADOPTION_EXCLUSION")
    if int(live_profile.get("minimum_eligible_peer_profiles_for_relative_classification") or 0) != 3:
        errors.append("LIVE_PROFILE_MIN_PEER_POOL")
    if abs(float(live_profile.get("growth_defensive_score_margin") or 0.0)-0.15)>1e-12:
        errors.append("LIVE_PROFILE_CLASS_MARGIN")
    if live_profile.get("fill_quality_slippage_missing_policy") != "NO_FABRICATION_NULL":
        errors.append("LIVE_PROFILE_EXECUTION_NO_FABRICATION")
    if int(live_profile.get("official_reclassification_min_interval_days") or 0) != 7:
        errors.append("LIVE_PROFILE_WEEKLY_REVIEW")
    if int(live_profile.get("reclassification_confirmation_reviews") or 0) != 2:
        errors.append("LIVE_PROFILE_TWO_REVIEW_HYSTERESIS")
    if live_profile.get("profile_never_overrides_hard_safety_gate") is not True:
        errors.append("LIVE_PROFILE_SAFETY_OVERRIDE_FORBIDDEN")
    if live_profile.get("canary_profile_ranking_bias") is not False:
        errors.append("LIVE_PROFILE_CANARY_NEUTRAL")
    if live_profile.get("small_normal_profile_ranking_bias") is not True:
        errors.append("LIVE_PROFILE_SMALL_NORMAL_BIAS")
    if live_profile.get("profile_changes_strategy_logic") is not False:
        errors.append("LIVE_PROFILE_STRATEGY_MUTATION_FORBIDDEN")
    if live_profile.get("profile_changes_exchange_leverage") is not False:
        errors.append("LIVE_PROFILE_LEVERAGE_MUTATION_FORBIDDEN")


    adaptive_boost = (obj.get("strategy_capital_ranking_and_allocation") or {}).get("adaptive_capital_boost_v1") or {}
    if adaptive_boost.get("policy_id") != "SUB_100M_AGGRESSIVE_GROWTH_ADAPTIVE_CAPITAL_BOOST_V1":
        errors.append("ADAPTIVE_CAPITAL_BOOST_POLICY")
    if adaptive_boost.get("enabled") is not True:
        errors.append("ADAPTIVE_CAPITAL_BOOST_ENABLED")
    proxy = adaptive_boost.get("sub_100m_operational_proxy") or {}
    if proxy.get("mode_required") != "GROWTH":
        errors.append("ADAPTIVE_CAPITAL_BOOST_MODE")
    if abs(float(proxy.get("avg_equity_usdt_lt") or 0.0)-70000.0)>1e-12:
        errors.append("ADAPTIVE_CAPITAL_BOOST_GROWTH_BOUNDARY")
    if proxy.get("fx_dependency_added") is not False:
        errors.append("ADAPTIVE_CAPITAL_BOOST_FX_DEPENDENCY")
    levels = adaptive_boost.get("tier_multiplier_levels") or {}
    expected_levels = {
        "CANARY": (1.30,1.40,1.50),
        "SMALL": (1.60,1.80,2.00),
        "NORMAL": (2.00,2.25,2.50),
    }
    for tier,(default,strong,hard_max) in expected_levels.items():
        row = levels.get(tier) or {}
        if abs(float(row.get("default") or 0.0)-default)>1e-12:
            errors.append("ADAPTIVE_CAPITAL_BOOST_"+tier+"_DEFAULT")
        if abs(float(row.get("strong") or 0.0)-strong)>1e-12:
            errors.append("ADAPTIVE_CAPITAL_BOOST_"+tier+"_STRONG")
        if abs(float(row.get("hard_max") or 0.0)-hard_max)>1e-12:
            errors.append("ADAPTIVE_CAPITAL_BOOST_"+tier+"_MAX")
    if adaptive_boost.get("exchange_leverage_changed") is not False:
        errors.append("ADAPTIVE_CAPITAL_BOOST_LEVERAGE_MUTATION")
    if adaptive_boost.get("capital_boost_applies_to_strategy_capital_weight_not_exchange_leverage") is not True:
        errors.append("ADAPTIVE_CAPITAL_BOOST_SCOPE")
    if adaptive_boost.get("existing_rollback_and_demotion_has_priority") is not True:
        errors.append("ADAPTIVE_CAPITAL_BOOST_DEMOTION_PRIORITY")
    if adaptive_boost.get("open_positions_retroactive_resize_forbidden") is not True:
        errors.append("ADAPTIVE_CAPITAL_BOOST_OPEN_POSITION_IMMUTABLE")


    stage13 = obj.get("reentry_and_common_leverage_alignment_stage13_v1") or {}
    if stage13.get("policy_id") != "REENTRY_AND_COMMON_LEVERAGE_ALIGNMENT_STAGE13_V1":
        errors.append("STAGE13_POLICY")
    if stage13.get("reentry_baseline") != "CURRENT_STATE_NEXT_1H":
        errors.append("STAGE13_REENTRY_BASELINE")
    if stage13.get("reentry_no_improvement") != "PRESERVE_BASELINE":
        errors.append("STAGE13_REENTRY_FALLBACK")
    if stage13.get("reentry_method_research") is not True:
        errors.append("STAGE13_REENTRY_METHOD_RESEARCH")
    if stage13.get("nonbaseline_proxy_auto_deploy_forbidden") is not True:
        errors.append("STAGE13_REENTRY_PROXY_DEPLOY")
    if int(stage13.get("all_live_timeframes_common_exchange_leverage_x") or 0) != 10:
        errors.append("STAGE13_COMMON_LEVERAGE")
    if list(stage13.get("live_timeframes") or []) != ["30M","1H","6H","12H","1D","1W"]:
        errors.append("STAGE13_COMMON_LEVERAGE_TFS")
    if stage13.get("existing_open_positions_retroactive_change_forbidden") is not True:
        errors.append("STAGE13_OPEN_POSITION_IMMUTABLE")

    reentry_contract = trailing_contract.get("reentry_search_contract") or {}
    if (reentry_contract.get("baseline") or {}).get("mode") != "CURRENT_STATE_NEXT_1H":
        errors.append("REENTRY_BASELINE_CURRENT_STATE_NEXT_1H")
    if reentry_contract.get("fallback") != "CURRENT_STATE_NEXT_1H":
        errors.append("REENTRY_FALLBACK_CURRENT_STATE_NEXT_1H")
    if reentry_contract.get("proxy_research_may_measure_but_may_not_auto_deploy") is not True:
        errors.append("REENTRY_PROXY_AUTO_DEPLOY_FORBIDDEN")
    if reentry_contract.get("selection_rule") != "EXACT_RUNTIME_EQUIVALENT_REPLAY_REQUIRED_BEFORE_NONBASELINE_ADOPTION":
        errors.append("REENTRY_EXACT_REPLAY_REQUIRED")

    leverage_contract = trailing_contract.get("leverage_contract") or {}
    if int(leverage_contract.get("common_exchange_leverage_x") or 0) != 10:
        errors.append("COMMON_EXCHANGE_LEVERAGE_10X")
    if list(leverage_contract.get("eligible_live_timeframes") or []) != ["30M","1H","6H","12H","1D","1W"]:
        errors.append("COMMON_EXCHANGE_LEVERAGE_TFS")
    if leverage_contract.get("timeframe_specific_live_exchange_leverage_forbidden") is not True:
        errors.append("TIMEFRAME_SPECIFIC_LIVE_LEVERAGE_FORBIDDEN")
    if leverage_contract.get("strategy_specific_exchange_leverage_forbidden") is not True:
        errors.append("STRATEGY_SPECIFIC_LIVE_LEVERAGE_FORBIDDEN")
    if leverage_contract.get("strategy_research_optimization") is not False:
        errors.append("LEVERAGE_RESEARCH_FORBIDDEN")
    if leverage_contract.get("capital_multiplier_is_separate_from_exchange_leverage") is not True:
        errors.append("CAPITAL_MULTIPLIER_LEVERAGE_SEPARATION")
    if leverage_contract.get("portfolio_and_risk_gates_remain_authoritative") is not True:
        errors.append("COMMON_LEVERAGE_RISK_GATE_AUTHORITY")

    if leverage_contract.get("runtime_common_policy_source") != "R5A_LEVERAGE_FROZEN_STANDARD_V2":
        errors.append("COMMON_LEVERAGE_RUNTIME_SOURCE")
    if int(leverage_contract.get("runtime_common_policy_target_x") or 0) != 10:
        errors.append("COMMON_LEVERAGE_RUNTIME_TARGET")
    nominal=leverage_contract.get("nominal_unconstrained_gross_equity_multiple_hard_ceiling") or {}
    if nominal != {"CANARY":3.0,"SMALL":10.0,"NORMAL":25.0}:
        errors.append("COMMON_LEVERAGE_TIER_NOMINAL_CEILINGS")
    if leverage_contract.get("nominal_ceiling_is_not_guaranteed_allocation") is not True:
        errors.append("COMMON_LEVERAGE_NOMINAL_NOT_GUARANTEED")
    if leverage_contract.get("portfolio_and_risk_gates_may_reduce_below_nominal_ceiling") is not True:
        errors.append("COMMON_LEVERAGE_GATE_REDUCTION")

    final_alignment = obj.get("final_constitution_runtime_alignment_stage12_v1") or {}
    if final_alignment.get("policy_id") != "FINAL_CONSTITUTION_RUNTIME_ALIGNMENT_STAGE12_V1":
        errors.append("FINAL_RUNTIME_ALIGNMENT_POLICY")
    if final_alignment.get("growth_common_safety_bypass_forbidden") is not True:
        errors.append("FINAL_RUNTIME_ALIGNMENT_GROWTH_SAFETY")
    expected_constraints = [
        "entry_risk_safe_size_pass",
        "account_risk_budget_pass",
        "portfolio_total_exposure_pass",
        "available_margin_pass",
        "margin_safety_reserve_pass",
        "symbol_concentration_pass",
        "strategy_concentration_pass",
        "correlation_concentration_pass",
        "cost_liquidity_scale_pass",
    ]
    if list(final_alignment.get("required_portfolio_constraints") or []) != expected_constraints:
        errors.append("FINAL_RUNTIME_ALIGNMENT_PORTFOLIO_CONSTRAINTS")
    if final_alignment.get("runtime_new_entry_consumes_strategy_instance_capital_weight") is not True:
        errors.append("FINAL_RUNTIME_ALIGNMENT_CAPITAL_WEIGHT")
    if final_alignment.get("malformed_adaptive_metadata_fail_closed") is not True:
        errors.append("FINAL_RUNTIME_ALIGNMENT_ADAPTIVE_FAIL_CLOSED")
    if abs(float(final_alignment.get("canary_base_weight") or 0.0)-0.20)>1e-12:
        errors.append("FINAL_RUNTIME_ALIGNMENT_CANARY_WEIGHT")
    if final_alignment.get("legacy_canary_one_percent_residue_forbidden") is not True:
        errors.append("FINAL_RUNTIME_ALIGNMENT_CANARY_RESIDUE")
    if final_alignment.get("open_position_retroactive_resize_forbidden") is not True:
        errors.append("FINAL_RUNTIME_ALIGNMENT_OPEN_POSITION")
    if final_alignment.get("exchange_leverage_changed") is not False:
        errors.append("FINAL_RUNTIME_ALIGNMENT_LEVERAGE")


    final_stage14 = obj.get(
        "final_constitution_full_alignment_stage14_v1"
    ) or {}

    if final_stage14.get(
        "policy_id"
    ) != "FINAL_CONSTITUTION_FULL_ALIGNMENT_STAGE14_V1":
        errors.append(
            "FINAL_STAGE14_POLICY"
        )

    if list(
        final_stage14.get(
            "trade_timeframes"
        )
        or []
    ) != [
        "30M",
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
    ]:
        errors.append(
            "FINAL_STAGE14_TRADE_TFS"
        )

    if list(
        final_stage14.get(
            "reference_only_timeframes"
        )
        or []
    ) != ["1M"]:
        errors.append(
            "FINAL_STAGE14_REFERENCE_TF"
        )

    if (
        final_stage14.get(
            "new_strategy_timeframe_distribution_percent"
        )
        or {}
    ) != {
        "30M": 20,
        "1H": 23,
        "6H": 23,
        "12H": 18,
        "1D": 10,
        "1W": 6,
    }:
        errors.append(
            "FINAL_STAGE14_TF_DISTRIBUTION"
        )

    if (
        final_stage14.get(
            "live_profile_initial_requirements"
        )
        or {}
    ).get(
        "1W"
    ) != {
        "closed_trades": 10,
        "days": 730,
    }:
        errors.append(
            "FINAL_STAGE14_1W_LIVE_PROFILE"
        )

    if int(
        final_stage14.get(
            "common_exchange_leverage_x"
        )
        or 0
    ) != 10:
        errors.append(
            "FINAL_STAGE14_COMMON_LEVERAGE"
        )

    if final_stage14.get(
        "reentry_baseline"
    ) != "CURRENT_STATE_NEXT_1H":
        errors.append(
            "FINAL_STAGE14_REENTRY"
        )

    if final_stage14.get(
        "open_position_retroactive_change_forbidden"
    ) is not True:
        errors.append(
            "FINAL_STAGE14_OPEN_POSITION_IMMUTABLE"
        )


    auto_manager = trailing_contract.get("auto_research_runtime_manager") or {}
    if auto_manager.get("policy_id") != "AUTO_RESEARCH_MANAGER_FINAL_ALIGNMENT_V1":
        errors.append("AUTO_RESEARCH_MANAGER_POLICY")
    if int(auto_manager.get("logical_workers") or 0) != 1:
        errors.append("AUTO_RESEARCH_MANAGER_LOGICAL_WORKERS")
    if auto_manager.get("server_push_hold_required") is not True:
        errors.append("AUTO_RESEARCH_MANAGER_PUSH_HOLD")
    if auto_manager.get("runner_dry_run_required") is not True:
        errors.append("AUTO_RESEARCH_MANAGER_DRY_RUN")
    if auto_manager.get("dry_run_required_before_start") is not True:
        errors.append("AUTO_RESEARCH_MANAGER_PREFLIGHT")
    if auto_manager.get("one_week_new_research_excluded") is not False:
        errors.append("AUTO_RESEARCH_MANAGER_1W_MUST_BE_INCLUDED")
    if list(auto_manager.get("new_strategy_timeframes") or []) != ["30M","1H","6H","12H","1D","1W"]:
        errors.append("AUTO_RESEARCH_MANAGER_TIMEFRAMES")
    if auto_manager.get("new_strategy_hard_pass_window_start") != "2016-01-01":
        errors.append("AUTO_RESEARCH_MANAGER_HARD_PASS_START")
    if auto_manager.get("new_strategy_hard_pass_window_end") != "2024-08-20":
        errors.append("AUTO_RESEARCH_MANAGER_HARD_PASS_END")

    redev = trailing_contract.get("redevelopment_contract") or {}
    if redev.get("entry_risk_redevelopment_method") != "BOUNDED_PRIOR_SIGNAL_CONFIRMATION_1_2_3_BARS":
        errors.append("REDEVELOPMENT_ENTRY_METHOD")
    if redev.get("final_risk_redevelopment_method") != "BOUNDED_CONSERVATIVE_MEASURED_TRAILING_ALTERNATIVE":
        errors.append("REDEVELOPMENT_FINAL_RISK_METHOD")
    if redev.get("full_validation_redevelopment_method") != "BOUNDED_PRIOR_SIGNAL_CONFIRMATION_1_2_3_BARS":
        errors.append("REDEVELOPMENT_FULL_VALIDATION_METHOD")
    return {
        "ok": not errors,
        "schema": obj.get("schema"),
        "constitution_version": obj.get("constitution_version"),
        "chapter_clause_count": len(clause_ids),
        "governance_clause_count": len(gov_ids),
        "tracks": tracks,
        "constitution_hash": stable_hash(obj),
        "errors": errors,
    }


# ============================================================
# Stable Trading Kernel Guard
# ============================================================


def protected_files() -> list[Path]:
    obj = constitution()
    names = ((obj.get("stable_trading_kernel") or {}).get("protected_files") or [])
    return [ROOT / str(name) for name in names]


def kernel_hashes() -> dict[str, str | None]:
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): file_hash(path)
        for path in protected_files()
    }


def capture_kernel_guard() -> dict[str, Any]:
    return {
        "schema": "B_BOT_V3_KERNEL_GUARD_V1",
        "captured_at": now(),
        "constitution_hash": validate_constitution().get("constitution_hash"),
        "hashes": kernel_hashes(),
    }


def assert_kernel_unchanged(before: dict[str, Any]) -> None:
    after = kernel_hashes()
    changed = []
    for rel, old in (before.get("hashes") or {}).items():
        if after.get(rel) != old:
            changed.append(rel)
    if changed:
        raise RuntimeError("RESEARCH_MUTATED_STABLE_KERNEL=" + ",".join(changed))


def research_write_allowed(path: Path) -> bool:
    path = Path(path).resolve()
    allowed_roots = [
        (ROOT / "strategy_lab" / "results" / "v3").resolve(),
        (ROOT / "strategy_lab" / "deploy_ready").resolve(),
    ]
    return any(path == base or base in path.parents for base in allowed_roots)


# ============================================================
# Strategy / Policy Version Registry
# ============================================================



def version_registry() -> dict[str, Any]:
    obj = load(VERSION_REGISTRY, {})
    if obj.get("schema") not in {"B_BOT_V3_VERSION_REGISTRY_V1", "B_BOT_R4_VERSION_REGISTRY_V1"}:
        obj = {
            "schema": "B_BOT_R4_VERSION_REGISTRY_V1",
            "updated_at": now(),
            "strategy_versions": {},
            "policy_versions": {},
            "strategy_instances": {},
            "active_bundles": {},
            "history": [],
        }
        atomic(VERSION_REGISTRY, obj)
        return obj
    changed = False
    if obj.get("schema") != "B_BOT_R4_VERSION_REGISTRY_V1":
        obj["schema"] = "B_BOT_R4_VERSION_REGISTRY_V1"
        changed = True
    for key, default in (("strategy_versions", {}), ("policy_versions", {}), ("strategy_instances", {}), ("active_bundles", {}), ("history", [])):
        if key not in obj or not isinstance(obj.get(key), type(default)):
            obj[key] = default
            changed = True
    if changed:
        obj["updated_at"] = now()
        atomic(VERSION_REGISTRY, obj)
    return obj



def _next_version_id(kind: str, track: str, payload: dict[str, Any]) -> str:
    digest = stable_hash({"kind": kind, "track": track, "payload": payload})[:12]
    prefix = "SV" if kind == "STRATEGY" else "PV"
    return f"{prefix}_{track}_{digest}"


def register_version_candidate(
    kind: str,
    track: str,
    payload: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    exact_strategy_version: str = "",
) -> dict[str, Any]:
    kind = str(kind).upper()
    track = str(track).upper()
    if track not in EXPECTED_TRACKS:
        raise RuntimeError("VERSION_TRACK_INVALID")
    if kind not in {"STRATEGY", "POLICY"}:
        raise RuntimeError("VERSION_KIND_INVALID")
    if kind == "STRATEGY" and track != "STRATEGY":
        raise RuntimeError("STRATEGY_VERSION_TRACK_MISMATCH")
    if kind == "POLICY" and track == "STRATEGY":
        raise RuntimeError("POLICY_VERSION_TRACK_MISMATCH")

    # 연구 버전은 기존 파이프라인의 최종 통과 증거와 원본 파일에 고정한다.
    exact_strategy_version = str(exact_strategy_version or "").strip()
    if exact_strategy_version:
        if kind != "STRATEGY" or track != "STRATEGY":
            raise RuntimeError("EXACT_VERSION_REQUIRES_STRATEGY")
        pipeline_path = V3 / "historical_314_pipeline_stage2.json"
        pipeline = json.loads(pipeline_path.read_text(encoding="utf-8-sig"))
        matches = [r for r in pipeline.get("entries", []) if isinstance(r, dict)
                   and r.get("exact_strategy_version") == exact_strategy_version]
        if len(matches) != 1:
            raise RuntimeError("EXACT_VERSION_PIPELINE_IDENTITY_INVALID")
        target = matches[0]
        if target.get("eligible") is not True or target.get("pipeline_stage") != "LOCAL_SHADOW_PENDING":
            raise RuntimeError("EXACT_VERSION_FINAL_GATES_REQUIRED")
        if (target.get("full_validation_gate") or {}).get("pass") is not True:
            raise RuntimeError("EXACT_VERSION_FULL_VALIDATION_REQUIRED")
        blind = target.get("sealed_final_blind_gate") or {}
        if blind.get("pass") is not True or blind.get("next_stage") != "LOCAL_SHADOW_PENDING":
            raise RuntimeError("EXACT_VERSION_BLIND_REQUIRED")
        for key in ("exact_strategy_version", "strategy_id", "timeframe", "canonical_source_file",
                    "source_sha256", "full_validation_gate", "sealed_final_blind_gate",
                    "individual_trailing", "individual_reentry"):
            if (evidence or {}).get(key) != target.get(key):
                raise RuntimeError("EXACT_VERSION_EVIDENCE_MISMATCH:" + key)
        source_rel = str(target.get("canonical_source_file") or "").replace(chr(92), "/")
        source = (ROOT / source_rel).resolve()
        source.relative_to(ROOT.resolve())
        expected = str(target.get("source_sha256") or "").lower()
        if len(expected) != 64 or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RuntimeError("EXACT_VERSION_SOURCE_HASH_MISMATCH")
        bindings = {"strategy": str(target.get("strategy_id") or "").upper(),
                    "timeframe": str(target.get("timeframe") or "").upper(),
                    "source_file": source_rel, "source_sha256": expected}
        for key, value in bindings.items():
            if payload.get(key) != value:
                raise RuntimeError("EXACT_VERSION_PAYLOAD_MISMATCH:" + key)

    reg = version_registry()
    version_id = exact_strategy_version or _next_version_id(kind, track, payload)
    bucket = "strategy_versions" if kind == "STRATEGY" else "policy_versions"
    existing = (reg.get(bucket) or {}).get(version_id)
    if isinstance(existing, dict):
        if exact_strategy_version:
            for key, value in bindings.items():
                if (existing.get("payload") or {}).get(key) != value:
                    raise RuntimeError("EXACT_VERSION_IMMUTABLE_MISMATCH:" + key)
        return existing

    row = {
        "version_id": version_id,
        "kind": kind,
        "track": track,
        "status": "RESEARCH_CANDIDATE",
        "created_at": now(),
        "payload": payload,
        "evidence": evidence or {},
        "core_mutation": False,
        "promotion_path": ["RESEARCH", "VALIDATION", "LOCAL_TEST", "LOCAL_SHADOW", "CANARY", "SMALL", "NORMAL"],
        "rollback_target": "PREVIOUS_VALIDATED_VERSION",
    }
    reg.setdefault(bucket, {})[version_id] = row
    reg.setdefault("history", []).append(
        {"event": "REGISTER_CANDIDATE", "version_id": version_id, "at": now()}
    )
    reg["history"] = reg["history"][-2000:]
    reg["updated_at"] = now()
    atomic(VERSION_REGISTRY, reg)
    return row




def strategy_policy_bundle(policy_versions: dict[str, Any], behavior_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """전략 행동에 영향을 주는 정책 스냅샷과 불변 bundle version을 만든다."""
    payload = {
        "policy_versions": policy_versions if isinstance(policy_versions, dict) else {},
        "behavior_policy": behavior_policy if isinstance(behavior_policy, dict) else {},
    }
    bundle_version = "PB_" + stable_hash(payload)[:32]
    return {"version": bundle_version, "snapshot": payload}


def canonical_strategy_instance_id(strategy_id: str, timeframe: str, exact_version: str, policy_bundle_version: str) -> str:
    parts = [str(strategy_id).upper().strip(), str(timeframe).upper().strip(), str(exact_version).strip(), str(policy_bundle_version).strip()]
    if not all(parts):
        raise RuntimeError("STRATEGY_INSTANCE_IDENTITY_INCOMPLETE")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return "SI_" + digest


def register_strategy_instance(
    strategy_id: str,
    timeframe: str,
    exact_version: str,
    policy_bundle_version: str,
    policy_snapshot: dict[str, Any],
    source_file: str = "",
    evidence: dict[str, Any] | None = None,
    status: str = "LOCAL_TEST",
) -> dict[str, Any]:
    """검증된 Strategy Version을 R4 Strategy Instance로 불변 등록한다."""
    status = str(status or "LOCAL_TEST").upper()
    if status != "LOCAL_TEST":
        raise RuntimeError("STRATEGY_INSTANCE_INITIAL_STATUS_MUST_BE_LOCAL_TEST")

    strategy_id = str(strategy_id or "").upper().strip()
    timeframe = str(timeframe or "").upper().strip()
    exact_version = str(exact_version or "").strip()
    policy_bundle_version = str(policy_bundle_version or "").strip()
    source_file = str(source_file or "").replace("\\", "/")

    if not all((strategy_id, timeframe, exact_version, policy_bundle_version)):
        raise RuntimeError("STRATEGY_INSTANCE_IDENTITY_INCOMPLETE")

    if not isinstance(policy_snapshot, dict):
        raise RuntimeError("STRATEGY_INSTANCE_POLICY_SNAPSHOT_INVALID")

    expected_bundle = strategy_policy_bundle(
        policy_snapshot.get("policy_versions") or {},
        policy_snapshot.get("behavior_policy") or {},
    )
    if expected_bundle["version"] != policy_bundle_version:
        raise RuntimeError("STRATEGY_INSTANCE_POLICY_BUNDLE_HASH_MISMATCH")

    reg = version_registry()
    sv = (reg.get("strategy_versions") or {}).get(exact_version)
    if not isinstance(sv, dict):
        raise RuntimeError("STRATEGY_INSTANCE_EXACT_VERSION_NOT_FOUND")
    if str(sv.get("status") or "").upper() not in {
        "VALIDATED", "PROVISIONAL", "CANARY", "SMALL", "NORMAL"
    }:
        raise RuntimeError("STRATEGY_INSTANCE_REQUIRES_VALIDATED_EXACT_VERSION")

    payload = sv.get("payload") if isinstance(sv.get("payload"), dict) else {}
    if str(payload.get("strategy") or "").upper().strip() != strategy_id:
        raise RuntimeError("STRATEGY_INSTANCE_STRATEGY_VERSION_MISMATCH")
    if str(payload.get("timeframe") or "").upper().strip() != timeframe:
        raise RuntimeError("STRATEGY_INSTANCE_TIMEFRAME_VERSION_MISMATCH")

    version_source = str(payload.get("source_file") or "").replace("\\", "/")
    if source_file and version_source and source_file != version_source:
        raise RuntimeError("STRATEGY_INSTANCE_SOURCE_VERSION_MISMATCH")
    if not source_file:
        source_file = version_source

    instance_id = canonical_strategy_instance_id(
        strategy_id,
        timeframe,
        exact_version,
        policy_bundle_version,
    )
    current = (reg.get("strategy_instances") or {}).get(instance_id)
    immutable = {
        "strategy_id": strategy_id,
        "timeframe": timeframe,
        "exact_strategy_version": exact_version,
        "strategy_policy_bundle_version": policy_bundle_version,
        "policy_snapshot": policy_snapshot,
        "source_file": source_file,
    }

    if isinstance(current, dict):
        for key, value in immutable.items():
            if current.get(key) != value:
                raise RuntimeError("STRATEGY_INSTANCE_IMMUTABLE_IDENTITY_MISMATCH:" + key)
        return current

    created_at = now()
    row = {
        "strategy_instance_id": instance_id,
        **immutable,
        "status": "LOCAL_TEST",
        "created_at": created_at,
        "updated_at": created_at,
        "canary_authorized": False,
        "approved_for_server_release": False,
        "evidence": evidence or {},
        "career": [
            {
                "event": "LOCAL_TEST",
                "at": created_at,
                "evidence": evidence or {},
            }
        ],
    }
    reg.setdefault("strategy_instances", {})[instance_id] = row
    reg.setdefault("history", []).append(
        {
            "event": "REGISTER_STRATEGY_INSTANCE",
            "strategy_instance_id": instance_id,
            "status": "LOCAL_TEST",
            "at": created_at,
        }
    )
    reg["history"] = reg["history"][-2000:]
    reg["updated_at"] = created_at
    atomic(VERSION_REGISTRY, reg)
    return row



def set_strategy_instance_status(
    instance_id: str,
    status: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """로컬 권한으로 LOCAL_TEST→LOCAL_SHADOW→CANARY까지만 전이한다."""
    instance_id = str(instance_id or "").strip()
    status = str(status or "").upper()
    reg = version_registry()
    row = (reg.get("strategy_instances") or {}).get(instance_id)
    if not isinstance(row, dict):
        raise RuntimeError("STRATEGY_INSTANCE_NOT_FOUND")

    current = str(row.get("status") or "LOCAL_TEST").upper()
    if current not in R4_INSTANCE_TRANSITIONS or status not in R4_INSTANCE_TRANSITIONS:
        raise RuntimeError("STRATEGY_INSTANCE_STATUS_INVALID")

    if status == current:
        return row

    if current in {"CANARY", "SMALL", "NORMAL"}:
        raise RuntimeError("SERVER_TIER_WRITER_REQUIRED_AFTER_CANARY")

    if status not in R4_INSTANCE_TRANSITIONS[current]:
        raise RuntimeError(f"STRATEGY_INSTANCE_TRANSITION_FORBIDDEN={current}->{status}")

    if current == "LOCAL_TEST" and status != "LOCAL_SHADOW":
        raise RuntimeError("STRATEGY_INSTANCE_LOCAL_TEST_MUST_ENTER_LOCAL_SHADOW")
    if current == "LOCAL_SHADOW" and status != "CANARY":
        raise RuntimeError("STRATEGY_INSTANCE_LOCAL_SHADOW_MUST_ENTER_CANARY")

    updated_at = now()
    row["status"] = status
    row["updated_at"] = updated_at
    row["canary_authorized"] = status == "CANARY"
    row["approved_for_server_release"] = status == "CANARY"
    row.setdefault("career", []).append(
        {
            "event": status,
            "at": updated_at,
            "evidence": evidence or {},
        }
    )
    row["career"] = row["career"][-500:]
    reg.setdefault("history", []).append(
        {
            "event": "STRATEGY_INSTANCE_STATUS",
            "strategy_instance_id": instance_id,
            "from": current,
            "status": status,
            "at": updated_at,
        }
    )
    reg["history"] = reg["history"][-2000:]
    reg["updated_at"] = updated_at
    atomic(VERSION_REGISTRY, reg)
    return row



def strategy_instance(instance_id: str) -> dict[str, Any]:
    row = (version_registry().get("strategy_instances") or {}).get(str(instance_id))
    if not isinstance(row, dict):
        raise RuntimeError("STRATEGY_INSTANCE_NOT_FOUND")
    return row

def set_version_status(version_id: str, status: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed = set(VERSION_TRANSITIONS)
    status = str(status).upper()
    if status not in allowed:
        raise RuntimeError("VERSION_STATUS_INVALID")

    reg = version_registry()
    row = None
    for bucket in ("strategy_versions", "policy_versions"):
        if version_id in (reg.get(bucket) or {}):
            row = reg[bucket][version_id]
            break
    if not isinstance(row, dict):
        raise RuntimeError("VERSION_NOT_FOUND")

    current = str(row.get("status") or "RESEARCH_CANDIDATE").upper()
    if current not in VERSION_TRANSITIONS:
        raise RuntimeError("VERSION_CURRENT_STATUS_INVALID")
    if status != current and status not in VERSION_TRANSITIONS[current]:
        raise RuntimeError(f"VERSION_TRANSITION_FORBIDDEN={current}->{status}")

    row["status"] = status
    row["updated_at"] = now()
    if evidence:
        row.setdefault("evidence_history", []).append(evidence)
        row["evidence_history"] = row["evidence_history"][-200:]
    reg.setdefault("history", []).append(
        {"event": "STATUS", "version_id": version_id, "from": current, "status": status, "at": now()}
    )
    reg["history"] = reg["history"][-2000:]
    reg["updated_at"] = now()
    atomic(VERSION_REGISTRY, reg)
    return row


# ============================================================
# Roadmap-first Change Control
# ============================================================


def write_change_control_state(
    verifier_ok: bool,
    implementation_ok: bool,
    self_verify_ok: bool,
    overfit_ok: bool = False,
    logic_ok: bool = False,
) -> dict[str, Any]:
    cv = validate_constitution()
    stages = {
        "ROADMAP_CONSTITUTION": bool(cv.get("ok")),
        "VERIFIER": bool(verifier_ok),
        "IMPLEMENTATION": bool(implementation_ok),
        "SELF_VERIFICATION": bool(self_verify_ok),
    }
    ordered_pass = True
    for stage in EXPECTED_CHANGE_ORDER:
        ordered_pass = ordered_pass and stages[stage]
        if not ordered_pass:
            break

    release_validation_gates = {
        "OVERFIT": bool(overfit_ok),
        "LOGIC": bool(logic_ok),
    }
    release_gates_pass = all(release_validation_gates.values())
    complete = ordered_pass and all(stages.values()) and release_gates_pass
    hold = (ROOT / "SERVER_PUSH_HOLD.flag").exists()
    obj = {
        "schema": "B_BOT_V3_CHANGE_CONTROL_V3",
        "updated_at": now(),
        "constitution_version": cv.get("constitution_version"),
        "constitution_hash": cv.get("constitution_hash"),
        "required_order": EXPECTED_CHANGE_ORDER,
        "stages": stages,
        "ordered_pass": ordered_pass,
        "release_validation_gates": release_validation_gates,
        "release_validation_pass": release_gates_pass,
        "change_control_complete": complete,
        "server_push_hold": hold,
        "server_sync_authorized": False if hold else bool(complete),
        "release_note": "LOCAL_VERIFICATION_COMPLETE_SERVER_STILL_HELD" if hold and complete else "RELEASE_GATES_INCOMPLETE" if not complete else "EXPLICIT_RELEASE_PATH_REQUIRED",
    }
    atomic(CHANGE_CONTROL, obj)
    return obj


# ================================================================================================
# STRATEGY_PASSPORT_MINIMAL_V1
#
# Stored inside existing version_registry.json.
# No separate Passport / comparison / history files.
# ================================================================================================

STRATEGY_PASSPORT_CAREER_EVENTS = {
    "RESEARCH",
    "LOCAL_TEST",
    "LOCAL_SHADOW",
    "CANARY",
    "SMALL",
    "NORMAL",
    "REVALIDATION",
    "DEMOTED",
    "QUARANTINED",
    "RETIRED",
}


def _passport_missing(value: Any) -> bool:
    # IMPORTANT:
    # Do not use `value in {None, ""}` because value may be list/dict.
    return value is None or value == ""


def ensure_strategy_passport(
    version_id: str,
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:

    version_id = str(version_id or "").strip()

    if not version_id.startswith("SV_"):
        raise RuntimeError("STRATEGY_VERSION_ID_INVALID")

    payload = payload if isinstance(payload, dict) else {}

    reg = version_registry()
    versions = reg.setdefault("strategy_versions", {})
    row = versions.get(version_id)

    if row is None:
        row = {
            "version_id": version_id,
            "kind": "STRATEGY",
            "track": "STRATEGY",
            "status": "RESEARCH_CANDIDATE",
            "created_at": now(),
            "payload": dict(payload),
            "evidence": dict(evidence or {}),
            "evidence_history": [],
            "core_mutation": False,
            "promotion_path": [
                "RESEARCH",
                "LOCAL_TEST",
                "LOCAL_SHADOW",
                "CANARY",
                "SMALL",
                "NORMAL",
            ],
            "rollback_target": "PREVIOUS_VALIDATED_VERSION",
        }

        versions[version_id] = row

        reg.setdefault("history", []).append({
            "event": "REGISTER_EXISTING_EXACT_STRATEGY_VERSION",
            "version_id": version_id,
            "at": now(),
        })

    if not isinstance(row, dict):
        raise RuntimeError("STRATEGY_VERSION_ROW_INVALID")

    if str(row.get("kind") or "STRATEGY").upper() != "STRATEGY":
        raise RuntimeError("STRATEGY_VERSION_KIND_CONFLICT")

    if str(row.get("track") or "STRATEGY").upper() != "STRATEGY":
        raise RuntimeError("STRATEGY_VERSION_TRACK_CONFLICT")

    existing_payload = row.setdefault("payload", {})

    if isinstance(existing_payload, dict):
        for key, value in payload.items():
            existing_payload.setdefault(key, value)

    passport = row.setdefault("passport", {})

    passport.setdefault(
        "schema",
        "B_BOT_V3_STRATEGY_PASSPORT_V1",
    )

    passport.setdefault("version_id", version_id)

    passport.setdefault(
        "identity",
        {
            "strategy_version": version_id,
            "created_at": row.get("created_at"),
            "timeframe": payload.get("timeframe"),
            "strategy": payload.get("strategy"),
            "strategies": payload.get("strategies"),
            "parent_version": payload.get("parent_version"),
            "code_hash": payload.get("code_hash"),
        },
    )

    identity = passport["identity"]

    if not isinstance(identity, dict):
        raise RuntimeError("STRATEGY_PASSPORT_IDENTITY_INVALID")

    for key in (
        "timeframe",
        "strategy",
        "strategies",
        "parent_version",
        "code_hash",
    ):
        current = identity.get(key)
        incoming = payload.get(key)

        if (
            _passport_missing(current)
            and not _passport_missing(incoming)
        ):
            identity[key] = incoming

    passport.setdefault(
        "qualification_path",
        [
            "LOCAL_TEST",
            "LOCAL_SHADOW",
            "CANARY",
            "SMALL",
            "NORMAL",
        ],
    )

    passport.setdefault("career_history", [])

    passport.setdefault(
        "current_summary",
        {
            "current_qualification": "RESEARCH",
            "last_event": None,
            "last_event_at": None,
        },
    )

    passport.setdefault(
        "evidence_contract",
        {
            "raw_checkpoint_authoritative": True,
            "historical_backfill_forbidden": True,
            "exact_strategy_version_required": True,
        },
    )

    row["updated_at"] = now()

    reg["history"] = (reg.get("history") or [])[-2000:]
    reg["updated_at"] = now()

    atomic(VERSION_REGISTRY, reg)

    return row


def record_strategy_career_event(
    version_id: str,
    event: str,
    detail: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:

    version_id = str(version_id or "").strip()
    event = str(event or "").upper()

    if event not in STRATEGY_PASSPORT_CAREER_EVENTS:
        raise RuntimeError(
            "STRATEGY_CAREER_EVENT_INVALID=" + event
        )

    ensure_strategy_passport(version_id)

    reg = version_registry()

    row = (
        reg.get("strategy_versions")
        or {}
    ).get(version_id)

    if not isinstance(row, dict):
        raise RuntimeError("STRATEGY_VERSION_NOT_FOUND")

    passport = row.setdefault("passport", {})
    history = passport.setdefault("career_history", [])

    # Idempotent exact event binding.
    if event_id:
        for existing in history:
            if (
                isinstance(existing, dict)
                and existing.get("event_id") == event_id
            ):
                return row

    observed_at = now()

    history.append({
        "event_id": event_id,
        "event": event,
        "at": observed_at,
        "detail": detail if isinstance(detail, dict) else {},
    })

    summary = passport.setdefault(
        "current_summary",
        {},
    )

    summary["last_event"] = event
    summary["last_event_at"] = observed_at

    if event in {
        "LOCAL_TEST",
        "LOCAL_SHADOW",
        "CANARY",
        "SMALL",
        "NORMAL",
        "REVALIDATION",
        "QUARANTINED",
        "RETIRED",
    }:
        summary["current_qualification"] = event

    elif event == "DEMOTED":
        summary["current_qualification"] = str(
            (detail or {}).get("target_qualification")
            or "DEMOTED"
        ).upper()

    row["updated_at"] = now()

    reg.setdefault("history", []).append({
        "event": "STRATEGY_CAREER",
        "career_event": event,
        "version_id": version_id,
        "event_id": event_id,
        "at": observed_at,
    })

    # Global index remains bounded.
    # Exact strategy career_history itself is permanent.
    reg["history"] = reg["history"][-2000:]
    reg["updated_at"] = now()

    atomic(VERSION_REGISTRY, reg)

    return row


def update_strategy_passport_summary(
    version_id: str,
    summary_patch: dict[str, Any],
) -> dict[str, Any]:

    if not isinstance(summary_patch, dict):
        raise RuntimeError("STRATEGY_SUMMARY_PATCH_INVALID")

    ensure_strategy_passport(version_id)

    reg = version_registry()

    row = (
        reg.get("strategy_versions")
        or {}
    ).get(version_id)

    if not isinstance(row, dict):
        raise RuntimeError("STRATEGY_VERSION_NOT_FOUND")

    passport = row.setdefault("passport", {})
    summary = passport.setdefault("current_summary", {})

    for key, value in summary_patch.items():
        summary[key] = value

    row["updated_at"] = now()
    reg["updated_at"] = now()

    atomic(VERSION_REGISTRY, reg)

    return row


def strategy_passport(
    version_id: str,
) -> dict[str, Any]:

    reg = version_registry()

    row = (
        reg.get("strategy_versions")
        or {}
    ).get(version_id)

    if not isinstance(row, dict):
        return {}

    passport = row.get("passport")

    return passport if isinstance(passport, dict) else {}


def strategy_passport_comparison(
    timeframe: str | None = None,
) -> list[dict[str, Any]]:

    tf_filter = str(timeframe or "").upper()
    reg = version_registry()

    output = []

    for version_id, row in (
        reg.get("strategy_versions")
        or {}
    ).items():

        if not isinstance(row, dict):
            continue

        passport = row.get("passport") or {}
        identity = passport.get("identity") or {}
        summary = passport.get("current_summary") or {}
        payload = row.get("payload") or {}

        tf = str(
            identity.get("timeframe")
            or payload.get("timeframe")
            or ""
        ).upper()

        if tf_filter and tf != tf_filter:
            continue

        output.append({
            "strategy_version": version_id,
            "timeframe": tf,
            "strategy": (
                identity.get("strategy")
                or payload.get("strategy")
            ),
            "registry_status": row.get("status"),
            "current_qualification": (
                summary.get("current_qualification")
            ),
            "last_event": summary.get("last_event"),
            "career_event_count": len(
                passport.get("career_history")
                or []
            ),
            "current_summary": dict(summary),
        })

    output.sort(
        key=lambda row: (
            row.get("timeframe") or "",
            row.get("strategy_version") or "",
        )
    )

    return output

