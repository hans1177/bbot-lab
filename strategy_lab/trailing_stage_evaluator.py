# B_BOT_TRAILING_STAGE_EVALUATOR_V1
#
# Generic stage bridge for:
#   BROAD
#   NARROW
#   CONFIRM
#
# IMPORTANT:
# - Reuses the already verified simulation / validation path
#   from trailing_fast_evaluator.py.
# - Does not modify LIVE trading source.
# - Execution is fail-closed unless stage plan explicitly
#   has execution.enabled == true.
# - Forward/live promotion remains forbidden here.

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"

LEGACY_EVALUATOR = (
    LAB / "trailing_fast_evaluator.py"
)

REG_FILE = (
    RESULTS / "candidate_registry.json"
)

STATE_FILE = (
    RESULTS / "research_state.json"
)

CHAPTER_FILE = (
    RESULTS / "chapter_state.json"
)

OVERFIT_FILE = (
    RESULTS / "overfit_ledger.json"
)


EXPECTED_LEGACY_SHA256 = (
    "5e5fb6d3e6d84a419324166711d8fecf"
    "0bed1fb08b2903fb142461a16c8d9bea"
)


STAGES = {
    "BROAD": {
        "plan":
            RESULTS
            / "trailing_broad_plan_latest.json",

        "result":
            RESULTS
            / "trailing_stage_broad_latest.json",

        "checkpoint_stage":
            "auto_trailing_broad_v1",

        "budget":
            10,
    },

    "NARROW": {
        "plan":
            RESULTS
            / "trailing_narrow_plan_latest.json",

        "result":
            RESULTS
            / "trailing_stage_narrow_latest.json",

        "checkpoint_stage":
            "auto_trailing_narrow_v1",

        "budget":
            7,
    },

    "CONFIRM": {
        "plan":
            RESULTS
            / "trailing_confirm_plan_latest.json",

        "result":
            RESULTS
            / "trailing_stage_confirm_latest.json",

        "checkpoint_stage":
            "auto_trailing_confirm_v1",

        "budget":
            4,
    },
}


TERMINAL_STATUSES = {
    "DONE",
    "FAST_PASS",
    "FAST_FAIL",
    "HARD_FAIL",
}


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_json(
    path: Path,
    default=None,
):
    if not path.exists():
        if default is not None:
            return copy.deepcopy(
                default
            )

        raise RuntimeError(
            "MISSING_JSON="
            + str(path)
        )

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def atomic_json(
    path: Path,
    obj,
):
    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    os.replace(
        tmp,
        path,
    )


def sha256_file(
    path: Path,
):
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            b = f.read(
                1024 * 1024
            )

            if not b:
                break

            h.update(b)

    return h.hexdigest()


def verify_legacy():
    actual = sha256_file(
        LEGACY_EVALUATOR
    )

    if actual != EXPECTED_LEGACY_SHA256:
        raise RuntimeError(
            "LEGACY_EVALUATOR_HASH_MISMATCH="
            + actual
        )


def import_legacy():
    verify_legacy()

    spec = importlib.util.spec_from_file_location(
        "bbot_trailing_fast_legacy",
        LEGACY_EVALUATOR,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "LEGACY_IMPORT_SPEC_FAIL"
        )

    mod = importlib.util.module_from_spec(
        spec
    )

    sys.modules[
        spec.name
    ] = mod

    spec.loader.exec_module(
        mod
    )

    return mod


def plan_axis(plan):
    axis = (
        plan.get("changed_axis")
        or plan.get("axis")
        or plan.get(
            "fixed",
            {},
        ).get(
            "changed_axis_only"
        )
    )

    return str(
        axis or ""
    ).upper()


def candidate_signature(
    candidate,
):
    sig = (
        candidate.get(
            "candidate_signature"
        )
        or candidate.get(
            "signature"
        )
    )

    if not sig:
        raise RuntimeError(
            "CANDIDATE_SIGNATURE_MISSING "
            + str(
                candidate.get(
                    "candidate_id"
                )
            )
        )

    return str(sig)


