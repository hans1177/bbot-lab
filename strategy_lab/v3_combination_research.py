# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_combination_research.py
"""V3 bounded combination research after standalone validation. No exhaustive combinations."""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(os.getenv("BBOT_LOCAL_ROOT",r"C:\my\bottest")); LAB=ROOT/"strategy_lab"; RES=LAB/"results"/"v3"; GENERATED=RES/"generated_combinations"; RUNNER=LAB/"run_strategy.py"
sys.path.insert(0,str(LAB))
import v3_research_knowledge as knowledge
import v3_strategy_research as single
COMPLEMENTARY=[("V3_DONCHIAN_BREAKOUT","V3_TREND_PULLBACK"),("V3_SQUEEZE_RELEASE","V3_VOLUME_BREAKOUT"),("V3_EFFICIENCY_TREND","V3_RANGE_EXPANSION"),("V3_MOMENTUM_ACCEL","V3_ADAPTIVE_CHANNEL"),("V3_FRACTAL_BREAKOUT","V3_MEAN_REVERSION_Z")]

# BOUNDED_RECOMPOSE_CROSS_TF_PARENT_EVIDENCE_V1
#
# This does not alter the original complementary wave.
# Only explicitly evidenced parent pairs may be added here.
# Each timeframe remains an independent exact research test.
BOUNDED_RECOMPOSE = {
    "30M": [
        (
            "V3_DIRECTIONAL_ENTROPY",
            "V3_EFFICIENCY_PULLBACK_RESUME",
        ),
    ],
}

# CROSS_TF_COMBO_PASS_RECOMPOSE_30M_V1
#
# These pairs are admitted only as fresh 30M BROAD tests because
# the exact pair has already produced PASS evidence on another TF.
# Parent standalone FAIL on 30M does not make the combo PASS; it only
# supplies completed local parent evidence. Evaluation remains unchanged.
CROSS_TF_RECOMPOSE_30M = [
    (
        "V3_ADAPTIVE_CHANNEL",
        "V3_MOMENTUM_ACCEL",
    ),
    (
        "V3_EFFICIENCY_TREND",
        "V3_RANGE_EXPANSION",
    ),
    (
        "V3_SQUEEZE_RELEASE",
        "V3_VOLUME_BREAKOUT",
    ),
]

# ANCHOR_RECOMPOSE_30M_V1
#
# Finite predeclared research budget:
# 2 independently PASS 30M anchors x 6 highest-ranked completed
# SINGLE_FAMILY OOS failures. No adaptive search-until-winner.
ANCHOR_RECOMPOSE_30M = [
    ("V3_DIRECTIONAL_ENTROPY","V3_MEAN_REVERSION_Z"),
    ("V3_DIRECTIONAL_ENTROPY","V3_TREND_PULLBACK"),
    ("V3_DIRECTIONAL_ENTROPY","V3_RANGE_EXPANSION"),
    ("V3_DIRECTIONAL_ENTROPY","V3_EFFICIENCY_TREND"),
    ("V3_DIRECTIONAL_ENTROPY","V3_BODY_IMPULSE"),
    ("V3_DIRECTIONAL_ENTROPY","V3_MEDIAN_DISPLACEMENT"),

    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_MEAN_REVERSION_Z"),
    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_TREND_PULLBACK"),
    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_RANGE_EXPANSION"),
    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_EFFICIENCY_TREND"),
    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_BODY_IMPULSE"),
    ("V3_EFFICIENCY_PULLBACK_RESUME","V3_MEDIAN_DISPLACEMENT"),
]


def candidate_pairs(tf):
    tf=str(tf or "").upper()

    source=(
        list(COMPLEMENTARY)
        + list(
            BOUNDED_RECOMPOSE.get(
                tf,
                [],
            )
        )
        + (
            list(CROSS_TF_RECOMPOSE_30M)
            if tf == "30M"
            else []
        )
        + (
            list(ANCHOR_RECOMPOSE_30M)
            if tf == "30M"
            else []
        )
    )

    rows=[]
    seen=set()

    for a,b in source:
        key=frozenset((a,b))

        if key in seen:
            continue

        seen.add(key)
        rows.append((a,b))

    return rows


