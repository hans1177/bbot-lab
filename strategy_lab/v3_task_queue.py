# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_task_queue.py
"""V3 9-Track bounded Roadmap queue.

각 Track은 동일 입력 중복 실행을 막고, Question/Hypothesis/Search Space/
Trial Budget/Validation/Stop Condition 계약을 반드시 포함한다.
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

import v3_governance as governance

# ============================================================
# 경로 / 상수
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
RES = LAB / "results"
V3 = RES / "v3"
QUEUE = V3 / "roadmap_queue.json"
CONFIG = ROOT / "strategy_config.json"
RUNTIME_DB = ROOT / "data" / "server_runtime" / "history.db"
RUNTIME_STATE = ROOT / "data" / "server_runtime" / "state"

TRACKS = list(governance.EXPECTED_TRACKS)
LIFECYCLE_SEQUENCE = [
    "STRATEGY",
    "ENTRY_RISK",
    "EXIT",
    "REENTRY",
    "EXECUTION",
    "SYMBOL",
    "PORTFOLIO",
    "REGIME",
    "DIRECTION",
]
STRUCTURE_TRACKS = {"STRATEGY", "DIRECTION"}

# New 3-layer Constitution. The legacy 9 tracks remain as
# compatibility/implementation axes only.
RESEARCH_POLICY_ID = "RA_MAIN_MIDDLE_DETAIL_20260830_V1"

TRACK_LAYER = {
    "STRATEGY": "MAIN",
    "DIRECTION": "MAIN",
    "ENTRY_RISK": "MIDDLE",
    "EXIT": "MIDDLE",
    "REENTRY": "MIDDLE",
    "SYMBOL": "DETAIL",
    "PORTFOLIO": "DETAIL",
    "EXECUTION": "DETAIL",
    "REGIME": "GUARDRAIL",
}

ALLOCATED_LAYERS = (
    "MAIN",
    "MIDDLE",
    "DETAIL",
)

# Relative wall-clock priors only. These are scheduler costs,
# never strategy performance scores.
EXPECTED_WALL_CLOCK = {
    "STRATEGY": 5.00,
    "DIRECTION": 1.00,
    "ENTRY_RISK": 1.50,
    "EXIT": 1.20,
    "REENTRY": 1.00,
    "SYMBOL": 1.10,
    "PORTFOLIO": 1.20,
    "REGIME": 0.80,
    "EXECUTION": 1.00,
}

QUESTIONS = {
    "DIRECTION": "LONG/SHORT 비대칭에서 독립적이고 반복 가능한 방향 edge가 존재하는가?",
    "STRATEGY": "현재 MAIN보다 독립적이고 강건한 ENTRY SIGNAL Strategy Version 후보가 존재하는가?",
    "ENTRY_RISK": "진입시점 예측 stop/MAE/adverse/safe leverage가 실현 경로와 일치하는가?",
    "EXIT": "청산 방식·Trailing·Exit timing을 바꾸면 비용 후 기대기여와 giveback이 개선되는가?",
    "REENTRY": "재진입 timing/조건이 whipsaw 비용을 낮추면서 재획득 edge를 보존하는가?",
    "SYMBOL": "Strategy×TF×Symbol별 기대기여와 자본시간 우선순위가 실현 결과와 일치하는가?",
    "PORTFOLIO": "중복·집중·margin reserve 제약이 계좌 기여도와 생존성을 개선하는가?",
    "REGIME": "시장 레짐에 따라 Strategy/Policy Version의 유효성 차이가 반복적으로 존재하는가?",
    "EXECUTION": "수수료·슬리피지·펀딩·체결 품질 및 REAL/Backtest 괴리가 기대값을 훼손하는가?",
}

BASE_SCORES = {
    "DIRECTION": (0.55, 0.50, 1.25, 1.00),
    "STRATEGY": (1.00, 0.50, 1.75, 5.00),
    "ENTRY_RISK": (0.80, 0.50, 1.10, 1.50),
    "EXIT": (0.85, 0.50, 1.20, 1.00),
    "REENTRY": (0.65, 0.50, 1.20, 1.00),
    "SYMBOL": (0.75, 0.50, 1.15, 1.00),
    "PORTFOLIO": (0.80, 0.50, 1.10, 1.00),
    "REGIME": (0.60, 0.50, 1.25, 1.20),
    "EXECUTION": (0.70, 0.50, 1.05, 1.00),
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


def _stat_token(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
        return (str(path), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return (str(path), -1, -1)


def research_architecture() -> dict[str, Any]:
    arch = (
        governance.constitution()
        .get("research_architecture_v3")
        or {}
    )

    if arch.get("policy_id") != RESEARCH_POLICY_ID:
        raise RuntimeError(
            "RESEARCH_ARCHITECTURE_POLICY_INVALID"
        )

    return arch


def research_scheduler() -> dict[str, Any]:
    scheduler = (
        research_architecture()
        .get("research_scheduler")
        or {}
    )

    if scheduler.get("status") != "FINAL_CONFIRMED":
        raise RuntimeError(
            "RESEARCH_SCHEDULER_INVALID"
        )

    return scheduler


def _find_equity_value(obj: Any):
    target = (
        "24h_average_futures_equity_krw"
    )

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() == target:
                try:
                    return float(value)
                except Exception:
                    return None

        for value in obj.values():
            found = _find_equity_value(value)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = _find_equity_value(value)
            if found is not None:
                return found

    return None


def _resolve_equity_krw():
    raw = os.getenv(
        "BBOT_24H_AVERAGE_FUTURES_EQUITY_KRW"
    )

    if raw not in (None, ""):
        try:
            return (
                float(raw),
                "ENV_BBOT_24H_AVERAGE_FUTURES_EQUITY_KRW",
            )
        except Exception:
            pass

    candidates = [
        CONFIG,
        RUNTIME_STATE / "account_state.json",
        RUNTIME_STATE / "account_summary.json",
        RUNTIME_STATE / "equity_state.json",
    ]

    for path in candidates:
        obj = load(path, {})
        value = _find_equity_value(obj)

        if value is not None:
            return (
                value,
                str(path),
            )

    # Exact-key discovery only. Generic equity fields are
    # intentionally not guessed.
    try:
        for path in sorted(
            RUNTIME_STATE.glob("*.json")
        ):
            obj = load(path, {})
            value = _find_equity_value(obj)

            if value is not None:
                return (
                    value,
                    str(path),
                )
    except Exception:
        pass

    return (
        None,
        "UNAVAILABLE_DEFAULT_FIRST_CONSTITUTION_TIER",
    )


def resource_allocation(
    equity_krw: float | None = None,
) -> dict[str, Any]:

    arch = research_architecture()

    resource_allocation = (
        arch.get("resource_allocation")
        or {}
    )

    tiers = (
        resource_allocation.get("tiers")
        or []
    )

    if not tiers:
        raise RuntimeError(
            "RESEARCH_RESOURCE_TIERS_MISSING"
        )

    source = "EXPLICIT_ARGUMENT"

    if equity_krw is None:
        equity_krw, source = (
            _resolve_equity_krw()
        )

    if equity_krw is None:
        index = 0
    else:
        value = float(equity_krw)

        thresholds = (
            100_000_000,
            300_000_000,
            1_000_000_000,
            3_000_000_000,
            10_000_000_000,
        )

        index = sum(
            value >= threshold
            for threshold in thresholds
        )

    index = min(
        index,
        len(tiers) - 1,
    )

    row = tiers[index] or {}

    weights = {
        layer: float(row.get(layer) or 0.0)
        for layer in ALLOCATED_LAYERS
    }

    total = sum(weights.values())

    if abs(total - 1.0) > 1e-9:
        raise RuntimeError(
            "RESEARCH_RESOURCE_WEIGHT_SUM_INVALID"
        )

    return {
        "basis":
            "24H_AVERAGE_FUTURES_EQUITY_KRW",
        "equity_krw":
            equity_krw,
        "equity_source":
            source,
        "tier_index":
            index,
        "MAIN":
            weights["MAIN"],
        "MIDDLE":
            weights["MIDDLE"],
        "DETAIL":
            weights["DETAIL"],
    }


def _layer_counts(q: dict[str, Any]) -> dict[str, int]:

    counts = {
        layer: 0
        for layer in ALLOCATED_LAYERS
    }

    for item in q.get("history") or []:
        track = str(
            item.get("track")
            or ""
        ).upper()

        layer = TRACK_LAYER.get(track)

        if layer in counts:
            counts[layer] += 1

    return counts


def input_signature(track: str) -> str:
    track = str(track).upper()
    paths = [
        CONFIG,
        V3 / "research_knowledge.json",
        V3 / "trial_ledger.json",
        V3 / "deployment_state.json",
        RUNTIME_DB,
        RUNTIME_STATE / "position_cache.json",
        RUNTIME_STATE / "major_symbols.json",
        RUNTIME_STATE / "v3_blocked_additional_candidates.json",
        RES / "candidate_registry.json",
    ]
    runtime_feedback = load(
        V3 / "runtime_feedback.json",
        {},
    )
    payload = {
        "track": track,
        "files": [_stat_token(p) for p in paths],
        "runtime_feedback_digest": str(
            runtime_feedback.get("evidence_digest")
            or ""
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ============================================================
# Phase / Task Contract
# ============================================================


def phase() -> str:
    cfg = load(CONFIG, {})
    frames = cfg.get("frames") or {}
    main_tfs = ["6H", "1H", "1D", "1W"]
    if any(not bool((frames.get(tf) or {}).get("enabled")) for tf in main_tfs):
        return "MAIN_EMPTY"
    tiers = {str((frames.get(tf) or {}).get("deployment_tier") or "").upper() for tf in main_tfs + ["12H"]}
    if tiers & {"CANARY", "SMALL"}:
        return "PROVISIONAL"
    return "VALIDATED"


def task_contract(track: str) -> dict[str, Any]:
    track = str(track).upper()

    track_meta = (
        governance.constitution()
        .get("tracks")
        or {}
    ).get(track) or {}

    arch = research_architecture()
    scheduler = research_scheduler()
    allocation = resource_allocation()

    layer = TRACK_LAYER.get(
        track,
        "GUARDRAIL",
    )

    return {
        "research_question":
            QUESTIONS[track],

        "hypothesis":
            "측정 가능하고 반복 가능한 개선 여지가 있을 수 있으나 발견만으로 LIVE/Core를 변경하지 않는다.",

        "architecture_layer":
            layer,

        "research_architecture_policy_id":
            arch.get("policy_id"),

        "allowed_search_space": {
            "track":
                track,
            "architecture_layer":
                layer,
            "scope":
                track_meta.get("scope"),
            "bounded":
                True,
            "search_until_winner":
                False,
            "live_policy_mutation":
                False,
            "core_mutation":
                False,
            "version_target":
                track_meta.get("version_target"),
        },

        "trial_budget": {
            "current_task":
                1,
            "rerun_same_input":
                False,
        },

        "research_scheduler": {
            "schema":
                scheduler.get("schema"),
            "status":
                scheduler.get("status"),
            "cheap_first":
                (
                    scheduler
                    .get("cheap_first_policy")
                    or {}
                ).get("enabled") is True,
            "early_stop":
                (
                    scheduler
                    .get("early_stop")
                    or {}
                ).get("enabled") is True,
            "bounded_compute":
                (
                    scheduler
                    .get("bounded_compute")
                    or {}
                ).get("required") is True,
            "resource_allocation":
                allocation,
        },

        "validation_plan": [
            "기존 evidence 무결성 확인",
            "CHEAP_PREFILTER",
            "LIGHT_SCREEN",
            "FULL_VALIDATION",
            "새 MAIN만 SEALED_BLIND_IF_NEW_MAIN",
            "MINIMAL_SHADOW_RUNTIME_GATE",
            "CANARY_REAL",
            "열화 또는 hard fail 시 이전 검증 버전 rollback",
        ],

        "stop_condition": [
            "task budget consumed",
            "no new input",
            "hard safety fail",
            "evidence integrity failure",
            "stable kernel hash changed",
            "duplicate input signature",
            "dominated candidate with no material novelty",
        ],
    }



# ============================================================
# Queue State
# ============================================================


def ensure() -> dict[str, Any]:
    q = load(QUEUE, {})
    if q.get("schema") != "B_BOT_V3_PARALLEL_TRACK_QUEUE_V2":
        q = {
            "schema": "B_BOT_V3_PARALLEL_TRACK_QUEUE_V2",
            "updated_at": now(),
            "phase": phase(),
            "scheduler_cycle": 0,
            "detail_cursor": 0,
            "tracks": {},
            "history": [],
        }
    q["phase"] = phase()
    for track in TRACKS:
        row = q.setdefault("tracks", {}).setdefault(
            track,
            {
                "status": "READY",
                "runs": 0,
                "successes": 0,
                "failures": 0,
                "no_new_input": 0,
                "last_task": None,
                "last_input_signature": None,
            },
        )
        row["task_contract"] = task_contract(track)
    q["dynamic_scores"] = dynamic_scores(q)
    q["updated_at"] = now()
    atomic(QUEUE, q)
    return q


def mix_guide(p: str) -> tuple[float, float]:
    """Legacy compatibility only; no longer drives scheduling."""
    if p == "MAIN_EMPTY":
        return (0.80, 0.20)
    if p == "PROVISIONAL":
        return (0.40, 0.60)
    return (0.20, 0.80)


def dynamic_scores(q: dict[str, Any]) -> dict[str, Any]:
    # Research scheduler score only.
    # Never substitute this for expectancy/MDD/WF/OOS/etc.
    scheduler = research_scheduler()
    runtime_feedback = load(
        V3 / "runtime_feedback.json",
        {},
    )
    priority_hints = (
        runtime_feedback.get("priority_hints")
        if isinstance(runtime_feedback, dict)
        else {}
    )
    priority_hints = priority_hints if isinstance(priority_hints, dict) else {}

    out = {}

    for track in TRACKS:

        (
            base_improvement,
            base_success,
            overfit,
            compute,
        ) = BASE_SCORES[track]

        row = (
            (q.get("tracks") or {})
            .get(track)
            or {}
        )

        runs = int(
            row.get("runs")
            or 0
        )

        successes = int(
            row.get("successes")
            or 0
        )

        deficiency = (
            1.0
            / (
                1.0
                + 0.10 * runs
            )
        )

        if runs > 0:
            observed_success = (
                successes + 1.0
            ) / (
                runs + 2.0
            )

            success_probability = (
                0.50 * base_success
                + 0.50 * observed_success
            )
        else:
            success_probability = (
                base_success
            )

        expected_improvement = (
            float(base_improvement)
        )

        measured_wall_clock = (
            row.get(
                "avg_wall_clock_seconds"
            )
        )

        try:
            measured_wall_clock = float(
                measured_wall_clock
            )
        except Exception:
            measured_wall_clock = 0.0

        if measured_wall_clock > 0.0:
            expected_wall_clock_time = max(
                measured_wall_clock / 60.0,
                0.10,
            )
            wall_clock_source = (
                "MEASURED_AVG_SECONDS"
            )
        else:
            expected_wall_clock_time = float(
                EXPECTED_WALL_CLOCK[track]
            )
            wall_clock_source = (
                "CONSERVATIVE_TRACK_PRIOR"
            )

        denominator = max(
            float(overfit)
            * float(compute)
            * expected_wall_clock_time,
            1e-12,
        )

        base_score = (
            expected_improvement
            * deficiency
            * success_probability
            / denominator
        )

        hint = priority_hints.get(track) or {}
        drift_multiplier = 1.0
        drift_reasons = []
        if isinstance(hint, dict) and hint.get("raise_priority") is True:
            try:
                reason_count = max(0, int(hint.get("reason_count") or 0))
            except Exception:
                reason_count = 0
            try:
                max_multiplier = float(hint.get("max_scheduler_multiplier") or 2.0)
            except Exception:
                max_multiplier = 2.0
            max_multiplier = min(max(max_multiplier, 1.0), 2.0)
            drift_multiplier = min(
                max_multiplier,
                1.0 + 0.25 * min(reason_count, 4),
            )
            drift_reasons = [
                str(x)
                for x in (hint.get("reasons") or [])[-16:]
            ]

        score = base_score * drift_multiplier

        out[track] = {
            "architecture_layer":
                TRACK_LAYER.get(
                    track,
                    "GUARDRAIL",
                ),

            "expected_improvement":
                expected_improvement,

            "expected_account_return_improvement_proxy":
                expected_improvement,

            "evidence_deficiency":
                deficiency,

            "success_probability":
                success_probability,

            "overfit_risk":
                float(overfit),

            "compute_cost":
                float(compute),

            "expected_wall_clock_time":
                expected_wall_clock_time,

            "wall_clock_source":
                wall_clock_source,

            "priority_model":
                (
                    scheduler
                    .get("priority_model")
                    or {}
                ).get("conceptual_formula"),

            "live_drift_priority_multiplier":
                drift_multiplier,

            "live_drift_priority_reasons":
                drift_reasons,

            "live_feedback_direct_policy_effect":
                False,

            "score":
                score,
        }

    return out



def select_next(
    exclude: set[str] | None = None,
) -> dict[str, Any] | None:

    q = ensure()

    exclude = {
        str(x).upper()
        for x in (exclude or set())
    }

    p = (
        q.get("phase")
        or "MAIN_EMPTY"
    )

    allocation = resource_allocation()

    weights = {
        layer: float(
            allocation.get(layer)
            or 0.0
        )
        for layer in ALLOCATED_LAYERS
    }

    counts = _layer_counts(q)

    scores = dynamic_scores(q)

    candidates = []

    for track in LIFECYCLE_SEQUENCE:

        if track in exclude:
            continue

        sig = input_signature(track)

        row = (
            (q.get("tracks") or {})
            .get(track)
            or {}
        )

        # Exact same input does not run twice.
        if (
            row.get("last_input_signature")
            == sig
        ):
            continue

        layer = TRACK_LAYER.get(
            track,
            "GUARDRAIL",
        )

        priority = float(
            (
                scores.get(track)
                or {}
            )
            .get("score")
            or 0.0
        )

        candidates.append(
            {
                "track":
                    track,
                "signature":
                    sig,
                "layer":
                    layer,
                "priority":
                    priority,
                "runs":
                    int(
                        row.get("runs")
                        or 0
                    ),
            }
        )

    if not candidates:
        return None

    allocated_candidates = [
        row
        for row in candidates
        if (
            row["layer"]
            in ALLOCATED_LAYERS
            and weights.get(
                row["layer"],
                0.0,
            ) > 0.0
        )
    ]

    if allocated_candidates:

        available_layers = sorted(
            {
                row["layer"]
                for row in allocated_candidates
            },
            key=lambda x:
                ALLOCATED_LAYERS.index(x),
        )

        total_count = sum(
            counts.values()
        )

        def deficit(layer: str) -> float:
            # Deficit scheduler converges toward the
            # Constitution allocation without fixed serial order.
            target_after_next = (
                weights[layer]
                * (total_count + 1)
            )

            return (
                target_after_next
                - counts[layer]
            )

        selected_layer = max(
            available_layers,
            key=lambda layer: (
                deficit(layer),
                weights[layer],
                -ALLOCATED_LAYERS.index(
                    layer
                ),
            ),
        )

        pool = [
            row
            for row in allocated_candidates
            if row["layer"] == selected_layer
        ]

    else:
        # REGIME is diagnostic/guardrail by default and
        # consumes no MAIN/MIDDLE/DETAIL allocation.
        selected_layer = "GUARDRAIL"

        pool = [
            row
            for row in candidates
            if row["layer"] == "GUARDRAIL"
        ]

        if not pool:
            pool = candidates

    # Highest expected research value per compute/time first.
    # Runs are only a deterministic secondary tie-break.
    chosen = sorted(
        pool,
        key=lambda row: (
            -row["priority"],
            row["runs"],
            LIFECYCLE_SEQUENCE.index(
                row["track"]
            ),
        ),
    )[0]

    track = chosen["track"]
    sig = chosen["signature"]

    legacy_structure_ratio, legacy_detail_ratio = (
        mix_guide(p)
    )

    return {
        "schema":
            "B_BOT_V3_BOUNDED_TASK_V1",

        "task_id":
            f"{track}_{sig[:12]}",

        "track":
            track,

        "architecture_layer":
            chosen["layer"],

        "phase":
            p,

        "input_signature":
            sig,

        "resource_allocation":
            allocation,

        "scheduler_priority":
            chosen["priority"],

        "research_scheduler": {
            "policy_id":
                RESEARCH_POLICY_ID,
            "mode":
                "MAIN_MIDDLE_DETAIL_DEFICIT_PLUS_VALUE_PRIORITY",
            "layer_counts":
                counts,
            "selected_layer":
                selected_layer,
        },

        # Preserved only for old readers. It no longer
        # drives selection.
        "mix_guide": {
            "legacy_compatibility_only":
                True,
            "structure":
                legacy_structure_ratio,
            "detail":
                legacy_detail_ratio,
        },

        "contract":
            task_contract(track),

        "created_at":
            now(),
    }



def record(
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:

    q = ensure()

    track = str(
        task.get("track")
        or ""
    ).upper()

    if track not in TRACKS:
        raise RuntimeError(
            "ROADMAP_TRACK_INVALID"
        )

    row = q["tracks"][track]

    status = str(
        result.get("status")
        or result.get("decision")
        or "UNKNOWN"
    ).upper()

    row["runs"] = int(
        row.get("runs")
        or 0
    ) + 1

    if status in {
        "PASS",
        "UPDATED",
        "CANDIDATE",
        "VALIDATED",
        "MEASURED",
    }:
        row["successes"] = int(
            row.get("successes")
            or 0
        ) + 1

    elif status == "NO_NEW_INPUT":
        row["no_new_input"] = int(
            row.get("no_new_input")
            or 0
        ) + 1

    else:
        row["failures"] = int(
            row.get("failures")
            or 0
        ) + 1

    elapsed = (
        result.get("wall_clock_seconds")
        or result.get("elapsed_seconds")
    )

    try:
        elapsed = float(elapsed)
    except Exception:
        elapsed = 0.0

    if elapsed > 0.0:
        previous = float(
            row.get(
                "avg_wall_clock_seconds"
            )
            or 0.0
        )

        previous_n = max(
            int(row["runs"]) - 1,
            0,
        )

        row["avg_wall_clock_seconds"] = (
            (
                previous * previous_n
                + elapsed
            )
            / max(
                previous_n + 1,
                1,
            )
        )

    row["last_input_signature"] = (
        task.get("input_signature")
    )

    row["last_task"] = {
        "task_id":
            task.get("task_id"),
        "architecture_layer":
            TRACK_LAYER.get(
                track,
                "GUARDRAIL",
            ),
        "scheduler_priority":
            task.get(
                "scheduler_priority"
            ),
        "at":
            now(),
        "status":
            status,
        "result":
            result,
    }

    q["scheduler_cycle"] = int(
        q.get("scheduler_cycle")
        or 0
    ) + 1

    q.setdefault(
        "history",
        [],
    ).append(
        {
            "task_id":
                task.get("task_id"),
            "track":
                track,
            "architecture_layer":
                TRACK_LAYER.get(
                    track,
                    "GUARDRAIL",
                ),
            "status":
                status,
            "at":
                now(),
        }
    )

    q["history"] = (
        q["history"][-2000:]
    )

    arch = research_architecture()

    q["research_architecture_v3"] = {
        "policy_id":
            arch.get("policy_id"),
        "MAIN":
            (arch.get("MAIN") or {})
            .get("stages"),
        "MIDDLE":
            (arch.get("MIDDLE") or {})
            .get("stages"),
        "DETAIL":
            (arch.get("DETAIL") or {})
            .get("stages"),
    }

    q["research_scheduler"] = (
        research_scheduler()
    )

    q["resource_allocation"] = (
        resource_allocation()
    )

    q["dynamic_scores"] = (
        dynamic_scores(q)
    )

    q["updated_at"] = now()

    atomic(
        QUEUE,
        q,
    )

    return q





def auto_research_allocation_contract() -> dict[str,Any]:
    return ((governance.constitution().get("individual_strategy_trailing_optimization") or {}).get("auto_research_allocation_contract") or {})


def _auto_manager_state(q):
    mgr=q.setdefault("auto_research_manager",{})
    mgr.setdefault("schema","B_BOT_AUTO_RESEARCH_MANAGER_STAGE10_V1")
    mgr.setdefault("modes",{})
    mgr.setdefault("history",[])
    return mgr


def _auto_mode_row(q,mode):
    mgr=_auto_manager_state(q); row=mgr.setdefault("modes",{}).setdefault(mode,{"bucket_seconds":{},"bucket_runs":{},"updated_at":None})
    return row


def auto_research_weights(backlog_mode: bool) -> dict[str,float]:
    contract=auto_research_allocation_contract()
    section=contract.get("historical_314_backlog_mode" if backlog_mode else "normal_mode") or {}
    raw=section.get("allocation_percent") or {}
    total=sum(float(x) for x in raw.values())
    if abs(total-100.0)>1e-9: raise RuntimeError("AUTO_RESEARCH_WEIGHT_SUM_INVALID")
    return {str(k):float(v)/100.0 for k,v in raw.items()}


def preview_auto_research_bucket(availability: dict[str,bool], backlog_mode: bool, q=None):
    q=q if isinstance(q,dict) else load(QUEUE,{})
    mode="HISTORICAL_314_BACKLOG" if backlog_mode else "NORMAL"
    weights=auto_research_weights(backlog_mode); row=((q.get("auto_research_manager") or {}).get("modes") or {}).get(mode) or {}
    spent={k:float((row.get("bucket_seconds") or {}).get(k) or 0.0) for k in weights}
    candidates=[k for k,w in weights.items() if w>0 and bool(availability.get(k))]
    if not candidates:return None
    total=sum(spent.values()); hypothetical=max(1.0,(total/max(sum((row.get("bucket_runs") or {}).values()) or 1,1)))
    contract=auto_research_allocation_contract(); trail_cap=float(((contract.get("trailing") or {}).get("cpu_share_max") or 0.30)); redev_cap=float(((contract.get("redevelopment") or {}).get("resource_share_max") or 0.10))
    def capped(bucket):
        if len(candidates)<=1:return False
        projected_total=total+hypothetical
        projected=(spent.get(bucket,0.0)+hypothetical)/max(projected_total,1e-12)
        if bucket in {"INDIVIDUAL_TRAILING","TRAILING_AND_REENTRY"} and projected>trail_cap:return True
        if bucket=="TARGETED_REDEVELOPMENT" and projected>redev_cap:return True
        return False
    uncapped=[b for b in candidates if not capped(b)] or candidates
    chosen=max(uncapped,key=lambda b:(weights[b]*(total+hypothetical)-spent.get(b,0.0),weights[b],-list(weights).index(b)))
    return {"mode":mode,"bucket":chosen,"weights":weights,"spent_seconds":spent,"available":{k:bool(availability.get(k)) for k in weights}}


def record_auto_research_bucket(selection: dict[str,Any], elapsed_seconds: float, result: dict[str,Any]):
    q=ensure(); mode=str(selection.get("mode") or ""); bucket=str(selection.get("bucket") or ""); row=_auto_mode_row(q,mode)
    charged=max(0.001,float(elapsed_seconds or 0.0)); secs=row.setdefault("bucket_seconds",{}); runs=row.setdefault("bucket_runs",{})
    secs[bucket]=float(secs.get(bucket) or 0.0)+charged; runs[bucket]=int(runs.get(bucket) or 0)+1; row["updated_at"]=now()
    mgr=_auto_manager_state(q); mgr["last_selection"]={"at":now(),"mode":mode,"bucket":bucket,"elapsed_seconds":charged,"result_status":str((result or {}).get("status") or (result or {}).get("action") or "")}
    mgr.setdefault("history",[]).append(dict(mgr["last_selection"])); mgr["history"]=mgr["history"][-1000:]; mgr["updated_at"]=now(); q["updated_at"]=now(); atomic(QUEUE,q); return mgr["last_selection"]


def auto_research_manager_status():
    q=load(QUEUE,{})
    mgr=(q.get("auto_research_manager") or {}) if isinstance(q,dict) else {}
    return {"schema":mgr.get("schema") or "B_BOT_AUTO_RESEARCH_MANAGER_STAGE10_V1","modes":mgr.get("modes") or {},"last_selection":mgr.get("last_selection"),"policy":auto_research_allocation_contract(),"single_logical_worker":True}

def status() -> dict[str, Any]:
    """Read-only queue view. It never advances scheduler state."""

    q = load(
        QUEUE,
        {},
    )

    if q.get("schema") != (
        "B_BOT_V3_PARALLEL_TRACK_QUEUE_V2"
    ):
        q = {
            "schema":
                "B_BOT_V3_PARALLEL_TRACK_QUEUE_V2",
            "updated_at":
                None,
            "phase":
                phase(),
            "scheduler_cycle":
                0,
            "detail_cursor":
                0,
            "tracks":
                {},
            "history":
                [],
        }

    q["phase"] = phase()

    for track in TRACKS:

        row = (
            q.setdefault(
                "tracks",
                {},
            )
            .setdefault(
                track,
                {
                    "status":
                        "READY",
                    "runs":
                        0,
                    "successes":
                        0,
                    "failures":
                        0,
                    "no_new_input":
                        0,
                    "last_task":
                        None,
                    "last_input_signature":
                        None,
                },
            )
        )

        row["task_contract"] = (
            task_contract(track)
        )

    arch = research_architecture()

    q["research_architecture_v3"] = {
        "policy_id":
            arch.get("policy_id"),
        "MAIN":
            (arch.get("MAIN") or {})
            .get("stages"),
        "MIDDLE":
            (arch.get("MIDDLE") or {})
            .get("stages"),
        "DETAIL":
            (arch.get("DETAIL") or {})
            .get("stages"),
    }

    q["research_scheduler"] = (
        research_scheduler()
    )

    q["resource_allocation"] = (
        resource_allocation()
    )

    q["dynamic_scores"] = (
        dynamic_scores(q)
    )

    q["auto_research_manager_status"] = auto_research_manager_status()

    return q