def candidate_parameters(
    candidate,
):
    params = candidate.get(
        "parameters",
        {}
    )

    start = (
        params.get(
            "trailing_start_pct"
        )
        if isinstance(params, dict)
        else None
    )

    callback = (
        params.get(
            "trailing_callback_pct"
        )
        if isinstance(params, dict)
        else None
    )

    if start is None:
        start = candidate.get(
            "start_pct"
        )

    if callback is None:
        callback = candidate.get(
            "callback_pct"
        )

    if (
        start is None
        or callback is None
    ):
        raise RuntimeError(
            "CANDIDATE_PARAMETERS_MISSING "
            + str(
                candidate.get(
                    "candidate_id"
                )
            )
        )

    return (
        float(start),
        float(callback),
    )


def normalize_candidate(
    candidate,
):
    cid = str(
        candidate.get(
            "candidate_id"
        )
        or ""
    )

    if not cid:
        raise RuntimeError(
            "CANDIDATE_ID_MISSING"
        )

    sig = candidate_signature(
        candidate
    )

    start, callback = (
        candidate_parameters(
            candidate
        )
    )

    out = copy.deepcopy(
        candidate
    )

    # Compatibility with verified legacy executor.
    out[
        "candidate_id"
    ] = cid

    out[
        "candidate_signature"
    ] = sig

    out[
        "signature"
    ] = sig

    out[
        "parameters"
    ] = {
        "trailing_start_pct":
            start,

        "trailing_callback_pct":
            callback,
    }

    out[
        "start_pct"
    ] = start

    out[
        "callback_pct"
    ] = callback

    return out


def find_registry_row(
    reg,
    candidate,
):
    cid = candidate[
        "candidate_id"
    ]

    sig = candidate[
        "candidate_signature"
    ]

    found = []

    for row in reg.get(
        "planned_experiments",
        []
    ):
        if not isinstance(
            row,
            dict,
        ):
            continue

        row_id = str(
            row.get(
                "candidate_id",
                "",
            )
        )

        row_sig = str(
            row.get(
                "candidate_signature"
            )
            or row.get(
                "signature"
            )
            or ""
        )

        if (
            row_id == cid
            or (
                row_sig
                and row_sig == sig
            )
        ):
            found.append(
                row
            )

    # ID + signature can identify the same dictionary only once
    # because loop appends a row once.
    if len(found) != 1:
        raise RuntimeError(
            "REGISTRY_TARGET_COUNT="
            + str(
                len(found)
            )
            + " candidate="
            + cid
        )

    row = found[0]

    row_sig = str(
        row.get(
            "candidate_signature"
        )
        or row.get(
            "signature"
        )
        or ""
    )

    if (
        row_sig
        and row_sig != sig
    ):
        raise RuntimeError(
            "REGISTRY_SIGNATURE_COLLISION "
            + cid
        )

    return row


