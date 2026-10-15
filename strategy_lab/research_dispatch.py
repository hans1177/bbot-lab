from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
from datetime import datetime
from pathlib import Path


ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"
PIPE = RESULTS / "research_pipeline_v2"
CHECKPOINTS = PIPE / "checkpoints"

STATE_FILE = RESULTS / "research_state.json"
REGISTRY_FILE = RESULTS / "candidate_registry.json"

R0_LATEST = RESULTS / "r0_cause_analysis_latest.json"


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


def obj_hash(obj):
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


# ============================================================
# BASELINE
# ============================================================

def get_baseline(reg):
    rows = reg.get("candidates", [])

    matches = [
        x for x in rows
        if isinstance(x, dict)
        and x.get("standard_id")
        == "STANDARD_V2"
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "STANDARD_V2_BASELINE_INVALID"
        )

    row = matches[0]
    metrics = row.get(
        "historical_metrics",
        {}
    )

    equity = metrics.get("final_equity")
    mdd = metrics.get("mdd_pct")
    liq = metrics.get("liquidations")

    if not isinstance(
        equity,
        (int, float),
    ):
        raise RuntimeError(
            "BASELINE_EQUITY_MISSING"
        )

    if not isinstance(
        mdd,
        (int, float),
    ):
        raise RuntimeError(
            "BASELINE_MDD_MISSING"
        )

    if int(liq or 0) != 0:
        raise RuntimeError(
            "BASELINE_LIQUIDATION_INVALID"
        )

    return {
        "standard_id": "STANDARD_V2",
        "final_equity": float(equity),
        "mdd_pct": float(mdd),
        "liquidations": int(liq or 0),
    }


# ============================================================
# EXISTING CHECKPOINT COUNTS -> DATA-DRIVEN BUDGET
# ============================================================

EXPLORATORY_STAGE_NAMES = [
    "control_sl_v1",
    "opposite_exit_source_v1",
    "opposite_timing_v1",
    "reentry_material_event_trigger_v1",
    "reentry_material_state_filter_v1",
    "reentry_structure_v1",
    "trailing_v1",
    "universe_size_v1",
]


def count_json_files(path):
    if not path.exists():
        return 0

    return len(
        list(
            path.glob("*.json")
        )
    )


def derive_budget():
    counts = {}

    for name in EXPLORATORY_STAGE_NAMES:
        counts[name] = count_json_files(
            CHECKPOINTS / name
        )

    positive = [
        v for v in counts.values()
        if v > 0
    ]

    if not positive:
        raise RuntimeError(
            "NO_EXISTING_EXPLORATORY_CHECKPOINTS"
        )

    broad = max(positive)

    narrow = int(
        math.ceil(
            statistics.median(positive)
        )
    )

    confirm = int(
        math.ceil(
            math.sqrt(broad)
        )
    )

    # 구조상 반드시 축소되는 funnel 유지.
    narrow = min(
        narrow,
        max(1, broad - 1),
    )

    confirm = min(
        confirm,
        max(1, narrow - 1),
    )

    return {
        "BROAD": broad,
        "NARROW": narrow,
        "CONFIRM": confirm,

        "derivation": {
            "method":
                "EXISTING_PIPELINE_CHECKPOINT_COUNTS",

            "stage_counts": counts,

            "broad":
                "MAX_EXISTING_EXPLORATORY_STAGE",

            "narrow":
                "CEIL_MEDIAN_EXISTING_EXPLORATORY_STAGE",

            "confirm":
                "CEIL_SQRT_BROAD",

            "manual_arbitrary_budget":
                False,
        },
    }


# ============================================================
# RESULT EXTRACTION
# ============================================================

AXIS_FILES = {
    "CONTROL_SL":
        PIPE / "control_sl_latest.json",

    "TRAILING":
        PIPE / "trailing_latest.json",

    "REENTRY_STRUCTURE":
        PIPE / "reentry_structure_latest.json",

    "REENTRY_MATERIAL":
        PIPE / "reentry_material_latest.json",

    "OPPOSITE_EXIT":
        PIPE / "opposite_exit_source_latest.json",

    "OPPOSITE_REOPEN":
        PIPE / "opposite_timing_latest.json",

    "UNIVERSE":
        PIPE / "universe_size_latest.json",
}



