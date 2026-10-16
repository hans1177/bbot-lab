# 파일명: verify_autonomous_runtime_release.py
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ORDER_HASH = (
    "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c"
)

roadmaps = [
    ROOT / "ROADMAP_CONSTITUTION_V3.json",
    ROOT / "strategy_lab" / "ROADMAP_CONSTITUTION_V3.json",
]

roadmap = next(
    (p for p in roadmaps if p.is_file()),
    None,
)

checks = []

def worker_base_sync_forbidden(source):
    """기존 수집 함수의 읽기 전용 파일 목록만 허용하고 실행 경로의 기본 배포는 금지한다."""
    tree=ast.parse(source)
    references={id(node) for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str) and 'sync_base_to_server.ps1' in node.value}
    allowed=set()
    read_calls={'is_symlink','is_file','stat','read_bytes','RuntimeError','any','search','b64encode','decode','sha256','hexdigest'}
    for function in tree.body:
        if not isinstance(function,ast.FunctionDef) or function.name!='prepare_server':continue
        for loop in ast.walk(function):
            if not isinstance(loop,ast.For):continue
            entries={id(node) for node in ast.walk(loop.iter) if isinstance(node,ast.Constant) and node.value=='sync_base_to_server.ps1'}
            if not entries:continue
            calls=[node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else '' for node in ast.walk(loop) if isinstance(node,ast.Call)]
            if all(name in read_calls for name in calls) and 'read_bytes' in calls:allowed.update(entries)
    return references<=allowed




def check(name, ok, detail=""):
    checks.append(
        (
            name,
            bool(ok),
            str(detail),
        )
    )


check(
    "ROADMAP_EXISTS",
    roadmap is not None,
)

if roadmap:

    obj = json.loads(
        roadmap.read_text(
            encoding="utf-8-sig"
        )
    )

    p = (
        obj.get(
            "autonomous_runtime_release_policy"
        )
        or {}
    )

    check(
        "AUTO_RELEASE_POLICY_ID",
        p.get("policy_id")
        == "AUTONOMOUS_RULE_GATED_RUNTIME_RELEASE_V1",
    )

    check(
        "AUTO_RELEASE_ENABLED",
        p.get("enabled") is True,
    )

    check(
        "NO_PER_RELEASE_USER_APPROVAL",
        p.get(
            "per_release_user_approval_required"
        ) is False,
    )

    check(
        "MIGRATION_COMPLETE",
        p.get("migration_complete") is True,
    )

    check(
        "ALLOWED_TIERS",
        p.get("allowed_tiers")
        == [
            "CANARY",
            "SMALL",
            "NORMAL",
            "ROLLBACK",
        ],
    )

    forbidden = (
        p.get("forbidden")
        or {}
    )

    check(
        "NO_BASE_AUTO_DEPLOY",
        forbidden.get(
            "base_code_auto_deploy"
        ) is True,
    )

    check(
        "NO_STABLE_KERNEL_MUTATION",
        forbidden.get(
            "stable_kernel_mutation"
        ) is True,
    )

    check(
        "ORDER_MUTATION_FORBIDDEN",
        forbidden.get(
            "order_py_mutation"
        ) is True,
    )

    check(
        "RESEARCH_TREE_SERVER_FORBIDDEN",
        forbidden.get(
            "research_tree_server_deploy"
        ) is True,
    )


order = ROOT / "order.py"

actual = hashlib.sha256(
    order.read_bytes()
).hexdigest()

check(
    "ORDER_FROZEN_HASH",
    actual == ORDER_HASH,
    actual,
)

worker = (
    ROOT
    / "strategy_lab"
    / "v3_auto_runtime_release.py"
)

check(
    "AUTO_RELEASE_WORKER_EXISTS",
    worker.is_file(),
)

if worker.is_file():

    src = worker.read_text(
        encoding="utf-8-sig"
    )

    required = (
        "strategy_runtime_sync.ps1",
        "sync_from_server.ps1",
        "verify_v3.py",
        "verify_overfit_validation.py",
        "verify_logic_validation.py",
        "verify_r4_sync.py",
        "verify_open_position_version_policy.py",
        "RELEASE_CANDIDATE",
        "SERVER_PUSH_HOLD.flag",
        "autonomous_rule_passed",
        "core_mutation",
        "CANARY",
        "SMALL",
        "NORMAL",
        "ROLLBACK",
    )

    for token in required:

        check(
            "WORKER_TOKEN_" + token,
            token in src,
        )

    check(
        "WORKER_NO_BASE_SYNC",
        worker_base_sync_forbidden(src),
    )

    check(
        "WORKER_NO_DIRECT_SSH",
        "ssh.exe" not in src.lower()
        and "scp.exe" not in src.lower(),
    )


failed = [
    name
    for name, ok, _ in checks
    if not ok
]

for name, ok, detail in checks:

    print(
        name
        + "="
        + (
            "PASS"
            if ok
            else "FAIL"
        )
        + (
            (" " + detail)
            if detail
            else ""
        )
    )

print(
    "AUTONOMOUS_RELEASE_VERIFY="
    + str(
        len(checks)
        - len(failed)
    )
    + "/"
    + str(len(checks))
)

if failed:

    print(
        "FAILED="
        + ",".join(failed)
    )

    sys.exit(1)

print(
    "AUTONOMOUS_RELEASE_VERIFY=PASS"
)