def validate_plan(
    stage,
    plan,
    reg,
):
    cfg = STAGES[
        stage
    ]

    if str(
        plan.get(
            "stage",
            "",
        )
    ).upper() != stage:
        raise RuntimeError(
            "PLAN_STAGE_MISMATCH"
        )

    if plan_axis(
        plan
    ) != "TRAILING":
        raise RuntimeError(
            "PLAN_AXIS_NOT_TRAILING"
        )

    baseline = plan.get(
        "baseline",
        {}
    )

    if baseline.get(
        "standard_id"
    ) != "STANDARD_V2":
        raise RuntimeError(
            "BASELINE_NOT_STANDARD_V2"
        )

    if baseline.get(
        "candidate_id"
    ) != (
        "T1_TIGHT_BOTH_3_1P5__"
        "NEXT_1H_STATE__U30"
    ):
        raise RuntimeError(
            "BASELINE_CANDIDATE_CHANGED"
        )

    if int(
        baseline.get(
            "universe_top_n",
            -1,
        )
    ) != 30:
        raise RuntimeError(
            "BASELINE_NOT_U30"
        )

    budget = int(
        plan.get(
            "budget",
            -1,
        )
    )

    if budget != int(
        cfg[
            "budget"
        ]
    ):
        raise RuntimeError(
            "STAGE_BUDGET_MISMATCH"
        )

    candidates = plan.get(
        "candidates",
        []
    )

    if len(
        candidates
    ) != budget:
        raise RuntimeError(
            "PLAN_CANDIDATE_COUNT_MISMATCH"
        )

    normalized = [
        normalize_candidate(x)
        for x in candidates
    ]

    ids = [
        x[
            "candidate_id"
        ]
        for x in normalized
    ]

    sigs = [
        x[
            "candidate_signature"
        ]
        for x in normalized
    ]

    if len(
        set(ids)
    ) != len(ids):
        raise RuntimeError(
            "DUPLICATE_CANDIDATE_ID"
        )

    if len(
        set(sigs)
    ) != len(sigs):
        raise RuntimeError(
            "DUPLICATE_CANDIDATE_SIGNATURE"
        )

    statuses = {}

    for candidate in normalized:
        row = find_registry_row(
            reg,
            candidate,
        )

        row_stage = str(
            row.get(
                "stage",
                stage,
            )
        ).upper()

        # BROAD legacy rows may omit explicit stage.
        if (
            stage != "BROAD"
            and row_stage != stage
        ):
            raise RuntimeError(
                "REGISTRY_STAGE_MISMATCH "
                + candidate[
                    "candidate_id"
                ]
            )

        statuses[
            candidate[
                "candidate_id"
            ]
        ] = row.get(
            "status"
        )

    return normalized, statuses


def choose_stage_candidate(
    stage,
    plan,
    reg,
):
    normalized, _ = validate_plan(
        stage,
        plan,
        reg,
    )

    for candidate in normalized:
        row = find_registry_row(
            reg,
            candidate,
        )

        status = str(
            row.get(
                "status",
                "PLANNED",
            )
        )

        if status in TERMINAL_STATUSES:
            continue

        if status != "PLANNED":
            raise RuntimeError(
                "NON_TERMINAL_UNKNOWN_STATUS "
                + candidate[
                    "candidate_id"
                ]
                + "="
                + status
            )

        return candidate

    raise RuntimeError(
        "NO_UNEXECUTED_"
        + stage
        + "_CANDIDATE"
    )


def stage_counts(
    stage,
    plan,
    reg,
):
    normalized, statuses = (
        validate_plan(
            stage,
            plan,
            reg,
        )
    )

    planned = sum(
        1
        for x in normalized
        if str(
            statuses[
                x[
                    "candidate_id"
                ]
            ]
        ) == "PLANNED"
    )

    passed = sum(
        1
        for x in normalized
        if str(
            statuses[
                x[
                    "candidate_id"
                ]
            ]
        ) == "FAST_PASS"
    )

    hard_fail = sum(
        1
        for x in normalized
        if str(
            statuses[
                x[
                    "candidate_id"
                ]
            ]
        ) == "HARD_FAIL"
    )

    terminal = (
        len(
            normalized
        )
        - planned
    )

    return {
        "total":
            len(
                normalized
            ),

        "planned":
            planned,

        "fast_pass":
            passed,

        "hard_fail":
            hard_fail,

        "terminal":
            terminal,
    }