BASELINE_STANDARD_ID = "STANDARD_V2"

BASELINE_CANDIDATE_ID = (
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30"
)

BASELINE_STRUCTURE_ID = (
    "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE"
)


def baseline_anchor_status(row):
    """
    R0 attribution용 strict anchor.

    기존 역사 결과는 모두 knowledge로 보존하지만,
    다음 연구축 선택에는 STANDARD_V2에서
    해당 축만 달라진 증거만 사용한다.

    앞으로 새 generator는 명시적 metadata:
      baseline_standard_id=STANDARD_V2
      changed_axis=<AXIS>
    를 기록하도록 한다.
    """

    axis = str(
        row.get("axis", "")
    ).upper()

    candidate = str(
        row.get("candidate", "")
    )

    # 미래 신규 generator용 명시적 provenance
    if (
        row.get("baseline_standard_id")
        == BASELINE_STANDARD_ID
        and str(
            row.get("changed_axis", "")
        ).upper()
        == axis
    ):
        return True

    # --------------------------------------------------------
    # 현재 보유한 역사 evidence 중
    # STANDARD_V2에 직접 anchor 가능한 것만 허용
    # --------------------------------------------------------

    if axis == "TRAILING":
        # 현재 V2 U30 구조 그대로,
        # trailing 한 축만 perturb한 robustness evidence
        return candidate.startswith(
            BASELINE_CANDIDATE_ID
            + "__TRAIL_"
        )

    if axis == "UNIVERSE":
        # 전략/exit/reopen/trailing 구조는
        # T1...NEXT_1H_STATE로 동일하고
        # U 숫자만 바뀐 후보.
        pattern = (
            "^"
            + re.escape(
                BASELINE_STRUCTURE_ID
            )
            + r"__U\d+$"
        )

        return (
            re.fullmatch(
                pattern,
                candidate,
            )
            is not None
        )

    # 기존 Control-SL / Reentry / Opposite 연구는
    # 순차 연구 중 다른 parent에서 수행된 결과가 섞여 있으므로
    # historical knowledge로만 보존.
    #
    # 해당 축은 이후 STANDARD_V2 anchor generator로 다시 연구.
    return False



def metric_dict(obj):
    if not isinstance(obj, dict):
        return False

    metrics = obj.get("metrics")

    if not isinstance(metrics, dict):
        return False

    return (
        isinstance(
            metrics.get("final_equity"),
            (int, float),
        )
        and isinstance(
            metrics.get("realized_mdd_pct"),
            (int, float),
        )
    )


def walk_metric_records(obj):
    out = []

    def walk(x):
        if isinstance(x, dict):
            if metric_dict(x):
                out.append(x)

            for value in x.values():
                walk(value)

        elif isinstance(x, list):
            for value in x:
                walk(value)

    walk(obj)
    return out


def record_name(rec):
    for key in (
        "candidate",
        "candidate_id",
        "name",
        "scenario",
        "label",
    ):
        value = rec.get(key)

        if value not in (
            None,
            "",
        ):
            return str(value)

    sc = rec.get(
        "scenario_config",
        {}
    )

    if isinstance(sc, dict):
        return (
            "SCENARIO_"
            + obj_hash(sc)[:12]
        )

    return (
        "UNKNOWN_"
        + obj_hash(rec)[:12]
    )


