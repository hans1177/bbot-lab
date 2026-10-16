#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: verify_logic_validation.py
"""B-Bot V3 상태전이/책임분리/안전논리 검증. 거래소 write 없음."""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import json
import os
import sys
import tempfile
from pathlib import Path

# ============================================================
# 경로 / 환경
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", Path(__file__).resolve().parent))
os.environ["BBOT_LOCAL_ROOT"] = str(ROOT)
os.environ["BOT_BASE_DIR"] = str(ROOT)
os.environ["RUNNER_DRY_RUN"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategy_lab"))

import research_controller
import v3_deployment as deployment
import v3_governance as governance
import v3_task_queue as task_queue

# ============================================================
# 검증 수집
# ============================================================

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)})


def rejects(name: str, fn, expected: str) -> None:
    try:
        fn()
    except Exception as exc:
        check(name, expected in str(exc), repr(exc))
    else:
        check(name, False, "exception not raised")


# ============================================================
# Constitution / Track 책임 분리
# ============================================================

cv = governance.validate_constitution()
check("CONSTITUTION_VALID", cv.get("ok"), cv)
logic_gate = ((governance.constitution().get("release_validation_gates") or {}).get("LOGIC") or {})
check("CONSTITUTION_LOGIC_GATE_REQUIRED", logic_gate.get("required") is True, logic_gate)
check("LOGIC_BLOCKS_PACKAGE", logic_gate.get("must_pass_before_package") is True, logic_gate)
check("LOGIC_BLOCKS_SERVER_RELEASE", logic_gate.get("must_pass_before_server_release") is True, logic_gate)

tracks = governance.constitution().get("tracks") or {}
check(
    "TRACKS_EXACT_9",
    len(tracks) == len(governance.EXPECTED_TRACKS)
    and set(tracks) == set(governance.EXPECTED_TRACKS),
    list(tracks),
)
for track in governance.EXPECTED_TRACKS:
    row = tracks.get(track) or {}
    target = str(row.get("version_target") or "")
    expected = "STRATEGY_VERSION" if track == "STRATEGY" else f"{track}_POLICY_VERSION"
    check(f"TRACK_VERSION_TARGET_{track}", target == expected, target)
    check(f"TRACK_NO_CORE_MUTATION_{track}", row.get("core_mutation") is False, row)

check("LIFECYCLE_ORDER_CORE", task_queue.LIFECYCLE_SEQUENCE[:5] == ["STRATEGY", "ENTRY_RISK", "EXIT", "REENTRY", "EXECUTION"], task_queue.LIFECYCLE_SEQUENCE)
queue = task_queue.status()
for track in governance.EXPECTED_TRACKS:
    contract = (((queue.get("tracks") or {}).get(track) or {}).get("task_contract") or {})
    allowed = contract.get("allowed_search_space") or {}
    check(f"TASK_SCOPE_{track}", allowed.get("track") == track and allowed.get("version_target") == tracks[track].get("version_target"), allowed)

# ============================================================
# Strategy/Policy Version 상태전이 실제 테스트
# ============================================================

old_registry = governance.VERSION_REGISTRY
with tempfile.TemporaryDirectory() as td:
    governance.VERSION_REGISTRY = Path(td) / "version_registry.json"
    try:
        rejects(
            "STRATEGY_KIND_WRONG_TRACK_REJECTED",
            lambda: governance.register_version_candidate("STRATEGY", "EXIT", {"x": 1}),
            "STRATEGY_VERSION_TRACK_MISMATCH",
        )
        rejects(
            "POLICY_KIND_STRATEGY_TRACK_REJECTED",
            lambda: governance.register_version_candidate("POLICY", "STRATEGY", {"x": 1}),
            "POLICY_VERSION_TRACK_MISMATCH",
        )
        strategy = governance.register_version_candidate("STRATEGY", "STRATEGY", {"name": "LOGIC_TEST"})
        sid = strategy["version_id"]
        rejects(
            "DIRECT_RESEARCH_TO_CANARY_REJECTED",
            lambda: governance.set_version_status(sid, "CANARY"),
            "VERSION_TRANSITION_FORBIDDEN",
        )
        governance.set_version_status(sid, "VALIDATED")
        governance.set_version_status(sid, "CANARY")
        governance.set_version_status(sid, "SMALL")
        normal = governance.set_version_status(sid, "NORMAL")
        check("VALID_STRATEGY_TRANSITION_CHAIN", normal.get("status") == "NORMAL", normal)
        rejects(
            "NORMAL_TO_VALIDATED_REGRESSION_REJECTED",
            lambda: governance.set_version_status(sid, "VALIDATED"),
            "VERSION_TRANSITION_FORBIDDEN",
        )

        policy = governance.register_version_candidate("POLICY", "EXIT", {"config_patch": {"trailing": {"start_pct": 4.0}}})
        pid = policy["version_id"]
        governance.set_version_status(pid, "VALIDATED")
        pcanary = governance.set_version_status(pid, "CANARY")
        check("VALID_POLICY_TRANSITION_CHAIN", pcanary.get("status") == "CANARY", pcanary)
    finally:
        governance.VERSION_REGISTRY = old_registry

