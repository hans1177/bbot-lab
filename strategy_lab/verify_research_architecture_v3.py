from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(r"C:\my\bottest")

POLICY_ID = "RA_MAIN_MIDDLE_DETAIL_20260830_V1"

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

ACTIVE = {
    "controller":
        ROOT / "strategy_lab" / "research_controller.py",

    "roadmap_manager":
        ROOT / "strategy_lab" / "roadmap_manager.py",

    "task_queue":
        ROOT / "strategy_lab" / "v3_task_queue.py",

    "governance":
        ROOT / "strategy_lab" / "v3_governance.py",

    "strategy_research":
        ROOT / "strategy_lab" / "v3_strategy_research.py",

    "combination_research":
        ROOT / "strategy_lab" / "v3_combination_research.py",

    "hypothesis_factory":
        ROOT / "strategy_lab" / "v3_hypothesis_factory.py",

    "challenger":
        ROOT / "strategy_lab" / "v3_challenger_research.py",

    "deployment":
        ROOT / "strategy_lab" / "v3_deployment.py",

    "release_approval":
        ROOT / "strategy_lab" / "local_release_approval.py",

    "auto_release":
        ROOT / "strategy_lab" / "v3_auto_runtime_release.py",

    "knowledge":
        ROOT / "strategy_lab" / "v3_research_knowledge.py",
}


