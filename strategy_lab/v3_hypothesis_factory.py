# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_hypothesis_factory.py
"""V3 bounded safe-grammar 독립 ENTRY SIGNAL 가설 생성기.

48개 고정 문법 후보만 허용하며 search-until-winner를 하지 않는다.
생성물은 strategy_lab/results/v3 아래 연구용 파일이고 Core/LIVE 파일을 수정하지 않는다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import hashlib
import importlib.util
import inspect
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import v3_governance as governance
import v3_research_knowledge as knowledge

# ============================================================
# 경로 / 상수
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
V3 = LAB / "results" / "v3"
GENERATED = V3 / "generated_safe_grammar"
RUNNER = LAB / "run_strategy.py"

TF_ORDER = ["30M", "1H", "6H", "12H", "1D", "1W"]
_scope_path = LAB / "lab_config.json"
if _scope_path.exists():
    _scope = json.loads(_scope_path.read_text(encoding="utf-8-sig")).get("research_candidate_fill_timeframes")
    if _scope is not None:
        if not isinstance(_scope, list) or not _scope or any(str(tf).upper() not in TF_ORDER for tf in _scope):
            raise RuntimeError("INVALID_RESEARCH_TIMEFRAME_SCOPE")
        TF_ORDER = list(dict.fromkeys(str(tf).upper() for tf in _scope))
WINDOWS = [12, 18, 24, 32, 48, 64]
THRESHOLDS = [0.4, 0.7, 1.0, 1.3]
MODES = ["TREND_Z", "CHANNEL_Z"]
MAX_RECIPES = 48

MUTATION_CONFIRM_MODES = [
    "PRICE_DIRECTION",
    "PERSIST_2",
]

MAX_MUTATION_RECIPES = 24

MUTATION_TYPE = (
    "STRUCTURAL_CONFIRMATION_FILTER"
)

# ============================================================
# 유틸
# ============================================================


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def recipe_name(recipe: dict[str, Any]) -> str:
    base = (
        f"V3_SAFE_{recipe['mode']}"
        f"_W{int(recipe['window'])}"
        f"_T{str(recipe['threshold']).replace('.', 'P')}"
    )

    confirm_mode = str(
        recipe.get(
            "confirm_mode"
        )
        or "BASE"
    ).upper()

    if confirm_mode != "BASE":
        return (
            base
            + "_M_"
            + confirm_mode
        )

    return base



# ============================================================
# 48개 Safe Grammar
# ============================================================


def recipes() -> list[dict[str, Any]]:
    out = []
    for mode in MODES:
        for window in WINDOWS:
            for threshold in THRESHOLDS:
                row = {
                    "mode": mode,
                    "window": window,
                    "threshold": threshold,
                    "bounded": True,
                    "uses_future_data": False,
                    "live_mutation": False,
                }
                row["name"] = recipe_name(row)
                out.append(row)
    if len(out) != MAX_RECIPES:
        raise RuntimeError("SAFE_GRAMMAR_RECIPE_COUNT_INVALID")
    return out

def semantic_recipe_fingerprint(
    recipe: dict[str, Any],
) -> str:
    """Canonical fingerprint of signal-affecting bounded grammar semantics."""

    semantic = {
        "mode":
            str(
                recipe.get("mode")
                or ""
            ).upper(),

        "window":
            int(
                recipe.get(
                    "window"
                )
            ),

        "threshold":
            round(
                float(
                    recipe.get(
                        "threshold"
                    )
                ),
                8,
            ),

        "confirm_mode":
            str(
                recipe.get(
                    "confirm_mode"
                )
                or "BASE"
            ).upper(),
    }

    raw = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        raw
    ).hexdigest()


def mutation_recipes() -> list[dict[str, Any]]:
    """Deterministic bounded structural variations of the frozen 48 BASE recipes.

    This is not arbitrary Python generation.  Only two explicit confirmation
    structures are permitted and the wave is hard-capped at 24 candidates.
    """

    base = recipes()

    if (
        MAX_MUTATION_RECIPES
        % len(
            MUTATION_CONFIRM_MODES
        )
        != 0
    ):
        raise RuntimeError(
            "MUTATION_WAVE_BALANCE_INVALID"
        )

    per_mode = (
        MAX_MUTATION_RECIPES
        // len(
            MUTATION_CONFIRM_MODES
        )
    )

    out = []

    base_fingerprints = {
        semantic_recipe_fingerprint(
            row
        )
        for row in base
    }

    seen = set(
        base_fingerprints
    )

    for confirm_mode in (
        MUTATION_CONFIRM_MODES
    ):

        candidates = []

        for parent in base:

            row = dict(
                parent
            )

            row[
                "confirm_mode"
            ] = confirm_mode

            row[
                "mutation_type"
            ] = MUTATION_TYPE

            row[
                "parent_name"
            ] = recipe_name(
                parent
            )

            row[
                "mutation_id"
            ] = (
                "MUT_"
                + confirm_mode
            )

            row[
                "bounded"
            ] = True

            row[
                "uses_future_data"
            ] = False

            row[
                "live_mutation"
            ] = False

            row[
                "promotion_eligible"
            ] = False

            row[
                "promotion_status"
            ] = (
                "RESEARCH_ONLY_UNTIL_PORTABLE_VALIDATION"
            )

            fingerprint = (
                semantic_recipe_fingerprint(
                    row
                )
            )

            row[
                "semantic_fingerprint"
            ] = fingerprint

            row[
                "name"
            ] = recipe_name(
                row
            )

            if fingerprint in seen:
                continue

            candidates.append(
                row
            )

        candidates.sort(
            key=lambda row:
                row[
                    "semantic_fingerprint"
                ]
        )

        selected = (
            candidates[
                :per_mode
            ]
        )

        if (
            len(selected)
            != per_mode
        ):
            raise RuntimeError(
                "MUTATION_MODE_BUDGET_UNFILLED="
                + confirm_mode
            )

        for row in selected:

            fingerprint = (
                row[
                    "semantic_fingerprint"
                ]
            )

            if fingerprint in seen:
                raise RuntimeError(
                    "MUTATION_SEMANTIC_DUPLICATE"
                )

            seen.add(
                fingerprint
            )

            out.append(
                row
            )

    if (
        len(out)
        != MAX_MUTATION_RECIPES
    ):
        raise RuntimeError(
            "MUTATION_RECIPE_COUNT_INVALID"
        )

    names = [
        row["name"]
        for row in out
    ]

    fingerprints = [
        row[
            "semantic_fingerprint"
        ]
        for row in out
    ]

    if (
        len(names)
        != len(set(names))
    ):
        raise RuntimeError(
            "MUTATION_NAME_DUPLICATE"
        )

    if (
        len(fingerprints)
        != len(
            set(
                fingerprints
            )
        )
    ):
        raise RuntimeError(
            "MUTATION_FINGERPRINT_DUPLICATE"
        )

    return out


def research_recipes() -> list[dict[str, Any]]:
    """BASE 48 + bounded structural mutation wave 24."""

    base = recipes()
    mutation = mutation_recipes()

    out = (
        base
        + mutation
    )

    fingerprints = [
        semantic_recipe_fingerprint(
            row
        )
        for row in out
    ]

    if (
        len(out)
        != (
            MAX_RECIPES
            + MAX_MUTATION_RECIPES
        )
    ):
        raise RuntimeError(
            "RESEARCH_RECIPE_COUNT_INVALID"
        )

    if (
        len(fingerprints)
        != len(
            set(
                fingerprints
            )
        )
    ):
        raise RuntimeError(
            "RESEARCH_RECIPE_SEMANTIC_DUPLICATE"
        )

    return out



def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def generate_signals_recipe(df: pd.DataFrame, recipe: dict[str, Any]) -> np.ndarray:
    d = df.copy()
    c = _num(d["close"])
    window = int(recipe["window"])
    threshold = float(recipe["threshold"])
    mode = str(recipe["mode"]).upper()

    out = np.zeros(len(d), dtype=np.int8)

    if mode == "TREND_Z":
        ema = c.ewm(
            span=window,
            adjust=False,
        ).mean()

        vol = (
            c.diff()
            .rolling(
                window,
                min_periods=window,
            )
            .std()
            .replace(
                0,
                np.nan,
            )
        )

        z = (
            c
            - ema
        ) / vol

        slope = (
            ema
            - ema.shift(
                max(
                    2,
                    window // 6,
                )
            )
        )

        long_mask = (
            (z > threshold)
            & (slope > 0)
        )

        short_mask = (
            (z < -threshold)
            & (slope < 0)
        )

    elif mode == "CHANNEL_Z":

        mean = (
            c.rolling(
                window,
                min_periods=window,
            ).mean()
        )

        std = (
            c.rolling(
                window,
                min_periods=window,
            )
            .std()
            .replace(
                0,
                np.nan,
            )
        )

        upper = (
            mean
            + threshold
            * std
        )

        lower = (
            mean
            - threshold
            * std
        )

        momentum = (
            c
            - c.shift(
                max(
                    2,
                    window // 8,
                )
            )
        )

        long_mask = (
            (c > upper)
            & (momentum > 0)
        )

        short_mask = (
            (c < lower)
            & (momentum < 0)
        )

    else:
        raise RuntimeError(
            "SAFE_GRAMMAR_MODE_INVALID"
        )

    confirm_mode = str(
        recipe.get(
            "confirm_mode"
        )
        or "BASE"
    ).upper()

    if confirm_mode == "BASE":
        pass

    elif (
        confirm_mode
        == "PRICE_DIRECTION"
    ):

        # Structural mutation:
        # signal condition + current closed-bar price direction confirmation.
        long_mask = (
            long_mask
            & (
                c
                > c.shift(1)
            )
        )

        short_mask = (
            short_mask
            & (
                c
                < c.shift(1)
            )
        )

    elif (
        confirm_mode
        == "PERSIST_2"
    ):

        # Structural mutation:
        # same bounded entry condition must persist for two closed bars.
        prior_long = (
            long_mask
            .shift(1)
            .fillna(False)
        )

        prior_short = (
            short_mask
            .shift(1)
            .fillna(False)
        )

        long_mask = (
            long_mask
            & prior_long
        )

        short_mask = (
            short_mask
            & prior_short
        )

    else:
        raise RuntimeError(
            "SAFE_GRAMMAR_CONFIRM_MODE_INVALID"
        )

    out[
        long_mask
        .fillna(False)
        .to_numpy()
    ] = 1

    out[
        short_mask
        .fillna(False)
        .to_numpy()
    ] = -1

    return out



def signal_recipe(df: pd.DataFrame, recipe: dict[str, Any]) -> dict[str, Any]:
    arr = generate_signals_recipe(df, recipe)
    value = int(arr[-1]) if len(arr) else 0
    name = recipe_name(recipe)
    if value > 0:
        return {"signal": "long", "state": "long", "strength_long": 1.0, "strength_short": 0.0, "reason": name}
    if value < 0:
        return {"signal": "short", "state": "short", "strength_long": 0.0, "strength_short": 1.0, "reason": name}
    return {"signal": "wait", "state": "wait", "strength_long": 0.0, "strength_short": 0.0, "reason": name}


# ============================================================
# 연구용 모듈 생성
# ============================================================


def module_for(recipe: dict[str, Any], tf: str) -> tuple[Path, str]:
    tf = str(tf).upper()
    name = recipe_name(recipe)
    strategy_name = f"{name}_{tf}_BROAD_V1"
    GENERATED.mkdir(parents=True, exist_ok=True)
    out = GENERATED / f"{name.lower()}_{tf.lower()}_broad_v1.py"
    recipe_json = json.dumps({key: recipe.get(key, "BASE" if key == "confirm_mode" else None) for key in ("mode", "window", "threshold", "confirm_mode")}, ensure_ascii=False, sort_keys=True)
    implementations = "\n\n".join(inspect.getsource(fn) for fn in (
        recipe_name, _num, generate_signals_recipe, signal_recipe))
    code = (
        "# -*- coding: utf-8 -*-\n# 파일명: " + out.name + "\n"
        "# PORTABLE_RUNTIME_ARTIFACT_V1\nfrom __future__ import annotations\n"
        "import json\nfrom typing import Any\nimport numpy as np\nimport pandas as pd\n"
        + implementations + "\n_RECIPE = json.loads(" + repr(recipe_json) + ")\n"
        + "STRATEGY_META = " + repr({"name": strategy_name, "development_mode": "AUTO_SAFE_GRAMMAR", "data_mode": "OHLCV", "allowed_timeframes": [tf]}) + "\n"
        + "def generate_signals(df, tf):\n    return generate_signals_recipe(df, _RECIPE)\n"
        + "def signal(df, params=None):\n    return signal_recipe(df, _RECIPE)\n"
    )
    compile(code, str(out), "exec")
    if out.exists():
        if out.read_text(encoding="utf-8-sig") != code:
            raise RuntimeError("FROZEN_STRATEGY_SOURCE_CONFLICT")
    else:
        with out.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(code)

    return out, strategy_name


# ============================================================
# Trial / Dedupe
# ============================================================


def completed_pairs() -> set[tuple[str, str]]:
    _, ledger = knowledge.ensure()
    out: set[tuple[str, str]] = set()
    for row in ledger.get("entries") or []:
        if row.get("event") != "COMPLETED" or str(row.get("kind") or "").upper() != "SAFE_GRAMMAR_HP2016_2024":
            continue
        out.add((str(row.get("candidate") or "").upper(), str(row.get("timeframe") or "").upper()))
    return out


def all_complete(tf: str) -> bool:
    tf = str(tf).upper()
    done = completed_pairs()
    return all((r["name"].upper(), tf) in done for r in research_recipes())


def choose_next() -> tuple[str, dict[str, Any]] | None:
    done=completed_pairs(); rows=research_recipes(); pending={tf:[] for tf in TF_ORDER}; done_counts={tf:0 for tf in TF_ORDER}
    for tf in TF_ORDER:
        for recipe in rows:
            if (recipe["name"].upper(),tf) in done: done_counts[tf]+=1
            else: pending[tf].append(recipe)
    contract=(governance.constitution().get("individual_strategy_trailing_optimization") or {}).get("auto_research_allocation_contract") or {}
    dist=contract.get("new_strategy_timeframe_distribution_percent") or {}
    total=sum(done_counts.values())
    available=[tf for tf in TF_ORDER if pending[tf]]
    if not available:return None
    tf=max(available,key=lambda x:(float(dist.get(x) or 0.0)/100.0*(total+1)-done_counts[x],float(dist.get(x) or 0.0),-TF_ORDER.index(x)))
    return tf,pending[tf][0]

def _find_result(strategy_name: str, after_ts: float) -> Path | None:
    candidates = []
    for p in (LAB / "results").glob("*.json"):
        try:
            if p.stat().st_mtime + 1 < after_ts:
                continue
            obj = json.loads(p.read_text(encoding="utf-8-sig"))
            if str(obj.get("strategy") or "").upper() == strategy_name.upper():
                candidates.append(p)
        except Exception:
            pass
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


# ============================================================
# Bounded Research 실행
# ============================================================


def run_one(candidate=None) -> dict[str, Any]:
    if candidate is not None and not isinstance(candidate, dict):
        raise ValueError("CANDIDATE_MUST_BE_OBJECT")
    nxt = (str(candidate["timeframe"]).upper(), candidate) if candidate is not None else choose_next()
    if not nxt:
        return {"status": "SAFE_GRAMMAR_COMPLETE", "recipe_count": MAX_RECIPES, "timeframes": TF_ORDER}

    tf, recipe = nxt
    name = recipe["name"].upper()
    module, strategy_name = module_for(recipe, tf)
    trial = knowledge.reserve_trial(f"V3_SAFE_HP2016_2024_{name}_{tf}", name, tf, "SAFE_GRAMMAR_HP2016_2024")
    before = governance.capture_kernel_guard()
    import v3_strategy_research as sr
    env = os.environ.copy()
    research_start,research_end=sr.hard_pass_dates()
    env["BBOT_RESEARCH_DATASET_MODE"]="HARD_PASS_ONLY"
    env["BBOT_RESEARCH_START"]=research_start
    env["BBOT_RESEARCH_END"]=research_end
    env["BBOT_LOCAL_ROOT"] = str(ROOT)
    start = time.time()
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(module)],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    governance.assert_kernel_unchanged(before)

    if proc.returncode != 0:
        knowledge.complete_trial(trial, "SAFE_GRAMMAR_RUN_FAIL", "FAIL", "UNKNOWN")
        row = {"strategy": name, "timeframe": tf, "trial": trial, "status": "FAIL", "reason": "RUN_STRATEGY_FAIL", "output_tail": proc.stdout[-4000:]}
        knowledge.record_result(f"generated:{name}:{tf}", row, failed=True)
        return row

    result = _find_result(strategy_name, start)
    if result is None:
        knowledge.complete_trial(trial, "RESULT_NOT_FOUND", "FAIL", "UNKNOWN")
        # RESULT_NOT_FOUND_REJECT_MEMORY_V1
        _result_not_found_source = locals().get('h')
        if not isinstance(_result_not_found_source,dict):
            _result_not_found_source = {}
        _result_not_found_strategy = str(
            locals().get('strategy_name')
            or locals().get('name')
            or locals().get('strategy')
            or _result_not_found_source.get('strategy')
            or _result_not_found_source.get('name')
            or ''
        ).upper()
        _result_not_found_origin = str(
            locals().get('origin')
            or (
                'SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE'
                if bool(locals().get('recovery'))
                else 'RESULT_NOT_FOUND'
            )
        ).upper()
        _result_not_found_row = {
            'status': 'FAIL',
            'reason': 'RESULT_NOT_FOUND',
            'trial': trial,
            'strategy': _result_not_found_strategy,
            'timeframe': str(tf).upper(),
            'research_origin': _result_not_found_origin,
        }
        knowledge.record_result(
            f'result_missing:{tf}:{trial}',
            _result_not_found_row,
            failed=True,
        )
        return {"status": "FAIL", "reason": "RESULT_NOT_FOUND", "trial": trial, "strategy": name, "timeframe": tf}

    # v3_strategy_research와 동일 evaluator를 사용한다.
    report = json.loads(result.read_text(encoding="utf-8-sig"))
    ev = sr.evaluate(report, tf, require_fixed_window=True)
    entry_gate = sr.evaluate_entry_risk_gate(report, tf, ev) if bool(ev.get("hard_pass")) else {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}
    # SOFT_PASS_STATUS_GENERATED_V1
    status=("PASS" if bool(ev.get("hard_pass",ev.get("pass"))) else ("SOFT_PASS" if bool(ev.get("soft_pass")) else "FAIL"))
    edge_pct = finite(ev.get("walk_forward_avg_net_pct"))
    edge = edge_pct / 100.0 if edge_pct is not None else None
    hold = finite(ev.get("expected_holding_time_hours"))
    cte = edge / hold if edge is not None and hold not in (None, 0) and hold > 0 else None
    row = {
        "strategy": name,
        "family_file": str(module.relative_to(ROOT)),
        "recipe": recipe,
        "timeframe": tf,
        "trial": trial,
        "status": status,
        "evaluation": ev,
        "result_json": str(result.relative_to(ROOT)),
        "expected_return": edge,
        "expected_loss": None,
        "expected_holding_time_hours": hold,
        "capital_time_efficiency": cte,
        "expected_account_contribution": edge,
        "confidence": None,
        "confidence_level": "INITIAL_BACKTEST_ONLY",
        "strategy_version": "SAFE_GRAMMAR_BROAD_V1",
        "exact_strategy_version": "EV_" + hashlib.sha256(module.read_bytes()).hexdigest()[:16] + "_" + tf,
        "source_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "research_start": research_start,
        "research_end": research_end,
        "fixed_window_contract": True,
        "research_epoch": "HARD_PASS_FIXED_WINDOW_2016_2024_STAGE10",
        "entry_risk_gate": entry_gate,
        "pipeline_stage": "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" if status=="PASS" and bool(entry_gate.get("pass")) else ("ENTRY_RISK_REDEVELOPMENT_PENDING" if status=="PASS" else "HARD_PASS_FAIL"),
        "expectation_basis": "NET_EXPECTED_RETURN_PER_UNIT_CAPITAL_SAME_ENGINE",
        "evidence_summary": {"source": "SAFE_GRAMMAR_BOUNDED", "cost_stress": "2X", "sealed_blind": "NOT_USED_IN_BROAD"},
    }
    # SOFT_PASS_ROW_FLAGS_GENERATED_V1
    row["qualification"]=(
        "HARD_PASS"
        if status=="PASS"
        else status
    )
    row["research_shadow_eligible"]=(status=="SOFT_PASS")
    row["canary_eligible"]=False
    row["automatic_promotion"]=False
    knowledge.complete_trial(trial, str(result.relative_to(ROOT)), status, "LOW" if status in {"PASS","SOFT_PASS"} else "UNKNOWN")
    knowledge.record_result(f"generated:{name}:{tf}", row, failed=(status=="FAIL"))

    if status == "PASS":
        cur = (((report.get("datasets") or {}).get("CURRENT_35_FROM_2020") or {}).get("timeframes") or {}).get(tf) or {}
        for sym, se in (cur.get("symbol_evidence") or {}).items():
            if not isinstance(se, dict):
                continue
            ssm = se.get("summary") if isinstance(se.get("summary"), dict) else {}
            srb = se.get("v3_robustness") if isinstance(se.get("v3_robustness"), dict) else {}
            swf = srb.get("walk_forward") if isinstance(srb.get("walk_forward"), dict) else {}
            sedge_pct = finite(swf.get("avg_net_pct"))
            shold = finite(ssm.get("avg_hold_hours"))
            sloss_pct = finite(ssm.get("avg_loss_pct"))
            strades = int(ssm.get("trades") or 0)
            if sedge_pct is None or shold is None or shold <= 0 or strades <= 0:
                continue
            sedge = sedge_pct / 100.0
            rec = dict(row)
            rec.update({
                "symbol": str(sym).upper(),
                "expected_return": sedge,
                "expected_loss": abs(sloss_pct / 100.0) if sloss_pct is not None else None,
                "expected_holding_time_hours": shold,
                "capital_time_efficiency": sedge / shold,
                "expected_account_contribution": sedge,
                "sample_trades": strades,
            })
            knowledge.record_result(f"runtime:{name}:{tf}:{str(sym).upper()}", rec, failed=False)
    return row


def all_results_for_tf(tf: str) -> list[dict[str, Any]]:
    k, _ = knowledge.ensure()
    rows = []
    for key, row in (k.get("records") or {}).items():
        if key.startswith("generated:") and str(row.get("timeframe") or "").upper() == str(tf).upper() and str(row.get("status") or "").upper() == "PASS":
            rows.append(row)
    return rows


if __name__ == "__main__":
    print(json.dumps(run_one(), ensure_ascii=False, indent=2, default=str))
