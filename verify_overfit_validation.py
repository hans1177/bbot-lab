#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: verify_overfit_validation.py
"""B-Bot V3 과적합/미래데이터 누수 방지 검증. 거래소 write 없음."""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 경로 / 환경
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", Path(__file__).resolve().parent))
os.environ["BBOT_LOCAL_ROOT"] = str(ROOT)
os.environ["BOT_BASE_DIR"] = str(ROOT)
os.environ["RUNNER_DRY_RUN"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategy_lab"))

import v3_challenger_research as challenger
import v3_governance as governance
import v3_hypothesis_factory as hypothesis_factory
import v3_strategy_research as strategy_research
import v3_task_queue as task_queue

# ============================================================
# 검증 수집
# ============================================================

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)})


# ============================================================
# Constitution / bounded search 계약
# ============================================================

constitution = governance.constitution()
release_gate = ((constitution.get("release_validation_gates") or {}).get("OVERFIT") or {})
check("CONSTITUTION_OVERFIT_GATE_REQUIRED", release_gate.get("required") is True, release_gate)
check("OVERFIT_BLOCKS_PACKAGE", release_gate.get("must_pass_before_package") is True, release_gate)
check("OVERFIT_BLOCKS_SERVER_RELEASE", release_gate.get("must_pass_before_server_release") is True, release_gate)

config = json.loads((ROOT / "strategy_config.json").read_text(encoding="utf-8-sig"))
research = config.get("research") or {}
check("SEARCH_UNTIL_WINNER_FORBIDDEN", research.get("search_until_winner") is False)
check("SINGLE_BEFORE_COMBINATION", research.get("single_before_combination") is True)
check("TRIAL_BUDGET_REQUIRED", research.get("trial_budget_required") is True)
check("SEALED_BLIND_ONE_SHOT_POLICY", research.get("sealed_blind_policy") == "ONE_SHOT_WINNER_ONLY_PER_RESEARCH_WAVE")
check("SAFE_GRAMMAR_BOUNDED_48", int(research.get("safe_grammar_bounded_wave_size") or 0) == 48)
check("RELEASE_GATE_CONFIG_OVERFIT", ((research.get("release_validation_gates") or {}).get("overfit") == "REQUIRED_PASS"))

queue = task_queue.status()
for track in governance.EXPECTED_TRACKS:
    contract = (((queue.get("tracks") or {}).get(track) or {}).get("task_contract") or {})
    search = contract.get("allowed_search_space") or {}
    budget = contract.get("trial_budget") or {}
    check(
        f"BOUNDED_TASK_{track}",
        search.get("bounded") is True
        and search.get("search_until_winner") is False
        and search.get("core_mutation") is False
        and int(budget.get("current_task") or 0) == 1
        and budget.get("rerun_same_input") is False,
        contract,
    )

# ============================================================
# Blind holdout가 broad rank에 누수되지 않는지 검증
# ============================================================


def synthetic_report(blind_avg: float, blind_stress: float) -> dict:
    return {
        "datasets": {
            "CURRENT_35_FROM_2020": {
                "timeframes": {
                    "6H": {
                        "summary": {"trades": 40, "avg_hold_hours": 12.0},
                        "v3_robustness": {
                            "development": {"avg_net_pct": 0.8},
                            "walk_forward": {"avg_net_pct": 0.5, "cost_stress_2x_avg_net_pct": 0.2},
                            "sealed_blind_single": {
                                "trades": 20,
                                "avg_net_pct": blind_avg,
                                "cost_stress_2x_avg_net_pct": blind_stress,
                            },
                        },
                    }
                }
            },
            "LONG_4_5Y": {"timeframes": {"6H": {"summary": {"trades": 30, "avg_net_pct": 0.1}}}},
        }
    }


blind_good = strategy_research.evaluate(synthetic_report(100.0, 100.0), "6H")
blind_bad = strategy_research.evaluate(synthetic_report(-100.0, -100.0), "6H")
check(
    "SEALED_BLIND_NOT_USED_FOR_BROAD_RANK",
    blind_good.get("pass") == blind_bad.get("pass")
    and blind_good.get("rank_tuple") == blind_bad.get("rank_tuple")
    and blind_good.get("reasons") == blind_bad.get("reasons"),
    {"good": blind_good, "bad": blind_bad},
)
check(
    "SEALED_BLIND_STATUS_EXPLICIT",
    blind_good.get("sealed_blind_status")
    == blind_bad.get("sealed_blind_status")
    == "LEGACY_INTERNAL_HOLDOUT_NOT_FINAL_GATE",
    {
        "good": blind_good.get("sealed_blind_status"),
        "bad": blind_bad.get("sealed_blind_status"),
    },
)