def normalize_record(
    axis,
    source,
    rec,
    baseline,
):
    metrics = rec["metrics"]

    equity = float(
        metrics["final_equity"]
    )

    mdd = float(
        metrics["realized_mdd_pct"]
    )

    liquidations = int(
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

    safe = (
        liquidations == 0
        and bankrupt == 0
    )

    eq_ratio = (
        equity
        / baseline["final_equity"]
    )

    mdd_delta = (
        mdd
        - baseline["mdd_pct"]
    )

    return_better = (
        safe
        and equity
        > baseline["final_equity"]
    )

    mdd_better = (
        safe
        and mdd
        > baseline["mdd_pct"]
    )

    pareto = (
        return_better
        and mdd_better
    )

    return {
        "axis": axis,
        "candidate":
            record_name(rec),

        "source_file":
            source.name,

        "final_equity":
            equity,

        "equity_ratio_vs_v2":
            eq_ratio,

        "mdd_pct":
            mdd,

        "mdd_delta_pp_vs_v2":
            mdd_delta,

        "liquidations":
            liquidations,

        "bankrupt":
            bankrupt,

        "safe":
            safe,

        "return_better":
            return_better,

        "mdd_better":
            mdd_better,

        "pareto_improvement":
            pareto,

        "scenario":
            rec.get("scenario"),

        "scenario_config":
            rec.get(
                "scenario_config"
            ),
    }


def load_axis_file(
    axis,
    path,
    baseline,
):
    if not path.exists():
        return []

    obj = load_json(path, {})

    rows = []

    for rec in walk_metric_records(obj):
        rows.append(
            normalize_record(
                axis,
                path,
                rec,
                baseline,
            )
        )

    return rows


# ============================================================
# ROBUSTNESS CONFIRM EVIDENCE
# ============================================================

def robustness_axis(rec):
    candidate = str(
        rec.get("candidate", "")
    ).upper()

    scenario = str(
        rec.get("scenario", "")
    ).upper()

    config = rec.get(
        "scenario_config",
        {}
    )

    family = ""

    if isinstance(config, dict):
        family = str(
            config.get(
                "family",
                "",
            )
        ).upper()

    joined = " ".join(
        [
            candidate,
            scenario,
            family,
        ]
    )

    if "TRAIL" in joined:
        return "TRAILING"

    if (
        "__U25" in candidate
        or "__U30" in candidate
        or "__U35" in candidate
        or "UNIVERSE" in joined
    ):
        return "UNIVERSE"

    return None


def load_robustness_neighbors(
    baseline,
):
    path = (
        PIPE
        / "robustness_confirm_u30_latest.json"
    )

    if not path.exists():
        return []

    obj = load_json(path, {})

    rows = []

    for rec in walk_metric_records(obj):
        axis = robustness_axis(rec)

        if axis is None:
            continue

        row = normalize_record(
            axis,
            path,
            rec,
            baseline,
        )

        row[
            "robustness_neighbor"
        ] = True

        rows.append(row)

    return rows


# ============================================================
# DEDUPE
# ============================================================

def dedupe(rows):
    out = []
    seen = set()

    for row in rows:
        key = (
            row["axis"],
            row["candidate"],
            round(
                row["final_equity"],
                8,
            ),
            round(
                row["mdd_pct"],
                8,
            ),
            row["liquidations"],
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(row)

    return out


# ============================================================
# AXIS SUMMARY
# ============================================================

def best_by_equity(rows):
    safe = [
        x for x in rows
        if x["safe"]
    ]

    if not safe:
        return None

    return max(
        safe,
        key=lambda x:
            x["final_equity"],
    )


def best_by_mdd(rows):
    safe = [
        x for x in rows
        if x["safe"]
    ]

    if not safe:
        return None

    return max(
        safe,
        key=lambda x:
            x["mdd_pct"],
    )


def compact_candidate(row):
    if row is None:
        return None

    return {
        "candidate":
            row["candidate"],

        "source_file":
            row["source_file"],

        "final_equity":
            row["final_equity"],

        "equity_ratio_vs_v2":
            row["equity_ratio_vs_v2"],

        "mdd_pct":
            row["mdd_pct"],

        "mdd_delta_pp_vs_v2":
            row["mdd_delta_pp_vs_v2"],

        "liquidations":
            row["liquidations"],

        "pareto_improvement":
            row["pareto_improvement"],
    }


def summarize_axis(axis, rows):
    safe = [
        x for x in rows
        if x["safe"]
    ]

    pareto = [
        x for x in safe
        if x["pareto_improvement"]
    ]

    return_better = [
        x for x in safe
        if x["return_better"]
    ]

    mdd_better = [
        x for x in safe
        if x["mdd_better"]
    ]

    equities = [
        x["final_equity"]
        for x in safe
        if x["final_equity"] > 0
    ]

    mdds = [
        x["mdd_pct"]
        for x in safe
    ]

    sensitivity = {
        "safe_candidates":
            len(safe),

        "equity_max_min_ratio":
            (
                max(equities)
                / min(equities)
                if len(equities) >= 2
                and min(equities) > 0
                else None
            ),

        "mdd_range_pp":
            (
                max(mdds)
                - min(mdds)
                if len(mdds) >= 2
                else None
            ),
    }

    return {
        "axis": axis,

        "records":
            len(rows),

        "safe_records":
            len(safe),

        "liquidation_records":
            sum(
                1 for x in rows
                if x["liquidations"] > 0
            ),

        "pareto_improvement_count":
            len(pareto),

        "return_better_count":
            len(return_better),

        "mdd_better_count":
            len(mdd_better),

        "best_return":
            compact_candidate(
                best_by_equity(rows)
            ),

        "best_mdd":
            compact_candidate(
                best_by_mdd(rows)
            ),

        "pareto_candidates": [
            compact_candidate(x)
            for x in sorted(
                pareto,
                key=lambda x:
                    x["final_equity"],
                reverse=True,
            )[:10]
        ],

        "sensitivity":
            sensitivity,
    }


# ============================================================
# NEXT AXIS SELECTION
# ============================================================

ROADMAP_ORDER = [
    "CONTROL_SL",
    "TRAILING",
    "REENTRY_STRUCTURE",
    "REENTRY_MATERIAL",
    "OPPOSITE_EXIT",
    "OPPOSITE_REOPEN",
    "UNIVERSE",
]


def priority_tuple(summary):
    # 가중치 score 사용 금지.
    # 명시적인 lexicographic evidence ordering.
    return (
        summary[
            "pareto_improvement_count"
        ],
        summary[
            "return_better_count"
        ],
        summary[
            "mdd_better_count"
        ],
    )


def select_next_axis(summaries):
    valid = [
        x for x in summaries
        if x["safe_records"] > 0
    ]

    if not valid:
        raise RuntimeError(
            "NO_VALID_AXIS_EVIDENCE"
        )

    best_tuple = max(
        priority_tuple(x)
        for x in valid
    )

    tied = [
        x for x in valid
        if priority_tuple(x)
        == best_tuple
    ]

    if len(tied) == 1:
        chosen = tied[0]
    else:
        order = {
            name: i
            for i, name
            in enumerate(
                ROADMAP_ORDER
            )
        }

        chosen = min(
            tied,
            key=lambda x:
                order.get(
                    x["axis"],
                    999,
                ),
        )

    return {
        "axis":
            chosen["axis"],

        "selection_method":
            "LEXICOGRAPHIC_EVIDENCE_NO_WEIGHTED_SCORE",

        "priority_tuple": {
            "pareto_improvement_count":
                chosen[
                    "pareto_improvement_count"
                ],

            "return_better_count":
                chosen[
                    "return_better_count"
                ],

            "mdd_better_count":
                chosen[
                    "mdd_better_count"
                ],
        },

        "reason":
            (
                "Pareto evidence first; "
                "then return evidence; "
                "then MDD evidence; "
                "roadmap order only breaks exact ties."
            ),
    }


# ============================================================
# REGISTRY KNOWLEDGE IMPORT
# ============================================================

def import_knowledge(
    reg,
    rows,
    summaries,
    selected,
):
    knowledge = reg.setdefault(
        "research_knowledge",
        {}
    )

    historical = knowledge.setdefault(
        "historical_axis_evidence",
        {},
    )

    for summary in summaries:
        historical[
            summary["axis"]
        ] = summary

    tested_names = set(
        knowledge.get(
            "historical_tested_candidate_names",
            [],
        )
    )

    evidence_signatures = set(
        knowledge.get(
            "historical_evidence_signatures",
            [],
        )
    )

    for row in rows:
        tested_names.add(
            row["candidate"]
        )

        evidence_signatures.add(
            obj_hash({
                "axis":
                    row["axis"],

                "candidate":
                    row["candidate"],

                "final_equity":
                    row["final_equity"],

                "mdd_pct":
                    row["mdd_pct"],

                "liquidations":
                    row["liquidations"],
            })
        )

    knowledge[
        "historical_tested_candidate_names"
    ] = sorted(tested_names)

    knowledge[
        "historical_evidence_signatures"
    ] = sorted(evidence_signatures)

    knowledge[
        "r0_selected_next_axis"
    ] = selected

    knowledge[
        "historical_import_updated_at"
    ] = now()


# ============================================================
# R0
# ============================================================

def run_r0():
    state = load_json(
        STATE_FILE,
        {},
    )

    reg = load_json(
        REGISTRY_FILE,
        {},
    )

    baseline = get_baseline(reg)

    budget = derive_budget()

    rows = []

    for axis, path in AXIS_FILES.items():
        rows.extend(
            load_axis_file(
                axis,
                path,
                baseline,
            )
        )

    rows.extend(
        load_robustness_neighbors(
            baseline
        )
    )

    rows = dedupe(rows)

    # 전체 역사결과는 knowledge로 보존.
    all_rows = rows

    # R0 다음 연구축 선택은
    # STANDARD_V2 baseline-anchored evidence만 사용.
    anchored_rows = [
        row
        for row in all_rows
        if baseline_anchor_status(row)
    ]

    if not anchored_rows:
        raise RuntimeError(
            "NO_BASELINE_ANCHORED_EVIDENCE"
        )

    by_axis = {}

    for row in anchored_rows:
        by_axis.setdefault(
            row["axis"],
            []
        ).append(row)

    summaries = [
        summarize_axis(
            axis,
            axis_rows,
        )
        for axis, axis_rows
        in sorted(by_axis.items())
    ]

    selected = select_next_axis(
        summaries
    )

    result = {
        "schema":
            "B_BOT_R0_CAUSE_ANALYSIS_V1",

        "created_at":
            now(),

        "baseline":
            baseline,

        "research_budget":
            budget,

        "axes":
            summaries,

        "selected_next_axis":
            selected,

        "evidence_counts": {
            "historical_total":
                len(all_rows),

            "baseline_anchored":
                len(anchored_rows),

            "legacy_unanchored":
                len(all_rows)
                - len(anchored_rows),
        },

        "methodology": {
            "weighted_score":
                False,

            "liquidation_policy":
                "ANY_EXCHANGE_LIQUIDATION_HARD_FAIL",

            "comparison":
                "STANDARD_V2",

            "selection":
                "PARETO_THEN_RETURN_THEN_MDD",

            "new_backtests_executed":
                False,

            "historical_results_reused":
                True,

            "axis_attribution":
                "STANDARD_V2_BASELINE_ANCHORED_ONLY",

            "legacy_cross_parent_results":
                "KNOWLEDGE_ONLY_NOT_USED_FOR_NEXT_AXIS_SELECTION",
        },
    }

    atomic_json(
        R0_LATEST,
        result,
    )

    # ------------------------------------------
    # Registry update
    # ------------------------------------------

    import_knowledge(
        reg,
        rows,
        summaries,
        selected,
    )

    reg["updated_at"] = now()

    atomic_json(
        REGISTRY_FILE,
        reg,
    )

    # ------------------------------------------
    # State update
    # ------------------------------------------

    ctrl = state.setdefault(
        "controller",
        {}
    )

    ctrl[
        "research_budget"
    ] = budget

    ctrl[
        "dispatch_status"
    ] = (
        "R0_ANALYSIS_COMPLETE_"
        "NEW_EXPERIMENTS_STILL_LOCKED"
    )

    ctrl[
        "research_execution"
    ] = (
        "LOCKED_UNTIL_AXIS_GENERATOR_"
        "AND_DUPLICATE_SIGNATURE_GATE_VERIFIED"
    )

    ctrl[
        "r0_result"
    ] = str(R0_LATEST)

    ctrl[
        "selected_next_axis"
    ] = selected["axis"]

    # R0 DONE, 선택 축 NEXT
    queue = state.get(
        "research_queue",
        []
    )

    name_map = {
        "CONTROL_SL":
            "CONTROL_SL",

        "TRAILING":
            "TRAILING",

        "REENTRY_STRUCTURE":
            "REENTRY",

        "REENTRY_MATERIAL":
            "REENTRY",

        "OPPOSITE_EXIT":
            "OPPOSITE_EXIT_REOPEN",

        "OPPOSITE_REOPEN":
            "OPPOSITE_EXIT_REOPEN",

        "UNIVERSE":
            "UNIVERSE_SYMBOL_FITNESS",
    }

    target_name = name_map.get(
        selected["axis"]
    )

    for item in queue:
        if not isinstance(item, dict):
            continue

        if item.get("id") == "R0":
            item["status"] = "DONE"

        elif (
            target_name
            and item.get("name")
            == target_name
        ):
            item["status"] = "NEXT"

    state[
        "current_research"
    ] = {
        "research_id":
            "R0_CAUSE_ANALYSIS_001",

        "objective":
            "V2_DRAWDOWN_RETURN_CAUSE",

        "status":
            "PASS",

        "phase":
            "ANALYSIS_COMPLETE",

        "selected_next_axis":
            selected["axis"],

        "next_action":
            (
                "BUILD_"
                + selected["axis"]
                + "_BROAD_GENERATOR"
            ),
    }

    state["updated_at"] = now()

    atomic_json(
        STATE_FILE,
        state,
    )

    # ------------------------------------------
    # Console summary
    # ------------------------------------------

    print("=" * 78)
    print("B-BOT R0 CAUSE ANALYSIS")
    print("=" * 78)

    print(
        "BASELINE=STANDARD_V2"
    )

    print(
        "BASELINE_EQUITY="
        + str(
            baseline[
                "final_equity"
            ]
        )
    )

    print(
        "BASELINE_MDD="
        + str(
            baseline[
                "mdd_pct"
            ]
        )
    )

    print(
        "BUDGET_BROAD="
        + str(
            budget["BROAD"]
        )
    )

    print(
        "BUDGET_NARROW="
        + str(
            budget["NARROW"]
        )
    )

    print(
        "BUDGET_CONFIRM="
        + str(
            budget["CONFIRM"]
        )
    )

    print()

    for s in summaries:
        print(
            f"{s['axis']}: "
            f"records={s['records']} "
            f"safe={s['safe_records']} "
            f"pareto={s['pareto_improvement_count']} "
            f"return_better={s['return_better_count']} "
            f"mdd_better={s['mdd_better_count']} "
            f"liquidations={s['liquidation_records']}"
        )

        br = s["best_return"]

        if br:
            print(
                "  BEST_RETURN="
                + br["candidate"]
                + " ratio="
                + f"{br['equity_ratio_vs_v2']:.4f}"
                + " mdd="
                + f"{br['mdd_pct']:.6f}"
            )

    print()

    print(
        "NEXT_RESEARCH_AXIS="
        + selected["axis"]
    )

    print(
        "SELECTION="
        + selected[
            "selection_method"
        ]
    )

    print(
        "HISTORICAL_RESULTS_IMPORTED="
        + str(len(all_rows))
    )

    print(
        "BASELINE_ANCHORED_EVIDENCE="
        + str(len(anchored_rows))
    )

    print(
        "LEGACY_UNANCHORED_EVIDENCE="
        + str(
            len(all_rows)
            - len(anchored_rows)
        )
    )

    print(
        "AXIS_ATTRIBUTION="
        "STANDARD_V2_ANCHORED_ONLY"
    )

    print(
        "NEW_BACKTESTS_EXECUTED=0"
    )

    print(
        "NEW_EXPERIMENT_DISPATCH=LOCKED_SAFE"
    )

    print(
        "R0_RESULT="
        + str(R0_LATEST)
    )

    print(
        "LIVE_CHANGE=NO"
    )

    print(
        "R0_ANALYSIS=PASS"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=[
            "r0",
        ],
        default="r0",
    )

    args = parser.parse_args()

    if args.mode == "r0":
        run_r0()


if __name__ == "__main__":
    main()