# ============================================================
# Stable Kernel / promotion / rollback / legacy freeze
# ============================================================

kernel_before = governance.capture_kernel_guard()
selftest = research_controller.selftest()
try:
    governance.assert_kernel_unchanged(kernel_before)
    kernel_ok = True
except Exception as exc:
    kernel_ok = False
    kernel_detail = repr(exc)
else:
    kernel_detail = "unchanged after controller selftest"
check("STABLE_KERNEL_UNCHANGED_BY_RESEARCH_SELFTEST", kernel_ok and selftest.get("ok"), kernel_detail)
check("RESEARCH_WRITE_RESULTS_ALLOWED", governance.research_write_allowed(ROOT / "strategy_lab/results/v3/test.json"))
check("RESEARCH_WRITE_DEPLOY_READY_ALLOWED", governance.research_write_allowed(ROOT / "strategy_lab/deploy_ready/test.py"))
check("RESEARCH_WRITE_RUNNER_FORBIDDEN", not governance.research_write_allowed(ROOT / "runner.py"))
check("RESEARCH_WRITE_ORDER_FORBIDDEN", not governance.research_write_allowed(ROOT / "order.py"))

rejects(
    "POLICY_CANARY_UNKNOWN_VERSION_REJECTED_EVEN_ON_HOLD",
    lambda: deployment.create_policy_canary_request("PV_DOES_NOT_EXIST"),
    "POLICY_VERSION_NOT_FOUND",
)

config = json.loads((ROOT / "strategy_config.json").read_text(encoding="utf-8-sig"))
check("ROLLBACK_PREVIOUS_VALIDATED", (config.get("deployment") or {}).get("rollback_target") == "PREVIOUS_VALIDATED_VERSION")
check("POLICY_CANARY_VALIDATED_REQUIRED", (config.get("deployment") or {}).get("policy_canary_requires_validated_version") is True)
check("DIRECT_FULL_SCALE_FORBIDDEN", (config.get("deployment") or {}).get("direct_full_scale") is False)
check("CANARY_PATH_ORDER", (config.get("deployment") or {}).get("auto_scale_path") == ["CANARY", "SMALL", "NORMAL"])

knowledge = json.loads((ROOT / "strategy_lab/results/v3/research_knowledge.json").read_text(encoding="utf-8-sig"))
ledger = json.loads((ROOT / "strategy_lab/results/v3/trial_ledger.json").read_text(encoding="utf-8-sig"))
check("TRIAL73_FROZEN_KNOWLEDGE", (knowledge.get("legacy") or {}).get("trial73") == "COMPLETED_DO_NOT_RERUN")
check("TRIAL73_FROZEN_LEDGER", int(ledger.get("legacy_counter_frozen_at") or -1) == 73)

# ============================================================
# Local-only 안전상태
# ============================================================

local_test = (ROOT / "LOCAL_V3_TEST.ps1").read_text(encoding="utf-8-sig", errors="ignore")
release = (ROOT / "ENABLE_SERVER_SYNC_AFTER_LOCAL_PASS.ps1").read_text(encoding="utf-8-sig", errors="ignore")
# SERVER_PUSH_HOLD_STATE_POLICY_V2
# HOLD is mandatory while a runtime release/request is active.
# After a completed migration the Constitution explicitly permits
# the normal emergency-hold state to be OFF.
_hold_path = ROOT / "SERVER_PUSH_HOLD.flag"
_request_path = ROOT / "strategy_lab/results/v3/runtime_sync_request.json"

_hold_exists = _hold_path.exists()
_request_exists = _request_path.exists()

# HOLD_RECOVERY_LAST_APPLIED_CONTRACT_V1
# ?? ?? ??/mark_applied? ??? request? active request ???
# ??? ?? request_id? ??? last-applied ??? ?? ??? ? ??.
_last_applied_path = ROOT / "strategy_lab/results/v3/runtime_sync_last_applied.json"
_release_state_path = ROOT / "strategy_lab/results/v3/auto_runtime_release_state.json"

_applied_req = {}
_release_state = {}

try:
    if _last_applied_path.is_file():
        _applied_req = json.loads(
            _last_applied_path.read_text(
                encoding="utf-8-sig"
            )
        )
except Exception:
    _applied_req = {}

try:
    if _release_state_path.is_file():
        _release_state = json.loads(
            _release_state_path.read_text(
                encoding="utf-8-sig"
            )
        )
except Exception:
    _release_state = {}

_applied_request_id = str(
    _applied_req.get("request_id") or ""
)

_release_state_request_id = str(
    _release_state.get("request_id") or ""
)