def update_registry_generic(
    stage,
    reg,
    candidate,
    result,
    fresh_trial,
    hard_fail=None,
):
    target = find_registry_row(
        reg,
        candidate,
    )

    metrics = result.get(
        "metrics",
        {}
    )

    target[
        "candidate_signature"
    ] = candidate[
        "candidate_signature"
    ]

    target[
        "signature"
    ] = candidate[
        "candidate_signature"
    ]

    target[
        "stage"
    ] = stage

    target[
        "completed_at"
    ] = now()

    target[
        "result_metrics"
    ] = dict(
        metrics
    )

    target[
        "checkpoint"
    ] = result.get(
        "checkpoint"
    )

    target[
        "resumed"
    ] = bool(
        result.get(
            "resumed",
            False,
        )
    )

    target[
        "hard_fail_reason"
    ] = hard_fail

    target[
        "status"
    ] = (
        "HARD_FAIL"
        if hard_fail
        else "FAST_PASS"
    )

    target[
        "overfit_risk"
    ] = (
        "HIGH"
        if hard_fail
        else target.get(
            "overfit_risk",
            "MEDIUM",
        )
    )

    overfit = target.setdefault(
        "overfit",
        {}
    )

    if fresh_trial:
        overfit[
            "search_trials"
        ] = int(
            overfit.get(
                "search_trials",
                0,
            ) or 0
        ) + 1

        overfit[
            "parameter_trials"
        ] = int(
            overfit.get(
                "parameter_trials",
                0,
            ) or 0
        ) + 1

        overfit[
            "family_trials"
        ] = int(
            overfit.get(
                "family_trials",
                0,
            ) or 0
        ) + 1

    overfit[
        "forward_status"
    ] = "NOT_STARTED"

    overfit[
        "overfit_risk"
    ] = (
        "HIGH"
        if hard_fail
        else target[
            "overfit_risk"
        ]
    )

    counters = reg.setdefault(
        "trial_counters",
        {}
    )

    if fresh_trial:
        counters[
            "search_trials_executed"
        ] = int(
            counters.get(
                "search_trials_executed",
                0,
            ) or 0
        ) + 1

        counters[
            "parameter_trials_executed"
        ] = int(
            counters.get(
                "parameter_trials_executed",
                0,
            ) or 0
        ) + 1

    counters[
        "planned_remaining"
    ] = sum(
        1
        for row in reg.get(
            "planned_experiments",
            []
        )
        if isinstance(
            row,
            dict,
        )
        and row.get(
            "status"
        ) == "PLANNED"
    )

    reg[
        "updated_at"
    ] = now()

    atomic_json(
        REG_FILE,
        reg,
    )


def update_state_generic(
    stage,
    state,
    candidate,
    payload,
    passed,
):
    ctrl = state.setdefault(
        "controller",
        {}
    )

    ctrl[
        "trailing_stage_evaluator_status"
    ] = (
        stage + "_PASS"
        if passed
        else stage + "_FAIL"
    )

    # Research execution remains explicitly controlled.
    ctrl[
        "research_execution"
    ] = (
        "LOCKED_STAGE_GATED"
    )

    ctrl[
        "last_trailing_stage_result"
    ] = str(
        STAGES[
            stage
        ][
            "result"
        ]
    )

    current = state.setdefault(
        "current_research",
        {}
    )

    current[
        "stage"
    ] = stage

    current[
        "status"
    ] = (
        "STAGE_TRIAL_PASS"
        if passed
        else "STAGE_TRIAL_FAIL"
    )

    current[
        "last_candidate"
    ] = candidate[
        "candidate_id"
    ]

    current[
        "next_action"
    ] = (
        "CONTINUE_"
        + stage
        if passed
        else "DIAGNOSE_"
        + stage
        + "_FAIL"
    )

    state[
        "updated_at"
    ] = now()

    atomic_json(
        STATE_FILE,
        state,
    )


def normalize_stage_payload(
    stage,
):
    path = STAGES[
        stage
    ][
        "result"
    ]

    if not path.exists():
        return

    payload = load_json(
        path
    )

    old_schema = payload.get(
        "schema"
    )

    payload[
        "legacy_schema"
    ] = old_schema

    payload[
        "schema"
    ] = (
        "B_BOT_TRAILING_STAGE_RESULT_V1"
    )

    payload[
        "chapter"
    ] = (
        "C2_STRATEGY_OPTIMIZATION"
    )

    payload[
        "research_stage"
    ] = stage

    payload[
        "checkpoint_stage"
    ] = STAGES[
        stage
    ][
        "checkpoint_stage"
    ]

    payload[
        "auto_deploy"
    ] = False

    payload[
        "live_change"
    ] = False

    atomic_json(
        path,
        payload,
    )


