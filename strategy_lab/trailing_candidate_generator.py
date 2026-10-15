from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from datetime import datetime
from pathlib import Path


ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"
PIPE = RESULTS / "research_pipeline_v2"

STATE_FILE = RESULTS / "research_state.json"
REGISTRY_FILE = RESULTS / "candidate_registry.json"
R0_FILE = RESULTS / "r0_cause_analysis_latest.json"

ROBUST_FILE = PIPE / "robustness_confirm_u30_latest.json"
EXACT_FILE = PIPE / "original_exact_u30_latest.json"

PLAN_FILE = RESULTS / "trailing_broad_plan_latest.json"

BASELINE_STANDARD_ID = "STANDARD_V2"
CHANGED_AXIS = "TRAILING"


def now():
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def load_json(path: Path, default=None):
    if not path.exists():
        return default

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def atomic_json(path: Path, obj):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    os.replace(tmp, path)


def digest(obj):
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def round_param(x):
    return round(float(x), 6)


# ============================================================
# BASELINE
# ============================================================

def get_baseline(reg):
    rows = [
        x for x in reg.get("candidates", [])
        if isinstance(x, dict)
        and x.get("standard_id")
        == BASELINE_STANDARD_ID
    ]

    if len(rows) != 1:
        raise RuntimeError(
            "STANDARD_V2_REGISTRY_INVALID"
        )

    row = rows[0]

    trailing = row.get("trailing")

    if (
        not isinstance(trailing, list)
        or len(trailing) != 2
    ):
        raise RuntimeError(
            "STANDARD_V2_TRAILING_MISSING"
        )

    metrics = row.get(
        "historical_metrics",
        {}
    )

    return {
        "standard_id":
            BASELINE_STANDARD_ID,

        "candidate_id":
            row["candidate_id"],

        "start_pct":
            float(trailing[0]),

        "callback_pct":
            float(trailing[1]),

        "universe_top_n":
            int(
                row.get(
                    "universe_top_n",
                    30,
                )
            ),

        "frames":
            row.get("frames"),

        "historical_metrics":
            metrics,
    }


# ============================================================
# DATA FINGERPRINT
# ============================================================

def get_data_fingerprint():
    for path in (
        EXACT_FILE,
        ROBUST_FILE,
    ):
        obj = load_json(path, {})

        fp = obj.get(
            "data_fingerprint"
        )

        if isinstance(fp, dict):
            if fp.get(
                "fingerprint_hash"
            ):
                return fp

    raise RuntimeError(
        "DATA_FINGERPRINT_MISSING"
    )


# ============================================================
# WALK ROBUSTNESS RESULT
# ============================================================

def walk_records(obj):
    out = []

    def walk(x):
        if isinstance(x, dict):
            candidate = x.get(
                "candidate"
            )

            metrics = x.get(
                "metrics"
            )

            if (
                isinstance(candidate, str)
                and isinstance(
                    metrics,
                    dict,
                )
            ):
                out.append(x)

            for value in x.values():
                walk(value)

        elif isinstance(x, list):
            for value in x:
                walk(value)

    walk(obj)

    return out


def extract_pair(rec):
    config = rec.get(
        "scenario_config",
        {}
    )

    if not isinstance(config, dict):
        config = {}

    start = config.get(
        "trailing_start_pct"
    )

    callback = config.get(
        "trailing_callback_pct"
    )

    if start is None:
        extra = rec.get(
            "extra",
            {}
        )

        if isinstance(extra, dict):
            start = extra.get(
                "trailing_start_pct"
            )

            callback = extra.get(
                "trailing_callback_pct"
            )

    if (
        not isinstance(
            start,
            (int, float),
        )
        or not isinstance(
            callback,
            (int, float),
        )
    ):
        return None

    return (
        round_param(start),
        round_param(callback),
    )