_hold_recovery_applied_contract_ok = bool(
    _applied_request_id
    and _release_state_request_id
    and _applied_request_id == _release_state_request_id
    and _release_state.get("schema")
        == "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
    and str(
        _release_state.get("status") or ""
    ).upper() == "WAIT_EMERGENCY_HOLD"
    and _applied_req.get("schema")
        == "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1"
    and _applied_req.get(
        "approved_for_server_release"
    ) is True
    and _applied_req.get("core_mutation") is False
    and str(
        _applied_req.get("tier") or ""
    ).upper() == "CANARY"
    and str(
        _applied_req.get(
            "local_qualification_tier"
        ) or ""
    ).upper() == "CANARY"
    and str(
        _applied_req.get("shadow_gate") or ""
    ).upper() == "PASS"
    and bool(
        str(
            _applied_req.get(
                "strategy_instance_id"
            ) or ""
        )
    )
    and bool(
        str(
            _applied_req.get(
                "strategy_version"
            ) or ""
        )
    )
    and bool(
        str(
            _applied_req.get("timeframe") or ""
        )
    )
)


_constitution_for_hold = json.loads(
    (ROOT / "ROADMAP_CONSTITUTION_V3.json").read_text(
        encoding="utf-8-sig"
    )
)

_emergency_hold_policy = (
    (
        _constitution_for_hold.get(
            "autonomous_runtime_release_policy"
        )
        or {}
    ).get(
        "emergency_hold"
    )
    or {}
)

_normal_post_migration_hold = str(
    _emergency_hold_policy.get(
        "normal_post_migration_state"
    )
    or ""
).upper()

_logic_mode = str(
    os.environ.get("B_BOT_V3_LOGIC_VERIFY_MODE")
    or "PACKAGE"
).upper()

if _logic_mode == "HOLD_RECOVERY":
    _hold_state_valid = (
        _hold_exists
        and
        (
            _request_exists
            or
            _hold_recovery_applied_contract_ok
        )
    )
elif _logic_mode == "RELEASE_CANDIDATE":
    _hold_state_valid = (
        _request_exists
        and
        not _hold_exists
    )
else:
    _hold_state_valid = (
        _hold_exists
        or
        (
            not _request_exists
            and
            _normal_post_migration_hold == "OFF"
        )
    )


check(
    "SERVER_PUSH_HOLD_PRESENT",
    _hold_state_valid,
)
check(
    "LOCAL_TEST_MODE_PRESENT",
    (ROOT / "LOCAL_TEST_MODE.flag").exists() or 'RUNNER_DRY_RUN="1"' in local_test,
)
check("REAL_ORDER_OFF_LOCAL", 'RUNNER_DRY_RUN="1"' in local_test)

# R4 release 후보 검증에서는 active request가 존재하는 것이 정상이다.
# 일반/package 검증에서는 기존과 같이 active request가 없어야 한다.
if _logic_mode in ("RELEASE_CANDIDATE", "HOLD_RECOVERY"):
    if _logic_mode == "HOLD_RECOVERY":
        check(
            "HOLD_RECOVERY_REQUEST_EVIDENCE_REQUIRED",
            (
                _request_exists
                or
                _hold_recovery_applied_contract_ok
            ),
        )
    else:
        check(
            "ACTIVE_RUNTIME_SYNC_REQUEST_REQUIRED",
            _request_exists,
        )

    if _logic_mode == "HOLD_RECOVERY":
        check("HOLD_RECOVERY_HOLD_REQUIRED", _hold_exists)
    else:
        check("RELEASE_CANDIDATE_HOLD_CLEARED", not _hold_exists)
    _request_contract_ok = False
    _request_contract_detail = "REQUEST_MISSING"
    if _request_exists:
        try:
            _req = json.loads(_request_path.read_text(encoding="utf-8-sig"))
            _request_contract_ok = (
                _req.get("schema") == "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1"
                and _req.get("approved_for_server_release") is True
                and _req.get("core_mutation") is False
                and str(_req.get("tier") or "").upper() == "CANARY"
                and str(_req.get("local_qualification_tier") or "").upper() == "CANARY"
                and str(_req.get("shadow_gate") or "").upper() == "PASS"
            )
            _request_contract_detail = _req.get("request_id") or "REQUEST_ID_MISSING"
        except Exception as exc:
            _request_contract_detail = repr(exc)
    if (
        not _request_exists
        and _logic_mode == "HOLD_RECOVERY"
        and _hold_recovery_applied_contract_ok
    ):
        _request_contract_ok = True
        _request_contract_detail = (
            "LAST_APPLIED:"
            + _applied_request_id
        )

    check("R4_RELEASE_REQUEST_LOGIC", _request_contract_ok, _request_contract_detail)
else:
    check("NO_ACTIVE_RUNTIME_SYNC_REQUEST", not _request_exists)

check("RELEASE_REQUIRES_OVERFIT_GATE", "OVERFIT_PASS_REQUIRED" in release)
check("RELEASE_REQUIRES_LOGIC_GATE", "LOGIC_PASS_REQUIRED" in release)
check("SL_V3_ACTIVE_PATH_EXCLUDED", not (ROOT / "sl.py").exists())
check("TP_V3_ACTIVE_PATH_EXCLUDED", not (ROOT / "tp.py").exists())

# ============================================================
# 결과
# ============================================================

report = {
    "schema": "B_BOT_V3_LOGIC_VALIDATION_V1",
    "ok": all(row["ok"] for row in CHECKS),
    "passed": sum(row["ok"] for row in CHECKS),
    "total": len(CHECKS),
    "checks": CHECKS,
}
print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
raise SystemExit(0 if report["ok"] else 1)