def pair_origin(a,b,tf):
    tf=str(tf or "").upper()
    key=frozenset((a,b))

    for x,y in BOUNDED_RECOMPOSE.get(tf,[]):
        if key == frozenset((x,y)):
            return "CROSS_TF_VALIDATED_PARENT_RECOMPOSE"

    if tf == "30M":
        for x,y in CROSS_TF_RECOMPOSE_30M:
            if key == frozenset((x,y)):
                return "CROSS_TF_COMBO_PASS_RECOMPOSE"

        for x,y in ANCHOR_RECOMPOSE_30M:
            if key == frozenset((x,y)):
                return "30M_PASS_ANCHOR_BOUNDED_RECOMPOSE"

    return "ORIGINAL_COMPLEMENTARY"

def now(): return datetime.now(timezone.utc).isoformat()
def validated(tf): return {str(r.get("strategy") or "").upper():r for r in single.all_results_for_tf(tf)}
def family_path(name):
    target=str(name or "").upper()

    # Original registered family source resolution.
    for p in single.family_files():
        if str(
            single.load_meta(p).get("name")
            or p.stem
        ).upper()==target:
            return p

    # GENERATED_RESEARCH_SOURCE_RESOLUTION_V1
    #
    # Generated SAFE_GRAMMAR and other research candidates retain their
    # exact source path in research knowledge.  They are valid combination
    # parents when that stored artifact still exists.  Do not require them
    # to be duplicated into the static family_files() directory.
    k,_=knowledge.ensure()

    for row in (k.get("records") or {}).values():
        if not isinstance(row,dict):
            continue

        if str(
            row.get("strategy")
            or row.get("strategy_name")
            or ""
        ).upper()!=target:
            continue

        stored=str(
            row.get("family_file")
            or row.get("source_file")
            or ""
        ).strip()

        if not stored:
            continue

        p=ROOT/stored

        if p.is_file():
            return p

    return None

def completed(tf):
    _,ledger=knowledge.ensure(); out=set()
    for e in ledger.get("entries") or []:
        if e.get("event")=="COMPLETED" and str(e.get("kind") or "").upper()=="COMBINATION_HP2016_2024" and str(e.get("timeframe") or "").upper()==tf: out.add(str(e.get("candidate") or "").upper())
    return out

def choose_next_tf():
    tfs=list(single.TF_ORDER)
    pending={tf:hypotheses(tf) for tf in tfs}
    done_counts={tf:len(completed(tf)) for tf in tfs}
    return single._weighted_tf_choice(pending,done_counts)

def hypotheses(tf):
    tf=str(tf or "").upper()
    pool=validated(tf); done=completed(tf); rows=[]

    _,ledger=knowledge.ensure()

    completed_single={
        str(e.get("candidate") or "").upper()
        for e in (ledger.get("entries") or [])
        if isinstance(e,dict)
        and e.get("event")=="COMPLETED"
        and str(e.get("kind") or "").upper() in {"SINGLE_FAMILY","SINGLE_FAMILY_HP2016_2024"}
        and str(e.get("timeframe") or "").upper()==tf
    }

    cross_tf_pairs={
        frozenset((a,b))
        for a,b in (
            CROSS_TF_RECOMPOSE_30M
            if tf=="30M"
            else []
        )
    }

    anchor_pairs={
        frozenset((a,b))
        for a,b in (
            ANCHOR_RECOMPOSE_30M
            if tf=="30M"
            else []
        )
    }

    for a,b in candidate_pairs(tf):
        cid=f"{a}+{b}"

        if cid in done:
            continue

        standard=(
            a in pool
            and b in pool
        )

        cross_tf_recompose=(
            frozenset((a,b)) in cross_tf_pairs
            and a in completed_single
            and b in completed_single
        )

        anchor_recompose=(
            frozenset((a,b)) in anchor_pairs
            and a in completed_single
            and b in completed_single
        )

        if (
            standard
            or cross_tf_recompose
            or anchor_recompose
        ):
            rows.append((a,b))

    return rows


# EMPTY_MAIN_BOUNDED_COMBO_RECOVERY_V1
#
# Candidate generation only.  Evaluation, ranking, sealed holdout,
# deployment validation, and CANARY policy remain exactly the existing
# combination-research contracts.
#
# The additional wave is capped to the same number of candidate pairs
# as the original COMPLEMENTARY wave; this is not search-until-winner.
RECOVERY_PAIR_BUDGET = len(COMPLEMENTARY)


