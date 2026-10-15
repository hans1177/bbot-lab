from __future__ import annotations

import ast
import hashlib
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

APPROVAL = (
    ROOT
    / "strategy_lab"
    / "local_release_approval.py"
)

EXPECTED_DEPLOY_SHA = (
    "b02d7996fdbbf1693cb217f2d22161f68c47cb9dc24b8ed7cf24b7c21b3ef38e"
)

EXPECTED_SHADOW_SHA = (
    "c3c8c501dfb2a74544e5aaae221e4aa76f4c2c310154a3acf7d4972d2b14cc8b"
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


def function_source(src, name):

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


def compact(src):
    return "".join(
        src.split()
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
                str(detail or ""),
        }
    )


deploy = read(DEPLOY)
shadow = read(SHADOW)
approval = read(APPROVAL)

next_tier = function_source(
    deploy,
    "next_tier_request",
)

shadow_processor = function_source(
    shadow,
    "process_pending_shadows",
)

next_compact = compact(
    next_tier
)

shadow_compact = compact(
    shadow_processor
)


# ============================================================
# FOUNDATION
# ============================================================

check(
    "GREEN4_DEPLOY_SHA",
    sha(DEPLOY)
    == EXPECTED_DEPLOY_SHA,
    sha(DEPLOY),
)

check(
    "GREEN4_SHADOW_SHA",
    sha(SHADOW)
    == EXPECTED_SHADOW_SHA,
    sha(SHADOW),
)

check(
    "NEXT_TIER_EXISTS",
    bool(next_tier),
)

check(
    "SHADOW_PROCESSOR_EXISTS",
    bool(shadow_processor),
)

check(
    "MINIMAL_SHADOW_VALIDATOR_EXISTS",
    "def minimal_shadow_pass"
    in approval,
)


# ============================================================
# EXISTING LIFECYCLE MUST REMAIN
# ============================================================

check(
    "CANARY_SMALL_NORMAL_PRESERVED",
    (
        '"CANARY"'
        in next_tier
        and
        '"SMALL"'
        in next_tier
        and
        '"NORMAL"'
        in next_tier
    ),
)

check(
    "ROLLBACK_PRESERVED",
    "ROLLBACK"
    in next_tier,
)

check(
    "REAL_EVIDENCE_PRESERVED",
    (
        "canary_evidence"
        in next_tier
        and
        "WAIT_MORE_REAL_EVIDENCE"
        in next_tier
    ),
)


# ============================================================
# GREEN-5 REQUIRED BOUNDARY
#
# Positive scaling is forbidden without Shadow PASS.
# Rollback must remain available regardless of Shadow.
# ============================================================

check(
    "NEXT_TIER_REQUIRES_MINIMAL_SHADOW",
    "minimal_shadow_pass"
    in next_tier,
)

check(
    "NEXT_TIER_BINDS_STRATEGY_VERSION_TO_SHADOW",
    (
        "strategy_version"
        in next_tier
        and
        "minimal_shadow_pass"
        in next_tier
    ),
)

check(
    "POSITIVE_SCALE_SHADOW_GATE_MARKER",
    (
        "LEGACY_CANARY_SHADOW_SCALE_GATE"
        in next_tier
        or
        "SHADOW_REQUIRED_BEFORE_SCALE"
        in next_tier
    ),
)

check(
    "ROLLBACK_NOT_BLOCKED_BY_SHADOW_MARKER",
    (
        "ROLLBACK_BYPASSES_SHADOW_SCALE_GATE"
        in next_tier
        or
        "ROLLBACK_ALLOWED_WITHOUT_SHADOW"
        in next_tier
    ),
)


# ============================================================
# LEGACY CANARY / SMALL EVIDENCE PRODUCTION
#
# Existing real Canary must not be reset or promoted.
# Shadow engine should simply generate local evidence for the
# same strategy_version while the real tier remains unchanged.
# ============================================================

check(
    "SHADOW_PROCESSOR_SEES_CANARY",
    '"CANARY"'
    in shadow_processor,
)

check(
    "SHADOW_PROCESSOR_SEES_SMALL",
    '"SMALL"'
    in shadow_processor,
)

check(
    "LEGACY_SCALE_SHADOW_MARKER",
    (
        "LEGACY_CANARY_SHADOW"
        in shadow
        or
        "LEGACY_SCALE_SHADOW"
        in shadow
    ),
)

check(
    "LEGACY_SHADOW_BINDS_STRATEGY_VERSION",
    (
        "strategy_version"
        in shadow_processor
        and
        "_evidence_path"
        in shadow_processor
    ),
)

check(
    "LEGACY_SHADOW_DOES_NOT_DOWNGRADE_CANARY",
    (
        '["tier"]="PENDING_SHADOW"'
        not in shadow_compact
        and
        '["tier"]="LOCAL_PROVISIONAL_READY"'
        not in shadow_compact
    ),
)

check(
    "SHADOW_EVIDENCE_ONLY_PRESERVED",
    (
        '"real_order":False'
        in compact(shadow)
        and
        '"server_mutation":False'
        in compact(shadow)
    ),
)


# ============================================================
# FROZEN
# ============================================================

for name, expected in FROZEN.items():

    got = sha(
        ROOT / name
    )

    check(
        "FROZEN_"
        + name.upper(),
        got == expected,
        got,
    )


failed = [
    row
    for row in rows
    if not row["ok"]
]

print("=" * 96)
print(
    "B-BOT V3 GREEN-5 LEGACY CANARY SHADOW BOUNDARY VERIFIER"
)
print("=" * 96)

for row in rows:

    status = (
        "PASS"
        if row["ok"]
        else "FAIL"
    )

    detail = (
        " :: " + row["detail"]
        if row["detail"]
        else ""
    )

    print(
        f"{status:4} "
        f"{row['name']}"
        f"{detail}"
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
        "LEGACY_CANARY_SHADOW_BOUNDARY_STATUS=RED"
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
    "LEGACY_CANARY_SHADOW_BOUNDARY_STATUS=GREEN"
)

raise SystemExit(0)