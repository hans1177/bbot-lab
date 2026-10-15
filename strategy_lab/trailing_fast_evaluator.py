from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None


ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
EXP = LAB / "experiments"
RESULTS = LAB / "results"

PIPELINE = EXP / "research_pipeline_v1.py"

PLAN_FILE = RESULTS / "trailing_broad_plan_latest.json"
STATE_FILE = RESULTS / "research_state.json"
REG_FILE = RESULTS / "candidate_registry.json"

CANARY_FILE = RESULTS / "trailing_fast_canary_latest.json"
LOCK_FILE = RESULTS / "research_controller.lock"

STAGE = "auto_trailing_broad_v1"


def now():
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def load_json(path, default=None):
    if not path.exists():
        return default

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def atomic_json(path, obj):
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


def sha(obj):
    return hashlib.sha256(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class GlobalLock:

    def __enter__(self):
        LOCK_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.f = LOCK_FILE.open("a+b")

        if msvcrt is None:
            raise RuntimeError(
                "WINDOWS_LOCK_UNAVAILABLE"
            )

        self.f.seek(0)
        self.f.write(b"0")
        self.f.flush()
        self.f.seek(0)

        try:
            msvcrt.locking(
                self.f.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        except OSError:
            raise RuntimeError(
                "RESEARCH_ALREADY_RUNNING"
            )

        print("GLOBAL_RESEARCH_LOCK=PASS")
        return self

    def __exit__(self, *args):
        try:
            self.f.seek(0)
            msvcrt.locking(
                self.f.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        except Exception:
            pass

        self.f.close()


# ============================================================
# PLAN
# ============================================================

def choose_canary(plan, reg):
    if plan.get("changed_axis") != "TRAILING":
        raise RuntimeError(
            "PLAN_AXIS_NOT_TRAILING"
        )

    if plan.get("stage") != "BROAD":
        raise RuntimeError(
            "PLAN_STAGE_NOT_BROAD"
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

    if int(
        baseline.get(
            "universe_top_n",
            -1,
        )
    ) != 30:
        raise RuntimeError(
            "BASELINE_NOT_U30"
        )

    registry_rows = {
        x.get("candidate_signature"): x
        for x in reg.get(
            "planned_experiments",
            []
        )
        if isinstance(x, dict)
    }

    for row in plan.get(
        "candidates",
        []
    ):
        sig = row.get(
            "candidate_signature"
        )

        rr = registry_rows.get(sig)

        if rr is None:
            raise RuntimeError(
                "PLAN_REGISTRY_MISMATCH="
                + str(sig)
            )

        if rr.get("status") in (
            "DONE",
            "FAST_PASS",
            "FAST_FAIL",
            "HARD_FAIL",
        ):
            continue

        return row

    raise RuntimeError(
        "NO_UNEXECUTED_BROAD_CANDIDATE"
    )


# ============================================================
# IMPORT PIPELINE
# ============================================================

def import_pipeline():
    for p in (
        str(EXP),
        str(LAB),
        str(ROOT),
    ):
        if p not in sys.path:
            sys.path.insert(0, p)

    spec = importlib.util.spec_from_file_location(
        "bbot_research_pipeline_canary",
        PIPELINE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "PIPELINE_IMPORT_SPEC_FAIL"
        )

    mod = importlib.util.module_from_spec(
        spec
    )

    sys.modules[
        spec.name
    ] = mod

    spec.loader.exec_module(mod)

    return mod


def find_original_runner(mod):
    # 가장 먼저 예상 이름.
    if callable(
        getattr(
            mod,
            "run_original_exact_u30",
            None,
        )
    ):
        return "run_original_exact_u30"

    # main()이 호출하는 ORIGINAL EXACT runner 자동 탐색.
    src = inspect.getsource(
        mod.main
    )

    names = re.findall(
        r"\b(run_[A-Za-z0-9_]*original_exact[A-Za-z0-9_]*)\s*\(",
        src,
        re.I,
    )

    for name in names:
        if callable(
            getattr(
                mod,
                name,
                None,
            )
        ):
            return name

    candidates = [
        name
        for name, value
        in vars(mod).items()
        if callable(value)
        and "original_exact" in name.lower()
        and name.lower().startswith("run")
    ]

    raise RuntimeError(
        "ORIGINAL_RUNNER_NOT_FOUND "
        + repr(candidates)
    )


# ============================================================
# STRICT PARENT VALIDATION
# ============================================================

def validate_parent(
    parent,
    plan,
):
    baseline = plan["baseline"]

    expected_candidate = baseline[
        "candidate_id"
    ]

    if parent.get(
        "baseline_candidate"
    ) != expected_candidate:
        raise RuntimeError(
            "PARENT_CANDIDATE_MISMATCH "
            + repr(
                parent.get(
                    "baseline_candidate"
                )
            )
        )

    if int(
        parent.get(
            "universe_size",
            -1,
        )
    ) != 30:
        raise RuntimeError(
            "PARENT_U30_FAIL"
        )

    symbols = parent.get(
        "universe_symbols",
        []
    )

    if len(symbols) != 30:
        raise RuntimeError(
            "PARENT_SYMBOL_COUNT_FAIL="
            + str(len(symbols))
        )

    if parent.get(
        "exit_source"
    ) != "STANDARD_COMBINED":
        raise RuntimeError(
            "EXIT_SOURCE_NOT_STANDARD_COMBINED"
        )

    if parent.get(
        "timing_mode"
    ) != "NEXT_1H_STATE":
        raise RuntimeError(
            "TIMING_NOT_NEXT_1H_STATE"
        )

    trailing = parent.get(
        "trailing_config",
        {}
    )

    if float(
        trailing.get(
            "trailing_start_pct",
            -1,
        )
    ) != 3.0:
        raise RuntimeError(
            "PARENT_TRAILING_START_NOT_3"
        )

    if float(
        trailing.get(
            "trailing_callback_pct",
            -1,
        )
    ) != 1.5:
        raise RuntimeError(
            "PARENT_TRAILING_CALLBACK_NOT_1P5"
        )

    print("PARENT_STANDARD_V2=PASS")
    print("PARENT_U30=PASS")
    print("PARENT_EXIT_STANDARD_COMBINED=PASS")
    print("PARENT_NEXT_1H_STATE=PASS")
    print("PARENT_TRAILING_3_1P5=PASS")


# ============================================================
# RESULT VALIDATION
# ============================================================

def validate_result(
    candidate,
    result,
):
    metrics = result.get(
        "metrics",
        {}
    )

    extra = result.get(
        "extra",
        {}
    )

    params = candidate[
        "parameters"
    ]

    if int(
        metrics.get(
            "bankrupt",
            0,
        ) or 0
    ) != 0:
        raise RuntimeError(
            "CANARY_BANKRUPT_HARD_FAIL"
        )

    if int(
        metrics.get(
            "liquidations",
            0,
        ) or 0
    ) != 0:
        raise RuntimeError(
            "CANARY_EXCHANGE_LIQUIDATION_HARD_FAIL"
        )

    actual_start = float(
        extra.get(
            "trailing_start_pct",
            -1,
        )
    )

    actual_cb = float(
        extra.get(
            "trailing_callback_pct",
            -1,
        )
    )

    if actual_start != float(
        params[
            "trailing_start_pct"
        ]
    ):
        raise RuntimeError(
            "EXECUTED_START_MISMATCH"
        )

    if actual_cb != float(
        params[
            "trailing_callback_pct"
        ]
    ):
        raise RuntimeError(
            "EXECUTED_CALLBACK_MISMATCH"
        )

    # robustness-confirm 경로의 정책통계까지 검증.
    exit_stats = extra.get(
        "exit_source_policy_stats",
        {}
    )

    timing_stats = extra.get(
        "timing_policy_stats",
        {}
    )

    if exit_stats:
        if int(
            exit_stats.get(
                "standard_combined_calls",
                0,
            ) or 0
        ) <= 0:
            raise RuntimeError(
                "STANDARD_COMBINED_NOT_USED"
            )

        for key in (
            "ema_only_calls",
            "sar_only_calls",
            "either_calls",
        ):
            if int(
                exit_stats.get(
                    key,
                    0,
                ) or 0
            ) != 0:
                raise RuntimeError(
                    "EXIT_POLICY_CONTAMINATION="
                    + key
                )

    if timing_stats:
        if int(
            timing_stats.get(
                "deferred_next_1h_state",
                0,
            ) or 0
        ) <= 0:
            raise RuntimeError(
                "NEXT_1H_STATE_NOT_USED"
            )

        if int(
            timing_stats.get(
                "immediate_calls",
                0,
            ) or 0
        ) != 0:
            raise RuntimeError(
                "IMMEDIATE_TIMING_CONTAMINATION"
            )

        if int(
            timing_stats.get(
                "deferred_new_event_only",
                0,
            ) or 0
        ) != 0:
            raise RuntimeError(
                "NEW_EVENT_TIMING_CONTAMINATION"
            )

    print("BANKRUPT=0")
    print("EXCHANGE_LIQUIDATIONS=0")
    print("TRAILING_PARAMETER_PARITY=PASS")

    if exit_stats:
        print(
            "STANDARD_COMBINED_RUNTIME=PASS"
        )

    if timing_stats:
        print(
            "NEXT_1H_STATE_RUNTIME=PASS"
        )


# ============================================================
# REGISTRY UPDATE
# ============================================================

def update_registry(
    reg,
    candidate,
    result,
    fresh_trial,
    hard_fail=None,
):
    sig = candidate[
        "candidate_signature"
    ]

    rows = reg.get(
        "planned_experiments",
        []
    )

    target = None

    for row in rows:
        if (
            isinstance(row, dict)
            and row.get(
                "candidate_signature"
            ) == sig
        ):
            target = row
            break

    if target is None:
        raise RuntimeError(
            "REGISTRY_TARGET_NOT_FOUND"
        )

    metrics = result.get(
        "metrics",
        {}
    )

    target[
        "evaluated_at"
    ] = now()

    target[
        "result_metrics"
    ] = dict(metrics)

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

    overfit = target.setdefault(
        "overfit",
        {}
    )

    if fresh_trial:
        overfit["search_trials"] = int(
            overfit.get(
                "search_trials",
                0,
            ) or 0
        ) + 1

        overfit["parameter_trials"] = int(
            overfit.get(
                "parameter_trials",
                0,
            ) or 0
        ) + 1

        overfit["family_trials"] = int(
            overfit.get(
                "family_trials",
                0,
            ) or 0
        ) + 1

    overfit.setdefault(
        "blind_uses",
        0,
    )

    overfit[
        "forward_status"
    ] = "NOT_STARTED"

    overfit[
        "overfit_risk"
    ] = (
        "HIGH"
        if hard_fail
        else "NOT_EVALUATED"
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
        "planned_not_executed"
    ] = sum(
        1
        for row in rows
        if isinstance(row, dict)
        and row.get("status")
        == "PLANNED"
    )

    reg["updated_at"] = now()

    atomic_json(
        REG_FILE,
        reg,
    )


# ============================================================
# STATE UPDATE
# ============================================================

def update_state(
    state,
    candidate,
    canary_payload,
    passed,
):
    ctrl = state.setdefault(
        "controller",
        {}
    )

    ctrl[
        "trailing_evaluator_status"
    ] = (
        "CANARY_PASS"
        if passed
        else "CANARY_FAIL"
    )

    ctrl[
        "research_execution"
    ] = (
        "LOCKED_UNTIL_BROAD_BATCH_ENABLE"
        if passed
        else "LOCKED_CANARY_FAILURE"
    )

    ctrl[
        "last_fast_canary"
    ] = str(
        CANARY_FILE
    )

    current = state.setdefault(
        "current_research",
        {}
    )

    current["stage"] = "BROAD"
    current[
        "status"
    ] = (
        "CANARY_PASS"
        if passed
        else "CANARY_FAIL"
    )

    current[
        "last_candidate"
    ] = candidate[
        "candidate_id"
    ]

    current[
        "next_action"
    ] = (
        "RUN_REMAINING_BROAD"
        if passed
        else "DIAGNOSE_FAST_CANARY"
    )

    state["updated_at"] = now()

    atomic_json(
        STATE_FILE,
        state,
    )


# ============================================================
# CANARY
# ============================================================

def run_canary():
    plan = load_json(
        PLAN_FILE,
        {},
    )

    state = load_json(
        STATE_FILE,
        {},
    )

    reg = load_json(
        REG_FILE,
        {},
    )

    candidate = choose_canary(
        plan,
        reg,
    )

    print("=" * 78)
    print(
        "STANDARD_V2 TRAILING FAST CANARY"
    )
    print("=" * 78)

    print(
        "CANDIDATE="
        + candidate[
            "candidate_id"
        ]
    )

    print(
        "SIGNATURE="
        + candidate[
            "candidate_signature"
        ]
    )

    print(
        "START="
        + str(
            candidate[
                "parameters"
            ][
                "trailing_start_pct"
            ]
        )
    )

    print(
        "CALLBACK="
        + str(
            candidate[
                "parameters"
            ][
                "trailing_callback_pct"
            ]
        )
    )

    mod = import_pipeline()

    required = [
        "_load_recheck_top_pass",
        "_load_universe_row_for_candidate",
        "_wf_parent_from_universe_row",
        "build_exit_source_books",
        "_run_or_resume_robust_scenario",
    ]

    for name in required:
        if not callable(
            getattr(
                mod,
                name,
                None,
            )
        ):
            raise RuntimeError(
                "PIPELINE_BRIDGE_MISSING="
                + name
            )

    runner_name = (
        find_original_runner(
            mod
        )
    )

    original_runner = getattr(
        mod,
        runner_name,
    )

    holder = {}

    def canary_runner(
        *args,
        **kwargs,
    ):
        if args:
            data = args[0]
        else:
            data = kwargs["data"]

        cli_args = (
            args[1]
            if len(args) > 1
            else kwargs.get("args")
        )

        recheck_payload, top = (
            mod._load_recheck_top_pass(
                data
            )
        )

        universe_payload, universe_row = (
            mod._load_universe_row_for_candidate(
                data,
                mod.ROBUSTNESS_CONFIRM_TARGET,
            )
        )

        parent = (
            mod._wf_parent_from_universe_row(
                universe_row
            )
        )

        validate_parent(
            parent,
            plan,
        )

        base_books = (
            mod.build_exit_source_books(
                data
            )
        )

        cfg = {
            "family":
                "TRAILING_NEIGHBORHOOD",

            "baseline_standard_id":
                "STANDARD_V2",

            "changed_axis":
                "TRAILING",

            "trailing_start_pct":
                float(
                    candidate[
                        "parameters"
                    ][
                        "trailing_start_pct"
                    ]
                ),

            "trailing_callback_pct":
                float(
                    candidate[
                        "parameters"
                    ][
                        "trailing_callback_pct"
                    ]
                ),
        }

        row = (
            mod._run_or_resume_robust_scenario(
                data=data,
                base_books=base_books,
                stage=STAGE,
                parent=parent,
                scenario_name=
                    candidate[
                        "candidate_id"
                    ],
                scenario_config=cfg,
                resume=True,
            )
        )

        holder["row"] = row
        holder["parent"] = parent

        return row

    setattr(
        mod,
        runner_name,
        canary_runner,
    )

    old_argv = list(
        sys.argv
    )

    try:
        sys.argv = [
            str(PIPELINE),
            "--stage",
            "original_exact_u30",
        ]

        mod.main()

    finally:
        sys.argv = old_argv

        setattr(
            mod,
            runner_name,
            original_runner,
        )

    if "row" not in holder:
        raise RuntimeError(
            "CANARY_RUNNER_NOT_INVOKED"
        )

    row = holder["row"]

    hard_fail = None

    try:
        validate_result(
            candidate,
            row,
        )

    except Exception as exc:
        hard_fail = (
            type(exc).__name__
            + ": "
            + str(exc)
        )

    fresh_trial = not bool(
        row.get(
            "resumed",
            False,
        )
    )

    baseline_metrics = (
        plan[
            "baseline"
        ][
            "historical_metrics"
        ]
    )

    metrics = row.get(
        "metrics",
        {}
    )

    eq = float(
        metrics.get(
            "final_equity",
            0.0,
        )
    )

    base_eq = float(
        baseline_metrics[
            "final_equity"
        ]
    )

    mdd = float(
        metrics.get(
            "realized_mdd_pct",
            -100.0,
        )
    )

    base_mdd = float(
        baseline_metrics[
            "mdd_pct"
        ]
    )

    payload = {
        "schema":
            "B_BOT_TRAILING_FAST_CANARY_V1",

        "created_at":
            now(),

        "candidate":
            candidate,

        "baseline_standard_id":
            "STANDARD_V2",

        "changed_axis":
            "TRAILING",

        "stage":
            "BROAD_CANARY",

        "result":
            row,

        "comparison": {
            "equity_ratio_vs_v2":
                (
                    eq / base_eq
                    if base_eq > 0
                    else None
                ),

            "mdd_delta_pp_vs_v2":
                mdd - base_mdd,

            "return_better":
                eq > base_eq,

            "mdd_better":
                mdd > base_mdd,

            "pareto_improvement":
                (
                    eq > base_eq
                    and mdd > base_mdd
                    and int(
                        metrics.get(
                            "liquidations",
                            0,
                        ) or 0
                    ) == 0
                ),
        },

        "fresh_trial":
            fresh_trial,

        "hard_fail":
            hard_fail,

        "verdict":
            (
                "PASS"
                if hard_fail is None
                else "HARD_FAIL"
            ),

        "live_change":
            False,

        "auto_deploy":
            False,
    }

    atomic_json(
        CANARY_FILE,
        payload,
    )

    reg = load_json(
        REG_FILE,
        {},
    )

    update_registry(
        reg,
        candidate,
        row,
        fresh_trial,
        hard_fail,
    )

    state = load_json(
        STATE_FILE,
        {},
    )

    update_state(
        state,
        candidate,
        payload,
        hard_fail is None,
    )

    print()
    print(
        "FRESH_TRIAL="
        + str(fresh_trial)
    )

    print(
        "FINAL_EQUITY="
        + str(eq)
    )

    print(
        "EQUITY_RATIO_VS_V2="
        + f"{eq/base_eq:.6f}"
    )

    print(
        "MDD="
        + f"{mdd:.6f}%"
    )

    print(
        "MDD_DELTA_VS_V2="
        + f"{mdd-base_mdd:+.6f}pp"
    )

    print(
        "PARETO_IMPROVEMENT="
        + str(
            payload[
                "comparison"
            ][
                "pareto_improvement"
            ]
        )
    )

    print(
        "CHECKPOINT="
        + str(
            row.get(
                "checkpoint"
            )
        )
    )

    if hard_fail:
        print(
            "HARD_FAIL="
            + hard_fail
        )

        print(
            "FAST_CANARY=FAIL"
        )

        raise SystemExit(2)

    print("FAST_CANARY=PASS")
    print(
        "REMAINING_BROAD=LOCKED"
    )
    print(
        "AUTO_DEPLOY=OFF"
    )
    print(
        "LIVE_CHANGE=NO"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=[
            "canary",
        ],
        default="canary",
    )

    args = parser.parse_args()

    if args.mode == "canary":
        with GlobalLock():
            run_canary()


if __name__ == "__main__":
    main()