def sync_knowledge_after_trial(
    stage,
    hard_fail=False,
):
    reg = load_json(
        REG_FILE
    )

    plan = load_json(
        STAGES[
            stage
        ][
            "plan"
        ]
    )

    counts = stage_counts(
        stage,
        plan,
        reg,
    )

    total_search = int(
        reg.get(
            "trial_counters",
            {},
        ).get(
            "search_trials_executed",
            0,
        )
    )

    total_param = int(
        reg.get(
            "trial_counters",
            {},
        ).get(
            "parameter_trials_executed",
            0,
        )
    )

    # --------------------------------------------------------
    # Chapter state
    # --------------------------------------------------------

    chapter = load_json(
        CHAPTER_FILE,
        {
            "schema":
                "B_BOT_CHAPTER_STATE_V1",

            "chapters":
                {},
        },
    )

    chapters = chapter.setdefault(
        "chapters",
        {}
    )

    c2 = chapters.setdefault(
        "C2_STRATEGY_OPTIMIZATION",
        {},
    )

    stage_key = stage.lower()

    section = c2.setdefault(
        stage_key,
        {}
    )

    section[
        "budget"
    ] = int(
        STAGES[
            stage
        ][
            "budget"
        ]
    )

    section[
        "trials_spent"
    ] = counts[
        "terminal"
    ]

    section[
        "candidate_count"
    ] = counts[
        "total"
    ]

    section[
        "fast_pass"
    ] = counts[
        "fast_pass"
    ]

    section[
        "hard_fail"
    ] = counts[
        "hard_fail"
    ]

    if hard_fail:
        section[
            "status"
        ] = "HARD_FAIL"

        c2[
            "current_stage"
        ] = (
            stage
            + "_HARD_FAIL"
        )

        c2[
            "overfit_risk"
        ] = "HIGH"

        c2[
            "next_action"
        ] = (
            "DIAGNOSE_"
            + stage
            + "_HARD_FAIL"
        )

    elif counts[
        "planned"
    ] == 0:

        section[
            "status"
        ] = "COMPLETE"

        c2[
            "current_stage"
        ] = (
            stage
            + "_COMPLETE"
        )

        if stage == "NARROW":
            c2[
                "next_action"
            ] = (
                "ANALYZE_NARROW_"
                "AND_BUILD_CONFIRM_4"
            )

        elif stage == "CONFIRM":
            c2[
                "next_action"
            ] = (
                "TRAILING_CONFIRM_"
                "OVERFIT_REVIEW"
            )

        else:
            c2[
                "next_action"
            ] = (
                "ANALYZE_"
                + stage
            )

    else:
        section[
            "status"
        ] = "IN_PROGRESS"

        c2[
            "current_stage"
        ] = (
            stage
            + "_IN_PROGRESS"
        )

        c2[
            "next_action"
        ] = (
            "CONTINUE_"
            + stage
        )

    c2[
        "auto_promotion"
    ] = "BLOCKED"

    c2[
        "forward_status"
    ] = "NOT_STARTED"

    c2[
        "updated_at"
    ] = now()

    chapter[
        "current_chapter"
    ] = (
        "C2_STRATEGY_OPTIMIZATION"
    )

    chapter[
        "current_stage"
    ] = c2[
        "current_stage"
    ]

    chapter[
        "updated_at"
    ] = now()

    atomic_json(
        CHAPTER_FILE,
        chapter,
    )

    # --------------------------------------------------------
    # Overfit ledger
    # --------------------------------------------------------

    overfit = load_json(
        OVERFIT_FILE,
        {
            "schema":
                "B_BOT_OVERFIT_LEDGER_V1",

            "events":
                [],
        },
    )

    risk = (
        "HIGH"
        if hard_fail
        else "MEDIUM"
    )

    overfit[
        "current_snapshot"
    ] = {
        "search_trials_executed":
            total_search,

        "parameter_trials_executed":
            total_param,

        "current_axis":
            "TRAILING",

        "current_stage":
            c2[
                "current_stage"
            ],

        "stage_trials_spent":
            counts[
                "terminal"
            ],

        "current_overfit_risk":
            risk,

        "forward_evidence":
            "NONE",

        "auto_promotion_allowed":
            False,

        "updated_at":
            now(),
    }

    atomic_json(
        OVERFIT_FILE,
        overfit,
    )

    # --------------------------------------------------------
    # Research state + knowledge hashes
    # --------------------------------------------------------

    state = load_json(
        STATE_FILE,
        {},
    )

    cc = state.setdefault(
        "chapter_controller",
        {}
    )

    cc[
        "current_chapter"
    ] = (
        "C2_STRATEGY_OPTIMIZATION"
    )

    cc[
        "current_axis"
    ] = "TRAILING"

    cc[
        "current_stage"
    ] = c2[
        "current_stage"
    ]

    cc[
        "overfit_risk"
    ] = risk

    cc[
        "auto_promotion_allowed"
    ] = False

    cc[
        "next_action"
    ] = c2[
        "next_action"
    ]

    cc[
        "updated_at"
    ] = now()

    ks = state.setdefault(
        "knowledge_sync",
        {}
    )

    ks[
        "status"
    ] = "SYNCED"

    ks[
        "chapter_state_sha256"
    ] = sha256_file(
        CHAPTER_FILE
    )

    ks[
        "overfit_ledger_sha256"
    ] = sha256_file(
        OVERFIT_FILE
    )

    ks[
        "candidate_registry_sha256"
    ] = sha256_file(
        REG_FILE
    )

    ks[
        "stage_plan_sha256"
    ] = sha256_file(
        STAGES[
            stage
        ][
            "plan"
        ]
    )

    result_path = STAGES[
        stage
    ][
        "result"
    ]

    if result_path.exists():
        ks[
            "stage_result_sha256"
        ] = sha256_file(
            result_path
        )

    ks[
        "updated_at"
    ] = now()

    state[
        "updated_at"
    ] = now()

    atomic_json(
        STATE_FILE,
        state,
    )