def _recovery_component_key(row):
    e=row.get("evaluation") or {}
    return (
        single.finite(e.get("walk_forward_cost_stress_2x_avg_net_pct")) or -1e9,
        single.finite(e.get("walk_forward_avg_net_pct")) or -1e9,
        single.finite(e.get("development_avg_net_pct")) or -1e9,
        -(single.finite(e.get("expected_holding_time_hours")) or 1e9),
    )


def recovery_hypotheses(tf):
    tf=str(tf or "").upper()

    # Never replace the original complementary wave.
    if hypotheses(tf):
        return []

    k,_=knowledge.ensure()
    records=k.get("records") or {}
    boundaries=k.get("evidence_boundaries") or {}

    # This recovery path exists only after the original standalone
    # WAVE1 has irreversibly failed.
    single_record=records.get(f"blind:{tf}:WAVE1")
    if not isinstance(single_record,dict):
        return []
    if single_record.get("pass") is not False:
        return []

    # The separate combination holdout must still be completely unused.
    combo_record=records.get(f"blind_combo:{tf}:WAVE1")
    if isinstance(combo_record,dict):
        return []

    boundary=boundaries.get(f"V3_WAVE1_{tf}_COMBO_SEALED_BLIND") or {}
    boundary_status=str(boundary.get("status") or "").upper()
    if boundary_status in {"SEEN_ONE_SHOT","REUSED_FOR_RESEARCH"}:
        return []

    # RECOVERY_TOTAL_BUDGET_HARD_CAP_V2
    # Durable records + failed memory? ?? ?? trial ???? 1?? ??.
    recovery_attempts=set()
    for _store in (
        (k.get('records') or {}).values(),
        (k.get('failed_research') or []),
    ):
        for _row in _store:
            if not isinstance(_row,dict):
                continue
            if str(_row.get('timeframe') or '').upper()!=str(tf).upper():
                continue
            _prov=_row.get('parent_provenance')
            _prov=_prov if isinstance(_prov,dict) else {}
            _origin=str(
                _row.get('research_origin')
                or _prov.get('origin')
                or ''
            ).upper()
            if _origin!='SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE':
                continue
            _trial=str(_row.get('trial') or '').strip()
            if _trial:
                _attempt_key=('TRIAL',_trial)
            else:
                _attempt_key=(
                    'FALLBACK',
                    str(_row.get('strategy') or '').upper(),
                    str(_row.get('result_json') or ''),
                )
            recovery_attempts.add(_attempt_key)
    if len(recovery_attempts)>=RECOVERY_PAIR_BUDGET:
        return []

    pool=validated(tf)
    done=completed(tf)

    eligible=[]

    for name,row in pool.items():
        name=str(name or "").upper()

        # Mutation-24 remains research-only and cannot enter this
        # promotion-eligible MAIN-fill combination wave indirectly.
        if "_M_" in name:
            continue

        # The existing wrapper must be able to resolve the exact source.
        if family_path(name) is None:
            continue

        eligible.append((name,row))

    # Candidate ordering uses the exact same evidence ordering that
    # provisional_choice uses.  This does NOT change evaluation scores.
    eligible.sort(
        key=lambda item:_recovery_component_key(item[1]),
        reverse=True,
    )

    original_pairs={
        frozenset((a,b))
        for a,b in COMPLEMENTARY
    }

    rows=[]

    for i,(a,_) in enumerate(eligible):

        for b,_ in eligible[i+1:]:

            if frozenset((a,b)) in original_pairs:
                continue

            cid=f"{a}+{b}"
            reverse_cid=f"{b}+{a}"

            if cid in done or reverse_cid in done:
                continue

            rows.append((a,b))

            if len(rows) >= RECOVERY_PAIR_BUDGET:
                return rows

    return rows