def read(path: Path) -> str:

    if not path.is_file():
        return ""

    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def sha256(path: Path) -> str:

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def main() -> int:

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--strict",
        action="store_true",
    )

    args = ap.parse_args()

    checks = []

    def check(
        name: str,
        ok: bool,
        detail: str = "",
        required: bool = True,
    ):

        checks.append(
            {
                "name": name,
                "ok": bool(ok),
                "detail": detail,
                "required": bool(required),
            }
        )

    # ========================================================
    # ROADMAP CONTRACT
    # ========================================================

    roadmap_path = (
        ROOT / "ROADMAP_CONSTITUTION_V3.json"
    )

    roadmap = json.loads(
        roadmap_path.read_text(
            encoding="utf-8-sig"
        )
    )

    arch = (
        roadmap.get("research_architecture_v3")
        or {}
    )

    scheduler = (
        arch.get("research_scheduler")
        or {}
    )

    check(
        "ROADMAP_POLICY_ID",
        arch.get("policy_id") == POLICY_ID,
        repr(arch.get("policy_id")),
    )

    check(
        "MAIN_STAGE_CONTRACT",
        (arch.get("MAIN") or {}).get("stages")
        == [
            "SETUP",
            "TRIGGER",
            "FILTER",
        ],
    )

    check(
        "MIDDLE_STAGE_CONTRACT",
        (arch.get("MIDDLE") or {}).get("stages")
        == [
            "HOLD_RISK",
            "EXIT_TRAILING",
            "REENTRY",
        ],
    )

    check(
        "DETAIL_STAGE_CONTRACT",
        (arch.get("DETAIL") or {}).get("stages")
        == [
            "REAL_COST",
            "SYMBOL_PORTFOLIO",
            "EXECUTION_REAL_GAP",
        ],
    )

    tiers = (
        (arch.get("resource_allocation") or {})
        .get("tiers")
        or []
    )

    first = tiers[0] if tiers else {}

    check(
        "UNDER_100M_50_30_20",
        (
            first.get("MAIN") == 0.50
            and first.get("MIDDLE") == 0.30
            and first.get("DETAIL") == 0.20
        ),
    )

    check(
        "SCHEDULER_FINAL",
        scheduler.get("status")
        == "FINAL_CONFIRMED",
    )

    check(
        "SCHEDULER_CHEAP_FIRST",
        (
            scheduler.get("cheap_first_policy")
            or {}
        ).get("enabled") is True,
    )

    check(
        "SCHEDULER_EARLY_STOP",
        (
            scheduler.get("early_stop")
            or {}
        ).get("enabled") is True,
    )

    check(
        "SCHEDULER_BOUNDED_COMPUTE",
        (
            scheduler.get("bounded_compute")
            or {}
        ).get("required") is True,
    )

    check(
        "SEALED_NEW_MAIN_ONLY",
        (
            (arch.get("evaluation") or {})
            .get("sealed_blind")
            or {}
        ).get("new_main_only") is True,
    )

    check(
        "SHADOW_MINIMAL_RUNTIME_GATE",
        (
            arch.get("shadow")
            or {}
        ).get(
            "mandatory_minimal_runtime_gate"
        ) is True,
    )

    check(
        "CANARY_TWENTY_PERCENT",
        (
            arch.get("canary")
            or {}
        ).get("initial_capital_weight")
        == 0.20,
    )


    # ========================================================
    # LEGACY COMPATIBILITY
    # ========================================================

    expected_tracks = [
        "DIRECTION",
        "STRATEGY",
        "ENTRY_RISK",
        "EXIT",
        "REENTRY",
        "SYMBOL",
        "PORTFOLIO",
        "REGIME",
        "EXECUTION",
    ]

    check(
        "LEGACY_9_TRACK_COMPATIBILITY",
        list(
            (roadmap.get("tracks") or {}).keys()
        ) == expected_tracks,
    )


    # ========================================================
    # REQUIRED NEW-ARCH BINDINGS
    # ========================================================

    controller = read(
        ACTIVE["controller"]
    )

    manager = read(
        ACTIVE["roadmap_manager"]
    )

    queue = read(
        ACTIVE["task_queue"]
    )

    governance = read(
        ACTIVE["governance"]
    )

    approval = read(
        ACTIVE["release_approval"]
    )

    check(
        "CONTROLLER_BINDS_NEW_ARCH",
        "research_architecture_v3"
        in controller,
        required=True,
    )

    check(
        "ROADMAP_MANAGER_BINDS_NEW_ARCH",
        "research_architecture_v3"
        in manager,
        required=True,
    )

    check(
        "TASK_QUEUE_BINDS_SCHEDULER",
        (
            "research_scheduler"
            in queue
            and "resource_allocation"
            in queue
        ),
        required=True,
    )

    check(
        "GOVERNANCE_VALIDATES_NEW_ARCH",
        "research_architecture_v3"
        in governance,
        required=True,
    )

    check(
        "RELEASE_APPROVAL_BINDS_MINIMAL_SHADOW",
        (
            "research_architecture_v3"
            in approval
            and "SHADOW_PASS"
            in approval
        ),
        required=True,
    )


    # ========================================================
    # SCHEDULER BUILDING BLOCKS
    # ========================================================

    for token in (
        "input_signature",
        "trial_budget",
        "stop_condition",
        "evidence_deficiency",
        "success_probability",
        "compute_cost",
    ):

        check(
            "TASK_QUEUE_" + token.upper(),
            token in queue,
            required=True,
        )

    check(
        "TASK_QUEUE_EXPECTED_IMPROVEMENT",
        "expected_improvement"
        in queue,
        required=True,
    )

    check(
        "TASK_QUEUE_WALL_CLOCK",
        bool(
            re.search(
                r"wall.?clock|expected_wall_clock",
                queue,
                re.I,
            )
        ),
        required=True,
    )


    # ========================================================
    # EXISTING MAIN VALIDATION ASSETS
    # ========================================================

    strategy_research = read(
        ACTIVE["strategy_research"]
    )

    combo_research = read(
        ACTIVE["combination_research"]
    )

    hypothesis = read(
        ACTIVE["hypothesis_factory"]
    )

    check(
        "BROAD_RANK_EXCLUDES_SEALED",
        (
            "Broad ranking intentionally excludes sealed_blind"
            in strategy_research
        ),
        required=True,
    )

    check(
        "SINGLE_SEALED_ONE_SHOT_BOUNDARY",
        (
            "SEEN_ONE_SHOT"
            in strategy_research
            and "SEALED_BLIND"
            in strategy_research
        ),
        required=True,
    )

    check(
        "COMBO_SEALED_ONE_SHOT_BOUNDARY",
        (
            "SEEN_ONE_SHOT"
            in combo_research
            and "COMBO_SEALED_BLIND"
            in combo_research
        ),
        required=True,
    )

    check(
        "SAFE_GRAMMAR_BROAD_NO_BLIND",
        (
            '"sealed_blind": "NOT_USED_IN_BROAD"'
            in hypothesis
        ),
        required=True,
    )


    # ========================================================
    # DEPLOYMENT ASSETS
    # ========================================================

    deployment = read(
        ACTIVE["deployment"]
    )

    auto_release = read(
        ACTIVE["auto_release"]
    )

    knowledge = read(
        ACTIVE["knowledge"]
    )

    check(
        "DEPLOYMENT_CANARY",
        "CANARY" in deployment,
    )

    check(
        "DEPLOYMENT_SMALL",
        "SMALL" in deployment,
    )

    check(
        "DEPLOYMENT_NORMAL",
        "NORMAL" in deployment,
    )

    check(
        "DEPLOYMENT_POLICY_VERSION_BINDING",
        (
            "strategy_version" in deployment
            and "policy_versions" in deployment
        ),
    )

    check(
        "AUTO_RELEASE_TIER_PIPELINE",
        all(
            token in auto_release
            for token in (
                "CANARY",
                "SMALL",
                "NORMAL",
                "ROLLBACK",
            )
        ),
    )

    check(
        "KNOWLEDGE_DO_NOT_RERUN",
        "do_not_rerun" in knowledge,
    )

    check(
        "KNOWLEDGE_EVIDENCE_ORDER",
        all(
            token in knowledge
            for token in (
                "LIVE",
                "NORMAL",
                "SMALL",
                "CANARY",
                "SEALED_BLIND_PASS",
            )
        ),
    )


    # ========================================================
    # ADD-ENTRY MUST NOT APPEAR IN ACTIVE IMPLEMENTATION
    # ========================================================

    forbidden = re.compile(
        r"\b("
        r"ADD_ENTRY|"
        r"SCALE_IN|"
        r"AVERAGING_DOWN|"
        r"PYRAMIDING"
        r")\b",
        re.I,
    )

    bad = []

    for label, path in ACTIVE.items():

        src = read(path)

        if forbidden.search(src):
            bad.append(label)

    check(
        "NO_ACTIVE_ADD_ENTRY",
        not bad,
        ",".join(bad),
    )


    # ========================================================
    # SHADOW: OLD 30/60/90 DAY RULE MUST NOT GOVERN ACTIVE
    # RELEASE APPROVAL PATH
    # ========================================================

    old_shadow_time = bool(
        re.search(
            r"shadow_minimum_days|"
            r"minimum_days\s*=\s*30|"
            r"preferred_days\s*=\s*60|"
            r"strong_evidence_days\s*=\s*90",
            approval,
            re.I,
        )
    )

    check(
        "ACTIVE_APPROVAL_NO_LONG_SHADOW_TIMER",
        not old_shadow_time,
    )


    # ========================================================
    # RUNNER FROZEN / NO STRATEGY-SPECIFIC BRANCH
    # ========================================================

    runner = read(
        ROOT / "runner.py"
    )

    direct_branch = bool(
        re.search(
            r"^\s*(if|elif)\b[^\n]*"
            r"(strategy_version|strategy_id|strategy_name)",
            runner,
            re.I | re.M,
        )
    )

    check(
        "RUNNER_NO_DIRECT_STRATEGY_BRANCH",
        not direct_branch,
    )


    # ========================================================
    # FROZEN CORE
    # ========================================================

    for name, expected in FROZEN.items():

        path = ROOT / name

        got = (
            sha256(path)
            if path.is_file()
            else ""
        )

        check(
            "FROZEN_" + name.upper(),
            got == expected,
            got,
        )


    # ========================================================
    # REPORT
    # ========================================================

    required_failures = [
        x
        for x in checks
        if x["required"]
        and not x["ok"]
    ]

    print("=" * 88)
    print(
        "B-BOT V3 RESEARCH ARCHITECTURE VERIFIER"
    )
    print("=" * 88)

    for row in checks:

        status = (
            "PASS"
            if row["ok"]
            else "FAIL"
        )

        req = (
            "REQUIRED"
            if row["required"]
            else "INFO"
        )

        print(
            f"{status:4} "
            f"{req:8} "
            f"{row['name']}"
            + (
                f" :: {row['detail']}"
                if row["detail"]
                else ""
            )
        )

    print()
    print(
        "TOTAL_CHECKS=",
        len(checks),
    )

    print(
        "REQUIRED_FAILURES=",
        len(required_failures),
    )

    print(
        "STRICT_MODE=",
        args.strict,
    )

    if required_failures:

        print(
            "ARCHITECTURE_IMPLEMENTATION_STATUS=RED"
        )

        print(
            "RED_GAPS="
            + ",".join(
                x["name"]
                for x in required_failures
            )
        )

        if args.strict:
            return 2

        return 0

    print(
        "ARCHITECTURE_IMPLEMENTATION_STATUS=GREEN"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())