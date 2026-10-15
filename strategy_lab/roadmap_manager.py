# -*- coding: utf-8 -*-
# 파일명: strategy_lab/roadmap_manager.py
"""B-Bot V3 Roadmap Constitution 기반 9-Track 스케줄러.

기존 research_state / Trial73 / legacy next_action은 보존만 하고 실행하지 않는다.
새 연구 실행 순서는 Constitution 검증 -> Controller 실행 -> state 병합으로 고정한다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 경로
# ============================================================
ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
RES = LAB / "results"
V3 = RES / "v3"
STATE = RES / "research_state.json"
STATUS = RES / "roadmap_status_latest.json"
CTRL = LAB / "research_controller.py"

# ROADMAP_SCOPED_RESEARCH_HOLD_V1
#
# Research needs SERVER_PUSH_HOLD while one bounded local research action
# executes, but LIVE/runtime normally needs this flag OFF.
#
# Ownership is fail-closed:
# - pre-existing HOLD is never modified;
# - exclusive-create prevents stealing another writer's HOLD;
# - release requires exact token AND unchanged mtime;
# - another process touching/replacing the HOLD makes cleanup preserve it.
_RESEARCH_HOLD = ROOT / "SERVER_PUSH_HOLD.flag"


def _acquire_scoped_research_hold() -> dict[str, Any]:
    if _RESEARCH_HOLD.exists():
        return {
            "owned": False,
            "preexisting": True,
            "token": None,
            "mtime_ns": None,
        }

    token = (
        "ROADMAP_RESEARCH_V1"
        + "|pid="
        + str(os.getpid())
        + "|nonce="
        + uuid.uuid4().hex
        + "|started="
        + datetime.now(timezone.utc).isoformat()
    )

    try:
        with _RESEARCH_HOLD.open(
            "x",
            encoding="ascii",
        ) as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())

    except FileExistsError:
        return {
            "owned": False,
            "preexisting": True,
            "token": None,
            "mtime_ns": None,
        }

    stat = _RESEARCH_HOLD.stat()

    return {
        "owned": True,
        "preexisting": False,
        "token": token,
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _release_scoped_research_hold(
    lease: dict[str, Any],
) -> str:

    if not isinstance(lease, dict):
        return "INVALID_LEASE_HOLD_PRESERVED"

    if lease.get("owned") is not True:
        return "PREEXISTING_HOLD_PRESERVED"

    if not _RESEARCH_HOLD.exists():
        return "OWNED_HOLD_ALREADY_ABSENT"

    token = str(
        lease.get("token")
        or ""
    )

    expected_mtime = lease.get(
        "mtime_ns"
    )

    if (
        not token
        or expected_mtime is None
    ):
        return "LEASE_EVIDENCE_MISSING_HOLD_PRESERVED"

    try:
        current = _RESEARCH_HOLD.read_text(
            encoding="ascii",
        )

        stat = _RESEARCH_HOLD.stat()

    except Exception:
        return "HOLD_READ_OR_STAT_FAIL_PRESERVED"

    if current != token:
        return "HOLD_CONTENT_CHANGED_PRESERVED"

    if int(stat.st_mtime_ns) != int(expected_mtime):
        return "HOLD_MTIME_CHANGED_PRESERVED"

    _RESEARCH_HOLD.unlink()

    return "OWNED_RESEARCH_HOLD_RELEASED"


sys.path.insert(0, str(LAB))
import v3_governance as governance
import v3_task_queue as task_queue

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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _legacy_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    chapter = state.get("chapter_controller") if isinstance(state.get("chapter_controller"), dict) else {}
    return {
        "next_action": state.get("next_action") or chapter.get("next_action"),
        "chapter_controller": chapter,
        "search_trials": int(state.get("search_trials") or 73),
        "parameter_trials": int(state.get("parameter_trials") or 73),
        "trial73": "COMPLETED_DO_NOT_RERUN",
        "execute_legacy_next_action": False,
        "history_scope": "R0-R16_PRESERVE",
    }

# ============================================================
# 메인
# ============================================================
def main() -> int:
    constitution = governance.validate_constitution()
    if not constitution.get("ok"):
        out = {
            "schema": "B_BOT_V3_ROADMAP_STATUS_V2",
            "updated_at": now(),
            "ok": False,
            "action": "GOVERNANCE_BLOCK",
            "constitution": constitution,
            "server_push_hold": (ROOT / "SERVER_PUSH_HOLD.flag").exists(),
        }
        atomic(STATUS, out)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 2

    research_architecture_v3 = (
        governance.constitution()
        .get("research_architecture_v3")
        or {}
    )

    research_scheduler = (
        research_architecture_v3
        .get("research_scheduler")
        or {}
    )

    before = governance.capture_kernel_guard()
    queue_before = task_queue.status()

    env = os.environ.copy()
    env["BBOT_LOCAL_ROOT"] = str(ROOT)
    env["BOT_BASE_DIR"] = str(ROOT)
    env["RUNNER_DRY_RUN"] = "1"
    _research_hold_lease = _acquire_scoped_research_hold()

    try:
        proc = subprocess.run(
            [sys.executable, str(CTRL), "--mode", "once", "--enable-research"],
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    finally:
        _research_hold_release = _release_scoped_research_hold(
            _research_hold_lease
        )
    text = proc.stdout.strip()
    result: dict[str, Any] = {"raw": text[-12000:], "returncode": proc.returncode}
    try:
        start = text.find("{")
        if start >= 0:
            parsed = json.loads(text[start:])
            if isinstance(parsed, dict):
                result = parsed
                result.setdefault("returncode", proc.returncode)
    except Exception:
        pass

    result.setdefault(
        "research_hold_owned",
        bool(_research_hold_lease.get("owned")),
    )
    result.setdefault(
        "research_hold_release",
        _research_hold_release,
    )

    governance.assert_kernel_unchanged(before)
    state = load(STATE, {})
    if not isinstance(state, dict):
        state = {}
    legacy = _legacy_snapshot(state)
    queue_after = task_queue.status()
    registry = governance.load(governance.VERSION_REGISTRY, {})

    state["v3"] = {
        "schema": "B_BOT_V3_ROADMAP_STATE_V2",
        "updated_at": now(),
        "constitution_version": constitution.get("constitution_version"),
        "constitution_hash": constitution.get("constitution_hash"),
        "scheduler":
            "MAIN_MIDDLE_DETAIL_RESEARCH_SCHEDULER_V1",

        "research_architecture_v3": {
            "policy_id":
                research_architecture_v3.get(
                    "policy_id"
                ),

            "status":
                research_architecture_v3.get(
                    "status"
                ),

            "MAIN":
                (
                    research_architecture_v3
                    .get("MAIN")
                    or {}
                ).get("stages"),

            "MIDDLE":
                (
                    research_architecture_v3
                    .get("MIDDLE")
                    or {}
                ).get("stages"),

            "DETAIL":
                (
                    research_architecture_v3
                    .get("DETAIL")
                    or {}
                ).get("stages"),
        },

        "research_scheduler":
            research_scheduler,

        "resource_allocation":
            (
                research_architecture_v3
                .get("resource_allocation")
                or {}
            ),

        "legacy_scheduler":
            "PARALLEL_9_TRACK_GOVERNED_QUEUE",

        "tracks": list(governance.EXPECTED_TRACKS),
        "main_fill_priority": ["6H", "1H", "1D", "1W"],
        "resource_weights": {"30M": 0.20, "1H": 0.23, "6H": 0.23, "12H": 0.18, "1D": 0.10, "1W": 0.06},
        "full_lifecycle_research": True,
        "stable_trading_kernel": "FROZEN_CORE_RESEARCH_CANNOT_MUTATE",
        "strategy_version_required": True,
        "policy_version_required": True,
        "promotion_path": ["RESEARCH", "VALIDATION", "PROVISIONAL", "CANARY", "SMALL", "NORMAL"],
        "rollback": "PREVIOUS_VALIDATED_VERSION",
        "change_control_order": list(governance.EXPECTED_CHANGE_ORDER),
        "last_controller_result": result,
        "queue": queue_after,
        "legacy_frozen": legacy,
        "version_registry": registry,
        "execute_legacy_next_action": False,
    }
    atomic(STATE, state)

    out = {
        "schema": "B_BOT_V3_ROADMAP_STATUS_V2",
        "updated_at": now(),
        "ok": proc.returncode == 0,
        "controller_result": result,
        "constitution": constitution,
        "queue_before_cycle": queue_before.get("scheduler_cycle"),
        "queue_after_cycle": queue_after.get("scheduler_cycle"),
        "legacy_trial73": "COMPLETED_DO_NOT_RERUN",
        "legacy_trial_counters": {"search": 73, "parameter": 73},
        "legacy_history": "R0-R16_PRESERVED",
        "legacy_serial_execution": False,
        "server_push_hold": (ROOT / "SERVER_PUSH_HOLD.flag").exists(),
        "real_order_off_for_local": os.getenv("RUNNER_DRY_RUN", "1") == "1",
        "v3_state_file": str(STATE),
    }
    atomic(STATUS, out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if proc.returncode == 0 else proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