def wrapper(a, b, tf):

    pa, pb = (
        family_path(a),
        family_path(b),
    )

    if (
        pa is None
        or pb is None
    ):
        raise RuntimeError(
            "FAMILY_SOURCE_MISSING"
        )

    GENERATED.mkdir(
        parents=True,
        exist_ok=True,
    )

    name = (
        f"V3_COMBO_"
        f"{a.removeprefix('V3_')}_"
        f"{b.removeprefix('V3_')}_"
        f"{tf}_V1"
    )

    out = (
        GENERATED
        / (
            name.lower()
            + ".py"
        )
    )

    source_a = pa.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    source_b = pb.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    module_base = (
        name
        .lower()
        .replace("-", "_")
    )

    module_a = (
        "_bbot_"
        + module_base
        + "_a"
    )

    module_b = (
        "_bbot_"
        + module_base
        + "_b"
    )

    meta = {
        "name":
            name,

        "development_mode":
            "BOUNDED_COMPLEMENTARY_COMBINATION",

        "data_mode":
            "OHLCV",

        "allowed_timeframes":
            [
                tf,
            ],

        "components":
            [
                a,
                b,
            ],

        "research_origin":
            pair_origin(
                a,
                b,
                tf,
            ),
    }

    # PORTABLE_GENERATED_COMBINATION_V1
    #
    # Generated strategy source embeds the exact component source
    # used during research. Runtime does not depend on local
    # strategy_lab/v3_single_families paths.

    code = f"""# -*- coding: utf-8 -*-
# PORTABLE_GENERATED_COMBINATION_V1
# AUTO-GENERATED BOUNDED COMBINATION RESEARCH.
# Component implementation is embedded for portable runtime packaging.

import sys
import types
import numpy as np


def _embedded_module(source, name):

    mod = types.ModuleType(name)

    mod.__file__ = (
        "<embedded:"
        + name
        + ">"
    )

    mod.__package__ = ""

    sys.modules[
        name
    ] = mod

    exec(
        compile(
            source,
            mod.__file__,
            "exec",
        ),
        mod.__dict__,
    )

    return mod


_SRC_A = {source_a!r}

_SRC_B = {source_b!r}


A = _embedded_module(
    _SRC_A,
    {module_a!r},
)

B = _embedded_module(
    _SRC_B,
    {module_b!r},
)


STRATEGY_META = {meta!r}


def generate_signals(df, tf):

    x = A.generate_signals(
        df,
        tf,
    )

    y = B.generate_signals(
        df,
        tf,
    )

    out = np.zeros(
        len(df),
        dtype=np.int8,
    )

    out[
        (x > 0)
        & (y > 0)
    ] = 1

    out[
        (x < 0)
        & (y < 0)
    ] = -1

    return out


def signal(df, params=None):

    z = generate_signals(
        df,
        "LIVE",
    )

    v = (
        int(
            z[-1]
        )
        if len(z)
        else 0
    )

    return {{
        "signal":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "state":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "strength_long":
            1.0
            if v > 0
            else 0.0,

        "strength_short":
            1.0
            if v < 0
            else 0.0,

        "reason":
            STRATEGY_META[
                "name"
            ],
    }}
"""

    compile(
        code,
        str(
            out
        ),
        "exec",
    )

    out.write_text(
        code,
        encoding="utf-8",
    )

    return (
        out,
        name,
    )



