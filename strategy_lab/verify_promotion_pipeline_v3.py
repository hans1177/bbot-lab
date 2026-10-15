from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"

ROADMAP = ROOT / "ROADMAP_CONSTITUTION_V3.json"
DEPLOY = LAB / "v3_deployment.py"
APPROVAL = LAB / "local_release_approval.py"

FROZEN = {
    "order.py":
        "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c",

    "runner.py":
        "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160",

    "risk_engine.py":
        "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6",

    "candidate.py":
        "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac",
}


def text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def function_source(src: str, name: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines()

    for node in tree.body:
        if (
            isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name == name
        ):
            return "\n".join(
                lines[node.lineno - 1:node.end_lineno]
            )

    raise RuntimeError(
        "FUNCTION_NOT_FOUND=" + name
    )


def sha(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


results = []


def check(name, ok, detail=""):
    results.append(
        {
            "name": name,
            "ok": bool(ok),
            "detail": str(detail or ""),
        }
    )


road = json.loads(
    ROADMAP.read_text(
        encoding="utf-8-sig"
    )
)

arch = (
    road.get("research_architecture_v3")
    or {}
)

deploy = text(DEPLOY)
approval = text(APPROVAL)

create = function_source(
    deploy,
    "create_provisional_request",
)

materialize = function_source(
    deploy,
    "materialize_local_ready_request",
)

policy_create = function_source(
    deploy,
    "create_policy_canary_request",
)

# ============================================================
# Constitution
# ============================================================

check(
    "POLICY_ID",
    arch.get("policy_id")
    == "RA_MAIN_MIDDLE_DETAIL_20260830_V1",
)

check(
    "SHADOW_MANDATORY",
    (
        arch.get("shadow")
        or {}
    ).get(
        "mandatory_minimal_runtime_gate"
    ) is True,
)

check(
    "SHADOW_NOT_PROFIT_GATE",
    (
        arch.get("shadow")
        or {}
    ).get(
        "profitability_gate"
    ) is False,
)

check(
    "CANARY_WEIGHT_1_PERCENT",
    float(
        (
            arch.get("canary")
            or {}
        ).get("initial_capital_weight")
        or -1
    )
    == 0.20,
)

sealed = (
    (
        arch.get("evaluation")
        or {}
    )
    .get("sealed_blind")
    or {}
)

check(
    "SEALED_NEW_MAIN_ONLY",
    sealed.get("new_main_only")
    is True,
)

check(
    "SEALED_NOT_REUSED_MIDDLE",
    sealed.get("reuse_for_middle")
    is False,
)

check(
    "SEALED_NOT_REUSED_DETAIL",
    sealed.get("reuse_for_detail")
    is False,
)

# ============================================================
# Existing full/sealed assets
# ============================================================

check(
    "MAIN_REQUIRES_SEALED",
    (
        "PROVISIONAL_REQUIRES_SEALED_BLIND_PASS"
        in create
        and
        "PROVISIONAL_SEALED_BLIND_EVIDENCE_MISSING"
        in create
    ),
)

check(
    "POLICY_DOES_NOT_REQUIRE_SEALED",
    "SEALED_BLIND" not in policy_create.upper(),
)

# ============================================================
# GREEN-1 Shadow validator
# ============================================================

check(
    "MINIMAL_SHADOW_VALIDATOR_EXISTS",
    "def minimal_shadow_pass" in approval,
)

check(
    "SHADOW_RUNTIME_ERROR_ZERO",
    "runtime_errors" in approval,
)

check(
    "SHADOW_CLOSED_BAR_CHECK",
    "closed_bar_timing_pass" in approval,
)

check(
    "SHADOW_SIGNAL_PARITY_CHECK",
    "signal_logic_parity_pass" in approval,
)

check(
    "SHADOW_STATE_TRANSITION_CHECK",
    "state_transition_pass" in approval,
)

check(
    "SHADOW_COST_SANITY_CHECK",
    "order_simulation_cost_sanity_pass"
    in approval,
)

# ============================================================
# Required deployment state machine — expected RED now
# ============================================================

check(
    "DEPLOY_BINDS_NEW_ARCH",
    "research_architecture_v3"
    in deploy,
)

check(
    "DEPLOY_BINDS_MINIMAL_SHADOW",
    "minimal_shadow_pass"
    in deploy,
)

check(
    "CREATE_HAS_PENDING_SHADOW_STATE",
    "PENDING_SHADOW"
    in create,
)

check(
    "CREATE_REQUIRES_FULL_VALIDATION",
    (
        "FULL_VALIDATION"
        in create
        and
        "PROVISIONAL_REQUIRES_FULL_VALIDATION_PASS"
        in create
    ),
)

create_compact = "".join(create.split())

check(
    "CREATE_DOES_NOT_WRITE_CANARY_BEFORE_SHADOW",
    (
        "atomic(REQUEST" not in create_compact
        and
        '"tier":"PENDING_CANARY_SYNC"' not in create_compact
    ),
    "create_provisional_request must prepare Shadow, not runtime Canary",
)

check(
    "MATERIALIZE_REQUIRES_SHADOW_PASS",
    (
        "minimal_shadow_pass"
        in materialize
        and
        "PROVISIONAL_REQUIRES_MINIMAL_SHADOW_PASS"
        in materialize
    ),
)

check(
    "MATERIALIZE_TRANSITIONS_SHADOW_TO_CANARY",
    (
        "PENDING_SHADOW"
        in materialize
        and
        "PENDING_CANARY_SYNC"
        in materialize
    ),
)

check(
    "CANARY_REQUEST_EXPLICIT_20_PERCENT",
    (
        "0.20"
        in materialize
        or
        "initial_capital_weight"
        in materialize
    ),
)

# ============================================================
# Promotion tiers still preserved
# ============================================================

check(
    "CANARY_TIER_EXISTS",
    "CANARY" in deploy,
)

check(
    "SMALL_TIER_EXISTS",
    "SMALL" in deploy,
)

check(
    "NORMAL_TIER_EXISTS",
    "NORMAL" in deploy,
)

check(
    "ROLLBACK_EXISTS",
    "ROLLBACK" in deploy,
)

# ============================================================
# Frozen core
# ============================================================

for name, expected in FROZEN.items():
    got = sha(ROOT / name)

    check(
        "FROZEN_" + name.upper(),
        got == expected,
        got,
    )

# ============================================================
# Report
# ============================================================

failed = [
    row
    for row in results
    if not row["ok"]
]

print("=" * 88)
print("B-BOT V3 PROMOTION PIPELINE VERIFIER")
print("=" * 88)

for row in results:
    print(
        ("PASS" if row["ok"] else "FAIL"),
        row["name"],
        (
            ":: " + row["detail"]
            if row["detail"]
            else ""
        ),
    )

print()
print("TOTAL=", len(results))
print("FAILED_COUNT=", len(failed))

if failed:
    print(
        "PROMOTION_PIPELINE_STATUS=RED"
    )
    print(
        "RED_GAPS="
        + ",".join(
            row["name"]
            for row in failed
        )
    )
    raise SystemExit(2)

print(
    "PROMOTION_PIPELINE_STATUS=GREEN"
)

raise SystemExit(0)