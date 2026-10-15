from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(r"C:\my\bottest")

ENGINE = ROOT / "strategy_lab" / "v3_shadow_engine.py"
AUTO = ROOT / "strategy_lab" / "v3_auto_runtime_release.py"
DEPLOY = ROOT / "strategy_lab" / "v3_deployment.py"
APPROVAL = ROOT / "strategy_lab" / "local_release_approval.py"
CANARY_VERIFY = ROOT / "verify_canary_runner_lifecycle.py"
ROADMAP = ROOT / "ROADMAP_CONSTITUTION_V3.json"

EXPECTED_DEPLOY_SHA = (
    "b02d7996fdbbf1693cb217f2d22161f68c47cb9dc24b8ed7cf24b7c21b3ef38e"
)

EXPECTED_AUTO_SHA = (
    "fdbcf9127f6fd1748d7afa9738234198146a7b2c066a66d270cf06fb76339894"
)

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


def read(path: Path) -> str:
    if not path.is_file():
        return ""

    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def sha(path: Path) -> str:
    if not path.is_file():
        return ""

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def functions(src: str) -> dict[str, ast.AST]:
    if not src:
        return {}

    tree = ast.parse(src)

    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }


def fsrc(src: str, name: str) -> str:
    if not src:
        return ""

    tree = ast.parse(src)
    lines = src.splitlines()

    for node in tree.body:
        if (
            isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and node.name == name
        ):
            return "\n".join(
                lines[
                    node.lineno - 1:
                    node.end_lineno
                ]
            )

    return ""


rows = []


