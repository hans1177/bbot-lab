# 파일명: v3_runtime_config_merge.py
from __future__ import annotations

# ================= 임포트 =================
import argparse
import copy
import json
import os
from pathlib import Path

# ================= 상수 =================
POLICY_ALLOWED_TOP_LEVEL = {
    "ENTRY_RISK": {"v3_decision"},
    "EXIT": {"trailing", "opposite_exit"},
    "REENTRY": {"state_reentry", "opposite_reopen"},
    "SYMBOL": {"v3_decision"},
    "PORTFOLIO": {"v3_decision"},
    "REGIME": {"v3_decision"},
    "EXECUTION": {"v3_decision"},
    "DIRECTION": {"v3_decision"},
}


# ================= 유틸 =================
def fail(reason):
    raise RuntimeError("GREEN6_CONFIG_MERGE_FAIL:" + str(reason))


def load_json(path):
    path = Path(path)
    if not path.is_file():
        fail("FILE_MISSING=" + str(path))
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        fail("JSON_NOT_OBJECT")
    return obj


def atomic_json(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _pending_instance(pending, instance_id):
    instance_id = str(instance_id or "").strip()
    row = (
        ((pending.get("r4_runtime") or {}).get("strategy_instances") or {})
        .get(instance_id)
    )
    if not isinstance(row, dict):
        fail("R4_STRATEGY_INSTANCE_PENDING_ROW_MISSING=" + instance_id)
    declared_id = str(row.get("strategy_instance_id") or instance_id).strip()
    if declared_id != instance_id:
        fail("R4_STRATEGY_INSTANCE_ID_BINDING_ERROR=" + instance_id)
    if row.get("approved_for_server_release") is not True:
        fail("R4_STRATEGY_INSTANCE_NOT_APPROVED=" + instance_id)
    if str(row.get("tier") or "").upper() != "CANARY":
        fail("R4_STRATEGY_INSTANCE_NOT_CANARY=" + instance_id)
    if not str(row.get("strategy_id") or "").strip():
        fail("R4_STRATEGY_INSTANCE_STRATEGY_ID_MISSING=" + instance_id)
    if not str(row.get("timeframe") or "").strip():
        fail("R4_STRATEGY_INSTANCE_TIMEFRAME_MISSING=" + instance_id)
    if not str(row.get("exact_strategy_version") or "").strip():
        fail("R4_STRATEGY_INSTANCE_EXACT_VERSION_MISSING=" + instance_id)
    if not str(row.get("strategy_policy_bundle_version") or "").strip():
        fail("R4_STRATEGY_INSTANCE_POLICY_BUNDLE_MISSING=" + instance_id)
    if not isinstance(row.get("policy_snapshot"), dict) or not row.get("policy_snapshot"):
        fail("R4_STRATEGY_INSTANCE_POLICY_SNAPSHOT_MISSING=" + instance_id)
    return copy.deepcopy(row)


def _activate_instance(result, pending, instance_id):
    """새 exact instance만 신규 진입 가능하게 하고 이전 instance의 open leg는 그대로 보존한다."""
    row = _pending_instance(pending, instance_id)
    row["strategy_instance_id"] = str(instance_id)
    row["tier"] = "CANARY"
    row["qualification"] = "CANARY"
    row["capital_weight"] = 0.20
    row["enabled"] = True
    row["entry_enabled"] = True
    row["server_activation_authorized"] = True
    row["rollback_requested_to_local"] = False
    runtime = result.setdefault("r4_runtime", {})
    if not isinstance(runtime, dict):
        fail("R4_RUNTIME_INVALID")
    rows = runtime.setdefault("strategy_instances", {})
    if not isinstance(rows, dict):
        fail("R4_STRATEGY_INSTANCES_INVALID")

    strategy_id = str(row.get("strategy_id") or "").upper()
    timeframe = str(row.get("timeframe") or "").upper()
    for current_id, current in list(rows.items()):
        if str(current_id) == str(instance_id) or not isinstance(current, dict):
            continue
        if (
            str(current.get("strategy_id") or "").upper() == strategy_id
            and str(current.get("timeframe") or "").upper() == timeframe
            and current.get("entry_enabled", current.get("enabled", False)) is True
        ):
            current = copy.deepcopy(current)
            current["entry_enabled"] = False
            current["superseded_by_strategy_instance_id"] = str(instance_id)
            rows[str(current_id)] = current

    rows[str(instance_id)] = row
    return row


# ================= 전략 병합 =================
def merge_strategy(
    active,
    pending,
    timeframe,
    strategy,
    strategy_version,
    tier,
    strategy_instance_id,
):
    """검증된 exact Strategy Instance 한 개만 CANARY Runtime에 반영한다."""
    timeframe = str(timeframe or "").upper()
    strategy = str(strategy or "")
    strategy_version = str(strategy_version or "")
    tier = str(tier or "").upper()
    strategy_instance_id = str(strategy_instance_id or "").strip()

    if not timeframe:
        fail("TIMEFRAME_MISSING")
    if tier != "CANARY":
        fail("LOCAL_RELEASE_TIER_MUST_BE_CANARY")
    if not strategy_instance_id:
        fail("STRATEGY_INSTANCE_ID_MISSING")

    active_frames = active.get("frames") or {}
    pending_frames = pending.get("frames") or {}
    if not isinstance(active_frames, dict):
        fail("ACTIVE_FRAMES_INVALID")
    if not isinstance(pending_frames, dict):
        fail("PENDING_FRAMES_INVALID")

    target = pending_frames.get(timeframe)
    if not isinstance(target, dict):
        fail("PENDING_TARGET_MISSING=" + timeframe)

    # FRAME_REPRESENTATIVE_PEER_CANARY_V1
    #
    # A peer Strategy Instance is added to r4_runtime.strategy_instances
    # without transferring representative frame ownership.
    #
    # Peer mode is allowed only when the pending target frame is byte-for-
    # byte equivalent at the object level to the currently active frame.
    # Otherwise the original frame/version binding contract remains strict.
    active_target = active_frames.get(timeframe)
    peer_add = (
        isinstance(active_target, dict)
        and target == active_target
    )

    pending_instance = _pending_instance(
        pending,
        strategy_instance_id,
    )

    if peer_add:
        if (
            str(
                pending_instance.get("timeframe")
                or ""
            ).upper()
            != timeframe
        ):
            fail("PEER_STRATEGY_INSTANCE_TIMEFRAME_MISMATCH")

        if (
            not strategy_version
            or str(
                pending_instance.get(
                    "exact_strategy_version"
                )
                or ""
            )
            != strategy_version
        ):
            fail("PEER_STRATEGY_VERSION_BINDING_ERROR")

        if (
            strategy
            and str(
                pending_instance.get("strategy_id")
                or ""
            ).upper()
            != strategy.upper()
        ):
            fail("PEER_STRATEGY_BINDING_ERROR")

    else:
        target_version = str(
            target.get("strategy_version")
            or ""
        )

        if (
            not strategy_version
            or target_version != strategy_version
        ):
            fail("STRATEGY_VERSION_BINDING_ERROR")

    # MULTI_STRATEGY_BUNDLE_BINDING_V1
    strategy_components = [
        str(x).upper()
        for x in (target.get("strategies") or [])
        if str(x).strip()
    ]
    names = set(strategy_components)
    direct = str(target.get("strategy") or "").upper()
    bundle = "+".join(strategy_components)

    if (
        not peer_add
        and strategy
        and strategy.upper() not in names
        and strategy.upper() != direct
        and strategy.upper() != bundle
    ):
        fail("STRATEGY_BINDING_ERROR")

    result = copy.deepcopy(active)
    result.setdefault("frames", {})[timeframe] = copy.deepcopy(target)

    active_registry = active.get("strategy_registry") or {}
    pending_registry = pending.get("strategy_registry") or {}
    if not isinstance(active_registry, dict):
        fail("ACTIVE_STRATEGY_REGISTRY_INVALID")
    if not isinstance(pending_registry, dict):
        fail("PENDING_STRATEGY_REGISTRY_INVALID")

    multi_bundle = bool(
        strategy
        and len(strategy_components) > 1
        and strategy.upper() == bundle
    )
    result_registry = result.setdefault("strategy_registry", {})
    if not isinstance(result_registry, dict):
        fail("RESULT_STRATEGY_REGISTRY_INVALID")

    if multi_bundle:
        for component in strategy_components:
            matches = [k for k in pending_registry if str(k).upper() == component]
            if len(matches) != 1:
                fail("PENDING_MULTI_STRATEGY_REGISTRY_BINDING_ERROR")
            row = pending_registry.get(matches[0])
            if not isinstance(row, dict) or not str(row.get("file") or "").strip():
                fail("PENDING_MULTI_STRATEGY_REGISTRY_FILE_MISSING")
            result_registry[component] = copy.deepcopy(row)
    elif strategy:
        matches = [k for k in pending_registry if str(k).upper() == strategy.upper()]
        if len(matches) != 1:
            fail("PENDING_STRATEGY_REGISTRY_BINDING_ERROR")
        row = pending_registry.get(matches[0])
        if not isinstance(row, dict):
            fail("PENDING_STRATEGY_REGISTRY_ROW_INVALID")
        if not str(row.get("file") or "").strip():
            fail("PENDING_STRATEGY_REGISTRY_FILE_MISSING")
        result_registry[strategy] = copy.deepcopy(row)

    # preserve_other_frames / non_target_frames_unchanged
    for frame, value in active_frames.items():
        if str(frame).upper() == timeframe:
            continue
        if result["frames"].get(frame) != value:
            fail("NON_TARGET_FRAME_CHANGED=" + str(frame))

    instance = _activate_instance(result, pending, strategy_instance_id)
    if str(instance.get("timeframe") or "").upper() != timeframe:
        fail("R4_STRATEGY_INSTANCE_TIMEFRAME_MISMATCH")
    if str(instance.get("exact_strategy_version") or "") != strategy_version:
        fail("R4_STRATEGY_INSTANCE_VERSION_MISMATCH")
    if strategy:
        iid_strategy = str(instance.get("strategy_id") or "").upper()
        if iid_strategy not in {strategy.upper(), direct, bundle} and iid_strategy not in names:
            fail("R4_STRATEGY_INSTANCE_STRATEGY_MISMATCH")

    return result


# ================= 정책 번들 병합 =================
def merge_strategy_instance_policy_bundle(
    active,
    pending,
    policy_track,
    policy_version,
    tier,
    strategy_instance_ids,
):
    """Shadow PASS된 Strategy Instance들의 새 Policy Bundle만 CANARY로 활성화한다."""
    policy_track = str(policy_track or "").upper()
    policy_version = str(policy_version or "")
    tier = str(tier or "").upper()
    instance_ids = [str(x).strip() for x in strategy_instance_ids if str(x).strip()]

    if policy_track not in POLICY_ALLOWED_TOP_LEVEL:
        fail("POLICY_TRACK_INVALID")
    if not policy_version:
        fail("POLICY_VERSION_MISSING")
    if tier != "CANARY":
        fail("LOCAL_RELEASE_TIER_MUST_BE_CANARY")
    if not instance_ids or len(instance_ids) != len(set(instance_ids)):
        fail("STRATEGY_INSTANCE_IDS_INVALID")

    pending_versions = pending.get("policy_versions") or {}
    if not isinstance(pending_versions, dict):
        fail("POLICY_VERSIONS_INVALID")
    if str(pending_versions.get(policy_track) or "") != policy_version:
        fail("POLICY_VERSION_BINDING_ERROR")

    # 정책 후보가 허용 범위 밖의 기존 설정을 바꾸면 fail closed.
    structural = {
        "frames", "strategy_registry", "r4_runtime", "policy_versions",
        "roadmap_constitution",
    }
    changed = {
        key
        for key in set(active) | set(pending)
        if active.get(key) != pending.get(key) and key not in structural
    }
    forbidden = changed - POLICY_ALLOWED_TOP_LEVEL[policy_track]
    if forbidden:
        fail("POLICY_PATCH_OUTSIDE_ALLOWED_SCOPE=" + ",".join(sorted(forbidden)))

    result = copy.deepcopy(active)
    for key in POLICY_ALLOWED_TOP_LEVEL[policy_track]:
        if key in pending and active.get(key) != pending.get(key):
            result[key] = copy.deepcopy(pending.get(key))

    versions = result.setdefault("policy_versions", {})
    if not isinstance(versions, dict):
        fail("RESULT_POLICY_VERSIONS_INVALID")
    versions[policy_track] = policy_version

    for instance_id in instance_ids:
        _activate_instance(result, pending, instance_id)

    # Policy Bundle release never rewrites legacy frame ownership.
    if result.get("frames") != active.get("frames"):
        fail("POLICY_BUNDLE_MUST_NOT_CHANGE_FRAMES")

    return result


# ================= 메인 =================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--active", required=True)
    parser.add_argument("--pending", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version-kind", required=True)
    parser.add_argument("--timeframe", default="")
    parser.add_argument("--strategy", default="")
    parser.add_argument("--strategy-version", default="")
    parser.add_argument("--policy-track", default="")
    parser.add_argument("--policy-version", default="")
    parser.add_argument("--tier", default="")
    parser.add_argument("--strategy-instance-id", action="append", default=[])
    args = parser.parse_args()

    active = load_json(args.active)
    pending = load_json(args.pending)
    version_kind = str(args.version_kind).upper()

    if version_kind == "STRATEGY":
        if len(args.strategy_instance_id) != 1:
            fail("STRATEGY_RELEASE_REQUIRES_ONE_INSTANCE")
        result = merge_strategy(
            active,
            pending,
            args.timeframe,
            args.strategy,
            args.strategy_version,
            args.tier,
            args.strategy_instance_id[0],
        )
    elif version_kind == "STRATEGY_INSTANCE_POLICY_BUNDLE":
        result = merge_strategy_instance_policy_bundle(
            active,
            pending,
            args.policy_track,
            args.policy_version,
            args.tier,
            args.strategy_instance_id,
        )
    else:
        fail("VERSION_KIND_INVALID")

    atomic_json(args.output, result)
    print("FRAME_SCOPED_CONFIG_MERGE=PASS")
    print("R4_APPROVED_INSTANCE_ACTIVATION=PASS")


if __name__ == "__main__":
    main()