def anchored_history(
    baseline,
):
    obj = load_json(
        ROBUST_FILE,
        {},
    )

    prefix = (
        baseline["candidate_id"]
        + "__TRAIL_"
    )

    rows = []
    seen = set()

    for rec in walk_records(obj):
        name = str(
            rec.get(
                "candidate",
                "",
            )
        )

        if not name.startswith(prefix):
            continue

        pair = extract_pair(rec)

        if pair is None:
            continue

        metrics = rec["metrics"]

        equity = metrics.get(
            "final_equity"
        )

        mdd = metrics.get(
            "realized_mdd_pct"
        )

        if not isinstance(
            equity,
            (int, float),
        ):
            continue

        if not isinstance(
            mdd,
            (int, float),
        ):
            continue

        liq = int(
            metrics.get(
                "liquidations",
                0,
            ) or 0
        )

        bankrupt = int(
            metrics.get(
                "bankrupt",
                0,
            ) or 0
        )

        key = (
            pair,
            round(float(equity), 8),
            round(float(mdd), 8),
            liq,
        )

        if key in seen:
            continue

        seen.add(key)

        rows.append({
            "candidate":
                name,

            "start_pct":
                pair[0],

            "callback_pct":
                pair[1],

            "final_equity":
                float(equity),

            "mdd_pct":
                float(mdd),

            "liquidations":
                liq,

            "bankrupt":
                bankrupt,
        })

    if not rows:
        raise RuntimeError(
            "NO_ANCHORED_TRAILING_HISTORY"
        )

    return rows


# ============================================================
# BEST CENTER
# ============================================================

def choose_center(
    baseline,
    rows,
):
    bmetrics = baseline[
        "historical_metrics"
    ]

    base_eq = float(
        bmetrics["final_equity"]
    )

    base_mdd = float(
        bmetrics["mdd_pct"]
    )

    safe = [
        x for x in rows
        if x["liquidations"] == 0
        and x["bankrupt"] == 0
    ]

    pareto = [
        x for x in safe
        if (
            x["final_equity"]
            > base_eq
            and x["mdd_pct"]
            > base_mdd
        )
    ]

    if pareto:
        best = max(
            pareto,
            key=lambda x:
                x["final_equity"],
        )

        method = (
            "BEST_SAFE_PARETO_"
            "ANCHORED_EVIDENCE"
        )

    else:
        best = max(
            safe,
            key=lambda x:
                x["final_equity"],
        )

        method = (
            "BEST_SAFE_ANCHORED_"
            "RETURN_FALLBACK"
        )

    return best, method


# ============================================================
# DATA-DRIVEN GRID STEP
# ============================================================

def minimum_positive_gap(values):
    vals = sorted(
        set(
            round_param(x)
            for x in values
        )
    )

    gaps = [
        b - a
        for a, b
        in zip(
            vals,
            vals[1:],
        )
        if b > a
    ]

    if not gaps:
        return None

    return min(gaps)


def derive_search_geometry(
    baseline,
    rows,
    center,
):
    starts = [
        baseline["start_pct"],
        *[
            x["start_pct"]
            for x in rows
        ],
    ]

    callbacks = [
        baseline["callback_pct"],
        *[
            x["callback_pct"]
            for x in rows
        ],
    ]

    start_gap = minimum_positive_gap(
        starts
    )

    callback_gap = minimum_positive_gap(
        callbacks
    )

    if start_gap is None:
        raise RuntimeError(
            "START_GAP_UNRESOLVED"
        )

    if callback_gap is None:
        raise RuntimeError(
            "CALLBACK_GAP_UNRESOLVED"
        )

    # 기존 실험 간격의 절반으로
    # 유망구간을 한 단계 더 촘촘히 본다.
    start_step = (
        start_gap / 2.0
    )

    callback_step = (
        callback_gap / 2.0
    )

    historical_start_min = min(
        starts
    )

    historical_start_max = max(
        starts
    )

    historical_cb_min = min(
        callbacks
    )

    historical_cb_max = max(
        callbacks
    )

    start_min = max(
        start_step,
        historical_start_min
        - start_gap,
    )

    start_max = (
        historical_start_max
        + start_gap
    )

    callback_min = max(
        callback_step,
        historical_cb_min
        - callback_gap,
    )

    callback_max = (
        historical_cb_max
        + callback_gap
    )

    return {
        "center": {
            "start_pct":
                center["start_pct"],

            "callback_pct":
                center[
                    "callback_pct"
                ],
        },

        "source_min_gap": {
            "start_pct":
                start_gap,

            "callback_pct":
                callback_gap,
        },

        "refinement_step": {
            "start_pct":
                start_step,

            "callback_pct":
                callback_step,
        },

        "bounds": {
            "start_pct": [
                start_min,
                start_max,
            ],

            "callback_pct": [
                callback_min,
                callback_max,
            ],
        },

        "method":
            "HALF_EXISTING_MINIMUM_"
            "POSITIVE_PARAMETER_GAP",
    }


