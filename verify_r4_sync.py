#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: verify_r4_sync.py
"""R4 로컬↔서버 동기화 헌법/구현 검증기. 서버 쓰기나 실주문은 수행하지 않는다."""

# ================= 임포트 =================
import hashlib
import importlib.util
import json
import re
from pathlib import Path

# ================= 설정 =================
ROOT = Path(__file__).resolve().parent
EXPECTED_FROZEN = {
    "runner.py": "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160",
    "order.py": "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c",
    "risk_engine.py": "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6",
    "candidate.py": "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def source(name):
    return (ROOT / name).read_text(encoding="utf-8-sig", errors="strict")


def load_merger():
    path = ROOT / "strategy_lab" / "v3_runtime_config_merge.py"
    spec = importlib.util.spec_from_file_location("_r4_sync_merger", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ================= 메인 =================
def main():
    constitution = load_json(ROOT / "ROADMAP_CONSTITUTION_V3.json")
    separation = constitution.get("local_server_separation_policy") or {}
    pipeline = constitution.get("continuous_operation_pipeline") or {}
    auto_release = constitution.get("autonomous_runtime_release_policy") or {}

    sync_text = source("strategy_runtime_sync.ps1")
    pull_text = source("sync_from_server.ps1")
    base_text = source("sync_base_to_server.ps1")
    enable_text = source("ENABLE_SERVER_SYNC_AFTER_LOCAL_PASS.ps1")
    auto_text = source("strategy_lab/v3_auto_runtime_release.py")
    merger_text = source("strategy_lab/v3_runtime_config_merge.py")
    logic_text = source("verify_logic_validation.py")

    frozen = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in EXPECTED_FROZEN
    }

    checks = {
        "frozen_core": frozen == EXPECTED_FROZEN,
        "constitution_runtime_unit_strategy_instance": pipeline.get("runtime_unit") == "STRATEGY_INSTANCE",
        "constitution_validated_only": ((separation.get("local_to_server_sync") or {}).get("validated_only") is True),
        "constitution_research_payload_forbidden": ((separation.get("local_to_server_sync") or {}).get("strategy_lab_forbidden") is True),
        "constitution_pre_pull": ((pipeline.get("server_to_local") or {}).get("pre_release_forced_pull") is True),
        "constitution_post_pull": ((pipeline.get("server_to_local") or {}).get("post_release_verification_pull") is True),
        "constitution_exact_instance_required": "EXACT_STRATEGY_INSTANCE_ID" in (auto_release.get("requirements") or []),
        "sync_approved_only": "APPROVED_FOR_SERVER_RELEASE_REQUIRED" in sync_text,
        "sync_canary_only_local": "LOCAL_RELEASE_TIER_MUST_BE_CANARY" in sync_text,
        "sync_exact_instance": "EXACT_STRATEGY_INSTANCE_ID_REQUIRED" in sync_text,
        "sync_exact_constitution_hash": "CONSTITUTION_HASH_MISMATCH" in sync_text and "v3_governance" in sync_text,
        "sync_request_bytes_stable_across_pull": "REQUEST_BYTES_CHANGED_AFTER_PRE_RELEASE_PULL" in sync_text,
        "sync_policy_bundle_kind": "STRATEGY_INSTANCE_POLICY_BUNDLE" in sync_text,
        "sync_pre_post_pull": "PreRelease" in sync_text and "PostRelease" in sync_text,
        "sync_no_research_payload": "research_knowledge_runtime.json" not in sync_text,
        "sync_no_service_restart": "systemctl restart" not in sync_text,
        "sync_legacy_writer_guard": "LEGACY_RUNNER_STILL_ACTIVE" in sync_text,
        "sync_remote_idempotency": "R4_RUNTIME_SYNC_DUPLICATE=YES" in sync_text,
        "sync_server_activation_authorized": "server_activation_authorized" in sync_text,
        "pull_read_only_markers": all(x in pull_text for x in ("SERVER_WRITE=0", "SERVICE_RESTART=0", "POSITION_MUTATION=0")),
        "pull_server_strategy_config_snapshot": "server_runtime\\strategy_config.json" in pull_text or '"$RUNTIME\\strategy_config.json"' in pull_text,
        "pull_receipt": "B_BOT_R4_SERVER_PULL_RECEIPT_V1" in pull_text,
        "pull_live_sqlite_not_scp": (
            any(
                all("! -name '" + pattern + "'" in pull_text for pattern in patterns)
                for patterns in (
                    ("*.db", "*.db-*", "*.db.*"),
                    ("history.db", "history.db-*", "history.db.*"),
                )
            )
            and all(marker in pull_text for marker in (
                '"tools\\server_history_dump_ro.py"',
                "$rawDb = Get-Content $dumpHelper -Raw | & ssh",
                '"$ROOT\\tools\\rebuild_history_snapshot.py" $dumpFile $dbPath',
            ))
        ),
        "base_auto_deploy_forbidden": "BASE_CODE_AUTO_DEPLOY=FORBIDDEN_R4" in base_text,
        "base_frozen_sync_forbidden": "FROZEN_CORE_SYNC_FORBIDDEN" in base_text,
        "base_strategy_payload_forbidden": "STRATEGY_PAYLOAD_GENERAL_SYNC=NO" in base_text,
        "base_no_service_restart": "systemctl restart" not in base_text,
        "base_r4_cutover_artifacts_maintainable": '"r4_cutover.py"' in base_text and '"r4-runtime.service"' in base_text,
        "base_single_persistent_backup": "B_BOT_PRE_R4_MAINTENANCE_BACKUP.zip" in base_text and "BASE_MAINTENANCE_BACKUP_COUNT=1" in base_text,
        "base_maintenance_rollback": "trap rollback ERR" in base_text and "rollback()" in base_text,
        "enable_no_live_cutover": "LIVE_CUTOVER=NOT_PERFORMED" in enable_text,
        "enable_overfit_logic_fail_closed": all(x in enable_text for x in ("OVERFIT_PASS_REQUIRED", "LOGIC_PASS_REQUIRED")),
        "enable_release_candidate_verify_mode": "B_BOT_V3_LOGIC_VERIFY_MODE" in enable_text and "RELEASE_CANDIDATE" in enable_text,
        "logic_release_candidate_request_contract": "R4_RELEASE_REQUEST_LOGIC" in logic_text and "ACTIVE_RUNTIME_SYNC_REQUEST_REQUIRED" in logic_text,
        "auto_release_runner_log": '/ "runner.log"' in auto_text,
        "auto_release_r4_kind": '"STRATEGY_INSTANCE_POLICY_BUNDLE"' in auto_text,
        "auto_release_canary_boundary": "R4_LOCAL_RELEASE_BOUNDARY_V1" in auto_text,
        "auto_release_overfit_gate_exec": "VERIFY_OVERFIT" in auto_text and "OVERFIT_VALIDATION" in auto_text,
        "auto_release_logic_gate_exec": "VERIFY_LOGIC" in auto_text and "RELEASE_CANDIDATE" in auto_text,
        "auto_release_r4_sync_gate_exec": "VERIFY_R4_SYNC" in auto_text and "R4_SYNC_VALIDATION" in auto_text,
        "auto_release_dry_run_verifiers": 'os.environ["RUNNER_DRY_RUN"] = "1"' in auto_text,
        "merger_r4_activation": "R4_APPROVED_INSTANCE_ACTIVATION=PASS" in merger_text,
        "merger_legacy_policy_kind_removed": 'elif version_kind == "POLICY"' not in merger_text,
    }

    # NORMAL_BASE_FILES에 Frozen Core / strategy_config가 들어가지 않는지 별도 검증.
    match = re.search(r"\$NORMAL_BASE_FILES\s*=\s*@\((.*?)\n\)", base_text, flags=re.S)
    normal_block = match.group(1) if match else ""
    checks["base_normal_list_exists"] = bool(normal_block)
    checks["base_normal_excludes_frozen_core"] = all(f'"{name}"' not in normal_block for name in EXPECTED_FROZEN)
    checks["base_normal_excludes_strategy_config"] = "strategy_config.json" not in normal_block
    checks["base_normal_excludes_research"] = "strategy_lab" not in normal_block and "verify_" not in normal_block

    # 실제 merger 동작 검증.
    merger = load_merger()
    policy_snapshot = {
        "strategy_policy_bundle_version": "SPB_TEST_V1",
        "behavior_policy": {"trailing": {"enabled": False}},
    }
    active = {
        "schema": "STRATEGY_CONFIG_V3",
        "roadmap_constitution": constitution.get("constitution_version"),
        "frames": {
            "1H": {"strategy_version": "SV_OLD", "strategies": ["OLD"], "deployment_tier": "CANARY"},
            "6H": {"strategy_version": "SV_KEEP", "strategies": ["KEEP"], "deployment_tier": "CANARY"},
        },
        "strategy_registry": {"OLD": {"file": "old.py"}, "KEEP": {"file": "keep.py"}},
        "policy_versions": {"EXIT": "PV_EXIT_OLD"},
        "trailing": {"enabled": False},
        "r4_runtime": {"capacity_units": {"BTCUSDT": 100}, "strategy_instances": {}},
    }
    pending_strategy = json.loads(json.dumps(active))
    pending_strategy["frames"]["1H"] = {"strategy_version": "SV_NEW", "strategies": ["NEW"], "deployment_tier": "CANARY"}
    pending_strategy["strategy_registry"]["NEW"] = {"file": "new.py"}
    pending_strategy["r4_runtime"]["strategy_instances"]["SI_TEST"] = {
        "strategy_id": "NEW",
        "timeframe": "1H",
        "exact_strategy_version": "SV_NEW",
        "strategy_policy_bundle_version": "SPB_TEST_V1",
        "policy_snapshot": policy_snapshot,
        "tier": "CANARY",
        "approved_for_server_release": True,
    }
    merged_strategy = merger.merge_strategy(active, pending_strategy, "1H", "NEW", "SV_NEW", "CANARY", "SI_TEST")
    si = merged_strategy["r4_runtime"]["strategy_instances"]["SI_TEST"]
    checks["merge_strategy_preserves_other_frame"] = merged_strategy["frames"]["6H"] == active["frames"]["6H"]
    checks["merge_strategy_preserves_capacity"] = merged_strategy["r4_runtime"]["capacity_units"] == {"BTCUSDT": 100}
    checks["merge_strategy_authorizes_exact_instance"] = si.get("server_activation_authorized") is True and si.get("qualification") == "CANARY"

    pending_policy = json.loads(json.dumps(active))
    pending_policy["policy_versions"]["EXIT"] = "PV_EXIT_NEW"
    pending_policy["trailing"] = {"enabled": True, "start_pct": 1.0, "callback_pct": 0.5}
    for iid, strategy_id, version in (("SI_A", "A", "SV_A"), ("SI_B", "B", "SV_B")):
        pending_policy["r4_runtime"]["strategy_instances"][iid] = {
            "strategy_id": strategy_id,
            "timeframe": "1H",
            "exact_strategy_version": version,
            "strategy_policy_bundle_version": "SPB_EXIT_NEW",
            "policy_snapshot": {
                "strategy_policy_bundle_version": "SPB_EXIT_NEW",
                "behavior_policy": {"trailing": pending_policy["trailing"]},
            },
            "tier": "CANARY",
            "approved_for_server_release": True,
        }
    merged_policy = merger.merge_strategy_instance_policy_bundle(
        active, pending_policy, "EXIT", "PV_EXIT_NEW", "CANARY", ["SI_A", "SI_B"]
    )
    checks["merge_policy_preserves_frames"] = merged_policy["frames"] == active["frames"]
    checks["merge_policy_updates_exact_track"] = merged_policy["policy_versions"]["EXIT"] == "PV_EXIT_NEW"
    checks["merge_policy_authorizes_all_targets"] = all(
        merged_policy["r4_runtime"]["strategy_instances"][iid].get("server_activation_authorized") is True
        for iid in ("SI_A", "SI_B")
    )

    bad_policy = json.loads(json.dumps(pending_policy))
    bad_policy["account_mode"] = {"enabled": False}
    rejected = False
    try:
        merger.merge_strategy_instance_policy_bundle(
            active, bad_policy, "EXIT", "PV_EXIT_NEW", "CANARY", ["SI_A", "SI_B"]
        )
    except RuntimeError as exc:
        rejected = "POLICY_PATCH_OUTSIDE_ALLOWED_SCOPE" in str(exc)
    checks["merge_policy_rejects_out_of_scope_patch"] = rejected

    report = {
        "schema": "B_BOT_R4_SYNC_VERIFY_V1",
        "ok": all(checks.values()),
        "checks": checks,
        "frozen_core_sha256": frozen,
        "server_write": False,
        "service_restart": False,
        "real_order": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