def record_preflight(
    stage,
):
    chapter = load_json(
        CHAPTER_FILE
    )

    c2 = chapter.setdefault(
        "chapters",
        {},
    ).setdefault(
        "C2_STRATEGY_OPTIMIZATION",
        {},
    )

    c2[
        "current_stage"
    ] = (
        stage
        + "_EVALUATOR_VERIFIED_LOCKED"
    )

    c2[
        "next_action"
    ] = (
        "RUN_"
        + stage
        + "_CANARY_1"
    )

    c2[
        "auto_promotion"
    ] = "BLOCKED"

    c2[
        "updated_at"
    ] = now()

    chapter[
        "current_chapter"
    ] = (
        "C2_STRATEGY_OPTIMIZATION"
    )

    chapter[
        "current_stage"
    ] = c2[
        "current_stage"
    ]

    chapter[
        "updated_at"
    ] = now()

    atomic_json(
        CHAPTER_FILE,
        chapter,
    )

    state = load_json(
        STATE_FILE
    )

    cc = state.setdefault(
        "chapter_controller",
        {}
    )

    cc[
        "current_chapter"
    ] = (
        "C2_STRATEGY_OPTIMIZATION"
    )

    cc[
        "current_axis"
    ] = "TRAILING"

    cc[
        "current_stage"
    ] = c2[
        "current_stage"
    ]

    cc[
        "overfit_risk"
    ] = "MEDIUM"

    cc[
        "auto_promotion_allowed"
    ] = False

    cc[
        "next_action"
    ] = c2[
        "next_action"
    ]

    cc[
        "updated_at"
    ] = now()

    ks = state.setdefault(
        "knowledge_sync",
        {}
    )

    ks[
        "status"
    ] = "SYNCED"

    ks[
        "generic_stage_evaluator"
    ] = str(
        Path(__file__).resolve()
    )

    ks[
        "generic_stage_evaluator_sha256"
    ] = sha256_file(
        Path(__file__).resolve()
    )

    ks[
        "chapter_state_sha256"
    ] = sha256_file(
        CHAPTER_FILE
    )

    ks[
        "overfit_ledger_sha256"
    ] = sha256_file(
        OVERFIT_FILE
    )

    ks[
        "candidate_registry_sha256"
    ] = sha256_file(
        REG_FILE
    )

    ks[
        "stage_plan_sha256"
    ] = sha256_file(
        STAGES[
            stage
        ][
            "plan"
        ]
    )

    ks[
        "updated_at"
    ] = now()

    state[
        "updated_at"
    ] = now()

    atomic_json(
        STATE_FILE,
        state,
    )