# ============================================================
# GENERATE BROAD POOL
# ============================================================

OFFSETS = [
    (0, -1),
    (0, +1),
    (-1, 0),
    (+1, 0),

    (0, -2),
    (0, +2),
    (-2, 0),
    (+2, 0),

    (-1, -1),
    (-1, +1),
    (+1, -1),
    (+1, +1),

    (-2, -1),
    (-2, +1),
    (+2, -1),
    (+2, +1),

    (-1, -2),
    (-1, +2),
    (+1, -2),
    (+1, +2),

    (-2, -2),
    (-2, +2),
    (+2, -2),
    (+2, +2),

    (0, -3),
    (0, +3),
    (-3, 0),
    (+3, 0),
]


def encode_num(x):
    s = (
        f"{x:.6f}"
        .rstrip("0")
        .rstrip(".")
    )

    return s.replace(
        ".",
        "P",
    )


def build_candidates(
    baseline,
    history,
    geometry,
    budget,
    fingerprint,
    state,
    reg,
):
    center_start = geometry[
        "center"
    ]["start_pct"]

    center_cb = geometry[
        "center"
    ]["callback_pct"]

    start_step = geometry[
        "refinement_step"
    ]["start_pct"]

    callback_step = geometry[
        "refinement_step"
    ]["callback_pct"]

    smin, smax = geometry[
        "bounds"
    ]["start_pct"]

    cmin, cmax = geometry[
        "bounds"
    ]["callback_pct"]

    tested_pairs = {
        (
            round_param(
                x["start_pct"]
            ),
            round_param(
                x["callback_pct"]
            ),
        )
        for x in history
    }

    baseline_pair = (
        round_param(
            baseline["start_pct"]
        ),
        round_param(
            baseline["callback_pct"]
        ),
    )

    existing_signatures = set(
        reg.get(
            "experiment_signatures",
            [],
        )
    )

    source_manifest = (
        state
        .get(
            "source_snapshot",
            {}
        )
        .get(
            "manifest_sha256"
        )
    )

    planned = []
    skipped = []

    for so, co in OFFSETS:
        start = round_param(
            center_start
            + so * start_step
        )

        callback = round_param(
            center_cb
            + co * callback_step
        )

        pair = (
            start,
            callback,
        )

        if (
            start < smin
            or start > smax
            or callback < cmin
            or callback > cmax
        ):
            continue

        # trailing callback은
        # activation threshold보다 작아야 한다.
        if (
            start <= 0
            or callback <= 0
            or callback >= start
        ):
            continue

        if pair == baseline_pair:
            skipped.append({
                "pair": pair,
                "reason":
                    "STANDARD_V2_BASELINE",
            })
            continue

        if pair in tested_pairs:
            skipped.append({
                "pair": pair,
                "reason":
                    "EXISTING_ANCHORED_RESULT",
            })
            continue

        spec = {
            "schema":
                "B_BOT_EXPERIMENT_SPEC_V1",

            "baseline_standard_id":
                BASELINE_STANDARD_ID,

            "parent_candidate":
                baseline[
                    "candidate_id"
                ],

            "changed_axis":
                CHANGED_AXIS,

            "stage":
                "BROAD",

            "parameters": {
                "trailing_start_pct":
                    start,

                "trailing_callback_pct":
                    callback,
            },

            "fixed": {
                "universe_top_n":
                    baseline[
                        "universe_top_n"
                    ],

                "frames":
                    baseline["frames"],

                "all_non_trailing_policy":
                    "STANDARD_V2_FROZEN",
            },

            "data_fingerprint_hash":
                fingerprint[
                    "fingerprint_hash"
                ],

            "source_manifest_sha256":
                source_manifest,
        }

        sig = digest(spec)

        if sig in existing_signatures:
            skipped.append({
                "pair": pair,
                "reason":
                    "EXPERIMENT_SIGNATURE_EXISTS",
            })
            continue

        candidate_id = (
            "SV2_TRAIL_BROAD"
            + "__S"
            + encode_num(start)
            + "__C"
            + encode_num(callback)
        )

        planned.append({
            "candidate_id":
                candidate_id,

            "candidate_signature":
                sig,

            "baseline_standard_id":
                BASELINE_STANDARD_ID,

            "parent_candidate":
                baseline[
                    "candidate_id"
                ],

            "changed_axis":
                CHANGED_AXIS,

            "stage":
                "BROAD",

            "parameters":
                spec["parameters"],

            "fixed":
                spec["fixed"],

            "data_fingerprint_hash":
                spec[
                    "data_fingerprint_hash"
                ],

            "source_manifest_sha256":
                source_manifest,

            "status":
                "PLANNED",

            "search_trial_spend":
                0,

            "overfit": {
                "search_trials":
                    0,

                "family_trials":
                    0,

                "parameter_trials":
                    0,

                "blind_uses":
                    0,

                "overfit_risk":
                    "NOT_EVALUATED",
            },
        })

        existing_signatures.add(sig)

        if len(planned) >= budget:
            break

    if len(planned) != budget:
        raise RuntimeError(
            "INSUFFICIENT_UNTESTED_BROAD_"
            f"CANDIDATES={len(planned)}/"
            f"{budget}"
        )

    return planned, skipped


