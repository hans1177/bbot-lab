from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(r"C:\my\bottest")

DEPLOY = (
    ROOT
    / "strategy_lab"
    / "v3_deployment.py"
)

SHADOW = (
    ROOT
    / "strategy_lab"
    / "v3_shadow_engine.py"
)

AUTO = (
    ROOT
    / "strategy_lab"
    / "v3_auto_runtime_release.py"
)

APPROVAL = (
    ROOT
    / "strategy_lab"
    / "local_release_approval.py"
)

ROADMAP = (
    ROOT
    / "ROADMAP_CONSTITUTION_V3.json"
)

EXPECTED_DEPLOY_SHA = (
    "b02d7996fdbbf1693cb217f2d22161f68c47cb9dc24b8ed7cf24b7c21b3ef38e"
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


MIDDLE_DETAIL_LEGACY_TRACKS = {
    "ENTRY_RISK",
    "EXIT",
    "REENTRY",
    "SYMBOL",
    "PORTFOLIO",
    "EXECUTION",
}


def sha(path):
    if not path.is_file():
        return ""

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def read(path):
    if not path.is_file():
        return ""

    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def top_function(src, name):
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


def compact(text):
    return "".join(
        text.split()
    )


rows = []


def check(name, ok, detail=""):

    rows.append(
        {
            "name":
                str(name),

            "ok":
                bool(ok),

            "detail":
                str(
                    detail
                    or ""
                ),
        }
    )


deploy = read(
    DEPLOY
)

shadow = read(
    SHADOW
)

auto = read(
    AUTO
)

approval = read(
    APPROVAL
)

roadmap = json.loads(
    ROADMAP.read_text(
        encoding="utf-8-sig"
    )
)

arch = (
    roadmap.get(
        "research_architecture_v3"
    )
    or {}
)

shadow_policy = (
    arch.get("shadow")
    or {}
)


# ============================================================
# GREEN-2 / GREEN-3 FOUNDATION
# ============================================================

check(
    "GREEN2_DEPLOY_SHA",
    sha(DEPLOY)
    == EXPECTED_DEPLOY_SHA,
    sha(DEPLOY),
)

check(
    "GREEN3_SHADOW_ENGINE_EXISTS",
    SHADOW.is_file(),
)

check(
    "GREEN3_AUTO_IMPORTS_SHADOW_ENGINE",
    (
        "v3_shadow_engine"
        in auto
        and
        "process_pending_shadows"
        in auto
    ),
)

check(
    "CONSTITUTION_SHADOW_REQUIRED",
    shadow_policy.get(
        "mandatory_minimal_runtime_gate"
    )
    is True,
)

check(
    "CONSTITUTION_SHADOW_NOT_PROFIT_GATE",
    shadow_policy.get(
        "profitability_gate"
    )
    is False,
)

check(
    "MINIMAL_SHADOW_VALIDATOR_EXISTS",
    "def minimal_shadow_pass"
    in approval,
)


# ============================================================
# CURRENT POLICY FUNCTIONS
# ============================================================

create_policy = (
    top_function(
        deploy,
        "create_policy_canary_request",
    )
)

materialize = (
    top_function(
        deploy,
        "materialize_local_ready_request",
    )
)

policy_evidence = (
    top_function(
        deploy,
        "policy_canary_evidence",
    )
)

next_policy = (
    top_function(
        deploy,
        "next_policy_tier_request",
    )
)

shadow_processor = (
    top_function(
        shadow,
        "process_pending_shadows",
    )
)

shadow_producer = (
    top_function(
        shadow,
        "produce_shadow_evidence",
    )
)


check(
    "POLICY_CREATE_EXISTS",
    bool(
        create_policy
    ),
)

check(
    "POLICY_CANARY_EVIDENCE_EXISTS",
    bool(
        policy_evidence
    ),
)

check(
    "POLICY_NEXT_TIER_EXISTS",
    bool(
        next_policy
    ),
)


# ============================================================
# Policy Full Validation boundary
#
# Existing governance uses VALIDATED Policy Version as
# the release boundary. GREEN-4 must preserve this.
# ============================================================

check(
    "POLICY_REQUIRES_VALIDATED",
    (
        "POLICY_VERSION_NOT_VALIDATED"
        in create_policy
        and
        '"VALIDATED"'
        in create_policy
    ),
)


# ============================================================
# SEALED BLIND MUST NOT BE ADDED TO MIDDLE / DETAIL
# ============================================================

sealed_tokens = (
    "SEALED_BLIND",
    "sealed_blind",
)

policy_uses_sealed = any(
    token
    in create_policy
    for token
    in sealed_tokens
)

check(
    "POLICY_DOES_NOT_REQUIRE_SEALED_BLIND",
    not policy_uses_sealed,
)


# ============================================================
# MIDDLE / DETAIL legacy track coverage
# ============================================================

missing_tracks = [
    track
    for track
    in sorted(
        MIDDLE_DETAIL_LEGACY_TRACKS
    )
    if (
        '"' + track + '"'
        not in create_policy
    )
]

check(
    "POLICY_MIDDLE_DETAIL_TRACK_COVERAGE",
    not missing_tracks,
    ",".join(
        missing_tracks
    ),
)


# ============================================================
# GREEN-4 REQUIRED CREATE STATE MACHINE
#
# VALIDATED
# -> PENDING_SHADOW
#
# create_policy_canary_request MUST NOT create a live
# runtime_sync_request before Shadow PASS.
# ============================================================

check(
    "POLICY_CREATE_HAS_PENDING_SHADOW",
    "PENDING_SHADOW"
    in create_policy,
)

create_compact = compact(
    create_policy
)

check(
    "POLICY_CREATE_DOES_NOT_WRITE_RUNTIME_REQUEST",
    "atomic(REQUEST,"
    not in create_compact,
)

# Direct transition at create stage is forbidden.
check(
    "POLICY_CREATE_DOES_NOT_DIRECT_CANARY_SYNC",
    (
        '"tier":"PENDING_CANARY_SYNC"'
        not in create_compact
        and
        "row[\"tier\"]=\"PENDING_CANARY_SYNC\""
        not in create_compact
    ),
)

check(
    "POLICY_CREATE_STORES_PENDING_REQUEST_TEMPLATE",
    "pending_request_template"
    in create_policy,
)

check(
    "POLICY_CREATE_DECLARES_SHADOW_GATE",
    (
        "MINIMAL_SHADOW_RUNTIME_SAFETY"
        in create_policy
        or
        "required_pre_runtime_gate"
        in create_policy
    ),
)

check(
    "POLICY_CREATE_BINDS_SHADOW_EVIDENCE_FILE",
    (
        "shadow_evidence_file"
        in create_policy
        and
        "shadow.json"
        in create_policy
    ),
)


# ============================================================
# GREEN-4 REQUIRED MATERIALIZATION
#
# Policy PENDING_SHADOW
# -> minimal_shadow_pass(policy_version)
# -> runtime request
# -> PENDING_CANARY_SYNC
# ============================================================

policy_marker = (
    "# Policy Version"
)

policy_pos = (
    materialize.find(
        policy_marker
    )
)

policy_materialize = (
    materialize[
        policy_pos:
    ]
    if policy_pos >= 0
    else ""
)

check(
    "POLICY_MATERIALIZE_SECTION_FOUND",
    bool(
        policy_materialize
    ),
)

check(
    "POLICY_MATERIALIZE_ACCEPTS_PENDING_SHADOW",
    "PENDING_SHADOW"
    in policy_materialize,
)

check(
    "POLICY_MATERIALIZE_REQUIRES_MINIMAL_SHADOW_PASS",
    "minimal_shadow_pass"
    in policy_materialize,
)

check(
    "POLICY_MATERIALIZE_TO_PENDING_CANARY_SYNC",
    (
        "PENDING_SHADOW"
        in policy_materialize
        and
        "PENDING_CANARY_SYNC"
        in policy_materialize
    ),
)

# Runtime request write is allowed here,
# but only in a policy section which contains the Shadow gate.
check(
    "POLICY_RUNTIME_REQUEST_AFTER_SHADOW_ONLY",
    (
        "atomic(REQUEST"
        in compact(
            policy_materialize
        )
        and
        "minimal_shadow_pass"
        in policy_materialize
    ),
)


# ============================================================
# GREEN-4 SHADOW PRODUCER
#
# Existing GREEN-3 processor handles Strategy frames.
# GREEN-4 must also process deployment_state["policies"].
# ============================================================

check(
    "SHADOW_ENGINE_PROCESSES_POLICIES",
    (
        'deployment.get("policies")'
        in compact(
            shadow_processor
        )
    ),
)

check(
    "SHADOW_ENGINE_BINDS_POLICY_VERSION",
    "policy_version"
    in shadow,
)

check(
    "SHADOW_ENGINE_BINDS_POLICY_TRACK",
    "policy_track"
    in shadow,
)

check(
    "SHADOW_ENGINE_POLICY_KIND",
    (
        '"POLICY"'
        in shadow
        or
        "'POLICY'"
        in shadow
    ),
)


# ============================================================
# Fail-closed / evidence-only requirements remain mandatory
# ============================================================

check(
    "SHADOW_ENGINE_FAIL_CLOSED",
    "shadow_pass_from_gate"
    in shadow,
)

check(
    "SHADOW_ENGINE_RUNTIME_ERRORS_GATE",
    "runtime_errors"
    in shadow,
)

check(
    "SHADOW_ENGINE_CLOSED_BAR_GATE",
    "closed_bar_timing_pass"
    in shadow,
)

check(
    "SHADOW_ENGINE_STATE_TRANSITION_GATE",
    "state_transition_pass"
    in shadow,
)

check(
    "SHADOW_ENGINE_COST_SANITY_GATE",
    "order_simulation_cost_sanity_pass"
    in shadow,
)

check(
    "SHADOW_ENGINE_NO_REAL_ORDER",
    (
        '"real_order":'
        in shadow
        and
        "False"
        in shadow
    ),
)

check(
    "SHADOW_ENGINE_NO_SERVER_MUTATION",
    (
        '"server_mutation":'
        in shadow
        and
        "False"
        in shadow
    ),
)


# ============================================================
# Existing post-Canary lifecycle must remain
# ============================================================

check(
    "POLICY_CANARY_TO_SMALL_NORMAL_PRESERVED",
    (
        "CANARY"
        in next_policy
        and
        "SMALL"
        in next_policy
        and
        "NORMAL"
        in next_policy
    ),
)

check(
    "POLICY_ROLLBACK_PRESERVED",
    "ROLLBACK"
    in next_policy,
)


# ============================================================
# Frozen core
# ============================================================

for name, expected in (
    FROZEN.items()
):

    got = sha(
        ROOT / name
    )

    check(
        "FROZEN_"
        + name.upper(),
        got == expected,
        got,
    )


# ============================================================
# RESULT
# ============================================================

failed = [
    row
    for row
    in rows
    if not row["ok"]
]

print(
    "=" * 96
)

print(
    "B-BOT V3 GREEN-4 POLICY SHADOW-FIRST VERIFIER"
)

print(
    "=" * 96
)

for row in rows:

    status = (
        "PASS"
        if row["ok"]
        else "FAIL"
    )

    suffix = (
        " :: "
        + row["detail"]
        if row["detail"]
        else ""
    )

    print(
        f"{status:4} "
        f"{row['name']}"
        f"{suffix}"
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
        "POLICY_SHADOW_RUNTIME_STATUS=RED"
    )

    print(
        "RED_GAPS="
        + ",".join(
            row["name"]
            for row
            in failed
        )
    )

    raise SystemExit(2)

print(
    "POLICY_SHADOW_RUNTIME_STATUS=GREEN"
)

raise SystemExit(0)