def preflight(
    stage,
    record=False,
):
    verify_legacy()

    cfg = STAGES[
        stage
    ]

    plan = load_json(
        cfg[
            "plan"
        ]
    )

    reg = load_json(
        REG_FILE
    )

    normalized, statuses = (
        validate_plan(
            stage,
            plan,
            reg,
        )
    )

    counts = stage_counts(
        stage,
        plan,
        reg,
    )

    enabled = bool(
        plan.get(
            "execution",
            {},
        ).get(
            "enabled",
            False,
        )
    )

    print("=" * 78)
    print(
        "B-BOT TRAILING GENERIC STAGE PREFLIGHT"
    )
    print("=" * 78)

    print(
        "STAGE="
        + stage
    )

    print(
        "BUDGET="
        + str(
            cfg[
                "budget"
            ]
        )
    )

    print(
        "CANDIDATES="
        + str(
            len(
                normalized
            )
        )
    )

    print(
        "PLANNED="
        + str(
            counts[
                "planned"
            ]
        )
    )

    print(
        "FAST_PASS="
        + str(
            counts[
                "fast_pass"
            ]
        )
    )

    print(
        "HARD_FAIL="
        + str(
            counts[
                "hard_fail"
            ]
        )
    )

    print(
        "SEARCH_TRIALS="
        + str(
            reg.get(
                "trial_counters",
                {},
            ).get(
                "search_trials_executed"
            )
        )
    )

    print(
        "PARAMETER_TRIALS="
        + str(
            reg.get(
                "trial_counters",
                {},
            ).get(
                "parameter_trials_executed"
            )
        )
    )

    print(
        "EXECUTION_ENABLED="
        + str(enabled)
    )

    for candidate in normalized:
        print(
            candidate[
                "candidate_id"
            ]
            + " status="
            + str(
                statuses[
                    candidate[
                        "candidate_id"
                    ]
                ]
            )
            + " start="
            + str(
                candidate[
                    "start_pct"
                ]
            )
            + " callback="
            + str(
                candidate[
                    "callback_pct"
                ]
            )
        )

    if record:
        record_preflight(
            stage
        )

        print(
            "PREFLIGHT_METADATA_RECORDED=YES"
        )

    print(
        "GENERIC_STAGE_PREFLIGHT=PASS"
    )

    print(
        "NEW_BACKTESTS=0"
    )

    print(
        "NEW_TRIALS=0"
    )

    print(
        "AUTO_DEPLOY=OFF"
    )

    print(
        "LIVE_CHANGE=NO"
    )

    if enabled:
        print(
            "EXECUTION_LOCK=OPEN"
        )
    else:
        print(
            "EXECUTION_LOCK=LOCKED"
        )


