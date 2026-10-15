# -*- coding: utf-8 -*-
# 파일명: strategy_lab/master_handoff.py
"""B-Bot V3 통합 MASTER HANDOFF 생성기.

기존 V2/R0-R16 역사와 machine truth를 삭제하지 않고 V3 Constitution/9-Track/
Strategy·Policy Version 상태를 추가한다. 같은 결과/실패 항목은 경로·ID 기준으로 dedupe한다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 경로 / 상수
# ============================================================
ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
RES = LAB / "results"
V3 = RES / "v3"
OUT = ROOT / "B_BOT_MASTER_HANDOFF.md"
BOT_OUT = ROOT / "BOT_MASTER_HANDOFF.md"
SYS = RES / "master_handoff_system_latest.json"
LEGACY_FROZEN = LAB / "legacy_v2" / "B_BOT_MASTER_HANDOFF_V2_FROZEN.md"

sys.path.insert(0, str(LAB))
import v3_governance as governance
import v3_task_queue as task_queue

ROADMAP = [
    ("R0", "V2 DD / Return Cause"), ("R1", "Control SL"), ("R2", "Trailing"),
    ("R3", "Reentry"), ("R4", "Opposite Exit / Reopen"), ("R5", "Leverage / Sizing"),
    ("R6", "Portfolio Exposure"), ("R6A", "Symbol Entry Priority"), ("R7", "Cross Entry Gate"),
    ("R8", "EMA_CROSS / SAR"), ("R9", "Long / Short"), ("R10", "TF / Multi-TF"),
    ("R11", "Symbol Fitness"), ("R12", "Dynamic Universe"), ("R12A", "PIT Universe"),
    ("R12B", "Symbol Entry Priority"), ("R13", "Market Regime"),
    ("R13A", "Psychology / Attention Regime"), ("R13B", "Derivatives Positioning"),
    ("R13C", "US / Rates Macro"), ("R13D", "Sentiment-Price Divergence"),
    ("R13E", "Sentiment + Positioning Symbol Priority"), ("R14", "Existing Strategies"),
    ("R15", "New Families"), ("R16", "Strategy Portfolio"),
]

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


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_json(path: Path, obj: Any) -> None:
    atomic_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def latest_results(limit: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not RES.exists():
        return rows
    paths = sorted(RES.rglob("*.json"), key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)
    for path in paths:
        if path.name.endswith(".tmp"):
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        obj = load(path, {})
        if not isinstance(obj, dict):
            continue
        rows.append({
            "path": rel,
            "schema": obj.get("schema"),
            "research_id": obj.get("research_id"),
            "status": obj.get("status") or obj.get("freeze_status"),
            "decision": obj.get("decision"),
            "winner": obj.get("winner"),
            "candidate": obj.get("candidate") or obj.get("source_candidate"),
            "next_action": obj.get("next_action"),
        })
        if len(rows) >= limit:
            break
    return rows


def freeze_registry() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not RES.exists():
        return rows
    for path in sorted(RES.rglob("*freeze*.json")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        obj = load(path, {})
        if not isinstance(obj, dict):
            continue
        rows.append({
            "path": rel,
            "research_id": obj.get("research_id"),
            "axis": obj.get("axis"),
            "status": obj.get("freeze_status") or obj.get("status"),
            "decision": obj.get("decision"),
            "winner": obj.get("winner"),
            "search_trials": obj.get("search_trials"),
            "parameter_trials": obj.get("parameter_trials"),
            "next_action": obj.get("next_action"),
        })
    return rows


def next_action(state: dict[str, Any], roadmap_status: dict[str, Any], queue: dict[str, Any]) -> str:
    result = roadmap_status.get("controller_result") if isinstance(roadmap_status, dict) else None
    if isinstance(result, dict) and result.get("action"):
        return f"V3:{result.get('action')}"
    tracks = queue.get("tracks") if isinstance(queue, dict) else {}
    ready = [k for k, v in (tracks or {}).items() if str((v or {}).get("status") or "READY").upper() in {"READY", "PENDING"}]
    if ready:
        return "V3_9_TRACK_QUEUE:" + ",".join(ready)
    return "V3_CONTROLLER_STATUS_REVIEW"


def completed_failed(knowledge: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    records = knowledge.get("records") if isinstance(knowledge, dict) else {}
    success = list(records.values()) if isinstance(records, dict) else list(records or [])
    failed = list(knowledge.get("failed_research") or []) if isinstance(knowledge, dict) else []
    def dedupe(rows: list[Any]) -> list[Any]:
        out, seen = [], set()
        for row in rows:
            key = stable_hash(row)
            if key in seen:
                continue
            seen.add(key); out.append(row)
        return out
    return dedupe(success)[-1000:], dedupe(failed)[-1000:]

# ============================================================
# Snapshot
# ============================================================
def build_snapshot() -> dict[str, Any]:
    cfg = load(ROOT / "strategy_config.json", {})
    state = load(RES / "research_state.json", {})
    registry = load(RES / "candidate_registry.json", {})
    roadmap_status = load(RES / "roadmap_status_latest.json", {})
    trials = load(V3 / "trial_ledger.json", {})
    deployment = load(V3 / "deployment_state.json", {})
    knowledge = load(V3 / "research_knowledge.json", {})
    runtime_knowledge = load(V3 / "research_knowledge_runtime.json", {})
    versions = load(V3 / "version_registry.json", {})
    change = load(V3 / "change_control_state.json", {})
    overfit_validation = load(ROOT / "V3_OVERFIT_VALIDATION_REPORT.json", {})
    logic_validation = load(ROOT / "V3_LOGIC_VALIDATION_REPORT.json", {})
    pending = load(V3 / "runtime_sync_request.json", None) if (V3 / "runtime_sync_request.json").exists() else None
    constitution = governance.validate_constitution()
    queue = task_queue.status()
    completed, failed = completed_failed(knowledge)
    chapter = state.get("chapter_controller") if isinstance(state, dict) and isinstance(state.get("chapter_controller"), dict) else {}
    legacy_search = int((registry.get("trial_counters") or {}).get("search_trials_executed") or state.get("search_trials") or 73) if isinstance(registry, dict) and isinstance(state, dict) else 73
    legacy_param = int((registry.get("trial_counters") or {}).get("parameter_trials_executed") or state.get("parameter_trials") or 73) if isinstance(registry, dict) and isinstance(state, dict) else 73
    snapshot = {
        "schema": "B_BOT_V3_MASTER_HANDOFF_SNAPSHOT_V2",
        "generated_at": now(),
        "constitution": constitution,
        "current_research": {
            "v3_next_action": next_action(state if isinstance(state, dict) else {}, roadmap_status, queue),
            "legacy_current_axis": chapter.get("current_axis"),
            "legacy_current_stage": chapter.get("current_stage"),
            "legacy_next_action_preserved_not_executed": chapter.get("next_action") or (state.get("next_action") if isinstance(state, dict) else None),
        },
        "legacy": {
            "history": ROADMAP,
            "history_scope": "R0-R16_PRESERVE",
            "trial73": "COMPLETED_DO_NOT_RERUN",
            "search_trials": max(73, legacy_search),
            "parameter_trials": max(73, legacy_param),
            "execute_legacy_next_action": False,
        },
        "roadmap_queue": queue,
        "strategy_config": cfg,
        "deployment_state": deployment,
        "version_registry": versions,
        "change_control_state": change,
        "release_validation": {"overfit": overfit_validation, "logic": logic_validation},
        "pending_runtime_sync": pending,
        "trial_ledger": trials,
        "completed_research": completed,
        "failed_research": failed,
        "freeze_registry": freeze_registry(),
        "candidate_registry": registry,
        "research_state": state,
        "research_knowledge": knowledge,
        "runtime_knowledge": runtime_knowledge,
        "result_index": latest_results(),
        "safety": {
            "server_push_hold": (ROOT / "SERVER_PUSH_HOLD.flag").exists(),
            "local_test_mode": (ROOT / "LOCAL_TEST_MODE.flag").exists(),
            "stable_kernel": "RESEARCH_MUTATION_FORBIDDEN",
            "strategy_change": "STRATEGY_VERSION_ONLY",
            "policy_change": "VALIDATED_POLICY_VERSION_ONLY",
            "promotion": "RESEARCH->VALIDATION->PROVISIONAL->CANARY->SMALL->NORMAL",
            "rollback": "PREVIOUS_VALIDATED_VERSION",
            "overfit_release_gate": "REQUIRED_PASS",
            "logic_release_gate": "REQUIRED_PASS",
        },
    }
    snapshot["snapshot_hash"] = stable_hash(snapshot)
    return snapshot

# ============================================================
# 렌더링
# ============================================================
def render(snapshot: dict[str, Any]) -> str:
    j = lambda obj: json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    legacy_text = ""
    if LEGACY_FROZEN.is_file():
        legacy_text = LEGACY_FROZEN.read_text(encoding="utf-8-sig", errors="replace")
    lines = [
        "# B-BOT MASTER HANDOFF — V3 GOVERNED RESEARCH CONTINUITY",
        "",
        f"- Generated: `{snapshot['generated_at']}`",
        f"- Snapshot hash: `{snapshot['snapshot_hash']}`",
        "- Trial73: **COMPLETED / DO_NOT_RERUN**",
        "- Legacy counters: **73/73 minimum preserved**",
        "- Legacy roadmap: **R0~R16 preserved / legacy next_action not executed**",
        "- SERVER PUSH: **HOLD until final local PASS and explicit release**",
        "- REAL ORDER during local test: **OFF**",
        "",
        "## 1. NEXT ACTION / 재시작 후 이어받기",
        "",
        f"**NEXT ACTION:** `{snapshot['current_research']['v3_next_action']}`",
        "",
        "재시작 시 이 파일, `research_state.json`, `candidate_registry.json`, `roadmap_status_latest.json`, V3 queue/version registry를 읽고 같은 input signature 연구를 중복 실행하지 않는다.",
        "",
        "## 2. Roadmap Constitution / Stable Trading Kernel",
        "",
        "```json", j(snapshot["constitution"]), "```", "",
        "- Strategy Lab은 ENTRY SIGNAL만이 아니라 ENTRY_RISK/HOLD/EXIT/REENTRY/COST/REAL↔Backtest 괴리를 정례 연구한다.",
        "- Runner/order 등 Stable Trading Kernel은 Research가 자동 수정할 수 없다.",
        "- 전략 교체는 Strategy Version, Exit/Risk/Reentry 등은 검증된 Policy Version으로만 한다.",
        "- 변경 순서: Constitution → verifier → implementation → self-verification.",
        "",
        "### Change Control / Release Validation",
        "",
        "```json", j({"change_control": snapshot["change_control_state"], "release_validation": snapshot["release_validation"]}), "```", "",
        "- OVERFIT과 LOGIC 검증은 최종 패키지 및 서버 동기화 전 필수 PASS 게이트다.",
        "",
        "## 3. 9-Track Roadmap Queue",
        "", "```json", j(snapshot["roadmap_queue"]), "```", "",
        "## 4. 현재 연구 / 완료 연구 / 실패 연구",
        "", "```json", j({"current": snapshot["current_research"], "completed": snapshot["completed_research"], "failed": snapshot["failed_research"]}), "```", "",
        "## 5. Trial / Freeze Registry",
        "", "```json", j({"legacy": snapshot["legacy"], "trial_ledger": snapshot["trial_ledger"], "freeze_registry": snapshot["freeze_registry"]}), "```", "",
        "## 6. candidate_registry / 전체 research_state",
        "", "### candidate_registry", "```json", j(snapshot["candidate_registry"]), "```", "", "### research_state", "```json", j(snapshot["research_state"]), "```", "",
        "## 7. Strategy / Policy Version / Deployment",
        "", "```json", j({"version_registry": snapshot["version_registry"], "deployment_state": snapshot["deployment_state"], "pending_runtime_sync": snapshot["pending_runtime_sync"]}), "```", "",
        "## 8. Shared Research Knowledge",
        "", "```json", j({"research_knowledge": snapshot["research_knowledge"], "runtime_knowledge": snapshot["runtime_knowledge"]}), "```", "",
        "## 9. 결과 인덱스",
        "", "```json", j(snapshot["result_index"]), "```", "",
        "## 10. 기존 R0~R16 역사",
        "",
    ]
    for axis, title in ROADMAP:
        lines.append(f"- `{axis}` {title}")
    lines += [
        "",
        "## 11. V2 Frozen Full History Archive",
        "",
        "아래 내용은 기존 상세 연구 역사를 손실 없이 보존하기 위한 frozen archive다. V3 scheduler가 실행 대상으로 사용하지 않는다.",
        "",
        legacy_text if legacy_text else "LEGACY_FROZEN_HANDOFF_MISSING_ON_THIS_OVERLAY",
        "",
    ]
    return "\n".join(lines)

# ============================================================
# 검증 / 메인
# ============================================================
def validate_text(text: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    required = [
        "NEXT ACTION", "Trial73", "73/73", "Freeze Registry", "candidate_registry",
        "research_state", "결과 인덱스", "R0~R16", "Strategy / Policy Version",
        "Roadmap Constitution", "Stable Trading Kernel", "COMPLETED / DO_NOT_RERUN",
        "Change Control / Release Validation", "OVERFIT", "LOGIC",
    ]
    missing = [x for x in required if x not in text]
    release = snapshot.get("release_validation") if isinstance(snapshot, dict) else {}
    release_ok = bool(
        isinstance(release, dict)
        and isinstance(release.get("overfit"), dict)
        and release["overfit"].get("ok") is True
        and isinstance(release.get("logic"), dict)
        and release["logic"].get("ok") is True
    )
    ok = not missing and bool((snapshot.get("constitution") or {}).get("ok")) and release_ok
    return {
        "ok": ok,
        "missing": missing,
        "release_validation_ok": release_ok,
        "snapshot_hash": snapshot.get("snapshot_hash"),
    }


# ============================================================
# GREEN7A canonical runtime evidence -> Master Handoff
# ============================================================
def derive_green7a_runtime_evidence(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    # GREEN7A_RUNTIME_EVIDENCE_DERIVATION_V1

    state_root = ROOT / "data" / "server_runtime" / "state"

    evidence_path = (
        state_root
        / "green7a_execution_telemetry_evidence.json"
    )

    processor_state_path = (
        state_root
        / "green7a_execution_telemetry_processor_state.json"
    )

    processor_path = (
        LAB
        / "v3_execution_telemetry_processor.py"
    )

    evidence = load(evidence_path, {})
    processor_state = load(processor_state_path, {})

    config = (
        snapshot.get("strategy_config")
        if isinstance(snapshot, dict)
        else {}
    ) or {}

    deployment = (
        snapshot.get("deployment_state")
        if isinstance(snapshot, dict)
        else {}
    ) or {}

    frames = (
        config.get("frames")
        if isinstance(config, dict)
        else {}
    ) or {}

    records = (
        evidence.get("records")
        if isinstance(evidence, dict)
        else []
    ) or []

    def as_int(value: Any) -> int:
        try:
            return int(float(value))
        except Exception:
            return 0

    def current_frame(tf: str) -> dict[str, Any]:

        if not isinstance(frames, dict):
            return {}

        direct = frames.get(tf)

        if isinstance(direct, dict):
            return direct

        for key, value in frames.items():
            if (
                str(key).upper() == tf.upper()
                and isinstance(value, dict)
            ):
                return value

        return {}

    def activation_ts(
        tf: str,
        version: str,
    ) -> int:

        found: list[int] = []

        def walk(obj: Any) -> None:

            if isinstance(obj, dict):

                for key, value in obj.items():

                    if (
                        str(key).upper() == tf.upper()
                        and isinstance(value, dict)
                    ):

                        activated = as_int(
                            value.get("activated_ts")
                        )

                        row_version = str(
                            value.get("strategy_version")
                            or ""
                        )

                        if (
                            activated > 0
                            and (
                                not row_version
                                or row_version == version
                            )
                        ):
                            found.append(activated)

                    walk(value)

            elif isinstance(obj, list):

                for value in obj:
                    walk(value)

        walk(deployment)

        return max(found) if found else 0

    live: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []

    for record in records:

        if not isinstance(record, dict):
            continue

        tf = str(
            record.get("timeframe")
            or ""
        ).upper()

        version = str(
            record.get("strategy_version")
            or ""
        )

        entry_id = str(
            record.get("entry_id")
            or ""
        )

        order_id = str(
            record.get("order_id")
            or ""
        )

        if not all((tf, version, entry_id, order_id)):
            continue

        cfg = current_frame(tf)

        if not cfg:
            continue

        if str(cfg.get("strategy_version") or "") != version:
            continue

        if str(cfg.get("deployment_tier") or "").upper() != "CANARY":
            continue

        if cfg.get("enabled") is not True:
            continue

        epoch = activation_ts(
            tf,
            version,
        )

        telemetry = record.get("telemetry") or {}

        preorder = (
            telemetry.get("preorder_reference")
            if isinstance(telemetry, dict)
            else {}
        ) or {}

        preorder_record = (
            preorder.get("record")
            if isinstance(preorder, dict)
            else {}
        ) or {}

        pending = (
            preorder_record.get("pending_snapshot")
            if isinstance(preorder_record, dict)
            else {}
        ) or {}

        entry_ts = as_int(
            preorder_record.get("entry_ts")
        )

        preorder_entry_id = str(
            preorder.get("entry_id")
            or preorder_record.get("entry_id")
            or ""
        )

        preorder_version = str(
            preorder.get("strategy_version")
            or preorder_record.get("strategy_version")
            or ""
        )

        tier_ok = (
            str(
                pending.get("deployment_tier")
                or ""
            ).upper()
            == "CANARY"
        )

        post_epoch = (
            epoch > 0
            and entry_ts >= epoch
        )

        binding_ok = (
            preorder.get("ok") is True
            and preorder_entry_id == entry_id
            and preorder_version == version
            and tier_ok
        )

        if not (
            post_epoch
            and binding_ok
        ):
            continue

        item = {
            "record": record,
            "telemetry": telemetry,
            "epoch": epoch,
            "entry_ts": entry_ts,
        }

        live.append(item)

        execution = (
            telemetry.get("execution_evidence")
            if isinstance(telemetry, dict)
            else {}
        ) or {}

        summary = (
            execution.get("summary")
            if isinstance(execution, dict)
            else {}
        ) or {}

        raw_ok = (
            summary.get("exact_order_id_binding") is True
            and int(summary.get("matched_trade_count") or 0) >= 1
            and summary.get("actual_fill_vwap") is not None
            and execution.get("actual_commission_quote") is not None
            and execution.get("actual_abs_slippage_bps") is not None
        )

        if raw_ok:

            item = dict(item)

            item["execution"] = execution
            item["summary"] = summary

            executed.append(item)

    sort_key = lambda row: as_int(
        (row.get("record") or {}).get("entry_history_id")
    )

    live.sort(key=sort_key)
    executed.sort(key=sort_key)

    live_proven = bool(live)
    execution_proven = bool(executed)

    latest = (
        executed[-1]
        if executed
        else (
            live[-1]
            if live
            else None
        )
    )

    record = (
        latest.get("record")
        if isinstance(latest, dict)
        else {}
    ) or {}

    telemetry = (
        latest.get("telemetry")
        if isinstance(latest, dict)
        else {}
    ) or {}

    execution = (
        latest.get("execution")
        if isinstance(latest, dict)
        else {}
    ) or {}

    summary = (
        latest.get("summary")
        if isinstance(latest, dict)
        else {}
    ) or {}

    validated_cost_policy = (
        execution.get(
            "validated_cost_policy_available"
        )
        is True
    )

    actual_fee_slippage_sane = (
        telemetry.get(
            "actual_fee_slippage_sane"
        )
        is True
    )

    # Actual execution telemetry is reference-only for promotion.
    # Positive scale is decided only by GREEN-7A promotion gates.
    positive_scale = (
        "NOT_DERIVED_FROM_EXECUTION_TELEMETRY_REFERENCE_ONLY"
    )

    if not live_proven:

        next_work = [
            "WAIT_FOR_FIRST_NATURAL_EXACT_VERSION_CANARY_ENTRY",
            "VERIFY_WATCHER_CAPTURED_DURABLE_PREORDER_REFERENCE",
            "VERIFY_ENTRY_HISTORY_EXACT_ENTRY_ID_STRATEGY_VERSION_AND_ORDER_ID",
            "VERIFY_TELEMETRY_PROCESSOR_FETCHED_EXACT_USERTRADES_FILLS",
            "VERIFY_ACTUAL_FILL_VWAP_COMMISSION_AND_SLIPPAGE_RAW_EVIDENCE",
            "WAIT_FOR_NATURAL_COMPLETED_LIFECYCLE_EVIDENCE",
            "VALIDATE_COST_AND_LIQUIDITY_POLICY",
            "EVALUATE_CANARY_TO_SMALL_ONLY_THROUGH_ALL_REQUIRED_GREEN7A_GATES",
        ]

    elif not execution_proven:

        next_work = [
            "VERIFY_TELEMETRY_PROCESSOR_FETCHED_EXACT_USERTRADES_FILLS",
            "VERIFY_ACTUAL_FILL_VWAP_COMMISSION_AND_SLIPPAGE_RAW_EVIDENCE",
            "WAIT_FOR_NATURAL_COMPLETED_LIFECYCLE_EVIDENCE",
            "VALIDATE_COST_AND_LIQUIDITY_POLICY",
            "EVALUATE_CANARY_TO_SMALL_ONLY_THROUGH_ALL_REQUIRED_GREEN7A_GATES",
        ]

    else:

        next_work = [
            "WAIT_FOR_NATURAL_COMPLETED_LIFECYCLE_EVIDENCE",
        ]

        if not validated_cost_policy:
            next_work.append(
                "VALIDATE_COST_AND_LIQUIDITY_POLICY"
            )

        next_work.append(
            "EVALUATE_CANARY_TO_SMALL_ONLY_THROUGH_ALL_REQUIRED_GREEN7A_GATES"
        )

    priority = (
        next_work[0]
        if next_work
        else "REVIEW_GREEN7A_CANARY_TO_SMALL_GATE"
    )

    source_sha = (
        hashlib.sha256(
            evidence_path.read_bytes()
        ).hexdigest()
        if evidence_path.is_file()
        else None
    )

    processor_sha = (
        hashlib.sha256(
            processor_path.read_bytes()
        ).hexdigest()
        if processor_path.is_file()
        else None
    )

    cursor = as_int(
        processor_state.get(
            "last_scanned_entry_history_id"
        )
        if isinstance(processor_state, dict)
        else 0
    )

    baseline = as_int(
        processor_state.get(
            "baseline_entry_history_id"
        )
        if isinstance(processor_state, dict)
        else 0
    )

    update = {
        "status":
            (
                "LIVE_CANARY_CAPTURE_AND_RAW_EXECUTION_EVIDENCE_PROVEN"
                if execution_proven
                else (
                    "LIVE_CANARY_CAPTURE_PROVEN_EXECUTION_TELEMETRY_PENDING"
                    if live_proven
                    else "WATCHER_SERVER_ACTIVE_STRUCTURE_VALIDATED"
                )
            ),

        "roadmap_runtime_status":
            (
                "REAL_CANARY_ENTRY_AND_RAW_EXECUTION_EVIDENCE_PROVEN_VALIDATED_COST_POLICY_PENDING"
                if (
                    execution_proven
                    and not validated_cost_policy
                )
                else (
                    "REAL_CANARY_ENTRY_AND_RAW_EXECUTION_EVIDENCE_PROVEN"
                    if execution_proven
                    else (
                        "REAL_CANARY_ENTRY_PROVEN_RAW_EXECUTION_EVIDENCE_PENDING"
                        if live_proven
                        else "IMPLEMENTATION_COMPLETE_SERVER_ACTIVE_STRUCTURE_VALIDATED_LIVE_CAPTURE_PENDING"
                    )
                )
            ),

        "actual_live_canary_capture":
            (
                "PROVEN_EXACT_VERSION_POST_EPOCH"
                if live_proven
                else "NOT_YET_PROVEN"
            ),

        "actual_execution_cost_evidence":
            (
                "PROVEN_RAW_EXACT_ORDER_FILLS"
                if execution_proven
                else "NOT_YET_PROVEN"
            ),

        "positive_scale":
            positive_scale,

        "telemetry_processor_sha256":
            processor_sha,

        "telemetry_activation_baseline_entry_history_id":
            baseline,

        "telemetry_last_scanned_entry_history_id":
            cursor,

        "telemetry_historical_backfill":
            False,

        "execution_telemetry_evidence":
            (
                "AVAILABLE_EXACT_VERSION_CANARY"
                if execution_proven
                else "NOT_YET_AVAILABLE"
            ),

        "validated_cost_policy":
            (
                "AVAILABLE"
                if validated_cost_policy
                else "NOT_YET_AVAILABLE"
            ),

        "actual_fee_slippage_sane":
            (
                "REFERENCE_ONLY_PASS"
                if actual_fee_slippage_sane
                else "REFERENCE_ONLY_NOT_PASS"
            ),

        "actual_execution_telemetry_promotion_role":
            "REFERENCE_ONLY_NON_BLOCKING_FOR_ALL_PROMOTIONS",

        "completed_lifecycle_evidence":
            "NOT_CLAIMED_FROM_ENTRY_TELEMETRY",

        "canonical_telemetry_source":
            "data/server_runtime/state/green7a_execution_telemetry_evidence.json",

        "canonical_telemetry_source_role":
            "SERVER_RUNTIME_SYNC_READ_ONLY_CANONICAL",

        "canonical_telemetry_sha256":
            source_sha,

        "historical_green7a_result_artifact_authoritative":
            False,

        "exact_version_post_epoch_live_record_count":
            len(live),

        "exact_version_raw_execution_record_count":
            len(executed),

        "latest_entry_history_id":
            record.get("entry_history_id"),

        "latest_entry_id":
            record.get("entry_id"),

        "latest_order_id":
            record.get("order_id"),

        "latest_symbol":
            record.get("symbol"),

        "latest_timeframe":
            record.get("timeframe"),

        "latest_strategy_version":
            record.get("strategy_version"),

        "evidence_epoch_ts":
            (
                latest.get("epoch")
                if isinstance(latest, dict)
                else None
            ),

        "latest_entry_ts":
            (
                latest.get("entry_ts")
                if isinstance(latest, dict)
                else None
            ),

        "post_epoch":
            bool(
                latest
                and as_int(latest.get("epoch")) > 0
                and as_int(latest.get("entry_ts"))
                >= as_int(latest.get("epoch"))
            ),

        "actual_fill_vwap":
            summary.get("actual_fill_vwap"),

        "actual_commission_quote":
            execution.get(
                "actual_commission_quote"
            ),

        "actual_abs_slippage_bps":
            execution.get(
                "actual_abs_slippage_bps"
            ),

        "matched_trade_count":
            summary.get(
                "matched_trade_count"
            ),
    }

    return {
        "handoff_update": update,

        "current_work": {
            "priority": priority,
            "detail_research_now": False,
            "new_mutation_gate_now": False,
            "next_check": priority,
        },

        "next_work": next_work,
    }


def apply_green7a_runtime_handoff(
    obj: Any,
    update: dict[str, Any],
) -> int:

    count = 0

    if isinstance(obj, dict):

        for key, value in obj.items():

            if (
                key == "green7a_preorder_runtime_evidence"
                and isinstance(value, dict)
            ):
                value.update(update)
                count += 1

            count += apply_green7a_runtime_handoff(
                value,
                update,
            )

    elif isinstance(obj, list):

        for value in obj:
            count += apply_green7a_runtime_handoff(
                value,
                update,
            )

    return count

def build_json_handoff(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Single authoritative B-Bot AI handoff/manual.

    Critical operational state is embedded.
    Bulky raw research/evidence remains in source ledgers and is referenced
    with path/hash/size instead of duplicated in full.
    """

    constitution_full = load(
        ROOT / "ROADMAP_CONSTITUTION_V3.json",
        {},
    )

    config = (
        snapshot.get("strategy_config")
        or {}
    )

    frames = (
        config.get("frames")
        or {}
    )

    knowledge = (
        snapshot.get("research_knowledge")
        or {}
    )

    runtime_knowledge = (
        snapshot.get("runtime_knowledge")
        or {}
    )

    records = (
        knowledge.get("records")
        or {}
    )

    if not isinstance(records, dict):
        records = {}

    runtime_records = (
        runtime_knowledge.get("records")
        or {}
    )

    if not isinstance(runtime_records, dict):
        runtime_records = {}

    blind_records = {
        str(k): v
        for k, v in records.items()
        if str(k).startswith("blind:")
    }

    evidence_boundaries = (
        knowledge.get("evidence_boundaries")
        or knowledge.get("boundaries")
        or {}
    )

    import v3_hypothesis_factory as hf

    def source(rel: str) -> dict[str, Any]:
        p = ROOT / rel

        return {
            "path": rel,
            "exists": p.is_file(),
            "sha256": (
                governance.file_hash(p)
                if p.is_file()
                else None
            ),
            "size_bytes": (
                p.stat().st_size
                if p.is_file()
                else None
            ),
        }

    frame_summary = {}

    for tf in (
        "1H",
        "6H",
        "12H",
        "1D",
        "1W",
        "1M",
    ):
        row = (
            frames.get(tf)
            or {}
        )

        frame_summary[tf] = {
            "enabled": row.get("enabled"),
            "status": row.get("status"),
            "strategies": row.get("strategies"),
            "strategy_version": row.get("strategy_version"),
            "deployment_tier": row.get("deployment_tier"),
            "leverage": row.get("leverage"),
            "capital_weight": row.get("capital_weight"),
            "legacy_manage_existing": row.get(
                "legacy_manage_existing"
            ),
        }

    strategy_path = (
        ROOT
        / "strategy_lab"
        / "v3_strategy_research.py"
    )

    strategy_text = (
        strategy_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
        if strategy_path.is_file()
        else ""
    )

    master = {
        "schema":
            "B_BOT_MASTER_HANDOFF_V1",

        "authoritative":
            True,

        "generated_at":
            now(),

        "purpose":
            "SINGLE_AI_HANDOFF_AND_BBOT_MANUAL",

        "identity": {
            "name": "B-Bot V3",
            "project_root": ".",
            "market": "BINANCE_USDT_M_FUTURES",
            "direct_entry_timeframes": [
                "1H",
                "6H",
                "12H",
                "1D",
                "1W",
            ],
            "reference_timeframes": [
                "1M",
            ],
        },

        "objective": {
            "primary":
                "LONG_RUN_COMPOUND_EXPECTED_ACCOUNT_RETURN",

            "forced_liquidation":
                "HARD_FAIL",

            "different_timeframes_may_use_different_main":
                True,

            "no_fixed_permanent_global_main":
                True,

            "current_priority":
                "FRAME_MAIN_SELECTION_BEFORE_DETAIL_RESEARCH",
        },

        "absolute_safety": {
            "order_py_modify":
                False,

            "order_sha256":
                "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c",

            "runner_sha256":
                "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160",

            "risk_engine_sha256":
                "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6",

            "candidate_sha256":
                "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac",

            "stable_kernel_research_mutation":
                False,

            "local_real_order":
                False,

            "manual_hold_change":
                False,

            "manual_runtime_request_recreate":
                False,

            "sealed_blind_reopen":
                False,

            "search_until_winner":
                False,

            "unvalidated_runtime_binding":
                False,
        },

        "constitution": {
            "source":
                "ROADMAP_CONSTITUTION_V3.json",

            "validation":
                snapshot.get("constitution"),

            "embedded_full":
                constitution_full,
        },

        "change_control": {
            "rule":
                "UNDERSTAND_EXISTING_STRUCTURE_BEFORE_CHANGE",

            "must_understand": [
                "CURRENT_IMPLEMENTATION",
                "CALLERS",
                "CALLEES",
                "DATA_FLOW",
                "DEPENDENCIES",
                "EXISTING_CONTRACTS",
                "EXISTING_VERIFIERS",
                "EVIDENCE_BOUNDARIES",
                "RUNTIME_EFFECT",
                "LIVE_EFFECT",
                "WHY_CURRENT_STRUCTURE_EXISTS",
            ],

            "sequence": [
                "UNDERSTAND_EXISTING_STRUCTURE",
                "ROADMAP_CONSTITUTION",
                "VERIFIER",
                "IMPLEMENTATION",
                "SELF_VERIFICATION",
                "REGRESSION",
                "UPDATE_MASTER_HANDOFF_JSON",
            ],

            "blind_replacement":
                False,

            "wrapper_monkey_patch_final_fix":
                False,

            "rollback":
                "EXACT_BYTES",
        },

        "architecture": {
            "pipeline": [
                "TIMEFRAME_SIGNAL",
                "CANDIDATE_AND_KNOWLEDGE",
                "LATE_ENTRY_REPLAY",
                "CANDIDATE_ENTRY_RISK",
                "EXPECTED_ACCOUNT_CONTRIBUTION",
                "CAPITAL_TIME_EFFICIENCY",
                "CONFIDENCE",
                "FINAL_CANDIDATE",
                "PORTFOLIO_ACCOUNT",
                "ALLOCATION",
                "SIZE_LEVERAGE",
                "FINAL_CROSS_STRESS_ENTRY_RISK",
                "EXECUTION",
            ],

            "strategy_identity":
                "STRATEGY_X_TIMEFRAME_X_VERSION",

            "knowledge_identity":
                "STRATEGY_X_TIMEFRAME_X_SYMBOL_X_VERSION",

            "entry_risk_separate_from_strategy_quality":
                True,
        },

        "runtime": {
            "frame_summary":
                frame_summary,

            "strategy_config":
                config,

            "deployment_state":
                snapshot.get("deployment_state"),

            "version_registry":
                snapshot.get("version_registry"),

            "change_control_state":
                snapshot.get("change_control_state"),

            "release_validation":
                snapshot.get("release_validation"),

            "pending_runtime_sync":
                snapshot.get("pending_runtime_sync"),

            "safety":
                snapshot.get("safety"),

            "last_confirmed_enabled_frames": ['12H', '1D', '6H', '1H'],

            "1d_release": {
                "status":
                    "ALREADY_APPLIED_DO_NOT_RERUN",

                "strategy":
                    "V3_COMBO_MOMENTUM_ACCEL_ADAPTIVE_CHANNEL_1D_V1",

                "strategy_version":
                    "SV_STRATEGY_c3dfbb6d68e4",

                "tier":
                    "CANARY",

                "runtime_request_recreate":
                    False,
            },
        },

        "strategy_research": {
            "evaluation_unit":
                "STRATEGY_X_TIMEFRAME_X_SYMBOL",

            "ranking": [
                "WALK_FORWARD_COST_STRESS_2X_AVG_NET_PCT",
                "WALK_FORWARD_AVG_NET_PCT",
                "DEVELOPMENT_AVG_NET_PCT",
                "SHORTER_EXPECTED_HOLDING_TIME_TIEBREAK",
            ],

            "initial_main_fill": {
                "priority": True,
                "frames": [
                    "1H",
                    "6H",
                    "1D",
                    "1W",
                ],
                "existing_12h_main_preserved":
                    True,

                "pool":
                    "18_STANDALONE_PLUS_48_BASE_SAFE_GRAMMAR",

                "mutation_blocks_initial_main":
                    False,

                "new_mutation_gate":
                    False,

                "detail_research_before_main":
                    False,
            },

            "standalone_18": {
                "count": 18,
            },

            "safe_grammar_48": {
                "count":
                    len(hf.recipes()),

                "role":
                    "INITIAL_MAIN_FILL_AND_CONTINUOUS_RESEARCH",
            },

            "mutation_24": {
                "count":
                    len(
                        hf.mutation_recipes()
                    ),

                "removed":
                    False,

                "role":
                    "CONTINUOUS_TRACK_STRATEGY_CHALLENGER_RESEARCH",

                "blocks_initial_main":
                    False,

                "is_detail_research":
                    False,

                "portability":
                    "COMPLETE_24_OF_24",

                "promotion_effect":
                    0,

                "runtime_binding":
                    0,

                "live_effect":
                    0,

                "last_confirmed_evidence": {
                    "with_any_evidence":
                        11,

                    "total":
                        24,

                    "all_5_timeframes":
                        0,

                    "meaning":
                        "INCOMPLETE_RESEARCH_WAVE_NOT_A_NEW_GATE",
                },
            },

            "factory_research_universe": {
                "base_plus_mutation":
                    len(
                        hf.research_recipes()
                    ),
            },

            "current_code_alignment": {
                "initial_main_18_plus_48_marker":
                    (
                        "INITIAL_MAIN_FILL_POOL_18_PLUS_48"
                        in strategy_text
                    ),

                "mutation_continues_marker":
                    (
                        "MUTATION_RESEARCH_CONTINUES_AFTER_INITIAL_MAIN_FILL"
                        in strategy_text
                    ),

                "legacy_all_complete_gate":
                    (
                        "hypothesis_factory.all_complete(tf)"
                        in strategy_text
                    ),

                "wave1_boundary":
                    (
                        "blind:{tf}:WAVE1"
                        in strategy_text
                    ),
            },
        },

        "research_tracks": {
            "tracks": [
                "DIRECTION",
                "STRATEGY",
                "ENTRY_RISK",
                "EXIT",
                "REENTRY",
                "SYMBOL",
                "PORTFOLIO",
                "REGIME",
                "EXECUTION",
            ],

            "current_priority":
                "STRATEGY_FRAME_MAIN_SELECTION",

            "detail_research_after_main":
                True,

            "roadmap_queue":
                snapshot.get("roadmap_queue"),
        },

        "evidence_policy": {
            "strength_order": [
                "REAL_LIVE",
                "CANARY_REAL",
                "SHADOW_FORWARD",
                "SEALED_BLIND",
                "WALK_FORWARD_ROBUSTNESS",
                "BACKTEST",
            ],

            "sealed_blind":
                "ONE_SHOT_IRREVERSIBLE",

            "seen_can_never_be_unseen":
                True,

            "cost_stress":
                "2X",

            "new_mutation_gate":
                False,

            "evidence_boundaries":
                evidence_boundaries,

            "sealed_blind_records":
                blind_records,
        },

        "machine_state": {
            "current_research":
                snapshot.get("current_research"),

            "candidate_registry":
                snapshot.get("candidate_registry"),

            "research_state":
                snapshot.get("research_state"),

            "trial_ledger":
                snapshot.get("trial_ledger"),

            "freeze_registry":
                snapshot.get("freeze_registry"),

            "result_index":
                snapshot.get("result_index"),

            "completed_research":
                snapshot.get("completed_research"),

            "failed_research":
                snapshot.get("failed_research"),

            "knowledge_summary": {
                "schema":
                    knowledge.get("schema"),

                "record_count":
                    len(records),

                "source":
                    "strategy_lab/results/v3/research_knowledge.json",
            },

            "runtime_knowledge_summary": {
                "schema":
                    runtime_knowledge.get("schema"),

                "record_count":
                    len(runtime_records),

                "source":
                    "strategy_lab/results/v3/research_knowledge_runtime.json",
            },
        },

        "completed_work": {'roadmap_constitution': 'COMPLETE',
                           'autonomous_strategy_contract': '37_OF_37_PASS_LAST_CONFIRMED',
                           'structural_mutation': {'status': 'COMPLETE',
                                                   'base': 48,
                                                   'mutation': 24,
                                                   'types': ['PRICE_DIRECTION', 'PERSIST_2']},
                           'mutation_portability': {'status': 'COMPLETE',
                                                    'count': 24,
                                                    'result': '15_OF_15_PASS',
                                                    'live_effect': 0},
                           'candidate_entry_risk': {'status': 'COMPLETE_RESEARCH_SHADOW',
                                                    'blocking': False,
                                                    'rank_effect': False,
                                                    'size_effect': False,
                                                    'leverage_effect': False,
                                                    'order_effect': False},
                           '1d_runtime_release': 'ALREADY_APPLIED_DO_NOT_RERUN',
                           'hold_verifier_fix': 'COMPLETE',
                           'green7a_preorder_runtime_evidence': {'status': 'WATCHER_SERVER_ACTIVE_STRUCTURE_VALIDATED',
                                                                 'roadmap_runtime_status': 'IMPLEMENTATION_COMPLETE_SERVER_ACTIVE_STRUCTURE_VALIDATED_LIVE_CAPTURE_PENDING',
                                                                 'watcher_server_activation': 'PASS',
                                                                 'linux_inotify_runtime_validation': 'PASS',
                                                                 'watcher_verifier': '32_OF_32_PASS',
                                                                 'observer_verifier': '23_OF_23_PASS',
                                                                 'execution_telemetry_verifier': '22_OF_22_PASS',
                                                                 'numeric_scale_policy_verifier': '29_OF_29_PASS',
                                                                 'frozen_core': 'UNCHANGED',
                                                                 'actual_live_canary_capture': 'NOT_YET_PROVEN',
                                                                 'actual_execution_cost_evidence': 'NOT_YET_PROVEN',
                                                                 'telemetry_server_deployment': 'ACTIVE',
                                                                 'positive_scale': 'NOT_DERIVED_FROM_EXECUTION_TELEMETRY_REFERENCE_ONLY',
                                                                 'telemetry_processor_sha256': 'bb898c63c69481cb7aaf6d7683d2d66b9a13997ce1ecf4e3fac99c88350c1b7f',
                                                                 'telemetry_timer': 'ACTIVE_ENABLED_RECURRING_5MIN',
                                                                 'telemetry_timer_schedule_validation': 'PASS',
                                                                 'telemetry_activation_baseline_entry_history_id': 0,
                                                                 'telemetry_last_scanned_entry_history_id': 0,
                                                                 'telemetry_historical_backfill': False,
                                                                 'execution_telemetry_evidence': 'NOT_YET_AVAILABLE',
                                                                 'validated_cost_policy': 'NOT_YET_AVAILABLE',
                                                                 'actual_fee_slippage_sane': 'REFERENCE_ONLY_NOT_PASS'}},

        "current_work": {'priority': 'OBSERVE_FIRST_NATURAL_EXACT_VERSION_CANARY_LIFECYCLE',
                         'detail_research_now': False,
                         'new_mutation_gate_now': False,
                         'next_check': 'OBSERVE_PREORDER_TO_USERTRADES_EXECUTION_EVIDENCE'},

        "next_work": ['WAIT_FOR_FIRST_NATURAL_EXACT_VERSION_CANARY_ENTRY',
                      'VERIFY_WATCHER_CAPTURED_DURABLE_PREORDER_REFERENCE',
                      'VERIFY_ENTRY_HISTORY_EXACT_ENTRY_ID_STRATEGY_VERSION_AND_ORDER_ID',
                      'VERIFY_TELEMETRY_PROCESSOR_FETCHED_EXACT_USERTRADES_FILLS',
                      'VERIFY_ACTUAL_FILL_VWAP_COMMISSION_AND_SLIPPAGE_RAW_EVIDENCE',
                      'WAIT_FOR_NATURAL_COMPLETED_LIFECYCLE_EVIDENCE',
                      'VALIDATE_COST_AND_LIQUIDITY_POLICY',
                      'EVALUATE_CANARY_TO_SMALL_ONLY_THROUGH_ALL_REQUIRED_GREEN7A_GATES'],

        "do_not_repeat": {
            "trial73":
                "COMPLETED_DO_NOT_RERUN",

            "1d_release":
                "DO_NOT_RERUN",

            "1d_runtime_request":
                "DO_NOT_RECREATE",

            "sealed_blind":
                "DO_NOT_REOPEN",

            "mutation_portability":
                "COMPLETE",

            "candidate_entry_risk":
                "DO_NOT_MIX_INTO_CURRENT_MAIN_SELECTION",

            "search_until_winner":
                "FORBIDDEN",

            "legacy_v2_master_handoff_generator":
                "LEGACY_ONLY_DO_NOT_USE_FOR_CURRENT_HANDOFF",
        },

        "important_decisions": {
            "frame_main":
                "PRIORITY_BEFORE_DETAIL_RESEARCH",

            "initial_main_pool":
                "18_STANDALONE_PLUS_48_BASE_SAFE_GRAMMAR",

            "mutation":
                "CONTINUOUS_TRACK_STRATEGY_CHALLENGER_RESEARCH",

            "mutation_initial_main_blocker":
                False,

            "mutation_specific_gate":
                False,

            "master_handoff":
                "B_BOT_MASTER_HANDOFF_JSON_ONLY",
        },

        "incidents_and_lessons": {
            "source_rollback":
                "USE_EXACT_BYTES",

            "packager_sha_incident":
                "TEXT_NORMALIZATION_ROLLBACK_FORBIDDEN",

            "hold_incident":
                "DO_NOT_MANUALLY_CREATE_OR_DELETE_HOLD_FOR_VERIFIER",

            "overfit_correction":
                "DO_NOT_REQUIRE_ONE_STRATEGY_TO_PASS_ALL_5_TFS",

            "priority_correction":
                "FRAME_MAIN_SELECTION_BEFORE_DETAIL_RESEARCH",

            "handoff_migration":
                "JSON_REPLACES_CURRENT_MD_HANDOFF",
        },

        "important_history": {
            "legacy":
                snapshot.get("legacy"),

            "freeze_registry":
                snapshot.get("freeze_registry"),

            "trial73":
                "COMPLETED_DO_NOT_RERUN",
        },

        "file_map": {'ROADMAP_CONSTITUTION_V3.json': 'LEGAL_CONSTITUTION',
                     'B_BOT_MASTER_HANDOFF.json': 'AUTHORITATIVE_AI_HANDOFF_AND_BBOT_MANUAL',
                     'strategy_lab/master_handoff.py': 'CURRENT_JSON_HANDOFF_GENERATOR',
                     'strategy_lab/legacy_v2/master_handoff_v2.py': 'LEGACY_V2_ONLY_DO_NOT_USE',
                     'strategy_lab/v3_strategy_research.py': 'STRATEGY_RESEARCH_AND_MAIN_SELECTION',
                     'strategy_lab/v3_hypothesis_factory.py': '48_BASE_PLUS_24_MUTATIONS',
                     'strategy_lab/v3_challenger_research.py': 'CHALLENGER_RESEARCH',
                     'strategy_lab/research_controller.py': 'AUTONOMOUS_RESEARCH_CONTROLLER',
                     'runner.py': 'FROZEN_RUNTIME',
                     'order.py': 'ABSOLUTELY_FROZEN_EXECUTION',
                     'strategy_lab/v3_preorder_reference_watcher.py': 'GREEN7A_PREORDER_RUNTIME_WATCHER',
                     'strategy_lab/v3_preorder_reference_observer.py': 'GREEN7A_PREORDER_REFERENCE_OBSERVER',
                     'strategy_lab/v3_execution_telemetry_collector.py': 'GREEN7A_REAL_EXECUTION_TELEMETRY_COLLECTOR',
                     'strategy_lab/v3_execution_cost_evidence.py': 'GREEN7A_REAL_EXECUTION_COST_EVIDENCE',
                     'strategy_lab/v3_scale_numeric_policy.py': 'GREEN7A_SCALE_NUMERIC_POLICY',
                     'strategy_lab/v3_scale_resource_projection.py': 'GREEN7A_SCALE_RESOURCE_PROJECTION',
                     'strategy_lab/v3_execution_telemetry_processor.py': 'GREEN7A_EXECUTION_TELEMETRY_RUNTIME_PROCESSOR',
                     'verify_green7a_execution_telemetry_processor.py': 'GREEN7A_EXECUTION_TELEMETRY_PROCESSOR_VERIFIER'},

        "sha_registry": {
            rel: source(rel)
            for rel in (
                "ROADMAP_CONSTITUTION_V3.json",
                "strategy_lab/master_handoff.py",
                "strategy_lab/v3_governance.py",
                "strategy_lab/v3_strategy_research.py",
                "strategy_lab/v3_hypothesis_factory.py",
                "strategy_lab/v3_portable_strategy_packager.py",
                "strategy_lab/v3_candidate_entry_risk.py",
                "strategy_lab/v3_auto_runtime_release.py",
                "verify_overlay_smoke.py",
                "verify_main_first_selection_policy.py",
                "verify_roadmap_constitution.py",
                "verify_logic_validation.py",
                "verify_overfit_validation.py",
                "candidate.py",
                "risk_engine.py",
                "runner.py",
                "order.py",
            )
        },

        "verifier_registry": {'main_first_selection': 'verify_main_first_selection_policy.py',
                              'roadmap': 'verify_roadmap_constitution.py',
                              'strategy_constitution': 'verify_autonomous_strategy_research_constitution.py',
                              'mutation_contract': 'verify_strategy_variation_mutation_contract.py',
                              'mutation_portability': 'verify_mutation_portability.py',
                              'safe_grammar': 'verify_safe_grammar_matrix.py',
                              'strategy_family': 'verify_strategy_family_matrix.py',
                              'overfit': 'verify_overfit_validation.py',
                              'logic': 'verify_logic_validation.py',
                              'overlay_smoke': 'B_BOT_MASTER_HANDOFF_JSON_SCHEMA_CHECK',
                              'green7a_preorder_runtime_watcher': 'verify_green7a_preorder_runtime_watcher.py',
                              'green7a_preorder_reference_observer': 'verify_green7a_preorder_reference_observer.py',
                              'green7a_execution_telemetry': 'verify_green7a_execution_telemetry.py',
                              'green7a_numeric_scale_policy': 'verify_green7a_numeric_scale_policy.py',
                              'green7a_execution_telemetry_processor': 'verify_green7a_execution_telemetry_processor.py'},

        "source_ledgers": {
            "policy":
                "RAW_BULKY_DATA_NOT_DUPLICATED",

            "research_state":
                source(
                    "strategy_lab/results/research_state.json"
                ),

            "candidate_registry":
                source(
                    "strategy_lab/results/candidate_registry.json"
                ),

            "research_knowledge":
                source(
                    "strategy_lab/results/v3/research_knowledge.json"
                ),

            "runtime_knowledge":
                source(
                    "strategy_lab/results/v3/research_knowledge_runtime.json"
                ),

            "trial_ledger":
                source(
                    "strategy_lab/results/v3/trial_ledger.json"
                ),

            "result_root":
                "strategy_lab/results",
        },

        "resume_instructions": {
            "read_this_file_first":
                True,

            "prior_chat_required":
                False,

            "sequence": [
                "READ_MASTER_JSON",
                "CHECK_CURRENT_WORK",
                "CHECK_NEXT_WORK",
                "VERIFY_FROZEN_HASHES",
                "READ_TARGET_IMPLEMENTATION",
                "READ_CALLERS_CALLEES",
                "READ_CONTRACTS_VERIFIERS",
                "CHECK_EVIDENCE_BOUNDARIES",
                "MAKE_MINIMUM_CHANGE",
                "SELF_VERIFY",
                "REGRESSION",
                "REFRESH_MASTER_JSON",
            ],

            "constitution_conflict":
                "CONSTITUTION_WINS",

            "runtime_conflict":
                "CURRENT_RUNTIME_STATE_WINS_AND_REFRESH_HANDOFF",

            "uncertainty":
                "READ_ONLY_DISCOVERY_FIRST",
        },

        "operator_workflow": {
            "shell":
                "WINDOWS_POWERSHELL",

            "python_pattern":
                "@' ... '@ | .\\.venv\\Scripts\\python.exe -",

            "preferred_patch":
                "ONE_SHOT_TRANSACTION_WHEN_SAFE",

            "wrapper":
                False,

            "monkey_patch":
                False,

            "rollback":
                "EXACT_BYTES",
        },
    }

    green7a_runtime = derive_green7a_runtime_evidence(
        snapshot
    )

    green7a_node_count = apply_green7a_runtime_handoff(
        master,
        green7a_runtime["handoff_update"],
    )

    if green7a_node_count != 1:
        raise RuntimeError(
            "GREEN7A_HANDOFF_NODE_COUNT_INVALID:"
            + str(green7a_node_count)
        )

    master["current_work"] = green7a_runtime["current_work"]
    master["next_work"] = green7a_runtime["next_work"]

    master["snapshot_hash"] = stable_hash(
        master
    )

    return master

def refresh() -> dict[str, Any]:
    # AUTHORITATIVE_HANDOFF_JSON_ONLY
    snapshot = build_snapshot()

    master = build_json_handoff(
        snapshot
    )

    target = (
        ROOT
        / "B_BOT_MASTER_HANDOFF.json"
    )

    atomic_json(
        target,
        master,
    )

    system = {
        "schema":
            "B_BOT_V3_MASTER_HANDOFF_SYSTEM_V3",

        "updated_at":
            now(),

        "authoritative_handoff":
            "B_BOT_MASTER_HANDOFF.json",

        "format":
            "JSON",

        "snapshot_hash":
            master.get("snapshot_hash"),

        "legacy_markdown_authoritative":
            False,

        "next_action":
            (
                master.get("next_work")
                or [None]
            )[0],
    }

    atomic_json(
        SYS,
        system,
    )

    return system



def main() -> None:
    result = refresh()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