def find_result(name,after): return single.find_result(name,after)
def run_one(tf,recovery=False):
    # RECOVERY_RECOMPOSE_PROVENANCE_V1
    hs=(
        recovery_hypotheses(tf)
        if recovery
        else hypotheses(tf)
    )

    if not hs:
        return {
            "status":"NO_PENDING_COMBINATION",
            "timeframe":tf,
        }

    a,b=hs[0]
    cid=f"{a}+{b}"

    origin=(
        "SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE"
        if recovery
        else pair_origin(a,b,tf)
    )

    parent_provenance={
        "parents":[a,b],
        "recovery_recompose":bool(recovery),
        "selection_basis":(
            "VALIDATED_PARENT_RANK_BY_"
            "WF_STRESS_WF_DEV_HOLD"
            if recovery
            else "DECLARED_PAIR_ORIGIN"
        ),
        "sealed_combo_blind_reuse_forbidden":
            bool(recovery),
    }

    trial=knowledge.reserve_trial(
        f"V3_COMBO_{cid}_{tf}",
        cid,
        tf,
        "COMBINATION_HP2016_2024",
    )
    # RECOVERY_DURABLE_ATTEMPT_LEDGER_V1
    if recovery:
        _recovery_source = locals().get('h')
        if not isinstance(_recovery_source,dict):
            _recovery_source = {}
        _recovery_strategy = str(
            locals().get('strategy_name')
            or locals().get('name')
            or locals().get('strategy')
            or _recovery_source.get('strategy')
            or _recovery_source.get('name')
            or ''
        ).upper()
        _recovery_attempt_row = {
            'timeframe': str(tf).upper(),
            'trial': trial,
            'strategy': _recovery_strategy,
            'status': 'ATTEMPTED',
            'research_origin': 'SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE',
            'parent_provenance': {
                'origin': 'SEALED_SINGLE_WAVE_FAIL_RECOVERY_RECOMPOSE',
                'recovery_recompose': True,
            },
            'result_json': '',
        }
        knowledge.record_result(
            f'recovery_attempt:{tf}:{trial}',
            _recovery_attempt_row,
            failed=False,
        )

    w,name=wrapper(a,b,tf)
    env=os.environ.copy()
    env["BBOT_LOCAL_ROOT"]=str(ROOT)
    research_start,research_end=single.hard_pass_dates()
    env["BBOT_RESEARCH_DATASET_MODE"]="HARD_PASS_ONLY"
    env["BBOT_RESEARCH_START"]=research_start
    env["BBOT_RESEARCH_END"]=research_end
    start=time.time()

    p=subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(w),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if p.returncode!=0:
        knowledge.complete_trial(
            trial,
            "RUN_STRATEGY_FAIL",
            "FAIL",
            "UNKNOWN",
        )

        knowledge.record_result(
            f"combo:{cid}:{tf}",
            {
                "strategy":name,
        "family_file":str(w.relative_to(ROOT)).replace("\\","/"),
                "components":[a,b],
                "timeframe":tf,
                "trial":trial,
                "status":"FAIL",
                "reason":"RUN_STRATEGY_FAIL",
                "research_origin":origin,
                "parent_provenance":
                    parent_provenance,
            },
            failed=True,
        )

        return {
            "status":"FAIL",
            "trial":trial,
        }

    result=find_result(name,start)

    if result is None:
        knowledge.complete_trial(
            trial,
            "RESULT_NOT_FOUND",
            "FAIL",
            "UNKNOWN",
        )

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
        return {
            "status":"FAIL",
            "reason":"RESULT_NOT_FOUND",
            "trial":trial,
        }

    rep=single.load(result,{})
    ev=single.evaluate(rep,tf,require_fixed_window=True)
    entry_gate=single.evaluate_entry_risk_gate(rep,tf,ev) if bool(ev.get("hard_pass")) else {"pass":False,"decision":"NOT_RUN","reason":"HARD_PASS_REQUIRED"}
    # SOFT_PASS_STATUS_COMBO_V1
    status=("PASS" if bool(ev.get("hard_pass",ev.get("pass"))) else ("SOFT_PASS" if bool(ev.get("soft_pass")) else "FAIL"))
    hold=ev.get("expected_holding_time_hours")
    edge=ev.get("walk_forward_avg_net_pct")

    cte=(
        float(edge)/float(hold)
        if edge is not None
        and hold not in (None,0)
        and float(hold)>0
        else None
    )

    row={
        "strategy":name,
        "family_file":str(w.relative_to(ROOT)).replace("\\","/"),
        "components":[a,b],
        "timeframe":tf,
        "trial":trial,
        "status":status,
        "evaluation":ev,
        "result_json":
            str(result.relative_to(ROOT)),
        "expected_return":edge,
        "expected_loss":None,
        "expected_holding_time_hours":hold,
        "capital_time_efficiency":cte,
        "expected_account_contribution":cte,
        "confidence":None,
        "confidence_level":"INITIAL_BACKTEST_ONLY",
        "strategy_version":"COMBO_V1",
        "exact_strategy_version":"EV_"+__import__("hashlib").sha256(w.read_bytes()).hexdigest()[:16]+"_"+tf,
        "source_sha256":__import__("hashlib").sha256(w.read_bytes()).hexdigest(),
        "research_start":research_start,
        "research_end":research_end,
        "fixed_window_contract":True,
        "research_epoch":"HARD_PASS_FIXED_WINDOW_2016_2024_STAGE10",
        "entry_risk_gate":entry_gate,
        "pipeline_stage":"INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" if status=="PASS" and bool(entry_gate.get("pass")) else ("ENTRY_RISK_REDEVELOPMENT_PENDING" if status=="PASS" else "HARD_PASS_FAIL"),
        "research_origin":origin,
        "parent_provenance":
            parent_provenance,
    }
    # SOFT_PASS_ROW_FLAGS_COMBO_V1
    row["qualification"]=(
        "HARD_PASS"
        if status=="PASS"
        else status
    )
    row["research_shadow_eligible"]=(status=="SOFT_PASS")
    row["canary_eligible"]=False
    row["automatic_promotion"]=False

    knowledge.complete_trial(
        trial,
        str(result.relative_to(ROOT)),
        status,
        "LOW" if status in {"PASS","SOFT_PASS"} else "UNKNOWN",
    )

    knowledge.record_result(
        f"combo:{cid}:{tf}",
        row,
        failed=(status=="FAIL"),
    )

    return row