# ============================================================
# PLAN
# ============================================================

def plan():
    state = load_json(
        STATE_FILE,
        {},
    )

    reg = load_json(
        REGISTRY_FILE,
        {},
    )

    r0 = load_json(
        R0_FILE,
        {},
    )

    if (
        r0
        .get(
            "selected_next_axis",
            {}
        )
        .get("axis")
        != "TRAILING"
    ):
        raise RuntimeError(
            "R0_SELECTED_AXIS_NOT_TRAILING"
        )

    ctrl = state.get(
        "controller",
        {}
    )

    budget_obj = ctrl.get(
        "research_budget",
        {}
    )

    broad_budget = budget_obj.get(
        "BROAD"
    )

    if (
        not isinstance(
            broad_budget,
            int,
        )
        or broad_budget <= 0
    ):
        raise RuntimeError(
            "BROAD_BUDGET_INVALID"
        )

    baseline = get_baseline(reg)

    if (
        baseline["universe_top_n"]
        != 30
    ):
        raise RuntimeError(
            "STANDARD_V2_U30_REQUIRED"
        )

    fingerprint = (
        get_data_fingerprint()
    )

    history = anchored_history(
        baseline
    )

    center, center_method = (
        choose_center(
            baseline,
            history,
        )
    )

    geometry = (
        derive_search_geometry(
            baseline,
            history,
            center,
        )
    )

    planned, skipped = (
        build_candidates(
            baseline,
            history,
            geometry,
            broad_budget,
            fingerprint,
            state,
            reg,
        )
    )

    result = {
        "schema":
            "B_BOT_TRAILING_BROAD_PLAN_V1",

        "created_at":
            now(),

        "baseline":
            baseline,

        "changed_axis":
            CHANGED_AXIS,

        "stage":
            "BROAD",

        "budget":
            broad_budget,

        "data_fingerprint":
            fingerprint,

        "historical_anchored_evidence":
            history,

        "center_selection": {
            "method":
                center_method,

            "candidate":
                center,
        },

        "search_geometry":
            geometry,

        "candidates":
            planned,

        "skipped_duplicates":
            skipped,

        "safety": {
            "new_backtests_executed":
                False,

            "live_change":
                False,

            "auto_deploy":
                False,

            "research_execution":
                "LOCKED",
        },
    }

    atomic_json(
        PLAN_FILE,
        result,
    )

    # ========================================================
    # REGISTRY
    # ========================================================

    reg.setdefault(
        "experiment_signatures",
        []
    )

    reg.setdefault(
        "planned_experiments",
        []
    )

    known_sig = set(
        reg["experiment_signatures"]
    )

    known_plan = {
        x.get(
            "candidate_signature"
        )
        for x in reg[
            "planned_experiments"
        ]
        if isinstance(x, dict)
    }

    for row in planned:
        sig = row[
            "candidate_signature"
        ]

        if sig not in known_sig:
            reg[
                "experiment_signatures"
            ].append(sig)

            known_sig.add(sig)

        if sig not in known_plan:
            reg[
                "planned_experiments"
            ].append(row)

            known_plan.add(sig)

    # 너무 커지지 않게 최근 계획만 유지.
    reg[
        "planned_experiments"
    ] = reg[
        "planned_experiments"
    ][-100:]

    reg.setdefault(
        "trial_counters",
        {}
    )

    reg["trial_counters"].setdefault(
        "search_trials_executed",
        0,
    )

    reg["trial_counters"].setdefault(
        "parameter_trials_executed",
        0,
    )

    reg["trial_counters"][
        "planned_not_executed"
    ] = sum(
        1
        for x
        in reg[
            "planned_experiments"
        ]
        if x.get("status")
        == "PLANNED"
    )

    reg["updated_at"] = now()

    atomic_json(
        REGISTRY_FILE,
        reg,
    )

    # ========================================================
    # STATE
    # ========================================================

    ctrl = state.setdefault(
        "controller",
        {}
    )

    ctrl[
        "dispatch_status"
    ] = (
        "TRAILING_BROAD_PLAN_READY"
    )

    ctrl[
        "research_execution"
    ] = (
        "LOCKED_UNTIL_TRAILING_"
        "EVALUATOR_VERIFIED"
    )

    ctrl[
        "selected_next_axis"
    ] = "TRAILING"

    ctrl[
        "active_plan"
    ] = str(PLAN_FILE)

    state[
        "current_research"
    ] = {
        "research_id":
            "R2_TRAILING_BROAD_001",

        "objective":
            "TRAILING",

        "hypothesis":
            (
                "STANDARD_V2 trailing "
                "neighborhood contains a "
                "robust return/MDD plateau."
            ),

        "baseline":
            BASELINE_STANDARD_ID,

        "variables": [
            "trailing_start_pct",
            "trailing_callback_pct",
        ],

        "fixed_variables":
            "ALL_OTHER_STANDARD_V2",

        "research_budget": {
            "BROAD":
                broad_budget,

            "NARROW":
                budget_obj.get(
                    "NARROW"
                ),

            "CONFIRM":
                budget_obj.get(
                    "CONFIRM"
                ),
        },

        "stage":
            "BROAD",

        "status":
            "PLAN_READY",

        "next_action":
            "VERIFY_TRAILING_EVALUATOR",
    }

    state["updated_at"] = now()

    atomic_json(
        STATE_FILE,
        state,
    )

    # ========================================================
    # OUTPUT
    # ========================================================

    print("=" * 78)
    print(
        "STANDARD_V2 TRAILING "
        "BROAD GENERATOR"
    )
    print("=" * 78)

    print(
        "BASELINE="
        + baseline["candidate_id"]
    )

    print(
        "BASELINE_TRAILING="
        + f"{baseline['start_pct']}/"
        + f"{baseline['callback_pct']}"
    )

    print(
        "UNIVERSE_TOP_N="
        + str(
            baseline[
                "universe_top_n"
            ]
        )
    )

    print(
        "CENTER="
        + f"{center['start_pct']}/"
        + f"{center['callback_pct']}"
    )

    print(
        "CENTER_METHOD="
        + center_method
    )

    print(
        "START_STEP="
        + str(
            geometry[
                "refinement_step"
            ]["start_pct"]
        )
    )

    print(
        "CALLBACK_STEP="
        + str(
            geometry[
                "refinement_step"
            ]["callback_pct"]
        )
    )

    print(
        "BROAD_BUDGET="
        + str(
            broad_budget
        )
    )

    print()

    for i, row in enumerate(
        planned,
        1,
    ):
        p = row["parameters"]

        print(
            f"{i:02d} "
            f"{row['candidate_id']} "
            f"start="
            f"{p['trailing_start_pct']} "
            f"callback="
            f"{p['trailing_callback_pct']}"
        )

    print()

    print(
        "ANCHORED_HISTORY="
        + str(len(history))
    )

    print(
        "DUPLICATES_SKIPPED="
        + str(len(skipped))
    )

    print(
        "PLANNED="
        + str(len(planned))
    )

    print(
        "SEARCH_TRIALS_SPENT=0"
    )

    print(
        "NEW_BACKTESTS_EXECUTED=0"
    )

    print(
        "RESEARCH_EXECUTION=LOCKED"
    )

    print(
        "AUTO_DEPLOY=OFF"
    )

    print(
        "LIVE_CHANGE=NO"
    )

    print(
        "PLAN_FILE="
        + str(PLAN_FILE)
    )

    print(
        "TRAILING_BROAD_GENERATOR=PASS"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=[
            "plan",
        ],
        default="plan",
    )

    args = parser.parse_args()

    if args.mode == "plan":
        plan()


if __name__ == "__main__":
    main()