def check(name, ok, detail=""):
    rows.append(
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

shadow_policy = (
    arch.get("shadow")
    or {}
)

engine = read(ENGINE)
auto = read(AUTO)
deploy = read(DEPLOY)
approval = read(APPROVAL)
canary_verify = read(CANARY_VERIFY)


# ============================================================
# Existing GREEN-2 foundation
# ============================================================

check(
    "GREEN2_DEPLOY_SHA",
    sha(DEPLOY) == EXPECTED_DEPLOY_SHA,
    sha(DEPLOY),
)

check(
    "AUTO_RELEASE_BASE_SHA",
    sha(AUTO) == EXPECTED_AUTO_SHA,
    sha(AUTO),
)

check(
    "CONSTITUTION_SHADOW_REQUIRED",
    shadow_policy.get(
        "mandatory_minimal_runtime_gate"
    ) is True,
)

check(
    "CONSTITUTION_SHADOW_NOT_PROFIT_GATE",
    shadow_policy.get(
        "profitability_gate"
    ) is False,
)

create = fsrc(
    deploy,
    "create_provisional_request",
)

materialize = fsrc(
    deploy,
    "materialize_local_ready_request",
)

check(
    "GREEN2_PENDING_SHADOW",
    "PENDING_SHADOW" in create,
)

check(
    "GREEN2_NO_CANARY_RUNTIME_BEFORE_SHADOW",
    "atomic(REQUEST"
    not in "".join(create.split()),
)

check(
    "GREEN2_MATERIALIZE_REQUIRES_SHADOW",
    (
        "minimal_shadow_pass"
        in materialize
        and
        "PENDING_CANARY_SYNC"
        in materialize
    ),
)

check(
    "APPROVAL_MINIMAL_SHADOW_VALIDATOR",
    "def minimal_shadow_pass"
    in approval,
)


# ============================================================
# GREEN-3 producer — expected RED now
# ============================================================

check(
    "SHADOW_ENGINE_EXISTS",
    ENGINE.is_file(),
)

engine_funcs = functions(
    engine
) if engine else {}

check(
    "SHADOW_ENGINE_PRODUCER_EXISTS",
    "produce_shadow_evidence"
    in engine_funcs,
)

check(
    "SHADOW_ENGINE_PENDING_PROCESSOR_EXISTS",
    "process_pending_shadows"
    in engine_funcs,
)

check(
    "SHADOW_ENGINE_SCHEMA",
    "B_BOT_V3_MINIMAL_SHADOW_EVIDENCE_V1"
    in engine,
)


# ============================================================
# Exact evidence contract
# ============================================================

required_fields = (
    "runtime_errors",
    "closed_bar_timing_pass",
    "signal_logic_parity_pass",
    "state_transition_pass",
    "order_simulation_cost_sanity_pass",
    "complete_lifecycles",
    "replay_dryrun_state_transition_pass",
    "live_closed_bar_confirmation_pass",
)

for field in required_fields:
    check(
        "SHADOW_FIELD_"
        + field.upper(),
        field in engine,
    )

check(
    "SHADOW_VERSION_BINDING",
    (
        "strategy_version"
        in engine
        and
        "version_id"
        in engine
    ),
)

check(
    "SHADOW_TIMEFRAME_BINDING",
    "timeframe" in engine,
)

check(
    "SHADOW_STRATEGY_BINDING",
    "strategy" in engine,
)

check(
    "SHADOW_WRITES_VALIDATION_EVIDENCE",
    (
        "validation"
        in engine
        and
        "shadow.json"
        in engine
    ),
)

check(
    "SHADOW_LOCAL_ROLE",
    (
        "LOCAL_RESEARCH"
        in engine
        and
        "real_order"
        in engine
        and
        "server_mutation"
        in engine
    ),
)


# ============================================================
# Fail-closed / no fake PASS
# ============================================================

producer = fsrc(
    engine,
    "produce_shadow_evidence",
)

processor = fsrc(
    engine,
    "process_pending_shadows",
)

# Producer must explicitly derive a final pass from the
# required runtime gate, not merely emit unconditional pass=True.
check(
    "SHADOW_FAIL_CLOSED_FUNCTION",
    (
        "shadow_pass_from_gate"
        in engine
        or
        "gate_pass"
        in engine
    ),
)

unconditional_pass_patterns = (
    '"pass": True',
    "'pass': True",
    '"pass":True',
    "'pass':True",
)

unconditional = any(
    token in producer
    for token in unconditional_pass_patterns
)

check(
    "SHADOW_NO_UNCONDITIONAL_PASS",
    not unconditional,
)

# The Shadow engine is evidence-only. It must never issue
# runtime requests or mutate the live/server layer.
forbidden_engine_tokens = (
    "atomic(REQUEST",
    "runtime_sync_request.json",
    "apply_runtime_sync",
    "strategy_runtime_sync.ps1",
    "paramiko",
    "ssh ",
    "scp ",
    "order.py",
    "import order",
    "from order",
)

bad_engine = [
    token
    for token in forbidden_engine_tokens
    if token.lower() in engine.lower()
]

check(
    "SHADOW_EVIDENCE_ONLY",
    not bad_engine,
    ",".join(bad_engine),
)


# ============================================================
# Autonomous integration
# ============================================================

tick = fsrc(
    auto,
    "tick",
)

check(
    "AUTO_RELEASE_IMPORTS_SHADOW_ENGINE",
    (
        "import v3_shadow_engine"
        in auto
        or
        "from v3_shadow_engine"
        in auto
    ),
)

check(
    "AUTO_RELEASE_IMPORTS_DEPLOYMENT",
    (
        "import v3_deployment"
        in auto
        or
        "from v3_deployment"
        in auto
    ),
)

check(
    "TICK_PROCESSES_PENDING_SHADOW",
    "process_pending_shadows"
    in tick,
)

check(
    "TICK_MATERIALIZES_AFTER_SHADOW",
    "materialize_local_ready_request"
    in tick,
)

shadow_pos = tick.find(
    "process_pending_shadows"
)

materialize_pos = tick.find(
    "materialize_local_ready_request"
)

request_idle_pos = tick.find(
    "if not REQUEST.is_file()"
)

check(
    "TICK_ORDER_SHADOW_THEN_MATERIALIZE",
    (
        shadow_pos >= 0
        and materialize_pos > shadow_pos
    ),
)

check(
    "TICK_SHADOW_BEFORE_IDLE",
    (
        shadow_pos >= 0
        and request_idle_pos > shadow_pos
    ),
)

check(
    "TICK_MATERIALIZE_BEFORE_IDLE",
    (
        materialize_pos >= 0
        and request_idle_pos > materialize_pos
    ),
)


# ============================================================
# Canary lifecycle verifier must migrate to new state machine
# ============================================================

check(
    "CANARY_VERIFIER_BINDS_PENDING_SHADOW",
    "PENDING_SHADOW"
    in canary_verify,
)

check(
    "CANARY_VERIFIER_BINDS_MINIMAL_SHADOW",
    (
        "minimal_shadow_pass"
        in canary_verify
        or
        "SHADOW_REQUIRED_BEFORE_CANARY"
        in canary_verify
    ),
)

check(
    "CANARY_VERIFIER_CHECKS_TWENTY_PERCENT",
    (
        "0.20" in canary_verify
        or
        "initial_capital_weight"
        in canary_verify
    ),
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
    for row in rows
    if not row["ok"]
]

print("=" * 92)
print("B-BOT V3 GREEN-3 MINIMAL SHADOW RUNTIME GATE VERIFIER")
print("=" * 92)

for row in rows:

    status = (
        "PASS"
        if row["ok"]
        else "FAIL"
    )

    extra = (
        " :: " + row["detail"]
        if row["detail"]
        else ""
    )

    print(
        f"{status:4} "
        f"{row['name']}"
        f"{extra}"
    )

print()
print(
    "TOTAL_CHECKS=",
    len(rows),
)

print(
    "FAILED_COUNT=",
    len(failed),
)

if failed:

    print(
        "SHADOW_RUNTIME_GATE_STATUS=RED"
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
    "SHADOW_RUNTIME_GATE_STATUS=GREEN"
)

raise SystemExit(0)