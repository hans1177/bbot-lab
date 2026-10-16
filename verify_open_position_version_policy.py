from pathlib import Path
import json
import sys

ROOT = Path(r"C:\my\bottest")

roadmap = json.loads(
    (ROOT / "ROADMAP_CONSTITUTION_V3.json").read_text(
        encoding="utf-8-sig"
    )
)

runner = (ROOT / "runner.py").read_text(
    encoding="utf-8-sig"
)

runtime = (ROOT / "strategy_runtime.py").read_text(
    encoding="utf-8-sig"
)

deploy = (
    ROOT / "strategy_lab" / "v3_deployment.py"
).read_text(
    encoding="utf-8-sig"
)

checks = []

def ck(name, ok):
    checks.append((name, bool(ok)))
    print(f"{name}={'PASS' if ok else 'FAIL'}")

policy = roadmap.get(
    "open_position_version_policy"
) or {}

ck(
    "ROADMAP_ACTIVE_RUNTIME_VERSION",
    policy.get("policy_id")
    == "ACTIVE_RUNTIME_VERSION_FOLLOWS_PROMOTED_NORMAL_V1"
)

ck(
    "ROADMAP_ENTRY_FACT_IMMUTABLE",
    policy.get("entry_facts_immutable") is True
)

ck(
    "ROADMAP_NORMAL_UPDATES_OPEN",
    policy.get(
        "normal_promotion_updates_open_positions"
    ) is True
)

ck(
    "ROADMAP_NO_CLOSE_REOPEN",
    policy.get(
        "close_reopen_for_version_update"
    ) is False
)

ck(
    "RUNTIME_ACTIVE_OWNER",
    "ACTIVE_RUNTIME_OWNER" in runtime
)

ck(
    "RUNNER_ACTIVE_VERSION_APPLY",
    "NORMAL_VERSION_APPLY_OPEN_POSITION" in runner
)

ck(
    "RUNNER_MIXED_VERSION_MARK",
    "mixed_version_lifecycle" in runner
)

ck(
    "RUNNER_VERSION_HISTORY",
    "runtime_version_history" in runner
)

ck(
    "ENTRY_FACTS_NOT_REWRITTEN",
    "entry_facts_immutable" in runner
)

ck(
    "MIXED_EXCLUDED_FROM_CANARY",
    "mixed_version_lifecycle" in deploy
    and "PURE_CANARY_EXCLUDES_MIXED" in deploy
)

failed = [
    name
    for name, ok in checks
    if not ok
]

print("========================================")
print(f"PASSED={len(checks)-len(failed)}/{len(checks)}")
print("FAILED=" + ",".join(failed))
print("SERVER_CHANGED=0")

if failed:
    print("OPEN_POSITION_VERSION_POLICY=EXPECTED_FAIL_BEFORE_IMPLEMENTATION")
    sys.exit(2)

print("OPEN_POSITION_VERSION_POLICY=PASS")