def all_results_for_tf(tf):
    k,_=knowledge.ensure(); rows=[]
    for key,row in (k.get("records") or {}).items():
        if key.startswith("combo:") and str(row.get("timeframe") or "").upper()==tf and str(row.get("status") or "").upper()=="PASS": rows.append(row)
    return rows

def _combo_blind(row):
    rep=single.load(ROOT/str(row.get("result_json") or ""),{}); tf=str(row.get("timeframe") or "").upper()
    cur=(((rep.get("datasets") or {}).get("CURRENT_35_FROM_2020") or {}).get("timeframes") or {}).get(tf) or {}
    blind=((cur.get("v3_robustness") or {}).get("sealed_blind_combo") or {})
    avg=single.finite(blind.get("avg_net_pct")); stress=single.finite(blind.get("cost_stress_2x_avg_net_pct")); trades=int(blind.get("trades") or 0)
    return {"pass":trades>0 and avg is not None and avg>0 and stress is not None and stress>0,"trades":trades,"avg_net_pct":avg,"cost_stress_2x_avg_net_pct":stress}

def provisional_choice(tf):
    # MAIN_FIRST_COMBO_BLIND_AVAILABLE_ROWS
    # Existing completed combo evidence may proceed without waiting for the entire combo queue.
    rows=all_results_for_tf(tf)
    if not rows:return None
    def key(r):
        e=r.get("evaluation") or {}; return (single.finite(e.get("walk_forward_cost_stress_2x_avg_net_pct")) or -1e9,single.finite(e.get("walk_forward_avg_net_pct")) or -1e9,single.finite(e.get("development_avg_net_pct")) or -1e9,-(single.finite(e.get("expected_holding_time_hours")) or 1e9))
    best=max(rows,key=key)
    k,_=knowledge.ensure()
    bkey=f"blind_combo:{tf}:WAVE1"
    boundary_key=f"V3_WAVE1_{tf}_COMBO_SEALED_BLIND"
    prior=(k.get("records") or {}).get(bkey)
    boundary=(k.get("evidence_boundaries") or {}).get(boundary_key) or {}
    boundary_status=str(boundary.get("status") or "").upper()
    if isinstance(prior,dict):
        if not (
            prior.get("pass")
            and str(
                prior.get("selected_strategy")
                or ""
            ).upper()
            ==
            str(
                best.get("strategy")
                or ""
            ).upper()
        ):
            return None

        out=dict(best)
        out["sealed_blind_combo"]=(
            prior.get("sealed_blind_combo")
            or {}
        )
        out["confidence_level"]="SEALED_BLIND_PASS"
        return out
    # SEALED_BOUNDARY_RECORD_MISSING_BLOCK
    # A consumed combo holdout can never be reopened because its result
    # metadata is absent or corrupt.
    if boundary_status in {"SEEN_ONE_SHOT","REUSED_FOR_RESEARCH"}:
        return None
    blind=_combo_blind(best); rec={"timeframe":tf,"selected_strategy":best.get("strategy"),"pass":bool(blind.get("pass")),"sealed_blind_combo":blind,"status":"PASS" if blind.get("pass") else "FAIL","evidence_boundary":"COMBO_SEALED_OPENED_ONCE_AFTER_BOUNDED_COMBO_SELECTION"}
    # SEALED_BLIND_OFFICIAL_RECORD_V1
    knowledge.record_result(bkey, rec, failed=False)
    if not bool(blind.get("pass")):
        _sealed_fail_memory = dict(rec)
        _sealed_fail_memory["official_record_key"] = bkey
        knowledge.record_result(
            bkey,
            _sealed_fail_memory,
            failed=True,
        )
    if not blind.get("pass"): return None
    out=dict(best); out["sealed_blind_combo"]=blind; out["confidence_level"]="SEALED_BLIND_PASS"; return out

def main():
    tf=(sys.argv[1] if len(sys.argv)>1 else "6H").upper(); print(json.dumps(run_one(tf),ensure_ascii=False,indent=2,default=str))
if __name__=="__main__":main()