def execute_one(
    stage,
):
    cfg = STAGES[
        stage
    ]

    plan = load_json(
        cfg[
            "plan"
        ]
    )

    reg = load_json(
        REG_FILE
    )

    validate_plan(
        stage,
        plan,
        reg,
    )

    enabled = bool(
        plan.get(
            "execution",
            {},
        ).get(
            "enabled",
            False,
        )
    )

    if not enabled:
        raise RuntimeError(
            stage
            + "_EXECUTION_LOCKED"
        )

    if stage not in (
        "BROAD",
        "NARROW",
        "CONFIRM",
    ):
        raise RuntimeError(
            "UNSUPPORTED_STAGE"
        )

    legacy = import_legacy()

    # --------------------------------------------------------
    # Patch only module-level orchestration.
    # The verified simulation and validation logic stays intact.
    # --------------------------------------------------------

    def patched_choose(
        incoming_plan,
        incoming_reg,
    ):
        return choose_stage_candidate(
            stage,
            incoming_plan,
            incoming_reg,
        )

    def patched_update_registry(
        incoming_reg,
        candidate,
        result,
        fresh_trial,
        hard_fail=None,
    ):
        return update_registry_generic(
            stage,
            incoming_reg,
            candidate,
            result,
            fresh_trial,
            hard_fail,
        )

    def patched_update_state(
        incoming_state,
        candidate,
        payload,
        passed,
    ):
        return update_state_generic(
            stage,
            incoming_state,
            candidate,
            payload,
            passed,
        )

    legacy.PLAN_FILE = cfg[
        "plan"
    ]

    legacy.REG_FILE = REG_FILE

    legacy.STATE_FILE = STATE_FILE

    legacy.CANARY_FILE = cfg[
        "result"
    ]

    legacy.STAGE = cfg[
        "checkpoint_stage"
    ]

    legacy.choose_canary = (
        patched_choose
    )

    legacy.update_registry = (
        patched_update_registry
    )

    legacy.update_state = (
        patched_update_state
    )

    old_argv = list(
        sys.argv
    )

    exit_code = 0

    try:
        sys.argv = [
            str(
                LEGACY_EVALUATOR
            ),
            "--mode",
            "canary",
        ]

        legacy.main()

    except SystemExit as exc:
        exit_code = int(
            exc.code or 0
        )

    finally:
        sys.argv = old_argv

    # Normalize generic metadata regardless of PASS/HARD FAIL.
    normalize_stage_payload(
        stage
    )

    sync_knowledge_after_trial(
        stage,
        hard_fail=(
            exit_code == 2
        ),
    )

    if exit_code not in (
        0,
        2,
    ):
        raise RuntimeError(
            "LEGACY_EXECUTION_EXIT="
            + str(
                exit_code
            )
        )

    return exit_code


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--stage",
        choices=[
            "BROAD",
            "NARROW",
            "CONFIRM",
        ],
        required=True,
    )

    parser.add_argument(
        "--mode",
        choices=[
            "preflight",
            "run-one",
        ],
        default="preflight",
    )

    parser.add_argument(
        "--record-preflight",
        action="store_true",
    )

    args = parser.parse_args()

    stage = args.stage.upper()

    if args.mode == "preflight":
        preflight(
            stage,
            record=args.record_preflight,
        )

        return

    exit_code = execute_one(
        stage
    )

    if exit_code == 0:
        print()
        print(
            "GENERIC_STAGE_TRIAL=PASS"
        )

        print(
            "STAGE="
            + stage
        )

        print(
            "AUTO_DEPLOY=OFF"
        )

        print(
            "LIVE_CHANGE=NO"
        )

        return

    print()
    print(
        "GENERIC_STAGE_TRIAL=HARD_FAIL"
    )

    print(
        "STAGE="
        + stage
    )

    print(
        "AUTO_DEPLOY=OFF"
    )

    print(
        "LIVE_CHANGE=NO"
    )

    raise SystemExit(
        2
    )


if __name__ == "__main__":
    main()