ok, reasons = challenger._dominance(
    {"wf_stress": 1.0, "blind_stress": -0.01, "long4_avg": 0.2, "capital_time_efficiency": 0.1},
    {"wf_stress": 0.5, "blind_stress": 0.1, "long4_avg": 0.1, "capital_time_efficiency": 0.05},
)
check("CHALLENGER_REJECTS_NEGATIVE_BLIND", not ok and "CHALLENGER_BLIND_STRESS_NONPOSITIVE" in reasons, reasons)

strategy_source = (ROOT / "strategy_lab/v3_strategy_research.py").read_text(encoding="utf-8-sig", errors="ignore")
combo_source = (ROOT / "strategy_lab/v3_combination_research.py").read_text(encoding="utf-8-sig", errors="ignore")
deploy_source = (ROOT / "strategy_lab/v3_deployment.py").read_text(encoding="utf-8-sig", errors="ignore")
check("SINGLE_BLIND_BOUNDARY_ONE_SHOT", "SEALED_OPENED_ONCE_AFTER_TF_BROAD_SELECTION" in strategy_source and "SEEN_ONE_SHOT" in strategy_source)
check("COMBO_BLIND_BOUNDARY_ONE_SHOT", "COMBO_SEALED_OPENED_ONCE_AFTER_BOUNDED_COMBO_SELECTION" in combo_source and "SEEN_ONE_SHOT" in combo_source)
check("PROVISIONAL_REQUIRES_BLIND_PASS", "PROVISIONAL_REQUIRES_SEALED_BLIND_PASS" in deploy_source and "PROVISIONAL_SEALED_BLIND_EVIDENCE_MISSING" in deploy_source)

# ============================================================
# 미래데이터 누수: prefix invariance 18x5 + 48x5
# ============================================================

rng = np.random.default_rng(20260829)
n = 420
cut = 300
returns = rng.normal(0.0003, 0.010, n)
close = 100.0 * np.exp(np.cumsum(returns))
open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.003, 0.002, n)))
low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.003, 0.002, n)))
volume = rng.lognormal(8.0, 0.4, n)
full = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
# cutoff 이후만 큰 충격을 주어 앞부분 신호가 미래값에 영향받는 구현을 잡아낸다.
full.loc[cut:, "close"] = full.loc[cut:, "close"].to_numpy() * np.linspace(1.0, 2.5, n - cut)
full.loc[cut:, "high"] = np.maximum(full.loc[cut:, "high"], full.loc[cut:, "close"] * 1.01)
full.loc[cut:, "low"] = np.minimum(full.loc[cut:, "low"], full.loc[cut:, "close"] * 0.99)
prefix = full.iloc[:cut].copy()
check("TAIL_SHOCK_MATERIAL", float(full.iloc[-1]["close"]) > float(prefix.iloc[-1]["close"]) * 1.5)

timeframes = ["1H", "6H", "12H", "1D", "1W"]
family_files = sorted((ROOT / "strategy_lab/v3_single_families").glob("*.py"))
check("FAMILY_COUNT_18", len(family_files) == 18, len(family_files))
for path in family_files:
    spec = importlib.util.spec_from_file_location("_overfit_" + path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for tf in timeframes:
        try:
            sig_full = np.asarray(module.generate_signals(full, tf))
            sig_prefix = np.asarray(module.generate_signals(prefix, tf))
            ok = len(sig_full) == n and len(sig_prefix) == cut and np.array_equal(sig_full[:cut], sig_prefix, equal_nan=True)
        except Exception as exc:
            ok = False
            sig_full = repr(exc)
        check(f"CAUSAL_FAMILY_{path.stem}_{tf}", ok, "prefix invariant" if ok else sig_full)

recipes = hypothesis_factory.recipes()
check("SAFE_GRAMMAR_COUNT_48", len(recipes) == 48, len(recipes))
for index, recipe in enumerate(recipes, 1):
    for tf in timeframes:
        try:
            sig_full = hypothesis_factory.generate_signals_recipe(full, recipe)
            sig_prefix = hypothesis_factory.generate_signals_recipe(prefix, recipe)
            ok = len(sig_full) == n and len(sig_prefix) == cut and np.array_equal(sig_full[:cut], sig_prefix)
        except Exception as exc:
            ok = False
            sig_full = repr(exc)
        check(f"CAUSAL_GRAMMAR_{index:02d}_{tf}", ok, recipe.get("name") if ok else sig_full)

# ============================================================
# 결과
# ============================================================

report = {
    "schema": "B_BOT_V3_OVERFIT_VALIDATION_V1",
    "ok": all(row["ok"] for row in CHECKS),
    "passed": sum(row["ok"] for row in CHECKS),
    "total": len(CHECKS),
    "family_prefix_cases": len(family_files) * len(timeframes),
    "safe_grammar_prefix_cases": len(recipes) * len(timeframes),
    "checks": CHECKS,
}
print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
raise SystemExit(0 if report["ok"] else 1)
